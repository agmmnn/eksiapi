"""Credential loading and the interactive ``eksi-auth`` command."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from ..client import EksiClient
from ..errors import EksiApiError

SERVICE_NAME = "io.github.agmmnn.eksiapi"
KEYRING_ACCOUNT = "default"


class CredentialError(RuntimeError):
    """Credentials are absent, incomplete, or unavailable."""


@dataclass(frozen=True)
class StoredCredentials:
    username: str
    password: str


def _timeout_from_environment() -> float:
    raw = os.environ.get("EKSI_TIMEOUT", "30")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise CredentialError("EKSI_TIMEOUT must be a number") from exc
    if timeout <= 0:
        raise CredentialError("EKSI_TIMEOUT must be greater than zero")
    return timeout


def _expires_in_from_environment() -> float | None:
    raw = os.environ.get("EKSI_EXPIRES_IN")
    if raw is None:
        return None
    try:
        expires_in = float(raw)
    except ValueError as exc:
        raise CredentialError("EKSI_EXPIRES_IN must be a number") from exc
    if expires_in < 0:
        raise CredentialError("EKSI_EXPIRES_IN cannot be negative")
    return expires_in


def save_credentials(username: str, password: str) -> None:
    payload = json.dumps(
        {"version": 1, "username": username, "password": password},
        ensure_ascii=False,
    )
    try:
        keyring.set_password(SERVICE_NAME, KEYRING_ACCOUNT, payload)
    except KeyringError as exc:
        raise CredentialError("Could not save credentials to the OS keychain") from exc


def load_stored_credentials() -> StoredCredentials | None:
    try:
        payload = keyring.get_password(SERVICE_NAME, KEYRING_ACCOUNT)
    except KeyringError as exc:
        raise CredentialError(
            "Could not read credentials from the OS keychain"
        ) from exc
    if payload is None:
        return None
    try:
        data = json.loads(payload)
        username = str(data["username"])
        password = str(data["password"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CredentialError("Stored Ekşi credentials are invalid") from exc
    if not username or not password:
        raise CredentialError("Stored Ekşi credentials are incomplete")
    return StoredCredentials(username=username, password=password)


def delete_credentials() -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, KEYRING_ACCOUNT)
    except PasswordDeleteError:
        return False
    except KeyringError as exc:
        raise CredentialError(
            "Could not delete credentials from the OS keychain"
        ) from exc
    return True


def credential_source() -> str | None:
    access_token = os.environ.get("EKSI_ACCESS_TOKEN")
    client_secret = os.environ.get("EKSI_CLIENT_SECRET")
    if access_token or client_secret:
        if not access_token or not client_secret:
            raise CredentialError(
                "EKSI_ACCESS_TOKEN and EKSI_CLIENT_SECRET must be set together"
            )
        return "environment token"

    username = os.environ.get("EKSI_USERNAME")
    password = os.environ.get("EKSI_PASSWORD")
    if username or password:
        if not username or not password:
            raise CredentialError(
                "EKSI_USERNAME and EKSI_PASSWORD must be set together"
            )
        return "environment login"

    return "OS keychain" if load_stored_credentials() else None


def create_authenticated_client() -> EksiClient:
    """Create an authenticated client without exposing credentials to MCP tools."""
    timeout = _timeout_from_environment()
    access_token = os.environ.get("EKSI_ACCESS_TOKEN")
    client_secret = os.environ.get("EKSI_CLIENT_SECRET")
    if access_token or client_secret:
        if not access_token or not client_secret:
            raise CredentialError(
                "EKSI_ACCESS_TOKEN and EKSI_CLIENT_SECRET must be set together"
            )
        return EksiClient(
            access_token=access_token,
            client_secret=client_secret,
            refresh_token=os.environ.get("EKSI_REFRESH_TOKEN"),
            expires_in=_expires_in_from_environment(),
            account_nick=os.environ.get("EKSI_NICK"),
            client_unique_id=os.environ.get("EKSI_CLIENT_UNIQUE_ID"),
            timeout=timeout,
        )

    username = os.environ.get("EKSI_USERNAME")
    password = os.environ.get("EKSI_PASSWORD")
    if username or password:
        if not username or not password:
            raise CredentialError(
                "EKSI_USERNAME and EKSI_PASSWORD must be set together"
            )
    else:
        stored = load_stored_credentials()
        if stored is None:
            raise CredentialError(
                "No Ekşi credentials configured. Run `eksi-auth login` first."
            )
        username, password = stored.username, stored.password

    client = EksiClient(timeout=timeout)
    try:
        client.login(username, password)
    except Exception:
        client.close()
        raise
    return client


def create_default_client() -> EksiClient:
    """Use configured account credentials, otherwise fall back to anonymous reads."""
    if credential_source() is None:
        return EksiClient.anonymous(timeout=_timeout_from_environment())
    return create_authenticated_client()


def _login(username: str | None) -> int:
    username = username or input("Ekşi username/email: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        return 2
    password = getpass.getpass("Ekşi password: ")
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return 2

    client = EksiClient(timeout=_timeout_from_environment())
    try:
        client.login(username, password)
    except EksiApiError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    save_credentials(username, password)
    print("Credentials verified and saved to the OS keychain.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eksi-auth", description="Configure credentials for eksi-mcp."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    login_parser = subparsers.add_parser("login", help="Verify and store credentials")
    login_parser.add_argument("--username", help="Ekşi username or email")
    subparsers.add_parser("status", help="Show the active credential source")
    subparsers.add_parser("logout", help="Delete credentials from the OS keychain")
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            return _login(args.username)
        if args.command == "status":
            source = credential_source()
            print(
                f"Configured via {source}." if source else "No credentials configured."
            )
            return 0 if source else 1
        if args.command == "logout":
            deleted = delete_credentials()
            print(
                "Stored credentials deleted."
                if deleted
                else "No stored credentials found."
            )
            return 0
    except CredentialError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
