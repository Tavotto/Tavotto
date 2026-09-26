# 多选浮动栏与共享排列参照（2026-09-02，ADR 0036）

> 原文出自 `web/AGENTS.md`「多选浮动栏与共享排列参照（2026-09-02，ADR 0036）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0036-multi-selection-context-bar.md`，改动前先读。

* **一个外壳五种目标**：`canvas/context-bar/ContextBar.tsx` 解析目标（正在裁剪的
  面板 / 单个图内元素 / 两个以上图内元素（ADR 0089）/ 单个画布对象 / 两个以上画布对象），出现与让位、落位
  （`position.ts` 纯函数）、Esc、拖动隐藏、portal 都在外壳；四种内容各一个文件。
  对外仍是 `ContextBar()`。裁剪的两条判据**不是同一个**：让位看
  `cropTargetId` 有没有值，出裁剪条看它指不指得到一个真面板。
* **进裁剪一律走 `actions.beginCrop`**（属性页 / 浮动条 / 右键菜单 / 面板上按
  Enter 四个入口），它把进裁剪那一刻的取景窗与包围盒记进 `uiStore.cropBaseline`；
  `cancelCrop` 还原到**那一刻**（不是还原到「从没裁剪过」——那是 `resetPanelCrop`），
  `finishCrop` 只退出。直接 `setCropTarget(id)` 不记基线，取消会降级成单纯退出：
  **宁可少还原，也不拿一份过期快照去改文档**（审计 T26）。
* **多选栏不是第二套排列系统**：按钮只发意图，落地走 `store/actions.alignSelectedTo`
  / `groupSelected` / `ungroupSelected`——与 `ArrangeSection` 同一个函数、同一条历史
  标签。按钮表在 `inspector/arrangeButtons.ts` **一份**，别在组件里再抄图标与顺序。
* **参照只有一份**：`store/arrangeStore`（UI 会话状态：不进文档、不进撤销、不
  persist、切文档不重置）。要读「此刻按什么对齐」就订阅它，不要再造模块级变量。
  **控件也只留一处**（审计 T29）：右栏属性页停靠着时（`ContextBar` 的
  `multiBarDocked`，与文字栏的 `textBarCompact` 同一条 `inspectorDocked` 判据）
  浮动栏收成「计数 + 六向对齐 + 成组 + 更多」，参照 / 分布 / 等宽等高的控件让给
  `ArrangeSection`；当前参照仍由计数上的 title 与每颗对齐按钮的提示报出来。
* **主选 = `selection.ids` 末位**。OverlaySvg 里主选轮廓 2 px 并挂
  `data-primary-selection`，联合框挂 `data-multi-selection-bounds`——浮动栏、e2e 与
  后续 coachmark 都锚在这两个节点上，别改名。
* **落位不查 DOM**：联合选区经 `position.selectionScreenRect`（与 OverlaySvg 的
  `toScreen` 同一份换算 + 视口原点）算窗口坐标。宽窄档两道判据：静态阈值
  `FULL_BAR_MIN_WIDTH` + 量出来放不下就降级；工具条盒子必须 `w-max`，否则
  `fixed` 盒子被可用宽度压扁、量到的不是自然宽度。
* **锁定对象不动但算进参照框**：`alignSelectedTo` 与拖动同用 `movableTargets`；
  对齐 / 成组 / 取消成组执行前 `finishActiveGesture()`。
* **本地活动信号** `lib/activity.ts`（`tavotto:activity`）：闭集 kind + 枚举 + 计数，
  无用户内容；核心 action 不 import onboarding；它不是遥测，别往 `telemetry` 里接。
* **Tooltip 不吃指针**（含 Radix 定位外壳，`index.css` 那条 `:has([role='tooltip'])`）：
  聚焦触发的气泡会停在下一排按钮上，真浏览器里点上去什么都不发生。
* **`workspace:contextBar.*` 是这条浮动栏的命名空间**（`context-bar/text.ts` 的
  `qb()`）。画布上方那条工作区上下文栏（审计 T01）用的是 `workspace:stage.*`，
  别把两组混进同一段——`pnpm i18n:check` 把 `qb()` 这种短助手当成动态前缀，
  **删掉它的 key 是绿的**，界面上才会显出原始 key（2026-09-06 实际发生过）。
* **多选时的快速排版（ADR 0089）**：图内多选（两个及以上图内元素）出 `ElementMultiBar`——
  对齐落地是 `alignSelectedPanelElements`（与 ElementInspector 对齐区同一个函数），排版只在
  同一文字家族时给、走 `useFigureTypography` 的批量适配器（与属性页 `styleBatch` 同一条判据）；
  「给什么」只由 `elementMultiPlan()` 判，`ContextBar` 与组件读同一份。画布多选全是文字时多选栏
  接一行排版（`useCanvasTypography`）。浮动栏上的文字控件只有 `context-bar/textQuick.tsx` 一份，
  图内 / 画布、单选 / 多选都画它；取色一轮一条历史；停靠时按 `textBarCompact` 缩减。
  「按下即藏」同时听 `pointerup` 与 `pointercancel`。
* 看护：`canvas/context-bar/position.test.ts` / `multiSelectionBar.test.tsx` /
  `canvas/context-bar/elementMultiBar.test.tsx` / `e2e/multi-selection-bar.spec.ts` /
  `canvas/primarySelection.test.tsx` / `store/alignSelectedTo.test.ts` /
  `store/arrangeStore.test.ts` / `canvas/contextBar.test.tsx`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 进裁剪一律 `beginCrop`（记基线）
- 浮动栏只发意图，落地走 `alignSelectedTo` 等同一函数
- 参照只有 `arrangeStore`
- `qb()` 的 key 删掉 i18n 检查是绿的
