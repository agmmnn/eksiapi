from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from mcp import Client

from eksiapi.errors import EksiApiError
from eksiapi.mcp.credentials import CredentialError
from eksiapi.mcp.server import EksiService, _clean, _Throttle, create_server


class FakeEksiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = False

    def _result(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return {
            "Success": True,
            "Data": {"operation": name, "args": list(args), "kwargs": kwargs},
        }

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: self._result(name, *args, **kwargs)

    def close(self) -> None:
        self.closed = True


def test_tools_are_read_only_and_search_returns_structured_data() -> None:
    fake = FakeEksiClient()
    server = create_server(lambda: fake, min_interval=0)

    async def run() -> None:
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 13
            assert all(tool.annotations.read_only_hint is True for tool in tools)
            assert all(tool.annotations.open_world_hint is True for tool in tools)

            result = await client.call_tool(
                "eksi_search_entries",
                {"topic_id": 123, "query": " yapay zeka ", "page": 2},
            )
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["data"]["operation"] == "search_entries"
            assert result.structured_content["page"] == 2
            assert "q=yapay%20zeka" in result.structured_content["source_url"]

    asyncio.run(run())


def test_account_summary_composes_account_calls() -> None:
    fake = FakeEksiClient()
    server = create_server(lambda: fake, min_interval=0)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.call_tool("eksi_get_account_summary", {})
            assert result.is_error is False
            data = result.structured_content["data"]
            assert set(data) == {
                "profile",
                "notification_count",
                "unread_topic_count",
                "unread_message_authors",
                "billing_status",
            }

    asyncio.run(run())


def test_research_prompt_is_bounded() -> None:
    server = create_server(lambda: FakeEksiClient(), min_interval=0)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.get_prompt(
                "eksi_research_topic", {"topic": "python", "max_pages": "99"}
            )
            text = result.messages[0].content.text
            assert "up to 5 pages" in text
            assert "source_url" in text

    asyncio.run(run())


def test_missing_credentials_become_a_safe_tool_error() -> None:
    def missing_credentials():
        raise CredentialError("No credentials configured")

    server = create_server(missing_credentials, min_interval=0)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.call_tool("eksi_get_entry", {"entry_id": 1})
            assert result.is_error is True
            assert "No credentials configured" in result.content[0].text

    asyncio.run(run())


def test_all_read_tools_route_to_client() -> None:
    fake = FakeEksiClient()
    server = create_server(lambda: fake, min_interval=0)
    calls = [
        ("eksi_search_topics", {"query": "python", "page": 2}),
        ("eksi_resolve_topic", {"term": "python"}),
        ("eksi_autocomplete", {"query": "py", "kind": "query"}),
        ("eksi_get_topic_entries", {"topic": "python", "page": 3}),
        ("eksi_get_entry", {"entry_id": 42}),
        ("eksi_get_user", {"nick": "alice/bob"}),
        ("eksi_get_user_entries", {"nick": "alice", "page": 4}),
        ("eksi_get_user_favorites", {"nick": "alice", "page": 5}),
        ("eksi_get_feed", {"feed": "popular", "page": 6, "channel_filters": ["spor"]}),
        ("eksi_get_feed", {"feed": "today", "page": 7}),
        ("eksi_get_feed", {"feed": "agenda", "page": 8}),
        ("eksi_get_notifications", {"page": 9}),
        ("eksi_get_channels", {}),
    ]

    async def run() -> None:
        async with Client(server) as client:
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                assert result.is_error is False

    asyncio.run(run())
    assert any(name == "feed" and args == ("popular",) for name, args, _ in fake.calls)
    server._eksi_service.close()
    assert fake.closed is True


def test_feed_validation_and_cleaning() -> None:
    server = create_server(lambda: FakeEksiClient(), min_interval=0)

    async def run() -> None:
        async with Client(server) as client:
            invalid_filter = await client.call_tool(
                "eksi_get_feed", {"feed": "today", "channel_filters": ["spor"]}
            )
            too_many = await client.call_tool(
                "eksi_get_feed",
                {"feed": "popular", "channel_filters": ["x"] * 21},
            )
            empty = await client.call_tool("eksi_get_user", {"nick": "   "})
            assert invalid_filter.is_error is True
            assert too_many.is_error is True
            assert empty.is_error is True

    asyncio.run(run())
    with pytest.raises(ValueError, match="cannot be empty"):
        _clean(" ", "query")


def test_service_masks_api_and_unexpected_errors() -> None:
    class ErrorClient:
        def api_error(self):
            raise EksiApiError("safe")

        def unexpected(self):
            raise OSError("sensitive")

        def close(self):
            pass

    service = EksiService(lambda: ErrorClient(), min_interval=0)
    with pytest.raises(RuntimeError, match="safe"):
        service.call("api_error")
    with pytest.raises(RuntimeError, match="Unexpected error"):
        service.call("unexpected")


def test_throttle_configuration(monkeypatch) -> None:
    with pytest.raises(ValueError, match="negative"):
        _Throttle(-1)

    monkeypatch.setenv("EKSI_MCP_MIN_INTERVAL", "bad")
    with pytest.raises(ValueError, match="must be a number"):
        create_server(lambda: FakeEksiClient())

    monkeypatch.setenv("EKSI_MCP_MIN_INTERVAL", "-1")
    with pytest.raises(ValueError, match="cannot be negative"):
        create_server(lambda: FakeEksiClient())

    throttle = _Throttle(0.001)
    throttle._last_call = time.monotonic()
    throttle.wait()
