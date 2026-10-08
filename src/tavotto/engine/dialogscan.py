"""脚本里的「弹窗」写法（交互式选文件 / 询问）的静态识别——准备会话跑之前给一句提示。

Tavotto 里的脚本是无界面 worker 跑的、而且每次编辑都会重跑：`tkinter.filedialog.askopenfilename()`
这类写法在这里本来就不成立（弹不出窗口；Windows 内置 runtime 甚至没有 tkinter），用户只会在运行失败
之后看到一条看不懂的缺包 / 报错。本模块只回答「源码里有没有这类调用」，**不执行脚本、不 import 任何用户模块**。
运行时的 `input()` / getpass 不在这里（那是 ADR 0099 的运行时作答桥）。

识别对象（调用，按**解析后的全名**判，不按子串）：

* `tkinter.filedialog.*`（`askopenfilename(s)` / `askopenfile(s)` / `asksaveasfilename` / `asksaveasfile` /
  `askdirectory` 与 `Open` / `SaveAs` / `Directory` 类）、`tkinter.simpledialog.ask*`、`tkinter.messagebox.ask*`；
* PyQt5/6、PySide2/6 的 `QtWidgets.QFileDialog.get*`；
* `easygui.fileopenbox` / `filesavebox` / `diropenbox`；
* `wx.FileDialog` / `wx.DirDialog`。

名字解析只认脚本自己的 `import` / `from … import … as …`（`import tkinter.filedialog`、`import tkinter as tk` 后写
`tk.filedialog.x()`、`from tkinter import filedialog as fd`、`from tkinter.filedialog import askopenfilename as pick`
都能解到同一个全名）。注释、字符串、docstring 里出现这些字样不是调用，AST 天然不算；没有 import 绑定的同名函数不算。

**取舍：看全部写在源码里的调用，只跳过确定不会执行的。** 与 `importscan` 相反：那里「宁可少装不误装」，因为动作会改用户的环境；
这里的结果只是一句**不阻塞**的提示（主按钮仍是运行），多提示一次的代价是点一下「仍然运行」，漏提示就是原来的那条看不懂的报错。
函数体里的调用（`def pick(): return filedialog.askopenfilename()` 再在入口调用，是最常见的写法）和 `if` / `try` /
`for` 里的调用都算；只有 `if TYPE_CHECKING:` 的那一半与 `if False:` / `if 0:` 这类常量假的分支被跳过。
只看脚本本身，不跟进它 import 的本地模块（跟进是另一件事，本包不做）。
"""

from __future__ import annotations

import ast
import os
import threading
from collections import OrderedDict
from pathlib import Path

#: 与 `scriptargs.MAX_SOURCE_BYTES` 同量级：超了按看不全处理（不提示，不猜）。
MAX_SOURCE_BYTES = 1024 * 1024
#: 报告里最多列几处（提示只需要「在哪几行」，不是清单）。
MAX_CALLS = 8

KIND_FILE = "file"  # 选文件 / 目录
KIND_PROMPT = "prompt"  # 询问文字 / 是否

_TK_FILE = {
    "askopenfilename",
    "askopenfilenames",
    "askopenfile",
    "askopenfiles",
    "asksaveasfilename",
    "asksaveasfile",
    "askdirectory",
    "Open",
    "SaveAs",
    "Directory",
}
_QT_FILE = {
    "getOpenFileName",
    "getOpenFileNames",
    "getSaveFileName",
    "getExistingDirectory",
    "getOpenFileUrl",
    "getOpenFileUrls",
    "getSaveFileUrl",
    "getExistingDirectoryUrl",
}

#: 解析后的全名 → 种类。
_TARGETS: dict[str, str] = {}
for _n in _TK_FILE:
    _TARGETS[f"tkinter.filedialog.{_n}"] = KIND_FILE
for _n in ("askstring", "askinteger", "askfloat"):
    _TARGETS[f"tkinter.simpledialog.{_n}"] = KIND_PROMPT
for _n in ("askquestion", "askokcancel", "askyesno", "askyesnocancel", "askretrycancel"):
    _TARGETS[f"tkinter.messagebox.{_n}"] = KIND_PROMPT
for _pkg in ("PyQt5", "PyQt6", "PySide2", "PySide6"):
    for _n in _QT_FILE:
        _TARGETS[f"{_pkg}.QtWidgets.QFileDialog.{_n}"] = KIND_FILE
for _n in ("fileopenbox", "filesavebox", "diropenbox"):
    _TARGETS[f"easygui.{_n}"] = KIND_FILE
for _n in ("FileDialog", "DirDialog"):
    _TARGETS[f"wx.{_n}"] = KIND_FILE
del _n, _pkg


def _bindings(tree: ast.AST) -> dict[str, str]:
    """脚本里 import 绑定的名字 → 它指向的全名。流程不敏感（整份文件一张表）：同名被重新绑定的极少见情形不处理。"""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:  # `import tkinter.filedialog` 绑定的是顶级名
                    top = a.name.split(".", 1)[0]
                    out[top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _resolve(node: ast.expr, names: dict[str, str]) -> str | None:
    """`Name` / `Attribute` 链 → 解析后的全名；根不是 import 绑定的名字 → None。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in names:
        return None
    return ".".join([names[node.id], *reversed(parts)])


def _never_runs(test: ast.expr) -> bool:
    return isinstance(test, ast.Constant) and not test.value


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Finder(ast.NodeVisitor):
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names
        self.calls: list[dict] = []

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if _is_type_checking(node.test):
            body: list[ast.stmt] = []
            rest = node.orelse  # `else:` 是运行时那一半
        elif _never_runs(node.test):
            body, rest = [], node.orelse
        else:
            body, rest = node.body, node.orelse
        for child in (*body, *rest):
            self.visit(child)

    def visit_Call(self, node: ast.Call) -> None:
        full = _resolve(node.func, self.names)
        kind = _TARGETS.get(full or "")
        if kind is not None:
            self.calls.append({"api": full, "kind": kind, "line": node.lineno})
        self.generic_visit(node)


def analyze(source: str) -> dict:
    """源码 → `{"status", "calls": [{api, kind, line}…], "truncated"}`；语法错误 / 看不懂 → `status="unknown"`，不猜。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {"status": "unknown", "calls": [], "truncated": False}
    finder = _Finder(_bindings(tree))
    try:
        finder.visit(tree)
    except RecursionError:
        return {"status": "unknown", "calls": [], "truncated": False}
    calls = sorted(finder.calls, key=lambda c: (c["line"], c["api"]))
    return {
        "status": "found" if calls else "none",
        "calls": calls[:MAX_CALLS],
        "truncated": len(calls) > MAX_CALLS,
    }


_CACHE_SIZE = 64
_cache: OrderedDict[tuple, dict] = OrderedDict()
_cache_lock = threading.Lock()


def analyze_file(path: str | os.PathLike) -> dict:
    """读一份脚本（有界）→ `analyze`。按 (realpath, mtime_ns, size) 缓存：报告每次轮询都要它，不重复解析。"""
    p = Path(path)
    unknown = {"status": "unknown", "calls": [], "truncated": False}
    try:
        st = p.stat()
    except OSError:
        return dict(unknown)
    if st.st_size > MAX_SOURCE_BYTES:
        return dict(unknown)
    key = (os.path.realpath(p), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    try:
        with open(p, "rb") as fh:
            raw = fh.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        return dict(unknown)
    if len(raw) > MAX_SOURCE_BYTES:
        return dict(unknown)
    result = analyze(raw.decode("utf-8", errors="replace"))
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return result
