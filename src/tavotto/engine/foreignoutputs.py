"""R / MATLAB 脚本把图存到哪（ADR 0184 修订：R 和 MATLAB 与 Python 同样认作「图的来源」）。

Python 脚本的「这个文件是哪个脚本画的」有注册表（运行 / 静态起草）；R / MATLAB 不运行，所以这里**只读源码**，
找出保存调用里**写死的路径**：

* R：`ggsave` / `pdf` / `png` / `svg` / `jpeg` / `tiff` / `bmp` / `cairo_pdf` / `svglite` / `save_plot` 的文件参数，
  `dev.copy(pdf, "x.pdf")` / `dev.print(..., file = "x.pdf")`；路径可以是字面量，也可以是全字面量参数的
  `file.path(...)` / `here(...)` / `here::here(...)`；
* MATLAB：`saveas(h, 'x.png')`、`saveas(h, 'x', 'png')`、`exportgraphics(h, 'x.pdf')`、`print(h, '-dpng', 'x')`、
  命令式 `print -dpng x.png`；路径可以是全字面量参数的 `fullfile(...)`。

拼出来的、变量、`paste0` 一律不认——判不出就不判，宁可少认（那张图照旧按同目录归组），不猜错归属。
相对路径按脚本所在目录解析；落在项目外的、不是素材格式的丢掉。纯标准库、不执行任何东西，Flask 父进程 import。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from . import foreignsource, sourcelang

#: 认作素材的输出后缀（与 `project_refresh` 的素材扩展名同一组，去掉 EPS 这类不是素材的）
ASSET_SUFFIXES = frozenset({".pdf", ".svg", ".png", ".jpg", ".jpeg", ".tif", ".tiff"})

# R：函数名 → (文件参数的位置下标, 关键字名)
_R_SAVERS = {
    "ggsave": (0, ("filename",)),
    "pdf": (0, ("file",)),
    "cairo_pdf": (0, ("filename", "file")),
    "svg": (0, ("filename", "file")),
    "svglite": (0, ("filename", "file")),
    "png": (0, ("filename", "file")),
    "jpeg": (0, ("filename", "file")),
    "tiff": (0, ("filename", "file")),
    "bmp": (0, ("filename", "file")),
    "save_plot": (0, ("filename",)),
    # dev.copy(pdf, "x.pdf") 的第二个位置参数转给设备函数
    "dev.copy": (1, ("file", "filename")),
    "dev.print": (1, ("file", "filename")),
}
_R_JOINERS = frozenset({"file.path", "here"})

_M_DEVICE_EXT = {
    "png": ".png",
    "pdf": ".pdf",
    "svg": ".svg",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "tiff": ".tif",
    "tiffn": ".tif",
}
_M_JOINERS = frozenset({"fullfile"})


# ------------------------------- 词法 -------------------------------------

_IDENT = re.compile(r"[A-Za-z_.][A-Za-z0-9_.]*")


def _tokens(text: str, language: str) -> list[tuple[str, str]]:
    """源码 → `(kind, value)` 序列：`str` / `id` / `p`（单个标点）/ `nl`。注释与空白丢掉。"""
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    comment = "#" if language == sourcelang.R else "%"
    while i < n:
        c = text[i]
        if c == "\n":
            out.append(("nl", "\n"))
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == comment:
            while i < n and text[i] != "\n":
                i += 1
        elif c == '"' or (c == "'" and _quote_starts_string(out, language)):
            value, i = _read_string(text, i, language)
            out.append(("str", value))
        else:
            m = _IDENT.match(text, i)
            if m:
                out.append(("id", m.group()))
                i = m.end()
            else:
                out.append(("p", c))
                i += 1
    return out


def _quote_starts_string(prev: list[tuple[str, str]], language: str) -> bool:
    """MATLAB 的 `'` 紧跟在值后面是转置，不是字符串；R 里永远是字符串。"""
    if language == sourcelang.R or not prev:
        return True
    kind, value = prev[-1]
    return not (kind in ("id", "str") or value in (")", "]", "}", "'"))


def _read_string(text: str, i: int, language: str) -> tuple[str, int]:
    quote = text[i]
    i += 1
    buf: list[str] = []
    n = len(text)
    while i < n and text[i] != "\n":
        c = text[i]
        if language == sourcelang.R and c == "\\" and i + 1 < n:
            buf.append(text[i + 1])
            i += 2
            continue
        if c == quote:
            # MATLAB：两个引号连写是转义
            if language != sourcelang.R and i + 1 < n and text[i + 1] == quote:
                buf.append(quote)
                i += 2
                continue
            return "".join(buf), i + 1
        buf.append(c)
        i += 1
    return "".join(buf), i  # 没闭合：按读到的算（后面只当作非字面量处理也无妨）


# ------------------------------- 调用 -------------------------------------


def _calls(tokens: list[tuple[str, str]]):
    """找出 `名字(参数…)`：产出 `(名字, [参数 token 序列…])`。`ns::name` 取 `name`。"""
    i = 0
    n = len(tokens)
    while i < n:
        kind, value = tokens[i]
        if kind == "id" and i + 1 < n and tokens[i + 1] == ("p", "("):
            args, _end = _split_args(tokens, i + 2)
            yield value, args
        # 不跳过参数：参数里嵌套的调用（`print(...)` 里的 `fullfile(...)`）照样逐个看到
        i += 1


def _split_args(tokens: list[tuple[str, str]], start: int) -> tuple[list[list], int]:
    """从 `(` 之后开始，按顶层逗号切参数，回 `(参数列表, 右括号之后的下标)`。"""
    args: list[list] = [[]]
    depth = 0
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok[0] == "p" and tok[1] in "([{":
            depth += 1
        elif tok[0] == "p" and tok[1] in ")]}":
            if depth == 0:
                return ([] if args == [[]] else args), i + 1
            depth -= 1
        elif tok == ("p", ",") and depth == 0:
            args.append([])
            i += 1
            continue
        if tok[0] != "nl":
            args[-1].append(tok)
        i += 1
    return ([] if args == [[]] else args), i


def _strip_ns(tokens: list) -> list:
    """`here :: here ( … )` → `here ( … )`。"""
    if (
        len(tokens) >= 4
        and tokens[0][0] == "id"
        and tokens[1] == ("p", ":")
        and tokens[2] == ("p", ":")
    ):
        return tokens[3:]
    return tokens


def _literal(arg: list, joiners: frozenset[str]) -> str | None:
    """一个参数 → 字面量路径；`file.path("a", "b.pdf")` 这类全字面量拼接也认。其余 None。"""
    arg = _strip_ns(arg)
    if len(arg) == 1 and arg[0][0] == "str":
        return arg[0][1]
    if len(arg) >= 3 and arg[0][0] == "id" and arg[0][1] in joiners and arg[1] == ("p", "("):
        parts, end = _split_args(arg, 2)
        if end != len(arg) or not parts:
            return None
        values = []
        for part in parts:
            if len(part) != 1 or part[0][0] != "str":
                return None
            values.append(part[0][1])
        return _join(values)
    return None


def _join(values: list[str]) -> str:
    """`file.path` / `fullfile` 的拼接：只去掉段与段之间的分隔符，**保留第一段的根**（`/tmp`、`C:\\`、`\\\\srv`），
    否则绝对路径会变成相对路径、被按脚本目录误解析（Codex #1906）。"""
    parts = [v for v in values if v]
    if not parts:
        return ""
    head = parts[0]
    stripped = head.rstrip("/\\")
    head = stripped if stripped and not re.fullmatch(r"[A-Za-z]:", stripped) else head
    rest = [v.strip("/\\") for v in parts[1:]]
    if not rest:
        return head
    sep = "" if head.endswith(("/", "\\")) else "/"
    return head + sep + "/".join(r for r in rest if r)


def _keyword(arg: list) -> tuple[str | None, list]:
    """`name = value` → (name, value tokens)；不是关键字参数 → (None, arg)。`==` 不算。"""
    if (
        len(arg) >= 2
        and arg[0][0] == "id"
        and arg[1] == ("p", "=")
        and (len(arg) < 3 or arg[2] != ("p", "="))
    ):
        return arg[0][1], arg[2:]
    return None, arg


def _r_outputs(tokens) -> list[str]:
    out: list[str] = []
    for name, args in _calls(tokens):
        spec = _R_SAVERS.get(name)
        if spec is None:
            continue
        index, keywords = spec
        positional: list[list] = []
        found = None
        for arg in args:
            key, value = _keyword(arg)
            if key is None:
                positional.append(arg)
            elif key in keywords:
                found = _literal(value, _R_JOINERS)
        if found is None and len(positional) > index:
            found = _literal(positional[index], _R_JOINERS)
        if found and name == "ggsave":
            found = _ggsave_dir(found, args, positional)
        if found:
            out.append(found)
    return out


def _ggsave_dir(filename: str, args: list[list], positional: list[list]) -> str | None:
    """`ggsave(filename, plot, device, path, ...)`：给了 `path`（关键字或第 4 个位置参数）就拼在前面（Codex #1906）。
    `path` 不是字面量 → 判不出真实位置，整条不认（None），免得把项目根下同名的另一个文件认成它的输出。"""
    path_arg = None
    for arg in args:
        key, value = _keyword(arg)
        if key == "path":
            path_arg = value
    if path_arg is None and len(positional) > 3:
        path_arg = positional[3]
    if path_arg is None:
        return filename
    base = _literal(path_arg, _R_JOINERS)
    if not base:
        return None
    return _join([base, filename])


def _m_outputs(tokens, text: str) -> list[str]:
    out: list[str] = []
    for name, args in _calls(tokens):
        if name == "saveas" and len(args) >= 2:
            path = _literal(args[1], _M_JOINERS)
            if path and not PurePosixPath(path).suffix and len(args) >= 3:
                fmt = _literal(args[2], _M_JOINERS)
                path = path + _M_DEVICE_EXT.get((fmt or "").lower(), "") if fmt else path
            if path:
                out.append(path)
        elif name == "exportgraphics" and len(args) >= 2:
            path = _literal(args[1], _M_JOINERS)
            if path:
                out.append(path)
        elif name == "print":
            values = [_literal(a, _M_JOINERS) for a in args]
            out += _m_print([v for v in values if v is not None])
    # 命令式：`print -dpng out.png`（不带括号）
    for line in text.splitlines():
        code = line.split("%", 1)[0].strip()
        m = re.match(r"print\s+(-\S.*)$", code)
        if m:
            out += _m_print(m.group(1).rstrip(";").split())
    return out


def _m_print(words: list[str]) -> list[str]:
    device = next((w[2:] for w in words if w.startswith("-d")), None)
    paths = [w for w in words if not w.startswith("-")]
    if not paths:
        return []
    path = paths[0]
    if not PurePosixPath(path).suffix and device:
        path += _M_DEVICE_EXT.get(device.lower(), "")
    return [path]


def outputs_of(text: str, language: str) -> list[str]:
    """一份 R / MATLAB 源码里写死的图输出路径（按出现顺序、去重，原样的相对 / 绝对写法）。"""
    tokens = _tokens(text, language)
    raw = _r_outputs(tokens) if language == sourcelang.R else _m_outputs(tokens, text)
    seen: list[str] = []
    for p in raw:
        p = p.strip()
        if (
            p
            and p not in seen
            and PurePosixPath(p.replace("\\", "/")).suffix.lower() in ASSET_SUFFIXES
        ):
            seen.append(p)
    return seen


def _project_rel(root: Path, script_rel: str, written: str) -> str | None:
    """脚本里写的路径 → 项目相对路径（`/` 分隔）；相对路径按脚本所在目录解析；出了项目 / `~` 开头 → None。"""
    w = written.replace("\\", "/")
    if w.startswith("~"):
        return None
    if PurePosixPath(w).is_absolute() or re.match(r"^[A-Za-z]:/", w):
        try:
            return Path(w).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
    base = PurePosixPath(script_rel).parent
    parts: list[str] = [] if str(base) == "." else list(base.parts)
    for seg in PurePosixPath(w).parts:
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts) if parts else None


def output_map(root: Path) -> dict[str, str]:
    """项目里 R / MATLAB 脚本写死的输出 → 写它的脚本（项目相对路径，`/` 分隔）。

    同一个输出被两个脚本都声称写过 → 不归给任何一个（不猜）。读不了的脚本跳过。
    """
    root = Path(root)
    claims: dict[str, set[str]] = {}
    for item in foreignsource.inventory(root):
        script = item["script"]
        try:
            data = (root / script).read_bytes()[: sourcelang.MAX_BYTES]
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for written in outputs_of(text, item["language"]):
            rel = _project_rel(root, script, written)
            if rel is not None:
                claims.setdefault(rel, set()).add(script)
    return {rel: next(iter(s)) for rel, s in claims.items() if len(s) == 1}


__all__ = ["ASSET_SUFFIXES", "output_map", "outputs_of"]
