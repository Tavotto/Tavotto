"""主页拖放拿真实路径（ADR 0092）：壳与前端两侧同源 + 命令 ACL 三处齐全。

与 `test_desktop_close_guard.py` 同一个理由站在两侧之外：事件名、载荷的 kind 词汇表
壳写一份、前端写一份，漂了之后 `cargo test` 与 vitest 各自都是绿的，只在真机上
表现为「拖进来什么都不发生」。`native_file_drop` 命令漏登记也是静默失败
（invoke 被拒 → 前端读成「不支持」→ 永远走降级）。

`src-tauri/` 不进 wheel/sdist，因此整个文件在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
MAIN_RS = TAURI / "src" / "main.rs"
DROP_RS = TAURI / "src" / "drop_paths.rs"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"

pytestmark = pytest.mark.skipif(
    not MAIN_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)


def _rust_event() -> str:
    m = re.search(r'pub const EVENT: &str = "([^"]+)";', DROP_RS.read_text(encoding="utf-8"))
    assert m, "drop_paths.rs 里找不到 EVENT 常量"
    return m.group(1)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _rust_kinds() -> set[str]:
    src = DROP_RS.read_text(encoding="utf-8")
    body = src.split("pub enum DropTarget {", 1)[1].split("\n}\n", 1)[0]
    # 变体名：行首缩进 4 格、大写开头、后面跟 `{`（带字段的变体）
    return {_snake(v) for v in re.findall(r"^    ([A-Z][A-Za-z]+) \{", body, re.M)}


def _ts_kinds() -> set[str]:
    src = DESKTOP_TS.read_text(encoding="utf-8")
    body = src.split("export type NativeFileDrop =", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"kind: '([a-z_]+)'", body))


def test_the_event_name_is_one_string_on_both_sides():
    event = _rust_event()
    assert event == "tavotto:file-drop"
    assert f"'{event}'" in DESKTOP_TS.read_text(encoding="utf-8")


def test_the_payload_kinds_are_the_same_closed_set_on_both_sides():
    rust, ts = _rust_kinds(), _ts_kinds()
    assert rust == {"script", "folder", "unsupported"}, rust
    assert ts == rust


def test_native_file_drop_is_declared_in_all_three_places():
    build_rs = (TAURI / "build.rs").read_text(encoding="utf-8")
    cap = json.loads((TAURI / "capabilities" / "main.json").read_text(encoding="utf-8"))
    # 剥掉注释与字面量再切：注释里写着命令名不算登记（与下面拖放处理器那条同一个理由）
    handler = _rust_code(MAIN_RS.read_text(encoding="utf-8")).split("generate_handler![")[1]
    handler = handler.split("]")[0]
    assert '"native_file_drop"' in build_rs, "build.rs 的 AppManifest::commands 里没有它"
    assert "allow-native-file-drop" in cap["permissions"], "capability 没放行它"
    assert "native_file_drop" in re.findall(r"\w+", handler), "generate_handler 里没有它"
    assert "'native_file_drop'" in DESKTOP_TS.read_text(encoding="utf-8")


_RAW_STR = re.compile(r'b?r(#*)"')
_CHAR_LIT = re.compile(r"b?'(?:\\.[^']*|[^'\\])'")


def _rust_code(src: str) -> str:
    """把注释、字符串与字符字面量换成等长空白（换行保留），只剩代码——位置与原文一一对应。

    判「调用在不在」时，注释里、字符串里的同名 token 不能算数（Codex #665 P1）。
    认：`//`、可嵌套的 `/* */`、`"…"` / `b"…"`（含转义）、`r#"…"#` 一类原始串、`'x'` / `'\\n'`
    字符字面量；生命周期 `'a` 不是字面量，原样保留。"""
    out = list(src)

    def blank(a: int, b: int) -> None:
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
            blank(i, j)
            i = j
        elif src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            blank(i, j + 1)
            i = j + 1
        elif m := _CHAR_LIT.match(src, i):
            blank(i, m.end())
            i = m.end()
        else:
            i += 1
    return "".join(out)


_OPEN, _CLOSE = "([{", ")]}"
_METHOD = re.compile(r"\.\s*(\w+)\s*[(<]")


def _chain_methods(code: str, start: int) -> list[str]:
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
    raise AssertionError("主窗口的建造链没有以 `;` 结束")


def _enclosing_headers(code: str, pos: int) -> list[str]:
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


def test_the_tauri_drag_drop_handler_stays_disabled():
    """真实路径靠旁听拿，不是把 Tauri 的拖放处理器打开——打开它页面里的 HTML5 拖放整片失效。

    判据的主语是**真正建主窗口的那条建造链**，不是 main.rs 里某个子串（Codex #665 P1：
    写进注释、字符串、`if false` 或一个没人调的函数里，子串照样在）。所以：剥掉注释与
    字面量后，`WebviewWindowBuilder::new(` 全壳只有一处；它这条链在第 0 层调了
    `disable_drag_drop_handler()`、且在 `.build()` 之前；它所在的每一层块都不是条件 /
    循环 / `#[cfg]` 块，最外层是 `fn main`（进程入口，必然执行）。"""
    code = _rust_code(MAIN_RS.read_text(encoding="utf-8"))
    sites = [m.start() for m in re.finditer(r"\bWebviewWindowBuilder\s*::\s*new\s*\(", code)]
    assert len(sites) == 1, f"建窗口的地方应当只有一处，实际 {len(sites)} 处"
    methods = _chain_methods(code, sites[0])
    assert "disable_drag_drop_handler" in methods, f"主窗口的建造链没关 Tauri 拖放处理器：{methods}"
    assert "build" in methods, f"建造链没有 build()：{methods}"
    assert methods.index("disable_drag_drop_handler") < methods.index("build"), methods

    heads = _enclosing_headers(code, sites[0])
    assert heads and re.fullmatch(r"fn main\s*\(\s*\)", heads[0]), heads[:1]
    stmt = code[max(code.rfind(ch, 0, sites[0]) for ch in ";{}") + 1 : sites[0]]
    for h in [*heads, stmt]:
        assert not re.search(r"\b(if|else|while|for|loop)\b|=>|#\s*\[\s*cfg", h), (
            f"建主窗口的那条链落在条件 / 循环 / cfg 块里，未必执行：{h!r}"
        )
