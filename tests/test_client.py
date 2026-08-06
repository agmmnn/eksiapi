from __future__ import annotations

from typing import Any

import pytest

from eksiapi import (
    EksiAuthenticationError,
    EksiClient,
    EksiRateLimitError,
    EksiTransportError,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_client_sets_auth_and_timeout() -> None:
    session = FakeSession(FakeResponse(200, {"Data": {"ok": True}}))
    client = EksiClient(
        access_token="token",
        client_secret="secret",
        timeout=12,
        session=session,
    )

    assert client.me() == {"Data": {"ok": True}}
    assert session.headers["Authorization"] == "Bearer token"
    assert session.headers["Client-Secret"] == "secret"
    assert session.calls[0][2]["timeout"] == 12


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, EksiAuthenticationError),
        (403, EksiAuthenticationError),
        (429, EksiRateLimitError),
    ],
)
def test_client_maps_safe_http_errors(status: int, error_type: type[Exception]) -> None:
    client = EksiClient(session=FakeSession(FakeResponse(status, {})))
    with pytest.raises(error_type):
        client.me()


def test_client_rejects_non_json_response() -> None:
    client = EksiClient(session=FakeSession(FakeResponse(200, ValueError("not json"))))
    with pytest.raises(EksiTransportError, match="invalid JSON"):
        client.me()


def test_login_flow_replaces_anonymous_auth() -> None:
    session = FakeSession(
        FakeResponse(200, {"Data": 1_800_000_000_000}),
        FakeResponse(200, {"Data": {"AccessToken": "anonymous"}}),
        FakeResponse(200, {"Data": 1_800_000_000_100}),
        FakeResponse(200, {"access_token": "account-token"}),
    )
    client = EksiClient(session=session)

    result = client.login("user", "password")

    assert result["access_token"] == "account-token"
    assert session.headers["Authorization"] == "Bearer account-token"
    assert session.headers["Client-Secret"]
    assert session.calls[-1][2]["data"]["password"] == "password"


def test_client_closes_in_context_manager() -> None:
    session = FakeSession()
    with EksiClient(session=session):
        pass
    assert session.closed is True


def test_user_path_segments_are_encoded() -> None:
    session = FakeSession(FakeResponse(200, {"Data": {}}))
    client = EksiClient(session=session)

    client.user("../token")

    assert session.calls[0][1].endswith("/v2/user/..%2Ftoken/")
