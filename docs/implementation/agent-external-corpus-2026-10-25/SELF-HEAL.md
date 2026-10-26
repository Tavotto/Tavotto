# Shared recovery loop and builtin default

2026-10-26. R sinc and plotmath now complete conversion, adoption and fresh-process rendering under the unchanged formal budget. API Key and Tavotto-managed OAuth connections now default to builtin, as explicitly requested by the maintainer. Explicit CLI preference, force switches, subscription CLI connections and pre-dispatch fallback remain available. This is a source change in PR #1421, not a release.

## Matched real-service trials

All trials use DeepSeek `deepseek-flash`, native Responses, no effort override, the same source bytes and runtime, and **32 turns / 800,000 cumulative input+output tokens / 900 seconds / 32,768 output tokens per response / three tool checks**. No candidate code was manually repaired. Raw receipts remain local; the public ledger（存档，见 [EVIDENCE.md](EVIDENCE.md)） retains each result and its hash.

| Case | Fresh baseline `41715ce13` | Intermediate `a48144aa2` | Final product `c7eff16e6` | Adopt / new app |
| --- | --- | --- | --- | --- |
| R sinc | Output truncated, 387 s | Cumulative tokens exhausted, 329 s | Completed, 254 s, 385,107 tokens | HTTP 200; succeeded, 2 figures |
| R plotmath | Output truncated, 367 s | Cumulative tokens exhausted, 316 s | Completed, 409 s, 454,100 tokens | HTTP 200; succeeded, 5 figures |
| Pillow book polygon | Historical baseline retained separately | Completed, 31 s | Completed, 25 s, 33,393 tokens | HTTP 200; succeeded, 1 figure |
| MATLAB bounded NaN | Historical baseline retained separately | Completed, 261 s | Completed, 250 s, 474,815 tokens | HTTP 200; succeeded, 1 figure |

Final four-case trial: **4/4 complete, adopted and reopened**, all original protected files unchanged. The new application process uses no model and no forced worker. A separate Pillow application trial with the preference key absent and no force override also dispatches builtin, completes, adopts and reopens successfully.

The final plotmath trial actually exercises one truncated-response recovery and one recovery-state release. The next request after recovery drops from 41,903 to 18,577 reported input tokens. Sinc does not truncate in its final trial; its pass alone is not proof that continuation caused the improvement. Model variability remains. Baseline and intermediate failures are not overwritten or pooled into a single success rate. These four trials are not a new 23/23 corpus result; [the frozen 23-case first pass](FULL-CORPUS.md) remains 16/23.

## What failed, and what changed

The baseline receives HTTP 200 with `incomplete/max_output_tokens`: the provider's response allowance includes reasoning. Tavotto discarded the interrupted reasoning and allowed only one lifetime retry, causing both R cases to start over and fail before the 900-second deadline. The intermediate fix permits continuation but retains its long recovery state on every subsequent request; both cases reach code generation and validation, then consume the cumulative budget. Increasing the wall-clock limit would not address either observed failure.

The final shared loop uses tool results as observations: correct the parameters or candidate, validate again, then finish only on the existing authoritative success result. It applies to editing, conversion and environment repair:

- Replayable interrupted reasoning is temporary context. Incomplete text and tool calls never execute. After a successful tool action, concrete calls/results replace the temporary state. Anthropic manual thinking retains its required signed prefix; other Anthropic modes discard invalidated signatures when history changes.
- Malformed non-object arguments are rejected. Their replay uses an empty object paired with the original call ID and explicit `bad_arguments` plus the advertised schema, so a strict provider can deliver the correction feedback instead of rejecting malformed history. Valid objects are not rewritten and unknown argument names are not guessed.
- Missing or duplicate call IDs reject the entire response, then request a valid replacement. Three consecutive invalid identities or truncated responses terminate. Identical failing tool+arguments+error repetitions terminate; distinct correction attempts may proceed within the unchanged global limits.
- The environment-repair wrapper forwards continuation, state release and wire-specific compression. It cannot bypass approval cards, cancellation or verification.
- Conversion write-denial feedback carries the worker's bounded, relative path and operation, alongside conversion-specific guidance. It does not reuse the conflicting generic MCP advice to write into the original project. Write roots remain unchanged.

No language, dataset, file name or model-specific workaround was added. Authentication/quota failures, refusals and arbitrary HTTP 400 responses are not claimed to be automatically repairable.

Protocol basis: [DeepSeek stateless Responses history](https://api-docs.deepseek.com/guides/responses_api/), [response limits including reasoning](https://api-docs.deepseek.com/api/create-response/), and [OpenAI reasoning-state replay](https://developers.openai.com/api/docs/guides/reasoning). Retained reasoning stays inside model history, never events, logs or diagnostics.

## Verification and scope

- Before/after and mutation checks reject the former behaviors: lost continuation, executing interrupted calls, malformed native argument replay, premature stopping of distinct corrections, missing repair-wrapper forwarding, retained recovery state after progress, and discarded write-denial evidence.
- 485 runner/routing tests, 795 related integration passes (two Linux sandbox skips plus a real-CLI opt-in subsequently run), 245 repair/CLI/media tests, 87 benchmark/architecture tests, and final 505 shared recovery/probe/application tests pass. Suites overlap and must not be summed. Full Ruff/format, diff and instruction-budget checks pass.
- Confirmation waiting, cancellation, bounded retries, signed-prefix validation, unchanged protected files and fresh-process application execution are verified separately. Additional real default-route environment-repair receipts are recorded in the [PR acceptance notes](https://github.com/Tavotto/Tavotto/pull/1421).
- This establishes successful execution for these cases. Full mathematical semantics, MATLAB/complex-glyph pixel fidelity, arbitrary scripts and dependencies, real OAuth refresh, the OpenAI service itself and same-model non-inferiority to Codex/Claude Code remain separate acceptance questions. Chat Completions remains a runner adapter, not an application connection.

Revert the default via `ai.builtin_default=false` for CLI preference or `TAVOTTO_AI_BUILTIN=0` to require CLI. In-flight failures do not automatically switch executors and replay side effects. Existing user preferences and adopted scripts are preserved.

## Follow-up: repaired external input missing from conversion context

The additional Pillow data-link handoffs at `0ca97589f` and `8d32f3054` completed adoption and reopened with seven figures,
but an output-name audit found that the upstream case has **eight distinct outputs**: the horse-mask figure was omitted in both.
Those two handoffs therefore do **not** prove complete output preservation; their receipts remain as partial-output failures.
The two environment repairs themselves (missing pandas and locating/linking the external image) succeeded. This finding does
not change the separate four-case conversion table above.

The model was shown a project-only snapshot without the external file and invented an `exists()`/skip branch. The application
had a confirmed read mapping, but mappings intentionally do not change filesystem probes. The generic correction exposes that
read policy and binds it to the current mapping fingerprint, while instructing the model to preserve original input paths,
read required inputs and let failures enter the correction loop. It neither patches a case-specific script nor changes
filesystem semantics, copies external data or reveals mapping destinations. Follow-up acceptance must compare output identities,
not just a nonzero figure count; the next real-model receipts are recorded in the PR.
