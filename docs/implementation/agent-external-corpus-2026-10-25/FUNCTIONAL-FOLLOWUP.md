# Functional conversion and repair acceptance

The latest acceptance order is **complete the application workflow first, then measure visual fidelity separately** (maintainer instruction, 2026-10-25). This report supersedes the previous pixel-first prioritization, not its negative evidence. Default executor routing remains unchanged. No release or production feedback was submitted.

## What the evidence establishes

The frozen source-only matrix at `77f865a3c7367c1e3c3f8423d8d0c54a05206a1e` used the same 23 external cases, builtin Sonnet 5.5, unchanged original inputs and a preinstalled Python runtime. It supplied no additional native geometry or SVG answers. Auto-install was disabled in this matrix; dependency installation is measured separately below.

| Source | Completed, adopted, and reopened in a new application process | Other first-round results |
| --- | --- | --- |
| Pillow | 10 / 11 | Composite failed during correction |
| R | 5 / 6 | Lattice intervals requested missing dataset and factor metadata |
| MATLAB | 6 / 6 | None |
| Total | **21 / 23** | **1 failed, 1 needs input** |

At `aeaee38953d853017dc675ca4584b453dd5c7f12`, the same Pillow composite input completed, adopted and reopened with **6 captured figures**. The R intervals input again correctly requested actual data; supplying an independently exported `lattice::singer` CSV and factor metadata through the public `reference_files` endpoint produced **2 captured figures**, adopted and reopened. The original scripts and protected inputs remained unchanged. The new R candidate's 8-category order and 24 median/confidence values match native R; maximum absolute numeric difference is **4.9738e-14**. This is one fixture's statistical check, not general R semantic certification.

Thus every case in this corpus now has a completed application path, one requiring additional user data. This combines a frozen matrix and explicit follow-ups; it is **not a 23/23 first-pass result at one final revision**. This round retains **27 conversion attempts** (23 completed, 1 failed, 3 needs-input) across 23 cases. Repeated attempts are not new independent samples. One duplicate-reference-copy harness failure occurred before any model request and is recorded separately.

The newer guidance-only change at `022a026bef9a4e6d541fadf43b9da25aa575fe06` requires copyable input-export commands, working locations and actual output names. A real R needs-input run retained the complete command on its card, and native R executed that exact command successfully to export the CSV and factor-level file. Its prose still names one file inconsistently before correcting itself. **Command execution passes; complete guidance accuracy does not.** The ordered-factor flag is explicitly printed to the console, with instructions to save it. No per-script command substitution was inserted.

## Common fixes and standards

The contract is centralized in [ADR 0143](../../adr/0143-convert-to-editable.md), with shared requirements consumed by builtin and CLI conversion paths:

1. Preserve project data, helper modules, fonts and relative-path meaning; never depend on a temporary candidate cache after adoption.
2. Preserve calculations, units, missing-value handling, categories and mathematical meaning. Missing real data or an unreliable statistical mapping still requires input.
3. Produce all intended editable figures/pages. Original photos and their pixel transformations may remain images; painted text/shapes cannot replace editable objects.
4. Prepare the candidate's Python dependencies through the same environment selection, approval, cancellation and resume mechanism for Pillow, R and MATLAB sources.
5. Record geometry, palette, font and antialiasing differences separately. A successful run is not pixel fidelity or scientific correctness certification.

Four shared workflow defects were exposed by the matrix and repair trials:

- Pillow conversion previously skipped the dependency preparation hook used by R/MATLAB. All three sources now enter the same hook; tests cover approval holds, resuming the same candidate and adoption through the real worker.
- The builtin conversion child rejected its own existing candidate on an outer correction restart. The parent now authorizes only the previously observed candidate bytes by SHA-256; links, changed bytes and invalid paths are refused. The three tool-check allowance is preserved across restarts.
- Read tools displayed home-relative resource paths, but `run_command` passed `~/...` literally to a non-shell process. Paths are now expanded before policy checks, approval and execution. The card and process use the same normalized arguments; no shell expansion or permission relaxation is added.
- Needs-input parsing kept only the marker's first line, losing following code blocks. Complete bounded commands now retain line breaks in the card body. Overlong commands are not silently truncated; a single-line reason keeps its existing presentation.

These fixes apply to capabilities, not named corpus scripts. The end-to-end correction test uses a real builtin child and safe worker with scripted model responses, and asserts successful adoption without resetting the check budget. Removing each fix made its new regression fail (exit 1); restoring it passed. The dependency-hook counterfactual failed the two Pillow cases while four R/MATLAB controls still passed.

## Environment and compatibility repair

All rows below use isolated case environments and real model/tool calls. Actual cards were reviewed before mutation. Fresh checks start a new application process without forcing the worker interpreter. Manual approval waits are included in elapsed times, so timings are not speed rankings.

| Controlled condition | Producer | Confirmed result |
| --- | --- | --- |
| Missing seaborn in unchanged upstream Python script | builtin, `77f865a3c` | Exact package plan; installation, actual app capture and fresh reopen pass |
| Missing Pillow project font | builtin, `aeaee3895` | Reviewed working directory and exact font copy; native reference comparison passes; actual app and fresh reopen execute; correct **conversion handoff**, no Matplotlib capture claimed |
| Missing SciPy in adopted MATLAB-to-Python Airy script | builtin, `77f865a3c` | Exact package plan; installation, actual app capture and fresh reopen pass |
| Missing pandas in adopted R-to-Python script | builtin, `aeaee3895` | Exact package plan; installation, actual app capture and fresh reopen pass |
| Same missing-pandas condition and candidate bytes | Codex, `aeaee3895` | Real Tavotto MCP callbacks, exact package plan; installation, actual app capture and fresh reopen pass |

The older `77f865a3c` missing-font attempt also eventually succeeded, but needed extra command attempts before the home-path fix. It remains in the ledger. The rerun first encountered the expected sandbox write denial, then requested a separate outside-sandbox confirmation and copied the exact supplied font successfully; no permission guard was weakened.

This round retains **6 model repair attempts plus 1 harness setup failure**. The missing-Pillow setup selected a usable application fallback before the agent started, violating the harness requirement to select its isolated test environment. It is excluded from agent success/failure counts. For generated MATLAB/R Python cases no native pixel oracle was supplied: the old `independent_repair_pass=false` field is explicitly inapplicable to runtime acceptance. For Pillow, `figure_captured=false` is expected before conversion and does not negate successful original execution.

The previous three-executor exact Pillow-version and missing-image comparisons remain in [FIDELITY-FOLLOWUP.md](FIDELITY-FOLLOWUP.md). Together these cover several real faults, not arbitrary user environments. Native R package installation and MATLAB toolbox repair are **not** product-supported paths; these trials repair their converted **Python** environments. Builtin uses Sonnet 5.5 and Codex uses subscription GPT-6 Astra, so the paired success does not establish general non-inferiority.

## Review follow-up: completed attempts are not necessarily successful repairs

[Review comment 4371332802](https://github.com/Tavotto/Tavotto/pull/1421#discussion_r4371332802) exposed a false-success predicate: a runtime missing dependency sets `execution_finished=true` while its authoritative outcome is `needs_dependencies`. Repair evidence now additionally requires `completed/succeeded` or the intentional `partial/execution_finished_no_figure` conversion handoff. Missing dependencies, failed registration, partial capture and absent outcomes cannot authorize `finish(fixed)`.

All five new negative regression cases failed against the previous implementation. A real preparation-action integration test reproduces the missing-package report; the existing real pip/worker round trip now also verifies that installing the dependency is insufficient until a new successful script attempt completes. Real Pillow font/output-directory tests retain the successful no-figure handoff.

The focused repair/preparation/MCP/import-architecture gate passed **797 tests, with 2 Linux-only sandbox skips** on macOS. The first invocation incorrectly pinned the worker interpreter and supplied a relative source path to child processes, causing 15 dependency-fixture failures and one MCP child failure; correcting the invocation passed without weakening those checks. Full-repository Ruff/format and rule-document budgets also passed.

The six archived model-repair reports above were re-evaluated with the stricter predicate: four have `completed/succeeded`, and the two font attempts have `partial/execution_finished_no_figure`. All six remain valid execution evidence. This is a re-audit of the frozen reports, not six new model trials; their revisions and attempt counts are unchanged.

Windows CI then exposed two test portability assumptions: a fixture written with native CRLF was compared with LF bytes, and a valid forward-slash command argument was compared with an assumed backslash spelling. The conversion process and resource copy had already succeeded. The tests now exercise both LF and CRLF originals with byte preservation at rejection, correction and stale-authorization stages, and compare the confirmation card with the actual spawned argv while independently checking file identity and copied bytes. The CRLF case reproduced the original assertion failure on macOS before the assertion fix. The conversion-runner, command-tool and Windows-regression suites pass **360 tests, with 6 platform-specific skips** on macOS; production behavior and routing are unchanged by this test-only follow-up.

## Verification and remaining limits

- Backend: **722 passed, 2 skipped** at `aeaee3895` (opt-in real CLI discovery and Linux-only bwrap). Guidance follow-up: **200 passed, zero skipped**.
- Frontend: **11,356 passed across 600 files**. The first full run hit one existing 50 ms wall-time assertion under concurrent build/test load; the complete rerun passed with four workers and no threshold/code change. The focused confirmation-card suite passed 299 tests.
- Chromium: **7 passed**, covering explicit reference retry and private diagnostic confirmation before network. The feedback tests use a loopback sink; no production report was sent.
- Full-repository Ruff/format, frontend type/build/Tailwind, i18n, playground/MCP fingerprint checks and guidance budgets passed. These are development validation, not a shipped desktop package.
- Remote CI must be read for the current PR head. Older green runs are not evidence for a later commit.

Unresolved: universal input/command accuracy; broad real-data formats and dependency combinations; all scientific/statistical mappings; MATLAB tick/layout/surface pixels; complex glyph pixels across renderers; repeated same-model executor comparisons; installed Windows/Linux acceptance. The application workflow and visual/scientific acceptance remain separate facts. Default routing stays unchanged.

Whitelisted per-attempt records and hashes（存档，见 [EVIDENCE.md](EVIDENCE.md)） contain the frozen matrix, follow-ups, repair confirmations, source hashes, fresh-process checks, semantic oracle and known failures. Raw logs remain local and are not published.
