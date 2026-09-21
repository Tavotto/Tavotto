# legacy_pymupdf/ — 旧后端（PyMuPDF 1.28.2）退役前留下的批准资产

统一实施包 U10（ADR 0072）删掉 `src/tavotto/pdfbackend/pymupdf_backend.py` 之后，旧后端不再能在测试里现跑；
「历史差分仅在隔离工具 / 批准资产」（06 §3）——需要旧行为当参照的用例改读这里的**冻结产物**，不再 import 旧模块。
每份文件都是在退役前的最后一个提交（`8961883d`，macOS arm64）上由旧后端跑出来的，生成方式写在 `oracle.json`。

| 文件 | 由谁生成 | 谁读 |
|---|---|---|
| `oracle.json` | 旧契约层 19 项里可钉成常量的返回值：`probe_asset` 三份夹具、`pdf_fonts`、三串文字 12 pt 的 `text_width` / `text_plan`、`CANVAS_TEXT_FAMILIES` / `COVERAGE_MAX_CP`、Times-Roman 的 ascender / descender | `tests/test_rendercore_facade.py`（对拍改成对常量）、`tests/test_rendercore_calibration.py`（基线迁移量） |
| `preview_300.png` | `render_preview_png(page.pdf, 300)`（MuPDF 栅格） | `test_rendercore_facade.py::test_compare_png_*`：另一个栅格器那一侧 |
| `calibration/<case>.pdf` | `test_rendercore_calibration.py::_old_pdf`（旧 `compose` 合成，dpi 600）五个 case | `test_rendercore_calibration.py`：新核心 vs 冻结的旧产物，按 case 阈值 |

**不是**用户合同的出处：用户合同在各自的用例里（尺寸、文件类型、像素网格、文字可检索、原件零改动……），
这里只是「旧实现当年怎么做」的证据。要重生成只能在装了 `tavotto[legacy-pymupdf]` 的环境里回到那个提交跑
——正常开发不需要。
