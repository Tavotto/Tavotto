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
def test_a_script_outside_the_project_root_is_never_read(tmp_path, monkeypatch):
    """CodeQL #145 / #146：`inspect_script` / `analyze_in_interpreter` / `probe.entry_candidates`
    读的都是钉在项目根之内的 realpath——`../` 回溯到项目外的脚本一个字节都不读，
    分别落 `io_error`（脚本不在项目目录之内）/ `{"error"}` / 盲试列表。"""
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text(PLOT, encoding="utf-8")
    reads: list[str] = []
    real_read = Path.read_bytes

    def spy(self):
        reads.append(str(Path(self).resolve()))
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    seen = discover.inspect_script(root / ".." / "secret.py", root)
    assert seen["problem"]["kind"] == discover.PROBLEM_IO and seen["info"] is None
    assert "不在项目目录之内" in seen["problem"]["detail"]
    remote = discover.analyze_in_interpreter(sys.executable, root / ".." / "secret.py", root)
    assert "error" in remote and "不在项目目录之内" in remote["error"]
    assert probe.entry_candidates(root, "../secret.py") == list(probe.FALLBACK_ENTRIES)
    assert str(outside.resolve()) not in reads, "项目外的脚本被读了"
    # 对照：同一份内容放在项目里照常分析
    (root / "fig.py").write_text(PLOT, encoding="utf-8")
    assert discover.inspect_script(root / "fig.py", root)["info"]["stems"] == ["fig"]


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


def test_undecodable_bytes_after_a_clean_first_line_are_a_decode_error_with_the_encoding(tmp_path):
    """首行干净（detect_encoding 判 utf-8）、正文里有坏字节：`data.decode()` 那一步报出来，
    带上判定的编码——与首行就解不出的那条是两条路，各自要能红。"""
    path = tmp_path / "fig.py"
    path.write_bytes(PLOT.encode("utf-8") + b"# tail \xff\xfe\n")
    seen = discover.inspect_script(path, tmp_path)
    assert seen["info"] is None
    assert seen["problem"]["kind"] == discover.PROBLEM_DECODE
    assert seen["problem"]["encoding"] == "utf-8"


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
NEWER_PYTHON_ENV = "TAVOTTO_FOUNDATION_NEWER_PYTHON"

#: 「宿主不认、更新的 minor 认」的语法样本，按**目标至少要到的 minor** 排：FO12 要的是
#: 宿主 `ast` 判语法错误而目标解析得了的真实语法差。样本选哪一条由宿主版本决定——
#: 3.13 宿主用 3.14 的模板字符串，3.10 宿主用 3.11 的 `except*`——写死一条只在一种
#: 宿主上成立（CI 的 3.10 腿找到 3.11 也不认 t-string，前提不成立却按失败报）。
_SYNTAX_SAMPLES: tuple[tuple[int, str, str], ...] = (
    (11, "except* (PEP 654)", "try:\n    pass\nexcept* ValueError:\n    pass\n"),
    (12, "type 语句 (PEP 695)", "type Alias = int\n"),
    (13, "类型参数默认值 (PEP 696)", "def _ident[T = int](x: T) -> T:\n    return x\n"),
    (14, "模板字符串 (PEP 750)", "name = 'x'\nlabel = t'hello {name}'\n"),
)


def _candidate_pythons() -> list[str]:
    """比宿主新的真实解释器候选（只解析，不需要 matplotlib）：环境变量点名的优先，
    其次 PATH 与常见安装目录里更高 minor 的 pythonX.Y。"""
    out: list[str] = []
    named = os.environ.get(NEWER_PYTHON_ENV, "").strip()
    if named:
        out.append(named)
    major, minor = sys.version_info[:2]
    for m in range(minor + 1, minor + 5):
        found = shutil.which(f"python{major}.{m}")
        if found:
            out.append(found)
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            cand = Path(base) / f"python{major}.{m}"
            if cand.is_file():
                out.append(str(cand))
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _parses_in(python: str, src: str) -> bool:
    """目标解释器**自己的** `ast.parse` 认不认这段——与 discover 无关的独立判据。"""
    try:
        proc = subprocess.run(
            [python, "-I", "-c", "import ast, sys; ast.parse(sys.stdin.read())"],
            input=src,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _syntax_gap() -> tuple[str, str, str] | None:
    """回 `(目标解释器, 样本源码, 样本名)`：宿主 `ast.parse` 判语法错误、目标解释器
    解析得了。两边都是**真实**判定（宿主在本进程、目标起子进程），不是按版本号推的；
    没有满足前提的组合就回 None，用例据此 skip 并写明前提。"""
    import ast

    host_minor = sys.version_info[1]
    for python in _candidate_pythons():
        for min_minor, name, src in _SYNTAX_SAMPLES:
            if min_minor <= host_minor:
                continue
            try:
                ast.parse(src)
            except SyntaxError:
                pass
            else:
                continue  # 宿主自己认这段：不是语法差
            if _parses_in(python, src):
                return python, src, name
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


def test_the_target_parser_cache_is_keyed_by_path_and_root_not_only_by_bytes(tmp_path):
    """两个内容逐字相同、`Path(__file__).with_suffix('.pdf')` 自命名的脚本：stem 来自文件名，
    只按内容缓存会让 `beta.py` 报出 `alpha`（Codex #454 P2）。"""
    src = (
        "from pathlib import Path\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
        "ax.plot([1])\nfig.savefig(Path(__file__).with_suffix('.pdf'))\n"
    )
    for name in ("alpha.py", "beta.py"):
        (tmp_path / name).write_text(src, encoding="utf-8")
    discover.reset_target_cache()
    a = discover.analyze_in_interpreter(sys.executable, tmp_path / "alpha.py", tmp_path)
    b = discover.analyze_in_interpreter(sys.executable, tmp_path / "beta.py", tmp_path)
    assert a["info"]["stems"] == ["alpha"] and b["info"]["stems"] == ["beta"]
    # 同一份脚本换一个项目根也是另一个键（`_resolve` 对的是那个根下的产物）
    other = tmp_path / "other"
    other.mkdir()
    (other / "alpha.py").write_text(src, encoding="utf-8")
    (other / "alpha.pdf").write_bytes(b"%PDF-1.4\n")
    c = discover.analyze_in_interpreter(sys.executable, other / "alpha.py", other)
    assert c["info"]["stems"] == ["alpha"]


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
    gap = _syntax_gap()
    if gap is None:
        pytest.skip(
            "not_run：前提是「宿主 ast 判语法错误、另一个真实解释器解析得了」的语法差——"
            f"这台机器上没有比 {sys.version_info[0]}.{sys.version_info[1]} 新、且认"
            f"{' / '.join(n for m, n, _ in _SYNTAX_SAMPLES if m > sys.version_info[1])}"
            f"之一的 Python（{NEWER_PYTHON_ENV} 可点名）"
        )
    newer, sample, name = gap
    src = sample + PLOT
    path = tmp_path / "fig.py"
    path.write_text(src, encoding="utf-8")
    host = discover.inspect_script(path, tmp_path)
    assert host["problem"]["kind"] == discover.PROBLEM_SYNTAX, (name, host)
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
