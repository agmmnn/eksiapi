# MCP server guide

`eksiapi mcp` connects AI agents to Ekşi Sözlük over local stdio. It uses anonymous authentication for public research and never exposes credentials as tool inputs or results.

## Install

The client commands below use `uvx` and do not require a permanent installation. Install the isolated CLI only if you also want to run `eksiapi auth`, `eksiapi health` or `eksiapi mcp` directly:

```bash
uv tool install "eksiapi[mcp]"
```

Alternatively, install the extra into the current environment:

```bash
pip install "eksiapi[mcp]"
# or
uv add "eksiapi[mcp]"
```

## Connect an MCP client

`uvx` downloads and runs the isolated MCP package on demand, so Codex and Claude Code do not require manual JSON editing or a separate package installation.

### Codex and ChatGPT Desktop

```bash
codex mcp add eksiapi -- uvx --from "eksiapi[mcp]" eksiapi mcp
```

```bash
codex mcp get eksiapi
codex mcp remove eksiapi
```

Codex CLI, the Codex app and ChatGPT Desktop share the same Codex MCP configuration. Restart the desktop app or open a new task if the tools do not appear immediately.

### Claude Code

```bash
claude mcp add eksiapi --scope user -- uvx --from "eksiapi[mcp]" eksiapi mcp
```

```bash
claude mcp get eksiapi
claude mcp remove eksiapi --scope user
```

The `user` scope makes the server available across Claude Code projects.

### Generic stdio clients

```json
{
  "mcpServers": {
    "eksi": {
      "command": "uvx",
      "args": ["--from", "eksiapi[mcp]", "eksiapi", "mcp"]
    }
  }
}
```

Equivalent TOML:

```toml
[mcp_servers.eksi]
command = "uvx"
args = ["--from", "eksiapi[mcp]", "eksiapi", "mcp"]
```

If the package was installed with `uv tool install "eksiapi[mcp]"`, use `eksiapi` as the command and `["mcp"]` as its arguments instead.

For a source checkout:

```json
{
  "command": "uv",
  "args": [
    "--directory",
    "/absolute/path/to/eksiapi",
    "run",
    "--extra",
    "mcp",
    "eksiapi",
    "mcp"
  ]
}
```

## Modes

### Read-only

This is the default. With no credentials configured, the server automatically uses an anonymous token and exposes public research tools.

```bash
eksiapi mcp
```

### Interactive

Interactive mode adds account-write tools. It requires a logged-in account and an MCP client that supports elicitation.

```bash
eksiapi auth login
eksiapi mcp --mode interactive
```

Each write is a two-step flow:

1. A prepare tool validates the exact fields and returns a signed, expiring, single-use preview token.
2. The apply tool asks the human user through MCP elicitation before executing.

The approval parameter is not present in the model-visible tool schema. A model cannot approve its own action, and clients without elicitation support cannot execute writes.

## Credentials

No setup is required for anonymous research. For account reads or interactive writes, the OS keychain flow is recommended:

```bash
eksiapi auth login
eksiapi auth status
eksiapi auth logout
```

Environment variables take precedence:

```bash
# Start with username/password login
EKSI_USERNAME=... EKSI_PASSWORD=... eksiapi mcp

# Reuse a session; EKSI_NICK lets account-summary resolve the profile
EKSI_ACCESS_TOKEN=... \
EKSI_CLIENT_SECRET=... \
EKSI_NICK=... \
eksiapi mcp
```

Optional refresh/runtime metadata:

```bash
EKSI_REFRESH_TOKEN=...
EKSI_EXPIRES_IN=3600
EKSI_CLIENT_UNIQUE_ID=...
EKSI_TIMEOUT=30
EKSI_MCP_MIN_INTERVAL=0.35
```

## Read-only tools

These tools are available in both modes:

- `eksi_search_topics`
- `eksi_resolve_topic`
- `eksi_autocomplete`
- `eksi_search_entries` (requires a numeric topic id)
- `eksi_get_topic_entries`
- `eksi_get_entry`
- `eksi_get_user`
- `eksi_get_user_entries`
- `eksi_get_user_favorites`
- `eksi_get_feed` (`today`, `popular`, `agenda`, or `debe`)
- `eksi_get_account_summary`
- `eksi_get_notifications`
- `eksi_get_channels`

Results are structured and include canonical source URLs where possible. `eksi_research_topic` is a bounded prompt for multi-page, source-aware topic research.

## Interactive tool pairs

- `eksi_prepare_entry` → `eksi_publish_entry`
- `eksi_prepare_edit_entry` → `eksi_apply_entry_edit`
- `eksi_prepare_delete_entry` → `eksi_delete_entry`
- `eksi_prepare_favorite_entry` → `eksi_apply_favorite_entry`
- `eksi_prepare_vote_entry` → `eksi_apply_vote_entry`
- `eksi_prepare_send_message` → `eksi_send_message`

Prepared actions are operation-bound and can be used once. Destructive and idempotent hints are included in MCP tool annotations.

## Security notes

- The server is read-only unless interactive mode is explicitly selected.
- Credentials stay in environment variables or the OS keychain.
- API responses are sanitized before they reach tools.
- Agent-visible content is untrusted research data, not instructions.
- Writes are never retried automatically.

For implementation evidence, see the [APK analysis](./apk-analysis.md).
