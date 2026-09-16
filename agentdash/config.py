"""Settings shared by hub, node and hooks. Loaded from env and ~/.agentdash/.env."""

from __future__ import annotations

import socket
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

STATE_DIR = Path.home() / ".agentdash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTDASH_",
        env_file=(STATE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # identity
    machine_id: str = Field(default_factory=lambda: socket.gethostname().split(".")[0])

    # hub
    hub_host: str = "127.0.0.1"
    hub_port: int = 8790
    hub_db: Path = STATE_DIR / "hub.db"
    web_token: str = ""  # empty = no auth (dev only)
    node_token: str = ""  # shared secret for node websocket
    history_url: str = "http://127.0.0.1:8080"  # agentsview
    history_token: str = ""

    # node
    hub_url: str = "ws://127.0.0.1:8790/nodes"
    node_host: str = "127.0.0.1"
    node_port: int = 8791  # local HTTP for hooks
    claude_config_dirs: list[Path] = Field(
        default_factory=lambda: [Path.home() / ".claude", Path.home() / ".claude-openrouter"]
    )
    roster_interval: float = 10.0
    tail_lines: int = 200
    decision_timeout: float = 1770.0  # seconds; keep below the hook timeout (1800)
    arm_hours: float = 12.0

    @property
    def state_dir(self) -> Path:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        return STATE_DIR


def get_settings() -> Settings:
    return Settings()
