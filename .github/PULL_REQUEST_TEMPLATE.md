## What this changes

<!-- What a user would notice, or what boundary this moves. One or two sentences. -->

## Why

<!-- The reasoning, or the symptom this fixes. For a fix, say what the user saw:
     "double-clicking a panel did nothing" beats "handle None in build_manifest". -->

Fixes #

## How it was verified

<!-- Tick what you ran; delete what doesn't apply. -->

- [ ] `.venv/bin/python -m pytest`
- [ ] `cd web && pnpm test`
- [ ] `cd web && pnpm build` (this is the real type check — `tsc --noEmit` passes unconditionally here)
- [ ] `cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check`
- [ ] Tried it in the running app
- [ ] Not needed, because:

## Scope and review

<!-- Filled in before clicking "Ready for review". Policy:
     docs/engineering/codex-review-policy.md — normally at most two Codex rounds. -->

- [ ] **Scope is frozen** — no new work will be added to this PR; further findings become issues
- Codex round 1 reviewed commit: `                    `
- Codex round 2 reviewed commit: `                    ` (leave blank if one round was enough)
- [ ] A third round was requested, because: <!-- only a new P0/P1, a security issue,
      or a data-corruption change qualifies. Anything else -> issue. -->

**Disposition of every review thread** (each one needs one of:
`Fixed in <sha>` / `Deferred to #<n>` / `Guarded in <sha>, long-term fix #<n>` /
`False positive: <evidence>`):

- P0 / P1: <!-- must all be "Fixed in <sha>" — the gate fails otherwise -->
- P2: <!-- "Deferred to #<n>" is a perfectly good answer; the issue must already exist -->
- Deferred P2 issues opened: #

## Risk

- [ ] This PR introduces **no new user-facing capability** (feature freeze until v1.0)
- [ ] This PR does **not enlarge the state space** (no new override kind, no new
      persisted field, no new supported platform)
- [ ] Needs the full matrix (`full-ci`), because: <!-- delete if PR-lane is enough -->
- [ ] **Regression proof done** — every new structural gate was seen failing with the
      fix removed, and the output is pasted below or in a thread

<!-- A gate that never went red is a gate that guards nothing, and it reads as if
     it did. docs/1.0-release-readiness.md §3 has the full reasoning. -->

## Checklist

- [ ] A bug that only reproduces on Windows was turned into a case in `tests/test_windows_regressions.py` first, and that case was seen failing
- [ ] Nothing new imports `pymupdf` outside `src/tavotto/pdfbackend/`
- [ ] Nothing Flask imports gained a non-stdlib dependency (`engine/registry.py`, `pool.py`, `ai_bridge.py`, `config.py`, `updater.py`, `runtime.py`)
- [ ] Dual-source pairs changed on both sides — `engine/patchspec.py` ↔ `workerd/src/patchspec.rs`, `lib/richText.ts` ↔ `richtext.py`, `lib/shapeGeometry.ts` ↔ the geometry in `pymupdf_backend.py`
- [ ] A new Tauri command was registered in all three places (`build.rs`, `capabilities/main.json`, `generate_handler`) — miss one and the call is silently rejected
- [ ] A performance claim points at a number in `docs/perf-baseline.md`

<!-- None of these are style rules; each one cost a real bug to establish.
     CONTRIBUTING.md has the short version, CLAUDE.md the full reasoning. -->
