from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eksiapi import (
    AsyncEksiClient,
    AsyncMockSession,
    EksiApiError,
    EksiAuthenticationError,
    EksiClient,
    MockResponse,
    MockSession,
    RetryPolicy,
)
from eksiapi.models import Entry, WritePreview

FIXTURES = Path(__file__).parent / "fixtures" / "apk-2.4.10"


def test_safe_get_retries_and_write_never_retries() -> None:
    session = MockSession(
        [MockResponse(503, {}), MockResponse(200, {"Data": {"ok": True}})]
    )
    client = EksiClient(
        session=session,
        retry_policy=RetryPolicy(max_attempts=2, backoff_factor=0),
    )
    assert client.me()["Data"]["ok"] is True
    assert len(session.calls) == 2

    write_session = MockSession([MockResponse(503, {"Message": "temporary"})])
    client = EksiClient(session=write_session)
    with pytest.raises(EksiApiError, match="temporary"):
        client.favorite_entry(1)
    assert len(write_session.calls) == 1


def test_recorded_apk_fixtures_are_valid_and_sanitized() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert files
    for fixture in files:
        text = fixture.read_text(encoding="utf-8")
        assert isinstance(json.loads(text), dict)
        lowered = text.lower()
        assert "authorization" not in lowered
        assert "access_token" not in lowered
        assert "client-secret" not in lowered
        assert "password" not in lowered


def test_refresh_rate_limit_raw_mode_and_typed_entry() -> None:
    session = MockSession(
        [
            MockResponse(200, {"Data": 1_900_000_000_000}),
            MockResponse(
                200,
                {
                    "access_token": "new-token",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                },
            ),
            MockResponse(
                200,
                {"Data": {"EntryId": 42, "Content": "text", "Author": "alice"}},
                headers={
                    "X-RateLimit-Limit": "60",
                    "X-RateLimit-Remaining": "59",
                    "X-Request-Id": "req-1",
                },
            ),
        ]
    )
    client = EksiClient(
        "old-token",
        "client-secret",
        refresh_token="refresh",
        expires_in=0,
        session=session,
    )
    entry = client.entry_typed(42)
    assert isinstance(entry, Entry)
    assert entry.id == 42
    assert client.token_info.access_token == "new-token"
    assert session.calls[1][2]["data"]["grant_type"] == "refresh_token"
    assert client.last_rate_limit.remaining == 59
    assert client.last_request_id == "req-1"

    unwrapped = EksiClient(
        session=MockSession([MockResponse(200, {"Data": {"value": 1}})]),
        raw_response=False,
    )
    assert unwrapped.me() == {"value": 1}


def test_pagination_iterator_and_write_preview_audit() -> None:
    session = MockSession(
        [
            MockResponse(
                200,
                {"Data": {"Entries": [{"EntryId": 1}], "PageCount": 2}},
            ),
            MockResponse(
                200,
                {"Data": {"Entries": [{"EntryId": 2}], "PageCount": 2}},
            ),
            MockResponse(200, {"Success": True, "Data": True}),
        ]
    )
    audits = []
    client = EksiClient(session=session, audit_sink=audits.append)
    assert [item["EntryId"] for item in client.iter_topic_entries("python")] == [1, 2]

    preview = client.create_entry("başlık", "içerik", dry_run=True)
    assert isinstance(preview, WritePreview)
    assert preview.digest
    assert len(session.calls) == 2
    result = client.create_entry("başlık", "içerik")
    assert result.success is True
    assert audits[0].operation == "create_entry"
    assert not hasattr(audits[0], "access_token")


def test_write_rejects_unsuccessful_api_envelope_and_audits_failure() -> None:
    audits = []
    client = EksiClient(
        session=MockSession(
            [MockResponse(200, {"Success": False, "Message": "rejected"})]
        ),
        audit_sink=audits.append,
    )
    with pytest.raises(EksiApiError, match="rejected"):
        client.favorite_entry(1)
    assert audits[0].outcome == "failed"


def test_async_client_uses_shared_behavior() -> None:
    async def run() -> None:
        session = AsyncMockSession(
            [
                MockResponse(502, {}),
                MockResponse(200, {"Data": {"EntryId": 7, "Content": "async"}}),
                MockResponse(200, {"Success": True, "Data": True}),
            ]
        )
        client = AsyncEksiClient(
            session=session,
            retry_policy=RetryPolicy(max_attempts=2, backoff_factor=0),
        )
        assert (await client.entry_typed(7)).id == 7
        preview = await client.favorite_entry(7, dry_run=True)
        assert isinstance(preview, WritePreview)
        assert (await client.favorite_entry(7)).success is True
        await client.close()
        assert session.closed is True

    asyncio.run(run())


def test_async_anonymous_client_bootstraps_renews_and_blocks_writes() -> None:
    async def run() -> None:
        session = AsyncMockSession(
            [
                MockResponse(200, {"Data": 1_900_000_000_000}),
                MockResponse(
                    200,
                    {
                        "Data": {
                            "access_token": "first-token",
                            "expires_in": 3600,
                        }
                    },
                ),
                MockResponse(401, {}),
                MockResponse(200, {"Data": 1_900_000_000_100}),
                MockResponse(
                    200,
                    {
                        "Data": {
                            "access_token": "second-token",
                            "expires_in": 3600,
                        }
                    },
                ),
                MockResponse(200, {"Data": {"EntryId": 9}}),
            ]
        )
        client = AsyncEksiClient.anonymous(session=session)

        assert client.auth_mode == "anonymous"
        assert session.calls == []
        assert (await client.entry(9))["Data"]["EntryId"] == 9
        assert session.headers["Authorization"] == "Bearer second-token"
        with pytest.raises(EksiAuthenticationError, match="authenticated Ekşi account"):
            await client.favorite_entry(9)
        await client.close()
        assert session.closed is True

    asyncio.run(run())


def test_sync_account_surface_builds_apk_requests_without_network_on_preview() -> None:
    session = MockSession([MockResponse(200, {}) for _ in range(8)])
    client = EksiClient(session=session)
    client.message_thread("a/b", 2)
    client.archived_message_thread("a/b")
    client.message_recipient_info("a/b")
    client.editable_entry(9)
    client.personal_settings()
    client.preferences()
    client.notification_preferences()
    client.trash(3)

    previews = [
        client.edit_entry(1, "new", dry_run=True),
        client.delete_entry(1, dry_run=True),
        client.unfavorite_entry(1, dry_run=True),
        client.vote_entry(1, -1, dry_run=True),
        client.remove_entry_vote(1, dry_run=True),
        client.follow_topic(2, dry_run=True),
        client.unfollow_topic(2, dry_run=True),
        client.block_user("nick", dry_run=True),
        client.unblock_user("nick", dry_run=True),
        client.follow_user("nick", dry_run=True),
        client.unfollow_user("nick", dry_run=True),
        client.mute_user("nick", dry_run=True),
        client.unmute_user("nick", dry_run=True),
        client.send_message("nick", "hello", dry_run=True),
        client.mark_message_thread_read("nick", dry_run=True),
        client.save_draft("title", "body", dry_run=True),
        client.delete_draft("title", dry_run=True),
        client.set_preferences({"theme": "dark"}, dry_run=True),
        client.delete_message_threads([(1, 2)], dry_run=True),
        client.archive_message_threads([(1, 2)], dry_run=True),
        client.delete_trash_entry(1, dry_run=True),
        client.empty_trash(dry_run=True),
        client.restore_trash_entry(1, dry_run=True),
    ]
    assert all(isinstance(item, WritePreview) for item in previews)
    assert "%2F" in session.calls[0][1]


def test_validation_rejects_unsafe_write_inputs() -> None:
    client = EksiClient(session=MockSession([]))
    invalid_calls = [
        lambda: client.create_entry("", "body", dry_run=True),
        lambda: client.vote_entry(1, 0, dry_run=True),
        lambda: client.send_message("nick", "body", thread_id=-1, dry_run=True),
        lambda: client.set_preferences({}, dry_run=True),
        lambda: client.delete_message_threads([], dry_run=True),
        lambda: client.archive_message_threads([], dry_run=True),
    ]
    for call in invalid_calls:
        with pytest.raises(ValueError):
            call()


def test_async_login_refresh_reads_and_previews() -> None:
    async def run() -> None:
        session = AsyncMockSession(
            [
                MockResponse(200, {"Data": 1_900_000_000_000}),
                MockResponse(200, {"Data": {"AccessToken": "anon"}}),
                MockResponse(200, {"Data": 1_900_000_000_100}),
                MockResponse(
                    200,
                    {
                        "access_token": "account",
                        "refresh_token": "refresh",
                        "expires_in": 3600,
                    },
                ),
                *[MockResponse(200, {"Data": {}}) for _ in range(9)],
            ]
        )
        client = AsyncEksiClient(session=session)
        token = await client.login("user", "password")
        assert token["refresh_token"] == "refresh"
        await client.me()
        await client.user("a/b")
        await client.topic_entries("topic", 2)
        await client.user_entries("nick", 2)
        await client.popular(2, ["spor"])
        await client.today(2)
        await client.agenda(2)
        await client.search_topics("q", 2)
        await client.search_entries("q", 2)
        assert isinstance(
            await client.create_entry("t", "c", dry_run=True), WritePreview
        )
        assert isinstance(await client.edit_entry(1, "c", dry_run=True), WritePreview)
        assert isinstance(await client.delete_entry(1, dry_run=True), WritePreview)
        assert isinstance(await client.unfavorite_entry(1, dry_run=True), WritePreview)
        assert isinstance(await client.vote_entry(1, 1, dry_run=True), WritePreview)
        assert isinstance(await client.remove_entry_vote(1, dry_run=True), WritePreview)
        assert isinstance(await client.follow_topic(1, dry_run=True), WritePreview)
        assert isinstance(await client.unfollow_topic(1, dry_run=True), WritePreview)
        assert isinstance(
            await client.send_message("n", "m", dry_run=True), WritePreview
        )
        assert isinstance(await client.block_user("n", dry_run=True), WritePreview)
        assert isinstance(await client.unblock_user("n", dry_run=True), WritePreview)

    asyncio.run(run())


def test_async_client_matches_extended_account_surface() -> None:
    async def run() -> None:
        session = AsyncMockSession([MockResponse(200, {"Data": {}}) for _ in range(18)])
        client = AsyncEksiClient(session=session)
        await client.is_developer()
        await client.user_favorites("nick", 2)
        await client.filter_channels()
        await client.autocomplete("q")
        await client.notification_count()
        await client.notifications(2)
        await client.unread_topic_count()
        await client.unread_message_authors()
        await client.message_thread("nick", 2)
        await client.archived_message_thread("nick")
        await client.message_recipient_info("nick")
        await client.editable_entry(1)
        await client.channel_list()
        await client.personal_settings()
        await client.preferences()
        await client.notification_preferences()
        await client.trash(2)
        await client.billing_status()

        previews = [
            await client.follow_user("nick", dry_run=True),
            await client.unfollow_user("nick", dry_run=True),
            await client.mute_user("nick", dry_run=True),
            await client.unmute_user("nick", dry_run=True),
            await client.mark_message_thread_read("nick", dry_run=True),
            await client.save_draft("title", "body", dry_run=True),
            await client.delete_draft("title", dry_run=True),
            await client.set_preferences({"theme": "dark"}, dry_run=True),
            await client.delete_message_threads([(1, 2)], dry_run=True),
            await client.archive_message_threads([(1, 2)], dry_run=True),
            await client.delete_trash_entry(1, dry_run=True),
            await client.restore_trash_entry(1, dry_run=True),
            await client.empty_trash(dry_run=True),
        ]
        assert all(isinstance(item, WritePreview) for item in previews)

    asyncio.run(run())


def test_async_user_pagination_iterator() -> None:
    async def run() -> None:
        session = AsyncMockSession(
            [
                MockResponse(
                    200, {"Data": {"Entries": [{"EntryId": 1}], "PageCount": 2}}
                ),
                MockResponse(
                    200, {"Data": {"Entries": [{"EntryId": 2}], "PageCount": 2}}
                ),
            ]
        )
        client = AsyncEksiClient(session=session)
        found = [item async for item in client.iter_user_entries("nick")]
        assert [item["EntryId"] for item in found] == [1, 2]

    asyncio.run(run())
