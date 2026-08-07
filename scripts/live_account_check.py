"""Opt-in live account smoke test with an optional reversible draft round-trip."""

from __future__ import annotations

import argparse
import getpass
import json
import time
from dataclasses import asdict
from typing import Any

from eksiapi import EksiClient
from eksiapi.errors import EksiApiError
from eksiapi.formatting import sanitize_payload


def _result_shape(payload: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "type": type(payload).__name__,
        "success": payload.get("Success") if isinstance(payload, dict) else None,
        "has_data": isinstance(payload, dict) and "Data" in payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reversible-writes",
        action="store_true",
        help="Save and then delete a uniquely named non-public draft",
    )
    args = parser.parse_args()
    username = getpass.getpass("Ekşi username/email (hidden): ")
    password = getpass.getpass("Ekşi password (hidden): ")
    audit = []
    client = EksiClient(audit_sink=audit.append)
    report: dict[str, Any] = {"login": False, "reads": {}, "writes": {}}
    try:
        try:
            token = client.login(username, password)
        except EksiApiError as exc:
            report["login_error"] = {
                "type": type(exc).__name__,
                "status_code": exc.status_code,
                "message": str(exc)[:300],
                "details": sanitize_payload(exc.details),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        report["login"] = True
        report["token_metadata"] = {
            "has_access_token": bool(client.token_info),
            "has_refresh_token": bool(
                client.token_info and client.token_info.refresh_token
            ),
            "has_expiry": bool(client.token_info and client.token_info.expires_at),
            "non_secret_response_keys": sorted(
                key for key in token if "token" not in key.lower()
            ),
        }
        reads = {
            "me": client.me,
            "notification_count": client.notification_count,
            "unread_topic_count": client.unread_topic_count,
            "unread_message_authors": client.unread_message_authors,
            "preferences": client.preferences,
            "personal_settings": client.personal_settings,
        }
        for name, call in reads.items():
            try:
                report["reads"][name] = _result_shape(call())
            except EksiApiError as exc:
                report["reads"][name] = {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc)[:200],
                }

        title = f"eksiapi-live-contract-{int(time.time())}"
        content = "eksiapi canlı sözleşme testi; yayınlanmayan geçici taslak."
        preview = client.save_draft(title, content, dry_run=True)
        report["writes"]["draft_preview"] = {
            "operation": preview.operation,
            "digest_length": len(preview.digest),
            "destructive": preview.destructive,
            "idempotent": preview.idempotent,
        }
        if args.reversible_writes:
            saved = client.save_draft(title, content)
            report["writes"]["draft_save"] = {"ok": saved.success}
            deleted = client.delete_draft(title)
            report["writes"]["draft_delete"] = {"ok": deleted.success}
        report["audit"] = [asdict(event) for event in audit]
        report["last_rate_limit"] = asdict(client.last_rate_limit)
    finally:
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
