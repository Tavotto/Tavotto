# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-26
- 当前 branch：`compat/bridge-session04-runtime-asset`（worktree
  `.claude/worktrees/compat-bridge-session01`，stacked 在
  `compat/bridge-session03-script-probe` → `…session02-execution-spec` →
  `…session01-audit` 之上；四支均未推送——按审计 §七，Session 2–6 合成
  一个 PR 1 再走 push → PR → merge）
- 基于 commit：Session 3 落库提交 `9d11465`
- 本 Session Prompt：`05_SESSION_04_RUNTIME_FIGURE_ASSET.md`（外部实施包）
- 目标 PR：PR 1（本 Session 交付 RuntimeFigureAsset 数据模型、cache、
  保存/重开、导出与 writeback 硬拒绝）
- 当前工作树状态：见 git log（本 Session 一个提交）

## 本轮唯一目标

让没有磁盘 PDF/PNG/SVG 的 live Figure 成为正式素材类型
**RuntimeFigureAsset**（ADR 0013 定稿并落地）：稳定身份、materialized
cache、文档持久化、lazy rehydrate、渲染/导出、正确的 writeback 能力。
**不做**普通素材库 UI（Session 5）、`tavotto open` 自动 probe（Session 6）、
native run、Artist fallback、代码生成；safe 安全边界零改动。

## 已完成

- [x] **ADR 0013 Proposed → Accepted**：三个待定稿事项裁决（lazy build 案 /
  renderStore 键形态 + 消费点 sweep / 项目包只带描述符 + 脚本），落地记录
  与看护清单写进 ADR「定稿裁决」「落地记录」两节。
- [x] **引擎唯一实现 `engine/runtimeasset.py`**（纯标准库，Flask import）：
  * `is_runtime_id`（只看前缀）/ `resolve`（注册表正向重算 id，**绝不反解**
    ——脚本名里可以有 `#`，有用例钉住）；
  * materialized cache：`data_dir()/cache/runtime/<slug>/`
    （preview.svg + metadata.json；metadata 标 `generated_by: "Tavotto"`、
    **永远最后写**、坏/错版当没有；`prune_cache` 与引擎会话缓存同一治理，
    app 启动线程调用）；slug = sha256(规范化项目路径|asset_id) 截断，
    只是文件名安全化不是身份；
  * `stale_status`：六档 `fresh / possibly_stale / missing_source /
    missing_environment / needs_rerun / rerun_failed`（判据 = 脚本
    sha256 + 注册表 entry；**只是提示**，文案说「可能已变化」；
    rerun_failed 的 producer 在前端）。注册表条目丢失用文档描述块兜底，
    fail closed（重算 id 对不上 → 未知）；
  * `writeback_rejection`：v1 全拒的唯一裁决出处。
- [x] **app.py 接线**：`_engine_worker` runtime 分支（渲染/preview_png/png/
  svg/history 全族自动获得 runtime 支持）；probe 成功与 runtime 渲染成功时
  `_materialize_runtime`（预览取热 worker build 已写好的 SVG +
  `worker.last_build_descriptors`，**绝不二次执行**）；新端点
  `POST /api/runtime/status`（只读）与 `GET /api/runtime/preview`（cache
  预览，404 = 尚未物化，不是错误）；`update_source` / `history/restore`
  对 runtime id **硬拒绝** 400 `runtime_asset_has_no_original_artifact`；
  `_resolve_panel_source` runtime 分支——导出永远 live worker 全质量
  渲染（cache 文件绝不冒充结果）；`/api/package` 带描述符 + 脚本
  （`runtime_assets` 键），`package/open` 不把 runtime 记缺失。
- [x] **pool**：两条控制面的 `ensure_built` 缓存
  `last_build_descriptors`（物化 cache 的数据来源；加属性，协议零改动）。
- [x] **文档 schema（不升版）**：`PanelObject.fileKind` 增 `'runtime'`
  取值 + 可选 `source` 描述块（RuntimePanelSource）；`panelKind()` 判别器
  ——未知取值 fail closed（占位显示，绝不猜成文件路径）；
  `migrateToProject` 零改动（保真用例钉住）。
- [x] **前端接线**：`api.ts`（RuntimeStaleStatus / fetchRuntimeStatus /
  runtimePreviewUrl；`panelSrc` 三形态 + unknown 回 null）；新
  `runtimeAssetStore`（状态查询幂等、失败按 needs_rerun、markFresh /
  markRerunFailed / invalidate）；`renderTargets` 的 **lazy 门**（runtime
  面板只有 editing 或本会话 latest 才入队；tracked 不构成自动重跑理由）；
  `PanelView`（cache 预览 → 引擎产物 → 占位的显示阶梯 + 六档 stale 角标）；
  `useWriteBackTargets` 与单面板写回按钮排除 runtime（后端硬拒绝仍兜底）；
  preflight spec 的 kind/missing 映射（runtime 按矢量档、不算 missing，
  golden vectors 枚举不扩张）；`useServerEvents` 用**描述块** stem 认领
  脚本变更（不解析 id）；项目切换清 runtimeAssetStore。
- [x] **最小开发入口**（普通素材库 UI 留给 Session 5）：RegistryDialog
  probe 成功后每张捕获图一个「把 <stem> 添加到画布」按钮 →
  `actions.addRuntimePanel(descriptor)`（fileId = asset id、写入 source
  块、overrides 从空开始）。
- [x] **错误码**：`runtime_asset_unknown` / `runtime_asset_has_no_original_artifact`
  / `runtime_cache_missing` 进 USER_VISIBLE_CODES（app 层字面量 + 双语
  文案）；`runtime_source_writeback_unsupported` 无 producer 端点，码表 +
  文案先行（对拍用例在 test_runtime_asset）。
- [x] **测试**：`tests/test_runtime_asset.py` 30 项（身份/解析、cache
  原子性、六档 stale、writeback 契约、产品 API 全链、项目搬移、导出
  sentinel、fail closed、项目包）；前端 `useEngineSync.test.ts` +4（lazy
  门）、`runtimeAssetStore.test.ts` 5 项、`document.test.ts` +3
  （AssetSource 双形态）。五条负向反证完成（见下）。
- [x] 两个受管产物重建且 `--check` 一致：playground 指纹
  `d61000f4a72d1f24`、canvas.html 指纹 `d709d1b1574acfc4`（widget 打包了
  api.ts/PanelView 等，逐字节有变化）。PR 1 合并后 re-sync 网站仓库。

## 未完成

- [ ]（无——本轮范围内全部完成）

## 本轮关键决策

### 决策 1：runtime id 的后端解析 = 注册表正向重算，不是反解

- id 是不透明标识（ADR 0013 §2），脚本名里可以有 `#`。`resolve()` 对注册
  表里每对 (script, stem) 重算 `runtime_asset_id` 与目标比对；注册表随
  图库走，项目拷贝/搬移后解析链原样成立（move 用例钉住）。
- 推论：**注册表是 runtime 面板可渲染性的权威**。条目被删 → 渲染 404
  `runtime_asset_unknown`；文档描述块只用于 status 的恢复线索（fail
  closed）与 stale 认领，绝不直接当执行参数。

### 决策 2：cache 预览 = 最近一次成功 build/渲染的展示态

- probe 时物化 build 原样预览；runtime 渲染成功后刷新（此时 SVG 可能带
  overrides）。cache 是**显示占位**，不参与导出、不是基线语义——重开
  文档显示的是「上次画好的样子」，与 renderStore.latest 的退路同理。
- metadata 的 script_sha256 在物化那一刻重采——脚本改了、用户重跑成功，
  stale 判定自动回 fresh，不需要额外失效协议。

### 决策 3：lazy 门放在 renderTargets，tracked 不触发 runtime 自动重跑

- 重开文档带 overrides 的 runtime 面板**不**入渲染队列（文件面板会）；
  入队条件 = 正在编辑 或 本会话已跑过（latest 有它）。脚本变更
  （watcher/AI）对「本会话已跑过」的 runtime 面板照常热重建（与文件面板
  同待遇），对重开未跑的只作废 stale 判定、亮角标。
- `/api/runtime/status`、`/api/runtime/preview` 结构上只读（用例断言全程
  `engine_pool._workers` 无该脚本的会话）。

### 决策 4：runtime id 的写回一律 400，savefig-有产物的写回走 FileAsset 身份

- ADR §7 的「savefig 且磁盘有产物照旧写回」指它的**文件面板**身份
  （scan_panels 列出的那份，事务防线一条不少）；对 `runtime:` id 的写回
  请求没有第二种解释，v1 无条件 `runtime_asset_has_no_original_artifact`。
  能力位 `can_writeback_artifact` 仍由 figcapture 工厂**派生**（负向反证
  #3 打在派生上）。

### 决策 5：`runtime_source_writeback_unsupported` 先落表、无 producer

- v1 没有任何改写脚本源码的端点，硬造一个只为发码是伪 API。码表
  （`runtimeasset.ERROR_*` + `writeback_rejection("source")`）与双语文案
  先行，对拍用例看护；将来 source writeback 端点出现时直接消费。
  它因此**不在** test_error_codes 的 USER_VISIBLE_CODES（那张表要求
  app.py 有字面量 producer），登记纪律写在表旁注释里。

## 架构与数据契约

### 新增/修改接口

```text
engine/runtimeasset.py（新，纯标准库）
  RUNTIME_PREFIX / CACHE_SCHEMA = 1
  STALE_*（六档）/ ERROR_*（四码）
  is_runtime_id(id) / resolve(id, registry)
  cache_dir / materialize / load_metadata / preview_path / drop_cache
  script_sha256 / stale_status / prune_cache / writeback_rejection

engine/pool.py
  EngineWorker.last_build_descriptors / WorkerdWorker 同名（ensure_built 缓存）

web/src/types/document.ts
  PanelFileKind = 'pdf' | 'raster' | 'runtime'
  RuntimePanelSource / PanelObject.source? / panelKind() / isRuntimePanel()

web/src/store/runtimeAssetStore.ts（新）
web/src/store/actions.ts  addRuntimePanel(descriptor)
web/src/hooks/useEngineSync.ts  renderTargets(objects, editing, tracked, latest)
```

### API/协议形状

```jsonc
// POST /api/runtime/status  {id, source?: {script, stem}} →
{ "id": "runtime:a.py#a", "status": "fresh", "script": "a.py", "stem": "a",
  "entry": "__main__", "registered": true, "cached": true }
// GET /api/runtime/preview?id=…  → image/svg+xml（no-store）
//   404 {code: "runtime_cache_missing"} = 尚未物化（不是错误路径）
// /api/engine/render、preview_png 等：id 可以是 runtime:…（形状不变）
// /api/engine/update_source、history/restore：runtime id → 400
//   {code: "runtime_asset_has_no_original_artifact"}
// POST /api/package 清单新增 "runtime_assets": [{id, kind: "runtime",
//   script, stem}]（旧读取端忽略）
// 文档 schema（project=3/canvas=2 不升版）：PanelObject 增
//   fileKind: "runtime" 枚举值 + 可选 source 块
```

worker/browser 协议**零改动**（last_build_descriptors 是调用方缓存）。

### 稳定错误码（本轮新增）

```text
runtime_asset_unknown                    app（404；渲染/导出/status 解析不到）
runtime_asset_has_no_original_artifact   app（400；artifact 写回硬拒绝）
runtime_cache_missing                    app（404；preview 尚未物化）
runtime_source_writeback_unsupported     码表+文案先行（无 producer，决策 5）
```

### Schema/version 变化

- 文档 schema 不升版（可选字段 + 枚举值；旧前端把 runtime 面板当缺失素材
  显示，不崩不丢）。cache schema = 1（runtimeasset.CACHE_SCHEMA，旧版本
  目录按没有 cache 处理——迁移 = 重建）。注册表格式零改动。

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| src/tavotto/engine/runtimeasset.py（新） | 引擎唯一实现 | test_runtime_asset 30 项 |
| src/tavotto/engine/pool.py | ensure_built 缓存描述符（两控制面） | test_runtime_asset + 既有池套件原样绿 |
| src/tavotto/app.py | runtime 分支/端点/硬拒绝/物化/包 | test_runtime_asset::TestRuntimeProductApi |
| tests/test_error_codes.py | +3 code 登记 | — |
| docs/adr/0013-runtime-figure-assets.md | 定稿 | 文档 |
| src/tavotto/AGENTS.md | 记录 Session 4 语义 | 文档 |
| web/src/types/document.ts(+test) | AssetSource 双形态 | vitest |
| web/src/lib/api.ts | runtime 类型与端点、panelSrc 三形态 | vitest（间接） |
| web/src/store/runtimeAssetStore.ts(+test)（新） | stale/cache 会话态 | vitest 5 项 |
| web/src/store/actions.ts | addRuntimePanel | vitest（间接） |
| web/src/hooks/useEngineSync.ts(+test) | lazy 门 | vitest +4 |
| web/src/canvas/PanelView.tsx | 显示阶梯 + 角标 + 占位 | 手工 + 既有面板套件绿 |
| web/src/components/inspector/UpdateSourceButton.tsx | 排除 runtime | 既有套件绿 |
| web/src/lib/preflight.ts | kind/missing 映射 | 既有 preflight 套件绿 |
| web/src/components/RegistryDialog.tsx | 开发入口「添加到画布」 | 手工 |
| web/src/hooks/useServerEvents.ts | 描述块认领 stale | 既有套件绿 |
| web/src/store/projectStore.ts | 换项目清 store | — |
| web/src/components/VersionDialog.tsx | panelSrc 可空 | 类型 |
| web/src/i18n/locales/*/{errors,workspace,dialogs}.json + resources.d.ts | 双语文案 | i18n:check + 码表对拍 |
| web/dist-playground（gitignore）/ codex-plugin/mcp/widget/canvas.html | 受管产物 | 各自 --check |
| docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md | 本文件 | — |

## 实际运行的测试

```bash
# worktree 内、PYTHONPATH=src、主仓 .venv 解释器（worktree 无 .venv）
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest \
    tests/test_runtime_asset.py                       # 30 passed
PYTHONPATH=src …/python -m pytest tests/test_error_codes.py \
    tests/test_script_probe.py tests/test_projects.py \
    tests/test_compat_capture_parity.py               # 173 passed
PYTHONPATH=src …/python -m pytest tests/test_write_back.py \
    tests/test_worker_roundtrip.py tests/test_workerd_pool.py \
    tests/test_execspec.py                            # 144 passed, 6 skipped
PYTHONPATH=src …/python -m pytest -q                  # 全量 2097 项 exit 0
                                                       #（workerd 6 skip，本机无产物）
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
    # 24 cases：21 full / 3 partial；product_bug 0；门禁 nightly 通过
python scripts/build_browser_playground.py && … --check   # d61000f4a72d1f24
python scripts/build_mcp_widget.py && … --check           # d709d1b1574acfc4
cd web && pnpm test（83 文件 925 项）&& pnpm build && pnpm i18n:check
```

（坑复述：asset id 里有 `#`，GET 请求必须 encodeURIComponent / test client
用 query_string——裸拼 URL 会把 stem 当 fragment 吃掉，表现是 preview
永远 404。）

## 负向反证（本轮五条，全部先红后还原）

| # | 变异 | 判据测试 | 结果 |
|---|---|---|---|
| 1 | `runtime_asset_id` 混入绝对路径（normalize 前拼 os.getcwd()） | `TestRuntimeProductApi::test_project_move_keeps_the_identity`（+ figcapture 相对路径校验用例连锁红） | **红**（还原后绿） |
| 2 | 导出 runtime 分支直接返回 cache 的 preview 文件 | `test_export_uses_the_live_worker_not_the_cache` | **红**（还原后绿） |
| 3 | `build_descriptor` 把 `can_writeback_artifact` 硬设 True | `TestWritebackContract::test_descriptor_capabilities_are_derived_not_declared`（+ capture parity 套件连锁红） | **红**（还原后绿） |
| 4 | `/api/runtime/status` 顺手 `pool.get(...).ensure_built()` | `test_status_and_preview_never_execute_the_script` | **红**（还原后绿） |
| 5 | `materialize` 先写 metadata 再写 preview | `TestCache::test_metadata_is_written_last` | **红**（还原后绿） |

## 真机/产品证据

- OS：macOS（arm64，本机开发环境）。产品 UI 变化：RegistryDialog probe
  成功后的「添加到画布」按钮（开发/高级验证入口）、runtime 面板的画布
  显示（占位/预览/角标）。素材库、`tavotto open`、MCP 普通入口均未触碰。
- workerd 腿本机 skip（未 cargo build）：runtime 渲染走 Python 池实测；
  workerd 侧 `last_build_descriptors` 为代码级同构（同一调用面）。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| runtime 素材在素材库面板不可见（只有 RegistryDialog 开发入口能放上画布） | asset_model × desktop | 高 | 否（设计如此——普通入口是 Session 5 的唯一目标） | Session 5 |
| `tavotto open script.py` 仍不自动 probe | product_entry × cli | 高 | 否 | Session 6 |
| probe/渲染仍是同步阻塞：分钟级脚本没有进度/取消 | product_entry × desktop | 中 | 否（审计风险 #4） | Session 5/6（SSE 化时复用码表） |
| MCP 内嵌画布对 runtime 面板：transport.panelSrc 回 null → 显示退回权威 SVG（本会话没跑过则占位）；MCP 侧无 probe 入口，runtime 面板只能从桌面文档带过去 | product_entry × mcp | 低 | 是（记录在案） | Session 5+ 视需要 |
| 跨项目粘贴 runtime 面板：目标项目注册表没有该 (script, stem) 时渲染 404 `runtime_asset_unknown`（fail closed，角标提示） | asset_model | 低 | 是（诚实边界） | 不修（描述块在，重跑即恢复） |
| cache 预览是「上次画好的样子」，不区分变体（同 asset 多面板共用一份占位） | asset_model | 低 | 是（决策 2 记录在案） | 不修 |
| preflight 对 runtime 面板按矢量档判，位图内文字类检查不适用（它本来就没有位图原件） | preflight | 低 | 是 | 不修 |

## 不得被下一 Session 破坏的约束

- Session 2/3 的全部约束仍然有效（ExecutionSpec/描述符唯一语义、发现维
  两视图一 walk、probe 错误码契约、成功 probe 一次执行、路径校验 realpath、
  stem 冲突显式化、rel_key 唯一出处、受管产物重建）。
- **runtime id 是不透明标识**：新入口（素材库、CLI、MCP）要拿 script/stem
  一律走 `runtimeasset.resolve` 或描述符字段，**绝不 split('#')**（用例
  `test_resolve_never_parses_the_id` 看护）。
- **打开项目/文档绝不执行脚本**：Session 5 的素材库入口、Session 6 的
  `tavotto open` 自动 probe 都必须是显式用户动作触发；
  `/api/runtime/status`、`/api/runtime/preview` 保持只读（零执行用例看护）。
- **cache 是派生物**：任何新消费点不得把 `cache/runtime` 里的文件当导出
  结果或用户原件交出去（sentinel 用例看护导出面）；metadata 最后写的
  顺序不许动（原子性用例看护）。
- **writeback 拒绝在后端**：Session 5 的 UI 隐藏入口只是礼貌，
  `update_source`/`restore` 的 runtime 400 不许放松；能力位只能由
  figcapture 工厂派生。
- **lazy 门**：`renderTargets` 的 runtime 分支（editing / latest）是
  「重开不自动执行」的唯一前端闸门，改同步逻辑必须保住那 4 条 vitest。
- probe 物化用 `worker.last_build_descriptors` + build 已写好的 SVG——
  **不得为拿预览/描述符增加第二次执行**（execution-count 用例看护）。

## 下一 Session 唯一目标

> 素材库普通入口（Session 5）：在素材面板展示「图」与「脚本」两类条目，
> 提供「运行并发现图」、运行状态、多 Figure 结果、Runtime badge、
> stale/re-run 操作、错误恢复与安全模式说明；不实现 native backend。
> （复用 `probe_and_register` 的冲突守卫与 `runtimeasset` 的状态/预览，
> 不另起第二套语义。）

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md（含 src/tavotto/AGENTS.md 的 RuntimeFigureAsset 一节）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/adr/0013-runtime-figure-assets.md（已 Accepted；§定稿裁决）
src/tavotto/engine/runtimeasset.py（状态/预览/解析的唯一实现）
src/tavotto/engine/probe.py（script_inventory——素材库「脚本」条目的数据源）
web/src/components/left/AssetBrowser.tsx（素材面板；「脚本」条目的接入点）
web/src/store/runtimeAssetStore.ts / actions.addRuntimePanel
web/src/components/RegistryDialog.tsx（probe 交互与错误展示的现成形态）
```

注意：素材库入口要复用 `/api/registry` 的 `all_scripts`（Session 3）+
`probe` + `addRuntimePanel`（Session 4）三件现成品；「运行并发现图」是
显式用户动作（总纲原则 5），运行中状态可先用同步请求 + busy 态，SSE 化
另立条目。stem 冲突走 `probe_and_register` 的守卫，不得绕过。

## 建议启动命令

```bash
git status --short && git log -8 --oneline
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_runtime_asset.py tests/test_script_probe.py
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
