"""Synchronous Ekşi Sözlük API client.

The public read API remains compatible with the original dictionary-returning
client. Stage-three capabilities (refresh, safe retries, typed views and
pagination) and stage-five writes share the same transport primitives as the
async client.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from types import TracebackType
from typing import Any, Literal
from urllib.parse import quote

from curl_cffi import requests

from .auth import generate_api_secret
from .config import AndroidFingerprint
from .errors import EksiApiError, EksiAuthenticationError, EksiTransportError
from .formatting import unwrap_response
from .models import (
    ApiResponse,
    AuditEvent,
    Entry,
    Page,
    RateLimitInfo,
    TokenInfo,
    Topic,
    User,
    WritePreview,
    WriteResult,
)
from .transport import RetryPolicy, SyncTransport, decode_response

BASE = "https://api.eksisozluk.com"
DEFAULT_FINGERPRINT = AndroidFingerprint()
UA = DEFAULT_FINGERPRINT.user_agent
IMPERSONATE = DEFAULT_FINGERPRINT.tls_impersonate
DEVICE_MODEL = DEFAULT_FINGERPRINT.device_model
PLATFORM = DEFAULT_FINGERPRINT.platform
VERSION = DEFAULT_FINGERPRINT.version
BUILD = DEFAULT_FINGERPRINT.build

AuditSink = Callable[[AuditEvent], None]


def _required_text(value: str, label: str, *, maximum: int) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} cannot be empty")
    if len(value) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} characters")
    return value


def _positive(value: int, label: str) -> int:
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return value


def _topic_id_from_query(payload: Any) -> int:
    """Extract a topic id from raw or unwrapped topic-query responses."""
    current = payload
    if isinstance(current, Mapping) and isinstance(current.get("Data"), Mapping):
        current = current["Data"]
    if isinstance(current, Mapping) and isinstance(current.get("QueryData"), Mapping):
        current = current["QueryData"]
    value = current.get("TopicId") if isinstance(current, Mapping) else None
    if not isinstance(value, int | str) or not str(value).isdigit():
        raise EksiApiError("Ekşi API could not resolve the requested topic")
    return int(value)


class EksiClient:
    """Sync client supporting public reads and authenticated account actions."""

    def __init__(
        self,
        access_token: str | None = None,
        client_secret: str | None = None,
        *,
        refresh_token: str | None = None,
        expires_in: float | None = None,
        account_nick: str | None = None,
        client_unique_id: str | None = None,
        timeout: float = 30.0,
        base_url: str = BASE,
        session: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        proxy: str | None = None,
        verify: bool | str = True,
        fingerprint: AndroidFingerprint | None = None,
        raw_response: bool = True,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if bool(access_token) != bool(client_secret):
            raise ValueError("access_token and client_secret must be provided together")
        if refresh_token and not access_token:
            raise ValueError("refresh_token requires access_token and client_secret")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.client_unique_id = client_unique_id or str(uuid.uuid4())
        self.fingerprint = fingerprint or DEFAULT_FINGERPRINT
        if session is None:
            session_kwargs: dict[str, Any] = {
                "impersonate": self.fingerprint.tls_impersonate,
                "verify": verify,
            }
            if proxy:
                session_kwargs["proxy"] = proxy
            session = requests.Session(**session_kwargs)
        self.session = session
        self.session.headers.update({"User-Agent": self.fingerprint.user_agent})
        self.transport = SyncTransport(
            self.session,
            timeout=timeout,
            retry_policy=retry_policy or RetryPolicy(),
        )
        self.raw_response = raw_response
        self.audit_sink = audit_sink
        self.last_rate_limit = RateLimitInfo()
        self.last_request_id: str | None = None
        self.token_info: TokenInfo | None = None
        self.auth_mode: Literal["none", "anonymous", "account"] = "none"
        self.account_nick = account_nick.strip() if account_nick else None
        if access_token and client_secret:
            expires_at = time.time() + expires_in if expires_in is not None else None
            self._set_auth(
                access_token,
                client_secret,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )

    @classmethod
    def anonymous(cls, **kwargs: Any) -> EksiClient:
        """Create a client with an RSA-authenticated anonymous bearer token."""
        client = cls(**kwargs)
        try:
            client.authenticate_anonymous()
        except Exception:
            client.close()
            raise
        return client

    def _set_auth(
        self,
        access_token: str,
        client_secret: str,
        *,
        refresh_token: str | None = None,
        expires_at: float | None = None,
        mode: Literal["anonymous", "account"] = "account",
    ) -> None:
        self.session.headers.update(
            {"Authorization": f"Bearer {access_token}", "Client-Secret": client_secret}
        )
        self.token_info = TokenInfo(
            access_token=access_token,
            client_secret=client_secret,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        self.auth_mode = mode
        if mode == "anonymous":
            self.account_nick = None

    def _auth_form(self, server_time: int, client_secret: str) -> dict[str, Any]:
        fingerprint = self.fingerprint
        return {
            "DeviceModel": fingerprint.device_model,
            "Platform": fingerprint.platform,
            "Version": fingerprint.version,
            "Build": fingerprint.build,
            "Api-Secret": generate_api_secret(
                server_time, client_secret, app_build=fingerprint.build
            ),
            "Client-Secret": client_secret,
        }

    def _get_server_time(self, *, allow_refresh: bool = True) -> int:
        payload = self._request(
            "GET",
            "/v2/clientsettings/time",
            retryable=True,
            force_raw=True,
            allow_refresh=allow_refresh,
        )
        try:
            return int(payload["Data"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EksiTransportError(
                "Ekşi API returned an invalid server time"
            ) from exc

    def authenticate_anonymous(self) -> dict[str, Any]:
        """Obtain an anonymous app token without Ekşi account credentials."""
        client_secret = str(uuid.uuid4())
        self.session.headers.pop("Authorization", None)
        body = self._auth_form(
            self._get_server_time(allow_refresh=False), client_secret
        )
        body["ClientUniqueId"] = self.client_unique_id
        self.session.headers.update({"Client-Secret": client_secret})
        payload = self._request(
            "POST",
            "/v2/account/anonymoustoken",
            data=body,
            force_raw=True,
            allow_refresh=False,
        )
        data = payload.get("Data") if isinstance(payload.get("Data"), Mapping) else {}
        access_token = data.get("access_token") or data.get("AccessToken")
        if not access_token:
            raise EksiAuthenticationError(
                "Ekşi anonymous token response did not contain an access token"
            )
        expires_in = data.get("expires_in")
        if expires_in is None:
            expires_in = data.get("ExpiresIn")
        try:
            expires_at = (
                time.time() + float(expires_in) if expires_in is not None else None
            )
        except (TypeError, ValueError):
            expires_at = None
        self._set_auth(
            str(access_token),
            client_secret,
            expires_at=expires_at,
            mode="anonymous",
        )
        return payload

    def login(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate using the Android password grant and retain refresh metadata."""
        username = _required_text(username, "username", maximum=100)
        password = _required_text(password, "password", maximum=500)
        self.authenticate_anonymous()

        login_secret = str(uuid.uuid4())
        login_body = self._auth_form(self._get_server_time(), login_secret)
        login_body.update(
            {
                "grant_type": "password",
                "username": username,
                "password": password,
                "ClientUniqueId": self.client_unique_id,
            }
        )
        self.session.headers.update({"Client-Secret": login_secret})
        token_data = self._request("POST", "/token", data=login_body, force_raw=True)
        self._capture_token(token_data, login_secret)
        return token_data

    def _capture_token(self, payload: Mapping[str, Any], client_secret: str) -> None:
        access_token = payload.get("access_token") or payload.get("AccessToken")
        if not access_token:
            raise EksiAuthenticationError(
                "Ekşi login response did not contain an access token"
            )
        refresh_token = payload.get("refresh_token") or payload.get("RefreshToken")
        expires_in = payload.get("expires_in") or payload.get("ExpiresIn")
        try:
            expires_at = (
                time.time() + float(expires_in) if expires_in is not None else None
            )
        except (TypeError, ValueError):
            expires_at = None
        self._set_auth(
            str(access_token),
            client_secret,
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=expires_at,
            mode="account",
        )
        nick = payload.get("nick") or payload.get("Nick")
        if nick and str(nick).strip():
            self.account_nick = str(nick).strip()

    def refresh_access_token(self) -> dict[str, Any]:
        """Exchange the retained refresh token for a new access token."""
        if self.token_info is None or not self.token_info.refresh_token:
            raise EksiAuthenticationError("No refresh token is available")
        info = self.token_info
        body = self._auth_form(
            self._get_server_time(allow_refresh=False), info.client_secret
        )
        body.update(
            {
                "grant_type": "refresh_token",
                "refresh_token": info.refresh_token,
                "ClientUniqueId": self.client_unique_id,
            }
        )
        payload = self._request("POST", "/token", data=body, force_raw=True)
        self._capture_token(payload, info.client_secret)
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        retryable: bool = False,
        force_raw: bool = False,
        allow_refresh: bool = True,
        **kwargs: Any,
    ) -> Any:
        if (
            allow_refresh
            and path not in {"/token", "/v2/account/anonymoustoken"}
            and self.token_info is not None
            and self.token_info.expired
        ):
            if self.token_info.refresh_token:
                self.refresh_access_token()
            elif self.auth_mode == "anonymous":
                self.authenticate_anonymous()
        response = self.transport.request(
            method, f"{self.base_url}{path}", retryable=retryable, **kwargs
        )
        if (
            int(getattr(response, "status_code", 0)) == 401
            and allow_refresh
            and retryable
            and self.token_info is not None
        ):
            renewed = False
            if self.token_info.refresh_token:
                self.refresh_access_token()
                renewed = True
            elif self.auth_mode == "anonymous":
                self.authenticate_anonymous()
                renewed = True
            if renewed:
                response = self.transport.request(
                    method, f"{self.base_url}{path}", retryable=retryable, **kwargs
                )
        payload, self.last_rate_limit, self.last_request_id = decode_response(response)
        return payload if force_raw or self.raw_response else unwrap_response(payload)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params, retryable=True)

    def _post(
        self,
        path: str,
        json_body: Any = None,
        form_body: Any = None,
        params: Any = None,
        *,
        retryable: bool = False,
    ) -> Any:
        if json_body is not None:
            return self._request(
                "POST", path, json=json_body, params=params, retryable=retryable
            )
        return self._request(
            "POST", path, data=form_body, params=params, retryable=retryable
        )

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> EksiClient:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # Public and account reads
    def me(self) -> Any:
        """Return the logged-in account's public profile.

        The Android API has no dedicated ``/v2/user/me`` endpoint. Login
        responses include the account nick, which is resolved through the
        public profile endpoint instead.
        """
        if self.auth_mode != "account":
            raise EksiAuthenticationError("me requires an authenticated Ekşi account")
        if not self.account_nick:
            raise EksiAuthenticationError(
                "Account nickname is unknown; pass account_nick when reusing a token"
            )
        return self.user(self.account_nick)

    def me_typed(self) -> User:
        payload = ApiResponse.from_payload(self.me()).data
        return User.from_mapping(payload if isinstance(payload, Mapping) else {})

    def user(self, nick: str) -> Any:
        return self._get(
            f"/v2/user/{quote(_required_text(nick, 'nick', maximum=60), safe='')}/"
        )

    def is_developer(self) -> Any:
        return self._get("/v2/user/isdeveloper")

    def entry(self, entry_id: int) -> Any:
        return self._get(f"/v2/entry/{_positive(entry_id, 'entry_id')}")

    def entry_typed(self, entry_id: int) -> Entry:
        payload = ApiResponse.from_payload(self.entry(entry_id)).data
        return Entry.from_mapping(payload if isinstance(payload, Mapping) else {})

    def query_topic(self, term: str) -> Any:
        """Resolve a title, slug or URL-like term using the app's topic router."""
        return self._get(
            "/v2/topic/query/",
            params={"term": _required_text(term, "term", maximum=500)},
        )

    def resolve_topic_id(self, term: str) -> int:
        """Resolve a title or slug directly to a numeric topic id."""
        return _topic_id_from_query(self.query_topic(term))

    def topic(
        self,
        topic_id: int,
        page: int = 1,
        *,
        action: Literal["popular", "today"] | None = None,
    ) -> Any:
        """Read a topic page, optionally filtered to popular or today's entries."""
        topic_id = _positive(topic_id, "topic_id")
        path = f"/v2/topic/{topic_id}"
        if action is not None:
            path += f"/{action}"
        return self._get(path, params={"p": _positive(page, "page")})

    def topic_typed(
        self,
        topic_id: int,
        page: int = 1,
        *,
        action: Literal["popular", "today"] | None = None,
    ) -> Topic:
        payload = ApiResponse.from_payload(
            self.topic(topic_id, page, action=action)
        ).data
        return Topic.from_mapping(payload if isinstance(payload, Mapping) else {})

    def topic_entries(self, topic: int | str, page: int = 1) -> Any:
        """Read entries by numeric topic id or resolve a title/slug first."""
        topic_id = topic if isinstance(topic, int) else self.resolve_topic_id(topic)
        return self.topic(_positive(topic_id, "topic"), page=page)

    def topic_popular(self, topic_id: int, page: int = 1) -> Any:
        """Read a topic's popular entries."""
        return self.topic(topic_id, page=page, action="popular")

    def topic_today(self, topic_id: int, page: int = 1) -> Any:
        """Read entries added to a topic today."""
        return self.topic(topic_id, page=page, action="today")

    def user_entries(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(f"/v2/user/{nick}/entries", params={"p": page})

    def user_favorites(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(f"/v2/user/{nick}/favorites", params={"p": page})

    def page(self, payload: Any, *, page: int = 1) -> Page[Any]:
        return Page.from_payload(payload, page=page)

    def iter_topic_entries(
        self, topic: int | str, *, start_page: int = 1, max_pages: int | None = None
    ) -> Iterator[Any]:
        topic_id = topic if isinstance(topic, int) else self.resolve_topic_id(topic)
        yield from self._iterate_pages(
            lambda page: self.topic(topic_id, page=page),
            start_page,
            max_pages,
        )

    def iter_user_entries(
        self, nick: str, *, start_page: int = 1, max_pages: int | None = None
    ) -> Iterator[Any]:
        yield from self._iterate_pages(
            lambda page: self.user_entries(nick, page=page), start_page, max_pages
        )

    def _iterate_pages(
        self, fetch: Callable[[int], Any], start_page: int, max_pages: int | None
    ) -> Iterator[Any]:
        current = _positive(start_page, "start_page")
        fetched = 0
        while max_pages is None or fetched < max_pages:
            page = Page.from_payload(fetch(current), page=current)
            yield from page.items
            fetched += 1
            if not page.items or not page.has_more:
                break
            current += 1

    def popular(self, page: int = 1, channel_filters: list[str] | None = None) -> Any:
        return self._post(
            "/v2/index/popular/",
            params={"p": page},
            json_body={"Filters": channel_filters or []},
            retryable=True,
        )

    def today(self, page: int = 1) -> Any:
        return self._get("/v2/index/today", params={"p": page})

    def agenda(self, page: int = 1) -> Any:
        """Return the account-only olay/agenda feed."""
        return self._get("/v2/index/olay/", params={"p": _positive(page, "page")})

    def filter_channels(self) -> Any:
        return self._get("/v2/index/getfilterchannels")

    def debe(self, page: int = 1) -> Any:
        """Return yesterday's most-liked entries feed."""
        return self._get("/v2/index/debe/", params={"p": _positive(page, "page")})

    def feed(
        self,
        kind: Literal["today", "popular", "debe", "agenda"],
        page: int = 1,
        *,
        channel_filters: list[str] | None = None,
    ) -> Any:
        """Read a named feed through one adapter-friendly method."""
        if kind == "popular":
            return self.popular(page=page, channel_filters=channel_filters)
        if channel_filters:
            raise ValueError("channel_filters can only be used with the popular feed")
        if kind == "today":
            return self.today(page=page)
        if kind == "debe":
            return self.debe(page=page)
        return self.agenda(page=page)

    def search_topics(
        self,
        query: str,
        page: int = 1,
        *,
        sort_order: int = 1,
        favorited_only: bool = False,
        nice_only: bool = False,
    ) -> Any:
        """Search topic titles through the Android app's index search."""
        return self._post(
            "/v2/index/search/",
            params={"p": _positive(page, "page")},
            json_body={
                "Keywords": _required_text(query, "query", maximum=200),
                "SortOrder": sort_order,
                "FavoritedOnly": favorited_only,
                "NiceOnly": nice_only,
            },
            retryable=True,
        )

    def autocomplete(self, query: str) -> Any:
        """Return title, query and nick suggestions for a partial term."""
        return self._post(
            "/v2/autocomplete/query",
            form_body={"Term": _required_text(query, "query", maximum=200)},
            retryable=True,
        )

    def autocomplete_nicks(self, query: str) -> Any:
        """Return nick suggestions for a partial term."""
        return self._post(
            "/v2/autocomplete/nick",
            form_body={"Term": _required_text(query, "query", maximum=200)},
            retryable=True,
        )

    def search_entries(self, topic_id: int, query: str, page: int = 1) -> Any:
        """Search entry bodies inside one topic."""
        return self._post(
            "/v2/topic/search",
            params={"p": _positive(page, "page")},
            json_body={
                "TopicId": _positive(topic_id, "topic_id"),
                "Keywords": _required_text(query, "query", maximum=200),
            },
            retryable=True,
        )

    def search_entries_advanced(
        self, topic_id: int, filters: Mapping[str, Any], page: int = 1
    ) -> Any:
        """Run the app's advanced entry search inside one topic."""
        body = dict(filters)
        body["TopicId"] = _positive(topic_id, "topic_id")
        return self._post(
            "/v2/topic/search/advanced",
            params={"p": _positive(page, "page")},
            json_body=body,
            retryable=True,
        )

    def notification_count(self) -> Any:
        return self._get("/v2/notification/notificationcount")

    def notifications(self, page: int = 1) -> Any:
        return self._get("/v2/notification/lastnotifications", params={"page": page})

    def unread_topic_count(self) -> Any:
        return self._get("/v2/topic/unreadtopiccount")

    def unread_message_authors(self) -> Any:
        return self._get("/v2/message/unreadthreadauthors")

    def message_thread(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(f"/v2/message/thread/Nick/{nick}", params={"p": page})

    def archived_message_thread(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(
            f"/v2/message/archivethread/Nick/{nick}",
            params={"p": _positive(page, "page")},
        )

    def message_archives(self, page: int = 1) -> Any:
        """List archived message conversations."""
        return self._get("/v2/message/archive", params={"p": _positive(page, "page")})

    def message_recipient_info(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(f"/v2/message/recipientinfo/{nick}")

    def unread_message_thread(self, nick: str, *, mark_read: bool = False) -> Any:
        """Read a conversation from the unread-thread endpoint."""
        return self._post(
            "/v2/message/thread/unread",
            form_body={
                "Nick": _required_text(nick, "nick", maximum=60),
                "MarkRead": mark_read,
            },
            retryable=not mark_read,
        )

    def comments(self, entry_id: int, page: int = 1, size: int = 20) -> Any:
        """List comments attached to an entry."""
        return self._get(
            f"/v2/comment/list/{_positive(entry_id, 'entry_id')}/",
            params={"page": _positive(page, "page"), "size": _positive(size, "size")},
        )

    def entry_likes(self, entry_id: int) -> Any:
        return self._entry_people(entry_id, "likes")

    def entry_favorites(self, entry_id: int) -> Any:
        return self._entry_people(entry_id, "favorites")

    def entry_caylak_likes(self, entry_id: int) -> Any:
        return self._entry_people(entry_id, "caylaklikes")

    def entry_caylak_favorites(self, entry_id: int) -> Any:
        return self._entry_people(entry_id, "caylakfavorites")

    def _entry_people(self, entry_id: int, collection: str) -> Any:
        return self._get(f"/v2/entry/{_positive(entry_id, 'entry_id')}/{collection}")

    def user_following(self, nick: str, page: int = 1) -> Any:
        return self._user_paged_collection(nick, "following", page)

    def user_followers(self, nick: str, page: int = 1) -> Any:
        return self._user_paged_collection(nick, "followers", page)

    def user_badges(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(f"/v2/user/{nick}/badges")

    def user_images(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        # The double slash is present in the APK 2.4.10 Retrofit contract.
        return self._get(
            f"/v2/user/{nick}//images", params={"p": _positive(page, "page")}
        )

    def _user_paged_collection(self, nick: str, collection: str, page: int) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return self._get(
            f"/v2/user/{nick}/{collection}", params={"p": _positive(page, "page")}
        )

    def buddies(self) -> Any:
        return self._get("/v2/user/buddies")

    def buddy_list(self, page: int = 1) -> Any:
        return self._get("/v2/user/buddy-list", params={"p": _positive(page, "page")})

    def mute_list(self, page: int = 1) -> Any:
        return self._get("/v2/user/mute-list", params={"p": _positive(page, "page")})

    def block_list(self, page: int = 1) -> Any:
        return self._get("/v2/user/block-list", params={"p": _positive(page, "page")})

    def blocked_users(self) -> Any:
        return self._get("/v2/user/blocks")

    def index_title_blocks(self) -> Any:
        return self._get("/v2/user/index-title-blocks")

    def index_title_block_list(self, page: int = 1) -> Any:
        return self._get(
            "/v2/user/index-title-block-list",
            params={"p": _positive(page, "page")},
        )

    def homepage_entries(self, page: int = 1) -> Any:
        return self._get("/v2/homepage/entries", params={"p": _positive(page, "page")})

    def offline_debe(self, from_date: str | None = None) -> Any:
        params = {"fromDate": from_date} if from_date else None
        return self._get("/v2/index/offlinedebe", params=params)

    def topic_creator_info(self, topic_id: int) -> Any:
        return self._get(
            "/v2/topic/gettopiccreatorinfo/",
            params={"topicId": _positive(topic_id, "topic_id")},
        )

    def user_channel_filters(self) -> Any:
        return self._get("/v2/index/getuserchannelfilters")

    def user_follow_approval_status(self) -> Any:
        return self._get("/v2/user/is-follow-approvel")

    def editable_entry(self, entry_id: int) -> Any:
        return self._get(f"/v2/entry/edit/{_positive(entry_id, 'entry_id')}")

    def channel_list(self) -> Any:
        return self._get("/v2/channel/list")

    def personal_settings(self) -> Any:
        return self._get("/v2/settings/get/personal")

    def preferences(self) -> Any:
        return self._get("/v2/settings/get/preferences")

    def notification_preferences(self) -> Any:
        return self._get("/v2/pushnotification/getpreferences")

    def trash(self, page: int = 1) -> Any:
        return self._get("/v2/trash", params={"p": page})

    def server_time(self) -> Any:
        return self._get("/v2/clientsettings/time")

    def billing_status(self) -> Any:
        return self._get("/v2/billing/subscription/status")

    # Writes: never retry, support preview/dry-run, emit secret-free audit events.
    def _write(
        self,
        operation: str,
        path: str,
        *,
        target: str,
        fields: Mapping[str, Any],
        destructive: bool,
        idempotent: bool,
        json_body: Any = None,
        form_body: Any = None,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        preview = WritePreview(operation, target, dict(fields), destructive, idempotent)
        if dry_run:
            return preview
        if self.auth_mode == "anonymous":
            raise EksiAuthenticationError(
                f"{operation} requires an authenticated Ekşi account"
            )
        try:
            payload = self._post(path, json_body=json_body, form_body=form_body)
            envelope = (
                ApiResponse.from_payload(payload)
                if isinstance(payload, Mapping)
                else None
            )
            if envelope is not None and envelope.success is False:
                raise EksiApiError(envelope.message or f"{operation} was rejected")
            result = WriteResult(operation, target, True, payload, self.last_request_id)
            self._audit(operation, target, "success")
            return result
        except Exception:
            self._audit(operation, target, "failed")
            raise

    def _audit(self, operation: str, target: str, outcome: str) -> None:
        if self.audit_sink is not None:
            self.audit_sink(
                AuditEvent(operation, target, outcome, self.last_request_id)
            )

    def create_entry(
        self, title: str, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        content = _required_text(content, "content", maximum=65_535)
        fields = {"Title": title, "Content": content}
        return self._write(
            "create_entry",
            "/v2/entry/add",
            target=title,
            fields=fields,
            destructive=False,
            idempotent=False,
            form_body=fields,
            dry_run=dry_run,
        )

    def edit_entry(
        self, entry_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        content = _required_text(content, "content", maximum=65_535)
        body = {"Title": "", "Id": entry_id, "Content": content}
        return self._write(
            "edit_entry",
            "/v2/entry/edit",
            target=str(entry_id),
            fields=body,
            destructive=True,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def delete_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return self._write(
            "delete_entry",
            "/v2/entry/delete",
            target=str(entry_id),
            fields=body,
            destructive=True,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    def favorite_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._entry_action(
            "favorite_entry", "/v2/entry/favorite", entry_id, True, dry_run
        )

    def unfavorite_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._entry_action(
            "unfavorite_entry", "/v2/entry/unfavorite", entry_id, True, dry_run
        )

    def vote_entry(
        self, entry_id: int, rate: Literal[-1, 1], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        if rate not in {-1, 1}:
            raise ValueError("rate must be -1 or 1")
        body = {"Id": entry_id, "Rate": rate}
        return self._write(
            "vote_entry",
            "/v2/entry/vote",
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def remove_entry_vote(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._entry_action(
            "remove_entry_vote", "/v2/entry/vote/remove", entry_id, True, dry_run
        )

    def _entry_action(
        self, operation: str, path: str, entry_id: int, idempotent: bool, dry_run: bool
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return self._write(
            operation,
            path,
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=idempotent,
            form_body=body,
            dry_run=dry_run,
        )

    def follow_topic(
        self, topic_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._topic_action("follow_topic", "/v2/topic/follow", topic_id, dry_run)

    def unfollow_topic(
        self, topic_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._topic_action(
            "unfollow_topic", "/v2/topic/unfollow", topic_id, dry_run
        )

    def _topic_action(
        self, operation: str, path: str, topic_id: int, dry_run: bool
    ) -> WritePreview | WriteResult:
        topic_id = _positive(topic_id, "topic_id")
        body = {"Id": topic_id}
        return self._write(
            operation,
            path,
            target=str(topic_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def block_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("block_user", "/v2/user/block", nick, dry_run)

    def unblock_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("unblock_user", "/v2/user/unblock", nick, dry_run)

    def follow_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("follow_user", "/v2/user/follow", nick, dry_run)

    def unfollow_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("unfollow_user", "/v2/user/unfollow", nick, dry_run)

    def mute_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("mute_user", "/v2/user/mute", nick, dry_run)

    def unmute_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._user_action("unmute_user", "/v2/user/removemute", nick, dry_run)

    def _user_action(
        self, operation: str, path: str, nick: str, dry_run: bool
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return self._write(
            operation,
            path,
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def send_message(
        self, to: str, message: str, *, thread_id: int = 0, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        to = _required_text(to, "to", maximum=60)
        message = _required_text(message, "message", maximum=10_000)
        if thread_id < 0:
            raise ValueError("thread_id cannot be negative")
        body = {"message": message, "to": to, "threadId": thread_id}
        return self._write(
            "send_message",
            "/v2/message/sendmessage",
            target=to,
            fields=body,
            destructive=False,
            idempotent=False,
            json_body=body,
            dry_run=dry_run,
        )

    def mark_message_thread_read(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return self._write(
            "mark_message_thread_read",
            "/v2/message/markread/nick",
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def add_comment(
        self, entry_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {
            "Id": _positive(entry_id, "entry_id"),
            "Content": _required_text(content, "content", maximum=10_000),
        }
        return self._write(
            "add_comment",
            "/v2/comment/add",
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    def edit_comment(
        self, comment_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {
            "Id": _positive(comment_id, "comment_id"),
            "Content": _required_text(content, "content", maximum=10_000),
        }
        return self._write(
            "edit_comment",
            "/v2/comment/edit",
            target=str(comment_id),
            fields=body,
            destructive=True,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def delete_comment(
        self, comment_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._comment_action(
            "delete_comment",
            "/v2/comment/delete",
            comment_id,
            destructive=True,
            idempotent=False,
            dry_run=dry_run,
        )

    def vote_comment(
        self,
        comment_id: int,
        rate: Literal[-1, 1],
        *,
        owner_id: int | None = None,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        comment_id = _positive(comment_id, "comment_id")
        if rate not in {-1, 1}:
            raise ValueError("rate must be -1 or 1")
        body: dict[str, Any] = {"Id": comment_id, "Rate": rate}
        if owner_id is not None:
            body["Owner"] = _positive(owner_id, "owner_id")
        return self._write(
            "vote_comment",
            "/v2/comment/vote/",
            target=str(comment_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def remove_comment_vote(
        self, comment_id: int, rate: Literal[-1, 1], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        comment_id = _positive(comment_id, "comment_id")
        if rate not in {-1, 1}:
            raise ValueError("rate must be -1 or 1")
        body = {"Id": comment_id, "Rate": rate}
        return self._write(
            "remove_comment_vote",
            "/v2/comment/removevote/",
            target=str(comment_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def _comment_action(
        self,
        operation: str,
        path: str,
        comment_id: int,
        *,
        destructive: bool,
        idempotent: bool,
        dry_run: bool,
    ) -> WritePreview | WriteResult:
        comment_id = _positive(comment_id, "comment_id")
        body = {"Id": comment_id}
        return self._write(
            operation,
            path,
            target=str(comment_id),
            fields=body,
            destructive=destructive,
            idempotent=idempotent,
            form_body=body,
            dry_run=dry_run,
        )

    def save_draft(
        self, title: str, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        content = _required_text(content, "content", maximum=65_535)
        body = {"Title": title, "Content": content}
        return self._write(
            "save_draft",
            "/v2/topic/savedraft",
            target=title,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def delete_draft(
        self, title: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        body = {"Title": title}
        return self._write(
            "delete_draft",
            "/v2/topic/deletedraft",
            target=title,
            fields=body,
            destructive=True,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    def set_preferences(
        self, preferences: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not preferences:
            raise ValueError("preferences cannot be empty")
        body = dict(preferences)
        return self._write(
            "set_preferences",
            "/v2/settings/set/preferences",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    def set_channel_filters(
        self, filters: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not filters:
            raise ValueError("filters cannot be empty")
        body = dict(filters)
        return self._write(
            "set_channel_filters",
            "/v2/index/setchannelfilter",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    def set_notification_preferences(
        self, preferences: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not preferences:
            raise ValueError("preferences cannot be empty")
        body = dict(preferences)
        return self._write(
            "set_notification_preferences",
            "/v2/pushnotification/setpreferences",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    def register_push_notification(
        self, registration: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._push_registration_action(
            "register_push_notification",
            "/v2/pushnotification/register",
            registration,
            dry_run,
        )

    def unregister_push_notification(
        self, registration: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._push_registration_action(
            "unregister_push_notification",
            "/v2/pushnotification/unregister",
            registration,
            dry_run,
        )

    def _push_registration_action(
        self,
        operation: str,
        path: str,
        registration: Mapping[str, Any],
        dry_run: bool,
    ) -> WritePreview | WriteResult:
        if not registration:
            raise ValueError("registration cannot be empty")
        body = dict(registration)
        return self._write(
            operation,
            path,
            target="device",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    def set_profile_biography(
        self, biography: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {"BiographyText": _required_text(biography, "biography", maximum=2_000)}
        return self._write(
            "set_profile_biography",
            "/v2/user/set-profile-biography",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def remove_profile_biography(
        self, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._write(
            "remove_profile_biography",
            "/v2/user/remove-profile-biography",
            target="account",
            fields={},
            destructive=True,
            idempotent=True,
            dry_run=dry_run,
        )

    def add_pinned_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._entry_state_action(
            "add_pinned_entry", "/v2/user/add-pinned-entry", entry_id, dry_run
        )

    def remove_pinned_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._entry_state_action(
            "remove_pinned_entry", "/v2/user/remove-pinned-entry", entry_id, dry_run
        )

    def _entry_state_action(
        self, operation: str, path: str, entry_id: int, dry_run: bool
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return self._write(
            operation,
            path,
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def block_index_titles(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._index_title_block_action(
            "block_index_titles", "/v2/user/indextitlesblock", nick, dry_run
        )

    def unblock_index_titles(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return self._index_title_block_action(
            "unblock_index_titles",
            "/v2/user/removeindextitlesblock",
            nick,
            dry_run,
        )

    def _index_title_block_action(
        self, operation: str, path: str, nick: str, dry_run: bool
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return self._write(
            operation,
            path,
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    def delete_message_threads(
        self,
        threads: list[tuple[int, int]],
        *,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        if not threads:
            raise ValueError("threads cannot be empty")
        items = [
            {
                "ThreadId": _positive(thread_id, "thread_id"),
                "MaxMessageId": _positive(max_message_id, "max_message_id"),
            }
            for thread_id, max_message_id in threads
        ]
        body = {"ThreadIdList": items}
        return self._write(
            "delete_message_threads",
            "/v2/message/deleteprocessthread",
            target=f"{len(items)} thread(s)",
            fields=body,
            destructive=True,
            idempotent=False,
            json_body=body,
            dry_run=dry_run,
        )

    def delete_message_archives(
        self,
        archive_ids: list[int],
        *,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        """Permanently delete conversations from the message archive."""
        if not archive_ids:
            raise ValueError("archive_ids cannot be empty")
        items = [
            {"ArchiveId": _positive(archive_id, "archive_id")}
            for archive_id in archive_ids
        ]
        body = {"ArchiveIdList": items}
        return self._write(
            "delete_message_archives",
            "/v2/message/deleteprocessarchive",
            target=f"{len(items)} archive(s)",
            fields=body,
            destructive=True,
            idempotent=False,
            json_body=body,
            dry_run=dry_run,
        )

    def archive_message_threads(
        self,
        threads: list[tuple[int, int]],
        *,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        if not threads:
            raise ValueError("threads cannot be empty")
        items = [
            {
                "ThreadId": _positive(thread_id, "thread_id"),
                "MaxMessageId": _positive(max_message_id, "max_message_id"),
            }
            for thread_id, max_message_id in threads
        ]
        body = {"ThreadIdList": items}
        return self._write(
            "archive_message_threads",
            "/v2/message/archiveprocessthread",
            target=f"{len(items)} thread(s)",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    def delete_trash_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return self._write(
            "delete_trash_entry",
            "/v2/trash/delete",
            target=str(entry_id),
            fields=body,
            destructive=True,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    def empty_trash(self, *, dry_run: bool = False) -> WritePreview | WriteResult:
        return self._write(
            "empty_trash",
            "/v2/trash/empty",
            target="all trash entries",
            fields={},
            destructive=True,
            idempotent=False,
            form_body={},
            dry_run=dry_run,
        )

    def restore_trash_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return self._write(
            "restore_trash_entry",
            "/v2/trash/resurrect",
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )
