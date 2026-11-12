"""证据机执行器（ADR 0166，docs/rules/ci/evidence-box.md）的看护用例。

真机行为（Docker 隔离、镜像构建、夜跑）只在证据机上实测；这里钉住不依赖机器的契约：
执行容器的隔离参数、只跑 main、每个容器都硬关回传、与 workflow 同源的 case 集与安装步骤、
cron 执行 main 上的副本、仓库里没有主机地址与凭据。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import shutil
import subprocess
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
    # 不许直接 > 本机副本（取一半失败会把它截成空文件）；crontab 里的 % 必须转义
    assert f"> {B.ROOT}/box.py " not in text
    assert "%" not in text.replace("\\%", "")


@pytest.mark.parametrize(
    ("offset_h", "expected"),
    [(8, "30 2 * * *"), (0, "30 18 * * *"), (-5, "30 13 * * *")],
)
def test_cron_schedule_is_18_30_utc_in_the_hosts_local_time(offset_h, expected):
    """cron 按本机时区解释时刻；证据机是 Asia/Shanghai，直接写 `30 18` 会在 UTC 10:30 跑。"""
    assert B.cron_schedule(_dt.timedelta(hours=offset_h)) == expected


def _run_cron(tmp_path, monkeypatch, show_failures: int):
    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("needs a POSIX bash")
    root, bin_ = tmp_path / "root", tmp_path / "bin"
    root.mkdir()
    bin_.mkdir()
    (root / "box.py").write_text("OLD")
    (root / "novice_u2404.sh").write_text("OLD")
    monkeypatch.setattr(B, "ROOT", root)
    monkeypatch.setattr(B, "CONTROL", root / "control")
    stubs = {
        # show 前 N 次写出半截内容后失败（模拟 TLS 中断），之后正常
        "git": f"""n=$(cat {tmp_path}/n 2>/dev/null || echo 0)
case "$*" in *" show "*)
  n=$((n+1)); echo $n > {tmp_path}/n
  if [ $n -le {show_failures} ]; then echo -n PART; exit 128; fi
  echo NEW;;
esac""",
        "python3": f'echo "$@" >> {tmp_path}/python.calls',
        "sleep": "true",
    }
    for name, body in stubs.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:/usr/bin:/bin"}
    subprocess.run([bash, "-c", B.cron_command()], env=env, check=False, timeout=30)
    calls = tmp_path / "python.calls"
    return root, calls.read_text() if calls.exists() else ""


def test_cron_retries_a_dropped_fetch_then_runs_main_copy(tmp_path, monkeypatch):
    root, calls = _run_cron(tmp_path, monkeypatch, show_failures=1)
    assert (root / "box.py").read_text().strip() == "NEW"
    assert (root / "novice_u2404.sh").read_text().strip() == "NEW"
    assert calls.strip() == f"{root}/box.py nightly"
    assert not list(root.glob("*.new"))


def test_cron_never_truncates_or_runs_when_main_cannot_be_fetched(tmp_path, monkeypatch):
    root, calls = _run_cron(tmp_path, monkeypatch, show_failures=99)
    assert (root / "box.py").read_text() == "OLD"
    assert (root / "novice_u2404.sh").read_text() == "OLD"
    assert calls == ""
    assert "nightly skipped" in (root / "nightly.log").read_text()


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


def _run_research_dependency_install(tmp_path, failures: int):
    """Run the generated install command with a failing-then-successful Python stub."""
    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("needs a POSIX bash")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = bin_ / "python"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$INSTALL_LOG"\n'
        'n=$(wc -l < "$INSTALL_LOG"); [ "$n" -gt "$INSTALL_FAILURES" ]\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    sleep = bin_ / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    generated = B.research_prepare("research_x", tex=False, source_package=False).splitlines()
    command = next(line for line in generated if "-r tests/compat/research-environments/" in line)
    command = command.replace("/work/science-venv/bin/python", shlex.quote(str(stub)))
    env = {
        **os.environ,
        "PATH": f"{bin_}:/usr/bin:/bin",
        "INSTALL_LOG": str(tmp_path / "calls"),
        "INSTALL_FAILURES": str(failures),
    }
    proc = subprocess.run(
        [bash, "-c", "set -euo pipefail\n" + command + "\necho NEXT"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    return proc, calls


def test_research_dependency_install_retries_without_changing_requirements(tmp_path):
    tmp_path = tmp_path / "path with spaces"
    tmp_path.mkdir()
    proc, calls = _run_research_dependency_install(tmp_path, failures=2)
    assert proc.returncode == 0, proc.stderr
    assert "NEXT" in proc.stdout
    assert len(calls) == 3
    assert all(call == calls[0] for call in calls)
    assert (
        "-c /work/runtime-constraints.txt -r tests/compat/research-environments/research_x.txt"
        in calls[0]
    )


def test_research_dependency_install_still_fails_after_retries(tmp_path):
    proc, calls = _run_research_dependency_install(tmp_path, failures=99)
    assert proc.returncode != 0
    assert "NEXT" not in proc.stdout
    assert len(calls) == 3


def _run_research_fetch(tmp_path, fetch_failures: int):
    """真 bash 跑 research_prepare 里「放缓存 → 重试取回 → 存回」那几行（python 换成桩）。"""
    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("needs a POSIX bash")
    box, work, bin_ = tmp_path / "boxcache", tmp_path / "work", tmp_path / "bin"
    (box / "research-sources").mkdir(parents=True)
    (box / "research-sources" / "old.py").write_text("cached")
    bin_.mkdir()
    stub = bin_ / "python"
    stub.write_text(
        f"""#!/bin/sh
case "$1" in *mirror_fill.py) exit 7;; esac
n=$(cat {tmp_path}/n 2>/dev/null || echo 0); n=$((n+1)); echo $n > {tmp_path}/n
[ -f "$TAVOTTO_RESEARCH_CACHE/old.py" ] || exit 9
echo part > "$TAVOTTO_RESEARCH_CACHE/part$n.py"
[ $n -le {fetch_failures} ] && exit 1
echo new > "$TAVOTTO_RESEARCH_CACHE/new.py"
"""
    )
    stub.chmod(0o755)
    (bin_ / "sleep").write_text("#!/bin/sh\ntrue\n")
    (bin_ / "sleep").chmod(0o755)
    lines = B.research_prepare("research_x", tex=False, source_package=False).splitlines()
    end = next(i for i, ln in enumerate(lines) if ln.endswith('[ "$fetched" = 1 ]')) + 1
    script = "\n".join(["set -euo pipefail", *lines[:end], "echo FETCHED"])
    script = script.replace("/boxcache", str(box)).replace("/work", str(work))
    env = {**os.environ, "PATH": f"{bin_}:/usr/bin:/bin"}
    proc = subprocess.run(
        [bash, "-c", script], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    return proc, box / "research-sources"


def test_research_fetch_reuses_cache_retries_and_saves_back(tmp_path):
    proc, saved = _run_research_fetch(tmp_path, fetch_failures=2)
    assert proc.returncode == 0, proc.stderr
    assert "FETCHED" in proc.stdout
    assert (saved / "new.py").read_text().strip() == "new"
    assert (saved / "old.py").read_text() == "cached"
    assert (tmp_path / "n").read_text().strip() == "3"


def test_research_fetch_keeps_partial_progress_and_fails_after_40_rounds(tmp_path):
    proc, saved = _run_research_fetch(tmp_path, fetch_failures=99)
    assert proc.returncode != 0
    assert "FETCHED" not in proc.stdout
    assert (tmp_path / "n").read_text().strip() == "40"
    assert not (saved / "new.py").exists()
    # 每一轮取到的（核过的）文件都已存回，下一晚只补缺的
    assert (saved / "part1.py").exists() and (saved / "part40.py").exists()


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


def _prepare_commands(monkeypatch, tmp_path) -> str:
    """build_image 真正交给准备容器的那段脚本。"""
    seen: dict[str, str] = {}

    def fake_sh(argv, **_kw):
        if argv[:2] == ["docker", "run"]:
            seen["script"] = argv[-1]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(B, "sh", fake_sh)
    monkeypatch.setattr(B, "image_exists", lambda _tag: False)
    monkeypatch.setattr(B, "checkout", lambda _sha: tmp_path)
    monkeypatch.setattr(B, "FONT_CACHE", tmp_path / "fonts")
    monkeypatch.setattr(B, "RESEARCH_CACHE", tmp_path / "research")
    B.build_image("a" * 40, "guardian", "SCRIPT_BODY")
    return seen["script"]


def test_font_mirror_runs_after_the_cache_and_before_the_real_fetch(monkeypatch, tmp_path):
    script = _prepare_commands(monkeypatch, tmp_path)
    cached = script.index("/boxcache/fonts/archives/. build/fonts-cache/")
    mirror = script.index("timeout 900 python scripts/evidencebox/mirror_fill.py fonts || true")
    assert cached < mirror < script.index("SCRIPT_BODY")


def test_research_mirror_runs_after_the_cache_and_before_the_fetch_loop():
    script = B.research_prepare("research_x", tex=False, source_package=False)
    cached = script.index("cp -a /boxcache/research-sources/. /work/research-sources/")
    mirror = script.index(
        "timeout 900 python scripts/evidencebox/mirror_fill.py research --case research_x || true"
    )
    assert cached < mirror < script.index("fetched=0")


def test_a_failing_research_mirror_does_not_stop_the_fetch_loop(tmp_path):
    # _run_research_fetch 的桩让 mirror_fill 退出 7；set -e 下取回循环仍要跑到底
    proc, _saved = _run_research_fetch(tmp_path, fetch_failures=0)
    assert proc.returncode == 0, proc.stderr
    assert "FETCHED" in proc.stdout
