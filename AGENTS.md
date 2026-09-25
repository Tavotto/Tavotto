# Tavotto — 仓库级规则

本文件是每次会话都要读的那一份，只放四样：任务路由、跨仓库不变量、验证入口、
索引。各层规则的**全文按主题**放在 `docs/rules/`（总览与体积预算见
`docs/rules/README.md`）：改哪一带，就按那一层速查表里的路径读那一份细则，
不要求每次全读。

## 任务路由

- 用户要求安装、运行或试用 Tavotto，而未要求修改源码：这是**用户安装任务**。
  只读 README 的「在 Codex 中第一次使用 Tavotto」章节；不得构建仓库、安装
  开发依赖或运行完整测试。普通用户安装绝不需要 clone 本仓库，也绝不需要
  `pnpm`、`npm`、`cargo`、Tauri、前端构建、`run.sh`、测试套件或源码
  editable install——源码开发只留给明确说「我要贡献/开发 Tavotto」的人。
- 用户明确要求修改 Tavotto：根据改动目录读取**最近的**子目录 `AGENTS.md`
  （索引见文末），它是那一层的速查表，每一行指向 `docs/rules/` 里的细则与
  `docs/adr/` 里的架构决策——动手前先读对应的那几份。

## 不可破坏的跨仓库不变量

- 产品名 **Tavotto**（拼写大小写固定）。品牌与格式常量唯一出处
  `web/src/lib/brand.ts` / `engine/brand.py`——界面、导出格式、仓库地址
  不得手写。Magplot/旧品牌是**干净断裂**，不加 LEGACY_ 常量
  （仅有的两个 mm 前缀例外见 `docs/rules/backend/brand-and-naming.md`）。
- **单一权威原则**：每条规则/判据只有一个出处，其余侧是它的镜像或消费者；
  改动一侧必须同步另一侧。二十来对**严格同源对**与各自的看护用例、以及
  出版规范 / 导出请求 / 用户样式 / 文字词汇四条「全产品只有一份」的结构，
  全文在 `docs/rules/repo/same-origin-pairs.md`——改到表里任一侧先开那张表。
- **安全边界**：会话认证（ADR 0008）不许被任何新端点绕过；worker 沙盒与
  `Path.unlink` 守卫不放松（safe 档的 cwd 可按项目显式切到脚本目录，ADR 0047——
  守卫原样，变的只是相对路径写到哪，且要用户按项目确认）。
- **渲染闭包零 PyMuPDF（ADR 0072，2026-09-22 起）**：Tavotto 的应用 / 发行 / runtime 闭包里
  没有 pymupdf / fitz——PDF 的读、写、栅格全经 `pdfbackend/` 契约层走 `rendercore/`
  （pikepdf / HarfBuzz / PDFium render child 与批准字体）。看护是 `scripts/ci/retirement_scan.py`
  的五把尺子（应用源码 AST / 声明依赖闭包 / 干净进程阻断器 / 产物 native 名单 + wheel METADATA /
  SBOM，`tests/test_retirement_scan.py` + package / exe smoke 那三步）；主语是闭包不是硬盘：
  测试的独立读取器、`scripts/dev/`、用户科学脚本自己的 fitz 都不算残留。`TAVOTTO_RENDER_BACKEND=pymupdf`
  是明确的 `backend_retired` 错误，不是回退。
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
  （空门禁比没有门禁更坏）。统一实施包（`docs/implementation/tavotto-foundation/`）的
  RenderCore / 首开两条主线是这条纪律的**登记例外**（ADR 0053 立项、ADR 0072 切默认）：
  它们替换的是渲染闭包与环境准备，按实施包的 registry / enrollment 逐项取得资格，不是「趁机重写」。
- 许可证 AGPL-3.0-only；`docs/support-matrix.json` 是平台支持口径的唯一出处，
  README/网站/应用内文案必须与它一致。

## 判据的主语（写判据之前）

写门禁 / 断言最常犯的错**不是实现写错，是判据问错了主语**——它量的不是你以为
的那个对象，于是恒真或恒假，而测试一直是绿的。这是个会连着犯的家族。
**动键盘之前先把主语说出口：谁的、哪个进程、哪个时刻、哪个维度**——哪一问
答不上来，先答上来再写。配套三条：**判源码结构用 AST 不用子串**，判据写成
正面形式（否定断言会被解释它的那句话咬到）；**判不出就别判**，把盲点写在
明处、收窄被判对象，假装覆盖比不覆盖更坏；**反证先验落点**——目标串在 →
变异落在预期位置 → 才看红不红，结论用退出码，每写一条用例立刻变异一次。
判据与豁免都带着它成立的前提，把前提写在判据旁边。八个「问错主语」的实例与
全文在 `docs/rules/repo/predicate-subject.md`。

## 最常用验证

```sh
ruff check . --fix && ruff format .        # 开发时：修 + 排 import + 格式化（秒级）
ruff check . && ruff format --check .      # 提交前：只检查，与 CI 跑的完全一致
.venv/bin/python -m pytest                 # 后端（tests/ 跑在 .venv）
cd web && pnpm test && pnpm build          # 前端 + 类型检查（别用 tsc --noEmit：恒假绿）
cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check
python scripts/smoke_app.py --python .venv/bin/python   # 端到端冒烟
```

- **改完 Python 先过 Ruff，再跑针对性 pytest，最后才是完整验证。** 提交前那条
  与 CI 那一格逐字相同；`--unsafe-fixes` 会动语义，要逐条看过再用。规则集在
  `pyproject.toml` 的 `[tool.ruff]`，细节与取舍在 `docs/ci/ruff.md`。示例图库 /
  playground 示例 / CompatBench 语料**不参与格式化**。**Ruff 不替代任何语义门禁。**
- **新增一处会被塞进 `sys.path` 的仓库内源码根时，必须同步审查 `[tool.ruff]`
  的 `src`**——否则从那个目录平铺 import 的模块会被判成第三方。在已有源码根
  下新增模块不用动它。
- 改了 `src/tavotto/rendercore/` 里字体 / 字形归属相关的东西、或换了批准字体集合
  （`rendercore/fonts_allowlist.json`，先改 ADR 0060）：`python scripts/gen_canvas_coverage.py --write`
  与 `python scripts/gen_glyph_plan_vectors.py --write`——那张覆盖表是前端「这个字
  导出后是不是方框」的唯一依据。字体文件不进 git：跑用例 / 打包前先 `python scripts/fetch_fonts.py`。
- 改了 `web/src` 或引擎四模块（manifest/overrides/pathgeom/patchspec）：
  playground 产物 `python scripts/build_browser_playground.py`（网站仓库提交它，
  `--check` 防漂移）；Codex 画布 `python scripts/build_mcp_widget.py` **只为本地
  试用**——它不进 git（ADR 0043），CI 从每次 checkout 现建并验证。
- 引擎改动后重启服务：`lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 完整验证链（CompatBench / 等价性矩阵 / 不变式 / nightly / E2E / 性能基线）
  见 `docs/rules/ci/verification-chain.md`（`.github/AGENTS.md` 是速查表）。指导文档自身的门禁：`tests/test_agents_rules_index.py`
  （速查表 ↔ 细则一一对应、引用的路径 / 用例 / ADR 都在），体积按实际加载路径
  量：`python scripts/dev/agents_budget.py`。

## 子系统索引（改哪里，先读哪份）

| 目录 | 规则文件 | 覆盖 |
| --- | --- | --- |
| `src/tavotto/`（含 `engine/`） | `src/tavotto/AGENTS.md` → `docs/rules/backend/` | Flask、渲染引擎、worker 协议、PDF 后端、写回、编码 Agent 桥、遥测、预检、外部交接 |
| `web/` | `web/AGENTS.md` → `docs/rules/frontend/` | 前端、渲染态、预览平面、命中几何、i18n、playground、UI 视觉纪律 |
| `src-tauri/` | `src-tauri/AGENTS.md` | 桌面壳、ACL、更新通道、安装界面、壳内 i18n |
| `workerd/` | `workerd/AGENTS.md` | Rust supervisor |
| `packaging/` | `packaging/AGENTS.md` | wheel/sdist、内置渲染 runtime、PyInstaller、macOS 签名 |
| `codex-plugin/` | `codex-plugin/AGENTS.md` | Codex 插件、技能、MCP server、内嵌画布、首次使用契约 |
| `.github/` | `.github/AGENTS.md`（速查表）→ `docs/rules/ci/` | CI 分层、门禁纪律、验证链、发布链 |
| 跨仓库 | `docs/rules/repo/` | 同源对总表、判据的主语 |
| 架构决策 | `docs/adr/` | 改动前先读对应 ADR |
