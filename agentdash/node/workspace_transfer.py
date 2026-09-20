"""File environment carried with a session; never overwrite destination work.

Archives include hidden/untracked files and local dependencies. Absolute paths in
programs and OS services are not relocatable; record the source platform and retain
symlinks, checking their destinations before launch.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path

from ..transfer_limits import MAX_BYTES, require_space, require_spaces
from .past import TransferError

PREFIX = "environment/"


def roots(cwd: Path, home: Path, harness: str, agent_home: Path) -> dict[str, Path]:
    result = {"workspace": cwd}
    # Credentials belong to the destination login. Only working configuration travels.
    names = (
        (
            "CLAUDE.md",
            "settings.json",
            "settings.local.json",
            "skills",
            "agents",
            "commands",
            "plugins",
        )
        if harness == "claude"
        else ("AGENTS.md", "config.toml", "rules", "skills", "plugins", "memories")
    )
    for name in names:
        path = agent_home / name
        if path.exists():
            result[f"agent/{name}"] = path
    if harness == "claude":
        from .past import claude_slug

        memory = agent_home / "projects" / claude_slug(str(cwd)) / "memory"
        if memory.exists():
            result["project-memory"] = memory
    if (home / ".agents/skills").exists():
        result["shared/skills"] = home / ".agents/skills"
    return result


def add(tar: tarfile.TarFile, cwd: Path, home: Path, harness: str, agent_home: Path) -> None:
    if not cwd.is_dir() or cwd.resolve() in (home.resolve(), Path("/")):
        raise TransferError("move requires a project directory, not the entire home or filesystem")
    total = 0
    links = []

    def include(info: tarfile.TarInfo) -> tarfile.TarInfo:
        nonlocal total
        if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
            raise TransferError(f"cannot move live socket/device: {info.name}")
        total += info.size
        if total > MAX_BYTES:
            raise TransferError("working environment exceeds the 32 GiB transfer limit")
        if info.isfile():
            require_space(Path(tar.name), info.size)
        if info.issym():
            links.append({"path": info.name, "target": info.linkname})
        return info

    for name, path in roots(cwd, home, harness, agent_home).items():
        # Skills/plugins commonly point into another checkout. Carry their contents.
        tar.dereference = name != "workspace"
        tar.add(path.resolve(), arcname=PREFIX + name, filter=include)
    tar.dereference = False
    data = json.dumps(
        {
            "version": 1,
            "cwd": str(cwd),
            "home": str(home),
            "system": platform.system(),
            "architecture": platform.machine(),
            "links": links,
        }
    ).encode()
    entry = tarfile.TarInfo(PREFIX + "manifest.json")
    entry.size, entry.mode = len(data), 0o600
    tar.addfile(entry, io.BytesIO(data))


def _same(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.is_symlink() and b.is_symlink() and os.readlink(a) == os.readlink(b)
    if not a.is_file() or not b.is_file() or a.stat().st_size != b.stat().st_size:
        return False

    def digest(p: Path) -> bytes:
        with p.open("rb") as f:
            return hashlib.file_digest(f, "sha256").digest()

    return digest(a) == digest(b) and (a.stat().st_mode & 0o777) == (b.stat().st_mode & 0o777)


def restore(bundle: Path, cwd: Path, home: Path, agent_home: Path, *, check_only=False) -> bool:
    """Validate the entire bundle before writes. Existing differing files are conflicts.

    Missing files may be added; existing files, including dirty files, are never replaced.
    The caller must not launch after any failure. Returns False for legacy bundles.
    """
    with tarfile.open(bundle, "r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        try:
            manifest = json.load(tar.extractfile(PREFIX + "manifest.json"))
        except KeyError:
            return False
        if manifest.get("version") != 1:
            raise TransferError("unsupported environment bundle version")
        if (manifest["system"], manifest["architecture"]) != (
            platform.system(),
            platform.machine(),
        ):
            raise TransferError(
                "source and destination OS/architecture differ; dependencies need rebuilding"
            )
        stage = Path(tmp)
        from .past import claude_slug

        mapping = {
            "workspace": cwd,
            "agent": agent_home,
            "shared": cwd / ".agents",
            "project-memory": agent_home / "projects" / claude_slug(str(cwd)) / "memory",
        }
        members = []
        total = 0
        seen = set()
        for m in tar.getmembers():
            if not m.name.startswith(PREFIX) or m.name == PREFIX + "manifest.json":
                continue
            rel = Path(m.name.removeprefix(PREFIX))
            if (
                rel.is_absolute()
                or ".." in rel.parts
                or not rel.parts
                or rel.parts[0] not in mapping
            ):
                raise TransferError("unsafe environment path")
            if m.name in seen:
                raise TransferError("duplicate environment path")
            seen.add(m.name)
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                raise TransferError("unsupported environment entry (hard links/devices)")
            total += m.size
            if total > MAX_BYTES:
                raise TransferError("environment bundle exceeds size limit")
            members.append((m, rel))
        require_space(stage, total)
        # Extract regular files first, never through a symlink supplied by the archive.
        for m, rel in members:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if m.isdir():
                target.mkdir(exist_ok=True)
            elif m.isfile():
                with tar.extractfile(m) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(m.mode & 0o777)
        for m, rel in members:
            if m.islnk():
                link = Path(m.linkname.removeprefix(PREFIX))
                if not m.linkname.startswith(PREFIX) or link.is_absolute() or ".." in link.parts:
                    raise TransferError("unsafe environment hard link")
                source = stage / link
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or not source.resolve().is_relative_to(stage)
                ):
                    raise TransferError("missing environment hard link target")
                os.link(source, stage / rel)
                (stage / rel).chmod(m.mode & 0o777)
            if m.issym():
                target = stage / rel
                if target.exists():
                    raise TransferError("environment symlink has children")
                target.symlink_to(m.linkname)
        operations = []
        destinations = {}
        for m, rel in members:
            dest = mapping[rel.parts[0]].joinpath(*rel.parts[1:])
            previous = destinations.get(dest)
            if previous is not None:
                if m.isdir() and previous.is_dir():
                    continue
                if _same(previous, stage / rel):
                    continue
                raise TransferError(f"conflicting bundled files for {dest}")
            destinations[dest] = stage / rel
            # Existing parent symlinks could redirect a write outside the chosen root.
            for parent in [dest, *dest.parents]:
                if parent.is_symlink():
                    if parent == dest and m.issym() and _same(stage / rel, dest):
                        continue
                    raise TransferError(f"destination has a symlink at {parent}")
            if dest.exists() or dest.is_symlink():
                if m.isdir() and dest.is_dir():
                    continue
                if not _same(stage / rel, dest):
                    raise TransferError(
                        f"destination conflict: {dest}; choose a clean folder or reconcile it first"
                    )
                continue
            operations.append((m, rel, dest))
        require_spaces(
            [
                (dest.parent, (stage / rel).stat().st_size)
                for m, rel, dest in operations
                if m.isfile() or m.islnk()
            ]
        )
        if check_only:
            return True
        for m, rel, dest in operations:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if m.isdir():
                dest.mkdir(exist_ok=True)
            elif m.issym():
                dest.symlink_to(m.linkname)
            else:
                # Exclusive creation also protects against changes after validation.
                with (stage / rel).open("rb") as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                dest.chmod(m.mode & 0o777)
    return True


def relocation_message(
    source: str, target: str, old_cwd: str, cwd: str, environment: bool, note: str = ""
) -> str:
    scope = (
        "Your project files (including hidden/untracked files and local dependencies), "
        "conversation, skills and working agent configuration were copied. "
        "Authentication uses the destination's existing login."
        if environment
        else "Only your conversation was copied; project files were not transferred."
    )
    return (
        f"This session has been moved from {source} to {target}.\n"
        f"Previous working directory: {old_cwd}\nCurrent working directory: {cwd}\n"
        f"{scope}\n"
        "You are a resumed process on a different host. Running commands, in-memory state, "
        "system packages, sockets, and services such as laptop Gnus did not migrate. "
        "Check the current host, working directory, instructions, dependencies and symlinks "
        "before continuing. Absolute paths in configuration or virtual environments may "
        "still refer to the old host. Identify any additional folders, tools, credentials "
        "or services you need and tell Robert exactly what is missing and where it was. "
        "Do not assume those resources are available or silently replace them. "
        "Continue independent work that is possible here.\n"
        + (f"\nRobert's instruction after the move:\n{note}" if note else "")
    )


def bootstrap_claude_login(login_root: Path, runtime: Path, home: Path) -> None:
    """Reuse destination onboarding/account state without sharing its writable config."""
    path = login_root / ".claude.json"
    if login_root == home / ".claude" or not path.is_file():
        path = home / ".claude.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    # Destination paths, MCP definitions and project state stay on their owner.
    keys = ("hasCompletedOnboarding", "lastOnboardingVersion", "theme", "oauthAccount")
    copied = {key: data[key] for key in keys if key in data}
    target = runtime / ".claude.json"
    if target.exists():
        current = json.loads(target.read_text())
        if isinstance(current, dict):
            copied.update(current)
    target.write_text(json.dumps(copied))
    target.chmod(0o600)
