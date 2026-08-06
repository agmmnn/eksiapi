from importlib.metadata import PackageNotFoundError, version

from .auth import generate_api_secret
from .client import EksiClient
from .errors import (
    EksiApiError,
    EksiAuthenticationError,
    EksiNotFoundError,
    EksiRateLimitError,
    EksiTransportError,
)

try:
    __version__ = version("eksiapi")
except PackageNotFoundError:  # pragma: no cover - only for unpackaged source trees
    __version__ = "0.0.0"

__all__ = [
    "EksiApiError",
    "EksiAuthenticationError",
    "EksiClient",
    "EksiNotFoundError",
    "EksiRateLimitError",
    "EksiTransportError",
    "__version__",
    "generate_api_secret",
]
