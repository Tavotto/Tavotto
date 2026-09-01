# ARCHITECTURE — 产品体验与可靠性改造的真实链路基线

> 本文件记录的是 **分支上真实存在的实现**，不是目标架构。基线是
> 2026-08-29 的 `ef9ac02`；本轨道自己改掉的地方**就地更新并标 `← 02`**
> 这样的记号（行号按起始 commit，改动后会漂，认函数名不认行号）。
> 目标架构的约束写在 `UX_CONTRACTS.md`，决策写在 `DECISIONS.md`。
>
> 仓库级不变量的唯一权威仍是根 `AGENTS.md` 与 `docs/adr/`，本文件只做
> 「后续 22 个 Session 要动的那几条链路」的定位索引，不复制规则。

---

## 0. 进程与边界

```text
Tauri 壳 (src-tauri/)  ──┐
                         ├─→ Flask 父进程 (src/tavotto/app.py, 4717 行)
浏览器 / MCP 宿主      ──┘        │
                                  ├─→ worker 池 (engine/pool.py) ─→ 用户解释器子进程
                                  │      └─ 可选 Rust supervisor (workerd/)
                                  └─→ PyMuPDF 后端 (pdfbackend/, 全仓唯一 import pymupdf)
```

- Flask 父进程**不 import matplotlib**：科学栈只活在 worker 子进程里
  （`tests/conftest.py` 顶部把这条边界写成了测试前提）。
- 前端 Zustand store 在 `web/src/store/`，与后端只经 `web/src/lib/api.ts`
  一层对话；组件里不直接 fetch，也不直接读写磁盘格式。

---

## 1. 项目打开 / 关闭

| 环节 | 位置 |
| --- | --- |
| 打开（复用已开的） | `app.py:1214 open_project()`（顺带 `seed_state` + 挂 watcher） |
| 项目上下文对象 | `app.py:415 class ProjectCtx`、`app.py:431 _project_id()` |
| 每请求解析当前项目 | `app.py:444 _request_ctx()` / `466 current_ctx()` |
| 关闭一个项目 | `app.py:1265 close_project()`（停它的 watcher + 收它的 worker） |
| 全部关闭 | `app.py:1291 reset_projects()` |
| HTTP 入口 | `POST /api/projects/open` `…/close` `…/remove`，`GET /api/projects`、`/api/projects/recent`、`/api/projects/browse` |
| 项目内收纳目录 | `app.py:1367 project_store_dir()` → `<项目>/tavottofile/`（常量 `engine/config.py:240 PROJECT_STORE_DIRNAME`） |
| 导出 / 备份目录 | `app.py:1376 project_export_dir()`、`1386 project_backup_dir()` |
| 前端 | `web/src/store/projectStore.ts`、`components/ProjectPicker.tsx`、`ProjectSwitcher.tsx` |

**项目身份**按路径归一化（`engine/config.py path_is_case_insensitive()`），
大小写不敏感卷上同一目录的两种写法归为一个项目。

**多项目隔离是既有不变量**：watcher、worker 池、`baked_overrides/<项目id>.json`
全部按 `ctx.id` 分键；SSE 事件带 `pj` 字段（`app.py:1185 _script_change_handler`、
`app.py:1322 _watch_sink`——三个回调都闭包着同一个 `ctx`，事件因此必然带对 pj）。

---

## 2. 文档：加载 / 保存 / undo / autosave / 恢复

这是本轮改造的核心链路，也是现状最需要看清楚的一条。

### 2.1 内存模型

- 单一 store：`web/src/store/documentStore.ts`（961 行，Zustand + immer patches）。
- 类型：`web/src/types/document.ts`
  - `FigureDocument`（`schema: 2`）= **一张画布**的编辑态；
  - `ProjectDocument`（`schema: 3`）= 项目文档，含 `canvases: CanvasData[]`；
  - 换算 `canvasToDoc()` / `docToCanvas()`；读档统一入口 `migrateToProject()`。
- 运行时激活画布活在 `state.doc`（schema 2 形状），画布列表在 `state.canvases`，
  所以既有画布编辑代码零改动 —— 见 `docs/adr/0001-project-canvas-tab-object.md`。

### 2.2 撤销 / 事务 / dirty

| 语义 | 入口 |
| --- | --- |
| 一次用户操作 = 一条历史 | `documentStore.commit(label, recipe)` |
| 拖动事务（pointerdown→up 合成一条） | `beginTxn` / `txnUpdate` / `endTxn` |
| **不进历史**的派生写入 | `documentStore.silent(recipe)` |
| 历史上限 | `HISTORY_LIMIT = 200` |
| 非激活画布的撤销栈 | `state.canvasSessions[canvasId]`，切画布时换入换出 |

`HistoryEntry.label` 存的是 **UiMessage 描述符**而不是翻译后的字符串
（`documentStore.ts:31` 的注释写明了原因：撤销栈活得比一次渲染长）。

### 2.3 落盘（现状）

```text
编辑 → store.subscribe(startAutosave)   documentStore.ts:892
     → dirty = true，防抖 1000ms
     → flushAutosave()                  documentStore.ts:806
          ├─ 同步：localStorage `tavotto.autosave.<documentId>`（崩溃兜底副本）
          ├─ 异步：scheduleDiskWrite() → PUT /api/autosave/<id>?base=<updatedAt>
          │        成功后删掉本机副本 —— 稳态下 localStorage 不存文档主体
          └─ 轻量索引 `tavotto.docIndex` + `tavotto.currentDoc`
```

- 后端：`app.py api_autosave_put()`，写在
  `AUTOSAVE_DIR = LAYOUT_DIR / "_autosave"`（**数据目录**，不是项目目录）。
  `← 02` 落盘改走 `engine/atomicio.write_json()`（含 fsync 与 tmp 清理），
  校验改走 `engine/documents.validate_document()`；响应多一个 `revision`
  （内容 hash），`GET` 多一个 `X-Tavotto-Revision` 头。
- 乐观并发：`app.py:4226 _autosave_newer_than()` 比较文档里的 `updatedAt`
  （不是文件 mtime），磁盘更新则回 `409 {"code": "stale_write"}`；
  前端 `documentStore.ts:762 diskBaseline` 收到 409 后**故意不推进基线**。
- 读回：`readAutosaveDoc()`（`documentStore.ts:861`）磁盘为主、本机副本更新则
  用副本并推回磁盘；`restoreSession()` 启动时恢复 `tavotto.currentDoc`。
- 崩溃逃生：`requestBlankStart()` + `sessionStorage['tavotto:skip-restore']`，
  由 `components/ErrorBoundary.tsx` 置位。
- 多标签页同开一份：`web/src/lib/docPresence.ts announceDocOpen()`。

### 2.4 命名画布文件（用户的「另存为」）

| 环节 | 位置 |
| --- | --- |
| 目录选择 | `app.py:4141 project_layout_dir()` → `<项目>/tavottofile/`，未开项目退回数据目录 `layouts/` |
| 读取查找顺序 | `app.py:4153 _layout_read_dirs()`：`tavottofile/` → 旧 `<项目>/canvases/` → 数据目录 `layouts/` |
| 列表 | `GET /api/layouts`，跨三个目录合并、重名以主位置为准；`← 02` 剔除收纳目录里 Tavotto 自己的文件（`_styles`，R-04） |
| 读 | `GET /api/layouts/<name>`（`app.py:4183`） |
| 写 | `POST /api/layouts/<name>` —— `← 02` 改为 `engine/atomicio.write_json()`；此前是**直接 `write_text`，非原子**（R-01） |
| 前端规范化 | `web/src/lib/migrate.ts normalizeLayout()`：schema 3 透传、schema 2 补齐、v1 字段映射 |

### 2.5 版本检查点（整份布局的时间线）

| 环节 | 位置 |
| --- | --- |
| 自动检查点 | `web/src/hooks/useVersionCheckpoints.ts`（停顿 15s 且距上版 ≥5min） |
| API | `GET/POST /api/versions/<doc_id>`、`PATCH`、`duplicate`、`DELETE`（`app.py:4356+`） |
| 存放 | 旧位置 `LAYOUT_DIR/_versions` 只读兼容，新写入项目 `tavottofile/versions/` |
| 保留策略 | `VERSION_KEEP_AUTO = 40`、`VERSION_KEEP_TOTAL = 120` |
| 恢复 UI | `web/src/components/VersionDialog.tsx:292 restore()` |

**与「写回原始文件」的版本历史（`baked_overrides/<项目id>.json`）是两件事**：
后者作用于单张图的源文件，恢复布局版本绝不触碰 `figures/` 里的任何文件。

### 2.6 落盘权威（`← 02`，ADR 0023）

| 职责 | 位置 |
| --- | --- |
| 原子写（唯一实现） | `engine/atomicio.py`：`write_json` / `write_bytes` / `dumps_json` / `content_revision` |
| 结构化错误 | `AtomicWriteError(code, message, path)`，`app.py` 的 `_atomic_write_error` 映射成 400/500 |
| schema 判据（唯一实现） | `engine/documents.py`：`validate_document` / `is_user_document_stem` / `require_user_document_stem` |
| 结构化错误 | `DocumentError(code, message, status)`，`app.py` 的 `_document_error` 映射成 400/409 |
| 已并入的写入点 | `_write_baked`、`_save_versions`、`_save_styles`、`api_autosave_put`、`api_layout_save` |
| **尚未并入** | `engine/` 里另外五处（`config.py` / `runspec.py` / `runtimeasset.py` / `locate.py` / `session_client.py` / `nativehandoff.py`）——它们写的不是文档 |

---

## 3. registry / discover / probe / worker / watcher

| 环节 | 位置 |
| --- | --- |
| 素材与脚本发现 | `engine/discover.py`（`EXCLUDE_DIRS` 含 `tavottofile`） |
| 注册表 | `engine/registry.py`，HTTP：`GET/PUT /api/registry`、`POST /api/registry/scan` |
| 探测（**显式动作**） | `POST /api/registry/probe`（`app.py:1852`）/ `…/probe/cancel` |
| worker 池 | `engine/pool.py`（2000+ 行），单 worker 协议 `engine/worker.py` + `wireproto.py` |
| Rust supervisor | `workerd/`，客户端 `engine/workerd_client.py`（默认关，`TAVOTTO_WORKERD`） |
| **项目 watcher（`← 05`，ADR 0026）** | `engine/project_watch.py`（**每项目一个**），`start()/stop()/watched_dirs()`；整棵树的快照轮询，默认 2 秒 |
| watcher 出口 | `app._watch_sink(ctx)`：`refresh` → `app.refresh_project(reason="watcher")`；`script_changed` → `panel.file_changed`；`error` → `project.error` |
| **统一刷新（`← 04`，ADR 0025）** | `engine/project_refresh.py`，app 层入口 `app.refresh_project()`，HTTP：`POST /api/project/refresh` |
| **接入就绪度（`← 07`）** | `engine/readiness.py`（纯诊断），HTTP：`GET /api/project/readiness`；`/api/panels` 每项的 `capability` 是同一次计算的投影 |

### 3.1 统一刷新（`← 04`，ADR 0025）

```text
app.refresh_project(ctx, reason=…)          ← app 层唯一入口（注入 SSE / watcher 出口）
  → engine/project_refresh.refresh_project_index()
      项目锁 → registry 前快照 → 静态 merge（内容变了才写盘）→ reload
      → registry 后快照 → 结构化 diff → 素材清单（与**上一轮**比）
      → 作废关系变了的 worker（限本项目）
      → 有差异才发 `registry.changed` / `assets.changed`
```

已接进来的调用方：`POST /api/project/refresh`（新）、`POST /api/registry/scan`、
probe 成功（`allow_static_merge=False`）、`PUT /api/registry`（同）、
**项目 watcher（`reason="watcher"`，`← 05`）**——五个入口一条编排，谁都不许
自己 merge、自己 reload、自己发第二套事件。

`app.py` 里原来的 `reload_registry()` 已删——它的两件事（重装 + 重挂 watcher）
都在服务里，而它还漏了第三件（作废过期 worker）。`RefreshSink.watch` 这个
「重挂 watcher」的钩子在 05 一并删除：项目 watcher 盯的是整棵树，没有
「盯谁」这个状态（ADR 0026 §9）。

### 3.2 项目 watcher（`← 05`，ADR 0026）

```text
每 interval（默认 2 s）：
  take_snapshot(root)                        ← 目录不可用返回 None，这一轮不动
    scripts  : discover.iter_all_scripts()   ← 与静态起草同一份剪枝/深度规则
    registry : tavotto_registry.json + mm_registry.json
    assets   : project_refresh.iter_assets() ← 与 /api/panels 同一把尺
    每个文件的签名 = (size, mtime_ns)         ← 两维缺一都会静默漏
  → 与上一张比，差异并进 pending，快照**立刻**换新（刷新期间的写入进下一批）
  → 安静 0.5 s（或批次已满 5 s）→ 结算这一批：
       作废 worker（内容变了的已登记脚本；paper_style* → 整项目）
       → 摘掉自己刚写的注册表（内容修订号）→ 还剩东西就调一次统一刷新
       → panel.file_changed（还在磁盘上的已登记脚本）
```

**worker 失效在两边各管一半**：注册表**关系**变了归刷新（ADR 0025），
脚本**内容**变了与 `paper_style*` 归 watcher——刷新看不见后者（改脚本内容
常常不改注册表结构）。

**「不静默执行用户脚本」在现状里的落实方式**：扫描 / 注册表 / watcher /
**就绪度** 都只做静态读取与 `stat()`；真正跑用户代码的只有显式的 probe、
渲染请求（`POST /api/engine/render`）与 native 会话（`docs/adr/0014`、`0020`，
`/api/native/*` 全部要用户批准，`engine/nativeperm.py`）。

### 3.3 接入就绪度（`← 07`）

```text
GET /api/project/readiness            ← 只读诊断；不写盘、不发事件、不跑脚本
  → engine/readiness.compute(ctx)
      项目锁（与刷新同一把）
      → 注册表 entries（内存里那份，引擎实际在用的）
      → 素材清单 iter_assets()          ← 与 /api/panels 同一把尺
      → 目录可写性 os.access(W_OK) + 磁盘注册表合法性（新实例校验，不碰 ctx 的）
      → 上一次刷新写注册表失败了没有（RefreshState.registry_write_failed）
      → 脚本签名（.py 集合 + (size, mtime_ns)）→ 命中就复用缓存的 discover 报告
      → 逐 stem 判定 → summary → conflicts → fingerprint（= 报告自身的内容哈希）
```

**判定表**（分支互斥，从上往下，每张图只落一个）：

| # | 条件 | status | reason_code |
| ---: | --- | --- | --- |
| 1 | 注册表映射了这个 stem，脚本文件在 | `editable` | `registered_source` |
| 2 | 注册表映射了，脚本文件不在 | `source_missing` | `registered_script_missing` |
| 3 | 这一轮静态扫描没跑成 | `layout_only` | `source_scan_unavailable` |
| 4 | 多个脚本认领同一个 stem | `conflict` | `multiple_source_candidates` |
| 5 | 恰好一个脚本认领 | `auto_linkable` | 下表 |
| 6 | 项目里有产图但输出名要跑才知道的脚本 | `needs_probe` | `runtime_output_unknown` |
| 7 | 其余 | `layout_only` | `no_source_candidate` |

第 5 行的 reason 说的是**卡在哪一步**，优先级从"刷多少次都没用"往"下一次
刷新就好了"排：`registry_invalid` > `project_read_only` >
`registry_write_failed` > `static_unique_candidate`。

**注册表优先于静态报告**（第 1 行在第 4 行之上）：注册表文件就是人工裁决的
落处（"一脚本多产物 / 归属有歧义的 stem，裁决结果记在各图库自己的注册表
文件里，勿改"）。冲突照旧出现在项目级 `conflicts` 里，附 `resolved_by`。

**动作能力**：`can_probe` = 手里有具体候选；`can_manual_link` = 项目可写；
`can_rescan` 在项目级。三者都只说"界面可以提供这个动作"——**就绪度自己一个
都不执行**，动作仍归 `/api/registry/probe`、`PUT /api/registry`、
`POST /api/project/refresh`。

**缓存**（项目级，挂在 `RefreshState.readiness`，随项目消亡）：两层，键都是
输入的内容签名；统一刷新在**确认事实真的动了之后**额外清一次（签名盖不住
"同尺寸 + 同一个 mtime_ns 刻度的就地改写"，而那正是刷新自己写注册表的形状）。
扫描失败的那一份**不进缓存**。进出都深拷贝。

**`stem`（`← 08`）**：每个 panel 除 `id` 外还带 `stem`——**关联动作的对象是
它**（注册表的键就是 stem），而同一个 stem 可能挂着两份素材。给出来是为了
让界面不必自己从文件名切一次（`sub/Fig.v2.pdf` → `Fig.v2`，既不是 id，也不是
第一个点号之前那一段）。`CAPABILITY_FIELDS` 一个字没改，`/api/panels` 上没有
这个字段。

### 3.4 接入就绪度的前端面（`← 08`）

```text
GET /api/project/readiness ─┐
                            ├→ store/projectReadinessStore  ← 唯一持有者
/api/panels 每项的 capability ┘        report / loading / error / focusId / dismissed
     │
     ├→ ProjectReadinessBanner       顶部一句话摘要（不阻塞画布、不自动弹框）
     ├→ RegistryDialog               「项目接入状态」= 每张图一行 + 下一步动作
     ├→ AssetBrowser                 卡片角标（<span>）+ listbox 外的说明条
     ├→ ContextBar / PanelSection    「为什么不能编辑？」/ 非阻塞说明
     └→ LeftRail                     常驻的项目级入口
```

**四条纪律**：

1. **前端不判状态。** 六个状态与十个 reason code 的唯一出处是后端；界面上
   连一个 `!!script` 的分支都没有。句子由 `lib/readinessText.ts` 一处按
   **`reason_code`**（不是 `status`）查——同一个状态下不同 code 要说的话完全
   不同（T-41）。
2. **开关只有一个**：`uiStore.registryOpen`（T-38）。就绪度 store 只管
   `focusId`；`focusPanel(id)` 是 Prompt 17/18 可以直接复用的入口。
3. **刷新挂在 `liveSync.refreshAssetsAndSync()` 一处**（T-39），与素材清单
   同一批事件、同一个 `force` 语义。
4. **动作不在这里执行**：试运行走 `/api/registry/probe`（用户显式点出来）、
   手工关联走 `PUT /api/registry`、重扫走 `POST /api/registry/scan`；每次成功
   之后只调一次统一刷新，不手拼状态。

**「没测量」三档一个都不许压扁**：`conflicts` 的 `null`、`project.registry_valid`
的 `null`、`PanelInfo.capability` 的 `undefined`。界面对第三档的处理是
**什么都不显示**（不是显示 `layout_only`）。

---

## 4. SSE → 前端 store

| 环节 | 位置 |
| --- | --- |
| 发布 | `app.py:1149 sse_publish()`（进程内 `queue.Queue`，maxsize 200） |
| 端点 | `GET /api/events`（`app.py:1158`），15s 心跳注释行 |
| 前端消费 | `web/src/store/assetStore.ts`、`renderStore.ts`、`nativeSessionStore.ts`、`scriptRunStore.ts` |
| 事件按项目隔离 | 事件体里的 `pj` 字段；前端按当前项目过滤 |

**04 之后**：registry / 素材两类事件由统一刷新服务批量发布（一次刷新至多
各一条，无差异一条不发），payload 见 ADR 0025 §2 与 `web/src/lib/api.ts` 的
`ServerEvent`。`panel.file_changed` 由项目 watcher 单独发，语义不变
（「已登记脚本内容变化，需要重建 Figure」）。

**05 之后**：项目 watcher 把一批连续写入合并成**一次**刷新，于是「一个编辑器
保存动作 → 最多一次刷新 → 最多一组 registry/assets 事件」是后端保证的，
不再靠前端各 store 各做各的防抖。新增 `project.error`（后台刷新失败，可恢复；
`{pj, reason, code, params}`，`code` 走 `errors:*` 那张双语码表）。

**06 之后**：四条事件全部有 handler，闭环是

```text
外部修改 → watcher → 统一刷新 → SSE
  → store/liveSync.refreshAssetsAndSync()
      ├ assetStore.load()                    合并请求 + 序号防旧覆盖 + 项目隔离
      ├ syncPanelSourceMetadata(byId)        PanelObject 派生字段原地同步
      └ applyPanelSync(result)               markStale / reset / 退出图内编辑 / 一条提示
```

| 事件 | handler 做什么 |
| --- | --- |
| `panel.file_changed` | 先同步 `markStale`（**不等**素材刷新：脚本变了而 PDF 还没重生成时 mtime 一动不动），再走合并刷新 |
| `registry.changed` | 脚本清单 / runtime 清单重取**已取过的**；合并刷新 + 全量派生同步 |
| `assets.changed` | 合并刷新（`affectedIds` = 事件里的素材 id）；`mtime` 换代顺带让静态图片 URL 失效 |
| `project.error` | 一条常驻错误提示（`errors:*` 码表，未知 code 有通用回退）；**不是模态框** |

`web/src/lib/api.ts` 另有三个纯函数解码事件的可选/兼容字段：
`affectedScriptsOf` / `affectedStemsOf` / `affectedAssetIdsOf`。
**同一批事件只发一次 `/api/panels`**（`assetStore` 的 in-flight 合并），
**无差异零改动**（`syncPanelSourceMetadata` 算不出差异就一个 `set()` 都不发）
——两层合起来保证一批事件至多一条提示、至多一次落盘。

SSE 重连（`subscribeEvents` 的 `onOpen`）补一次合并刷新 + 派生同步，
3 秒节流，**不调后端的静态刷新**。

---

## 5. 单图编辑 → 画布 → 导出

### 5.1 图内编辑（现状的「快速编辑」）

```text
选中面板 → 元素树 / QuickEdit → override 补丁
   web/src/canvas/QuickEdit.tsx、quickEditStore.ts、components/inspector/
      ↓ POST /api/engine/render (app.py:2487)  —— 带 patches，热会话重放
   engine/pool.py（worker 会话） → manifest（engine/manifest.py, 3006 行）
      ↓
   预览：/api/engine/svg、/api/engine/preview_png、/api/engine/png
```

- 补丁格式的唯一权威 `engine/patchspec.py`，Rust 镜像 `workerd/src/patchspec.rs`，
  逐字节看护 `tests/golden/patch_vectors.json`。
- 写回源文件：`POST /api/engine/update_source`（`app.py:2668`），
  prepare → verify（全量重放 + 几何比对 + 像素门）→ commit，任一环不过 409
  （`app.py:3009 _write_back_prepare`、`2950 _replay_pixel_diff`）。

**（`← 09`）两条工作流已经合流到同一个对象**（ADR 0028 / T-43）：

```text
web/src/store/workspace.ts   mode: fast_edit | layout, activePanelId
  openFastEdit(figureId)     找对象 / 没有就走既有 addPanel → 进图内编辑
  addFigureToLayout(figureId)已在文档里就聚焦，不重复创建
  returnToLayout()           回排版；x/y/w/h 一个字节没动过
  focusLayoutPanel(panelId)  切画布 + 选中 + 滚进视野（11/12 复用）
```

一张图在文档里只有**一个**面板对象；快速编辑是它的另一种看法
（`CanvasStage` 里 `only={activePanelId}`：不铺纸面、不画网格、不画别的对象，
取景框改成那张图的包围盒），画布排版是它在页面上的落位。模式与当前图是**工作区
状态**——不进文档、不进撤销、不置 dirty，按 documentId 存本机一档。

### 5.1b 属性能力层：一段文字长什么样（`← 13`，ADR 0032）

```text
                       web/src/lib/typography.ts
        规范属性名 · 取值语义 · 能力表 · property path · 校验/规整
                              │
              ┌───────────────┴───────────────┐
   useFigureTypography                 useCanvasTypography
   （能力问 manifest）                  （能力看 TextObject 字段）
   setOverride / setOverrides            updateObjects
              └───────────────┬───────────────┘
                     TypographyAdapter（一个接口）
                              │
                controls/TypographyControls.tsx（一份控件）
        属性页图内文字 · 图内批量 · 画布标注 · 浮动工具条 —— 四个入口
```

* **控件看不到目标是哪一类、是一个还是三个**，两件事都由适配器吸收；
  写入仍各走各的 document action，**没有一条路径绕开 `documentStore.commit`**。
* **值有四档**（`uniform` / `mixed` / `inherit` / `unsupported`），压扁任意
  两档都是数据损坏级的误导。
* **property path 只有 `propertyPathOf(kind, prop)` 一份**：检查报的字段名、
  控件挂的 `data-prop`、问题面板查的选择器同源（`← 11` 的定位链最后一跳）。
* **画布文字的字体族是闭集**（三个通用族），与
  `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源——合成跑在没有 matplotlib 的
  Flask 进程里，画得出来的就是 PyMuPDF 的 base-14。
* `TextObject.fontFamily` 是**可选字段**：缺席 = 没设过 = 继承默认族。
  磁盘格式不升版，载荷缺省不发。

### 5.2 画布

`web/src/canvas/CanvasStage.tsx`、`PanelView.tsx`、`ObjectView.tsx`、
`OverlaySvg.tsx`、`ContextBar.tsx`、`interactions.ts`；几何权威
`exactPanelRender`（`docs/adr/0016`、`0017`）。

### 5.3 导出（`← 12`，ADR 0031）

**原图与画布是同一个请求的两个 scope，不是两条管线。**

| 环节 | 唯一出处 | 备注 |
| --- | --- | --- |
| **「这次要什么」** | `engine/exportreq.py` ↔ `web/src/lib/exportRequest.ts` | `ExportRequest`；缺省值只有一处。`scope ∈ {original, canvas}` |
| **原图规格**（`← 09`，ADR 0028） | 决策 `web/src/lib/originalSpec.ts`；事实 `engine/originalspec.py` + `/api/panels` 的 `original_spec` | `original` 段的 w/h/px 从它来，**不从画布落位来** |
| 文件名规则 | `engine/exportreq.check_filename()` ↔ `lib/exportName.ts` | **严格同源对**，`tests/golden/filename_vectors.json` |
| 作业生命周期 | `engine/exportjob.py` | prepare / validate / run / cancel；临时目录 → 原子 replace；`partial` 独立一档 |
| 原子发布 | `engine/atomicio.publish_file()` | fsync 文件 → replace → fsync 目录（与 ADR 0023 同一序列） |
| 合成（canvas） | `pdfbackend.compose(w, h, transparent)` | 全仓唯一 import pymupdf 的模块；PDF 与 PNG 出自**同一页** |
| 原图产出（original） | `pdfbackend.original_pdf/original_png` | 矢量整页搬运不重画；位图**保源像素网格** |
| 面板取源 | `app._resolve_panel_source()` | 有 override 的图先由引擎全质量重渲染，两个格式共享它 |
| HTTP | `POST /api/export`（同步）/ `/start` / `/state` / `/cancel` / `/validate` | 五个端点、**一个服务**；老契约由 `normalize()` 抬成同一个作业 |
| 后台线程的项目 | `app.bound_project(ctx)` | `_request_ctx()` 的兜底是默认项目，不绑定 = 导出另一个图库的同名图 |
| 前端编排 | `web/src/store/exportStore.ts` | 作业活在 store 不活在对话框；SSE `export.progress` + 轮询，同一个 `applyExportJob()` |
| UI | `web/src/components/ExportDialog.tsx` | 文件名 → 范围 → 格式 → 分辨率 → 规范 → 检查 → 高级 |
| 样式检查报告 | `app._style_check_report()` + `lib/preflight.buildProofPayload()` | v3；前端给检查结果，服务端补版本/时间/产物事实；**失败不牵连成图** |
| 打包（图 + 脚本 + 清单） | `POST /api/package`；入口在 **TopBar 文档菜单** | 从导出对话框的三点菜单搬走（ADR 0031 §6），与"导入项目包"并排 |

---

## 6. Style / Spec / Validation / Export（`← 10`，ADR 0029）

| 层 | 唯一出处 | 备注 |
| --- | --- | --- |
| **Spec（内置）** | `src/tavotto/profiles/publication.json` | 两侧求值器共读；`default_profile: lab-publication-v1`（v1.1.0） |
| **Spec（用户自建）** | `<data_dir>/profiles/specs.json` ← `engine/profilestore.py` | 与内置**同形**；走 `profiles.validate_spec()` 同一套校验 |
| 「任意 id → 规范」 | `profilestore.resolve_spec(id, journal)` | **唯一入口**（HTTP 与 MCP 都走它）。`profiles.load()` 仍只读内置那份 canonical JSON——不让它知道用户数据目录，避免循环 import |
| Spec 求值器（Python） | `engine/preflight.py` | MCP / 后端走这条 |
| Spec 求值器（TS） | `web/src/lib/preflight.ts` | 浏览器里跑不了 Python，所以有两份 |
| 两份求值器的对齐 | `tests/golden/preflight_vectors.json` | pytest 与 vitest 各跑一遍同一份向量 |
| 缺键时的字号兜底 | `profiles.FALLBACK_MIN_FONT_SIZE_PT` ↔ `lib/profile.ts` 同名常量 | **严格同源对**；求值器里一个字面量都没有 |
| 文档上的规范绑定 | `CanvasData.profile { id, journal?, snapshot?, snapshotVersion?, follow? }` | **规则全文进文档**（快照）；解析只有 `lib/specBinding.resolveDocumentSpec()` 一处 |
| **Style（内置）** | 从默认 Spec **派生**（`profilestore._builtin_style_record`） | 不落盘、不是第二份数字 |
| **Style（用户）** | `<data_dir>/profiles/styles.json` | 旧位置 `layouts/_styles.json` 首次访问时一次性迁走并腾空（原件备份进 `profiles/backup/`） |
| Style 的应用 | `lib/stylePresets.planStyle()` → `store/actions.applyStylePlan()` | 一条历史、可撤销、含画布背景 |
| profile 的显示名 / 技术详情 | `lib/profileText.ts` | 内置跟界面语言走；**默认视图不出现 id 与版本号** |
| 清单的前端持有者 | `store/profileStore.ts` → `/api/profiles/*` | 组件里没有 fetch，也没有磁盘格式的知识 |
| 管理界面 | `components/settings/ProfilesSettings.tsx`（设置分区 `profiles`） | Style 与 Spec **不在同一张表单里混改** |
| **Export** | `web/src/lib/exportDefaults.ts`（localStorage）+ `exportPayload.ts` | 与 Spec 无耦合；**PPI 不在 Style 里**（T-49） |

磁盘布局：

```text
<data_dir>/profiles/styles.json     用户自建样式（schema 1，每条带 revision）
<data_dir>/profiles/specs.json      用户自建规范
<data_dir>/profiles/backup/         坏文件与迁移前的原件（**不删**）
```

纪律：原子写（`atomicio`）、乐观并发（`expected_revision` 对不上回 409 + 磁盘
现值）、损坏回退内置且坏文件挪进 `backup/`、比本构建新的清单**原样不动**、
导入一律建新的一条（id 重新分配，所以不存在"导入把我的改动冲掉了"）。

最小字号：**默认规范只有 8 pt 一个数**（`min_effective` = `absolute_min` =
`legend_policy.min` = 8.0）。`eff <= floor` 的边界语义未变。

---

## 6b. 统一检查与问题定位（`← 11`，ADR 0030）

| 环节 | 唯一出处 | 备注 |
| --- | --- | --- |
| 规则求值 | `engine/preflight.py` / `web/src/lib/preflight.ts` | 与 ADR 0029 同一条链；golden vectors 对齐 |
| 逐条命中 | `preflight.ts` 的 `Sink.record()` → `PreflightOccurrence` | **TS 侧的展开层，不进跨语言合同**；看护用例盯着与聚合项一致 |
| 接成可定位问题 | `web/src/lib/validation.ts` | `ValidationIssue` / `ObjectRef` / 规则目录 / 指纹 / 导出上下文 / 摘要组装（`summaryFor`） |
| 编排 | `web/src/store/validationStore.ts` | 防抖 250ms + 代次、按画布增量、失败不清空、**不改文档**；`startValidation()` 在 `App.tsx` 装配一次 |
| 定位 | `web/src/lib/issueFocus.ts` | **跨模块唯一**；`focusObject` / `focusIssue` / `openProblems`；失败回闭集原因 |
| 措辞 | `web/src/lib/validationText.ts` | 短标题 / 当前值→要求 / 人话主语 / 技术详情 / 等级图标表 |
| 修复计划（纯计算） | `web/src/lib/issueFix.ts` | `planFix` / `fixOptions`；**不碰 store** |
| 修复落地 | `web/src/store/issueFixActions.ts` | `applyIssueFix` / `applyIssueFixes` → `documentStore.commit` |
| 界面 | `web/src/components/left/ProblemPanel.tsx` + `LeftRail`（常驻入口 + 角标） | 抽屉内容由 `LeftPanel` 按 `leftTab === 'problems'` 分派 |
| 定位高亮 | `uiStore.issueHighlight` → `canvas/OverlaySvg.tsx` | 加粗虚线外框，与选中态分开；`motion-safe:` 下才闪 |
| 属性字段落点 | `data-prop`（`ElementInspector.FieldBlock` / `TextSection`） | **不是 aria-label** |
| 导出面板消费 | `getValidationSummary(scope, extra)` / `rawIssuesFor(canvasId)` / `openProblems(filter?)` | 不跑第二遍求值器；proof 留档仍用聚合投影（格式一个字节没动） |

三类规则：Document/Object（实时）、Export-context（`exportContextRaw()`，与
`codex-plugin` 的 `bridge.export_raster_issues()` 严格同源）、Readiness
（ADR 0027，**不进这个清单**）。

---

## 7. 设置与 Tavotto 管理的运行环境

| 环节 | 位置 |
| --- | --- |
| 设置外壳 | `web/src/components/SettingsDialog.tsx` + `components/settings/` |
| 项目设置 | `PATCH /api/project/settings`（`app.py:2074`），存 `engine/config.py project_settings()` |
| 环境解析 | `engine/projectenv.py`（`docs/adr/0018`）、`engine/depresolve.py` |
| 受管环境 | `engine/managedenv.py`；`GET/PATCH /api/engine/environment`、`POST …/install`、`…/managed/rebuild` |
| 受控依赖修复 | `engine/deprepair.py`（`docs/adr/0019`），`POST /api/engine/dependency/plan|install|cancel` |
| 前端 | `web/src/store/envStore.ts`、`depRepairStore.ts`、`components/EngineEnvironmentCard.tsx`、`DependencyRepairCard.tsx` |
| Coding Agent 注册表 | `engine/ai_agents.py`（`docs/adr/0015`），`/api/ai/agents/*` |

---

## 8. Codex / 内置 AI / 遥测 / i18n / 教程

| 环节 | 位置 |
| --- | --- |
| Codex 插件 / MCP | `codex-plugin/`（`docs/adr/0005`、`0006`、`0012`），前端内嵌画布 `web/src/mcp/` |
| 外部交接 | `engine/handoff.py` ↔ `src-tauri/src/main.rs::parse_open_args()` |
| 内置 AI | `engine/ai_bridge.py`、`ai_providers.py`、`ai_history.py`，`/api/ai/*`，前端 `store/aiStore.ts`、`components/ai/` |
| 遥测 | `engine/telemetry.py` + 代理白名单，三档同意，`TAVOTTO_NO_TELEMETRY=1` 硬开关；前端 `lib/telemetry.ts`、`store/telemetryStore.ts` |
| i18n | `web/src/i18n/`（8 个命名空间：common/workspace/project/inspector/dialogs/errors/ai/shortcuts），门禁 `pnpm i18n:check` |
| 诊断 | `engine/diagnostics.py`、`diagnostics_frontend.py`（`docs/adr/0016`），`web/src/diagnostics/` |
| **教程 / onboarding** | **不存在**（全仓搜 `tutorial`/`onboarding` 零命中）——Prompt 20/21 是全新实现 |

---

## 9. 打包

`packaging/`（wheel / sdist / 内置渲染 runtime / PyInstaller / macOS 签名），
`src-tauri/`（桌面壳、ACL、更新通道）。平台支持口径唯一出处
`docs/support-matrix.json`。
