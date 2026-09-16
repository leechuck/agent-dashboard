"""Web Push (VAPID) notifications to the phone."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Pusher:
    def __init__(
        self, state_dir: Path, subject: str = "mailto:robert.hoehndorf@kaust.edu.sa"
    ) -> None:
        self.keyfile = state_dir / "vapid.json"
        self.subject = subject
        self.private_pem: str = ""
        self.public_key: str = ""
        self._load_or_create()

    def _load_or_create(self) -> None:
        if self.keyfile.exists():
            data = json.loads(self.keyfile.read_text())
            self.private_pem, self.public_key = data["private_pem"], data["public_key"]
            return
        try:
            from cryptography.hazmat.primitives import serialization
            from py_vapid import Vapid, b64urlencode
        except ImportError as e:  # pragma: no cover
            log.warning("push disabled: %s", e)
            return
        v = Vapid()
        v.generate_keys()
        pem = v.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        raw = v.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.private_pem, self.public_key = pem, b64urlencode(raw)
        self.keyfile.parent.mkdir(parents=True, exist_ok=True)
        self.keyfile.write_text(
            json.dumps({"private_pem": pem, "public_key": self.public_key}, indent=2)
        )
        self.keyfile.chmod(0o600)

    @property
    def enabled(self) -> bool:
        return bool(self.private_pem)

    def _send_one(self, sub: dict[str, Any], payload: dict[str, Any]) -> bool:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=self.private_pem,
                vapid_claims={"sub": self.subject},
                ttl=600,
            )
            return True
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            log.warning("push failed (%s): %s", status, e)
            return status not in (404, 410)  # gone -> drop subscription

    async def send(self, subs: list[dict[str, Any]], payload: dict[str, Any]) -> list[str]:
        """Send to all; return endpoints that should be removed."""
        if not self.enabled or not subs:
            return []
        results = await asyncio.gather(
            *(asyncio.to_thread(self._send_one, s, payload) for s in subs)
        )
        return [s["endpoint"] for s, ok in zip(subs, results, strict=True) if not ok]
