"""发行说明闸（scripts/ci/release_note_gate.py）的看护。

判错的两头：把没写待发条目的用户可见 PR 放进 main（发版当天又要回头补），
或者把内部 PR 错拦在队列外（没人看得懂为什么红）。workflow 的形状契约
（merge_group 必须 success、接进 Gate 闭集、判定器取自默认分支）在
`TestReleaseNoteJob`，和判定逻辑放在同一个文件，一起落地。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import release_note_gate as G  # noqa: E402

UN = G.UNRELEASED_PATH
ADD_SECTION = "@@ -10,0 +11,3 @@\n+## Fixed a thing\n+\n+Symptom: x."


def f(name, status="modified", patch=None, **kw):
    d = {"filename": name, "status": status}
    if patch is not None:
        d["patch"] = patch
    d.update(kw)
    return d


def run(files, labels=(), author="alice", event="pull_request"):
    return G.decide(event, files, list(labels), author)["status"]


class TestDecide:
    def test_user_visible_without_note_fails(self):
        r = G.decide("pull_request", [f("src/tavotto/engine/x.py")], [], "alice")
        assert r["status"] == "failure"
        assert "no-release-note" in r["message"] and "## " in r["message"]

    @pytest.mark.parametrize(
        "path",
        [
            "src/tavotto/engine/x.py",
            "web/src/App.tsx",
            "src-tauri/src/main.rs",
            "src-tauri/tauri.conf.json",
            "codex-plugin/skills/tavotto-figure/SKILL.md",
        ],
    )
    def test_each_visible_prefix_fails(self, path):
        assert run([f(path)]) == "failure"

    def test_user_visible_with_new_section_passes(self):
        files = [f("web/src/App.tsx"), f(UN, patch=ADD_SECTION)]
        assert run(files) == "success"

    @pytest.mark.parametrize(
        "path",
        [
            "web/src/App.test.tsx",
            "web/src/canvas/foo.spec.ts",
            "web/src/test/fakeSessionBackend.ts",
            "web/src/canvas/__fixtures__/geoReference.json",
            "src-tauri/src/exit_guard_tests.rs",
            "src/tavotto/engine/test_thing.py",
            "src/tavotto/AGENTS.md",
        ],
    )
    def test_test_only_changes_pass(self, path):
        assert run([f(path)]) == "success"

    def test_docs_and_pipeline_only_pass(self):
        files = [f("docs/RELEASING.md"), f(".github/workflows/ci.yml"), f("tests/test_x.py")]
        assert run(files) == "success"

    def test_exempt_label(self):
        assert run([f("src/tavotto/a.py")], labels=["no-release-note"]) == "exempt"

    def test_other_label_does_not_exempt(self):
        assert run([f("src/tavotto/a.py")], labels=["bug", "full-ci"]) == "failure"

    def test_dependabot_exempt_exactly(self):
        files = [f("web/src/a.ts")]
        assert run(files, author="dependabot[bot]") == "exempt"
        # 不是「带 [bot] 后缀就放行」
        assert run(files, author="other[bot]") == "failure"

    def test_unreleased_comment_only_edit_fails(self):
        patch = "@@ -1,3 +1,3 @@\n-<!--\n+<!-- 改了措辞\n 说明"
        assert run([f("src/tavotto/a.py"), f(UN, patch=patch)]) == "failure"

    def test_unreleased_removal_only_fails(self):
        patch = "@@ -10,3 +10,0 @@\n-## Old\n-\n-text"
        assert run([f("src/tavotto/a.py"), f(UN, patch=patch)]) == "failure"

    def test_level3_heading_does_not_count(self):
        patch = "@@ -1,0 +2,1 @@\n+### sub"
        assert run([f("src/tavotto/a.py"), f(UN, patch=patch)]) == "failure"

    def test_release_pr_with_new_versioned_notes_passes(self):
        files = [
            f("src/tavotto/__init__.py"),
            f("docs/release-notes/v0.19.3.md", status="added"),
            f(UN, patch="@@ -10,3 +10,0 @@\n-## Old"),
        ]
        assert run(files) == "success"

    def test_editing_old_release_notes_is_not_a_release_pr(self):
        files = [f("src/tavotto/a.py"), f("docs/release-notes/v0.19.2.md")]
        assert run(files) == "failure"

    def test_rename_out_of_visible_path_counts(self):
        files = [f("docs/old.py", status="renamed", previous_filename="src/tavotto/a.py")]
        assert run(files) == "failure"

    def test_missing_patch_on_unreleased_is_config_error(self):
        with pytest.raises(G.ConfigError):
            G.decide("pull_request", [f("src/tavotto/a.py"), f(UN)], [], "alice")

    def test_merge_group_is_success_not_skipped(self):
        assert run([], event="merge_group") == "not_applicable"

    def test_unknown_event_is_config_error(self):
        with pytest.raises(G.ConfigError):
            G.decide("push", [], [], "alice")


class TestCli:
    def _run(self, tmp_path, files, *args):
        p = tmp_path / "files.json"
        p.write_text(json.dumps(files), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-I",
                str(CI_DIR / "release_note_gate.py"),
                "--files-json",
                str(p),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_exit_codes(self, tmp_path):
        bad = self._run(tmp_path, [f("web/src/a.ts")], "--event", "pull_request")
        assert bad.returncode == 1 and json.loads(bad.stdout)["status"] == "failure"
        ok = self._run(
            tmp_path,
            [f("web/src/a.ts")],
            "--event",
            "pull_request",
            "--labels-json",
            '["no-release-note"]',
        )
        assert ok.returncode == 0
        mg = subprocess.run(
            [sys.executable, "-I", str(CI_DIR / "release_note_gate.py"), "--event", "merge_group"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert mg.returncode == 0

    def test_incomplete_file_list_is_config_error(self, tmp_path):
        r = self._run(tmp_path, [f("a.md")], "--event", "pull_request", "--expected-files", "2")
        assert r.returncode == 2

    def test_concatenated_pages_accepted(self, tmp_path):
        p = tmp_path / "files.json"
        p.write_text(json.dumps([f("a.md")]) + "\n" + json.dumps([f("b.md")]), encoding="utf-8")
        assert len(G.load_files(p, 2)) == 2

    def test_empty_files_is_config_error(self, tmp_path):
        p = tmp_path / "files.json"
        p.write_text("", encoding="utf-8")
        with pytest.raises(G.ConfigError):
            G.load_files(p, None)


class TestVisiblePathsExist:
    def test_every_visible_prefix_exists(self):
        for pre in G.USER_VISIBLE_PREFIXES:
            assert (ROOT / pre).exists(), f"{pre} 不存在——目录改名了，清单没跟着改"


# ───────────────────────────────────────────── workflow 形状

CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
JOB_ID = "release-note-gate"


def _job(job_id):
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", CI, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`"
    return m.group(0)


def _code(text):
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


class TestReleaseNoteJob:
    def test_runs_on_pr_and_merge_group(self):
        m = re.search(r"(?m)^\s+if: (.+)$", _code(_job(JOB_ID)))
        assert m
        assert "github.event_name == 'pull_request'" in m.group(1)
        assert "github.event_name == 'merge_group'" in m.group(1), (
            "merge_group 上被跳过 = CI fast gate 永久红"
        )

    def test_in_fast_gate_needs_and_required(self):
        block = _job("ci-fast-gate")
        needs = re.search(r"(?m)^    needs: \[(.*?)\]", block).group(1).split(", ")
        req = re.search(r"--required (\S+)", block).group(1).split(",")
        assert JOB_ID in needs and JOB_ID in req

    def test_script_comes_from_default_branch_and_runs_isolated(self):
        code = _code(_job(JOB_ID))
        assert "default_branch" in code
        assert 'python3 -I "$TRUSTED/scripts/ci/release_note_gate.py"' in code

    def test_cheap_no_dependency_install(self):
        code = _code(_job(JOB_ID))
        for heavy in ("pip install", "setup-python", "setup-node", "pnpm", "actions/cache"):
            assert heavy not in code

    def test_no_pull_request_target(self):
        assert "pull_request_target" not in _code(CI)
