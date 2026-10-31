# 批准字体随包与 RenderCore 原生闭包

> 原文出自 `packaging/AGENTS.md`「内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `packaging/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：字体不进 git，构建机先跑 `fetch_fonts.py`、缺字体拒绝打包；PDFium / qpdf 原生库显式收进冻结产物；`entry.py` 最先分派 `--render-child`；产物用 `retirement_scan.py --dist` 扫

<!-- agents-route
sheet: packaging/AGENTS.md
order: 60
改到: `src/tavotto/resources/fonts/`、`scripts/fetch_fonts.py`、`fonts_allowlist.json`、`packaging/entry.py`、`scripts/ci/retirement_scan.py`
主题: 批准字体（ADR 0060）与 RenderCore 原生闭包（ADR 0072） → `fonts-and-rendercore-closure.md`
必守要点: 字体不进 git，构建机先跑 `fetch_fonts.py`；PDFium / qpdf 原生库显式收进冻结产物；`entry.py` 最先分派 `--render-child`
-->

- **批准字体随包走（2026-09-21，统一实施包 U06，ADR 0060）**：`src/tavotto/resources/fonts/`
  由 `scripts/fetch_fonts.py` 按 `src/tavotto/rendercore/fonts_allowlist.json` 的 sha256 取下来
  （Liberation 2.1.5 × 12 + Noto Sans SC 子集，OFL 1.1，许可证全文同目录）；**不进 git**
  （`.gitignore` 挡、`tests/test_font_provenance.py` 看护）。wheel 靠 pyproject 的
  `[tool.hatch.build] artifacts` 收回（与 `src/tavotto/web/**` 同一条理由），PyInstaller 靠上面那条
  `resources/` datas 整棵带走——**构建机先跑一次 `fetch_fonts.py`**，否则冻结产物里没有字体、
  RenderCore 报 `fonts_dir_missing`。U10 切默认后（ADR 0072）这一步已接进 `build_desktop.py`
  （PyInstaller 之前）、ci.yml 的每条装项目 / 打 wheel / PyInstaller 的腿（`tests/test_merge_queue_workflows.py::
  TestApprovedFontsAndRetirementScan` 钉着闭集）、`tavotto.spec`（缺字体拒绝打包，判据与 `fetch_fonts.py --check`
  同源）与 `lab_acceptance.py` 的结构检查（13 张脸 + 覆盖表 + 依赖闭包零 pymupdf）。运行时定位
  `TAVOTTO_FONTS_DIR` 排他覆盖 → 包内 `tavotto/resources/fonts`；下载缓存在 `build/fonts-cache/`，
  不在字体目录里（字体目录整棵进包）。
- **RenderCore 的原生闭包随冻结产物走（U10，ADR 0072）**：PDFium 的共享库住在 `pypdfium2_raw` 的包目录里、
  pikepdf 的 qpdf 库同理——PyInstaller 的依赖分析看不见，`tavotto.spec` 显式 `collect_dynamic_libs("pypdfium2_raw")`
  + `collect_all("pikepdf")`（配方与 `scripts/dev/u07_freeze_child.py` 同源，四平台验过 child 自起 / 真渲染）；
  `PIL` 从 excludes 拿掉（Pillow 是 `rasterio` 的直接依赖），退役的 `pymupdf` / `fitz` 反过来进 excludes。
  冻结入口 `packaging/entry.py` **最先**分派 `--render-child`（父进程用同一个 exe 起 PDFium 子进程，
  在 stdin / stdout 上说 JSON，不能先经 `_redirect_streams`）。打包依赖装的是 `requirements.txt` 那份钉死的
  运行时闭包（不再手写 `flask + pymupdf`）；产物出来之后 `scripts/ci/retirement_scan.py --dist dist/Tavotto`
  扫原生库名单（零 mupdf、PDFium / qpdf 在、字体 13 张）。看护 `tests/test_runtime_build.py::
  test_spec_ships_the_rendercore_closure_and_refuses_to_freeze_without_the_fonts`。
