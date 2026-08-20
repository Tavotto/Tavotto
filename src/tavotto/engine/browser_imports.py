"""浏览器 playground 的 import 静态分类（**纯标准库**，独立成模块是有意的）。

`browser.py` 模块级就 import matplotlib——而分类恰恰要在「决定下不下载
matplotlib 那十几 MB」**之前**跑：脚本要 rdkit 的话，包一个字节都不该下，
直接告诉用户去桌面版。所以 JS Worker 在 Pyodide 核心起来后先单独
`pyimport('browser_imports')` 做分类，通过了才 loadPackage + import browser。

`browser.handle` 的 `classify` 命令代理到这里——实现只有这一份。
"""
from __future__ import annotations

import ast
import json
import sys


def classify_imports(source: str, supported_roots: dict[str, str]) -> dict:
    """静态分类脚本 import 的顶层模块名。

    `supported_roots` 是「import 根名 → Pyodide 包名」的映射，**由调用方传入**
    ——权威在 `packaging/playground-runtime.json`，JS 侧 import 了那份 JSON，
    pytest 侧直接读文件。这里不内置一份副本，两份迟早漂开。

    `try: import x / except ImportError` 里的 import 按**可选**处理，不算
    unsupported——科研脚本里 `try: import seaborn` 这种模式很常见，它们自己
    已经准备好了没有的情形。
    """
    tree = ast.parse(source)

    optional_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import = any(
            _handler_catches_import_error(h) for h in node.handlers)
        if catches_import and node.body:
            optional_spans.append(
                (node.body[0].lineno, max(n.end_lineno or n.lineno for n in node.body)))

    def _optional(lineno: int) -> bool:
        return any(a <= lineno <= b for a, b in optional_spans)

    roots: dict[str, bool] = {}  # 根名 → 是否必需（任何一处必需即必需）
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            required = not _optional(node.lineno)
            roots[name] = roots.get(name, False) or required

    stdlib = set(sys.stdlib_module_names)
    out = {"supported": [], "stdlib": [], "unsupported": [], "optional_unsupported": []}
    packages: set[str] = set()
    for name in sorted(roots):
        if name in supported_roots:
            out["supported"].append(name)
            packages.add(supported_roots[name])
        elif name in stdlib or name.startswith("_"):
            out["stdlib"].append(name)
        elif roots[name]:
            out["unsupported"].append(name)
        else:
            out["optional_unsupported"].append(name)
    out["packages"] = sorted(packages)
    return out


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    def _is(t) -> bool:
        name = t.id if isinstance(t, ast.Name) else (
            t.attr if isinstance(t, ast.Attribute) else "")
        return name in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")

    t = handler.type
    if t is None:
        return True  # 裸 except 也接住 ImportError
    if isinstance(t, ast.Tuple):
        return any(_is(el) for el in t.elts)
    return _is(t)


def classify_json(request_json: str) -> str:
    """JS Worker 的出入口：JSON 字符串进出，与 `browser.handle` 同一纪律。"""
    try:
        req = json.loads(request_json)
        out = {"ok": True,
               **classify_imports(req["source"], req["supported_roots"])}
    except SyntaxError as exc:
        out = {"ok": False, "code": "syntax_error",
               "message": f"{exc.msg} (line {exc.lineno})", "line": exc.lineno}
    except Exception as exc:  # noqa: BLE001 - 边界函数，绝不向 JS 抛
        out = {"ok": False, "code": "internal_error", "message": str(exc)}
    return json.dumps(out, ensure_ascii=False)
