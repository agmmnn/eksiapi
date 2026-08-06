"""
Ekşi Sözlük API Client
Reverse-engineered from Android app v2.4.4 traffic.

Supports full standalone authentication — no Frida needed.
See eksi_auth.py for Api-Secret generation details.
"""

from __future__ import annotations

import uuid
from typing import Any, Self
from urllib.parse import quote

from curl_cffi import requests  # browser TLS fingerprint to bypass Cloudflare

from .auth import generate_api_secret
from .errors import (
    EksiApiError,
    EksiAuthenticationError,
    EksiNotFoundError,
    EksiRateLimitError,
    EksiTransportError,
)

BASE = "https://api.eksisozluk.com"
UA = "eksisozluk-android/137"
IMPERSONATE = "chrome110"
DEVICE_MODEL = "Google sdk_gphone_x86_64"
PLATFORM = "g"
VERSION = "2.4.4"
BUILD = 137


class EksiClient:
    def __init__(
        self,
        access_token: str | None = None,
        client_secret: str | None = None,
        *,
        timeout: float = 30.0,
        base_url: str = BASE,
        session: Any | None = None,
    ):
        if bool(access_token) != bool(client_secret):
            raise ValueError("access_token and client_secret must be provided together")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session(impersonate=IMPERSONATE)
        self.session.headers.update({"User-Agent": UA})
        if access_token and client_secret:
            self._set_auth(access_token, client_secret)

    def _set_auth(self, access_token: str, client_secret: str):
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Client-Secret": client_secret,
            }
        )

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_server_time(self) -> int:
        """GET /v2/clientsettings/time → server timestamp in ms."""
        payload = self._request("GET", "/v2/clientsettings/time")
        try:
            return int(payload["Data"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EksiTransportError(
                "Ekşi API returned an invalid server time"
            ) from exc

    def login(self, username: str, password: str) -> dict:
        """
        Full login flow:
          1. GET server time
          2. Generate anonymous Client-Secret UUID
          3. POST /v2/account/anonymoustoken (with Api-Secret)
          4. POST /token with credentials (with Api-Secret)
        Returns the token response dict.
        """
        # Step 1: server time
        server_time = self._get_server_time()

        # Step 2: anonymous client secret
        anon_client_secret = str(uuid.uuid4())
        client_unique_id = str(uuid.uuid4())
        self.session.headers.update({"Client-Secret": anon_client_secret})

        # Step 3: anonymous token
        anon_api_secret = generate_api_secret(server_time, anon_client_secret)
        anon_body = {
            "DeviceModel": DEVICE_MODEL,
            "Platform": PLATFORM,
            "Version": VERSION,
            "Build": BUILD,
            "Api-Secret": anon_api_secret,
            "Client-Secret": anon_client_secret,
            "ClientUniqueId": client_unique_id,
        }
        anon_payload = self._request(
            "POST", "/v2/account/anonymoustoken", data=anon_body
        )
        anon_token = anon_payload.get("Data", {}).get("AccessToken", "")
        if anon_token:
            self.session.headers.update({"Authorization": f"Bearer {anon_token}"})

        # Step 4: real login — fresh server time + fresh client secret
        server_time2 = self._get_server_time()
        login_client_secret = str(uuid.uuid4())
        login_api_secret = generate_api_secret(server_time2, login_client_secret)
        self.session.headers.update({"Client-Secret": login_client_secret})

        login_body = {
            "DeviceModel": DEVICE_MODEL,
            "Platform": PLATFORM,
            "Version": VERSION,
            "Build": BUILD,
            "grant_type": "password",
            "username": username,
            "password": password,
            "Api-Secret": login_api_secret,
            "Client-Secret": login_client_secret,
            "ClientUniqueId": client_unique_id,
        }
        token_data = self._request("POST", "/token", data=login_body)
        access_token = token_data.get("access_token") or token_data.get(
            "AccessToken", ""
        )
        if not access_token:
            raise EksiAuthenticationError(
                "Ekşi login response did not contain an access token"
            )
        self._set_auth(access_token, login_client_secret)
        return token_data

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.request(method, f"{self.base_url}{path}", **kwargs)
        except Exception as exc:
            raise EksiTransportError("Ekşi API request failed") from exc

        status = int(getattr(response, "status_code", 0))
        if status in {401, 403}:
            raise EksiAuthenticationError(
                "Ekşi authentication failed or the session expired"
            )
        if status == 404:
            raise EksiNotFoundError("Ekşi resource was not found")
        if status == 429:
            raise EksiRateLimitError("Ekşi API rate limit was reached")
        if status >= 400:
            message = f"Ekşi API returned HTTP {status}"
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = {}
            api_message = body.get("Message") if isinstance(body, dict) else None
            if api_message:
                message = f"{message}: {str(api_message)[:300]}"
            raise EksiApiError(message)

        try:
            payload = response.json()
        except Exception as exc:
            raise EksiTransportError("Ekşi API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise EksiTransportError("Ekşi API returned an unexpected response shape")
        return payload

    def _get(self, path: str, params: dict[str, Any] | None = None):
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body=None, form_body=None, params=None):
        if json_body is not None:
            return self._request("POST", path, json=json_body, params=params)
        return self._request("POST", path, data=form_body, params=params)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        close = getattr(self.session, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # ── User ─────────────────────────────────────────────────────────────────

    def me(self):
        """Authenticated user profile."""
        return self._get("/v2/user/me")

    def user(self, nick: str):
        """Public profile for any nick."""
        return self._get(f"/v2/user/{quote(nick, safe='')}/")

    def is_developer(self):
        return self._get("/v2/user/isdeveloper")

    # ── Entries ───────────────────────────────────────────────────────────────

    def entry(self, entry_id: int):
        """Single entry by id."""
        return self._get(f"/v2/entry/{entry_id}")

    def topic_entries(self, topic_slug: str, page: int = 1):
        """Entries for a topic (slug = url-encoded title or id)."""
        return self._get(
            "/v2/entry/entriesbytopic", params={"title": topic_slug, "p": page}
        )

    def user_entries(self, nick: str, page: int = 1):
        """Entries authored by a user."""
        return self._get(f"/v2/user/{quote(nick, safe='')}/entries", params={"p": page})

    def user_favorites(self, nick: str, page: int = 1):
        return self._get(
            f"/v2/user/{quote(nick, safe='')}/favorites", params={"p": page}
        )

    # ── Index / Trending ──────────────────────────────────────────────────────

    def popular(self, page: int = 1, channel_filters: list[str] | None = None):
        """Popular topics with optional channel filters."""
        if channel_filters is None:
            channel_filters = []
        return self._post(
            "/v2/index/popular/",
            params={"p": page},
            json_body={"Filters": channel_filters},
        )

    def today(self, page: int = 1):
        """Today's topics (gündem)."""
        return self._get("/v2/index/today", params={"p": page})

    def agenda(self, page: int = 1):
        return self._get("/v2/entry/agenda", params={"p": page})

    def filter_channels(self):
        return self._get("/v2/index/getfilterchannels")

    # ── Search ────────────────────────────────────────────────────────────────

    def search_topics(self, query: str, page: int = 1):
        return self._get("/v2/topic/search", params={"searchTerm": query, "p": page})

    def autocomplete(self, query: str):
        return self._get("/v2/topic/autocomplete", params={"searchTerm": query})

    def search_entries(self, query: str, page: int = 1):
        return self._get("/v2/entry/search", params={"searchTerm": query, "p": page})

    # ── Notifications ─────────────────────────────────────────────────────────

    def notification_count(self):
        return self._get("/v2/notification/notificationcount")

    def notifications(self, page: int = 1):
        return self._get("/v2/notification/lastnotifications", params={"page": page})

    def unread_topic_count(self):
        return self._get("/v2/topic/unreadtopiccount")

    def unread_message_authors(self):
        return self._get("/v2/message/unreadthreadauthors")

    # ── Channels ──────────────────────────────────────────────────────────────

    def channel_list(self):
        return self._get("/v2/channel/list")

    # ── Misc ──────────────────────────────────────────────────────────────────

    def server_time(self):
        return self._get("/v2/clientsettings/time")

    def billing_status(self):
        return self._get("/v2/billing/subscription/status")
