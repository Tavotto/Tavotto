# Codex / AI 刷新、入口整合与遥测映射（2026-09-02，ADR 0041）

> 原文出自 `web/AGENTS.md`「Codex / AI 刷新、入口整合与遥测映射（2026-09-02，ADR 0041）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0041-codex-ai-refresh-and-telemetry-integration.md`，改动前先读。

* **项目文件变化只走统一刷新**：前端唯一的刷新入口是 `liveSync.refreshProjectNow()`（调
  `/api/project/refresh`），命令面板 `refresh-project`、顶栏「更多」、素材库按钮都调它。**不新增
  第二套 watcher，不在前端猜 readiness**——接入状态只读 `projectReadinessStore`（后端事实），
  打开它走 `openCenter({ source })` / `focusPanel(id, source)`，`source` 是闭集
  `banner | panel | quickedit | palette`，新入口必须带上（不带 = 不记遥测，不是默认值）。
* **`ai.done` 不 markStale**：文件变了的话后端在它之前已经作废 worker、跑过刷新、发过
  `panel.file_changed`（`reason: 'ai'`），stale 只由那条事件置一次；`reason === 'ai'` 时不弹
  「脚本已更新」，一次修改只留 `ai.done` 那条提示。`ev.refresh.status === 'failed'` 要单独说
  （`ai:status.aiChangedRefreshFailed`），不把代码改动伪装成全部成功。
* **onboarding 活动信号与遥测分离**：`lib/activity.ts` 不出网；活动 → 遥测的映射**只有**
  `lib/activityTelemetry.ts` 一处、只映射浮动栏的排列 / 成组 / 取消成组（`fromContextBar()`
  作用域内发出的才算），其余 kind 逐种反证为不映射。遥测永远不反过来驱动界面。
* **新遥测事件只捕获成功边界**：`document_saved` 在 `scheduleDiskWrite` 的三个结局；
  `recovery_action` 在恢复 / 保留主版本的动作里；`tutorial_step_completed` 只在
  `completeStep(id, 'done')`（跳过不记）；`tutorial_started` 只在真的开始 / 重新开始。所有
  字段先进后端 `EVENTS` 表（两侧对拍），前端不发表里没有的键。
* **命令面板的 id 是稳定标识**（e2e 与资源都认它）：`refresh-project / readiness / tutorial-start /
  tutorial-resume / tutorial-reset / hints-reset / shortcut-help`；项目命令按
  `projectStore.phase === 'open'` 出现，embedded / playground 整组不出现。中英文 label + keywords
  两份都要有（`CommandPalette.test.tsx` 比两份资源的 id 集合）。**高亮行按身份记不按位置记**
  （光标 `{ id, query }`，查询一变回首行；查询按词切、每个词都要命中、顺序不限）——宪法第二十四节。
* **UI 文案用「可编辑的图 / 仅版面」（2026-09-26 前叫「仅排版」，「排版」改作名词后改名）**，不把 parameterizable 翻成「可参数化」；「已登记的源脚本」
  这个说法**留在注册表对话框自己身上**——2026-09-06 审计 T40 之后，设置页那个入口改成结果式的
  「可编辑来源：n 个脚本」+「管理来源…」：登记规则是对话框自己的事，入口只报结果。
* 看护：`lib/activityTelemetry.test.ts` / `components/CommandPalette.test.tsx` /
  `store/projectReadinessStore.test.ts`「打开接入中心的遥测」/ `hooks/useServerEvents.test.ts`
  「AI 修改之后」。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 刷新入口只有 `refreshProjectNow()`
- `ai.done` 不 markStale、`refresh.status === 'failed'` 单独说
- 活动 → 遥测映射只在一处、只映射浮动栏排列
- 命令 id 稳定、高亮按身份记
