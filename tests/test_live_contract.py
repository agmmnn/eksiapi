from __future__ import annotations

import os

import pytest

from eksiapi import EksiClient

pytestmark = pytest.mark.skipif(
    os.environ.get("EKSI_LIVE_TESTS") != "1",
    reason="set EKSI_LIVE_TESTS=1 to run read-only production contract tests",
)


def test_public_server_time_contract() -> None:
    """Opt-in live test performs no authenticated or mutating operation."""
    with EksiClient.anonymous() as client:
        payload = client.server_time()
    assert isinstance(payload, dict)
    assert int(payload["Data"]) > 0


def test_authenticated_read_and_write_preview_contracts() -> None:
    """Exercise account reads and every write shape without mutating production."""
    access_token = os.environ.get("EKSI_ACCESS_TOKEN")
    client_secret = os.environ.get("EKSI_CLIENT_SECRET")
    account_nick = os.environ.get("EKSI_NICK")
    if not access_token or not client_secret:
        pytest.skip(
            "authenticated live contract requires EKSI_ACCESS_TOKEN/CLIENT_SECRET"
        )
    if not account_nick:
        pytest.skip("authenticated live profile requires EKSI_NICK with token auth")
    with EksiClient(access_token, client_secret, account_nick=account_nick) as client:
        assert isinstance(client.me(), dict)
        previews = [
            client.create_entry("contract test", "not published", dry_run=True),
            client.edit_entry(1, "not applied", dry_run=True),
            client.delete_entry(1, dry_run=True),
            client.favorite_entry(1, dry_run=True),
            client.unfavorite_entry(1, dry_run=True),
            client.vote_entry(1, 1, dry_run=True),
            client.remove_entry_vote(1, dry_run=True),
            client.follow_topic(1, dry_run=True),
            client.unfollow_topic(1, dry_run=True),
            client.block_user("contract", dry_run=True),
            client.unblock_user("contract", dry_run=True),
            client.send_message("contract", "not sent", dry_run=True),
            client.mark_message_thread_read("contract", dry_run=True),
            client.save_draft("contract", "not saved", dry_run=True),
            client.delete_draft("contract", dry_run=True),
            client.delete_message_archives([1], dry_run=True),
        ]
    assert all(preview.digest for preview in previews)
