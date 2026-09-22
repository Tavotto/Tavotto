<!--
待发条目：已经合进 main、但还没有任何一版告诉用户的行为变更与迁移提示。

写在这里而不是留在 PR 正文里，因为发行说明是发版那天写的，写的人不会回头
翻每一个 PR 的「遗留」段——issue #244 就是这么漏掉的。

发版时（RELEASING.md 第 2 步）把下面的 `## ` 段落搬进
`docs/release-notes/vX.Y.Z.md` 并从这里删掉；带着没搬走的段落打 tag，
release.yml 的「拼 release body」当场红（scripts/check_pending_release_notes.py）。
这段注释留在原处。

英文写，与 release notes 一致：**按症状和触发条件写，不要按提交写**。
-->

## Added

**Export jobs carry a bounded phase trace that names the failing step.**
Trigger: any export (`POST /api/export`, or `tavotto_export` over MCP)
whose status you read back. Symptom before: a `partial` or `failed` job
told you *that* it failed and, at best, the error code of one output;
where in the pipeline it broke (source, compile, compose, raster,
inspect, publish, report) was not recorded. Now the job status has a
`trace`: one event per phase (phase, elapsed ms, outcome, error code),
capped at 64 events (the middle is dropped, head and tail are kept and
`truncated` says so), and `failed_phase` / `failed_code` point at the
*first* failure — later knock-on failures do not overwrite it. A job whose
figures all succeeded but whose style-check report could not be written is
`partial` with `failed_phase = "report"`. Existing keys are unchanged. (ADR
0071)

**Preparation results carry a complete execution receipt, and a plan that
no longer matches the world is refused with a named reason.** Trigger:
opening or re-preparing a figure through the preparation API (`POST
/api/engine/preparation`). Symptom before: the receipt described the
interpreter Tavotto *intended* to use; a health-check or probe result could
be mistaken for the report of the process that actually ran the script;
and a plan made before the working-directory grant, the interpreter choice
or the script's data changed was still executed. Now the receipt's runtime
section is accepted only from the process that ran the script
(`report_origin = build`, and its pid — or, on Windows where a virtual
environment's `python.exe` is a launcher, the launcher's child — matches
the one the control plane started; anything else is recorded as
`runtime_rejected` and the receipt is `partial`), it lists the project
files the script opened through Python (`inputs`, always `partial`: native
readers such as h5py are named as unobserved, never guessed), and
`binding_check` compares them with the data the plan was made against.
Before a session is started the plan is re-checked against the grant, the
interpreter decision and the data binding; any mismatch is
`preparation_plan_stale` with a `reason`, and nothing runs. A live session
whose data changed after it ran keeps showing the figure it made, marked
as an old snapshot (`binding_check.matched = false`) until you rebuild;
your edits are kept. (ADR 0070, 0071)
