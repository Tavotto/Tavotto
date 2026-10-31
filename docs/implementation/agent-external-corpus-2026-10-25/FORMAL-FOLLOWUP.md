# Formal-budget follow-ups after funding restoration

2026-10-26. These are follow-ups to the frozen [23-case first pass](FULL-CORPUS.md), not replacements for its failures or a new 23/23 pass rate. Public structural receipts and local-evidence hashes are in FORMAL-FOLLOWUP-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)）. Source scripts, model transcripts, commands and credentials remain local. Default routes and production budgets are unchanged.

## Same-condition follow-ups

Producer `3a7e20eb6a762dd1fe656c140bcb585bbc2f12d6`; the same DeepSeek `deepseek-flash`, native Responses protocol, source manifest, conversion runtime, reference delivery and provider-default effort. Conversion limits remain 32 turns / 800,000 cumulative tokens / 900 seconds / 32,768 output tokens per response. Each row below is one follow-up attempt.

| Case | Actual result | Fresh application reopening |
| --- | --- | --- |
| MATLAB bounded NaN | Completed and explicitly adopted | Succeeded, one figure |
| MATLAB shaded error bar | Completed and explicitly adopted | Succeeded, one figure |
| R percentage stack | Completed and explicitly adopted | Succeeded, one figure |
| R sinc | Two output-truncated responses, no candidate created | Not applicable |
| R plotmath | Two output-truncated responses, no candidate created | Not applicable |
| R lattice intervals | Requested genuine package-owned data with a complete export command | Data-assisted follow-up below |
| R lattice labels | Reported completed/adoptable, but the generated main entry contained an `Axes.y0` error | **Failed; this is a product false positive, not a pass** |

The two MATLAB failures from the first pass therefore have successful same-budget follow-ups. R sinc and plotmath still fail before producing code. Neither reaches the 900-second deadline, so increasing elapsed time alone does not address these observed failures.

## Real data, dependencies and confirmation closure

The intervals model produced a 1,552-character input guide, fully retained in the input card. Its command was executed unchanged in native R and exited 0, exporting **235 genuine rows and eight factor levels**. No values or Python candidate were supplied by the evaluator. The resulting files entered through the public `reference_files` endpoint; the new model conversion completed, was explicitly adopted, and reopened with **two figures**. This is data-assisted success, separate from the no-input trial. The export preserves the requested level order; it is not a general guarantee for every R factor attribute.

| Repair scenario | Confirmed result |
| --- | --- |
| Missing seaborn | Reviewed plan and separate run card; completed/succeeded and fresh capture |
| Converted MATLAB missing SciPy | Reviewed plan and separate run card; completed/succeeded and fresh capture |
| Converted R missing pandas | Reviewed plan and separate run card; completed/succeeded and fresh capture |
| Project-external Pillow image | Reviewed cwd and data mapping, original execution, conversion in the same persisted project, adoption and seven fresh captures |
| Removed Pillow API | Reviewed isolated Pillow 9.5.0 compatibility plan, original execution, conversion handoff after funding restoration, adoption and two fresh captures |
| Missing font | Read/list/find/grep confined to a workspace excluding observation logs; seven individually reviewed cards, exact supplied-font copy, cwd correction and original execution; conversion handoff, adoption and one fresh capture |

All six fault scenarios now have an observed execution-to-capture closure. These are controlled cases, not evidence that arbitrary packages, datasets, native R installations or MATLAB toolboxes can always be repaired. Package absence was verified while installation cards waited. Source bytes were unchanged. No model was used during fresh reopening and no Python interpreter was forced there. The first font diagnostic with observation contamination remains excluded.

The real-model tests exercised actual app endpoints, task/card state and workers. Separately, an actual Chromium test of the built application passed: a complete guide over 2,000 characters remained visible; no reference retry was sent before the user action; invalid reference input could be corrected. Only the model response was mocked in this browser test. Existing confirmation/store tests passed 417 cases.

## Primary-entry validation defect and repair

Generic probing tried another entry after `main` failed. In the labels candidate it reached a helper which drew a partial figure, causing conversion to claim success while reopening at `main` still failed. Conversion now pins the first entry selected by the existing shared discovery logic. The original execution failure reaches the agent's bounded correction path; an auxiliary function's output cannot certify the complete script. Ordinary multi-entry material probing remains available.

Two real-worker regressions failed against the old implementation: a broken main with a working helper, and an empty main with a working helper. Both pass after the fix and refuse adoption. The existing child-agent correction regression now also contains a working helper; it still requires actual correction before adoption. The 29-test conversion application suite and 238 related conversion/probe/rules tests passed.

Replaying **22 unedited, previously adopted candidates** on `79ff5faeb598572ba9d24aae0689b4b2ac4ab75a` gave 21 successful checks and correctly rejected the erroneous labels candidate with `script_error`. This is a gate replay, not 22 new model successes. A fresh labels model trial on that revision exhausted two responses before writing code; it remains failed and does not demonstrate real-model correction of this particular candidate.

## Effort ablation, kept separate

One diagnostic per repeatedly truncated R case changed only the explicit effort to `low`; model, source, protocol and all formal limits stayed fixed on `3a7e20e`.

- R sinc completed and reopened with two figures.
- R plotmath still exhausted two responses before creating a candidate.

These are not default-condition passes. They show that a thinking setting can influence one case but does not solve the entire failure class. No provider-specific default, route or global budget was changed.

## Integration and remaining limits

Main subsequently merged #1419, which adds figure-edit checks to the shared runner. Integration preserves its explicit registered entry and data fingerprints, conversion's complete-script validation, one shared check counter, candidate-resume budget, image evidence and cancellation watchdog. Figure-edit checks retain the original output identity and cannot apply conversion-only output renaming; registered outputs remain owned by the original source. Current integration checks, model smoke results and exact-head CI are recorded on [PR #1421](https://github.com/Tavotto/Tavotto/pull/1421).

Local integration verification passed 317 initial targeted tests, then 609 wider runner/bridge/conversion/edit/cancellation/dependency/rules tests with one opt-in real-CLI detection smoke skipped. These suites overlap and are not summed. Two new registered-savefig tests passed; independently removing either the edit/convert normalization boundary or the original output-owner mapping makes them fail (exit 1). Full Ruff/format and rule-budget checks passed. This is source integration evidence, not a new 23-case model batch.

The frozen first pass remains **16/23** successful captures with its failures and quota blocks retained. Follow-ups are not pooled into that success rate. Default-effort R truncation remains unresolved, and this set does not establish pixel/scientific fidelity, general compatibility or non-inferiority to Codex/Claude. No release or application package was made.
