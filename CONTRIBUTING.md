# Contributing to Tavotto

Thanks for taking the time. Issues and pull requests are both welcome — a good
bug report is worth as much as a patch.

## Reporting a bug

The most useful thing you can attach is the **diagnostics bundle**: in the app,
**Settings → Privacy, diagnostics and About → Download diagnostics bundle**. It
collects the version, your platform and encoding, how Tavotto was installed,
which Python interpreter is doing the rendering and what matplotlib it has, the
last errors and the log. Keys and personal paths are redacted before it is
written, so it is safe to attach to a public issue.

If the problem involves a specific figure, the *script* is usually more useful
than the PDF — and a cut-down script that still shows the problem is best of all.
Please don't attach unpublished research data; a minimal reproduction with fake
numbers is what we can actually work with.

## Getting set up

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py     # needs node + pnpm
.venv/bin/tavotto
```

`run.sh` does the same thing in one step. Note that there is **no `app.py` at the
repository root** — the entry point is `tavotto` (`src/tavotto/app.py`).

The frontend lives in `web/` (Vite + React 19 + TypeScript + Tailwind v4). While
working on it, `pnpm dev` is faster than rebuilding; just remember that a built
`src/tavotto/web/` takes priority over `web/dist`, so after running
`scripts/build_frontend.py` you should either re-run it or delete that directory
to get back to development mode.

## Verifying a change

```sh
ruff check .                      # Python lint (milliseconds — run this first)
.venv/bin/python -m pytest        # backend
cd web && pnpm test               # frontend (vitest)
cd web && pnpm build              # type-check + bundle
```

**Run `ruff check .` before pytest.** It comes back in about 20 ms for the whole
repository and catches the things that are cheap to find and expensive to wait
for — a misspelled name, an import left behind after a refactor, a local variable
nobody reads. `ruff check . --fix` applies the safe fixes; `--unsafe-fixes` can
change behaviour, so read those one at a time rather than applying them in bulk.
Ruff comes with `pip install -e ".[dev]"`.

The rule set lives in `[tool.ruff]` in `pyproject.toml` — deliberately a small,
high-signal one (`E4`, `E7`, `E9`, `F`, `I`) that is meant to stay green rather
than accumulate suppressions. Don't pass `--select` or `--ignore` on the command
line: that would make your run differ from CI's. Import sorting (`I`) is on, and
`ruff check . --fix` will sort for you; the formatter (`ruff format`) is **not**
enabled yet — see [docs/ci/ruff.md](docs/ci/ruff.md) for why and what's queued
next. CI runs the same `ruff check .` as the
`Python lint (Ruff)` job, which feeds the `CI fast gate`.

**Use `pnpm build`, not `tsc --noEmit`, for type checking.** The root `tsconfig.json`
is a solution file (`files: []` + project references); `--noEmit` doesn't follow
project references, compiles nothing, and passes unconditionally. The `tsc -b`
inside `pnpm build` is the real check.

Bigger changes have their own gates:

| Area | How to verify |
|---|---|
| Render engine | `tests/test_equivalence_matrix.py` — hot edit, full replay, fresh worker and reopen-after-write-back must all agree |
| End to end | `python scripts/smoke_app.py --python .venv/bin/python` |
| Desktop build | `python scripts/build_desktop.py`, then `python scripts/smoke_desktop.py --sidecar dist/Tavotto/Tavotto` |
| Golden path in a real browser | `cd web && pnpm e2e` (build the frontend first) |
| Rust supervisor | `cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check` |
| Telemetry / analytics | `pytest tests/test_telemetry.py tests/test_telemetry_api.py tests/test_telemetry_invariants.py tests/test_telemetry_proxy.py tests/test_distribution_metrics.py` — no test makes a real network request; `tests/conftest.py` pins `TAVOTTO_NO_TELEMETRY=1` |
| Distribution collector | `python scripts/collect_distribution_metrics.py --dry-run` |

Tests that need matplotlib spawn their own interpreter and skip cleanly if there
isn't one, so a `.venv` without the scientific stack still runs most of the suite.

### Working on telemetry

Nothing here needs a PostHog account. The test suites never make a real request:
`tests/conftest.py` pins `TAVOTTO_NO_TELEMETRY=1` for every test, and the telemetry
tests replace the transport with a collector.

```sh
# A local proxy instead of the production one. Validation and rejection paths work
# without a PostHog key; only the final forward needs one.
cd services/telemetry_proxy && python3 -m tavotto_telemetry_proxy.wsgi   # :8787

# Point a dev client at it, then opt in when the first-run prompt appears.
TAVOTTO_TELEMETRY_ENDPOINT=http://127.0.0.1:8787/v1/events tavotto

# Or make sure nothing is ever sent, whatever the saved setting says.
TAVOTTO_NO_TELEMETRY=1 tavotto

# Preview the distribution collector without transmitting anything.
python scripts/collect_distribution_metrics.py --dry-run
```

`TAVOTTO_NO_TELEMETRY` and `TAVOTTO_NO_UPDATE_CHECK` are independent switches; neither
covers the other. The event contract is in
[docs/analytics/telemetry-events.md](docs/analytics/telemetry-events.md) — it is
duplicated on purpose between client and proxy, with a parity test holding the two
copies together. Deployment steps live in
[services/telemetry_proxy/README.md](services/telemetry_proxy/README.md).

## Things that will get a PR sent back

These aren't style preferences — each one is a boundary that took a real bug to
establish. `CLAUDE.md` has the full list with the reasoning.

- **`import pymupdf` outside `src/tavotto/pdfbackend/`.** That package is the only
  module allowed to touch the PDF library; everything above it goes through the
  contract layer in `pdfbackend/__init__.py`. This is what makes the backend
  replaceable, and it matters for licensing.
- **Anything imported by Flask that isn't pure standard library.** `engine/registry.py`,
  `pool.py`, `ai_bridge.py`, `config.py`, `updater.py` and `runtime.py` run in a
  virtualenv that deliberately has no matplotlib. The scientific stack exists only
  in the worker process.
- **Writing to the package directory or the repository root at runtime.** Everything
  writable goes through `engine/config.data_dir()`. Installed as a wheel, site-packages
  isn't writable and this crashes outright.
- **Changing one side of a dual-source pair.** `engine/patchspec.py` ↔
  `workerd/src/patchspec.rs` (byte-identical, pinned by `tests/golden/patch_vectors.json`),
  `web/src/lib/richText.ts` ↔ `src/tavotto/richtext.py`, `web/src/lib/shapeGeometry.ts` ↔
  the geometry in `pdfbackend/pymupdf_backend.py`. Change both, or neither.
- **Adding a Tauri command without updating all three places** — `build.rs`, the
  capability file, and `generate_handler`. Miss the first two and the call is
  **silently** rejected at runtime.
- **`pathlib` in code that reasons about another platform's paths.** `Path()`
  dispatches on `os.name`, so constructing a Windows path on macOS raises. The
  runtime-location logic uses string operations throughout for exactly this reason.

## Two working habits

**A bug that only happens on someone else's computer becomes a test first.**
Encoding (cp936/cp1252), file locking, drive letters and backslashes, non-ASCII
paths, port conflicts, CLI shims — these go into `tests/test_windows_regressions.py`
as a *failing* test before the fix, because macOS and Linux will never reproduce them.

**Measure before optimising.** `docs/perf-baseline.md` holds the numbers and the
method (`python scripts/bench_render.py`). If a change is about performance, point
at a number in there. Two plausible-sounding optimisations are already recorded as
*rejected by measurement* — please don't re-litigate them without new data.

## Pull requests

Branch off `main` and open a PR; CI runs the backend matrix (Linux/macOS/Windows,
Python 3.10 and 3.13), the frontend, packaging, and a real Windows `.exe` smoke test.

Commit messages and code comments in this repository are written in Chinese;
user-facing text — the README, release notes, the interface — is English first,
with a Simplified Chinese README alongside. Either language is fine in a PR
description. What matters more is that the message says *why*, and that a fix
names the symptom a user would have seen.

## Licence

Contributions are made under [AGPL-3.0-only](LICENSE), the same licence as the
project.
