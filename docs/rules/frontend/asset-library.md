# 素材库普通入口（2026-08-26，Compatibility Bridge Session 5）

> 原文出自 `web/AGENTS.md`「素材库普通入口（2026-08-26，Compatibility Bridge Session 5）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

素材面板分「图」（FileAsset + RuntimeFigureAsset 同一个 listbox，runtime
卡带「运行时图」badge、cache 预览、stale 角标与重跑）与「脚本」
（`ScriptLibrary`，项目内每个合理 .py 一行）两个区。普通路径必须在这里
完成；RegistryDialog 只留冲突裁决 / 手工 stem / 高级诊断。

> **T09（ADR 0116）起**：脚本行 ▶ 默认打开准备面板（后端准备会话：只读检查 → 用户确认 → 运行 → 进入编辑），不再直接试运行；
> 行上的状态一句话翻译会话 phase。下文的 `scriptRunStore` 状态机、门相位与「答完重跑」协调、修复卡 / 失败恢复 / 「复制诊断」
> 是本地开关（`lib/preparationFlag.ts`）关闭时的**旧路径**，保留一版，退出条件见 ADR 0116 §七；面板的规则在
> `readiness-and-left-shell.md`「准备面板」。

- **数据源三件套**：`scriptLibraryStore`（`/api/registry` 全视图，缓存 +
  幂等去重）、`runtimeAssetStore.assets`（`GET /api/runtime/assets`，只读
  清单 + `previewNonce` 预览换代）、`scriptRunStore`（运行状态机）。
  三者都在 `registry.changed` SSE 时重取**已经取过的**，项目切换全清。
  **三个 store 都有项目代际（epoch）**（清单本身的 `assetStore` 同样有，见下一条）：模块级 in-flight 请求活得比一次
  Zustand reset 长，`clear()` 必须换代 + 清 inflight，A 项目的响应绝不
  落进 B（Session 6 评审修复；vitest 各有作废用例看护）。`packageStore`
  按同一条纪律换代（见 `docs/rules/frontend/settings-shell-and-packages.md`）——**新写一个会在项目之间
  存活的 store 时，先回来把它加进这份名单**。`depRepairStore`（依赖修复：单包计划、联合计划、钉住的解释器、
  装包进度）同样在 `resetForNewProject()` 里 `clear()` 换代（#590）：计划 / 绑定 / 采用 / 跳过的在途响应作废，
  已落地的计划与错误清掉；**装包作业不取消**（后端 `close_project` 不碰它，结果按计划自己的项目记账），进度按
  所属项目分格（`startedPlans` + `parked`，与 `packageStore` 的作业同一形状）——B 上不显示 A 的进度、A 的终态
  副作用不在 B 上派发，切回 A 时接回进行中的进度或交出切走期间的结局。受管环境重建的进度 id **每次一个**、由前端在发请求
  之前生成（`newRebuildProgressId()`，与后端 `REBUILD_PROGRESS_ID_RE` 是同源对，#606），所以重建**只在同一项目里单飞**
  （`rebuilding` 按项目记，`clear()` 不动）；POST 失败先拿同一个 id 问 `GET /api/engine/dependency/state` 实况，在跑就接回来。
  `envStore` 同样有项目代际（`resetProject()` 换代）：`refresh` / `setPython` / `setProjectPython` /
  `setWorkdirMode` / `revertAdoptedEnvironment` 在 await 之后、**写状态的那一侧**判代际——调用方在拿到结果之后
  再判已经晚了（写在返回之前就发生了，#605 评审）。看护 `store/projectSwitchDepRepair.test.ts`。
  `aiStore`（改图助手的对话）同样在 `resetForNewProject()` 里 `clear()` 换代（#589）：会话记下所属项目
  （`project`），发起 / 撤销 / 中止都钉在它上面；`start` / `revert` / `cancel` 在 await 之后判代际，
  撤销的后果（标脏、提示）由 `revertSession` 在 `revert()` 回 `true` 之后才写；`ai.*` 事件带发起项目的
  `pj`，而 `ai.done` 的副作用只在 `finish()` 认出是本标签页此刻持有的会话时才做（老后端不带 pj，项目判据
  拦不住它）；助手面板以 `generation`
  为 key 重挂，任务历史视图钉在打开它的项目上。**后端任务不取消**——照样改完、记进 A 的历史（按项目存），
  切回 A 在历史里看得到；首选 Agent / 作用范围 / 模型 / `caps` 是本机偏好，不清。看护
  `store/projectSwitchAi.test.ts`、`components/ai/assistantProjectScope.test.tsx`。
- **`assetStore` 的清单纪律**（`/api/panels`：面板、`unsupported`、目录；本条是全文，
  `project-document-and-autosave.md` 只引用）：**请求序号**挡旧响应覆盖新响应（不是"谁最后返回"）；
  **发请求那一刻的 pj** 挡串项目（`null` 与具体 id 是两个取值）；**项目代际**——`resetForNewProject()`
  里 `clear()` 清空并换代，发请求时记下的代际对不上就丢（#577：切项目时 B 的清单挂起或失败都不许显示
  A 的卡片与「无法使用」清单；A → B → A 时 A 第一次发出的迟到响应 pj 对得上，只有代际挡得住）；
  **同一项目**刷新失败不清空 `panels` / `byId` / `unsupported`（#561）；同项目的在途请求被合并、在途期间
  的调用共用一次补问，`force: true` 永远另起一次（手动刷新不许被在途请求吞掉）。`recentlyUsed` 是按
  文件 id 记的本机偏好，不清。看护 `store/assetStore.test.ts`、`store/projectSwitchAssets.test.ts`。
- **`scriptRunStore` 的四条纪律**（vitest 看护）：同脚本防并发（busy 即
  no-op，后端另有 409）；cancel 走后端取消端点（置标志 + 硬杀 worker），
  行内状态等**原请求**以 `execution_cancelled` 落地——绝不「界面装停了、
  脚本还在跑」；每次 run 换代，迟到响应丢弃；`clear()` 升 epoch，在途
  响应绝不落进新项目。错误存**原始 code + params**，显示那一刻才翻
  （i18n 纪律）。SSE `probe.started` 驱动 starting_runtime → running。
- **脚本缺包时就地给修复卡片**（2026-09-28）：试运行以 `missing_dependency` 收场且带
  `dependency_repair`（后端 `probe._error_from_worker` 挂的同一份 `deprepair.offer()`）时，脚本行上渲染
  **同一张** `DependencyRepairCard`（同一个 `depRepairStore`、同一次授权）——新脚本的图还没上画布时右栏
  不会有卡片，这是走到安装的唯一入口。修复状态全局一份，属于别的脚本时这一行不显示；装好后
  `depRepairStore.onProgress` 按进度里的 `script` 重跑那一行（只在它仍停在 `missing_dependency` 时）。
  **从脚本行发起时，那份 offer 随作业一起收放**（#729）：卡片带 `fromScriptRow`，发起时把 offer 交进
  `depRepairStore.scriptOffer`，`clear()` 把它与重试上下文收进所属项目那格、切回来放回；脚本行在自己的运行里
  没有 offer（`scriptRunStore` 换项目被刻意清空）时用它——A → B → A 之后进度 / 取消 / 重试仍在那一行，B 上没有。
  装好（切走期间，或切回之后）同样重跑那一行、收起卡片，走同一条 `rerunScriptAfterRepair`：这类修复里那一行
  **没有运行记录**也算仍停在缺包上；收起后作业不再收放，再切走切回不重复触发，B 上不触发。
  看护 `ScriptLibrary.test.tsx`「脚本行发起的修复切项目再切回」、`projectSwitchDepRepair.test.ts`。
- **一键修复**（2026-09-29，用户：「太冗杂，坚决不能出现，一定要让用户一句话就能读懂」）：修复卡起点**默认可见的只有**
  一句话（「这个脚本还缺 openpyxl，点一下自动装好。」；要下载私有 Python 时大小用括号放进同一句——「…自动装好（需下载约
  25 MB）。」，`oneClickSentence()` 一处拼，安装包自带 / 已缓存 / 已就位时不提下载）+ 一个主按钮「一键修复」+ 折叠标题「详情」（`data-repair-advanced`）——
  没有标题、解释段落、列表、第二个并列按钮。这台电脑上已有装好那个包的环境时改用它（不装、不下载），否则装进为项目准备的受管
  环境。「详情」首段（`data-repair-primary-facts`）只说**主按钮真正要做的事**——改用已有 Python 时说改用哪一个、不装不下，
  不许说「将安装 / 需下载」；其余的路（含受管环境的要素）放在「其他方式（备选）」一节（`data-repair-alternatives`）。
  受管环境那一段最多三条、各说一件事（要装什么 / 要下载什么多大——联网只在这一条里说，`downloadFact()` /
  不改动什么），修复卡与跑前授权框同一套；下载大小（`data-one-click-cost`，来源只按 `privatePythonOrigin()` 判：`bundled` / `cached` 不提下载，字段缺失按
  `cached` 推、再缺按下载）、环境说明、需求串、其余目标、被跳过的系统解释器、「换一个 Python」（`OtherPython`，兜底出口必须
  **始终在**）与「选择渲染环境」全在「详情」里。两样都没有时才把各条路摊开；真的无路可走时同样一句话（`repairManagedUnavailable`，版本范围与「指定已有 Python」在「详情」里）。
  一次授权的比对（`planMatchesDisclosure`）连私有 Python 的**来源**一起比：说的是自带 / 已缓存、计划换成另一个就停在
  确认页（只有「说要下载、计划变成不下 / 下得更少」放行）。
  受管目标 `available: null`（后端还在探基础解释器、offer 上没挂私有 Python）时卡片先 `previewManaged()` 形成一份计划**只读它的
  要素**（只在受管目标**会是主按钮**时读——已有解释器是主按钮时不读，免得「检查中」把它禁用住；计划不装东西；按脚本 + 模块分格，两张卡同时预读互不覆盖，装好后清空重读），按它授权——否则计划多出一段下载，`planMatchesDisclosure` 不符，要多点一次确认页。进行中同样**一行**
  （`RepairProgressLine`：「正在安装 openpyxl…（3/4）」，下载那一段「正在下载 Python… 12 / 25 MB」+ 细进度条 +「取消」；
  四个阶段的完整列表 `RepairStageList` 与 pip 日志在折叠的「详情」里），私有 Python 的子阶段按 #743 `privatepython.STAGE_*` 闭集各说各的（校验 / 解压 / 试启动），自带 / 已缓存的归档不说「下载」
  与字节数，认不出的子阶段降级成「正在准备 Python…」；乐观的第一条进度（SSE 还没来）就带上目标 / 脚本 / 包名，之后缺目标的快照沿用上一条（阶段数按目标定）；
  字节数读 `result.download`、**只在 state 仍是
  `downloading_python` 时读**（后端的 result 沿用上一条）；换用 PyPI 镜像（进度记录顶层的 `pypi_mirror`，后端 #743 `deprepair._note_mirror` 给出；没有这个键时一个字都不说）
  只在「详情」里说（修复卡与跑前授权框的进行中都一样：#743 的联合准备两条路与单包修复同一个字段）。一键修复改用了电脑上已有的环境、或清掉了全局固定之后，停在缺这个包上的脚本行（这一行与同样缺它的）
  立刻重跑（`rerunAfterEnvironmentChange`，与装好之后同一件事）；发请求那一刻记项目代际，回来时已切项目的话这些
  副作用一个都不在新项目上做，结局按所属项目停放（`pendingEnvChanges`）、切回来 `clear()` 取出来落地。结局**带标签**，绝不把成败当成未知：失败的回来
  只在卡片上说那一句、不重跑；成功的回来先按此刻重新读一次环境、核实确实生效了才重跑，没生效按失败处理（用户自己的
  Python 以脚本目录为 cwd，在没换成的解释器下重跑并不无害）。所以这两条路直接问后端拿结局，不经 `envStore` 那两个
  换代后成败都回 null 的方法。判「作用于谁」的规则只有
  一条、一个辅助函数 `deliverToOwner`：结果回来时所属项目就是当前项目（含 A → B → A 已经切回）就立即执行，不是就停放——
  判的是所属项目，不是代际（代际变了、所属项目却开着时停放下去就没人再取）；安装完成（`onProgress`）/ 迟到的失败（`lateFailure`）/ 重建实况都经它。模块级的停放槽（`startedPlans` / `parkedRetry` /
  `pendingReruns`）活得比 zustand reset 长，测试在 `beforeEach` 里调 `__resetDepRepairParkingForTests()`，用例互不串（「恢复自动检测」因此挪进
  store：`clearPinnedInterpreter`）。看护 `projectSwitchDepRepair.test.ts`「环境改动的回调按项目代际判」。跑前授权框
  （`DependencyPrepareDialog`）同一套：没有装齐的用户环境、默认目标是受管环境时，标题就是那一句（干净机器上什么包都
  不缺时换成「需要先准备运行环境」那一句；私有 Python 的披露只跟**此刻选中的**目标走，选了项目 venv 就不提），底部只有「稍后」「一键修复」，
  其余（含「不准备，直接运行」）进「详情」；默认目标是项目 venv（会改用户环境）时目标单选留在外面，每个选项的说明压成一句
  短语，其余照样进「详情」。同一个包缺在几个脚本上**只挂一张卡**
  （修复进行中的那一行优先），装好后同样缺它的几行一起重跑（`rerunSameModule`；发起时把那几行记进 `scriptOffer.peers` 随作业收放，
  切走期间装好、切回来运行记录已清空时按名单补跑）；有修复 offer 的行不叠 `FailureRecovery`。
  缺包的脚本在素材库里单独归「需要修复」组、排最前；超时与一般失败仍在「可能需要原环境」。
  **故障同样一句**：一键修复卡的失败结局、起点上的失败（形成计划 / 改用环境被拒）、跑前授权框的失败，默认只露原因 + 下一步
  合成的那一句（`repairShortMessage` → `repairErrorShort.<code>`，没登记的码与只有后端原文的落到 `repairErrorShortGeneric`）
  和一个主按钮；`repairError.*` 的完整说明、错误码、日志、blocked 的逐条理由只在「详情」里。`repairError.*` 本身不改——
  设置 › 包管理页（`repairCodeMessage`）照旧整句在用。
  这一组新文案一律经 `{{product}}` 插值、不手写产品名（仓库里没有扫语言包的品牌门禁，存量文案里还有手写的；
  `i18n/prBrand.test.ts` 先钉住一键修复碰过的键）。只准备环境（联合计划 `requirements` 为空）时「详情」不说「装包需要联网」，
  只说准备哪份 Python（`downloadFact(pp, { packages: false })`）。
  看护：「默认可见」按**可见元素**判（`test/visibleBlocks.ts`：收起的 details 里只有 summary 可见；主区域按**语种自己的**句末标点数句子 ≤ 1（`sentenceCount`：中文「。！？」、英文后跟空白或到结尾的「. ! ?」，
  没有规则的语种直接抛错；`oneSentence.test.tsx` 把每种状态 × 每个语种都跑一遍，故障按每个 `repairError` 码 × 每个语种各一例，并核对
  `repairErrorShort` 与 `repairError` 的码集合相等）、
  数看得见的主按钮 = 1；e2e 用 `checkVisibility`），不按子串——`DependencyRepairCard.test.tsx`「一键修复」、`DependencyPrepareDialog.test.tsx`、`ScriptLibrary.test.tsx`、
  `e2e/dependency-one-click.spec.ts`（`@feature:assets.dependency-one-click-repair`）。
- **试运行撞上起会话之前的门不是失败**（Windows 真机验收 main 493a1310）：`/api/registry/probe` 以
  `dependency_preparation_required` / `workdir_confirmation_required` 回来时（200 或带 code 的非 200 都认），
  `scriptRunStore` 的相位是 `needs_preparation` / `needs_workdir`，**不进「可能需要原环境」**（那组只有换环境 /
  复制诊断，只有脚本、画布上还没图的新用户就再也走不到安装），载荷经 `handOffProbeGate` 交给 `envStore`——与渲染那条路
  同一个 `DependencyPrepareDialog` / `WorkdirConfirmDialog`、同一次作答（发请求那一刻的项目挡串项目）。「稍后」之后脚本行上
  是与画布错误块同一颗再打开的按钮（`GateReopen`）。有了答案由作答的一方调 `rerunGated` 重跑停在门上的那一行：准备成功
  （`depRepairStore.onProgress` 的 joint done，按进度里的 `script`）、明确跳过、改用用户环境按脚本；运行目录由
  `WorkdirConfirmDialog` 选定后重跑停在这一相位上的全部（项目级；放在组件里是因为 envStore → scriptRunStore 会扩大既有 import 环；
  作答期间换过项目就不重跑——`setWorkdirMode` 的换代作废与成功同形，按发起时的**代际**判：`scriptRunEpoch()`，每次换项目 +1，
  A → B → A 项目 id 相同但代际已变）。
  接入中心的试运行（开关关闭时；T09b）**委派这一台状态机**（`scriptRunStore.run`），那一行读它的状态显示（`RegistryDialog.probeNoteOf`）：
  门、再打开的按钮、`rerunGated` 重跑都只有这一份，同一脚本天然不并发，换项目时随 `clear()` 整个清空（含 A → B → A）——原先接入中心
  自己那份记账（`onGateResolved` / `whenScriptIdle` / 门载荷的代际）随之删除。抛出来的错误（门以 409 回来）只经 `run` 里那一处
  `probeErrorOf` 解析。**新写一个调试运行端点的入口，先认这两个 code。**
  看护 `scriptRunStore.test.ts`「试运行撞上起会话之前的门」、`ScriptLibrary.test.tsx` 同名 describe、
  `RegistryDialog.test.tsx`、`e2e/asset-library.spec.ts`「试运行撞上依赖门」。
  **直接弹一键修复框（用户 2026-10-03，取代 #760 的行内例外）**：素材库脚本行与编辑图的入口撞上依赖门，都交给同一个
  `DependencyPrepareDialog`；默认一句话 + 「一键修复」，进度 / 取消 / 重试留在弹窗里。`ScriptPreparation` 只留再打开的按钮，
  「稍后」之后能重新打开同一份授权；不在脚本行再执行一套准备流程。授权对账：绑定回来的计划超出 offer 的清单不执行、重新披露；
  改用装齐的用户环境成功后经 `settleEnvChange` 与 `rerunGated` 重跑发起的行。按发起时的试运行记录记联合准备归属（`jointScript`），
  随切项目停放；切回恢复同一弹窗，切走期间装好只补跑发起的脚本一次。看护 `ScriptPreparationRow.test.tsx`、
  `ScriptLibrary.test.tsx` 同名 describe、`e2e/asset-library.spec.ts`、
  `e2e/dependency-one-click.spec.ts`。
- **运行/取消是同一个按钮**（busy 态翻转）：取消后焦点天然留在原脚本行，
  不做焦点搬运。状态行 aria-live=polite，只随相位变化播报。
- **多 Figure 结果进 Dialog**（自带 focus trap），每张各有「添加到画布」，
  `dropped_figures` 如实显示——绝不只显示第一张。
- **safe 失败的恢复路径**：文案解释「可能依赖原来的 Python 环境 / cwd /
  参数」（与下面两个入口、诊断一起收在每一行默认折叠的「详情」里，2026-09-29），真实入口只有「选择渲染环境」（就地打开 `EngineEnvironmentDialog`，
  开关 `uiStore.engineEnvOpen`，正文就是那一份 `EngineEnvironmentCard`——**不深链设置页**：
  卡片在设置里住在「诊断」页、环境正常时还折叠在技术详情里，此前深链的「关于」段早已
  没有它，用户被扔进一页毫不相干的内容）与「复制诊断」；**native 未落地前不渲染任何
  可点但无功能的按钮**（PR 2 合并后再升级为实际入口）。
- **素材卡的两个动作各说各的后果（UI 审计 T06）**：「编辑原图」（Enter / 双击）
  进快速编辑——图还不在文档里时它**必然**把图加进来（ADR 0028：快速编辑的
  对象只能是文档里的面板对象），这一步由 `openFastEdit` 用状态提示
  `fastEdit.addedForEdit` 说出口，一条历史、撤销即移除；图已在文档里时零文档
  改动。**这句话分两份，别合成一份**：可见的常驻说明在 `FastEditBar`
  （`data-fast-edit-added-note`，**不带 role**），读屏播报在 `CanvasStage` 的
  `data-fast-edit-live`（`role="status"`，sr-only）。播报那份挂在 `CanvasStage`
  而不是浮动条里，是因为浮动条本身就是进快速编辑那一刻才挂上的——活动区跟它一起
  插进 DOM 的话，插进来时就已经填好了字，而读屏播报的是**区内内容的变化**，
  「带着内容整个插入」的活动区各家 AT 行为不一致、很可能一声不吭，等于用一个
  role 承诺了一件它并没有做的事。区先在、内容后变，由
  `fastEditStage.test.tsx`「播报区常驻」那条钉着。「添加到画布」（Shift+Enter / 就近入口 / 看大图弹窗）一律走
  `addFigureToLayout`（文件与 runtime 同一条路，已在文档里只聚焦）——加图分层链的第 1 层；
  哪个入口走哪一层以 `canvas-objects-and-workspace.md` 的「加图是一条分层链」为准。列表下方
  `SelectedAssetActions` 给一对 listbox 之外的真按钮——option 里不许嵌可 Tab
  控件。**不许再用一个中性的「打开」承载加入文档。**
  搜索词与筛选在 `store/assetBrowseStore`（组件会被卸载；换项目 `clear()`，
  不落 localStorage）。同脚本 + 同 stem 的 runtime 条目紧跟它的磁盘图并写
  「同源：X.pdf」（`runtimeSiblingOf`）；`assets.changed` 时 runtime 清单也
  重取（只重取已取过的）——「哪张图有原件」正是那一刻变的。
- **runtime 卡片没有假值**：没跑过的没有尺寸、没有描述符，主动作是
  「运行并发现图」；「添加到画布」只走描述符（`addRuntimePanelToCanvas`，分层链第 2 层），
  绝不解析 id、绝不指望磁盘路径。运行时图的写回区
  （`PanelSection.RuntimeSourceArea`）**显示原因**（没有原始图文件，
  导出会创建新文件）而不是无声隐藏；按钮缺席只是礼貌，硬拒绝在后端。
- **交接定位认 runtime 素材（Session 6）**：`applyOpenRequest` 找不到磁盘
  面板时按 stem 查 `GET /api/runtime/assets`（只读），有描述符就
  经 `openFastEdit` 打开（它在 workspace 里新建，见加图分层链）；没有描述符**不造假面板**，引导去脚本区运行。多
  Figure 交接（`?pick=<脚本>` / `tavotto:open` 事件的 `pick`）打开
  `FigurePickerDialog`——每张可见、各自可加、**绝不静默选第一张**；条目
  从 assetStore + runtimeAssetStore 现算（磁盘图走 `addPanelToCanvas`、runtime 走
  描述符 `addRuntimePanelToCanvas`，已有就只选中），没跑出预览的条目不渲染假按钮。看护
  `openRequest.test.ts` / `FigurePickerDialog.test.tsx`。
- **TIFF 素材（issue #534）**：`/api/file` 回原字节，Chromium / WebView2 画不出 TIFF——`panelSrc`
  的位图分支按**浏览器能力的允许清单**（`BROWSER_RASTER_EXTS` = png / jpg / jpeg）判，清单之外一律走
  `/api/render` 分档渲染（后端转 PNG）；新加一种素材格式时默认落在这条安全的路上。这张表不是素材清单，
  不与 `project_refresh.IMG_EXT` 成对。卡片格式名按扩展名说实话（`formatOf`：JPEG / TIFF 不再叫 PNG）。
  范围之外的 TIFF（`/api/panels` 的 `unsupported`）不给卡片、也不静默消失：图区末尾逐个一行
  （`UnsupportedAssets`，`data-asset-unsupported`），跟着同一组搜索 / 来源 / 类型筛选走，文案取
  `errors:backend.<code>`；它非空时不出空态 / 「没有匹配」（空态与清单不同时出现）；刷新失败时与素材卡
  一样保留上一份照常显示；来源筛选的选项由 `assetFolders()` 并上它的目录。看护 `lib/panelSrc.test.ts`、`AssetBrowser.tiff.test.tsx`。
- **脚本 `input()` 的作答（ADR 0099）**：`scriptInputStore` 有项目代际（`clear()` 换代；后端那一问不取消，切回来经
  `loadAnswers()` 的 `pending` 接回对话框）；`ScriptInputDialog` 是闸（`blockDismiss`，出口只有提交 / 结束输入 / 停止脚本），
  脚本的提示与输出片段只当纯文本；事件流以 `/api/events?answers=1` 声明「能答题」（同一道会话认证），收到 `stream.hello` 与每次认领新项目时（`onCurrentProjectChange`，与事件过滤同一时刻，不等 `project` 赋值）经 `announce()` 报在看哪个项目（后端按项目认答题方）；改 / 删答案的结果换了项目就是 `stale`，调用方不许接着重跑；「记住的输入」入口只在
  这个脚本真有答案时出现在脚本行上；zustand 选择器的空值用模块级常量（每次新建 `[]` = React #185 无限重渲染）。
  作答内容在 `ScriptInputForm`（`useScriptInputAnswer` + 字段 + 按钮），原对话框与准备面板共用；同一问只有一个展示面：
  面板挂载时 `claimPresentation`、卸载时 `releasePresentation`，对话框只在没有展示面认领时出现（关面板 = 换展示，不停脚本）。
  口令（`secret` / getpass）用密码框、交出去就清空、不进 store；`suggestion` 只说给人看，不预填（ADR 0099 §九）。看护：
  `scriptInputStore.test.tsx`、`ScriptInputDialog.test.tsx`、`ScriptAnswersDialog.test.tsx`、`e2e/script-input.spec.ts`。
- 看护：`scriptRunStore.test.ts` / `ScriptLibrary.test.tsx` /
  `AssetBrowser.runtime.test.tsx` / `runtimeSourceSection.test.tsx` / `projectSwitchDepRepair.test.ts` /
  `projectSwitchAssets.test.ts` +
  `e2e/asset-library.spec.ts`（show-only 项目真实后端黄金路径 + 窄视口 +
  保存/关闭/重开/重放/预检/导出完整链 + 多 Figure 选择器）。

## 运行参数（T03）

「全部脚本」里每个可试运行的脚本行有一个默认收起的「运行参数」折叠段（`ScriptArgvEditor`）：**一项一个 token**，可增 / 删 / 上移，
空串是合法 token，永远不 `split(' ')` / `join(' ')`；勾"敏感"时输入框变密码框，值只在内存里（`scriptArgvStore` 不持久化，换项目
`scriptRunStore.clear()` 一并清掉）。试运行在**开始那一刻**取草稿拷贝（`probeWithDraft`）交给 `probeScript`，空草稿时请求体里没有
`argv` 字段、调用形状与此前相同。后端错误码 `invalid_argv` / `run_config_*` 走 `errors:backend.*`。界面不另判"能不能跑"。
**先问再发**（T10，ADR 0117）：非空 argv 的试运行与准备会话发出之前，`lib/api.ts` 的 `requireEngineFeature` 先读 `/api/version` 的
`features`（一个标签页一次），引擎没宣告 `script-argv` 就以 `engine_capability_missing` 拒绝、运行请求一次都不发（旧端点会静默丢掉
argv、无参数运行）；无参数时不问。看护 `lib/engineFeatures.test.ts`。
看护：`store/scriptArgv.test.ts`、`store/scriptRunArgv.test.ts`、`components/ScriptArgvEditor.test.tsx`。

**参数表单与粘贴命令（T07）**：展开时取一次静态 schema（`fetchScriptArguments`，后端只读源码）；有参数就在列表上方多一个表单
（`ScriptArgsForm`）。表单**不另存意图**：每次从草稿 token 读出字段视图（`readTokens`），编辑 = 只改这个参数自己那几个 token
（`applyEdit`，`scriptArgvStore.setTokens`），认不出的 token 原样留在原位并列出来；默认值只当占位符，删光输入 = 空字符串、× = 不提供，
BooleanOptional 三态；编辑被拒时输入框保留原值与焦点、token 不变、说出原因；缺必填 / 互斥冲突只提示不拦。粘贴命令（`lib/argvPaste.ts`）
只接受一条简单 POSIX sh 调用（拒绝管道 / 重定向 / 变量 / 通配符 / 多行 / 前置赋值，失败提示改用逐项填写），被接受的分词与
`shlex.split` 逐项相同。两份 golden：`tests/golden/script_args_form_vectors.json`、`argv_paste_vectors.json`。
看护：`lib/scriptArgsForm.golden.test.ts`、`lib/argvPaste.golden.test.ts`、`components/ScriptArgsForm.test.tsx`。

