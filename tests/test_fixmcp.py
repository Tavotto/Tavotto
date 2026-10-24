"""AI 修复 D3 的 MCP 修复模式与 CLI 会话（ADR 0149 修订 D3）。

判据的主语：

* **`tools/list` 的结果** 对 **`fixtools.REGISTRY`**（名字 / 描述 / schema 逐项相等，任何地方都没有 tier）；
* **回调端点的 HTTP 状态**（真 HTTP 打真 server，会话认证 guard 开着：错令牌 / 别的会话 / 已结束 / 已取消一律进不来）；
* **磁盘上的审计行**与**卡片**（证明 `tools/call` 走的是 `run_tool` 与 `ctx.ask`，不是 MCP 层自己另做一套）；
* **假 CLI 进程的生死**（取消后那个 pid 真的没了）与会话的 `outcome` / `reason`。

假 CLI = 一个小 Python 进程；假 MCP 客户端 = 它经管道对真的 `tavotto fix-mcp` 子进程说 JSON-RPC。
不起真实 codex / claude，不做真实模型调用。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from support import procprobe
from tavotto import app as appmod, security
from tavotto.engine import fixmcp, fixsession
from tavotto.engine.fixtools import REGISTRY, AuditLog

TOKEN_ENV = fixmcp.ENV_TOKEN


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    fixsession.SESSIONS.reset_for_tests()
    yield type("Box", (), {"home": home, "proj": proj, "tmp": tmp_path, "data": data})
    fixsession.SESSIONS.reset_for_tests()


class Served:
    """真 server + 开着的会话认证 guard（与 test_browser_auth 同一做法）。"""

    def __init__(self):
        self._srv = make_server("127.0.0.1", 0, appmod.app, threaded=True)
        self.port = self._srv.server_port
        self.state = security.SessionState("n" * 32, api_secret="s" * 32)
        self.state.port = self.port
        appmod.app.config[security.STATE_KEY] = self.state
        self.thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self._srv.shutdown()
        self.thread.join(timeout=5)
        appmod.app.config.pop(security.STATE_KEY, None)


@pytest.fixture
def served():
    s = Served()
    yield s
    s.stop()


FAKE_CLI_SLEEP = (
    "import sys, time; open(sys.argv[1], 'w').write(str(__import__('os').getpid())); time.sleep(25)"
)


def start_session(box, argv: list[str], *, scope: str = "pj", env_extra: dict | None = None, **kw):
    """起一个 CLI 会话；`argv` 是假 CLI 的命令行。返回 (session, token)。"""
    seen: dict = {}

    def build(fix_id: str, token: str, prompt: str) -> fixsession.CliLaunch:
        seen.update(fix_id=fix_id, token=token, prompt=prompt)
        env = {**os.environ, TOKEN_ENV: token, **(env_extra or {})}
        return fixsession.CliLaunch(
            argv=[a.replace("{FIX_ID}", fix_id) for a in argv], env=env, cwd=str(box.proj)
        )

    s = fixsession.SESSIONS.start(
        scope=scope,
        prep_session_id="p1",
        script="fig.py",
        project_root=box.proj,
        cli=build,
        audit=AuditLog(),
        ctx_kwargs={"home": box.home, "case_insensitive": False},
        **kw,
    )
    deadline = time.monotonic() + 5
    while "token" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    return s, seen["token"]


def wait_finished(s, timeout: float = 15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if s.state == fixsession.STATE_FINISHED:
            return
        time.sleep(0.05)
    raise AssertionError(f"session still {s.state}")


def http(url: str, token: str | None, *, body: dict | None = None, path_auth: bool = True):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[security.FIX_TOKEN_HEADER] = token
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=20
        ) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def audit_rows(box) -> list[dict]:
    path = box.data / "ai" / "fix_audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# =========================================================================== tools/list
def test_tools_list_equals_the_registry_and_never_carries_tier(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    status, out = http(f"{served.url}/api/ai/fix/{s.id}/tools", token)
    assert status == 200
    listed = {t["name"]: t for t in out["tools"]}
    assert set(listed) == set(REGISTRY) and len(listed) == len(out["tools"])
    for name, ft in REGISTRY.items():
        assert listed[name] == {
            "name": name,
            "description": ft.description,
            "inputSchema": ft.schema,
        }
    blob = json.dumps(out)
    assert "tier" not in blob and "waits_for_user" not in blob
    # MCP 服务把它原样翻成 tools/list 的结果
    cb = fixmcp.Callback(served.url, s.id, token)
    assert [t["name"] for t in cb.tools()] == list(REGISTRY)


# =========================================================================== 认证
def test_wrong_token_other_session_and_missing_token_are_all_refused(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    base = f"{served.url}/api/ai/fix/{s.id}"
    call = {"name": "read_run_report", "arguments": {}}
    assert http(f"{base}/tool", token, body=call)[0] == 200
    for bad in (None, "", "nope", token + "x", token[:-1]):
        assert http(f"{base}/tools", bad)[0] in (401, 403)
        assert http(f"{base}/tool", bad, body=call)[0] in (401, 403)
    # 令牌是按会话发的：另一个会话的令牌拿到这个会话上也不行
    s.cancel.set()
    wait_finished(s)
    s2, token2 = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    assert token2 != token
    assert http(f"{served.url}/api/ai/fix/{s2.id}/tool", token, body=call)[0] in (401, 403)
    assert http(f"{base}/tool", token2, body=call)[0] in (401, 403)
    assert http(f"{served.url}/api/ai/fix/{s2.id}/tool", token2, body=call)[0] == 200


def test_the_token_opens_only_the_two_callback_paths(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    for path in (f"/api/ai/fix/{s.id}", "/api/ai/capabilities", "/api/ping", "/api/projects"):
        status, _ = http(served.url + path, token)
        assert status == 401, path
    # 方法也卡死：tools 只认 GET，tool 只认 POST
    assert http(f"{served.url}/api/ai/fix/{s.id}/tool", token)[0] in (401, 403, 405)
    assert http(f"{served.url}/api/ai/fix/{s.id}/tools", token, body={})[0] in (401, 403, 405)


def test_token_stops_working_once_the_session_ended(box, served):
    s, token = start_session(box, [sys.executable, "-c", "pass"])
    wait_finished(s)
    assert s.outcome == fixsession.OUTCOME_FAILED
    base = f"{served.url}/api/ai/fix/{s.id}"
    assert http(f"{base}/tools", token)[0] in (401, 403)
    assert http(f"{base}/tool", token, body={"name": "read_run_report", "arguments": {}})[0] in (
        401,
        403,
    )
    assert not fixsession.SESSIONS.token_valid(s.id, token)


def test_token_stops_working_the_moment_the_session_is_cancelled(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    assert fixsession.SESSIONS.token_valid(s.id, token)
    fixsession.SESSIONS.cancel(s.id, "pj")
    assert not fixsession.SESSIONS.token_valid(s.id, token)
    assert http(f"{served.url}/api/ai/fix/{s.id}/tools", token)[0] in (401, 403)


def test_the_token_never_appears_in_the_public_state_or_audit(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    http(
        f"{served.url}/api/ai/fix/{s.id}/tool",
        token,
        body={"name": "read_run_report", "arguments": {}},
    )
    assert token not in json.dumps(fixsession.SESSIONS.get(s.id, "pj"))
    path = box.data / "ai" / "fix_audit.jsonl"
    assert path.is_file() and token not in path.read_text(encoding="utf-8")


def test_a_session_of_another_project_scope_is_not_reachable_through_the_public_api(box):
    s, _ = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"], scope="pj-a")
    with pytest.raises(fixsession.FixError):
        fixsession.SESSIONS.get(s.id, "pj-b")


# =========================================================================== tools/call 走 run_tool 与 ctx.ask
def test_tool_call_goes_through_run_tool_so_it_is_audited_and_unknown_tools_are_errors(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    base = f"{served.url}/api/ai/fix/{s.id}/tool"
    status, out = http(base, token, body={"name": "read_run_report", "arguments": {}})
    assert status == 200 and "content" in out and "is_error" in out
    status, out = http(base, token, body={"name": "run_command", "arguments": {}})
    assert status == 200 and out["is_error"] is True
    status, out = http(base, token, body={"name": "read_file", "arguments": "nope"})
    assert out["is_error"] is True  # 参数不是对象：run_tool 的 bad_arguments
    rows = [r for r in audit_rows(box) if r["session"] == s.id]
    assert [r["tool"] for r in rows] == ["read_run_report", "read_file"]
    assert rows[1]["result"]["code"] == "bad_arguments"


def test_a_tool_that_needs_consent_shows_the_users_card_and_waits_for_the_answer(box, served):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    base = f"{served.url}/api/ai/fix/{s.id}/tool"
    result: dict = {}

    def call():
        result["r"] = http(
            base, token, body={"name": "propose_fix", "arguments": {"summary": "装 numpy"}}
        )

    t = threading.Thread(target=call)
    t.start()
    end = time.monotonic() + 10
    while time.monotonic() < end and fixsession.SESSIONS.get(s.id, "pj")["state"] != "waiting":
        time.sleep(0.02)
    card = fixsession.SESSIONS.get(s.id, "pj")["card"]
    assert card and card["kind"] == "approve" and "numpy" in card["summary"]
    assert t.is_alive()  # 用户还没点：调用就挂着
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], "yes")
    t.join(timeout=10)
    status, out = result["r"]
    assert status == 200 and out["is_error"] is False and "approved" in out["content"]
    assert [r["tool"] for r in audit_rows(box) if r["session"] == s.id] == ["propose_fix"]


def test_cancel_returns_a_blocked_tool_call_and_kills_the_cli_process(box, served):
    pid_file = box.tmp / "cli.pid"
    s, token = start_session(box, [sys.executable, "-c", FAKE_CLI_SLEEP, str(pid_file)])
    end = time.monotonic() + 10
    while not pid_file.is_file() and time.monotonic() < end:
        time.sleep(0.02)
    pid = int(pid_file.read_text())
    base = f"{served.url}/api/ai/fix/{s.id}/tool"
    result: dict = {}
    t = threading.Thread(
        target=lambda: result.update(
            r=http(base, token, body={"name": "propose_fix", "arguments": {"summary": "x"}})
        )
    )
    t.start()
    end = time.monotonic() + 10
    while time.monotonic() < end and fixsession.SESSIONS.get(s.id, "pj")["state"] != "waiting":
        time.sleep(0.02)
    t0 = time.monotonic()
    fixsession.SESSIONS.cancel(s.id, "pj")
    t.join(timeout=10)
    assert not t.is_alive() and time.monotonic() - t0 < 8  # 不是等满卡片超时
    status, out = result["r"]
    assert status == 200 and out["is_error"] is True
    wait_finished(s, 10)
    assert s.outcome == fixsession.OUTCOME_CANCELLED
    assert procprobe.wait_gone(
        pid, timeout=10
    )  # 进程真的没了（Windows 上 os.kill(pid, 0) 会杀进程，探测只经 procprobe）


# =========================================================================== 会话结局
def test_cli_exiting_without_finish_is_failed_with_a_closed_reason_code(box):
    s, _ = start_session(box, [sys.executable, "-c", "print('I fixed it!')"])
    wait_finished(s)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "cli_no_finish")
    assert s.summary == ""  # stdout 一个字不当结论
    s2, _ = start_session(box, [sys.executable, "-c", "import sys; sys.exit(3)"])
    wait_finished(s2)
    assert (s2.outcome, s2.reason) == (fixsession.OUTCOME_FAILED, "cli_exit")


def test_a_cli_that_cannot_start_is_failed_not_a_crash(box):
    s, _ = start_session(box, [str(box.tmp / "no-such-cli")])
    wait_finished(s)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "cli_start")


def test_finish_tool_is_the_only_conclusion(box, served, monkeypatch):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    out = fixsession.SESSIONS.call_tool(
        s.id, token, "finish", {"outcome": "fixed", "summary": "装好了 numpy"}
    )
    assert out["is_error"] is False
    # 之后的工具调用被拒（会话已交出结论）
    again = fixsession.SESSIONS.call_tool(s.id, token, "read_run_report", {})
    assert again["is_error"] is True
    # 进程自己没退出：给收尾宽限，到点只结束自己起的那个
    monkeypatch.setattr(fixsession, "FINISH_GRACE", 0.3)
    wait_finished(s, 15)
    assert (s.outcome, s.summary, s.reason) == (fixsession.OUTCOME_FIXED, "装好了 numpy", "")


def test_a_cli_that_overruns_the_budget_is_stopped_and_failed(box):
    s, _ = start_session(
        box,
        [sys.executable, "-c", "import time; time.sleep(25)"],
        limits=fixsession.Limits(
            max_turns=1, max_total_tokens=1, max_seconds=0.5, max_output_tokens=1
        ),
    )
    wait_finished(s, 15)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "budget")


# =========================================================================== 整条链：假 CLI → 真的 `tavotto fix-mcp` 子进程 → 回调 → run_tool
FAKE_MCP_CLIENT = r"""
import json, os, subprocess, sys
url, fid = sys.argv[1], sys.argv[2]
p = subprocess.Popen(
    [sys.executable, "-m", "tavotto", "fix-mcp", "--url", url, "--fix-id", fid],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=os.environ.copy(),
)
n = 0
def rpc(method, params=None):
    global n
    n += 1
    p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n").encode())
    p.stdin.flush()
    return json.loads(p.stdout.readline())
rpc("initialize", {"protocolVersion": "2025-06-18"})
names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
assert "finish" in names, names
rpc("tools/call", {"name": "read_run_report", "arguments": {}})
r = rpc("tools/call", {"name": "finish", "arguments": {"outcome": "not_fixed", "summary": "没能修好"}})
assert r["result"]["isError"] is False, r
p.stdin.close()
p.wait(timeout=10)
"""


def test_fake_mcp_client_through_the_real_server_process_reaches_run_tool_and_finishes(box, served):
    fake = box.tmp / "fake_cli.py"
    fake.write_bytes(FAKE_MCP_CLIENT.encode("utf-8"))
    s, _ = start_session(box, [sys.executable, str(fake), served.url, "{FIX_ID}"])
    wait_finished(s, 30)
    assert (s.outcome, s.summary, s.reason) == (fixsession.OUTCOME_NOT_FIXED, "没能修好", "")
    assert [r["tool"] for r in audit_rows(box) if r["session"] == s.id] == [
        "read_run_report",
        "finish",
    ]
    # 令牌只经环境变量：子进程的命令行里没有它（假 CLI 把 env 原样交给 MCP 子进程）
    assert fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished"


def test_mcp_server_refuses_to_start_without_a_token_or_with_a_remote_url(monkeypatch, capsys):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert fixmcp.cli(["--url", "http://127.0.0.1:1", "--fix-id", "x"]) == 2
    monkeypatch.setenv(TOKEN_ENV, "t")
    assert fixmcp.cli(["--url", "http://evil.example.com", "--fix-id", "x"]) == 2
    assert capsys.readouterr().out == ""  # stdout 归协议独占：一个字节都没有


def test_mcp_callback_failures_become_tool_errors_not_protocol_errors():
    class Down:
        def open(self, *_a, **_k):
            raise urllib.error.URLError("down")

    cb = fixmcp.Callback("http://127.0.0.1:9", "fx", "t", opener=Down())
    out = _Pipe()
    server = fixmcp.FixMcpServer(cb, _Lines(), out)
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "finish", "arguments": {}},
        }
    )
    msg = out.messages()[0]
    assert msg["result"]["isError"] is True and "error" not in msg
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})
    assert out.messages()[1]["error"]["code"] == fixmcp.METHOD_NOT_FOUND


class _Lines:
    def readline(self):
        return b""


class _Pipe:
    def __init__(self):
        self.buf = b""

    def write(self, data):
        self.buf += data

    def flush(self):
        pass

    def messages(self):
        return [json.loads(line) for line in self.buf.splitlines()]


def test_loopback_requests_bypass_system_proxies(box, served, monkeypatch):
    s, token = start_session(box, [sys.executable, "-c", "import time; time.sleep(25)"])
    for k in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:1")  # 死代理：走了它就连不上
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert fixmcp.Callback(served.url, s.id, token).tools()


def test_launcher_is_the_tavotto_cli_subcommand():
    cmd = fixsession.mcp_launcher()
    assert cmd[-1] == "fix-mcp" and (Path(cmd[0]).name or cmd[0])
    assert "fix-mcp" in __import__("tavotto.engine.cli", fromlist=["COMMANDS"]).COMMANDS
