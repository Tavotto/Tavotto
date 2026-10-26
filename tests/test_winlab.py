"""Windows Lab 执行器（docs/rules/ci/windows-lab.md）的看护用例。

真机行为（Job Object、ForceCommand、代理连通）只在 Lab 上实测；这里钉住
不依赖真机的契约：任务闭集与参数审计、清理不跟随链接、代理失败不回落直连、
提交可信度、代理模板只绑回环、同源的散列钉子。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINLAB = ROOT / "scripts" / "winlab"
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(WINLAB))

import agent as A  # noqa: E402

import winlab as W  # noqa: E402

RUN = "20261024T120000Z-abc123"


# --------------------------------------------------------------------------- 任务闭集
def test_registered_fixed_test_lists_exist_in_repo():
    for task in A.TASKS.values():
        for sel in task.tests:
            assert (ROOT / sel.split("::")[0]).is_file(), (task.id, sel)


def test_every_task_states_claim_and_unverified():
    for task in A.TASKS.values():
        assert task.claim and task.unverified, task.id


def test_build_tasks_never_route_through_the_proxy():
    # 依赖下载、构建、普通测试只许直连：DMIT 是日常节点，大流量不许进代理
    for task in A.TASKS.values():
        if task.kind in ("pytest", "hosts"):
            assert task.networks == ("direct",), task.id


@pytest.mark.parametrize(
    "sel",
    [
        "-k",
        "--pdb",
        "tests/test_x.py --pdb",
        "tests/../setup.py",
        "tests/a/../../x.py",
        "src/tavotto/x.py",
        "C:/x/tests/a.py",
        "tests/test_x.py;whoami",
        "tests/test_x.py::t -x",
        "tests\\test_x.py",
    ],
)
def test_test_selectors_cannot_smuggle_options_or_escape(sel):
    err = A.validate_test_args(A.TASKS["pytest"], [sel], None, "direct")
    assert err and "selector" in err


def test_valid_selector_and_required_arguments():
    assert (
        A.validate_test_args(
            A.TASKS["pytest"], ["tests/test_windows_regressions.py::test_a[x-1]"], None, "direct"
        )
        is None
    )
    assert "requires --tests" in A.validate_test_args(A.TASKS["pytest"], [], None, "direct")
    assert "requires --shard" in A.validate_test_args(A.TASKS["pytest-shard"], [], None, "direct")
    assert "does not accept --tests" in A.validate_test_args(
        A.TASKS["render-roundtrip"], ["tests/test_a.py"], None, "direct"
    )
    assert "network" in A.validate_test_args(A.TASKS["pytest"], ["tests/test_a.py"], None, "trojan")
    for bad in ("0/4", "5/4", "1/", "a/b", "1/4 -x"):
        assert A.validate_test_args(A.TASKS["pytest-shard"], [], bad, "direct"), bad
    assert A.validate_test_args(A.TASKS["pytest-shard"], [], "2/4", "direct") is None


@pytest.mark.parametrize(
    "argv",
    [
        ["powershell"],
        ["cmd", "/c", "whoami"],
        ["test", "anything"],
        ["test", "pytest", "--tests", "tests/test_a.py", "--", "-x"],
        ["prepare", "--sha", "HEAD"],
        ["reports", "--id", "..\\..\\secrets\\sing-box"],
    ],
)
def test_agent_rejects_unregistered_commands(argv, tmp_path, capsys):
    rc = A.main(argv, lab=A.Lab(tmp_path))
    text = capsys.readouterr().out
    out = json.loads(text[text.index("{") :])
    assert rc != 0 and out["result"] == "error"


def test_ssh_entry_parses_only_ssh_original_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status")
    monkeypatch.setattr(sys, "argv", ["agent.py", "--ssh"])
    assert A.main(lab=A.Lab(tmp_path)) == 0
    assert '"lock": null' in capsys.readouterr().out


# --------------------------------------------------------------------------- 网络模式
def test_direct_mode_strips_every_proxy_variable():
    env = A.network_env(
        "direct", {"HTTPS_PROXY": "x", "http_proxy": "y", "ALL_PROXY": "z", "PATH": "p"}
    )
    assert env == {"PATH": "p"}


@pytest.mark.parametrize("mode,port", [("trojan", 7890), ("hysteria2", 7891)])
def test_proxy_mode_is_per_process_and_keeps_local_traffic_local(mode, port):
    env = A.network_env(mode, {"PATH": "p"})
    assert env["HTTPS_PROXY"] == f"http://127.0.0.1:{port}"
    for local in ("localhost", "127.0.0.1", "::1", "100.64.0.0/10", ".ts.net"):
        assert local in env["NO_PROXY"].split(",")


def test_unknown_mode_is_an_error():
    with pytest.raises(ValueError):
        A.network_env("auto", {})


def test_proxy_down_is_environment_unavailable_not_a_direct_fallback(monkeypatch):
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: False)

    def boom(*a, **k):
        raise AssertionError("must not send any request when the proxy entry is down")

    monkeypatch.setattr(A, "_timed_request", boom)
    monkeypatch.setattr(A, "egress_digest", boom)
    rec = A.probe_mode("trojan", "0" * 64)
    assert rec["result"] == "environment_unavailable"


def test_proxy_egress_must_be_the_dmit_node(monkeypatch):
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: True)
    monkeypatch.setattr(
        A, "egress_digest", lambda mode: {"ok": True, "sha256": "f" * 64, "ttfb_ms": 1}
    )
    assert A.probe_mode("hysteria2", "0" * 64)["result"] == "failed"
    assert A.probe_mode("hysteria2", None)["result"] == "environment_unavailable"


def _probe(monkeypatch, mode, outcome, stream_ok=True):
    """outcome: 目标名 -> 单次请求结果（对 3 个样本都一样）；其余目标一律通。"""
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: True)
    monkeypatch.setattr(
        A, "egress_digest", lambda m: {"ok": True, "sha256": "0" * 64, "ttfb_ms": 1}
    )
    ok = {"ok": True, "status": 200, "ttfb_ms": 5}
    by_url = {url: outcome.get(name, ok) for name, url in A.PROBE_TARGETS}
    monkeypatch.setattr(A, "_timed_request", lambda opener, url, *a, **k: by_url[url])
    monkeypatch.setattr(A, "_stream_probe", lambda m: {"ok": stream_ok})
    return A.probe_mode(mode, "0" * 64)


FAIL = {"ok": False, "error": "TimeoutError"}
TLS = {"ok": False, "error": "tls_verify_failed"}


@pytest.mark.parametrize("mode", ["trojan", "hysteria2"])
def test_proxy_mode_passes_when_everything_is_reachable(monkeypatch, mode):
    assert _probe(monkeypatch, mode, {})["result"] == "passed"


@pytest.mark.parametrize("mode", ["trojan", "hysteria2"])
@pytest.mark.parametrize("target", sorted(A.PROBE_MUST_REACH["trojan"]))
def test_proxy_mode_failed_when_a_required_target_is_unreachable(monkeypatch, mode, target):
    rec = _probe(monkeypatch, mode, {target: FAIL})
    assert rec["result"] == "failed" and target in rec["reason"]


@pytest.mark.parametrize("mode", ["trojan", "hysteria2"])
def test_proxy_mode_all_targets_zero_of_three_is_failed(monkeypatch, mode):
    rec = _probe(monkeypatch, mode, {n: FAIL for n, _ in A.PROBE_TARGETS})
    assert rec["result"] == "failed"


@pytest.mark.parametrize("mode", ["trojan", "hysteria2"])
def test_proxy_mode_stream_failure_is_failed(monkeypatch, mode):
    rec = _probe(monkeypatch, mode, {}, stream_ok=False)
    assert rec["result"] == "failed" and "stream" in rec["reason"]


@pytest.mark.parametrize("mode", ["direct", "trojan", "hysteria2"])
def test_tls_verify_failure_is_failed_in_every_mode(monkeypatch, mode):
    # chatgpt_web 直连时本就不要求可达，但证书校验失败仍是中间人信号
    rec = _probe(monkeypatch, mode, {"chatgpt_web": TLS})
    assert rec["result"] == "failed" and "tls_verify_failed" in rec["reason"]


def test_direct_mode_tolerates_blocked_overseas_targets_but_records_it(monkeypatch):
    rec = _probe(
        monkeypatch,
        "direct",
        {"anthropic_api": {"ok": True, "status": 403, "ttfb_ms": 9}, "openai_api": FAIL},
        stream_ok=False,
    )
    assert rec["result"] == "passed"
    assert rec["targets"]["anthropic_api"]["expected_unreachable"] is True
    assert rec["targets"]["openai_api"]["expected_unreachable"] is True
    assert "expected_unreachable" not in rec["targets"]["pypi_mirror"]


def test_direct_mode_needs_the_domestic_mirror(monkeypatch):
    rec = _probe(monkeypatch, "direct", {"pypi_mirror": FAIL})
    assert rec["result"] == "failed" and "pypi_mirror" in rec["reason"]


def test_probe_expectations_cover_every_mode_and_known_target():
    names = {n for n, _ in A.PROBE_TARGETS}
    assert set(A.PROBE_MUST_REACH) == set(A.NETWORK_MODES) == set(A.PROBE_STREAM_REQUIRED)
    assert all(v <= names for v in A.PROBE_MUST_REACH.values())


def test_one_unavailable_mode_never_aggregates_to_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        A,
        "probe_mode",
        lambda m, e: {
            "mode": m,
            "result": "passed" if m != "hysteria2" else "environment_unavailable",
        },
    )
    monkeypatch.setattr(A, "fail_closed_probe", lambda: {"result": "passed"})
    monkeypatch.setattr(A, "proxy_listeners", lambda: {})
    body = A.run_network_task(A.Lab(tmp_path), list(A.NETWORK_MODES))
    assert body["result"] == "environment_unavailable"


# --------------------------------------------------------------------------- 代理模板
def _template():
    return json.loads((WINLAB / "sing-box.template.json").read_text(encoding="utf-8"))


def test_template_listens_on_loopback_only_and_never_sets_system_proxy():
    t = _template()
    ports = {}
    for ib in t["inbounds"]:
        assert ib["listen"] == "127.0.0.1"
        assert ib["set_system_proxy"] is False
        ports[ib["tag"]] = ib["listen_port"]
    assert ports == {
        "in-trojan": A.PROXY_PORTS["trojan"],
        "in-hysteria2": A.PROXY_PORTS["hysteria2"],
    }


def test_template_has_no_direct_outbound_so_failures_cannot_fall_back():
    t = _template()
    assert "direct" not in {ob["type"] for ob in t["outbounds"]}
    assert t["route"]["final"] == "block"
    tls = [ob["tls"] for ob in t["outbounds"] if "tls" in ob]
    assert tls and all(x["enabled"] and not x.get("insecure") for x in tls)


def _secrets(obfs=True):
    h = {
        "server": "203.0.113.9",
        "server_port": 4443,
        "password": "p2",
        "server_name": "n.example",
        "alpn": ["h3"],
    }
    if obfs:
        h.update(obfs_type="salamander", obfs_password="o")
    return {
        "trojan": {
            "server": "203.0.113.9",
            "server_port": 443,
            "password": "p1",
            "server_name": "n.example",
        },
        "hysteria2": h,
    }


def test_render_fills_every_placeholder_with_typed_values():
    s = _secrets()
    cfg = W.render_proxy_config(s, W._sub_optional(_template(), s))
    assert "${" not in json.dumps(cfg)
    ports = {ob["tag"]: ob.get("server_port") for ob in cfg["outbounds"]}
    assert ports["trojan"] == 443 and ports["hysteria2"] == 4443


def test_render_drops_obfs_when_not_configured_and_rejects_missing_fields():
    s = _secrets(obfs=False)
    cfg = W.render_proxy_config(s, W._sub_optional(_template(), s))
    assert all("obfs" not in ob for ob in cfg["outbounds"])
    del s["trojan"]["password"]
    with pytest.raises(KeyError):
        W.render_proxy_config(s, W._sub_optional(_template(), s))


# --------------------------------------------------------------------------- 可信度
def test_trust_decision():
    me = "me@example.com"
    assert W.trust_decision("a", me, [], True)[0]
    assert W.trust_decision("a", me, [("a", me, me)], False)[0]
    assert not W.trust_decision("a", me, [], False)[0]
    assert not W.trust_decision("a", me, [("a", "other@x", me)], False)[0]
    assert not W.trust_decision("a", me, [("a", me, "bot@x")], False)[0]
    assert not W.trust_decision("a", me, [("a", me, me)] * 201, False)[0]


# --------------------------------------------------------------------------- 清理边界
def test_clean_refuses_anything_but_run_id_children(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    for bad in ("..", "x", "", "C:", "..\\secrets", RUN + "/../x"):
        assert not A.safe_remove_child(runs, bad)


def test_clean_never_follows_links_out_of_the_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    runs = tmp_path / "runs"
    victim = runs / RUN
    (victim / "nested").mkdir(parents=True)
    (victim / "nested" / "f.txt").write_text("x")
    try:
        os.symlink(outside, victim / "nested" / "escape", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    assert A.safe_remove_child(runs, RUN)
    assert not victim.exists()
    assert (outside / "keep.txt").read_text() == "precious"


def test_clean_refuses_when_runs_dir_itself_is_a_link(tmp_path):
    real = tmp_path / "real"
    (real / RUN).mkdir(parents=True)
    link = tmp_path / "runs"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    assert not A.safe_remove_child(link, RUN)
    assert (real / RUN).exists()


def test_clean_keeps_the_active_run(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    names = [f"20261024T12000{i}Z-abc12{i}" for i in range(3)]
    for n in names:
        (lab.runs / n).mkdir(parents=True)
    lab.state.mkdir()
    lab.lock_path.write_text(json.dumps({"pid": os.getpid(), "run_id": names[0]}))
    out = A.cmd_clean(lab, keep=0)
    assert out["removed"] == names[1:]
    assert (lab.runs / names[0]).exists()


# --------------------------------------------------------------------------- 并发
def test_single_job_lock_and_stale_lock_recovery(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    with A.job_lock(lab, RUN, "pytest"):
        with pytest.raises(A.LabBusy):
            with A.job_lock(lab, "other", "network"):
                pass
    assert not lab.lock_path.exists()
    lab.state.mkdir(exist_ok=True)
    lab.lock_path.write_text(json.dumps({"pid": 999999, "run_id": "dead"}))
    monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
    with A.job_lock(lab, RUN, "pytest"):
        assert json.loads(lab.lock_path.read_text())["run_id"] == RUN


def test_empty_fresh_lock_file_is_a_live_lock_not_a_stale_one(tmp_path):
    # 另一进程刚建好文件、还没写完记录：读到空文件不能当成持有者已退出
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    lab.lock_path.write_text("")
    with pytest.raises(A.LabBusy):
        with A.job_lock(lab, RUN, "pytest"):
            pass
    assert lab.lock_path.exists() and lab.lock_path.read_text() == ""
    assert A.cmd_stop(lab)["result"] == "error" and lab.lock_path.exists()
    assert A.cmd_clean(lab, keep=0)["result"] == "error"


def test_empty_old_lock_file_is_recoverable(tmp_path):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    lab.lock_path.write_text("{trunc")
    old = __import__("time").time() - 3600
    os.utime(lab.lock_path, (old, old))
    with A.job_lock(lab, RUN, "pytest"):
        assert json.loads(lab.lock_path.read_text())["run_id"] == RUN
    assert A.cmd_stop(lab) == {"result": "passed", "summary": "no active job"}


def test_lock_is_published_complete_never_empty(tmp_path, monkeypatch):
    # 发布瞬间锁文件就带完整记录（硬链接发布，而不是先建空文件再写）
    lab = A.Lab(tmp_path)
    seen = []
    real_link = os.link

    def spy(src, dst, *a, **k):
        real_link(src, dst, *a, **k)
        seen.append(json.loads(lab.lock_path.read_text(encoding="utf-8")))

    monkeypatch.setattr(A.os, "link", spy)
    with A.job_lock(lab, RUN, "pytest"):
        pass
    assert seen and seen[0]["run_id"] == RUN
    assert not list(lab.state.glob("*.tmp"))


def test_pytest_task_without_venv_is_environment_unavailable(tmp_path):
    body = A.run_pytest_task(A.Lab(tmp_path), A.TASKS["render-roundtrip"], tmp_path, [], None, {})
    assert body["result"] == "environment_unavailable"


# --------------------------------------------------------------------------- 服务器端边界 / 同源钉子
def test_force_command_block_locks_the_lab_key_to_the_agent():
    block = W.FORCE_BLOCK
    assert "ForceCommand C:\\TavottoLab\\tools\\winlab\\agent.cmd" in block
    for directive in (
        "AllowTcpForwarding no",
        "AllowAgentForwarding no",
        "PermitTTY no",
        "PermitTunnel no",
    ):
        assert directive in block
    assert " -I " in W.AGENT_CMD and "--ssh" in W.AGENT_CMD


def test_bootstrap_script_is_ascii_for_windows_powershell_51():
    data = (WINLAB / "bootstrap-admin.ps1").read_bytes()
    assert all(b < 128 for b in data)


def test_sing_box_hash_pinned_identically_on_both_sides():
    ps = (WINLAB / "bootstrap-admin.ps1").read_text(encoding="ascii")
    m = re.search(r"\$SingBoxSha256 = '([0-9A-F]{64})'", ps)
    assert m and m.group(1).lower() == W.SING_BOX_SHA256
    assert f"SingBox = '{W.SING_BOX_VERSION}'" in ps


def test_large_installers_never_use_the_proxy():
    ps = (WINLAB / "bootstrap-admin.ps1").read_text(encoding="ascii")
    assert "127.0.0.1:789" not in ps and "-Proxy" not in ps


def test_deployed_modules_cover_agent_verify_local_imports():
    # agent_verify 的同目录 import 都要随执行器部署，否则 Lab 上 agent 一启动就 ImportError
    src = (ROOT / "scripts" / "ci" / "agent_verify.py").read_text(encoding="utf-8")
    local = {
        m + ".py"
        for m in re.findall(r"^from (\w+) import", src, re.M)
        if (ROOT / "scripts" / "ci" / f"{m}.py").exists()
    }
    assert local | {"agent_verify.py"} <= set(W.DEPLOYED_CI_MODULES)


def test_deployed_files_alone_can_import_the_agent(tmp_path):
    # 结构保证：只把 admin_deploy_agent 实际部署的 .py 放进一个目录、隔离模式下 import agent 必须成功。
    # 上一条只认 `from X import`，漏掉 `import proctree` 这类写法（Lab 上 agent 启动即 ModuleNotFoundError）。
    import shutil
    import subprocess as sp

    for src in (
        WINLAB / "agent.py",
        *(ROOT / "scripts" / "ci" / n for n in W.DEPLOYED_CI_MODULES),
    ):
        shutil.copy(src, tmp_path / src.name)
    r = sp.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path[:0] = [sys.argv[1]]; import agent",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr


def test_lab_ssh_never_multiplexes():
    # Win32-OpenSSH + ForceCommand：复用连接上的后续会话拿到的是第一条命令的 SSH_ORIGINAL_COMMAND
    base = " ".join(W.SSH_BASE)
    assert "ControlMaster=no" in base and "ControlPath=none" in base


def test_ssh_drop_waits_for_the_report_instead_of_judging(monkeypatch):
    import subprocess as sp

    calls = []

    def fake_remote(argv, *, stdin=None, capture=False):
        calls.append(argv)
        if argv[0] == "test":
            return sp.CompletedProcess(
                argv,
                255,
                b"",
                b"WINLAB_RUN_ID " + RUN.encode() + b"\nTimeout, server not responding.",
            )
        if len(calls) == 2:
            return sp.CompletedProcess(argv, 255, b"", b"")  # 还连不上
        return sp.CompletedProcess(
            argv, 1, json.dumps({"run_id": RUN, "result": "failed"}).encode(), b""
        )

    monkeypatch.setattr(W, "remote", fake_remote)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    rc, rep = W.remote_json(["test", "pytest"])
    assert rep["result"] == "failed" and rc == 1
    assert calls[-1] == ["reports", "--id", RUN]


def test_ssh_drop_without_run_id_is_an_error_not_a_pass(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(
        W, "remote", lambda argv, **k: sp.CompletedProcess(argv, 255, b"", b"Connection reset")
    )
    rc, rep = W.remote_json(["status"])
    assert rc != 0 and rep["result"] == "error"


@pytest.mark.parametrize(
    "member", ["../evil.ttf", "a/../../evil.ttf", "/abs.ttf", "C:/x.ttf", "a\\b.ttf"]
)
def test_font_zip_cannot_escape_the_fonts_dir(tmp_path, member):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("noto/ok.otf", b"x")
        zf.writestr(member, b"evil")
    lab = A.Lab(tmp_path / "lab")
    lab.cache.mkdir(parents=True)
    out = A.receive_fonts(lab, buf.getvalue())
    if member == "a\\b.ttf" and os.sep == "\\":
        # Windows 上 zipfile 读写都把 os.sep 规范成 "/"：这个成员就是合法的 a/b.ttf，必须落在字体目录里面
        assert out["result"] == "passed"
        assert (lab.fonts / "a" / "b.ttf").read_bytes() == b"evil"
    else:
        assert out["result"] == "error"
        assert not lab.fonts.exists()
    assert not (tmp_path / "evil.ttf").exists()


def test_font_zip_replaces_the_fonts_dir(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("noto/a b.otf", b"x")
        zf.writestr("LICENSE.txt", b"ofl")
    lab = A.Lab(tmp_path)
    (lab.fonts / "stale").mkdir(parents=True)
    out = A.receive_fonts(lab, buf.getvalue())
    assert out == {"result": "passed", "summary": "fonts received", "files": 2}
    assert not (lab.fonts / "stale").exists()


def test_git_trusts_only_the_lab_repo_via_protected_env_config(tmp_path):
    env = A.base_env(A.Lab(tmp_path))
    assert env["GIT_CONFIG_COUNT"] == "1" and env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_0"] == (tmp_path / "repo").as_posix()


def test_failure_summary_strips_the_lab_root(tmp_path):
    junit = tmp_path / "j.xml"
    root = tmp_path / "TavottoLab"
    junit.write_text(
        '<testsuite tests="2" failures="1" errors="1">'
        f'<testcase classname="tests.test_a" name="t1"><failure message="boom at {root}/repo/x.py"/></testcase>'
        '<testcase classname="tests.test_a" name="t2"><error message="ImportError"/></testcase>'
        '<testcase classname="tests.test_a" name="ok"/></testsuite>',
        encoding="utf-8",
    )
    out = A.junit_failures(junit, root)
    assert [f["test"] for f in out] == ["tests.test_a::t1", "tests.test_a::t2"]
    assert str(root) not in out[0]["message"] and "<lab>" in out[0]["message"]


def test_lab_env_matches_ci_on_bytecode_and_encoding(tmp_path):
    # CI 两项都不设：Lab 的价值是用户默认态（写 .pyc、ANSI 代码页），不是 agent_verify 的隔离态
    env = A.base_env(A.Lab(tmp_path))
    assert "PYTHONDONTWRITEBYTECODE" not in env and "PYTHONUTF8" not in env


def test_releasing_never_deletes_another_jobs_lock(tmp_path):
    # 10-24 实测：误判持有者已退出 → 第二个任务接手 → 第一个结束时把第二个的锁也删了
    lab = A.Lab(tmp_path)
    with A.job_lock(lab, RUN, "pytest"):
        lab.lock_path.write_text(json.dumps({"pid": os.getpid(), "run_id": "someone-else"}))
    assert json.loads(lab.lock_path.read_text())["run_id"] == "someone-else"


def test_stop_keeps_the_lock_while_the_holder_is_alive(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    lab.lock_path.write_text(json.dumps({"pid": 4242, "run_id": RUN}))
    monkeypatch.setattr(A, "_terminate", lambda pid: None)
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    ticks = iter(range(0, 100, 5))
    monkeypatch.setattr(A.time, "monotonic", lambda: next(ticks))
    out = A.cmd_stop(lab)
    assert out["result"] == "error" and lab.lock_path.exists()


def test_stop_releases_the_lock_once_the_holder_is_gone(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    lab.lock_path.write_text(json.dumps({"pid": 4242, "run_id": RUN}))
    killed = []
    monkeypatch.setattr(A, "_terminate", killed.append)
    monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
    out = A.cmd_stop(lab)
    assert (
        out == {"result": "passed", "stopped": RUN}
        and killed == [4242]
        and not lab.lock_path.exists()
    )


def test_pytest_tasks_run_with_the_ci_hang_watchdog(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    py = lab.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    (lab.repo / "tests").mkdir(parents=True)
    (lab.repo / "tests" / "test_a.py").write_text("")
    seen = {}
    monkeypatch.setattr(
        A,
        "_run_logged",
        lambda argv, cwd, env, prefix, timeout, **k: (
            seen.update(argv=argv, env=env) or {"exit_code": 0}
        ),
    )
    A.run_pytest_task(lab, A.TASKS["pytest"], tmp_path, ["tests/test_a.py"], None, {})
    assert seen["env"]["TAVOTTO_TEST_HANG_SECONDS"] == "900"
    assert "-vv" in seen["argv"] and "-rs" in seen["argv"]


# --------------------------------------------------------------------------- hosts-live（L4/L5）
def _stream(*events):
    return "\n".join(json.dumps(e) for e in events)


def _use(i, name):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": i, "name": f"mcp__tavotto__{name}", "input": {}}]
        },
    }


def _res(i, text, err=False):
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": i,
                    "content": [{"type": "text", "text": text}],
                    "is_error": err,
                }
            ]
        },
    }


def test_live_task_is_direct_only_and_needs_a_secret():
    t = A.TASKS["hosts-live"]
    assert t.networks == ("direct",) and t.needs_model_conf
    assert any("Anthropic / OpenAI" in u for u in t.unverified)


def test_live_env_points_at_the_domestic_endpoint_and_silences_anthropic_traffic():
    env = A._host_model_env({"base_url": "https://api.deepseek.com/anthropic", "api_key": "k" * 20})
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert not any("PROXY" in k for k in env)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b'{"api_key": "short", "base_url": "https://x"}',
        b'{"api_key": "' + b"a" * 20 + b'", "base_url": "http://x"}',
        b'{"api_key": "has space in it here", "base_url": "https://x"}',
    ],
)
def test_bad_secrets_are_refused(raw):
    import io

    assert isinstance(A.read_model_conf(io.StringIO(raw.decode())), str)


H1 = "a" * 64
H2 = "b" * 64


def _replay2(sid, patch_hash, ok="true"):
    body = f'{{"ok": {ok}, "session_id": "{sid}"'
    if patch_hash:
        body += f', "patch_hash": "sha256:{patch_hash}"'
    return A.parse_stream(_stream(_use("6", "tavotto_verify_replay"), _res("6", body + "}")))


def _s1_with_hash(h):
    return A.parse_stream(
        _stream(
            _use("2", "tavotto_open_figure"),
            _res("2", '{"session_id": "s-0123456789ab"}'),
            _use("3", "tavotto_apply_overrides"),
            _res("3", '{"ok": true, "patch_hash": "sha256:' + h + '"}'),
            _use("5", "tavotto_verify_replay"),
            _res("5", '{"ok": true}'),
        )
    )


@pytest.mark.parametrize(
    "s2",
    [
        _replay2("s-0123456789ab", H2),  # ok:true，但恢复出来的修改不是第一轮那一组
        _replay2("s-ffffffffffff", H1),  # 恢复的是另一个会话
        _replay2("s-0123456789ab", None),  # 结果里没有 patch_hash：解析不到就是失败
    ],
    ids=["other-patch-hash", "other-session", "no-patch-hash"],
)
def test_fresh_restore_must_match_session_and_edits_not_just_ok(tmp_path, s2):
    pdf = tmp_path / "o.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    out = A.judge_live(_s1_with_hash(H1), s2, pdf, True, "s-0123456789ab")
    assert out["L5"] == "failed"
    assert out["checks"]["fresh_process_restored_same_edits"] is False
    assert out["checks"]["edits_in_session_journal"] is True


def test_fresh_restore_uses_the_last_successful_apply(tmp_path):
    pdf = tmp_path / "o.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    s1 = A.parse_stream(
        _stream(
            _use("2", "tavotto_open_figure"),
            _res("2", '{"session_id": "s-0123456789ab"}'),
            _use("3", "tavotto_apply_overrides"),
            _res("3", '{"ok": true, "patch_hash": "sha256:' + H2 + '"}'),
            _use("4", "tavotto_apply_overrides"),
            _res("4", '{"ok": true, "patch_hash": "sha256:' + H1 + '"}'),
            _use("5", "tavotto_verify_replay"),
            _res("5", '{"ok": true}'),
        )
    )
    sid = "s-0123456789ab"
    assert A.judge_live(s1, _replay2(sid, H1), pdf, True, sid)["L5"] == "passed"
    assert A.judge_live(s1, _replay2(sid, H2), pdf, True, sid)["L5"] == "failed"


def test_judge_live_requires_real_calls_pdf_journal_and_fresh_restore(tmp_path):
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    s1 = A.parse_stream(
        _stream(
            _use("1", "tavotto_health"),
            _res("1", "{}"),
            _use("2", "tavotto_open_figure"),
            _res("2", '{"session_id": "s-0123456789ab"}'),
            _use("3", "tavotto_apply_overrides"),
            _res("3", '{"ok": true, "patch_hash": "sha256:' + H1 + '"}'),
            _use("4", "tavotto_export"),
            _res("4", "{}"),
            _use("5", "tavotto_verify_replay"),
            _res("5", '{"ok": true, "divergence": []}'),
        )
    )
    s2 = _replay2("s-0123456789ab", H1)
    sid = "s-0123456789ab"
    assert A.opened_session_id(s1) == sid
    out = A.judge_live(s1, s2, pdf, True, sid)
    assert out["L4"] == "passed" and out["L5"] == "passed" and all(out["checks"].values())
    # 每一样缺了都让 L5 失败：日志里没修改、重开后重放不一致、PDF 不是 PDF、工具报错
    assert A.judge_live(s1, s2, pdf, False, sid)["L5"] == "failed"
    s2_bad = A.parse_stream(_stream(_use("6", "tavotto_verify_replay"), _res("6", '{"ok": false}')))
    assert A.judge_live(s1, s2_bad, pdf, True, sid)["L5"] == "failed"
    pdf.write_bytes(b"<html>")
    assert A.judge_live(s1, s2, pdf, True, sid)["L5"] == "failed"
    s1_err = A.parse_stream(_stream(_use("1", "tavotto_health"), _res("1", "boom", err=True)))
    assert A.judge_live(s1_err, s2, None, True, sid)["L4"] == "failed"


def test_journal_check_reads_the_real_record(tmp_path):
    d = tmp_path / "data" / "mcp-sessions"
    d.mkdir(parents=True)
    sid = "s-0123456789ab"
    (d / f"{sid}.json").write_text(
        json.dumps({"patches": [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3}]})
    )
    assert A.journal_has_edits(tmp_path / "data", sid)
    (d / f"{sid}.json").write_text(json.dumps({"patches": []}))
    assert not A.journal_has_edits(tmp_path / "data", sid)
    assert not A.journal_has_edits(tmp_path / "data", None)


def test_a_tool_use_without_any_result_is_not_a_success():
    s = A.parse_stream(_stream(_use("1", "tavotto_health")))
    assert A._ok_calls(s, "tavotto_health") == []


def test_secret_is_scrubbed_from_evidence(tmp_path):
    (tmp_path / "log.txt").write_bytes(b"token=sk-abcdefghijklmnop end")
    A._scrub(tmp_path, "sk-abcdefghijklmnop")
    assert (tmp_path / "log.txt").read_bytes() == b"token=<redacted> end"


def test_live_test_without_local_secret_file_stops_before_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "MODEL_CONF_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(
        W, "remote_json", lambda *a, **k: pytest.fail("must not reach the Lab without a secret")
    )
    assert W.main(["test", "hosts-live"]) == 3


def test_stream_events_with_string_messages_do_not_crash_the_parser():
    text = _stream(
        {"type": "system", "message": "init"}, {"type": "result", "result": "DONE"}, "not-a-dict"
    )
    assert A.parse_stream(text) == {"calls": [], "final": "DONE"}


def test_an_agent_crash_still_produces_an_error_report(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(A, "_run_body", boom)
    rep = A.run_task(A.Lab(tmp_path), A.TASKS["network"], [], None, "direct")
    assert rep["result"] == "error" and "kaput" in rep["summary"]
    assert (tmp_path / "reports" / f"{rep['run_id']}.json").exists()
    assert not (tmp_path / "state" / "job.lock").exists()


def _fake_body_env(monkeypatch):
    monkeypatch.setattr(A, "windows_facts", lambda: {})


def test_report_commit_is_the_one_under_test_even_if_head_moves_mid_run(tmp_path, monkeypatch):
    _fake_body_env(monkeypatch)
    head = ["aaaa111"]
    monkeypatch.setattr(A, "repo_head", lambda lab: head[0])

    def body(*a, **k):
        head[0] = "bbbb222"  # 别人在跑的当口切了提交
        return {"result": "passed", "summary": "x"}

    monkeypatch.setattr(A, "_run_body", body)
    rep = A.run_task(A.Lab(tmp_path), A.TASKS["network"], [], None, "direct")
    assert rep["commit"] == "aaaa111"
    on_disk = json.loads((tmp_path / "reports" / f"{rep['run_id']}.json").read_text("utf-8"))
    assert on_disk["commit"] == "aaaa111"


def test_report_is_written_while_the_run_still_holds_the_lock(tmp_path, monkeypatch):
    _fake_body_env(monkeypatch)
    monkeypatch.setattr(A, "repo_head", lambda lab: None)
    monkeypatch.setattr(A, "_run_body", lambda *a, **k: {"result": "passed", "summary": "x"})
    lab = A.Lab(tmp_path)
    held = []
    real = A._write_report

    def spy(lab_, run_dir, run_id, report):
        held.append((A.read_lock(lab_) or {}).get("run_id") == run_id)
        # 锁内 clean --keep 0 删不掉本 run
        assert A.cmd_clean(lab_, keep=0)["removed"] == []
        real(lab_, run_dir, run_id, report)

    monkeypatch.setattr(A, "_write_report", spy)
    rep = A.run_task(lab, A.TASKS["network"], [], None, "direct")
    assert held == [True]
    assert (lab.reports / f"{rep['run_id']}.json").exists()
    assert not lab.lock_path.exists()


def test_session_id_falls_back_to_other_results_and_the_final_line():
    s = A.parse_stream(
        _stream(
            _use("2", "tavotto_open_figure"),
            _res("2", "Error: result exceeds maximum allowed tokens. Output has been saved"),
            _use("3", "tavotto_apply_overrides"),
            _res("3", '{"ok":true,"session_id":"s-aaaaaaaaaaaa"}'),
        )
    )
    assert A.opened_session_id(s) == "s-aaaaaaaaaaaa"
    s = A.parse_stream(_stream({"type": "result", "result": "all good\nSESSION s-bbbbbbbbbbbb"}))
    assert A.opened_session_id(s) == "s-bbbbbbbbbbbb"


def test_reclaiming_never_takes_away_a_lock_published_after_the_stale_judgement(tmp_path):
    # 两个回收者都判了同一把陈旧锁：先到者回收并发布新锁后，后到者手里的「陈旧」判断已过期，不能动新锁
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    stale = {"pid": 999999, "run_id": "dead"}
    fresh = {"pid": os.getpid(), "run_id": "winner"}
    lab.lock_path.write_text(json.dumps(fresh))
    assert A._reclaim_stale(lab, stale) is False
    assert json.loads(lab.lock_path.read_text()) == fresh
    assert not list(lab.state.glob("*.stale"))


def test_reclaiming_the_judged_stale_lock_succeeds(tmp_path):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    stale = {"pid": 999999, "run_id": "dead"}
    lab.lock_path.write_text(json.dumps(stale))
    assert A._reclaim_stale(lab, stale) is True
    assert not lab.lock_path.exists() and not list(lab.state.glob("*.stale"))


# --------------------------------------------------------------------------- installer
@pytest.mark.parametrize(
    "name", ["..\\x.exe", "a/b.exe", "x.msi", "-x.exe", "x.exe;calc", "C:x.exe", ""]
)
def test_package_names_are_a_closed_shape(name):
    err = A.validate_test_args(A.TASKS["installer"], [], None, "direct", name or None)
    assert err


def test_installer_requires_a_package_and_other_tasks_refuse_one():
    assert "requires --package" in A.validate_test_args(A.TASKS["installer"], [], None, "direct")
    assert A.validate_test_args(A.TASKS["installer"], [], None, "direct", "a.exe", "b.exe") is None
    assert "does not accept --package" in A.validate_test_args(
        A.TASKS["render-roundtrip"], [], None, "direct", "a.exe"
    )


def test_staged_package_must_match_its_pinned_hash(tmp_path):
    lab = A.Lab(tmp_path)
    d = lab.cache / "packages"
    d.mkdir(parents=True)
    (d / "a.exe").write_bytes(b"MZ payload")
    assert A.package_path(lab, "a.exe")[1] == "package a.exe not staged"
    (d / "a.exe.sha256").write_text("0" * 64)
    assert "mismatch" in A.package_path(lab, "a.exe")[1]
    import hashlib

    (d / "a.exe.sha256").write_text(hashlib.sha256(b"MZ payload").hexdigest())
    assert A.package_path(lab, "a.exe") == (d / "a.exe", None)


def test_installer_check_is_ascii_and_mirrors_the_nightly_checks():
    ps = (WINLAB / "installer-check.ps1").read_bytes()
    assert all(b < 128 for b in ps)
    text = ps.decode()
    # Lab 按「父进程 + 可执行文件路径」认 sidecar：非管理员 SSH 会话读不到命令行（PS 7 的
    # CommandLine 也走 WMI）；nightly 按命令行里的 desktop-sidecar 认，同一件事两种取法
    for needle in (
        "runtime\\python.exe",
        "tavotto-workerd.exe",
        "GNU AFFERO",
        "$_.Path -ieq $exePath",
        "--expect-source bundled",
    ):
        assert needle in text, needle
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    for needle in (
        "runtime\\python.exe",
        "tavotto-workerd.exe",
        "GNU AFFERO",
        "desktop-sidecar",
        "--expect-source bundled",
    ):
        assert needle in nightly, (
            f"nightly no longer checks {needle}: update installer-check.ps1 to match"
        )


def test_deploy_ships_the_installer_check():
    import inspect

    assert "installer-check.ps1" in inspect.getsource(W.admin_deploy_agent)


def test_pytest_env_leaves_data_isolation_to_conftest_like_ci(tmp_path, monkeypatch):
    # CI 跑 pytest 不在外层设 TAVOTTO_DATA_DIR / MPLCONFIGDIR（tests/conftest.py 自己隔离）；Lab 同口径
    lab = A.Lab(tmp_path)
    py = lab.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    (lab.repo / "tests").mkdir(parents=True)
    (lab.repo / "tests" / "test_a.py").write_text("")
    seen = {}
    monkeypatch.setattr(
        A,
        "_run_logged",
        lambda argv, cwd, env, prefix, timeout, **k: seen.update(env=env) or {"exit_code": 0},
    )
    A.run_pytest_task(lab, A.TASKS["pytest"], tmp_path, ["tests/test_a.py"], None, {})
    assert "TAVOTTO_DATA_DIR" not in seen["env"] and "MPLCONFIGDIR" not in seen["env"]


# --------------------------------------------------------------------------- 交互会话执行器
import gui_host as G  # noqa: E402


def test_gui_bridge_without_an_interactive_session_is_environment_unavailable(
    tmp_path, monkeypatch
):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    monkeypatch.setattr(A, "ensure_gui_task", lambda lab: {"exit_code": 0})
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(A, "GUI_START_WAIT_S", 0)
    out = A.run_in_gui(lab, RUN, ["x"], tmp_path, {}, tmp_path / "p", 10)
    assert out["gui"] == "no_interactive_session"
    assert not (lab.state / "gui" / "request.json").exists()  # 没人接的请求不留着等下一次误跑


def _write_request(lab, run_id, argv, written=None, timeout=30):
    gui = lab.state / "gui"
    gui.mkdir(parents=True, exist_ok=True)
    (gui / "request.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "argv": argv,
                "cwd": str(lab.root),
                "env": dict(os.environ),
                "timeout": timeout,
                "out_prefix": str(lab.root / "out" / "p"),
                "written": A.time.time() if written is None else written,
            }
        )
    )


@pytest.mark.parametrize("run_id,age", [("../evil", 0), (RUN, 10_000)])
def test_gui_host_ignores_stale_or_malformed_requests(tmp_path, run_id, age):
    lab = A.Lab(tmp_path)
    _write_request(
        lab, run_id, [sys.executable, "-c", "open('ran','w')"], written=A.time.time() - age
    )
    assert G.serve(lab) == 0
    assert not (tmp_path / "ran").exists() and not list((lab.state / "gui").glob("*.result.json"))


def test_gui_host_runs_the_request_and_writes_the_result(tmp_path):
    lab = A.Lab(tmp_path)
    _write_request(lab, RUN, [sys.executable, "-c", "print('hello from the desktop')"])
    assert G.serve(lab) == 0
    res = json.loads((lab.state / "gui" / f"{RUN}.result.json").read_text())
    assert res["exit_code"] == 0 and res["timed_out"] is False
    assert "hello from the desktop" in (lab.root / "out" / "p.stdout.log").read_text()
    assert not (lab.state / "gui" / "request.json").exists()


def test_gui_host_honours_the_stop_flag(tmp_path):
    lab = A.Lab(tmp_path)
    _write_request(lab, RUN, [sys.executable, "-c", "import time; time.sleep(60)"])
    (lab.state / "gui" / f"{RUN}.stop").write_text("stop")
    G.serve(lab)
    res = json.loads((lab.state / "gui" / f"{RUN}.result.json").read_text())
    assert res["timed_out"] is True and res["exit_code"] is None


def test_desktop_result_from_session_zero_is_not_a_window_test(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    py = lab.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(A, "package_path", lambda lab, name: (tmp_path / "s.exe", None))
    monkeypatch.setattr(
        A, "ensure_msedgedriver", lambda lab, env: (tmp_path / "d.exe", "155.0.0.1")
    )
    monkeypatch.setattr(A, "uninstall_all", lambda: True)
    monkeypatch.setattr(
        A, "install_package", lambda s: {"ok": True, "shell": str(tmp_path / "Tavotto.exe")}
    )
    monkeypatch.setattr(A, "webview2_policy_ok", lambda shell: True)
    monkeypatch.setattr(
        A, "run_in_gui", lambda *a, **k: {"gui": "ran", "exit_code": 0, "session_id": 0}
    )
    out = A.run_desktop_window_task(lab, RUN, tmp_path, {}, "x.exe")
    assert out["result"] == "error" and "session 0" in out["summary"]


def test_stop_also_signals_the_desktop_half(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    lab.lock_path.write_text(json.dumps({"pid": 4242, "run_id": RUN}))
    monkeypatch.setattr(A, "_terminate", lambda pid: None)
    monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
    A.cmd_stop(lab)
    assert (lab.state / "gui" / f"{RUN}.stop").exists()


def test_gui_setup_keeps_the_password_out_of_the_registry():
    ps = W.GUI_SETUP_PS
    assert "LsaStorePrivateData" in ps and "Remove-ItemProperty $wl DefaultPassword" in ps
    assert "Set-ItemProperty $wl DefaultPassword" not in ps
    assert "'__PW__'" in ps  # 只在发送时替换，仓库里没有真值


# --------------------------------------------------------------------------- 环境矩阵 / 脚本语料
def test_corpus_modes_are_a_closed_set():
    assert A.validate_test_args(A.TASKS["corpus"], [], None, "direct", mode="smoke") is None
    assert A.validate_test_args(A.TASKS["corpus"], [], None, "direct", mode="all") is None
    assert "does not accept --mode" in A.validate_test_args(
        A.TASKS["corpus"], [], None, "direct", mode="--pdb"
    )
    assert "does not accept --mode" in A.validate_test_args(
        A.TASKS["pytest"], ["tests/test_a.py"], None, "direct", mode="smoke"
    )


def test_matrix_and_corpus_never_route_through_the_proxy():
    for tid in ("env-matrix", "corpus", "desktop-window", "installer"):
        assert A.TASKS[tid].networks == ("direct",), tid


def test_env_matrix_aggregates_unavailable_envs_honestly(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    py = lab.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    (lab.repo / "examples" / "figures").mkdir(parents=True)
    shell = tmp_path / "inst" / "Tavotto beta.exe"
    side = tmp_path / "inst" / "sidecar" / "Tavotto" / "Tavotto.exe"
    (side.parent / "_internal").mkdir(parents=True)
    shell.write_text("")
    side.write_text("")
    monkeypatch.setattr(A, "package_path", lambda lab, name: (tmp_path / "s.exe", None))
    monkeypatch.setattr(
        A,
        "prepare_envs",
        lambda lab, env, log: (
            {"cpython-3.13": tmp_path / "py.exe"},
            {"conda": "miniforge download failed"},
        ),
    )
    monkeypatch.setattr(A, "uninstall_all", lambda: True)
    monkeypatch.setattr(
        A, "install_package", lambda s: {"ok": True, "shell": str(shell), "version": "x"}
    )
    monkeypatch.setattr(
        A, "_run_logged", lambda argv, cwd, env, prefix, timeout, **k: {"exit_code": 0, "env": env}
    )
    out = A.run_env_matrix_task(lab, tmp_path / "run", {}, "x.exe")
    legs = {leg["leg"]: leg["result"] for leg in out["legs"]}
    assert legs == {
        "bundled": "passed",
        "cpython-3.13": "passed",
        "conda": "environment_unavailable",
    }
    assert out["result"] == "environment_unavailable"  # 一条腿没环境，整体就不能读成通过


def test_sidecar_is_found_by_layout_not_by_the_shell_name(tmp_path):
    shell = tmp_path / "Tavotto.exe"
    shell.write_text("")
    assert A._find_sidecar(shell) is None
    side = tmp_path / "sidecar" / "Tavotto" / "Tavotto.exe"
    (side.parent / "_internal").mkdir(parents=True)
    side.write_text("")
    assert A._find_sidecar(shell) == side


# --------------------------------------------------------------------------- MCP 记录器 + 多宿主


def test_recorder_passes_bytes_through_and_records_only_bounded_facts(tmp_path):
    import subprocess as sp

    server = tmp_path / "server.py"
    server.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m.get('method') == 'tools/list':\n"
        "        r = {'tools': [{'name': 'tavotto_open_figure'}, {'name': 'tavotto_health'}]}\n"
        "    else:\n"
        "        r = {'content': [{'type': 'text', 'text': json.dumps({'ok': True, 'session_id': 's-0123456789ab', 'patch_hash': 'sha256:ab', 'svg': '<svg>SECRET-PATH</svg>'})}], 'isError': False}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}) + '\\n'); sys.stdout.flush()\n",
        encoding="utf-8",
    )
    log = tmp_path / "rec.jsonl"
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "tavotto_verify_replay", "arguments": {"path": "C:/x"}},
        },
    ]
    stdin = "".join(json.dumps(r) + "\n" for r in reqs)
    out = sp.run(
        [
            sys.executable,
            str(WINLAB / "mcp_recorder.py"),
            str(log),
            "--",
            sys.executable,
            str(server),
        ],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    replies = [json.loads(x) for x in out.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]  # 宿主拿到的就是 server 原样的回复
    facts = A.parse_recorded(log)
    assert facts["listed_tools"] == ["tavotto_health", "tavotto_open_figure"]
    assert (
        facts["calls"][0]["name"] == "mcp__tavotto__tavotto_verify_replay"
        and not facts["calls"][0]["is_error"]
    )
    text = log.read_text(encoding="utf-8")
    assert "SECRET-PATH" not in text and "C:/x" not in text  # 参数与结果正文不进记录


def test_recorder_registers_request_before_forwarding_it(tmp_path):
    import io

    import mcp_recorder as R

    rec = R.Recorder(str(tmp_path / "rec.jsonl"))
    seen = []

    class Dst(io.BytesIO):
        def write(self, b):
            seen.append(1 in rec._pending)  # 转发那一刻 id 必须已登记，否则快回复会被丢
            return super().write(b)

        def close(self):
            pass

    line = (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n").encode()
    R._pump(io.BytesIO(line), Dst(), rec.from_host)
    assert seen == [True]


def test_recorded_split_separates_the_fresh_process(tmp_path):
    log = tmp_path / "r.jsonl"
    rows = [
        {
            "dir": "server",
            "method": "tools/call",
            "tool": "tavotto_open_figure",
            "error": False,
            "facts": {"session_id": "s-aaaaaaaaaaaa"},
        },
        {
            "dir": "server",
            "method": "tools/call",
            "tool": "tavotto_verify_replay",
            "error": False,
            "facts": {"ok": True},
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert len(A.parse_recorded(log)["calls"]) == 2
    assert [c["name"] for c in A.parse_recorded(log, 1)["calls"]] == [
        "mcp__tavotto__tavotto_verify_replay"
    ]


def test_live_hosts_are_a_closed_set_matching_the_modes():
    assert set(A.LIVE_HOSTS) == set(A.LIVE_HOST_IDS) == set(A.TASKS["hosts-live"].modes)
    assert A.validate_test_args(A.TASKS["hosts-live"], [], None, "direct", mode="codex-cli") is None
    assert A.validate_test_args(A.TASKS["hosts-live"], [], None, "direct", mode="anything")


def test_codex_config_uses_env_key_not_the_key_itself(tmp_path):
    conf = {"api_key": "sk-" + "x" * 30, "base_url": "https://api.deepseek.com/anthropic"}
    extra = A._codex_configure(
        A.Lab(tmp_path),
        tmp_path,
        tmp_path,
        tmp_path / "codex.cmd",
        ["py", "rec.py"],
        {"TAVOTTO_MCP_ROOTS": "C:/p"},
        conf,
        {},
    )
    toml = (tmp_path / "work" / "codex-home" / "config.toml").read_text(encoding="utf-8")
    assert conf["api_key"] not in toml and 'env_key = "WINLAB_MODEL_KEY"' in toml
    assert extra["WINLAB_MODEL_KEY"] == conf["api_key"] and 'wire_api = "responses"' in toml


@pytest.mark.parametrize("host", ["codebuddy", "minimax-code", "dsh"])
def test_new_host_configs_never_write_the_key_to_disk(tmp_path, monkeypatch, host):
    key = "sk-" + "k" * 30
    conf = {
        "api_key": key,
        "base_url": "https://api.deepseek.com/anthropic",
        "provider": "deepseek",
    }
    monkeypatch.setattr(A, "_run_logged", lambda *a, **k: {"exit_code": 0})
    proj = tmp_path / "proj"
    proj.mkdir()
    extra = A.LIVE_HOSTS[host].configure(
        A.Lab(tmp_path),
        tmp_path,
        proj,
        tmp_path / "x.cmd",
        ["py", "rec.py", "log", "--", "py", "server.py"],
        {"TAVOTTO_MCP_ROOTS": str(proj)},
        conf,
        {},
    )
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert key not in p.read_text(encoding="utf-8", errors="replace"), p
    assert key in extra.values()  # 只进宿主进程的环境
    argv = A.LIVE_HOSTS[host].argv(tmp_path / "x.cmd", "PROMPT", extra)
    assert argv[0].endswith("x.cmd") and "PROMPT" in argv


def test_codebuddy_pre_authorizes_the_deferred_tool_path(tmp_path):
    argv = A._codebuddy_argv(
        tmp_path / "cb.cmd", "P", {"WINLAB_MODEL": "m", "WINLAB_MCP_CONFIG": "c.json"}
    )
    i = argv.index("--allowedTools")
    assert {"mcp__tavotto", "ToolSearch", "DeferExecuteTool"} <= set(argv[i + 1 :])
    assert argv.index("P") < i  # 提示词不在可变长选项之后


def test_dsh_patch_uses_the_same_mcp_client_plugin_as_the_bundle(tmp_path):
    A._dsh_configure(
        A.Lab(tmp_path),
        tmp_path,
        tmp_path,
        tmp_path / "dsh.cmd",
        ["py", "rec.py"],
        {},
        {"api_key": "k" * 20},
        {},
    )
    patch = json.loads((tmp_path / "work" / "dsh-tavotto.patch.yml").read_text(encoding="utf-8"))
    bundle = (ROOT / "codex-plugin" / "dsh" / "cordis.patch.yml").read_text(encoding="utf-8")
    name = patch[0]["insert"][0]["name"]
    assert name == "@deepseek-ai/dsh-mcp-client" and name in bundle


def test_login_wall_is_blocked_not_failed():
    assert A.LOGIN_MARKERS.search("Error: Sign in to MiniMax to continue")
    assert A.LOGIN_MARKERS.search("请先登录后再使用")
    assert not A.LOGIN_MARKERS.search("opened the figure; logged 3 patches")


# --------------------------------------------------------------------------- 桌面宿主 L2
def test_desktop_hosts_are_a_closed_set_matching_the_modes():
    assert tuple(A.DESKTOP_HOSTS) == A.DESKTOP_HOST_IDS == A.TASKS["desktop-hosts"].modes
    assert A.TASKS["desktop-hosts"].networks == ("direct",)  # --network 不开放：线路由固定清单决定


def test_desktop_host_pins_are_https_sha256_and_route_domestic_cdns_direct():
    from urllib.parse import urlsplit

    for h in A.DESKTOP_HOSTS.values():
        u = urlsplit(h.url)
        assert u.scheme == "https" and u.hostname, h.id
        assert re.fullmatch(r"[0-9a-f]{64}", h.sha256), h.id
        assert h.network in A.NETWORK_MODES, h.id
        assert h.installer in ("inno", "nsis", "squirrel"), h.id
        assert h.version in h.url, h.id  # 改版本必须同时改 URL 与散列
        # 国内 CDN 一律直连：只有海外 CDN 才允许经 DMIT（用户 10-25 授权的范围）
        if u.hostname.endswith(".cn"):
            assert h.network == "direct", h.id


def test_pinned_download_refuses_when_the_proxy_is_down_instead_of_going_direct(
    tmp_path, monkeypatch
):
    lab = A.Lab(root=tmp_path)
    host = A.DESKTOP_HOSTS["cursor"]
    assert host.network != "direct"
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: False)
    opened = []
    monkeypatch.setattr(A, "_opener", lambda mode: opened.append(mode))
    path, info = A.fetch_pinned(lab, host)
    assert path is None and "not listening" in info["unavailable"]
    assert opened == []


class _Resp:
    def __init__(self, data: bytes):
        self._data = [data]

    def read(self, _n):
        return self._data.pop() if self._data else b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pinned_download_with_wrong_hash_leaves_nothing_behind(tmp_path, monkeypatch):
    lab = A.Lab(root=tmp_path)
    host = A.DESKTOP_HOSTS["vscode"]
    modes = []

    class Opener:
        def open(self, req, timeout):
            return _Resp(b"not the installer")

    monkeypatch.setattr(A, "_opener", lambda mode: modes.append(mode) or Opener())
    path, info = A.fetch_pinned(lab, host)
    assert path is None and "sha256_mismatch" in info
    assert modes == [host.network]
    d = lab.cache / "desktop-hosts" / host.id / host.version
    assert not any(d.iterdir())


def test_pinned_download_reuses_a_cached_file_only_when_the_hash_matches(tmp_path, monkeypatch):
    import hashlib

    lab = A.Lab(root=tmp_path)
    data = b"installer-bytes"
    host = A.DesktopHost(
        id="x",
        name="X",
        version="1",
        display_version="1",
        url="https://example.invalid/x/1/X-Setup.exe",
        sha256=hashlib.sha256(data).hexdigest(),
        network="direct",
        installer="nsis",
        display_re="^X",
        exe_name="X.exe",
    )
    d = lab.cache / "desktop-hosts" / "x" / "1"
    d.mkdir(parents=True)
    (d / "X-Setup.exe").write_bytes(data)
    monkeypatch.setattr(A, "_opener", lambda mode: pytest.fail("must not download"))
    path, info = A.fetch_pinned(lab, host)
    assert path == d / "X-Setup.exe" and info["cached"] is True


@pytest.mark.parametrize(
    "cmd,argv",
    [
        (
            '"C:\\Users\\u\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe"',
            ["C:\\Users\\u\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe"],
        ),
        (
            '"C:\\P\\Uninstall ZCode.exe" /currentuser /S',
            ["C:\\P\\Uninstall ZCode.exe", "/currentuser", "/S"],
        ),
        (
            "C:\\Users\\u\\AppData\\Local\\AnthropicClaude\\Update.exe --uninstall",
            ["C:\\Users\\u\\AppData\\Local\\AnthropicClaude\\Update.exe", "--uninstall"],
        ),
    ],
)
def test_uninstall_command_lines_split_like_windows(cmd, argv):
    assert A._command_argv(cmd) == argv


def test_window_probe_only_counts_processes_inside_the_install_dir():
    import window_probe as P

    root = (
        Path("C:/Users/u/AppData/Local/Programs/cursor") if os.name == "nt" else Path("/opt/cursor")
    )
    assert P.under(str(root / "Cursor.exe"), root)
    assert P.under(str(root / "resources" / "app" / "x.exe"), root)
    assert not P.under(str(root.parent / "cursor-other" / "Cursor.exe"), root)
    assert not P.under(None, root)


def test_window_probe_is_deployed_with_the_agent():
    src = (WINLAB / "winlab.py").read_text(encoding="utf-8")
    assert 'HERE / "window_probe.py"' in src


@pytest.mark.parametrize(
    ("name", "plane"),
    [
        ("conda-prefix", "conda-fallback"),
        ("conda-named", "conda-fallback"),
        ("bundled", "workerd"),
        ("cpython-3.13", "workerd"),
        ("uv-3.12", "workerd"),
    ],
)
def test_matrix_leg_control_plane_expectation(name, plane):
    # workerd 按设计不接需要 Conda 激活的启动（回退到 Python 渲染池）；其余腿必须真走 workerd
    argv = A.matrix_leg_argv(Path("py"), Path("side.exe"), name, "env_override", "figs", ["--x"])
    assert argv[argv.index("--expect-control-plane") + 1] == plane
    assert argv[-1] == "--x"


def test_host_exe_is_found_next_to_the_icon_or_uninstaller_when_install_location_is_empty(tmp_path):
    # ZCode（electron-builder NSIS）10-25 实测：InstallLocation 空、DisplayIcon 指向 uninstallerIcon.ico
    root = tmp_path / "Programs" / "ZCode"
    root.mkdir(parents=True)
    (root / "ZCode.exe").write_bytes(b"")
    host = A.DESKTOP_HOSTS["zcode"]
    by_icon = {"DisplayIcon": str(root / "uninstallerIcon.ico"), "InstallLocation": ""}
    by_uninstaller = {"UninstallString": f'"{root / "Uninstall ZCode.exe"}" /currentuser'}
    assert A._host_exe(host, by_icon) == root / "ZCode.exe"
    assert A._host_exe(host, by_uninstaller) == root / "ZCode.exe"
    assert A._host_exe(host, {"DisplayIcon": str(tmp_path / "x.ico")}) is None


def test_every_desktop_host_pins_a_display_version():
    assert all(h.display_version for h in A.DESKTOP_HOSTS.values())


class _FakeDesktopLab:
    """run_desktop_hosts_task 的纯逻辑夹具：登记表、安装器、GUI 桥全部假的。"""

    def __init__(self, monkeypatch, tmp_path, *, entries, installer_exit=0, window=True):
        host = A.DESKTOP_HOSTS["zcode"]
        self.host = host
        self.version = host.display_version  # 安装器登记的版本
        self.entries = list(entries)  # 当前登记项；卸载按 uninstall_ok 决定是否清空
        self.stuck = False  # 为真：卸载器跑完登记项仍在
        self.installer_exit = installer_exit
        root = tmp_path / "ZCode"
        root.mkdir()
        (root / host.exe_name).write_bytes(b"")
        self.root = root
        setup = tmp_path / "setup.exe"
        setup.write_bytes(b"")
        lab = type("L", (), {})()
        lab.venv_python = lambda: tmp_path / "py"
        (tmp_path / "py").write_bytes(b"")
        self.lab = lab
        monkeypatch.setattr(A, "fetch_pinned", lambda lab, host: (setup, {}))
        monkeypatch.setattr(A, "authenticode", lambda p: {"status": "Valid", "org": "x"})
        monkeypatch.setattr(A, "_uninstall_entries", lambda rx: list(self.entries))
        monkeypatch.setattr(A, "_run_until", self._run_until)

        def gui(lab, run_id, argv, work, env, log, timeout):
            out = Path(argv[argv.index("--out") + 1])
            payload = {"window": {"title": "w"}, "seconds_to_window": 1.0} if window else {}
            out.write_text(json.dumps(payload), encoding="utf-8")
            return {"gui": "ran", "session_id": 1}

        monkeypatch.setattr(A, "run_in_gui", gui)
        self.tmp = tmp_path

    def _run_until(self, argv, cwd, env, log, done, timeout):
        if Path(argv[0]).name == "setup.exe":
            if self.installer_exit == 0:
                self.entries.append(self._entry(self.version))
            else:  # 安装器失败，登记表里却留着旧版本
                self.entries.append(_stale(self.host, self.tmp))
            done()  # 真实的 _run_until 在主进程退出后轮询它
            return {"exit_code": self.installer_exit, "seconds": 0.1}
        if not self.stuck:
            self.entries.clear()
        return {"exit_code": 0, "seconds": 0.1}

    def _entry(self, version):
        return {
            "key": "k",
            "DisplayName": "ZCode",
            "DisplayVersion": version,
            "InstallLocation": str(self.root),
            "UninstallString": f'"{self.root / "Uninstall ZCode.exe"}" /currentuser',
        }

    def run(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        return A.run_desktop_hosts_task(self.lab, "rid", run_dir, {}, "zcode")


def _stale(host, tmp_path):
    root = tmp_path / "ZCode"
    return {
        "key": "old",
        "DisplayName": "ZCode",
        "DisplayVersion": "0.0.1",
        "InstallLocation": str(root),
        "UninstallString": f'"{root / "Uninstall ZCode.exe"}" /currentuser',
    }


def test_desktop_host_stale_install_that_cannot_be_removed_is_unavailable(monkeypatch, tmp_path):
    f = _FakeDesktopLab(monkeypatch, tmp_path, entries=[])
    f.entries.append(_stale(f.host, tmp_path))
    f.stuck = True
    r = f.run()
    assert r["result"] == "environment_unavailable"
    assert "旧安装卸不掉" in r["summary"]
    assert "install" not in r


def test_desktop_host_failed_installer_is_not_passed_by_a_stale_registration(monkeypatch, tmp_path):
    f = _FakeDesktopLab(monkeypatch, tmp_path, entries=[], installer_exit=1)
    r = f.run()
    assert r["result"] == "failed"
    assert r["levels"]["L2"] == "failed"


def test_desktop_host_wrong_registered_version_is_failed_with_both_versions(monkeypatch, tmp_path):
    f = _FakeDesktopLab(monkeypatch, tmp_path, entries=[])
    f.version = "9.9.9"
    r = f.run()
    assert r["result"] == "failed"
    assert "9.9.9" in r["summary"] and f.host.display_version in r["summary"]


def test_desktop_host_passed_window_with_failed_final_uninstall_is_error(monkeypatch, tmp_path):
    f = _FakeDesktopLab(monkeypatch, tmp_path, entries=[])
    orig = f._run_until

    def run_until(argv, cwd, env, log, done, timeout):
        r = orig(argv, cwd, env, log, done, timeout)
        f.stuck = Path(argv[0]).name == "setup.exe"  # 安装之后的卸载都卸不掉
        return r

    monkeypatch.setattr(A, "_run_until", run_until)
    r = f.run()
    assert r["levels"]["L2"] == "passed"
    assert r["result"] == "error"
    assert "L2 通过但清理失败" in r["summary"]


@pytest.mark.parametrize(
    "bad_step", [{"exit_code": None, "timed_out": True}, {"exit_code": 1, "seconds": 0.1}]
)
def test_desktop_host_final_uninstall_step_failure_is_error_even_if_entries_are_gone(
    monkeypatch, tmp_path, bad_step
):
    f = _FakeDesktopLab(monkeypatch, tmp_path, entries=[])
    orig = f._run_until

    def run_until(argv, cwd, env, log, done, timeout):
        r = orig(argv, cwd, env, log, done, timeout)  # 登记项照常被清空
        return r if Path(argv[0]).name == "setup.exe" else dict(bad_step)

    monkeypatch.setattr(A, "_run_until", run_until)
    r = f.run()
    assert r["levels"]["L2"] == "passed"
    assert r["uninstall"]["ok"] is False
    assert r["result"] == "error"


def test_desktop_host_clean_pass_stays_passed(monkeypatch, tmp_path):
    r = _FakeDesktopLab(monkeypatch, tmp_path, entries=[]).run()
    assert r["result"] == "passed", r


def test_installer_that_exits_nonzero_returns_at_once_instead_of_waiting_for_the_timeout(tmp_path):
    t0 = __import__("time").monotonic()
    step = A._run_until(
        [sys.executable, "-c", "raise SystemExit(3)"],
        tmp_path,
        dict(os.environ),
        tmp_path / "x",
        lambda: False,
        60,
    )
    assert step["exit_code"] == 3 and "timed_out" not in step
    assert __import__("time").monotonic() - t0 < 20


# --------------------------------------------------------------------------- 桌面宿主 L3–L5（desktop-live）
import desktop_driver as D  # noqa: E402


def test_desktop_live_modes_are_a_closed_set_of_host_extension_cells():
    t = A.TASKS["desktop-live"]
    assert t.modes == A.DESKTOP_LIVE_MODES == tuple(A.DESKTOP_LIVE)
    assert t.networks == ("direct",) and t.needs_model_conf  # --network 不开放；要一次性模型凭据
    for cell in A.DESKTOP_LIVE.values():
        assert cell.host in A.DESKTOP_HOSTS
        assert (
            cell.extension in A.DESKTOP_EXTENSIONS
        ) != cell.native  # 要么装扩展，要么用宿主自己的 Agent
    assert A.validate_test_args(t, [], None, "direct", mode="vscode-claude") is None
    for ok in ("cursor-claude", "trae-claude"):
        assert A.validate_test_args(t, [], None, "direct", mode=ok) is None
    for bad in ("vscode", "vscode-codex", "workbuddy-claude", "../x"):
        assert A.validate_test_args(t, [], None, "direct", mode=bad)
    assert A.validate_test_args(t, [], None, "trojan", mode="vscode-claude")


def test_extension_pin_is_exact_version_sha256_and_goes_through_the_proxy():
    from urllib.parse import urlsplit

    for e in A.DESKTOP_EXTENSIONS.values():
        u = urlsplit(e.url)
        assert u.scheme == "https" and u.hostname == "open-vsx.org", e.id
        assert re.fullmatch(r"[0-9a-f]{64}", e.sha256), e.id
        assert e.version in e.url and e.version in e.dir_name, e.id
        assert e.network == "trojan" and 0 < e.max_bytes <= 500 * 1024**2, e.id
        assert e.id == e.id.lower()


def test_extension_download_refuses_when_the_proxy_is_down_instead_of_going_direct(
    tmp_path, monkeypatch
):
    ext = A.DESKTOP_EXTENSIONS["anthropic.claude-code"]
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: False)
    monkeypatch.setattr(A, "_opener", lambda mode: pytest.fail("must not open any connection"))
    path, info = A.fetch_extension(A.Lab(root=tmp_path), ext)
    assert path is None and "not listening" in info["unavailable"]


def test_extension_download_with_wrong_hash_deletes_it(tmp_path, monkeypatch):
    lab = A.Lab(root=tmp_path)
    ext = A.DESKTOP_EXTENSIONS["anthropic.claude-code"]
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: True)
    modes = []

    class Opener:
        def open(self, req, timeout):
            return _Resp(b"not the vsix")

    monkeypatch.setattr(A, "_opener", lambda mode, **kw: modes.append(mode) or Opener())
    path, info = A.fetch_extension(lab, ext)
    assert path is None and "sha256_mismatch" in info and modes == ["trojan"]
    d = lab.cache / "desktop-extensions" / ext.id / ext.version
    assert not any(d.iterdir())  # 散列不符：连 .part 都不留


def test_pinned_download_stops_at_the_size_cap_and_leaves_nothing(tmp_path, monkeypatch):
    class Opener:
        def open(self, req, timeout):
            return _Resp(b"0123456789")

    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: True)
    monkeypatch.setattr(A, "_opener", lambda mode: Opener())
    d = tmp_path / "c"
    path, info = A._fetch_pinned_file(d, "https://open-vsx.org/x.vsix", "0" * 64, "trojan", 4)
    assert path is None and "larger than 4 bytes" in info["unavailable"]
    assert not any(d.iterdir())


def test_extension_cache_is_reused_only_when_the_hash_matches(tmp_path, monkeypatch):
    import hashlib

    data = b"vsix-bytes"
    d = tmp_path / "c"
    d.mkdir()
    (d / "x.vsix").write_bytes(data)
    monkeypatch.setattr(A, "_opener", lambda mode: pytest.fail("must not download"))
    path, info = A._fetch_pinned_file(
        d, "https://open-vsx.org/x.vsix", hashlib.sha256(data).hexdigest(), "trojan", 10**6
    )
    assert path == d / "x.vsix" and info["cached"] is True


def test_secret_env_split_moves_by_value_not_by_name():
    key = "sk-" + "s" * 30
    pub, sec = A.split_secret_env(
        {"ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_BASE_URL": "https://x", "ODD": f"a{key}b"}, [key]
    )
    assert sec == {"ANTHROPIC_AUTH_TOKEN": key, "ODD": f"a{key}b"}
    assert pub == {"ANTHROPIC_BASE_URL": "https://x"}
    assert A.split_secret_env({"A": "b"}, [""]) == ({"A": "b"}, {})  # 空密钥不把什么都当密钥


def test_request_json_never_carries_the_secret_only_a_pipe_name(tmp_path, monkeypatch):
    key = "sk-" + "r" * 30
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    monkeypatch.setattr(A, "ensure_gui_task", lambda lab: {"exit_code": 0})
    monkeypatch.setattr(A, "GUI_START_WAIT_S", 0)
    seen = {}

    class FakePipe:
        name = "tavotto-winlab-" + "a" * 32
        delivered = False
        error = None

        def __init__(self, payload):
            seen["payload"] = payload

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(A, "_open_secret_pipe", FakePipe)

    def run(argv, **kw):  # schtasks /Run：这一刻 request.json 就在盘上，抓下来
        seen["request"] = (lab.state / "gui" / "request.json").read_text(encoding="utf-8")

    monkeypatch.setattr(A.subprocess, "run", run)
    public, secret = A.split_secret_env({"ANTHROPIC_AUTH_TOKEN": key, "OK": "1"}, [key])
    out = A.run_in_gui(lab, RUN, ["x"], tmp_path, public, tmp_path / "p", 10, secret_env=secret)
    assert out["gui"] == "no_interactive_session"
    assert key not in seen["request"] and json.loads(seen["request"])["env_pipe"] == FakePipe.name
    assert seen["payload"] == {"ANTHROPIC_AUTH_TOKEN": key} and seen["closed"] is True
    for p in tmp_path.rglob("*"):  # 整个 Lab 根下任何文件都没有它
        if p.is_file():
            assert key not in p.read_text(encoding="utf-8", errors="replace"), p


def test_run_in_gui_without_secrets_opens_no_pipe(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    lab.state.mkdir()
    monkeypatch.setattr(A, "ensure_gui_task", lambda lab: {"exit_code": 0})
    monkeypatch.setattr(A, "GUI_START_WAIT_S", 0)
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(A, "_open_secret_pipe", lambda p: pytest.fail("no secrets, no pipe"))
    assert A.run_in_gui(lab, RUN, ["x"], tmp_path, {}, tmp_path / "p", 10)["gui"]


def test_gui_host_merges_the_pipe_secret_into_the_child_env_only(tmp_path, monkeypatch):
    key = "sk-" + "p" * 30
    lab = A.Lab(tmp_path)
    pipe = tmp_path / "pipe.json"
    pipe.write_text(json.dumps({"WINLAB_SECRET_PROBE": key}), encoding="utf-8")
    name = "tavotto-winlab-" + "b" * 32
    monkeypatch.setattr(A, "_pipe_path", lambda n: str(pipe) if n == name else "/nonexistent/x")
    _write_request(
        lab,
        RUN,
        [sys.executable, "-c", "import os; print(len(os.environ['WINLAB_SECRET_PROBE']))"],
    )
    req_path = lab.state / "gui" / "request.json"
    req = json.loads(req_path.read_text(encoding="utf-8"))
    req["env_pipe"] = name
    req_path.write_text(json.dumps(req), encoding="utf-8")
    assert G.serve(lab) == 0
    res = json.loads((lab.state / "gui" / f"{RUN}.result.json").read_text())
    assert res["exit_code"] == 0
    assert (lab.root / "out" / "p.stdout.log").read_text().strip() == str(len(key))


@pytest.mark.parametrize(
    "name", ["../evil", "tavotto-winlab-xyz", "C:\\x", "tavotto-winlab-" + "A" * 32]
)
def test_secret_pipe_name_must_have_the_agent_generated_shape(name):
    with pytest.raises(ValueError):
        A.read_secret_pipe(name)


def test_gui_host_with_an_unreadable_secret_pipe_does_not_run_the_command(tmp_path, monkeypatch):
    lab = A.Lab(tmp_path)
    monkeypatch.setattr(A, "_pipe_path", lambda n: "/nonexistent/pipe")
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    _write_request(lab, RUN, [sys.executable, "-c", "open('ran','w')"])
    req_path = lab.state / "gui" / "request.json"
    req = json.loads(req_path.read_text(encoding="utf-8"))
    req["env_pipe"] = "tavotto-winlab-" + "c" * 32
    req_path.write_text(json.dumps(req), encoding="utf-8")
    G.serve(lab)
    res = json.loads((lab.state / "gui" / f"{RUN}.result.json").read_text())
    assert res["exit_code"] is None and res.get("spawn_error") and not (tmp_path / "ran").exists()


# --- 录制器：首个 initialize 的 client 与进程链 --------------------------------
_HOST_ROOT = "C:/Users/u/AppData/Local/Programs/Microsoft VS Code"


def _init_line(name="claude-code", version="2.1.310"):
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": name, "version": version}, "secret": "ARGS"},
            }
        )
        + "\n"
    ).encode()


def _records(tmp_path, chain, host_root=_HOST_ROOT):
    import mcp_recorder as R

    log = tmp_path / "rec.jsonl"
    rec = R.Recorder(str(log), host_root, chain=lambda: chain)
    rec.from_host(_init_line())
    rec.from_host(_init_line("again", "9"))  # 只记首个
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


def test_recorder_records_client_and_ancestry_with_the_host_ancestor(tmp_path):
    chain = [
        ("python.exe", "C:/v/Scripts/python.exe"),
        ("claude.exe", "C:/run/ext/anthropic.claude-code/resources/native-binary/claude.exe"),
        ("Code.exe", _HOST_ROOT + "/Code.exe"),
        ("explorer.exe", "C:/Windows/explorer.exe"),
    ]
    rows = _records(tmp_path, chain)
    inits = [r for r in rows if r.get("method") == "initialize"]
    assert len(inits) == 2 and "ancestry" in inits[0] and "ancestry" not in inits[1]
    assert inits[0]["client"] == {"name": "claude-code", "version": "2.1.310"}
    assert inits[0]["ancestry"] == ["python.exe", "claude.exe", "Code.exe", "explorer.exe"]
    assert inits[0]["host_ancestor"].endswith("Code.exe")
    assert "ARGS" not in json.dumps(rows)  # 只记映像名与那一条路径，不记参数
    p = A.host_provenance(tmp_path / "rec.jsonl")
    assert p["from_host"] is True and p["host_ancestor"] == "Code.exe"
    assert p["client"]["name"] == "claude-code"


def test_recorder_without_a_host_ancestor_is_not_from_host(tmp_path):
    chain = [
        ("python.exe", "C:/v/python.exe"),
        ("claude.exe", "C:/npm/claude.exe"),
        ("cmd.exe", "C:/Windows/cmd.exe"),
        # 同名但不在安装目录下的程序（另一个 Code.exe）不算宿主
        ("Code.exe", "C:/Users/u/AppData/Local/Programs/Microsoft VS Code Insiders/Code.exe"),
    ]
    rows = _records(tmp_path, chain)
    init = next(r for r in rows if r.get("method") == "initialize")
    assert init["host_ancestor"] is None and init["ancestry"][1] == "claude.exe"
    p = A.host_provenance(tmp_path / "rec.jsonl")
    assert p["seen"] is True and p["from_host"] is False


def test_recorder_ancestry_is_bounded_and_survives_a_failing_chain_provider(tmp_path):
    import mcp_recorder as R

    long = [(f"p{i}.exe", f"C:/x/p{i}.exe") for i in range(30)]
    assert len(R.provenance(long, None)["ancestry"]) == R.MAX_RECORDED_NAMES
    assert "host_ancestor" not in R.provenance(long, None)  # 没给 --host-root：不判
    assert R._under("/a/b/Code.exe", "/a/b") is True
    assert R._under("/a/bc/Code.exe", "/a/b") is False  # 前缀相同的兄弟目录不算
    log = tmp_path / "r.jsonl"

    def boom():
        raise RuntimeError("snapshot failed")

    rec = R.Recorder(str(log), "/a", chain=boom)
    rec.from_host(_init_line())  # 取不到进程链不许打断通信
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["method"] == "initialize" and row["ancestry"] == []
    assert A.host_provenance(log)["from_host"] is False


def test_recorder_cli_accepts_host_root_before_the_separator(tmp_path):
    import subprocess as sp

    server = tmp_path / "s.py"
    server.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    log = tmp_path / "rec.jsonl"
    out = sp.run(
        [
            sys.executable,
            str(WINLAB / "mcp_recorder.py"),
            str(log),
            "--host-root",
            "/opt/code",
            "--",
            sys.executable,
            str(server),
        ],
        input=_init_line().decode(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    init = next(r for r in rows if r.get("method") == "initialize")
    assert "host_ancestor" in init and init["client"]["name"] == "claude-code"


# --- L3 判据：流量必须来自宿主进程树 ------------------------------------------
def _desktop_judge_inputs(tmp_path):
    pdf = tmp_path / "o.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    sid = "s-0123456789ab"
    s1 = A.parse_stream(
        _stream(
            _use("1", "tavotto_health"),
            _res("1", "{}"),
            _use("2", "tavotto_open_figure"),
            _res("2", '{"session_id": "s-0123456789ab"}'),
            _use("3", "tavotto_apply_overrides"),
            _res("3", '{"ok": true, "patch_hash": "sha256:' + H1 + '"}'),
            _use("5", "tavotto_verify_replay"),
            _res("5", '{"ok": true}'),
        )
    )
    s1["listed_tools"] = ["tavotto_open_figure", "tavotto_health"]
    return s1, _replay2(sid, H1), pdf, sid


GOOD = {"seen": True, "from_host": True, "host_ancestor": "Code.exe"}
NO_HOST = {"seen": True, "from_host": False, "host_ancestor": None}


def test_desktop_judge_passes_when_the_cli_judge_passes_and_traffic_is_from_the_host(tmp_path):
    s1, s2, pdf, sid = _desktop_judge_inputs(tmp_path)
    base = A.judge_live(s1, s2, pdf, True, sid)
    out = A.judge_desktop_live(s1, s2, pdf, True, sid, GOOD, GOOD)
    assert (base["L4"], base["L5"]) == ("passed", "passed")
    assert (out["L3"], out["L4"], out["L5"]) == ("passed",) * 3
    assert out["checks"] == base["checks"]  # judge_live 的结论原样带出


@pytest.mark.parametrize("bad", [NO_HOST, {"seen": False, "from_host": False}])
def test_desktop_l3_fails_when_traffic_has_no_host_ancestor_and_drags_l4_l5_down(tmp_path, bad):
    s1, s2, pdf, sid = _desktop_judge_inputs(tmp_path)
    assert A.judge_live(s1, s2, pdf, True, sid)["L5"] == "passed"  # 单看 judge_live 本会过
    out = A.judge_desktop_live(s1, s2, pdf, True, sid, bad, GOOD)
    assert (out["L3"], out["L4"], out["L5"]) == ("failed",) * 3


def test_desktop_l5_fails_when_the_fresh_process_round_was_not_from_the_host(tmp_path):
    s1, s2, pdf, sid = _desktop_judge_inputs(tmp_path)
    out = A.judge_desktop_live(s1, s2, pdf, True, sid, GOOD, NO_HOST)
    assert (out["L3"], out["L4"], out["L5"]) == ("passed", "passed", "failed")


def test_desktop_l3_still_needs_the_tools_list(tmp_path):
    s1, s2, pdf, sid = _desktop_judge_inputs(tmp_path)
    s1["listed_tools"] = []
    assert A.judge_desktop_live(s1, s2, pdf, True, sid, GOOD, GOOD)["L3"] == "failed"


def test_desktop_judge_keeps_judge_live_failures(tmp_path):
    s1, s2, pdf, sid = _desktop_judge_inputs(tmp_path)
    assert A.judge_desktop_live(s1, s2, pdf, False, sid, GOOD, GOOD)["L5"] == "failed"
    pdf.write_bytes(b"<html>")
    assert A.judge_desktop_live(s1, s2, pdf, True, sid, GOOD, GOOD)["L5"] == "failed"


def test_host_provenance_only_reads_the_requested_round(tmp_path):
    log = tmp_path / "r.jsonl"
    rows = [
        {
            "dir": "host",
            "method": "initialize",
            "ancestry": ["a"],
            "host_ancestor": "C:/h/Code.exe",
        },
        {"dir": "host", "method": "initialize", "ancestry": ["b"], "host_ancestor": None},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert A.host_provenance(log, 0, 1)["from_host"] is True
    assert A.host_provenance(log, 1)["from_host"] is False
    assert A.host_provenance(log, 2)["seen"] is False


# --- 驱动器与配置 --------------------------------------------------------------
def test_driver_debug_port_is_loopback_random_and_never_widened():
    assert all(1024 < D.free_port() < 65536 for _ in range(3))
    assert D.LOOPBACK == "127.0.0.1"
    args = D.code_args("C:/u", "C:/e", "C:/w", 40123)
    assert "--remote-debugging-port=40123" in args
    assert not any(a.startswith("--remote-debugging-address") for a in args)
    assert not any("0.0.0.0" in a for a in args)
    assert args[-1] == "C:/w" and "--disable-workspace-trust" in args
    assert not any(re.search(r"token|secret|key", a, re.I) for a in args)
    uri = D.uri_open_args("C:/u", "C:/e")
    assert "--open-url" in uri and uri[-1].startswith("vscode://anthropic.claude-code/open")
    assert not any(a.startswith("--remote-debugging") for a in uri)


def test_driver_websocket_client_follows_rfc6455():
    assert D.ws_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="  # RFC §1.3
    for n in (5, 200, 70000):
        payload = (bytes(range(256)) * (n // 256 + 1))[:n]
        frame = D.encode_frame(payload, 0x1, mask_key=b"\x01\x02\x03\x04")
        assert frame[1] & 0x80  # 客户端帧必须掩码
        if n < 126:
            head = bytes([0x81, n])
        elif n < 65536:
            head = bytes([0x81, 126]) + n.to_bytes(2, "big")
        else:
            head = bytes([0x81, 127]) + n.to_bytes(8, "big")
        # 服务器帧不掩码
        assert D.decode_frame(head + payload) == (0x1, True, payload, len(head) + n)
        assert D.decode_frame((head + payload)[:-1]) is None  # 半帧：留着等
    key = b"\xaa\xbb\xcc\xdd"
    masked = D.encode_frame(b"hello", 0x1, mask_key=key)
    assert masked[2:6] == key
    assert bytes(b ^ key[i % 4] for i, b in enumerate(masked[6:])) == b"hello"


def test_driver_argv_has_only_the_registered_flags_and_no_credentials(tmp_path):
    argv = A.driver_argv(
        Path("py"),
        Path("desktop_driver.py"),
        exe="C:/vs/Code.exe",
        root="C:/vs",
        profile=tmp_path / "profile",
        ext_dir=tmp_path / "ext",
        workspace=tmp_path / "proj",
        prompt_file=tmp_path / "prompt-1.txt",
        rec_log=tmp_path / "rec.jsonl",
        out_dir=tmp_path / "ev",
        label="round1",
    )
    assert argv[:3] == ["py", "-I", "desktop_driver.py"]
    flags = [a for a in argv if a.startswith("--")]
    assert len(flags) == len(set(flags))
    assert "--remote-debugging-port" not in " ".join(argv)  # 端口由驱动器自己随机选
    assert not any(re.search(r"sk-|token|api[_-]?key", a, re.I) for a in argv)
    # 驱动器真能解析这些 flag（agent 与 driver 两侧参数名同源）
    src = (WINLAB / "desktop_driver.py").read_text(encoding="utf-8")
    for f in flags:
        assert f'"{f}"' in src, f


def test_vscode_settings_and_claude_config_carry_no_secret_and_grant_only_tavotto(tmp_path):
    settings = json.dumps(A.vscode_user_settings())
    assert not re.search(r"token|secret|api[_-]?key|sk-|password", settings, re.I)
    assert json.loads(settings)["claudeCode.disableLoginPrompt"] is True
    cfg, proj = tmp_path / "cfg", tmp_path / "proj"
    cfg.mkdir()
    (cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"tavotto": {}}}), encoding="utf-8")
    A.claude_isolated_config(cfg, proj)
    perms = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))["permissions"]
    # 只放行 tavotto 这一个服务器与只读工具，不是放开全部；会执行 / 写入 / 上网的明确拒绝（面板里没人点弹窗）
    assert set(perms["allow"]) == {"mcp__tavotto", "Read", "Glob", "Grep"}
    assert {"Bash", "PowerShell", "Write", "Edit", "WebFetch"} <= set(perms["deny"])
    assert not set(perms["allow"]) & set(perms["deny"])
    assert "defaultMode" not in perms  # 不靠 bypassPermissions
    state = json.loads((cfg / ".claude.json").read_text(encoding="utf-8"))
    assert state["mcpServers"] == {"tavotto": {}} and state["hasCompletedOnboarding"] is True


def test_desktop_driver_is_deployed_and_importable_from_the_deployed_files_alone(tmp_path):
    import shutil
    import subprocess as sp

    deploy = (WINLAB / "winlab.py").read_text(encoding="utf-8")
    assert 'HERE / "desktop_driver.py"' in deploy and 'HERE / "native_probe.py"' in deploy
    for src in (
        WINLAB / "agent.py",
        WINLAB / "window_probe.py",
        WINLAB / "desktop_driver.py",
        WINLAB / "native_probe.py",
        *(ROOT / "scripts" / "ci" / n for n in W.DEPLOYED_CI_MODULES),
    ):
        shutil.copy(src, tmp_path / src.name)
    r = sp.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path[:0] = [sys.argv[1]]; import desktop_driver, native_probe",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr


# --- 任务编排：不兼容 / 代理 ----------------------------------------------------
class _FakeLive:
    def __init__(self, monkeypatch, tmp_path):
        self.tmp = tmp_path
        host = A.DESKTOP_HOSTS["vscode"]
        self.root = tmp_path / "vs"
        (self.root / "bin").mkdir(parents=True)
        (self.root / "bin" / "code.cmd").write_text("")
        lab = A.Lab(tmp_path / "lab")
        lab.cache.mkdir(parents=True)
        py = lab.venv_python()
        py.parent.mkdir(parents=True)
        py.write_text("")
        self.lab = lab
        setup = tmp_path / "setup.exe"
        setup.write_bytes(b"")
        self.vsix = tmp_path / "x.vsix"
        self.vsix.write_bytes(b"")
        monkeypatch.setattr(A, "fetch_pinned", lambda lab, h: (setup, {}))
        monkeypatch.setattr(A, "fetch_extension", lambda lab, e: (self.vsix, {}))
        monkeypatch.setattr(A, "authenticode", lambda p: {"status": "Valid", "org": "x"})
        monkeypatch.setattr(A, "uninstall_desktop_host", lambda *a: {"ok": True, "steps": []})
        monkeypatch.setattr(
            A,
            "install_desktop_host",
            lambda *a: {
                "ok": True,
                "exe": str(self.root / "Code.exe"),
                "root": str(self.root),
                "version": host.display_version,
            },
        )
        self.conf = {"api_key": "sk-" + "z" * 30, "provider": "deepseek"}

    def run(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir(exist_ok=True)
        return A.run_desktop_live_task(self.lab, "rid", run_dir, {}, self.conf, "vscode-claude")


def test_host_rejecting_the_extension_as_incompatible_is_not_supported_not_failed(
    monkeypatch, tmp_path
):
    f = _FakeLive(monkeypatch, tmp_path)

    def logged(argv, cwd, env, prefix, timeout, stdin_data=None):
        Path(f"{prefix}.stderr.log").parent.mkdir(parents=True, exist_ok=True)
        Path(f"{prefix}.stderr.log").write_text(
            "Unable to install extension 'x' as it is not compatible with VS Code '1.141.2'.",
            encoding="utf-8",
        )
        Path(f"{prefix}.stdout.log").write_text("", encoding="utf-8")
        return {"exit_code": 1}

    monkeypatch.setattr(A, "_run_logged", logged)
    r = f.run()
    assert r["result"] == "skipped" and r["levels"]["L3"] == "not_supported"
    assert r["levels"]["L5"] == "not_supported" and "host limitation" in r["summary"]


def test_extension_that_fails_to_install_for_another_reason_is_failed(monkeypatch, tmp_path):
    f = _FakeLive(monkeypatch, tmp_path)
    monkeypatch.setattr(A, "_run_logged", lambda *a, **k: {"exit_code": 1})
    r = f.run()
    assert r["result"] == "failed" and r["levels"]["L3"] != "not_supported"


def test_desktop_live_proxy_down_or_bad_hash_never_becomes_a_pass(monkeypatch, tmp_path):
    f = _FakeLive(monkeypatch, tmp_path)
    monkeypatch.setattr(
        A, "fetch_extension", lambda lab, e: (None, {"unavailable": "proxy trojan not listening"})
    )
    r = f.run()
    assert r["result"] == "environment_unavailable" and "not listening" in r["summary"]
    monkeypatch.setattr(A, "fetch_extension", lambda lab, e: (None, {"sha256_mismatch": "0" * 64}))
    assert f.run()["result"] == "error"


def test_extension_download_trusts_one_pinned_root_and_keeps_tls_verification_on():
    import ssl

    ext = A.DESKTOP_EXTENSIONS["anthropic.claude-code"]
    pem = A.extension_ca_pem(ext)
    assert (
        pem and A.hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == A.EXTENSION_CA_SHA256
    )
    # 钉子对不上（PEM 被改过）就一个额外的根都不信
    import dataclasses

    assert (
        A.extension_ca_pem(dataclasses.replace(ext, extra_ca_pem=pem.replace("MIID3T", "MIID3U")))
        is None
    )
    handlers = [
        h
        for h in A._opener("trojan", ca_pem=pem).handlers
        if isinstance(h, A.urllib.request.HTTPSHandler)
    ]
    ctx = handlers[0]._context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    # 域名内置（Open VSX 与它重定向到的 Eclipse 内容站）：这个根不用于别的下载
    for h in A.DESKTOP_HOSTS.values():
        assert not hasattr(h, "extra_ca_pem")


def test_extension_tls_prefers_certifi_then_pinned_root_and_fails_honestly_without_trust(
    tmp_path, monkeypatch
):
    import ssl
    import urllib.error

    lab = A.Lab(root=tmp_path)
    ext = A.DESKTOP_EXTENSIONS["anthropic.claude-code"]
    monkeypatch.setattr(A, "_tcp_open", lambda *a, **k: True)
    seen = {}

    class Opener:
        def open(self, req, timeout):
            raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer"))

    def opener(mode, **kw):
        seen.update(kw)
        return Opener()

    monkeypatch.setattr(A, "_opener", opener)
    # 没有 certifi：回退到钉死的单根；信任源缺根 -> 诚实失败，不降级、不关校验
    path, info = A.fetch_extension(lab, ext)
    assert path is None and "download failed" in info["unavailable"]
    assert "ca_pem" in seen and "cafile" not in seen and info["trust_source"] == "pinned-root"
    d = lab.cache / "desktop-extensions" / ext.id / ext.version
    assert not any(d.iterdir())
    # 有 certifi：优先用它，不再追加钉死的根
    cert = lab.venv_python().parent.parent / "Lib" / "site-packages" / "certifi" / "cacert.pem"
    cert.parent.mkdir(parents=True)
    cert.write_text("x")
    seen.clear()
    A.fetch_extension(lab, ext)
    assert seen == {"cafile": str(cert)}
    # 没有额外根：什么都不追加
    seen.clear()
    A._fetch_pinned_file(d, ext.url, ext.sha256, "trojan", 10)
    assert seen == {}
    f = _FakeLive(monkeypatch, tmp_path / "f")
    monkeypatch.setattr(
        A, "fetch_extension", lambda lab, e: (None, {"unavailable": "download failed: URLError"})
    )
    assert f.run()["result"] == "environment_unavailable"


@pytest.mark.skipif(
    os.name != "nt", reason="命名管道 + 进程令牌 SID 只在 Windows 上有意义（在 Lab 上跑）"
)
def test_secret_pipe_delivers_once_to_another_process_of_the_same_user():
    import subprocess as sp

    key = "sk-" + "q" * 30
    pipe = A._open_secret_pipe({"K": key})
    try:
        code = (
            "import sys; sys.path[:0]=[sys.argv[1], sys.argv[3]]; import agent as A, json;"
            "print(json.dumps(A.read_secret_pipe(sys.argv[2])))"
        )
        r = sp.run(
            [
                sys.executable,
                "-I",
                "-c",
                code,
                str(WINLAB),
                pipe.name,
                str(ROOT / "scripts" / "ci"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout) == {"K": key}
        assert pipe.error is None
        # 只发一次：第二次读不到（单实例、已断开）
        again = sp.run(
            [
                sys.executable,
                "-I",
                "-c",
                code,
                str(WINLAB),
                pipe.name,
                str(ROOT / "scripts" / "ci"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert again.returncode != 0 and key not in again.stdout
    finally:
        pipe.close()
    assert pipe.delivered is True


def test_driver_waits_for_the_panel_to_stop_being_busy_not_only_for_recorder_silence(
    tmp_path, monkeypatch
):
    """run 20261026T130906Z-feb0dd：模型在 open_figure 之后想了一分多钟，录制器 45 s 没新行就收场，
    面板还在忙（有 Stop 按钮）——L5 判 failed 是驱动器提前走了，不是宿主做不到。"""
    clock = {"t": 0.0}
    monkeypatch.setattr(D.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(D.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    rec = tmp_path / "rec.jsonl"
    rec.write_text("a\nb\n", encoding="utf-8")
    busy_until = 200.0
    reason, n = D.wait_quiet(rec, 1, 45, 1200, 300, lambda: True, lambda: clock["t"] < busy_until)
    assert reason == "quiet" and n == 2
    assert clock["t"] >= busy_until
    # 一直在忙 → 到 max_s 如实报 max，不当 quiet
    clock["t"] = 0.0
    reason, _ = D.wait_quiet(rec, 1, 45, 300, 300, lambda: True, lambda: True)
    assert reason == "max"


def test_host_cli_is_found_only_inside_the_install_dir_for_each_vscode_like_host(tmp_path):
    vs, cur, none = tmp_path / "vs", tmp_path / "cursor", tmp_path / "none"
    (vs / "bin").mkdir(parents=True)
    (vs / "bin" / "code.cmd").write_text("")
    (cur / "resources" / "app" / "bin").mkdir(parents=True)
    (cur / "resources" / "app" / "bin" / "cursor.cmd").write_text("")
    none.mkdir()
    assert A.host_cli(vs) == vs / "bin" / "code.cmd"
    assert A.host_cli(cur) == cur / "resources" / "app" / "bin" / "cursor.cmd"
    assert A.host_cli(none) is None


def test_each_desktop_live_cell_opens_the_panel_through_its_own_host_url_scheme(tmp_path):
    for mode, cell in A.DESKTOP_LIVE.items():
        assert A.DESKTOP_HOSTS[cell.host].vscode_like or cell.native, mode
    # Cursor 打开扩展 URI 前弹模态确认框：只走命令面板
    assert A.DESKTOP_HOSTS["vscode"].url_scheme == "vscode"
    assert A.DESKTOP_HOSTS["cursor"].url_scheme == ""
    argv = A.driver_argv(
        Path("py"),
        Path("d.py"),
        exe="C.exe",
        root="r",
        profile=tmp_path,
        ext_dir=tmp_path,
        workspace=tmp_path,
        prompt_file=tmp_path / "p",
        rec_log=tmp_path / "r",
        out_dir=tmp_path,
        label="round1",
        open_uri="cursor://anthropic.claude-code/open",
    )
    assert argv[argv.index("--open-uri") + 1] == "cursor://anthropic.claude-code/open"


def test_native_probe_only_looks_and_its_summary_reports_login_and_byok_markers():
    import native_probe as NP

    args = NP.host_args(1234, None, None)
    assert args == [
        "--remote-debugging-port=1234"
    ]  # 不传 address：只绑回环；非 VS Code 系不带 profile
    assert any(a.startswith("--user-data-dir=") for a in NP.host_args(1, "p", "w"))
    assert NP.host_args(None, None, None) == []  # 宿主拒绝调试端口时的第二次：不带
    assert NP.NOT_READY_RE.search("Preparing Environment") and not NP.NOT_READY_RE.search("Sign in")
    src = (WINLAB / "native_probe.py").read_text(encoding="utf-8")
    for verb in ("Input.dispatch", "--remote-debugging-address", "insertText"):
        assert verb not in src  # 只看，不打字
    assert src.count(".click()") == 1  # 唯一的点击在 peek 里，只点登记的那一个按钮
    for b in A.NATIVE_PEEK.values():
        assert not re.search(r"登录|sign|log ?in|connect|授权", b, re.I), b
    nat = A.summarize_native(
        {
            "cdp": True,
            "titles": ["Cursor"],
            "pages": [{"text": "Sign in to continue\nOr add your OpenAI API key"}],
        }
    )
    assert "sign in" in nat["login_markers"] and "api key" in nat["byok_markers"]
    assert A.summarize_native({"titles": ["X"], "pages": []})["login_markers"] == []
    assert "desktop-native" in A.TASKS and A.TASKS["desktop-native"].modes == A.DESKTOP_HOST_IDS


def test_workbench_page_is_recognized_by_its_vscode_file_scheme_too():
    assert D.is_workbench_url("vscode-file://vscode-app/c:/x/workbench/workbench.html")
    assert D.is_workbench_url("vscode-file://vscode-app/c:/x/trae/index.html")
    assert not D.is_workbench_url("vscode-file://vscode-app/c:/x/setup/setup.html")
    # 引导页只点白名单里的按钮：没有登录 / 注册 / 授权
    for b in D.ONBOARDING_BUTTONS:
        assert not re.search(r"登录|注册|sign|log ?in|授权|allow|同意|accept", b, re.I), b
    assert not D.is_workbench_url("vscode-webview://abc/index.html")
    assert (
        D._url_shape("vscode-file://vscode-app/c:/a/b/index.html?x=1") == "vscode-file:index.html"
    )


def test_host_own_login_prompt_is_not_mistaken_for_the_extension_requiring_login(tmp_path):
    """run 20261026T134946Z-82ba4a：Cursor 自己的「AI features require you to be logged in」进了面板文字，
    被判成扩展要登录。判据只看扩展 webview 的文字。"""
    src = (WINLAB / "agent.py").read_text(encoding="utf-8")
    i = src.index("LOGIN_MARKERS.search(panel)")
    assert "round1-webview.txt" in src[i - 400 : i]
    assert "round1-panel" not in src[i - 400 : i]


def test_native_probe_that_aborts_after_the_window_is_an_error_not_a_passed_probe(
    tmp_path, monkeypatch
):
    """Codex #1436：probe_once 保留 window 又写了 error（非回环可连、CDP 失败）时不能报 passed。"""
    lab = A.Lab(tmp_path / "lab")
    lab.cache.mkdir(parents=True)
    py = lab.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    setup = tmp_path / "s.exe"
    setup.write_bytes(b"")
    root = tmp_path / "z"
    root.mkdir()
    monkeypatch.setattr(A, "fetch_pinned", lambda lab, h: (setup, {}))
    monkeypatch.setattr(A, "authenticode", lambda p: {"status": "Valid", "org": "x"})
    monkeypatch.setattr(A, "uninstall_desktop_host", lambda *a: {"ok": True, "steps": []})
    monkeypatch.setattr(
        A,
        "install_desktop_host",
        lambda *a: {"ok": True, "exe": str(root / "ZCode.exe"), "root": str(root), "version": "1"},
    )

    def fake_gui(lab, run_id, argv, cwd, env, prefix, timeout, secret_env=None):
        out = Path(argv[argv.index("--out") + 1])
        out.write_text(
            json.dumps({"window": {"title": "ZCode"}, "error": "CDPError: boom", "pages": []}),
            encoding="utf-8",
        )
        return {"exit_code": 0, "gui": "ran", "session_id": 1}

    monkeypatch.setattr(A, "run_in_gui", fake_gui)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    res = A.run_desktop_hosts_task(lab, "rid", run_dir, {}, "zcode", native=True)
    assert res["result"] == "error" and "aborted" in res["summary"]


# --- ZCode 原生 Agent：落盘例外 ---------------------------------------------------
def test_zcode_key_goes_only_into_provider_config_and_mcp_points_at_the_recorder(tmp_path):
    key = "sk-" + "k" * 32
    conf = {
        "api_key": key,
        "base_url": "https://api.deepseek.com/anthropic",
        "provider": "deepseek",
    }
    zdata, proj = tmp_path / "zdata", tmp_path / "proj"
    proj.mkdir()
    pc = A.zcode_isolated_config(
        zdata, proj, ["py", "rec.py", "log", "--", "py", "server.py"], {"X": "1"}, conf
    )
    assert key in pc.read_text(encoding="utf-8")
    p = json.loads(pc.read_text(encoding="utf-8"))["config"]["providerConfigRules"][
        "providerRules"
    ][0]
    assert p["config"]["api"] == {
        "type": "anthropic-messages",
        "baseUrl": "https://api.deepseek.com/anthropic/v1",
    }
    for f in (
        proj / ".zcode" / "config.json",
        zdata / ".zcode" / "cli" / "config.json",
        zdata / ".zcode" / "workspace" / "default" / ".zcode" / "config.json",
    ):
        txt = f.read_text(encoding="utf-8")
        assert key not in txt  # 运行配置（含 MCP）里没有 key
        run = json.loads(txt)
        srv = run["mcp"]["servers"]["tavotto"]
        assert srv["type"] == "stdio" and srv["command"] == "py" and srv["args"][0] == "rec.py"
        assert {"Bash", "Write", "Edit", "WebFetch"} <= set(run["permission"]["disallowedTools"])
        assert run["permission"]["allowedTools"] == []  # 没给工具名就不放行任何东西
    # 只有 provider_config.json 一个文件含 key
    assert A._files_containing([tmp_path], key.encode()) == ([pc], [])


def test_ondisk_key_is_wiped_and_any_residual_fails_the_run(tmp_path, monkeypatch):
    key = "sk-" + "w" * 32
    run_dir = tmp_path / "run"
    conf = {"api_key": key, "base_url": "https://x/anthropic"}
    proj = run_dir / "work" / "project"
    proj.mkdir(parents=True)
    A.zcode_isolated_config(run_dir / "work" / "zdata", proj, ["py"], {}, conf)
    (run_dir / "evidence").mkdir()
    (run_dir / "evidence" / "log.txt").write_text(f"echo {key}", encoding="utf-8")
    appdata = tmp_path / "appdata"
    (appdata / "ZCode" / "Local Storage").mkdir(parents=True)
    (appdata / "ZCode" / "Local Storage" / "leveldb.log").write_bytes(b"x" + key.encode())
    env = {"APPDATA": str(appdata), "LOCALAPPDATA": str(tmp_path / "none")}
    res = A.wipe_ondisk_key(run_dir, env, key)
    assert res["wiped"] is True and res["checked_app_dirs"] == ["ZCode"]
    assert A._files_containing([run_dir, appdata], key.encode()) == ([], [])


@pytest.mark.skipif(
    os.name == "nt", reason="winlab.py 是 Mac 端入口：密钥文件的 0600 权限位检查只在 POSIX 上有意义"
)
def test_ondisk_modes_use_their_own_dedicated_key_file_and_never_fall_back(tmp_path, monkeypatch):
    assert W.ONDISK_MODES == A.DESKTOP_LIVE_ONDISK_MODES
    default = tmp_path / "model.json"
    default.write_text("{}")
    default.chmod(0o600)
    monkeypatch.setattr(W, "MODEL_CONF_PATH", default)
    monkeypatch.setattr(W, "MODEL_CONF_ONDISK_PATH", tmp_path / "model-ondisk.json")
    assert W.load_model_secret("vscode-claude") == b"{}"
    assert W.load_model_secret("zcode-native") is None  # 专用 key 没录：不跑，不拿默认那把凑数


def test_native_onboarding_never_clicks_continue_connect_or_login():
    for b in D.NATIVE_ONBOARDING_BUTTONS:
        assert not re.search(r"continue|connect|sign|log ?in|登录|授权", b, re.I), b
    assert (
        "Skip for now" in D.NATIVE_ONBOARDING_BUTTONS
        and "Use API key" in D.NATIVE_ONBOARDING_BUTTONS
    )


def test_tavotto_tool_names_come_from_the_server_handlers_table():
    names = A.tavotto_tool_names(ROOT / "codex-plugin" / "mcp")
    assert (
        "tavotto_health" in names
        and "tavotto_open_figure" in names
        and "tavotto_close_session" in names
    )
    assert len(names) == len(set(names)) >= 8


def test_driver_stops_waiting_when_the_host_shows_a_permission_prompt(tmp_path, monkeypatch):
    """run 20261026T154420Z-ca5927：ZCode 的权限框没人点，驱动器干等到 1200 s 上限。"""
    clock = {"t": 0.0}
    monkeypatch.setattr(D.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(D.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    rec = tmp_path / "rec.jsonl"
    rec.write_text("a\nb\n", encoding="utf-8")
    reason, _ = D.wait_quiet(
        rec, 1, 45, 1200, 300, lambda: True, lambda: True, lambda: clock["t"] > 10
    )
    assert reason == "permission_prompt" and clock["t"] < 20
    assert D.PERMISSION_PROMPT_RE.search(
        "MCP Tavotto Health\n\nPermission required\n\nAwaiting approval"
    )


def test_wipe_scans_large_files_and_counts_unreadable_ones_as_not_verified(tmp_path, monkeypatch):
    """Codex #1439：超过 64 MiB 的文件、读不了的文件都不能算「没有 key」。"""
    key = "sk-" + "L" * 32
    chunk = 1 << 20
    big = tmp_path / "big.bin"
    with open(big, "wb") as f:
        f.write(b"\0" * chunk * 3)
        f.write(b"x" * (chunk - 5) + key.encode())  # 跨块边界
    assert A._files_containing([tmp_path], key.encode()) == ([big], [])
    run_dir = tmp_path / "run"
    (run_dir / "work").mkdir(parents=True)
    locked = run_dir / "work" / "locked.bin"
    locked.write_bytes(b"x")
    real = A._file_contains

    def fake(f, n, **k):
        if f == locked:
            raise OSError("locked")
        return real(f, n, **k)

    monkeypatch.setattr(A, "_file_contains", fake)
    res = A.wipe_ondisk_key(run_dir, {"APPDATA": str(tmp_path / "none")}, key)
    assert res["wiped"] is False and res["unreadable"] == [str(Path("work") / "locked.bin")]


@pytest.mark.skipif(os.name == "nt", reason="winlab.py 是 Mac 端入口（POSIX 权限位）")
def test_default_and_ondisk_keys_must_differ_in_either_order(tmp_path, monkeypatch):
    import getpass

    monkeypatch.setattr(W, "MODEL_CONF_PATH", tmp_path / "model.json")
    monkeypatch.setattr(W, "MODEL_CONF_ONDISK_PATH", tmp_path / "model-ondisk.json")
    same = "sk-" + "s" * 32
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": same)
    assert W.secret_init("deepseek", ondisk=True) == 0
    assert W.secret_init("deepseek") == 2  # 后录默认那把、填了同一个 → 拒收
    # 手工把两个文件改成同一把：落盘那格照样不跑
    (tmp_path / "model.json").write_text(json.dumps({"api_key": same}))
    (tmp_path / "model.json").chmod(0o600)
    assert W.load_model_secret("zcode-native") is None


def test_wipe_keeps_going_when_one_run_file_cannot_be_rewritten(tmp_path, monkeypatch):
    """Codex #1439：run 目录里有个文件写不回去时，应用数据目录里的副本照样要删，结果如实 wiped=False。"""
    key = "sk-" + "R" * 32
    run_dir = tmp_path / "run"
    (run_dir / "evidence").mkdir(parents=True)
    stuck = run_dir / "evidence" / "stuck.log"
    stuck.write_text(key, encoding="utf-8")
    appdata = tmp_path / "appdata"
    (appdata / "ZCode").mkdir(parents=True)
    copy = appdata / "ZCode" / "state.json"
    copy.write_text(key, encoding="utf-8")
    real = Path.write_bytes

    def fake(self, data):
        if self == stuck:
            raise PermissionError("read-only")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", fake)
    res = A.wipe_ondisk_key(run_dir, {"APPDATA": str(appdata)}, key)
    assert not copy.exists()  # 应用数据里的副本删掉了
    assert res["wiped"] is False and res["residual"] == [str(Path("evidence") / "stuck.log")]


def test_ondisk_key_is_wiped_even_when_the_uninstaller_raises(tmp_path, monkeypatch):
    """Codex #1439：最后的卸载抛异常时，provider_config.json 里的 key 也不能留在盘上。"""
    f = _FakeLive(monkeypatch, tmp_path)
    key = "sk-" + "u" * 32
    f.conf = {"api_key": key, "provider": "deepseek", "base_url": "https://x/anthropic"}
    zdata_pc = tmp_path / "run" / "work" / "zdata" / ".zcode" / "v2" / "provider_config.json"

    def body(*a, **k):
        zdata_pc.parent.mkdir(parents=True, exist_ok=True)
        zdata_pc.write_text(key, encoding="utf-8")
        return {"result": "passed", "summary": "ok"}

    calls = {"n": 0}

    def uninstall(*a):
        calls["n"] += 1
        if calls["n"] > 1:  # 第一次是 preexisting 清理；第二次是收尾
            raise PermissionError("uninstaller would not start")
        return {"ok": True, "steps": []}

    monkeypatch.setattr(A, "_desktop_live_body", body)
    monkeypatch.setattr(A, "uninstall_desktop_host", uninstall)
    monkeypatch.setattr(A, "_zcode_app_dirs", lambda env: [])
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    with pytest.raises(PermissionError):
        A.run_desktop_live_task(f.lab, "rid", run_dir, {}, f.conf, "zcode-native")
    assert not zdata_pc.exists()


def test_stale_provider_configs_from_killed_runs_are_swept_before_the_next_run(tmp_path):
    runs = tmp_path / "runs"
    old = (
        runs
        / "20261026T000000Z-aaaaaa"
        / "work"
        / "zdata"
        / ".zcode"
        / "v2"
        / "provider_config.json"
    )
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    keep = runs / "20261026T000000Z-aaaaaa" / "evidence" / "x.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("x")
    assert A.sweep_stale_ondisk_configs(runs) == ["20261026T000000Z-aaaaaa"]
    assert not old.exists() and keep.exists()
