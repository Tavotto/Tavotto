# 交互式 Onboarding 与本地活动信号（2026-09-02，ADR 0040）

> 原文出自 `web/AGENTS.md`「交互式 Onboarding 与本地活动信号（2026-09-02，ADR 0040）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0040-onboarding-coachmarks-and-hints.md`（Activity Bus / 状态 / 提示）与
`docs/adr/0136-native-three-step-onboarding.md`（三步原生教程，取代 0040 的 coachmark 教程），改动前先读。

* **本地活动信号 `lib/activity.ts` 是闭集**：`ACTIVITY_KINDS` 列 kind、`ACTIVITY_PAYLOAD_KEYS` 列允许的
  字段（只有枚举与计数：**没有 id / gid / name / path / text / value**）。新增一种信号 = 加 union 分支 +
  进两张表 + `activity.test.ts` 加样本；**一个 action 一个发射点、只在成功之后发**，组件里不补第二枪。
  它不是遥测：不出网、不落盘；Prompt 22 映射遥测只许从这张表挑，且必须经同意态与后端白名单。
* **教程状态只在 `store/onboardingStore.ts`**（`tavotto.onboarding`）：状态机 / 步骤 id / 提示记录 /
  欢迎页出现过没有 / 演示图上的两处改动（图的 pt，不是 DOM 坐标）；不记 DOM、文案、路径、对象 id。
  只有 `surface`（教程层此刻显示哪一屏）不持久化。改步骤内容升 `ONBOARDING_FLOW_VERSION`，
  **不改 step id**（`lib/onboarding/stepIds.ts` 是持久化格式的一部分）。删步骤要在 `migratePersisted` 里把停在它上面的进行中 / 暂停用户落到新位置，并同步遥测枚举（前端 / `engine/telemetry.py` / 代理契约三处同源，代理把旧值登记进 `LEGACY_ENUM_VALUES` 继续收）；2026-09-30 取消 `welcome`、2026-10-19 换成三步教程（flow v3，ADR 0136）即照此办理——**升级后旧版留下的已完成 / 已跳过 / 进行中 / 暂停 / 未开始一律迁回新手态（`not_started`、`welcomeSeenAt` 清空、`hintSeen` 保留），把新教程自动看一次，只一次**（2026-10-21 用户裁决，ADR 0136 修订，覆盖此前「已完成 / 已跳过的不被打扰」；迁移纪元为 flow v4——被 v3 代码读过并写回的旧 blob 也要迁）；之后完成 / 跳过照常是终态。跳过是 `skipped`，绝不伪装 `completed`。**Windows 卸载后重装同样迁回新手态**（2026-10-21，ADR 0136 修订二）：NSIS 卸载器在非 `/UPDATE` 时写 `%APPDATA%\Tavotto\reinstall-marker`，启动后 `autoOpen.ts` 问 `GET /api/onboarding/reinstall`，为真则 `onboardingStore.resetToNewcomer()` 再 `POST …/ack` 删记号（读不删、处理完才 ack）；应用内更新不写记号、macOS / Linux 无此行为；看护 `reinstallMarker.test.tsx`、`tests/test_reinstall_marker.py`、`tests/test_nsis_template.py`。
* **入口共用 `lib/onboarding/tutorial.ts`**（`tutorialEntry / runTutorialEntry / markDemoStep / finishTutorial /
  skipTutorial / restartTutorial / leaveFromWelcome / resetHints`）：主页、全部项目、顶栏更多、命令面板、设置常规、画布空态。
  **不许在入口里判状态**。三步教程（ADR 0136）**不打开项目、不发请求**：入口只改 onboardingStore 并打开教程层。
  示例项目（ADR 0039）是另一件事：`openSampleProject()` / `resetTutorial()`（「重置示例项目」）走
  `projectStore.adoptOpenedProject(status, { prepareDocument })`——与打开任何项目同一条认领链路；
  示例画布的 documentId **必须**是 `metadata.document_id`（T-106）；同一项目里再开不走认领；**onboarding 一个字段都不碰**。
  **手里就是示例画布时，打开 / 重置都先 `suspendAutosaveFor` 再调 API**：重置会清磁盘槽位、打开可能
  因资源升级换副本（新项目 id → 走认领，认领第一句就是把当前文档冲刷落盘），不挂起的话旧布局会在
  同一个 documentId 下落回槽位、装回来的还是它；换到示例画布时 `switchDocument` 自动恢复，
  没换文档 / 没做成就 `resumeAutosave` 接回。
* **主页两版只按 onboarding 状态分（2026-09-26）**：`lib/onboarding/tutorial.homeVariant(status)`
  是唯一判据——`completed` / `skipped` → 老手版，`not_started` / `active` / `paused` → 新手版；
  **不另设标志、渲染主页不写任何东西**（派生出来的版式不许回写偏好）。新手版 = 一句大问题 + 拖放区 + 黑色主按钮「导入我的脚本」，教程入口
  （`runTutorialEntry('picker')`，锚点 `tutorial-entry`，暂停过的显示「继续」）降为次按钮「用示例学一遍（带引导）」
  （三步说明卡与提示条已删，拖放区标题本身就是说明；两版共用一个 `DropZone`）。**2026-10-07 设计刷新（审计 §4.2）**：
  拖放区静态时是一张普通 `ui/Card`、只有带文件拖进页面才是接收态（`ui/dropZone`）；两颗 CTA 是 `size="lg"`、一屏一颗
  填色主按钮；老手版标题下是一句叙事句，句中两枚 24px chip（`data-home-chip="recent"` 直接打开最近一个仍存在的项目、
  `"script"` = 导入），整句经 `Trans` 进翻译；两版的最近项目都是 Card interactive 网格（`RecentProject` 不带画布数据，
  缩略图格是画布灰上的文件夹图标）。老手版的「使用示例
  脚本试试看」走 `openSampleProject()`：同一条认领链路打开
  示例项目，**onboarding 一个字段都不碰**、不记 `tutorial_started`——否则一次「看看示例」就把
  `completed` 改回 `active`，下次回主页又成了新手版。「重新开始教程」照旧在「全部项目」视图、帮助菜单、
  命令面板、设置里。新手版的教程入口不依赖后端的教程资源（三步教程是内置演示），总是可用；只有老手版的
  示例按钮在资源坏了（`available: false`）时多说一句「请重新安装」，正常时不说「已内置」。
* **三步教程是受控演示层，不是编辑器（2026-10-19，ADR 0136）**：图是 `lib/onboarding/demoFigure.json`
  （开发期用真 matplotlib 量 `fig1_kinetics.py` 得来，严格同源对，看护 `tests/test_tutorial_demo_figure.py`）
  画出的 SVG（`components/onboarding/DemoFigure`）；图例位移与标题字号记在 `onboardingStore.demo`，
  **不进 documentStore、不写 override、不进撤销历史、不发后端、不写文件**。演示层的用例只证明演示层，
  不替图内编辑作证。**每一步只在真实交互成功时完成**（`markDemoStep`）：卡片真的落进投放框（松手点或卡片
  中心在框里，悬停 / 计时都不算）、图例离原位 ≥ `LEGEND_MIN_MOVE_PT`、提交的字号大于进入这一步时的字号；
  「下一步」只挪指针（`onboardingStore.next` 在当前步骤没完成时是 no-op）。拖动在 pointercancel /
  lostpointercapture / 失焦 / 缩放 / 切后台 / 卸载时作废、监听随卸载摘掉；键盘（空格 / 方向键 / 回车 / Esc）
  是等价入口，**没有「直接使用示例」捷径**（第一步只教拖放）。卡片放进去之后先演 1.8 秒「渲染中」
  （`RENDER_MS`，按时间走、不读任何作业），走完才 `markDemoStep('demo_import')`；渲染途中离开不补记完成，
  减少动态效果时直接落到完成（`tween` 契约）。这段是有意的产品设计（ADR 0136 §2 修订），别当假进度删掉。
* **按任务触发的提示 `layout_page_bounds`（P04，2026-10-20）**：第一次**用户主动**从快速编辑回到排版
  （`store/workspace.returnToLayout` 发唯一一枪 `workspace.returned_to_layout`，无 payload；`exitToLayout` 的被动退出——
  换文档 / 修剪 / 定位问题 / 启动恢复——不发，所以「启动时恢复到 layout」不触发）→ 一条非模态说明「白色纸面就是导出的
  Figure 尺寸，单栏 / 双栏宽按当前规范」，**锚在右栏页签条下**（`components/inspector/PageBoundsHint`，一颗「打开画布页」）。
  规则：① 带锚点的提示（`hints.ANCHORED_HINTS`）不进通知轨，仍占 `useHintStore.current` 这一个槽位，与轨上的提示互斥不叠；
  ② 锚点判据读 store（`hints.anchorAvailable` = `uiStore.rightOpen`），不读 DOM；锚点不在（右栏收起 / 窄屏抽屉互斥收掉）
  **不出现、也不记看过**，已出的随右栏收起一并收掉（`startHintEngine` 订阅 uiStore）；③ 看过记在 `onboardingStore.hintSeen`
  （本机、跨项目、跨重启；`resetHints` 清）→ 每台机器一次，关掉后不重现；④ 教程进行中不出；⑤ 发射点在重新选中那张图**之前**，
  先到先占槽位（否则 `panel_editable` 抢走）；⑥ 悬停 / 聚焦不走表，reduced-motion 下关闭即卸载。**没有新增遥测**。
  看护：`lib/onboarding/hints.test.ts`、`components/inspector/pageBoundsHint.test.tsx`、`activity.test.ts`。
  已评估**不做**：「选中多张 → 组合成 Figure / 进入排版」——活动信号里「多选」与「导入多个文件」不可区分
  （`selection.changed` 只有 count；导入是 `figure.added_to_layout` / `project.opened`，不带来源），P04 明说导入多文件≠组合意图，
  没有干净信号就不做；多选对齐 / 分布由既有 `multi_select` 覆盖，问题定位 / 修复入口由既有 `problem_found` + 问题抽屉覆盖。
* **教程层的边界**：portal 到 `body`、`z-overlay`（对话框压在它上面），打开期间 `#root` 加 `inert`；层里的按键
  不冒泡到 window（工作台快捷键不在看不见的画布上生效）；系统拖进来的文件拒收（`dropEffect = 'none'`，不读
  `DataTransfer.files`）只说一句，主页的 `onNativeFileDrop` 在教程层开着时也不认（第二道闸）。新手态（没开始过、
  `welcomeSeenAt` 空）且启动后停在主页才自己弹欢迎页，只一次——**不再看有没有自己的项目**（升级的老用户被迁回新手态后走同一条路径）；上次停在教程中途的接着那一步。
  e2e 的 `noProject` 用例由 fixture 默认记成「欢迎页已出现过」，测教程的 spec `test.use({ firstRunWelcome: true })`。
* **锚点是稳定的 `data-*`**：`data-onboarding-anchor="export | export-scope | to-layout
  | tutorial-entry | help-tutorial | settings-tutorial"`、`data-object-id`、`data-card`、`data-rail`、
  `data-issue-row[data-issue-rule][data-issue-object]`、`data-multi-selection-context-bar`、
  `data-element-svg`（+ manifest bbox）、新手教程的 `data-native-tutorial[data-surface]` / `data-tutorial-step` /
  `data-tutorial-{begin,next,back,skip,file-card,drop-zone,use-sample,font-controls,size-increase,start-using,…}` /
  `data-demo-{figure,legend,title}`、`data-world-transform`（`CanvasStage` 唯一的世界变换节点，
  教程不用它，e2e 靠它量视口有没有被还原——`e2e/nav-audit.spec.ts`，2026-09-06 审计 T01）、
  `data-status-live`、`data-fast-edit-live`、`data-dialog[="<名字>"]`、`data-dialog-close`、
  `data-overlay-svg`、`data-inspector-panel`、`data-prop`。
  **aria-label / 文案 / class / ARIA role 都不能当选择器。**
  改了教程这些属性要同步 `components/onboarding/nativeTutorial.test.tsx` 与 `e2e/tutorial.spec.ts`（`data-world-transform` 同步的是
  `nav-audit.spec.ts`）。
* **指代一个具体单例，就不许用「取第一个匹配」（issue #307）**：`querySelector` / `.first()`
  配一个不唯一的语义（role / class / 裸标签 / 本地化文案），赌的是「以后不会有人在它前面插一个
  同类」——那个赌注在写下的当天是对的，一直对到某个无关的改动插进来为止（PR #296 已经输过一次）。
  扫集合（`querySelectorAll`）、同质列表里「随便哪一行」（`[role="treeitem"]).first()`）、
  谓词（`el.closest('[role=dialog]')`）、把一次扫描限定进某个容器（`AxeBuilder.include`）不在此列。
  这一族的锚点：
  - **`data-dialog` = 共用对话框外壳**（`components/ui/Dialog.tsx` 的 `RD.Content`），
    `anchor` prop 给它一个名字（`data-dialog="export"` = 导出对话框）。`role="dialog"` 在这个
    应用里有**五个**产出点（本组件、`canvas/QuickEdit.tsx`、`onboarding/NativeTutorial.tsx`、
    `components/VersionDialog.tsx`、`playground/PlaygroundApp.tsx`），所以
    `querySelector('[role=dialog]')` 拿到的是「排在最前的那个」——旧的 coachmark 教程 e2e 被迫
    写成 `:not([data-onboarding-coachmark])` 就是撞过的证据（新手教程认 `data-native-tutorial`）。
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
  什么」的——产品行为完好，红的是判据。scope 在自己渲染根里的单测（如 `RegistryDialog.test.tsx`
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
* 看护：`onboardingStore.test.ts` / `activity.test.ts` / `selectionStore.test.ts` /
  `lib/onboarding/{tutorial,hints,demoFigure}.test.ts` / `components/onboarding/nativeTutorial.test.tsx` /
  `components/home/HomeView.test.tsx`（两版判据、示例入口不改状态、教程入口不依赖资源）/ `e2e/home.spec.ts` /
  `e2e/tutorial.spec.ts`（从欢迎页真实拖放走完 / 教程期间没有运行·渲染·准备·打开项目的请求 / 外部 .py 拒收 /
  刷新恢复 + 键盘 / 跳过 / v2 老用户迁移 / 小窗口 + reduced-motion / axe；chromium 与 webkit 两腿）/
  `tests/test_tutorial_demo_figure.py`。jsdom 里所有盒子都是 0×0：教程层的用例要给舞台尺寸与目标的
  `getBoundingClientRect` 假值。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 活动信号闭集、一个 action 一个发射点
- step id 是持久化格式
- 四个入口共用 `tutorial.ts`
- 锚点是稳定 `data-*`（清单在细则里）
- `data-status-live` 是唯一的播报区、`[role=status]` 绝不当选择器
