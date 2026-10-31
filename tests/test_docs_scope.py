"""docs-only 分类器（scripts/ci/docs_scope.py）的看护。

判错的两头：把改了代码的 PR 判成 docs-only（重型资格被跳过，坏代码进 main，最贵），
或把纯发行说明 PR 判成非 docs-only（只是没提速，便宜）。所以每条用例都从「会不会
误判成 docs-only」这一头出发；workflow 形状与 Gate 配合在
tests/test_merge_queue_workflows.py::TestDocsOnlyFastLane。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import docs_scope as DS  # noqa: E402

RN = "docs/release-notes/"


class TestInScope:
    @pytest.mark.parametrize(
        "path",
        [f"{RN}v0.19.2.md", f"{RN}v0.19.0-beta.1.md", f"{RN}sub/dir/x.md"],
    )
    def test_release_notes_markdown_is_in_scope(self, path):
        assert DS.in_scope(path)

    @pytest.mark.parametrize(
        "path",
        [
            f"{RN}UNRELEASED.md",  # 有测试读它（test_claude_plugin / test_dsh_bundle）
            f"{RN}v0.19.2.txt",  # 只认 .md
            f"{RN}v0.19.2.md.py",
            "docs/release-notes-x/a.md",  # 前缀撞名
            "docs/release-notes.md",
            "docs/adr/0001-x.md",  # 整个 docs/ 不算，用户只批了发行说明
            "README.md",
            "src/tavotto/x.py",
            f"{RN}../../src/tavotto/x.md",  # 穿越
            f"{RN}./v1.md",
            f"{RN}a//b.md",
            "/docs/release-notes/v1.md",
            f"{RN}a\\b.md",
            "",
        ],
    )
    def test_everything_else_is_out_of_scope(self, path):
        assert not DS.in_scope(path)


class TestDecide:
    def test_only_release_notes_is_docs_only(self):
        v = DS.decide([f"{RN}v0.19.2.md", f"{RN}v0.19.1.md"])
        assert v["docs_only"] is True and v["reason"] == "release_notes_only"

    def test_a_single_outside_file_is_enough_to_say_no(self):
        """队列里多个 PR 的组合：任一改了别的就不是 docs-only。"""
        v = DS.decide([f"{RN}v0.19.2.md", "src/tavotto/engine/x.py"])
        assert v["docs_only"] is False
        assert v["outside"] == ["src/tavotto/engine/x.py"]

    def test_unreleased_alone_is_not_docs_only(self):
        assert DS.decide([f"{RN}UNRELEASED.md"])["docs_only"] is False

    def test_empty_change_set_is_not_docs_only(self):
        v = DS.decide([])
        assert v["docs_only"] is False and v["reason"] == "no_changes"

    def test_rename_counts_both_paths(self):
        """把别处的文件挪进发行说明目录（或挪出去）都是改动。"""
        files = [
            {"filename": f"{RN}v1.md", "status": "renamed", "previous_filename": "src/a.py"},
        ]
        assert DS.decide(DS.paths_from_pr_files(files))["docs_only"] is False
        files = [{"filename": f"{RN}v1.md", "status": "renamed", "previous_filename": f"{RN}v0.md"}]
        assert DS.decide(DS.paths_from_pr_files(files))["docs_only"] is True


class TestLoadFiles:
    def _write(self, tmp_path, text):
        p = tmp_path / "files.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_paginated_arrays_are_concatenated(self, tmp_path):
        p = self._write(tmp_path, '[{"filename": "a.md"}]\n[{"filename": "b.md"}]')
        assert [f["filename"] for f in DS.load_files(p, 2)] == ["a.md", "b.md"]

    def test_count_mismatch_is_a_config_error(self, tmp_path):
        """列表不全不能据此判 docs-only——漏掉的那个文件可能是代码。"""
        p = self._write(tmp_path, '[{"filename": "a.md"}]')
        with pytest.raises(DS.ConfigError):
            DS.load_files(p, 2)

    def test_missing_expected_count_is_a_config_error(self, tmp_path):
        p = self._write(tmp_path, '[{"filename": "a.md"}]')
        with pytest.raises(DS.ConfigError):
            DS.load_files(p, None)

    @pytest.mark.parametrize("text", ["", "not json", '{"filename": "a"}', '[{"name": "a"}]'])
    def test_malformed_input_is_a_config_error(self, tmp_path, text):
        with pytest.raises(DS.ConfigError):
            DS.load_files(self._write(tmp_path, text), 1)


# ───────────────────────────────────────────── merge_group：真 git diff
@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
class TestGitDiff:
    @pytest.fixture
    def repo(self, tmp_path):
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip()

        git("init", "-q")
        (tmp_path / "docs" / "release-notes").mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "docs/release-notes/v1.md").write_text("one\n" * 20, encoding="utf-8")
        (tmp_path / "src/a.py").write_text("print(1)\n" * 20, encoding="utf-8")
        git("add", ".")
        git("commit", "-qm", "base")
        return tmp_path, git

    def _classify(self, repo, git, mutate):
        base = git("rev-parse", "HEAD")
        mutate(repo)
        git("add", "-A")
        git("commit", "-qm", "candidate")
        head = git("rev-parse", "HEAD")
        return DS.decide(DS.paths_from_git_diff(base, head, repo))

    def test_notes_only_candidate(self, repo):
        tmp, git = repo
        v = self._classify(
            tmp,
            git,
            lambda r: (r / "docs/release-notes/v2.md").write_text("two\n", encoding="utf-8"),
        )
        assert v["docs_only"] is True

    def test_deleting_a_release_note_is_still_docs_only(self, repo):
        tmp, git = repo
        v = self._classify(tmp, git, lambda r: (r / "docs/release-notes/v1.md").unlink())
        assert v["docs_only"] is True

    def test_queue_group_with_one_code_change_is_not_docs_only(self, repo):
        tmp, git = repo

        def mutate(r):
            (r / "docs/release-notes/v2.md").write_text("two\n", encoding="utf-8")
            (r / "src/a.py").write_text("print(2)\n" * 20, encoding="utf-8")

        assert self._classify(tmp, git, mutate)["docs_only"] is False

    def test_rename_out_of_release_notes_is_not_docs_only(self, repo):
        """`--no-renames`：改名拆成 删旧 + 加新，新路径在范围外就拦住。"""
        tmp, git = repo
        v = self._classify(
            tmp, git, lambda r: (r / "docs/release-notes/v1.md").rename(r / "src/v1.md")
        )
        assert v["docs_only"] is False

    def test_unicode_path_does_not_break_parsing(self, repo):
        tmp, git = repo
        v = self._classify(
            tmp,
            git,
            lambda r: (r / "docs/release-notes/说明.md").write_text("x\n", encoding="utf-8"),
        )
        assert v["docs_only"] is True

    def test_bad_sha_is_a_config_error_not_a_quiet_false(self, repo):
        tmp, git = repo
        with pytest.raises(DS.ConfigError):
            DS.paths_from_git_diff("deadbeef" * 5, git("rev-parse", "HEAD"), tmp)


# ───────────────────────────────────────────── CLI：退出码与 GITHUB_OUTPUT
class TestMain:
    def _files(self, tmp_path, names):
        p = tmp_path / "files.json"
        p.write_text(json.dumps([{"filename": n, "status": "modified"} for n in names]))
        return p

    def test_docs_only_writes_output_true(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        files = self._files(tmp_path, [f"{RN}v1.md"])
        rc = DS.main(
            ["--event", "pull_request", "--files-json", str(files), "--expected-files", "1"]
        )
        assert rc == 0 and out.read_text() == "docs_only=true\n"
        assert json.loads(capsys.readouterr().out.splitlines()[0])["docs_only"] is True

    def test_not_docs_only_is_exit_zero_with_false(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        files = self._files(tmp_path, [f"{RN}v1.md", "src/a.py"])
        rc = DS.main(
            ["--event", "pull_request", "--files-json", str(files), "--expected-files", "2"]
        )
        assert rc == 0 and out.read_text() == "docs_only=false\n"

    def test_config_error_exits_2_and_writes_no_output(self, tmp_path, monkeypatch):
        """输出缺失 + job 红：Gate 看到分类 job 失败，不会拿到一个「false」当作无事发生。"""
        out = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        files = self._files(tmp_path, [f"{RN}v1.md"])
        rc = DS.main(
            ["--event", "pull_request", "--files-json", str(files), "--expected-files", "5"]
        )
        assert rc == 2 and not out.exists()

    @pytest.mark.parametrize("event", ["push", "schedule", ""])
    def test_undesigned_events_are_config_errors(self, event):
        assert DS.main(["--event", event]) == 2

    def test_merge_group_requires_both_shas(self):
        assert DS.main(["--event", "merge_group", "--base-sha", "abc"]) == 2
