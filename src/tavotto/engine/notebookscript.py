"""Jupyter 笔记本（`.ipynb`）→ 同名 `.py`：确定性的抽取与落盘（ADR 0179）。

不经 AI、不联网、不执行任何东西，只读笔记本的 JSON，只写笔记本旁边的一个 `.py`。
**用户的笔记本一个字节不动**；用户自己的同名 `.py` 也绝不覆盖。纯标准库（Flask 父进程一侧）。

两半：

* **抽取**（`extract`，纯函数）：只认 nbformat 4；代码单元按顺序拼成脚本，单元之间 `# %%`（VS Code / Spyder 的分隔写法）；
  Markdown 单元留作 `# ` 注释（`# %% [markdown]`）；输出 / 执行计数 / 元数据 / 内嵌图片一律不带。
  IPython 专有语法（行魔法、单元魔法、`!` 与 `?`）不是合法 Python：改成注释并保留原文（`# [notebook] %matplotlib inline`），
  不猜等价物；最后整份过 `ast.parse` 核验，仍不合法就不落盘。
* **落盘**（`place`）：在笔记本同目录、同基名；已有同名文件时的处置见 `place` 的文档。文件头第三行是自己的标记
  （笔记本文件名 + 正文的 sha256），靠它认出「这是本功能生成的、且用户没改过」。

错误码都在 `ERRORS` / `ERROR_CODES`（文案归前端 errors.json，`tests/test_error_codes.py` 看护）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import symtable
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import assetimport, atomicio, discover
from .brand import DIST_NAME, PRODUCT_NAME

#: 笔记本后缀：区分大小写（与 `discover.FOREIGN_SUFFIXES` 一样；Jupyter 只生成小写）。
#: ↔ `src-tauri/src/drop_paths.rs::is_notebook`、`web/src/lib/notebookScript.ts::isNotebookName`（严格同源对，
#: `tests/golden/drop_script_rule.json`）
SUFFIX = ".ipynb"

#: 笔记本上限。笔记本带内嵌图片时常有几十 MB；整份要在内存里解析，所以必须有界（与联系作者附件同一量级）
MAX_BYTES = 50 * 1024 * 1024

#: 本模块与 `/api/notebook/*` 发出的错误码及其中文原文（文案归前端 errors.json）。
#: 落盘失败复用 `asset_import_not_writable` / `_disk_full` / `_write_failed` / `_name_exhausted`（同样的句子）。
ERRORS = {
    "notebook_unreadable": "这个文件不是能打开的笔记本",
    "notebook_unsupported_version": "这是很旧版本的笔记本，暂时转不了",
    "notebook_no_code": "这个笔记本里没有可以运行的代码",
    "notebook_syntax_error": "笔记本里有看不懂的写法，转成脚本后会运行不了",
    "notebook_too_large": "笔记本太大，转不了",
    "notebook_empty": "文件是空的",
    "notebook_not_found": "笔记本不存在",
    "notebook_path_outside_project": "笔记本路径在项目目录之外",
    "notebook_unsupported_type": "不是 .ipynb 笔记本",
}
ERROR_CODES = tuple(ERRORS)


class NotebookError(ValueError):
    """抽取 / 定位失败。`code` 是稳定码，`status` 是 HTTP 状态，`params` 给前端文案占位。"""

    def __init__(self, code: str, status: int = 422, params: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params or {}


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------

#: 注释掉的行的前缀：保留原文，一眼看得出是笔记本里的哪一类东西
_NOTE = "# [notebook] "

#: 单元魔法里**后面跟的是 Python** 的那几个：只注释第一行，代码照留（`%%time` 之后的绘图代码不能丢）。
#: 其余（`%%bash` / `%%html` / `%%writefile` / 不认识的……）整个单元不是 Python，整单元注释掉——
#: 不执行、不猜等价物。
_PYTHON_CELL_MAGICS = frozenset(
    {"time", "timeit", "capture", "prun", "python", "python3", "py", "debug"}
)

_CELL_MAGIC = re.compile(r"%%\s*([A-Za-z_][\w.-]*)")
#: `files = !ls` / `out = %timeit f()`：赋值右边是 shell 转义或行魔法
_ASSIGN_ESCAPE = re.compile(r"^[\w.,\s\[\]()*]+=\s*[!%]")
_HEADER_MARK = re.compile(rf"^# {re.escape(DIST_NAME)}-notebook: (.*) sha256=([0-9a-f]{{64}})\s*$")


@dataclass(frozen=True)
class Extraction:
    """一次抽取的结果：`body` 是文件头之后的全部正文（LF 换行、以换行结尾）"""

    body: str
    #: 有真实语句的代码单元数
    code_cells: int
    #: 被改成注释的行数（IPython 专有语法）
    commented_lines: int


def _label(name: str) -> str:
    """笔记本文件名放进文件头前去掉控制字符（POSIX 文件名可以含换行，不许把头撑破）"""
    return re.sub(r"[\x00-\x1f\x7f]", "_", unicodedata.normalize("NFC", name))


def _source_of(cell: object) -> str:
    """单元的 `source`：字符串或字符串列表；统一成 LF 换行的一个字符串"""
    raw = cell.get("source", "") if isinstance(cell, dict) else ""
    if isinstance(raw, list):
        raw = "".join(s for s in raw if isinstance(s, str))
    if not isinstance(raw, str):
        return ""
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _comment(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    indent = line[: len(line) - len(line.lstrip(" \t"))]
    return f"{indent}{_NOTE}{stripped}"


def _is_ipython_line(line: str) -> bool:
    """出错的这一行像不像 IPython 专有语法（只有 `ast.parse` 已经在这一行报错时才问这个问题）"""
    s = line.strip()
    if not s:
        return False
    return s[0] in "%!?" or s.endswith("?") or bool(_ASSIGN_ESCAPE.match(s))


class _Unfixable(Exception):
    def __init__(self, line: int) -> None:
        super().__init__(line)
        self.line = line


def _fix_code_cell(source: str) -> tuple[list[str], int]:
    """一个代码单元 → `(行, 被注释的行数)`；注释不掉的语法错误抛 `_Unfixable(单元内行号)`"""
    lines = source.split("\n")
    commented = 0
    # 单元魔法：第一个非空行以 `%%` 开头
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None and lines[first].lstrip().startswith("%%"):
        found = _CELL_MAGIC.match(lines[first].lstrip())
        if found and found.group(1).lower() in _PYTHON_CELL_MAGICS:
            lines[first] = _comment(lines[first])
            commented += 1
        else:
            return [_comment(ln) for ln in lines], sum(1 for ln in lines if ln.strip())
    # 其余 IPython 语法：让 Python 自己指出哪一行不合法，是 IPython 写法就注释掉那一行再试。
    # 以 `%` 开头的**续行**（括号里的取模）本来就合法，不会被 `ast.parse` 报错，所以不会误伤
    for _ in range(len(lines) + 1):
        try:
            ast.parse("\n".join(lines))
            return lines, commented
        except (ValueError, RecursionError, MemoryError):
            # 源码里有空字节 / 嵌套深到解析器放弃：核验不了就是不过，不落盘
            raise _Unfixable(0) from None
        except SyntaxError as exc:
            n = exc.lineno or 0
            if not 1 <= n <= len(lines) or not _is_ipython_line(lines[n - 1]):
                raise _Unfixable(n) from None
            i = n - 1
            lines[i] = _comment(lines[i])
            commented += 1
            # 行尾反斜杠续行的 shell 命令：后面接着的行一起
            while lines[i].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                lines[i] = _comment(lines[i])
                commented += 1
    raise _Unfixable(0)


def _has_statement(lines: list[str]) -> bool:
    return any(ln.strip() and not ln.lstrip().startswith("#") for ln in lines)


def extract(data: bytes) -> Extraction:
    """笔记本字节 → 脚本正文。纯函数：不读盘、不执行。失败抛 `NotebookError`。"""
    if not data.strip():
        raise NotebookError("notebook_empty", 400)
    if len(data) > MAX_BYTES:
        raise NotebookError("notebook_too_large", 413, {"limit_mb": MAX_BYTES // (1024 * 1024)})
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise NotebookError("notebook_unreadable") from None
    if not isinstance(doc, dict):
        raise NotebookError("notebook_unreadable")
    version = doc.get("nbformat")
    if not isinstance(version, int) or isinstance(version, bool):
        # 3 及更早的笔记本有 nbformat 却没有顶层 cells；连 nbformat 都没有的不是笔记本
        raise NotebookError("notebook_unreadable")
    if version != 4:
        raise NotebookError("notebook_unsupported_version", 422, {"version": version})
    cells = doc.get("cells")
    if not isinstance(cells, list):
        raise NotebookError("notebook_unreadable")

    out: list[str] = []
    #: （脚本里的起止行号，第几个单元）——整份核验失败时把行号翻回单元
    spans: list[tuple[int, int, int]] = []
    code_cells = 0
    commented = 0
    for index, cell in enumerate(cells, start=1):
        kind = cell.get("cell_type") if isinstance(cell, dict) else None
        source = _source_of(cell).rstrip()
        if kind == "markdown":
            if not source.strip():
                continue
            block = ["# %% [markdown]"] + [f"# {ln}".rstrip() for ln in source.split("\n")]
        elif kind == "code":
            if not source.strip():
                continue
            try:
                fixed, n = _fix_code_cell(source)
            except _Unfixable as exc:
                raise NotebookError(
                    "notebook_syntax_error", 422, {"cell": index, "line": exc.line}
                ) from None
            commented += n
            if _has_statement(fixed):
                code_cells += 1
            block = ["# %%"] + fixed
        else:
            continue  # raw 单元与不认识的类型不带
        if out:
            out.append("")
        spans.append((len(out) + 1, len(out) + len(block), index))
        out.extend(block)
    if code_cells == 0:
        raise NotebookError("notebook_no_code")
    body = "\n".join(out).rstrip("\n") + "\n"
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        line = getattr(exc, "lineno", None) or 0
        cell = next((c for lo, hi, c in spans if lo <= line <= hi), 0)
        raise NotebookError("notebook_syntax_error", 422, {"cell": cell, "line": line}) from None
    try:
        body = _apply_shim(body, tree)
        # 最终核验用 `compile`：`ast.parse` 放过的错（`__future__` 位置之类）在落盘前拦住，不等用户运行。
        # 顶层 `await`（IPython 的 autoawait）与 `ast.parse` 一样放行
        compile(body, "<notebook>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise NotebookError("notebook_syntax_error", 422, {"cell": 0, "line": 0}) from None
    return Extraction(body=body, code_cells=code_cells, commented_lines=commented)


# ---------------------------------------------------------------------------
# IPython 内置名的替身（只在笔记本真用到时才加）
# ---------------------------------------------------------------------------

_SHIM_QUIET = (
    "class _TavottoQuiet:\n"
    "    def __getattr__(self, name):\n"
    "        return self\n"
    "    def __call__(self, *args, **kwargs):\n"
    "        return None\n"
    "_tavotto_quiet = _TavottoQuiet()\n"
)
_SHIM_DISPLAY = "def display(*args, **kwargs):\n    return None\n"
_SHIM_GET_IPYTHON = "def get_ipython():\n    return _tavotto_quiet\n"


def _is_ipython_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == "IPython" or a.name.startswith("IPython.") for a in node.names)
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        return node.level == 0 and (mod == "IPython" or mod.startswith("IPython."))
    return False


def _scope_facts(body: str) -> tuple[set[str], set[str]]:
    """`(模块级绑定的名字, 被当作全局名读取的名字)`——用 CPython 自己的作用域分析（`symtable`），不手写遍历。

    绑定只算**模块级**：顶层赋值 / `def` / `class` / import / `for` / `with … as`，以及函数里先 `global` 再赋值的；
    函数、类、lambda、推导式里的参数与局部名不算。读取只算解析到全局的：函数里 `display` 是局部名或外层函数的名字时，读它不算用了全局的 `display`。
    """
    bound: set[str] = set()
    used: set[str] = set()

    def visit(table, top: bool) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if top:
                if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
                    bound.add(name)
                if sym.is_referenced():
                    used.add(name)
            else:
                if sym.is_declared_global() and sym.is_assigned():
                    bound.add(name)
                if sym.is_referenced() and sym.is_global():
                    used.add(name)
        for child in table.get_children():
            visit(child, False)

    visit(symtable.symtable(body, "<notebook>", "exec"), True)
    return bound, used


def _alias(a: ast.alias) -> str:
    return f"{a.name} as {a.asname}" if a.asname else a.name


def _bound_name(node: ast.AST, a: ast.alias) -> str:
    if isinstance(node, ast.ImportFrom):
        return a.asname or a.name
    return a.asname or a.name.split(".")[0]


def _is_ipython_alias(node: ast.AST, a: ast.alias) -> bool:
    if isinstance(node, ast.ImportFrom):
        return True  # 同一条 from 的模块相同：整条都是 IPython
    return a.name == "IPython" or a.name.startswith("IPython.")


def _import_fallback(node: ast.AST, aliases: list[ast.alias]) -> list[str]:
    """没装 IPython 时被导入的名字各给什么：`get_ipython` 是返回安静对象的函数，其余都是安静对象本身"""
    names = []
    for a in aliases:
        if a.name == "*":
            continue
        bound = _bound_name(node, a)
        value = "_tavotto_get_ipython" if bound == "get_ipython" else "_tavotto_quiet"
        names.append(f"{bound} = {value}")
    return names or ["pass"]


def _whole_lines(lines: list[str], node: ast.AST) -> tuple[int, int, str] | None:
    """语句独占的行区间 `(起, 止, 缩进)`；与别的语句同一行（`;`）就 None——不拆语句"""
    lo, hi = node.lineno - 1, node.end_lineno - 1  # type: ignore[attr-defined]
    indent = lines[lo][: node.col_offset]  # type: ignore[attr-defined]
    if indent.strip() or lines[hi][node.end_col_offset :].strip():  # type: ignore[attr-defined]
        return None
    return lo, hi, indent


def _apply_shim(body: str, tree: ast.Module) -> str:
    """抽出的正文再加工三件事（都只动生成的 `.py`）：

    1. **`from __future__ import …` 提到文件最前**：`__future__` 导入必须在文件最前，而 Jupyter 里每个单元各自编译、放在中间也合法，
       拼成一个文件就非法了。所有**顶层**的 `from __future__ import …` 合并去重（保持出现顺序）提到最前，原位置留一行注释；
       缩进里的（本来就非法）不动，由最终的 `compile` 核验拒绝。
    2. **IPython 的 import 兜底**：每条 `import IPython…` / `from IPython… import …` 包进 `try: … except ImportError:`，失败时被导入的名字给
       安静对象。`import math, IPython` 这类混合导入**拆开**：别的名字原样导入、失败照旧失败，只有 IPython 那几项进 try；
       一行里还有别的语句的不动。
    3. **`display` / `get_ipython` 的替身**：按作用域（`symtable`）判，笔记本在模块级读到而自己没绑定才补；补在 future 导入之后、正文之前。
    没有 future 导入、没有 IPython 的 import、也没读到这两个名字时，正文逐字不变。
    """
    lines = body.split("\n")
    bound, used = _scope_facts(body)
    need_display = "display" in used and "display" not in bound
    need_get = "get_ipython" in used and "get_ipython" not in bound
    # 编辑项：(起行, 止行, 替换行)；自下而上套用，行号不乱
    edits: list[tuple[int, int, list[str]]] = []
    futures: list[str] = []
    wrapped = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "__future__":
            span = _whole_lines(lines, node)
            if span is None or span[2]:
                continue
            # 去重按「特性 + 别名」：`annotations as ann` 与 `annotations` 是两个不同的绑定，别名丢了后面读 `ann` 就 NameError
            for a in node.names:
                if _alias(a) not in futures:
                    futures.append(_alias(a))
            edits.append(
                (
                    span[0],
                    span[1],
                    [
                        f"{_NOTE}已把 from __future__ import {', '.join(_alias(a) for a in node.names)} 提到文件最前"
                    ],
                )
            )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if not _is_ipython_import(node):
                continue
            mine = [a for a in node.names if _is_ipython_alias(node, a)]
            span = _whole_lines(lines, node)
            if span is None:
                continue
            lo, hi, indent = span
            rest = [a for a in node.names if a not in mine]
            if rest:  # 混合导入：别的名字原样导入，只有 IPython 那几项进 try
                head = [f"{indent}import {', '.join(_alias(a) for a in rest)}"]
                stmt = [f"{indent}import {', '.join(_alias(a) for a in mine)}"]
            else:
                head = []
                stmt = [ln[len(indent) :] if ln.strip() else ln for ln in lines[lo : hi + 1]]
                stmt = [f"{indent}{ln}" for ln in stmt]
            block = (
                head
                + [f"{indent}try:"]
                + [f"{indent}    {ln[len(indent) :]}" if ln.strip() else ln for ln in stmt]
            )
            block += [f"{indent}except ImportError:"] + [
                f"{indent}    {ln}" for ln in _import_fallback(node, mine)
            ]
            edits.append((lo, hi, block))
            wrapped = True
    for lo, hi, repl in sorted(edits, key=lambda e: -e[0]):
        lines[lo : hi + 1] = repl
    if not (futures or need_display or need_get or wrapped):
        return body
    head = f"from __future__ import {', '.join(futures)}\n" if futures else ""
    shim = ""
    if need_display or need_get or wrapped:
        shim = "# --- 笔记本里用到了 IPython 的名字：下面是不做事的替身，没装 IPython 也能跑（图由本软件自己捕获）---\n"
        shim += _SHIM_QUIET
        if wrapped:
            shim += "_tavotto_get_ipython = lambda: _tavotto_quiet\n"
        if need_display:
            shim += _SHIM_DISPLAY
        if need_get:
            shim += _SHIM_GET_IPYTHON
        shim += "\n"
    return head + shim + "\n".join(lines)


def render(notebook_name: str, extraction: Extraction) -> str:
    """文件头 + 正文。头三行：来源与「这是自动生成的副本」（中英各一句）、本功能自己的标记"""
    label = _label(notebook_name)
    digest = hashlib.sha256(extraction.body.encode("utf-8")).hexdigest()
    return (
        f"# 由 {PRODUCT_NAME} 从笔记本「{label}」自动生成的副本；改它不会改动笔记本。\n"
        f'# Generated by {PRODUCT_NAME} from the notebook "{label}"; editing this copy does not change the notebook.\n'
        f"# {DIST_NAME}-notebook: {label} sha256={digest}\n" + extraction.body
    )


def _split_header(text: str) -> tuple[str, str, str] | None:
    """文件 → `(标记里的笔记本名, 标记里的正文哈希, 正文)`；没有自己的标记就 None。只看前 5 行"""
    pos = 0
    for _ in range(5):
        end = text.find("\n", pos)
        line = text[pos:] if end < 0 else text[pos:end]
        found = _HEADER_MARK.match(line)
        if found:
            return found.group(1), found.group(2), "" if end < 0 else text[end + 1 :]
        if end < 0:
            return None
        pos = end + 1
    return None


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

#: 同一个项目里候选名的上限：`名.py`、`名 (notebook).py`、`名 (notebook 2).py` …
_MAX_CANDIDATES = 99


def script_stem(notebook_name: str) -> str:
    """笔记本文件名 → `.py` 的基名：去掉 `.ipynb`，其余过 `assetimport.clean_name`（非法字符 / Windows 保留名 / 过长）"""
    stem = notebook_name[: -len(SUFFIX)] if notebook_name.endswith(SUFFIX) else notebook_name
    return assetimport.clean_name(stem)[0]


def candidate_names(stem: str) -> list[str]:
    """按优先级排的 `.py` 文件名：先 `名.py`，被占了才 `名 (notebook).py`、`名 (notebook 2).py` …"""
    names = [f"{stem}.py", f"{stem} (notebook).py"]
    names += [f"{stem} (notebook {n}).py" for n in range(2, _MAX_CANDIDATES)]
    return names


def _listing(directory: Path) -> dict[str, str]:
    """目录里的文件名（不分大小写、NFC 归一的键 → 真实名字）：大小写不敏感的文件系统上 `A.py` 与 `a.py` 是同一个"""
    found: dict[str, str] = {}
    try:
        with os.scandir(directory) as it:
            for entry in it:
                found[unicodedata.normalize("NFC", entry.name).casefold()] = entry.name
    except OSError:
        pass
    return found


def _read_text(path: Path) -> str | None:
    """候选文件的文本；不是普通文件（目录 / 符号链接 / 读不动 / 不是 UTF-8）一律 None = 当作用户的、不碰"""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class Placed:
    """`status`：`created` 新写 · `updated` 更新了自己生成且没被改过的那份 · `reused` 内容相同、没写 ·
    `renamed` 名字被占、写到了别的名字"""

    path: Path
    status: str


def place(directory: Path, notebook_name: str, extraction: Extraction) -> Placed:
    """把脚本放到 `directory`（笔记本所在目录）。**调用方须持有 `assetimport.LOCK`**。

    逐个候选名（`candidate_names`）看：

    1. 不存在 → 新写这里（候选不是第一个就是 `renamed`）。
    2. 已存在、**正文与这次抽取相同**（不管头怎样）→ 复用，不写。
    3. 已存在、有本功能的标记、标记指的是**这个**笔记本、且**现有正文的哈希 == 标记里记的哈希**
       （= 自生成以来一个字没被改过）→ 笔记本更新了，原子替换它（这是「覆盖自己生成的、没被人碰过的文件」，
       可以重新生成出一模一样的，没有任何东西丢失）。
    4. 其余（用户自己的同名脚本、标记对不上 / 正文被改过、不是普通文件）→ **绝不碰**，看下一个候选名。
       判断不了有没有被用户改过，就当改过。

    原子写：先写同目录隐藏临时文件，再 `place_exclusive`（目标存在由内核拒绝，不覆盖）或 `publish_file`（替换）。
    """
    taken = _listing(directory)
    text = render(notebook_name, extraction)
    label = _label(notebook_name)
    for position, name in enumerate(candidate_names(script_stem(notebook_name))):
        key = unicodedata.normalize("NFC", name).casefold()
        existing = taken.get(key)
        dest = directory / (existing or name)
        if existing is None:
            _write_new(directory, dest, text)
            return Placed(dest, "created" if position == 0 else "renamed")
        current = _read_text(dest)
        if current is None:
            continue
        header = _split_header(current)
        body = header[2] if header else current
        if body == extraction.body:
            return Placed(dest, "reused")
        if (
            header is not None
            and header[0] == label
            and hashlib.sha256(header[2].encode("utf-8")).hexdigest() == header[1]
        ):
            tmp = assetimport.temp_path(directory, ".py")
            try:
                atomicio.write_bytes(tmp, text.encode("utf-8"))
                atomicio.publish_file(tmp, dest)
            finally:
                assetimport.discard(tmp)
            return Placed(dest, "updated")
    raise assetimport.ImportRefused("asset_import_name_exhausted", 409)


def _write_new(directory: Path, dest: Path, text: str) -> None:
    tmp = assetimport.temp_path(directory, ".py")
    try:
        atomicio.write_bytes(tmp, text.encode("utf-8"))
        try:
            assetimport.place_exclusive(tmp, dest)
        except FileExistsError:
            raise assetimport.ImportRefused("asset_import_name_exhausted", 409) from None
    finally:
        assetimport.discard(tmp)


def convert_file(notebook: Path) -> Placed:
    """磁盘上的笔记本 → 同目录的 `.py`（抽取 + 落盘；路径必须已由调用方钉在项目内）。原笔记本只读。"""
    try:
        size = notebook.stat().st_size
    except OSError:
        raise NotebookError("notebook_not_found", 404) from None
    if size > MAX_BYTES:
        raise NotebookError("notebook_too_large", 413, {"limit_mb": MAX_BYTES // (1024 * 1024)})
    try:
        with notebook.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        raise NotebookError("notebook_not_found", 404) from None
    extraction = extract(data)
    with assetimport.LOCK:
        return place(notebook.parent, notebook.name, extraction)


def convert_bytes(directory: Path, name: str, data: bytes) -> Placed:
    """上传的笔记本字节（浏览器拿不到路径时）→ 项目根里的 `.py`。笔记本本身不存进项目。"""
    extraction = extract(data)
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    with assetimport.LOCK:
        return place(directory, script_stem(base) + SUFFIX, extraction)


# ---------------------------------------------------------------------------
# 项目里已有的笔记本（给脚本库列出）
# ---------------------------------------------------------------------------


def _generated_copy(directory: Path, names: dict[str, str], notebook_name: str) -> Path | None:
    """同目录里由本功能从这个笔记本生成的 `.py`（只看候选名、只读文件头），没有就 None"""
    label = _label(notebook_name)
    for name in candidate_names(script_stem(notebook_name)):
        existing = names.get(unicodedata.normalize("NFC", name).casefold())
        if existing is None:
            continue
        path = directory / existing
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                head = stream.read(2048).decode("utf-8", errors="ignore")
        except OSError:
            continue
        header = _split_header(head)
        if header is not None and header[0] == label:
            return path
    return None


def inventory(root: Path) -> list[dict]:
    """项目内还**需要转换**的笔记本，按路径排序：`[{"notebook": <项目相对路径>}]`。

    只看文件名与文件头，不解析笔记本（几十 MB 的笔记本每次刷新都解析不起）；坏笔记本也列，
    转换时再如实说清。已经转过的**不重复列**：同目录里有一份由它生成的 `.py`、且 `.py` 不比笔记本旧——
    那份 `.py` 已经是脚本库里的一个普通脚本；笔记本之后又被改过（比 `.py` 新）就再列出来，点一下更新那份。
    """
    out: list[dict] = []
    listings: dict[Path, dict[str, str]] = {}
    for nb in discover.iter_notebooks(root):
        names = listings.get(nb.parent)
        if names is None:
            names = listings[nb.parent] = _listing(nb.parent)
        copy = _generated_copy(nb.parent, names, nb.name)
        if copy is not None:
            try:
                if copy.stat().st_mtime_ns >= nb.stat().st_mtime_ns:
                    continue
            except OSError:
                continue
        out.append({"notebook": discover.rel_key(nb, root)})
    return sorted(out, key=lambda item: item["notebook"])
