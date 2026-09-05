"""`scripts/ci/check_generated_untracked.py`：发行生成物不许进索引（ADR 0043）。

主语是**索引**——`git ls-files` 与 `git check-ignore`，不是 .gitignore 的文本。三种状态各一条：
被跟踪（红）、没跟踪但没被忽略（红：下一次 `git add -A` 就带回来）、没跟踪且被忽略（绿）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.support import pluginkit as kit

ROOT = kit.ROOT
checker = kit.load_script("ci/check_generated_untracked")


def _repo(tmp_path: Path, *, ignore: bool, track: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    kit.git("init", "--quiet", "-b", "main", cwd=repo)
    canvas = repo / "codex-plugin" / "mcp" / "widget" / "canvas.html"
    canvas.parent.mkdir(parents=True)
    canvas.write_text("<!-- x -->", encoding="utf-8")
    (repo / ".gitignore").write_text(
        (
            "codex-plugin/mcp/widget/canvas.html\nweb/dist-mcp/\nweb/dist-playground/\n"
            if ignore
            else "nothing\n"
        ),
        encoding="utf-8",
    )
    kit.git("add", ".gitignore", cwd=repo)
    if track:
        kit.git("add", "-f", "codex-plugin/mcp/widget/canvas.html", cwd=repo)
    kit.git("commit", "--quiet", "-m", "seed", cwd=repo)
    return repo


def test_a_tracked_canvas_is_reported(tmp_path):
    repo = _repo(tmp_path, ignore=True, track=True)
    problems = checker.check(repo)
    assert any("在索引里" in p and "canvas.html" in p for p in problems), problems


def test_an_unignored_canvas_is_reported_even_if_untracked(tmp_path):
    repo = _repo(tmp_path, ignore=False, track=False)
    problems = checker.check(repo)
    assert any("没被 .gitignore 挡住" in p and "canvas.html" in p for p in problems), problems


def test_untracked_and_ignored_is_clean(tmp_path):
    repo = _repo(tmp_path, ignore=True, track=False)
    assert checker.check(repo) == []


def test_the_real_repository_is_clean():
    """本仓库自己：画布不在索引里、被忽略。这是 PR B 的核心事实，用真 git 问。"""
    assert checker.check(ROOT) == []
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "codex-plugin/mcp/widget/canvas.html"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.strip() == ""


def test_the_cli_exit_code_carries_the_verdict(tmp_path):
    bad = _repo(tmp_path, ignore=True, track=True)
    assert checker.main(["--repo", str(bad)]) == 1
    good = _repo(tmp_path / "g", ignore=True, track=False)
    assert checker.main(["--repo", str(good)]) == 0
