"""Tavotto — 论文 Figure 排版 + 参数化图表编辑工具。

包内只放轻量常量：`tavotto.app` 会拉起 Flask 与 PyMuPDF，
import 代价不该由 `import tavotto` 承担（CLI 探测版本号等场景）。
"""

__version__ = "0.12.0"

__all__ = ["__version__"]
