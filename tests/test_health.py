from __future__ import annotations

from eksiapi.health import main


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def today(self, page=1):
        return {"Topics": [{"Title": "bugün", "MatchedCount": 12}]}

    def popular(self, page=1):
        return {"Topics": [{"Title": "popüler", "FullCount": 8}]}

    def entry(self, entry_id):
        return {
            "Title": "pena",
            "Entries": [{"Id": entry_id, "Author": {"Nick": "ssg"}}],
        }

    def user(self, nick):
        return {
            "UserInfo": {"UserIdentifier": {"Nick": nick}, "EntryCounts": {"Total": 3}}
        }

    def channel_list(self):
        return {"AllChannels": [{"Name": "gündem"}]}

    def server_time(self):
        return 1786111200000


def test_health_reports_compact_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "eksiapi.health.EksiClient.anonymous", lambda **_kwargs: FakeClient()
    )

    assert main(["--user", "alice", "--entry-id", "42"]) == 0
    output = capsys.readouterr().out
    assert "6/6 kontrol başarılı" in output
    assert "pena · @ssg · #42" in output
    assert "@alice · 3 entry" in output
