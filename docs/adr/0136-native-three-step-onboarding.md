# ADR 0136：三步原生新手教程——受控演示层，不执行 Python

日期：2026-10-19 · 状态：**Proposed**（随 PR 评审）
相关：[0039 离线教程项目](0039-offline-tutorial-project.md)（示例项目照旧，教程不再借用它）、
[0040 交互式 Onboarding](0040-onboarding-coachmarks-and-hints.md)（本 ADR 取代其中的 coachmark 教程；
Activity Bus 与一次性提示不变）、[0041 遥测整合](0041-codex-ai-refresh-and-telemetry-integration.md)、
[0092 主页原生拖放](0092-home-native-file-drop.md)；规则全文 `docs/rules/frontend/onboarding-and-activity.md`。

## 问题

ADR 0040 的教程是盖在真实工作台上的八步 coachmark：打开示例项目 → 双击进图内编辑 → 选文字 →
改字号 → 问题定位 → 原图导出 → 加入画布 → 多选对齐 → 画布导出。它教得全，但第一次打开 Tavotto
的人在第二步就要**真的跑一次 Python**：图内编辑的每一次进入与改动，都由 worker 执行 `fig1_kinetics.py`
并重放 override（`engine/tutorial.py`：「脚本只在用户进入图内编辑、明确要求渲染那一刻才由 worker 跑」）。
于是新手教程变成了一次环境测试——没有可用的 Python、依赖没装好、首开准备慢，教程就卡在用户还
不知道 Tavotto 是什么的时候。

联合创始人交来一份 HTML 交互原型（欢迎页 + 三步 + 前后对比），要求把它高保真地做成标准新手教程，
并且 P0：**完成教程不需要用户的 Python、不运行脚本、不联网、不碰用户的项目**。

## 决定

### 1. 三步教程是一个受控演示层，不是编辑器

审计结论：真实编辑引擎（documentStore → override → worker 重放 → 写回事务）无法在「不执行 Python」
的约束下拖动图例、改标题字号——每一次改动都要重渲染。所以三步教程走规则允许的降级：**仅属于教程的
演示组件**，边界写死：

* 图是 `web/src/lib/onboarding/demoFigure.json` 画出的 SVG。这份几何**不手写**：开发期由
  `scripts/gen_tutorial_demo_figure.py` 用真 matplotlib 跑随包分发的 `fig1_kinetics.py`，量出图幅、
  坐标轴、刻度、两条曲线的 200 个采样点、轴标签、标题锚点与字号、图例外框与每一行；
  `tests/test_tutorial_demo_figure.py` 看护「生成物 == 现在跑脚本量出来的」（容差 0.75 pt，文字与颜色逐字）
  与「欢迎页每一行代码都逐字是脚本里的一行」。**脚本是唯一权威，JSON 是镜像**——严格同源对。
* 用户的两处改动（图例相对原位置的位移、标题字号，单位都是图的 pt）记在 `onboardingStore.demo`。
  不进 documentStore、不写 override、不进撤销历史、不发后端、不写任何文件；完成页明说
  「教程里的改动只在这个演示中，不会写进任何文件」。
* 演示层的测试（`components/onboarding/nativeTutorial.test.tsx`、`e2e/tutorial.spec.ts`）只证明演示层，
  **不拿来给图内编辑作证**，两份文件的开头都写着这句。

### 2. 流程：欢迎页 → 三步 → 完成页；唯一状态源仍是 onboardingStore

* `STEP_IDS = demo_import / demo_move_legend / demo_change_title / done`（`done` 是完成页），
  `ONBOARDING_FLOW_VERSION = 3`。欢迎页不是步骤，是 `surface: 'welcome'`（不持久化）。
* 每一步只在**真实交互成功**时完成（`markDemoStep`）：卡片真的落进投放框（松手点或卡片中心在框里；
  悬停、计时都不算）、图例离原位至少 6 pt、提交的字号大于进入这一步时的字号。「下一步」只挪指针
  （`next()` 在当前步骤没完成时是 no-op）。
* 第一步的文件卡是**教程里的逻辑对象**：没有文件选择器、不读本机文件、不调用脚本运行接口；
  放下后先演一段 1.8 秒的「渲染中」（任务标题位「正在生成示例图…」+ 白框里顶端文件行、居中转圈 +「正在渲染图表…」、
  底部按时间走的进度条，「下一步」不亮），走完才记完成、出现内置演示图（与第二步的图同一位置，不再挂文件条）。
  第三步的字号条在任务标题上方、与「返回上一步」同一行（原型如此），选中标题时标题不跳。键盘（空格拿起 / 方向键 / 回车放下 / Esc）是等价入口；**不设**
  「直接使用示例」。
  *修订（2026-10-21，联合创始人裁决）*：初版以「没有渲染环节」为由删掉了原型的渲染动画，并加了
  「直接使用示例」——两处都违背原型的设计意图，已改回。渲染动画是有意的：让用户感到「脚本 → 渲染 → 图」，
  并把整体使用切成【导入】+【调整】两段；它不执行任何脚本，演示层的约束不变。不做「直接使用示例」，
  是因为教程模拟不了「选脚本」，而那一步在真实使用里本来就好懂，所以这一步只教拖放。
* 字号区间 7–11 pt：下限是出版规范的绝对下限，上限是图幅还装得下的最大值（脚本把子图上沿固定在
  89%，标题基线离图幅上沿只有 8.6 pt；12 pt 实测已贴边）。初始值是脚本里的 9 pt（`axes.titlesize`），
  不是原型写死的 12。
* 图例被夹在坐标轴框里；脚本里 `loc="lower right"` 的图例原本就压着两条曲线的起始段——教程说的是
  「换个位置」，不宣称「修好了遮挡」（受真实数据所限，挪到哪都会碰到曲线）。

### 3. 什么时候出现、怎么退出

* 第一次打开（没开始过、`welcomeSeenAt` 为空、启动后停在主页）→ 欢迎页，只这一次。
  上次关应用时停在教程中途（`active`）→ 接着那一步。（~~最近 / 收藏里没有自己的项目~~：该条件与
  「已经在正常使用的人不打扰」已于 2026-10-21 撤销，见文末修订。）
* 入口（主页新手版、全部项目、帮助菜单、命令面板、设置、画布空态）都调 `runTutorialEntry`：进行中 /
  暂停的接着来，其余从头。不打开项目、不发请求。
* 「跳过教程」= `skipped`（不记完成、不发遥测）；欢迎页「直接进入工作台」对没开始的人只是收起；
  从第一步退回欢迎页再选它，记成跳过。完成页「开始使用 Tavotto」= `completed`，落回底下真实的主页 /
  工作台，不打开文件选择器、不跑任何脚本。

### 4. 教程层的边界

* 整层 portal 到 `body`、`z-overlay`（对话框照常压在它上面，首启的遥测同意框仍可答），打开期间 `#root`
  加 `inert`：底下的工作台点不到、Tab 不进、读屏不读。
* 教程层里的按键不冒泡到 window：工作台快捷键不会在看不见的画布上生效。
* 系统拖进来的文件在这一层拒收（`dropEffect = 'none'`，不读 `DataTransfer.files`），说一句
  「当前为示例教程，完成后可以导入自己的脚本」。macOS 壳只在页面接受了拖动时才旁听系统路径
  （ADR 0092），拒收即不发 `tavotto:file-drop`；主页的订阅另加一道闸（教程层开着就不认）。
  正常工作台的拖放路径一行没改。
* 拖动在 pointercancel / lostpointercapture / 窗口失焦 / 缩放 / 切后台 / 卸载时作废；监听随卸载摘掉。

### 5. 迁移与旧教程

* ~~v2 进行中 / 暂停的用户记成用户暂停；已完成 / 已跳过的原样，不被重新打扰。~~
  **已被文末 2026-10-21 修订取代**：旧版留下的任何状态（含看过欢迎页的「未开始」）都回到新手态。上一版的演示改动不带过来。
* 八步 coachmark 的引擎（`flow.ts` / `steps.ts` / `position.ts` / `Coachmark` / `OnboardingLayer`）删除。
  它与三步教程会是两套互相竞争的状态机，且它的每一步都依赖执行 Python；保留成「进阶教学」的维护
  代价大于收益。一次性情境提示（`hints.ts`）与 Activity Bus 原样保留。
* 示例项目（ADR 0039）不变：老手版主页「使用示例脚本试试看」照旧打开它；「重置示例项目」只换磁盘副本，
  与三步教程的进度无关。

### 6. 遥测

`tutorial_step_completed.step_id` 的客户端枚举换成四个新 id（前端 `stepIds.ts` / `engine/telemetry.py` /
代理契约三处同源）。代理继续收 flow v2 的八个旧 id（已发布的客户端还会发），登记在 `LEGACY_ENUM_VALUES`。
事件与属性集合没变，**不升同意版本**。PostHog 看板 `c2_tutorial_steps_by_step_id` 仍按 `tutorial_version = 2`
过滤；带 v3 的客户端发布后再把它切到 3（远端同步是单独一步）。

## 后果

* 新手第一次打开就能在不碰任何环境的情况下走完教程；真实脚本的兼容性问题留在用户导入自己的脚本时
  按既有链路处理，教程的成功不替任何脚本作保。
* 原型的视觉语言（纸灰底、旋转的脚本卡、虚线投放框、聚焦环 + 黑色提示签、前后对比）保留；落地时
  映射到既有 token（颜色 / 圆角 / 字体角色 / 动效时长），新增两样登记过的东西：字体角色 `type-hero`
  （只给欢迎页那一句）与图标 `ArrowRight` / `GripVertical`。原型画的 macOS 红绿灯不画（真窗口自带）。
* 演示图与真实渲染不是同一个渲染器：浏览器没有 STIX，用文档字体按 matplotlib 量出的外框居中排字。
  曲线、刻度、坐标范围、文字内容与脚本逐点 / 逐字一致；字形细节会有差异。

## 修订 2026-10-21：升级后每个人把新教程看一次（用户裁决）

起因：一份真实诊断包——10-08 用过旧版的 Windows 机器装了 0.19.0b1，`tavotto.onboarding` 里留着旧流程的
`completed` / `skipped`，`homeVariant()` 判成老手版主页、教程也不自动弹出；用户把老手版主页当成「老版本」，
根本不知道换了新手引导。用户裁决：**老用户升级后也显示一次新手教程和新手版主页**，覆盖上文「已完成 / 已跳过的
用户不被重新打扰」与「已经在正常使用的人不打扰」两条。

* **迁移**（`migratePersisted`）：`flowVersion` 低于当前的 blob——`completed`、
  `skipped`，也包括 `active` / `paused`——一律迁成新手态：`status = not_started`、`currentStep = null`
  （起点由 `start()` 设为第一步，与新用户同路径）、完成 / 跳过历史、`startedAt` / `completedAt`、演示改动清空，
  **`welcomeSeenAt` 清空**（它就是「自动弹欢迎页一次」的闸，不清则老用户永远不弹；不新增标志位）。
  `hintSeen` 保留（已经知道的事不再提示）；`tutorialProjectId` / `tutorialDocumentId` 是 v2 遗留格式字段，原样带着。
* **只发生一次**：迁移结果带当前 `flowVersion`，用户在新版里完成 / 跳过后重读即照常是老手版，不再满足迁移条件。
  迁移本身不主动写盘（首次 commit 如 `openWelcome` 时写回）；没写回前每次启动结果相同，是幂等的。
* **active / paused 改走同一条**：原先「active → paused、入口显示继续」是为了不强迫老用户重学；新裁决的精神
  正是让他们看一次新教程。且这两种状态在 v3 下进度必为空（旧步骤 id 全部认不出），保留 `paused` 只会让主页
  入口写「继续」而其实没有可继续的东西。当前版本的 active / paused 逻辑不变。
* **自动弹出**（`useAutoOpen`）：去掉「最近 / 收藏里没有自己的项目」这一条——老用户恰恰有自己的项目，不去掉
  这条迁移就是空转。仍保留：新手态、`welcomeSeenAt` 空、启动后停在主页（启动时项目就开着不弹，主页新手版与
  各入口仍在）、项目列表取到之后再判。欢迎页弹出即记 `welcomeSeenAt`，同一台机器只这一次。
* **迁移纪元升到 4**（Codex #1269）：v3 代码读到旧 blob 时会把 `flowVersion` 规范成 3 并在 `markHintSeen` 等写回，
  用 `< 3` 判就把这些人认成「当前」。教程内容不变，只把 `ONBOARDING_FLOW_VERSION` 升到 4，凡 `< 4` 一律迁回新手态一次。
  代价：在 0.19.0b1 里真走完 v3 教程的人再看一次。PostHog 看板 `tutorial_version` 过滤相应切到 4。
* 旧版的 `not_started`（例如看过欢迎页但没开始）同样清掉 `welcomeSeenAt`，否则这些人升级后永远不会自动弹；当前版本的任何状态不动。

## 修订 2026-10-21（二）：Windows 卸载后重装也回到新手态（用户裁决）

起因：用户反馈「每次卸载安装后都要被当成新手」的反面——Windows 卸载默认不删
`%LOCALAPPDATA%\com.tavotto.tavotto\EBWebView`（前端 `localStorage` 的 `tavotto.onboarding` 在里面）和
`%APPDATA%\Tavotto`，所以卸载再装仍是「教程已完成」的老手版；用户要的是：**卸载重装 = 新手，应用内更新不算**。
macOS 先不做（拖进废纸篓没有钩子）。

* **卸载记号**：NSIS `Section Uninstall` 在 `$UpdateMode <> 1`（非 `/UPDATE`）时写
  `%APPDATA%\Tavotto\reinstall-marker`（与 `engine/config.py` 的 Windows `config_dir()` 同址，内容固定一句、无标识）。
  应用内更新以 `/UPDATE` 跑安装器，`$UpdateMode = 1`，不写。写不出来静默忽略，不打断卸载。
* **后端**（`engine/reinstall_marker.py`）：`GET /api/onboarding/reinstall` 只回 `{reinstalled: bool}`（不回路径 / 内容 /
  install_id）；`POST /api/onboarding/reinstall/ack` 删记号。**读不删、前端处理完才 ack**：前端没加载或中途失败，
  记号还在，下次启动接着来。只在 Windows 生效（`os.name == "nt"`），其它平台即使有同名文件也不读不删。
* **前端**（`components/onboarding/autoOpen.ts`）：启动时问一次；`true` → `onboardingStore.resetToNewcomer()`
  （与上一节迁移同一状态：`not_started`、`welcomeSeenAt` 清空、`hintSeen` 保留，并落盘）→ 自动弹欢迎页的判定重跑
  → ack。同一页面会话只问一次；只有桌面客户端（`isDesktop()`）消费：浏览器模式（pip / 源码安装、远程窗口）不取、不 ack，免得耗掉记号；取不到（旧后端）当没有。内嵌宿主不挂 `useAutoOpen`，不受影响。
* **不碰**：EBWebView、用户的项目 / 配置 / 最近项目；只有 onboarding 一格回到新手态。勾了「删除应用数据」的卸载本来就清掉
  localStorage，记号随之多余但无害。
* **局限**：记号在卸载时写；若用户手动删安装目录而没跑卸载器（无钩子），不触发。
