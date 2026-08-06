from __future__ import annotations

import asyncio
from typing import Any

from mcp import Client

from eksiapi.mcp.credentials import CredentialError
from eksiapi.mcp.server import create_server


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
            assert len(tools) == 11
            assert all(tool.annotations.read_only_hint is True for tool in tools)
            assert all(tool.annotations.open_world_hint is True for tool in tools)

            result = await client.call_tool(
                "eksi_search_entries", {"query": " yapay zeka ", "page": 2}
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
