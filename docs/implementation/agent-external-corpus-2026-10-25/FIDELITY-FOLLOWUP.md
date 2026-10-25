# Conversion fidelity and repair follow-up

Status: implementation and evaluation in progress. Default routing remains unchanged.

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

CodeQL's quoted-string regular-expression finding was checked with 10,000, 100,000 and 1,000,000 escaped-quote repetitions, valid and invalid JSON. Invalid JSON is rejected before the expression; the largest valid input completed in 0.1543 seconds on this host. No hang was reproduced and no redaction rule was weakened.
