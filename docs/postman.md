# Postman public documentation

`openapi.yaml` is the source of truth for the Postman API reference. Do not maintain endpoint paths, descriptions or authentication rules separately in Postman.

[Open the published API documentation and collection](https://documenter.getpostman.com/view/24047519/2sBY4VLHxb).

## Generate the collection

Run the pinned official Postman converter:

```bash
./scripts/generate_postman.sh
```

The generated collection is written to `postman/eksi-sozluk-api.postman_collection.json`. Converter settings live in `postman/converter-options.json` and organize requests into one flat folder per OpenAPI tag.

## Authentication in Postman

The generated collection performs the reverse-engineered RSA authentication flow directly in Postman. Python is not required for manual requests in the Postman app.

1. Open the [public collection](https://documenter.getpostman.com/view/24047519/2sBY4VLHxb) and select **Run in Postman**, or import `postman/eksi-sozluk-api.postman_collection.json` from a source checkout.
2. Select **Vault** in the Postman bottom bar, then open **Local Vault → Settings** and enable **Allow Vault secrets in scripts**.
3. Open `Authentication / Get anonymous bearer token` and select **Send**.
4. When Postman requests Vault permission, select **Only this collection → Grant Access**.
5. If access was previously denied, open **Local Vault → Settings → Manage access**, grant access to the collection and send the request again.
6. Confirm that the response is `200 OK` and Local Vault contains `eksi-bearer-token`, `eksi-client-secret`, `eksi-client-unique-id` and `eksi-token-expires-at`.
7. Send a public request such as `Feeds / Today`. The collection pre-request script loads the bearer token and Client-Secret from Local Vault automatically.

The collection also overrides Postman's default `PostmanRuntime` user agent with `eksisozluk-android/144`. The mobile API rejects anonymous authentication when the mobile user agent is missing.

If anonymous authentication returns `500`, open the Postman Console with `⌘ Option C` on macOS or `Ctrl Alt C` on Windows and Linux. Verify that the outgoing request uses `User-Agent: eksisozluk-android/144`; `PostmanRuntime/2.2.1` is rejected by the mobile API.

For account login, create `eksi-username` and `eksi-password` secrets in Postman Local Vault, then send `Authentication / Login with account`. Use `Authentication / Refresh session` when the account token expires.

The collection stores generated values under `eksi-bearer-token`, `eksi-client-secret`, `eksi-refresh-token`, `eksi-client-unique-id`, `eksi-token-expires-at` and `eksi-account-nick`. These values are not written into the exported collection.

Postman Vault scripts work in manual HTTP requests and manual collection runs. Postman CLI, Newman, monitors and scheduled runs do not support `pm.vault`; use the Python SDK or disable the Vault events and supply CI secrets explicitly in those environments.

## Replace the stale collection

1. Import `openapi.yaml` into Postman Spec Hub from the repository or its raw GitHub URL.
2. Generate a new collection from the specification instead of manually reorganizing the existing Path-based collection.
3. Select `Fallback` request names, `Tags` folder organization and disable nested tag folders.
4. Enable inherited authentication, keep optional parameters disabled and exclude auth data from examples.
5. Enable collection update suggestions and remove orphan requests so deleted endpoints such as `/v2/user/me` do not remain in the collection.
6. Compare the result with `postman/eksi-sozluk-api.postman_collection.json` before publishing.

## Variables and credentials

The generated collection defines `baseUrl` from the OpenAPI server. Bearer tokens, refresh tokens, passwords, `Client-Secret` values and generated `Api-Secret` values must remain empty in every shared collection and environment.

Public reads require an anonymous or account bearer token plus the `Client-Secret` header. Account reads and writes require a logged-in account bearer. The collection is a browsable reference by default; authenticated requests require the RSA auth flow documented in the collection overview.

Do not attach a shared environment to public documentation unless every shared value has been reviewed. Local or Vault-backed secrets are safer for personal runs.

## Publish

1. Preview the complete documentation and verify the Authentication, Feeds, Topics, Entries, Profiles, Comments, Relationships, Messages, Drafts, Settings and devices, Notifications and Trash folders.
2. Confirm that `/v2/user/me` is absent and `/v2/user/{nick}` is present.
3. Search the preview for real email addresses, passwords, bearer tokens, UUID client secrets and refresh tokens.
4. Publish from a public workspace with the default Postman URL first.
5. Use `Ekşi Sözlük Mobile API Docs | eksiapi` as the SEO title.
6. Use `Unofficial Ekşi Sözlük mobile API reference with anonymous reads, authenticated account actions, Python SDK and MCP integration.` as the SEO description.
7. Add the public documentation URL to the repository only after the published page has been verified in a signed-out browser.

Published documentation follows collection updates automatically, but the generated collection must still be synchronized whenever `openapi.yaml` changes.
