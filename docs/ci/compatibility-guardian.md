# Compatibility Guardian

Guardian is a specialized evidence runner over existing CompatBench and
V2/Foundation harnesses. It does not replace `agent_verify`, a worker, environment
resolution, product support classifications, or the existing CI gates.

```sh
python scripts/ci/compat_guardian.py run --suite compat_v2 \
  --case s01_plain,s04_replace_readback,s06_savefig_pil_readback \
  --expected-sha "$(git rev-parse HEAD)" --out /tmp/cg-results
python scripts/ci/compat_guardian.py run --suite compat_engine \
  --case shape_oo_savefig,shape_pyplot_show_only,shape_local_helper_import \
  --expected-sha "$(git rev-parse HEAD)" --out /tmp/cg-results
python scripts/ci/compat_guardian.py validate /tmp/cg-results/RUN_ID/reports/guardian.json
python tests/test_compat_guardian.py
```

Omit `--case` to run all registered cases. Outputs must be outside the source
worktree. Each invocation creates a UUID directory with an atomic terminal report
and hash-referenced raw evidence. The versioned public contract is
`tests/compat/guardian-report.schema.json`. Exit codes: 0 complete scoped success,
1 independently supported failure, 2 invalid invocation/evidence, 3 incomplete or
unavailable qualification. `validate` checks evidence validity, not a passing verdict.
It binds both actual HEAD and all tracked/untracked nonignored source content;
uncommitted code cannot borrow main's identity.

Only committed, unchanged registered fixtures AND execution metadata execute.
Generated/untrusted scripts are not accepted. All launches have private temporary
HOME/config/data/cache; credential environment variables are not inherited.
This is not OS isolation. Dependencies/fonts must already be prepared by an
authorized developer using repository locks; the runner never installs them.

The engine adapter uses `compat_matrix.run_group`, native `compat_driver`, and
the existing stage oracles. Real module-exec events count source-hash-matched
scripts. Native, Tavotto and diagnostic native/census counts are separate. Each
actual execution records interpreter/package/environment facts; mismatches or
missing events prevent a success claim. Engine product-route tests are **not run**
by this adapter: existing dedicated route tests remain authoritative.

The V2 adapter uses `compatbench_v2.observe` and its authenticated preparation
sessions. It checks the explicit IDEAL oracle and native titles, preserves the
fixture's external execution log, and verifies it against module-exec events.
Its edit/replay/export/fidelity stages remain `not_run`, so it cannot qualify a
complete nine-stage pass. Historical baseline matches are not successes.
The Safe API cannot establish desktop Native fallback: fixtures whose IDEAL
requires Native must not be reported as desktop regressions. The current missing-
dependency V2 precondition also rejects the runner's explicit worker override;
those scenarios remain environment_unavailable until interpreter equivalence is
verified by the existing harness.

Native errors are classified before Tavotto attribution: missing dependencies
are environment_unavailable, native script exceptions are invalid_fixture,
missing trustworthy output is unknown. No graph/exit0/HTTP200 alone qualifies.
The current native semantics supplement samples titles, not general scientific
arrays. Existing engine semantics are roles/editable fields and fidelity is
grayscale. Scientific values, save-occurrence snapshots, full RGBA fidelity and
real WebView2/WKWebView require their own evidence. Bitmap layout does not imply
Artist editing.

CG-00 findings and the acceptance ledger are in
`docs/audit/compatibility-guardian-architecture.md`. No baseline is updated and no
candidate is promoted automatically. Controlled fixtures are not a sample of
real-user success rates.

The existing hosted `compat-smoke` job runs two observation steps after its locked
dependencies/fonts are prepared: engine OO/savefig, show-only, relative CSV,
helper import, multi-Figure capture and Pillow; V2 plain, replace/readback,
savefig/Pillow readback, installed Pillow and native-error/no-figure/missing-CV2
controls through the authenticated Safe server. Each step
retains the actual CLI exit and validates the terminal report against the exact
checkout, then uploads the entire UUID report/evidence tree even on failure.
`continue-on-error` does not change a report verdict: V2's four unrun stages still
prevent complete qualification. These steps add no gate, runner or dependency
manager. Only unchanged committed fixtures run; checkout credentials may still
exist in Git configuration, so this job cannot execute external/generated scripts.
Interrupted/negative adapters preserve any execution logs before temporary data
is removed.
The broader controls can produce environment_unavailable, invalid_fixture,
unknown or Safe-only failures. A Safe-only failure does not establish a desktop
regression or authorize a product repair. The generic process lifetime wrapper
reuses package_smoke; the actual server/authentication remains Foundation's.

## First cloud run and limits (2026-10-16)

Source before commit: main `7675956233a812faee398eb9d5dede56e1b8329d` plus dirty
content digest `8c487cd26e3cf1d9ebcc69488cb5690e2ff7cadcae1d0ec1176960971b7e1b5a`.
15 V2 native controls and three engine native controls each executed once.
The V2 native ZeroDivisionError/NameError cases were invalid_fixture, no-figure
was unknown, and other product qualifications were environment_unavailable.
Both aggregate CLI exits were 3. Tavotto executions were null/unobserved, not zero
or success. Evidence run IDs: `0bab6f2413af4f178ff6b6d4ba5d0c3a` and
`d8e5046840044b8982e643fdb8b5b005` (local `/workspace/compat-guardian-evidence`).

Authorized isolated venv creation succeeded. Installing existing declared
dependencies from official PyPI failed with a proxy tunnel error at
`https://pypi.org/simple/numpy/`; locked Liberation Fonts download failed with
CONNECT 403. No alternate registry, credential extraction or environment bypass
was attempted. Pytest, Ruff and full product qualification remain blocked.

Standard-library contract suite: 36 tests passed. Four isolated, witnessed
mutations (current worktree binding, schema validation, fixture digest and native
missing-dependency classification) all exited 1 under the tests. These are
validator mutations, not product correctness evidence. Independent read-only
review accepted an observation-only Draft; it did not approve merging or claim
scientific/desktop acceptance.
