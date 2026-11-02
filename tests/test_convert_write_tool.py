"""转换会话的「代写目标文件」工具（`write_conversion_target`，TVD-BCZ0 方案 A，`engine/convertwrite.py`）。

判据的主语：

* **磁盘上的目标文件**（字节）与候选目录里**还有什么文件**（只能是那一个目标）——不是工具的返回文字；
* **回调端点的 HTTP 状态**（真 HTTP 打真 server，会话认证 guard 开着）与令牌的有效期（会话结束即失效）；
* **`CodexAgent.build_command` 的 argv**（所有平台都带 MCP 注入、令牌值不在其中）与转换提示词；
* **端到端**：假 Codex 进程先报一次 `file_change` 失败，再经真的 `fix-mcp` 回调代写 → 转换成功；工具失败 + 目标缺失 → `agent_write_denied`。

不起真实 codex，不做真实模型调用。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from support import linkkit
from tavotto import security
from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_convert,
    ai_prompt_pack,
    convertwrite,
    fixmcp,
)
from test_ai_convert import (
    GOOD,
    _unmet_codes,
    _wait,
    env as convert_env,  # noqa: F401 —— 夹具复用（别名，再包一层同名夹具）
)
from test_fixmcp import (
    Served,
    served as fixmcp_served,  # noqa: F401 —— 真 server + guard 开着
)

TOOL = fixmcp.CONVERT_TOOL_NAME


@pytest.fixture
def env(convert_env):  # noqa: F811
    return convert_env


@pytest.fixture
def served(fixmcp_served):  # noqa: F811
    return fixmcp_served


@pytest.fixture(autouse=True)
def _clean_grants():
    yield
    for sid in list(convertwrite._GRANTS):
        convertwrite.close(sid)


def _grant(tmp_path, sid="s1"):
    extras: dict = {}
    g = convertwrite.open_grant(sid, tmp_path / "fig_matplotlib.py", extras)
    return g, extras


# --------------------------------------------------------------------------- 工具本体
def test_write_creates_only_the_target_with_exact_bytes(tmp_path):
    g, extras = _grant(tmp_path)
    text = "import matplotlib\n# 中文注释\nx = 1\r\ny = 2"
    out = convertwrite.write("s1", g.token, text)
    assert out == {"ok": True, "bytes": len(text.encode("utf-8"))}
    assert (tmp_path / "fig_matplotlib.py").read_bytes() == text.encode("utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["fig_matplotlib.py"], "临时文件不留"
    assert extras["target_tool"] == {
        "calls": 1,
        "ok": True,
        "last_code": "",
        "bytes": len(text.encode("utf-8")),
    }
    assert convertwrite.ok("s1") is True


def test_second_call_replaces_the_whole_content(tmp_path):
    g, _ = _grant(tmp_path)
    convertwrite.write("s1", g.token, "a = 1\n")
    convertwrite.write("s1", g.token, "b = 2\n")
    assert (tmp_path / "fig_matplotlib.py").read_bytes() == b"b = 2\n"


def test_too_large_is_refused_and_target_untouched(tmp_path):
    g, extras = _grant(tmp_path)
    big = "x" * (convertwrite.MAX_BYTES + 1)
    assert convertwrite.write("s1", g.token, big) == {"ok": False, "code": "too_large"}
    assert not (tmp_path / "fig_matplotlib.py").exists()
    assert (
        extras["target_tool"]["ok"] is False and extras["target_tool"]["last_code"] == "too_large"
    )
    # 恰好等于上限可以写
    assert convertwrite.write("s1", g.token, "y" * convertwrite.MAX_BYTES)["ok"] is True


@pytest.mark.parametrize(
    "content,code",
    [
        ("", "empty"),
        (" \n\t", "empty"),
        (None, "bad_content"),
        (123, "bad_content"),
        ("\ud800", "bad_content"),
    ],
)
def test_bad_content_gets_a_closed_code(tmp_path, content, code):
    g, _ = _grant(tmp_path)
    assert convertwrite.write("s1", g.token, content) == {"ok": False, "code": code}
    assert code in convertwrite.CODES
    assert not (tmp_path / "fig_matplotlib.py").exists()


@pytest.mark.parametrize("token", [None, "", "wrong", 5])
def test_wrong_or_missing_token_is_forbidden(tmp_path, token):
    _grant(tmp_path)
    with pytest.raises(convertwrite.Forbidden):
        convertwrite.write("s1", token, "x = 1\n")
    assert not (tmp_path / "fig_matplotlib.py").exists()


def test_token_of_another_session_is_forbidden(tmp_path):
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    a = convertwrite.open_grant("sa", a_dir / "t.py", {})
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    b = convertwrite.open_grant("sb", b_dir / "t.py", {})
    with pytest.raises(convertwrite.Forbidden):
        convertwrite.write("sa", b.token, "x = 1\n")
    with pytest.raises(convertwrite.Forbidden):
        convertwrite.write("sb", a.token, "x = 1\n")
    assert convertwrite.write("sb", b.token, "x = 1\n")["ok"] is True
    assert not (tmp_path / "t.py").exists() and (b_dir / "t.py").exists()


def test_token_dies_when_the_session_ends(tmp_path):
    g, _ = _grant(tmp_path)
    assert convertwrite.token_valid("s1", g.token) is True
    convertwrite.close("s1")
    assert convertwrite.token_valid("s1", g.token) is False
    with pytest.raises(convertwrite.Forbidden):
        convertwrite.write("s1", g.token, "x = 1\n")
    convertwrite.close("s1")  # 幂等


def test_token_expires_on_its_own(tmp_path, monkeypatch):
    g, _ = _grant(tmp_path)
    real = convertwrite.time.monotonic()
    monkeypatch.setattr(convertwrite.time, "monotonic", lambda: real + convertwrite.GRANT_TTL_S + 1)
    assert convertwrite.token_valid("s1", g.token) is False


def test_reopening_a_session_replaces_the_token(tmp_path):
    old, _ = _grant(tmp_path)
    new, _ = _grant(tmp_path)
    assert old.token != new.token
    assert convertwrite.token_valid("s1", old.token) is False


def test_tool_has_no_path_parameter_and_cannot_write_elsewhere(tmp_path):
    props = fixmcp.CONVERT_TOOL["inputSchema"]["properties"]
    assert list(props) == ["content"] and fixmcp.CONVERT_TOOL["inputSchema"]["required"] == [
        "content"
    ]
    g, _ = _grant(tmp_path)
    # 端点只取 content；路径永远是授权里 Tavotto 定的那一个
    convertwrite.write("s1", g.token, "x = 1\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fig_matplotlib.py"]


def test_symlink_target_is_refused(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    cand = tmp_path / "cand"
    cand.mkdir()
    linkkit.symlink_or_skip(outside, cand / "fig_matplotlib.py")
    g = convertwrite.open_grant("s1", cand / "fig_matplotlib.py", {})
    assert convertwrite.write("s1", g.token, "x = 1\n") == {"ok": False, "code": "target_unsafe"}
    assert outside.read_bytes() == b"keep"


# --------------------------------------------------------------------------- HTTP 端点 + guard
def _post(served, sid, token, body):
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[security.CONVERT_TOKEN_HEADER] = token
    req = urllib.request.Request(
        f"{served.url}/api/ai/convert/{sid}/target",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_endpoint_accepts_only_the_session_token_and_only_post(served, tmp_path):
    g, _ = _grant(tmp_path)
    assert _post(served, "s1", None, {"content": "x = 1\n"})[0] == 401
    assert _post(served, "s1", "wrong", {"content": "x = 1\n"})[0] == 401
    assert _post(served, "other", g.token, {"content": "x = 1\n"})[0] == 401
    assert not (tmp_path / "fig_matplotlib.py").exists()
    status, out = _post(served, "s1", g.token, {"content": "x = 1\n"})
    assert (status, out) == (200, {"ok": True, "bytes": 6})
    assert (tmp_path / "fig_matplotlib.py").read_bytes() == b"x = 1\n"
    # 令牌换不到别的端点
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{served.url}/api/ai/convert/s1",
        headers={security.CONVERT_TOKEN_HEADER: g.token},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 401
    convertwrite.close("s1")
    assert _post(served, "s1", g.token, {"content": "y"})[0] == 401


# --------------------------------------------------------------------------- fixmcp 转换模式
def test_convert_mode_lists_one_fixed_tool_without_calling_back():
    def transport(*a):  # 转换模式 tools/list 不回调
        raise AssertionError("no callback expected")

    cb = fixmcp.Callback("http://127.0.0.1:4321", "s1", "tok", transport=transport, convert=True)
    assert cb.tools() == [fixmcp.CONVERT_TOOL]
    assert fixmcp.CONVERT_TOOL["name"] == "write_conversion_target"


def test_convert_mode_maps_results_and_forwards_only_content():
    seen = []

    def transport(port, method, path, body, headers, timeout):
        seen.append((method, path, json.loads(body), headers))
        return 200, json.dumps(reply[0]).encode()

    reply = [{"ok": True, "bytes": 7}]
    cb = fixmcp.Callback("http://127.0.0.1:4321", "s/1", "tok", transport=transport, convert=True)
    text, is_error = cb.call(TOOL, {"content": "abc", "path": "/etc/passwd"})
    assert is_error is False and "7 bytes" in text
    method, path, body, headers = seen[0]
    assert (method, path) == ("POST", "/api/ai/convert/s%2F1/target")
    assert body == {"content": "abc"}, "额外参数（如 path）一概不转发"
    assert headers[fixmcp.CONVERT_TOKEN_HEADER] == "tok" and fixmcp.TOKEN_HEADER not in headers
    reply[0] = {"ok": False, "code": "too_large"}
    text, is_error = cb.call(TOOL, {"content": "abc"})
    assert is_error is True and "too_large" in text and "stop" in text
    assert cb.call("other_tool", {}) == ("Unknown tool.", True)


# --------------------------------------------------------------------------- argv
def _mcp(token_env="TAVOTTO_CONVERT_TOKEN"):
    return ai_agents.ConvertMcp(
        command=["tavotto", "fix-mcp", "--url", "http://127.0.0.1:5089", "--convert-id", "s1"],
        name="tavotto_convert",
        token_env=token_env,
        tool_timeout_s=120,
    )


@pytest.mark.parametrize("osname", ["posix", "nt"])
def test_codex_convert_argv_injects_the_mcp_service_on_every_platform(monkeypatch, osname):
    monkeypatch.setattr(ai_agents.os, "name", osname)
    argv = (
        ai_agents.CodexAgent()
        .build_command(
            ai_agents.RunContext(argv=["codex"], prompt="PROMPT", cwd="/w", convert_mcp=_mcp())
        )
        .argv
    )
    pre = "mcp_servers.tavotto_convert"
    assert f'{pre}.command="tavotto"' in argv
    assert f'{pre}.args=["fix-mcp", "--url", "http://127.0.0.1:5089", "--convert-id", "s1"]' in argv
    assert f'{pre}.env_vars=["TAVOTTO_CONVERT_TOKEN"]' in argv
    assert f'{pre}.default_tools_approval_mode="approve"' in argv
    for flag in [a for a in argv if a.startswith(pre)]:
        assert argv[argv.index(flag) - 1] == "-c"
    assert argv[-1] == "PROMPT"
    # Windows 才有的沙盒覆盖与 MCP 注入并存
    assert ('windows.sandbox="unelevated"' in argv) is (osname == "nt")


def test_codex_convert_argv_without_mcp_is_unchanged(monkeypatch):
    monkeypatch.setattr(ai_agents.os, "name", "posix")
    argv = (
        ai_agents.CodexAgent()
        .build_command(ai_agents.RunContext(argv=["codex"], prompt="p", cwd="/w"))
        .argv
    )
    assert not any("mcp_servers" in a for a in argv)


def test_token_value_never_appears_in_argv(env, monkeypatch):
    """桥真的发了令牌：它只在子进程环境里，argv 里只有环境变量名。"""
    seen = {}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None, **kw):
        seen["argv"] = (
            ai_agents.CodexAgent()
            .build_command(
                ai_agents.RunContext(
                    argv=["codex"], prompt=prompt, cwd=cwd, convert_mcp=kw["convert_mcp"]
                )
            )
            .argv
        )
        return [sys.executable, "-c", "print(0)"], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    captured = {}
    real_popen = ai_bridge._popen

    def popen(cmd, cwd, environ, *a, **k):
        captured["env"] = environ
        return real_popen(cmd, cwd, environ, *a, **k)

    monkeypatch.setattr(ai_bridge, "_popen", popen)
    sid = ai_bridge.run(
        "codex",
        "fig.py",
        "p",
        str(env["figs"]),
        task_hooks={"runner": env["runner"]},
        convert={"library": "pillow", "original_rel": "out.png"},
    )
    _wait(sid)
    token = captured["env"][convertwrite.CONVERT_TOKEN_ENV]
    assert len(token) >= 32
    assert not any(token in a for a in seen["argv"])
    assert convertwrite.token_valid(sid, token) is False, "会话终态后令牌已失效"


# --------------------------------------------------------------------------- 提示词
def test_prompt_tells_the_model_to_use_the_tool_and_stop_on_failure(monkeypatch):
    monkeypatch.setattr(ai_prompt_pack, "_on_windows", lambda: True)
    for lang in ("python", "r", "matlab"):
        p = ai_prompt_pack.convert_prompt(
            script="fig.py",
            target="fig_matplotlib.py",
            reference=None,
            project_root="/p",
            source_language=lang,
            write_tool=True,
        )
        assert TOOL in p and "不要用文件编辑工具" in p and "整份脚本，一次写入" in p
        assert "直接说明它给出的失败原因并停止" in p
        assert "如果写入目标文件失败（权限被拒等）" not in p
        assert "cmd.exe" in p, "Windows 那条保留"
    plain = ai_prompt_pack.convert_prompt(
        script="fig.py", target="fig_matplotlib.py", reference=None, project_root="/p"
    )
    assert TOOL not in plain and "如果写入目标文件失败（权限被拒等）" in plain


def test_prompt_for_reads_the_candidate_flag(tmp_path):
    cand_dir = tmp_path
    cand = SimpleNamespace(
        script_rel="fig.py",
        target_name="fig_matplotlib.py",
        library=None,
        source_language="python",
        reference_name=None,
        project_root=tmp_path,
        extras={},
        dir=cand_dir,
    )
    cand.extras["write_tool"] = True
    assert TOOL in ai_convert.prompt_for(cand)
    cand.extras["write_tool"] = False
    assert TOOL not in ai_convert.prompt_for(cand)


def test_only_codex_declares_the_write_tool():
    assert ai_agents.get_agent("codex").convert_write_tool is True
    for agent in ai_agents.agents():
        if agent.id != "codex":
            assert agent.convert_write_tool is False, agent.id
    assert ai_agents.get_agent(ai_agents.BUILTIN_ID).convert_write_tool is False


# --------------------------------------------------------------------------- 判定
def test_write_denied_after_a_failed_tool_call():
    cand = SimpleNamespace(extras={})
    assert ai_convert.write_denied(cand) is False
    cand.extras["target_tool"] = {"calls": 0, "ok": False, "last_code": "", "bytes": 0}
    assert ai_convert.write_denied(cand) is False, "没调用过 = 不是工具失败"
    cand.extras["target_tool"] = {"calls": 2, "ok": False, "last_code": "too_large", "bytes": 0}
    assert ai_convert.write_denied(cand) is True
    cand.extras["target_tool"] = {"calls": 2, "ok": True, "last_code": "", "bytes": 9}
    assert ai_convert.write_denied(cand) is False


def test_merge_run_facts_carries_the_tool_flag():
    base = {"events": 1}
    assert ai_agents.merge_run_facts([base, base])["target_tool_ok"] is None
    assert (
        ai_agents.merge_run_facts(
            [{**base, "target_tool_ok": False}, {**base, "target_tool_ok": True}]
        )["target_tool_ok"]
        is True
    )
    assert ai_agents.merge_run_facts([{**base, "target_tool_ok": False}])["target_tool_ok"] is False


# --------------------------------------------------------------------------- 端到端：假 Codex → 真 fix-mcp → 回调 → 代写
FAKE_CODEX = r"""
import json, os, subprocess, sys
FILE_CHANGE_FAILED = {"type": "item.completed", "item": {"type": "file_change", "status": "failed",
                      "changes": [{"path": sys.argv[2] + "/" + sys.argv[3], "kind": "add"}]}}
print(json.dumps({"type": "turn.started"}), flush=True)
print(json.dumps(FILE_CHANGE_FAILED), flush=True)  # 沙盒里 Codex 自带的写文件工具失败
content = open(sys.argv[4], encoding="utf-8").read()
p = subprocess.Popen(
    [sys.executable, "-m", "tavotto", "fix-mcp", "--url", sys.argv[5], "--convert-id", sys.argv[6]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=os.environ.copy(),
)
def rpc(i, method, params=None):
    p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n").encode())
    p.stdin.flush()
    return json.loads(p.stdout.readline())
rpc(1, "initialize")
tools = rpc(2, "tools/list")["result"]["tools"]
assert [t["name"] for t in tools] == ["write_conversion_target"], tools
res = rpc(3, "tools/call", {"name": "write_conversion_target", "arguments": {"content": content}})["result"]
print(json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "status": "failed" if res["isError"] else "completed"}}), flush=True)
print(json.dumps({"type": "turn.completed"}), flush=True)
p.stdin.close(); p.wait(10)
"""


def _install_fake_codex(env, monkeypatch, served, tmp_path, content):
    body = tmp_path / "body.txt"
    body.write_bytes(content.encode("utf-8"))
    convertwrite.note_port(served.port)
    # 假 Codex 拉起的 `python -m tavotto` 要解析到被测的这份源码（不是环境里装的另一份）
    src = str(Path(ai_agents.__file__).resolve().parents[2])
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")]))
    )
    seen = {}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None, **kw):
        mcp = kw["convert_mcp"]
        env["cwd"].append(cwd)
        seen["mcp"] = mcp
        seen["prompt"] = prompt
        sid = mcp.command[-1]
        url = mcp.command[mcp.command.index("--url") + 1]
        return [
            sys.executable,
            "-c",
            FAKE_CODEX,
            "-",
            cwd,
            "fig_matplotlib.py",
            str(body),
            url,
            sid,
        ], {}

    # `python -c code - cwd name body url sid`：sys.argv = ["-c", "-", cwd, name, body, url, sid]
    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    return seen


def _run_codex(env):
    return ai_bridge.run(
        "codex",
        "fig.py",
        "生成可编辑的 Matplotlib 版本",
        str(env["figs"]),
        task_hooks={"runner": env["runner"]},
        convert={"library": "pillow", "original_rel": "out.png"},
    )


def test_end_to_end_failed_file_change_then_tool_write_converts(env, monkeypatch, tmp_path):
    s = Served()
    try:
        seen = _install_fake_codex(env, monkeypatch, s, tmp_path, GOOD)
        sess = _wait(_run_codex(env))
    finally:
        s.stop()
    print(
        "DBG",
        (ai_bridge.SNAP_DIR / (sess["id"] + ".stderr.log")).read_text()[-1500:],
        sess.get("transcript"),
        sess.get("status"),
        sess.get("cli_runs"),
        sess.get("error"),
    )
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert TOOL in seen["prompt"], "Codex 的转换提示词讲了工具"
    facts = ai_agents.merge_run_facts(sess["cli_runs"])
    assert facts["file_changes_failed"] == 1 and facts["target_tool_ok"] is True
    assert facts["target_on_disk"] is True and facts["target_bytes"] == len(GOOD.encode("utf-8"))
    assert convertwrite.token_valid(sess["id"], "x") is False


def test_end_to_end_tool_failure_with_missing_target_is_agent_write_denied(
    env, monkeypatch, tmp_path
):
    s = Served()
    try:
        _install_fake_codex(env, monkeypatch, s, tmp_path, "x" * (convertwrite.MAX_BYTES + 1))
        sess = _wait(_run_codex(env))
    finally:
        s.stop()
    assert sess["task"]["outcome"] == "failed"
    unmet = _unmet_codes(sess)
    assert ("candidate.target", "agent_write_denied") in unmet
    assert ("candidate.target", "target_missing") not in unmet
    assert ai_agents.merge_run_facts(sess["cli_runs"])["target_tool_ok"] is False
    assert len(env["cwd"]) == 1, "不可修正：不再起一轮 Agent"


def test_non_codex_sessions_get_no_mcp_and_no_tool_in_the_prompt(env, monkeypatch):
    seen = {}

    def _cmd(agent_id, prompt, cwd, **kw):
        seen["kw"] = kw
        seen["prompt"] = prompt
        return [sys.executable, "-c", "pass"], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    sid = ai_bridge.run(
        "claude",
        "fig.py",
        "p",
        str(env["figs"]),
        task_hooks={"runner": env["runner"]},
        convert={"library": "pillow", "original_rel": "out.png"},
    )
    _wait(sid)
    assert "convert_mcp" not in seen["kw"] and TOOL not in seen["prompt"]
