# src/tavotto/ — 后端与渲染引擎规则（速查表）

仓库级路由、跨仓库不变量与验证命令在根 `AGENTS.md`；前端在 `web/AGENTS.md`，
插件与 MCP server 在 `codex-plugin/AGENTS.md`，打包与内置 runtime 在
`packaging/AGENTS.md`。本层规则的**全文按主题**在 `docs/rules/backend/`：
先在下表按改动路径找到主题，读那一份细则（各 1–10 KB）与它点名的 ADR，再动手。
规则改在细则文件里，并同步这里那一行；这里不放第二份全文。

技术形状：Flask 后端（`src/tavotto/app.py`）+ RenderCore（`rendercore/`：pikepdf /
HarfBuzz / PDFium render child + 批准字体，**只经 `src/tavotto/pdfbackend/` 契约层**）；
渲染引擎 `engine/` 在子进程 / 用户解释器里跑 matplotlib；前端 `web/`（Vite + React 19 +
TS + Tailwind v4）。

## 本层不可破坏

- **进程边界**：Flask 父进程（`.venv` 只有 flask + packaging + RenderCore 的五个包）import 链上的
  engine 模块纯标准库；`worker.py` / `manifest.py` / `overrides.py` /
  `figsession.py` / `wireproto.py` 只在子进程；`bridge_runner.py` /
  `bridgeboot.py` 在**用户的**解释器里跑：纯标准库、3.10 可跑、启动阶段绝不
  import matplotlib。可写数据只走 `engine/config.data_dir()`。
- **PDF 库边界**：`app.py` 只认 `pdfbackend/__init__.py` 的契约名，唯一实现是 `rendercore/facade.py`；
  应用闭包零 pymupdf（U10，ADR 0072；`scripts/ci/retirement_scan.py` 看护），native 包只在
  `rendercore/` 适配层里 import。
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
| `engine/brand.py`、任何品牌 / 标识符 / 存储键 | 品牌与命名 → `brand-and-naming.md` | 干净断裂：旧名一律不认、不加 LEGACY_；唯二 `mm` 例外只在读取端回退 | `tests/test_desktop_launch.py`、`tests/test_handoff.py` |
| Flask 侧 / 子进程侧 / bridge 侧任一模块的 import、`pool.resolve_worker_python` 的优先级链 | 进程与依赖边界 → `process-boundaries.md` | 三侧模块名单与各自允许的依赖；项目级解释器决策唯一出处 `pool.resolve_worker_python(项目, script=)`，失效的显式选择报错不静默替换 | `tests/test_install_locate.py`（`test_subcommands_run_without_flask_or_pymupdf`）、`tests/bridge/`、`tests/test_first_open_environment.py` |
| `pdfbackend/`、`glyphplan.py`、字体 / 色图事实、`/api/render` 缓存 | PDF 后端边界 → `pdf-backend-boundary.md` | 契约层唯一实现 `rendercore/facade`；`pymupdf` 报 `backend_retired`、不静默回退；字形归属四层只在 `glyphplan.py` | `tests/test_rendercore_facade.py`、`tests/test_retirement_scan.py`、`tests/test_glyph_plan.py`、`tests/test_font_family_options.py`、`tests/test_cmap_facts.py`、`tests/test_cjk_figure_text.py`、`tests/test_render_cache.py`、`tests/test_windows_regressions.py` |
| `engine/updater.py`、`/api/update/*` | 检查更新 → `update-check.md` | 纯标准库；升级永不静默、升级后 `restart_required`；桌面模式整个关掉 | `tests/test_updater.py` |
| `engine/figcapture.py`、`execspec.py`、`workdir.py`、`databinding.py`、`worker.py` 的 savefig 拦截与 sys.argv | Figure 捕获、执行描述与 live-figure 会话 → `figure-capture-and-execution.md` | 捕获策略两条入口同一份实现；`safe_spec()` / `worker_argv()` 唯一出处；首开问不问由 `workdir.resolve_mode` 定，不猜不就近 | `tests/test_compat_capture_parity.py`、`test_execspec.py`、`test_workerd_pool.py`、`test_workdir_mode.py`、`test_first_open_workdir.py`、`test_databinding.py`、`test_zero_capture.py` |
| `engine/pool.py`、`wireproto.py`、`workerd_client.py`、超时 / 关停 / 计时 | worker 协议、计时、超时与关停 → `worker-protocol-and-lifecycle.md` | 协议 v1 信封原样回显、`request_id` 对不上 kill；patch 规范化唯一权威 `patchspec.py`；kill 之后必 reap | `tests/test_worker_protocol.py`、`test_worker_roundtrip.py`、`test_worker_exit_report.py`、`test_workerd_client.py`、`test_build_watchdog.py`、`test_windows_regressions.py` |
| `engine/overrides.py` 的 `apply` / `_apply_rank` / `_FRAC_ANCHORED`、tight 布局、字体脸、`normalize.py` 三模块、`specfix.py` | override 语义、应用顺序与全量重放 → `override-application-and-replay.md` | override 是全量列表；七档顺序是契约；刻度类与 frac 锚定 prop 每次重放；带 `identity` 的 patch 对不上目标身份就不应用（ADR 0083） | `tests/test_invariants_engine.py`、`test_override_identity.py`、`test_text_bbox_visibility.py`、`test_patch_edgecolor_mode.py`、`test_equivalence_matrix.py`、`test_layout_engine_pinning.py`、`test_text_drag_anchor.py`、`test_scientific_text_matrix.py`、`test_normalize.py`、`test_restore_failure_retry.py`、`test_specfix*.py`、`test_manifest_value_original.py`、`test_value_original_pair.py` |
| `axestraversal.ordered_axes`、`spinemodel.py`、`tickmodel.py`、`colorbarmodel.py`、`legendmodel.py`、`_cls_key`、能力表、3D / 箭头 / 散点 marker | axes 遍历、特殊 artist 与 artist family 能力层 → `axes-and-artist-families.md` | `fig.axes` 只许出现在白名单函数里；族模块是叶子；能力按真实 getter 实况判不按类名 | `tests/test_figure_recognition.py`、`tests/test_axes_traversal_authority.py`、`tests/test_engine_family_modules.py`、`test_parasite_axes.py`、`test_artist_families.py`、`test_worker_roundtrip.py` |
| `_marker_field` / `marker_current` / `marker_original`、`engine/pathgeom.py`、`manifest.vector_text_metrics` | 标记形状事实与路径几何 → `marker-and-path-geometry.md` | 形状是只读派生事实不是取值；geometry 不进文档；超过 `MAX_MARKERS` 整组退回 bbox | `tests/test_manifest_marker_shape.py`、`test_manifest_geometry.py`、`test_series_shape_geometry.py`、`test_manifest_vector_text_metrics.py` |
| `legendmodel.py`（`LegendEntries`、`rebuild_legend`、`legend_pos_cfg`）、`FigState.reapply` | 图例条目模型与位置模型 → `legend-model.md` | `texts_j` 的 j 是原始序号；重建型 prop 一律 `rebuild_legend`；三条位置 prop 写槽位再整体重建 | `tests/test_legend_binding.py`、`test_legend_custom_handler.py`、`test_legend_model_pairs.py`、`test_legend_anchor.py`、`test_legend_text.py`、`test_hidden_legend_geometry.py`、`test_legend_fontsize_native.py` |
| `colorbarmodel.py`（`_cb_reorient`、`extend`、`follow_map`）、色条轴 position | 色条：方向、延伸与大小 → `colorbar.md` | 就地结构改造、`fig.axes` 顺序不动；色阶兄弟只认 `scale_siblings` | `tests/test_colorbar_orientation.py`、`test_colorbar_resize.py`、`test_figure_recognition.py` |
| `tickmodel.py`（`tick_cfg` / `apply_tick_model` / `TickSet` / `TickLabel`）、`spinemodel.spine_cfg`、`manifest.spine_geometry` | 刻度定位、边框模型与 spines 几何 → `ticks-and-spines.md` | 写进 cfg 再整体重建，没表态 = 脚本原样；spines 端点取 `Spine.get_path()` | `tests/test_axes_ticks_scale.py`、`test_tick_sides_geometry.py`、`test_manifest_ticklabel_cost.py` |
| `engine/registry.py`、`discover.py`、`probe.py`、`/api/registry/*` | 注册表、静态扫描与试运行探测 → `registry-discovery-and-probe.md` | 冲突只报告不裁决；probe 绝不猜也绝不静默跳过、失败不写注册表 | `tests/test_registry.py`、`test_discover.py`、`test_discover_problems.py`、`test_script_probe.py`、`test_asset_library.py` |
| `engine/runtimeasset.py`、`runtime:` id、`/api/runtime/*` | RuntimeFigureAsset → `runtime-figure-assets.md` | id 不透明；端点只读绝不执行；写回硬拒绝；导出必须当次 live 渲染 | `tests/test_runtime_asset.py`、`test_asset_library.py` |
| `engine/previewbudget.py`、`preview_complexity.py`、`preview_hybrid.py`、`figsession.render()` | 编辑预览的表示法与复杂度预算 → `preview-complexity-budget.md` | 判定在 `read_text()` 之前；超限是一次成功渲染；降级 ≠ 只读 | `tests/test_preview_budget.py`、`test_preview_complexity.py`、`test_preview_hybrid.py`、`test_issue181_large_preview.py` |
| `engine/depresolve.py`、`managedenv.py`、`deprepair.py`、`userenvs.py`、`projectenv.probe_environment`、`pool.mutating_environment`、`importscan.py`、`depplan.py` | 受控依赖修复与包管理、联合依赖准备（ADR 0061） → `dependency-repair-and-packages.md` | 内置 runtime 永不是安装目标；pip exit 0 ≠ 修好；用户显式决定过的环境一个都不碰 | `tests/test_dependency_repair.py`、`test_dependency_repair_e2e.py`、`test_package_management.py`、`test_package_lookup.py`、`test_environment_health_parity.py`、`test_dependency_plan.py`、`test_user_environments.py`、`test_env_project_attribution.py` |
| `engine/privatepython.py`、`resources/private_python_lock.json`、`managedenv.base_python` 的末级、代记录的 `base_runtime` | 私有完整 Python（ADR 0063）→ `private-python.md` | 只补基础解释器来源、安装器仍是 pip；校验先于一切执行；`enabled` 全 false 直到目标资格取得 | `tests/test_private_python.py`、`tests/test_private_python_transaction.py` |
| `figsession.py`、`wireproto.py`、`bridge_runner.py`、`bridgeboot.py`、`worker.py` 的装载形态 | 两条执行入口：safe worker 与 native bridge → `execution-entries.md` | 编辑语义只有一份；用户的同名模块永远赢；native 侧不起后台线程 | `tests/test_worker_roundtrip.py`、`tests/test_install_dir_bytecode_free.py`、`tests/bridge/test_bridge_namespace.py`、`tests/bridge/test_bridge_thread_model.py`、`tests/test_import_architecture.py`、`tests/test_foundation_first_open.py` |
| `runcli.py`、`runspec.py`、`nativerelay.py`、`nativesession.py`、`envlease.py`、`enginesession.py` | `tavotto run` 的控制面 → `tavotto-run-control-plane.md` | 确认前一行用户代码不跑；Tavotto 只写 stderr；屏障释放必经 `release_barrier()` | `tests/native/`（含 `test_run_cli_integration.py`、`test_native_inconsistent.py`）、`tests/test_windows_regressions.py` |
| `/api/versions`、`/api/styles`、`/api/package`、`tavottofile/`、`atomicio.py`、`documents.loads_document`、自动保存槽位 | 布局版本、项目文件收纳与文档落盘 → `layout-versions-and-documents.md` | 文档落盘只有 `atomicio`；`project_layout_dir()` 是收纳规则唯一出处 | `tests/test_versions.py`、`test_document_persistence.py`、`test_package.py`、`test_ai_revert_atomic.py` |
| `/api/export*`、`engine/exportreq.py`、`exportjob.py`、`tiffwrite.py`、`_serialize_figure` | 导出（ADR 0031 / 0046） → `export-pipeline.md` | 五个端点一个服务；`partial` 独立一档；文件名规则严格同源对 | `tests/test_export_pipeline.py`、`test_export_request.py`、`test_export_endpoint.py`、`test_tiffwrite.py`、`test_epsfile.py`、`tests/golden/filename_vectors.json` |
| `app.py` 的 `PROJECTS` / `_request_ctx` / `bound_project`、`project_refresh.py`、`project_watch.py`、`readiness.py`、`originalspec.py`、`tutorial.py`、`tiffprobe.py` | 项目系统（后端侧） → `project-system.md` | 指名不存在的项目 409 绝不落默认项目；派生刷新只有 `app.refresh_project()`；readiness 只报告不动手 | `tests/test_projects.py`、`test_paths_and_baked.py`、`test_project_refresh.py`、`test_project_watch.py`、`test_project_readiness.py`、`test_original_spec.py`、`test_tutorial.py`、`test_tiff_assets.py` |
| `security.py`、`session_client.py`、任何新端点 | 会话认证（ADR 0008） → `session-auth.md` | 旁路只有三个；用户可控的路径碰文件系统 / 子进程前只经 `app.safe_resolve` / `projectenv.contained_path` | `tests/test_browser_auth.py`、`scripts/smoke_app.py` 的 401 硬断言 |
| `engine/ai_bridge.py`、`ai_agents.py`、`ai_providers.py`、`codexinstall.py`、`/api/ai/*` | 编码 Agent 桥 → `ai-agent-bridge.md` | 「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml；revert 只回滚本次 AI 改完的那一版，之后脚本又变过就 409 `ai_revert_conflict`、原件零改动 | `tests/test_ai_agents.py`、`test_ai_bridge.py`、`test_ai_capabilities.py`、`test_ai_refresh.py`、`test_ai_history.py`、`test_ai_revert_stale.py`、`test_codex_install_cli.py` |
| `richtext.py` | 文字：行内上下标与大小写 → `richtext.md` | ↔ `web/src/lib/richText.ts` 严格同源（三常量 + parse） | `tests/test_compose_text.py`、`test_glyph_plan.py` |
| `profiles/publication.json`、`engine/profiles.py`、`preflight.py`、`profilestore.py` | 出版规范 profile 与预检 → `profiles-and-preflight.md` | 规则唯一权威是那份 JSON；两侧求值器靠 golden vectors 对齐 | `tests/test_preflight.py`、`test_manifest_clip_bbox.py`、`test_profile_store.py`、`tests/golden/preflight_vectors.json` |
| `engine/telemetry.py`、`EVENTS`、`services/telemetry_proxy/`、`collect_distribution_metrics.py` | 匿名用量统计 → `telemetry.md` | 不引入任何分析 SDK；`capture()` 永不抛不阻塞；范围扩大升 `CONSENT_VERSION` | `tests/test_telemetry.py`、`test_telemetry_api.py`、`test_telemetry_proxy.py`、`test_telemetry_invariants.py`、`test_telemetry_disclosure.py`、`test_distribution_metrics.py` |
| `engine/diagnostics.py`、`diagnostics_frontend.py`、`/api/diagnostics/*` | 诊断包 → `diagnostics.md` | 先脱敏再交出；report.json 换形必升 bundle schema；不写盘不上传不进 telemetry；包里的日志只读出门版（模板原样、参数换形，REL-05；闭集值经 `engine/logsafe.py` 按出处明文放行） | `tests/test_diagnostics_bundle.py`、`test_diagnostics_worker_evidence.py`、`test_diagnostics_log_privacy.py` |
| `_write_source_files`、`pool.one_shot()`、`REPLAY_PIXEL_TOL`、`/api/update_source`、`history/restore` | 写回事务 → `writeback-transaction.md` | prepare 两道校验；verify 全量重放 + 几何 + 像素；热态不是这组 patches 就报 `fresh_only` | `tests/test_write_back.py`、`test_worker_roundtrip.py` 末节、`web` 的 `WriteBackDialog.test.tsx` |
| `engine/locate.py`、`cli.py`、`handoff.py`、`tavotto open` / `doctor`、桌面 argv | 外部交接 → `external-handoff.md` | 发现链唯一权威 `locate.py`；子命令分派在 import Flask 之前；`HandoffError` 一律带稳定 code | `tests/test_install_locate.py`、`test_handoff.py`、`test_open_script_route.py`、`test_desktop_launch.py` |
| `engine/preparation.py`、`receipt.py`、`trace.py`、`databinding.binding_for`、`figcapture.InputObserver`、`execspec.launch_context`、`workdir.grant_for`、`depresolve.DependencyIntent`、`figcapture.SourceArtifact`、`exportreq.render_plan_ref`、`/api/engine/preparation*` | 准备计划、执行回执与源图产物（ADR 0053 / 0057 / 0070 / 0071） → `preparation-and-receipts.md` | 计划与观测分开；执行只走 `pool.build`；过期计划 `preparation_plan_stale` 不执行 | `tests/test_execution_receipt.py`、`test_preparation_api.py`、`test_worker_runtime_report.py`、`tests/bridge/test_bridge_e2e.py`、`tests/test_foundation_harness.py`、`tests/test_foundation_first_open.py`、`tests/test_trace.py`、`tests/test_foundation_join.py` |
| `rendercore/`（`ir` / `geometry` / `typography` / `fonts` / `sources` / `plan` / `placement` / `raster` 纯模型；`hbshaper` / `pdfwriter` / `rasterio` / `renderchild` / `renderhost` / `preview` / `job` / `facade` / `inspector` native 适配与入口；`identity` 四身份）、`fonts_allowlist.json`、`pdfbackend/canvas_coverage.json`、`scripts/fetch_fonts.py`、pyproject 的 `dependencies`（五个 native 包，U10 起） | RenderCore：Render IR、RenderPlan、字体政策、可检索文字、合成、栅格、facade 接线与有限产物验证（ADR 0059 / 0060 / 0065 / 0066 / 0067 / 0068 / 0070 / 0071 / 0072 / 0077） → `rendercore.md` | 纯模型只许标准库、整包零 `import pymupdf`；`facade.py` 是契约的唯一实现；离开本机的只有 `inspector.public_projection()` | `tests/test_rendercore_*.py`（二十一份）及导出核验 / 身份 / 退役扫描等九份，全名单见细则末节 |
| `engine/browser.py`、`browser_imports.py`、`ENGINE_FILES` | 浏览器 playground（引擎侧） → `browser-playground-engine.md` | 平铺 import 与 worker 同一条 sys.path 纪律；加 flat import 同步 `ENGINE_FILES` | `tests/test_browser_session.py`、`test_playground_build.py` |

## 验证

- 针对性：`.venv/bin/python -m pytest tests/<上表的看护文件>`；改了 `figsession` /
  `wireproto` 等于同时改两条入口，先跑 `tests/test_worker_roundtrip.py` 与 `tests/bridge/`。
- 引擎改动后重启服务：`lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 改了 `rendercore/`：先 `python scripts/fetch_fonts.py`（缺字体用例 skip，skip 不是绿），再跑 `tests/test_rendercore_*.py`、
  旧契约用例与 `tests/test_retirement_scan.py`；重生成与 evidence 的全套步骤见 `rendercore.md` 末节「验证」。
- 改了回执 / 准备 / 数据绑定 / 轨迹 / manifest 身份（U09）：用例清单与 FO32 真跑见 `preparation-and-receipts.md` 末节「验证」。
- 改了引擎四模块（manifest / overrides / pathgeom / patchspec）：重建 playground 产物
  （`python scripts/build_browser_playground.py --check`），MCP 画布由 CI 现建。
- 完整验证链见 `.github/AGENTS.md`。
