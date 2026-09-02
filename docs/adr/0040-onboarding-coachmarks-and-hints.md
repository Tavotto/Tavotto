# ADR 0040：交互式 Onboarding——真实完成条件、本地 Activity Bus、coachmark 与一次性提示

日期：2026-09-02 · 状态：已接受 · 关联：ADR 0028（两条工作流）/ 0030（统一检查与定位）/
0031（导出管线）/ 0032（属性能力层）/ 0036（多选浮动栏）/ 0039（离线教程项目）

## 背景

ADR 0039 交付了离线教程项目（包内资源、数据目录里的版本化副本、`GET/POST /api/tutorial*`），
但界面上没有任何入口，也没有 onboarding：新用户打开一份自己的图库，第一印象仍然是「这张图
不能编辑」。

本仓库此前的「引导」只有两种形态：文案解释（问号、tooltip）和空状态。没有任何一处知道
「用户刚刚做了什么」——遥测知道，但它经过同意态、后端白名单、出网，**不能**也不该被
界面反过来消费。

Prompt 21 要的是一条克制的新手路径：让第一次使用的科研用户**通过真实动作**走一遍两条
核心工作流（快速编辑一张图 → 改文字 → 从问题定位 → 原图导出；加入画布 → 多选对齐 →
画布导出），而不是看一组「下一步」幻灯片。

## 决定

### 1. 本地 Activity Bus 扩成教程的唯一信号源；它不是遥测

Session 17 已经有 `lib/activity.ts`（`tavotto:activity` 自定义事件，三种排列信号）。本轮把
它扩成 18 种 kind 的闭集（`ACTIVITY_KINDS`），每种由**一个** action 在**成功之后**发一声：
`project.opened` / `workspace.mode_changed` / `figure.opened_fast_edit` /
`figure.element_edit_entered` / `selection.changed` / `element.selection_changed` /
`element.property_changed` / `history.pushed` / `problems.opened` / `problem.focused` /
`export.dialog_opened` / `export.scope_changed` / `figure.added_to_layout` / `menu.opened` /
`document.saved`，加上原有的三条 `selection.aligned|grouped|ungrouped`。

payload 只有闭集枚举与计数（`ACTIVITY_PAYLOAD_KEYS` 白名单：`mode / ref / count /
tutorial / outcome / prop / label / ok / field / scope / menu`）。**没有对象 id、gid、
文件名、路径、文字、值**。「是哪一个对象」由订阅方在收到信号那一刻自己去问 store。
`activity.test.ts` 用一张 `Record<ActivityKind, 样本>` 反证：新增 kind 不加样本编译红，
样本里出现白名单外的键用例红。

**为什么不直接让教程订阅 store**：store 变化说不出「用户改了字号」和「撤销把字号改回去」
的区别，也说不出「定位成功」和「用户自己点了对象」的区别；只有 action 出口知道这件事
成功了没有。而**只**用信号也不行：「已经在图内编辑态」「两张图都在画布上」这类状态
用户可能在教程到那一步之前就做完了——所以完成条件是**状态可说清的读 store、说不清的
读信号**（§4）。

### 2. `onboardingStore`：版本化、本机、与文档分离、可关闭持久化

一格 `localStorage['tavotto.onboarding']`：`schemaVersion` / `flowVersion` / `status`
（`not_started | active | paused | completed | skipped`）/ `currentStep` / `completedSteps` /
`hintSeen` / `startedAt` / `completedAt` / `tutorialProjectId` / `tutorialDocumentId` /
`pausedBy`（`user | system`）。

* **不记** DOM 节点、翻译后的字符串、路径、对象 id / gid——教程目标按 `tutorial_meta`
  的 `file` / role 现找（§4）。
* `schemaVersion` 说格式，`flowVersion` 说步骤内容。格式认不出 → 安全默认；步骤内容升版
  → 进行中 / 暂停的回到**第一个未完成的步骤**（新步骤会被提供），已完成 / 已跳过的
  **不被重新打扰**、完成历史原样保留。逐字段校验：坏一个字段丢一个，不整份作废。
* `pause('user')`（关掉 coachmark / Esc）与 `pause('system')`（切走了项目）分开记：
  只有系统暂停的会在回到教程时自动继续。**关掉 coachmark 是 paused，绝不伪装 completed。**
* 「重新开始教程」（后端换副本 + 本机进度从头）、「重置 onboarding」（清这一格）、
  「重置提示」（只清 `hintSeen`）是三件事。教程重开**不**抹提示记录——用户已经知道的事
  不必再被提示一遍。
* `configureOnboardingPersistence(adapter | null)`：embedded 宿主可换后端或关掉（纯内存）。
  核心状态机不分叉。

### 3. 教程入口只有一份逻辑（`lib/onboarding/tutorial.ts`）

项目选择器「用示例了解 Tavotto」、顶栏「更多」、命令面板（`Start / Resume / Reset tutorial`）、
设置「常规」四个入口都调同一组动作，自己不判状态：

```text
tutorialEntry()    → 'start' | 'resume' | 'restart'（按 onboardingStore.status）
runTutorialEntry() → startTutorial()：POST /api/tutorial/open → 认领项目 → 装教程画布 → 开始 / 继续
resetTutorial()    → 确认（点名列出用户另存的画布） → POST /api/tutorial/reset → 忘掉本机那格 autosave → 从头
resetHints()
```

三条硬约束原样继承 ADR 0039：只从 `/api/tutorial/*` 的响应与元数据拿一切（T-105）；教程
画布的 documentId **必须**是 `metadata.document_id`（T-106）；打开不执行脚本（T-107）。

两个接线细节值得写下来：

* `projectStore.adoptOpenedProject(status, { prepareDocument })`：从 `open()` 里抽出来的
  「后端已经打开了一个项目，前端认领并换代」——教程的 `/api/tutorial/open` 不走
  `/api/projects/open`，但之后要做的事**完全相同**，不许抄一遍。`prepareDocument` 在
  「前端状态已换代、但 `phase` 还没变成 `open`」的空档里装教程画布：工作台一挂载就会
  `restoreSession()`，那一刻 `tavotto.currentDoc` 记的必须已经是教程画布，否则空白文档会被
  再装回去（实测撞到的时序）。
* 已经在教程项目里再点入口**不再走认领**（那会把正在排的版换成空白文档），只在文档不是
  教程画布时换过去——与 `lib/openRequest` 同一条纪律。
* 从项目菜单切回教程项目、或重启后进来时，项目打开链路给的是空白文档；引擎收到
  `project.opened{tutorial:true}` 且 onboarding 仍在这份教程里（进行中 / 系统暂停）时，
  经 `ensureTutorialDocument()` 把教程画布装回来（元数据不在就先 `GET /api/tutorial`）。

### 4. 步骤表：完成条件来自真实状态与真实信号（`lib/onboarding/steps.ts`）

十个 id（`stepIds.ts`，持久化格式的一部分，改内容升 `flowVersion` 不改 id）：

| 步骤 | 完成条件 | coachmark 挂哪 |
| --- | --- | --- |
| `welcome` | 手动：点「开始」 | 居中 |
| `open_fast_edit` | `ui.elementPanelId === 要编辑那张图.id`（只有 `enterElementEdit` 能产生这个状态） | 素材抽屉开着 → `[data-card=<file>]`；否则画布上 `[data-object-id]`；再否则 `[data-rail=assets]` |
| `select_text` | 主选 gid 的 role ∈ 文字类 ∩ `editable_roles` | manifest bbox 映射到 `[data-element-svg]` 的那一块（title 优先） |
| `change_typography` | 信号 `element.property_changed`（prop ∈ figureText 排版路径）**且** `history.pushed`（事务落进撤销栈） | `[data-prop="fontsize"]`；右栏没开就临时露出（不写偏好） |
| `locate_problem` | 信号 `problem.focused{ok}` 且主选是教程面板；**替代出口**「问题已解决，继续」= 那张图渲染过 + 检查跑过 + 教程面板上零问题 | `[data-rail=problems]` → 抽屉开了之后 `[data-issue-row][data-issue-rule=…][data-issue-object=…]` |
| `export_original` | 面板开着时 `export.scope_changed{original}` **然后面板关掉** | `[data-onboarding-anchor=export]` → 面板开着时 `…=export-scope`（portal 进对话框） |
| `add_to_layout` | 两张教程图都在文档里 **且** 回到画布模式 | 快速编辑里 `…=add-to-layout`；画布模式下直接完成 |
| `multi_select_align` | 信号 `selection.aligned` 那一刻选区里 ≥2 张教程图 | 先指另一张图；选够两张后 `[data-multi-selection-context-bar]` |
| `export_canvas` | 同 `export_original`，范围是 `canvas` | 同上 |
| `done` | 手动：「继续探索」/「打开自己的项目」 | 居中 |

* **要编辑的那张图 = 带 `spec_issue` 的第二张（Fig2）**，不是元数据里的第一张。原因是
  实测：问题面板的图内问题从**渲染后的 manifest** 算（`validationStore` 读 `render.byKey`），
  没进过编辑的图不会有图内问题；教程在 Fig1 上改字号之后去「问题」定位，面板里没有
  Fig2 的 7 pt——除非再让用户打开第二张图。把 Step 1–4 收敛到同一张图，四步是一条线。
* 信号按步骤**消费**（`StepDef.consumes`）：一条重放的 `history.pushed` 只能完成它该完成
  的那一步；不在教程里（切走了项目 / 文档）发生的信号不累计。
* 状态可说清的条件天然覆盖「用户提前做完了」：状态在那儿，步骤一到就完成
  （`flow.test`：`welcome` 之前就进了图内编辑 → 点「开始」后 `open_fast_edit` 立刻完成）。
* 「Step 3 把问题修掉了」的情形：Step 3 改的是标题、问题在那条 7 pt 说明上，正常不会；
  用户真去改了 7 pt（或按了「修复」）就走替代出口——**不造假问题**。
* `add_to_layout` 在画布模式下直接完成：教程画布本来就摆好两张（ADR 0039 为多选对齐准备
  的）。教的是「加入画布 / 回到版面」这一个动作，不是「再摆一张」。

### 5. coachmark 层：没有遮罩、目标缺失可恢复、模态对话框里进同一层

`components/onboarding/OnboardingLayer.tsx` + `Coachmark.tsx`：

* **没有全屏遮罩**。用户随时能点真实界面；coachmark 是一张 300px 的非模态卡片
  （`role=dialog aria-modal=false`）+ 一个 `pointer-events:none` 的高亮环。
* 锚点用稳定的 `data-*` 选择器或 manifest bbox 找，**不用** aria-label / 文案 / class。
  找不到先等 `WAIT_MS`（属性页重排、抽屉展开）再说「找不到目标」并给返回 / 跳过；
  目标在视口外先 `scrollIntoView`；藏在折叠侧栏里由步骤的 `reveal()` 临时露出——直接
  `uiStore.setState`，**不经 `setLeftTab`（那条会 persist 偏好）**。
* 落位是纯函数（`position.ts`：下 → 上 → 右 → 左 → 夹进视口），jsdom 里能用数字验。
* 锚点在 Radix 模态对话框里（导出面板）时 portal **进对话框的内容节点**、绝对定位：
  模态层会把外面的指针事件与焦点都挡掉，只有进同一层才点得到、Tab 得到。这也是
  Step 5 / 8 的完成条件要求「面板关掉」的原因——下一步的目标在面板后面。
* Esc（焦点在 coachmark 里）= 暂停；关闭键 = 暂停。**不劫持全局 Esc**：应用里 Esc 是
  「逐层退出」（图内元素 → 编辑态 → 清空选区），教程自己就要用到它。
* Tab 顺序返回 → 跳过 → 主动作 → 关闭（关闭画在右上角、放在 DOM 末尾）；换步骤时
  `aria-live` 读一遍「第几步 · 标题 · 正文」；reduced motion 下不带位移过渡、高亮环
  不带进场动画（`prefersReducedMotion()`，与 `lib/motion` 同一判据）。
* 文案最多标题 + 两句 + 进度 + 按钮；变体（多选前 / 后、面板开 / 关）按上下文换，
  key 在 `dialogs:onboarding.steps.<id>[.<variant>]`。

### 6. 一次性情境提示（`lib/onboarding/hints.ts`）

五类、每类一次、教程进行中不出、可关、到时自己走、记在 `onboardingStore.hintSeen`：
第一次单选可编辑面板（画布模式）/ 第一次单选仅排版面板 / 第一次进快速编辑 / 第一次多选 /
第一次出现问题（盯 `validationStore`，不是动作）。不依赖遥测同意。

### 7. 隐私边界与 Prompt 22 的映射

信号本身一个字节不出网。Prompt 22 若要映射遥测，只许从 `ACTIVITY_KINDS` 挑粗粒度事件、
只带 `ACTIVITY_PAYLOAD_KEYS` 里的字段，且必须经过同意态与后端 `EVENTS` 白名单
（`test_client_and_proxy_contracts_match`）。教程进度（第几步、完成没有）可以映射成
`tutorial_step_completed{step}` 这类枚举事件；`hintSeen`、教程项目 id、文档 id 不映射。

## 不做的事

* 不为教程复制任何 production action；不在普通项目里创建教程对象；不自动运行脚本、
  不自动安装包；不用 DOM 文案 / CSS class 猜状态。
* 不做每个按钮的常驻 tooltip、不做营销文案。
* embedded（MCP 画布 / playground）默认不出 onboarding：它们不渲染 `App`，入口在项目
  选择器与工作台里；Tutorial API 不可达时入口整行不出现（`no_api`）。宿主要用只能显式
  `startTutorial()` 并按需 `configureOnboardingPersistence()`。

## 后果

* 每个核心 action 多了一行 `emitActivity`（18 处）；`selectionStore` / `uiStore` 的
  `set` 多了一次「真的变了才发」的比较。都是同步、本地、吞异常的。
* `onboardingStore` 是第三份持久化的本机偏好（`tavotto.ui` / `tavotto.locale` 之外）。
* 教程画布的对象 id 不被记录：重置 / 重建之后按 `file` 现找，连不错。
