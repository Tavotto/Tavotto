# Protocol acceptance and controlled builtin trial

Current follow-up: [shared recovery and the user-authorized builtin default](SELF-HEAL.md). Routing statements below describe the earlier frozen trial, before this switch.

2026-10-26. This follow-up prepares protocol acceptance and a reversible trial of the existing builtin preference. **The application default is unchanged. No release, user rollout or production feedback submission occurred.** The baseline and negative results in [FUNCTIONAL-FOLLOWUP.md](FUNCTIONAL-FOLLOWUP.md) remain valid historical evidence.

Subsequent conversion-loop fixes, isolated budget ablations and the final three-case retest are recorded in [LOOP-REPAIR.md](LOOP-REPAIR.md). The failed attempts below remain in that cumulative ledger; later passes do not replace them.

## What was changed

`scripts/dev/taf_bench.py` now accepts `--builtin-wire anthropic|openai-responses`, an optional `--base-url`, and `--prefer-builtin`. Official endpoints still come from the product catalog. Credentials remain in a local key file and the child environment, never report fields or command arguments. The preferred-mode benchmark temporarily overlays `ai.builtin_default=true` and removes the force override; it restores both afterward. It does not change the user's configuration.

The benchmark also records `routed_agents` and refuses an executor mismatch before spawning or spending model quota. A CLI fallback cannot be credited to builtin acceptance. This instrumentation does not change the application's fallback behavior.

The routing matrix covers Anthropic Key, Responses Key and Tavotto-direct OAuth, with and without a CLI, all default preference states and both force overrides. ChatGPT/Claude subscription connections continue to select their corresponding CLI. The application connection API exposes Anthropic and Responses; Chat Completions is currently a runner-level adapter, not a selectable application connection.

A new real-model Pillow failure exposed a common worker defect: `Path.mkdir(parents=True, exist_ok=True)` on an existing project directory was rejected as an outside-root mutation. The audit hook now raises `FileExistsError(EEXIST)` before the syscall for this no-op, preserving normal `exist_ok` behavior. It never permits that mkdir syscall. Even if the directory disappears after inspection, nothing is created. New outside-root directories and other writes remain rejected; no write roots or per-script exceptions were added.

DeepSeek then exposed a second shared defect: a real write denial lost its structured cause inside `script_probe_failed`. The model received generic advice to write into the project, although conversion deliberately allows only the candidate and infrastructure roots. At `fd6ab0f3d630f262653d7ca0e7c91f4cd778fb6f`, probe preserves `params.worker_code` and conversion provides the appropriate guidance: remove output-only directory creation, use memory or temporary files for intermediates, and retain Matplotlib `savefig` capture. The original project remains protected. Error-message substrings cannot trigger this diagnosis.

## Real-model evidence

The same three frozen external inputs, Sonnet 5.5, macOS source checkout and preinstalled conversion worker were used without supplying an additional oracle. Input tree hashes match across each case's attempts. The baseline at `336109aee77e5fe704d748d237ea6c4c284fb2d5` forced builtin; the fixed runs at **`b48a1a42dd0796b110611ec094b73f59dd527f22`** used the ordinary preference route and all actually dispatched builtin. This change in routing mode is recorded, not hidden as an identical setting.

| Case | Baseline | Fixed revision: task, adoption and fresh application process |
| --- | --- | --- |
| Pillow composite | Failed after correction; existing-directory checks were blocked | 3/3 completed and reopened; 2/3 without an outer correction restart |
| R percent stack | Completed and reopened | Completed and reopened; one figure |
| MATLAB Airy surface | Completed and reopened | Completed and reopened; one figure |

There are **8 conversion attempts across 3 distinct cases**, including the baseline failure. At the fixed revision, **5/5** completed, adopted and reopened; **4/5** completed without an outer correction restart. These are repeated development trials, not a population success estimate or a new 23-case full run. The model's internal check/edit iterations are separate: all three fixed Pillow attempts encountered check findings before final success; one exhausted the three-check allowance and succeeded after the bounded outer correction. R and MATLAB each passed their first tool check.

Two Pillow attempts captured five editable compositions and kept the resized input as a raster intermediate; the third captured six figures. **Five captured figures must not be reported as six editable outputs or complete fidelity.** These runs establish execution/adoption/reopen, with output completeness and semantics still assessed separately. All protected original files were unchanged during conversion, and the adopted script bytes were unchanged by the fresh run. Fresh R/MATLAB runs used automatically prepared managed environments; fresh Pillow used the current process environment. This is not clean-machine or installed-package acceptance.

The preferred-route repair trial deliberately removed pandas from an isolated environment containing the adopted R-to-Python script. The real application observed the missing package before starting the agent. The agent presented two reviewed cards: install `pandas`, then run the actual script in safe mode. It finished `fixed` only after the application reported `completed/succeeded` with a figure. A new application process, with no forced worker and no model call, selected the same project environment and also captured successfully. The source hash was unchanged. Runtime success does not imply native R pixel equivalence; that oracle was not supplied.

Two FigureBench cases (`FB-S02`, `FB-S03`) also used preferred routing and a real Anthropic model: both completed in one round with goal and preservation verified, and no unintended change. The sample is intentionally small and does not independently justify a default switch.

After adding the dispatch-identity guard at `1f46e4ff071dac2e0e865ada3c7f7a64f6b09ac0`, both cases were repeated with the same model and preference mode. Both again passed in one round; `routed_agents=["builtin"]` and two actual agent calls were recorded. The earlier two attempts are retained separately. This benchmark-only guard does not change the runtime code used for the conversion and repair evidence at `b48a1a42d`.

PROTOCOL-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)） records every attempt, producer revision, input/candidate/report/harness hashes, corrections, confirmation counts and fresh-process outcomes. Raw prompts, logs, absolute paths and credentials remain local.

## DeepSeek native Responses evidence

The user supplied an API key in a local file. The application used a custom Responses connection to `api.deepseek.com`, model `deepseek-flash`, through the ordinary builtin preference route. No subscription credential or persistent user setting was changed. The external inputs, preinstalled worker and product budgets were unchanged; the model differs from the Anthropic trials, so this is protocol/task coverage, not a model-controlled comparison.

At `30a07e92eebc912652a8948eb3d6bd6dc154db5f`:

- FigureBench `FB-S02` and `FB-S03`: 2/2 completed in one round, goal and preservation verified, zero unintended changes; actual `routed_agents=["builtin"]`.
- R percent stack: completed, adopted and reopened in a fresh application process with one captured figure and unchanged source bytes.
- Missing pandas in the same adopted R candidate: the initial application failure was observed, then two reviewed cards authorized installation and actual execution. The agent finished `fixed` only after `completed/succeeded` with a captured figure; a new application process also succeeded with the same project environment and unchanged source.
- Pillow composite: the first attempt and one diagnostic repeat both reached `agent_max_turns`. The diagnostic repeat exposed the incorrect write-denial guidance after the editable-output check rejected raster drawing. Neither attempt was adopted.
- MATLAB Airy surface: the first attempt reached the token budget after 14 turns. An internal check had captured a figure, but the task did not complete or adopt; it remains a failure.

After the guidance fix, the bounded Pillow repeat still failed: after rejecting raster drawing, the agent made repeated invalid `edit_file` calls and terminated with `tool_error_loop/bad_arguments` after 18 turns. It never reached a new write-denial check, so this attempt does **not** demonstrate a real-model success caused by the guidance fix. Empty replacement strings are supported by the tool; the retained trace does not include raw arguments, so the exact malformed argument is not established. No further retries are being counted as independent cases.

The MATLAB diagnostic repeat on the same fixed revision reached the token budget after 17 turns (213.198 s). Two internal checks captured a figure, but the agent continued editing and did not complete or adopt. This remains a second failed attempt, not a successful conversion. The original attempt and repeat used the same model, inputs and unchanged product budgets.

The DeepSeek ledger therefore contains **6 conversion attempts across 3 distinct cases: 1 completed/adopted/reopened, 5 failed** (Pillow three attempts, MATLAB two, R one). The initial three-case batch was 1/3; diagnostic and post-fix repeats are shown separately, not substituted for those initial results. Figure editing and the separate package-repair trial passed as described above.

All completed and failed attempts retained the original protected files. Content-free transport traces record successful HTTP 200 requests containing image inputs and multi-turn tool results. This establishes use of the real Responses protocol, not success of every task. DeepSeek's [native Responses documentation](https://api-docs.deepseek.com/guides/responses_api/) describes its full-history model; these traces confirm full-history requests. It does not certify OpenAI-specific service behavior, real OAuth refresh, pixel fidelity or readiness to switch every API connection.

## Protocol coverage and its limits

| Connection / adapter | Local protocol and lifecycle tests | Real service acceptance in this follow-up |
| --- | --- | --- |
| Anthropic API Key | Passed | Selected conversion, repair and figure-edit workflows above |
| Responses API Key | Passed, including the application preference route | DeepSeek native Responses: selected workflows passed; complex conversion failures above retained. OpenAI service not tested. |
| Anthropic Bearer / Tavotto-direct OAuth | Routing, Bearer headers and refresh regressions passed | No real OAuth refresh/service trial |
| OpenAI Chat Completions | Runner-level request/tool/media tests passed | Not run; not an application connection |
| ChatGPT / Claude subscription | Retained CLI routing and cancellation regressions | Historical CLI evidence remains in the functional report; no new comparison here |

An API test connection is an ordinary provider endpoint, API model and key, not a special testing URL. A working Codex/ChatGPT subscription does not supply evidence for the builtin Responses adapter. No subscription credentials were copied or repurposed. The benchmark supports this explicit paid invocation (replace placeholders and choose a new output directory):

```sh
PYTHONPATH=src .venv/bin/python scripts/dev/taf_bench.py \
  --real builtin --builtin-wire openai-responses --prefer-builtin \
  --key-file /absolute/local/key-file --model API_MODEL \
  --only FB-S02,FB-S03 --max-runs 6 \
  --work /absolute/new-run-directory --out /absolute/report.json
```

An authorized compatible gateway additionally uses `--base-url`; provider and model identity must remain in the report. Passing these two cases is an entry check, followed by the same conversion, resource, package-repair and lifecycle scenarios. Stubbed protocol tests are never counted as real-model acceptance.

## Controlled trial and rollback

The activation contract is [ADR 0147 §五](../../adr/0147-builtin-agent-runner.md). Once the release's normal qualification is complete, designated preview installations can use the existing `ai.builtin_default=true` preference with qualified connections. The setting is per installation, **not per protocol or a server-managed percentage rollout**; do not activate an untested connection inadvertently. No rollout cohort has been activated by this change.

- Keep installed Codex/Claude subscription connections available. They retain their original CLI route, including when builtin preference is enabled.
- Setting `ai.builtin_default=false` restores CLI preference where an applicable CLI is available; without a CLI, usable API connections can still use builtin. `TAVOTTO_AI_BUILTIN=0` disables builtin entirely and requires an applicable CLI. Both are existing controls, with regression coverage.
- Preference-mode fallback occurs before dispatch when builtin cannot accept the connection. A failed in-flight task does not silently rerun on another executor. Preserve the failed result and let the user choose the next attempt.
- Record first-attempt completion, final completion, outer corrections, internal check findings and confirmation-card count separately. A user waiting to confirm a card is not a stall. Check cancellation reaches a terminal state, stops its subprocess and releases the session slot.
- Trial records include revision, protocol, task family and closed outcome codes. Use local reports or consented private feedback with a reviewed, redacted diagnostic bundle. No new telemetry, automatic source upload or public issue submission was introduced.
- A false `fixed`, protected-file mutation, unexpected executor, stuck cancellation or unusable repair card stops expansion and requires a reproducible fix. Compare builtin and CLI on the same inputs, model and budget before claiming non-inferiority; different-model paired successes are not that comparison.

These controls make a scoped trial reviewable. They do not substitute for resolving the observed complex-conversion failures, real OAuth service evidence, installed-platform qualification or observed user results. Do not expand the default to all API connections based on the passing tasks. No production rollout success is claimed.

## Deterministic verification and counterfactuals

| Verification | Result |
| --- | --- |
| Write-tripwire and real conversion-worker suites | 60 passed |
| Connection routing, benchmark, runner/media and cross-process cancellation | 420 passed |
| Repair stalls, confirmation asks, authoritative application verification and architecture/rule index | 153 passed |
| Expanded benchmark preference matrix after its fixture refinement | 8 passed |
| Complete benchmark and rule-index suites after adding dispatch verification | 71 passed |
| Structured write-denial correction, conversion/probe/write guards and architecture/rule index | 202 passed |
| Full repository Ruff and formatting | Passed, 1,226 Python files formatted |
| Diff whitespace and rule-document budgets | Passed |

Removing the existing-directory fix makes five new worker/application/race tests fail (exit 1), while the new-directory rejection control still passes. Restoring it passes all 60 tests. Removing the benchmark's preference overlay makes all four preferred-route cases dispatch the available CLI and fail, while four forced-route controls pass; restoring it passes all eight. These are deliberate negative tests, not evidence inferred from a green implementation-mirroring assertion.

The dispatch-identity negative test first reproduced a real route selection of `claude` under a requested builtin benchmark (exit 1). With the guard, the report records `routed_agents=["claude"]`, `executor_mismatch` and zero agent calls; the test and complete benchmark suite pass. A requested executor label alone is not accepted as execution evidence.

The new real-worker correction test failed against the old diagnostic mapping because the conversion-specific hint was absent. With the structured-cause fix it corrects, captures and adopts, while the denied directory is never created and original files remain unchanged. A separate negative test confirms that a user exception merely mentioning `write_outside_roots` keeps its actual script-error diagnosis.

Cancellation, stalled-model termination, authentication/quota failures and media/tool protocol behavior use local HTTP fixtures and real subprocess lifecycle tests; the remote services were not deliberately faulted. CI must be checked on the actual latest PR head; older green runs do not certify a later report or code commit.
