"""Asynchronous Ekşi Sözlük API client."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal
from urllib.parse import quote

from curl_cffi import requests

from .auth import generate_api_secret
from .client import BASE, DEFAULT_FINGERPRINT, _positive, _required_text
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
        return await self._get("/v2/user/me")

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

    async def topic_entries(self, topic_slug: str, page: int = 1) -> Any:
        return await self._get(
            "/v2/entry/entriesbytopic",
            params={
                "title": _required_text(topic_slug, "topic_slug", maximum=200),
                "p": page,
            },
        )

    async def user_entries(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/entries", params={"p": page})

    async def user_favorites(self, nick: str, page: int = 1) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/user/{nick}/favorites", params={"p": page})

    async def iter_topic_entries(
        self, topic_slug: str, *, start_page: int = 1, max_pages: int | None = None
    ) -> AsyncIterator[Any]:
        current = _positive(start_page, "start_page")
        fetched = 0
        while max_pages is None or fetched < max_pages:
            page = Page.from_payload(
                await self.topic_entries(topic_slug, current), page=current
            )
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
        return await self._get("/v2/entry/agenda", params={"p": page})

    async def filter_channels(self) -> Any:
        return await self._get("/v2/index/getfilterchannels")

    async def search_topics(self, query: str, page: int = 1) -> Any:
        return await self._get(
            "/v2/topic/search",
            params={
                "searchTerm": _required_text(query, "query", maximum=200),
                "p": page,
            },
        )

    async def search_entries(self, query: str, page: int = 1) -> Any:
        return await self._get(
            "/v2/entry/search",
            params={
                "searchTerm": _required_text(query, "query", maximum=200),
                "p": page,
            },
        )

    async def autocomplete(self, query: str) -> Any:
        return await self._get(
            "/v2/topic/autocomplete",
            params={"searchTerm": _required_text(query, "query", maximum=200)},
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

    async def archived_message_thread(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/message/archivethread/Nick/{nick}")

    async def message_recipient_info(self, nick: str) -> Any:
        nick = quote(_required_text(nick, "nick", maximum=60), safe="")
        return await self._get(f"/v2/message/recipientinfo/{nick}")

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
