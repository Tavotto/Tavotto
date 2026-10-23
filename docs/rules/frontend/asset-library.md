# 素材库普通入口（2026-08-26，Compatibility Bridge Session 5）

> 原文出自 `web/AGENTS.md`「素材库普通入口（2026-08-26，Compatibility Bridge Session 5）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`web/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`scriptRunStore.test.ts`、`ScriptLibrary.test.tsx`、`AssetBrowser.runtime.test.tsx`、`AssetBrowser.tiff.test.tsx`、`lib/panelSrc.test.ts`、`projectSwitchAssets.test.ts`、`openRequest.test.ts`、`FigurePickerDialog.test.tsx`、`e2e/asset-library.spec.ts`、`projectSwitchDepRepair.test.ts`、`projectSwitchAi.test.ts`、`rebuildProgressId.golden.test.ts`

<!-- agents-route
sheet: web/AGENTS.md
order: 220
改到: `AssetBrowser`、`ScriptLibrary`、`scriptRunStore` / `runtimeAssetStore` / `scriptLibraryStore` / `assetStore` / `depRepairStore` / `envStore` / `aiStore`、`FigurePickerDialog`
主题: 素材库普通入口 → `asset-library.md`
必守要点: 七个 store 都有项目代际；同脚本防并发；多 Figure 结果绝不只显示第一张；素材卡加图走 `addFigureToLayout`
-->

素材面板分「图」（FileAsset + RuntimeFigureAsset 同一个 listbox，runtime
卡带「运行时图」badge、cache 预览、stale 角标；重跑在卡片的右键 / ⇧F10 / ContextMenu 键菜单里的「重新运行」，不嵌在卡里；素材抽屉没有页脚，UI 2.0 第二轮 08）与「脚本」
（`ScriptLibrary`，项目内每个合理 .py 一行）两个区。普通路径必须在这里
完成；RegistryDialog 只留冲突裁决 / 手工 stem / 高级诊断。

> **T09（ADR 0116）起**：脚本行 ▶ 默认打开准备引导卡（T13b 起是画布右下的浮动卡）（后端准备会话：只读检查 → 用户确认 → 运行 → 进入编辑），不再直接试运行；
> **V2 PR4 起 ▶ 就是明确的「打开并运行」**（`open(..., { run: true })`：检查完认领后端的 `run_when_ready`，准备完成后自动进入编辑）；
> 行上的状态一句话翻译会话 phase。下文的 `scriptRunStore` 状态机、门相位与「答完重跑」协调、修复卡 / 失败恢复 / 「复制诊断」
> 是本地开关（`lib/preparationFlag.ts`）关闭时的**旧路径**，保留一版，退出条件见 ADR 0116 §七；卡片的规则在
> `readiness-and-left-shell.md`「准备引导卡」。

- **数据源三件套**：`scriptLibraryStore`（`/api/registry` 全视图，缓存 +
  同批通知合并、在途期间来的刷新共用一次补问；`loadedAt` 记录这份已落地快照的**请求发出时刻**，
  不把响应晚到当成比准备结局更新，#902）、`runtimeAssetStore.assets`（`GET /api/runtime/assets`，只读
  清单 + `previewNonce` 预览换代）、`scriptRunStore`（运行状态机）。
  三者都在 `registry.changed` SSE 时重取**已经取过的**，项目切换全清。
- **SSE 断线对账**：依赖安装 / 引擎 bootstrap 的进度只走 SSE（`/api/events` 无重放，队列满会丢），丢了终态事件卡片会永停「正在安装」。
  SSE 重连与页面恢复（`visibilitychange`）时 `reconcileInstalls()`：`depRepairStore.reconcile()` 对本标签页起过、还没见到终态的作业
  （当前卡片 + 切走项目收着的）按 `plan_id` 问一次 `fetchDependencyState`，结果走 `onProgress` 同一条规则（所属项目分格、终态副作用只在所属项目开着时派发）；
  `idle`（后端不认识）或问不到都不改任何东西，等回包期间 SSE 已推到终态则丢弃较早的快照；`envStore` 只在还显示 `installing` 时刷环境、后端说不在跑就收尾。
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
  **提交可靠性与草稿（ADR 0132）**：不变式是「同一条 AI 修改不得被无意执行两次」。`start()` 生成请求 id
  （`client_request_id`）进 `submissions`：`aiRun` 有 30 s 超时，**超时 / 断网 / 无 code 的 5xx / 读不出响应都是
  「已发出未确认」不是失败**——此态禁用一切发送与重试，只能 `queryRequest(rid)`（`GET /api/ai/requests/<id>`）；
  只有后端自己的 404 `ai_request_not_found` 才放开 `resend`，且复用**同一个** rid（后端按 rid 去重兜底）；明确的
  4xx / 带稳定 code 的 5xx 才算失败，下一次发送换新 rid。会话终态只认第一条 `ai.done`（`finalized`），早于
  `aiRun` 响应的 `ai.delta` / `ai.done` 按 rid 暂存、会话建好后回放；`cancel` 回 `ok:false` 不标 cancelled。
  失败会话 `changed=true` 的「重试」先 `askConfirm`。草稿与待确认账本**存后端**（`lib/aiDraft.ts` 只是
  `/api/ai/drafts*` 的客户端，**不碰任何浏览器存储**；ADR 0132）：主语 (项目, fileId[, rid])，防抖 600 ms，
  pagehide / 卸载 `keepalive` 冲刷，写失败在内存里留着 3 s 后重试；账本按 rid 一条（两个标签页的 rid 并存）。
  **草稿同步状态机（`components/ai/useAiDraft.ts`，所有入口共用；每一条都是「比较什么」的明确选择）**：
  1. **只有一个对账函数 `reconcile(source)`**：挂载 / 窗口焦点 / 页面回到前台 / SSE 重连（`mm:sse-open`）/ 提交被确认
     （`mm:ai-draft-changed`）全部只调它，不存在只取文本或只刷新一半的入口。它每次同时做三件事：① 待确认提交（含同脚本
     其它面板、发起面板已不在的，项目级列表按 `sameScript` 匹配）按挂载时完全相同的路径还原进 aiStore；② 本地所有
     非终态会话按后端快照收尾（`aiStore.settleRunningSessions`：`accept` 之后任务在 SSE 断线期间结束、事件流不重放，
     否则会话永远 running、脚本被锁死）；③ 草稿文本有条件地应用（见 2）。读失败 = 不知道，什么都不动。
  **按版本对账（#1115，`aiStore.reconcileChanges`，`store/aiRev.test.ts`）**：本地没有会话、账本行也已删时，断线 / A→B→A 间隙里结束的任务没有锚点。
  `doRestore`（面板挂载 / SSE 重连 / 回切项目都经它）与带 `rev` 的 `ai.done` 提示都会拉 `GET /api/ai/sessions/changes?since=<水位>`，把「终态、有脚本、本页没呈现过」的会话
  补成已完成的只读轮次，统一走 `settleSessionTo`（唯一收尾点）并给一次完成提示。水位 `revMarks` 按项目分键放**页面内存**（`clear()` 不清；A 的水位不被 B 的拉取推过去），
  `presented` 记本页呈现过的终态（`clear()` 后不重复补）；项目第一次对账只取起点、不补历史；读详情失败不越过水位；没有端点（老后端）静默跳过。事件只是提示，事实以增量为准。
  **跨刷新（D1(b)）**：水位与 `presented` 同时落 `sessionStorage`（按标签页、刷新保留，键按项目 id；读写全 try/catch，读不到 = 只取起点，宁可漏也不重复；存的水位比后端 `head` 大 = 换了库，按 `head` 重置）。**边界**：新开的标签页没有水位，不补别的页面呈现过的历史（面板历史列表可见）；水位只在对账成功后前进。
  2. **文本按写入版本应用，不按文本相等比**：本地每次改动 `seq++`、后端接受后 `acked` 追上、在途 PUT 记 `inflight`；
     只要 `seq > acked`（没发出 / 在途 / 失败待重试）读回的文本不覆盖输入框，对账期间本地又改过同样不覆盖；PUT 落地时
     本地若有更新的写入以本地为准补写一次。
  3. **写入戳（旧请求赢在服务端不可能发生）**：**戳代表内容版本的时刻，不是发送尝试的时刻**——每次本地编辑（`seq++`）
     就给这个版本分配一个戳，这一版的所有 PUT / 重试 / keepalive 都带同一个戳（发送时不重新取），旧版本无论何时重试戳都更小、
     一定被拒。**未落定的版本由发件箱持有，跨实例、跨目标都有效**（`lib/aiDraftOutbox.ts`，模块级，按 项目×文件 分槽，只留最新版本）：
     hook 只负责 `post` 版本、订阅发件箱的结果；防抖、PUT、重试（失败前 3 次 3 s 一次，之后退避加长到最多 30 s 一次；**槽不因时间放弃**，只要页面还在就一直试，页面回到可见 / `online` 时立即再试一次）、stale 处置
     都在发件箱，直到服务器接受该版本、它被同目标更新的版本取代、或被放弃才清槽。所以用户打完字、防抖没到就切面板 / 项目 / 关助手，
     卸载时那次 keepalive 即使失败，发件箱也会继续重试；退役实例不发任何写入，不会拿旧文本覆盖重新挂载后保存的新草稿。
     重新挂载同一目标时，发件箱里有未落定版本就先显示它（不是服务器上的旧值），对账也不覆盖它。pagehide / 转入后台由发件箱对
     所有未落定的槽发 keepalive。连续失败超过 1 分钟，输入框上方显示一行灰字「草稿还没保存到本机项目，正在重试」（`data-ai-draft-note=stalled`，成功后消失；与「已恢复」同一位置，不弹窗）。**已知限制**：页面真的关掉时那次 keepalive 也可能失败，内存里的发件箱随页面消失；不用
     localStorage 兜底（规格禁止），丢的只有最后一个防抖窗口内、且最后一次 keepalive 也失败的那一版。
     每次草稿 PUT（含 pagehide 的 keepalive——拿不到响应、前端排队救不了）带一个单调写入戳（墙钟微秒，页内再 +1，回拨也递增），
     后端 `ai_drafts.stamp` 只接受比已存戳更新的写入，旧戳回 `stale: true` + 服务器当前那一行的戳且不覆盖；清空 / 超限 = 删但留墓碑，
     服务端的清理（条件删除 / accept）也推进戳。**戳的下限**：每次读到服务器的戳（对账的 GET——含墓碑的行戳、PUT 的响应、
     stale 的响应）都抬高本页下限。**stale 的处置只看「这一版自己的基线」**：每次 `post` 一个版本，发件箱记下这一版编辑**所基于的服务器戳** `baseStamp`
     （编辑发生那一刻本页已知的最新服务器戳：GET 读到的、自己 PUT 被接受的、stale 回的最大值；基线未知则为 `undefined`）。
     stale 回来时只拿**这一版的** `baseStamp` 和服务器戳比：
     - **服务器戳 ≤ 基线**（本版是在已知的最新状态上编辑的；墙钟回拨属于这种）：本版仍是本页最新的版本时，换更大的戳重写；
       已有更新的本地版本就由那一版按自己的戳去写，旧版本不换戳、不重写；
     - **服务器戳 > 基线**（有我们编辑时不知道的写入，别的标签页写了更新稿）：**不提升、不重写**，放弃这次未确认的写入并对账采纳服务器值。
     **对账之后读到的戳只影响之后新编辑的基线，不改已经 post 的版本**——A 在途、B 被别处保存、本页对账读到 B（界面没采纳），
     A 的 stale 回来仍按 A 自己的基线判，被放弃，不会盖掉 B（没有全局「见过的戳」集合）。
     **基线 = 输入框当前显示的那份文本对应的服务器戳**：只有两种情况推进它——对账确实把服务器文本应用到了输入框（或输入框里本来就是同一份文本），
     以及本页自己的写入被接受。没被应用的读取（本地有未落定的版本）和 stale 回的戳都不推进；用户接着输入的新版本继承的是「正在显示的那份文本」的基线。
     **语义取舍：按编辑时间「最后写入者胜」（LWW）。** 对账读到别处的稿 B、但因本地有未落定的版本而没有应用，之后用户继续输入 A2——
     A2 的戳按时钟比 B 新（读到 B 的戳会抬高本页写入戳的下限），所以 A2 被接受、覆盖 B。这是有意的，不做「服务端比对基线、基线不匹配就拒绝」：
     用户在本页看到、编辑的是自己的稿，晚于 B 的那次输入就是最新意图；反过来的方向（B 比本页的编辑更新、本页的版本因此被放弃）
     由「草稿已被另一个窗口的修改替换」的提示覆盖——两个方向都不再是静默丢字。基线只用来判「这一版编辑时是否知道服务器上有更新的写入」，
     不用来拒绝比服务器更新的写入。
     **写入戳 = 按 1024 µs 对齐的时间部分 + 本页随机的 10 位 client 段**（量级仍是墙钟微秒，页内严格递增，跨页面可比较，后端只比数字大小）：
     client 段只有 10 位，两个页面仍有 1/1024 的同戳概率，所以「服务器回 stale、但它那行的戳等于这一版自己的戳」**不靠本地计数**判，靠**内容身份**：
     stale 响应带 `text_hash`（服务器那一行当前文本的 64 位 FNV-1a 哈希，前后端各一份实现、固定文本用例钉住一致），
     **戳相等且哈希等于本版文本的哈希**才按「自己的重复尝试先落地 = 已接受」收尾；戳相等但内容不同（别的页面碰巧同戳、赢了）按普通 stale 规则走。
     **每次落地（不论结果、不论是不是最新版本）之后统一过「槽不变式」**：槽里还有未落定版本，却既没有在途请求、也没有任何定时器，就补一个——
     结果是 unavailable 时按退避，其他结果立即发送；不逐个分支去补（A 在途时编辑出 B，B 的防抖复用了 A 的 promise，A 落地为 unavailable 就会让 B 永远挂着）。
     **`inflight` 只跟踪普通 PUT**，并带 15 s 超时（超时按 `unavailable` 走正常重试；迟到的真实结果仍会落地）；keepalive 是发出去就不管的
     尽力尝试，不替换被跟踪的请求（它的连接半开挂住不能卡住之后的编辑）。
     **同一版本连续 stale 到上限（5 次）之后**：推进戳、按退避间隔等一会儿，再**重新走一遍完整的 stale 判定**（计数清零；仍可提升就用更大的
     新戳重写），不会无限重发同一个被拒的戳；这种状态计入「连续失败」，超过一分钟照样出灰字。
     服务端清理（accept / 条件删除）留下的墓碑戳只 **+1**（取代的恰好是被删的那一版）：用户**之后**的新编辑（戳一定更大）不会被它当成「更新的写入」挡掉。
     放弃并采纳另一个窗口的稿之后输入框里的字变了：在输入框上方（与「已恢复」「正在重试」同一行、同一种安静样式）说一句
     「草稿已被另一个窗口的修改替换」（`data-ai-draft-note=replaced`），下一次编辑后消失——不静默替换。
     **这条规则没有缺口靠两件事**：(a) 第一次草稿读取成功之前输入框**只读**（占位「正在载入草稿…」，`onChange` 也不收），所以每一次
     本地编辑都有一个已知的基线；读请求超过 5 s 不回按读失败处理，不让输入框永远只读。
     (b) 首读失败（不知道服务器上有没有更新的版本）时**允许编辑**——用户的输入不能丢——此时 post 的版本基线为 `undefined`：收到 stale
     一律按「可提升」处理，以本地为准换更大的戳重写。取舍：读不到、又恰好别的标签页写过更新稿时会盖掉别处的稿；第一次带戳的服务器结果
     （保存 / 墓碑）之后再 post 的版本就有了基线。
  4. **提交确认后清草稿比「草稿戳」，不比文本**：发送前 `draftSnapshot()` 先把没写出去的草稿写成功、拿到服务器接受的戳，
     连同提交一起记进账本（`draft_stamp`）；确认时后端只有「草稿当前的戳仍等于它」（自提交后没被改过）才清草稿，
     账本没记戳 = 不知道、不动草稿。提交文本是 trim 过的、草稿是原样，按文本比永远对不上。输入框本地只在 `seq`
     没变时清；之后以后端清理的结果经对账为准。
  4c. **账本行的删除只发生在后端事务里**：accept（确认）、`/api/ai/run` 明确拒绝时由后端自己删该 rid 的行、新行 PUT 带
     `supersedes: [旧 rid]`（放弃旧等待「改写后再发」）由后端在写新行的**同一个事务**里删旧行——客户端**没有** DELETE。
     客户端单方面判断「这一行不存在 / 已不需要」可能是错的（原请求可能还在路上，DELETE 也可能失败），旧行之后被还原成「未确认」，
     而新 rid 已经执行过，同一条指令就执行两次。新行 PUT 失败 = 整个事务回滚 = 旧行还在，状态仍然一致。
     **`supersedes` 同时给旧 rid 写墓碑预留**（`ai_requests.session = '~superseded'`，同一个事务）：原 POST 可能只是慢，删了行之后它才到达也会照样登记并执行，
     旧指令和改写后的指令就都执行了。有了墓碑，晚到的旧 rid 走原子预留必撞唯一约束，被 409 `ai_request_superseded` 拒绝（不 spawn）；
     查询该 rid 回 `status: superseded`（不是 404），前端不再提供同 rid 重发。旧 rid 若已被真实预留（旧请求其实到达了），
     整个 PUT 回滚并回 409 `ai_supersede_conflict`——不能再当成「没收到」取代它。
     **「这份脚本还有没落定的指令」由后端判，不信客户端**：同一个写事务里，同面板、或 `sameScript` 认作同一份脚本的兄弟面板上还有待确认行、
     这次又没在 `supersedes` 里显式取代它 → 409 `ai_pending_unresolved`（带 `rids`），什么都不写、不发。前端的锁（`submissionBlocks` /
     `ledgerState`）只管体验：多标签页、对账读到一半 / 读失败、旧请求迟到登记，本页视图随时可能过期，正确性只靠这道闸。收到后面板
     立即对账一次把它们还原成「未确认」，再按 rid 逐条查询：执行过的接回会话，没收到的交给用户「重发」或「改写后再发」。
     `sameScript` ↔ 后端 `ai_history.same_script` 严格同源，两边共用用例表 `web/src/store/sameScriptCases.json`。
     **spawn 之前的异常**（如 `record_start` 抛 `OperationalError`，被全局处理器转成 500）也由后端删该 rid 的待确认行（`engine_ai.spawn_started()` 标记
     Popen 成功的那一刻）；**已经 spawn 的绝不删**——那条指令可能正在执行，账本行是响应丢了之后还能查到它的线索。
     **删除本身也失败了**（SQLite 锁住 / 磁盘满）就不能再回「明确拒绝」：回 503 `ai_request_unresolved`（不确定），客户端按 `unknown` 处理——
     保持同一个 rid、进入「已发出未确认」，不换新 rid。
     **发送所属的 (项目 id, 项目代次) 在第一个 await 之前固定**，一路传给 `start()`（`start` 不再自己读「当前项目」）；
     每个 await 之后核对代次，变了就作废（`ProjectChangedError`，不发送、不写账本）。点发送后 `await draftSnapshot()` 期间切了项目，
     指令既不会被发到新项目的同名文件上，也不会发到旧项目。
     **账本写入的结局三分**：`ok` / `rejected`（明确的 4xx：确定没写）/ `unknown`（网络错误 / 超时 / 5xx / 读不出响应：可能已经写入、
     只是响应丢了）。`unknown` 不发，且这条指令**保持同一个 rid** 幂等重试（后端同 rid 重复 PUT 不占名额），不换新 rid。
  4b. **发送前草稿必须先写成功**：composer 来源的发送在 `draftSnapshot()` 拿不到草稿戳（PUT 暂时失败 / 后端不可用）时**中止**——
     不发 run、不写账本，一句话 + 重试，输入框原样（与「账本写不成就不发」同一种处理）；`history` 来源（气泡重发 /
     失败会话重试）不碰草稿、跳过快照。失败后的重试按失败记录里的来源与输入框版本走。**指令上限只有一个**
     `INSTRUCTION_MAX_BYTES`（草稿、账本、发送共用，前后端同源、有一致性测试）：输入框超限就地提示并禁用发送，不等发送时才失败。
  5. **账本恢复状态 `LedgerState`（unknown / ok / failed，与 `reconcile` 共用一个状态源）**：只有成功对账过一次
     （草稿 + 项目级账本都读成功、pendings 已还原）才允许发送；还在读或读失败（一句话 + 重试）一律锁——读失败是「不知道」，
     不是空账本；成功过之后一次读失败不降级。
     对账里的两个读（草稿 GET、项目账本 GET）**各自带 5 秒超时**（中止请求并不再等它）：半开连接上的读不会自己结束，
     超时等同读失败（`ledgerState=failed`、重试提示可达，`draftRead` 的失败也由这个结局驱动，不另设计时）；迟到的旧响应按代次丢弃。
  6. **会话终态的来源有三个：事件（`ai.done`）、对账入口（挂载 / 焦点 / 可见 / 重连 / 接受，含建完占位后一律读一次详情）、
     安全网轮询；任何一个都足以收尾。** 事件和对账都可能错过（SSE 没连上任务就结束、断线期间结束），逐个补时序只会越补越多，
     所以只要本地还有非终态会话（running 或接回的占位），`aiStore` 每 15 s（`SAFETY_NET_MS`）对它们走一次
     `settleRunningSessions`（只读快照）：错过的 `ai.done` 最多 15 s 后被收尾、发送锁解开。没有非终态会话不轮询、页面不可见暂停
     （恢复可见立即跑一次）、换项目会话清空后自然停；详情读取按「代次 + 项目 + 会话 id」在途去重。
     **快照只有 `final === true` 才算终态**（`snapshotIsFinal`；`cancel` 之后 status 是 cancelled 但 changed / diff 还没算出来，不是终态；
     老后端没有该字段时退回按 status 判），终态快照的转录是权威来源——收尾时按顺序与本地 entries 合并（补回断线期间丢的、不重复、本地独有的保留）。
     **任务结局（TAF，ADR 0138）只翻译 `task.outcome`**：`ai.done` 与会话详情的可选 `task` 与 `refresh` 走同一几处（`finish` / 详情恢复 / 对账 / 建占位），状态行、结局卡（`data-ai-task-outcome`）、状态栏按它选文案，前端不判断检查本身；`status` 与 `outcome` 分开记（done + partial / failed = 改了但核对没通过），老后端没有 `task` 时全部走原逻辑。
  **「首个 POST 不确定」引出的所有分支**（用户点查询、同 id 重发返回 `deduped:true`）都收在 `aiStore.settleFromSnapshot`：
  向后端要请求 + 会话快照（详情只在 `readSessionDetail` 一处读），按快照的真实状态收尾（终态走正常完成路径含 refresh
  警告，仍在跑才是 running，读不到保持未确认、绝不凭摘要或 deduped 响应建 running 会话）。看护
  `components/ai/assistantDraft.test.tsx` 的「同步状态机」一组（含入口同路径的结构性用例）与后端 `tests/test_ai_drafts.py`。
  **草稿文本与待确认提交原文是两份独立状态**：输入框永远显示草稿（`fetchDraftState().text`），待确认原文在
  `pendings[].text`、只在「已发出未确认」那一行里展示（`data-ai-submission-text`），确认只清待确认、不碰草稿——
  否则 A 标签页的待确认会盖掉 B 标签页更新的草稿，A 确认后本地清空、B 又被下一次按键覆盖掉（#1098）。
  aiStore 经 `setAiSubmissionLedger` 注入落盘（Codex 内嵌画布与 playground 的包里带着 aiStore 但不得带草稿模块）。
  草稿与 rid 不进日志 / 追踪环 / frontend-state。看护 `store/aiSubmission.test.ts`、`lib/aiDraft.test.ts`、
  `lib/aiDraft.hosts.test.ts`、`lib/aiDraft.privacy.test.ts`、`components/ai/assistantDraft.test.tsx`、`e2e/ai-draft.spec.ts`、后端 `tests/test_ai_drafts.py`。
- **`assetStore` 的清单纪律**（`/api/panels`：面板、`unsupported`、目录；本条是全文，
  `project-document-and-autosave.md` 只引用）：**请求序号**挡旧响应覆盖新响应（不是"谁最后返回"）；
  **发请求那一刻的 pj** 挡串项目（`null` 与具体 id 是两个取值）；**项目代际**——`resetForNewProject()`
  里 `clear()` 清空并换代，发请求时记下的代际对不上就丢（#577：切项目时 B 的清单挂起或失败都不许显示
  A 的卡片与「无法使用」清单；A → B → A 时 A 第一次发出的迟到响应 pj 对得上，只有代际挡得住）；
  **同一项目**刷新失败不清空 `panels` / `byId` / `unsupported`（#561）；同项目的在途请求被合并、在途期间
  的调用共用一次补问，`force: true` 永远另起一次（手动刷新不许被在途请求吞掉）。`recentlyUsed` 是按
  文件 id 记的本机偏好，不清。看护 `store/assetStore.test.ts`、`store/projectSwitchAssets.test.ts`。
  `refreshProjectNow()` 从扫描开始到强制清单落地一直持有 `assetStore.beginRefresh()` 的忙碌态，
  清单请求的 finally 只释放自己的那一份；并发刷新各自释放，`clear()` 作废旧代全部持有者（#904）。
  卡片不直接写 `loading`，不据它猜是否另发清单。看护 `store/libraryRefreshRace.test.ts`、`PreparationCard.test.tsx`。
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
  （`DependencyPrepareDialog`）同一套：没有装齐的用户环境、默认目标是受管环境时，**正文**就是那一句（标题固定是「准备环境」，
  2026-10-07 设计审计 §10.2：标题不是生成句、失败时不被错误句替换；干净机器上什么包都
  不缺时换成「需要先准备运行环境」那一句；私有 Python 的披露只跟**此刻选中的**目标走，选了项目 venv 就不提），底部是
  「稍后」「一键修复」与 start 槽里的「不准备，直接运行」（ghost），其余进「详情」（一组平铺的 `FieldGroup`，不再 Details 套 Details）；
  失败是正文末尾一条危险 `Notice`，进行中页脚原位置灰；默认目标是项目 venv（会改用户环境）时目标单选留在外面，每个选项的说明压成一句
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
  哪个入口走哪一层以 `canvas-objects-and-workspace.md` 的「加图是一条分层链」为准。抽屉标题行的
  工具区给一对 listbox 之外的真按钮（`IconButton`「编辑原图」「添加到画布」，`data-asset-open` /
  `data-asset-add`，作用于当前选中的卡，没选中时置灰）——位置固定，选中不同卡时抽屉几何不变；
  option 里不许嵌可 Tab 控件，卡片右上角的「＋」只是指针的快捷入口（不可聚焦）。**不许再用一个中性的「打开」承载加入文档。**
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
  `FigurePickerDialog`——每张可见、各自可加、**绝不静默选第一张**；加一张**不关框**，已在画布上的那张说「已在画布上」，
  脚部「全部添加（N）」+「完成」（2026-10-07 设计审计 §10.2）；条目
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
  脚本的提示与输出片段只当纯文本；事件流以 `/api/events?answers=1` 声明「能答题」（同一道会话认证），收到 `stream.hello` 与每次认领新项目时（`onCurrentProjectChange`，与事件过滤同一时刻，不等 `project` 赋值）经 `announce()` 报在看哪个项目（后端按项目认答题方）；改 / 删答案的结果换了项目就是 `stale`，调用方不许接着重跑；答案管理（`ScriptAnswersDialog`；答案按运行配置 `run_config` 分行，改 / 删只动那一行、重跑只重跑那份配置，整批提交涉及几份配置就各重跑一次）行内只改值、没有主按钮，「删除」在行尾 ⋯ 里且是**暂存**的（行上标「保存时删除」、可撤销，2026-10-07 设计审计 §10.2），脚部唯一的主动作「保存并重新运行（N）」依次提交所有改过 / 标了删除的答案、只重跑一次（在飞时对话框 `busy`：×、点外面、Esc 都关不掉，半途关掉会只带部分改动重跑——Codex #831 P1）；没提交前什么都没发，换了项目（store 换代）暂存的删除随之作废；改 / 删答案一次只许一件（整批提交算一件、只拿一个 token），锁归 `scriptInputStore`（`answersBusy` + `beginAnswersChange()` / `endAnswersChange(token)`）不归对话框组件——对话框的 `busy` / 禁用都读 `answersBusy`：拿不到锁就一个请求都不发、也不重跑，`saveAnswer` / `forgetAnswer` 只认持锁 token，答案管理被卸载再挂上（`busy` 拦住了 ×/Esc，但 `closeManager()` 等别的路径仍能关）时新挂上的对话框读同一把锁、在飞的那批回来才放开（否则两份同项目整份快照互盖、重跑两次）；`clear()` 换代一并清锁，旧项目那批回来时 token 已不是持有者、放不掉新锁；`ScriptInputDialog` 打开时焦点直接在答案框（`initialFocusRef`），「停止脚本」在 start 槽（危险浅底胶囊）；「记住的输入」入口只在
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
BooleanOptional 仅在有长名反向选项时提供三态，短名只有「用默认 / 开」，不生成无法表达的「关」；短选项的选项形状值用
`-k=-x` 消歧，不能被完整的 `-k-x` 选项接管。编辑被拒时输入框保留原值与焦点、token 不变、说出原因；缺必填 / 互斥冲突只提示不拦。粘贴命令（`lib/argvPaste.ts`）
只接受一条简单 POSIX sh 调用（拒绝管道 / 重定向 / 变量 / 通配符 / 多行 / 前置赋值，失败提示改用逐项填写），被接受的分词与
`shlex.split` 逐项相同；直接调用别的 `.py` / `.pyw` 脚本也报 `different_script`，不吞成位置参数。两份 golden：`tests/golden/script_args_form_vectors.json`、`argv_paste_vectors.json`。
看护：`lib/scriptArgsForm.golden.test.ts`、`lib/argvPaste.golden.test.ts`、`components/ScriptArgsForm.test.tsx`。


答案管理（T08）：行身份、改 / 删请求与随后重跑都绑定答案的 `run_config`（只显示不透明引用，不取 argv / 上下文）。
无参数条目显式用 `null` 重跑，避免读取当前草稿；配置引用保留到运行门放行与数据改指后的重试。换项目的迟到响应不重跑。

**门后排队与同脚本重跑串行（`scriptRunStore`，Codex #816 r4221169169 / r4221258366 / r4221258376）**：
- 批量重跑（`runConfigsInOrder`）中途撞上运行目录 / 依赖门，就把「停在门上的那份 + 后面没跑的」记进 `gatedQueues`（按脚本，带代际），不算失败。**同一脚本的多批排队按配置做并集**：先来先跑，已在队里的配置不重复排（重跑本来就读最新答案，位置保持首次入队），绝不覆盖——关掉门又保存另一批后再解门，A、B、C 各跑一次。门有了答案时 `rerunGated` 取回整份并集按序续跑（不要求队首等于停在门上的那份；停在门上的那份不在队里就追加到队尾，宁多跑不丢）。队列记着自己停在哪道门（`phase`），`rerunGated` 的候选 = 可见行停在这道门上的 ∪ 队列停在这道门上的：**续跑以队列本身为准，与当前可见行解耦**——门后排队期间同脚本别的配置跑完替换了 `byScript` 那一行，解门照样续跑整份队列。换项目 `clear()` 整份作废；`reset()` 只在收起的是停在门上的行时丢该脚本的队列。
- **运行方式显式判别（Codex #816 r4221496552）**：队列与解门重跑里的每一份是 `RunSpec`——`{kind:'draft'}`（读运行那一刻的参数草稿，行上 `runConfig === undefined`）或 `{kind:'config', id}`（答案管理的固定配置，`id: null` = 显式无参数）。**绝不用 `?? null` / `|| null` 把 undefined 折成 null**：草稿运行撞门后解门必须仍走 `run(script)`（`probeWithDraft`），否则丢参数重跑。
- **续跑批的失败要说出来（r4221584209）**：门后续跑是 fire-and-forget，发起它的对话框早已返回；续跑批结束时 `reportResumedFailures` 按配置发出与对话框直提交同一条 `manageRerunFailed` 提示——不管此刻可见行是哪一份（A 失败、B 成功时 B 会盖住可见行）。
- **同脚本的重跑串行**：`runConfigsInOrder` 在同步段里认领脚本（`batchClaims`），一直占到收尾，包括批内两份之间的空档；第一份在认领的同一个同步段起跑（已空闲不 `await`），所以 `rerunGated` 通知门监听者时脚本已是忙的。认领按 (项目代际, 脚本) 记（r4221496560）：切项目后旧代的长批不挡新项目的同名脚本，旧代释放只删自己那一代的键；批内的等待（`whenNotBusy`）换代即放弃。`whenScriptIdle`（接入中心的门后重跑）把认领也算忙，另一批答案先等前一批释放认领再进场。`runConfigsInOrder` 的项目代际在**第一个 `await` 之前**取，等认领期间切了项目醒来即放弃（不在新项目里跑旧项目的配置）。新增任何「同一脚本的重跑入口」都要么走 `runConfigsInOrder`、要么先 `whenScriptIdle`，不要自己写 `whenNotBusy` 之类的并行等待。
- 看护 `scriptRunStore.test.ts`「门后排队续跑的排队语义」（并集不丢旧批；答案批次与 Registry 同时等门无并发探测）与 `ScriptAnswersDialog.test.tsx` 第一份停在运行目录门上。

## 2026-10-07 设计刷新（审计 §5 / §10.3）

- **素材卡经 `ui/Card`**（raised + interactive + selected）：`li[role=option][data-card=<素材 id>]` 是焦点 / 键盘 / 拖拽的那个
  option，里面一层 `Card`（它自己的 `data-card` 是外观名 `raised`——按素材找卡片一律带值 `[data-card="<id>"]` 或 `li[data-card]`）。
  就近入口（`data-card-actions`）是箭头光标，卡片本身才是抓手。
- **选中之后只有一条页脚**（`data-asset-footer`）：上一行名字 + 动作（`data-selected-asset-actions`，真按钮、在 listbox 外），
  需要说话时下面跟接入说明（`data-capability-notice`，同一个容器）。
- 「筛选」「刷新」在抽屉标题行的动作槽里（`left/DrawerHeader`）；筛选 chip 是胶囊；
  **能不能编辑**是搜索框下面一排常驻的 `ui/Segmented` 胶囊（`data-testid="asset-editability"`；全部 / 可编辑 / 待连接 / 仅版面，
  2026-10-14 起替代标题行那颗「只看可编辑的图」图标钮与类型筛选里的「可编辑」一项——藏在图标里没人找得到）。归组唯一一处
  `editabilityOf()`，只翻译后端 `capability.status`：`editable` / `layout_only` 各成一组，`PENDING_STATUSES` 四档是「待连接」
  （不许并进「仅版面」，卡上写着「需试运行」时下一步是跑一下）；`capability` 缺席时有 `script` 算可编辑、没有就不归组（不补
  `layout_only`）；运行时图有描述符算可编辑、没有算待连接。「待连接」一档只在有这类图或正选着它时出现；它是一眼可见的取值，
  不进可移除的筛选标签；用不了的文件（`UnsupportedAssets`）只在「全部」下列出。胶囊的显示规则：有图、或有用不了的文件、或正选着非「全部」时都出，三者皆无才不出——正选着非默认值时绝不藏控件，否则最后一张图消失会把用户困在「没有匹配的图」里（Codex #1027）。胶囊标签是专用短键（`assets.editabilityEditable/Layout/Pending/All`），不复用 `statusLabel` 的长句：它要放进 280px 最窄抽屉（扣内边距 256px），英文四档「All / Editable / Pending / Layout」由 e2e 在最窄抽屉里量过不溢出（Codex #1027 r4267757501）。看护 `AssetBrowser.editability.test.tsx`、`e2e/asset-library.spec.ts`（功能 `assets.editability-filter`）；
  刷新失败是图区里的一条 danger `Notice`（一行中性提示：「无法刷新」+ 一句原因，长原因进「详情」；包 `NoticeReveal`，长出来时下面的网格顺着滑下去而不是瞬间跳），
  挂在「图」区头下面、**不在可折叠的网格里**（首次读不出的 `EmptyState` 同此）——
  图区收着时 `/api/panels` 失败不弹提示（`load()` 吞错返回 null），藏进 `hidden` 就是静默失败（Codex #832，`AssetBrowser.refresh.test.tsx`）。刷新中那行「正在刷新…」（`data-asset-refreshing`）**暂留**：`e2e/tutorial.spec.ts`
  拿它挤动卡片来量 coachmark 的同步重测（Codex #731），换掉那根杠杆之前不删（审计「刷新只留旋转图标」那一条待办）。
- **脚本区**：组头 28px `type-section` + `type-meta` 计数；运行中 = 一颗静止的 accent 点 + 状态句的 `text-shimmer`（不转圈）；
  所有恢复入口（一键修复卡、跑前准备、运行目录、缺数据、失败详情）是同一种「第二行」（`SecondRow`）；读清单 = 静态骨架、
  筛不到 = `EmptyState`、读不出 = danger `Notice`；每行一份 `ui/RowMenu`（运行 / 取消、记住的输入、复制路径）。复制路径按**项目根**（`projectStore.project.figures_dir`，退一步才用 `assetStore.figuresDir`）拼绝对路径——不等素材清单；两边都不知道就不给这一项，不复制会在别处解析的相对名；没有 `navigator.clipboard` / 写入被拒一律报 `pathCopyFailed`（带路径），不静默（Codex #832）。

## 速查表原要点（2026-10-08 迁入，Windows CRLF 下的 32 KiB 硬线）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限按 Windows
检出的 CRLF 字节量，#608）。下面是当时写在那一格的要点，原文照搬、一字未改（按「；」分条）；
它们与上文同等有效，改规则时一并改这里。

- 七个 store 都有项目代际
- 同脚本防并发、取消等原请求以 `execution_cancelled` 落地
- 多 Figure 结果绝不只显示第一张
- runtime 卡没有假值
- 「编辑原图」必然加进文档并说出口、素材卡的「添加到画布」走 `addFigureToLayout`（哪个入口走加图分层链哪一层以 `canvas-objects-and-workspace.md` 为准）
- `role="option"` 里不嵌可 Tab 控件
- TIFF 经 `panelSrc` 走 `/api/render`

## 静态 SVG 图片（2026-10-18）

素材扫描返回独立 `kind=svg`，文档持久化为 `fileKind=svg`。素材卡、放大预览、画布及缩略图
使用经过完整校验的静态 SVG，不插入内联 HTML、不先转成位图。SVG 有独立格式筛选，
与同名 PDF 同时收录；坏文件进入 `unsupported`，不静默消失。

本轮边界是整图预览与摆放、原图 SVG 导出。静态 SVG 的画布合成和跨格式导出尚未接入
RenderCore，必须明确报 `svg_asset_export_unsupported`，不能交付空图或偷偷栅格化。
已有关联脚本的图内编辑仍走既有执行入口，不能把 SVG DOM 当成 manifest 几何权威。

看护：`tests/test_svg_assets.py`、`lib/panelSrc.test.ts`、
`components/left/AssetBrowser.tiff.test.tsx`、`e2e/svg-assets.spec.ts`。
