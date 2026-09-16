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


if __name__ == "__main__":
    app()
