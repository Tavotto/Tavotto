# 项目、文档模型与自动保存

> 原文出自 `web/AGENTS.md`「项目系统与多画布（前端侧）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- 前端把 pj 存 **sessionStorage**（`lib/session.ts`，按标签页隔离，「不同
  标签页开不同图库」就是靠它）；`?pj=` 出现在地址栏时认下并立刻抹掉。
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些。后端语义见
  `src/tavotto/AGENTS.md`。
- **schema 3**：`ProjectDocument{project, canvases[], activeCanvasId}`；
  运行时激活画布仍是 schema 2 形状的 `documentStore.doc`（画布编辑代码零改动），
  持久化/读档统一走 `migrateToProject()`（接受 2/3）。画布切换换入换出
  undo 栈（canvasSessions）与 UI 会话（`store/canvasSession.ts`）。
  标签页 openTabs 按 documentId 存本机。后端 versions/package 接受 schema 2/3。
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
- **自动保存**：磁盘为主（`PUT /api/autosave/<docId>` 原子写
  `layouts/_autosave/`），localStorage 只留索引 + 崩溃兜底副本
  （写盘成功即清、读取按 updatedAt 取新）。失败发
  `tavotto:autosave-error` 事件 → 常驻错误 toast。**怎样安全地落到磁盘上**——按文档
  排队、串行 PUT、乐观并发基线（updatedAt）与外部修改基线（内容 hash 三档 + 缺席）、
  写前确认、409 不推基线、冲突挡住排队那份——2026-09-18 起住在
  `lib/autosave/diskWriter.ts`（`createDiskWriter(ports)`，不认识 store、不碰 window /
  localStorage / 遥测），`documentStore` 只装配端口；用例 `lib/autosave/diskWriter.test.ts`
  用假端口 + 手动 gate 直接量时序。
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
  按项目 id 在本机记最近一份**有内容**的 documentId（`tavotto.projectDoc.<pj>`，
  空白文档不记——它从不落盘），`projectStore.adoptOpenedProject` 在换代之后按记录
  读自动保存槽位换回去；读不回来时 `lastDocumentIssue` → `DocumentBanner` 指名那份
  文档并给「打开上次文档」重试，**不静默留一份空白**。带 `prepareDocument` 的入口
  （教程）不走这条。记录的键取 `currentProjectId()` 而不是 `project` 字段：换代期间
  后者还是旧项目。Project Picker 的同名区分 / 失效分组 / 筛选判据只在
  `lib/recentProjects.ts` 一份，顶栏项目切换器共用。
- **新文档的默认名跟界面语言走**（`types/document.defaultDocumentName()`）：
  只在创建那一刻取一次，之后是用户内容（不翻、不追认）。它同时是「另存为」的
  默认文件名，所以取值必须磁盘安全。
- **「最近文档」标出所属项目（2026-09-06，审计 T04）**：`tavotto.docIndex` 跨项目
  共用一份，条目里记 `projectId` / `projectName`。归属在文档**换进来那一刻**定
  （`documentStore` 的 `docProject`），不在落盘那一刻现问——切项目的顺序是先认领
  新项目再换空白文档，而换文档第一句就是把旧文档冲刷落盘。当前项目名的投影在
  `lib/projectLabel.ts`（由 `projectStore` 写、`documentStore` 读，避免两个 store
  互相 import 成环）。旧条目没有这两个字段 = **不知道**，什么都不标。
