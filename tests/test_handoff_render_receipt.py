"""交接三态（ADR 0131 第六节）：`launch.render` 与插件的 launch / render / verified 三个字段。

`ok` 只说「解析、登记、唤起成功」，不说图画出来了（#961：交接返回 ok:true，sidecar 却因缺
引擎文件渲染失败）。确认信号是一次**经认证的 HTTP 查询**（`GET /api/handoff/render-status`）：

* 端点的认证面：无凭据 / 错 token / 不合法 Host、Origin 一律拒；完整 secret 与 scoped 只读 token
  可用，且 scoped token 带去任何其它端点都被拒；
* 进程内结论表：`since` 之前的不返回、第一份赢、项目 / stem 隔离、有界、并发不乱、响应不含路径；
* scoped 凭据文件：0600 / 0700、原子写、退出删除、陈旧清理、不含主 secret；
* `render` 三值的**每一条路径**——只有实例明确报错才是 failed，查不到 / 超时 / 没有目标 stem
  / 通道不可用一律 unverified；CLI 不往磁盘写任何东西；
* `verified` 的判据（stem 一致 ∧ 注册表有 ∧ manifest 非空）逐条变异；
* `/api/engine/render` 的真实收尾处确实记结论（成功、取 worker 阶段失败、override 失败三条）；
* 插件顶层三字段，含旧 CLI（没有 `launch.render`）与新 CLI 的兼容矩阵；
* #961 形状：`launch_success=true`、`render_success=false`、`render.code=engine_files_incomplete`，
  退出码与 `ok` 不变。
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m, desktop, security
from tavotto.engine import (
    handoff,
    handoffreceipt as receipt,
    locate,
    pool as engine_pool,
    project_watch as engine_watch,
    session_client,
)

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "codex-plugin" / "skills" / "tavotto-figure"
PLUGIN_HANDOFF = SKILL_DIR / "scripts" / "handoff.py"
STATUS_PATH = session_client.HANDOFF_STATUS_PATH

posix_only = pytest.mark.skipif(os.name == "nt", reason="权限位 / uid 判据只在 POSIX 上有意义")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """凭据文件落在 `<data_dir>/session/`：每条用例一个私有数据目录；结论表每条用例清空。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    receipt.LEDGER.clear()
    yield tmp_path / "data"
    receipt.LEDGER.clear()


class Clock:
    """可注入的假时钟：sleep 推进时间，测试里没有真等待。"""

    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture()
def project(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    return d


# ------------------------------ 进程内结论表 ------------------------------
def test_first_conclusion_after_since_wins(project):
    t = [100.0]
    now = lambda: t[0]  # noqa: E731
    receipt.record_failed(str(project), "Fig1", "first_err", now=now)
    t[0] = 101.0
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=3, now=now)
    got = receipt.query(str(project), "Fig1", 99.0, now=now)
    assert got == {"status": "failed", "code": "first_err"}


def test_conclusions_before_since_are_not_returned(project):
    now = lambda: 100.0  # noqa: E731
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=3, now=now)
    assert receipt.query(str(project), "Fig1", 100.5, now=now) == {"status": "pending"}
    assert receipt.query(str(project), "Fig1", 100.0, now=now)["status"] == "ok"


def test_projects_and_stems_are_isolated(project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    now = lambda: 100.0  # noqa: E731
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=3, now=now)
    assert receipt.query(str(project), "Fig2", 0, now=now) == {"status": "pending"}
    assert receipt.query(str(other), "Fig1", 0, now=now) == {"status": "pending"}


def test_the_ledger_is_bounded_in_count_and_age(project):
    ledger = receipt.RenderLedger(max_entries=5, ttl_s=10)
    for i in range(20):
        ledger.record(str(project), f"F{i}", {"status": "ok"}, now=lambda: 100.0)
    assert len(ledger._entries) == 5
    assert ledger.first_since(str(project), "F0", 0, now=lambda: 100.0) is None  # 被挤出
    assert ledger.first_since(str(project), "F19", 0, now=lambda: 100.0) is not None
    assert ledger.first_since(str(project), "F19", 0, now=lambda: 111.0) is None  # 过期
    assert len(ledger._entries) == 0


def test_the_default_ledger_limits_are_the_documented_ones():
    assert receipt.MAX_ENTRIES == 128 and receipt.TTL_S == 600.0


def test_concurrent_records_keep_a_consistent_ledger(project):
    ledger = receipt.RenderLedger(max_entries=64, ttl_s=60)
    threads = [
        threading.Thread(
            target=lambda i=i: [
                ledger.record(str(project), "Fig1", {"status": "ok", "n": i * 100 + j})
                for j in range(50)
            ]
        )
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ledger._entries) == 64
    seqs = [e["seq"] for e in ledger._entries]
    assert seqs == sorted(seqs) and len(set(seqs)) == 64
    assert ledger.first_since(str(project), "Fig1", 0)["status"] == "ok"


def test_the_response_never_echoes_paths_or_tracebacks(project):
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=2)
    body = json.dumps(receipt.query(str(project), "Fig1", 0))
    assert str(project) not in body and "Traceback" not in body
    assert set(json.loads(body)) <= {"status", "stem", "registry_match", "manifest_elements"}
    receipt.LEDGER.clear()
    receipt.record_failed(str(project), "Fig1", "Bad Code /etc/passwd")
    assert receipt.query(str(project), "Fig1", 0) == {"status": "failed", "code": "render_failed"}


@pytest.mark.parametrize("code", ["engine_files_incomplete", "missing_dependency"])
def test_a_wellformed_failure_code_is_kept(project, code):
    receipt.record_failed(str(project), "Fig1", code)
    assert receipt.query(str(project), "Fig1", 0)["code"] == code


# ------------------------------ verified 判据 -----------------------------
def _ok(stem="Fig1", registry_match=True, manifest_elements=5):
    return {
        "status": "ok",
        "stem": stem,
        "registry_match": registry_match,
        "manifest_elements": manifest_elements,
    }


def test_verified_needs_all_three_facts():
    assert receipt.summarize("Fig1", _ok(), 10, reason="x")["verified"] is True
    # 逐条变异：少一条都不是 verified，但渲染本身仍是 ok
    for bad in (
        _ok(stem="Fig2"),  # 渲染的不是目标那张
        _ok(registry_match=False),  # 注册表里没有这个 stem
        _ok(manifest_elements=0),  # 清单为空
    ):
        out = receipt.summarize("Fig1", bad, 10, reason="x")
        assert out["status"] == "ok" and out["verified"] is False, bad


def test_failed_and_unverified_are_never_verified():
    failed = receipt.summarize(
        "Fig1", {"status": "failed", "code": "engine_files_incomplete"}, 3, reason="x"
    )
    assert failed["status"] == "failed" and failed["verified"] is False
    assert failed["code"] == "engine_files_incomplete"
    unver = receipt.summarize("Fig1", None, 3, reason=receipt.REASON_TIMEOUT)
    assert unver == {"status": "unverified", "reason": "timeout", "verified": False, "waited_ms": 3}


def test_clean_result_sanitises_what_the_endpoint_says():
    assert receipt.clean_result({"status": "pending"}) is None
    assert receipt.clean_result("nope") is None
    assert receipt.clean_result({"status": "failed", "code": "../../x"}) == {
        "status": "failed",
        "code": "render_failed",
    }
    assert receipt.clean_result(
        {"status": "ok", "stem": 3, "registry_match": "yes", "manifest_elements": -1}
    ) == {"status": "ok", "stem": None, "registry_match": False, "manifest_elements": 0}


# ------------------------------ 有界等待 ----------------------------------
def _find_none(port):
    return []


def test_wait_without_any_credential_is_channel_unavailable(project):
    clock = Clock()
    out = receipt.wait(
        str(project), "Fig1", 0.0, 20, clock=clock, sleep=clock.sleep, interval=0.5, find=_find_none
    )
    assert out["status"] == "unverified" and out["reason"] == "channel_unavailable"
    assert clock.t - 1000.0 < 21  # 有界：不会无限轮询


def test_wait_with_a_silent_instance_is_timeout_not_failed(project):
    clock = Clock()
    out = receipt.wait(
        str(project),
        "Fig1",
        0.0,
        20,
        clock=clock,
        sleep=clock.sleep,
        interval=0.5,
        find=lambda p: [(1, "t")],
        get=lambda *a: {"status": "pending"},
    )
    assert out["status"] == "unverified" and out["reason"] == "timeout"
    assert out["waited_ms"] >= 20000


def test_wait_unreachable_instance_is_channel_unavailable(project):
    clock = Clock()
    out = receipt.wait(
        str(project),
        "Fig1",
        0.0,
        5,
        clock=clock,
        sleep=clock.sleep,
        find=lambda p: [(1, "t")],
        get=lambda *a: None,
    )
    assert out["reason"] == "channel_unavailable"


def test_wait_rediscovers_credentials_each_round_and_returns_on_conclusion(project):
    """冷启动：sidecar 在唤起之后才发布凭据；一有结论立刻收。"""
    clock = Clock()
    calls = {"n": 0}

    def find(port):
        calls["n"] += 1
        return [] if calls["n"] < 3 else [(7, "tok")]

    out = receipt.wait(
        str(project),
        "Fig1",
        0.0,
        20,
        clock=clock,
        sleep=clock.sleep,
        interval=0.5,
        find=find,
        get=lambda *a: _ok(),
    )
    assert out["status"] == "ok" and out["verified"] is True
    assert out["waited_ms"] < 5000


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -3.0])
def test_wait_budget_is_defended_against_non_finite_values(project, bad):
    out = receipt.wait(
        str(project),
        "Fig1",
        0.0,
        bad,
        clock=lambda: 0.0,
        sleep=lambda s: pytest.fail("不该睡"),
        find=_find_none,
    )
    assert out["status"] == "unverified"


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1"])
def test_render_wait_rejects_non_finite_or_negative(bad):
    with pytest.raises(SystemExit) as e:
        handoff.cli(["x.pdf", f"--render-wait={bad}"])
    assert e.value.code == 2


# ------------------------- 端点与 guard（认证面） --------------------------
PORT = 5611


@pytest.fixture
def guarded():
    """装上会话认证状态（桌面形态：无 api_secret），test_client 用匹配的 Host。"""
    state = security.SessionState("boot-nonce")
    state.port = PORT
    m.app.config[security.STATE_KEY] = state
    yield state
    m.app.config.pop(security.STATE_KEY, None)


def _get(state, *, token=None, headers=None, base=f"http://127.0.0.1:{PORT}", path=STATUS_PATH):
    h = dict(headers or {})
    if token is not None:
        h[session_client.AUTH_HEADER] = token
    return m.app.test_client().get(path, headers=h, base_url=base)


QS = "?project=/tmp/none&stem=Fig1&since=0"


def test_endpoint_rejects_missing_and_wrong_credentials(guarded):
    assert _get(guarded, path=STATUS_PATH + QS).status_code == 401
    assert _get(guarded, token="wrong", path=STATUS_PATH + QS).status_code == 401
    assert _get(guarded, token="", path=STATUS_PATH + QS).status_code == 401


def test_endpoint_accepts_the_scoped_token(guarded):
    r = _get(guarded, token=guarded.status_token, path=STATUS_PATH + QS)
    assert r.status_code == 200 and r.get_json() == {"status": "pending"}


def test_endpoint_accepts_the_full_secret_in_browser_mode(guarded):
    guarded.api_secret = "full-secret"
    r = _get(guarded, token="full-secret", path=STATUS_PATH + QS)
    assert r.status_code == 200


def test_scoped_token_is_rejected_everywhere_else(guarded):
    tok = guarded.status_token
    for path in ("/api/panels", "/api/projects/recent", "/api/session/ping", "/api/version-x"):
        r = _get(guarded, token=tok, path=path)
        assert r.status_code == 401, path
    # 换不到 relaunch nonce（桌面本来就 404，浏览器模式则是 403 bad_secret）
    r = m.app.test_client().post(
        session_client.RELAUNCH_PATH,
        json={"secret": tok},
        headers={session_client.AUTH_HEADER: tok},
        base_url=f"http://127.0.0.1:{PORT}",
    )
    assert r.status_code in (403, 404)
    guarded.api_secret = "full-secret"
    r = m.app.test_client().post(
        session_client.RELAUNCH_PATH,
        json={"secret": tok},
        base_url=f"http://127.0.0.1:{PORT}",
    )
    assert r.status_code == 403
    # 其它方法也不认：scoped token 只管 GET 这一个端点
    r = m.app.test_client().post(
        STATUS_PATH + QS,
        headers={session_client.AUTH_HEADER: tok},
        base_url=f"http://127.0.0.1:{PORT}",
    )
    assert r.status_code == 401


def test_scoped_token_is_independent_of_the_other_secrets(guarded):
    assert guarded.status_token not in ("boot-nonce", guarded.api_secret)
    assert not guarded.valid_api_secret(guarded.status_token)
    assert guarded.redeem(guarded.status_token) is None
    assert not guarded.valid_cookie(guarded.status_token)


def test_endpoint_enforces_host_and_origin(guarded):
    tok = guarded.status_token
    r = _get(guarded, token=tok, path=STATUS_PATH + QS, base="http://localhost:%d" % PORT)
    assert r.status_code == 403
    r = _get(guarded, token=tok, path=STATUS_PATH + QS, base="http://127.0.0.1:9")
    assert r.status_code == 403
    r = _get(guarded, token=tok, path=STATUS_PATH + QS, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = _get(
        guarded,
        token=tok,
        path=STATUS_PATH + QS,
        headers={"Origin": f"http://127.0.0.1:{PORT}"},
    )
    assert r.status_code == 200


def test_endpoint_validates_its_query(guarded):
    tok = guarded.status_token
    for qs in (
        "",
        "?project=/p&stem=F",
        "?project=/p&stem=F&since=abc",
        "?project=/p&stem=F&since=nan",
        "?project=/p&stem=F&since=inf",
        "?project=&stem=F&since=0",
        "?project=/p&stem=&since=0",
    ):
        assert _get(guarded, token=tok, path=STATUS_PATH + qs).status_code == 400, qs


def test_endpoint_returns_only_conclusions_after_since_and_no_paths(guarded, project):
    import time as _t

    tok = guarded.status_token
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=4)
    qs = f"?project={project}&stem=Fig1&since="
    future = _t.time() + 100
    assert _get(guarded, token=tok, path=STATUS_PATH + qs + repr(future)).get_json() == {
        "status": "pending"
    }
    r = _get(guarded, token=tok, path=STATUS_PATH + qs + "0")
    body = r.get_data(as_text=True)
    assert r.get_json()["status"] == "ok" and str(project) not in body
    other = f"?project={project}&stem=Other&since=0"
    assert _get(guarded, token=tok, path=STATUS_PATH + other).get_json() == {"status": "pending"}


def test_endpoint_is_open_without_session_state_like_every_endpoint(project):
    """测试 / --insecure-no-auth 旁路：与其它端点一致，不单独开洞也不单独关门。"""
    assert m.app.config.get(security.STATE_KEY) is None
    r = m.app.test_client().get(STATUS_PATH + f"?project={project}&stem=F&since=0")
    assert r.status_code == 200


# ------------------------- scoped 凭据文件与桌面发布 -----------------------
@posix_only
def test_status_file_is_private_atomic_and_scoped(isolated_data_dir):
    path = session_client.publish_status_token(5700, "tok-abc")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")] == []
    data = json.load(open(path, encoding="utf-8"))
    assert set(data) == {"port", "pid", "token", "scope", "created"}
    assert data["scope"] == "handoff_render_status" and data["pid"] == os.getpid()
    assert session_client.status_credentials() == [(5700, "tok-abc")]
    session_client.remove_status_token(5700)
    assert session_client.status_credentials() == []


def test_stale_and_foreign_status_files_are_not_used(isolated_data_dir):
    d = Path(session_client.session_dir())
    d.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (d / "handoff-status-5701.json").write_text(
        json.dumps({"port": 5701, "pid": dead.pid, "token": "t", "scope": "handoff_render_status"}),
        encoding="utf-8",
    )
    (d / "handoff-status-5702.json").write_text(
        json.dumps({"port": 5702, "pid": os.getpid(), "token": "t", "scope": "everything"}),
        encoding="utf-8",
    )
    (d / "handoff-status-5703.json").write_text("not json", encoding="utf-8")
    (d / "handoff-status-5704.json").write_text(
        json.dumps(
            {"port": "x", "pid": os.getpid(), "token": "t", "scope": "handoff_render_status"}
        ),
        encoding="utf-8",
    )
    if os.name == "posix":
        assert session_client.status_credentials() == []


@posix_only
def test_publishing_prunes_files_of_dead_processes(isolated_data_dir):
    d = Path(session_client.session_dir())
    d.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    old = d / "handoff-status-5801.json"
    old.write_text(
        json.dumps({"port": 5801, "pid": dead.pid, "token": "t", "scope": "handoff_render_status"}),
        encoding="utf-8",
    )
    session_client.publish_status_token(5802, "new")
    assert not old.exists() and (d / "handoff-status-5802.json").exists()


class Sidecar:
    """真桌面 sidecar：真 SidecarServer + 真 HTTP（与 test_desktop_sidecar 同款装配）。"""

    def __init__(self, tmp_path: Path):
        self.state = desktop.DesktopState("boot-nonce-for-test")
        self.srv = desktop.SidecarServer(m.app, self.state, tmp_path / "hs" / "handshake.json")
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.srv.announce_ready()
        self.port = self.srv.port

    def stop(self):
        self.srv.shutdown()
        self.srv.wait_stopped(timeout=10)
        self.thread.join(timeout=5)


@pytest.fixture
def sidecar(tmp_path):
    sc = Sidecar(tmp_path)
    yield sc
    sc.stop()


def test_sidecar_publishes_only_the_scoped_credential_and_removes_it_on_exit(tmp_path):
    sc = Sidecar(tmp_path)
    path = session_client.status_file_path(sc.port)
    try:
        data = json.load(open(path, encoding="utf-8"))
        assert data["token"] == sc.state.status_token and data["port"] == sc.port
        blob = open(path, encoding="utf-8").read()
        assert "boot-nonce-for-test" not in blob  # 主 nonce 不上盘
        assert not os.path.exists(session_client.session_file_path(sc.port))  # 完整凭据不上盘
    finally:
        sc.stop()
    assert not os.path.exists(path)


def test_sidecar_without_a_writable_data_dir_still_starts(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("read-only")

    monkeypatch.setattr(session_client, "publish_status_token", boom)
    sc = Sidecar(tmp_path)
    try:
        assert sc.port > 0
    finally:
        sc.stop()


def test_real_http_cli_client_against_a_real_sidecar(sidecar, project):
    """真 urllib 打真 sidecar：凭据靠文件发现；scoped token 查得到，别的端点 401。"""
    since = 0.0
    out = receipt.wait(str(project), "Fig1", since, 0.3, interval=0.05)
    assert out["reason"] == "timeout"  # 通了、尚无结论
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=9)
    out = receipt.wait(str(project), "Fig1", since, 3, interval=0.05)
    assert out["status"] == "ok" and out["verified"] is True and out["manifest_elements"] == 9
    # 同一 token 打别的端点：401
    req = urllib.request.Request(
        f"http://127.0.0.1:{sidecar.port}/api/panels",
        headers={session_client.AUTH_HEADER: sidecar.state.status_token},
    )
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=3)
    assert e.value.code == 401


def test_cli_with_a_wrong_token_file_is_channel_unavailable(sidecar, project):
    session_client.publish_status_token(sidecar.port, "forged")  # 同端口覆盖成错 token
    out = receipt.wait(str(project), "Fig1", 0.0, 0.3, interval=0.05)
    assert out["reason"] == "channel_unavailable"  # 被拒 = 查不到，不是 failed


def test_the_credentialed_query_never_goes_through_a_proxy(sidecar, project, monkeypatch):
    """带 `X-Tavotto-Auth` 的回环查询绝不经过代理：环境里配了代理（NO_PROXY 清空）、假代理在监听——
    请求直达实例拿到结论，假代理 0 个连接。换回 `urlopen` 会把凭据发给代理（本用例红）。"""
    import socket
    import urllib.request

    # 模块级 `urlopen` 第一次调用就把当时的代理环境缓存进全局 opener：清掉，别让用例顺序决定红绿
    monkeypatch.setattr(urllib.request, "_opener", None)
    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(8)
    proxy.settimeout(0.2)
    url = f"http://127.0.0.1:{proxy.getsockname()[1]}"
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.setenv(name, url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    try:
        receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=4)
        out = receipt.wait(str(project), "Fig1", 0.0, 3, interval=0.05)
        assert out["status"] == "ok" and out["manifest_elements"] == 4
        with pytest.raises(socket.timeout):
            proxy.accept()  # 假代理一个连接都没收到
    finally:
        proxy.close()


def test_browser_mode_falls_back_to_the_full_secret_for_the_known_port(project):
    session_client.publish_secret(5811, "browser-secret")
    try:
        assert receipt.candidates(5811) == [(5811, "browser-secret")]
        assert receipt.candidates(5812) == []
    finally:
        session_client.remove_secret(5811)


# ------------------------ open_target：launch.render ----------------------
def _fig_project(tmp_path):
    d = tmp_path / "gallery"
    d.mkdir(exist_ok=True)
    (d / "fig1.py").write_text(
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    fig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )
    (d / "Fig1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return d


class FakeLaunch:
    """替身 `handoff.launch`：可选地在「唤起」期间让实例记结论。"""

    def __init__(self, on_launch=None, raises=None):
        self.on_launch = on_launch
        self.raises = raises
        self.calls = 0

    def __call__(self, target, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.on_launch:
            self.on_launch(target)
        return {"mode": "desktop", "app": "/x/Tavotto", "handoff": "launched", "pid": 1}


@pytest.fixture
def serving(monkeypatch):
    """把 CLI 的发现 + 取数接到进程内（结论表直连），不开 socket。"""
    monkeypatch.setattr(receipt, "candidates", lambda port: [(1, "t")])

    def fake_fetch(port, cred, project, stem, since):
        return receipt.clean_result(receipt.query(project, stem, since)) or {"status": "pending"}

    monkeypatch.setattr(receipt, "fetch", fake_fetch)


def _open(tmp_path, monkeypatch, fake, **kw):
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", fake)
    clock = Clock()
    result = handoff.open_target(
        str(d / "Fig1.pdf"), receipt_clock=clock, receipt_sleep=clock.sleep, **kw
    )
    return d, result


def test_render_ok_when_the_instance_reports_the_target(tmp_path, monkeypatch, serving):
    fake = FakeLaunch(
        lambda t: receipt.record_ok(t.project, t.stem, registry_match=True, manifest_elements=6)
    )
    _, result = _open(tmp_path, monkeypatch, fake, render_wait=20)
    render = result["launch"]["render"]
    assert result["ok"] is True and result["protocol"] == 1
    assert render["status"] == "ok" and render["verified"] is True
    assert render["manifest_elements"] == 6 and render["stem"] == "Fig1"


def test_render_failed_carries_the_stable_code_and_ok_is_unchanged(tmp_path, monkeypatch, serving):
    """#961 的形状：交接 ok:true，渲染 failed + engine_files_incomplete。"""
    fake = FakeLaunch(lambda t: receipt.record_failed(t.project, t.stem, "engine_files_incomplete"))
    _, result = _open(tmp_path, monkeypatch, fake, render_wait=20)
    assert result["ok"] is True  # 公开语义不变
    assert result["launch"]["mode"] == "desktop"
    assert result["launch"]["render"] == {
        "status": "failed",
        "code": "engine_files_incomplete",
        "verified": False,
        "waited_ms": result["launch"]["render"]["waited_ms"],
    }


def test_a_render_that_finished_before_since_is_not_this_handoffs(tmp_path, monkeypatch, serving):
    """since 过滤：久远的旧渲染不是这次交接的结论（超出回拨余量）。"""
    d = _fig_project(tmp_path)
    old = receipt.SINCE_SKEW_S + 60
    import time as _t

    receipt.record_ok(
        str(d), "Fig1", registry_match=True, manifest_elements=5, now=lambda: _t.time() - old
    )
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    assert result["launch"]["render"]["reason"] == "timeout"


def test_no_instance_reachable_is_channel_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "candidates", lambda port: [])
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    assert result["ok"] is True
    assert result["launch"]["render"]["reason"] == "channel_unavailable"


def test_no_signal_within_the_budget_is_unverified_timeout(tmp_path, monkeypatch, serving):
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    render = result["launch"]["render"]
    assert result["ok"] is True
    assert render["status"] == "unverified" and render["reason"] == "timeout"


def test_render_wait_zero_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "wait", lambda *a, **k: pytest.fail("不该查询"))
    _, result = _open(tmp_path, monkeypatch, FakeLaunch())  # open_target 默认 0
    assert result["launch"]["render"]["reason"] == "not_waited"


def test_directory_handoff_has_no_target_stem(tmp_path, monkeypatch):
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", FakeLaunch())
    result = handoff.open_target(str(d), render_wait=20)
    assert result["launch"]["render"]["reason"] == "no_target_stem"


def test_a_figure_without_a_script_is_not_waited_for(tmp_path, monkeypatch):
    """不可参数化 = 界面不发起引擎渲染：不查、不等，直接 not_renderable。"""
    d = tmp_path / "gallery"
    d.mkdir()
    (d / "Fig9.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")  # 没有任何脚本产出它
    monkeypatch.setattr(handoff, "launch", FakeLaunch())
    monkeypatch.setattr(receipt, "wait", lambda *a, **k: pytest.fail("不该查询"))
    result = handoff.open_target(str(d / "Fig9.pdf"), render_wait=20)
    assert result["registry"]["parameterizable"] is False
    assert result["launch"]["render"]["reason"] == "not_renderable"


def test_cli_writes_nothing_to_disk_for_the_handoff_signal(tmp_path, monkeypatch, serving):
    """CLI 不再往磁盘写任何东西（没有期望文件、没有 session 目录里的新文件）。"""
    session = Path(session_client.session_dir())
    before = sorted(p.name for p in session.rglob("*")) if session.exists() else []
    fake = FakeLaunch(
        lambda t: receipt.record_ok(t.project, t.stem, registry_match=True, manifest_elements=1)
    )
    _open(tmp_path, monkeypatch, fake, render_wait=20)
    after = sorted(p.name for p in session.rglob("*")) if session.exists() else []
    assert after == before
    assert not (session / "handoff").exists()


def test_since_is_taken_before_launch(tmp_path, monkeypatch, serving):
    """转发给已在跑的实例时渲染可能比 launch 返回还早：since 在 launch 之前取。"""
    seen = {}
    real_wait = receipt.wait

    def spy_wait(project, stem, since, budget, **kw):
        seen["since"] = since
        return real_wait(project, stem, since, budget, **kw)

    monkeypatch.setattr(receipt, "wait", spy_wait)
    import time as _t

    stamp = {}

    def on_launch(t):
        stamp["launch"] = _t.time()
        receipt.record_ok(t.project, t.stem, registry_match=True, manifest_elements=2)

    _, result = _open(tmp_path, monkeypatch, FakeLaunch(on_launch), render_wait=20)
    assert seen["since"] <= stamp["launch"]
    assert result["launch"]["render"]["status"] == "ok"


def test_a_failed_launch_still_raises_and_does_not_query(tmp_path, monkeypatch):
    fake = FakeLaunch(raises=handoff.HandoffError("起不来", "launch_failed"))
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", fake)
    monkeypatch.setattr(receipt, "wait", lambda *a, **k: pytest.fail("不该查询"))
    with pytest.raises(handoff.HandoffError):
        handoff.open_target(str(d / "Fig1.pdf"), render_wait=20)


def test_launch_keys_other_than_render_are_untouched(tmp_path, monkeypatch, serving):
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    launch = dict(result["launch"])
    launch.pop("render")
    assert launch == {"mode": "desktop", "app": "/x/Tavotto", "handoff": "launched", "pid": 1}


def test_no_launch_has_no_render_block(tmp_path, monkeypatch):
    d = _fig_project(tmp_path)
    result = handoff.open_target(str(d / "Fig1.pdf"), launch_ui=False, render_wait=20)
    assert result["launch"] is None


def test_cli_exposes_render_wait_and_passes_it_through(monkeypatch, capsys):
    seen = {}

    def fake_open(path, **kw):
        seen.update(kw)
        return {
            "ok": True,
            "protocol": 1,
            "project": "/p",
            "stem": None,
            "registry": {},
            "launch": None,
        }

    monkeypatch.setattr(handoff, "open_target", fake_open)
    assert handoff.cli(["/x", "--json"]) == 0
    assert seen["render_wait"] == handoff.DEFAULT_RENDER_WAIT
    assert handoff.cli(["/x", "--json", "--render-wait", "0"]) == 0
    assert seen["render_wait"] == 0.0
    capsys.readouterr()


def test_protocol_version_is_still_one():
    assert locate.PROTOCOL_VERSION == 1


# ----------------- /api/engine/render 收尾处确实记结论 ---------------------
@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


class _Worker:
    def __init__(self, manifest=None, error=None):
        self.built = True
        self.rev = 1
        self.manifest = {"elements": [{"gid": "a"}]} if manifest is None else manifest
        self.error = error

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        if self.error:
            raise self.error
        return {"manifest": self.manifest, "warnings": []}


def _gallery(client, tmp_path):
    figs = tmp_path / "p"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    client.post("/api/projects/open", json={"path": str(figs)})
    return figs


def _stub(monkeypatch, worker, registered=True):
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: (
            {"script": "x.py", "entry": "main", "cost": "light"} if registered else None
        ),
    )
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **kw: worker)


def _render(client):
    return client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})


def _summary(figs, stem="p1"):
    got = receipt.clean_result(receipt.query(str(figs), stem, 0))
    return None if got is None else receipt.summarize(stem, got, 0, reason="x")


def test_render_endpoint_reports_success_with_manifest_and_registry_facts(
    client, tmp_path, monkeypatch
):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())
    assert _render(client).status_code == 200
    out = _summary(figs)
    assert out["status"] == "ok" and out["verified"] is True and out["manifest_elements"] == 1


def test_render_endpoint_with_an_empty_manifest_is_ok_but_not_verified(
    client, tmp_path, monkeypatch
):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker(manifest={"elements": []}))
    _render(client)
    out = _summary(figs)
    assert out["status"] == "ok" and out["verified"] is False


def test_render_endpoint_with_an_unregistered_stem_is_not_verified(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker(), registered=False)
    # 没登记 = 磁盘面板拿不到 worker（404）：渲染根本没发生，不得凭空记 ok
    _render(client)
    assert _summary(figs) is None


def test_render_endpoint_reports_a_worker_error_code(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    err = engine_pool.WorkerError("缺引擎文件", code="engine_files_incomplete")
    _stub(monkeypatch, _Worker(error=err))
    assert _render(client).status_code == 500
    assert receipt.clean_result(receipt.query(str(figs), "p1", 0)) == {
        "status": "failed",
        "code": "engine_files_incomplete",
    }


def test_render_endpoint_reports_a_worker_startup_error(client, tmp_path, monkeypatch):
    """worker 启动 / 环境解析阶段（取 worker 的那段）失败也要记结论，CLI 才不白等到 timeout。"""
    figs = _gallery(client, tmp_path)
    err = engine_pool.WorkerError("缺引擎文件", code="engine_files_incomplete")

    def boom(*a, **kw):
        raise err

    monkeypatch.setattr(m, "_engine_worker", boom)
    _render(client)
    assert receipt.clean_result(receipt.query(str(figs), "p1", 0)) == {
        "status": "failed",
        "code": "engine_files_incomplete",
    }


def test_render_endpoint_ignores_a_query_for_another_stem(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())
    _render(client)
    assert _summary(figs, "other") is None


def test_a_broken_ledger_never_breaks_the_render(client, tmp_path, monkeypatch):
    _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())

    def boom(*a, **kw):
        raise OSError("disk on fire")

    monkeypatch.setattr(receipt, "record_ok", boom)
    assert _render(client).status_code == 200


def test_cli_and_endpoint_agree_end_to_end(client, tmp_path, monkeypatch):
    """「唤起」期间界面发起渲染 → 结论表 → 真端点（scoped token）→ CLI 轮询客户端 → launch.render。"""
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())
    state = security.SessionState("boot")
    state.port = PORT
    base = f"http://127.0.0.1:{PORT}"

    def via_test_client(port, cred, project, stem, since):
        qs = f"?project={project}&stem={stem}&since={since}"
        r = m.app.test_client().get(
            STATUS_PATH + qs, headers={session_client.AUTH_HEADER: cred}, base_url=base
        )
        return receipt.clean_result(r.get_json()) or {"status": "pending"}

    monkeypatch.setattr(receipt, "fetch", via_test_client)
    monkeypatch.setattr(receipt, "candidates", lambda port: [(PORT, state.status_token)])

    def render_in_window(target):
        # 渲染请求走无认证旁路；查询请求才装上认证状态
        assert _render(client).status_code == 200
        m.app.config[security.STATE_KEY] = state

    monkeypatch.setattr(handoff, "launch", FakeLaunch(render_in_window))
    try:
        result = handoff.open_target(str(figs / "p1.pdf"), render_wait=20)
    finally:
        m.app.config.pop(security.STATE_KEY, None)
    assert result["launch"]["render"]["status"] == "ok"
    assert result["launch"]["render"]["verified"] is True


# ------------------------------- 插件三字段 -------------------------------
@pytest.fixture(scope="module")
def plugin():
    spec = importlib.util.spec_from_file_location("_plugin_handoff_states", PLUGIN_HANDOFF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _final(render=None, launch=True, ok=True):
    out = {"ok": ok}
    if launch:
        out["launch"] = {"mode": "desktop", **({"render": render} if render is not None else {})}
    else:
        out["launch"] = None
    return out


def test_plugin_protocol_constant_still_matches_the_locator(plugin):
    assert plugin.PROTOCOL == locate.PROTOCOL_VERSION == 1


def test_new_plugin_new_cli_verified(plugin):
    s = plugin.three_state(_final({"status": "ok", "verified": True, "waited_ms": 5}))
    assert s["launch_success"] is True
    assert s["render_success"] is True and s["verified_success"] is True
    assert s["render"]["status"] == "ok"


def test_new_plugin_new_cli_rendered_but_not_verified(plugin):
    s = plugin.three_state(_final({"status": "ok", "verified": False}))
    assert s["render_success"] is True and s["verified_success"] is False


def test_new_plugin_new_cli_961_shape(plugin):
    s = plugin.three_state(
        _final({"status": "failed", "code": "engine_files_incomplete", "verified": False})
    )
    assert s["launch_success"] is True
    assert s["render_success"] is False and s["verified_success"] is False
    assert s["render"]["code"] == "engine_files_incomplete"


def test_new_plugin_new_cli_unverified_is_unknown_not_failed(plugin):
    s = plugin.three_state(_final({"status": "unverified", "reason": "timeout", "verified": False}))
    assert s["launch_success"] is True
    assert s["render_success"] is None and s["verified_success"] is False


def test_new_plugin_old_cli_is_distinguishable_unknown(plugin):
    s = plugin.three_state(_final(render=None))  # 老 CLI：launch 里没有 render
    assert s["launch_success"] is True
    assert s["render_success"] is None and s["verified_success"] is False
    assert s["render"] == {"status": "unverified", "reason": "cli_without_render_signal"}


def test_verified_must_be_strictly_true(plugin):
    for bad in ("true", 1, None, "yes"):
        s = plugin.three_state(_final({"status": "ok", "verified": bad}))
        assert s["verified_success"] is False, bad
    # 一个不合规的 render（未知 status）按「老 CLI 没给」处理，不凭它宣称任何事
    s = plugin.three_state(_final({"status": "great", "verified": True}))
    assert s["render_success"] is None and s["verified_success"] is False


def test_no_launch_is_neither_launched_nor_failed(plugin):
    s = plugin.three_state(_final(launch=False))
    assert s["launch_success"] is None
    assert s["render_success"] is None and s["verified_success"] is False
    assert "render" not in s


# --- 真起插件脚本 + 假 CLI：旧/新 CLI × 新插件，退出码与 ok 不变 -----------
FAKE_CLI = """#!PYTHON
import json, os, sys
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
if "--no-launch" not in sys.argv:
    resp.setdefault("launch", {"mode": "desktop"})
print(json.dumps(resp, ensure_ascii=False))
"""


def _run_plugin(tmp_path, response):
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "tavotto"
    fake.write_text(FAKE_CLI.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response), encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = {**os.environ, "PATH": str(bin_dir), "FAKE_RESPONSE": str(resp_file)}
    env.pop("TAVOTTO_CLI", None)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_HANDOFF), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc, json.loads(proc.stdout.strip().splitlines()[-1])


BASE = {
    "ok": True,
    "project": "/p",
    "stem": "Fig1",
    "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []},
}


@posix_only
def test_plugin_end_to_end_961_keeps_exit_code_zero_but_reports_the_failure(tmp_path):
    resp = {
        **BASE,
        "launch": {
            "mode": "desktop",
            "render": {"status": "failed", "code": "engine_files_incomplete", "verified": False},
        },
    }
    proc, out = _run_plugin(tmp_path, resp)
    assert proc.returncode == 0  # ok 的公开语义不变
    assert out["ok"] is True and out["parameterizable"] is True and out["launch"] == "desktop"
    assert out["launch_success"] is True
    assert out["render_success"] is False and out["verified_success"] is False
    assert out["render"]["code"] == "engine_files_incomplete"


@posix_only
def test_plugin_end_to_end_old_cli(tmp_path):
    proc, out = _run_plugin(tmp_path, BASE)  # 假 CLI 补 {"mode": "desktop"}，没有 render
    assert proc.returncode == 0
    assert out["ok"] is True and out["launch_success"] is True
    assert out["render_success"] is None and out["verified_success"] is False
    assert out["render"]["reason"] == "cli_without_render_signal"


@posix_only
def test_plugin_end_to_end_non_parameterizable_still_exits_four(tmp_path):
    resp = {
        **BASE,
        "registry": {"parameterizable": False, "conflicts": [], "dynamic_names": []},
        "launch": {"mode": "desktop", "render": {"status": "ok", "verified": True}},
    }
    proc, out = _run_plugin(tmp_path, resp)
    assert proc.returncode == 4 and out["error_code"] == "not_parameterizable"
    assert out["launch_success"] is True


@posix_only
def test_every_failure_payload_still_carries_the_three_fields(tmp_path):
    """没走到唤起的失败出口：launch 未成功、渲染未知、未确认——字段齐全，不缺键。"""
    proc, out = _run_plugin(tmp_path, {"ok": False, "code": "registry_write_failed", "error": "x"})
    assert proc.returncode == 2
    assert out["error_code"] == "registry_write_failed"
    assert out["launch_success"] is False
    assert out["render_success"] is None and out["verified_success"] is False


def test_plugin_missing_path_payload_has_the_three_fields(tmp_path):
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(PLUGIN_HANDOFF), str(tmp_path / "nope.pdf")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = json.loads(proc.stdout)
    assert out["launch_success"] is False and out["verified_success"] is False


# --------------------------- 同源对：文档与技能文本 -------------------------
def test_protocol_doc_and_skill_text_name_the_fields():
    doc = (ROOT / "docs" / "handoff-protocol.md").read_text(encoding="utf-8")
    ref = (SKILL_DIR / "references" / "desktop-handoff.md").read_text(encoding="utf-8")
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for token in ("launch_success", "render_success", "verified_success"):
        assert token in doc and token in ref, token
    assert "render_success" in skill and "verified_success" in skill
    assert "--render-wait" in doc
    # 文档里列的 unverified.reason 闭集与实现一致（单一来源 receipt.REASONS）
    for reason in receipt.REASONS:
        assert reason in doc, reason
    assert "cli_without_render_signal" in doc and "cli_without_render_signal" in ref


def test_plugin_unsupported_reason_is_documented_and_not_a_cli_reason(plugin):
    """`cli_without_render_signal` 只由插件产生（CLI 的闭集里没有它）。"""
    assert plugin.RENDER_UNSUPPORTED["reason"] == "cli_without_render_signal"
    assert "cli_without_render_signal" not in receipt.REASONS


def test_docs_describe_the_http_query_and_not_a_receipt_file():
    doc = (ROOT / "docs" / "handoff-protocol.md").read_text(encoding="utf-8")
    adr = (ROOT / "docs" / "adr" / "0131-agent-verification-entry.md").read_text(encoding="utf-8")
    for text in (doc, adr):
        assert "/api/handoff/render-status" in text
        assert "session/handoff/" not in text
