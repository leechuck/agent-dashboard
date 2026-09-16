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


if __name__ == "__main__":
    app()
