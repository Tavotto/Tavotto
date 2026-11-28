# 建议待办工具（ADR 0214 第 3 期）

> 2026-11-28 新写（第 3 期 3a：插件工具 + 宿主自己问用户）。速查行与排版标注那行合并（`codex-plugin/AGENTS.md` 的「按改动路径找细则」表，由 `layout-annotate.md` 的 agents-route 生成，为守棘轮上限不单列一行）；这里是这一主题的**唯一全文**，改规则改这里，并同步那一行。
> 看护用例：`tests/test_changeplan_agent_card.py`（窗口里的卡：列表 / agent-applied / 先落定的赢）、`web/src/store/agentPlanDiscovery.test.ts`、`suggestionCardAgent.test.tsx`、`tests/test_mcp_suggest.py`（出方案只算不写、逐条结果、只写勾着的、过期 / 别的会话拒绝、旧引擎不出现）、
> `tests/test_changeplan*.py`（引擎流水线，桌面端点同一份）、`tests/test_mcp_server.py::test_tools_list_shape`、`tests/test_mcp_error_next.py`。

- **只翻译，同一条流水线**：宿主交进来的建议经 `changeplan.host_proposal` 变成与方案模型输出同形的提议，**原样交给 `validate_proposal`**
  （藏数据 / 未授权图幅 / 连续曲线逐点标记 / Unicode 上下标，与改图助手同一套守卫）；之后 `require_log_lower_limits` → `candidate_patches` →
  `realize`（候选渲染、新 warning 拒、出界腾边距）→ `build_plan`，与桌面 `/api/change-plans` 是同一份代码（`app.py` 也调 `realize`）。
  桥里不许再写一份校验或流水线。
- **出方案不写会话**：候选在会话的热 worker 上试渲染（`Session.acquire()`，唯一 worker 入口不变），结束后把会话此刻的 override 放回去；
  `session.patches` 不动。跑在真实 Python 里的会话（`session.native`）不试渲染（`suggest_native_unsupported`，与桌面 `safe_only` 同一处置）。
  回给宿主的图宽 `SUGGEST_SNAPSHOT_WIDTH`（900 px，要进宿主上下文）。方案存进与桌面同一个方案目录（`changeplan.save`，项目 id 同一把尺 `bridge.project_id`），带 `agent.session`；
  与桌面的方案一起受 `changeplan.sweep` 管（6 小时过期、每项目最多 12 份）。
- **只写用户勾的**：`suggest_apply` 写 `apply_groups(B0, 组, 勾着的图内待办)` 这份全量列表，经 `apply_overrides`——规范化合同照旧：
  `user_authorized` 由宿主显式带（与 `tavotto_apply_overrides` 同一个声明），不在桥里替它写死；脚本待办不在这里改，原样回 `script_todos`，宿主改脚本。一份方案只应用一次（`suggest_plan_applied`）。
- **出方案之后图变了就拒**：`fingerprint(script_revision, session.patches)` 对不上出方案时的基线 → `suggest_plan_stale`，不在别的底子上套旧修改。
  方案只认出它的会话与图（`suggest_plan_unknown`，别的会话 / 过期 / 坏 id 同一个码）。
- **新错误码都要有下一步**：登记在 `nextstep.NO_NEXT`（`tests/test_mcp_error_next.py` 钉住）。三个工具同 0208 / 0212 的处置**不进** `usage.TOOLS`
  （走 `other` 桶，不升同意版本）。旧引擎（`changeplan` 缺 `SUGGEST_ENGINE_API` 里任何一个）三个工具不出现，调用回 `suggest_unavailable`。
- **窗口里的卡（3b，ADR 0217 §五）**：绑定到排版子图的会话出方案时，`base.target` 记子图的 `fileId`（桌面口径），会话的图名另记 `agent.stem`
  （`_suggest_load` 认 `agent.stem`，没有才认 `base.target`），再记 `agent.panel` / `agent.layout` / `agent.at`；没绑定的照旧、桌面不显示。
  - **发现**：桌面轮询 `GET /api/change-plans?agent=1`（只列本项目、6 小时内、带 `agent.panel`、`applied` 与 `agent_applied` 都没有的，最新在前、最多一个项目的方案上限；
    字段 `{id, target, panel, layout, created, todos}`）。节奏与「并进来」共用 `AGENT_WATCH_INTERVAL_MS`（回到前台 + 每 60 秒）；不推送。
    `web/src/store/agentPlanDiscovery.ts`：`target` 对得上当前画布上一张可改的图、且那张图此刻的 override == 方案 `base_patches`（卡片「图已改过」同一个判据）才出卡；
    同一份方案不重复出，用户「收起」后本次运行不再出（只在窗口本地收起，**不**调 `/discard`——那份方案是插件的）。
  - **应用**：图内待办照 0214 写进文档（一次 commit）；脚本待办**不在窗口做**（不起脚本方案），卡上写「交给 Agent 改」。点完 `POST /api/change-plans/<id>/agent-applied {keys}`
    把勾着的全部 key（含脚本）记进 `agent_applied: {by: "desktop", keys, at}`（**不**占脚本方案的 `applied`）。`suggest_status` 回 `state: applied, by: "desktop", chosen: keys`；
    `suggest_apply` 见 `applied` 或 `agent_applied` 都回 `suggest_plan_applied`。**先落定的赢**：桌面一侧 `changeplan.settle_agent_plan` 在（进程内）锁里判「没人落定」才写；插件一侧是先查 `applied` / `agent_applied` 再 `mark_applied`，两个进程同一瞬间都点才可能都成功（极窄，已知）；
    窗口这一侧输了（`change_plan_agent_settled`，409）就撤回刚写的图内修改、卡收起。
  - **卡的外观**：同一张 `SuggestionCard`，标题行多一个「来自 Agent」；脚本分组小标题写「交给 Agent 改」；「再加一条」不出现（加的那条要 Agent 想）；停靠位置放「收起」；
    落定后图内改了几项 + 一行灰字「N 项交给 Agent 改」；撤销只撤图内那次，撤完卡收起（后端已记了这次应用，方案不能再用）。
- **已知没做**：窗口里匹配图只认 `fileId`（不核方案记的排版名，同一张图在别的排版里也会出卡）；宿主工具说明没有提示「用户可能在窗口里先点了，调 `suggest_status` 看」；窗口认不出脚本在出方案之后被改过（指纹里的脚本修订在后端算）；此时图内待办仍按当时的基线写，Agent 那边的 `suggest_apply` 才会判 `suggest_plan_stale`。
