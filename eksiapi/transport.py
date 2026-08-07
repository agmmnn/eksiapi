"""Shared sync/async HTTP transport behavior."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .errors import (
    EksiApiError,
    EksiAuthenticationError,
    EksiNotFoundError,
    EksiRateLimitError,
    EksiTransportError,
)
from .models import RateLimitInfo


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry policy applied only when a request is explicitly marked retryable."""

    max_attempts: int = 3
    backoff_factor: float = 0.25
    max_backoff: float = 4.0
    retry_statuses: tuple[int, ...] = (408, 425, 502, 503, 504)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.backoff_factor < 0 or self.max_backoff < 0:
            raise ValueError("retry backoff values cannot be negative")

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_backoff)
        base = min(self.backoff_factor * (2 ** max(attempt - 1, 0)), self.max_backoff)
        return random.uniform(base * 0.5, base) if base else 0.0


def response_headers(response: Any) -> Mapping[str, Any]:
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, Mapping) else {}


def retry_after_seconds(headers: Mapping[str, Any]) -> float | None:
    value = next(
        (
            str(item)
            for key, item in headers.items()
            if str(key).lower() == "retry-after"
        ),
        None,
    )
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max((parsed - datetime.now(timezone.utc)).total_seconds(), 0.0)


def request_id_from_headers(headers: Mapping[str, Any]) -> str | None:
    for key, value in headers.items():
        if str(key).lower() in {"x-request-id", "request-id", "traceparent"}:
            return str(value)[:200]
    return None


def decode_response(response: Any) -> tuple[dict[str, Any], RateLimitInfo, str | None]:
    headers = response_headers(response)
    rate_limit = RateLimitInfo.from_headers(headers)
    request_id = request_id_from_headers(headers)
    status = int(getattr(response, "status_code", 0))
    kwargs = {"status_code": status, "request_id": request_id}
    if status in {401, 403}:
        raise EksiAuthenticationError(
            "Ekşi authentication failed or the session expired", **kwargs
        )
    if status == 404:
        raise EksiNotFoundError("Ekşi resource was not found", **kwargs)
    if status == 429:
        raise EksiRateLimitError(
            "Ekşi API rate limit was reached",
            retry_after=rate_limit.retry_after,
            **kwargs,
        )
    if status >= 400:
        message = f"Ekşi API returned HTTP {status}"
        try:
            body = response.json()
        except (TypeError, ValueError):
            body = {}
        api_message = None
        if isinstance(body, dict):
            api_message = (
                body.get("Message")
                or body.get("message")
                or body.get("error_description")
                or body.get("error")
            )
        if api_message:
            message = f"{message}: {str(api_message)[:300]}"
        raise EksiApiError(message, details=body, **kwargs)

    try:
        payload = response.json()
    except Exception as exc:
        raise EksiTransportError(
            "Ekşi API returned invalid JSON", status_code=status, request_id=request_id
        ) from exc
    if not isinstance(payload, dict):
        raise EksiTransportError(
            "Ekşi API returned an unexpected response shape",
            status_code=status,
            request_id=request_id,
        )
    return payload, rate_limit, request_id


class SyncTransport:
    def __init__(
        self, session: Any, *, timeout: float, retry_policy: RetryPolicy
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.retry_policy = retry_policy

    def request(self, method: str, url: str, *, retryable: bool, **kwargs: Any) -> Any:
        attempts = self.retry_policy.max_attempts if retryable else 1
        timeout = kwargs.pop("timeout", self.timeout)
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
            except Exception as exc:
                if attempt == attempts:
                    raise EksiTransportError(
                        f"Ekşi API request failed after {attempt} attempt(s)"
                    ) from exc
                time.sleep(self.retry_policy.delay(attempt))
                continue
            status = int(getattr(response, "status_code", 0))
            if status not in self.retry_policy.retry_statuses or attempt == attempts:
                return response
            time.sleep(
                self.retry_policy.delay(
                    attempt, retry_after_seconds(response_headers(response))
                )
            )
        raise AssertionError("retry loop did not return")


class AsyncTransport:
    def __init__(
        self, session: Any, *, timeout: float, retry_policy: RetryPolicy
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.retry_policy = retry_policy

    async def request(
        self, method: str, url: str, *, retryable: bool, **kwargs: Any
    ) -> Any:
        attempts = self.retry_policy.max_attempts if retryable else 1
        timeout = kwargs.pop("timeout", self.timeout)
        for attempt in range(1, attempts + 1):
            try:
                response = await self.session.request(
                    method, url, timeout=timeout, **kwargs
                )
            except Exception as exc:
                if attempt == attempts:
                    raise EksiTransportError(
                        f"Ekşi API request failed after {attempt} attempt(s)"
                    ) from exc
                await asyncio.sleep(self.retry_policy.delay(attempt))
                continue
            status = int(getattr(response, "status_code", 0))
            if status not in self.retry_policy.retry_statuses or attempt == attempts:
                return response
            await asyncio.sleep(
                self.retry_policy.delay(
                    attempt, retry_after_seconds(response_headers(response))
                )
            )
        raise AssertionError("retry loop did not return")


@dataclass(slots=True)
class MockResponse:
    """Small response object for deterministic library tests and fixture replay."""

    status_code: int
    payload: Any
    headers: Mapping[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class MockSession:
    """Session-compatible scripted transport; no network access is performed."""

    def __init__(self, responses: list[MockResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("MockSession has no scripted response left")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class AsyncMockSession(MockSession):
    async def request(self, method: str, url: str, **kwargs: Any) -> MockResponse:
        return super().request(method, url, **kwargs)

    async def close(self) -> None:
        self.closed = True
