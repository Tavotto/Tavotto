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
| 打开（复用已开的） | `app.py:1198 open_project()` |
| 项目上下文对象 | `app.py:413 class ProjectCtx`、`app.py:429 _project_id()` |
| 每请求解析当前项目 | `app.py:442 _request_ctx()` / `464 current_ctx()` |
| 关闭一个项目 | `app.py:1245 close_project()`（停它的 watcher + 收它的 worker） |
| 全部关闭 | `app.py:1271 reset_projects()` |
| HTTP 入口 | `POST /api/projects/open` `…/close` `…/remove`，`GET /api/projects`、`/api/projects/recent`、`/api/projects/browse` |
| 项目内收纳目录 | `app.py:1303 project_store_dir()` → `<项目>/tavottofile/`（常量 `engine/config.py:240 PROJECT_STORE_DIRNAME`） |
| 导出 / 备份目录 | `app.py:1312 project_export_dir()`、`1322 project_backup_dir()` |
| 前端 | `web/src/store/projectStore.ts`、`components/ProjectPicker.tsx`、`ProjectSwitcher.tsx` |

**项目身份**按路径归一化（`engine/config.py path_is_case_insensitive()`），
大小写不敏感卷上同一目录的两种写法归为一个项目。

**多项目隔离是既有不变量**：watcher、worker 池、`baked_overrides/<项目id>.json`
全部按 `ctx.id` 分键；SSE 事件带 `pj` 字段（`app.py:1169 _script_change_handler`）。

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
| watcher | `engine/pool.py:2003 _watchers`（**每项目一个**），`start_watcher/stop_watcher`；mtime 轮询，约 2 秒窗口 |
| watcher 回调 | `app.py:1169 _script_change_handler()` → `sse_publish("panel.file_changed", {...,"pj": ctx.id})` |

**「不静默执行用户脚本」在现状里的落实方式**：扫描 / 注册表 / watcher 都只做
静态读取与 mtime 比较；真正跑用户代码的只有显式的 probe、渲染请求
（`POST /api/engine/render`）与 native 会话（`docs/adr/0014`、`0020`，
`/api/native/*` 全部要用户批准，`engine/nativeperm.py`）。

---

## 4. SSE → 前端 store

| 环节 | 位置 |
| --- | --- |
| 发布 | `app.py:1133 sse_publish()`（进程内 `queue.Queue`，maxsize 200） |
| 端点 | `GET /api/events`（`app.py:1142`），15s 心跳注释行 |
| 前端消费 | `web/src/store/assetStore.ts`、`renderStore.ts`、`nativeSessionStore.ts`、`scriptRunStore.ts` |
| 事件按项目隔离 | 事件体里的 `pj` 字段；前端按当前项目过滤 |

现状**没有**独立的「项目 watcher + 批次合并」层：watcher 事件一条一条
直接 `sse_publish`，批次合并/防抖在前端各 store 里各做各的。Prompt 04–06 的
统一 refresh 服务要落在这里，而不是再加一条并行通道。

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

**现状没有独立的「快速编辑工作流」**：图内编辑必须先把面板放进画布。
这正是 Prompt 09 要解决的核心产品缺口。

### 5.2 画布

`web/src/canvas/CanvasStage.tsx`、`PanelView.tsx`、`ObjectView.tsx`、
`OverlaySvg.tsx`、`ContextBar.tsx`、`interactions.ts`；几何权威
`exactPanelRender`（`docs/adr/0016`、`0017`）。

### 5.3 导出

| 环节 | 位置 |
| --- | --- |
| 载荷构造 | `web/src/lib/exportPayload.ts`、默认值 `lib/exportDefaults.ts`（localStorage 偏好） |
| UI | `web/src/components/ExportDialog.tsx` |
| 后端 | `POST /api/export`（`app.py:860`），面板取源 `app.py:816 _resolve_panel_source()` |
| 打包（图 + 脚本 + 证明） | `POST /api/package`（`app.py:989`） |
| 合成 | `pdfbackend/`（全仓唯一 import pymupdf 的模块） |

---

## 6. Style / Spec / Validation / Export 的现状数据来源

| 层 | 现状唯一出处 | 备注 |
| --- | --- | --- |
| **Spec**（出版规范） | `src/tavotto/profiles/publication.json` | 两侧求值器共读；`default_profile: lab-publication-v1` |
| Spec 求值器（Python） | `engine/preflight.py` | MCP / 后端走这条 |
| Spec 求值器（TS） | `web/src/lib/preflight.ts` | 浏览器里跑不了 Python，所以有两份 |
| 两份求值器的对齐 | `tests/golden/preflight_vectors.json` | pytest 与 vitest 各跑一遍同一份向量 |
| 文档上的规范绑定 | `DocumentProfile { id, journal? }`，在 `CanvasData.profile` | 只存 id 与期刊覆盖，**规则本身一条都不进文档** |
| **Style**（命名样式预设） | `LAYOUT_DIR/_styles.json`（文件名常量在 `engine/documents.STYLES_FILENAME`），`GET/POST/DELETE /api/styles` | 跨文档共享，上限 100 条；前端 `components/StyleDialog.tsx`、`lib/stylePresets.ts` |
| **Export** | `web/src/lib/exportDefaults.ts`（localStorage）+ `exportPayload.ts` | 与 Spec 无耦合 |

最小字号现状：`absolute_min_font_size_pt: 8.0`，
`legend_policy.min_font_size_pt: 8.5`（profile 里注明是"从严"的本项目补充）。
Prompt 10 的「统一为 8 pt」要在 **profile 文件**里改，不在两个求值器里改。

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
