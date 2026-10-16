# CG-03 execution minimization mechanism

`guardian_minimize.py` provides a bounded top-level AST deletion search, a native
truth + actual V2 server oracle, strict same-stage/signature acceptance and
evidence-aware triage/cluster proposals. It reuses `compat_driver`,
`compatbench_v2.observe`, Guardian launch isolation and existing process cleanup.
No new worker/environment manager exists.

Execution requires a committed, unchanged review plan listing every exact approved
source/data graph hash, including the original. Unknown reductions are queued for
review and never executed. All file relationships remain intact. The native title
truth must be independently reviewed; graph existence is insufficient. Native
failure, changed environment, zero execution, missing evidence or a different
Tavotto failure stage/signature rejects a reduction. Shared traceback alone never
confirms common root cause. Classification is a hypothesis until causal evidence
is provided.

The example plan is intentionally pending, with no approved hashes. Running
`python scripts/ci/guardian_minimize.py --plan docs/ci/guardian-minimizer-plan.example.json --out /tmp/cg-minimize`
returns exit 3/not_run. The example truth is a placeholder requiring native review,
not fabricated evidence. CLI has execution/time budgets; the approved oracle reruns
both legs for every accepted reduction.

**AC-06 is not complete.** Current cloud dependencies/fonts are network-blocked.
Six standard-library tests use explicit synthetic oracles to check mechanism
rejection and budgets; they are not actual native/Tavotto minimization evidence.
No product failure was minimized in this environment. Do not promote this stage
until a real reviewed family supplies original, every accepted trial, raw native
truth, same Tavotto signature, and final re-verification evidence.
