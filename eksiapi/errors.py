"""Public exception types raised by :mod:`eksiapi`."""

from __future__ import annotations

from typing import Any


class EksiApiError(RuntimeError):
    """Base exception for Ekşi API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.details = details


class EksiAuthenticationError(EksiApiError):
    """The supplied Ekşi credentials or token are not valid."""


class EksiNotFoundError(EksiApiError):
    """The requested Ekşi resource does not exist."""


class EksiRateLimitError(EksiApiError):
    """The Ekşi API rejected the request because of rate limiting."""

    def __init__(
        self, message: str, *, retry_after: float | None = None, **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class EksiTransportError(EksiApiError):
    """The Ekşi API could not be reached or returned invalid data."""
