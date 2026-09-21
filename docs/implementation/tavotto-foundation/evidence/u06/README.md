# U06 evidence（RenderCore 第一个切片：真字体 → RenderPlan → 可检索 PDF → 独立读取器）

全部由 `scripts/dev/u06_evidence.py` 生成；候选包装在独立 venv（`python -m venv <scratch>/rc-venv &&
<scratch>/rc-venv/bin/pip install -e '.[rendercore,dev]' pdfminer.six==20251107 pypdf==6.7.5`），批准字体由
`scripts/fetch_fonts.py` 取到 `src/tavotto/resources/fonts/`。结论在 `docs/adr/0059-render-ir-and-render-plan.md` /
`0060-font-policy-and-allowlist.md`，交接在 `../../handoffs/U06_ir_text.md`。

| 文件 | 来源 | 进 git 的理由 |
|---|---|---|
| `truth.json` | **手写**的输入规格（页面 / 对象 / 每行文字各把尺子该抽出什么 / 像素采样点的页面 pt 坐标——手算，不调 `plan.py`） | 判据的主语 |
| `u06.pdf`（14 KB） | `rendercore.pdfwriter` 写出（pikepdf + fontTools + HarfBuzz；同一输入两次运行字节相同） | `tests/test_rendercore_evidence.py` 用纯标准库读它 |
| `u06_pdfium.png`（851×454 RGBA，28 KB） | PDFium 5.13.0 / 153 栅格（scale 2）。**跨平台像素不同**（ADR 0055 §7），只做同平台对照 | 同上（像素采样） |
| `report.json` | 版本 / 输入输出 hash / 写入事实 / 四把独立读取器（纯标准库 / PDFium / pdfminer / poppler）的读数 / 覆盖表 diff 摘要 / 42 条核对 | 与其它文件的 hash 互相钉住 |
| `canvas_coverage.rendercore.json` | 批准字体集合下画布文字的三层覆盖表（primary = 12 张 Liberation 脸 cmap 的**交集**，cjk = Noto Sans SC，fallback 空），与 `pdfbackend/canvas_coverage.json` 同形 | **不是产品用的那份**（U06 不切默认）；D07 迁移的依据 |
| `coverage_diff.json` | 旧表（PyMuPDF 1.28.2）→ 新表逐层码位差、可画码位总数变化、Liberation 各脸 cmap 差异、科学文本矩阵在新集合下的分层 | 集合外限制的数字出处（ADR 0060 §1） |

重生成（macOS arm64）：

```sh
S=<scratch>
python scripts/fetch_fonts.py
PYTHONPATH=src $S/rc-venv/bin/python scripts/dev/u06_evidence.py --out docs/implementation/tavotto-foundation/evidence/u06
```

其它平台由 `.github/workflows/foundation-u06-rendercore.yml`（`workflow_dispatch` + 只在 rendercore 文件变动时的
`pull_request`，非 required）以工件 `u06-evidence-<os>-py<ver>` 给出：`u06.pdf` 与覆盖表必须逐字节等于 git 里的，
PNG 只作信息；run 号记在交接文件。
