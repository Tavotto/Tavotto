# src/tavotto/ — 后端与渲染引擎规则（速查表）

仓库级路由、跨仓库不变量与验证命令在根 `AGENTS.md`；前端在 `web/AGENTS.md`，
插件与 MCP server 在 `codex-plugin/AGENTS.md`，打包与内置 runtime 在
`packaging/AGENTS.md`。本层规则的**全文按主题**在 `docs/rules/backend/`：
先在下表按改动路径找到主题，读那一份细则（各 1–10 KB）与它点名的 ADR，再动手。
看护用例在细则头部。下表是生成的：改细则头部的 `agents-route` 块，再
`python scripts/dev/gen_agents_sheets.py --write`，别手改；这里不放第二份全文。

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

<!-- BEGIN GENERATED: agents-routes -->
| 改到 | 主题（细则在 `docs/rules/backend/`） | 必守要点 |
| --- | --- | --- |
| `engine/brand.py`、任何品牌 / 标识符 / 存储键 | 品牌与命名 → `brand-and-naming.md` | 干净断裂：旧名一律不认、不加 LEGACY_；唯二 `mm` 例外只在读取端回退 |
| Flask 侧 / 子进程侧 / bridge 侧任一模块的 import、`pool.resolve_worker_python` 的优先级链 | 进程与依赖边界 → `process-boundaries.md` | 三侧模块名单与各自允许的依赖；项目级解释器唯一出处 `pool.resolve_worker_python`，失效的显式选择报错不静默替换 |
| `pdfbackend/`、`glyphplan.py`、字体 / 色图事实、`/api/render` 缓存 | PDF 后端边界 → `pdf-backend-boundary.md` | 契约层唯一实现 `rendercore/facade`；`pymupdf` 报 `backend_retired`、不静默回退；字形归属四层只在 `glyphplan.py` |
| `engine/updater.py`、`/api/update/*` | 检查更新 → `update-check.md` | 纯标准库；升级永不静默、升级后 `restart_required`；桌面模式整个关掉；HTTPS 只经 `tlstrust` |
| `engine/figcapture.py`、`artifactcontext.py`、`execspec.py`、`runconfig.py`、`scriptargs.py`、`workdir.py`、`databinding.py`、`inputremap.py`、`worker.py` 的 savefig 拦截与 sys.argv | Figure 捕获、执行描述与 live-figure 会话 → `figure-capture-and-execution.md` | 捕获三条入口同源；`safe_spec()` / `worker_argv()` 唯一出处、argv 只经 `safe_spec`；表单 schema 是建议不是门槛 |
| `engine/pool.py`、`executionfact.py`、`wireproto.py`、`workerd_client.py`、`texdiag.py`、`loginpath.py`、超时 / 关停 / 计时 | worker 协议、计时、超时与关停、TeX 分类 → `worker-protocol-and-lifecycle.md` | 协议 v1 信封原样回显、`request_id` 对不上 kill；patch 规范化唯一权威 `patchspec.py`；kill 之后必 reap；TeX 先于缺依赖判 |
| `engine/overrides.py` 的 `apply` / `_apply_rank` / `_FRAC_ANCHORED`、tight 布局、字体脸、`normalize.py` 三模块、`specfix.py` | override 语义、应用顺序与全量重放 → `override-application-and-replay.md` | override 是全量列表；七档顺序是契约；刻度类与 frac 锚定 prop 每次重放；带 `identity` 的 patch 对不上身份就不应用 |
| `axestraversal.ordered_axes`、`spinemodel`、`tickmodel`、`colorbarmodel`、`legendmodel`、`_cls_key`、能力表、3D 数据图元 / 箭头 / marker | axes 遍历、特殊 artist 与 artist family 能力层 → `axes-and-artist-families.md` | `fig.axes` 只许出现在白名单函数里；族模块是叶子；能力按真实 getter 实况判不按类名 |
| `_marker_field` / `marker_current` / `marker_original`、`engine/pathgeom.py`、`manifest.vector_text_metrics` | 标记形状事实与路径几何 → `marker-and-path-geometry.md` | 形状是只读派生事实不是取值；geometry 不进文档；超过 `MAX_MARKERS` 整组退回 bbox |
| `legendmodel.py`（`LegendEntries`、`rebuild_legend`、`legend_pos_cfg`）、`FigState.reapply` | 图例条目模型与位置模型 → `legend-model.md` | `texts_j` 的 j 是原始序号；重建型 prop 一律 `rebuild_legend`；三条位置 prop 写槽位再整体重建 |
| `colorbarmodel.py`（`_cb_reorient`、`extend`、`follow_map`）、色条轴 position | 色条：方向、延伸与大小 → `colorbar.md` | 就地结构改造、`fig.axes` 顺序不动；色阶兄弟只认 `scale_siblings`；布局归属只认显式宿主 |
| `tickmodel.py`（`tick_cfg` / `apply_tick_model` / `TickSet` / `TickLabel`）、`spinemodel.spine_cfg`、`manifest.spine_geometry` | 刻度定位、边框模型与 spines 几何 → `ticks-and-spines.md` | 写进 cfg 再整体重建，没表态 = 脚本原样；刻度记忆表只活一次测量；spines 端点取 `Spine.get_path()` |
| `engine/registry.py`、`discover.py`、`probe.py`、`projscan.py`、`/api/registry/*`、`/api/project/scan*` | 注册表、静态扫描与试运行探测 → `registry-discovery-and-probe.md` | 冲突只报告不裁决；probe 失败不写注册表；导入即扫描只读零执行、看不全 = partial 不是「没有」 |
| `engine/runtimeasset.py`、`runtime:` id、`/api/runtime/*` | RuntimeFigureAsset → `runtime-figure-assets.md` | id 不透明；端点只读绝不执行；写回硬拒绝；导出必须当次 live 渲染 |
| `engine/previewbudget.py`、`preview_complexity.py`、`preview_hybrid.py`、`figsession.render()` | 编辑预览的表示法与复杂度预算 → `preview-complexity-budget.md` | 判定在 `read_text()` 之前；超限是一次成功渲染；降级 ≠ 只读 |
| `engine/depresolve.py`、`managedenv.py`、`deprepair.py`、`userenvs.py`、`rootenv.py`、`envadvice.py`、`projectenv.probe_environment`、`pool.mutating_environment`、`importscan.py`、`distmeta.py`、`depplan.py` | 受控依赖修复、联合准备、来源解析、镜像回退 → `dependency-repair-and-packages.md` | 内置 runtime 永不是安装目标；pip exit 0 ≠ 修好；用户显式决定过的环境不碰；来源解析默认零执行 |
| `engine/privatepython.py`、`engine/tlstrust.py`、`resources/private_python_lock.json`、`managedenv.base_python` 的末级、代记录的 `base_runtime` | 私有完整 Python（ADR 0063 / 0111 / 0112）→ `private-python.md` | 安装器仍是 pip；校验先于一切执行；下载 / 缓存认原锁 SHA；失败报 `private_python_tls`；`enabled` 全 false 到资格取得 |
| `figsession.py`、`wireproto.py`、`bridge_runner.py`、`bridgeboot.py`、`worker.py` 的装载形态 | 两条执行入口：safe worker 与 native bridge → `execution-entries.md` | 编辑语义只有一份；用户的同名模块永远赢；native 侧不起后台线程 |
| `runcli.py`、`runspec.py`、`nativerelay.py`、`nativesession.py`、`envlease.py`、`enginesession.py`、`desktopproc.py` | `tavotto run` 的控制面 → `tavotto-run-control-plane.md` | 确认前一行用户代码不跑；Tavotto 只写 stderr；屏障释放必经 `release_barrier()` |
| `/api/versions`、`/api/styles`、`/api/package`、`tavottofile/`、`atomicio.py`、`documents.loads_document`、自动保存槽位、`engine/layoutsession.py`、`/api/layout-session` | 布局版本、项目文件收纳与文档落盘 → `layout-versions-and-documents.md` | `atomicio` / `project_layout_dir()` 唯一权威（ADR 0120） |
| `/api/export*`、`engine/exportreq.py`、`exportjob.py`、`exportroute.py`、`svgstore.py`、`svgexecution.py`、`tiffwrite.py`、`_serialize_figure` | 导出（ADR 0031 / 0046） → `export-pipeline.md` | 五端点一服务；文件名严格同源 |
| `app.py` 的 `PROJECTS` / `_request_ctx` / `bound_project`、`project_refresh.py`、`project_watch.py`、`readiness.py`、`originalspec.py`、`tutorial.py`、`tiffprobe.py` | 项目系统（后端侧） → `project-system.md` | 指名不存在的项目 409 绝不落默认项目；派生刷新只有 `app.refresh_project()`；readiness 只报告不动手 |
| `security.py`、`session_client.py`、任何新端点 | 会话认证（ADR 0008） → `session-auth.md` | 旁路只有三个；用户可控的路径碰文件系统 / 子进程前只经 `app.safe_resolve` / `projectenv.contained_path` |
| `engine/ai_bridge.py`、`ai_agents.py`、`ai_providers.py`、`codexinstall.py`、`/api/ai/*` | 编码 Agent 桥 → `ai-agent-bridge.md` | 「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml |
| `richtext.py` | 文字：行内上下标与大小写 → `richtext.md` | ↔ `web/src/lib/richText.ts` 严格同源（三常量 + parse） |
| `profiles/publication.json`、`engine/profiles.py`、`preflight.py`、`profilestore.py` | 出版规范 profile 与预检 → `profiles-and-preflight.md` | 规则唯一权威是那份 JSON；两侧求值器靠 golden vectors 对齐 |
| `engine/telemetry.py`、`EVENTS`、`services/telemetry_proxy/`、`collect_distribution_metrics.py` | 匿名用量统计 → `telemetry.md` | 不引入任何分析 SDK；`capture()` 永不抛不阻塞；范围扩大升 `CONSENT_VERSION`；HTTPS 只经 `tlstrust` |
| `engine/diagnostics.py`、`diagnostics_frontend.py`、`diagdetail.py`、`taskdiag.py`、`diagsend.py`、`contact*.py`、`/api/diagnostics/*` | 诊断包与任务绑定诊断 → `diagnostics.md` | 先脱敏再交出；report.json 换形必升 bundle schema；不写盘不进 telemetry；不自动上传（唯一例外是用户逐次确认） |
| `engine/scriptbackup.py`、`scriptedit.py`、`scriptlock.py`、`/api/script-edit/*`、`/api/script-backups*`、AI 回滚 | 改用户脚本：经确认的改写、备份与复原（ADR 0110 / 0094） → `script-edits.md` | 只有用户在界面看过 diff、勾选确认才写；两处备份落盘才替换；「判一次再写」整段持 `scriptlock.script_guard` |
| `_write_source_files`、`pool.one_shot()`、`REPLAY_PIXEL_TOL`、`/api/update_source`、`history/restore` | 写回事务 → `writeback-transaction.md` | prepare 两道校验；verify 全量重放 + 几何 + 像素；热态不是这组 patches 就报 `fresh_only` |
| `engine/locate.py`、`cli.py`、`handoff.py`、`tavotto open` / `doctor`、桌面 argv | 外部交接 → `external-handoff.md` | 发现链唯一权威 `locate.py`；子命令分派在 import Flask 之前；`HandoffError` 一律带稳定 code |
| `engine/preparation.py`、`prepsession.py`、`dialogscan.py`、`receipt.py`、`trace.py`、`/api/engine/preparation*`（完整清单见细则） | 准备计划、执行回执与源图产物、准备会话 → `preparation-and-receipts.md` | 计划与观测分开；过期不执行 |
| `engine/exectrace.py`、`data_dir()/cache/exec-trace/`、任何 `start_span` / `end_span` / `event` 调用点 | Execution Trace（层级执行追踪，ADR 0151） → `execution-trace.md` | 只观察不判定；自由字符串没有入口；采集失败不影响业务；时间不伪造 |
| `rendercore/`、`fonts_allowlist.json`、`pdfbackend/canvas_coverage.json`、`scripts/fetch_fonts.py`、pyproject 的 native 依赖（模块分层见细则） | RenderCore → `rendercore.md` | 纯模型只许标准库、整包零 `import pymupdf`；`facade.py` 是契约的唯一实现；离开本机的只有 `inspector.public_projection()` |
| `engine/browser.py`、`browser_imports.py`、`ENGINE_FILES` | 浏览器 playground（引擎侧） → `browser-playground-engine.md` | 平铺 import 与 worker 同一条 sys.path 纪律；加 flat import 同步 `ENGINE_FILES` |
<!-- END GENERATED: agents-routes -->

## 验证

- 针对性：`.venv/bin/python -m pytest tests/<细则头部「看护用例」点名的文件>`；改了 `figsession` /
  `wireproto` 等于同时改两条入口，先跑 `tests/test_worker_roundtrip.py` 与 `tests/bridge/`。
- 引擎改动后重启服务：`lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 改了 `rendercore/`：先 `python scripts/fetch_fonts.py`（缺字体用例 skip，skip 不是绿），再跑 `tests/test_rendercore_*.py`、
  旧契约用例与 `tests/test_retirement_scan.py`；重生成与 evidence 的全套步骤见 `rendercore.md` 末节「验证」。
- 改了回执 / 准备 / 数据绑定 / 轨迹 / manifest 身份（U09）：用例清单与 FO32 真跑见 `preparation-and-receipts.md` 末节「验证」。
- 改了引擎四模块（manifest / overrides / pathgeom / patchspec）：重建 playground 产物
  （`python scripts/build_browser_playground.py --check`），MCP 画布由 CI 现建。
- 完整验证链见 `.github/AGENTS.md`。
