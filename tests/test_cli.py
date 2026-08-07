from __future__ import annotations

import builtins

import pytest

from eksiapi.cli import _load, _run_auth, _run_mcp, main


def test_mcp_cli_explains_missing_optional_extra(monkeypatch, capsys) -> None:
    real_import = builtins.__import__

    def import_without_mcp(name, *args, **kwargs):
        if name == "eksiapi.mcp.server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_mcp)

    assert _run_mcp() == 2
    assert "pip install 'eksiapi[mcp]'" in capsys.readouterr().err


def test_auth_cli_delegates_to_optional_command(monkeypatch) -> None:
    monkeypatch.setattr("eksiapi.mcp.credentials.main", lambda: 7)
    assert _run_auth() == 7


def test_mcp_cli_delegates_to_optional_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("eksiapi.mcp.server.main", lambda: calls.append("run"))

    assert _run_mcp() == 0
    assert calls == ["run"]


def test_cli_does_not_hide_unrelated_import_errors(monkeypatch) -> None:
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "eksiapi.mcp.server":
            raise ModuleNotFoundError("No module named 'unrelated'", name="unrelated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)

    with pytest.raises(ModuleNotFoundError, match="unrelated"):
        _load("mcp")


def test_unified_cli_routes_subcommands(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "eksiapi.cli._run_auth", lambda argv: calls.append(("auth", argv)) or 0
    )
    monkeypatch.setattr(
        "eksiapi.cli._run_mcp", lambda argv: calls.append(("mcp", argv)) or 0
    )

    assert main(["auth", "status"]) == 0
    assert main(["mcp", "--mode", "interactive"]) == 0
    assert calls == [("auth", ["status"]), ("mcp", ["--mode", "interactive"])]


def test_unified_cli_prints_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip()
