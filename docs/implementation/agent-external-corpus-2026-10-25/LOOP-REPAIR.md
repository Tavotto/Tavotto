# Bounded conversion loop repair and real-model retest

2026-10-26. This follow-up tests whether a user can convert an external script, explicitly adopt the new file, and reopen it in a fresh application process that captures figures. Scientific and visual fidelity remain separate acceptance dimensions. **Default routing is unchanged. No merge, release, installed-package acceptance or user rollout occurred.**

The previous [protocol results](PROTOCOL-ROLLOUT.md) remain historical evidence. LOOP-REPAIR-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)） retains every previous DeepSeek conversion attempt plus every diagnostic, intermediate fix, budget ablation and final run in this follow-up. It includes producer revisions, input hashes, effective budgets, tool error codes, runtime identity, adoption and fresh-process outcomes. Raw source, images, paths and credentials remain local.

## What failed, and which layer was responsible

| Observation | Attribution and general correction |
| --- | --- |
| Valid JSON used `new_string` with `old`; repeated generic errors exhausted the tool loop | Model did not follow the original spelling; the runtime gave insufficient recovery information. Both explicit text-replacement spellings are now declared in one shared contract. Conflicting duplicates and non-strings are rejected, and errors explain the actual schema without echoing values or guessing unknown keys. |
| A validated candidate on the last model turn still needed an extra model reply | Product completion protocol. `finish(summary)` delegates to the same real final check; a true validation result ends the loop immediately. Intermediate checks do not complete it. Parent validation and explicit adoption still run. |
| An edit changed the file hash, but only create/append returned the current receipt | Product tool contract. Edit now returns the updated SHA-256; append still refuses a stale receipt. |
| An intermediate fix used top-level schema alternatives and Anthropic rejected the request before generating | Product protocol regression, not model inability. A controlled real-service probe returned HTTP 400 with root alternatives and HTTP 200 with flat properties. The shared handler validates required alternative names. The failed attempt is retained. |
| A complete 6,268-character code block was rejected by a 6,000-character chunk ceiling | Product imposed a second size authority. The chunk size is now advice; complete blocks are accepted within the existing 200 KiB UTF-8 file limit. Truncated responses, oversize cumulative files, stale hashes and secret-bearing writes remain rejected. |
| MATLAB kept editing after a passing check; Pillow needed more correction steps | Model planning plus product budget calibration. Conversion gets remaining-resource reminders at 50% and 75%, an explicit delivery tool, and sufficient measured turn/token allowance. It must still correct known data, semantic and missing-output problems. |
| Model used raster drawing, wrote an output directory, or supplied old text that no longer matched | The validator correctly rejected these candidates/calls. The model must correct them; the sandbox and editable-output checks are retained. The final Pillow run recovered from two failed checks and one `old_not_found`, then passed its third check. |

The common acceptance guidance applies to CLI and builtin conversion through the same prompt authority. It asks the model to finish the complete runnable script and disclose unresolved visual differences, rather than repeatedly reimplementing native rendering. A guarded `main` entry avoids import-time duplicate captures. No per-corpus filename, language-specific success exception, additional write root or automatic adoption was introduced.

## Is the time limit too short?

The observed stops were **turn/token budgets**, not the 900-second deadline. At the same product revision `40ed69f2bd36e1f3b06d2034082aa62341843495`, with identical inputs, model, protocol, runtime and output limits, a separate ablation changed only 20 turns / 400,000 cumulative tokens to 32 / 800,000:

| Case | Turns | Actual cumulative input + output tokens | Wall seconds | Adoption and fresh reopen |
| --- | ---: | ---: | ---: | --- |
| Pillow composite | 22 | 420,040 | 158.164 | Passed; 6 captures |
| MATLAB Airy surface | 16 | 676,836 | 304.642 | Passed; 1 capture |

Those successful paths exceed the old budgets but fit well inside 900 seconds. This is evidence of insufficient allowance for these workflows, not proof that every failed generation would succeed with more tokens. Model outputs remain stochastic; attempts are not reruns of a frozen generated transcript.

The final product default therefore changes **conversion only** to **32 turns / 800,000 total tokens**. The 900-second deadline, 32,768 single-response limit, three-check allowance, cancellation and consecutive-error cutoff remain. All input tokens, including cached input, and output tokens still count. The worst permitted token total per conversion doubles; this is not a claim that monetary cost always doubles. Figure editing, environment repair and routing defaults are unchanged.

## Final fixed batch

Producer: **`eebd0765c06326fccfc2c3bfe060d4ced0bf2374`**, clean checkout. DeepSeek uses the actual `openai-responses` application connection, model `deepseek-flash`, ordinary builtin preference in isolated test configuration, and the same frozen external input trees and preinstalled conversion worker as the earlier trials. No extra reference image, native oracle, effort override or test-only budget override was supplied. Every run actually dispatched builtin. The new default budget is intentionally different from the older baseline and is reported above.

| DeepSeek case | Turns | Input + output tokens | Wall seconds | Completed / adopted / fresh reopen | Fresh captures |
| --- | ---: | ---: | ---: | --- | ---: |
| Pillow composite | 18 | 299,074 | 157.763 | Passed / 200 / succeeded | 6 |
| MATLAB Airy surface | 9 | 174,133 | 139.723 | Passed / 200 / succeeded | 1 |
| R percent stack | 12 | 241,492 | 133.089 | Passed / 200 / succeeded | 1 |

The final targeted batch is **3/3 complete, adopted and reopened across three distinct cases**. None failed with `bad_arguments`, chunk size, turn limit or token budget. Completion used trusted successful final checks; an ordinary intermediate pass is not credited as completion. All protected original files and adopted-script bytes remained unchanged by the fresh run.

A separate Anthropic `claude-sonnet-5-5` control used the same final code and Pillow input: 5 turns, 61,666 cumulative tokens, 44.181 seconds, completed/adopted/reopened with 6 captures. This is cross-protocol compatibility evidence; the different model is not a controlled performance comparison. Fresh runs use a new application process, no model call or forced worker, and explicitly confirm only offered preparation actions. This is source-checkout acceptance through the real application API and worker, not browser or clean-machine release acceptance.

## Preserve failures and intermediate results

The ledger contains **28 attempts**, including the prior six DeepSeek attempts, across the same three input cases. Iterative development attempts must not be aggregated into a representative success rate.

| Stage | Recorded results |
| --- | --- |
| Prior protocol batch | 6 DeepSeek attempts: 1 completed, 5 failed. |
| `c469f0a3f` diagnostic | Pillow reached 20 turns after a passing internal check; not adopted. |
| `562ecc647` final validation and error guidance | DeepSeek R/MATLAB completed; Pillow still reached 20 turns. MATLAB produced two captures of one plot, so this is not full output-identity acceptance. |
| `a5406bd70` text parameters and receipts | DeepSeek Pillow/R completed; MATLAB exhausted tokens. Anthropic schema HTTP 400 before any model turn. |
| `5a66722ba` flat schema, finish and reminders | DeepSeek Pillow/R completed; MATLAB exhausted tokens after a complete block was rejected. Anthropic completed. |
| `40ed69f2b` complete blocks | DeepSeek MATLAB/R completed; Pillow exhausted tokens at turn 20. Anthropic completed. |
| Separate budget ablation at `40ed69f2b` | Pillow and MATLAB completed with explicit 32/800k overrides, then reopened. These are not tests of the old product defaults. |
| Final defaults at `eebd0765c` | DeepSeek 3/3 and the separate Anthropic control completed, adopted and reopened. |

Earlier Pillow runs sometimes captured only five final composites, omitting the resized-image output; they remain runnable results, not proof of complete fidelity. The final Pillow captures include that sixth output. Captured counts alone still do not certify data, editable-object identity, alpha masks or pixel equality.

## Regression evidence and limits

- The runner/conversion/media/cancellation/application/prompt/architecture/rule suites cover 522 passing cases across the closeout-fix validation batches. The first run had 519 passes and one closed-tool-set assertion failure; the allowlist was updated for `finish`, two real-child application variants were added, and the 41-case follow-up passed. These counts overlap and must not be summed.
- Complete-block and related suites: 306 passed. Final budget-default and related suites: 309 passed. Full Ruff, format, diff and repository rule-budget checks passed.
- Counterfactuals before the fixes: 3 finalization cases failed; 13 parameter/receipt cases failed; 4 finish/reminder/schema cases failed. Two valid complete-block cases were wrongly rejected and two oversize-append cases returned the wrong error class. Restoring the corrections passed. Log hashes are in the ledger.
- Real-child application tests require parent Task Core validation before adoption; a false report or a candidate rejected by the parent cannot be rescued by the terminal tool. Cancellation, token/time exhaustion, same-batch trailing mutation, stale append, path/secret restrictions and complete-response truncation remain covered.

This is a targeted three-case regression, not a new 23-case full-corpus run, representative success-rate estimate or proof of non-inferiority to Codex/Claude Code. No new missing-package repair model trial was added in this follow-up; previous package/card/actual-run evidence remains in [FUNCTIONAL-FOLLOWUP.md](FUNCTIONAL-FOLLOWUP.md) and [PROTOCOL-ROLLOUT.md](PROTOCOL-ROLLOUT.md). Native R packages/MATLAB toolboxes, arbitrary user data, real OAuth refresh and OpenAI-service behavior are not certified here. MATLAB layout, ticks, antialiasing, fonts and complex glyphs remain a separate fidelity program.
