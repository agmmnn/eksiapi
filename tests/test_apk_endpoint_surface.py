from __future__ import annotations

import asyncio

from eksiapi import (
    AsyncEksiClient,
    AsyncMockSession,
    EksiClient,
    MockResponse,
    MockSession,
)
from eksiapi.models import WritePreview

READ_CALLS = [
    ("comments", (7,), {"page": 2, "size": 5}),
    ("entry_likes", (7,), {}),
    ("entry_favorites", (7,), {}),
    ("entry_caylak_likes", (7,), {}),
    ("entry_caylak_favorites", (7,), {}),
    ("user_following", ("a/b",), {"page": 2}),
    ("user_followers", ("a/b",), {"page": 2}),
    ("user_badges", ("a/b",), {}),
    ("user_images", ("a/b",), {"page": 2}),
    ("buddies", (), {}),
    ("buddy_list", (), {"page": 2}),
    ("mute_list", (), {"page": 2}),
    ("block_list", (), {"page": 2}),
    ("blocked_users", (), {}),
    ("index_title_blocks", (), {}),
    ("index_title_block_list", (), {"page": 2}),
    ("homepage_entries", (), {"page": 2}),
    ("offline_debe", (), {"from_date": "2026-08-07"}),
    ("topic_creator_info", (7,), {}),
    ("user_channel_filters", (), {}),
    ("user_follow_approval_status", (), {}),
    ("message_archives", (), {"page": 2}),
    ("archived_message_thread", ("a/b",), {"page": 2}),
    ("unread_message_thread", ("a/b",), {}),
]


def test_sync_apk_read_surface_builds_requests() -> None:
    client = EksiClient(
        session=MockSession([MockResponse(200, {"Data": {}}) for _ in READ_CALLS])
    )

    for name, args, kwargs in READ_CALLS:
        getattr(client, name)(*args, **kwargs)

    calls = client.session.calls
    assert len(calls) == len(READ_CALLS)
    assert calls[0][1].endswith("/v2/comment/list/7/")
    assert calls[0][2]["params"] == {"page": 2, "size": 5}
    assert "%2F" in calls[5][1]
    assert calls[8][1].endswith("/v2/user/a%2Fb//images")
    assert calls[-1][0] == "POST"
    assert calls[-1][2]["data"] == {"Nick": "a/b", "MarkRead": False}


def test_sync_apk_write_surface_previews_exact_contracts() -> None:
    client = EksiClient(session=MockSession([]))
    previews = [
        client.add_comment(7, "hello", dry_run=True),
        client.edit_comment(8, "updated", dry_run=True),
        client.delete_comment(8, dry_run=True),
        client.vote_comment(8, 1, owner_id=9, dry_run=True),
        client.remove_comment_vote(8, 1, dry_run=True),
        client.set_channel_filters({"Filters": ["spor"]}, dry_run=True),
        client.set_notification_preferences({"Message": True}, dry_run=True),
        client.register_push_notification({"Token": "device"}, dry_run=True),
        client.unregister_push_notification({"Token": "device"}, dry_run=True),
        client.set_profile_biography("bio", dry_run=True),
        client.remove_profile_biography(dry_run=True),
        client.add_pinned_entry(7, dry_run=True),
        client.remove_pinned_entry(7, dry_run=True),
        client.block_index_titles("nick", dry_run=True),
        client.unblock_index_titles("nick", dry_run=True),
        client.delete_message_archives([3, 4], dry_run=True),
    ]

    assert all(isinstance(preview, WritePreview) for preview in previews)
    assert previews[0].fields == {"Id": 7, "Content": "hello"}
    assert previews[3].fields == {"Id": 8, "Rate": 1, "Owner": 9}
    assert previews[9].fields == {"BiographyText": "bio"}
    assert previews[-1].fields == {
        "ArchiveIdList": [{"ArchiveId": 3}, {"ArchiveId": 4}]
    }
    assert previews[-1].destructive is True
    assert client.session.calls == []


def test_async_apk_surface_matches_sync_client() -> None:
    async def run() -> None:
        session = AsyncMockSession(
            [MockResponse(200, {"Data": {}}) for _ in READ_CALLS]
        )
        client = AsyncEksiClient(session=session)
        for name, args, kwargs in READ_CALLS:
            await getattr(client, name)(*args, **kwargs)

        preview = await client.delete_message_archives([3], dry_run=True)
        assert isinstance(preview, WritePreview)
        assert preview.fields == {"ArchiveIdList": [{"ArchiveId": 3}]}
        assert len(session.calls) == len(READ_CALLS)

    asyncio.run(run())
