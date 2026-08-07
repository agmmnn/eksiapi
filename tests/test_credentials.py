from __future__ import annotations

import json
from typing import ClassVar

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from eksiapi.errors import EksiAuthenticationError
from eksiapi.mcp import credentials
from eksiapi.mcp.credentials import CredentialError, StoredCredentials


@pytest.fixture(autouse=True)
def clear_credential_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "EKSI_ACCESS_TOKEN",
        "EKSI_CLIENT_SECRET",
        "EKSI_REFRESH_TOKEN",
        "EKSI_EXPIRES_IN",
        "EKSI_CLIENT_UNIQUE_ID",
        "EKSI_NICK",
        "EKSI_USERNAME",
        "EKSI_PASSWORD",
        "EKSI_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_environment_token_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.setenv("EKSI_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EKSI_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("EKSI_EXPIRES_IN", "3600")
    monkeypatch.setenv("EKSI_CLIENT_UNIQUE_ID", "device-id")
    monkeypatch.setenv("EKSI_NICK", "alice")
    monkeypatch.delenv("EKSI_USERNAME", raising=False)
    monkeypatch.delenv("EKSI_PASSWORD", raising=False)

    client = credentials.create_authenticated_client()
    try:
        assert client.session.headers["Authorization"] == "Bearer token"
        assert client.session.headers["Client-Secret"] == "secret"
        assert client.token_info.refresh_token == "refresh"
        assert client.client_unique_id == "device-id"
        assert client.account_nick == "alice"
    finally:
        client.close()


def test_incomplete_environment_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.delenv("EKSI_CLIENT_SECRET", raising=False)
    with pytest.raises(CredentialError, match="must be set together"):
        credentials.create_authenticated_client()


@pytest.mark.parametrize("value", ["oops", "-1"])
def test_invalid_expires_in_is_rejected(monkeypatch, value) -> None:
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.setenv("EKSI_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EKSI_EXPIRES_IN", value)
    with pytest.raises(CredentialError, match="EKSI_EXPIRES_IN"):
        credentials.create_authenticated_client()


@pytest.mark.parametrize("value", ["oops", "0", "-1"])
def test_invalid_timeout_is_rejected(monkeypatch, value) -> None:
    monkeypatch.setenv("EKSI_TIMEOUT", value)
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.setenv("EKSI_CLIENT_SECRET", "secret")
    with pytest.raises(CredentialError, match="EKSI_TIMEOUT"):
        credentials.create_authenticated_client()


def test_keyring_round_trip_and_errors(monkeypatch) -> None:
    saved = {}
    monkeypatch.setattr(
        credentials.keyring,
        "set_password",
        lambda service, account, payload: saved.update(payload=payload),
    )
    credentials.save_credentials("alice", "secret")
    monkeypatch.setattr(
        credentials.keyring, "get_password", lambda service, account: saved["payload"]
    )
    assert credentials.load_stored_credentials() == StoredCredentials("alice", "secret")

    monkeypatch.setattr(
        credentials.keyring,
        "set_password",
        lambda *args: (_ for _ in ()).throw(KeyringError()),
    )
    with pytest.raises(CredentialError, match="save"):
        credentials.save_credentials("alice", "secret")

    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda *args: (_ for _ in ()).throw(KeyringError()),
    )
    with pytest.raises(CredentialError, match="read"):
        credentials.load_stored_credentials()


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"username": "alice"}),
        json.dumps({"username": "", "password": "x"}),
    ],
)
def test_invalid_stored_credentials_are_rejected(monkeypatch, payload) -> None:
    monkeypatch.setattr(credentials.keyring, "get_password", lambda *args: payload)
    with pytest.raises(CredentialError, match="invalid|incomplete"):
        credentials.load_stored_credentials()


def test_delete_credentials_results(monkeypatch) -> None:
    monkeypatch.setattr(credentials.keyring, "delete_password", lambda *args: None)
    assert credentials.delete_credentials() is True

    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda *args: (_ for _ in ()).throw(PasswordDeleteError()),
    )
    assert credentials.delete_credentials() is False

    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda *args: (_ for _ in ()).throw(KeyringError()),
    )
    with pytest.raises(CredentialError, match="delete"):
        credentials.delete_credentials()


def test_credential_source_precedence_and_validation(monkeypatch) -> None:
    monkeypatch.setattr(credentials, "load_stored_credentials", lambda: None)
    assert credentials.credential_source() is None

    monkeypatch.setenv("EKSI_USERNAME", "alice")
    with pytest.raises(CredentialError, match="must be set together"):
        credentials.credential_source()
    monkeypatch.setenv("EKSI_PASSWORD", "secret")
    assert credentials.credential_source() == "environment login"

    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    with pytest.raises(CredentialError, match="must be set together"):
        credentials.credential_source()
    monkeypatch.setenv("EKSI_CLIENT_SECRET", "client-secret")
    assert credentials.credential_source() == "environment token"


class FakeLoginClient:
    instances: ClassVar[list[FakeLoginClient]] = []

    def __init__(self, *, timeout=30) -> None:
        self.timeout = timeout
        self.login_args = None
        self.closed = False
        self.__class__.instances.append(self)

    def login(self, username, password) -> None:
        self.login_args = (username, password)

    def close(self) -> None:
        self.closed = True


def test_login_client_uses_environment_and_keyring(monkeypatch) -> None:
    FakeLoginClient.instances.clear()
    monkeypatch.setattr(credentials, "EksiClient", FakeLoginClient)
    monkeypatch.setenv("EKSI_USERNAME", "alice")
    monkeypatch.setenv("EKSI_PASSWORD", "secret")
    monkeypatch.setenv("EKSI_TIMEOUT", "4.5")

    client = credentials.create_authenticated_client()
    assert client.login_args == ("alice", "secret")
    assert client.timeout == 4.5

    monkeypatch.delenv("EKSI_USERNAME")
    monkeypatch.delenv("EKSI_PASSWORD")
    monkeypatch.setattr(
        credentials,
        "load_stored_credentials",
        lambda: StoredCredentials("bob", "stored"),
    )
    assert credentials.create_authenticated_client().login_args == ("bob", "stored")


def test_login_client_closes_after_failed_login(monkeypatch) -> None:
    class FailingClient(FakeLoginClient):
        def login(self, username, password) -> None:
            raise EksiAuthenticationError("denied")

    monkeypatch.setattr(credentials, "EksiClient", FailingClient)
    monkeypatch.setenv("EKSI_USERNAME", "alice")
    monkeypatch.setenv("EKSI_PASSWORD", "bad")
    with pytest.raises(EksiAuthenticationError):
        credentials.create_authenticated_client()
    assert FailingClient.instances[-1].closed is True


def test_missing_credentials_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(credentials, "load_stored_credentials", lambda: None)
    with pytest.raises(CredentialError, match="eksi-auth login"):
        credentials.create_authenticated_client()


def test_default_client_uses_anonymous_mode_without_credentials(monkeypatch) -> None:
    sentinel = object()
    calls = []
    monkeypatch.setattr(credentials, "credential_source", lambda: None)
    monkeypatch.setattr(credentials, "_timeout_from_environment", lambda: 12.5)
    monkeypatch.setattr(
        credentials.EksiClient,
        "anonymous",
        classmethod(lambda cls, **kwargs: calls.append(kwargs) or sentinel),
    )

    assert credentials.create_default_client() is sentinel
    assert calls == [{"timeout": 12.5}]


def test_default_client_prefers_configured_account(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(credentials, "credential_source", lambda: "OS keychain")
    monkeypatch.setattr(credentials, "create_authenticated_client", lambda: sentinel)

    assert credentials.create_default_client() is sentinel


def test_auth_command_status_logout_and_login(monkeypatch, capsys) -> None:
    monkeypatch.setattr(credentials, "credential_source", lambda: "OS keychain")
    assert credentials.main(["status"]) == 0
    assert "OS keychain" in capsys.readouterr().out

    monkeypatch.setattr(credentials, "delete_credentials", lambda: False)
    assert credentials.main(["logout"]) == 0
    assert "No stored" in capsys.readouterr().out

    monkeypatch.setattr(credentials, "_login", lambda username: 3)
    assert credentials.main(["login", "--username", "alice"]) == 3


def test_interactive_login_validation_and_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert credentials._login(None) == 2
    assert "Username" in capsys.readouterr().err

    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "")
    assert credentials._login("alice") == 2
    assert "Password" in capsys.readouterr().err

    fake = FakeLoginClient()
    monkeypatch.setattr(credentials, "EksiClient", lambda **kwargs: fake)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "secret")
    saved = []
    monkeypatch.setattr(
        credentials, "save_credentials", lambda *args: saved.append(args)
    )
    assert credentials._login("alice") == 0
    assert saved == [("alice", "secret")]
    assert fake.closed is True


def test_interactive_login_reports_api_error(monkeypatch, capsys) -> None:
    class FailingClient(FakeLoginClient):
        def login(self, username, password) -> None:
            raise EksiAuthenticationError("denied")

    fake = FailingClient()
    monkeypatch.setattr(credentials, "EksiClient", lambda **kwargs: fake)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "bad")
    assert credentials._login("alice") == 1
    assert "Login failed" in capsys.readouterr().err
    assert fake.closed is True
