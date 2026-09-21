"""U00 facade 迁移清单（`docs/implementation/tavotto-foundation/U00_FACADE_LEDGER.json`）的门禁。

判据的主语：**`tavotto.pdfbackend.__all__` 与清单里的 exports 名字集合**——两边必须
逐项相等（漏一项红、多一项红、重复红）。加上「清单里点名的每个调用点 / 用例仍含那个
名字」——调用点按 **符号** 锚定（`symbol` = 所在函数 / 方法的 AST 限定名，模块级
`<module>`），不按绝对行号：行号量的是「第 N 行」不是「这个调用点」，别的 PR 在文件
前面插几行它就红（2026-09-21 一天让三个 PR 红过），而真正该红的「调用被删了 / 挪进了
别的函数」它反而看不见。`line` 只作信息，重生成时照填。派生的 Markdown 必须与 JSON
一致（一个真值、一个派生）。

这不是产品测试：它守的是 U08 / U10 迁移用的清单不腐烂。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC_ROOT = REPO / "docs" / "implementation" / "tavotto-foundation"
LEDGER = DOC_ROOT / "U00_FACADE_LEDGER.json"
CANVAS_METHODS = {
    "place",
    "save_pdf",
    "save_png",
    "save_tiff",
    "size_pt",
    "close",
    "__enter__/__exit__",
}


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _load_module(name: str, path: Path):
    """按路径装载一个夹具 / 工具模块，**不往它旁边写 `__pycache__`**——夹具目录的
    文件快照是隔离用例的判据，测试自己往里写字节码会把判据弄成随执行顺序漂的东西。"""
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def _facade_all() -> list[str]:
    from tavotto import pdfbackend

    return list(pdfbackend.__all__)


def test_ledger_names_are_unique():
    names = [e["name"] for e in _ledger()["exports"]]
    assert len(names) == len(set(names)), "清单里有重复的导出项"


def test_every_facade_export_is_in_the_ledger_and_nothing_else():
    """漏一项必红：`__all__` 加了新名字而清单没登记 → 这里先红。"""
    facade = set(_facade_all())
    ledger = {e["name"] for e in _ledger()["exports"]}
    assert facade - ledger == set(), f"facade 导出了但清单没登记: {sorted(facade - ledger)}"
    assert ledger - facade == set(), f"清单里有 facade 没导出的名字: {sorted(ledger - facade)}"
    assert len(facade) >= 19, "facade 的 __all__ 少于 19 项，判据多半量在空集合上"


def test_every_ledger_entry_has_a_category_and_a_migration_criterion():
    d = _ledger()
    categories = set(d["categories"])
    for e in d["exports"]:
        assert e["category"] in categories, e["name"]
        assert e["migration_criterion"].strip(), e["name"]
        assert e["kind"] in {"function", "constant"}, e["name"]
        for t in e["tests"]:
            assert t["class"] in d["test_classes"], (e["name"], t)


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _span(node: ast.AST) -> tuple[int, int]:
    """定义的行范围，**含装饰器**（`@pytest.mark.parametrize(..., pdfbackend.X)` 也是引用）。"""
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return start, node.end_lineno


def _symbol_source(path: str, symbol: str) -> str:
    """`symbol`（AST 限定名：`f` / `Class.method` / `outer.inner`，模块级 `<module>`）在
    `path` 里的源码；找不到这个符号就 fail——清单指着的东西已经不在了。"""
    src = (REPO / path).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    if symbol == "<module>":
        tops = [n for n in tree.body if not isinstance(n, (*_DEFS, ast.ClassDef))]
        return "\n".join("\n".join(lines[_span(n)[0] - 1 : n.end_lineno]) for n in tops)
    defs: dict[str, ast.AST] = {}

    def walk(body, prefix):
        for node in body:
            if isinstance(node, (*_DEFS, ast.ClassDef)):
                defs[f"{prefix}{node.name}"] = node
                walk(node.body, f"{prefix}{node.name}.")

    walk(tree.body, "")
    node = defs.get(symbol)
    assert node is not None and isinstance(node, _DEFS), (
        f"{path} 里没有函数 / 方法 `{symbol}`——清单指着的符号已经不在了"
    )
    a, b = _span(node)
    return "\n".join(lines[a - 1 : b])


def test_every_cited_caller_symbol_still_mentions_the_export():
    """按符号锚定：清单点名的函数 / 方法体里仍有那个导出名。行号不参与判定。"""
    d = _ledger()
    for e in d["exports"]:
        for c in e["callers"]:
            assert e["name"] in _symbol_source(c["file"], c["symbol"]), (
                f"{c['file']} 的 `{c['symbol']}` 已不含 `{e['name']}`——调用被删了或挪进了别的函数，清单要跟着改"
            )
    for m in d["canvas_methods"]["methods"]:
        assert m["name"] in CANVAS_METHODS, m["name"]
        # 方法 / 属性：`.name`；`__enter__/__exit__`：那个函数体里有 `with` 语句
        needle = "with " if "/" in m["name"] else "." + m["name"]
        for c in m["callers"]:
            assert needle in _symbol_source(c["file"], c["symbol"]), (
                f"{c['file']} 的 `{c['symbol']}` 已不含 `{m['name']}`"
            )
    assert {m["name"] for m in d["canvas_methods"]["methods"]} == CANVAS_METHODS


def test_every_cited_test_symbol_still_contains_its_snippet():
    """用例引用同样按用例函数名锚定：那个用例（含它的装饰器）里仍有登记的片段。"""
    for e in _ledger()["exports"]:
        for t in e["tests"]:
            assert t["contains"] in _symbol_source(t["file"], t["symbol"]), (
                f"{t['file']} 的 `{t['symbol']}` 已不含 `{t['contains']}`（{e['name']}）"
            )


def test_cited_lines_are_information_only_but_inside_the_symbol():
    """`line` 不参与判定，但作为信息它得指在那个符号的范围里（重生成脚本照填）；
    否则读清单的人会被带到一行不相干的代码。"""
    d = _ledger()
    cites = [(c["file"], c["symbol"], c["line"]) for e in d["exports"] for c in e["callers"]]
    cites += [
        (c["file"], c["symbol"], c["line"])
        for m in d["canvas_methods"]["methods"]
        for c in m["callers"]
    ]
    cites += [(t["file"], t["symbol"], t["line"]) for e in d["exports"] for t in e["tests"]]
    assert len(cites) >= 80, "引用少于 80 条，判据多半量在空集合上"
    for path, symbol, line in cites:
        src = (REPO / path).read_text(encoding="utf-8")
        tree = ast.parse(src)
        if symbol == "<module>":
            assert 1 <= line <= len(src.splitlines()), (path, symbol, line)
            continue
        spans = []

        def walk(body, prefix):
            for node in body:
                if isinstance(node, (*_DEFS, ast.ClassDef)):
                    if f"{prefix}{node.name}" == symbol:
                        spans.append(_span(node))
                    walk(node.body, f"{prefix}{node.name}.")

        walk(tree.body, "")
        assert spans and spans[0][0] <= line <= spans[0][1], (
            f"{path}:{line} 不在 `{symbol}` 的范围 {spans} 里——重生成清单把行号填回去"
        )


def test_canvas_methods_exist_on_the_real_canvas_object():
    from tavotto import pdfbackend

    canvas = pdfbackend.compose(10, 10)
    try:
        for m in _ledger()["canvas_methods"]["methods"]:
            for name in m["name"].split("/"):
                assert hasattr(canvas, name), name
    finally:
        canvas.close()


def test_markdown_view_is_derived_from_the_json():
    gen = _load_module(
        "u00_generate_facade_ledger", DOC_ROOT / "tools" / "generate_facade_ledger.py"
    )
    expected = gen.render(_ledger())
    actual = (DOC_ROOT / "U00_FACADE_LEDGER.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "U00_FACADE_LEDGER.md 不是当前 JSON 的派生物：跑 tools/generate_facade_ledger.py"
    )


@pytest.mark.parametrize(
    "path", ["src/tavotto/pdfbackend/__init__.py", "src/tavotto/pdfbackend/pymupdf_backend.py"]
)
def test_ledger_points_at_files_that_exist(path):
    d = _ledger()
    assert (REPO / d["facade_module"]).is_file()
    assert (REPO / d["implementation_module"]).is_file()
    assert (REPO / path).is_file()
