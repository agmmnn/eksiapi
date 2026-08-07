"""Typed, dependency-free models for common Ekşi API responses."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def _pick(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        if name in data:
            return data[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class ApiResponse(Generic[T]):
    """Normalized view of the API's envelope without discarding the original payload."""

    data: T
    success: bool | None
    message: str | None
    status_code: int | None
    raw: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ApiResponse[Any]:
        status = _pick(payload, "StatusCode", "statusCode")
        return cls(
            data=_pick(payload, "Data", "ResultObject", default=payload),
            success=_pick(payload, "Success", "success"),
            message=_pick(payload, "Message", "message"),
            status_code=int(status)
            if isinstance(status, int | str) and str(status).isdigit()
            else None,
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class TokenInfo:
    access_token: str
    client_secret: str
    refresh_token: str | None = None
    expires_at: float | None = None

    @property
    def expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc).timestamp() >= self.expires_at - 30


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    limit: int | None = None
    remaining: int | None = None
    reset_at: float | None = None
    retry_after: float | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, Any]) -> RateLimitInfo:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}

        def integer(name: str) -> int | None:
            value = normalized.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        def number(name: str) -> float | None:
            value = normalized.get(name)
            try:
                return float(value) if value is not None else None
            except ValueError:
                return None

        return cls(
            limit=integer("x-ratelimit-limit"),
            remaining=integer("x-ratelimit-remaining"),
            reset_at=number("x-ratelimit-reset"),
            retry_after=number("retry-after"),
        )


@dataclass(frozen=True, slots=True)
class Entry:
    id: int | None
    content: str | None
    author: str | None
    topic: str | None
    created_at: str | None
    source_url: str | None
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Entry:
        entry_id = _pick(data, "EntryId", "entryId", "entry_id", "Id", "id")
        author_value = _pick(data, "Author", "author", "Nick", "nick")
        if isinstance(author_value, Mapping):
            author_value = _pick(author_value, "Nick", "nick", "Name", "name")
        parsed_id = (
            int(entry_id)
            if isinstance(entry_id, int | str) and str(entry_id).isdigit()
            else None
        )
        return cls(
            id=parsed_id,
            content=_pick(data, "Content", "content", "EntryContent", "entryContent"),
            author=str(author_value) if author_value is not None else None,
            topic=_pick(data, "Title", "title", "Topic", "topic"),
            created_at=_pick(
                data, "Created", "created", "CreateDate", "createDate", "Date", "date"
            ),
            source_url=(
                _pick(data, "source_url")
                or (
                    f"https://eksisozluk.com/entry/{parsed_id}"
                    if parsed_id is not None
                    else None
                )
            ),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class User:
    nick: str | None
    id: int | None
    karma: Any
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> User:
        user_id = _pick(data, "Id", "id", "UserId", "userId")
        parsed_id = (
            int(user_id)
            if isinstance(user_id, int | str) and str(user_id).isdigit()
            else None
        )
        return cls(
            nick=_pick(data, "Nick", "nick", "Username", "username"),
            id=parsed_id,
            karma=_pick(data, "Karma", "karma"),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class Message:
    id: int | None
    thread_id: int | None
    sender: str | None
    recipient: str | None
    content: str | None
    created_at: str | None
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Message:
        def parsed(*names: str) -> int | None:
            value = _pick(data, *names)
            return (
                int(value)
                if isinstance(value, int | str) and str(value).isdigit()
                else None
            )

        return cls(
            id=parsed("Id", "id", "MessageId", "messageId"),
            thread_id=parsed("ThreadId", "threadId"),
            sender=_pick(data, "From", "from", "Sender", "sender"),
            recipient=_pick(data, "To", "to", "Recipient", "recipient"),
            content=_pick(data, "Message", "message", "Content", "content"),
            created_at=_pick(data, "Date", "date", "Created", "created"),
            raw=data,
        )


_ITEM_KEYS = (
    "Entries",
    "entries",
    "EntryList",
    "entryList",
    "Items",
    "items",
    "Notifications",
    "notifications",
    "Messages",
    "messages",
    "Results",
    "results",
)


def extract_items(payload: Any) -> list[Any]:
    """Extract a paginated item list from known Android response containers."""
    if isinstance(payload, Mapping):
        data = _pick(payload, "Data", "ResultObject", default=payload)
        if data is not payload:
            return extract_items(data)
        for key in _ITEM_KEYS:
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(
                value, str | bytes | bytearray
            ):
                return list(value)
        for value in payload.values():
            if isinstance(value, Mapping):
                nested = extract_items(value)
                if nested:
                    return nested
        return []
    if isinstance(payload, Sequence) and not isinstance(
        payload, str | bytes | bytearray
    ):
        return list(payload)
    return []


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_count: int | None
    has_more: bool
    raw: Any = field(repr=False)

    @classmethod
    def from_payload(cls, payload: Any, *, page: int = 1) -> Page[Any]:
        envelope = _mapping(payload)
        data = _mapping(_pick(envelope, "Data", "ResultObject", default=envelope))
        page_count = _pick(data, "PageCount", "pageCount", "TotalPages", "totalPages")
        parsed_count = (
            int(page_count)
            if isinstance(page_count, int | str) and str(page_count).isdigit()
            else None
        )
        items = tuple(extract_items(payload))
        return cls(
            items=items,
            page=page,
            page_count=parsed_count,
            has_more=page < parsed_count if parsed_count is not None else bool(items),
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class WritePreview:
    operation: str
    target: str
    fields: Mapping[str, Any]
    destructive: bool
    idempotent: bool

    @property
    def digest(self) -> str:
        serialized = json.dumps(
            {
                "operation": self.operation,
                "target": self.target,
                "fields": self.fields,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class WriteResult:
    operation: str
    target: str
    success: bool
    data: Any
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    operation: str
    target: str
    outcome: str
    request_id: str | None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
