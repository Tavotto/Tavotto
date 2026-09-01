# Tavotto — 仓库级规则

## 任务路由

- 用户要求安装、运行或试用 Tavotto，而未要求修改源码：这是**用户安装任务**。
  只读 README 的「在 Codex 中第一次使用 Tavotto」章节；不得构建仓库、安装
  开发依赖或运行完整测试。普通用户安装绝不需要 clone 本仓库，也绝不需要
  `pnpm`、`npm`、`cargo`、Tauri、前端构建、`run.sh`、测试套件或源码
  editable install——源码开发只留给明确说「我要贡献/开发 Tavotto」的人。
- 用户明确要求修改 Tavotto：根据改动目录读取**最近的**子目录 `AGENTS.md`
  （索引见文末），并先读 `docs/adr/` 里对应的架构决策。

## 不可破坏的跨仓库不变量

- 产品名 **Tavotto**（拼写大小写固定）。品牌与格式常量唯一出处
  `web/src/lib/brand.ts` / `engine/brand.py`——界面、导出格式、仓库地址
  不得手写。Magplot/旧品牌是**干净断裂**，不加 LEGACY_ 常量
  （仅有的两个 mm 前缀例外见 `src/tavotto/AGENTS.md`）。
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
  | codex-plugin `.mcp.json` ↔ `skills/tavotto-figure/agents/openai.yaml` 依赖声明 | `tests/test_codex_plugin.py` |
  | 遥测 `EVENTS` 表 ↔ 代理白名单 | `test_client_and_proxy_contracts_match` |
  | `engine/documents.py` `SCHEMA_CURRENT` ↔ `web/src/types/document.ts` 同名常量 | `test_frontend_and_backend_agree_on_the_current_schema` |
  | `engine/originalspec.py` `DPI_SOURCES` ↔ `web/src/lib/api.ts` `dpi_source` 联合 | `test_frontend_and_backend_agree_on_the_dpi_source_set` |
  | `engine/profiles.py` `FALLBACK_MIN_FONT_SIZE_PT` ↔ `web/src/lib/profile.ts` 同名常量 | `test_font_floor_fallback_is_one_number_on_both_sides` |
  | codex-plugin `bridge.export_raster_issues()` ↔ `web/src/lib/validation.ts` `exportContextRaw()` | `test_the_export_context_rule_is_one_rule_on_both_sides` |
  | `engine/exportreq.py` 文件名规则 ↔ `web/src/lib/exportName.ts` | `tests/golden/filename_vectors.json`（八条原因逐条比，顺序也比） |
  | `pdfbackend.CANVAS_TEXT_FAMILIES` ↔ `web/src/lib/typography.ts` 同名常量 | `test_typography_families.py`（闭集 + 顺序） |

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
  取消清临时文件）；PPI **只在有位图格式时是数字**，否则是 `null`。
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
- **安全边界**：会话认证（ADR 0008）不许被任何新端点绕过；worker 沙盒与
  `Path.unlink` 守卫不放松；`pdfbackend/pymupdf_backend.py` 是全仓库唯一
  import pymupdf 的模块。
- **隐私**：遥测三档同意（unset ≠ 同意）、白名单结构性防线、
  `TAVOTTO_NO_TELEMETRY=1` 硬开关；用户脚本/路径/图内文字在结构上就发不出去。
  诊断包先脱敏再交出。
- **写回事务不变式**：热态所见 == 写进文件的 == 重开后重放出来的；
  prepare → verify（全量重放 + 几何比对 + 像素门）→ commit，任一环不过
  一律 409 且原文件零改动。不许为省时间跳过 verify。
- **运行时可写数据一律走 `engine/config.data_dir()`**，不往包目录、安装目录
  或仓库根写任何东西（macOS 上写 .app 会当场破坏代码签名）。
- **1.0 收敛纪律**（退出条件与缺陷分级见 `docs/1.0-release-readiness.md`）：
  除非 correctness / safety / compatibility / release blocker，禁止扩大产品
  能力，禁止趁机重写已稳定模块。新增核心不变式测试提交前必须手工反证一次
  （空门禁比没有门禁更坏）。
- 许可证 AGPL-3.0-only；`docs/support-matrix.json` 是平台支持口径的唯一出处，
  README/网站/应用内文案必须与它一致。

## 最常用验证

```sh
ruff check . --fix && ruff format .        # 开发时：修 + 排 import + 格式化（秒级）
ruff check . && ruff format --check .      # 提交前：只检查，与 CI 跑的完全一致
.venv/bin/python -m pytest                 # 后端（tests/ 跑在 .venv）
cd web && pnpm test && pnpm build          # 前端 + 类型检查（别用 tsc --noEmit：恒假绿）
cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check
python scripts/smoke_app.py --python .venv/bin/python   # 端到端冒烟
```

- **改完 Python 先过 Ruff，再跑针对性 pytest，最后才是完整验证。**
  开发时 `ruff check . --fix && ruff format .` 让它替你修；提交前跑
  `ruff check . && ruff format --check .`——**与 CI 那一格逐字相同**，
  本地绿就不会在 CI 上因为格式再红一轮。
  全仓 20~30 ms 回来，挡的是拼错的名字、没用的 import、没用的局部变量那一类
  ——它们不值得先花十分钟跑完整套。能自动修的用 `ruff check . --fix`（只应用
  安全修复；`--unsafe-fixes` 会动语义，要逐条看过再用）。规则集在
  `pyproject.toml` 的 `[tool.ruff]`（lint、import 排序、formatter **均已启用**），
  细节见 `docs/ci/ruff.md`。示例图库 / playground 示例 / CompatBench 语料
  **不参与格式化**，它们的排版属于内容。
- **新增一处会被塞进 `sys.path` 的仓库内源码根时，必须同步审查
  `[tool.ruff]` 的 `src`**——否则从那个目录平铺 import 的模块会被 ruff 判成
  第三方，排进 matplotlib 那一组。**在已有源码根下新增模块不用动它**，
  ruff 按路径自然认出来。**Ruff 不替代任何语义门禁**，它只是最便宜的第一层。
- 改了 `src/tavotto/pdfbackend/` 里字体相关的东西、或换了 PyMuPDF 版本：
  `python scripts/gen_canvas_coverage.py`（`--write` 重新生成）——那张覆盖表
  是前端「这个字导出后是不是方框」的唯一依据，漂了不看住的表现是
  「预览说画得出、导出上是个方框」。
- 改了 `web/src` 或引擎四模块（manifest/overrides/pathgeom/patchspec）：
  **两个受管产物都要重建**——`python scripts/build_mcp_widget.py` 与
  `python scripts/build_browser_playground.py`（各有 `--check`）。
- 引擎改动后重启服务：
  `lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 完整验证链（CompatBench / 等价性矩阵 / 不变式 / nightly / E2E / 性能基线）
  见 `.github/AGENTS.md`。

## 子系统索引（改哪里，先读哪份）

| 目录 | 规则文件 | 覆盖 |
| --- | --- | --- |
| `src/tavotto/`（含 `engine/`） | `src/tavotto/AGENTS.md` | Flask、渲染引擎、worker 协议、PDF 后端、写回、编码 Agent 桥、遥测、预检、外部交接 |
| `web/` | `web/AGENTS.md` | 前端、渲染态、预览平面、命中几何、i18n、playground、UI 视觉纪律 |
| `src-tauri/` | `src-tauri/AGENTS.md` | 桌面壳、ACL、更新通道、安装界面、壳内 i18n |
| `workerd/` | `workerd/AGENTS.md` | Rust supervisor |
| `packaging/` | `packaging/AGENTS.md` | wheel/sdist、内置渲染 runtime、PyInstaller、macOS 签名 |
| `codex-plugin/` | `codex-plugin/AGENTS.md` | Codex 插件、技能、MCP server、内嵌画布、首次使用契约 |
| `.github/` | `.github/AGENTS.md` | CI 分层、门禁纪律、验证链、发布链 |
| 架构决策 | `docs/adr/` | 改动前先读对应 ADR |
