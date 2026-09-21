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
  | `src/tavotto/glyphplan.py` ↔ `web/src/lib/glyphPlan.ts` | `tests/golden/glyph_plan_vectors.json`（**算法同源、oracle 刻意不同源**：Python 问真字体，浏览器读生成的`pdfbackend/canvas_coverage.json`；表的漂移由 `scripts/gen_canvas_coverage.py --check` 单独看住） |
  | `web/src/lib/shapeGeometry.ts` ↔ `pdfbackend` `_polygon_points`/`_dash_pattern` | pytest get_drawings() 几何看护 |
  | `handoff.desktop_argv()` ↔ `src-tauri/src/main.rs::parse_open_args()` | 两侧单测 |
  | `engine/locate.py` ↔ codex-plugin `handoff.py` | `test_install_locate.py::test_plugin_mirrors_the_locator` |
  | `engine/projectenv.PYTHON_MIN`/`PYTHON_MAX_EXCLUSIVE` ↔ `codex-plugin/mcp/server.py` 同名常量（`--provision` 挑 venv 基础解释器用） | `test_mcp_resolver.py::test_provision_python_range_mirrors_the_engine`（projectenv 那侧再由 `test_support_matrix.py` 钉在 pyproject 的 `requires-python` 上） |
  | codex-plugin `.mcp.json` ↔ `skills/tavotto-figure/agents/openai.yaml` 依赖声明 | `tests/test_codex_plugin.py` |
  | 上面这一对在**已装副本**里也得同步（`tavotto codex install` 换启动命令时两侧一起改） | `tests/test_codex_install_cli.py` |
  | 遥测 `EVENTS` 表 ↔ 代理白名单 | `test_client_and_proxy_contracts_match` |
  | 遥测 `EVENTS` 表 ↔ `web/src/lib/telemetryDisclosure.ts` + 两份界面文案 | `tests/test_telemetry_disclosure.py`（顺序也比；界面上那份「会发送哪些数据」不许漏一条，也不许多写一条） |
  | `engine/overrides.LEGEND_ENTRY_STYLE_PROPS`+`LEGEND_BINDINGS` ↔ `web/src/lib/legendModel.ts` | `tests/test_legend_model_pairs.py`（顺序也比） |
  | `engine/documents.py` `SCHEMA_CURRENT` ↔ `web/src/types/document.ts` 同名常量 | `test_frontend_and_backend_agree_on_the_current_schema` |
  | `engine/originalspec.py` `DPI_SOURCES` ↔ `web/src/lib/api.ts` `dpi_source` 联合 | `test_frontend_and_backend_agree_on_the_dpi_source_set` |
  | `engine/profiles.py` `FALLBACK_MIN_FONT_SIZE_PT` ↔ `web/src/lib/profile.ts` 同名常量 | `test_font_floor_fallback_is_one_number_on_both_sides` |
  | codex-plugin `bridge.export_raster_issues()` ↔ `web/src/lib/validation.ts` `exportContextRaw()` | `test_the_export_context_rule_is_one_rule_on_both_sides` |
  | `engine/exportreq.py` 文件名规则 ↔ `web/src/lib/exportName.ts` | `tests/golden/filename_vectors.json`（八条原因逐条比，顺序也比） |
  | `pdfbackend.CANVAS_TEXT_FAMILIES` ↔ `web/src/lib/typography.ts` 同名常量 | `test_typography_families.py`（闭集 + 顺序） |
  | `engine/overrides.NO_COLOR`（manifest 颜色字段的「无」取值）↔ `web/src/components/ui/Input.tsx` 同名常量 | `tests/test_no_color_pair.py` |
  | `engine/pool.EXIT_GRACE`（管道 EOF 后等子进程自己退出的宽限）↔ `workerd/src/worker.rs` `EXIT_GRACE` | 两侧各自钉在 `tests/golden/exit_grace_ms.txt`：`tests/test_worker_exit_report.py::test_the_exit_grace_is_one_number_on_both_control_planes` + `workerd/tests/exit_grace_pair.rs`（不读对方源码） |

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
