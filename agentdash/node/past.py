"""Sessions that are not running: every transcript a machine still holds.

The roster shows what runs now. This lists what ran before, from the harnesses' own
stores (Claude `projects/`, the Codex thread database, pi's session files), so any of
them can be opened, resumed here, or packed up and resumed on another machine.
"""

from __future__ import annotations

import json
import re
import secrets
import tarfile
from pathlib import Path
from typing import Any

from ..models import Harness, Session, SessionStatus
from .adapters import pi_session
from .adapters.claude_transcript import _NOT_A_REQUEST, parse_record
from .collectors.claude import account_of, find_transcript

RESUMABLE = ("claude", "codex", "pi")
_NOT_TEXT = re.compile(r"[^A-Za-z0-9]")


class TransferError(Exception):
    pass


def claude_slug(cwd: str) -> str:
    """The folder under `projects/` that Claude Code uses for a working directory."""
    return _NOT_TEXT.sub("-", cwd)


def pi_slug(cwd: str) -> str:
    return "-" + cwd.replace("/", "-") + "--"


# ---- Claude ---------------------------------------------------------------------


def _claude_head(path: Path, max_bytes: int = 64_000) -> dict[str, Any]:
    """cwd, first request and start time from the first records of a transcript."""
    out: dict[str, Any] = {"cwd": "", "first_user": "", "started": None, "model": ""}
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return out
    for raw in data.split(b"\n")[:-1]:
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if not out["cwd"] and rec.get("cwd"):
            out["cwd"] = str(rec["cwd"])
        if out["started"] is None and rec.get("timestamp"):
            for m in parse_record(rec):
                out["started"] = m.ts
                break
        msg = rec.get("message")
        if not out["model"] and rec.get("type") == "assistant" and isinstance(msg, dict):
            out["model"] = str(msg.get("model") or "")
        if not out["first_user"]:
            for m in parse_record(rec):
                if m.kind == "text" and m.role == "user" and not m.is_meta and not m.agent_id:
                    if not m.text.lstrip().startswith(_NOT_A_REQUEST):
                        out["first_user"] = " ".join(m.text.split())[:300]
                        break
        if out["cwd"] and out["first_user"] and out["model"]:
            break
    return out


def _claude_title(path: Path, window: int = 300_000) -> str:
    """The title Claude gave the session, if it wrote one (near the start or the end)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(window)
            tail = b""
            if size > window:
                f.seek(max(window, size - window))
                tail = f.read()
    except OSError:
        return ""
    title = ""
    for line in (head + b"\n" + tail).split(b"\n"):
        if b'"ai-title"' not in line and b'"custom-title"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            title = str(rec.get("customTitle") or rec.get("aiTitle") or title)
    return title.strip()


_facts_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _claude_facts(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except OSError:
        return {}
    hit = _facts_cache.get(str(path))
    if hit and hit[0] == int(st.st_mtime) and hit[1] == st.st_size:
        return hit[2]
    facts = _claude_head(path)
    facts["title"] = _claude_title(path)
    facts["size"] = st.st_size
    facts["mtime"] = int(st.st_mtime * 1000)
    if len(_facts_cache) > 2000:
        _facts_cache.clear()
    _facts_cache[str(path)] = (int(st.st_mtime), st.st_size, facts)
    return facts


MOVE_RECORD = "agentdash-move.json"


def record_move(root: Path, sid: str, cwd: str) -> None:
    """Remember where a moved session works now.

    Claude transcripts and Codex thread rows keep the cwd of the machine the session came
    from, and Codex leaves it there even when resumed with `--cd`. Several sessions can
    move into one store, so the record keeps every session; the top-level fields stay for
    nodes that read the older single-session form.
    """
    path = root / MOVE_RECORD
    moves: dict[str, str] = {}
    try:
        old = json.loads(path.read_text())
        if isinstance(old.get("moves"), dict):
            moves = {str(k): str(v) for k, v in old["moves"].items()}
        if old.get("session_id") and old.get("cwd"):
            moves.setdefault(str(old["session_id"]), str(old["cwd"]))
    except (OSError, ValueError, AttributeError):
        pass
    moves[sid] = cwd
    path.write_text(json.dumps({"session_id": sid, "cwd": cwd, "moves": moves}))


def relocated_cwd(root: Path, sid: str, fallback: str) -> str:
    """The directory a session was moved into, if it was; else what the harness says."""
    try:
        move = json.loads((root / MOVE_RECORD).read_text())
        cwd = None
        if isinstance(move.get("moves"), dict):
            cwd = move["moves"].get(sid)
        if cwd is None and move.get("session_id") == sid:
            cwd = move.get("cwd", "")
        if cwd and Path(cwd).is_absolute():
            return str(cwd)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return fallback


def claude_session(machine: str, config_dir: Path, path: Path) -> Session | None:
    facts = _claude_facts(path)
    if not facts:
        return None
    sid = path.stem
    acct = account_of(config_dir)
    name = facts.get("title") or facts.get("first_user", "")[:72] or path.parent.name[-40:]
    extra: dict[str, Any] = {"config_dir": str(config_dir), "past": True, "size": facts["size"]}
    if acct["account"]:
        extra["account"] = acct["account"]
    if facts.get("first_user"):
        extra["first_user"] = facts["first_user"]
    if facts.get("title"):
        extra["title"] = facts["title"]
    return Session(
        key=Session.make_key(machine, Harness.claude, sid),
        machine=machine,
        harness=Harness.claude,
        provider=acct["provider"],
        session_id=sid,
        name=name,
        cwd=relocated_cwd(config_dir, sid, facts.get("cwd", "")),
        status=SessionStatus.done,
        started_at=facts.get("started"),
        updated_at=facts["mtime"],
        transcript_path=str(path),
        model=facts.get("model", ""),
        extra=extra,
    )


def list_claude(machine: str, config_dirs: list[Path], limit: int | None) -> list[Session]:
    """Newest transcripts first. Logins that share `projects/` are read once."""
    found: list[tuple[int, Path, Path]] = []
    seen_projects: set[Path] = set()
    for d in config_dirs:
        projects = d / "projects"
        try:
            real = projects.resolve()
        except OSError:
            continue
        if real in seen_projects or not real.is_dir():
            continue
        seen_projects.add(real)
        for folder in real.iterdir():
            if not folder.is_dir():
                continue
            for f in folder.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_size < 500:
                    continue  # opened and closed without a word
                found.append((int(st.st_mtime * 1000), f, d))
    found.sort(key=lambda x: -x[0])
    out: list[Session] = []
    seen: set[str] = set()
    for _, f, d in found:
        if f.stem in seen:
            continue
        seen.add(f.stem)
        s = claude_session(machine, d, f)
        if s and (s.cwd or s.extra.get("first_user")):
            out.append(s)
        if limit is not None and len(out) >= limit:
            break
    return out


# ---- Codex and pi -----------------------------------------------------------------


def codex_session(machine: str, t: dict[str, Any], codex_home: Path) -> Session:
    first = " ".join(str(t.get("first_user_message") or "").split())[:300]
    name = t.get("name") or t.get("title") or first[:72] or Path(t.get("cwd") or "codex").name
    extra: dict[str, Any] = {"codex_home": t.get("codex_home") or str(codex_home), "past": True}
    if first:
        extra["first_user"] = first
    if t.get("title"):
        extra["title"] = str(t["title"])
    return Session(
        key=Session.make_key(machine, Harness.codex, str(t["id"])),
        machine=machine,
        harness=Harness.codex,
        provider="openai",
        session_id=str(t["id"]),
        name=str(name)[:72],
        cwd=relocated_cwd(Path(extra["codex_home"]), str(t["id"]), str(t.get("cwd") or "")),
        status=SessionStatus.done,
        started_at=t.get("created_at_ms"),
        updated_at=int(t.get("updated_at_ms") or 0),
        transcript_path=str(t.get("rollout_path") or ""),
        model=str(t.get("model") or ""),
        extra=extra,
    )


def _pi_first_user(path: Path, max_bytes: int = 64_000) -> str:
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return ""
    lines = (line.decode("utf-8", "replace") for line in data.split(b"\n")[:-1])
    for m in pi_session.iter_messages(lines):
        if m.kind == "text" and m.role == "user":
            return " ".join(m.text.split())[:300]
    return ""


def pi_session_at(machine: str, path: Path) -> Session | None:
    head = pi_session.session_header(path)
    try:
        st = path.stat()
    except OSError:
        return None
    sid = str(head.get("id") or path.stem.split("_")[-1])
    cwd = str(head.get("cwd") or "")
    first = _pi_first_user(path)
    return Session(
        key=Session.make_key(machine, Harness.pi, sid),
        machine=machine,
        harness=Harness.pi,
        session_id=sid,
        name=first[:72] or Path(cwd).name or sid[:8],
        cwd=cwd,
        status=SessionStatus.done,
        started_at=int(st.st_ctime * 1000),
        updated_at=int(st.st_mtime * 1000),
        transcript_path=str(path),
        extra={"past": True, "size": st.st_size, **({"first_user": first} if first else {})},
    )


def list_pi(machine: str, sessions_dir: Path, limit: int | None) -> list[Session]:
    if not sessions_dir.is_dir():
        return []
    files = []
    for p in sessions_dir.glob("*/*.jsonl"):
        try:
            files.append((p.stat().st_mtime, p))
        except OSError:
            continue
    files.sort(key=lambda x: -x[0])
    out = []
    for _, p in files[:limit]:
        s = pi_session_at(machine, p)
        if s:
            out.append(s)
    return out


def find_pi(machine: str, sessions_dir: Path, sid: str) -> Session | None:
    for p in sessions_dir.glob(f"*/*{sid}*.jsonl"):
        return pi_session_at(machine, p)
    return None


def find_claude(machine: str, config_dirs: list[Path], sid: str) -> Session | None:
    for d in config_dirs:
        p = find_transcript(d, sid, "")
        if p:
            return claude_session(machine, d, p)
    return None


def matches(s: Session, q: str) -> bool:
    q = q.lower().strip()
    if not q:
        return True
    hay = " ".join(
        str(x)
        for x in (
            s.name,
            s.cwd,
            s.session_id,
            s.extra.get("first_user", ""),
            s.extra.get("title", ""),
            s.model,
        )
    ).lower()
    return all(word in hay for word in q.split())


def search_sessions(sessions, query, live, titles, content=False):
    """Filter the full archive before pagination; optionally scan conversation text."""
    result = []
    seen = set()
    for session in sorted(sessions, key=lambda s: -s.updated_at):
        if session.key in live or session.key in seen:
            continue
        seen.add(session.key)
        if titles.get(session.key):
            session.extra["title"] = titles[session.key]
        if matches(session, query):
            result.append(session)
        elif content and query.strip():
            snippet = content_match(Path(session.transcript_path), query)
            if snippet:
                session.extra["search_excerpt"] = snippet
                result.append(session)
    return result


def content_match(path: Path, query: str) -> str:
    """Search decoded text, including escaped Unicode, without loading a whole log."""
    remaining = set(query.casefold().split())
    snippet = ""

    def texts(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("text", "content", "message", "summary") and isinstance(item, str):
                    yield item
                elif isinstance(item, (dict, list)):
                    yield from texts(item)
        elif isinstance(value, list):
            for item in value:
                yield from texts(item)

    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                for text in texts(record):
                    folded = text.casefold()
                    hits = {word for word in remaining if word in folded}
                    if hits:
                        at = min(folded.index(word) for word in hits)
                        snippet = snippet or " ".join(text[max(0, at - 80) : at + 220].split())
                        remaining -= hits
                    if not remaining:
                        return snippet
    except OSError:
        pass
    return ""


# ---- moving a session between machines -------------------------------------------


def _safe_member(name: str) -> bool:
    return bool(name) and not name.startswith("/") and ".." not in Path(name).parts


def pack(sess: Session, out_dir: Path, environment: bool = False) -> tuple[Path, dict[str, Any]]:
    """Bundle what another machine needs to resume this session: the transcript and, for
    Claude, the folder of sub-agent transcripts next to it."""
    if sess.harness not in RESUMABLE:
        raise TransferError(f"{sess.harness} sessions cannot move")
    src = Path(sess.transcript_path)
    if not src.is_file():
        raise TransferError("this session has no transcript file to move")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle = out_dir / f"{sess.harness}-{secrets.token_hex(16)}.tgz"
    try:
        with tarfile.open(bundle, "w:gz", compresslevel=1) as tar:
            if sess.harness == "claude":
                tar.add(src, arcname=src.name)
                side = src.with_suffix("")
                if side.is_dir():
                    tar.add(side, arcname=side.name)
            elif sess.harness == "codex":
                root = Path(sess.extra.get("codex_home") or Path.home() / ".codex") / "sessions"
                rel = src.relative_to(root) if src.is_relative_to(root) else Path(src.name)
                tar.add(src, arcname=str(rel))
            else:
                tar.add(src, arcname=src.name)
            if environment:
                from . import workspace_transfer

                agent_home = Path(
                    sess.extra.get("config_dir" if sess.harness == "claude" else "codex_home")
                    or Path.home() / (".claude" if sess.harness == "claude" else ".codex")
                )
                workspace_transfer.add(
                    tar, Path(sess.cwd), Path.home(), str(sess.harness), agent_home
                )
    except BaseException:
        bundle.unlink(missing_ok=True)
        raise
    meta = {
        "environment": environment,
        "harness": str(sess.harness),
        "session_id": sess.session_id,
        "cwd": sess.cwd,
        "name": sess.name,
        "model": sess.model,
        "size": bundle.stat().st_size,
    }
    return bundle, meta


def unpack(
    bundle: Path, harness: str, sid: str, cwd: str, root: Path, *, check_only: bool = False
) -> Path:
    """Put a bundle where the harness on this machine looks for it, and return the
    transcript's new path. `root` is the Claude config dir, the Codex home, or pi's
    sessions folder."""
    if harness == "claude":
        dest = (root / "projects").resolve() / claude_slug(cwd)
        ok = lambda n: n == f"{sid}.jsonl" or n.startswith(f"{sid}/")  # noqa: E731
        target = dest / f"{sid}.jsonl"
    elif harness == "codex":
        dest = root / "sessions"
        ok = lambda n: n.endswith(".jsonl") and sid in n  # noqa: E731
        target = None
    elif harness == "pi":
        dest = root / pi_slug(cwd)
        ok = lambda n: n.endswith(".jsonl") and "/" not in n  # noqa: E731
        target = None
    else:
        raise TransferError(f"{harness} sessions cannot move")
    with tarfile.open(bundle, "r:gz") as tar:
        members = [m for m in tar.getmembers() if _safe_member(m.name) and ok(m.name)]
        files = [m for m in members if m.isfile()]
        if not files:
            raise TransferError("the bundle holds no transcript for this session")
        if target is None:
            target = dest / next(m.name for m in files if m.name.endswith(".jsonl"))
        if not any(dest / m.name == target for m in files):
            raise TransferError("the bundle holds no main transcript for this session")
        for m in files:
            existing = dest / m.name
            if existing.exists():
                import hashlib

                with existing.open("rb") as old, tar.extractfile(m) as new:
                    if (
                        hashlib.file_digest(old, "sha256").digest()
                        != hashlib.file_digest(new, "sha256").digest()
                    ):
                        raise TransferError(f"destination has a different transcript: {existing}")
        if check_only:
            return target
        dest.mkdir(parents=True, exist_ok=True)
        for m in members:
            if m.isfile() or m.isdir():
                tar.extract(m, dest, filter="data")
    if not target.is_file():
        raise TransferError("the transcript did not arrive")
    return target
