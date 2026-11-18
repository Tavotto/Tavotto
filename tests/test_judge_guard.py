"""判据路径分类器（scripts/ci/judge_guard.py）与名单（.github/judge-paths.json）的看护。

规则全文：docs/rules/ci/judge-guard.md。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "judge_guard.py"
CONFIG = ROOT / ".github" / "judge-paths.json"
CI = ROOT / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(SCRIPT.parent))
import judge_guard  # noqa: E402


def _config() -> dict:
    return judge_guard.load_config(CONFIG)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    return [p for p in out.split("\x00") if p]


def _run(tmp_path: Path, paths: list[str], config: dict | None = None, raw: str | None = None):
    pf = tmp_path / "changed.txt"
    pf.write_text("\n".join(paths) + "\n", encoding="utf-8")
    cf = tmp_path / "judge-paths.json"
    cf.write_text(raw if raw is not None else json.dumps(config or _config()), encoding="utf-8")
    summary = tmp_path / "summary.md"
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--paths-file", str(pf), "--config", str(cf)],
        capture_output=True,
        encoding="utf-8",
        env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    return proc, (summary.read_text(encoding="utf-8") if summary.exists() else "")


class TestMatching:
    def test_directory_entry_is_a_prefix_not_a_substring(self):
        assert judge_guard.matches("scripts/ci/", "scripts/ci/aggregate_gate.py")
        assert not judge_guard.matches("scripts/ci/", "scripts/cid/x.py")
        assert not judge_guard.matches("scripts/ci/", "web/scripts/ci/x.py")

    def test_file_entry_is_exact(self):
        assert judge_guard.matches("AGENTS.md", "AGENTS.md")
        assert not judge_guard.matches("AGENTS.md", "web/AGENTS.md")
        assert not judge_guard.matches("AGENTS.md", "AGENTS.md.bak")

    def test_star_entry_is_a_filename_prefix_in_one_directory(self):
        assert judge_guard.matches(
            "tests/test_compat_guardian*", "tests/test_compat_guardian_trend.py"
        )
        assert not judge_guard.matches(
            "tests/test_compat_guardian*", "tests/test_compat_guardian/x.py"
        )
        assert not judge_guard.matches(
            "tests/test_compat_guardian*", "tests/test_compat_explore.py"
        )

    def test_product_change_is_not_a_judge_change(self):
        r = judge_guard.decide(["src/tavotto/engine/manifest.py", "web/src/App.tsx"], _config())
        assert r["touched"] is False and r["hits"] == {}

    def test_rename_old_path_counts(self):
        # 把看护用例挪走：PR 文件列表里新旧两条路径都会进来，旧路径命中就算。
        r = judge_guard.decide(["tests/test_aggregate_gate.py", "tests/old/x.py"], _config())
        assert r["hits"] == {"tests/test_aggregate_gate.py": ["tests/test_aggregate_gate.py"]}


class TestConfig:
    def test_every_entry_points_at_something_tracked(self):
        """空条目 = 读的人以为它在看着、其实什么都没看（空转门禁）。"""
        tracked = _tracked()
        dead = [
            e["path"]
            for e in _config()["entries"]
            if not any(judge_guard.matches(e["path"], p) for p in tracked)
        ]
        assert dead == [], f"判据名单里这些条目匹配不到任何已跟踪文件：{dead}"

    def test_every_entry_says_why(self):
        assert all(e.get("why", "").strip() for e in _config()["entries"])

    def test_guard_watches_itself(self):
        cfg = _config()
        for own in (
            ".github/judge-paths.json",
            "scripts/ci/judge_guard.py",
            "tests/test_judge_guard.py",
            "docs/rules/ci/judge-guard.md",
            ".github/workflows/ci.yml",
        ):
            assert judge_guard.decide([own], cfg)["touched"], own

    def test_mode_is_known(self):
        assert _config()["mode"] in judge_guard.KNOWN_MODES


class TestCli:
    def test_observe_hit_notices_and_exits_zero(self, tmp_path):
        proc, summary = _run(tmp_path, ["scripts/ci/aggregate_gate.py", "src/x.py"])
        assert proc.returncode == 0, proc.stderr
        assert "::notice title=这次改了判据路径::scripts/ci/" in proc.stderr
        assert "`scripts/ci/aggregate_gate.py`" in summary
        assert json.loads(proc.stdout)["touched"] is True

    def test_no_hit_is_silent(self, tmp_path):
        proc, summary = _run(tmp_path, ["src/x.py"])
        assert proc.returncode == 0
        assert "::notice" not in proc.stderr and summary == ""

    def test_off_is_silent_even_on_hit(self, tmp_path):
        cfg = {**_config(), "mode": "off"}
        proc, summary = _run(tmp_path, ["AGENTS.md"], cfg)
        assert proc.returncode == 0 and "::notice" not in proc.stderr and summary == ""

    def test_unknown_mode_falls_back_to_observe_with_warning(self, tmp_path):
        cfg = {**_config(), "mode": "enforce-someday"}
        proc, _ = _run(tmp_path, ["AGENTS.md"], cfg)
        assert proc.returncode == 0
        assert "::warning title=判据名单 mode 未知" in proc.stderr
        assert "::notice title=这次改了判据路径" in proc.stderr

    @pytest.mark.parametrize("raw", ["", "{", '{"entries": []}', '{"entries": [{"why": "x"}]}'])
    def test_broken_config_warns_but_never_fails(self, tmp_path, raw):
        proc, _ = _run(tmp_path, ["AGENTS.md"], raw=raw)
        assert proc.returncode == 0
        assert "::warning title=判据路径检测没跑成" in proc.stderr


def _docs_scope_steps() -> list[str]:
    """ci.yml 里 docs-scope job 的各个 step 块（文本切分，与 test_merge_queue_workflows 同法：仓库不装 PyYAML）。"""
    text = CI.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^  docs-scope:\s*$(.*?)(?=^  [A-Za-z_][\w-]*:\s*$)", text)
    assert m, "ci.yml 里切不出 docs-scope job"
    body = m.group(1).split("\n    steps:\n", 1)[1]
    return [b for b in re.split(r"(?m)^      - ", body) if b.strip()]


class TestWorkflowWiring:
    @staticmethod
    def _step() -> str:
        steps = [s for s in _docs_scope_steps() if "judge_guard.py" in s]
        assert len(steps) == 1, "docs-scope 里应恰好一步跑 judge_guard.py"
        return steps[0]

    def test_reads_classifier_and_list_from_default_branch(self):
        step = self._step()
        assert "REF: ${{ github.event.repository.default_branch }}" in step
        assert "for f in scripts/ci/judge_guard.py .github/judge-paths.json; do" in step
        assert "contents/$f?ref=$REF" in step
        assert '"$JG/judge_guard.py"' in step and '"$JG/judge-paths.json"' in step

    def test_observe_step_cannot_turn_docs_scope_red(self):
        assert re.search(r"(?m)^        continue-on-error: true$", self._step())

    def test_runs_after_the_pr_file_list_is_reconciled(self):
        """PR 文件列表在分类那一步已和 changed_files 对过账；本步复用它，必须排在后面。"""
        steps = _docs_scope_steps()
        classify = next(i for i, s in enumerate(steps) if "name: 校验 docs-only 范围（分类）" in s)
        guard = next(i for i, s in enumerate(steps) if "judge_guard.py" in s)
        assert classify < guard
