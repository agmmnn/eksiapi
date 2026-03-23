# eksiapi

Unofficial Python client for [Ekşi Sözlük](https://eksisozluk.com), reverse-engineered from the Android app v2.4.4.

- Full standalone authentication — no Frida, no proxy
- Bypasses Cloudflare via `curl_cffi` Chrome TLS impersonation
- Typed, minimal, no magic

## Example

```bash
uv run examples/explore.py
# or with env vars to skip the prompt:
EKSI_USERNAME=you@mail.com EKSI_PASSWORD=pass uv run examples/explore.py
```

## Install

```bash
uv add eksiapi
# or
pip install eksiapi
```

## Quick start

```python
from eksiapi import EksiClient

api = EksiClient()
api.login("username", "password")

print(api.me())
print(api.popular())
print(api.today())
print(api.entry(1))
```

## Usage

### Authentication

```python
api = EksiClient()
api.login("username", "password")
```

Reuse an existing token (skips login):

```python
api = EksiClient(access_token="...", client_secret="uuid-...")
```

### User

```python
api.me()                          # authenticated user profile
api.user("agmmnn")                # any user's public profile
api.user_entries("agmmnn", page=1)
api.user_favorites("agmmnn", page=1)
api.is_developer()
```

### Entries

```python
api.entry(1)
api.topic_entries("python", page=1)
api.search_entries("query", page=1)
api.agenda(page=1)
```

### Index

```python
api.popular(page=1)
api.popular(page=1, channel_filters=["channel-id"])
api.today(page=1)
api.filter_channels()
```

### Search

```python
api.search_topics("python", page=1)
api.autocomplete("pyth")
```

### Notifications & messages

```python
api.notification_count()
api.notifications(page=1)
api.unread_topic_count()
api.unread_message_authors()
```

### Misc

```python
api.channel_list()
api.billing_status()
api.server_time()
```

## How auth works

Every request to the auth endpoints requires an `Api-Secret` header — an RSA-encrypted token the app generates on the fly.

**Plaintext format** (reversed from APK via Frida + jadx):

```
{randomHex(40-80)}-{APP_UUID}-{len²}-{adjustedTime}-{dayOff}-{hourOff}-{minOff}-eksisozluk-android/137-{clientSecret}
```

`eksiapi/auth.py` reproduces this using the 2048-bit public key embedded in the APK.

**Login flow:**

1. `GET /v2/clientsettings/time` — get server timestamp
2. `POST /v2/account/anonymoustoken` — obtain anonymous bearer
3. `GET /v2/clientsettings/time` — fresh timestamp
4. `POST /token` — login with credentials → `access_token` + `Client-Secret`

## API reference

See [`openapi.yaml`](./openapi.yaml) — import into Postman or Insomnia for interactive exploration.

> Note: Postman can't generate `Api-Secret` natively (requires RSA). Use the Python client to get a token, then paste it into Postman's `Authorization` header.

## Project layout

```
eksiapi/
├── eksiapi/
│   ├── __init__.py   # EksiClient, generate_api_secret
│   ├── auth.py       # Api-Secret generation (RSA)
│   └── client.py     # API client
├── openapi.yaml      # OpenAPI 3.0 spec
├── pyproject.toml
└── uv.lock
```

## Disclaimer

For educational and personal use only. Not affiliated with Ekşi Teknoloji.
