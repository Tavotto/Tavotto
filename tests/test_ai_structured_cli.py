"""`ai_structured` 的订阅登录（CLI）路径：Codex / Claude Code 的无工具单轮结构化调用（ADR 0178）。

主语：**调用方看到的结局** + **起进程时的命令行**。本文件**永不起真 CLI、永不调模型**：「跑进程」是注入的假 `runner`
（真起进程的 `_run_process` 在 conftest 里被钉成一碰就红）；argv 快照直接问适配器要命令，不执行。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from support import procprobe
from tavotto.engine import ai_agents, ai_bridge, ai_structured as S

REAL_FIND = S.find_cli_connection
REAL_RUN = S._run_process  # conftest 的 autouse 会把模块上的换成「没有」；本文件要测真的就从这里取

JSON_OK = '{"answer": 1}'
USER_SENTINEL = "USER-CELL-SECRET-XYZ"
SYSTEM_TEXT = "SYSTEM-TEXT-ABC"
TOKEN_LIKE = "sk-live-NOT-A-REAL-KEY"

CODEX_CONN = {"kind": "cli", "agent": "codex", "model": "gpt-test", "conn_id": "sub:codex"}
CLAUDE_CONN = {"kind": "cli", "agent": "claude", "model": "claude-test", "conn_id": "sub:claude"}


def validate_answer(obj: dict) -> dict:
    if obj.get("answer") != 1:
        raise ValueError("shape")
    return obj


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setattr(
        ai_agents,
        "resolve",
        lambda agent, probe_readiness=True: ai_agents.Resolution(
            argv=[f"/fake/{agent.id}"], path=f"/fake/{agent.id}"
        ),
    )


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class FakeRunner:
    """按脚本依次回应每次起进程。脚本项：`"text"`（正常回复）、`("exit", n)`、`"timeout"`、`"launch"`、`("codex_fail", status)`、
    `("claude_fail", status)`、`("raw", stdout)`、`"empty"`、callable（拿到 `CliRun`、回 `CliProc`）。"""

    def __init__(self, script, agent="codex"):
        self.script = list(script)
        self.agent = agent
        self.runs: list[S.CliRun] = []
        self.cwd_listing: list[list[str]] = []
        self.aux_seen: list[list[str]] = []

    def __call__(self, run: S.CliRun) -> S.CliProc:
        self.runs.append(run)
        self.cwd_listing.append(sorted(os.listdir(run.cwd)))
        out_file = _flag_value(run.argv, "-o") if "-o" in run.argv else None
        if out_file:
            self.aux_seen.append(sorted(os.listdir(os.path.dirname(out_file))))
        item = self.script.pop(0)
        if callable(item):
            return item(run)
        if item == "timeout":
            return S.CliProc(None, timed_out=True)
        if item == "launch":
            return S.CliProc(None, launch_failed=True)
        if item == "empty":
            return self._reply("")
        if isinstance(item, tuple) and item[0] == "exit":
            return S.CliProc(item[1], "")
        if isinstance(item, tuple) and item[0] == "codex_fail":
            ev = {"type": "turn.failed", "error": {"message": f"unexpected status {item[1]} X: y"}}
            return S.CliProc(1, json.dumps({"type": "thread.started"}) + "\n" + json.dumps(ev))
        if isinstance(item, tuple) and item[0] == "claude_fail":
            ev = {"type": "result", "is_error": True, "api_error_status": item[1], "result": "e"}
            return S.CliProc(1, json.dumps(ev))
        if isinstance(item, tuple) and item[0] == "raw":
            return S.CliProc(0, item[1])
        return self._reply(item)

    def _reply(self, text: str) -> S.CliProc:
        if self.agent == "codex":
            out = _flag_value(self.runs[-1].argv, "-o")
            Path(out).write_text(text, encoding="utf-8")
            return S.CliProc(0, json.dumps({"type": "turn.completed"}))
        return S.CliProc(0, json.dumps({"type": "result", "is_error": False, "result": text}))


def call(script, *, conn=CODEX_CONN, limits=S.DEFAULT_LIMITS, validate=validate_answer, **kw):
    runner = FakeRunner(script, conn["agent"])
    res = S.call_structured(
        system=SYSTEM_TEXT,
        user=USER_SENTINEL,
        validate=validate,
        limits=limits,
        conn=conn,
        runner=runner,
        **kw,
    )
    return res, runner


# --------------------------------------------------------------------------- 正常 / 失败闭集
@pytest.mark.parametrize("conn", [CODEX_CONN, CLAUDE_CONN], ids=["codex", "claude"])
def test_a_good_reply_comes_back_validated_with_honest_usage(conn):
    res, runner = call([JSON_OK], conn=conn)
    assert res.ok and res.value == {"answer": 1}
    assert (res.attempts, res.requests) == (1, 1)
    assert res.usage_reported is False and res.cost_usd is None  # CLI 拿不到 token / 花费
    assert res.wire == f"cli:{conn['agent']}" and res.model == conn["model"]
    assert len(runner.runs) == 1


def test_claude_structured_output_field_is_accepted_as_the_reply():
    so = json.dumps({"type": "result", "is_error": False, "structured_output": {"answer": 1}})
    res, _ = call([("raw", so)], conn=CLAUDE_CONN)
    assert res.ok and res.value == {"answer": 1}


@pytest.mark.parametrize(
    ("script", "conn", "reason", "detail"),
    [
        ([("exit", 1)], CODEX_CONN, "transport", "exit"),
        ([("exit", 1)], CLAUDE_CONN, "transport", "exit"),
        ([("codex_fail", 401)], CODEX_CONN, "auth", "agent_auth"),
        ([("codex_fail", 429)], CODEX_CONN, "quota", "agent_quota"),
        ([("codex_fail", 503)], CODEX_CONN, "transport", "agent_network"),
        ([("claude_fail", 401)], CLAUDE_CONN, "auth", "agent_auth"),
        ([("claude_fail", 429)], CLAUDE_CONN, "quota", "agent_quota"),
        ([("claude_fail", 500)], CLAUDE_CONN, "transport", "exit"),  # 认不得的状态不猜
        (["timeout"], CODEX_CONN, "timeout", "time"),
        (["launch"], CODEX_CONN, "no_connection", "launch"),
        (["empty", "empty"], CODEX_CONN, "invalid_output", "empty"),
        (["not json", "still not json"], CODEX_CONN, "invalid_output", "not_json"),
        (['{"answer": 2}'], CODEX_CONN, "invalid_output", "shape"),
        ([("raw", "not even json")] * 2, CLAUDE_CONN, "invalid_output", "empty"),
    ],
)
def test_every_failure_lands_in_the_closed_set(script, conn, reason, detail):
    res, _ = call(script, conn=conn)
    assert not res.ok and res.value is None
    assert (res.reason, res.detail) == (reason, detail)
    assert res.reason in S.REASONS


def test_failure_does_not_retry_except_for_non_json():
    for script in ([("exit", 1)], [("codex_fail", 401)], ["timeout"], ['{"answer": 2}']):
        res, runner = call(script + ["SHOULD-NOT-BE-USED"])
        assert not res.ok and len(runner.runs) == 1, script


def test_non_json_is_retried_once_with_the_fixed_notice_and_no_previous_reply():
    res, runner = call(["garbage-reply-text", JSON_OK])
    assert res.ok and (res.attempts, res.requests) == (2, 2)
    first, second = runner.runs
    assert first.stdin == USER_SENTINEL
    assert second.stdin == f"{USER_SENTINEL}\n\n{S.RETRY_NOTICE}"
    assert "garbage-reply-text" not in second.stdin  # 上一次的回复不带回去


def test_retry_that_fails_again_is_invalid_output_after_exactly_two_processes():
    res, runner = call(["a", "b", "c"])
    assert (res.reason, res.attempts, len(runner.runs)) == ("invalid_output", 2, 2)


def test_retries_can_be_switched_off():
    res, runner = call(["a", JSON_OK], limits=S.Limits(max_retries=0))
    assert res.reason == "invalid_output" and len(runner.runs) == 1


def test_oversized_reply_is_over_budget_without_retry():
    res, runner = call(["x" * 100], limits=S.Limits(max_output_tokens=5))
    assert (res.reason, res.detail) == ("over_budget", "output") and len(runner.runs) == 1


def test_oversized_input_never_starts_a_process():
    res, runner = call([JSON_OK], limits=S.Limits(max_input_chars=10))
    assert (res.reason, res.detail) == ("over_budget", "input") and runner.runs == []


def test_max_requests_counts_processes():
    runner = FakeRunner(["bad", JSON_OK])
    res = S.call_structured(
        system="s",
        user="u",
        validate=validate_answer,
        conn=CODEX_CONN,
        runner=runner,
        max_requests=1,
    )
    assert (res.reason, res.detail) == ("over_budget", "requests") and len(runner.runs) == 1


def test_a_total_time_that_is_already_gone_starts_no_second_process():
    def slow_bad(run):
        time.sleep(0.12)
        Path(_flag_value(run.argv, "-o")).write_text("nope", encoding="utf-8")
        return S.CliProc(0, "")

    res, runner = call([slow_bad, JSON_OK], limits=S.Limits(timeout_s=0.1))
    assert (res.reason, res.detail) == ("timeout", "time") and len(runner.runs) == 1


def test_each_attempt_gets_the_time_that_is_left():
    res, runner = call(["bad", JSON_OK], limits=S.Limits(timeout_s=30))
    assert res.ok
    assert runner.runs[0].timeout_s <= 30 and runner.runs[1].timeout_s <= runner.runs[0].timeout_s


def test_the_work_dir_is_empty_the_files_are_elsewhere_and_everything_is_deleted_after():
    seen: dict = {}

    def spy(run):
        seen["cwd"] = run.cwd
        seen["listing"] = sorted(os.listdir(run.cwd))
        seen["root"] = os.path.dirname(run.cwd)
        out = _flag_value(run.argv, "-o")
        seen["aux"] = sorted(os.listdir(os.path.dirname(out)))
        Path(out).write_text(JSON_OK, encoding="utf-8")
        return S.CliProc(0, "")

    res, _ = call([spy])
    assert res.ok
    assert seen["listing"] == []  # 模型工作区里什么都没有
    assert seen["aux"] == ["system.md"]
    assert not os.path.exists(seen["root"])  # 用完删除


def test_the_temp_dir_is_deleted_on_failure_too():
    roots = []

    def spy(run):
        roots.append(os.path.dirname(run.cwd))
        return S.CliProc(None, timed_out=True)

    call([spy])
    assert roots and not os.path.exists(roots[0])


def test_unsupported_adapter_or_missing_cli_are_closed_set_failures(monkeypatch):
    class Plain(ai_agents.AgentDefinition):
        id = "plain"

    monkeypatch.setattr(ai_agents, "get_agent", lambda _id: Plain())
    res, runner = call([JSON_OK])
    assert (res.reason, res.detail) == ("unsupported_connection", "cli_only") and not runner.runs
    monkeypatch.undo()
    monkeypatch.setattr(
        ai_agents, "resolve", lambda agent, probe_readiness=True: ai_agents.Resolution()
    )
    res, runner = call([JSON_OK])
    assert (res.reason, res.detail) == ("no_connection", "not_installed") and not runner.runs


def test_a_runner_that_raises_is_transport_internal_never_an_exception():
    def boom(run):
        raise RuntimeError("PROMPT-LEAK " + run.stdin)

    runner = boom
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, conn=CODEX_CONN, runner=runner
    )
    assert (res.reason, res.detail) == ("transport", "internal")
    assert "PROMPT-LEAK" not in repr(res)


def test_the_result_never_carries_the_prompt_or_the_reply():
    res, _ = call([json.dumps({"answer": 1, "note": "REPLY-SECRET"})])
    assert USER_SENTINEL not in repr(res) and SYSTEM_TEXT not in repr(res)
    bad, _ = call(["REPLY-SECRET not json", "REPLY-SECRET again"])
    assert "REPLY-SECRET" not in repr(bad) and USER_SENTINEL not in repr(bad)


# --------------------------------------------------------------------------- 同一时刻只有一个 CLI 进程
def test_only_one_cli_process_at_a_time():
    active = [0, 0]  # 当前、峰值
    lock = threading.Lock()

    def slow(run):
        with lock:
            active[0] += 1
            active[1] = max(active[1], active[0])
        time.sleep(0.05)
        Path(_flag_value(run.argv, "-o")).write_text(JSON_OK, encoding="utf-8")
        with lock:
            active[0] -= 1
        return S.CliProc(0, "")

    results = []

    def work():
        results.append(
            S.call_structured(
                system="s", user="u", validate=validate_answer, conn=CODEX_CONN, runner=slow
            )
        )

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(results) == 4 and all(r.ok for r in results) and active[1] == 1


def test_the_slot_is_reentrant_for_the_same_thread():
    with S.cli_slot(1):
        res, _ = call([JSON_OK])  # `_call_cli` 里再占一次：同线程不死锁
    assert res.ok


def test_waiting_for_the_slot_longer_than_the_budget_is_a_timeout():
    release, held = threading.Event(), threading.Event()
    holder = threading.Thread(target=lambda: _hold(release, held), daemon=True)
    holder.start()
    assert held.wait(5)
    try:
        res, runner = call([JSON_OK], limits=S.Limits(timeout_s=0.2))
        assert (res.reason, res.detail) == ("timeout", "queue") and not runner.runs
    finally:
        release.set()
        holder.join(5)


def _hold(release, held):
    with S.cli_slot():
        held.set()
        release.wait(5)


# --------------------------------------------------------------------------- 起什么命令（只问适配器，不执行）
def _ctx(agent_id, *, schema=None, effort="low", tmp="/tmp/x"):
    return ai_agents.StructuredRunContext(
        argv=[f"/fake/{agent_id}"],
        system=SYSTEM_TEXT,
        user=USER_SENTINEL,
        model="m-test",
        cwd=f"{tmp}/work",
        aux_dir=f"{tmp}/aux",
        effort=effort,
        schema=schema,
    )


def _forbidden(argv: list[str]) -> None:
    joined = " ".join(argv)
    assert USER_SENTINEL not in joined and TOKEN_LIKE not in joined
    assert "--dangerously" not in joined and "danger-full-access" not in joined
    assert "workspace-write" not in joined and "--bare" not in argv
    assert "--add-dir" not in argv and "--worktree" not in joined


def test_codex_command_snapshot(monkeypatch):
    # 型号目录读取用 pathlib：`os.name` 被改掉之后 `Path` 会分派到另一半平台而炸（Windows 上改成 posix 就是这样），先桩掉
    monkeypatch.setattr(ai_agents.CodexAgent, "_catalog", classmethod(lambda cls: []))
    monkeypatch.setattr(ai_agents.os, "name", "posix")
    spec = ai_agents.get_agent("codex").build_structured_command(_ctx("codex"))
    assert spec is not None
    # 产品用 `os.path.join` 拼辅助文件（Windows 上分隔符是反斜杠）：期望值用同一个函数拼，不写死 `/`
    sys_file, out_file = (
        os.path.join("/tmp/x/aux", "system.md"),
        os.path.join("/tmp/x/aux", "out.txt"),
    )
    assert spec.argv == [
        "/fake/codex",
        "exec",
        "-C",
        "/tmp/x/work",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "-c",
        "skills.include_instructions=false",
        "-c",
        f"model_instructions_file={ai_agents.toml_string(sys_file)}",
        "-m",
        "m-test",
        "-o",
        out_file,
        "-",
    ]
    _forbidden(spec.argv)
    assert spec.stdin == USER_SENTINEL and spec.env == {}
    assert spec.files == {sys_file: SYSTEM_TEXT}
    assert spec.output_file == out_file


def test_claude_command_snapshot():
    spec = ai_agents.get_agent("claude").build_structured_command(_ctx("claude"))
    assert spec is not None
    assert spec.argv == [
        "/fake/claude",
        "-p",
        "--output-format",
        "json",
        "--system-prompt",
        SYSTEM_TEXT,
        "--tools",
        "",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--model",
        "m-test",
    ]
    _forbidden(spec.argv)
    i = spec.argv.index("--tools")
    assert spec.argv[i + 1] == "" and spec.argv[i + 2].startswith(
        "--"
    )  # 变长参数后必须紧跟另一个开关
    assert spec.stdin == USER_SENTINEL and spec.files == {} and spec.output_file is None


def test_schema_flags_only_when_a_schema_is_given():
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    c = ai_agents.get_agent("codex").build_structured_command(_ctx("codex", schema=schema))
    schema_file = os.path.join(
        "/tmp/x/aux", "schema.json"
    )  # 与产品同一个拼法（Windows 上是反斜杠）
    assert _flag_value(c.argv, "--output-schema") == schema_file
    assert json.loads(c.files[schema_file]) == schema
    k = ai_agents.get_agent("claude").build_structured_command(_ctx("claude", schema=schema))
    assert json.loads(_flag_value(k.argv, "--json-schema")) == schema


def test_effort_is_sent_only_when_the_catalog_confirms_it(tmp_path, monkeypatch):
    codex = ai_agents.get_agent("codex")
    base = codex.build_structured_command(_ctx("codex"))
    assert not any("model_reasoning_effort" in a for a in base.argv)  # 目录里没有这个型号 = 不发

    def catalog(cls):
        return [
            ai_agents.ModelDetail(
                id="m-test", display_name="m", efforts=["low", "high"], efforts_known=True
            )
        ]

    monkeypatch.setattr(ai_agents.CodexAgent, "_catalog", classmethod(catalog))
    sent = codex.build_structured_command(_ctx("codex"))
    assert "model_reasoning_effort=low" in sent.argv
    none = codex.build_structured_command(_ctx("codex", effort="xhigh"))
    assert not any("model_reasoning_effort" in a for a in none.argv)
    claude = ai_agents.get_agent("claude")
    monkeypatch.setattr(
        ai_agents.ClaudeAgent, "_catalog", classmethod(lambda cls: (catalog(cls), {}, {}))
    )
    assert _flag_value(claude.build_structured_command(_ctx("claude")).argv, "--effort") == "low"
    assert "--effort" not in claude.build_structured_command(_ctx("claude", effort="max")).argv


def test_windows_sandbox_override_is_reused_not_reinvented(monkeypatch):
    monkeypatch.setattr(ai_agents.CodexAgent, "_catalog", classmethod(lambda cls: []))
    monkeypatch.setattr(
        ai_agents.os, "name", "nt"
    )  # pathlib 在非 Windows 上会炸：目录读取已桩掉，函数里没有别的路径操作
    spec = ai_agents.get_agent("codex").build_structured_command(_ctx("codex"))
    i = spec.argv.index('windows.sandbox="unelevated"')
    assert spec.argv[i - 1] == "-c"
    assert _flag_value(spec.argv, "--sandbox") == "read-only"


def test_adapters_without_the_command_are_not_structured_capable():
    assert (
        ai_agents.CodexAgent.supports_structured() and ai_agents.ClaudeAgent.supports_structured()
    )
    assert not ai_agents.BuiltinAgent.supports_structured()
    assert ai_agents.AgentDefinition().build_structured_command(_ctx("x")) is None


# --------------------------------------------------------------------------- resolve_connection 的三种分流
def _caps(*conns):
    return {"connections": list(conns)}


def _sub(cid, **kw):
    return {"id": cid, "state": "ready", "default_model": "m1", "models": ["m1", "m2"], **kw}


@pytest.fixture
def real_find(monkeypatch):
    from tavotto.engine import ai_connections

    monkeypatch.setattr(S, "find_cli_connection", REAL_FIND)
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)


def test_subscription_only_resolves_to_a_cli_connection(monkeypatch, real_find):
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(_sub("sub:codex")))
    conn, why, detail = S.resolve_connection()
    assert conn == {"kind": "cli", "agent": "codex", "model": "m1", "conn_id": "sub:codex"}
    assert (why, detail) == ("", "")
    assert S.cli_identity(conn) == "cli:codex|m1"


def test_the_model_falls_back_to_the_first_listed_and_never_to_a_hardcoded_name(
    monkeypatch, real_find
):
    c = _sub("sub:claude", default_model=None)
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(c))
    assert S.resolve_connection()[0]["model"] == "m1"
    c2 = _sub("sub:claude", default_model=None, models=[])
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(c2))
    assert S.resolve_connection() == (None, "unsupported_connection", "cli_only")


def test_subscription_with_an_adapter_that_cannot_do_it_is_unsupported(monkeypatch, real_find):
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(_sub("sub:codex")))
    monkeypatch.setattr(
        ai_agents.CodexAgent,
        "build_structured_command",
        ai_agents.AgentDefinition.build_structured_command,
    )
    assert S.resolve_connection() == (None, "unsupported_connection", "cli_only")
    res = S.call_structured(system="s", user="u", validate=validate_answer)
    assert (res.reason, res.detail) == ("unsupported_connection", "cli_only")


def test_nothing_usable_is_no_connection(monkeypatch, real_find):
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps())
    assert S.resolve_connection() == (None, "no_connection", "none")
    # 需要重新登录的订阅不算连接
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: _caps(_sub("sub:codex", state="needs_auth")),
    )
    assert S.resolve_connection() == (None, "no_connection", "none")
    res = S.call_structured(system="s", user="u", validate=validate_answer)
    assert res.reason == "no_connection"


def test_a_key_connection_still_wins_over_a_subscription(monkeypatch, real_find):
    from tavotto.engine import ai_connections

    key = {"wire": "anthropic", "provider_id": "p", "model": "k"}
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: key)
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(_sub("sub:codex")))
    assert S.resolve_connection()[0] == key


def test_the_cached_only_lookup_never_probes(monkeypatch, real_find):
    def boom(refresh=False):
        raise AssertionError("probed")

    monkeypatch.setattr(ai_bridge, "capabilities", boom)
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: None)
    assert S.find_cli_connection(cached_only=True) == (None, "no_connection", "none")
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: _caps(_sub("sub:claude")))
    assert S.find_cli_connection(cached_only=True)[0]["agent"] == "claude"


def test_a_resolved_cli_connection_runs_through_call_structured(monkeypatch, real_find):
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: _caps(_sub("sub:codex")))
    runner = FakeRunner([JSON_OK], "codex")
    res = S.call_structured(system="s", user="u", validate=validate_answer, runner=runner)
    assert res.ok and _flag_value(runner.runs[0].argv, "-m") == "m1"


# --------------------------------------------------------------------------- 真起进程那一处（本机 Python 当「CLI」：不碰任何 CLI / 模型）
def test_the_real_runner_feeds_stdin_and_reads_stdout():
    run = S.CliRun(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        dict(os.environ),
        os.getcwd(),
        "hello",
        20,
    )
    proc = REAL_RUN(run)
    assert (proc.exit_code, proc.stdout, proc.timed_out, proc.launch_failed) == (
        0,
        "HELLO",
        False,
        False,
    )


def test_the_real_runner_reports_a_nonzero_exit_and_a_missing_executable():
    bad = S.CliRun(
        [sys.executable, "-c", "raise SystemExit(3)"], dict(os.environ), os.getcwd(), "", 20
    )
    assert REAL_RUN(bad).exit_code == 3
    gone = S.CliRun(["/definitely/not/here/cli"], dict(os.environ), os.getcwd(), "", 5)
    assert REAL_RUN(gone).launch_failed


@pytest.mark.skipif(os.name == "nt", reason="进程组语义按平台分开测；Windows 走 taskkill /T")
def test_the_real_runner_kills_the_whole_process_tree_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(60)\n"
    )
    t0 = time.monotonic()
    proc = REAL_RUN(
        S.CliRun([sys.executable, "-c", code], dict(os.environ), str(tmp_path), "", 4.0)
    )
    assert proc.timed_out and time.monotonic() - t0 < 20
    child = int(pidfile.read_text())
    assert procprobe.wait_gone(child, timeout=5)  # 孙进程也被收掉
