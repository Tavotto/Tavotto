# CodeQL finding reconciliation — 2026-10-08

Scope: findings on draft PR #853, initially published service revision
`bc76506341529f2d4b729ee458e50995306ea6d5`; the UTF-8-only follow-up
`e48d2d240f77274bcb784280556977d89a4d4b3d` did not address these findings.
A green CodeQL workflow is not evidence that its alerts are resolved.

## Alert 221: PKCE S256 challenge

[Inline finding](https://github.com/Tavotto/Tavotto/pull/853#discussion_r4219409946)
classifies the value hashed in `Application.route('/login')` as a password.
The source is `Store.oauth_start()`: `secrets.token_urlsafe(32)` generates a fresh
256-bit verifier. It is bound to the browser/state, expires after five minutes and
is consumed once. No user-supplied password reaches this expression.

[RFC 7636 §4.2](https://www.rfc-editor.org/rfc/rfc7636#section-4.2) requires the
SHA-256/base64url S256 challenge. Sections 7.1 and 7.3 distinguish its random verifier
from low-entropy passwords. A password KDF would break interoperability here.
The algorithm is retained; a production-code comment explains its purpose, and a
test exercises the actual HTTP login route against the public Appendix B vector,
checks all three random-token requests use 32 bytes, and confirms a real generated
verifier decodes to 32 bytes.

Assessment: qualified false positive for this exact dataflow, independently
reviewed. No blanket suppression, alert dismissal or algorithm substitution was
performed. This is not a statement about unrelated SHA-256/password uses.

## Alert 222: response splitting in the test OAuth peer

[Inline finding](https://github.com/Tavotto/Tavotto/pull/853#discussion_r4219409988)
is valid. The synthetic loopback `Peer` reflected decoded `redirect_uri` into a
`Location` header. An actual HTTP request with percent-encoded CRLF produced an
extra response header. The peer is a temporary test fixture; production signing
routes do not use this reflected redirect path.

The fix validates exact equality with the fixture-owned registered callback URL
before issuing a code, constructs `Location` from that trusted URL, and encodes
state/code as query values. Actual HTTP regressions cover CRLF and off-origin
redirect rejection, absence of injected headers/codes, and safe state encoding
with the legitimate callback. The negative test failed on the prior peer (exit 1).
The existing real OAuth/PKCE contract flow remains covered.

No existing assertion or CodeQL query was disabled. Live scanner confirmation and
maintainer triage are separate from this local source-based reconciliation.
