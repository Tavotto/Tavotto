# 单一权威原则与严格同源对

> 原文出自根 `AGENTS.md`「不可破坏的跨仓库不变量」的「单一权威原则」一条（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是同源对总表的**唯一全文**；根 `AGENTS.md` 只留原则一句。新增一对同源对：加进这张表，并在对应层的速查表点名它的看护用例。

- **单一权威原则**：每条规则/判据只有一个出处，其余侧是它的镜像或消费者。
  改动一侧必须同步另一侧的严格同源对：

  | 两侧 | 看护 |
  | --- | --- |
  | `engine/patchspec.py` ↔ `workerd/src/patchspec.rs`+`pyfloat.rs` | `tests/golden/patch_vectors.json`（逐字节） |
  | `engine/preflight.py` ↔ `web/src/lib/preflight.ts` | `tests/golden/preflight_vectors.json`（只比判据不比措辞） |
  | `src/tavotto/richtext.py` ↔ `web/src/lib/richText.ts` | pytest 真 PDF 几何看护 |
  | `src/tavotto/glyphplan.py` ↔ `web/src/lib/glyphPlan.ts` | `tests/golden/glyph_plan_vectors.json`（**算法同源、oracle 刻意不同源**：Python 问真字体（批准字体集合，ADR 0060），浏览器读生成的`pdfbackend/canvas_coverage.json`；表的漂移由 `scripts/gen_canvas_coverage.py --check` 单独看住；与退役前旧表的差异闭集由 `tests/test_rendercore_glyph_vectors.py` 对着 `evidence/u10/*.pymupdf.json` 钉着） |
  | `web/src/lib/shapeGeometry.ts` ↔ `rendercore/geometry.py` `polygon_points`/`dash_pattern`（U06 起的宿主，ADR 0059；旧 `pdfbackend` 的 `_polygon_points`/`_dash_pattern` 随 U10 删除） | 共享向量 `tests/golden/shape_geometry_vectors.json`（退役前从旧 facade 记下）：`tests/test_rendercore_geometry.py` 与 `web/src/lib/shapeGeometry.golden.test.ts` 各跑一遍；`tests/test_compose_arrow.py` 从合成 PDF 的内容流抽坐标做几何级看护 |
  | `handoff.desktop_argv()` ↔ `src-tauri/src/main.rs::parse_open_args()` | 两侧单测 |
  | `src-tauri/src/main.rs::menu_spec` 真正挂上去的 `menu-*` 项 id ↔ `web/src/lib/desktop.ts` `MENU_ACTIONS`（`tavotto:menu` 转发；加速键可能先于 keydown 截获，转发与键盘的让位判据是同一个 `yieldsCanvasShortcuts`） | 两侧各读 `tests/golden/menu_actions.json`：`main.rs` 的 `menu_ids_match_the_golden_pair_on_both_platforms`（量声明式菜单结构，不数源码字符串）+ `web/src/lib/desktop.golden.test.ts`；`web/src/hooks/menuActions.test.tsx` 逐个加速键比「按键」与「菜单转发」的效果 |
  | 关窗询问闸（issue #223，ADR 0002）：`src-tauri/src/main.rs` 发的事件 `tavotto:close-requested`、`CloseDecision` 认的答复闭集 `hold`/`close`/`cancel`、命令 `arm_close_guard` / `resolve_close_request` ↔ `web/src/lib/desktop.ts` 的 `onDesktopCloseRequested` / `CloseDecision` 联合 / `armDesktopCloseGuard` / `resolveDesktopCloseRequest`（消费者 `components/CloseGuardDialog.tsx` 与 `lib/closeGuard.ts`） | `tests/test_desktop_close_guard.py`：事件名两侧各恰好一处、答复闭集两侧相等、两个命令三处登记、能拦窗口的入口只有 `hold_window`；`main.rs` 的 `the_decision_vocabulary_is_a_closed_set` / `holding_a_window_always_arms_a_watchdog_for_the_same_generation`；`web/src/lib/closeGuard.test.ts`（三选一答了什么）。三处登记读结构不读子串：`tests/support/rustsrc.py` 先抹掉注释与字面量，再按括号配对读 `build.rs` 的 `commands(&[...])` 数组与 `generate_handler![...]` 的条目（注释掉、只留在无关字符串里的名字不算；尺子自己的反证在 `tests/test_rustsrc.py`）；全部命令的三处齐全由 `test_desktop_codex_button.py::test_every_tauri_command_is_declared_in_all_three_places` 枚举 |
  | 主页拖放（#665，ADR 0092）：`src-tauri/src/drop_paths.rs` 的 `EVENT`（`tavotto:file-drop`）与 `DropTarget` 的 kind 闭集 `script`/`folder`/`unsupported`、命令 `native_file_drop` ↔ `web/src/lib/desktop.ts` 的 `onNativeFileDrop` / `NativeFileDrop` / `nativeFileDropAvailable()`（唯一订阅者 `components/home/HomeView.tsx`） | `tests/test_desktop_file_drop.py`：事件名、kind 闭集两侧相等、命令三处登记 + 前端 invoke 名、主窗口建造链关着 Tauri 拖放处理器；分派规则 `drop_paths::classify` 由它的 Rust 单测钉、前端怎么接由 `HomeView.test.tsx`「系统拖放」一组钉——两边各测各的。三处登记同上一行，读 `tests/support/rustsrc.py` 解析出的结构 |
  | 主页拖放里「什么算脚本、打开哪个目录」：壳 `drop_paths::classify`（先 `canonicalize`，符号链接按**目标**判；`Path::extension` 不分大小写等于 `py` → 打开上级目录；目录 → 打开它自己；其余不收）↔ 页面 `web/src/lib/scriptImport.ts` 的 `dropTargetOf`（`isScriptName`：最后一段 `.py` 前至少一个字符、**不 trim**；`looksLikeDir` 只能看形状）。壳问文件系统，页面只有路径串、不能 stat、不能解析链接——页面判不出的条目在 golden 里逐条写 `page_blind`，不硬凑成一样；**哪些条目两侧不同，以 golden 为准**，这里不另列 | 两侧各读 `tests/golden/drop_script_rule.json`：`drop_paths.rs` 的 `the_script_rule_matches_the_golden_pair`（真建文件 / 目录 / 符号链接，量 `shell` 栏，链接的条目还核对打开的是目标那一侧）+ `web/src/lib/dropScriptRule.golden.test.ts`（带路径与只有文件名两条分支，量 `page` 栏）；两侧都断言「结论不同或是符号链接 ⇔ 写了 `page_blind`」，三档都出现 |
  | `engine/brand.py` `REPO_URL` ↔ `web/src/lib/brand.ts` 同名常量；壳的 `REPO_URL` 不是镜像而是 `src-tauri/build.rs` 编译期从 `brand.ts` 读出注入（壳在 webview 之前建菜单） | `tests/test_desktop_i18n.py::test_shell_repo_url_comes_from_the_brand_constant` + `test_ui_sources_never_handwrite_the_repo_url`；`main.rs` 的 `repo_url_is_injected_from_the_brand_source` |
  | 桌面壳 stdin 首行的 `preferred_port`：生产方 `src-tauri/src/sidecar/port_memory.rs`（`HELLO_FIELD` / `VALID` / `FIRST_CHOICE`）↔ 消费方 `src/tavotto/desktop.py`（`PREFERRED_PORT_FIELD` / `PREFERRED_PORT_MIN` / `_MAX`，`parse_preferred_port`）（ADR 0108：字段名漂了，sidecar 永远读不到建议端口，每次启动换 origin，而启动照常成功） | 两侧各读 `tests/golden/desktop_preferred_port.json`：`port_memory.rs` 的 `hello_field_is_the_golden_pair` + `tests/test_desktop_sidecar.py::test_preferred_port_field_is_the_golden_pair`（不读对方源码） |
  | `engine/locate.py` ↔ codex-plugin `handoff.py` | `test_install_locate.py::test_plugin_mirrors_the_locator` |
  | `engine/projectenv.PYTHON_MIN`/`PYTHON_MAX_EXCLUSIVE` ↔ `codex-plugin/mcp/server.py` 同名常量（`--provision` 挑 venv 基础解释器用） | `test_mcp_resolver.py::test_provision_python_range_mirrors_the_engine`（projectenv 那侧再由 `test_support_matrix.py` 钉在 pyproject 的 `requires-python` 上） |
  | `engine/runtime._owned_mplconfigdir`（+ `_MPL_CONFIG_ENTRIES` / `MPL_LINKED_CONFIG_DIRNAME`）↔ codex-plugin `handoff.mplconfigdir_for`（跑用户脚本的 `MPLCONFIGDIR`） | `test_codex_plugin.py::test_script_env_mpl_rule_mirrors_the_engine`（平台 × 目录在不在 × 用户设没设） |
  | `engine/deprepair.PIP_OPTIONS_PROBE` + `PYPI_DEFAULT_INDEX` / `parse_pip_options` / `options_name_a_custom_index`（在目标解释器里让 pip 自己解析 `pip install` 的选项，#767）↔ codex-plugin `mcp/server.py` 的 `_PIP_OPTIONS_PROBE` / `_PYPI_DEFAULT_INDEX` / `parse_pip_options` / `options_name_a_custom_index`（插件判 pip 是否指向镜像，#737；插件 import 不到引擎。漂了，引擎换不换镜像与插件给不给 `--index-url` 就各说各的） | `tests/test_pip_config_pair.py`：探测脚本逐字相等、判据与解析逐条相等；`tests/golden/pip_options_cases.json` 的每条情形在**真 pip**（当前版 + 23.x）上三方对拍——引擎、插件、真装包时 pip 打印的 `Looking in indexes:` |
  | `engine/runtime.PLUGIN_RUNTIME_DIRNAME` + `_owned_cache_root` 的插件那一支 ↔ `codex-plugin/mcp/server.py` `managed_runtime_dir` / `managed_cache_dir` / `provision_env`（自管运行时的缓存归宿，#733） | `test_probe_leaves_no_trace.py::test_the_plugin_runtime_cache_dir_is_one_path_on_both_sides` |
  | codex-plugin `codex.mcp.json` ↔ `skills/tavotto-figure/agents/openai.yaml` 依赖声明 | `tests/test_codex_plugin.py` |
  | 上面这一对在**已装副本**里也得同步（`tavotto codex install` 换启动命令时两侧一起改） | `tests/test_codex_install_cli.py` |
  | 遥测 `EVENTS` 表 ↔ 代理白名单 | `test_client_and_proxy_contracts_match` |
  | 遥测 `EVENTS` 表 ↔ `web/src/lib/telemetryDisclosure.ts` + 两份界面文案 | `tests/test_telemetry_disclosure.py`（顺序也比；界面上那份「会发送哪些数据」不许漏一条，也不许多写一条） |
  | `engine/overrides.LEGEND_ENTRY_STYLE_PROPS`+`LEGEND_BINDINGS` ↔ `web/src/lib/legendModel.ts` | `tests/test_legend_model_pairs.py`（顺序也比） |
  | `engine/documents.py` `SCHEMA_CURRENT` ↔ `web/src/types/document.ts` 同名常量 | `test_frontend_and_backend_agree_on_the_current_schema` |
  | `engine/diagnostics.py` `BUNDLE_SCHEMA_VERSION` ↔ `web/src/diagnostics/types.ts` 同名常量 | `tests/test_diagnostics_bundle.py::test_bundle_schema_is_one_number_on_both_sides` |
  | `engine/originalspec.py` `DPI_SOURCES` ↔ `web/src/lib/api.ts` `dpi_source` 联合 | `test_frontend_and_backend_agree_on_the_dpi_source_set` |
  | `engine/profiles.py` `FALLBACK_MIN_FONT_SIZE_PT` ↔ `web/src/lib/profile.ts` 同名常量 | `test_font_floor_fallback_is_one_number_on_both_sides` |
  | `engine/specfix.FIXABLE_RULES`（后端修得了的规则）↔ `web/src/lib/issueFix.ts` `ENGINE_FIX_RULES`（ADR 0080） | `tests/test_specfix.py::test_fixable_rules_are_the_same_closed_set_on_both_sides`（顺序也比） |
  | `engine/specfix.FONT_GRID_STEPS_PER_PT`（字号修复的档格，每 pt 几档）↔ `web/src/lib/issueFix.ts` 同名常量 | `tests/test_specfix.py::test_font_grid_is_one_number_on_both_sides` |
  | codex-plugin `bridge.export_raster_issues()` ↔ `web/src/lib/validation.ts` `exportContextRaw()` | `test_the_export_context_rule_is_one_rule_on_both_sides` |
  | `app.VERSION_MOMENTS`（排版时间线的关键时刻闭集，ADR 0101）↔ `web/src/lib/api.ts` `LAYOUT_MOMENTS` | `tests/test_layout_timeline.py::test_key_moments_are_the_same_closed_set_on_both_sides` |
  | `engine/exportreq.py` 文件名规则 ↔ `web/src/lib/exportName.ts` | `tests/golden/filename_vectors.json`（八条原因逐条比，顺序也比） |
  | `engine/profilestore._STYLE_KEYS`（样式内容的键白名单，含 `pt_basis` 与它唯一的取值 `"page"`）↔ `web/src/lib/stylePresets.ts` `StyleProfileData`（ADR 0081 §九：漏一个键，存一次就被收进 `extra`、前端读不到） | `tests/test_style_schema_pair.py`（读 TS 源码的接口字段与后端白名单比集合 + 比取值） |
  | `pdfbackend.CANVAS_TEXT_FAMILIES` ↔ `web/src/lib/typography.ts` 同名常量 ↔ `rendercore/typography.py` 同名常量（U06 起；U10 起契约层的就是 rendercore 的那一份） | `test_typography_families.py`（闭集 + 顺序）；`tests/test_rendercore_typography.py` |
  | `pyproject.toml` `dependencies` ↔ `requirements.txt`（钉死镜像，同名同序；U10 起含 RenderCore 的五个 native 包，PyMuPDF 只在 `legacy-pymupdf` extra，ADR 0072） | `tests/test_rendercore_fonts.py::test_requirements_txt_mirrors_the_runtime_dependencies_and_is_pinned` / `test_pymupdf_is_only_the_legacy_extra_never_a_runtime_dependency`；产物侧由 `scripts/ci/retirement_scan.py` 的 wheel / deps 尺子看 |
  | `engine/overrides.NO_COLOR`（manifest 颜色字段的「无」取值）↔ `web/src/components/ui/Input.tsx` 同名常量 | `tests/test_no_color_pair.py` |
  | `engine/manifest.VALUE_ORIGINAL_KEY`（可编辑字段上「override 之前脚本的值」那一键）↔ `web/src/lib/api.ts` `EditableField.value_original`（ADR 0081 §十三：键名漂了，前端读到的永远是「不知道」，样式写的 override 再也不给脚本让位，界面上一切如常） | `tests/test_value_original_pair.py`（后端 AST + 前端 TS 接口结构；发射点必须用常量） |
  | `app._browse_shortcuts()` 的常用起点 `id`（`"home"` + `app.BROWSE_SHORTCUTS`）↔ `web/src/lib/api.ts` `ShortcutId` 联合 ↔ `web/src/components/DirBrowser.tsx` `shortcutLabel` 的 `case` ↔ 两份语言包 `project:browser.shortcut.*`（#668：少了哪一侧，那个起点静默回退成后端写死的中文名） | `tests/test_ui_terminology.py::test_shortcut_ids_match_the_frontend_closed_set`（后端真调用取实际发出的 id，四方比集合） |
  | `engine/bridge_runner.INCONSISTENT_CODE`（runner 在用户解释器里跑、import 不到 `tavotto.*`）↔ `engine/runcodes.NATIVE_FIGURE_INCONSISTENT` | `tests/native/test_native_inconsistent.py::test_the_runner_and_the_sidecar_agree_on_the_code` |
  | `engine/pool.EXIT_GRACE`（管道 EOF 后等子进程自己退出的宽限）↔ `workerd/src/worker.rs` `EXIT_GRACE` | 两侧各自钉在 `tests/golden/exit_grace_ms.txt`：`tests/test_worker_exit_report.py::test_the_exit_grace_is_one_number_on_both_control_planes` + `workerd/tests/exit_grace_pair.rs`（不读对方源码） |
  | `rendercore/ir._crop`（引擎收得下的面板裁剪框：宽高为正、落在 [0,1]² 里，右 / 下边容 1e-9）↔ `web/src/types/document.ts` `cropInBounds()`（前端写进文档之前把关；判据漂了，前端放行的 crop 让 RenderCore 拒掉整份排版，#688） | 两侧各读 `tests/golden/crop_bounds_vectors.json`：`tests/test_rendercore_ir.py::test_crop_bounds_accept_is_the_golden_pair` / `test_crop_bounds_reject_is_the_golden_pair` + `web/src/types/cropBounds.golden.test.ts` |
  | `engine/scriptargs.analyze`（argparse 字面量 schema）+ 真 argparse 对「编辑之后的 token」的解析 ↔ `web/src/lib/scriptArgsForm.ts` `applyEdit` / `readTokens`（T07：表单编辑漂了，表单上填的与脚本收到的不是一回事） | 两侧各读 `tests/golden/script_args_form_vectors.json`：`tests/test_script_args.py`（schema 快照 + 子进程跑真 argparse 比 Namespace / 退出码）+ `web/src/lib/scriptArgsForm.golden.test.ts`；重生成 `scripts/dev/gen_script_args_vectors.py` |
  | Python `shlex.split`（粘贴命令的分词真值）↔ `web/src/lib/argvPaste.ts` `parsePastedCommand`（T07：只接受一条简单 POSIX sh 调用） | 两侧各读 `tests/golden/argv_paste_vectors.json`：`tests/test_script_args.py::test_accepted_paste_vectors_split_exactly_like_shlex` + `web/src/lib/argvPaste.golden.test.ts` |
  | `engine/deprepair.REBUILD_PROGRESS_ID_RE`（重建进度 id 的格式）↔ `web/src/store/depRepairStore.ts` `newRebuildProgressId()`（#606） | 两侧各读 `tests/golden/rebuild_progress_id.json`：`tests/test_env_project_attribution.py::test_the_rebuild_id_format_is_the_golden_pair` + `web/src/store/rebuildProgressId.golden.test.ts` |
  | `engine/runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME`（运行时找包内私有 Python 归档的目录名）↔ `packaging/tavotto.spec` 的 datas 目的地；`scripts/stage_private_python.DESKTOP_TARGETS` ↔ `packaging/runtime-lock.json` 的 shipped 目标（ADR 0111：落点漂了，包里明明带着归档，运行时却看不见、照样联网下载） | `tests/test_private_python_bundle.py::test_spec_ships_the_archive_where_the_runtime_looks_for_it`、`::test_desktop_targets_are_the_shipped_runtime_targets` |
  | `scripts/plugin_publish.py::GIT_CONFIG` 里的维护项（`maintenance.auto=false` / `gc.auto=0`）↔ `tests/support/pluginkit.py::NO_AUTO_MAINTENANCE`（插件测试夹具的 git；#604：漏一侧，fetch / commit 分离出的后台维护与临时仓库清理赛跑，ENOTEMPTY 盖掉真正的结论） | `tests/test_plugin_publish.py::test_fixture_git_mirrors_the_publisher_maintenance_settings`（两侧都按 git 读到的 `GIT_CONFIG_*` 还原后比）；行为侧 `::test_publisher_fetch_spawns_no_background_maintenance` |
  | `src/tavotto/resources/private_python_lock.json` 两个 macOS 目标的 CPython 来源（version / release / triple / url / sha256 / size / archive_root）↔ `packaging/runtime-lock.json` 的 `macos-*` 目标 `python` 块（ADR 0063：桌面版内置渲染 runtime 与私有 Python 是同一份字节） | `tests/test_private_python.py::TestLock::test_macos_entries_are_the_same_origin_as_the_runtime_lock` |

  出版规范规则唯一权威 `src/tavotto/profiles/publication.json`（两侧求值器
  共读，绝不硬编码第二份）。**「这份项目有什么问题」全产品只有一份服务**
  （ADR 0030）：求值在 `preflight`，接成可定位问题在 `web/src/lib/validation.ts`，
  编排在 `store/validationStore.ts`，定位在 `lib/issueFocus.ts`，措辞在
  `lib/validationText.ts`——导出面板只消费摘要，不跑第二遍求值器。
  **「这次导出要什么」全产品只有一个结构**（ADR 0031）：`engine/exportreq.py`
  ↔ `web/src/lib/exportRequest.ts` 的 `ExportRequest`，`scope` 只有
  `original` / `canvas` 两个取值，**`original` 段里没有 x/y/w/h 与页面尺寸**
  （想让画布缩放漏进原图导出得先改结构）；作业生命周期只有
  `engine/exportjob.py` 一份（临时目录 → 原子 replace，`partial` 是独立一档，
  取消清临时文件）；PPI **只在有位图格式时是数字**，否则是 `null`。格式闭集
  `pdf / png / eps / tiff`（ADR 0046）：TIFF 与 PNG 同一次栅格化，EPS 只有 worker
  的 matplotlib 写得出——给不出的那一档如实逐项报失败，**不伪称矢量**。
  **用户自建的样式 / 规范**在用户数据目录
  `<data_dir>/profiles/`，磁盘入口只有 `engine/profilestore.py`；「任意 id →
  规范」只有 `profilestore.resolve_spec()`；项目里存的是**绑定 + 规则全文快照**
  （ADR 0029，「项目结果稳定」优先于「规范升级自动生效」）。默认规范的字号下限
  **只有一个数 8 pt**。
  **「一段文字长什么样」全产品只有一套词汇**（ADR 0032）：规范属性名 / 取值
  语义 / 能力表 / property path / 校验全在 `web/src/lib/typography.ts`，写入经
  `TypographyAdapter` 的两个适配器（图内 `setOverride(s)`、画布
  `updateObjects`），控件只有 `controls/TypographyControls.tsx` 一份。
  `weight` / `style` 两侧同一枚举，字号一律 pt；**「不支持」「没设过」
  「多个值」是三个不同的答案**。画布文字能选的字体族是闭集（三个通用族），
  与 `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源——**前端摆得出的，后端必须
  画得出**。
