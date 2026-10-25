# Conversion fidelity and repair follow-up

Status: corrective implementation and external-corpus retesting. **Full-fidelity acceptance and non-inferiority are not established; default routing remains unchanged.**

## Acceptance subjects

- Conversion: independently measured values, annotations, dimensions, colors, alpha, geometry and every output page of the adopted script, both in a clean interpreter and a fresh Tavotto application. Runtime success alone is insufficient.
- Repair: the actual preparation session after the last mutation; a diagnostic copy cannot certify the application fixed.
- Consent: the exact package requirement or command shown on the card; a declined or changed action must not execute.
- Liveness: the owning child process and in-flight tool, including silence/partial stdout, blocked model requests and cancellation during pip.
- Reports: the user-visible reviewed draft and immutable diagnostics bundle; no automatic or public submission.

## Third-path review (A–F)

A. Preserve scientific meaning and editable elements while keeping project inputs intact and every mutation reviewable.
B. Source-language defaults are external facts, runtime capture is a separate fact from fidelity, and a no-shell conversion task is a repository safety constraint. CLI configuration and model differences are experimental confounders.
C. A text-only loop cannot inspect supplied reference images or its own rendered figures. Full-script-only output consumes one response window. Matching an approved package by name discards the reviewed version constraint. Checking deadlines only after stdout lines misses silent children. Detached pip cancellation misses the repair session's stop request.
D. Carry bounded visual evidence in native model protocols, render the current live figures before retiring their worker, write long scripts in checked chunks, bind approvals to full requirements, and share cancellation/deadline authority. Do not insert precomputed answers into candidates or add an approximate palette fallback.
E. Tests assert received HTTP image bytes in all three protocols, actual worker PNGs/page order, immutable sources/stale append rejection, exact version consent, interrupted pip, silent child reaping and detailed diagnostic report preparation. Known external numerical/visual failures remain negative oracles. Native runtime color tables are an exploratory evidence source; they are not silently included in the source-only benchmark.
F. Structural fixes are testable separately from model fidelity. Full fidelity and non-inferiority remain unproven until paired real-model results and independent oracles pass. Broader native semantic extraction is not implemented by this patch.

## Protocol and semantic sources

- [Anthropic tool results](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)
- [OpenAI function outputs](https://developers.openai.com/api/docs/guides/function-calling)
- [R plotmath](https://stat.ethz.ch/R-manual/R-devel/library/grDevices/html/plotmath.html)
- [MATLAB parula and version history](https://www.mathworks.com/help/matlab/ref/parula.html)
- [MATLAB color specifications](https://www.mathworks.com/help/matlab/creating_plots/specify-plot-colors.html)

## Pilot runs (all retained)

The initial builtin plotmath pilot adopted 5 pages but still changed a mathematical bound; it is a fidelity failure. Its check did not yet carry live PNGs, which the actual worker regression exposed and the implementation now fixes. The matching Claude Code pilot failed Mathtext parsing after correction. The subscription Codex pilot exhausted the time limit with inherited unrelated MCP authentication errors; this is not evidence that the builtin model is more capable. The next comparison isolates Codex CLI configuration for the run, preserving its existing login and explicit selected model, without modifying user files.

## Bounded output recovery

The next source-only builtin plotmath attempt (`r_base_plotmath-builtin-v2`, Sonnet 5.5, high effort) stopped at 16,384 output tokens before writing a candidate. Merely offering append_file was insufficient. Conversion writes now have a 6,000-character chunk boundary in both schema and implementation. A truncated response may be discarded and retried once with a small-section instruction, under the same total token, turn and time limits. No calls or partial text from that response execute or reach the user. All usage remains accounted. Other task types retain their existing truncation behavior.

Validation: 219 executor tests passed; replacing the loop/tools with the previous commit made the new recovery tests exit 1, restoring the patch made the conversion suite exit 0. The earlier broader backend gate passed 1,678 tests before this bounded recovery addition. UI verification: 74 repair/store tests and 6 Chromium diagnostics-flow tests passed; confirmation-before-network was measured against a loopback sink, with no production upload. These gates do not certify mathematical or palette fidelity.

Codex comparison preflight failures (wrong option position, nested macOS sandbox, quoted configuration key) are retained as harness failures, not model failures. The corrected native Codex permission profile permits its own source and denies the hidden manifest; an isolated polygon run adopted successfully in 46.088 seconds. Codex uses the existing subscription and GPT-6 Astra, whereas builtin/Claude use Sonnet 5.5; this is not a same-model Codex comparison.

CodeQL's quoted-string finding initially did not reproduce a hang with 10,000, 100,000 and 1,000,000 escaped-quote repetitions. The final implementation removes that expression: the standard JSON decoder consumes each literal once, while preserving original escapes, whitespace and duplicate keys. Existing redaction rules remain unchanged. Two thousand randomized JSON inputs matched the previous output exactly; one million escaped pairs completed in 0.0484 seconds on this host. The diagnostic privacy/context gate passed 1,232 tests. This local timing is supporting evidence, not an all-platform performance guarantee.


## Final corrective work and acceptance boundaries

The additional product changes address confirmed failures rather than interpreting successful rendering as fidelity:

- The missing-input card now accepts explicit project-relative reference files, for Python, R and MATLAB. Nothing is sent while typing. On retry the real API validates every selection through the existing read/privacy policy, preserves paths and hashes, and rejects resource changes before adoption. Invalid paths leave the input editable and do not retain a paid request identity. Chromium tests exercise typing, rejection, correction and resubmission; actual model tests use this API field, not a patched snapshot.
- A repair session's total deadline includes confirmation-card wait time. Expiry cancels the tool and drains it before releasing its slot. Intentional cancellation is not relabeled as an unexpected stall. Version constraints remain bound to the exact reviewed package; shell commands and application reruns retain explicit cards.
- Anthropic Sonnet 5.5 rejected compressed histories with HTTP 400 because previously signed thinking blocks were bound to the old prefix. A controlled real request recorded this provider reason. Adaptive-mode compaction now drops invalidated thinking blocks while retaining tool calls/results and effort. Manual thinking remains append-only under the total budget. The regression receiver rejects changed signed prefixes, so a JSON-shape-only test cannot give a false pass. [Protocol requirement](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting).
- Long conversion responses have a 32,768-token allowance, including thinking; file writes stay at 6,000 characters and total time/token/turn limits remain unchanged. A bounded 300-second request window permits long responses; total deadlines and cancellation still apply. Truncation, network, credentials, quota and other executor failures retain distinct translated card reasons instead of collapsing to unknown failure. The 16,384-token and experimental override failures remain in the evidence.
- A positive AST check rejects proven Pillow text/shape painting in a supposed editable candidate. This applies to the agent check and final staging; photo reads and font measurements remain allowed. Ambiguous bindings are not treated as proof. This check catches a confirmed full-bitmap substitute, and **does not certify all editable semantics or fidelity**.
- Confirmed product defects use the existing private in-app feedback flow, with a detailed report and sanitized diagnostic package. The user reviews and confirms before submission; source inclusion remains a separate opt-in. No production report or public GitHub issue was sent during testing.
- Multi-page PDFs receive a separate filename when `PdfPages` is constructed, before the file is opened. Waiting until `savefig` sees the stream was too late. The real five-page plotmath candidate was deterministically postprocessed and independently executed: the previous PDF bytes remained identical, and the new PDF contained all five 504 × 504 pt pages. This is verification of product postprocessing, not another model trial.
- CSV does not carry R factor levels or the ordered flag. The shared prompt requires native metadata instead of guessing category order, and preserves `boxplot.stats` hinges/IQR before outlier whisker adjustment. Pillow text anchors use measured offsets in both directions, with explicit pixel alignment and drawing order checks.

### Additional third-path review

The invariant is that a runnable image must not masquerade as an editable reconstruction. The external composite candidate retained Pillow's drawing operations and embedded the finished bitmap, violating that invariant despite a successful capture. The structural correction is shared positive evidence at both checking boundaries; unproven syntax is left to runtime/user comparison, not an expanding general Python allowlist. Restoring the old always-accept behavior makes both the tool-check and final-session regression fail. Rasterization is not an accepted workaround for unsupported native semantics.

### Experiment separation

`d1c077044` was frozen for 23 builtin cases (one plotmath request retried once after a timeout) and eight matched Claude cases. Seventeen Pillow/MATLAB cases had a supplied native reference image; R cases did not. Therefore this arm is described as **without additional native numeric facts**, not uniformly source-only. Three Claude cases reached the bounded 900-second deadline. This is an execution outcome, not proof of model inferiority.

`d9d91f6d3` tests the product `reference_files` endpoint. The MATLAB file contains only generic R2025a version/default colors and full lookup tables, not case answers. The R interval arm supplies the real singer CSV. Prior runs marked `test-only snapshot injection` remain a separate exploratory arm. Native RNG seed 20261025 is an evaluation control; random MATLAB scripts that do not specify their own seed did not receive that seed, so their noise pixel arrays cannot be scored as matched deterministic data.

Codex conversion ran with an isolated native permissions profile, GPT-6 Astra and the existing subscription; builtin/Claude used Sonnet 5.5. Codex repair never reached Tavotto's MCP callbacks even after isolated configuration attempts and showed unrelated MCP authentication errors. This baseline is blocked by integration/setup evidence and is not evidence that the builtin agent is better. Non-inferiority requires comparable completed runs, repeated trials and fidelity oracles; this PR does not establish it.

The package-version repair comparison did complete for builtin and Claude: 31.451 s and 34.793 s, nine steps each, exact reviewed `pillow==9.5.0`, explicit run confirmation, native outputs matching, original bytes unchanged, and a fresh Tavotto application completing execution. Both correctly handed a Pillow script to conversion instead of claiming a Matplotlib capture. Manual-wait timeout attempts and the unsuccessful Codex repair attempt are retained separately.

### Latest real-model results

The machine-readable inventory contains **70 conversion attempt records and 15 repair attempt records**, including preflight failures, development retries and comparison setup failures. These are not independent trials. The frozen `d1c077044` batch remains 16 adopted / four needs-input / three failed for 23 builtin cases; its eight Claude cases remain three adopted / two needs-input / three failed. Adoption is not a fidelity verdict, and later successes do not replace those rows.

| Case and evidence arm | Latest observation | Remaining acceptance boundary |
| --- | --- | --- |
| R plotmath, builtin `734cb1d8e` | Adopted in 353.723 s; all five pages independently executed and captured; reviewed summation bounds, full-size fractions and phantom spacing corrected; normal editable text/lines/patches | Full font/glyph geometry is not independently certified; native PNG device dimensions are not silently equated with the candidate's PDF device |
| MATLAB contour, builtin `d9d91f6d3`, native runtime facts through the product API | Rendered 256-entry RGB table error 0; upper CLim error 2.78e-17; all image-plane Z values remain 150 | Layout/camera not accepted; random source and native evaluator used unmatched RNG states, so noisy pixel arrays are not a deterministic oracle |
| MATLAB Airy surface, same arm | Surface Z error 2.78e-17; supplied runtime LUT exactly matches native MATLAB | Axes position differs; LUT evidence alone does not certify the final lighting or geometry |
| R intervals, CSV only, builtin `bf85a215a` | Stopped in 14.922 s with precise `levels` / `is.ordered` commands and a reference-file request; no guessed candidate adopted | User must supply native factor metadata |
| R intervals, CSV + native factor metadata, builtin and Claude `c1747f06d` | Both adopted and independently produced two pages; all eight factor positions and medians match; maximum CI error 4.98e-14; fresh applications execute and capture both candidates | Page layout/font geometry still unaccepted; earlier wrong-order and 0.133534 CI-error candidates remain negative evidence |
| Pillow text anchors | Latest builtin `c1747f06d`: dimensions and alpha match, visible RGB MAE 1.02524/255; Claude `bf85a215a` has the same metric. Both retain ordinary editable elements | Different prompt revisions; one local result cannot establish non-inferiority. Nonzero raster/font differences remain |
| Pillow composite, builtin `734cb1d8e` | Explicit needs-input for the unsupported drawing/filter reconstruction, no candidate adopted | A safe refusal is not a successful editable conversion |

Nine new-application verification records are retained, including the initial two dependency-confirmation stops. After explicit confirmation of the offered managed dependency/run actions, Airy and the R interval candidate execute and capture successfully. The final R paired candidates and Pillow anchor candidate also complete fresh application execution with unchanged adopted source hashes. These checks use neither a model nor a forced worker override; they do not infer success from `pip` exit status.

The final R statistical correction follows the [native `boxplot.stats` contract](https://stat.ethz.ch/R-manual/R-devel/library/grDevices/html/boxplot.stats.html). Independent measurements compare group-aligned medians/intervals separately from category order. Matplotlib axes-coordinate guide lines are not confidence intervals. Pillow RGB is compared after compositing against black and white, so invisible RGB under alpha zero does not become a false failure; missing outputs produce an unavailable dimension verdict, never `all([]) == true`.

### Release decision

**No-go for a general full-fidelity or non-inferiority claim.** The fixes close verified context, execution, protocol, consent, output-identity and liveness defects. They do not prove that all 23 scripts preserve all visual semantics. Remaining work includes MATLAB camera/axes geometry, Pillow font and raster edges, complete math glyph comparison, repeated matched trials and a functioning Codex repair baseline. The chosen private-feedback path is implemented and consent-tested; real production submission was not performed. Default routing remains unchanged.

### Validation

- Broad backend gate on `d9d91f6d3`: 4,056 passed, four skipped. Absolute source PYTHONPATH is required for MCP child-process tests. A preceding relative-PYTHONPATH failure was a harness failure, and the actual budget-to-stalled regression was corrected.
- Frontend suite: 600 files / 11,354 tests passed; subsequent conversion-card/store gate 342 passed. TypeScript/Vite build and translation checks passed.
- Browser: explicit-reference retry test passed in Chromium after correcting the test's welcome-dialog handling and query-bearing route matcher. Earlier six diagnostics-flow browser tests verified confirmation-before-network against a loopback sink.
- Final agent/conversion gate on `bf85a215a`: 450 passed, one skipped. Shared semantic prompt contracts on `c1747f06d`: 171 passed. Diagnostic privacy/parser/context on `01b444e7c`: 1,232 passed. These suites overlap and must not be summed.
- Final service smoke on `01b444e7c` passed cold/hot rendering, PDF export/overwrite, diagnostics and clean process exit. Ruff and i18n checks passed; documentation index/budget checks use the repository's existing ratchet thresholds.
- Counterfactuals: reference snapshot omission, total-deadline regression, short request timeout, raster-check removal, old signed-history compression and old multi-page PDF output handling each fail the new regression (exit 1); restored implementations pass (exit 0).
- Exact model/candidate hashes, code versions, failures, numerical measurements and validation log hashes are in [LATEST-RESULTS.json](LATEST-RESULTS.json). The original [REPORT.md](REPORT.md) and `results.json` remain historical snapshots.

Raw local benchmark directories contain credentials/configuration artifacts and are not publication payloads. Only selected metadata, hashes, numerical measurements and sanitized findings are committed. Earlier failures, rather than only successful retries, remain in the local evidence inventory.
