# 前端诊断：状态快照与交互轨迹（2026-08-27，ADR 0016）

> 原文出自 `web/AGENTS.md`「前端诊断：状态快照与交互轨迹（2026-08-27，ADR 0016）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0016-diagnostics-v2-frontend-state-tracing.md`，改动前先读。
模块是 `web/src/diagnostics/`，业务代码**只 import `@/diagnostics`**。

- **权威判据不在这里**：诊断报的 `authority_variant` 一律委托
  `docs/rules/frontend/display-fallback-vs-geometry-authority.md` 的 `exactPanelRender`（ADR 0017）。诊断**绝不另立一份判据**——否则会出现
  「诊断说权威就绪、写路径当场拒绝」，两边各说各话。因此权威只有两种取值：
  **就是当前这一版，或者根本没有**；「来自别的变体的权威」这个概念不存在。
  ADR 0017 的追踪环（原 lib/authorityTrace.ts）已并入本模块，别再建第二个环。
- **只观察，不当真源**：诊断不参与任何业务判断，快照是**读**业务 store 得来的，
  不维护影子状态（影子状态会漂移，漂移的诊断比没有诊断更坏）。
  `recordDiagnosticEvent` 整体吞异常——诊断把一次编辑弄挂比没有诊断糟得多。
- **隐私是两道判据不同的防线**：`types.ts` 的可辨识联合挡编译期手滑（事件里写
  `text: element.label` 直接 TS 报错），`sanitize.ts` 的**逐事件字段表**挡运行期。
  序列化**遍历 schema 而不是输入的键**——多出来的字段是「根本没被读过」，
  不是「读了再丢掉」。**写入即脱敏**：环里物理上不存在未脱敏的数据。
- **`data-display-key` 是对外暴露面**：它落在 DOM 上（e2e 读它、用户也看得到），
  用的是 `diagnosticHash`。`diagnostics/privacy.test.ts` 是它不泄漏文件名与
  override 原文的唯一看护，别删。
- **定长 240 条、纯内存**：不写磁盘、不自动上传、不进 telemetry；只有用户点
  「导出诊断包」才 POST 给本机后端。**切项目要 `clearDiagnosticTrace()`**
  （已接进 `resetForNewProject`）——否则新项目的包会带着上一个项目的操作序列。
  `seq` 刻意不重置：编号缺口是「这里被清过 / 被环挤掉」的唯一线索。
- **不记 mousemove、不记每一帧预览**；document 摘要只在真状态边界算，
  且靠 immer 的结构共享 + WeakMap 缓存（`digest.ts`），改一个对象只 hash 一个。
- 看护：`web/src/diagnostics/*.test.ts` + `tests/test_diagnostics_bundle.py`
  （服务端第二道校验、ZIP、端到端隐私回归）。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 权威判据委托 `exactPanelRender`
- 只观察不当真源、吞异常
- 序列化遍历 schema 不遍历输入
- 定长 240 纯内存、切项目清环

## 「本次问题的诊断」入口（T04，`components/TaskDiagnostic.tsx`）

失败提示处一键取回**那一次**尝试的快照（后端 `GET /api/diagnostics/task`，见 `docs/rules/backend/diagnostics.md`「任务绑定诊断」）：

- **卡片一句话**：默认只露一个折叠标题（`Details`），主按钮仍是失败提示自己的「重试」；展开才是一句说明 + 一个下载按钮。
  已经住在折叠区里的调用方（脚本行的「详情」）传 `folded={false}`，只出按钮与结果。
- **项目取组件出现那一刻的**（`useRef(currentProjectId())`），不在点击时再取——失败提示可能比项目切换活得久。
- **记录没有 / 过期如实说没有**（`data-task-diagnostic-gone`）；**绝不**退而求其次下载一份当前状态的诊断包冒充当时的状态。
- 引用来源：导出 = `job.job_id`；脚本试运行 = `ProbeResult.diagnostic.ref`（存在 `ScriptRunState.diagnostic`，下一次运行清掉）；
  准备 = 会话报告的 `provider.attempt_id`（T09 的面板直接挂）。
- 文案在 `dialogs:taskDiagnostic.*`，看护 `components/taskDiagnostic.test.tsx`（两个语种 × 默认可见块数 / 主按钮数、项目绑定、
  过期 / 不存在 / 服务端错误三种结局）、`store/scriptRunStore.test.ts`。

## 发送问题反馈（设置 → 帮助与诊断，ADR 0118；入口由引擎能力说明决定，发行版默认开启）

- **前端不碰远程。** 上传由本机引擎做；`lib/api.ts` 的 `*DiagSend*` 只是本机遥控器，其中**只有 `startDiagSend` 会让引擎去连诊断服务**，且请求体恒带 `confirm: true`
  （`lib/diagSendApi.test.ts`）。入口是否存在只看 `store/diagSendStore`（`GET /api/diagnostics/send` 的镜像，App 启动取一次；没取到 / 关着（`TAVOTTO_NO_DIAG_UPLOAD=1`）/ 回了别的 = `null` = **不画入口**（前端侧失败即关；引擎侧默认开启），
  隐私摘要也不多说那一句）。
- **打开 = 备包，发送 = 确认。** `components/settings/DiagnosticsSendDialog.tsx`：打开时只 `prepareDiagSend`（载荷现采，同导出），列「将发送的内容」类别、大小、保留期，
  「保存这份诊断包」取的是备好的**同一份**字节（按 id，不重新生成）；点「发送」才 `startDiagSend`。关窗 / 卸载 = `discardDiagSend`（发送中等于取消）；备包响应晚于关窗到达也要丢弃。
  未确认 / 关窗 / 取消时 `startDiagSend` 调用次数为 0（`DiagnosticsSendDialog.test.tsx`）。**不许**把说明文字、报告编号以外的任何东西放进遥测。
- **布局不跳（#797）。** 入口行是「诊断报告」组的最后一行；对话框页脚两颗按钮始终在原位只换字 / disabled；状态文字住在页脚左侧、与按钮同一行（Dialog `footer.start` 槽，固定 `h-8`、最多两行、超出截断，完整文字在 `title`），不为它预留大块空白——收起态对话框紧凑（e2e 量高度；2026-10-19 说明框在前之后上限 500px），异步结果只换这一格里的字，e2e 量发送前后「发送」按钮坐标不变。
  发送中 `blockDismiss`，Esc = 取消发送；取消回到可编辑表单并说明「没有发送」。
- **失败文案与引擎同源。** `settings.diagnostics.send.failure.*` 的键集 = `engine/diagsend.py` 的 `FAILURES`；问题类型 `category.*` = `CATEGORIES`（服务端契约闭集）；
  `kind.*` ⊇ `ENTRY_KINDS` 的值。三条都由 `DiagnosticsSendDialog.test.tsx` 读 Python 源码对拍；码不认识时按 `unexpected_response` 说，不空白。
- **故障卡入口与轻确认框（用户 10-10 要求）。** 对话框全应用只挂一处（`components/DiagnosticsSendHost.tsx`，状态 `diagSendStore.open`）；设置页入口行与故障卡的 `components/SendReportButton.tsx` 都只是 `setOpen(true)`
  （打开 = 本机备包，不发送），开关判据同一个（`diagSendStore.capability`，关着完全不渲染）。覆盖的故障卡（都经 `TaskDiagnostic` 或点名处理）：导出失败卡（`ExportDialog.ResultBlock`：可重试时在折叠的「本次问题的诊断」里；
  不可恢复、没有任何修复动作时**就是主按钮**）、导出部分失败（折叠详情）、准备卡失败态（`PreparationCard`：有主按钮时在详情里；失败且没有任何可执行动作时是主按钮）、脚本行「详情」里的运行失败、依赖准备失败。
  **不带故障上下文进对话框**（note 只由用户自己写；没有现成的闭集字段可带）。**说明框在前**（用户 2026-10-19 裁决，取代 10-10 的「默认只露一句话」——用户嫌展开后太臃肿、说明框反被折起来）：默认可见 = 对话框副标题那一句（`Dialog description`：「将发送诊断包，含报错详情和日志（日志里可能有脚本打印出的内容），不附带你的脚本文件和数据文件。」——
  必须如实提醒日志里可能有脚本输出，#1012 4，文案测试钉住，**这句不许折进去**）+ 表单（参照 OpenBitFun 的表单语言：标签在字段上方、间距 6；提示与字数同一行、提示左字数右；框只给可编辑的控件）：「补充说明（可选）」textarea、「问题类型」`Select`；
  + 一组（`FieldGroup`，3% 底、无边）里的一行可展开项「附带诊断包 · 大小 … 查看内容」（展开内容与标题文字对齐缩进）+「发送」「关闭」（一颗主按钮）。其余（内容类别、额外勾选项、包内文件、保存 ZIP、保存期、隐私政策链接、「脱敏尽力而为」）
  全在默认收起的「查看内容」里（`DiagnosticsSendDialog.test.tsx`「说明框在前」一条钉可见集合；e2e 量收起态高度 < 500px）。那句承诺由
  `tests/test_diagnostics_bundle.py` / `test_diagnostics_log_privacy.py` 的金丝雀全文搜索撑着（源码行略去、异常 message 不出门、路径哈希化）；这两处任何一处放松，那句话就得先改。
- **项目代次与引擎丢会话（Codex #923）。** 备包流程绑定当前项目：`DiagnosticsSendDialog` 订阅 `onCurrentProjectChange`，对话框开着时项目一换（外部 `tavotto open`、切项目）就让备包重来——
  旧包随清理 `discardDiagSend`（发送中等于取消）、在途的旧备包响应按代号丢弃并通知引擎释放；`send()` 还会在发那一刻核对备包时的项目，不一致就不发、重新备包。
  状态轮询遇到 404（引擎重启 / 会话过期）是**终态**：停止轮询、解除 `blockDismiss`、说「已不在引擎里」并给「重新准备」，绝不把用户困在模态框里（`DiagnosticsSendDialog.test.tsx` 的「项目代次」「引擎丢了会话」两组，sending 与 cancelling 两态都测）。
  **备包只在切换完成之后做**（Codex #923 复核 P1）：`projectStore.adoptSteps` 先 `setCurrentProjectId(B)`、之后才 await 加载并 `resetForNewProject`，认领那一刻内存里仍是 A 的文档与轨迹。所以对话框以 `projectStore.switching`（`runSwitch` 排队到执行完一直亮着，既有机制，不另造代次）为「项目已完全加载」的信号：switching 亮起 → 旧包作废、置「正在准备」、发送禁用、不备包；落下 → 才备新包（`DiagnosticsSendDialog.test.tsx`「真实的切换顺序」，反证：去掉等待则红）。
- **迟到 start 的 409 不是失败（#928，Codex #923）。** 取消先被引擎处理时，引擎正确拒绝迟到的 start（409 `diag_stale_action` / `diag_session_cancelled`）。`DiagnosticsSendDialog` 的 `send()` catch 把这两个 code 当作取消流程的一部分：置 `cancelling`（不覆盖成不可重试的 `failed`），轮询继续到 `cancelled` 后自动重新备包（`DiagnosticsSendDialog.test.tsx`「迟到的 start」两组）。
- **清理显式带备包时的项目（Codex #955）。** 引擎会话按项目分键，而切项目时全局当前项目先于 effect 清理变成 B；`discardDiagSend(id, pj)` 因此由对话框传 `packagePj`（备包那一刻的项目；迟到的 prepare 响应用闭包里捕获的 `pj`），请求以 `?pj=` + 头显式指向 A。不传则按 B 找不到 A 的 id、静默 `{ok:true}`，A 的上传继续。后端 `?pj=` 本就先于请求头（`app._request_ctx` / `_diag_send_scope`），`test_diag_send_api.py::test_explicit_pj_query_wins_over_a_conflicting_header` 钉住。
- **联系维护者（企业微信，仅中文界面）。** 两个地址只在 `lib/contact.ts`（获客链接 `WECOM_CONTACT_URL`、名片码地址 `WECOM_CARD_URL`），二维码图 `assets/wecom-contact-qr.png` 必须解码为后者（`tests/test_wecom_contact_qr.py`，无 `cv2` 则跳过）。
  组件 `settings/WeComContact.tsx`：**判据是界面语言 `currentLocale() === 'zh-CN'`，不看系统区域**；英文界面不渲染元素（不是隐藏）。位置：发送问题反馈确认框的 done / unknown / failed 三态（ready / preparing 不出现），
  与设置页「诊断报告」组里发送行之前的「联系维护者」行（发送功能关闭也在，联系方式不依赖上传通道）；确认框里链接与「显示二维码」同占一行，其余阶段该行**占位不显示**（`invisible` + `inert`，#797 的发送按钮不跳位；英文界面连占位也不渲染）；二维码默认折叠、展开才挂 `<img>`；链接是 `target=_blank` 外链。不收集、不上传任何联系方式，不进诊断包；/try 不加（`WeComContact.test.tsx`）。
- **异步回调一律认代号**：`DiagnosticsSendDialog` 里 prepare / startDiagSend / 保存 / 状态轮询的 then·catch 都捕获发起时的 `gen`（轮询用 `stop`），代号变了（项目切换、重新备包、关窗）就忽略——A 的迟到失败不许改 B 的对话框（`DiagnosticsSendDialog.test.tsx`「A 的 start 挂起 → 切到 B」，含 404）。

## 发送包的 detail 载荷与勾选项（bundle schema 8，ADR 0118 修订 2026-10-13，`diagnostics/detail.ts`）

- **只走发送路径。** `buildSendPayload(options)` = `buildDiagnosticPayload()`（老载荷）+ `detail_options` + `detail`；导出路径仍用 `buildDiagnosticPayload()`，一个 detail 字段都没有（`detail.test.ts` 钉）。引擎导出端点也不读这些键。
- **两个定长全局环**（纯内存、不写盘、不进 telemetry、**切项目不清**——console 输出不属于某个项目，包里 `frontend-console.json` 注明 `scope: global`）：console.error/warn 40 条（原文 + Error 自带栈，字符串消息记调用点栈）、未捕获错误 / unhandledrejection 30 条。
- **console 实参只记原始值与 Error，其余对象写 `[object]`**（#1012 2）：判断「是不是纯数据」（取原型 / 读描述符 / `for...in`）本身就会触发 Proxy（immer draft）的 trap，所以不判；业务侧确实要记一棵纯数据时用 `markPlainData(tree)` 声明（调用方保证无 Proxy / 副作用 accessor）。Error 判定走 `Error.isError`（不触发 trap），老 WebView 退回 `instanceof`。看护 `detail.test.ts` 的 trap 计数用例。
- **焦点面板的顺序**（#1012 1）：没有正在编辑的面板时，`overrides[0]` 必须是主选（最后选中的面板），其余按文档顺序——引擎拿它当焦点面板去探环境。
  `main.tsx` 里 `installConsoleCapture()` 越早装越好；包装函数**吞掉自身所有异常**且一定调用原方法，幂等、可卸载。
- **detail 里还有**：显示环境（缩放 / 窗口 / 屏幕 / 语言 / 主题）、壳信息（Tauri `getVersion` / `getTauriVersion`、UA 里的 WebView 版本；`lib/desktop.ts` 的 `getUpdateOutcome()` 记本窗口会话内最近一次检查 / 安装的结果：来源 mirror/github、稳定码、attempts，没发生过 = 引擎写 unknown）、正在编辑（没有就取选中）面板的 overrides 热态至多 3 张。
- **三个复选框**（`DiagnosticsSendDialog`，在折叠的「查看详情」里）：**默认不勾、不记忆**——每次打开（以及切项目）从全不勾开始；勾选变化 = 内容变了 = 推进 epoch 重新备包（旧包 `discardDiagSend`，新包换新 `client_request_id`），用 `keepForm` 让这次重备不清掉已填的类型与说明；备包中 / 发送中复选框 disabled。
  折叠里还有「包内文件（N 个）」逐项清单（路径 + 大小）与一句「因体积被截短」说明；默认可见仍只有一句话 + 发送 + 关闭。
- 文案键 `settings.diagnostics.send.{kind.detail_*, extra.*, extraHint.*, short.*, filesTitle, truncated}`；`short.*` 的键集 = `diagdetail.PRIORITY`，`extra.*` = `OPTION_KEYS`（`DiagnosticsSendDialog.test.tsx` 读 Python 源码对拍）。
- 看护：`diagnostics/detail.test.ts`、`DiagnosticsSendDialog.test.tsx`「诊断包扩充」一组。


## 意外错误入口（PR A，ADR 0118 补充 2026-10-14）

- **唯一汇聚点** `lib/unexpectedErrors.ts` + 纯内存 `store/unexpectedErrorStore.ts`。来源：`diagnostics/detail.ts` 的 `onUncaughtError`（window error / unhandledrejection 全应用只有那一份监听，**不得再装第二个**）、`ErrorBoundary.componentDidCatch`、`lib/api.ts` 的 `setEngine500Handler`（`status >= 500` 且 `code === 'internal_error'`，只认兜底码；回调注册制避免 api ↔ store 成环）。`main.tsx` 调 `installUnexpectedErrorCapture()`。
- **事件闭集**：`ui.unexpected_error { source, error_name, message_hash }`。`source` ∈ `UNEXPECTED_ERROR_SOURCES`，`error_name` ∈ `UNEXPECTED_ERROR_NAMES`（`types.ts`；后端 `_ENUM_FIELDS["source" / "error_name"]` 严格同源，`test_unexpected_error_enums_match_frontend` 对拍；字段名由 `test_event_fields_match_frontend_schema` 对拍）。**trace 里没有 message / stack 原文**，原文只在未捕获错误环（`recordUncaughtDetail('boundary' | 'engine_500', …)` 或 detail.ts 自己的 window 记录）随发送包走。
- **顺序固定**：分类（忽略的不记任何东西）→ 先 `recordDiagnosticEvent`（吞异常）→ 补环 → 仅当 `capability !== null` 且非 boundary 来源、发送对话框没开、栈不指回诊断模块，才进去重 / 上界 / 登记卡片。
- **分类按结构**：`ApiError`、原型不是内建错误原型的业务错误类、AbortError、`ResizeObserver loop…`、`Script error.`、pyodide / worker 来源、非 Error 的拒绝 → 忽略。**不准凭消息关键词判严重度**；要新增一类忽略，加结构判据与一条看护。
- **上界**：`MAX_FINGERPRINTS`（64，LRU）、`MAX_PROMPTS_PER_SESSION`（5）、轨上同时 1 张；指纹同一会话只提示一次，关掉后不再提示。
- **卡片**（2026-10-19 起，用户：「一键反馈只出现在胶囊内，很多人不知道点」）：**窗口右下角**的 `components/FeedbackCorner.tsx`（`data-feedback-corner`，`fixed` + `z-drawer`——在画布浮层之上、任何对话框的遮罩与对话框之下，哪个对话框开着都自然盖住它，Codex #1184；宽 320），不在通知轨里、不占轨的三条名额。
  `data-unexpected-error-card`：红色图标 + 标题（`unexpectedError.text`）+ 一句为什么（`.body`）+ 主按钮「发送问题反馈」+ ×；主动作只 `setOpen(true)`（并收起这张卡），prepare / start 调用次数为 0（`feedbackCorner.test.tsx`、e2e `diagnostics-send.spec.ts`「右下角反馈卡」）。
  **只认意外错误**（用户 10-19 裁决）：可预期的失败（用户脚本报错、导出失败）不弹这张卡，它们的入口仍在各自故障卡的折叠详情里。同时只有一张：意外错误在前，「上次没有正常退出」在它关掉后才出。点「发送问题反馈」两类一起收起（一份包已覆盖），确认框开着时整个角落不渲染（Codex #1184）。与准备引导卡（`PreparationCard`，画布右下）不叠：卡显示期间观察 `[data-prep-card]`，水平相交时按序挑一个整张都在窗口里的位置：上方 8px（放得下实测高度）→ 左侧 8px 贴底 → 都放不下就留在默认位置（宁可叠也不推出窗口）；属性栏收起时两张卡同在右下角（Codex #1184，e2e「不盖住准备引导卡」在 1024×768 / 1024×560 实量）。准备卡两种形态（`data-prep-card` 展开卡 / `data-prep-pill` 收起角标）都避让；观察面含准备卡的各级定位祖先（属性栏变窄时准备卡自己尺寸不变、是画布把它带走的）；e2e「属性栏变窄」量的是行为——它在不观察祖先时也绿，因为画布视口一变缩放读数就重渲染、MutationObserver 先触发了。
  App 两个分支（项目选择页 / 工作区）各挂一份，互斥。**绝不往工作面板的文档流里加提示条（`BannerStack` 已于 2026-10-16 删除）。**
- **ErrorBoundary 崩溃页**：「重新加载」仍是唯一主按钮；`SendReportButton`（secondary）在折叠「详情」里；崩溃页自己渲染 `DiagnosticsSendHost`（`TooltipProvider` + 内层 `SendHostBoundary`，对话框出错退回空）。**放宽的边界仅限 `diagSendStore` 与发送对话框**；关着时元素序列与改动前一致（`errorBoundary.test.tsx` 锚定）。**只有主应用入口 `main.tsx` 传 `<ErrorBoundary diagnostics>`**：playground / mcp 复用该边界但没有 Flask 诊断 API，默认不取能力说明、不渲染入口与对话框（测试锚定「不发 `/api/diagnostics/send`」）。
- 看护：`lib/unexpectedErrors.test.ts`、`store/unexpectedErrorStore.test.ts`、`diagnostics/privacy.test.ts`（新事件一组）、`components/feedbackCorner.test.tsx`、`components/errorBoundary.test.tsx`、`tests/test_diagnostics_bundle.py`。

## 退出相关入口（PR B，ADR 0129）

- **两张卡，纯内存 store** `store/exitNoticeStore.ts`，接线只在 `lib/exitNotices.ts`（`App` 挂载时 `initExitNotices(diagSendStore.load() 的 promise)`）。**绝不往工作面板的文档流里加提示条（`BannerStack` 已于 2026-10-16 删除）。**
- **修复卡**（`data-engine-exited-card`）：壳事件 `tavotto:engine-exited` / 回读 `getEngineExited()` 驱动；一句话「渲染服务意外退出了」+ 主按钮「重新启动」（`restartEngineFromCard()`：转圈期间幂等，壳没动手就把按钮还回去），折叠的一句「重启后可以发送问题反馈」只放在 `title`。**不受发送开关控制、不含发送入口**；在通知轨里从不让位。
- **「上次没有正常退出」卡**（`data-previous-exit-card`）：`diagSendStore.load()` 完成后、且 `capability !== null` 才 `takePreviousExit()`（取走即清空，关着时不取）；取到才登记。住在右下角 `FeedbackCorner`（同意外错误卡）：标题 + 一句为什么（`exitNotice.previous.body`）+ 主按钮「发送问题反馈」+ ×，主动作只 `setOpen(true)` 并收起这张卡，prepare / start 调用次数为 0。排在意外错误卡之后。
- **假重连**：`PrepContext.engineExited`（`exitNoticeStore.engineExit !== null`）为真时，准备卡断线一句用 `prep.line.engineStopped`（「渲染服务已停止」）而不是「连接断了，正在重连…」；其它原因断线保持原样。
- **`lib/desktop.ts` 是唯一桌面感知点**：`onEngineExited` / `getEngineExited` / `takePreviousExit` / `restartEngine`；浏览器 / 远程窗口 / try 一律 null / false / 空取消函数。事件名与两个闭集与 `src-tauri/src/{main,exit_marker}.rs` 严格同源（`tests/test_desktop_engine_exit.py`）。
- 看护：`store/exitNoticeStore.test.ts`、`lib/desktop.test.ts`、`lib/preparationText.test.ts`、`components/notificationRail.test.tsx`（「引擎退出修复卡」一组）、`components/feedbackCorner.test.tsx`。
