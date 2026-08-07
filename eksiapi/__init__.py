from importlib.metadata import PackageNotFoundError, version

from .async_client import AsyncEksiClient
from .auth import generate_api_secret
from .client import EksiClient
from .config import AndroidFingerprint
from .errors import (
    EksiApiError,
    EksiAuthenticationError,
    EksiNotFoundError,
    EksiRateLimitError,
    EksiTransportError,
)
from .models import Entry, Page, RateLimitInfo, User, WritePreview, WriteResult
from .transport import AsyncMockSession, MockResponse, MockSession, RetryPolicy

try:
    __version__ = version("eksiapi")
except PackageNotFoundError:  # pragma: no cover - only for unpackaged source trees
    __version__ = "0.0.0"

__all__ = [
    "AndroidFingerprint",
    "AsyncEksiClient",
    "AsyncMockSession",
    "EksiApiError",
    "EksiAuthenticationError",
    "EksiClient",
    "EksiNotFoundError",
    "EksiRateLimitError",
    "EksiTransportError",
    "Entry",
    "MockResponse",
    "MockSession",
    "Page",
    "RateLimitInfo",
    "RetryPolicy",
    "User",
    "WritePreview",
    "WriteResult",
    "__version__",
    "generate_api_secret",
]
