"""
Ekşi Sözlük API Client
Reverse-engineered from Android app v2.4.4 traffic.

Supports full standalone authentication — no Frida needed.
See eksi_auth.py for Api-Secret generation details.
"""

import json
import uuid
from curl_cffi import requests  # browser TLS fingerprint to bypass Cloudflare
from .auth import generate_api_secret

BASE        = "https://api.eksisozluk.com"
UA          = "eksisozluk-android/137"
IMPERSONATE = "chrome110"
DEVICE_MODEL   = "Google sdk_gphone_x86_64"
PLATFORM       = "g"
VERSION        = "2.4.4"
BUILD          = 137


class EksiClient:
    def __init__(self, access_token: str = None, client_secret: str = None):
        self.session = requests.Session(impersonate=IMPERSONATE)
        self.session.headers.update({"User-Agent": UA})
        if access_token and client_secret:
            self._set_auth(access_token, client_secret)

    def _set_auth(self, access_token: str, client_secret: str):
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Client-Secret": client_secret,
        })

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_server_time(self) -> int:
        """GET /v2/clientsettings/time → server timestamp in ms."""
        r = self.session.get(f"{BASE}/v2/clientsettings/time")
        r.raise_for_status()
        return r.json()["Data"]

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
        client_unique_id   = str(uuid.uuid4())
        self.session.headers.update({"Client-Secret": anon_client_secret})

        # Step 3: anonymous token
        anon_api_secret = generate_api_secret(server_time, anon_client_secret)
        anon_body = {
            "DeviceModel":    DEVICE_MODEL,
            "Platform":       PLATFORM,
            "Version":        VERSION,
            "Build":          BUILD,
            "Api-Secret":     anon_api_secret,
            "Client-Secret":  anon_client_secret,
            "ClientUniqueId": client_unique_id,
        }
        r = self.session.post(f"{BASE}/v2/account/anonymoustoken", data=anon_body)
        r.raise_for_status()
        anon_token = r.json().get("Data", {}).get("AccessToken", "")
        if anon_token:
            self.session.headers.update({"Authorization": f"Bearer {anon_token}"})

        # Step 4: real login — fresh server time + fresh client secret
        server_time2        = self._get_server_time()
        login_client_secret = str(uuid.uuid4())
        login_api_secret    = generate_api_secret(server_time2, login_client_secret)
        self.session.headers.update({"Client-Secret": login_client_secret})

        login_body = {
            "DeviceModel":    DEVICE_MODEL,
            "Platform":       PLATFORM,
            "Version":        VERSION,
            "Build":          BUILD,
            "grant_type":     "password",
            "username":       username,
            "password":       password,
            "Api-Secret":     login_api_secret,
            "Client-Secret":  login_client_secret,
            "ClientUniqueId": client_unique_id,
        }
        r = self.session.post(f"{BASE}/token", data=login_body)
        r.raise_for_status()
        token_data   = r.json()
        access_token = token_data.get("access_token") or token_data.get("AccessToken", "")
        self._set_auth(access_token, login_client_secret)
        return token_data

    def _get(self, path: str, params: dict = None):
        r = self.session.get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json_body=None, form_body=None, params=None):
        if json_body is not None:
            r = self.session.post(f"{BASE}{path}", json=json_body, params=params)
        else:
            r = self.session.post(f"{BASE}{path}", data=form_body, params=params)
        r.raise_for_status()
        return r.json()

    # ── User ─────────────────────────────────────────────────────────────────

    def me(self):
        """Authenticated user profile."""
        return self._get("/v2/user/me")

    def user(self, nick: str):
        """Public profile for any nick."""
        return self._get(f"/v2/user/{nick}/")

    def is_developer(self):
        return self._get("/v2/user/isdeveloper")

    # ── Entries ───────────────────────────────────────────────────────────────

    def entry(self, entry_id: int):
        """Single entry by id."""
        return self._get(f"/v2/entry/{entry_id}")

    def topic_entries(self, topic_slug: str, page: int = 1):
        """Entries for a topic (slug = url-encoded title or id)."""
        return self._get("/v2/entry/entriesbytopic", params={"title": topic_slug, "p": page})

    def user_entries(self, nick: str, page: int = 1):
        """Entries authored by a user."""
        return self._get(f"/v2/user/{nick}/entries", params={"p": page})

    def user_favorites(self, nick: str, page: int = 1):
        return self._get(f"/v2/user/{nick}/favorites", params={"p": page})

    # ── Index / Trending ──────────────────────────────────────────────────────

    def popular(self, page: int = 1, channel_filters: list = None):
        """Popular topics with optional channel filters."""
        if channel_filters is None:
            channel_filters = []
        return self._post("/v2/index/popular/", params={"p": page},
                          json_body={"Filters": channel_filters})

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
