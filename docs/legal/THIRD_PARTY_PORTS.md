# Third-party protocol ports

Tavotto re-implements — in its own code, in its own structure — the *wire
protocol details* of two open-source clients, so that Tavotto can sign a user in
to a model vendor's account with the same device-code flow those clients use.
No file from either project is copied into this tree. The notes below record the
sources, their licences, and what was and was not carried over, so the
attribution travels with the code.

| Tavotto file | Reference (read-only) | Licence | What was taken |
|---|---|---|---|
| `src/tavotto/engine/ai_oauth.py` (generic RFC 8628 flavour; no vendor uses it yet) | `kimi-cli` — `src/kimi_cli/auth/oauth.py`, © Moonshot AI | Apache-2.0 | Handling details only: the RFC 8628 grant types, the handling of `authorization_pending` / `slow_down` / `expired_token`, and the 429 / 5xx refresh-retry policy. |
| `src/tavotto/engine/ai_oauth.py` (MiniMax flavour) | OpenClaw — `extensions/minimax/oauth.ts`, © the OpenClaw contributors | MIT | Endpoint paths (`/oauth2/device/code`, `/oauth2/token`), the `user_code` grant type, PKCE (S256) with `state` verification, the scope string, the "relative seconds / absolute seconds / absolute milliseconds" expiry reading, the 8 KB error-body cap and 30 s timeout, and "refresh only at the regional endpoint that issued the token". |

## What was deliberately not taken

* **Any client_id.** Both references embed their own vendor-registered client
  identifiers. Tavotto uses its **own** client_id (`ai_oauth.CLIENT_IDS`, or the
  `TAVOTTO_OAUTH_CLIENT_ID_MINIMAX` environment
  variable). While Tavotto has none for a vendor, that vendor's sign-in entry
  does not appear. `tests/test_ai_oauth.py` guards that no UUID-shaped constant
  and neither reference's identifier exists in the source.
* **Client identification headers.** Requests identify themselves as Tavotto
  and its version only; they never claim to be another client.
* **Device identifiers, host names, OS details and `X-Msh-*` headers**, which the Kimi reference
  sends. Its endpoints and client_id are not used at all (Kimi is not integrated through this flow).
* **A server-supplied `resource_url`** as the API base (the MiniMax reference
  honours one): Tavotto's base URLs are fixed per region so a response can never
  redirect an access token to a different host.
* **Token revocation.** Neither reference calls a revocation endpoint at logout.

## Licence texts

* Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>
* MIT: <https://opensource.org/license/mit>

Because only protocol facts (URLs, parameter names, grant identifiers, numeric
thresholds) and behavioural ideas are reused and the code is written afresh, no
upstream licence text is vendored; if any upstream file is ever copied in, its
licence header and a `NOTICE` entry must be added here first.
