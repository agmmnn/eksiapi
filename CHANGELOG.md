# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-08-07

### Added

- 🕵️ Automatic anonymous bearer-token bootstrap and renewal for sync and async clients, enabling credential-free public reads and MCP research.

### Changed

- ⚡ `EksiClient.anonymous()` now obtains a usable token immediately; the async counterpart obtains it lazily on its first read without changing its API.
- 🔒 Account mutations now fail locally with a clear authentication error when an anonymous client is used.

## [1.1.0] - 2026-08-07

### Added

- ⚡ Async client, typed response models, pagination iterators, anonymous mode, proxy/TLS/fingerprint configuration, rate-limit metadata and mock sessions.
- 🔄 Access-token expiry tracking and refresh-token exchange with safe read-only retry/backoff; mutating requests are never retried.
- 🛠️ Previewable Python account actions for entries, reactions, topic/user state, messages, drafts, settings and trash, with input validation and secret-free audit events.
- 🔬 APK 2.4.10/build 144 JADX analysis, sanitized fixtures, expanded OpenAPI contract and opt-in read-only live contract test.
- 🤖 MCP `interactive` mode with signed, expiring, single-use previews and client-side human elicitation for every exposed account write. Default mode remains read-only.

### Changed

- 📱 Android authentication fingerprint updated from 2.4.4/build 137 to 2.4.10/build 144.
- 📦 Direct and development dependencies refreshed to their latest compatible releases.

## [0.1.0] - 2026-08-07

### Added

- Standalone Ekşi Sözlük authentication and Python API client.
- Safe public exception types, request timeouts, and response normalization.
- Optional `mcp` dependency extra and read-only stdio MCP server with 11 tools.
- OS keychain and environment-based credential providers.
- `eksi-auth` and `eksi-mcp` console commands.
- Python 3.10–3.14 CI, coverage gate, clean wheel installation checks, and linting.
- Trusted Publishing workflows for TestPyPI and PyPI, artifact attestations, and GitHub Releases.

[Unreleased]: https://github.com/agmmnn/eksiapi/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/agmmnn/eksiapi/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/agmmnn/eksiapi/compare/v0.1.0...v1.1.0
[0.1.0]: https://github.com/agmmnn/eksiapi/releases/tag/v0.1.0
