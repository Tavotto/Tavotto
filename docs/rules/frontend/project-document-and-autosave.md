# 项目、文档模型与自动保存

> 原文出自 `web/AGENTS.md`「项目系统与多画布（前端侧）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`web/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`lib/artifactValidation.test.ts`、`store/documentStore.test.ts`、`store/panelSourceSync.test.ts`、`hooks/useEngineSync.test.ts`、`lib/projectDocs.test.ts`、`store/projectDocOwnership.test.ts`、`e2e/cross-tab-paste.spec.ts`、`lib/autosave/diskWriter.test.ts`、`lib/timelineCheckpoint.test.ts`、`store/documentRestore.test.ts`、`store/restoreGate.test.ts`、`store/recovery.acceptance.test.ts`、`lib/recoveryCandidates.test.ts`
> `web/AGENTS.md`「改到」压缩前原文（2026-10-23）：`lib/session.ts`、`types/document.ts`、`documentStore` 的 `loadSeq` / `derivedSeq` / `registerTxnFinalizer`、`lib/autosave/diskWriter.ts`、`panelSourceSync.ts`、`assetStore`、`lib/projectDocs.ts`、`lib/docOwnership.ts`、恢复门与判决 `store/documentRestore.ts`、找回入口 `LayoutRecover` / `lib/recoveryCandidates.ts`、排版时间线（`lib/timelineCheckpoint.ts` / `VersionDialog.tsx`）
> `web/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效；计算项目格式按ADR-XXXX同步）：schema 3/4 只在持久化边界；外部派生唯一写入口 `applyDerivedUpdate()`；手势中不许写；文档 ready 只看恢复门、新写入口必须看门（ADR 0120）

<!-- agents-route
sheet: web/AGENTS.md
order: 200
改到: `lib/session.ts`、`types/document.ts`、`documentStore`、`lib/autosave/`、`assetStore`、`lib/projectDocs.ts`、`store/documentRestore.ts`、排版时间线（完整清单见细则）
主题: 项目、文档模型与自动保存 → `project-document-and-autosave.md`
必守要点: schema3/4仅在持久化边界；外部派生唯一写入口 `applyDerivedUpdate()`；手势中不许写；文档 ready 只看恢复门（ADR 0120）
-->

- 前端把 pj 存 **sessionStorage**（`lib/session.ts`，按标签页隔离，「不同
  标签页开不同图库」就是靠它）；`?pj=` 出现在地址栏时认下并立刻抹掉。
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些（按**此刻认领的** pj 判，不看 `project`：
  切项目的换代窗口里它还是旧值，#589）。后端语义见
  `src/tavotto/AGENTS.md`。
- **schema 3/4**：`ProjectDocument{project, canvases[], activeCanvasId}`；
  运行时激活画布仍是 schema 2 形状的 `documentStore.doc`（画布编辑代码零改动），
  持久化/读档统一走 `migrateToProject()`（接受 2/3/4）。画布切换换入换出
  undo 栈（canvasSessions）与 UI 会话（`store/canvasSession.ts`）。
  标签页 openTabs 按 documentId 存本机。legacy缺省仍保存schema3，v4显式保存schema4；versions按所选格式隔离，包安装另行验收，不能借旧package入口读写v4。
- **计算项目格式（ADR-XXXX）**：只有一个documentStore和对象模型，运行时`documentFormat: legacy | v4`，格式闭集消费后端`engine/documentformat.py`。首次出现`chart`或`chartSource`字段（包括坏状态）经同一入口升级为新working documentId，保留当前画布/对象身份、编辑及undo，不覆盖旧文件；事务内首次出现先延迟到成功结束，取消不升级。撤销计算编辑不把v4降回legacy。API路径、本机缓存、revision、owner、保存队列和projectFile键均消费`lib/documentFormat.ts`；省略格式参数时旧URL/键/调用形状不变。
- **计算项目包导出**：沿原TopBar项目菜单入口，完整深拷贝所有画布/附件/原序编辑。createPackage旧三参保持旧wire，显式v4固定pj并走真实v4端点，必须收到document_format='v4'，不降回旧接口。项目/格式/workingId/loadSeq/操作序任一变化后丢弃迟到成功或错误状态；v4 missing非空准确提示包已生成但需补齐资源，旧状态不改。不复用旧relink处理计算附件，也不把导出当作安装。看护`lib/packageApi.test.ts`、`components/topBarPackageExport.test.tsx`。
  v4响应有`execution_coverage=incomplete`时说明运行依赖未确认；与已知缺素材分别或合并提示，不把未知依赖算作缺素材、不因missing为空暗示可完整运行。旧后端/普通无脚本包没有该事实时保持原消息；事实只由后端提供。
- **计算项目包安装与采用**：TopBar与项目选择器的显式安装入口经`projectPackageInstallStore`协调原v4检视/安装API；
  安装回执只证明资源与配置阶段，不能当作正文已保存。关闭/项目换代仅退休当前界面归属，保已收到的资源回执。
  每次实际请求在await前分独立attemptId并记destination；网络/坏响应没有合法回执时保`completion_unknown`，
  原入口可重看该路径，不假报已发布、不自动重装/打开/Run。合法迟到回执只晋升对应记录，显式查看后再打开。
  旧稿经原writer保护后，初次采用只走原switchTransaction/adopt.prepareDocument/openDocumentAs；
  非空稿等原一次AS写入及revision/正文回读，空稿明确persistEmpty一次，不补第二writer/PUT或named票据。
  正文同步装入即记本次真实adopted scope；保存确认迟到或失败不等于未采用，重开不能重新套安装正文。
  迟到的已核保存事实只晋升原scope的记录，不让已关闭窗口重新认领当前界面。
  已保存的安装记录继续编辑时，同项目保当前稿与undo，跨项目沿原restore读目标最新稿，不能重套旧package正文；
  取消/ABA只使本次精确恢复token失权，不撤销另一个项目的token。配置检查仅在已采用项目当前认领时打开原project设置。
  看护：`lib/projectPackageApi.test.ts`、`lib/projectPackageEntry.test.ts`、`store/projectPackageInstallStore.test.ts`、
  `components/ProjectPackageInstallDialog.test.tsx`及原`store/workingDocumentReceipt.test.ts`。
- **附件保存回执**：`chart_sources`是五字段位置数组`{canvas_index,object_index,canvas_id,panel_id,chart_source}`。发送上下文捕获实际深拷贝正文、pj/workingId/loadSeq/format，响应只在同上下文按数组位置和原id核对后合入，并比较完整来源/模型/原序overrides/旧ref。重复位置整份拒合入；位置重排或请求后输入改变只跳过受影响项。不按可重复panelId建Map、不新增旧文档ID唯一约束。回执更新沿派生入口，不加undo、不触发循环autosave；正文成功但回执过期不伪造I/O失败。
- **跨格式冷发现**：两格式的显式GET `include_open_intent=1`须得到同一snapshot，才依共享latest自动恢复；有界重读后仍混代、stale或不可用则沿恢复门needs_user，不能按at/mtime选新旧。所有格式的明确打开/repair共用一条发送队列；自动确认用repair，只确认/晋升现意图而不分新sequence，superseded/unconfirmed不假ack。旧3含计算字段升级fork只在成功安装且令牌仍当前时记新v4意图一次。备选项以(format,id)寻址，不合并同id不同格式。
- **时间线**：仍只捕获并恢复当时活动画布；v4在持久化边界用单canvas项目封装，不从完整多画布项目猜一个覆盖当前画布。缺thumb按需获取该版本正文和校验附件回建PNG，列表草图不携带完整数据模型。命名文件绑定沿ADR0096/0108后端合同持久化，冷开从正式关联恢复，不能按标题猜目标；时间线正文、命名关联及项目包安装分别验收。
  看护：`lib/documentFormat.test.ts`、`lib/documentFormatApi.test.ts`、`lib/documentFormatNamespaces.test.ts`、`lib/chartSourceReceipts.test.ts`、`store/documentFormatPersistence.test.ts`、`store/documentRestore.v4.test.ts`、`store/projectSave.v4.test.ts`、`lib/timelineCheckpoint.v4.test.ts`、`components/LayoutDialog.v4.test.tsx`、`components/VersionDialog.v4.test.tsx`。
- **图幅记号 `PanelObject.figureFrame`（ADR 0098 §三）**：缺席 = 升级前放上排版的面板。读档
  （`migrateToProject`）、布局版本恢复（`restoreLayoutVersion`）与剪贴板粘贴（`materializePaste`，
  负载可能出自升级前的标签页）都经
  `lib/figureFrameMigration.migrateFigureFrames` 迁移：此刻样子来自引擎的（runtime、带图内修改的
  PDF）补一条 `figure.frame = "figsize"`、其余只打记号；新建面板的入口生来带记号。
  真正补入override时撤销原`figureSnapshot`，由原保存链冻结新状态；仅补记号、已有FRAME或已有记号时保留引用，不重签旧hash、不改原输入对象。看护`lib/figureFrame.test.ts`的schema2/3/4实际打包请求回归。
  标记放在面板上不放在项目上：面板会经检查点、粘贴、项目包在项目之间流动。切换到脚本的图幅只经
  `adoptScriptFrame`（内容在页面上不动的换算在 `lib/figureFrame.frameSwitchPatch`）。裁过的面板
  旧可见范围与脚本图幅不相交（含只剩一条边）时 `frameSwitchPatch` 返回 null、不采用，属性里的
  按钮禁用并说明原因——判据是换算出的 crop 过不过 `cropInBounds`（与 `rendercore/ir._crop` 同源对），
  越界的 crop 会让 RenderCore 拒掉整份排版（#688）。
- 文档模型可选字段（schema 仍为 2，旧文档兼容）：
  `PanelObject.lockedGids / flipH / flipV`、`ObjectBase.layoutPinned`、
  `FigureDocument.layoutGroups`（行/列/网格约束，id 即 groupId，
  尺寸变化自动重排）。**自动重排只跟用户编辑**（`startLayoutAutoReflow`，按
  `historyMove` 在栈上认：新条目 = 编辑，条目在 past / future 间挪格 = 撤销 / 重做，
  `loadSeq` / 画布换了 = 换文档）：撤销 / 重做 / 载入 / 切画布之后、下一次用户编辑
  之前，文档停在历史的某一格上，这期间不进历史的写入（渲染同步 silent 补图幅、文字
  自适应高度）只记尺寸不重排——重排是一条 commit，会清空 future、给打开的文档添
  一条用户没做过的历史。紧跟在用户编辑之后的派生同步（改图幅 override → 渲染回来
  → 补图幅）照常重排。事务松手（`endTxn` 不换 doc 引用）从栈上认，不看 doc；
  收尾修正在事务还开着时写，与松手那条是同一次编辑，不单独触发。防抖窗口里被撤销作废的
  重排锚在作废那一刻最新的一条历史上（按条目身份），重做一路放回到那一条时补上；
  只重做到更早的一条不补——补排是 commit，会冲掉 future 里的新条目。
- **撤销防线（2026-08-17，数据损坏级）**：`txnUpdate` 在无事务时**丢弃更新**
  ——绝不静默直写 doc（拖动中事务被外部 endTxn/undo 结束后，pointermove 落进
  静默分支 = 位移绕过历史、撤销永远找不回，真实用户撞见过）。一切撤销入口
  （键盘 / 顶栏按钮 / 桌面菜单加速键）必须走 `runUndoRedo`（带
  undoRedoBlocked 守卫）；undo/redo 的 applyPatches 有 try/catch，坏补丁丢弃
  该条而不是让栈与文档错位。
- **事务收尾修正（2026-09-24，#543 评审）**：`documentStore.registerTxnFinalizer(fn)`
  登记一个 recipe；`endTxn` 在把事务压成**一条历史之前**依次跑一遍登记过的修正，
  改动并进这条事务（与手势本身同一条撤销记录）。**丢弃的事务不跑**（`discard` 或
  没有补丁）：回滚后由派生方自己 silent 补。它给「手势进行中不许写、手势结束时
  必须和手势一起进历史」的派生值用——中途写不行，缩放 / 裁剪 / 属性栏数值拖动都在
  按下时抓了几何、每帧按它写绝对的 w/h，会把中途的派生修正盖回去；手势后 silent
  写也不行，不在这条历史里，撤销 / 重做 / 取消就不自洽。第一个使用者是
  `useEngineSync` 的**原生图幅同步**：事务开着时一个字都不写（`txnOpen` 在 effect
  依赖里，空事务回滚不换 doc 也能补上），收尾时按同一比例把 w/h 换到新图幅
  （换算只在 `lib/panelNativeSize.ts` 的 `syncPanelNativeSize` 一处，silent 同步、
  收尾修正、撤销 / 重做的换基三处共用）；撤销整体回到手势前的旧图幅（同步器随即
  silent 补一次），重做回到松手那一刻。
  **锚点是这一处换算的参数**：手势开事务后用 `setTxnAnchor(id, 页面点)` 登记它刻意
  钉住的那一点（缩放 = 被拖手柄的对侧手柄：角柄取对角，边柄取对边的中点——
  另一轴取中点，否则两轴图幅同时变时对边手柄会漂；裁剪 = 未裁剪整图的中心），收尾修正用
  `txnAnchorOf(id)` 读出来传给换算；换算先算新的 w/h，再让包围盒绕锚点按同一对比例
  伸缩反推 x/y——**只有这一处写 x/y**，别处不另写位置修正。没登记（数值拖动、
  事务外的 silent 同步）= 默认锚点左上角，x/y 不动。锚点只活在当前事务里：开新事务、
  事务结束（含丢弃）都清空。
  **事务之外的图幅变化不进历史，已有条目靠自己记的基准换算**：补丁里的 w/h 是按
  当时的原生图幅量的绝对值，图幅在历史之外被 silent 改过之后原样打回去，缩放比就错了
  （同一变体键、同一 manifest，同步器不会再补）。所以每条改过面板 w/h 的历史记下前后
  两侧的原生图幅与包围盒值（`HistoryEntry.sizeBasis`，运行时状态、不进文档；`after` 一侧取在
  **收尾修正跑完之后**，并进来的新图幅算在松手那一刻），撤销 / 重做落地后把打回来的
  w/h 从那一侧的基准换算到此刻的图幅，缩放比 = 条目那一侧的缩放比。条目自己改过
  图幅时（收尾修正）补丁连图幅一起打回，系数为 1。**只换条目打回的那几维**：按落进栈的
  （压缩后的）补丁里**实际存在**的路径认（`writtenPageDims`；中途动过、松手回到原值的 h
  仍留着 replace，前后值相等也算），外加旋转改了宽高与原生轴的对应（0↔90）时两维都算（正方形转 90° 的 w/h 数值
  不变、补丁里没有，也要按条目那一侧重摆）；没打回的那一维从没离开此刻的单位，再乘一遍
  就翻倍两次。换基的锚点是默认的左上角（打回来的 x/y 原样）——与事务外 silent 同步同一个
  锚点，重做那一侧正是它换算过的状态，逐位回得去。撤销 / 重做仍各只 `set` 一次，
  不多出历史条目。
  **已知代价**：渲染在手势中途回来时，画布上的框到松手那一刻才换比例（中途仍按
  旧图幅的纵横比）。看护：`hooks/useEngineSync.test.ts` 的「几何事务进行中收到
  改了图幅的渲染」「渲染到达之后又有拖动帧」「事务之外改了图幅：撤销 / 重做回到
  用户设定的缩放比」三组（前两组由真实 `startResizeDrag` / `startCropDrag` 驱动，
  含西 / 北边柄单轴与两轴图幅变化、裁剪整图锚点），以及 `lib/panelNativeSize.test.ts`
  （补丁路径认维度）。
- **override 的目标身份（2026-09-25，ADR 0083）**：`PanelOverride.identity`（可选，不升
  schema）由 `documentStore.commit / txnUpdate` 在 recipe 之后跑一遍登记的抄写
  （`registerOverrideStamper`，`useEngineSync` 登记，抄写本身是纯函数
  `lib/overrideIdentity.stampOverrideIdentities`），补丁并进同一条历史 / 事务。只给**新写或
  改了值**的条目抄**提交之前用户看着的那一版** manifest（`panelRender`）里的身份；同值被重建丢了
  身份的放回原来的。写入点一律不手填。**恢复的来源显式传**：从存储原样读回来的（布局版本
  `restoreLayoutVersion`、写回历史 `restorePanelOverrides`、写回基线 / 换素材 / 重链接）一律
  `commit(…, { overrides: 'restored' })`，整次不抄——不从「身份等不等」推断是不是恢复
  （同一身份、值不同的恢复与原地改值长得一样，#602 评审 P1）。新增一条恢复路径同样要带。
  「清除失效修改」把身份对不上的也算进去（`isStaleIdentityOverride`，与引擎同一条判据）。
- **自动保存**：磁盘为主（`PUT /api/autosave/<docId>` 原子写
  `layouts/_autosave/`），localStorage 只留索引 + 崩溃兜底副本
  （成功仅清同一快照，#646；内容不同的副本保留待裁决，忽略更新时间与键序，旧格式比较迁移前原稿）。桌面兜底恢复取决于
  origin 稳定（ADR 0108：壳记端口；端口被占的那次仍读不到）。失败发
  `tavotto:autosave-error` 事件 → 常驻错误 toast。**怎样安全地落到磁盘上**——按文档
  排队、串行 PUT、乐观并发基线（updatedAt）与外部修改基线（内容 hash 三档 + 缺席）、
  写前确认、409 不推基线、冲突挡住排队那份——2026-09-18 起住在
  `lib/autosave/diskWriter.ts`（`createDiskWriter(ports)`，不认识 store、不碰 window /
  localStorage / 遥测），`documentStore` 只装配端口；用例 `lib/autosave/diskWriter.test.ts`
  用假端口 + 手动 gate 直接量时序。
- **立即冲刷与防抖共用捕获**：`flushAutosave`在同步捕获前消费已被本次正文覆盖的旧timer；不能在异步写盘回执后清timer，否则会吞掉捕获后的真实新编辑。安装等待项目文件元信息时，初次冲刷与期间到期的防抖共用原`deferredProjectFileFlushes`，hydration完成后捕获最新正文一次；无项目保原立即冲刷。失败/409仍保本机稿与原重试/裁决门，不靠残留timer重试。看护`store/documentFlushDebounce.test.tsx`（真实TextView测量、慢元信息、捕获后编辑、失败/冲突及无项目路径）。
- **保存失败的原因**：`SaveIssue`的`stale/external`仍触发原冲突裁决；有结构化code的其他后端拒绝为`backend`，只在内存保留status/code/params/fallback，经`saveIssueMessage`消费既有`backendCodeMsg`描述符，状态与App通知/无项目保存共用同一翻译器。422来源校验失败不能被归为磁盘空间不足；真正无结构I/O或网络错误才用原通用提示。错误不进可携正文、本机恢复副本或遥测，不保存整个响应体；未知code保既有fallback。失败不推进基线、不清本机副本、不覆盖原文件。看护`lib/autosave/diskWriter.errors.test.ts`、`store/saveFailures.test.ts`、`App.pickerNotices.test.tsx`。
- **⌘S 保存到项目（ADR 0096，2026-11-06 修订）**：排版可以**绑定**项目里的一个文件
  （后端会话按项目/格式/workingId持久化，`lib/projectFile.ts`消费内存镜像，不进文档）——从「项目里的
  排版」打开的、存进项目的（「存进项目」与开着项目时的另存为）都绑定。`runManualSave` 三条路：
  绑定了当前项目的文件 → 先 `saveNow()` 再 `store/projectSave.writeBoundProjectFile()`
  （`target=project`，基线是**绑定里的修订号**：这份工作副本基于项目文件的哪一版，刷新后照样成立，
  与只活一个窗口的 `lib/layoutRevision.ts` 不是同一个量）；开着项目没绑定 → 弹 `saveToProject`
  命名框（预填**排版名** `projectMeta.name`，不是画布名）；没开项目 → 只存本机、说「存在本机」。
  保存键在捕获阶段认领，富文本框不能吞掉；保存前先让聚焦的富文本框冲刷组字中的内容，
  再经手势收尾提交画布就地文字，最后取保存快照（系统菜单的闸门之前也先冲刷）。
  **富文本框带着焦点被卸载**（点画布空白取消选择 / 退出就地编辑，浏览器的 blur 来不及触发）时，
  `RichTextField` 的卸载清理补一次 `onBlur`（冲刷待写内容）：宿主在 `onFocus` 开的 `history.editText` 事务因此一定被收掉，
  不会悬着并进下一个离散动作（QA-WIN-14 / QA-WIN-28；看护 `RichTextField.unmount.test.tsx`、`TextView.test.tsx`）。
  本机那一份 `conflict` 时不写项目文件。绑定只认当前项目（`bindingForProject`），别的项目的
  绑定不抹掉。**自动保存仍只写本机**；绑定里的 `dirty` 是项目文件那根轴，只跟用户编辑（与
  `saveState` 推 `dirty` 同一处，外加改排版名），派生同步不置位，不进关闭保护。409 `external_change`
  带着冲突打开命名框（`uiStore.layoutConflict`），出口与另存为同一个。写回**串行、按排版合并**（全局至多一次在路上；
  同一份排版还没开始的那次才并进去，别的排版自成一项；排着的那次现读绑定；轮到时换了排版就不写、
  并报 `save.projectSkipped`——显式保存没写成必须说出来）；修订号按后端回的规范名记
  （`rememberLayoutName`，前端不复刻净化规则）；**保存上下文**（`store/saveContext.ts`，ADR 0096 §四之四）：
  ⌘S / 另存为 / 打开在入口 `captureSaveContext()` 一次，链上每个 await 之后作用于此刻界面的事先过
  `stillCurrent(ctx)`，否则报「没写成 / 没打开」；新增 await 点要登记进那张清单并进参数化用例；
  写成之后判「途中改没改过」（`projectFileSnapshot()`）比的是**被提交那份排版自己的编辑代次**，
  不拿此刻开着的文档去比；代次与上面的 `dirty` 同源（只在 `markProjectFileDirty()` 里前进，
  派生同步 / 载入 / 换文档不算），不另写判据；对话框 await 之后的界面变更一律
  `ifStillCurrent(ctx, …)`，切走了改在状态条上说；「仍然覆盖」写冲突那份文件、不改文档标题；
  当前保存链消费已核后端视图的内存镜像；仅明确404旧后端沿旧会话/localStorage兼容链，503/未知不降级。
  保存链不许只读持久副本；await 之后的落账（修订号缓存、绑定、冲突岔口）一律记在
  **发请求那一刻**的 pj 名下（`rememberLayoutRevision(name, rev, pj)`），切走之后不开冲突岔口。
- **保存关联与明确冲突恢复**：`projectFileSession`每个活跃编辑器用独立内存actor；只有真实用户编辑推进轴，派生回执不推进。初次打开先在hydrate屏障捕获无编辑基线/票据，等候期间的用户编辑仍保dirty；named正文成功但元信息未确认只补confirm，不重写旧正文。绑定代次和`loadSeq`参与上下文，A→B→A或同ID重装挡旧回包；冷恢复不读取新的named revision洗白外改。后端wire唯一合同见[文档落盘规则](../backend/layout-versions-and-documents.md)。
  只有用户明确覆盖/重载才创建新branch，并把观察到的实际AS修订与完整会话视图固定到同一个`DiskSaveContext`，排入原writer；普通队列或重试不能刷新这份证明。读取、确认和落账守项目/格式/workingId/loadSeq/绑定及操作意图。重载安装前把**最新内存稿**写入原本机副本并提升原恢复槽；任一步失败停止安装、保留内存与磁盘，此不变量也适用于404兼容路径。看护`lib/projectFileSession.test.ts`、`lib/projectFileApi.test.ts`、`lib/autosave/diskWriter.projectFileRecovery.test.ts`、`store/projectFilePersistence.test.ts`和`components/documentBanner.test.tsx`。
- **实际工作副本回执**：`waitForWorkingDocumentSave`只等待原writer并回读实际AS修订/正文，核项目/格式/workingId/loadSeq与已成功capture；它和named保存共用同一回执读取器，不预留named票据。非空安装稿不补写第二次PUT；只有明确`persistEmpty:true`才为本安装尚无成功记录的空稿排一次原writer写入，普通空稿保存语义不变。`openDocumentAs`成功或空稿`wrote:true`均不能代替真实落盘回执；失败/旧记录/外改/元信息未确认不能报告安装完成。看护`store/workingDocumentReceipt.test.ts`；该叶子不表示项目包安装入口已接通。
  **重开同一个项目文件沿用 documentId**（QA-WIN-46，ADR 0096 修订 2026-11-04）：`openLayoutDocument` 带项目文件绑定时，`findDocumentsForProjectFile` 按最近绑定排序，取第一个没在别的窗口开着（`isDocHeldElsewhere`）、且不 `dirty` 的 documentId 沿用（版本时间线按 documentId 分存，新建身份会让命名节点看不到），并 `adoptSlotBaseline` 记下旧槽位的磁盘基线免得首次自动保存误报外部修改；`dirty` 的旧绑定不沿用（槽位里有项目文件没有的编辑）；另存为把当前排版改绑新文件，原文件不再有绑定，副本不继承历史。看护 `store/openProjectFileIdentity.test.ts`。
- **显式运行完成与恢复门（SVG 快照）**：`figureSnapshotInvalidation` 只接收入口已按项目代际接受的成功执行事实；
  尚未接纳的旧回包仍由原入口拒绝。恢复中 / 待用户处理、以及尚未真正初始化的 `initial-ready` 窗口内，
  只保留匹配来源所需的最小字段，按稳定项目 id + source / runtime asset id 覆盖去重，不保留报告或参数。
  同 runtime asset 的新捕获只覆盖 descriptor；每次明确观察到的 original artifact 另按精确 source 累计，复用 `capturedFigureSources` 的路径规范化，不扩大配置图的 stem 匹配。
  已接受但未消费的 A 项目事实跨 A → B → A 暂留，B 文档不得消费；恢复重试 / 等待超过 readiness 超时也不丢。
  真正 ready 且正文安装完成后，才用恢复文档的全部画布与历史重算静态同 stem / 不同格式的来源，退役旧引用并走正常 dirty / 自动保存。
  `supersedeRestore` 先宣布 ready 后安装正文，因此在稳定微任务点核对 gate 的 docId；`installingUnderToken` 不算恢复完成。
  首次真实恢复消费后即删除，不永久重放到用户之后主动打开的历史文档。临时登记随应用生命周期释放；不以静默容量淘汰丢失执行事实。
  native 图放置的旧回调仍守其原项目代际，不能因保留执行事实而在另一个项目插图；执行事实只登记一次。
- **启动恢复判决（2026-10-11，ADR 0120，替代下面 2026-09-26 那条的前两道判断）**：「项目 ready」与「文档 ready」分开。
  documentStore 的恢复门（`restoring` / `needs_user` / `ready`，带 `gen` + `pj` 令牌）是**唯一**的「文档准备好了没有」：
  - 门关着时内存里是**占位文档**：`commit` / 事务 / 撤销重做 / 画布与标签增删改 / `renameProject` / `applyDerivedUpdate` 一律拒，
    `flushAutosave` 回 `'skipped'`，`switchDocument(…, { placeholder: true })` 不写 currentDoc / docIndex / 归属；remember-last 订阅、
    时间线自动节点与关键时刻都看门。**新增一个文档写入口必须同时看门**（看护 `store/restoreGate.test.ts` 的入口遍历）。
  - **离开旧文档的冲刷在设门之前**（`resetForNewProject` 第一步），DiskWriter 已排的旧文档写入不清。
  - 只有持当前令牌的安装能在门关着时写（`installRestoredDocument`）；没有令牌的普通 `switchDocument` = 显式意图 = **取代**（gen+1），
    旧恢复晚到的结果没有提交权。教程 `prepareDocument` 返回 `Promise<boolean>`，以返回值 + 门被取代双重确认，resolve 不等于装好。
  - 冷启动（`restoreSession`）、Picker（`adoptSteps`）、重试走同一个 `store/documentRestore.resolveProjectDocument`；线索（`captureClues`：全局
    currentDoc、内存 id、skip-restore 的一次性决定）在任何 reset 之前捕获。`restoreSession` 不再按 `currentDoc === documentId` 短路，
    门已对当前项目落定就回落定结果；同项目在路上的恢复（StrictMode 第二轮）加入它。
  - 候选与读取结果见 `lib/projectDocs.planRestore`（只读）与 `documentStore.probeDocument`（零副作用）/ `acceptProbe`（选中且仍有提交权
    才记修订号、基线、挪恢复副本、推回磁盘）。`readAutosaveDoc` = 两者组合，只给显式打开用。读不到（`unavailable`）≠ 没有（`missing`）≠
    坏（`invalid`）≠ 太新；暂态先有界重试原目标，首选明确失败 → `needs_user`，只列只读验证过的其他候选，**不悄悄换旧稿**；
    纯打开意图（没有后端 durable 记录）没有正文 → 退回 durable 那份，不算故障。**读不到不自动退回本机**：会话端点重试后仍不可用、或正文读失败（非 404）→ `needs_user`，本机线索 / 本机副本只作用户明确选择的候选；只有确认的 404 才自动按本机恢复。每条安装路径都经 `installChecked`：读到的正文 revision 与指针 / 扫描 / 提供候选时记下的不同，或指针曾明确缺席而正文出现 → 先问 `/owner`，只有 owners+verified 且 revision 对得上才装；备选探测不能用新 revision 抹掉缺席证据。C1 校正的本机归属须在安排本机副本首次写盘之前完成。判决表全文在 `documentRestore.ts` 文件头与 ADR 0120 §二。
    恢复期间排队的用户加图 / 粘贴操作跨同项目的「重试」保留，直到成功落定执行一次；显式新建、打开其他文档与切项目仍取代它。加图返回 `null` 时 UI 不报「已添加」、不关闭接入中心。
  - **用户的动作不丢（ADR 0120 修订 2026-11-10）**：用户级入口（加图 / 拖放 / 粘贴 / 进入编辑）一律先过 `deferUserEntry`，门关着就记住、
    文档定下来之后执行一次，只回 `run` / `deferred`——**不许新增「拒掉并说一句」的入口**。`needs_user` 期间用户动了手（含底层写入被拒）走
    `resolveHeldIssue`：没有别的排版可接 → 静默继续新建并说一句；暂时读不到 → 自动再试一次；其余等用户选。绝不替用户挑旧稿。
    外部交接落图前等 `whenDocumentSettledForEntry`（同一套），不直接等 `whenDocumentReady`。
  - `needs_user` 的出口在**空画布正中的恢复卡**（`CanvasStage` 的 `data-canvas-restore-issue`，此时不摆「添加图」/ 选图卡）与顶栏文档状态芯片
    （`restore` issue），两处同一个 `components/RestoreIssueBody.tsx`：一句话 + 一个主按钮（重试 / 打开另一份 /
    继续新建），其余折叠；`lastDocumentIssue` 只是 `restoreIssue.ref` 的薄适配。保存成功（`saved → clean`）不清恢复问题；保存元数据
    失败（autosave 回 `metadata:"failed"`）是另一件事 `restoreMetaIssue`，与 `saveState` 正交。
  - 外部交接：`queueNative()` 不等文档；落图 / Figure 选择器先 `whenDocumentReady(pj)`，被取代 → 不落、超时 120 s → `deferred`。
  - 看护：`store/documentRestore.test.ts`、`store/restoreGate.test.ts`、`lib/openRequestRestore.test.ts`、`components/topBarRestore.test.tsx`、
    `store/recovery.acceptance.test.ts`（审计夹具转成的正确行为验收，在 `e090923b4` 上必红）。
- **保存成功驱动 last（2026-10-11，ADR 0120 §三）**：后端 last 只指向有有效正文的槽位，没正文的打开意图只记 `pending`、正文首次落盘时在同一
  文档锁里晋升。前端 `DiskWriterPorts.onWritten(id, savedAt, { pj, metadata })` 的 pj 取**排队那一刻**的值；`afterWriteOk` 的 UI 一半仍按
  `isCurrentDoc` 守，元数据一半无论当前与否都经 `onDocumentPersisted` 交给 `projectDocs.notifyDocumentPersisted`（缓存意图 == 这份且上次推送
  pending / 失败才重推，不被同值去重挡住）。`loadProjectDocument` 不再在验证正文之前把本机引用推成后端 last（迁移在 `confirmRestored` 里、接受之后）。
  C1：本机索引说别的项目、后端 `/owner` 回 `owners` 证据且 `verified` → 可纠正本机索引；只有 assets / versions → 不翻案；问不到 → 不认领。
- **找回本机排版（2026-10-11，ADR 0120 §六 / ADR 0108 §四）**：「打开排版」对话框的 `components/LayoutRecover.tsx`（逻辑在 `lib/recoveryCandidates.ts`）
  两组：本项目的（原 id 打开）、折叠的归属待确认（默认**恢复副本**：固定目标 id、确认时重比 revision、经 `actions.openDocumentAs` + 正常保存链建立归属，
  源槽位与源归属不动，取消零写入）。不为列表运行脚本、不编缩略图。看护 `lib/recoveryCandidates.test.ts`、`components/LayoutDialog.recover.test.tsx`。
- **启动恢复不覆盖已经装好的文档（2026-09-26；前两道判断已被上面「启动恢复判决」替代，保护本身由恢复门兑现）**：工作台挂载时 `restoreSession()` 读
  「当前项目的 last（后端）→ `tavotto.currentDoc`」那一份（2026-09-29 起，见下条「会话状态以后端为准」）——但教程 / 切项目的 `prepareDocument` 往往**挂载前**就把它装好了，
  这时再读盘整份替换，读到的是用户第一次编辑还没落盘时的旧一版（慢机器上教程一打开就拖，拖动被盖回去，
  windows-exe-smoke 撞到过）。两道判断：`documentId` 已经是它就不读盘（本机 `currentDoc` 就指着内存里这份时
  连后端都不问）；问后端 / 读盘在路上时
  `persistedIdentity()`（`buildProject` 读的每一片 + `documentId` / `loadSeq`，**它多读一片这里就多加一片**）
  任一变了就让位——身份在**第一个 await 之前**取。启动时的 `documentId` 是随机新 id，刷新后恢复不受影响。
  **读盘带回来的待裁决事项（恢复副本 / schema 太新）每条路径恰好说一次**：`readAutosaveDoc` 的调用方只有
  `loadAutosavedDocument`（教程装文档、切项目接回上次文档，切换之后挂 `docNotice`——`switchDocument` 会清掉它）、
  `restoreSession`、`openRecentDocument` 三个，都把 `notice` 挂上。看护 `store/documentStore.test.ts`、
  `store/projectStore.lastDocument.test.ts`、`lib/onboarding/tutorial.test.ts` + `e2e/tutorial.spec.ts`「教程刚打开就拖」
  （把那次读盘扣到拖动之后再放）。
- **`doc` / `canvases` 的变化有三种性质**，`startAutosave` 的订阅按两个代次
  区分，**改这段之前先想清楚新写入属于哪一档**：

  | 性质 | 判据 | `dirty` | `saveState` | 撤销历史 | 落盘 |
  | --- | --- | --- | --- | --- | --- |
  | 载入 | `loadSeq` 变了 | 由载入方声明 | 由载入方声明 | 清空 | 不排队 |
  | 用户编辑 | 两个代次都没变 | 置位 | 推成 `dirty` | 进 | 排队 |
  | 外部派生同步 | `derivedSeq` 变了 | 置位 | **不动** | **不进** | 排队 |

  第三档的唯一写入口是 `documentStore.applyDerivedUpdate()`，唯一调用方是
  `store/panelSourceSync.ts`。「不动 `saveState`」是因为一次外部文件改动不是
  用户的编辑（`hasUnsavedWork()` 读的正是它，推了会让关闭保护拦一件用户没做
  过的事）；「照样排队落盘」是因为 `script` 是**存进文档的字段**，只改内存的话
  下次打开面板又回到不可编辑。写盘本身照常走状态机，`save_error` 一个不吞。
- **外部修改 → 画布的闭环只有一条路径**（Prompt 06）：SSE 事件、素材面板的
  「刷新项目」按钮、SSE 重连恢复，三个入口都走 `store/liveSync.ts` 的
  `refreshAssetsAndSync()`。合并做在两层——`assetStore.load()` 复用同项目的
  在途请求（一批事件一个 `/api/panels`），`syncPanelSourceMetadata()` 无差异
  零改动（并不成一个请求的那些也不会重复置 dirty / 重复弹提示）。
  `assetStore` 的并发与换代纪律（请求序号、发请求那一刻的 pj、项目代际、同项目失败不清空、
  `force`）全文只在 `asset-library.md`「`assetStore` 的清单纪律」一条，这里不留第二份。
- **派生字段 vs 用户数据**（`panelSourceSync.ts` 的表）：只有
  `script` / `cost` / `fileKind` / `pxW` 由 `/api/panels` 说了算；
  几何、`nativeW/nativeH`、crop、rotation、overrides、成组、锁定、选择一律
  不碰。**图幅不是派生字段**——它是几何（`useEngineSync` 盯着它按同一比例调 `w/h`，事务中推迟到收尾），
  而且权威在这个变体自己渲染回来的 manifest 上，不在磁盘文件上。runtime 面板
  整个跳过（`runtime:` 前缀的 id 永远不在 `/api/panels` 里）。
  **素材不在清单里 ≠ 脚本关系失效**：前者只记 `missing`、对象一个字节不动
  （网盘抖一下不该让一批面板永久失去编辑入口），后者才降级并清掉失效的
  manifest / 渲染缓存（`renderStore.reset`）——只置 `script = null` 是不够的，
  留着的 manifest 会让元素树与检查器继续按"可参数化"办事。
- **切项目回到那个项目上次开着的文档（2026-09-06，审计 T02）**：`lib/projectDocs.ts`
  按项目 id 记最近一份**有内容**的 documentId（2026-09-29 起权威在后端、`tavotto.projectDoc.<pj>` 只当缓存，见上条「会话状态以后端为准」；
  空白文档不记——它从不落盘），`projectStore.adoptOpenedProject` 在换代之后按记录
  读自动保存槽位换回去；读不回来时 `lastDocumentIssue` → 顶栏文档状态芯片（`TopBar.SaveStateLabel` 的说明块，2026-10-07 起不再是横幅）指名那份
  文档并给「打开上次文档」重试，**不静默留一份空白**。带 `prepareDocument` 的入口
  （教程）不走这条。记录的键取 `currentProjectId()` 而不是 `project` 字段：换代期间
  后者还是旧项目。Project Picker 的同名区分 / 失效分组 / 筛选判据只在
  `lib/recentProjects.ts` 一份，顶栏项目切换器共用。
- **会话状态以后端为准（2026-09-29，#715 PR-B，ADR 0108（`docs/adr/0108-desktop-stable-origin-and-backend-session-state.md`）§二—§三）**：桌面版每次启动 sidecar 可能换端口，
  localStorage 按 origin 隔离——换了端口就是一份空存储，「上次打开的排版」与导出默认值随之丢失（#715）。
  - **「这个项目上次开着哪份排版」的权威在后端**：`GET /api/layout-session`（按 pj 认项目，回 `{last}`）、
    `PUT /api/layout-session/last`（`{doc_id, name}`），数据目录 `state/layout-sessions.json`
    （`engine/layoutsession.py`，后端细则见 `layout-versions-and-documents.md`）。前端入口只在
    `lib/projectDocs.ts`：`fetchRemoteProjectDocument(pj)` 三种结局分开——记着的那份 / `null`（后端有这组端点、
    这个项目没记过）/ `undefined`（404：playground、嵌入画布、旧后端，或此刻不可达）。**「不知道」不折成
    「没记过」**：`undefined` 时退回本机的旧逻辑。`tavotto.projectDoc.<pj>` 只当缓存；后端 `null` 而本机有
    = 同一个 origin 升级上来的旧记录，拿它当迁移源并推一份上去。
  - 取哪一份（**2026-10-11 起由 `planRestore` 的候选计划替代**：后端 `unavailable` 不再折成「本机旧逻辑」而是先重试，旧端点 404 才走兼容；见上「启动恢复判决」）：`restoreSession` = `loadProjectDocument(pj)`（当前项目的 last：后端 → 本机按项目的缓存）→ 旧的
    `tavotto.currentDoc`（**只在确知属于当前项目时**：本机索引记着，或后端给得出证据）；`adoptNow` = `loadProjectDocument(pj)`。
    `lastDocumentIssue` / 顶栏文档状态芯片机制不变。
  - **一个项目的排版不许漏进另一个项目（2026-10-01，#715 Windows 真机验收 P1）**：稳定端口之后先后打开的项目共用
    一个 origin，`currentDoc` / `docIndex` 这类全局键跨项目存活。改造前「后端没记过」（`null`）时 `restoreSession`
    退回全局 `currentDoc`，关掉 F 再打开新项目 G，G 一打开就是 F 的排版，随后记成 G 的 last、往 G 的目录打时间线节点。
    判据唯一出处 `lib/docOwnership.ts`（`isForeignDocument` / `recordedProjectOf`，按本机「最近文档」索引里写下那一刻的
    `projectId`；「不知道」不折成任何一边）。消费方：`restoreSession` 的 `currentDoc` 退路要正面证据（本机索引记着 `=== pj`，或下面的后端证据）；
    **本机索引不知道归属**（T04 之前的条目没有 projectId / 索引里没有这条，升级上来的用户都是这样）时
    `restoreSession` 不猜，问后端要证据（`GET /api/layout-session/owner`：槽位归属是定论 → 当前项目的
    `tavottofile/versions/<id>.json` → 面板素材全在当前项目里），有证据才恢复并当场把归属补进本机索引，
    没有或问不到就不恢复；后端没有这个端点（404：playground / 旧后端）时照旧认（#773）。
    `projectDocs` 的本机缓存（含待确认那条、迁移源）与后端回的 last 确知属于别的项目就不认、并作废缓存；迁移推送
    **等后端裁决**，后端回 `409 layout_foreign`（`engine/layoutsession.owner_conflict`，纵深那一道）就不恢复、不重推；
    「记上次开着哪份」的订阅不记别的项目的排版。`currentDoc` 不改成按项目分键：按项目的那一份就是 `projectDoc.<pj>`，
    `currentDoc` 只剩「没开项目」与旧数据的退路，给它加归属判据比再造一套键、再迁一遍旧数据省事也更不容易漏。
    **归属只随用户的动作转移**（#773 Codex P2）：显式从「最近文档」打开别的项目的排版仍然允许（列表里标着所属项目，
    审计 T04），但「打开 = 冲刷一次」不算带进当前项目——`switchDocument` 换进来时归属（`docProject`）保留在原项目，
    真改一笔（自动保存订阅的用户编辑档，派生同步不算）、⌘S 或改名才转到**打开时**所在的项目（`pendingClaim`）；
    转的那一刻当场改记本机索引并经 `onDocumentClaimed` 记成这个项目「上次开着的」（只改一笔就离开，后面没有
    下一次变化去触发记录）。
    写盘（`PUT /api/autosave`）一律按**这份排版所属的项目**发 pj（后端按它记槽位归属），不现问 `currentProjectId()`；
    读盘时把本机副本推回磁盘同理（`ownerProjectForWrite`）。切项目先认领新项目、再冲刷旧文档：认领之前
    `pinDocumentOwner()` 把还没换过的初始文档钉在旧项目上，否则离开那一下会把旧项目的排版记成新项目的。
  - **记录时机不变**：`projectStore` 的那个订阅（documentId 或名字变、且排版有内容才记，与缓存同值不写），
    `rememberProjectDocument` 同时写缓存与后端；推给后端的写入串行（后发的必须后到），读之前先等它排空；
    回过 404 就在本模块实例里不再推。没认领项目（pj 为空）时不记。**切项目期间停记**：`adoptNow` 从认领新 pj
    到 `resetForNewProject()` 完成之间要 await 一次后端，内存里还是上一个项目的文档，这段时间订阅不记（否则会把
    旧项目的文档记到新项目名下）。
  - **推失败的写入不丢**：记录时本机缓存先带 `pendingBase`（写下那一刻本机见过的**后端**最新 `last.at`，即缓存里的
    `seenAt`）与 `pendingGen`（这次写入的唯一标识，只用来认「后端确认的是不是这一次」），后端确认后才摘掉；推失败（非 404）
    就留着。下次读时后端那条 `last.at` 不晚于 `pendingBase`（写下之后后端没收过别的写；或后端没记过）→ 本机为准并重推，
    否则后端为准。**只比后端自己的钟**：连着远程实例时浏览器与服务器是两台机器，不拿本机 `Date.now()` 去比服务器时刻。
    本标签页更早的一次写入在待确认之后才被确认时，把待确认的基准抬到它（那是自己的旧写，不比待确认的新）。**读在路上时本机缓存原文变了**（本标签页或同 origin 别的标签页又记了一次，哪怕已确认）→ 回包作废、
    以本机为准；导出默认值的取回同理（缓存原文 + 待确认标记在 GET 前后不一致就不覆盖）。升级迁移（后端 `null`、
    本机有旧记录）推之前同样先标待确认。已知未覆盖：同 origin 多标签页并发写时后端按到达顺序落地（#738）。
    导出默认值同理（`tavotto.export.defaults.pending` 存唯一标识，有它就本机为准重推）。换了 origin 时本机缓存本来
    就是空的，这条只护同一个 origin。
  - **磁盘槽位的清理只由后端做**：`flushAutosave` 不再按本机 12 条 `docIndex` 发 DELETE——那只是本机「最近文档」
    放不下了，不是用户不要了；换了 origin 的索引是空的，按它删会删错。`docIndex` 仍只管本机列表与本机兜底副本。
  - **导出默认值**同理：`GET/PUT /api/preferences/export-defaults`（按用户一份），字段语义只在
    `lib/exportDefaults.ts`；`readExportDefaults()` 仍同步读本机缓存，`App` 挂载时 `hydrateExportDefaults()` 用后端那份
    覆盖它，写入两边都写。导出对话框在 `App` 里常驻挂载、初值只在挂载时读一次缓存：取回之后经
    `onExportDefaultsHydrated` 通知它与设置页重读；对话框开着时不当面换掉，记下、关上时再重读。
  - 看护：`store/projectDocOwnership.test.ts` + `e2e/layout-project-isolation.spec.ts` + `tests/desktop_windows/test_restart_restore.py`
    （跨项目不漏，同一个 origin）；`store/layoutSession.restart.test.ts`（有状态的假后端跨两次「启动」存活，中间 `localStorage.clear()` +
    `vi.resetModules()`；外加 404 退回旧逻辑、不再发 DELETE）、`e2e/layout-session.spec.ts`（同一服务新开空存储的
    浏览器 context → 恢复上次的排版），两者在改造前的 main 上都红。
- **新文档的默认名跟界面语言走**（`types/document.defaultDocumentName()`）：
  只在创建那一刻取一次，之后是用户内容（不翻、不追认）。它同时是「另存为」的
  默认文件名，所以取值必须磁盘安全。
- **「最近文档」标出所属项目（2026-09-06，审计 T04）**：`tavotto.docIndex` 跨项目
  共用一份，条目里记 `projectId` / `projectName`。归属在文档**换进来那一刻**定
  （`documentStore` 的 `docProject`），不在落盘那一刻现问——切项目的顺序是先认领
  新项目再换空白文档，而换文档第一句就是把旧文档冲刷落盘。当前项目名的投影在
  `lib/projectLabel.ts`（由 `projectStore` 写、`documentStore` 读，避免两个 store
  互相 import 成环）。旧条目没有这两个字段 = **不知道**，什么都不标。
- **排版时间线（ADR 0101，2026-09-27）**：打节点只有一个入口
  `lib/timelineCheckpoint.takeCheckpoint()`——自动节点、关键时刻（`markMoment()`）、命名节点、
  「恢复前」全走它，它在**第一个 await 之前**同步取走 pj / 文档 / 画布身份 / 缩略图图源（离开
  项目那一刻紧跟着就是换 pj、清 renderStore）。`markMoment()` 只在 `startVersionCheckpoints`
  挂上时才发请求。「保存」时刻由保存侧发 `lib/layoutSaved.emitLayoutSaved()`、时间线订阅——
  新增一条写排版文件的路，在它的成功分支 emit 一次，不 import 时间线。恢复只有 `VersionDialog.restoreNode()` 一处：先 await「恢复前」节点，存不下来
  就不恢复，再一次 `restoreLayoutVersion` commit（⌘Z 一步退回；布局组跟着对象恢复）。
  保存侧在原排队时刻调用纯叶子 `lib/momentCapture.captureDocumentMoment`，不反向import时间线；
  身份与ctx key由该叶子唯一组装，真实图源由原renderStore同步装配/冻结，timelineThumb只合成。
  排队正文、MomentSnapshot和PNG Promise保原引用；成功回调不得取新状态补拍。
  保存Moment的pj必须消费本次writer已冻结的owner/address/scope，与该次正文写盘地址同源；
  冲刷离开稿、冲突恢复和明确空稿写入均不重新读取currentProjectId。看护`store/documentMomentOwner.test.ts`。
  缺真实源端口须明确失败并保稿，不能默认丢Moment；直接文档、App、embedded、MCP、playground入口均须装配。
  图源冻结必须把同一identity.pj传入SourceCapture，再贯穿HTTP URL/header与静态fallback；
  不在等待期间重读current pj。明确离开快照可跨全局项目换代完成，不能按活动预览门一概取消。
  renderer缓存SVG按实际响应捕获的projectId认源；旧A缓存可在current已B、clear前冻结，B同名缓存不能借给A。
  活动缩略图/导出/版本预览经原docProject只读端口捕获PreviewOwner，正文、请求pj、缓存键和响应采纳同属一份身份；
  历史正文读取也用该pj。门变但正文不变时，仅同项目代次/documentId/format/loadSeq的原owner可续期，不能重认领旧正文。
  找回候选另随原列表读取的pj/项目代次/seq存活，不要求先成为活动文档；归属未知的候选只留结构草图，不借当前项目同名素材。
  看护：`lib/momentCapture.test.ts`、`lib/previewProjectOwner.test.ts`、`components/previewOwner.test.tsx`、`components/LayoutDialog.recover.test.tsx`、`store/workingDocumentReceipt.test.ts`、原importArchitecture两门。
  恢复别的图（2026-10-31）：同一张画布上，这一版的图与画布上此刻的图不是同一组（换了 / 多了 / 少了，含单图编辑 ↔ 排版；比 fileId 集合，与后端 `_version_figures` 同一把尺子）→ 先确认、说清会变成几张图；恢复后图变了 → 时间线范围切到「全部图」（`timelineStore.revealAll`）；
  单图编辑时来自另一张画布的图，确认框按「图」说、不说「画布」。
  两个恢复入口（预览页脚、时间线行上弹出的对比小卡片）都经 `restoreUnderLock()`：挂 `timelineStore.restoring`、
  让模态预览以 `busy` 开着——遮罩挡画布、预览与抽屉都关不掉，「恢复前」在存时不许再编辑（Codex #831）。
  卡片入口的模态预览在**取正文之前**就以「加载中」开起来（不是正文回来才开）；另记编辑标记（`past` / `future` / `txn` / 激活画布），
  正文回来时变了就放弃恢复（不写、不打「恢复前」），取不回来就把「加载中」预览收回。
  预览是 `timelineStore.preview` 上的模态对话框（`TimelinePreviewDialog`，默认焦点在对话框容器上、回车不触发恢复；
  页脚只有「恢复到这里」，关闭是右上角 ×——ADR 0101 2026-10-07 修订），抽屉一行一颗 ⋯（`ui/RowMenu`）；单击行弹出锚定的对比小卡片（`CompareCard`：那一刻 | 当前并排 + 一句摘要 + 「恢复到这里」；悬停不冒按钮；卡片角上「放大查看」才开模态预览——ADR 0101 2026-10-23 修订），
  不进 documentStore、换项目时 `clear()`；「存为命名节点」只有 `saveNamedNode()` 一份（抽屉里「给现在存个名字…」
  点开展开输入；⌥⌘S / 命令面板在工作面板顶部就地弹 `NamedNodeQuickBox`、不开抽屉，顶栏没有书签钮）。自动间隔 15 s 停顿 / 2 分钟，e2e 只经
  `window.__TAVOTTO_TIMELINE_TIMING__` 注入，产品默认值不动。
  **入口形态（ADR 0101 2026-10-21 修订）**：时间线是右栏第四个页签（`RightTab 'timeline'`），不再有盖在画布上的浮板；
  打开只有 `actions.openTimeline()`、「亮着」只有 `uiStore.isTimelineOpen`，`TimelinePanel` 挂载即打开；页签是会话态不持久化。
  预览里的位图取档 / 文字字号按显示宽 × DPR（`snapshotBucket`），不许写死档位（看护 `components/layoutSnapshotSharpness.test.tsx`）。
  时间线的范围：多画布时「这张画布 / 全部画布」（按节点的 `canvasId`）；当前是单图编辑（或图内编辑某张图）且有别的图的版本时「这张图 / 全部」
  （按后端读时派生的 `figures` = 那一版画布上面板的 `fileId`，不落盘；ADR 0101 2026-10-24 修订）。打开第二个脚本的图必须落在自己的画布上
  （`enterEdit` 把描述符交给 `openFastEdit`，不先 `addRuntimePanelToCanvas`；仅「重建为可编辑版」`host.beside` 例外），看护 `store/enterEditCanvas.test.ts`。
  时间线行内动作的 e2e 看护 `e2e/timeline-row-actions.spec.ts`；整条时间线 e2e 看护 `e2e/layout-timeline.spec.ts`。
  存盘与帧迁移的补充看护：`lib/figureFrame.test.ts`、`e2e/savefig-frame-migration.spec.ts`、`e2e/save-to-project.spec.ts`、`e2e/cross-tab-paste.spec.ts`。
  文档与会话的补充看护：`store/layoutSession.restart.test.ts`、`e2e/layout-session.spec.ts`、`e2e/layout-project-isolation.spec.ts`、`store/projectSave.test.ts`、`lib/overrideIdentity.test.ts`、`store/layoutAutoReflow.test.ts`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- schema 3/4 只在持久化边界、读档走 `migrateToProject()`
- 文档变化三种性质（载入 / 用户编辑 / 外部派生）按两个代次区分，第三档唯一写入口 `applyDerivedUpdate()`
- 手势中不许写、须与手势同进一条历史的派生值走 `registerTxnFinalizer`（丢弃的事务不跑）
- 派生字段只有 `script / cost / fileKind / pxW`，图幅不是派生字段
- 素材不在清单 ≠ 脚本关系失效
- 切项目回到上次文档、旧条目「不知道」不标
- ⌘S 写回绑定的项目文件（基线是绑定里的修订号）、自动保存只写本机、项目文件的 dirty 只跟用户编辑

## 完整 PNG 的原图采用

`artifactValidation` 是非坐标 guard，形状与唯一判据在 `lib/artifactValidation.ts`；
坐标仍由既有有效 `figure.frame="figsize"` 定义，last-wins 不变。显式 enter-edit 的成功采用
把 FRAME/guard/native 元数据写成一条历史，页面位置/尺寸/裁剪不变；迟到、撤销、切项目/画布
的响应没有提交权。旧读取器 roundtrip 的范围与拒绝恢复边界见 ADR 0098 最后一节。
已带 guard 的记录不再过 legacy FRAME 自动迁移。畸形/未知/不匹配 guard 原样保存并拒绝，
不能把它删掉来获得 legacy 退路。完整重置/确认换素材才按既有丢弃语义原子移除 guard。
看护：`store/artifactEntry.test.ts`、`lib/artifactValidation.test.ts`、`lib/artifactApi.test.ts`。

### 来源驱动采用的并列 guard（ADR 0139 PR2）

`sourceBackedFigure`（`{version, policy: 'script-backed-figure-v1', sourceId, bytesSha256, sizeBytes, status:
'exact'|'reconstructed', reconstructionSha256, confirmedAt?}`）是 `artifactValidation` 的**并列且互斥**字段，
不复用它的形状或字段名；判据同在 `lib/artifactValidation.ts`。**「这块面板处于哪种选中上下文」只有一个出处**
`adoptionOf()`（`strict` / `source` / `none` / `invalid`），其余一律经它派生（`isAdoptedPanel` / `adoptionGuardOf` /
`refuseArtifactOperation` / `artifactRequestFields` / `clearAdoption`）——不再各处写
`Object.hasOwn(panel, 'artifactValidation')`。未知键 / 畸形 / 版本不对 / 两种 guard 并存都是 `invalid`，不当 legacy。
- 严格策略先试；仅当它因 `artist_profile` / `source_mismatch` 被拒、且 `GET /api/experiments` 说开关开着，才用来源驱动策略再准入
  一次。`exact` 直接一次提交；`needs_confirmation` 先开对照确认（`SourceBackedReviewDialog`），**取消什么都不写**，确认才
  提交 `reconstructed` + `confirmedAt`。采用 / 确认各是一次提交，只写 FRAME、nativeW/H、guard，不改页面 x/y/w/h/crop。
- 零编辑（只有结构性 `figure.frame`）= 磁盘原像素：显示与原图导出都不出引擎图，重开 / 脚本变更 / 邻居补渲染不跑脚本
  （`isSourceBackedZeroEdit`）。有真实编辑 = 整图重渲染，请求带 `confirmed_reconstruction_sha256`（缺失 409
  `confirmation_required`，不等 409 `reconstruction_changed`）。
- `reconstruction_changed`：编辑与 guard 原样保留、不自动换基，一句话 + 「重新对照确认」；确认后一次提交更新摘要。
看护：`lib/sourceBackedFigure.test.ts`、`store/sourceBackedAdoption.test.ts`、`canvas/sourceBackedPreview.test.tsx`、
`components/SourceBackedReviewDialog.test.tsx`、`e2e/guarded-png-activation.spec.ts`。

## 没有写入权限的项目（2026-10-30）

打开 `project.writable === false` 的项目时，`ReadOnlyProjectPrompt` 问**一次**：一句话 + 「另存为…」（主）+「继续只读打开」（次）。
另存为 = 选文件夹（桌面壳原生选择器，浏览器版 `DirBrowser`）→ `saveProjectAs` → `projectStore.open(副本)`；
「继续只读打开」写 `settings.readonly_save_as_declined`（按项目路径，持久）；Esc 只是本次会话不再问、不写记忆
（`lib/readOnlyPromptMemory`）。界面不判权限，事实只认后端的 `writable`；教程项目不问。
看护：`components/ReadOnlyProjectPrompt.test.tsx`。
