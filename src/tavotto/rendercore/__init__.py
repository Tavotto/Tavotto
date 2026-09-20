"""RenderCore —— 画布合成 / 可检索文字 / 栅格的新核心（统一实施包 U06 起，ADR 0059 / 0060）。

## 分层（`tests/test_rendercore_model.py` 用 importgraph 钉着）

| 层 | 模块 | 允许的依赖 |
|---|---|---|
| 纯模型 | `ir` / `geometry` / `typography` / `sources` / `plan`（`fonts` 随 ADR 0060 加入） | **只有标准库**与仓库里同样纯标准库的模块（`richtext` / `glyphplan` / `engine.exportreq` / `engine.figcapture`）。不 import matplotlib / numpy / pymupdf / flask / pikepdf / fontTools / uharfbuzz |
| native 适配 | `hbshaper`（uharfbuzz + fontTools）、`pdfwriter`（pikepdf + fontTools） | 候选包只在这里 import，且都在函数 / 类内按需 import：装了 `tavotto[rendercore]` 才有 |
| 入口 | `job`（接 `engine.exportjob` 的 `produce` 形状） | 纯模型 + native 适配 |

**新核心零 `import pymupdf`**（D03：先限制新核心不借旧库，`tests/test_rendercore_model.py`
钉着）；PyMuPDF 仍是默认后端，本包在 U06 不接任何用户可见入口——它由测试驱动跑通
「录制（IR）→ 编译（RenderPlan）→ 写入（可检索 PDF）→ 独立读回」这一条切片。

本文件不 import 任何子模块：`import tavotto.rendercore` 不该把候选包拉起来。
"""

BACKEND_NAME = "rendercore"
#: 本包自己的版本串（进渲染缓存键的那一维，U08 接 facade 时与 `pdfbackend.BACKEND_VERSION`
#: 同一位置）。候选包的版本另记在 `pdfwriter.versions()`。
BACKEND_VERSION = "0.1"
