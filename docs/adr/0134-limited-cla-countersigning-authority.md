# ADR 0134 — Explicit limited authority for candidate CLA countersigning

Status: engineering candidate, default disabled; real authority and deployment pending approval.

The existing native CLA service requires both people to sign personally. A request
to continue engineering does not approve CLA terms, authorize an agent to sign,
or establish that a GitHub account represents a legal person.

The contributor's session, CSRF, origin, single-use intent, exact agreement and
personal declarations remain mandatory. Config.autoHolder remains false.
Personal holder signing remains available. Delegated execution uses separate
encrypted receipts and never manufactures a personal name or signature.

A future rights holder must separately approve and sign a canonical authorization
for one repository, holder identity, individual agreement version and SHA-256,
purpose, adult/independent/own-rights scope, and start/end times. A separately
approved identity-to-public-key registration must pin the holder's Ed25519 key
and authorization ID. Cryptographic verification alone does not establish legal
identity or authority. No key registration, authorization or real signature is
created by this change. The unsigned template is deliberately not importable.

Grant registration does not enable execution. Signed enable/suspend/revoke
controls form a bounded, immutable sequence and predecessor-hash chain. Revocation
is terminal. Grant/control preparation APIs have no HTTP routes. Each change
increments the existing evidence revision atomically. A receipt records the
exact grant, latest enable control and actual contributor evidence hash; its
transaction rechecks revision, packet identity/deadline, holds and current control.
Qualification verifies contributor evidence, grant signatures, the entire bounded
control chain and encrypted receipt anew. Turning the capability off, suspension,
revocation, expiry or a changed binding removes delegated technical eligibility.
Proof expiry is capped by authorization expiry. No old proof is extended.

Personal holder signatures retain their existing independent eligibility rules.
An automatic receipt never occupies their immutable database slot. After a
deadline, an expired incomplete personal packet can be replaced using the existing
fresh-signature process; consent is never copied. A hold and withdrawal of
automation authority are engineering controls, not legal conclusions about an
existing contract.

The new-head candidate accepts bounded, HMAC-verified GitHub PR locators for the
exact repository and installation. It deduplicates delivery IDs/body hashes,
re-reads current GitHub bindings on admission and evaluation, and commits a
new observational result under head, revision, attempt and lease guards. Evidence
revision changes atomically clear completed/running observations and enqueue a
new evaluation. Manual failures remain manual. It does not send GitHub checks,
provide a CI eligibility endpoint, or reuse an earlier observation as proof.

Automatic observations use a separate reserved cron invocation
(`7-59/15 * * * *`); the original recovery invocation remains separate. Each
invocation uses the existing 20 work / 40 total SQL budget. No cron or other
Cloudflare resource is configured here. An operator must preserve the recovery
trigger and verify both triggers before any future activation.

Delegated fallback requires six SQL reads per non-exempt person. Read-only
verification admits at most two; a scheduled new-head observation currently fits
one. Existing same-head publication refresh cannot fit any non-exempt contributor
with this mode enabled (16 + 6N > 20); it defers into bounded manual recovery.
The global flag conservatively applies that accounting even to personal signatures.
The ceilings are unchanged, and this limitation is not a legal verdict.

This PR does not activate the provider, change legal texts/hashes, connect CI,
implement delegated signing UI/audit export, configure an App/OAuth or deploy
resources. Those remain separate review/approval gates. The trusted-main CI
bootstrap requires code to land before wiring; native merge-group proof support
is still absent. Offline tests do not establish legal effect or live acceptance.
