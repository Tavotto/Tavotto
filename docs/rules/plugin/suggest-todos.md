# 建议待办工具（ADR 0214 第 3 期）

> 2026-11-28 新写（第 3 期 3a：插件工具 + 宿主自己问用户），速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**。
> 看护用例：`tests/test_mcp_suggest.py`（出方案只算不写、逐条结果、只写勾着的、过期 / 别的会话拒绝、旧引擎不出现）、
> `tests/test_changeplan*.py`（引擎流水线，桌面端点同一份）、`tests/test_mcp_server.py::test_tools_list_shape`、`tests/test_mcp_error_next.py`。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 140
改到: `tavotto_suggest_*`
主题: 建议待办 → `suggest-todos.md`
必守要点: 与桌面同一流水线
-->

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
- **3b 未做**：Tavotto 窗口开着这张图时在助手面板出同一张待办卡、用户在桌面点的结果经 `suggest_status` 告诉宿主——另一个 PR（ADR 0214 修订 2026-11-28）。
