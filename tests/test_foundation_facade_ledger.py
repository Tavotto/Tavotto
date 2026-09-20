"""U00 facade 迁移清单（`docs/implementation/tavotto-foundation/U00_FACADE_LEDGER.json`）的门禁。

判据的主语：**`tavotto.pdfbackend.__all__` 与清单里的 exports 名字集合**——两边必须
逐项相等（漏一项红、多一项红、重复红）。加上「清单里点名的每个 file:line 仍含那个
名字」——清单是按 SHA 采样的，代码挪了行清单就该跟着改，而不是继续指向一行不相干
的代码。派生的 Markdown 必须与 JSON 一致（一个真值、一个派生）。

这不是产品测试：它守的是 U08 / U10 迁移用的清单不腐烂。
"""

from __future__ import annotations

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


def _line(path: str, line: int) -> str:
    text = (REPO / path).read_text(encoding="utf-8").splitlines()
    assert 1 <= line <= len(text), f"{path}:{line} 超出文件长度 {len(text)}"
    return text[line - 1]


def test_every_cited_caller_line_still_mentions_the_export():
    d = _ledger()
    for e in d["exports"]:
        for c in e["callers"]:
            assert e["name"] in _line(c["file"], c["line"]), (
                f"{c['file']}:{c['line']} 已不含 `{e['name']}`——代码挪了行，清单要跟着改"
            )
    for m in d["canvas_methods"]["methods"]:
        assert m["name"] in CANVAS_METHODS, m["name"]
        for c in m["callers"]:
            needle = m["name"].split("/")[0].strip("_") if "/" in m["name"] else m["name"]
            assert needle in _line(c["file"], c["line"]) or "with " in _line(
                c["file"], c["line"]
            ), f"{c['file']}:{c['line']} 已不含 `{m['name']}`"
    assert {m["name"] for m in d["canvas_methods"]["methods"]} == CANVAS_METHODS


def test_every_cited_test_line_still_contains_its_snippet():
    for e in _ledger()["exports"]:
        for t in e["tests"]:
            assert t["contains"] in _line(t["file"], t["line"]), (
                f"{t['file']}:{t['line']} 已不含 `{t['contains']}`（{e['name']}）"
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
