"""静态扫描的「读不动 / 解析不了」分类与目标解释器解析（U03，ADR 0057；FO-016 / FO-017 / FO12）。

判据的主语：`discover.inspect_script()` 对**一个文件**说的话——是确认非绘图（`info=None,
problem=None`）、还是 io_error / decode_error / syntax_error 之一；宿主判语法错误而项目的解释器
是另一个版本时，同一份分析在那个解释器里再跑一遍（真实子进程，只解析不执行）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import discover, probe

PLOT = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1])\nfig.savefig('fig.pdf')\n"


# ---------------------------------------------------------------- 编码
def test_a_latin1_source_with_a_coding_cookie_is_read_and_analyzed(tmp_path):
    src = "# -*- coding: latin-1 -*-\n# caf\xe9\n" + PLOT
    path = tmp_path / "fig.py"
    path.write_bytes(src.encode("latin-1"))
    seen = discover.inspect_script(path, tmp_path)
    assert seen["problem"] is None and seen["parser"] == "host"
    assert seen["info"]["stems"] == ["fig"]


def test_a_utf8_bom_source_is_read_and_analyzed(tmp_path):
    path = tmp_path / "fig.py"
    path.write_bytes(b"\xef\xbb\xbf" + PLOT.encode("utf-8"))
    seen = discover.inspect_script(path, tmp_path)
    assert seen["problem"] is None and seen["info"]["stems"] == ["fig"]


def test_undecodable_bytes_are_a_decode_error_not_a_syntax_error(tmp_path):
    path = tmp_path / "fig.py"
    path.write_bytes(b"# comment \xff\xfe bad utf-8\n" + PLOT.encode("utf-8"))
    seen = discover.inspect_script(path, tmp_path)
    assert seen["info"] is None
    assert seen["problem"]["kind"] == discover.PROBLEM_DECODE
    assert seen["problem"]["encoding"] in (
        None,
        "utf-8",
    )  # 首行就解不出时 detect_encoding 说不出编码


def test_a_bad_coding_cookie_is_a_decode_error(tmp_path):
    path = tmp_path / "fig.py"
    path.write_bytes(b"# -*- coding: no-such-codec -*-\n" + PLOT.encode("utf-8"))
    assert discover.inspect_script(path, tmp_path)["problem"]["kind"] == discover.PROBLEM_DECODE


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="chmod 000 在这里不挡读")
def test_an_unreadable_file_is_an_io_error(tmp_path):
    path = tmp_path / "fig.py"
    path.write_text(PLOT, encoding="utf-8")
    path.chmod(0)
    try:
        seen = discover.inspect_script(path, tmp_path)
    finally:
        path.chmod(0o644)
    assert seen["problem"]["kind"] == discover.PROBLEM_IO


def test_a_real_syntax_error_is_reported_with_the_host_version_and_line(tmp_path):
    path = tmp_path / "fig.py"
    path.write_text("def (:\n" + PLOT, encoding="utf-8")
    seen = discover.inspect_script(path, tmp_path)
    p = seen["problem"]
    assert p["kind"] == discover.PROBLEM_SYNTAX and p["parser"] == "host"
    assert p["lineno"] == 1 and p["parser_version"] == discover.host_parser_version()
    assert seen["entry_candidates"] is None


def test_a_non_plotting_module_is_neither_a_problem_nor_a_script(tmp_path):
    path = tmp_path / "helpers.py"
    path.write_text("def f(x):\n    return x * 2\n", encoding="utf-8")
    seen = discover.inspect_script(path, tmp_path)
    assert seen == {
        "info": None,
        "problem": None,
        "parser": "host",
        "entry_candidates": ["__main__"],
    }


# ---------------------------------------------------------------- 报告与清单
def test_discover_report_keeps_problems_apart_from_confirmed_non_scripts(tmp_path):
    (tmp_path / "fig.py").write_text(PLOT, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe\n")
    rep = discover.discover(tmp_path)
    assert set(rep["scripts"]) == {"fig.py"}
    assert {k: v["kind"] for k, v in rep["problems"].items()} == {
        "broken.py": discover.PROBLEM_SYNTAX,
        "bad.py": discover.PROBLEM_DECODE,
    }
    assert "helpers.py" not in rep["problems"]  # 确认非绘图：既不是脚本也不是问题


def test_inventory_keeps_unparseable_files_visible_and_says_which_kind(tmp_path, monkeypatch):
    (tmp_path / "fig.py").write_text(PLOT, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    from tavotto.engine import pool as engine_pool

    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda *a, **k: (sys.executable, "current_process")
    )
    rows = {r["script"]: r for r in probe.script_inventory(tmp_path, registered=set())}
    assert rows["broken.py"]["reason"] == probe.REASON_UNPARSEABLE
    assert rows["broken.py"]["problem"]["kind"] == discover.PROBLEM_SYNTAX
    assert rows["broken.py"]["can_probe"] is True
    assert rows["helpers.py"]["reason"] == probe.REASON_NO_STATIC_OUTPUT
    assert rows["helpers.py"]["problem"] is None
    assert rows["fig.py"]["reason"] == probe.REASON_STATIC and rows["fig.py"]["parser"] == "host"


# ---------------------------------------------------------------- 目标解释器解析（FO12 的机制）
def _newer_python() -> str | None:
    """比宿主新一个 minor 的真实解释器（只解析，不需要 matplotlib）。"""
    major, minor = sys.version_info[:2]
    for cand in (f"python{major}.{minor + 1}", f"python{major}.{minor + 2}"):
        found = shutil.which(cand)
        if found:
            return found
    for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        for m in (minor + 1, minor + 2):
            p = Path(base) / f"python{major}.{m}"
            if p.is_file():
                return str(p)
    return None


def _older_python() -> str | None:
    major, minor = sys.version_info[:2]
    for m in range(minor - 1, 9, -1):
        found = shutil.which(f"python{major}.{m}")
        if found:
            return found
    return None


def test_the_target_parser_is_a_real_subprocess_that_only_parses(tmp_path):
    """目标解析器起的是真实子进程：装本模块做静态分析，**不 import 用户脚本**——脚本里
    的副作用（写文件）不能发生。"""
    marker = tmp_path / "executed.txt"
    path = tmp_path / "fig.py"
    path.write_text(
        f"open({str(marker)!r}, 'w').write('ran')\n" + PLOT,
        encoding="utf-8",
    )
    out = discover.analyze_in_interpreter(sys.executable, path, tmp_path)
    assert "error" not in out, out
    assert out["problem"] is None and out["info"]["stems"] == ["fig"]
    assert out["parser_version"] == discover.host_parser_version()
    assert not marker.exists(), "目标解析器执行了用户脚本"


def test_the_target_parser_result_is_cached_by_content(tmp_path, monkeypatch):
    path = tmp_path / "fig.py"
    path.write_text(PLOT, encoding="utf-8")
    discover.reset_target_cache()
    calls = []
    real = subprocess.run

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(discover.subprocess, "run", counting)
    discover.analyze_in_interpreter(sys.executable, path, tmp_path)
    discover.analyze_in_interpreter(sys.executable, path, tmp_path)
    assert len(calls) == 1
    path.write_text(PLOT + "# changed\n", encoding="utf-8")
    discover.analyze_in_interpreter(sys.executable, path, tmp_path)
    assert len(calls) == 2


def test_an_unavailable_target_parser_keeps_the_host_verdict_and_records_why(tmp_path):
    path = tmp_path / "fig.py"
    path.write_text("def (:\n", encoding="utf-8")
    seen = discover.inspect_script(path, tmp_path, target_python=str(tmp_path / "no" / "python"))
    assert seen["problem"]["kind"] == discover.PROBLEM_SYNTAX
    assert seen["problem"]["parser_error"].startswith("spawn:")


def test_the_same_interpreter_as_the_host_is_not_asked_twice(tmp_path, monkeypatch):
    path = tmp_path / "fig.py"
    path.write_text("def (:\n", encoding="utf-8")
    monkeypatch.setattr(
        discover, "analyze_in_interpreter", lambda *a, **k: pytest.fail("不该起子进程")
    )
    seen = discover.inspect_script(path, tmp_path, target_python=sys.executable)
    assert (
        seen["problem"]["kind"] == discover.PROBLEM_SYNTAX and "parser_error" not in seen["problem"]
    )


def test_fo12_syntax_the_host_ast_rejects_but_the_target_accepts_keeps_the_script(tmp_path):
    """宿主 AST 不认识、目标解释器认识的合法语法：脚本不从列表消失，由目标解析器分析，
    stem / entry 与宿主同一份算法。需要一台有更新 minor 的 Python 的机器；没有就 skip。"""
    newer = _newer_python()
    if newer is None:
        pytest.skip("not_run：这台机器上没有比宿主新一个 minor 的 Python")
    # 3.14 的模板字符串（PEP 750）：3.13 的 ast 判语法错误
    src = "name = 'x'\nlabel = t'hello {name}'\n" + PLOT
    path = tmp_path / "fig.py"
    path.write_text(src, encoding="utf-8")
    host = discover.inspect_script(path, tmp_path)
    if host["problem"] is None:
        pytest.skip("not_run：宿主自己就认这段语法，没有 FO12 要的语法差")
    seen = discover.inspect_script(path, tmp_path, target_python=newer)
    assert seen["problem"] is None, seen
    assert seen["parser"] == "target" and seen["parser_version"] != discover.host_parser_version()
    assert seen["info"]["stems"] == ["fig"] and seen["info"]["entry"] == "__main__"
    assert seen["entry_candidates"] == ["__main__"]
    rep = discover.discover(tmp_path, target_python=newer)
    assert rep["scripts"]["fig.py"]["parser"] == "target" and rep["problems"] == {}
    # 对照：真正的语法错误两边都判错，problem 里两边的版本都在
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    broken = discover.inspect_script(tmp_path / "broken.py", tmp_path, target_python=newer)
    assert broken["problem"]["kind"] == discover.PROBLEM_SYNTAX
    assert broken["problem"]["target"]["parser_version"] != discover.host_parser_version()


def test_syntax_the_host_accepts_is_not_sent_to_the_target(tmp_path, monkeypatch):
    """宿主解析得了就不问目标——目标解析器只在宿主判语法错误时出场。"""
    path = tmp_path / "fig.py"
    path.write_text(PLOT, encoding="utf-8")
    monkeypatch.setattr(
        discover, "analyze_in_interpreter", lambda *a, **k: pytest.fail("不该起子进程")
    )
    older = _older_python() or str(tmp_path / "fake-python")
    seen = discover.inspect_script(path, tmp_path, target_python=older)
    assert seen["parser"] == "host" and seen["info"]["stems"] == ["fig"]
