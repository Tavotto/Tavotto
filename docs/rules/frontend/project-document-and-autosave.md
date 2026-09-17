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
  尺寸变化自动重排、undo/redo 不触发）。
- **撤销防线（2026-08-17，数据损坏级）**：`txnUpdate` 在无事务时**丢弃更新**
  ——绝不静默直写 doc（拖动中事务被外部 endTxn/undo 结束后，pointermove 落进
  静默分支 = 位移绕过历史、撤销永远找不回，真实用户撞见过）。一切撤销入口
  （键盘 / 顶栏按钮 / 桌面菜单加速键）必须走 `runUndoRedo`（带
  undoRedoBlocked 守卫）；undo/redo 的 applyPatches 有 try/catch，坏补丁丢弃
  该条而不是让栈与文档错位。
- **自动保存**：磁盘为主（`PUT /api/autosave/<docId>` 原子写
  `layouts/_autosave/`），localStorage 只留索引 + 崩溃兜底副本
  （写盘成功即清、读取按 updatedAt 取新）。失败发
  `tavotto:autosave-error` 事件 → 常驻错误 toast。
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
  `assetStore` 的三条并发纪律：**请求序号**挡旧响应覆盖新响应（不是"谁最后
  返回"）、**发请求那一刻的 pj** 挡串项目（`null` 与具体 id 是两个取值）、
  **失败不清空** `panels`/`byId`。`force: true` 永远另起一次（手动刷新不许被
  在途请求吞掉）。
- **派生字段 vs 用户数据**（`panelSourceSync.ts` 的表）：只有
  `script` / `cost` / `fileKind` / `pxW` 由 `/api/panels` 说了算；
  几何、`nativeW/nativeH`、crop、rotation、overrides、成组、锁定、选择一律
  不碰。**图幅不是派生字段**——它是几何（`useEngineSync` 盯着它调 `h`），
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
