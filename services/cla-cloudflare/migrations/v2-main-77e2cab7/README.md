# Frozen repo-v2 source profile

These two SQL files are byte-for-byte copies of `src/base.sql` and
`src/native/schema.sql` at commit `77e2cab77597318859f874d9319c47f15da0e1dd`.
The Store at that commit already uses `cla_native_v2_`. They are not v1 schema
fixtures and are not an attestation of the current schema of any live database.
A read of main `0991bb9501489395a38542c94867fcfeffbdf196` matched both files.

The source/target hashes are pinned by `scripts/v2-upgrade-offline.mjs`.
Only synthetic in-place rehearsal is available; the existing separate v1 tool
and its frozen schemas/default whitelist are unchanged. See
`docs/implementation/cla-v2-upgrade/README.md` for supported shape, quiescence,
preservation, transaction rollback and live-acceptance limits.
