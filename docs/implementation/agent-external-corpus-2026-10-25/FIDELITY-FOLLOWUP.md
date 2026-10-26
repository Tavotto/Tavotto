# Conversion fidelity and repair follow-up

Latest acceptance order and functional results: [FUNCTIONAL-FOLLOWUP.md](FUNCTIONAL-FOLLOWUP.md). The user prioritized complete conversion/repair workflows before pixel matching. The visual failures below remain open evidence; default routing remains unchanged.

Status: corrective implementation and external-corpus retesting. **Full-fidelity acceptance and non-inferiority are not established; default routing remains unchanged.**

Latest follow-up: the final sections record MATLAB frame/tick/surface measurements, four complex-text groups, project-runtime context and default-mode signature fixes, then bounded large-reference reads through `a56bd5cf7`. All older attempts remain historical evidence. The new outputs still fail full visual acceptance.

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


## Previous corrective work through `106867b20`

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

Codex conversion ran with an isolated native permissions profile, GPT-6 Astra and the existing subscription; builtin/Claude used Sonnet 5.5. At that checkpoint Codex repair had not reached Tavotto's MCP callbacks, and unrelated authentication warnings were present. The operational baseline and corrected causal diagnosis appear in the `02bac2f89` follow-up below; those warnings did not prove a model-capability defect. Non-inferiority requires comparable completed runs, repeated trials and fidelity oracles; this PR does not establish it.

The package-version repair comparison did complete for builtin and Claude: 31.451 s and 34.793 s, nine steps each, exact reviewed `pillow==9.5.0`, explicit run confirmation, native outputs matching, original bytes unchanged, and a fresh Tavotto application completing execution. Both correctly handed a Pillow script to conversion instead of claiming a Matplotlib capture. Manual-wait timeout attempts and the unsuccessful Codex repair attempt are retained separately.

### Previous real-model checkpoint

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

### Previous release checkpoint

**No-go for a general full-fidelity or non-inferiority claim.** The fixes close verified context, execution, protocol, consent, output-identity and liveness defects. They do not prove that all 23 scripts preserve all visual semantics. Remaining work at that checkpoint included MATLAB camera/axes geometry, Pillow font and raster edges, complete math glyph comparison, repeated matched trials and a functioning Codex repair baseline. The later section updates these items without replacing the earlier results. The chosen private-feedback path is implemented and consent-tested; real production submission was not performed. Default routing remains unchanged.

### Validation

- Broad backend gate on `d9d91f6d3`: 4,056 passed, four skipped. Absolute source PYTHONPATH is required for MCP child-process tests. A preceding relative-PYTHONPATH failure was a harness failure, and the actual budget-to-stalled regression was corrected.
- Frontend suite: 600 files / 11,354 tests passed; subsequent conversion-card/store gate 342 passed. TypeScript/Vite build and translation checks passed.
- Browser: explicit-reference retry test passed in Chromium after correcting the test's welcome-dialog handling and query-bearing route matcher. Earlier six diagnostics-flow browser tests verified confirmation-before-network against a loopback sink.
- Final agent/conversion gate on `bf85a215a`: 450 passed, one skipped. Shared semantic prompt contracts on `c1747f06d`: 171 passed. Diagnostic privacy/parser/context on `01b444e7c`: 1,232 passed. These suites overlap and must not be summed.
- Final service smoke on `01b444e7c` passed cold/hot rendering, PDF export/overwrite, diagnostics and clean process exit. Ruff and i18n checks passed; documentation index/budget checks use the repository's existing ratchet thresholds.
- Counterfactuals: reference snapshot omission, total-deadline regression, short request timeout, raster-check removal, old signed-history compression and old multi-page PDF output handling each fail the new regression (exit 1); restored implementations pass (exit 0).
- Exact model/candidate hashes, code versions, failures, numerical measurements and validation log hashes are in LATEST-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)）. The original [REPORT.md](REPORT.md) and `results.json` remain historical snapshots.

Raw local benchmark directories contain credentials/configuration artifacts and are not publication payloads. Only selected metadata, hashes, numerical measurements and sanitized findings are committed. Earlier failures, rather than only successful retries, remain in the local evidence inventory.

## MATLAB geometry, font pixels and operational Codex repair (`02bac2f89`)

This follow-up contains **89 conversion attempts, 22 repair attempts and 18 fresh-application conversion checks**, including historical failures, interrupted/configuration-confounded attempts and retries. They are not independent samples. The earlier frozen batch and its negative results remain unchanged. The latest paired runs use product commit `02bac2f89ae736575bae52d6be8a5347aa25a28a`.

### Confirmed causes and corrections

- A 600 × 300 WebP reference had no dimension parser, so the builtin loop charged the unknown-image fallback of 65,536 tokens. This exceeded its 60,000-token compression threshold despite actual provider inputs around 6–8k tokens in the affected turns. Repeated compression evicted source/resource context. A shared pure-stdlib header reader now supplies PNG/JPEG/GIF/WebP dimensions to both comparison metadata and model budgeting. VP8, VP8L and VP8X, odd RIFF padding and extended canvas dimensions are covered; unknown/truncated input retains the conservative fallback. Header recognition does not replace image decoding or file validation. [WebP container specification](https://developers.google.com/speed/webp/docs/riff_container).
- The real `HttpModel` transport-boundary regression proves later request bodies retain source and image bytes; its injected POST receiver is deterministic, while separate wire tests exercise loopback HTTP. The pre-fix real-model diagnostic used 18 turns and 18 compressions (152.007 s); the post-fix source/reference-only attempt used five turns and zero compressions (45.884 s). These are individual diagnostic trials, not a latency benchmark. Font pixels did **not** automatically improve from this context fix alone.
- On Windows, `stat` and `fstat` can expose different meanings for `ctime`. Comparing that field across the two APIs falsely rejected unchanged files. Cross-API comparison now uses device/inode/size/mtime; before/after comparisons within each API still include ctime, and reads use binary mode. Both project-resource and model-image reads retain replacement/change checks. [CPython issue and platform semantics](https://github.com/python/cpython/issues/157671).
- Shared conversion guidance now preserves actual figure units/export crop, camera/up vector, projection and both aspect ratios **with their Modes**. MATLAB auto stretch-to-fill and Matplotlib's square active 3D viewport cannot be equated by copying position numbers. Font guidance uses actual font bytes, anchor bounding boxes, kerning/shaping and glyph-origin rounding, with editable glyph placement limited to proven independent LTR Latin text. It does not prescribe splitting complex scripts or embedding text bitmaps. [MATLAB automatic resize behavior](https://www.mathworks.com/help/matlab/creating_plots/automatic-axes-resize.html), [Pillow FreeType implementation](https://github.com/python-pillow/Pillow/blob/main/src/_imagingft.c).

The prior CI failures were corrected: Windows resource-identity assertions, platform-dependent test text encodings, and missing classification entries for `convert_reference_invalid` / `preview_render_failed`. No route/default-setting file changed in this follow-up.

### Same-version, same-input model comparisons

Builtin and Claude Code used Sonnet 5.5, high effort, the same product commit and candidate interpreter/packages. Every staged source/resource byte hash is checked equal for each pair in `same_commit_conversion_pairs`. CLI tools/provider call strategy still differ. These are development cases used to improve the shared prompt, one latest trial per arm; they are not held-out repeated evidence of non-inferiority.

The geometry arm supplies unchanged native full-frame exports, figure/axes/camera attributes and actual R2025a LUT through the real `reference_files` endpoint. Its geometry metadata explicitly supplies seed **20261025** to both executors. Unlike the earlier `d9d` arm, the final contour CData is therefore a valid matched deterministic comparison. The font-facts arm supplies observational metrics from the unchanged original Pillow draw calls, font SHA and kerning advances; it is separate from the source/reference-image-only arm. No model received a prior converted candidate or evaluator oracle directory.

| Paired arm | Builtin | Claude Code | Independent acceptance result |
| --- | --- | --- | --- |
| Airy full-frame geometry | Adopted; 250.700 s; eight projected corners max error **1.0834 px**, RMS 0.5881 px | Adopted; 117.729 s; max corner error **5483.94 px**, visibly outside the frame | Builtin camera greatly improved; Claude fidelity failure despite execution/capture. Neither is a full pixel pass |
| Two-page contour, supplied geometry + seed | Adopted; 122.140 s; second-plane boundary maximum difference **0.9625 px** | Adopted; 169.752 s; maximum difference **23.4584 px** | Both CData errors **0**, actual artist LUT errors **0**, CLim error 2.78e-17, contour-data error 8.33e-17, image plane Z remains **150**. Layout is better in this builtin trial |
| Pillow anchors, original source + reference image only | Adopted; 45.884 s; WebP RGB MAE **1.02524/255** | Adopted; 32.812 s; **1.02524/255** | Context correction alone does not remove glyph positioning error |
| Pillow anchors, supplied original font metrics | Adopted; 90.829 s; raw PNG RGB MAE **0.000361/255**, maximum **1/255**; WebP MAE **0.070220/255** | Adopted; 63.549 s; raw PNG MAE **1.06214/255**, maximum **246/255**; WebP MAE **1.18337/255** | Dimensions 600 × 300 and alpha agree. Builtin near-equivalence is limited to these nine Latin labels; exact equality and general shaping are unproven |

All six geometry/font-facts candidates independently execute and reopen in a new application process with unchanged adopted source, actual figure capture and no forced-worker override. Offered dependency/run actions are explicitly confirmed by the test client. This proves the application loop, **not** the failed Claude geometry's fidelity. The standalone source/reference-only anchor pair is also independently executed, but is not included in those six fresh-app checks.

Airy native calibration adds colored corner markers only after freezing the native camera/aspect/axes/limits; their equality is asserted before and after. Candidate points use the drawn projection matrix and display transform. Contour uses native colored support (`max(R,G,B)-min(R,G,B)>20`) versus the projected image plane, so rasterized stroke edges remain a measurement limitation. Neither comparison resizes or crops output. Both executors still export **1342 × 808** against **1343 × 810** native frames; ticks, final antialiasing, export-edge rounding and other camera configurations remain unaccepted. The builtin Airy heightfield's depth-order choice is not proof for arbitrary intersecting 3D geometry.

The final font measurement executes the real model candidate unchanged, separately from the earlier reviewer-only quantization experiment. Ordinary editable `Text` glyphs preserve the visual labels, with finer edit granularity than whole-label text. Its success cannot be extended to ligatures, combining marks, RTL shaping or other fonts. Lossy WebP error is recorded separately from pre-encoding PNG error.

### Codex repair baseline and CLI configuration confounders

Codex's local MCP child did not inherit the parent's source `PYTHONPATH`, so it loaded a different checkout that lacked the required repair endpoint. A per-invocation `mcp_servers.tavotto_fix.env.PYTHONPATH` binding makes the child load the measured commit; its module digest is recorded. No global Codex configuration or authentication was changed. A read-only MCP probe completed, followed by real repair callbacks and actual application runs. Earlier unrelated OAuth warnings were **not** evidence of the local MCP root cause.

| Controlled repair | Builtin | Claude Code | Codex | Required outcome |
| --- | --- | --- | --- | --- |
| Exact `pillow==9.5.0`, shared product `3cf845fde` | 28.730 s / 10 steps | 37.977 s / 9 steps | 58.261 s / 12 steps | All confirm the exact version and run; source unchanged; **2/2** native outputs pixel-identical; actual app execution completes |
| Missing case image, shared product `02bac2f89` | 97.453 s / 16 steps | 41.659 s / 15 steps | 281.602 s / 23 steps | All use reviewed case-local data actions; source unchanged; **8/8** native outputs pixel-identical; actual app execution and separate fresh-app reopens complete |

Pillow is not a Matplotlib capture: all six repairs correctly end with `execution_finished=true`, `figure_captured=false` and the conversion handoff. This is not reported as a captured editable figure. Codex reaches 12 and 23 real Tavotto tool callbacks in the final version/missing-image runs. Its subscription GPT-6 Astra differs from key-backed Sonnet 5.5; repair thinking settings also differ. Wall totals include manual card waits in some runs and cannot rank speed.

Three later Claude conversion processes were observed waiting on an inherited desktop integration hook; those test-owned children were stopped and each intervention retained. Per-invocation `--bare --setting-sources ""` isolates hooks/plugins for the control, with strict empty MCP configuration for conversion and explicit Tavotto MCP for repair. The product's tool permissions, oracle read guard and repair cards remain enforced. User settings are untouched. The two earlier geometry attempts that reached the 900-second deadline remain separate failures; the later observed hook cause is not retroactively asserted for every older timeout.

### Verification and remaining decision

- `3cf845fde`: **494 passed** for resource identity, CI hygiene/error-code classification, prompt and current-app verification. `94f3a324f`: **169 passed** for glyph/camera contracts. `02bac2f89`: **350 passed, zero skipped** for shared image headers, HTTP transport-boundary context retention, Windows image identity and agent/conversion regressions. These overlap; do not sum them.
- Counterfactuals restore the old Windows identity check / old WebP budgeting and image-read behavior: regressions exit **1**. Restoring the fixes exits **0**. Ruff, diff checks and the repository's documentation ratchet/index checks pass.
- Remote CI is tied to its exact head. At this report's measurement checkpoint, `02bac2f89` had 29 successful checks, 38 still running, one skipped and no failures; this is **not** an all-green claim. The PR exposes the current live result, including later report-only commits.

**Default routing stays unchanged.** The current result supports specific context/Windows/liveness repairs and measured improvement in these MATLAB/font cases. It does not certify every conversion, complete math glyph fidelity, arbitrary MATLAB cameras, all fonts, platform release packages or general superiority over Claude Code/Codex. Private product-feedback drafts still require user review and confirmation; no production feedback was submitted. The earlier unavailable Codex baseline is now operational and is no longer listed as an unresolved integration blocker.

## Frame, ticks, surface coverage and complex shaping through `21152e1d6`

The two supplied Airy pictures differed in actual tick direction/endpoints, grid and mesh coverage, and text appearance. Matching camera corners was insufficient. This follow-up retains **17 additional real-model attempts**, giving **106 conversion attempts**, **22 repair attempts** and **22 fresh-application conversion verifications** in the cumulative record. These are development attempts with retries, not independent samples. The external corpus remains 23 cases / 22 upstream files / 10 repositories; the additional complex-text fixture is one synthetic case with four script groups, derived from Pillow's public text APIs and test fonts.

### Confirmed product corrections

- `cef5b69ca`: the shared conversion guidance separates export pixels from figure screen dimensions and point rounding, projected tick endpoints from accepted `tick_params` keywords, and face/edge coverage from a vaguely similar surface. It requires original surface depth, face colors, actual font/shaping and antialiasing evidence. Whole Arabic/bidi/combining/ligature runs remain logical editable text; reversing or splitting Unicode is not an accepted substitute.
- `db49c5912`: conversion now supplies the selected project's worker interpreter as an **unverified context hint**. Codex had probed its ambient Matplotlib 3.10.8 and incorrectly requested a newer environment even though the product selected 3.11.2. Reading the hint neither resolves/probes an environment nor adopts a candidate. Actual product checking remains authoritative. The controlled follow-up used the indicated runtime and completed.
- `21152e1d6`: signed `redacted_thinking` can arrive even when the requested Anthropic thinking mode is `none`. Compaction had only removed stale signatures in adaptive mode, causing a real provider HTTP 400. Non-budget compaction now removes invalidated signed blocks in both modes while retaining tool calls/results; manual budget mode stays append-only. The real same-input rerun no longer returned HTTP 400 but exhausted its total token budget, so it is retained as a failure. Subsequent SVG-assisted runs completed through eight compactions without changing requested mode or effort.

### Native evidence and fixture correction

The native R2025a export is 1343 × 810 pixels. The light theme is set only in the measurement process. The native style has 0.5 pt lines, RGB (33,33,33) axes/mesh edges, grid alpha 0.15, outward ticks and Helvetica 11 pt. Its 24 tick segments are independently extracted from the supplied native SVG. SVG coordinates are mapped into the PNG viewport for **geometry**; the original PNG remains the raster/antialiasing oracle.

The first collector incorrectly labeled an omitted `exportgraphics` background option as `figure`, conflicting with the actual white PNG/SVG. This was a fixture defect. The original images remain unchanged; old trials and both metadata hashes are retained. The final paired arm uses the corrected observed white background and a compact native facts file containing the same parula values, excluding unused color tables. It is a separate evidence arm, not a silent replacement of earlier failures. The gray-background diagnostic is not counted as a full-fidelity acceptance result.

### Final matched inputs, different models

Both final executors used product `21152e1d6`, the same source/context/reference bytes and Matplotlib 3.11.2 worker. The original/native SVG, style and palette were explicitly supplied through the real `reference_files` interface. Builtin used key-backed Sonnet 5.5; Codex used subscription GPT-6 Astra. This controls inputs and runtime, **not model/authentication/tool differences**, and cannot establish non-inferiority.

| Final corrected Airy measurement | Builtin | Codex |
| --- | --- | --- |
| Actual canvas | 1343 × 810 | 1343 × 810 |
| Native tick segments matched | 24/24 | 24/24 |
| Maximum tick endpoint error | 9.0764 px | < 2e-12 px |
| Tick width | 0.5 pt | 0.5 pt |
| Whole-frame RGB MAE, 0–255 | 1.77154 | 3.46464 |
| Colored surface interior RGB MAE | 2.09422 | 7.94485 |
| Colored surface border RGB MAE | 6.39597 | 14.71563 |
| Independent visual acceptance | **Fail**: tick direction, mesh/coverage and text differences remain | **Fail**: incorrect occlusion, yellow back-face slivers and missing/incorrect edges |

The Codex tick result uses supplied native SVG geometry; it is not a claim that arbitrary MATLAB tick projection was solved. Its surface preserves nonzero Z but centroid painter sorting still draws incorrect faces/edges. The builtin band ordering is also not a general occlusion solution. Antialiasing is enabled in both outputs; that flag alone does not prove matching edge coverage. No tolerance was relaxed, no candidate was manually repaired before measurement, and neither result is certified by its low whole-frame average error.

The exact captured images (`style-v3/native-airy.png`, `style-v3/builtin-airy.png`, `style-v3/codex-airy.png`) are archived outside the repository; their hashes are in [EVIDENCE.md](EVIDENCE.md). They are unmodified outputs, not composites or resized acceptance inputs.

Both final Airy candidates and both final complex-text candidates were then opened in fresh Tavotto application processes with their own project state, no model call and no forced worker. The test client explicitly confirmed the offered preparation/run cards. All four completed execution and figure capture with unchanged adopted scripts. This verifies the application loop, separately from the failed visual acceptance.

### Complex glyph coverage

The synthetic source renders four separate 960 × 180 outputs with Pillow 12.3.0 / RAQM 0.10.5: Arabic with combining marks (Aref Ruqaa), mixed Hebrew/Arabic/Latin and digits (DejaVu Sans), ffi/fl ligatures plus combining marks (DejaVu Sans), and Khmer (Khmer OS Battambang). Font blobs, source, native metrics and candidate hashes are recorded. Fonts are local fixtures and are not committed to Git.

| Group | Whole-frame RGB MAE against native Pillow, both executors | Ink-union RGB MAE, both executors |
| --- | --- | --- |
| Arabic marks | 0.35649 | 23.27900 |
| Mixed bidi | 0.84755 | 20.00192 |
| Ligatures and combining marks | 1.64763 | 59.26676 |
| Khmer | 2.09453 | 67.86210 |

Every output contains one logical editable Text run. Builtin and Codex PNGs are pixel-identical to each other, but **not** to Pillow. The ink mask is the union of native/candidate pixels with any RGB channel below 250; the whole-frame measure has no mask. Thus whitespace cannot hide the remaining glyph-spacing and coverage differences. Matching strings and output dimensions is insufficient to certify the glyphs.

`tests/test_complex_text_fidelity.py` separately compares Tavotto capture/edit/restore with **uninstrumented Matplotlib in a separate process using the same runtime/font**. It requires exact PNG pixels and positioned SVG outlines. Negative fixtures split Arabic, reverse mixed bidi, and disable ligatures; both oracles reject them. These six tests pass on 3.11.2. A runtime lacking the whole-run API explicitly skips instead of certifying support. This regression covers Tavotto's preservation of Matplotlib shaping, not native Pillow equality or every font. The Khmer real-model fixture is additional coverage, not a falsely claimed bundled-font CI test.

### Controls, counterfactuals and remaining work

- The original runtime hint makes the new regression exit 1; restoring the fix exits 0. The signed-prefix HTTP receiver fails both default-mode variants before the fix, while adaptive/manual controls already pass; all six variants pass afterwards. Signed block contents are never published.
- Targeted gates: **122 passed** for shaped capture and conversion, **194 passed** for project-runtime/app conversion, and **234 passed** for agent/media/signature/conversion. They overlap and are not summed. Full-repository Ruff and formatting passed at each product commit.
- The final assessment sandbox also denies the stored native oracle, other candidates and prior measurement directories. A negative shell probe reads its own approved work with exit 0 and is denied the native oracle/another candidate with exit 1. This strengthened assessment guard is only claimed for the new assessments, not retroactively for older harnesses.
- Claude's isolated controls were blocked by automatic permission review: the cached candidate was classified as a sensitive file, and a compound probe could not be approved before execution. These outcomes are retained as permission/integration failures, not model-fidelity failures. No global setting or sandbox was weakened to obtain a pass.
- Remaining acceptance blockers are MATLAB tick direction in builtin output, general surface occlusion/edge coverage, native-to-Matplotlib complex glyph metrics, R full math/layout fidelity, and repeated independent same-model comparisons. The previous Codex environment-context blocker is closed; the capability comparison is still bounded.

Third-path review A–F: preserve source data, complete canvas, editable logical text and true 3D geometry; identify native geometry/raster coverage and ambient/project runtime as distinct facts; reject zero-Z sorting, bitmap substitution and whitespace-dominated averages; carry explicit native evidence and the selected worker context; falsify protocol/runtime and glyph oracles independently; retain failed visual results without switching default routing. The structural runtime/protocol fixes are verified. Universal fidelity is not.

The final-head remote CI result is reported in the PR Checks/body after the report commit; this document does not turn an in-progress checkpoint into an all-green claim. No production private feedback was submitted.

### Large reference text access

The final controls supplied identical 667,133-byte native SVGs, but builtin `read_file` could only read an entire file of at most 200 KiB. The final builtin trace did not attempt this SVG read, so this limitation is **not asserted as the sole cause of its wrong ticks**. It is a reproduced resource-access difference from the CLI control.

Third-path A–F: retain both approved project resources and bounded tool reads; distinguish a per-response bound from total resource size; remove the assumption that text must be read all at once; permit UTF-8 byte windows with returned continuation offsets, stable open-file identity and existing path containment; reject traversal, invalid limits, binary/split-start reads, concurrent file change and partial-read write grants; verify the old implementation fails the large-resource regression before measuring new model trials. Full-target read/write limits remain 200 KiB. Each optional window is at most 64 KiB, defaults to 16 KiB and never authorizes an edit. No shell, network, new read root or default-routing change is introduced.

Restoring `c3c5d0b6e` makes the new large-file regression fail in both task toolsets (exit 1). The final fixed gate passes **585 tests, zero skipped**, including media, agent/conversion, error taxonomy and guidance index. A direct product-tool probe reads the real SVG's first and last 16 KiB, returns correct continuation/end metadata and leaves the whole-file size limit intact.

Two further real-model trials on `a56bd5cf7` have identical input bytes to each other. Source/assets/reference bytes match the corrected `21152e1d6` arm, but `context.json` lists the same references in a different order; the cross-version serialized input is not claimed byte-identical. The builtin trace now explicitly reads SVG bytes **0–9000** and **650500–667133**. It later exhausts the existing token budget after **499.506 s**, 12 turns and 9 compactions; the task ends as failed and **no candidate is adopted**. No signature HTTP 400 occurs. This verifies access and bounded failure, not successful conversion.

Codex completes in **205.515 s**, with 1343 × 810 pixels and all 24 native-SVG tick endpoints aligned within 3e-13 px. Its custom mesh renderer still draws major surfaces in the wrong order: whole-frame RGB MAE **4.93271**, surface interior **34.27987**, surface border **20.30995**. Exact failed output（存档，见 [EVIDENCE.md](EVIDENCE.md)）. It retains XYZ arrays but standard surface-property editing was not independently certified. A fresh application run captures successfully and leaves its script unchanged; visual fidelity still fails.

The cumulative record now contains **108 conversion attempts, 22 repair attempts and 23 fresh-application conversion verifications**. No older failure is replaced. Bounded large-resource access is repaired, while surface occlusion/coverage, builtin tick fidelity, complex native glyph metrics and repeated same-model capability acceptance remain open. Default routing is unchanged.
