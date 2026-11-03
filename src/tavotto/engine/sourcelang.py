"""来源语言识别（ADR 0143 修订「R / MATLAB 来源」）：一个脚本文件是 Python、R、MATLAB，还是判不出。

「生成可编辑版本」第一版接受 `.py`（Python）与 `.R` / `.r`（R）、`.m`（MATLAB）纯文本脚本。`.m` 是同时被
MATLAB 与 Objective-C 用的后缀，所以这里不只看后缀：看内容里的语言特征，**判不出就回 None（拒绝，不猜）**——
把一份 Objective-C 源文件当 MATLAB 交给 Agent 去“转写”，既浪费一次付费调用，也会给出一份看似合理的假脚本。

边界（与 `tests/test_sourcelang.py` 对应）：

* 体积上限 `MAX_BYTES`（1 MB）：超过的不看内容，直接 None；
* 必须是 UTF-8（带 BOM 可以）；含 NUL 字节（二进制）也是 None；
* `.Rmd` / `.qmd` / `.mlx` / `.fig` / `.rds` 等不在范围内：后缀不是 `.R` / `.r` / `.m` / `.py` 一律 None。

纯标准库，Flask 父进程 import（`app.api_ai_convert` 复核请求声明的语言与文件一致）。
"""

from __future__ import annotations

import re
from pathlib import Path

PYTHON = "python"
R = "r"
MATLAB = "matlab"
#: 请求体 `source_language` 的闭集（缺省 = python）
LANGUAGES = (PYTHON, R, MATLAB)
#: 需要内容复核 / 走“转写”路径的来源语言（非 Python）
FOREIGN = (R, MATLAB)

#: 内容体积上限：更大的“脚本”基本不是人写的绘图脚本，也撑不住一次提示词
MAX_BYTES = 1024 * 1024

# Objective-C（以及别的 C 系）的强特征：行首的预处理指令 / @ 关键字 / Foundation 类型 / main 签名。
# MATLAB 的 `@` 只出现在匿名函数（`@(x)`）与函数句柄（`@sin`）里，不会以这些关键字紧跟行首。
_OBJC = re.compile(
    r"""^[ \t]*(?:
        \#[ \t]*(?:import|include|define|ifdef|ifndef|pragma)\b
      | @(?:interface|implementation|end|property|protocol|class|synthesize|autoreleasepool|selector)\b
      | (?:int|void)[ \t]+main[ \t]*\(
      | [-+][ \t]*\([ \t]*\w[\w \t*<>]*\)[ \t]*\w+          # - (void)foo: 方法声明
    )""",
    re.M | re.X,
)
_OBJC_TYPES = re.compile(r"\bNS[A-Z]\w+\b|\bUI[A-Z]\w+\b|\[\[\w+[ \t]+(?:alloc|new)\]")

# MATLAB 的特征（每类算一票）：行首 `%` 注释、`function` 定义、独占一行的 `end`、绘图 / 读数据调用、工作区命令、续行号 `...`、矩阵字面量里的分号行
_M_COMMENT = re.compile(r"^[ \t]*%", re.M)
_M_FUNCTION = re.compile(r"^[ \t]*function\b[ \t]*[\w\[\]=, \t.]+", re.M)
_M_END = re.compile(r"^[ \t]*end[ \t]*;?[ \t]*(?:%.*)?$", re.M)
_M_CALL = re.compile(
    r"\b(?:plot|plot3|figure|scatter|errorbar|bar|barh|histogram|hist|boxplot|subplot|tiledlayout|nexttile|"
    r"xlabel|ylabel|zlabel|title|legend|hold|axis|xlim|ylim|grid|colorbar|colormap|saveas|exportgraphics|"
    r"readtable|readmatrix|csvread|importdata|xlsread|fitlm|polyfit|polyval|fitnlm|lsqcurvefit|linspace|"
    r"meshgrid|surf|contour|imagesc|semilogx|semilogy|loglog)[ \t]*(?:\(|[ \t]+['\"\w])",
)
_M_WORKSPACE = re.compile(
    r"^[ \t]*(?:clc|clear(?:[ \t]+all)?|close[ \t]+all|hold[ \t]+(?:on|off)|format[ \t]+\w+)[ \t]*;?[ \t]*(?:%.*)?$",
    re.M,
)
_M_ELLIPSIS = re.compile(r"\.\.\.[ \t]*$", re.M)
_M_MATRIX = re.compile(r"=[ \t]*\[[^\]\n]*;[^\]\n]*\]|\b\w+\([^()\n]*\)[ \t]*=[ \t]*\[")
_M_FEATURES = (_M_COMMENT, _M_FUNCTION, _M_END, _M_CALL, _M_WORKSPACE, _M_ELLIPSIS, _M_MATRIX)
#: 至少这么多类特征同时出现才认 MATLAB（一类可能只是巧合：例如一个 `%` 开头的注释行）
MATLAB_MIN_FEATURES = 2


def _decode(text: str | bytes) -> str | None:
    """文本 / 字节 → str；超限、非 UTF-8、含 NUL 回 None。"""
    if isinstance(text, bytes):
        if len(text) > MAX_BYTES:
            return None
        try:
            out = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    else:
        if len(text) > MAX_BYTES or len(text.encode("utf-8", errors="surrogatepass")) > MAX_BYTES:
            return None
        out = text
    return None if "\x00" in out else out


def _looks_matlab(text: str) -> bool:
    if _OBJC.search(text) or _OBJC_TYPES.search(text):
        return False
    return sum(1 for rx in _M_FEATURES if rx.search(text)) >= MATLAB_MIN_FEATURES


def detect(path: str | Path, text: str | bytes) -> str | None:
    """`path` 的后缀 + 内容 → `"python"` | `"r"` | `"matlab"` | `None`（判不出 / 不在范围内：调用方一律拒绝，不猜）。

    `.py` 只认后缀（Python 脚本的语法有效性是后面的检查的事）；`.R` / `.r` 要求内容非空、是文本；`.m` 要求内容里
    有足够的 MATLAB 特征且没有 Objective-C 特征。"""
    suffix = Path(str(path)).suffix
    body = _decode(text)
    if body is None or not body.strip():
        return None
    if suffix == ".py":
        return PYTHON
    if suffix in (".R", ".r"):
        return R
    if suffix == ".m":
        return MATLAB if _looks_matlab(body) else None
    return None


def detect_file(path: str | Path) -> str | None:
    """读文件（最多 `MAX_BYTES` + 1 字节，多读一字节只为发现超限）再 `detect`；读不出来回 None。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError:
        return None
    return detect(path, data)


def extension_hint(name: str) -> str | None:
    """只看后缀的粗判（前端同款：`.R` / `.r` → r，`.m` → matlab）。**不是**最终结论——`.m` 还要 `detect` 过内容。"""
    suffix = Path(name).suffix
    if suffix in (".R", ".r"):
        return R
    if suffix == ".m":
        return MATLAB
    return None
