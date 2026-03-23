"""
eksiapi — interactive exploration example

Run:
    uv run examples/explore.py

Set credentials via env vars to skip the prompt:
    EKSI_USERNAME=... EKSI_PASSWORD=... uv run examples/explore.py
"""

import json
import os
import sys
import io
import getpass

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from eksiapi import EksiClient


def pp(data):
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])


def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


def run(label, fn, *args, **kwargs):
    section(label)
    try:
        pp(fn(*args, **kwargs))
    except Exception as e:
        print(f"  ERROR: {e}")


# ── Credentials ──────────────────────────────────────────────────────────────

username = os.environ.get("EKSI_USERNAME") or input("Username: ")
password = os.environ.get("EKSI_PASSWORD") or getpass.getpass("Password: ")

# ── Login ────────────────────────────────────────────────────────────────────

api = EksiClient()

print("\n[*] Logging in...")
tok = api.login(username, password)
print(f"[+] access_token  : {str(tok.get('access_token', ''))[:48]}...")
print(f"[+] client_secret : {api.session.headers.get('Client-Secret')}")
print(f"[+] expires_in    : {tok.get('expires_in')}s")

# ── User ─────────────────────────────────────────────────────────────────────

run("me()", api.me)
run("user('agmmnn')", api.user, "agmmnn")
run("user_entries('agmmnn', page=1)", api.user_entries, "agmmnn", page=1)

# ── Index ────────────────────────────────────────────────────────────────────

run("today(page=1)", api.today, page=1)
run("popular(page=1)", api.popular, page=1)
run("filter_channels()", api.filter_channels)

# ── Entries ───────────────────────────────────────────────────────────────────

run("entry(1)", api.entry, 1)
run("topic_entries('python', page=1)", api.topic_entries, "python", page=1)

# ── Search ───────────────────────────────────────────────────────────────────

run("search_topics('python')", api.search_topics, "python")
run("autocomplete('ekşi')", api.autocomplete, "ekşi")

# ── Notifications ─────────────────────────────────────────────────────────────

run("notification_count()", api.notification_count)
run("notifications(page=1)", api.notifications, page=1)
run("unread_topic_count()", api.unread_topic_count)

# ── Misc ──────────────────────────────────────────────────────────────────────

run("channel_list()", api.channel_list)
run("billing_status()", api.billing_status)
run("server_time()", api.server_time)
