from __future__ import annotations

import pytest

from eksiapi.mcp.credentials import CredentialError, create_authenticated_client


def test_environment_token_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.setenv("EKSI_CLIENT_SECRET", "secret")
    monkeypatch.delenv("EKSI_USERNAME", raising=False)
    monkeypatch.delenv("EKSI_PASSWORD", raising=False)

    client = create_authenticated_client()
    try:
        assert client.session.headers["Authorization"] == "Bearer token"
        assert client.session.headers["Client-Secret"] == "secret"
    finally:
        client.close()


def test_incomplete_environment_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EKSI_ACCESS_TOKEN", "token")
    monkeypatch.delenv("EKSI_CLIENT_SECRET", raising=False)
    with pytest.raises(CredentialError, match="must be set together"):
        create_authenticated_client()
