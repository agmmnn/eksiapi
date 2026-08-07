"""Console-script shims that keep MCP dependencies optional."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

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


def _run_auth(argv: Sequence[str] | None = None) -> int:
    """Run the credential command when optional MCP dependencies are installed."""
    main = _load("auth")
    if main is None:
        return 2
    return int((main() if argv is None else main(argv)) or 0)


def _run_mcp(argv: Sequence[str] | None = None) -> int:
    """Run the MCP server when optional MCP dependencies are installed."""
    main = _load("mcp")
    if main is None:
        return 2
    if argv is None:
        main()
    else:
        main(list(argv))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the unified ``eksiapi`` command."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="eksiapi", description="Ekşi Sözlük API client and MCP server."
    )
    parser.add_argument("--version", action="store_true", help="Show package version")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("auth", help="Manage account credentials", add_help=False)
    subparsers.add_parser("mcp", help="Run the local MCP server", add_help=False)
    subparsers.add_parser(
        "health", help="Test the API with anonymous reads", add_help=False
    )

    if arguments == ["--version"]:
        from eksiapi import __version__

        print(__version__)
        return 0
    if arguments and arguments[0] == "auth":
        return _run_auth(arguments[1:])
    if arguments and arguments[0] == "mcp":
        return _run_mcp(arguments[1:])
    if arguments and arguments[0] == "health":
        from eksiapi.health import main as run_health

        return run_health(arguments[1:])
    if arguments:
        parser.parse_args(arguments)
    parser.print_help()
    return 0
