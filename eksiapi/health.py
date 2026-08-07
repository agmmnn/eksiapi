"""Compact live health check for the public Ekşi Sözlük API surface."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from eksiapi.client import EksiClient


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any, key: str) -> list[Any]:
    items = _mapping(value).get(key)
    return items if isinstance(items, list) else []


def _clip(value: Any, limit: int = 58) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _topic_summary(value: Any) -> str:
    topics = _items(value, "Topics")
    if not topics:
        return "yanıt alındı"
    topic = _mapping(topics[0])
    count = topic.get("MatchedCount") or topic.get("FullCount") or 0
    return f"{len(topics)} başlık · {_clip(topic.get('Title'), 34)} ({count})"


def _entry_summary(value: Any) -> str:
    data = _mapping(value)
    entries = _items(data, "Entries")
    entry = _mapping(entries[0]) if entries else {}
    author = _mapping(entry.get("Author"))
    return f"{_clip(data.get('Title'), 28)} · @{author.get('Nick') or '?'} · #{entry.get('Id') or '?'}"


def _user_summary(value: Any, nick: str) -> str:
    data = _mapping(value)
    info = _mapping(data.get("UserInfo"))
    identity = _mapping(info.get("UserIdentifier"))
    counts = _mapping(info.get("EntryCounts"))
    return f"@{identity.get('Nick') or nick} · {counts.get('Total') or 0} entry"


def _channel_summary(value: Any) -> str:
    return f"{len(_items(value, 'AllChannels'))} kanal"


def _time_summary(value: Any) -> str:
    try:
        return datetime.fromtimestamp(
            float(value) / 1000, ZoneInfo("Europe/Istanbul")
        ).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return _clip(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eksiapi health", description=__doc__)
    parser.add_argument(
        "--user", default="ssg", help="Public profile used by the check"
    )
    parser.add_argument(
        "--entry-id", type=int, default=1, help="Public entry used by the check"
    )
    parser.add_argument(
        "--timeout", type=float, default=15, help="Request timeout in seconds"
    )
    args = parser.parse_args(argv)
    if args.entry_id < 1:
        parser.error("--entry-id must be greater than zero")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    try:
        client = EksiClient.anonymous(timeout=args.timeout, raw_response=False)
    except Exception as exc:  # noqa: BLE001 - a health command must report startup errors
        print(f"\n🔴 eksiapi health · anonymous token failed\n   {_clip(exc, 80)}")
        return 1

    checks: list[tuple[str, Callable[[], Any], Callable[[Any], str]]] = [
        ("today", lambda: client.today(page=1), _topic_summary),
        ("popular", lambda: client.popular(page=1), _topic_summary),
        ("entry", lambda: client.entry(args.entry_id), _entry_summary),
        (
            "user",
            lambda: client.user(args.user),
            lambda value: _user_summary(value, args.user),
        ),
        ("channels", client.channel_list, _channel_summary),
        ("server time", client.server_time, _time_summary),
    ]
    rows: list[tuple[str, bool, str]] = []
    with client:
        for name, call, summarize in checks:
            try:
                rows.append((name, True, summarize(call())))
            except Exception as exc:  # noqa: BLE001 - continue checking remaining endpoints
                rows.append((name, False, _clip(exc, 70)))

    print("\n🩺 eksiapi health · 👻 anonymous")
    width = max(len(name) for name, _, _ in rows)
    for name, passed, summary in rows:
        print(f"{'✅' if passed else '❌'} {name:<{width}}  {summary}")
    passed = sum(result for _, result, _ in rows)
    print(
        f"\n{'🟢' if passed == len(rows) else '🟡'} {passed}/{len(rows)} kontrol başarılı"
    )
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
