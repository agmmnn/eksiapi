from __future__ import annotations

from typing import Any

import pytest

from eksiapi import (
    EksiApiError,
    EksiAuthenticationError,
    EksiClient,
    EksiNotFoundError,
    EksiRateLimitError,
    EksiTransportError,
    __version__,
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


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


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


def test_client_maps_not_found_and_server_message() -> None:
    client = EksiClient(session=FakeSession(FakeResponse(404, {})))
    with pytest.raises(EksiNotFoundError):
        client.me()

    client = EksiClient(
        session=FakeSession(FakeResponse(500, {"Message": "service unavailable"}))
    )
    with pytest.raises(EksiApiError, match="service unavailable"):
        client.me()


def test_client_maps_network_and_response_shape_errors() -> None:
    class BrokenSession(FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            raise OSError("offline")

    with pytest.raises(EksiTransportError, match="request failed"):
        EksiClient(session=BrokenSession()).me()

    with pytest.raises(EksiTransportError, match="unexpected response shape"):
        EksiClient(session=FakeSession(FakeResponse(200, []))).me()


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


def test_login_rejects_missing_access_token_and_bad_server_time() -> None:
    session = FakeSession(
        FakeResponse(200, {"Data": 1_800_000_000_000}),
        FakeResponse(200, {"Data": {}}),
        FakeResponse(200, {"Data": 1_800_000_000_100}),
        FakeResponse(200, {}),
    )
    with pytest.raises(EksiAuthenticationError, match="access token"):
        EksiClient(session=session).login("user", "password")

    with pytest.raises(EksiTransportError, match="server time"):
        EksiClient(session=FakeSession(FakeResponse(200, {"Data": "bad"}))).login(
            "user", "password"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"access_token": "token"},
        {"client_secret": "secret"},
        {"timeout": 0},
    ],
)
def test_client_rejects_invalid_configuration(kwargs) -> None:
    with pytest.raises(ValueError):
        EksiClient(**kwargs)


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


def test_endpoint_helpers_build_expected_requests() -> None:
    methods = [
        ("is_developer", (), {}),
        ("entry", (42,), {}),
        ("topic_entries", ("python",), {"page": 2}),
        ("user_entries", ("alice/bob",), {"page": 3}),
        ("user_favorites", ("alice",), {"page": 4}),
        ("popular", (), {"page": 5}),
        ("today", (), {"page": 6}),
        ("agenda", (), {"page": 7}),
        ("filter_channels", (), {}),
        ("search_topics", ("python",), {"page": 8}),
        ("autocomplete", ("py",), {}),
        ("search_entries", ("python",), {"page": 9}),
        ("notification_count", (), {}),
        ("notifications", (), {"page": 10}),
        ("unread_topic_count", (), {}),
        ("unread_message_authors", (), {}),
        ("channel_list", (), {}),
        ("server_time", (), {}),
        ("billing_status", (), {}),
    ]
    session = FakeSession(*(FakeResponse(200, {}) for _ in methods))
    client = EksiClient(session=session)

    for name, args, kwargs in methods:
        getattr(client, name)(*args, **kwargs)

    assert len(session.calls) == len(methods)
    assert "%2F" in session.calls[3][1]
    assert session.calls[5][2]["json"] == {"Filters": []}


def test_post_supports_form_body() -> None:
    session = FakeSession(FakeResponse(200, {}))
    client = EksiClient(session=session)
    client._post("/form", form_body={"x": 1})
    assert session.calls[0][2]["data"] == {"x": 1}
