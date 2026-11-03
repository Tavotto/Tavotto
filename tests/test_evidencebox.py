"""证据机执行器（ADR 0166，docs/rules/ci/evidence-box.md）的看护用例。

真机行为（Docker 隔离、镜像构建、夜跑）只在证据机上实测；这里钉住不依赖机器的契约：
执行容器的隔离参数、只跑 main、每个容器都硬关回传、与 workflow 同源的 case 集与安装步骤、
cron 执行 main 上的副本、仓库里没有主机地址与凭据。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOX_DIR = ROOT / "scripts" / "evidencebox"
sys.path.insert(0, str(BOX_DIR))

import box as B  # noqa: E402

SHA = "a" * 40
OUT = Path("/srv/tavotto-evidence/runs/x")


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


# --------------------------------------------------------------------------- 执行容器
def test_exec_container_carries_the_whole_isolation_set():
    argv = B.exec_argv("img", OUT, ["true"])
    assert argv[:3] == ["docker", "run", "--rm"]
    assert _flag_values(argv, "--network") == ["none"]
    assert _flag_values(argv, "--cap-drop") == ["ALL"]
    assert _flag_values(argv, "--security-opt") == ["no-new-privileges"]
    assert "--read-only" in argv
    user = _flag_values(argv, "--user")
    assert user and not user[0].startswith("0") and user[0] != "root"
    for limit in ("--pids-limit", "--memory", "--cpus", "--tmpfs"):
        assert _flag_values(argv, limit), limit


def test_exec_container_mounts_only_the_run_directory():
    argv = B.exec_argv("img", OUT, ["true"])
    assert _flag_values(argv, "-v") == [f"{OUT}:/out:rw"]
    joined = " ".join(argv)
    for forbidden in ("docker.sock", "--privileged", "--network host", "--pid host", "--ipc host"):
        assert forbidden not in joined, forbidden


@pytest.mark.parametrize("shards", [1, 2, 3, 4])
def test_explore_shards_use_the_same_isolated_constructor(shards, monkeypatch, tmp_path):
    """explore 的每一片也必须经 exec_argv：截获 Popen 的 argv 逐个核对。"""
    seen: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv):
            seen.append(argv)

        def wait(self):
            return 0

    monkeypatch.setattr(B, "RUNS", tmp_path)
    monkeypatch.setattr(B, "build_image", lambda *a, **k: "img")
    monkeypatch.setattr(B.os, "chown", lambda *a: None, raising=False)  # Windows 的 os 没有 chown
    monkeypatch.setattr(B.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(B, "run_container", lambda *a, **k: 0)
    assert B.task_guardian_explore(SHA, 42, shards, 60) == 0
    assert len(seen) == shards
    for argv in seen:
        assert _flag_values(argv, "--network") == ["none"]
        assert _flag_values(argv, "--cap-drop") == ["ALL"]
        assert "--read-only" in argv


def test_every_container_hard_disables_phone_home():
    ctors = {
        "prepare": B.prepare_argv("n", Path("/src"), "base"),
        "exec": B.exec_argv("img", OUT, ["true"]),
        "novice": B.novice_argv(Path("/n.sh"), OUT),
    }
    for name, argv in ctors.items():
        env = set(_flag_values(argv, "-e"))
        for key in ("TAVOTTO_NO_TELEMETRY", "TAVOTTO_NO_UPDATE_CHECK", "TAVOTTO_NO_DIAG_UPLOAD"):
            assert f"{key}=1" in env, (name, key)


def test_novice_container_mounts_only_its_script_and_run_directory():
    argv = B.novice_argv(Path("/n.sh"), OUT)
    assert _flag_values(argv, "-v") == [f"{Path('/n.sh')}:/novice.sh:ro", f"{OUT}:/out:rw"]
    assert "--rm" in argv and "--privileged" not in argv and "docker.sock" not in " ".join(argv)


# --------------------------------------------------------------------------- 可信度
@pytest.mark.parametrize(
    ("sha", "on_main", "ok"),
    [
        (SHA, True, True),
        (SHA, False, False),  # 不在 main 上：没有维护者例外
        ("a" * 12, True, False),  # 短 SHA
        ("main", True, False),
        ("A" * 40, True, False),
    ],
)
def test_trust_decision_is_full_sha_on_main(sha, on_main, ok):
    assert B.trust_decision(sha, on_main) is ok


def test_resolve_main_commit_refuses_a_commit_off_main(monkeypatch):
    monkeypatch.setattr(B, "sync", lambda: "b" * 40)
    monkeypatch.setattr(B, "git", lambda *a, **k: SHA)
    monkeypatch.setattr(B, "is_ancestor", lambda sha: False)
    with pytest.raises(SystemExit, match="not on origin/main"):
        B.resolve_main_commit(SHA)


def test_resolve_main_commit_defaults_to_fresh_main(monkeypatch):
    calls = []
    monkeypatch.setattr(B, "sync", lambda: calls.append("sync") or "b" * 40)
    assert B.resolve_main_commit(None) == "b" * 40
    assert calls == ["sync"]


def test_cron_runs_the_copy_from_origin_main(monkeypatch, tmp_path):
    cron = tmp_path / "cron"
    monkeypatch.setattr(B, "CRON_FILE", str(cron))
    B.install_cron()
    text = cron.read_text()
    assert "fetch -q origin main" in text
    assert "show origin/main:scripts/evidencebox/box.py" in text
    assert "show origin/main:scripts/evidencebox/novice_u2404.sh" in text


def test_task_closed_set_matches_the_rules_doc():
    doc = (ROOT / "docs" / "rules" / "ci" / "evidence-box.md").read_text(encoding="utf-8")
    line = next(ln for ln in doc.splitlines() if ln.startswith("- 任务闭集"))
    assert tuple(re.findall(r"`([a-z0-9-]+)`", line)) == B.TASKS


# --------------------------------------------------------------------------- 与 workflow 同源
WORKFLOW = (ROOT / ".github" / "workflows" / "compat-guardian.yml").read_text(encoding="utf-8")


def test_engine_cases_match_the_observation_job():
    m = re.search(r"run --suite compat_engine \\\s*\n\s*--case (\S+)", WORKFLOW)
    assert m, "observation job's engine --case not found"
    assert m.group(1) == B.ENGINE_CASES


def test_guardian_prepare_mirrors_the_workflow_install_steps():
    for line in B.GUARDIAN_PREPARE.strip().splitlines():
        step = line.replace(" -q", "").strip()
        assert step in WORKFLOW, step


def test_research_prepare_mirrors_the_s0_workflow():
    s0 = (ROOT / ".github" / "workflows" / "compat-research-s0.yml").read_text(encoding="utf-8")
    for needle in (
        "runtime_pins.py --format lines",
        "runtime_pins.py --include-corpus --format lines",
        "verify-environment --case",
        "verify-science-environment",
        "-m pip check",
        "texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science cm-super dvipng",
    ):
        assert needle in s0, needle
        assert needle in B.research_prepare("research_x", tex=True, source_package=True), needle


# --------------------------------------------------------------------------- 不进仓库的东西
def test_no_host_address_or_credentials_in_the_executor():
    for path in sorted(p for p in BOX_DIR.iterdir() if p.suffix in (".py", ".sh")):
        text = path.read_text(encoding="utf-8")
        ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) - {"127.0.0.1"}
        assert not ips, (path, ips)  # 主机地址只在维护者本机 ~/.ssh/config
        for word in ("GITHUB_TOKEN", "GH_TOKEN", "ghp_", "secrets.", "webhook", "cos.", "SecretId"):
            assert word not in text, (path, word)


# --------------------------------------------------------------------------- 失败要看得见（Codex #1682）
def test_research_matrix_runs_the_script_with_its_own_directory_importable(monkeypatch, tmp_path):
    """`-I` 隐含 `-P`：脚本目录不进 sys.path，同目录的 compat_corpus 导入失败（P1）。"""
    seen = []
    monkeypatch.setattr(B, "checkout", lambda sha: tmp_path)
    monkeypatch.setattr(
        B,
        "sh",
        lambda argv, **kw: seen.append(argv) or type("R", (), {"stdout": '{"include": []}'})(),
    )
    assert B.research_matrix(SHA) == []
    assert "-I" not in seen[0] and "-P" not in seen[0]


def test_research_matrix_really_imports_its_sibling_modules():
    """不打桩：照 box.py 的同一条 argv 在本仓库上真跑一次 matrix。"""
    import subprocess

    out = subprocess.run(
        [sys.executable, "scripts/ci/compat_guardian_research.py", "matrix"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert B.json.loads(out.stdout)["include"]


def test_guarded_records_any_failure_instead_of_raising():
    import subprocess

    def boom():
        raise subprocess.CalledProcessError(1, ["python", "x"])

    assert B.guarded(boom).startswith("error: CalledProcessError")
    assert B.guarded(lambda: (_ for _ in ()).throw(SystemExit("prep failed"))).startswith(
        "error: SystemExit"
    )


def test_nightly_survives_a_failing_s0_matrix_and_still_cleans_and_reports(monkeypatch, tmp_path):
    import subprocess

    calls = []
    monkeypatch.setattr(B, "RUNS", tmp_path)
    monkeypatch.setattr(B, "resolve_main_commit", lambda sha: SHA)
    monkeypatch.setattr(B, "task_guardian_suites", lambda sha: 0)
    monkeypatch.setattr(B, "task_guardian_explore", lambda *a: 1)

    def bad_matrix(sha):
        raise subprocess.CalledProcessError(1, ["python", "matrix"])

    monkeypatch.setattr(B, "research_matrix", bad_matrix)
    monkeypatch.setattr(B, "task_novice_u2404", lambda: calls.append("novice") or 0)
    monkeypatch.setattr(B, "clean", lambda keep: calls.append("clean") or 0)
    assert B.nightly() == 0
    assert "clean" in calls
    reports = list(tmp_path.glob("*-nightly.json"))
    assert len(reports) == 1
    data = B.json.loads(reports[0].read_text())
    assert data["research-s0"].startswith("error: CalledProcessError")


def _run_merge(tmp_path, triage_rc: int, summarize_rc: int) -> int:
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("needs a POSIX bash")
    out = tmp_path / "out"
    rep = out / "shard1" / "run" / "reports"
    rep.mkdir(parents=True)
    (rep / "explore-report.json").write_text("{}")
    stub = (
        'python() { case "$2" in triage) return %d;; summarize) return %d;; esac; return 0; }\n'
        % (triage_rc, summarize_rc)
    )
    script = stub + B.explore_merge_script(1).replace("/out", str(out))
    return subprocess.run(
        [bash, "-c", "set -uo pipefail; " + script], capture_output=True
    ).returncode


@pytest.mark.parametrize(
    ("triage_rc", "summarize_rc", "expected"), [(2, 0, 2), (2, 2, 2), (0, 2, 0), (0, 0, 0)]
)
def test_explore_merge_exit_code_is_triage_exit_code(tmp_path, triage_rc, summarize_rc, expected):
    """triage 拒绝合并（报告损坏 / 混 seed / 重复片）= 这次长跑没有有效证据，不许记成 0（P2）。"""
    assert _run_merge(tmp_path, triage_rc, summarize_rc) == expected


def test_novice_required_steps_fail_the_run():
    """必需步骤一律 need（失败计入退出码）；裸 step 只留给两处预期会失败的探测（P2）。"""
    text = (BOX_DIR / "novice_u2404.sh").read_text(encoding="utf-8")
    bare = re.findall(r"^\s*(?:if ! )?step ([a-z0-9-]+) ", text, re.M)
    assert sorted(bare) == ["pip-direct-pep668", "pipx-install-readme"]
    assert re.findall(r"^\s*need ([a-z0-9-]+) ", text, re.M), "no required steps found"
    tail = text[text.rindex("FAILED[@]") :]
    assert "exit 1" in tail and tail.index("exit 1") < tail.index("echo DONE")
