"""Wire and storage models shared by node, hub and web."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class Harness(StrEnum):
    claude = "claude"
    codex = "codex"
    pi = "pi"
    opencode = "opencode"
    hermes = "hermes"
    tmux = "tmux"


class SessionStatus(StrEnum):
    busy = "busy"
    idle = "idle"
    waiting = "waiting"  # needs the user
    done = "done"
    failed = "failed"
    stopped = "stopped"
    offline = "offline"


class Session(BaseModel):
    key: str  # "<machine>:<harness>:<session_id>"
    machine: str
    harness: Harness
    provider: str = ""  # anthropic | openrouter | openai | ...
    session_id: str
    name: str = ""
    cwd: str = ""
    kind: Literal["interactive", "background", "headless", "unknown"] = "unknown"
    status: SessionStatus = SessionStatus.idle
    waiting_for: str = ""
    pid: int | None = None
    started_at: int | None = None  # ms
    updated_at: int = Field(default_factory=now_ms)
    transcript_path: str = ""
    native_url: str = ""  # deep link into the harness' own remote control
    last_line: str = ""
    model: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def make_key(machine: str, harness: str, session_id: str) -> str:
        return f"{machine}:{harness}:{session_id}"


class Message(BaseModel):
    id: str
    ts: int | None = None  # ms
    role: Literal["user", "assistant", "tool", "system"]
    kind: Literal["text", "thinking", "tool_use", "tool_result", "system"] = "text"
    text: str = ""
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict[str, Any] | None = None
    is_error: bool = False
    is_meta: bool = False
    agent_id: str = ""


class Machine(BaseModel):
    id: str
    hostname: str = ""
    os: str = ""
    harnesses: list[str] = Field(default_factory=list)
    node_version: str = ""
    online: bool = False
    armed: bool = False
    armed_until: int = 0  # ms; 0 = no expiry
    last_seen: int = Field(default_factory=now_ms)

    @property
    def armed_now(self) -> bool:
        return self.armed and (self.armed_until == 0 or now_ms() < self.armed_until)


class DecisionStatus(StrEnum):
    pending = "pending"
    allowed = "allowed"
    denied = "denied"
    expired = "expired"
    cancelled = "cancelled"
    answered = "answered"  # question answered elsewhere / not answerable here


class Decision(BaseModel):
    """A prompt a harness raised that needs Robert: permission, approval, question."""

    id: str
    machine: str
    session_key: str
    harness: Harness
    kind: Literal["permission", "approval", "question"] = "permission"
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    question: str = ""
    reason: str = ""
    cwd: str = ""
    session_name: str = ""
    native_url: str = ""
    created_at: int = Field(default_factory=now_ms)
    expires_at: int = 0
    status: DecisionStatus = DecisionStatus.pending
    answered_at: int | None = None
    answer_reason: str = ""
    remember: bool = False


class UsageWindow(BaseModel):
    """One rate-limit or credit window as seen from a machine."""

    provider: str  # anthropic | openai | openrouter
    account: str = ""  # plan / label, never a secret
    window: str  # five_hour | seven_day | primary_10080m | credits | ...
    label: str = ""
    used_pct: float = 0.0
    resets_at: int | None = None  # ms
    source: str = ""
    machine: str = ""
    fetched_at: int = Field(default_factory=now_ms)
    detail: dict[str, Any] = Field(default_factory=dict)


# --- node <-> hub frames -------------------------------------------------


class Frame(BaseModel):
    """Envelope for both directions. `type` selects the payload shape."""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: int = Field(default_factory=now_ms)


# node -> hub types
NODE_HELLO = "hello"
NODE_SESSIONS = "sessions.snapshot"
NODE_MESSAGES = "session.messages"
NODE_EVENT = "event"
NODE_PONG = "pong"
NODE_DECISION_CREATED = "decision.created"
NODE_DECISION_RESOLVED = "decision.resolved"
NODE_USAGE = "usage.snapshot"

# hub -> node types
HUB_HELLO_OK = "hello.ok"
HUB_SUBSCRIBE = "messages.subscribe"
HUB_UNSUBSCRIBE = "messages.unsubscribe"
HUB_SEND_PROMPT = "session.prompt"
HUB_PING = "ping"
HUB_ARM = "machine.arm"
HUB_SESSION_ACTION = "session.action"
HUB_SESSION_START = "session.start"
HUB_DECISION_ANSWER = "decision.answer"
