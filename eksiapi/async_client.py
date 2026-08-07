"""Asynchronous Ekşi Sözlük API client."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal
from urllib.parse import quote

from curl_cffi import requests

from .auth import generate_api_secret
from .client import (
    BASE,
    DEFAULT_FINGERPRINT,
    _positive,
    _required_text,
    _topic_id_from_query,
)
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
from .transport import AsyncTransport, RetryPolicy, decode_response

AuditSink = Callable[[AuditEvent], None]


class AsyncEksiClient:
    """Async counterpart of :class:`eksiapi.client.EksiClient`."""

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
            kwargs: dict[str, Any] = {
                "impersonate": self.fingerprint.tls_impersonate,
                "verify": verify,
            }
            if proxy:
                kwargs["proxy"] = proxy
            session = requests.AsyncSession(**kwargs)
        self.session = session
        self.session.headers.update({"User-Agent": self.fingerprint.user_agent})
        self.transport = AsyncTransport(
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
    def anonymous(cls, **kwargs: Any) -> AsyncEksiClient:
        """Create a client that obtains an anonymous bearer on its first read."""
        client = cls(**kwargs)
        client.auth_mode = "anonymous"
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
            access_token, client_secret, refresh_token, expires_at
        )
        self.auth_mode = mode
        if mode == "anonymous":
            self.account_nick = None

    def _auth_form(self, server_time: int, client_secret: str) -> dict[str, Any]:
        fp = self.fingerprint
        return {
            "DeviceModel": fp.device_model,
            "Platform": fp.platform,
            "Version": fp.version,
            "Build": fp.build,
            "Api-Secret": generate_api_secret(
                server_time, client_secret, app_build=fp.build
            ),
            "Client-Secret": client_secret,
        }

    async def _get_server_time(self, *, allow_refresh: bool = True) -> int:
        payload = await self._request(
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

    async def authenticate_anonymous(self) -> dict[str, Any]:
        """Obtain an anonymous app token without Ekşi account credentials."""
        client_secret = str(uuid.uuid4())
        self.session.headers.pop("Authorization", None)
        body = self._auth_form(
            await self._get_server_time(allow_refresh=False), client_secret
        )
        body["ClientUniqueId"] = self.client_unique_id
        self.session.headers.update({"Client-Secret": client_secret})
        payload = await self._request(
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

    async def login(self, username: str, password: str) -> dict[str, Any]:
        username = _required_text(username, "username", maximum=100)
        password = _required_text(password, "password", maximum=500)
        await self.authenticate_anonymous()
        login_secret = str(uuid.uuid4())
        body = self._auth_form(await self._get_server_time(), login_secret)
        body.update(
            {
                "grant_type": "password",
                "username": username,
                "password": password,
                "ClientUniqueId": self.client_unique_id,
            }
        )
        self.session.headers.update({"Client-Secret": login_secret})
        token = await self._request("POST", "/token", data=body, force_raw=True)
        self._capture_token(token, login_secret)
        return token

    def _capture_token(self, payload: Mapping[str, Any], client_secret: str) -> None:
        access_token = payload.get("access_token") or payload.get("AccessToken")
        if not access_token:
            raise EksiAuthenticationError(
                "Ekşi login response did not contain an access token"
            )
        refresh = payload.get("refresh_token") or payload.get("RefreshToken")
        expires = payload.get("expires_in") or payload.get("ExpiresIn")
        try:
            expires_at = time.time() + float(expires) if expires is not None else None
        except (TypeError, ValueError):
            expires_at = None
        self._set_auth(
            str(access_token),
            client_secret,
            refresh_token=str(refresh) if refresh else None,
            expires_at=expires_at,
            mode="account",
        )
        nick = payload.get("nick") or payload.get("Nick")
        if nick and str(nick).strip():
            self.account_nick = str(nick).strip()

    async def refresh_access_token(self) -> dict[str, Any]:
        if self.token_info is None or not self.token_info.refresh_token:
            raise EksiAuthenticationError("No refresh token is available")
        info = self.token_info
        body = self._auth_form(
            await self._get_server_time(allow_refresh=False), info.client_secret
        )
        body.update(
            {
                "grant_type": "refresh_token",
                "refresh_token": info.refresh_token,
                "ClientUniqueId": self.client_unique_id,
            }
        )
        payload = await self._request("POST", "/token", data=body, force_raw=True)
        self._capture_token(payload, info.client_secret)
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retryable: bool = False,
        force_raw: bool = False,
        allow_refresh: bool = True,
        **kwargs: Any,
    ) -> Any:
        authentication_path = path in {"/token", "/v2/account/anonymoustoken"}
        if (
            allow_refresh
            and not authentication_path
            and self.auth_mode == "anonymous"
            and self.token_info is None
        ):
            await self.authenticate_anonymous()
        if (
            allow_refresh
            and not authentication_path
            and self.token_info is not None
            and self.token_info.expired
        ):
            if self.token_info.refresh_token:
                await self.refresh_access_token()
            elif self.auth_mode == "anonymous":
                await self.authenticate_anonymous()
        response = await self.transport.request(
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
                await self.refresh_access_token()
                renewed = True
            elif self.auth_mode == "anonymous":
                await self.authenticate_anonymous()
                renewed = True
            if renewed:
                response = await self.transport.request(
                    method, f"{self.base_url}{path}", retryable=retryable, **kwargs
                )
        payload, self.last_rate_limit, self.last_request_id = decode_response(response)
        return payload if force_raw or self.raw_response else unwrap_response(payload)

    async def _get(self, path: str, params: Any = None) -> Any:
        return await self._request("GET", path, params=params, retryable=True)

    async def _post(
        self,
        path: str,
        *,
        json_body: Any = None,
        form_body: Any = None,
        params: Any = None,
        retryable: bool = False,
    ) -> Any:
        if json_body is not None:
            return await self._request(
                "POST", path, json=json_body, params=params, retryable=retryable
            )
        return await self._request(
            "POST", path, data=form_body, params=params, retryable=retryable
        )

    async def close(self) -> None:
        result = self.session.close()
        if result is not None:
            await result

    async def __aenter__(self) -> AsyncEksiClient:  # noqa: PYI034
        return self

    async def __aexit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None:
        await self.close()

    async def me(self) -> Any:
        """Return the logged-in account's public profile."""
        if self.auth_mode != "account":
            raise EksiAuthenticationError("me requires an authenticated Ekşi account")
        if not self.account_nick:
            raise EksiAuthenticationError(
                "Account nickname is unknown; pass account_nick when reusing a token"
            )
        return await self.user(self.account_nick)

    async def me_typed(self) -> User:
        payload = ApiResponse.from_payload(await self.me()).data
        return User.from_mapping(payload if isinstance(payload, Mapping) else {})

    async def user(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/")

    async def is_developer(self) -> Any:
        return await self._get("/v2/user/isdeveloper")

    async def entry(self, entry_id: int) -> Any:
        return await self._get(f"/v2/entry/{_positive(entry_id, 'entry_id')}")

    async def entry_typed(self, entry_id: int) -> Entry:
        payload = ApiResponse.from_payload(await self.entry(entry_id)).data
        return Entry.from_mapping(payload if isinstance(payload, Mapping) else {})

    async def query_topic(self, term: str) -> Any:
        """Resolve a title, slug or URL-like term using the app's topic router."""
        return await self._get(
            "/v2/topic/query/",
            params={"term": _required_text(term, "term", maximum=500)},
        )

    async def resolve_topic_id(self, term: str) -> int:
        """Resolve a title or slug directly to a numeric topic id."""
        return _topic_id_from_query(await self.query_topic(term))

    async def topic(
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
        return await self._get(path, params={"p": _positive(page, "page")})

    async def topic_typed(
        self,
        topic_id: int,
        page: int = 1,
        *,
        action: Literal["popular", "today"] | None = None,
    ) -> Topic:
        payload = ApiResponse.from_payload(
            await self.topic(topic_id, page, action=action)
        ).data
        return Topic.from_mapping(payload if isinstance(payload, Mapping) else {})

    async def topic_entries(self, topic: int | str, page: int = 1) -> Any:
        """Read entries by numeric topic id or resolve a title/slug first."""
        topic_id = (
            topic if isinstance(topic, int) else await self.resolve_topic_id(topic)
        )
        return await self.topic(_positive(topic_id, "topic"), page=page)

    async def topic_popular(self, topic_id: int, page: int = 1) -> Any:
        """Read a topic's popular entries."""
        return await self.topic(topic_id, page=page, action="popular")

    async def topic_today(self, topic_id: int, page: int = 1) -> Any:
        """Read entries added to a topic today."""
        return await self.topic(topic_id, page=page, action="today")

    async def user_entries(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/entries", params={"p": page})

    async def user_favorites(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/favorites", params={"p": page})

    def page(self, payload: Any, *, page: int = 1) -> Page[Any]:
        """Create a typed page view from an async client's response payload."""
        return Page.from_payload(payload, page=page)

    async def iter_topic_entries(
        self, topic: int | str, *, start_page: int = 1, max_pages: int | None = None
    ) -> AsyncIterator[Any]:
        topic_id = (
            topic if isinstance(topic, int) else await self.resolve_topic_id(topic)
        )
        current = _positive(start_page, "start_page")
        fetched = 0
        while max_pages is None or fetched < max_pages:
            page = Page.from_payload(await self.topic(topic_id, current), page=current)
            for item in page.items:
                yield item
            fetched += 1
            if not page.items or not page.has_more:
                break
            current += 1

    async def iter_user_entries(
        self, nick: str, *, start_page: int = 1, max_pages: int | None = None
    ) -> AsyncIterator[Any]:
        current = _positive(start_page, "start_page")
        fetched = 0
        while max_pages is None or fetched < max_pages:
            page = Page.from_payload(
                await self.user_entries(nick, current), page=current
            )
            for item in page.items:
                yield item
            fetched += 1
            if not page.items or not page.has_more:
                break
            current += 1

    async def popular(
        self, page: int = 1, channel_filters: list[str] | None = None
    ) -> Any:
        return await self._post(
            "/v2/index/popular/",
            params={"p": page},
            json_body={"Filters": channel_filters or []},
            retryable=True,
        )

    async def today(self, page: int = 1) -> Any:
        return await self._get("/v2/index/today", params={"p": page})

    async def agenda(self, page: int = 1) -> Any:
        """Return the account-only olay/agenda feed."""
        return await self._get("/v2/index/olay/", params={"p": _positive(page, "page")})

    async def filter_channels(self) -> Any:
        return await self._get("/v2/index/getfilterchannels")

    async def debe(self, page: int = 1) -> Any:
        """Return yesterday's most-liked entries feed."""
        return await self._get("/v2/index/debe/", params={"p": _positive(page, "page")})

    async def feed(
        self,
        kind: Literal["today", "popular", "debe", "agenda"],
        page: int = 1,
        *,
        channel_filters: list[str] | None = None,
    ) -> Any:
        """Read a named feed through one adapter-friendly method."""
        if kind == "popular":
            return await self.popular(page=page, channel_filters=channel_filters)
        if channel_filters:
            raise ValueError("channel_filters can only be used with the popular feed")
        if kind == "today":
            return await self.today(page=page)
        if kind == "debe":
            return await self.debe(page=page)
        return await self.agenda(page=page)

    async def search_topics(
        self,
        query: str,
        page: int = 1,
        *,
        sort_order: int = 1,
        favorited_only: bool = False,
        nice_only: bool = False,
    ) -> Any:
        """Search topic titles through the Android app's index search."""
        return await self._post(
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

    async def search_entries(self, topic_id: int, query: str, page: int = 1) -> Any:
        """Search entry bodies inside one topic."""
        return await self._post(
            "/v2/topic/search",
            params={"p": _positive(page, "page")},
            json_body={
                "TopicId": _positive(topic_id, "topic_id"),
                "Keywords": _required_text(query, "query", maximum=200),
            },
            retryable=True,
        )

    async def search_entries_advanced(
        self, topic_id: int, filters: Mapping[str, Any], page: int = 1
    ) -> Any:
        """Run the app's advanced entry search inside one topic."""
        body = dict(filters)
        body["TopicId"] = _positive(topic_id, "topic_id")
        return await self._post(
            "/v2/topic/search/advanced",
            params={"p": _positive(page, "page")},
            json_body=body,
            retryable=True,
        )

    async def autocomplete(self, query: str) -> Any:
        """Return title, query and nick suggestions for a partial term."""
        return await self._post(
            "/v2/autocomplete/query",
            form_body={"Term": _required_text(query, "query", maximum=200)},
            retryable=True,
        )

    async def autocomplete_nicks(self, query: str) -> Any:
        """Return nick suggestions for a partial term."""
        return await self._post(
            "/v2/autocomplete/nick",
            form_body={"Term": _required_text(query, "query", maximum=200)},
            retryable=True,
        )

    async def notification_count(self) -> Any:
        return await self._get("/v2/notification/notificationcount")

    async def notifications(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/notification/lastnotifications", params={"page": page}
        )

    async def unread_topic_count(self) -> Any:
        return await self._get("/v2/topic/unreadtopiccount")

    async def unread_message_authors(self) -> Any:
        return await self._get("/v2/message/unreadthreadauthors")

    async def message_thread(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/message/thread/Nick/{nick}", params={"p": page})

    async def archived_message_thread(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(
            f"/v2/message/archivethread/Nick/{nick}",
            params={"p": _positive(page, "page")},
        )

    async def message_archives(self, page: int = 1) -> Any:
        """List archived message conversations."""
        return await self._get(
            "/v2/message/archive", params={"p": _positive(page, "page")}
        )

    async def message_recipient_info(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/message/recipientinfo/{nick}")

    async def unread_message_thread(self, nick: str, *, mark_read: bool = False) -> Any:
        """Read a conversation from the unread-thread endpoint."""
        return await self._post(
            "/v2/message/thread/unread",
            form_body={
                "Nick": _required_text(nick, "nick", maximum=60),
                "MarkRead": mark_read,
            },
            retryable=not mark_read,
        )

    async def comments(self, entry_id: int, page: int = 1, size: int = 20) -> Any:
        """List comments attached to an entry."""
        return await self._get(
            f"/v2/comment/list/{_positive(entry_id, 'entry_id')}/",
            params={"page": _positive(page, "page"), "size": _positive(size, "size")},
        )

    async def entry_likes(self, entry_id: int) -> Any:
        return await self._entry_people(entry_id, "likes")

    async def entry_favorites(self, entry_id: int) -> Any:
        return await self._entry_people(entry_id, "favorites")

    async def entry_caylak_likes(self, entry_id: int) -> Any:
        return await self._entry_people(entry_id, "caylaklikes")

    async def entry_caylak_favorites(self, entry_id: int) -> Any:
        return await self._entry_people(entry_id, "caylakfavorites")

    async def _entry_people(self, entry_id: int, collection: str) -> Any:
        return await self._get(
            f"/v2/entry/{_positive(entry_id, 'entry_id')}/{collection}"
        )

    async def user_following(self, nick: str, page: int = 1) -> Any:
        return await self._user_paged_collection(nick, "following", page)

    async def user_followers(self, nick: str, page: int = 1) -> Any:
        return await self._user_paged_collection(nick, "followers", page)

    async def user_badges(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/badges")

    async def user_images(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(
            f"/v2/user/{nick}//images", params={"p": _positive(page, "page")}
        )

    async def _user_paged_collection(
        self, nick: str, collection: str, page: int
    ) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(
            f"/v2/user/{nick}/{collection}", params={"p": _positive(page, "page")}
        )

    async def buddies(self) -> Any:
        return await self._get("/v2/user/buddies")

    async def buddy_list(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/user/buddy-list", params={"p": _positive(page, "page")}
        )

    async def mute_list(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/user/mute-list", params={"p": _positive(page, "page")}
        )

    async def block_list(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/user/block-list", params={"p": _positive(page, "page")}
        )

    async def blocked_users(self) -> Any:
        return await self._get("/v2/user/blocks")

    async def index_title_blocks(self) -> Any:
        return await self._get("/v2/user/index-title-blocks")

    async def index_title_block_list(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/user/index-title-block-list",
            params={"p": _positive(page, "page")},
        )

    async def homepage_entries(self, page: int = 1) -> Any:
        return await self._get(
            "/v2/homepage/entries", params={"p": _positive(page, "page")}
        )

    async def offline_debe(self, from_date: str | None = None) -> Any:
        params = {"fromDate": from_date} if from_date else None
        return await self._get("/v2/index/offlinedebe", params=params)

    async def topic_creator_info(self, topic_id: int) -> Any:
        return await self._get(
            "/v2/topic/gettopiccreatorinfo/",
            params={"topicId": _positive(topic_id, "topic_id")},
        )

    async def user_channel_filters(self) -> Any:
        return await self._get("/v2/index/getuserchannelfilters")

    async def user_follow_approval_status(self) -> Any:
        return await self._get("/v2/user/is-follow-approvel")

    async def editable_entry(self, entry_id: int) -> Any:
        return await self._get(f"/v2/entry/edit/{_positive(entry_id, 'entry_id')}")

    async def channel_list(self) -> Any:
        return await self._get("/v2/channel/list")

    async def personal_settings(self) -> Any:
        return await self._get("/v2/settings/get/personal")

    async def preferences(self) -> Any:
        return await self._get("/v2/settings/get/preferences")

    async def notification_preferences(self) -> Any:
        return await self._get("/v2/pushnotification/getpreferences")

    async def trash(self, page: int = 1) -> Any:
        return await self._get("/v2/trash", params={"p": page})

    async def server_time(self) -> Any:
        return await self._get("/v2/clientsettings/time")

    async def billing_status(self) -> Any:
        return await self._get("/v2/billing/subscription/status")

    async def _write(
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
            payload = await self._post(path, json_body=json_body, form_body=form_body)
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

    async def create_entry(
        self, title: str, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        content = _required_text(content, "content", maximum=65_535)
        body = {"Title": title, "Content": content}
        return await self._write(
            "create_entry",
            "/v2/entry/add",
            target=title,
            fields=body,
            destructive=False,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    async def edit_entry(
        self, entry_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        content = _required_text(content, "content", maximum=65_535)
        body = {"Title": "", "Id": entry_id, "Content": content}
        return await self._write(
            "edit_entry",
            "/v2/entry/edit",
            target=str(entry_id),
            fields=body,
            destructive=True,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def delete_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "delete_entry",
            "/v2/entry/delete",
            "entry_id",
            entry_id,
            destructive=True,
            idempotent=False,
            dry_run=dry_run,
        )

    async def favorite_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "favorite_entry",
            "/v2/entry/favorite",
            "entry_id",
            entry_id,
            dry_run=dry_run,
        )

    async def unfavorite_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "unfavorite_entry",
            "/v2/entry/unfavorite",
            "entry_id",
            entry_id,
            dry_run=dry_run,
        )

    async def vote_entry(
        self, entry_id: int, rate: Literal[-1, 1], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        if rate not in {-1, 1}:
            raise ValueError("rate must be -1 or 1")
        body = {"Id": entry_id, "Rate": rate}
        return await self._write(
            "vote_entry",
            "/v2/entry/vote",
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def remove_entry_vote(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "remove_entry_vote",
            "/v2/entry/vote/remove",
            "entry_id",
            entry_id,
            dry_run=dry_run,
        )

    async def follow_topic(
        self, topic_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "follow_topic", "/v2/topic/follow", "topic_id", topic_id, dry_run=dry_run
        )

    async def unfollow_topic(
        self, topic_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._id_action(
            "unfollow_topic",
            "/v2/topic/unfollow",
            "topic_id",
            topic_id,
            dry_run=dry_run,
        )

    async def _id_action(
        self,
        operation: str,
        path: str,
        label: str,
        identifier: int,
        *,
        destructive: bool = False,
        idempotent: bool = True,
        dry_run: bool = False,
    ) -> WritePreview | WriteResult:
        identifier = _positive(identifier, label)
        body = {"Id": identifier}
        return await self._write(
            operation,
            path,
            target=str(identifier),
            fields=body,
            destructive=destructive,
            idempotent=idempotent,
            form_body=body,
            dry_run=dry_run,
        )

    async def send_message(
        self, to: str, message: str, *, thread_id: int = 0, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        to = _required_text(to, "to", maximum=60)
        message = _required_text(message, "message", maximum=10_000)
        if thread_id < 0:
            raise ValueError("thread_id cannot be negative")
        body = {"message": message, "to": to, "threadId": thread_id}
        return await self._write(
            "send_message",
            "/v2/message/sendmessage",
            target=to,
            fields=body,
            destructive=False,
            idempotent=False,
            json_body=body,
            dry_run=dry_run,
        )

    async def block_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action("block_user", "/v2/user/block", nick, dry_run)

    async def unblock_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action(
            "unblock_user", "/v2/user/unblock", nick, dry_run
        )

    async def follow_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action("follow_user", "/v2/user/follow", nick, dry_run)

    async def unfollow_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action(
            "unfollow_user", "/v2/user/unfollow", nick, dry_run
        )

    async def mute_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action("mute_user", "/v2/user/mute", nick, dry_run)

    async def unmute_user(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._nick_action(
            "unmute_user", "/v2/user/removemute", nick, dry_run
        )

    async def _nick_action(
        self, operation: str, path: str, nick: str, dry_run: bool
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return await self._write(
            operation,
            path,
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def mark_message_thread_read(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return await self._write(
            "mark_message_thread_read",
            "/v2/message/markread/nick",
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def add_comment(
        self, entry_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {
            "Id": _positive(entry_id, "entry_id"),
            "Content": _required_text(content, "content", maximum=10_000),
        }
        return await self._write(
            "add_comment",
            "/v2/comment/add",
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    async def edit_comment(
        self, comment_id: int, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {
            "Id": _positive(comment_id, "comment_id"),
            "Content": _required_text(content, "content", maximum=10_000),
        }
        return await self._write(
            "edit_comment",
            "/v2/comment/edit",
            target=str(comment_id),
            fields=body,
            destructive=True,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def delete_comment(
        self, comment_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._comment_action(
            "delete_comment",
            "/v2/comment/delete",
            comment_id,
            destructive=True,
            idempotent=False,
            dry_run=dry_run,
        )

    async def vote_comment(
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
        return await self._write(
            "vote_comment",
            "/v2/comment/vote/",
            target=str(comment_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def remove_comment_vote(
        self, comment_id: int, rate: Literal[-1, 1], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        comment_id = _positive(comment_id, "comment_id")
        if rate not in {-1, 1}:
            raise ValueError("rate must be -1 or 1")
        body = {"Id": comment_id, "Rate": rate}
        return await self._write(
            "remove_comment_vote",
            "/v2/comment/removevote/",
            target=str(comment_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def _comment_action(
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
        return await self._write(
            operation,
            path,
            target=str(comment_id),
            fields=body,
            destructive=destructive,
            idempotent=idempotent,
            form_body=body,
            dry_run=dry_run,
        )

    async def save_draft(
        self, title: str, content: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        content = _required_text(content, "content", maximum=65_535)
        body = {"Title": title, "Content": content}
        return await self._write(
            "save_draft",
            "/v2/topic/savedraft",
            target=title,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def delete_draft(
        self, title: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        title = _required_text(title, "title", maximum=200)
        body = {"Title": title}
        return await self._write(
            "delete_draft",
            "/v2/topic/deletedraft",
            target=title,
            fields=body,
            destructive=True,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    async def set_preferences(
        self, preferences: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not preferences:
            raise ValueError("preferences cannot be empty")
        body = dict(preferences)
        return await self._write(
            "set_preferences",
            "/v2/settings/set/preferences",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    async def set_channel_filters(
        self, filters: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not filters:
            raise ValueError("filters cannot be empty")
        body = dict(filters)
        return await self._write(
            "set_channel_filters",
            "/v2/index/setchannelfilter",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    async def set_notification_preferences(
        self, preferences: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        if not preferences:
            raise ValueError("preferences cannot be empty")
        body = dict(preferences)
        return await self._write(
            "set_notification_preferences",
            "/v2/pushnotification/setpreferences",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    async def register_push_notification(
        self, registration: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._push_registration_action(
            "register_push_notification",
            "/v2/pushnotification/register",
            registration,
            dry_run,
        )

    async def unregister_push_notification(
        self, registration: Mapping[str, Any], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._push_registration_action(
            "unregister_push_notification",
            "/v2/pushnotification/unregister",
            registration,
            dry_run,
        )

    async def _push_registration_action(
        self,
        operation: str,
        path: str,
        registration: Mapping[str, Any],
        dry_run: bool,
    ) -> WritePreview | WriteResult:
        if not registration:
            raise ValueError("registration cannot be empty")
        body = dict(registration)
        return await self._write(
            operation,
            path,
            target="device",
            fields=body,
            destructive=False,
            idempotent=True,
            json_body=body,
            dry_run=dry_run,
        )

    async def set_profile_biography(
        self, biography: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        body = {"BiographyText": _required_text(biography, "biography", maximum=2_000)}
        return await self._write(
            "set_profile_biography",
            "/v2/user/set-profile-biography",
            target="account",
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def remove_profile_biography(
        self, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._write(
            "remove_profile_biography",
            "/v2/user/remove-profile-biography",
            target="account",
            fields={},
            destructive=True,
            idempotent=True,
            dry_run=dry_run,
        )

    async def add_pinned_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._entry_state_action(
            "add_pinned_entry", "/v2/user/add-pinned-entry", entry_id, dry_run
        )

    async def remove_pinned_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._entry_state_action(
            "remove_pinned_entry", "/v2/user/remove-pinned-entry", entry_id, dry_run
        )

    async def _entry_state_action(
        self, operation: str, path: str, entry_id: int, dry_run: bool
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return await self._write(
            operation,
            path,
            target=str(entry_id),
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def block_index_titles(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._index_title_block_action(
            "block_index_titles", "/v2/user/indextitlesblock", nick, dry_run
        )

    async def unblock_index_titles(
        self, nick: str, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._index_title_block_action(
            "unblock_index_titles",
            "/v2/user/removeindextitlesblock",
            nick,
            dry_run,
        )

    async def _index_title_block_action(
        self, operation: str, path: str, nick: str, dry_run: bool
    ) -> WritePreview | WriteResult:
        nick = _required_text(nick, "nick", maximum=60)
        body = {"nick": nick}
        return await self._write(
            operation,
            path,
            target=nick,
            fields=body,
            destructive=False,
            idempotent=True,
            form_body=body,
            dry_run=dry_run,
        )

    async def delete_message_threads(
        self, threads: list[tuple[int, int]], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._message_threads_action(
            "delete_message_threads",
            "/v2/message/deleteprocessthread",
            threads,
            destructive=True,
            idempotent=False,
            dry_run=dry_run,
        )

    async def delete_message_archives(
        self, archive_ids: list[int], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        """Permanently delete conversations from the message archive."""
        if not archive_ids:
            raise ValueError("archive_ids cannot be empty")
        items = [
            {"ArchiveId": _positive(archive_id, "archive_id")}
            for archive_id in archive_ids
        ]
        body = {"ArchiveIdList": items}
        return await self._write(
            "delete_message_archives",
            "/v2/message/deleteprocessarchive",
            target=f"{len(items)} archive(s)",
            fields=body,
            destructive=True,
            idempotent=False,
            json_body=body,
            dry_run=dry_run,
        )

    async def archive_message_threads(
        self, threads: list[tuple[int, int]], *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._message_threads_action(
            "archive_message_threads",
            "/v2/message/archiveprocessthread",
            threads,
            destructive=False,
            idempotent=True,
            dry_run=dry_run,
        )

    async def _message_threads_action(
        self,
        operation: str,
        path: str,
        threads: list[tuple[int, int]],
        *,
        destructive: bool,
        idempotent: bool,
        dry_run: bool,
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
        return await self._write(
            operation,
            path,
            target=f"{len(items)} thread(s)",
            fields=body,
            destructive=destructive,
            idempotent=idempotent,
            json_body=body,
            dry_run=dry_run,
        )

    async def delete_trash_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._trash_id_action(
            "delete_trash_entry",
            "/v2/trash/delete",
            entry_id,
            destructive=True,
            dry_run=dry_run,
        )

    async def restore_trash_entry(
        self, entry_id: int, *, dry_run: bool = False
    ) -> WritePreview | WriteResult:
        return await self._trash_id_action(
            "restore_trash_entry",
            "/v2/trash/resurrect",
            entry_id,
            destructive=False,
            dry_run=dry_run,
        )

    async def _trash_id_action(
        self,
        operation: str,
        path: str,
        entry_id: int,
        *,
        destructive: bool,
        dry_run: bool,
    ) -> WritePreview | WriteResult:
        entry_id = _positive(entry_id, "entry_id")
        body = {"Id": entry_id}
        return await self._write(
            operation,
            path,
            target=str(entry_id),
            fields=body,
            destructive=destructive,
            idempotent=False,
            form_body=body,
            dry_run=dry_run,
        )

    async def empty_trash(self, *, dry_run: bool = False) -> WritePreview | WriteResult:
        return await self._write(
            "empty_trash",
            "/v2/trash/empty",
            target="all trash entries",
            fields={},
            destructive=True,
            idempotent=False,
            form_body={},
            dry_run=dry_run,
        )
