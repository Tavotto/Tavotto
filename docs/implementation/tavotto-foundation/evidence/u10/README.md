# evidence/u10 — 默认后端切换与 PyMuPDF 退役（ADR 0072 / 0073）

全部在本机 macOS arm64（Python 3.13.11，rc-venv）跑出；路径已脱敏成 `<worktree>` / `<scratch>` / `<home>`。

| 文件 | 内容 | 判据 |
|---|---|---|
| `wheel_matrix.json` | `scripts/dev/u10_wheel_matrix.py`：五个原生包（pikepdf / pypdfium2 / uharfbuzz / pillow / lxml）× Python 3.10–3.14 × 五个目标逐格 `pip download --only-binary` | 正式三格（win x64 / macOS arm64 / Linux x86_64）75/75；informational：Linux aarch64 全有，**macOS x86_64 pikepdf 10.x 五档无 wheel** |
| `pre_cutover/retirement_scan_old_closure.json` | 退役扫描器在 U09 tip（旧闭包，PyMuPDF 默认）上跑 | **负例**：source / deps / block 三尺全红 |
| `pre_cutover/retirement_scan_candidate_block.json` | 同一提交，`TAVOTTO_RENDER_BACKEND=rendercore`，block 尺子 | 17 步全过（候选在阻断器下跑通全部主要路径） |
| `pre_cutover/retirement_scan_candidate_smoke.json` | 同上，`--smoke`：`smoke_app.py` 全路径，父进程经 sitecustomize 带阻断器 | 退出 0，marker 证明阻断器在父进程 |
| `pre_cutover/parity_u09_tip.json` | `scripts/dev/u08_parity.py` 在 U09 tip 上的最后一次对拍 | 654 通过 / 0 失败 / 13 skip（workerd 二进制 / 画布产物没建）/ 14 deselect |
| `freeze/report-darwin-arm64.json` + png | `scripts/dev/u07_freeze_child.py`：最小候选 freeze | 7/7（PDFium 库进产物、13 张脸、child 以同一 exe 自起、真渲染） |
| `canvas_coverage.pymupdf.json` / `glyph_plan_vectors.pymupdf.json` | 退役前的旧覆盖表 / 旧向量（**批准资产**） | `tests/test_rendercore_glyph_vectors.py` 对着它们钉差异闭集 |
| `post_cutover/retirement_scan_source_deps_block_run.json` | 切换后源码树：source / deps / block / run 四尺 | 全绿 |
| `post_cutover/retirement_scan_wheel.json` | `python -m build --wheel` 出来的 wheel | Requires-Dist 运行时 7 个包、字体 13、覆盖表在、零原生文件 |
| `post_cutover/retirement_scan_frozen_dist.json` | `build_desktop.py --skip-tauri --skip-runtime` 出来的 `dist/Tavotto`（产品 spec） | 111 个原生文件零 mupdf、libpdfium + libqpdf 在、字体 13 |
| `post_cutover/frozen_smoke_macos_arm64.txt` | 同一冻结产物的 `smoke_app.py --exe` | 预览 PNG（frozen render child 自起）、渲染 × 2、导出 × 2、干净退出 |
| `post_cutover/main_venv_contrast.txt` | 主 `.venv`（没装候选包）里跑新默认 | `CandidatePackagesMissing` / `backend_unavailable` / `export_failed` 点名缺的包——没有静默回退 |
| `mutations.json` | 15 条变异反证（ADR 0072 §4 + 本阶段新增判据），每条记变异 / 文件 / 目标用例 / 退出码 / 打红的那条用例名 | `all_red: true`：15 条各把**指定的**用例打红（不是 import 炸掉），还原后同一批用例全绿 |

旧后端的其它冻结资产在 `tests/fixtures/legacy_pymupdf/`（用例直接读）。
