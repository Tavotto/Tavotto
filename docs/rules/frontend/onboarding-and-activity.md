# 交互式 Onboarding 与本地活动信号（2026-09-02，ADR 0040）

> 原文出自 `web/AGENTS.md`「交互式 Onboarding 与本地活动信号（2026-09-02，ADR 0040）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0040-onboarding-coachmarks-and-hints.md`，改动前先读。

* **本地活动信号 `lib/activity.ts` 是闭集**：`ACTIVITY_KINDS` 列 kind、`ACTIVITY_PAYLOAD_KEYS` 列允许的
  字段（只有枚举与计数：**没有 id / gid / name / path / text / value**）。新增一种信号 = 加 union 分支 +
  进两张表 + `activity.test.ts` 加样本；**一个 action 一个发射点、只在成功之后发**，组件里不补第二枪。
  它不是遥测：不出网、不落盘；Prompt 22 映射遥测只许从这张表挑，且必须经同意态与后端白名单。
* **教程状态只在 `store/onboardingStore.ts`**（`tavotto.onboarding`）：状态机 / 步骤 id / 提示记录 /
  教程项目与文档 id；不记 DOM、文案、路径、对象 id。改步骤内容升 `ONBOARDING_FLOW_VERSION`，
  **不改 step id**（`lib/onboarding/stepIds.ts` 是持久化格式的一部分）。关掉 coachmark 是 `paused`
  （`pausedBy: 'user'`），切走项目是 `paused`（`'system'`），绝不伪装 `completed`。
* **四个入口共用 `lib/onboarding/tutorial.ts`**（`tutorialEntry / runTutorialEntry / resetTutorial /
  resetHints`）：项目选择器、顶栏更多、命令面板、设置常规。**不许在入口里判状态**。打开教程走
  `projectStore.adoptOpenedProject(status, { prepareDocument })`——与打开任何项目同一条认领链路；
  教程画布的 documentId **必须**是 `metadata.document_id`（T-106）；同一项目里再点入口不走认领。
  **手里就是教程画布时，打开 / 重置都先 `suspendAutosaveFor` 再调 API**：重置会清磁盘槽位、打开可能
  因资源升级换副本（新项目 id → 走认领，认领第一句就是把当前文档冲刷落盘），不挂起的话旧布局会在
  同一个教程 documentId 下落回槽位、装回来的还是它；换到教程画布时 `switchDocument` 自动恢复，
  没换文档 / 没做成就 `resumeAutosave` 接回。
* **完成条件在 `lib/onboarding/steps.ts`**：状态可说清的读 store，说不清的读 `StepSignals`（引擎按
  信号累计、按 `consumes` 消费）。教程要编辑的是带 `spec_issue` 的那张（T-108）。**不用 DOM 文案 /
  CSS class 猜状态；不为教程复制任何 action。**
* **前置状态先验，缺了给真实行动（2026-09-06，审计 T36；flow v2）**：每步可有 `precondition(ctx)`，
  不满足时卡片说清缺什么（`dialogs:onboarding.precondition.<reason>`）、主按钮只调稳定动作
  （`openFastEdit` / `addFigureToLayout` / `returnToLayout` / `setSelectedGid`），「跳过此步」照旧；
  「正在等待目标出现」只在前置满足之后的 `WAIT_MS` 窗口出现，计时从那一刻起算。`add_to_layout`
  按 `missingTutorialPanels()` 出变体——文档里只剩一张时说「还缺哪张」，不许说「两张都在」。
  **完成与跳过分两本账**：`completedSteps` 是走过的（推进状态机用），`skippedSteps` 是其中跳过的
  子集；结束页按 `tallyOutcomes()` 分「教程完成 / 完成 n 步跳过 m 步 / 跳过了全部」三种措辞，
  不用完成式总结一份跳完的教程。
* **锚点是稳定的 `data-*`**：`data-onboarding-anchor="export | export-scope | add-to-layout | to-layout
  | tutorial-entry | help-tutorial | settings-tutorial"`、`data-object-id`、`data-card`、`data-rail`、
  `data-issue-row[data-issue-rule][data-issue-object]`、`data-multi-selection-context-bar`、
  `data-element-svg`（+ manifest bbox）、`data-world-transform`（`CanvasStage` 唯一的世界变换节点，
  教程不用它，e2e 靠它量视口有没有被还原——`e2e/nav-audit.spec.ts`，2026-09-06 审计 T01）、
  `data-status-live`、`data-fast-edit-live`、`data-dialog[="<名字>"]`、`data-dialog-close`、
  `data-overlay-svg`、`data-inspector-panel`、`data-prop`。
  **aria-label / 文案 / class / ARIA role 都不能当选择器。**
  改了这些属性要同步 `steps.ts` 与 `e2e/tutorial.spec.ts`（`data-world-transform` 同步的是
  `nav-audit.spec.ts`）。
* **指代一个具体单例，就不许用「取第一个匹配」（issue #307）**：`querySelector` / `.first()`
  配一个不唯一的语义（role / class / 裸标签 / 本地化文案），赌的是「以后不会有人在它前面插一个
  同类」——那个赌注在写下的当天是对的，一直对到某个无关的改动插进来为止（PR #296 已经输过一次）。
  扫集合（`querySelectorAll`）、同质列表里「随便哪一行」（`[role="treeitem"]).first()`）、
  谓词（`el.closest('[role=dialog]')`）、把一次扫描限定进某个容器（`AxeBuilder.include`）不在此列。
  这一族的锚点：
  - **`data-dialog` = 共用对话框外壳**（`components/ui/Dialog.tsx` 的 `RD.Content`），
    `anchor` prop 给它一个名字（`data-dialog="export"` = 导出对话框）。`role="dialog"` 在这个
    应用里有**五个**产出点（本组件、`canvas/QuickEdit.tsx`、`onboarding/Coachmark.tsx`、
    `components/VersionDialog.tsx`、`playground/PlaygroundApp.tsx`），所以
    `querySelector('[role=dialog]')` 拿到的是「排在最前的那个」——`e2e/tutorial.spec.ts` 被迫
    写成 `:not([data-onboarding-coachmark])` 就是撞过的证据。
  - **`data-dialog-close` = 对话框右上角的关闭按钮**（同一个文件的 `RD.Close`）。
    它的 `aria-label` 是 `actions.close` 的译文，换语言就选不中。
  - **`data-overlay-svg` = 画布覆盖层 SVG**（`canvas/OverlaySvg.tsx`）：选中描示、参考线、
    手柄都画在它里面。以前拿那个「不吃指针事件」的工具类 `pointer-events-none` 当选择器
    ——CSS class 是排版手段不是标识。本文件从 2026-09-14 起可以写出完整类名（扫描面收到
    `web/src`，见 `docs/rules/frontend/verification.md`）；`OverlaySvg.tsx` 里那段注释**仍然**拆着写——它是 `.tsx`、
    在面内，实测把 `web/src` 里 40 处真实用法全中和掉之后，一句注释就能把规则吊在产物里。
  - **`data-inspector-panel` = 右侧检查器栏**（`components/inspector/Inspector.tsx` 的 `aside`）：
    左抽屉（`data-left-drawer`）、版本面板、快捷任务卡也都是 `aside`。
  - **`data-prop` = 属性字段行**（`inspector/ElementInspector.tsx` 的 `FieldBlock`、
    `inspector/controls/TypographyControls.tsx` 的 `Anchor`，值一律从
    `lib/typography.propertyPathOf()` 出）：e2e 要走到字号 / 线宽输入框时用
    `[data-prop="fontsize"] input` / `[data-prop="linewidth"] input`，不是
    `input[aria-label="字号"]`。同一条规则在 `docs/rules/frontend/typography-capability-layer.md` 里已经写着，
    这里只是把 e2e 侧的落点点名。
* **`data-status-live` = 状态播报区**：`components/StatusBar.tsx` 的 `StatusToasts` 里那块常驻
  `aria-live="polite"` 的 sr-only 区，内容是 `uiStore.setStatus` 的 info 档（error 档在它旁边的
  `role="alert"` 里）。问「**应用刚说了什么**」的一律认它——`e2e/twin-axes-pick.spec.ts`（⌥ 轮换
  播报 `status.elementCycled`）与 `e2e/cross-tab-paste.spec.ts`（已复制 / 已粘贴）靠它。
  **`role="status"` 不是唯一的**：快速编辑那行常驻说明、素材库、导出面板、问题面板、设置页、
  onboarding 层…… 十几处都在产出，所以 `[role="status"]` / `getByRole('status')` 拿到的是
  「文档里排在最前的那个」，不是播报区。T06 给上下文条加的那行 `fastEdit.addedForEdit`
  排在播报区**前面**，就是这么把 twin-axes-pick 的判据主语从「刚播报了什么」换成「那行说明写着
  什么」的——产品行为完好，红的是判据。scope 在自己渲染根里的单测（`ProjectReadinessBanner.test.tsx`
  的 `host.querySelector`）可以继续用 role：那里主语唯一。
  看护：`lib/liveRegionSelector.test.ts` 用 AST 扫 `e2e/` 的字符串字面量，任何
  `[role=status|alert|log|marquee|timer]` 当选择器都点名——**活动区天生是复数且与 DOM
  顺序相关**，「the status region」这个说法本身不成立，所以这条规则是绝对的、豁免为零。
  `role=dialog` 那一族不进这条门禁：它还有「把 axe 扫描收进对话框」这类正当的限定用法，
  判不死，硬加只会逼出一张越来越长的豁免表。
  jsdom 单测**整片**不进（那 5 处此刻都对：三处 scope 在自己的渲染根里，两处只挂一个设置页
  组件），但门禁另钉一条**真的变了的交集**：`CanvasStage` 自带一块常驻活动区
  （`data-fast-edit-live`），所以挂载它的单测里「一次只挂一个组件、`document` 就是渲染根」
  **不再成立**——那些文件不许用活动区的 role 当选择器。今天这个交集是空的（3 个文件挂载
  `CanvasStage`，0 个这么写），两条规则的豁免都是零。
* **`data-fast-edit-live` = 「这张图刚为编辑加进文档」的读屏播报**：挂在 `CanvasStage`（两种模式
  都常驻），**不在上下文条里**——后者是进快速编辑那一刻才挂上的，活动区跟它一起插进来时就已经
  填好了字，那种「带着内容整个插入」的活动区各家 AT 很可能一声不吭。可见的那一份在
  `WorkspaceContextBar`，锚点 `data-fast-edit-added-note`，**不带 role**：一条提示不播两遍。
* **coachmark 没有遮罩、不改偏好**：`reveal()` 露出折叠侧栏直接 `uiStore.setState`（不经 `setLeftTab`
  的 persist）；画布对象被平移出 `[data-canvas-stage]` 时只调 `viewportStore.revealRect`。锚点在
  `[role=dialog]` 里就 portal 进那个节点（模态层外面点不到）。Esc 只在焦点落在卡片里时暂停。
* 看护：`onboardingStore.test.ts` / `activity.test.ts` / `selectionStore.test.ts` /
  `lib/onboarding/{position,flow,tutorial,hints}.test.ts` / `components/onboarding/onboardingLayer.test.tsx` /
  `e2e/tutorial.spec.ts`（四条：完整走完 / 刷新恢复 + Esc + 更多菜单 + axe / 重新开始 / 切项目暂停继续）。
  jsdom 里所有盒子都是 0×0：层的用例要给锚点 `getBoundingClientRect` 假矩形；用假计时器时 flush 要
  `advanceTimersByTimeAsync`，别等真的 setTimeout。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 活动信号闭集、一个 action 一个发射点
- step id 是持久化格式
- 四个入口共用 `tutorial.ts`
- 锚点是稳定 `data-*`（清单在细则里）
- `data-status-live` 是唯一的播报区、`[role=status]` 绝不当选择器
