"""Safety policy primitives for interactive MCP account actions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from typing import Literal

from eksiapi.models import WritePreview

ServerMode = Literal["readonly", "interactive"]


@dataclass(slots=True)
class _StoredPreview:
    preview: WritePreview
    expires_at: float


class PreviewStore:
    """Process-local, signed, expiring and single-use preview registry."""

    def __init__(self, *, ttl: float = 300.0, secret: bytes | None = None) -> None:
        if ttl <= 0:
            raise ValueError("preview TTL must be greater than zero")
        self.ttl = ttl
        self._secret = secret or secrets.token_bytes(32)
        self._items: dict[str, _StoredPreview] = {}
        self._lock = threading.Lock()

    def issue(self, preview: WritePreview) -> dict[str, object]:
        identifier = secrets.token_urlsafe(24)
        signature = hmac.new(
            self._secret, identifier.encode(), hashlib.sha256
        ).hexdigest()
        token = f"{identifier}.{signature}"
        with self._lock:
            self._items[identifier] = _StoredPreview(
                preview, time.monotonic() + self.ttl
            )
        return {
            "preview_token": token,
            "expires_in": self.ttl,
            "preview": asdict(preview),
            "digest": preview.digest,
        }

    def peek(self, token: str) -> WritePreview:
        identifier = self._verify(token)
        with self._lock:
            stored = self._items.get(identifier)
            if stored is None:
                raise ValueError("preview token is unknown or was already used")
            if time.monotonic() >= stored.expires_at:
                del self._items[identifier]
                raise ValueError("preview token has expired")
            return stored.preview

    def consume(self, token: str, *, operation: str) -> WritePreview:
        identifier = self._verify(token)
        with self._lock:
            stored = self._items.pop(identifier, None)
            if stored is None:
                raise ValueError("preview token is unknown or was already used")
            if time.monotonic() >= stored.expires_at:
                raise ValueError("preview token has expired")
            if stored.preview.operation != operation:
                self._items[identifier] = stored
                raise ValueError("preview token belongs to a different operation")
            return stored.preview

    def _verify(self, token: str) -> str:
        try:
            identifier, supplied = token.rsplit(".", 1)
        except ValueError:
            raise ValueError("invalid preview token") from None
        expected = hmac.new(
            self._secret, identifier.encode(), hashlib.sha256
        ).hexdigest()
        if not identifier or not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid preview token")
        return identifier
