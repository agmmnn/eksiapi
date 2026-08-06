"""Console-script shims that keep MCP dependencies optional."""

from __future__ import annotations

import sys
from collections.abc import Callable

_OPTIONAL_MODULES = {"keyring", "mcp", "pydantic"}


def _load(command: str) -> Callable[[], int | None] | None:
    try:
        if command == "auth":
            from eksiapi.mcp.credentials import main
        else:
            from eksiapi.mcp.server import main
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".", 1)[0]
        if missing not in _OPTIONAL_MODULES:
            raise
        print(
            "The MCP dependencies are not installed. "
            "Install them with `pip install 'eksiapi[mcp]'`.",
            file=sys.stderr,
        )
        return None
    return main


def run_auth() -> int:
    """Run ``eksi-auth`` when the optional MCP dependencies are installed."""
    main = _load("auth")
    return 2 if main is None else int(main() or 0)


def run_mcp() -> int:
    """Run ``eksi-mcp`` when the optional MCP dependencies are installed."""
    main = _load("mcp")
    if main is None:
        return 2
    main()
    return 0
