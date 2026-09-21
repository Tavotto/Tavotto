# src/tavotto/ — 后端与渲染引擎规则（速查表）

仓库级路由、跨仓库不变量与验证命令在根 `AGENTS.md`；前端在 `web/AGENTS.md`，
插件与 MCP server 在 `codex-plugin/AGENTS.md`，打包与内置 runtime 在
`packaging/AGENTS.md`。本层规则的**全文按主题**在 `docs/rules/backend/`：
先在下表按改动路径找到主题，读那一份细则（各 1–10 KB）与它点名的 ADR，再动手。
规则改在细则文件里，并同步这里那一行；这里不放第二份全文。

技术形状：Flask 后端（`src/tavotto/app.py`）+ PyMuPDF（**只经
`src/tavotto/pdfbackend/`**）；渲染引擎 `engine/` 在子进程 / 用户解释器里跑
matplotlib；前端 `web/`（Vite + React 19 + TS + Tailwind v4）。

## 本层不可破坏

- **进程边界**：Flask 父进程（`.venv` 只有 flask + pymupdf）import 链上的
  engine 模块纯标准库；`worker.py` / `manifest.py` / `overrides.py` /
  `figsession.py` / `wireproto.py` 只在子进程；`bridge_runner.py` /
  `bridgeboot.py` 在**用户的**解释器里跑：纯标准库、3.10 可跑、启动阶段绝不
  import matplotlib。可写数据只走 `engine/config.data_dir()`。
- **PDF 库边界**：`pdfbackend/pymupdf_backend.py` 是全仓唯一 import pymupdf 的
  模块，`app.py` 只认 `pdfbackend/__init__.py` 的契约名。
- **写回是事务**：prepare → verify（一次性 worker 全量重放 + 几何比对 + 像素门）
  → commit，任一环不过 409 且原件零改动；worker 一条 warning 即阻断。
- **会话认证**旁路只有三个（pytest test_client / `--insecure-no-auth` /
  `TAVOTTO_INSECURE_NO_AUTH=1`），新端点不得绕过 guard。
- **热会话状态 == 全量重放**：override 按 `_apply_rank` 七档顺序应用，figure
  锚定 prop 每次重放；「有哪些 axes」只有 `axestraversal.ordered_axes` 一处（AST 门禁）。
- **worker 生命周期**：请求一律有超时；状态未知的 worker 绝不复用；`kill()` 之后
  必须 reap；一次性目录删除不许 `ignore_errors=True`。
- **「谁来渲染」只在 `enginesession.resolve()` 分支**；native 会话绝不进池；
  环境占用只有 `envlease` 一张表。
- **遥测**纯标准库、三档同意、白名单结构性防线；CI 与测试里
  `TAVOTTO_NO_TELEMETRY=1`，worker 对遥测一无所知。
- **每个项目自己的**：`baked_overrides/<项目id>.json`、worker 池键、watcher、
  后台线程必须 `app.bound_project(ctx)`——不绑定 = 成功地导出了另一个图库的同名图。

## 按改动路径找细则

| 改到 | 主题（细则在 `docs/rules/backend/`） | 必守要点 | 看护 |
| --- | --- | --- | --- |
| `engine/brand.py`、任何品牌 / 标识符 / 存储键 | 品牌与命名 → `brand-and-naming.md` | 干净断裂：旧名一律不认、不加 LEGACY_；唯二例外 `mm_registry.json`（`registry.existing_registry_path()`）与 `MM_WORKER_PYTHON`（`pool.worker_python_env()`）只在读取端回退；桌面 id `com.tavotto.tavotto` | `tests/test_desktop_launch.py`、`tests/test_handoff.py` |
| Flask 侧 / 子进程侧 / bridge 侧任一模块的 import | 进程与依赖边界 → `process-boundaries.md` | 三侧模块名单与各自允许的依赖；解释器由 `pool.find_worker_python()` 探测（`TAVOTTO_WORKER_PYTHON` 覆盖） | `tests/test_install_locate.py`（`test_subcommands_run_without_flask_or_pymupdf`）、`tests/bridge/` |
| `pdfbackend/`、`glyphplan.py`、字体 / 色图事实、`/api/render` 缓存 | PDF 后端边界 → `pdf-backend-boundary.md` | 字形归属四层只在 `glyphplan.py`；本机字体族在 manifest 顶层 `font_families` 只发一次；「自定义色图」判对象不判名（`_registered_colormap`）；CJK 回退尾巴必须在 `font.family` 列表里；缓存键 `sha1(id\|内容\|宽\|后端-版本)` 不用 mtime，Windows 上 `os.replace` 撞读者退让 | `tests/test_font_provenance.py`、`test_font_family_options.py`、`test_cmap_facts.py`、`test_cjk_figure_text.py`、`test_render_cache.py`、`test_windows_regressions.py` |
| `engine/updater.py`、`/api/update/*` | 检查更新 → `update-check.md` | 纯标准库；升级永不静默、升级后 `restart_required`；源码安装只提示 `git pull`；桌面模式整个关掉 | `tests/test_updater.py` |
| `engine/figcapture.py`、`execspec.py`、`workdir.py`、`worker.py` 的 savefig 拦截与 sys.argv | Figure 捕获、执行描述与 live-figure 会话 → `figure-capture-and-execution.md` | 捕获策略两条入口同一份实现；fallback stem 按本次捕获序号；相对路径只读回退四条同时成立才改指（`builtins.open` / `io.open` / 3.10 的 `Path.open` 三处都 patch）、不扩到 `exists` / `glob`；`safe_spec()` / `worker_argv()` 唯一出处；`import paper_style` 留在 try 里、排在 argv 换好之后且在 SystemExit 保护内；`sys.argv` 换成脚本自己的 | `tests/test_compat_capture_parity.py`、`test_execspec.py`、`test_workerd_pool.py`、`test_workdir_mode.py`、`test_zero_capture.py` |
| `engine/pool.py`、`wireproto.py`、`workerd_client.py`、超时 / 关停 / 计时 | worker 协议、计时、超时与关停 → `worker-protocol-and-lifecycle.md` | 协议 v1 信封原样回显、`request_id` 对不上 kill；patch 规范化唯一权威 `patchspec.py`（↔ Rust 逐字节）；build 用静默看门狗（ADR 0050）；管道 EOF 先 `EXIT_GRACE` 内问退出状态再 kill，退出码进信封、解释只在 `pool.describe_exit` / `session_dead_message`，worker 开 faulthandler、脚本 `sys.exit(0)` 是正常结束（#435）；`_terminate_and_reap()` / `_kill_and_reap()` 闭环；export / preview_png 状态中立 | `tests/test_worker_protocol.py`、`test_worker_roundtrip.py`、`test_worker_exit_report.py`、`test_workerd_client.py`、`test_build_watchdog.py`、`test_windows_regressions.py` |
| `engine/overrides.py` 的 `apply` / `_apply_rank` / `_FRAC_ANCHORED`、tight 布局、字体脸、`normalize.py` 三模块 | override 语义、应用顺序与全量重放 → `override-application-and-replay.md` | override 是全量列表；七档顺序是契约；刻度类与 frac 锚定 prop 每次重放；`PinnedTightLayoutEngine` 安装点只有一个、`set_position` 排在换引擎之前；PDF/PS 一律 fonttype 42；`face` / `math_face` 唯一出处 `manifest.font_faces()`；文字背景框显隐只归 `bbox_visible`，样式不露框；原样是模式的 getter 回哨兵（`_AUTOSCALE` / `_NO_BBOX` / `_PatchEdge` / `_PatchFace`）；颜色字段 alpha 0 报 `NO_COLOR` | `tests/test_invariants_engine.py`、`test_text_bbox_visibility.py`、`test_patch_edgecolor_mode.py`、`test_equivalence_matrix.py`、`test_layout_engine_pinning.py`、`test_scientific_text_matrix.py`、`test_normalize.py` |
| `axestraversal.ordered_axes`、`spinemodel.py`、`tickmodel.py`、`colorbarmodel.py`、`legendmodel.py`、`_cls_key`、能力表、3D / 箭头 / 散点 marker | axes 遍历、特殊 artist 与 artist family 能力层 → `axes-and-artist-families.md` | `fig.axes` 只许出现在白名单函数里；族模块（`axestraversal` / `spinemodel` / `tickmodel` / `colorbarmodel` / `legendmodel`，基座 `pathgeom`）是叶子、只依赖排在前面的族、只迁移一份、`HANDLERS` 只经展开登记、`RESTORE` 必须并进 `_RESTORE`；编号顺序 `fig.axes` → 子 axes → 寄生轴是契约；能力按真实 getter 实况判不按类名（映射中的 Collection 不给 facecolor）；认不出的 Artist 只开 visible / zorder；annotate 的箭头绝不出端点 | `tests/test_axes_traversal_authority.py`、`tests/test_engine_family_modules.py`、`test_parasite_axes.py`、`test_artist_families.py`、`test_worker_roundtrip.py` |
| `_marker_field` / `marker_current` / `marker_original`、`engine/pathgeom.py` | 标记形状事实与路径几何 → `marker-and-path-geometry.md` | 形状是只读派生事实不是取值，13 个名字之外发归一化几何；发不发看 `state.applied`；geometry 是渲染派生数据不进文档；散点 / 纯 marker 线 / 柱逐个描，超过 `MAX_MARKERS` 整组退回 bbox | `tests/test_manifest_marker_shape.py`、`test_manifest_geometry.py` |
| `legendmodel.py`（`LegendEntries`、`rebuild_legend`、`legend_pos_cfg`）、`FigState.reapply` | 图例条目模型与位置模型 → `legend-model.md` | `texts_j` 的 j 是原始序号；重建型 prop 一律 `rebuild_legend`，不把 `legend_handles` 副本喂回；脱开的项 = 脚本原样 + 文档里的 handle_*（没有会话内的 custom_base）；三条位置 prop 写槽位再整体重建，拖动过即绝对定位；`loc_anchor` 的 `null` 是取值；隐藏图例的文字几何按文档 dpi 现排（`manifest._layout_undrawn_legends`），不靠上一次 draw | `tests/test_legend_binding.py`、`test_legend_model_pairs.py`、`test_legend_anchor.py`、`test_legend_text.py`、`test_hidden_legend_geometry.py` |
| `colorbarmodel.py`（`_cb_reorient`、`extend`、`follow_map`）、色条轴 position | 色条：方向、延伸与大小 → `colorbar.md` | 就地结构改造、`fig.axes` 顺序不动；`_inside` 与 `extend` 一起改；改 extend 前 box_aspect 放回基线；`("axes","position")` 原样记 `get_position(original=True)`；色条轴上的 patch 不登记 | `tests/test_colorbar_orientation.py`、`test_colorbar_resize.py` |
| `tickmodel.py`（`tick_cfg` / `apply_tick_model` / `TickSet` / `TickLabel`）、`spinemodel.spine_cfg`、`manifest.spine_geometry` | 刻度定位、边框模型与 spines 几何 → `ticks-and-spines.md` | 刻度与边框都是「写进 cfg 再整体重建」，没表态 = 脚本原样；单条刻度文字冻结整条轴、序号越界抛异常；spines 端点取 `Spine.get_path()` 不用 `get_window_extent` | `tests/test_axes_ticks_scale.py`、`test_tick_sides_geometry.py`、`test_manifest_ticklabel_cost.py` |
| `engine/registry.py`、`discover.py`、`probe.py`、`/api/registry/*` | 注册表、静态扫描与试运行探测 → `registry-discovery-and-probe.md` | 注册表随图库走、冲突只报告不裁决；probe 绝不猜也绝不静默跳过，失败不写注册表、错误码闭集；同脚本互斥、取消当场 kill；成功路径只执行一次 | `tests/test_registry.py`、`test_discover.py`、`test_script_probe.py`、`test_asset_library.py` |
| `engine/runtimeasset.py`、`runtime:` id、`/api/runtime/*` | RuntimeFigureAsset → `runtime-figure-assets.md` | id 不透明、解析正向重算不反解；cache 是派生物、metadata 最后写；status / preview / assets 端点只读绝不执行；写回硬拒绝 `runtime_asset_has_no_original_artifact`；导出必须当次 live 渲染 | `tests/test_runtime_asset.py`、`test_asset_library.py` |
| `engine/previewbudget.py`、`preview_complexity.py`、`preview_hybrid.py`、`figsession.render()` | 编辑预览的表示法与复杂度预算 → `preview-complexity-budget.md` | 判定在 `read_text()` 之前；超限是一次成功渲染（只少 `svg`）；降级 ≠ 只读；分析器只算账不改 artist；rasterized 先读后写、还原失败要吵；成本模型改了必须重跑对拍 | `tests/test_preview_budget.py`、`test_preview_complexity.py`、`test_preview_hybrid.py`、`test_issue181_large_preview.py` |
| `engine/depresolve.py`、`managedenv.py`、`deprepair.py`、`projectenv.probe_environment`、`pool.mutating_environment` | 受控依赖修复与包管理 → `dependency-repair-and-packages.md` | 内置 runtime 永不是安装目标；import 名只认两档高置信解析；包名语法是安全边界、装前再验；计划绑定不是 `confirmed=true`；pip exit 0 ≠ 修好（三层验证）；查找走 `pip index versions`、`--retries 1` 是判据一部分；体检执行的是 `worker.py` 文件本身（不是清单、也不是 `import worker`），全局与项目路径同一份体检（#435） | `tests/test_dependency_repair.py`、`test_dependency_repair_e2e.py`、`test_package_management.py`、`test_package_lookup.py`、`test_environment_health_parity.py` |
| `figsession.py`、`wireproto.py`、`bridge_runner.py`、`bridgeboot.py` | 两条执行入口：safe worker 与 native bridge → `execution-entries.md` | 编辑语义只有一份；native 里兄弟模块只在模块层平铺 import 并登记进 `_PHASE2` / `_TOPLEVEL_TO_RESTORE`（函数体内裸 import 会命中用户文件）；native 侧不起后台线程；spike 不是产品 | `tests/test_worker_roundtrip.py`、`tests/bridge/test_bridge_namespace.py`、`tests/bridge/test_bridge_thread_model.py` |
| `runcli.py`、`runspec.py`、`nativerelay.py`、`nativesession.py`、`envlease.py`、`enginesession.py` | `tavotto run` 的控制面 → `tavotto-run-control-plane.md` | CLI 拥有用户的 Python；确认前一行用户代码不跑；Tavotto 只写 stderr（`--help` 唯一例外）；屏障释放必经 `release_barrier()`；socket 先 `shutdown(SHUT_RDWR)` 再 `close()` | `tests/native/`（含 `test_run_cli_integration.py`）、`tests/test_windows_regressions.py` |
| `/api/versions`、`/api/styles`、`/api/package`、`tavottofile/`、`atomicio.py`、`documents.loads_document`、自动保存槽位 | 布局版本、项目文件收纳与文档落盘 → `layout-versions-and-documents.md` | 版本上限条数 + 字节两条；文档落盘只有 `atomicio`（NaN/∞ 落盘前拒）、读侧同样有闸；`project_layout_dir()` 是收纳规则唯一出处；另存为与自动保存共用 `_revision_conflict`、锁不可重入、GET 不用 `send_file` | `tests/test_versions.py`、`test_document_persistence.py`、`test_package.py`、`test_ai_revert_atomic.py` |
| `/api/export*`、`engine/exportreq.py`、`exportjob.py`、`tiffwrite.py`、`_serialize_figure` | 导出（ADR 0031 / 0046） → `export-pipeline.md` | 五个端点一个服务；`original` 段没有 x/y/w/h；PPI 只在有位图格式时是数字；`partial` 独立一档；终局字段先于 `status` 可见；文件名规则严格同源对；EPS 只有 worker 写得出、不伪称矢量 | `tests/test_export_pipeline.py`、`test_export_request.py`、`test_export_endpoint.py`、`test_tiffwrite.py`、`test_epsfile.py`、`tests/golden/filename_vectors.json` |
| `app.py` 的 `PROJECTS` / `_request_ctx` / `bound_project`、`project_refresh.py`、`project_watch.py`、`readiness.py`、`originalspec.py`、`tutorial.py` | 项目系统（后端侧） → `project-system.md` | `pj` 查询参数与请求头两条路都认，指名不存在的 409 绝不落默认项目；基线按项目分键、绑定文件身份（`_baked_matches_file`）；派生刷新只有 `app.refresh_project()` 一条编排；readiness 六态十码闭集、只报告不动手、「没测量」不压成零；`originalspec` 先量后猜 | `tests/test_projects.py`、`test_paths_and_baked.py`、`test_project_refresh.py`、`test_project_watch.py`、`test_project_readiness.py`、`test_original_spec.py`、`test_tutorial.py` |
| `security.py`、`session_client.py`、任何新端点 | 会话认证（ADR 0008） → `session-auth.md` | nonce → bootstrap → HttpOnly cookie；Host 只认 `127.0.0.1:<port>`、带 Origin 必须同源；旁路只有三个 | `tests/test_browser_auth.py`、`scripts/smoke_app.py` 的 401 硬断言 |
| `engine/ai_bridge.py`、`ai_agents.py`、`ai_providers.py`、`codexinstall.py`、`/api/ai/*` | 编码 Agent 桥 → `ai-agent-bridge.md` | 「支持哪些 Agent」只在 `AGENT_REGISTRY`，通用层不许 `if agent == "codex"`；CLI 子进程一律 `spawn_env()`；就绪检查只跑官方本地状态命令、`claude auth status` 只取 `loggedIn`；`path_override` 必须 realpath + 指向该 Agent；spawn 时注入、绝不改写用户的 settings / config.toml；模型名不写进源码 | `tests/test_ai_agents.py`、`test_ai_bridge.py`、`test_ai_capabilities.py`、`test_ai_refresh.py`、`test_ai_history.py`、`test_codex_install_cli.py` |
| `richtext.py` | 文字：行内上下标与大小写 → `richtext.md` | ↔ `web/src/lib/richText.ts` 严格同源（三常量 + parse）；序列化按需转义；图内文字走 mathtext、大小写要 `protectMath` | `tests/test_compose_text.py`、`test_glyph_plan.py` |
| `profiles/publication.json`、`engine/profiles.py`、`preflight.py`、`profilestore.py` | 出版规范 profile 与预检 → `profiles-and-preflight.md` | 规则唯一权威是那份 JSON，两侧求值器靠 golden vectors 对齐（只比判据）；字号按最终物理尺寸判、阈值不进求值器；`clip_bbox` 只给 `element-outside-figure` 用；没登记的检查项兜底 warn；磁盘更高版本只读不写 | `tests/test_preflight.py`、`test_manifest_clip_bbox.py`、`test_profile_store.py`、`tests/golden/preflight_vectors.json` |
| `engine/telemetry.py`、`EVENTS`、`services/telemetry_proxy/`、`collect_distribution_metrics.py` | 匿名用量统计 → `telemetry.md` | 不引入任何分析 SDK；`capture()` 永不抛不阻塞、无落盘队列；成功边界埋点；加事件两侧表 + 样例 + 文档三处一起改、范围扩大升 `CONSENT_VERSION`；发行量指标绝不混进用户队列 | `tests/test_telemetry.py`、`test_telemetry_api.py`、`test_telemetry_proxy.py`、`test_telemetry_invariants.py`、`test_telemetry_disclosure.py`、`test_distribution_metrics.py` |
| `engine/diagnostics.py`、`diagnostics_frontend.py`、`/api/diagnostics/*` | 诊断包 → `diagnostics.md` | 先脱敏再交出、项目清单只留条数；schema 2 老三件不动；服务端第二道校验刻意与前端判据不同；坏载荷退化成不带前端文件的包、不 400；不写盘不上传不进 telemetry；traceback 配收尾异常行、worker.log 只带证据行（#435） | `tests/test_diagnostics_bundle.py`、`test_diagnostics_worker_evidence.py` |
| `_write_source_files`、`pool.one_shot()`、`REPLAY_PIXEL_TOL`、`/api/update_source`、`history/restore` | 写回事务 → `writeback-transaction.md` | `expected_mtime` / `script_sha1` 两道 prepare 校验；verify 用一次性 worker 全量重放 + 几何 + 像素（`pdfbackend.compare_png` 逐 RGBA 通道）；热态不是这组 patches 就报 `fresh_only` 不假比；commit 第 2+ 个撞锁回滚；泄漏断言只对本次 `one_shot()` 的 base 负责 | `tests/test_write_back.py`、`test_worker_roundtrip.py` 末节、`web` 的 `WriteBackDialog.test.tsx` |
| `engine/locate.py`、`cli.py`、`handoff.py`、`tavotto open` / `doctor`、桌面 argv | 外部交接 → `external-handoff.md` | 发现链唯一权威 `locate.py`，清单是缓存不是真相、不动用户 PATH；子命令分派在 import Flask 之前；`HandoffError` 一律带稳定 code；桌面契约 `desktop_argv()` ↔ `parse_open_args()`；macOS 走 `open -na … --args`；`ok: true` 是等出来的 | `tests/test_install_locate.py`、`test_handoff.py`、`test_open_script_route.py`、`test_desktop_launch.py` |
| `engine/preparation.py`、`receipt.py`、`execspec.launch_context`、`workdir.grant_for`、`depresolve.DependencyIntent`、`figcapture.SourceArtifact`、`exportreq.render_plan_ref`、`/api/engine/preparation*` | 准备计划、执行回执与源图产物（ADR 0053） → `preparation-and-receipts.md` | 计划与观测分开；执行只走 `pool.build`；取消只碰自己起的会话；按项目认领；回执两半缺一半就是 `partial`；身份三分不混（私有键含路径、公开身份不含、文件 hash 单列）；LaunchContext 是派生视图；DependencyIntent 只读不装；enrollment 台账空集合不是通过 | `tests/test_execution_receipt.py`、`test_preparation_api.py`、`test_worker_runtime_report.py`、`tests/bridge/test_bridge_e2e.py`、`tests/test_foundation_harness.py` |
| `engine/browser.py`、`browser_imports.py`、`ENGINE_FILES` | 浏览器 playground（引擎侧） → `browser-playground-engine.md` | 平铺 import 与 worker 同一条 sys.path 纪律、不许分叉出 browser_manifest；加一个 flat import 必须同步 `ENGINE_FILES` 白名单 | `tests/test_browser_session.py`、`test_playground_build.py` |

## 验证

- 针对性：`.venv/bin/python -m pytest tests/<上表的看护文件>`；改了 `figsession` /
  `wireproto` 等于同时改两条入口，先跑 `tests/test_worker_roundtrip.py` 与 `tests/bridge/`。
- 引擎改动后重启服务：`lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 改了 `pdfbackend/` 字体相关或换了 PyMuPDF：`python scripts/gen_canvas_coverage.py --write`。
- 改了引擎四模块（manifest / overrides / pathgeom / patchspec）：重建 playground 产物
  （`python scripts/build_browser_playground.py --check`），MCP 画布由 CI 现建。
- 完整验证链见 `.github/AGENTS.md`。
