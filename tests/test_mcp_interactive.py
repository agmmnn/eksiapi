from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from mcp import Client
from mcp.types import ElicitResult

from eksiapi.mcp.policy import PreviewStore
from eksiapi.mcp.server import create_server
from eksiapi.models import WritePreview, WriteResult


class WriteClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _call(self, operation: str, fields: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append((operation, tuple(fields.values()), kwargs))
        if kwargs.get("dry_run"):
            return WritePreview(
                operation, str(next(iter(fields.values()))), fields, False, False
            )
        return WriteResult(operation, "target", True, {"ok": True})

    def create_entry(self, title: str, content: str, **kwargs: Any) -> Any:
        return self._call(
            "create_entry", {"Title": title, "Content": content}, **kwargs
        )

    def favorite_entry(self, entry_id: int, **kwargs: Any) -> Any:
        return self._call("favorite_entry", {"Id": entry_id}, **kwargs)

    def close(self) -> None:
        pass


def test_readonly_is_default_and_interactive_hides_approval_parameter() -> None:
    async def run() -> None:
        async with Client(
            create_server(lambda: WriteClient(), min_interval=0)
        ) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 13
            assert all(tool.annotations.read_only_hint for tool in tools)

        server = create_server(
            lambda: WriteClient(), min_interval=0, mode="interactive"
        )
        async with Client(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert len(tools) == 25
            assert "eksi_publish_entry" in tools
            schema = tools["eksi_publish_entry"].input_schema
            assert set(schema["properties"]) == {"preview_token"}
            assert tools["eksi_delete_entry"].annotations.destructive_hint is True
            assert tools["eksi_apply_entry_edit"].annotations.idempotent_hint is True
            assert tools["eksi_apply_vote_entry"].annotations.idempotent_hint is True

    asyncio.run(run())


def test_human_approval_publishes_exact_preview_once() -> None:
    async def approve(context: Any, params: Any) -> ElicitResult:
        assert "create_entry" in params.message
        return ElicitResult(action="accept", content={"confirm": True})

    async def run() -> None:
        fake = WriteClient()
        server = create_server(lambda: fake, min_interval=0, mode="interactive")
        async with Client(server, elicitation_callback=approve) as client:
            prepared = await client.call_tool(
                "eksi_prepare_entry", {"title": "başlık", "content": "içerik"}
            )
            token = prepared.structured_content["data"]["preview_token"]
            result = await client.call_tool(
                "eksi_publish_entry", {"preview_token": token}
            )
            assert result.is_error is False
            repeated = await client.call_tool(
                "eksi_publish_entry", {"preview_token": token}
            )
            assert repeated.is_error is True
        assert [call[0] for call in fake.calls].count("create_entry") == 2

    asyncio.run(run())


def test_false_human_confirmation_never_mutates() -> None:
    async def reject(context: Any, params: Any) -> ElicitResult:
        return ElicitResult(action="accept", content={"confirm": False})

    async def run() -> None:
        fake = WriteClient()
        server = create_server(lambda: fake, min_interval=0, mode="interactive")
        async with Client(server, elicitation_callback=reject) as client:
            prepared = await client.call_tool(
                "eksi_prepare_favorite_entry", {"entry_id": 8}
            )
            token = prepared.structured_content["data"]["preview_token"]
            result = await client.call_tool(
                "eksi_apply_favorite_entry", {"preview_token": token}
            )
            assert result.is_error is True
        assert len(fake.calls) == 1
        assert fake.calls[0][2]["dry_run"] is True

    asyncio.run(run())


def test_preview_tokens_are_signed_expiring_and_operation_bound() -> None:
    store = PreviewStore(ttl=0.01, secret=b"x" * 32)
    preview = WritePreview("favorite_entry", "1", {"Id": 1}, False, True)
    token = str(store.issue(preview)["preview_token"])
    with pytest.raises(ValueError, match="invalid"):
        store.peek(token + "tampered")
    with pytest.raises(ValueError, match="different operation"):
        store.consume(token, operation="delete_entry")
    time.sleep(0.02)
    with pytest.raises(ValueError, match="expired"):
        store.peek(token)
