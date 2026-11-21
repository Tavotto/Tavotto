"""`scripts/dev/lessons_for_diff.py`：按改动文件取错题本里命中的条目。

匹配规则本身（目录前缀 / 同目录文件名前缀 / 精确路径）在 `test_lessons_format.py` 钉；
这里钉选择逻辑（通用条目默认不列、已升级的不列）与命令行的退出码、输出形状，
并用 `python3 -I` 真起一次：隔离模式不带脚本目录进 sys.path，导入写错会在这里才红。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dev" / "lessons_for_diff.py"


def _mod():
    spec = importlib.util.spec_from_file_location("tavotto_lessons_for_diff", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


D = _mod()

BOOK = """# 错题本 · ci

### L-001 改 CI 脚本先在本机设好环境变量
- 类: 别这样
- 触发: scripts/ci/, .github/workflows/ci.yml
- Seen: 2026-10-31
- 为什么: 只有 CI 才有的变量。
- 证据: #1

### L-002 结论取自退出码
- 类: 照这样
- 触发: *
- Seen: 2026-10-31
- 为什么: 管道吞退出码。
- 证据: #2

### L-003 只测 e2e 文件名前缀
- 类: 别这样
- 触发: web/e2e/card*
- Seen: 2026-10-31
- 为什么: 前缀。
- 证据: #3

### L-004 已升级的条目 → docs/rules/ci/x.md
"""


@pytest.fixture
def books(tmp_path):
    d = tmp_path / "lessons"
    d.mkdir()
    for x in D.L.LAYERS:
        (d / f"{x}.md").write_text("# 空\n", encoding="utf-8")
    (d / "ci.md").write_text(BOOK, encoding="utf-8")
    return d


def _ids(hits):
    return [x.id for x in hits]


def test_select_matches_by_path_and_hides_universal_by_default(books):
    lessons = D.L.load(books)
    assert _ids(D.select(lessons, ["scripts/ci/gate.py"])) == ["L-001"]
    assert _ids(D.select(lessons, ["scripts/ci/gate.py", "web/e2e/card_a.spec.ts"])) == [
        "L-001",
        "L-003",
    ]
    assert D.select(lessons, ["README.md"]) == []


def test_select_lists_universal_only_with_all(books):
    lessons = D.L.load(books)
    assert _ids(D.select(lessons, ["README.md"], include_universal=True)) == ["L-002"]
    assert _ids(D.select(lessons, ["scripts/ci/gate.py"], include_universal=True)) == [
        "L-001",
        "L-002",
    ]


def test_promoted_pointers_are_never_selected(books):
    lessons = D.L.load(books)
    assert "L-004" not in _ids(D.select(lessons, ["docs/rules/ci/x.md"], include_universal=True))


def test_cli_output_and_exit_codes(books, tmp_path, capsys):
    pf = tmp_path / "paths.txt"
    pf.write_text("scripts/ci/gate.py\n\n  web/e2e/card_a.spec.ts  \n", encoding="utf-8")
    assert D.main(["--dir", str(books), "--paths-file", str(pf)]) == 0
    cap = capsys.readouterr()
    lines = cap.out.splitlines()
    assert lines[0] == "L-001\tdocs/rules/repo/lessons/ci.md\t改 CI 脚本先在本机设好环境变量"
    assert lines[1].startswith("L-003\t")
    assert "L-002" not in cap.out
    assert "1 条通用条目" in cap.err  # 提示有通用条目没列
    assert D.main(["--dir", str(books), "--paths-file", str(pf), "--all", "--why"]) == 0
    out = capsys.readouterr().out
    assert "L-002" in out and "（通用）" in out and "为什么: 管道吞退出码。" in out


def test_cli_no_hit_is_exit_zero_and_empty(books, tmp_path, capsys):
    pf = tmp_path / "paths.txt"
    pf.write_text("README.md\n", encoding="utf-8")
    assert D.main(["--dir", str(books), "--paths-file", str(pf)]) == 0
    assert capsys.readouterr().out == ""


def test_cli_bad_inputs_exit_two(books, tmp_path, capsys):
    assert D.main(["--dir", str(books), "--paths-file", str(tmp_path / "nope.txt")]) == 2
    assert D.main(["--dir", str(tmp_path / "nolessons"), "--paths-file", "-"]) == 2
    capsys.readouterr()
    # 取不到 diff（范围不存在）= 2，不是「没有命中」的 0
    assert D.main(["--dir", str(books), "--range", "no-such-ref...HEAD"]) == 2
    assert capsys.readouterr().err.strip()


def test_changed_paths_uses_name_only_without_renames(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (tmp_path / "old.txt").write_text("x" * 200, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "a")
    git("mv", "old.txt", "new 中文.txt")
    git("commit", "-qm", "b")
    # 改名按「删旧 + 加新」各算：只认旧路径的触发也不会漏
    assert sorted(D.changed_paths("HEAD~1...HEAD", tmp_path)) == ["new 中文.txt", "old.txt"]


def test_runs_under_isolated_interpreter_against_the_real_book(tmp_path):
    """`python3 -I`：无 PYTHONPATH、无脚本目录入 sys.path，仍能读真错题本并按路径命中。"""
    pf = tmp_path / "p.txt"
    pf.write_text("README.md\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--paths-file", str(pf), "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    real = D.L.load()
    universal = [x.id for x in real if x.universal]
    assert [ln.split("\t")[0] for ln in r.stdout.splitlines()] == sorted(universal)
    # 真路径真命中：L-101 的触发就是它守的那个文件
    pf.write_text("tests/test_tracked_paths_are_windows_safe.py\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--paths-file", str(pf)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "L-101\tdocs/rules/repo/lessons/ci.md\t" in r.stdout


# ---- --hook：编码 agent 改完文件后注入命中的条目 ----


@pytest.fixture
def repo(tmp_path, books, monkeypatch):
    monkeypatch.setattr(D.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    return tmp_path


def _hook(repo, books, path, session="s1"):
    payload = {"session_id": session, "tool_input": {"file_path": str(repo / path)}}
    out = D.hook(payload, root=repo, directory=books)
    return None if out is None else json.loads(out)["hookSpecificOutput"]


def test_hook_injects_hits_once_per_session(repo, books):
    first = _hook(repo, books, "scripts/ci/gate.py")
    assert first["hookEventName"] == "PostToolUse"
    ctx = first["additionalContext"]
    assert "L-001" in ctx and "为什么：只有 CI 才有的变量。" in ctx
    assert "通用纪律" in ctx and "L-002" in ctx  # 会话里第一次改仓库文件附通用条目
    assert "L-003" not in ctx
    # 同一会话再改同一带：已提示过，不再重复
    assert _hook(repo, books, "scripts/ci/other.py") is None
    # 换一个文件命中新条目：只提示新的，通用的不再附
    ctx = _hook(repo, books, "web/e2e/card_x.spec.ts")["additionalContext"]
    assert "L-003" in ctx and "L-001" not in ctx and "L-002" not in ctx
    # 新会话从头来
    assert "L-001" in _hook(repo, books, "scripts/ci/gate.py", session="s2")["additionalContext"]


def test_hook_ignores_files_outside_the_repo_and_bad_payloads(repo, books, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "scripts" / "ci" / "gate.py"
    assert (
        D.hook(
            {"session_id": "s", "tool_input": {"file_path": str(outside)}},
            root=repo,
            directory=books,
        )
        is None
    )
    assert D.hook({"session_id": "s", "tool_input": {}}, root=repo, directory=books) is None
    assert D.hook({}, root=repo, directory=books) is None


def test_hook_cli_never_fails(tmp_path):
    """钩子挡住改文件比漏提示坏得多：坏输入、读不到错题本都静默退出 0。"""
    for stdin in ("not json", "", '{"tool_input": {"file_path": 3}}'):
        r = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--hook", "--dir", str(tmp_path / "none")],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert r.returncode == 0 and r.stdout == "", (stdin, r)


def test_hook_against_the_real_book(tmp_path):
    """真错题本、真起 `python3 -I`：改 L-101 守的那个文件，提示里点名 L-101。"""
    payload = {
        "session_id": f"real-{tmp_path.name}",
        "tool_input": {"file_path": str(ROOT / "tests" / "test_tracked_paths_are_windows_safe.py")},
    }
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **__import__("os").environ,
            "TMPDIR": str(tmp_path),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "L-101" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
