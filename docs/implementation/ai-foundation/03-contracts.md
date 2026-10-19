# 03 · 契约（schema 1，Phase 1 落代码）

字段名是逻辑名；Phase 1 实现时以与现有数据模型的贴合为准，改名要在这里同步。所有契约纯 JSON 可序列化、
纯标准库可校验；未知字段**拒绝**（防止模型或前端塞进越权参数），版本不认识整体拒绝。

## 一、TaskEnvelope（请求被持久化接受时冻结）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | int | 1 |
| `task_id` | str | = `ai_history.sessions.id`（一个任务就是一条会话，不另建 id 空间） |
| `task_type` | enum | `figure_edit` / `data_to_figure`；未来 `publication_normalize` / `figure_diagnostics` / `scientific_workflow` 只在注册时出现 |
| `project_ref` | obj | `{project_key}`——即今天 `ai_history.project` 的值（`Path(figures_dir).resolve()`），不新造身份 |
| `client_request_id` | str | 0132 原样；幂等只认 `ai_requests` |
| `user_intent` | str | 用户原话；只存本地历史库，不进遥测 / 日志 / 诊断包 |
| `input_refs[]` | obj | `{kind: script|data_file|figure, path_rel, sha256, size}`——受控引用，不带内容；`path_rel` 经 `contained_path` |
| `target_refs[]` | obj | `{kind: figure|axes|artist|script, stem, gid?, identity?, path_rel?}`；提交前不可变 |
| `base_revision` | obj | `{script_sha256?, manifest_hash?}`——提交时比对，不等即冲突 |
| `constraints` | obj | Figure Intent Contract（见 §四） |
| `output_contract` | obj | `{allowed_writes: [path_rel…], new_figures: bool, max_files: int}`——**适配器声明、后端强制**，Agent 不能扩大 |
| `execution_policy` | obj | `{executor: agent_id|deterministic, render: "safe"}`——验证渲染恒为 safe；native 不在此契约内 |
| `verification_policy` | obj | 必做层（L1…L4）与各层不可降级项 |
| `budget` | obj | `{max_agent_runs, max_wall_s, max_renders, max_cost_usd?}`——见 §五 |
| `privacy_policy` | obj | `{send_to_model: [字段名…]}`——明列哪些上下文会进提示词（例：脚本路径、gid、用户原话；数据摘要只在 data_to_figure） |

## 二、TaskResult

```json
{
  "schema_version": 1,
  "task_id": "…",
  "outcome": "completed | partial | needs_input | failed | cancelled | interrupted",
  "verification": {
    "figure":      {"verdict": "verified|failed|unknown|not_applicable", "checks": [CheckResult…]},
    "persistence": {"verdict": "…", "checks": [CheckResult…]}
  },
  "unmet": [{"constraint_id": "…", "reason_code": "…"}],
  "candidates": [{"id": "…", "status": "committed|discarded|stale", "evidence_ref": "…"}],
  "budget_used": {"agent_runs": 1, "renders": 1, "wall_s": 42.0},
  "explain": {"message_key": "…", "params": {}}
}
```

- `outcome` 与 `verification` **独立**：`completed` 只在所有不可降级检查为 `verified` 或 `not_applicable` 时允许；
  任一 `unknown` 的必检项 → 最高只能 `partial`；任一 `failed` → `failed` 或 `partial`（视失败项是否是用户要求本身）。
- 汇总规则：一层的 verdict = 最差项（failed > unknown > verified；not_applicable 不参与，全是 not_applicable 才是 not_applicable）。
- 开发验证（0131）**不在**这个结构里。

## 三、CheckResult（验证证据单元）

```json
{"id": "L2.series_data_unchanged.axes_0.treated",
 "layer": "L2", "subject": {"stem": "…", "gid": "axes_0.lines_1", "identity": "l1:…"},
 "claim": "曲线 treated 的数据与改前一致",
 "method": "worker data_fingerprint sha256 对比",
 "observed": {"before": "sha256:…", "after": "sha256:…"},
 "verdict": "verified", "reason_code": null,
 "preconditions": ["两次读取都在 safe worker、同一 matplotlib 版本"],
 "not_covered": ["自定义 Artist 的数据"],
 "evidence_ref": "ai_tasks/<task_id>/checks.json#…"}
```

`verdict` 取 `rendercore.inspector.VERDICTS`；`reason_code` 是闭集（Phase 2 定表）；证据存本地
`data_dir()/cache/ai_tasks/<task_id>/`，原始证据与可公开摘要分开（公开摘要只含 id / layer / verdict / reason_code）。

## 四、Figure Intent Contract

```json
{"targets": [{"stem": "…", "gid": "…", "identity": "…"}],
 "goal": [{"id": "g1", "kind": "fontsize", "subject": "…", "expected": 12, "objective": true}],
 "preserve": [{"id": "p1", "kind": "series_data_unchanged", "subject": "*"}],
 "allowed_changes": ["style", "layout", "text"],
 "forbidden_changes": ["data", "units", "axis_meaning", "fit", "error_definition", "series_order"],
 "user_exceptions": [],
 "subjective": ["更专业"]}
```

- 默认 `preserve` = 所有数据系列指纹、误差数据、xscale / yscale、未被点名的 xlim / ylim、轴标题里的单位、图数量与 stem。
- 用户原话明确要求改科学内容 → 写进 `user_exceptions` **之前**先 `needs_input` 确认（FigureBench `FB-V04`）。
- `subjective` 项永不产生 `verified`；L5 评价只写进 `explain`，不改 verdict。
- 断言类型闭集与 FigureBench 同名（`tests/test_figurebench_fixtures.py::ASSERTION_KINDS`），Phase 2 把判据实现为
  manifest + 数据指纹上的求值，FigureBench 的探针事实做对拍。

## 五、预算与策略（TaskPolicy）

| 档 | 判定（适配器声明） | 默认预算 |
| --- | --- | --- |
| simple | 路径 A 可表达 | 0 次 Agent；渲染 ≤ 1（预览） |
| normal | 路径 B，单脚本 | Agent ≤ 1 + 修正 ≤ 1；渲染 ≤ 2 |
| complex | 多面板 / 多约束 | Agent ≤ 2 + 修正 ≤ 1；候选 ≤ 2（只在隔离候选可建立时） |
| high_risk | 科学计算 / 数据转换 / 多文件 | Agent ≤ 1，修正 0（失败即报告）；必须 needs_input 确认 |

预算由 orchestrator 记账（会话行上的计数列 + 内存），超出即停并返回 `partial` + 未完成条件。修正准入：上一轮确定
结束 + 有具体失败的 CheckResult + 「失败条件集合 + 执行配置」与之前任何一轮不同。

## 六、Task Adapter 接口

```python
class TaskAdapter(Protocol):
    task_type: str                      # 注册键；与 AGENT_REGISTRY 正交
    schema_version: int
    def validate(self, envelope) -> list[Problem]: ...           # 输入适用性；不执行任何东西
    def intent(self, envelope) -> IntentContract: ...            # 目标 / 约束 / 不可验证项
    def plan(self, envelope, intent) -> Plan: ...                # 步骤 DAG（简单任务就一步）
    def tools(self) -> frozenset[str]: ...                       # 所需工具名（白名单）
    def verifications(self, intent) -> list[str]: ...            # 必做检查 id
    def explain(self, result) -> Explain: ...                    # 文案键 + 参数
```

适配器**不**实现：历史、去重、取消、恢复、模型发现、SSE、写文件——这些是 Task Core / AI 桥的。

## 七、Tool 契约

每个工具登记：`name`、输入 / 输出 schema、`task_types`、`access`（read / write_candidate / write_project）、
`side_effects`（none / render / fs_write）、`auto`（是否允许不经用户确认自动执行）、`timeout_s`、`cancel`（可中断 /
到点才停）、`evidence`（产出哪些 CheckResult）、`errors`（稳定错误码 + 可恢复与否）。

| 工具 | access | side_effects | auto | 复用 |
| --- | --- | --- | --- | --- |
| `inspect_project` | read | none | ✓ | registry / readiness |
| `inspect_figure` / `inspect_element` | read | render（safe） | ✓ | worker manifest |
| `inspect_data_source` | read | none | ✓ | 标准库 csv，受控摘要 |
| `propose_figure_patch` | read | render（safe 预览） | ✓ | patchspec + `/api/engine/render` |
| `stage_script_edit` | write_candidate | fs_write（候选目录） | ✓ | AI 桥 + 候选工作区 |
| `render_candidate` | read | render（safe） | ✓（预算内） | `pool.build` |
| `compare_figures` / `verify_scientific_invariants` | read | none | ✓ | `_compare_manifests` + 数据指纹 |
| `run_preflight` | read | none | ✓ | `preflight.run` + `interference.detect` |
| `inspect_export` | read | none | ✓ | `rendercore.inspector.inspect` |
| `commit_candidate` | write_project | fs_write | 随任务（用户已发起的改图视同确认，与今天相同） | `scriptbackup.replace` |
| `revert_candidate` | write_project | fs_write | ✗（用户点） | `ai_bridge.revert` |

模型给出的工具参数一律当不可信输入：路径经 `contained_path`、gid 必须在当前 manifest 里、prop 必须在 `editable[]` 里。

## 八、状态：映射到现有会话，不另建状态机

| 逻辑状态 | 落在哪 |
| --- | --- |
| Created | `ai_requests` 预留 + `sessions` 行（今天的 `record_start`） |
| Planning / Executing / Verifying / ReadyToCommit / Committing | `sessions.status = running` + 新列 `phase`（非终态枚举）；SSE 新增 `ai.stage{phase}`（前端加一种事件，`ai.delta` 的语义不动） |
| NeedsInput | 终态：`status = done`、`outcome = needs_input`；用户的回答是一个新任务（`parent_task_id`），**不**让会话长时间挂着占用脚本 |
| Completed / Partial / Failed | `status = done / failed` + 新列 `outcome`；`timeout` 原样 |
| Cancelled / Interrupted | 原样（`cancel()` / `mark_interrupted_running`）；取消后 orchestrator 在提交前复查 `status`，已取消就丢弃候选 |

新列（`_migrate` 加列，旧行为 NULL = 「旧路径产生的会话」）：`task_type`、`phase`、`outcome`、`verification`（TaskResult 的
verification 段，JSON）、`envelope`（JSON，不含 user_intent 以外的内容拷贝）、`parent_task_id`、`budget_used`。
每列都要在 `SESSION_DTO_KEYS` / `SESSION_TERMINAL_FIELDS` / `SESSION_INTERNAL_KEYS` 之一表态。

## 九、Harness Lab 的变更面

可优化（版本化、可回退、要过 holdout）：Prompt Pack 模板、任务类型上下文、Tool Guidance、Skill 触发条件、
工具调用顺序建议、规划策略、上下文压缩、非安全的执行器选择。

**不可自动优化**（不在 Prompt Pack 的 schema 里，改它们只能走普通 PR + 独立审查）：会话认证、文件写入范围与
`output_contract` 的强制、Agent CLI 的 sandbox / permission 参数、safe / native 授权、L1 与 L2 的必检项与阈值、
隐私与遥测白名单、备份与回滚、CI / CodeQL / 评审门禁。Prompt Pack 的 schema 用**白名单字段**表达这条边界：
加载器拒绝任何不认识的字段。
