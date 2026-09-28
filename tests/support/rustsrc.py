"""从桌面壳的 Rust 源码里读出**结构**：`#[tauri::command]`、`build.rs` 的命令清单、`generate_handler!` 的条目。

与 `tsconst.py` 同一个理由：子串判据会被注释与无关的字符串字面量满足。`'"arm_close_guard"' in build_rs`
在那一条登记被注释掉、名字还留在注释里时照样是绿的，而 Tauri 的 ACL 已经把前端的 invoke 静默拒了
（Codex #696 P1）。这里的做法是**先把注释（和需要时连同字符串字面量）换成等长空白，再在剩下的代码上
按括号配对定位结构**——偏移量与原文一一对应。

认得的词法：`//`、可嵌套的 `/* */`、`"…"` / `b"…"`（含转义）、`r#"…"#` 一类原始串、`'x'` / `'\\n'`
字符字面量；生命周期 `'a` 不是字面量，原样保留。**已知边界**：不是完整的 Rust 解析器——宏展开、`#[cfg]`
分支、从别的模块 `use` 进来的命令都不认；读不出确切结构时一律当场报错，不猜。

另一半是**建造链**：按括号层数读 `WebviewWindowBuilder::new(…)` 那条链调了哪些方法、按花括号判它是否
必然执行。使用者：`test_desktop_file_drop.py`（每个窗口都关掉 Tauri 的拖放处理器）、
`test_desktop_remote.py`（远程实例窗口的建造链，ADR 0105）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "src-tauri"

_RAW_STR = re.compile(r'b?r(#*)"')
_CHAR_LIT = re.compile(r"b?'(?:\\.[^']*|[^'\\])'")
_IDENT = re.compile(r"[A-Za-z_]\w*")


def rust_code(src: str, *, keep_strings: bool = False) -> str:
    """把注释（`keep_strings=False` 时连同字符串与字符字面量）换成等长空白（换行保留）。

    `keep_strings=True` 用在要读字符串**内容**的地方（`build.rs` 的命令清单）：字面量仍被
    词法认出来——字符串里的 `//` 不会被当成注释——只是不抹。"""
    out = list(src)

    def blank(a: int, b: int, literal: bool = False) -> None:
        if literal and keep_strings:
            return
        out[a:b] = [c if c == "\n" else " " for c in src[a:b]]

    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif (m := _RAW_STR.match(src, i)) and not (
            i and (src[i - 1].isalnum() or src[i - 1] == "_")
        ):
            end = src.find('"' + m.group(1), m.end())
            j = n if end < 0 else end + 1 + len(m.group(1))
            blank(i, j, literal=True)
            i = j
        elif src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            blank(i, j + 1, literal=True)
            i = j + 1
        elif m := _CHAR_LIT.match(src, i):
            blank(i, m.end(), literal=True)
            i = m.end()
        else:
            i += 1
    return "".join(out)


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _bracket_body(code: str, open_at: int) -> str:
    """`code[open_at]` 是开括号：返回与它配对的闭括号之间的内容（代码已抹掉注释与字面量）。"""
    stack = [_PAIRS[code[open_at]]]
    for j in range(open_at + 1, len(code)):
        c = code[j]
        if c in _PAIRS:
            stack.append(_PAIRS[c])
        elif c in ")]}":
            if c != stack.pop():
                raise AssertionError(f"括号不配对（偏移 {j}）")
            if not stack:
                return code[open_at + 1 : j]
    raise AssertionError("括号没有闭合")


def _only(pattern: str, code: str, what: str) -> re.Match[str]:
    hits = list(re.finditer(pattern, code))
    assert len(hits) == 1, f"{what} 应当恰好一处，实际 {len(hits)} 处"
    return hits[0]


def tauri_commands(main_rs: str | None = None) -> list[str]:
    """`main.rs` 里真正声明的 `#[tauri::command]` 函数名（注释里的不算）。"""
    code = rust_code(main_rs if main_rs is not None else _read("src", "main.rs"))
    names = re.findall(
        r"#\s*\[\s*tauri\s*::\s*command\s*\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", code
    )
    assert names, "一个 #[tauri::command] 都没找到——文件结构变了，这道门禁已经空了"
    return names


def manifest_commands(build_rs: str | None = None) -> list[str]:
    """`build.rs` 里 `AppManifest::new().commands(&[...])` 数组的条目（按顺序）。

    数组里只许有字符串字面量与逗号：注释掉的条目不算，变量、宏、展开读不出确切取值，当场报错。"""
    src = build_rs if build_rs is not None else _read("build.rs")
    code = rust_code(src)  # 结构在抹干净的代码上找
    m = _only(r"\.\s*commands\s*\(\s*&\s*\[", code, "build.rs 的 AppManifest::commands(&[...])")
    open_at = m.end() - 1
    body = _bracket_body(code, open_at)
    lo, hi = open_at + 1, open_at + 1 + len(body)
    assert not body.replace(",", "").strip(), f"命令清单里有字符串以外的东西：{body.strip()!r}"
    # 同一段在「只抹注释」的版本里读字符串内容；偏移一一对应
    kept = rust_code(src, keep_strings=True)[lo:hi]
    names = re.findall(r'"([^"\\]*)"', kept)
    rest = re.sub(r'"[^"\\]*"', "", kept).replace(",", "").strip()
    assert not rest, f"命令清单里有读不懂的条目：{rest!r}"
    return names


def handler_commands(main_rs: str | None = None) -> list[str]:
    """`generate_handler![...]` 的条目（按顺序）；`module::cmd` 取最后一段。"""
    code = rust_code(main_rs if main_rs is not None else _read("src", "main.rs"))
    m = _only(r"\bgenerate_handler\s*!\s*\[", code, "generate_handler![...]")
    body = _bracket_body(code, m.end() - 1)
    names = []
    for entry in (e.strip() for e in body.split(",")):
        if not entry:
            continue
        segs = [s.strip() for s in entry.split("::")]
        assert all(_IDENT.fullmatch(s) for s in segs), (
            f"generate_handler 里有读不懂的条目：{entry!r}"
        )
        names.append(segs[-1])
    return names


def capability_permissions() -> list[str]:
    """主窗口（`capabilities/main.json`）放行的权限。"""
    return json.loads(_read("capabilities", "main.json"))["permissions"]


def granted_permissions() -> set[str]:
    """`capabilities/` 里**任一个**窗口放行的权限之并（主窗口 + 远程实例窗口，ADR 0105）。

    只回答「有没有一处放行」；哪个窗口该拿哪条由 `test_desktop_remote.py` 按窗口钉成闭集。"""
    allowed: set[str] = set()
    for cap in sorted((TAURI / "capabilities").glob("*.json")):
        allowed |= set(json.loads(cap.read_text(encoding="utf-8"))["permissions"])
    return allowed


def allow_permission(command: str) -> str:
    return "allow-" + command.replace("_", "-")


def _read(*parts: str) -> str:
    return TAURI.joinpath(*parts).read_text(encoding="utf-8")


_OPEN, _CLOSE = "([{", ")]}"
_METHOD = re.compile(r"\.\s*(\w+)\s*[(<]")


def chain_methods(code: str, start: int) -> list[str]:
    """从 `start` 起的那条表达式在第 0 层依次调用的方法名，到第 0 层的 `;` 为止。"""
    depth, i, methods = 0, start, []
    while i < len(code):
        c = code[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ";" and depth == 0:
            return methods
        elif c == "." and depth == 0 and (m := _METHOD.match(code, i)):
            methods.append(m.group(1))
        i += 1
    raise AssertionError("建造链没有以 `;` 结束")


def enclosing_headers(code: str, pos: int) -> list[str]:
    """包住 `pos` 的每一层 `{` 的「头」（上一个 `;` / `{` / `}` 到这个 `{` 之间的代码），由外到内。"""
    stack: list[int] = []
    for i, c in enumerate(code[:pos]):
        if c == "{":
            stack.append(i)
        elif c == "}":
            stack.pop()
    heads = []
    for b in stack:
        k = max(code.rfind(ch, 0, b) for ch in ";{}")
        heads.append(" ".join(code[k + 1 : b].split()))
    return heads


_BUILDER = re.compile(r"\bWebviewWindowBuilder\s*::\s*new\s*\(\s*\w+\s*,\s*(\w+)\s*,")


def window_builders(code: str) -> dict[str, int]:
    """壳里每一处 `WebviewWindowBuilder::new(app, <LABEL 常量>, …)`：常量名 → 位置。

    label 必须写成常量（`MAIN_WINDOW` / `REMOTE_WINDOW`）：字面量在 `rust_code` 里已被
    换成空白，写成字面量的建窗口点在这里对不上号——那正是要它红的时候。同一个常量
    出现两次也红（两处建同一个窗口，谁生效取决于调用顺序）。"""
    sites: dict[str, int] = {}
    total = 0
    for m in re.finditer(r"\bWebviewWindowBuilder\s*::\s*new\s*\(", code):
        total += 1
        named = _BUILDER.match(code, m.start())
        assert named, f"建窗口的 label 不是常量：{code[m.start() : m.start() + 80]!r}"
        assert named.group(1) not in sites, f"{named.group(1)} 被建了不止一处"
        sites[named.group(1)] = m.start()
    assert total == len(sites)
    return sites


def assert_unconditionally_in(code: str, pos: int, outer_fn: str) -> None:
    """`pos` 处的语句在 `outer_fn` 这个函数里**必然执行**：它所在的每一层块都不是条件 /
    循环 / `#[cfg]` 块，最外层就是那个函数。"""
    heads = enclosing_headers(code, pos)
    assert heads and re.match(rf"fn {outer_fn}\s*[(<]", heads[0]), heads[:1]
    stmt = code[max(code.rfind(ch, 0, pos) for ch in ";{}") + 1 : pos]
    for h in [*heads[1:], stmt]:
        assert not re.search(r"\b(if|else|while|for|loop)\b|=>|#\s*\[\s*cfg", h), (
            f"那条语句落在条件 / 循环 / cfg 块里，未必执行：{h!r}"
        )
    # 最外层的函数头只查 cfg：签名里的 `->` 不是分支
    assert not re.search(r"#\s*\[\s*cfg", heads[0]), heads[0]
