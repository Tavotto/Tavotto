# Full external-corpus run with formal budgets

This is the frozen first-pass snapshot. Funding was subsequently restored; see
[FORMAL-FOLLOWUP.md](FORMAL-FOLLOWUP.md) for the separately recorded follow-ups and newly found validation defect.

2026-10-26. Frozen producer `79baa11824e379b24a1dead51537d0cacde823a5`, DeepSeek `deepseek-flash`, native Responses protocol. **Default routing is unchanged. No merge, release or package was produced.**

This is one first-pass run over all 23 external cases (11 Pillow, 6 MATLAB, 6 R), using the same input manifest, reference delivery, preinstalled conversion runtime and formal product budgets. Each case was attempted once. No pixel/scientific-equivalence claim is made. Failures are retained in FULL-CORPUS-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)）; original provenance remains in [sources.json](sources.json).

## Results

**16 completed, adopted and reopened in a fresh application process; 3 model budget failures; 1 missing-real-data request; 3 blocked by API balance.** All 23 preserved protected source/resource bytes. The 16 successful fresh runs captured 47 figures.

| Language | Fresh-process success | Model budget failure | Needs real data | API balance blocked |
| --- | ---: | ---: | ---: | ---: |
| Pillow | 11 | 0 | 0 | 0 |
| MATLAB | 4 | 2 | 0 | 0 |
| R | 1 | 1 | 1 | 3 |

| Case | First-pass result | Fresh captures |
| --- | --- | ---: |
| `matlab_airy_surface` | completed | 1 |
| `matlab_bounded_nan` | model_budget_failure | — |
| `matlab_bounded_syntax` | completed | 1 |
| `matlab_contour_over_image` | completed | 2 |
| `matlab_multi_colormap` | completed | 1 |
| `matlab_shaded_errorbar` | model_budget_failure | — |
| `pillow_book_pieslice` | completed | 1 |
| `pillow_book_polygon` | completed | 1 |
| `pillow_book_text_opacity` | completed | 1 |
| `pillow_circular_thumbnail` | completed | 2 |
| `pillow_nkmk_absolute_font` | completed | 1 |
| `pillow_nkmk_composite` | completed | 6 |
| `pillow_nkmk_paste` | completed | 9 |
| `pillow_nkmk_putalpha` | completed | 4 |
| `pillow_nkmk_rotate` | completed | 11 |
| `pillow_nkmk_shapes` | completed | 4 |
| `pillow_official_anchors` | completed | 1 |
| `r_base_plotmath` | model_budget_failure | — |
| `r_base_sinc` | quota_blocked | — |
| `r_gallery_bar` | completed | 1 |
| `r_gallery_percent_stack` | quota_blocked | — |
| `r_lattice_intervals` | needs_real_input | — |
| `r_lattice_labels` | quota_blocked | — |

## Budget and attribution

Conversion used **32 turns / 800,000 cumulative input+output tokens / 900 seconds / 32,768 output tokens per response**, with three checks and the existing cancellation/safety rules. There were no overrides. Missing-package repair used **40 turns / 600,000 tokens / 1,800 seconds / 4,096 response tokens**; card waiting counts toward the repair deadline.

- MATLAB `bounded_nan` and R `base_plotmath` exhausted one response and the single permitted recovery response, mostly in model reasoning before creating code. No incomplete call was executed.
- MATLAB `shaded_errorbar` created a candidate but a real check found a shape mismatch, `(80,)` versus `(30, 80)`. After edits, the next check would exceed the 800k cumulative token budget; it was not accepted or adopted.
- All three finished below 366 seconds. **None reached the 900-second limit. Raising time alone would not fix these observed failures.** No evidence here justifies another global budget increase.
- R `lattice_intervals` correctly refused to invent package-owned data. Its final export command incorrectly qualified `writeLines` as `utils::writeLines`; executing that exact command in native R failed. This is a model command error, separate from the product display defect below.
- DeepSeek returned HTTP 402 for `base_sinc`, `gallery_percent_stack` and `lattice_labels`; the last two received no model turn. These are unfinished trials, not model/agent capability failures. The service [documents HTTP 402 as insufficient balance](https://api-docs.deepseek.com/quick_start/error_codes/).

## Data, dependencies and confirmation cards

| Scenario | Confirmed actions and actual result | Fresh-process result |
| --- | --- | --- |
| Missing seaborn | Reviewed installation plan, then a separate real-script run card; authoritative completed/succeeded | Passed, one figure |
| Converted MATLAB missing SciPy | Same two-card sequence; original script unchanged | Passed, one figure |
| Converted R missing pandas | Same two-card sequence; original script unchanged | Passed, one figure |
| Missing external Pillow mask | Reviewed project cwd, actual run, exact data link and second actual run. Original Pillow execution completed and handed off to conversion. Same project/config/mapping used for conversion and explicit adoption | Passed, seven editable captures |
| Pillow removed API (`Image.ANTIALIAS`) | Confirmed project-local Pillow 9.5.0 plan and real run. Original source execution completed and offered conversion | Subsequent conversion blocked by HTTP 402; not claimed complete |

For the three package plans, the package was checked absent while the confirmation card waited. Only the displayed package plan was approved; actual execution required its own card. Every recorded answer returned HTTP 200. Reopening used a new application process, no model and no forced Python interpreter. These are actual backend card/session/worker flows; the separate four-file frontend suite passed 417 tests, including waiting, answer, cancellation and explicit reference-retry behavior.

The first missing-font diagnostic reached a real font copy and working-directory confirmation but could read its own local test transcript. It is excluded from independent capability counts. The corrected harness places observation files outside the model read/list/find/grep scope; that rerun received HTTP 402 before its first model turn. An earlier setup typo also stopped before any API request. Neither is counted as a model failure or pass.

## Product correction and data follow-up

The real R response was slightly longer than the old 2,000-character reason limit. Tavotto replaced the whole guide, including file names and export commands, with a generic request to shorten it. The input card now accepts complete guidance up to 8,192 characters, with the existing no-truncated-command behavior above the bound. The shared prompt asks for concise missing-input instructions and uses the actual R function namespaces, including `base::dput(..., file=...)`, `utils::write.csv`, and `base::writeLines`. It does not embed a corpus-specific script or dataset workaround.

A new Task Core regression proves that a complete guide longer than 2,000 characters reaches the needs-input card and cannot adopt a draft. It failed against the previous limit. The existing over-limit rejection remains covered. These code changes were made **after** the frozen matrix and have not received a new DeepSeek model run because balance was exhausted.

The R export was separately corrected and executed in native R: 235 genuine observations and metadata for eight factor levels were written, with the original source unchanged. Supplying these files through the real `reference_files` endpoint was attempted; HTTP 402 stopped the model before turn one. This data-assisted follow-up remains unvalidated and is not merged into the first-pass success count.

## Local regression verification

495 targeted backend tests, 52 architecture/rule tests and 417 frontend confirmation/store tests passed. Full Ruff/format, diff and rule-budget checks passed. The first backend command had one real-MCP-child failure from using the other checkout installation; rerunning the full targeted suite with an absolute worktree `PYTHONPATH` passed. No product patch was made for that harness invocation problem.

The new complete-guide regression failed before the reason-limit correction and passed afterward. Over-limit commands are still not truncated into executable guidance. Remote CI belongs to the final PR head and is reported on the PR after it completes.

## Remaining acceptance

Restore API balance before finishing the three quota-blocked R trials and the data-assisted/compatibility handoffs. Keep their follow-up results separate from this frozen first pass. The two output-truncation cases and the shape-error/token-exhaustion case still need successful same-budget model follow-ups. A full representative success claim, arbitrary-environment support, pixel fidelity and non-inferiority to Codex/Claude remain unproven.
