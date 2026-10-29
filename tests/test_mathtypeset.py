"""标注公式排版（ADR 0152 PR 4）：子进程 + 按内容缓存。

真跑 matplotlib（测试解释器自己有它；没有就跳过）：排得出的给轮廓与盒子，排不出的给原因；
同一条第二次从缓存来、不再起子进程；没有可用解释器时回 `no_runtime` 且不落盘。
"""

from __future__ import annotations

import importlib.util

import pytest

from tavotto.engine import mathtypeset

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None, reason="测试解释器没有 matplotlib（not_run）"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    mathtypeset._memory.clear()
    mathtypeset._python.clear()
    yield
    mathtypeset._memory.clear()
    mathtypeset._python.clear()


def test_typesets_to_em_outlines_and_reports_syntax_errors():
    got = mathtypeset.typeset_many([("A e^{-E/RT}", "serif"), ("x^{2", "serif")])
    ok = got[mathtypeset.key_of("A e^{-E/RT}", "serif")]
    assert ok["ok"] and ok["width"] > 1.0 and ok["ascent"] > 0.5
    assert ok["segments"][0][0] == "M"
    assert {s[0] for s in ok["segments"]} <= {"M", "L", "C", "Z"}
    bad = got[mathtypeset.key_of("x^{2", "serif")]
    assert bad["ok"] is False and bad["error"]


def test_second_call_comes_from_cache_without_a_child(monkeypatch):
    mathtypeset.typeset_many([("x_i", "serif")])
    mathtypeset._memory.clear()  # 进程内缓存清掉，只剩磁盘那份

    def boom(_items):
        raise AssertionError("命中缓存时不该再起子进程")

    monkeypatch.setattr(mathtypeset, "_run_child", boom)
    assert mathtypeset.typeset_many([("x_i", "serif")])[mathtypeset.key_of("x_i", "serif")]["ok"]


def test_no_runtime_is_reported_and_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(mathtypeset, "_pick_python", lambda: None)
    got = mathtypeset.typeset_many([("y", "serif")])
    assert got[mathtypeset.key_of("y", "serif")] == {"ok": False, "error": mathtypeset.NO_RUNTIME}
    assert not (tmp_path / "cache" / "mathtext").exists()


def test_math_items_only_reads_latex_annotations():
    objs = [
        {"type": "text", "text": "k = $x^2$", "text_format": "latex", "font_family": "sans-serif"},
        {"type": "text", "text": "$y$"},  # 老标注：$ 是字
        {"type": "text", "text": "$z$", "text_format": "latex", "hidden": True},
    ]
    assert mathtypeset.math_items(objs) == [("x^2", "sans-serif")]
