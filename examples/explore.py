"""A compact, data-rich tour of the Ekşi Sözlük API.

Examples:
    uv run examples/explore.py
    uv run examples/explore.py --mode login
    EKSI_USERNAME=... EKSI_PASSWORD=... uv run examples/explore.py
"""

from __future__ import annotations

import argparse
import getpass
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from eksiapi import EksiClient

MAX_TEXT = 88


def _clip(value: Any, maximum: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else f"{text[: maximum - 1]}…"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _date(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return "tarih yok"


def _server_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(
            float(value) / 1000, tz=ZoneInfo("Europe/Istanbul")
        ).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return _clip(value, 24)


def _topic_lines(payload: Any, limit: int) -> list[str]:
    topics = _list(_mapping(payload).get("Topics"))[:limit]
    lines = []
    for index, item in enumerate(topics, 1):
        topic = _mapping(item)
        count = topic.get("FullCount") or topic.get("MatchedCount") or 0
        lines.append(f"  {index}. {_clip(topic.get('Title'), 56)} · {count} entry")
    return lines or ["  veri yok"]


def _entry_lines(payload: Any) -> list[str]:
    topic = _mapping(payload)
    entry = _mapping(next(iter(_list(topic.get("Entries"))), {}))
    author = _mapping(entry.get("Author"))
    title = _clip(topic.get("Title") or "başlık yok", 54)
    meta = (
        f"@{author.get('Nick') or '?'} · {_date(entry.get('Created'))}"
        f" · ⭐ {entry.get('FavoriteCount') or 0} · 💬 {entry.get('CommentCount') or 0}"
    )
    return [
        f"📝 Entry #{entry.get('Id') or '?'} · {title}",
        f"  {meta}",
        f"  {_clip(entry.get('Content') or 'içerik yok')}",
    ]


def _profile_lines(payload: Any, requested_nick: str) -> list[str]:
    profile = _mapping(payload)
    info = _mapping(profile.get("UserInfo"))
    identity = _mapping(info.get("UserIdentifier"))
    counts = _mapping(info.get("EntryCounts"))
    nick = identity.get("Nick") or requested_nick
    flags = []
    if info.get("IsCaylak"):
        flags.append("çaylak")
    if info.get("IsVerified"):
        flags.append("doğrulanmış")
    status = f" · {' / '.join(flags)}" if flags else ""
    lines = [
        f"👤 @{nick}{status}",
        (
            f"  ✍️ {counts.get('Total') or 0} entry · "
            f"👥 {profile.get('FollowerCount') or 0} takipçi · "
            f"➡️ {profile.get('FollowingsCount') or 0} takip"
        ),
    ]
    biography = profile.get("Biograpyh") or profile.get("Biography")
    if biography:
        lines.append(f"  “{_clip(biography, 76)}”")
    return lines


def _user_entry_lines(payload: Any, limit: int) -> list[str]:
    items = _list(_mapping(payload).get("Entries"))[:limit]
    lines = ["🗒️ Son entry'ler"]
    for item in items:
        wrapper = _mapping(item)
        entry = _mapping(wrapper.get("Entry"))
        topic = _mapping(wrapper.get("TopicId"))
        lines.append(
            f"  #{entry.get('Id') or '?'} · {_clip(topic.get('Title'), 28)} · "
            f"{_clip(entry.get('Content'), 54)}"
        )
    if not items:
        lines.append("  veri yok")
    return lines


def _account_lines(profile: Any, notifications: Any, unread_topics: Any) -> list[str]:
    info = _mapping(_mapping(profile).get("UserInfo"))
    identity = _mapping(info.get("UserIdentifier"))
    nick = identity.get("Nick") or "?"
    status = "çaylak" if info.get("IsCaylak") else "yazar"
    return [
        f"🔐 Oturum · @{nick} · {status}",
        f"  🔔 {_scalar(notifications)} bildirim · 📚 {_scalar(unread_topics)} okunmamış başlık",
    ]


def _scalar(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key in ("Count", "TotalCount", "Value"):
            if key in value:
                return value[key]
    return value if value is not None else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("anonymous", "login"),
        help="Defaults to login when both credential env vars exist, otherwise anonymous.",
    )
    parser.add_argument("--user", default="agmmnn", help="Public profile to inspect")
    parser.add_argument("--entry-id", type=int, default=1, help="Entry to inspect")
    parser.add_argument(
        "--items", type=int, choices=range(1, 6), default=2, help="Items per section"
    )
    parser.add_argument("--timeout", type=float, default=15, help="Request timeout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    username = os.environ.get("EKSI_USERNAME")
    password = os.environ.get("EKSI_PASSWORD")
    mode = args.mode or ("login" if username and password else "anonymous")

    if mode == "login":
        username = username or input("Ekşi username/email: ").strip()
        password = password or getpass.getpass("Ekşi password: ")
        if not username or not password:
            parser.error("login mode requires a username and password")

    try:
        if mode == "anonymous":
            client = EksiClient.anonymous(timeout=args.timeout, raw_response=False)
        else:
            client = EksiClient(timeout=args.timeout, raw_response=False)
            client.login(username, password)
    except Exception as exc:  # noqa: BLE001 - CLI reports startup failures cleanly
        print(f"❌ Kimlik doğrulama başarısız: {_clip(exc)}")
        return 2

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def fetch(name: str, call: Callable[[], Any]) -> None:
        try:
            results[name] = call()
        except Exception as exc:  # noqa: BLE001 - exploration continues per endpoint
            errors[name] = _clip(exc, 64)

    with client:
        checks: list[tuple[str, Callable[[], Any]]] = [
            ("today", lambda: client.today(page=1)),
            ("popular", lambda: client.popular(page=1)),
            ("entry", lambda: client.entry(args.entry_id)),
            ("user", lambda: client.user(args.user)),
            ("user_entries", lambda: client.user_entries(args.user, page=1)),
            ("channels", client.channel_list),
            ("server_time", client.server_time),
        ]
        if mode == "login":
            checks.extend(
                [
                    ("me", client.me),
                    ("notifications", client.notification_count),
                    ("unread_topics", client.unread_topic_count),
                ]
            )
        for name, call in checks:
            fetch(name, call)

    icon = "👻" if mode == "anonymous" else "🔑"
    print(f"\n🟢 eksiapi explore · {icon} {mode}")
    print("\n🔥 Bugün")
    print("\n".join(_topic_lines(results.get("today"), args.items)))
    print("\n⭐ Popüler")
    print("\n".join(_topic_lines(results.get("popular"), args.items)))
    print("\n" + "\n".join(_entry_lines(results.get("entry"))))
    print("\n" + "\n".join(_profile_lines(results.get("user"), args.user)))
    print("\n" + "\n".join(_user_entry_lines(results.get("user_entries"), args.items)))

    channels = _list(_mapping(results.get("channels")).get("AllChannels"))
    print(f"\n📡 {len(channels)} kanal · 🕒 {_server_time(results.get('server_time'))}")
    if mode == "login" and "me" in results:
        print(
            "\n"
            + "\n".join(
                _account_lines(
                    results["me"],
                    results.get("notifications"),
                    results.get("unread_topics"),
                )
            )
        )

    for name, message in errors.items():
        print(f"⚠️ {name}: {message}")
    total = len(results) + len(errors)
    print(f"\n{'✅' if not errors else '⚠️'} {len(results)}/{total} endpoint başarılı")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
