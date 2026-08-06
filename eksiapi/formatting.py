"""Helpers for turning reverse-engineered API payloads into agent-safe data."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from typing import Any

from .errors import EksiApiError

_SENSITIVE_KEYS = {
    "access_token",
    "accesstoken",
    "api-secret",
    "api_secret",
    "apisecret",
    "authorization",
    "client-secret",
    "client_secret",
    "clientsecret",
    "password",
    "refresh_token",
    "refreshtoken",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li"} and self.parts:
            self.parts.append("\n")

    def get_text(self) -> str:
        return unescape("".join(self.parts)).strip()


def _plain_text(value: str) -> str:
    if "<" not in value or ">" not in value:
        return value
    parser = _TextExtractor()
    try:
        parser.feed(value)
        return parser.get_text()
    except (TypeError, ValueError):
        return value


def sanitize_payload(value: Any) -> Any:
    """Remove credential-shaped fields and flatten HTML in an API payload."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                continue
            cleaned[str(key)] = sanitize_payload(item)

        entry_id = next(
            (
                cleaned[key]
                for key in ("EntryId", "entryId", "entry_id")
                if key in cleaned
            ),
            None,
        )
        if entry_id is not None and "source_url" not in cleaned:
            cleaned["source_url"] = f"https://eksisozluk.com/entry/{entry_id}"
        return cleaned
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _plain_text(value)
    return value


def unwrap_response(payload: Any) -> Any:
    """Unwrap the API's ``Data`` envelope and preserve non-envelope payloads."""
    if not isinstance(payload, dict) or "Data" not in payload:
        return sanitize_payload(payload)

    success = payload.get("Success")
    if success is False:
        message = payload.get("Message") or "Ekşi API request was not successful"
        raise EksiApiError(str(message)[:500])
    return sanitize_payload(payload.get("Data"))
