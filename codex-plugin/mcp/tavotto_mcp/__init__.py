"""Tavotto 的 MCP server —— 把 Tavotto 的渲染引擎接到 Codex 里。

分工（三层，别混）：

* **skill**（`codex-plugin/skills/tavotto-figure/`）：教 Codex 怎么写出一张
  「Tavotto 接得住」的脚本，以及交接给桌面版的最后一跳。
* **MCP server**（本包）：把 Tavotto 的引擎会话、canonical override、出版规范
  预检与导出暴露成工具。**没有 UI 的 host 里这五个工具就足以走完整条流程。**
* **MCP App UI**（`codex-plugin/mcp/widget/canvas.html`）：Codex 内嵌的交互画布，
  复用 Tavotto 前端那一份画布代码（拖拽/命中测试/吸附/undo 一行都没重写），
  所有后端往来都经 `tools/call` 回到本 server。

**绝不在这里重写渲染器**：manifest、override 语义、patch 规范化、worker 协议
全部直接用 `tavotto.engine.*`。本包只是一层协议翻译 + 会话账本。

纯标准库（除了它 import 的 tavotto 本体）。
"""

__all__ = ["__version__"]

#: 与 Tavotto 本体同版本；实际取值在 server.py 里从 tavotto.__version__ 读，
#: 这里只是包元数据的占位（本包不单独发版）。
__version__ = "0"
