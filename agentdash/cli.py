"""Command line entry points: hub, node, hooks, install."""

from __future__ import annotations

import asyncio

import typer

from . import __version__
from .config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="agentdash fleet dashboard")


@app.callback()
def _main() -> None:
    pass


@app.command()
def version() -> None:
    """Print version."""
    typer.echo(f"agentdash {__version__}")


@app.command()
def hub(
    host: str = typer.Option(None, help="bind host (default from settings)"),
    port: int = typer.Option(None, help="bind port (default from settings)"),
    reload: bool = typer.Option(False, help="uvicorn autoreload (dev)"),
) -> None:
    """Run the hub server (REST + SSE for the web app, websocket for nodes)."""
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "agentdash.hub.app:create_app",
        factory=True,
        host=host or s.hub_host,
        port=port or s.hub_port,
        reload=reload,
        log_level="info",
    )


@app.command()
def node(
    hub_url: str = typer.Option(None, help="hub websocket URL (default from settings)"),
) -> None:
    """Run the per-machine node: collectors, hook receiver, hub link."""
    from .node.runner import run_node

    s = get_settings()
    if hub_url:
        s.hub_url = hub_url
    asyncio.run(run_node(s))


@app.command()
def doctor() -> None:
    """Check hooks, tokens, node, hub, Tailscale and history on this machine."""
    from .doctor import main as doctor_main

    raise typer.Exit(doctor_main(get_settings()))


install_app = typer.Typer(help="Install hooks and services on this machine.")
app.add_typer(install_app, name="install")


@install_app.command("hooks")
def install_hooks(
    config_dir: list[str] = typer.Option(
        None, help="Claude config dir(s); default: settings claude_config_dirs that exist"
    ),
    permission_timeout: int = typer.Option(1800, help="seconds a phone decision may take"),
    remove: bool = typer.Option(False, help="remove agentdash hooks instead"),
) -> None:
    """Write agentdash hook entries into ~/.claude/settings.json (backup kept)."""
    from pathlib import Path

    from .install.hooks import install, uninstall

    s = get_settings()
    dirs = (
        [Path(d).expanduser() for d in config_dir]
        if config_dir
        else [d for d in s.claude_config_dirs if d.exists()]
    )
    for d in dirs:
        path = d / "settings.json"
        if remove:
            uninstall(path)
            typer.echo(f"removed agentdash hooks from {path}")
        else:
            install(path, permission_timeout)
            typer.echo(f"installed agentdash hooks into {path}")


@install_app.command("codex")
def install_codex_cmd(
    permission_timeout: int = typer.Option(1800, help="seconds a phone decision may take"),
) -> None:
    """Write agentdash hooks into ~/.codex/hooks.json (then trust them with /hooks in Codex)."""
    from pathlib import Path

    from .install.hooks import install_codex

    path = Path.home() / ".codex" / "hooks.json"
    install_codex(path, permission_timeout)
    typer.echo(f"installed agentdash hooks into {path}; run /hooks inside Codex to trust them")


@install_app.command("pi")
def install_pi_cmd() -> None:
    """Install the agentdash pi extension into ~/.pi/agent/extensions."""
    from pathlib import Path

    from .install.hooks import install_pi

    dst = install_pi(Path.home() / ".pi" / "agent" / "extensions")
    typer.echo(f"installed {dst}")


@install_app.command("statusline")
def install_statusline_cmd(
    config_dir: list[str] = typer.Option(None, help="Claude config dir(s)"),
) -> None:
    """Wrap the Claude status line so rate-limit windows are recorded for the node."""
    from pathlib import Path

    from .install.hooks import install_statusline

    s = get_settings()
    dirs = (
        [Path(d).expanduser() for d in config_dir]
        if config_dir
        else [d for d in s.claude_config_dirs if d.exists()]
    )
    for d in dirs:
        install_statusline(d / "settings.json")
        typer.echo(f"statusline sidecar installed into {d / 'settings.json'}")


@install_app.command("launcher")
def install_launcher_cmd() -> None:
    """Install agent-tmux, which starts an agent inside tmux so the dashboard can type into it."""
    import shutil
    from importlib import resources
    from pathlib import Path

    dst = Path.home() / ".local" / "bin" / "agent-tmux"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(resources.files("agentdash.install") / "files" / "agent-tmux") as src:
        shutil.copy2(src, dst)
    dst.chmod(0o755)
    typer.echo(f"installed {dst}")
    typer.echo(
        "\nTo start agents in tmux by default, add to ~/.bashrc:\n"
        "  alias codex='agent-tmux codex'\n"
        "  alias claude='agent-tmux claude'\n"
        "  alias pi='agent-tmux pi'\n"
        "Scrolling inside tmux: PgUp/PgDn, or put `set -g mouse on` in ~/.tmux.conf."
    )


@install_app.command("account")
def install_account_cmd(
    name: str = typer.Argument(..., help='short name of the login, e.g. "team"'),
) -> None:
    """Add a second Claude login (~/.claude-NAME and a claude-NAME command) that the node tracks."""
    from .install.account import install_account

    try:
        for line in install_account(name):
            typer.echo(line)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(
        f"\nNow run `claude-{name}` and log in with /login using that account.\n"
        "Then restart the node: systemctl --user restart agentdash-node"
    )


if __name__ == "__main__":
    app()
