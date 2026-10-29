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
    for d in (mathtypeset._memory, mathtypeset._picked, mathtypeset._missed):
        d.clear()
    yield
    for d in (mathtypeset._memory, mathtypeset._picked, mathtypeset._missed):
        d.clear()


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
    monkeypatch.setattr(mathtypeset, "_pick_python", lambda _dir=None: None)
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


def test_a_missing_interpreter_is_retried_later(monkeypatch):
    """第一次找不到解释器不记死：装好之后（这里是隔了 RETRY_S）不重启也能排（Codex #1497）。"""
    import sys

    answers = [None, (sys.executable, False)]
    monkeypatch.setattr(mathtypeset, "_pick_python", lambda _dir=None: answers.pop(0))
    monkeypatch.setattr(mathtypeset, "RETRY_S", 0.0)
    first = mathtypeset.typeset_many([("q", "serif")])[mathtypeset.key_of("q", "serif")]
    assert first == {"ok": False, "error": mathtypeset.NO_RUNTIME}
    assert mathtypeset.typeset_many([("q", "serif")])[mathtypeset.key_of("q", "serif")]["ok"]


def test_the_figure_interpreter_decision_is_a_candidate(monkeypatch, tmp_path):
    """内置 runtime 不在、当前解释器也不算（打包态）时，用图渲染那条决策给的解释器——
    环境变量 / 设置 / 项目环境都在那条决策里（Codex #1497）。"""
    import sys

    from tavotto.engine import pool, runtime

    seen = {}

    def resolve(figures_dir, **_kw):
        seen["dir"] = figures_dir
        return sys.executable, "env_override"

    monkeypatch.setattr(runtime, "bundled_python", lambda: None)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "resolve_worker_python", resolve)
    assert mathtypeset._pick_python(tmp_path) == (sys.executable, False)
    assert seen["dir"] == tmp_path
