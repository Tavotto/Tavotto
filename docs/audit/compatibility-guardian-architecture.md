# Compatibility Guardian — Architecture & Gap Report (CG-00)

Recorded 2026-10-16. Initial clean checkout: `f5062da92d8751170ba79f162fafe5b363ebc787`.
Implementation base: `7675956233a812faee398eb9d5dede56e1b8329d`, tree
`bf341b0d526284602a6ebb7a9b012065233a481d`.
`git fetch origin main` returned 128 / CONNECT proxy 403. The connected GitHub
read channel supplied all 97 changed paths. Every blob ID, the full index tree,
and reconstructed signed commit ID were checked before updating local origin/main.
The checkout is shallow at this commit; it does not claim a successful Git fetch.

## Six parallel read-only audits, completed before implementation

All six roles read the root and relevant subsystem AGENTS/rules/ADRs. Their code
baseline was f5062da92. The 97-path update changes neither CompatBench nor V2 nor
Foundation harness code; its engine changes concern colorbar and diagnostic detail.
No role edited code, ran a fixture, installed dependencies, or certified a test.
`.agents/` contains only `plugins/marketplace.json`; there is no `.agents/skills`.

| Role | Evidence and finding |
| --- | --- |
| A architecture | `compat_matrix.run_group`, `foundation_app.running_app`, `compatbench_v2.observe` already supply the engine/server paths. `route_desktop_project` is registry/API, not a window. `test_execution_fallback_e2e.sidecar_app` supplies the real sidecar protocol, not WebView. |
| B environment/authority | `pool.resolve_worker_python`, `worker_env`, `envlease`, `execspec`, `execpolicy.decide_fallback`, `prepsession._plan_fallback` are authoritative. API/MCP cannot claim desktop identity. Native receipt lacks spawn source revision; never fill it by guessing. Dependency impact authorization remains untouched. |
| C capture/edit/replay/export | `figcapture`, `figsession`, `stage_edit`, `stage_replay`, `stage_export`, `stage_fidelity` are reusable. Roles/fields are weaker than scientific values. Save observations are not historical Figure snapshots. Product writeback already has geometry AND RGBA verification. |
| D CI/machine interface | Existing hosted PR `compat-smoke`, nightly minimum/bundled/browser, and `_lab-qualification` must be reused. Existing report can omit final gate verdict; summary defaults a missing `ok` to true. V2 rejects mixed records but does not bind a uniform old SHA to the current checkout. |
| E corpus/provenance | 149 CompatBench cases: 32 shapes, 47 Artists, 30 layout, 14 scientific stack, 26 metamorphic. 15 V2 fixtures; Foundation synthetic projects carry independent truth. No per-file provenance/seed contract was found. |
| F security/evidence | Safe is Python guards/cwd, not OS isolation. Native/reference drivers inherit host access. Only reviewed, digest-bound repository fixtures qualify. Unknown scripts and generated candidates remain static. Missing stages, zero executions, bad native controls, stale SHA and mismatched environments must fail closed. |

## In-flight ownership

Initial GitHub snapshot: #1023, #1024, #1028 and #1002 were merged; #1034 and #1044
were open. #1055 head `f2324f7642816f6e9d265592efd3132d44b0a6ec` and #1048
are owned by another task; Guardian does not modify their branches or duplicate them.
#1085 introduces `agent_verify` and engine triage; Guardian is a specialized
compatibility evidence adapter, not another universal verification dispatcher.

The latest #1034 writer-pattern finding is explicitly deferred by the maintainer:
[discussion r4289852315](https://github.com/Tavotto/Tavotto/pull/1034#discussion_r4289852315),
tracked by #1086. It is **unfixed / deferred**, not a passing Guardian acceptance.
Follow-up: #1034 merged at 04:09:15 UTC, head
`aae624a6d053cc6c95e6929a62936c7000c94dde`, squash
`c6f577baec5442775ba1f2e02764ea738a9b2196`. Latest main observed through the
connected read channel is `a48872109012b13cb6c2e83265cd4f7e52782ef2`.
The writer-pattern/alias risk remains deferred under #1086; merging #1034 does
not qualify its independent Native acceptance. New product reproduction must
bind the later actual CI checkout and must not repair already merged work.
The complete observed open-PR list and exact heads are in `compatibility-guardian-state.json`.

## Nine-stage paths and what each proves

| Stage | Existing engine entry | Server / independent evidence | Gap / limit |
| --- | --- | --- | --- |
| discover | `compat_matrix.stage_discover` → merge/Registry/probe | V2 `/api/project/scan` → preparation session | Static discovery and execution probe differ; no execution before server run claim. |
| execute | `_fresh_worker` → `pool.one_shot` | V2 fixture external `exec_log`; SQLite side effects | Build count and HTTP count are not script counts. Environment/source must be actual launch facts. |
| capture | `run_group` checks stems AND count | V2 `captured`, then real `validate_captured` render | Captured declaration or PNG presence alone is insufficient. |
| open | real `override([])` | server render | Engine/server open does not prove desktop first screen. |
| semantic | `stage_semantic` roles/editable | V2 title truth; Foundation numeric truth | Both sides having a graph can still use different data. |
| edit | `stage_edit` cumulative patch, zero-list undo | `test_equivalence_matrix`, Foundation edit | Field roundtrip does not prove pixel restore or OS keyboard undo. |
| replay | `stage_replay` hot/clear/fresh | product `_compare_manifests`; writeback tests | Existing property comparator intersects keys; missing fields can escape it. |
| export | `stage_export`, decoder and warnings | Foundation PDF/PNG/TIFF numeric/byte identity | Existing CompatBench exports zero patches and deletes successful artifacts. |
| fidelity | `stage_fidelity`, native `compat_driver`, shared pixelcompare | product RGBA comparator | CompatBench pixel metric is grayscale; native uses final Figure, not every save occurrence. |

`compat_driver._fail` intentionally exits zero with `ok:false`; structured payload
and independent checks are mandatory. Native failure is classified as fixture or
environment first, never a Tavotto defect. Existing `classify(case, {}, {})` may
return full_support; Guardian must require a complete stage set and actual evidence.

Product classifications remain the six existing CompatBench classes. Guardian
execution statuses are a separate contract: passed, failed, expected_limitation,
not_reproduced, environment_unavailable, not_run, invalid_fixture, unknown.
A known baseline failure matching its shape proves reproduction, not compatibility.

## Public contract and security

Each run binds actual git HEAD/tree/dirty digest, expected SHA, UUID run_id,
registered suite/case set, source/helper/data/corpus hashes, interpreter/package
identity before and after, actual fixture script execution counts, native control,
nine explicit stage statuses, evidence relative paths/hashes, and unverified scope.
An atomic machine report has a required terminal verdict. Validation rejects missing
fields, stale SHA, changed evidence, empty execution and incomparable environments.
Null means unobserved, never zero or pass. A report is not an authenticated attestation;
malicious scripts can forge their own counters, hence the reviewed-fixture restriction.

No new auth endpoint, RootAuthority exception, worker, environment manager, automatic
install, Native escalation, product writeback shortcut, PyMuPDF runtime import,
baseline auto-update, merge, release or production mutation is authorized here.
Repair branches are isolated and never rewrite a user's research source.
Temporary HOME/data/config/cache are explicitly set; inherited secrets are excluded
from runner child launch. This is directory hygiene, not OS sandboxing.

## Acceptance ledger (initial audit only)

All initial execution results below are `not_run`; audit is not execution evidence.
Environment: isolated Linux engine/server tests, pending dependencies/fonts.

| Acceptance | Existing entry / positive oracle | Negative oracle / boundary |
| --- | --- | --- |
| ordinary one plot | V2 s01: title plain, one real script execution | extra execution, no real render fail |
| show only | shape_pyplot_show_only and real product route | show returning / exit0 insufficient |
| savefig readback | V2 s06: measured size/title | absent file fails; API remains Safe |
| replace scientific value | V2 s04: title value=2 | wrong scientific value fails |
| multi Figure | shape_multi_figure and V2 s25 | exact total and failed stems retained |
| helper+CSV | Foundation split_scripts_data: [1,3,5,7] | wrong cwd / same-name data rejected |
| provider/install consent | joint_dependencies / dependency impact tests | observation cannot authorize installation |
| OpenCV/Pillow stages | s26 dependency plan, s27 installed PIL, s06 readback | no claim of successful OpenCV processing from import plan |
| native failure | s16a/s16b plus independent native | fixture error not product_bug |
| wrong reduction | CG-03 new acceptance contract | missing package/syntax cannot preserve original failure |
| empty execution | corpus and V2 empty-set guards | all not_run never passed |
| stale SHA | Guardian expected HEAD and evidence digest | one uniform old SHA still rejected |
| restricted origin | execution_policy / fallback origin tests | API/MCP never auto Native |
| graphs with wrong semantics | Foundation same_name_data/FO32 numeric truth | decoy [601,1201,2401] fails despite output |
| both correct | s01 and native truth | no invented defect / forced repair |

Real Windows WebView2, macOS WKWebView, Intel desktop, native UI input/cancel and
installation paths remain `not_run` in this Linux environment. Browser testing does
not discharge those rows. Controlled corpus results are never user success rates.

## Independent PR split and order

1. CG-01: this audit, thin registered Agent Runner, versioned JSON Schema, strict
   evidence validation, atomic report, raw run evidence. Reuse matrix/V2/driver.
2. CG-02: deterministic seed/template/AST candidate generator, source graph and
   per-file provenance, static only; no external LLM API.
3. CG-03: evidence-bound root-cause triage/cluster proposals and bounded minimizer;
   each accepted reduction reruns native correctness and same Tavotto failure.
4. CG-04: reproduce a real current-main gap, minimize, independent red/green and
   mutation evidence; product fix in a separate branch/PR. Do not invent a bug if
   the author already fixed it, or claim a product fix while environment is absent.
5. CG-05: observation-only report in existing hosted CI/nightly/manual tiers;
   no new required gate, no unknown scripts on persistent self-hosted runners;
   promote produces review material and never updates baseline automatically.

The primary agent is the unique source writer. Read-only auditors/reviewer inspect
evidence and criteria independently. Each PR records base/head, commands/exit codes,
raw evidence, CI and platform limits, remaining risks, and merge eligibility.
