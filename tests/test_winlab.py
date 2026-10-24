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
