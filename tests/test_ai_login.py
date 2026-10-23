"""官方登录按钮（`POST /api/ai/agents/<id>/login`）。

盯的是这几条纪律：
  * 只启动**官方命令**——argv = 探测通过的真实启动 argv + 适配器写死的子命令，请求体里
    没有、也不接受任何命令字段；环境与探测、运行是同一份 `spawn_env`；
  * background 同一 Agent 同时只有一个进程，超时由 Tavotto 结束它自己起的那一个；
  * terminal 只负责打开系统终端（三平台各一条路径），不发全局按键、不碰 System Events；
  * 通用层没有 `if agent == "codex"`：Fake Adapter 声明登录规格就能走通；
  * **所有用例都是桩**——绝不对真实用户的 ~/.codex / ~/.claude 执行 login。
"""

import inspect
import io
import logging
import os
import re
import shlex
import subprocess
import sys

import pytest

from tavotto.engine import ai_agents, ai_bridge, config


class _FakeProc:
    """`subprocess.Popen` 的桩：像真进程一样——不结束就一直 wait，terminate 后才退出。"""

    def __init__(self, hang: bool = False):
        self.hang = hang  # True = 忽略 terminate 之外的一切，仅用于超时用例
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        import time

        end = time.time() + (timeout if timeout is not None else 30)
        while self.returncode is None and time.time() < end:
            if self.terminated or self.killed:
                self.returncode = -15
                break
            time.sleep(0.005)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("login", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class _FakeLoginAgent(ai_agents.AgentDefinition):
    """一个只存在于用例里的 Agent，声明了自己的登录规格。"""

    id = "fakelogin"
    display_name = "Fake Login"
    icon_key = "fake"
    command_names = ("fakelogin",)

    def login_spec(self):
        return ai_agents.LoginSpec(("signin", "--now"), "background", "Fake")

    def build_command(self, ctx):
        return ai_agents.SpawnSpec([*ctx.argv], {})

    def classify_event(self, line, state):
        return []


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ai_bridge.invalidate_capabilities()
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate(f"/fake/bin/{agent.id}", "path")],
    )
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    # 宿主环境（CI 跑机设了 XDG_CONFIG_HOME、开发机可能有代理）不许漏进「登录命令带哪些变量」的断言：
    # 闭集里 PATH / HOME / LANG / LC_ALL 之外的成员一律清掉，需要的用例自己在 spawn_env 里加
    for name in ai_bridge._TERMINAL_ENV_NAMES:
        if name not in ("PATH", "HOME", "LANG", "LC_ALL"):
            monkeypatch.delenv(name, raising=False)
    ai_bridge._LOGINS.clear()
    yield
    ai_bridge._LOGINS.clear()
    ai_bridge.invalidate_capabilities()


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.fixture
def popen(monkeypatch):
    """替换 ai_bridge 里的 Popen，返回调用记录。"""
    calls: list[dict] = []
    procs: list[_FakeProc] = []

    def fake_popen(argv, **kw):
        calls.append({"argv": list(argv), **kw})
        proc = _FakeProc(hang=bool(getattr(fake_popen, "hang", False)))
        proc.stdout = (
            io.BytesIO(getattr(fake_popen, "output", b""))
            if kw.get("stdout") == subprocess.PIPE
            else None
        )
        procs.append(proc)
        return proc

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", fake_popen)
    fake_popen.calls = calls
    fake_popen.procs = procs
    return fake_popen


def _wait_registry_empty(agent_id: str, timeout: float = 3.0) -> bool:
    import time

    end = time.time() + timeout
    while time.time() < end:
        if agent_id not in ai_bridge._LOGINS:
            return True
        time.sleep(0.01)
    return False


# ---------------- 规格与 capabilities ----------------------------------------


def test_login_specs_are_declared_by_the_adapters():
    codex = ai_agents.get_agent("codex").login_spec()
    claude = ai_agents.get_agent("claude").login_spec()
    assert (codex.argv_suffix, codex.mode, codex.account) == (("login",), "background", "ChatGPT")
    assert (claude.argv_suffix, claude.mode, claude.account) == (
        ("auth", "login"),
        "terminal",
        "Claude",
    )
    # 基类默认不支持登录——没有官方登录子命令就不画按钮
    assert _FakeAgentWithoutLogin().login_spec() is None


class _FakeAgentWithoutLogin(ai_agents.AgentDefinition):
    id = "nologin"


def test_capabilities_expose_login_feature_per_agent(monkeypatch):
    monkeypatch.setattr(
        ai_agents, "AGENT_REGISTRY", (*ai_agents.AGENT_REGISTRY, _FakeAgentWithoutLogin())
    )
    ai_bridge.invalidate_capabilities()
    by_id = {a["id"]: a for a in ai_bridge.capabilities(refresh=True)["agents"]}
    assert by_id["codex"]["features"]["login"] is True
    assert by_id["codex"]["login_account"] == "ChatGPT"
    assert by_id["claude"]["features"]["login"] is True
    assert by_id["claude"]["login_account"] == "Claude"
    assert by_id["nologin"]["features"]["login"] is False
    assert by_id["nologin"]["login_account"] is None


# ---------------- background ------------------------------------------------


def test_background_login_uses_real_argv_and_spawn_env(popen):
    out = ai_bridge.start_login("codex")
    assert out == {"started": True, "mode": "background"}
    (call,) = popen.calls
    # 探测通过的那份真实 argv + 适配器写死的子命令，没有别的
    assert call["argv"] == ["/fake/bin/codex", "login"]
    # 与探测 / 运行同一份环境
    assert call["env"]["PATH"] == ai_agents.spawn_env("/fake/bin/codex")["PATH"]
    # 不给它终端输入；输出走管道（只为取授权地址，见下面的用例），stderr 并进同一根管道
    assert call["stdin"] == subprocess.DEVNULL
    assert call["stdout"] == subprocess.PIPE and call["stderr"] == subprocess.STDOUT
    popen.procs[0].returncode = 0
    assert _wait_registry_empty("codex")


def test_request_body_cannot_inject_a_command(client, popen):
    resp = client.post(
        "/api/ai/agents/codex/login", json={"argv": ["/bin/sh", "-c", "id"], "agent": "claude"}
    )
    assert resp.status_code == 200 and resp.get_json()["started"] is True
    assert popen.calls[0]["argv"] == ["/fake/bin/codex", "login"]
    popen.procs[0].returncode = 0


def test_only_one_background_login_at_a_time(popen):
    first = ai_bridge.start_login("codex")
    second = ai_bridge.start_login("codex")
    assert first["started"] is True
    assert second == {"started": False, "mode": "background", "already_running": True}
    assert len(popen.calls) == 1
    # 进程结束后允许重新来一次
    popen.procs[0].returncode = 0
    assert _wait_registry_empty("codex")
    assert ai_bridge.start_login("codex")["started"] is True
    assert len(popen.calls) == 2
    popen.procs[1].returncode = 0


def test_login_status_follows_the_process(client, popen):
    assert client.get("/api/ai/agents/codex/login").get_json() == {"status": "idle"}
    ai_bridge.start_login("codex")
    assert client.get("/api/ai/agents/codex/login").get_json() == {"status": "running"}
    popen.procs[0].returncode = 0
    assert client.get("/api/ai/agents/codex/login").get_json() == {"status": "idle"}
    assert _wait_registry_empty("codex")


def test_login_timeout_terminates_only_our_own_process(popen, monkeypatch):
    monkeypatch.setattr(ai_bridge, "LOGIN_TIMEOUT_S", 0.05)
    popen.hang = True
    ai_bridge.start_login("codex")
    proc = popen.procs[0]
    assert _wait_registry_empty("codex"), "超时后登记必须清掉"
    assert proc.terminated is True
    # 能 terminate 掉就不必 kill
    assert proc.killed is False


def test_spawn_failure_is_a_stable_error(client, monkeypatch):
    def boom(argv, **kw):
        raise OSError("nope")

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", boom)
    resp = client.post("/api/ai/agents/codex/login")
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "ai_agent_login_failed"
    assert "codex" not in ai_bridge._LOGINS


# ---------------- 授权地址（浏览器没打开时给用户点）-------------------------

_SECRET = "eyJhbGciOiJIUzI1NiJ9.SECRETTOKEN.sig sk-live-0123456789abcdef"
_AUTH = "https://auth.openai.com/oauth/authorize?response_type=code&state=s1&code_challenge=c1"


def _wait_for(pred, timeout: float = 3.0) -> bool:
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _status(client=None):
    return ai_bridge.login_status("codex")


def test_only_the_allowlisted_auth_url_is_kept_and_nothing_else(popen, caplog):
    """读线程只留主机命中的授权地址；形似 token 的串 / 其它地址 / 其它行都不进登记、不进日志。"""
    popen.output = (
        f"Starting local login server\n{_SECRET}\n"
        "https://evil.example/x?token=abc\n"
        "https://auth.openai.com.evil.example/oauth/authorize?state=1\n"
        "https://auth.openai.com@evil.example/oauth/authorize\n"
        f"If your browser did not open, visit \x1b[1m{_AUTH}\x1b[0m\n"
        "https://auth.openai.com/oauth/authorize?state=second\n"
    ).encode()
    with caplog.at_level(logging.DEBUG):
        ai_bridge.start_login("codex")
        assert _wait_for(lambda: "auth_url" in ai_bridge._LOGINS["codex"])
    assert _status() == {"status": "running", "auth_url": _AUTH}  # 第一条命中的，ANSI 已剥掉
    state = ai_bridge._LOGINS["codex"]
    assert set(state) == {"proc", "started", "auth_url"}
    blob = repr({k: v for k, v in state.items() if k != "proc"}) + caplog.text
    for secret in ("SECRETTOKEN", "sk-live", "evil.example", "second"):
        assert secret not in blob
    popen.procs[0].returncode = 0
    assert _wait_registry_empty("codex")


def test_no_matching_url_leaves_no_trace(popen, caplog):
    popen.output = f"{_SECRET}\nhttps://evil.example/?u=https://auth.openai.com/x\n".encode()
    with caplog.at_level(logging.DEBUG):
        ai_bridge.start_login("codex")
        assert _wait_for(lambda: popen.procs[0].stdout.tell() == len(popen.output))  # 读线程已读完
    assert _status() == {"status": "running"}
    assert "auth_url" not in ai_bridge._LOGINS["codex"]
    assert "SECRETTOKEN" not in caplog.text and "sk-live" not in caplog.text
    popen.procs[0].returncode = 0
    assert _wait_registry_empty("codex")


def test_output_beyond_the_byte_cap_is_discarded(popen, monkeypatch):
    monkeypatch.setattr(ai_bridge, "LOGIN_OUTPUT_CAP", 1024)
    popen.output = (b"x" * 100 + b"\n") * 30 + _AUTH.encode() + b"\n"
    ai_bridge.start_login("codex")
    assert _wait_for(lambda: popen.procs[0].stdout.tell() == len(popen.output))
    assert _status() == {"status": "running"}
    popen.procs[0].returncode = 0
    assert _wait_registry_empty("codex")


# ---------------- 拒绝路径 ---------------------------------------------------


def test_agent_without_login_spec_is_refused(client, monkeypatch, popen):
    monkeypatch.setattr(
        ai_agents, "AGENT_REGISTRY", (*ai_agents.AGENT_REGISTRY, _FakeAgentWithoutLogin())
    )
    resp = client.post("/api/ai/agents/nologin/login")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "ai_agent_login_unsupported"
    assert popen.calls == []


def test_linux_manual_fallback_uses_the_resolved_quoted_path(monkeypatch, popen):
    _terminal_agent(monkeypatch)  # 解析到 /opt/my tools/claude
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(ai_bridge.shutil, "which", lambda exe, *a, **k: None)
    out = ai_bridge.start_login("claude")
    assert out["command"] == "'/opt/my tools/claude' auth login"


def test_linux_manual_fallback_carries_login_env_but_no_secrets(monkeypatch, popen):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(ai_bridge.shutil, "which", lambda exe, *a, **k: None)
    real_spawn_env = ai_agents.spawn_env

    def env_with_extras(path, *a, **k):
        env = dict(real_spawn_env(path, *a, **k))
        env.update(
            CLAUDE_CONFIG_DIR="/x y",
            HTTPS_PROXY="http://proxy:8080",
            ANTHROPIC_API_KEY="sk-secret",
            HOME="/home/someone",
        )
        return env

    monkeypatch.setattr(ai_agents, "spawn_env", env_with_extras)
    cmd = ai_bridge.start_login("claude")["command"]
    assert shlex.split(cmd)[0] == "env"
    assert "CLAUDE_CONFIG_DIR='/x y'" in cmd or "'CLAUDE_CONFIG_DIR=/x y'" in cmd
    assert "HTTPS_PROXY=http://proxy:8080" in cmd
    assert "sk-secret" not in cmd and "ANTHROPIC_API_KEY" not in cmd
    assert "HOME=" not in cmd and "PATH=" not in cmd
    assert cmd.endswith("'/opt/my tools/claude' auth login")


def test_unknown_agent_and_not_installed(client, monkeypatch, popen):
    for resp in (
        client.post("/api/ai/agents/opencode/login"),
        client.get("/api/ai/agents/opencode/login"),
    ):
        assert resp.status_code == 400 and resp.get_json()["code"] == "ai_agent_unknown"
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    ai_bridge.invalidate_capabilities()
    resp = client.post("/api/ai/agents/codex/login")
    assert resp.status_code == 409 and resp.get_json()["code"] == "ai_agent_not_installed"
    assert popen.calls == []


# ---------------- 通用层没有 agent 分支 --------------------------------------


def test_a_third_agent_login_needs_no_new_branch(monkeypatch, popen):
    monkeypatch.setattr(ai_agents, "AGENT_REGISTRY", (*ai_agents.AGENT_REGISTRY, _FakeLoginAgent()))
    ai_bridge.invalidate_capabilities()
    fake = next(a for a in ai_bridge.capabilities(refresh=True)["agents"] if a["id"] == "fakelogin")
    assert fake["features"]["login"] is True and fake["login_account"] == "Fake"
    assert ai_bridge.start_login("fakelogin") == {"started": True, "mode": "background"}
    assert popen.calls[0]["argv"] == ["/fake/bin/fakelogin", "signin", "--now"]
    # 适配器没声明授权地址主机 = 输出直接丢弃，不开管道
    assert popen.calls[0]["stdout"] == subprocess.DEVNULL
    popen.procs[0].returncode = 0


def test_generic_login_layer_names_no_agent():
    for fn in (
        ai_bridge.start_login,
        ai_bridge._login_argv,
        ai_bridge._open_in_terminal,
        ai_bridge.login_status,
    ):
        src = inspect.getsource(fn)
        assert not re.search(r"""["'](codex|claude)["']""", src), fn.__name__
        assert not re.search(r"agent(_id)?\s*(==|in)\s*[\(\[\"']", src), fn.__name__


# ---------------- terminal 三平台 --------------------------------------------


def _terminal_agent(monkeypatch):
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate("/opt/my tools/claude", "path")],
    )
    ai_bridge.invalidate_capabilities()


def test_terminal_login_on_macos_opens_terminal_with_a_command_file(monkeypatch, tmp_path):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    ran: list[list[str]] = []

    def fake_run(argv, **kw):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(ai_bridge.subprocess, "run", fake_run)
    out = ai_bridge.start_login("claude")
    assert out == {"started": True, "mode": "terminal", "command": "claude auth login"}
    (argv,) = ran
    # 只用 open -a Terminal：不是 osascript，更不是 System Events 按键
    assert argv[:3] == ["open", "-a", "Terminal"]
    script = open(argv[3], encoding="utf-8").read()
    assert "System Events" not in script and "osascript" not in script
    assert "'/opt/my tools/claude' auth login" in script
    assert 'rm -f "$0"' in script
    assert (tmp_path / "cache" / "login_terminal").is_dir()


def test_macos_command_file_exports_cli_env_but_never_secrets(monkeypatch, tmp_path):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    real_spawn_env = ai_agents.spawn_env

    def env_with_extras(path, *a, **k):
        env = dict(real_spawn_env(path, *a, **k))
        env.update(
            CLAUDE_CONFIG_DIR="/Users/x/my cfg",
            HTTPS_PROXY="http://127.0.0.1:7890",
            ANTHROPIC_API_KEY="sk-secret",
            RANDOM_VAR="zzz-unrelated",
        )
        return env

    monkeypatch.setattr(ai_agents, "spawn_env", env_with_extras)
    ran: list[list[str]] = []
    monkeypatch.setattr(
        ai_bridge.subprocess,
        "run",
        lambda argv, **kw: ran.append(list(argv)) or subprocess.CompletedProcess(argv, 0, b"", b""),
    )
    ai_bridge.start_login("claude")
    script = open(ran[0][3], encoding="utf-8").read()
    assert "export CLAUDE_CONFIG_DIR='/Users/x/my cfg'\n" in script
    assert "export HTTPS_PROXY=http://127.0.0.1:7890\n" in script
    assert "export PATH=" in script
    assert "sk-secret" not in script and "ANTHROPIC_API_KEY" not in script
    assert "RANDOM_VAR" not in script and "zzz-unrelated" not in script
    if (
        os.name != "nt"
    ):  # 这条用例在任何宿主上模拟 macOS；Windows 没有 POSIX 权限位，chmod 0o700 读回来是 0o666
        assert oct(os.stat(ran[0][3]).st_mode & 0o777) == "0o700"


def test_terminal_login_on_windows_opens_a_new_console(monkeypatch, popen):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: True)
    out = ai_bridge.start_login("claude")
    assert out["started"] is True and out["mode"] == "terminal"
    (call,) = popen.calls
    assert call["argv"] == ["/opt/my tools/claude", "auth", "login"]
    # CREATE_NEW_CONSOLE，不是 CREATE_NO_WINDOW：用户要看见这个窗口
    assert call["creationflags"] == 0x00000010


def test_terminal_login_on_linux_uses_first_available_terminal(monkeypatch, popen):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(
        ai_bridge.shutil,
        "which",
        lambda exe, *a, **k: "/usr/bin/gnome-terminal" if exe == "gnome-terminal" else None,
    )
    out = ai_bridge.start_login("claude")
    assert out["started"] is True
    (call,) = popen.calls
    assert call["argv"][:3] == ["/usr/bin/gnome-terminal", "--", "sh"]
    assert "'/opt/my tools/claude' auth login" in call["argv"][-1]


def test_terminal_login_on_linux_without_terminal_says_which_command(monkeypatch, popen):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(ai_bridge.shutil, "which", lambda exe, *a, **k: None)
    out = ai_bridge.start_login("claude")
    assert out == {
        "started": False,
        "mode": "terminal",
        "reason": "terminal_unavailable",
        "command": "'/opt/my tools/claude' auth login",
    }
    assert popen.calls == []


def test_terminal_open_failure_is_a_stable_error(monkeypatch, tmp_path):
    _terminal_agent(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ai_agents, "_is_windows", lambda: False)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ai_bridge.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"x"),
    )
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.start_login("claude")
    assert exc.value.code == "ai_agent_login_failed"
    assert list((tmp_path / "cache" / "login_terminal").glob("*.command")) == []


# ---------------- 就绪检查不变 -----------------------------------------------


def test_login_never_changes_readiness_probe_commands(monkeypatch):
    """登录按钮不动就绪检查：仍然只跑 `login status` / `auth status`。"""
    seen: list[list[str]] = []

    def probe(argv, timeout=10):
        seen.append(list(argv))
        return (1, "")

    monkeypatch.setattr(ai_agents, "_run_probe", probe)
    ai_agents.get_agent("codex").readiness(["/c/codex"])
    ai_agents.get_agent("claude").readiness(["/c/claude"])
    assert seen == [["/c/codex", "login", "status"], ["/c/claude", "auth", "status"]]


# ---------------- 关停：后台登录进程随应用一起收掉（#1324 评审）-----------------


def test_stop_logins_ends_registered_fake_process_and_clears_registry(popen):
    ai_bridge.start_login("codex")
    proc = popen.procs[0]
    assert "codex" in ai_bridge._LOGINS
    assert ai_bridge.stop_logins(grace=1) == 1
    assert proc.terminated is True and proc.returncode is not None
    assert ai_bridge._LOGINS == {}


def test_stop_logins_kills_a_process_that_ignores_terminate(monkeypatch):
    class _Stubborn(_FakeProc):
        def wait(self, timeout=None):
            if self.killed:
                self.returncode = -9
                return self.returncode
            raise subprocess.TimeoutExpired("login", timeout)

    proc = _Stubborn()
    ai_bridge._LOGINS["codex"] = {"proc": proc, "started": 0.0}
    assert ai_bridge.stop_logins(grace=0.01) == 1
    assert proc.terminated and proc.killed
    assert ai_bridge._LOGINS == {}


def test_stop_logins_really_stops_a_real_child_and_ignores_finished_ones():
    real = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    try:
        ai_bridge._LOGINS["codex"] = {"proc": real, "started": 0.0}
        ai_bridge._LOGINS["claude"] = {"proc": done, "started": 0.0}
        assert ai_bridge.stop_logins(grace=5) == 1  # 只数真正在跑的
        assert real.poll() is not None, "真实子进程必须已被结束并回收"
        assert ai_bridge._LOGINS == {}
    finally:
        if real.poll() is None:
            real.kill()
            real.wait()


def test_api_shutdown_stops_background_logins(monkeypatch):
    """/api/shutdown 之后是 os._exit——daemon 线程不会替我们收登录进程，所以它必须自己调 stop_logins。"""
    import types

    from tavotto import app as m

    called: list[str] = []
    monkeypatch.setenv("TAVOTTO_ALLOW_SHUTDOWN", "1")
    monkeypatch.setattr(m, "reset_projects", lambda wait=False: None)
    monkeypatch.setattr(m.engine_ai, "interrupt_all", lambda: 0)
    monkeypatch.setattr(m.engine_ai, "stop_logins", lambda *a, **k: called.append("stop") or 0)

    class _NoThread:  # 不真的起 `os._exit` 线程
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(m, "threading", types.SimpleNamespace(Thread=_NoThread))
    m.app.config["TESTING"] = True
    assert m.app.test_client().post("/api/shutdown").status_code == 200
    assert called == ["stop"]


def test_desktop_sidecar_cleanup_also_stops_background_logins():
    from tavotto import desktop

    src = inspect.getsource(desktop)
    assert "engine_ai.stop_logins" in src
