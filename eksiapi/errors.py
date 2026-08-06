"""Public exception types raised by :mod:`eksiapi`."""


class EksiApiError(RuntimeError):
    """Base exception for Ekşi API failures."""


class EksiAuthenticationError(EksiApiError):
    """The supplied Ekşi credentials or token are not valid."""


class EksiNotFoundError(EksiApiError):
    """The requested Ekşi resource does not exist."""


class EksiRateLimitError(EksiApiError):
    """The Ekşi API rejected the request because of rate limiting."""


class EksiTransportError(EksiApiError):
    """The Ekşi API could not be reached or returned invalid data."""
