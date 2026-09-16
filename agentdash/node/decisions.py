"""Pending decisions on this node: created by hooks, answered from the hub."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..models import Decision, DecisionStatus, now_ms


@dataclass
class Pending:
    decision: Decision
    future: asyncio.Future[dict[str, Any]] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class DecisionManager:
    def __init__(self) -> None:
        self.pending: dict[str, Pending] = {}
        self.remembered: set[tuple[str, str]] = set()  # (session_id, tool_name)

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def add(self, d: Decision) -> Pending:
        p = Pending(decision=d)
        self.pending[d.id] = p
        return p

    def answer(
        self, decision_id: str, behavior: str, reason: str = "", remember: bool = False
    ) -> bool:
        p = self.pending.get(decision_id)
        if not p or p.future.done():
            return False
        d = p.decision
        d.status = DecisionStatus.allowed if behavior == "allow" else DecisionStatus.denied
        d.answered_at = now_ms()
        d.answer_reason = reason
        d.remember = remember
        if remember and behavior == "allow":
            self.remembered.add((d.session_key, d.tool_name))
        p.future.set_result({"behavior": behavior, "reason": reason})
        return True

    def is_remembered(self, session_key: str, tool_name: str) -> bool:
        return (session_key, tool_name) in self.remembered

    def forget_session(self, session_key: str) -> None:
        self.remembered = {r for r in self.remembered if r[0] != session_key}

    def finish(self, decision_id: str) -> Decision | None:
        p = self.pending.pop(decision_id, None)
        return p.decision if p else None

    def cancel_all(self, status: DecisionStatus = DecisionStatus.cancelled) -> list[Decision]:
        out = []
        for p in list(self.pending.values()):
            if not p.future.done():
                p.decision.status = status
                p.future.set_result({})
            out.append(p.decision)
        self.pending.clear()
        return out
