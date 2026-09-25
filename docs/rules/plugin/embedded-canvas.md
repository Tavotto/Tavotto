# 内嵌画布

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **内嵌画布 = Tavotto 前端那一份代码**（`CanvasStage`/`OverlaySvg`/`interactions.ts`/
  `ElementInspector`/既有 stores），拖拽、命中、吸附、undo、patch 状态**没有第二份实现**。
  唯一改动是 `web/src/lib/engineTransport.ts`：一个**可选覆盖**（HTTP ↔ `tools/call`）。
  它**不 import `lib/api`**——搬默认实现进去会与 api 绕成环（TDZ），而且既有单测大量
  `vi.mock('@/lib/api')` 打桩 `engineRender`，实测会炸 7 个文件。
- UI 只挂在 `tavotto_open_figure` / `tavotto_apply_overrides` 上（其余工具的产出是文字与
  文件，挂 UI 只会让画布不停重建）；CSP 的 `connectDomains` **是空的**（sidecar 端口动态，
  写不进白名单，这也是必须走 `tools/call` 的原因）；**绝不用「开浏览器」冒充内嵌画布**；
  iframe 的 `localStorage`/`widgetState` **不存业务数据**。
- 画布产物 `codex-plugin/mcp/widget/canvas.html` 是**构建物，不进 git**（ADR 0043）：本地
  `python scripts/build_mcp_widget.py` 构建到原位置试用（`--check` 三档只给本地用）；CI 从
  本次 checkout 现建并验证完整插件（`scripts/plugin_stage.py`，`plugin-candidate` job），
  用户装到的来自发行分支 `plugin-stable`（release.yml 在固定发行 SHA 上构建、验证、发布）。
  **不许把它加回索引**（`scripts/ci/check_generated_untracked.py` 在 PR 与 main 落地审计上看着）。
  三个路径分清：源码 `codex-plugin/`（无画布）/ staging / 已装副本，见 `docs/ci/plugin-stable-channel.md`。
  **装工作副本**：`codex plugin marketplace add <仓库>/codex-plugin` + `codex plugin add tavotto@tavotto-dev`
  （`codex-plugin/.agents/plugins/marketplace.json` 是开发用的本地市场，staging 不带它）。
- **协议绿灯不能冒充 Codex Desktop iframe 证据**。真实验收必须按
  `docs/acceptance/codex-desktop-canvas.md`：新任务、真实 capability JSON、先取消
  证明 fail-closed、再人工批准精确路径、同一任务里出现并实际交互画布，且保留截图与
  工具 metadata；缺一项就继续写“未验证”。
- **内嵌 Codex 画布不发遥测**（widget 打包同一份前端代码，但没人调
  `setTelemetryEnabled`）——这是决定，不是疏漏。
