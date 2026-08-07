# Python SDK guide

`eksiapi` provides matching synchronous and asynchronous clients for the reverse-engineered Ekşi Sözlük mobile API. Python 3.10 or newer is required.

## Installation

```bash
pip install eksiapi
# or
uv add eksiapi
```

The base package contains the SDK and its HTTP/cryptography dependencies. MCP dependencies remain optional.

## Authentication modes

### Anonymous

Use this for public research. The client obtains an anonymous app bearer and renews it automatically when needed.

```python
from eksiapi import EksiClient

with EksiClient.anonymous(raw_response=False) as eksi:
    print(eksi.today())
    print(eksi.entry(1))
    print(eksi.user("agmmnn"))
```

`auth_mode` is set to `"anonymous"`. Account mutations fail locally with `EksiAuthenticationError`.

### Password login

```python
from eksiapi import EksiClient

eksi = EksiClient(raw_response=False)
eksi.login("username-or-email", "password")
print(eksi.me())
```

Login first obtains an anonymous bearer, then performs the Android password grant. The response's account nick and refresh metadata are retained.

### Reuse a token

```python
eksi = EksiClient(
    access_token="...",
    client_secret="uuid-...",
    account_nick="your-nick",
    refresh_token="...",  # optional
    expires_in=3600,  # optional
)
```

`account_nick` is required for `me()` because the mobile API has no dedicated `/v2/user/me` route; the SDK resolves the current account through the public profile endpoint.

## Response modes

Methods return the original API envelope by default:

```python
{"Success": True, "Data": {...}}
```

Set `raw_response=False` to unwrap `Data`, remove credential-shaped fields and flatten entry HTML into plain text:

```python
with EksiClient.anonymous(raw_response=False) as eksi:
    data = eksi.entry(1)
```

Typed helpers include `entry_typed()`, `me_typed()` and `page()`. Existing dictionary-returning methods remain available.

## Common reads

```python
# Feeds and search
eksi.today(page=1)
eksi.popular(page=1)
eksi.agenda(page=1)
eksi.search_topics("python", page=1)
eksi.search_entries("python", page=1)

# Topics, entries and comments
eksi.entry(1)
eksi.topic_entries("python", page=1)
eksi.comments(1, page=1, size=20)
eksi.entry_likes(1)
eksi.entry_favorites(1)

# Users
eksi.user("agmmnn")
eksi.user_entries("agmmnn", page=1)
eksi.user_favorites("agmmnn", page=1)
eksi.user_followers("agmmnn", page=1)
eksi.user_following("agmmnn", page=1)

# Account reads
eksi.notifications(page=1)
eksi.message_archives(page=1)
eksi.preferences()
eksi.trash(page=1)
```

See [`openapi.yaml`](../openapi.yaml) for the complete documented route inventory and request shapes.

## Pagination

Use a typed page view when you want metadata:

```python
payload = eksi.topic_entries("python", page=1)
page = eksi.page(payload)
print(page.items, page.has_more)
```

Or stream bounded pages:

```python
for entry in eksi.iter_topic_entries("python", max_pages=3):
    print(entry)
```

## Async client

The async client mirrors the sync surface. Anonymous authentication is lazy and happens on the first read.

```python
from eksiapi import AsyncEksiClient

async with AsyncEksiClient.anonymous(raw_response=False) as eksi:
    topic = await eksi.entry(1)
    async for entry in eksi.iter_user_entries("agmmnn", max_pages=2):
        print(entry)
```

## Writes and previews

Every high-level write supports `dry_run=True`. It validates fields and returns a deterministic `WritePreview` without making an HTTP request.

```python
preview = eksi.create_entry("başlık", "entry içeriği", dry_run=True)
print(preview.operation)
print(preview.fields)
print(preview.destructive, preview.idempotent, preview.digest)

# Execute only after your application's confirmation step.
result = eksi.create_entry("başlık", "entry içeriği")
```

Implemented actions cover entries, comments, reactions, topic/user state, messages, drafts, preferences, message archives, profile state and trash. Writes are never retried automatically. Pass `audit_sink=` to receive secret-free `AuditEvent` records.

The app's `POST /v2/entry/add` response confirms success but does not reliably return the new entry ID.

## Transport and diagnostics

```python
from eksiapi import AndroidFingerprint, EksiClient, RetryPolicy

eksi = EksiClient(
    timeout=15,
    proxy="http://127.0.0.1:8080",
    verify=True,
    retry_policy=RetryPolicy(max_attempts=3),
    fingerprint=AndroidFingerprint(),
)
```

Safe GETs and explicitly safe read POSTs use bounded retry/backoff. Inspect `last_rate_limit` and `last_request_id` after a request.

Public errors are `EksiApiError`, `EksiAuthenticationError`, `EksiNotFoundError`, `EksiRateLimitError` and `EksiTransportError`.

## API evidence

- [Full OpenAPI contract](../openapi.yaml)
- [APK analysis](./apk-analysis.md)
