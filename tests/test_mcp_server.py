"""Codex MCP server 的看护。

盯的是这几件「坏了不报错、只是悄悄不生效」的事：

* **协议层**：initialize 的版本协商、tools/list 的形状、tools/call 的双份返回
  （人类可读 + 机器可读）。任何一条坏了，Codex 里表现为「插件没有工具」。
* **路径范围**：Codex 传来的路径可能是模型推断的，越界必须当场拒。
* **没有 UI 也能干活**：五个工具在不渲染 iframe 的 host 里要走完整条流程。
* **stdout 独占**：协议帧写错到 stderr 上，症状是「服务器不回消息」且没有任何报错。

需要 matplotlib 的全链路在 `tests/test_mcp_roundtrip.py`（自己 spawn worker）。
这里用假 worker，跑在 .venv 里。
"""
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

from tavotto_mcp import bridge, rpc, server, widget  # noqa: E402


# ------------------------------ 假 worker -----------------------------------
class FakeWorker:
    """只回声不渲染：协议层的用例不该依赖 matplotlib。"""

    def __init__(self) -> None:
        self.rev = 0
        self.generation = 1
        self.calls: list[tuple] = []
        self.exported: list[tuple] = []

    def _manifest(self, patches):
        size = [80.0, 60.0]
        elements = [
            {"gid": "figure", "role": "figure", "label": "整图", "draggable": False,
             "bbox": [0, 0, 1, 1], "editable": []},
            {"gid": "axes_0", "role": "axes", "label": "子图", "draggable": False,
             "bbox": [0.1, 0.1, 0.8, 0.8],
             "editable": [{"prop": "spine_top", "value": True},
                          {"prop": "spine_right", "value": True},
                          {"prop": "spine_bottom", "value": True},
                          {"prop": "spine_left", "value": True},
                          {"prop": "spine_linewidth", "value": 0.75}]},
            {"gid": "axes_0.xticks", "role": "ticks", "label": "x 刻度",
             "draggable": False, "bbox": [0.1, 0.9, 0.8, 0.05],
             "editable": [{"prop": "direction", "value": "in"},
                          {"prop": "fontsize",
                           "value": next((p["value"] for p in patches
                                          if p["gid"] == "axes_0.xticks"
                                          and p["prop"] == "fontsize"), 9.0)}]},
        ]
        return {"stem": "Fig1", "size_mm": size, "elements": elements}

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.calls.append(("override", stem, list(patches), preview_dpi, inline_svg))
        self.rev += 1
        out = {"manifest": self._manifest(patches), "warnings": [], "timings": {"total_ms": 1}}
        if inline_svg:
            out["svg"] = "<svg width='1pt' height='1pt'></svg>"
        return out

    def export(self, stem, patches, path, fmt="pdf", dpi=600):
        self.exported.append((stem, list(patches), path, fmt, dpi))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF-fake\n" if fmt == "pdf" else b"fake")
        return {"path": path, "warnings": []}

    def preview_png(self, stem, patches, width, tag):
        raise AssertionError("协议层用例不该走到位图预览")


@pytest.fixture(autouse=True)
def _clean_sessions():
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """一个最小图库：脚本 + 产物 + 注册表，落在允许的范围内。"""
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "tavotto_registry.json").write_text(json.dumps(
        {"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8")
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    return figures


@pytest.fixture
def fake_pool(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: worker)
    return worker


def _call(name: str, args: dict) -> dict:
    return server.call_tool(name, args)


def _body(result: dict) -> dict:
    return result["structuredContent"]


# ------------------------------- 协议层 -------------------------------------
def test_initialize_echoes_a_version_we_support():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    res = s.dispatch("initialize", {"protocolVersion": "2025-06-18"})
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert res["serverInfo"]["name"] == "tavotto"
    assert "tavotto_open_figure" in res["instructions"]


def test_initialize_falls_back_to_our_latest_for_unknown_versions():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    res = s.dispatch("initialize", {"protocolVersion": "1999-01-01"})
    assert res["protocolVersion"] == server.SUPPORTED_PROTOCOL_VERSIONS[0]


def test_tools_list_shape():
    tools = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO())) \
        .dispatch("tools/list", {})["tools"]
    names = [t["name"] for t in tools]
    assert names == ["tavotto_health",
                     "tavotto_open_figure", "tavotto_apply_overrides",
                     "tavotto_preflight", "tavotto_export",
                     "tavotto_verify_replay", "tavotto_close_session"]
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_only_canvas_tools_carry_the_ui_resource():
    """每个工具调用都拖一块 iframe 出来 = 画布不停重建。"""
    if not widget.available():
        pytest.skip("画布产物未构建")
    tools = {t["name"]: t for t in server._tools()}
    for name in server.UI_TOOLS:
        assert tools[name]["_meta"]["ui"]["resourceUri"] == widget.RESOURCE_URI
    for name in ("tavotto_preflight", "tavotto_export", "tavotto_close_session"):
        assert "_meta" not in tools[name]


def test_unknown_method_is_a_clean_error():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    with pytest.raises(rpc.RpcError) as exc:
        s.dispatch("nope/nope", {})
    assert exc.value.code == rpc.METHOD_NOT_FOUND


def test_notifications_are_never_answered():
    """通知不能回响应——回了就是协议错误，host 侧会当成野消息。"""
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(), out))
    s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert out.getvalue() == b""


def test_real_protocol_roundtrip_acquires_host_roots_and_opens_canvas(
        project, fake_pool, monkeypatch):
    """假 Codex host 走完整双向协议，不靠 TAVOTTO_MCP_ROOTS 偷渡答案。

    roots/list 必须嵌在真实 tools/call 处理窗口里；拿到根后 health 能说明来源，
    随后的 open 返回真正的 MCP App metadata，而不是只证明某个 Python 函数能调。
    """
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    root_uri = project.parent.resolve().as_uri()
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "codex-test", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_health", "arguments": {},
        }},
        {"jsonrpc": "2.0", "id": "tavotto-roots-1",
         "result": {"roots": [{"uri": root_uri, "name": "fixture"}]}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "tavotto_open_figure",
            "arguments": {"project_path": "figures"},
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(wire), out))
    assert s.serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]

    assert frames[1]["method"] == "roots/list"
    health = frames[2]["result"]["structuredContent"]
    assert health["roots"] == [str(project.parent.resolve())]
    assert health["root_authority"]["source"] == "mcp_roots"
    assert health["root_authority"]["mcp_roots"] == {
        "advertised": True,
        "list_changed": True,
        "state": "ready",
        "error": None,
        "compatibility_only": True,
        "deprecated_since": "2026-07-28",
    }
    opened = frames[3]["result"]
    assert not opened.get("isError"), opened.get("structuredContent")
    assert opened["structuredContent"]["stem"] == "Fig1"
    if widget.available():
        assert opened["_meta"]["ui"]["resourceUri"] == widget.RESOURCE_URI
        assert opened["_meta"]["openai/outputTemplate"] == widget.RESOURCE_URI


def test_roots_change_notification_refreshes_only_inside_the_next_tool_call(
        tmp_path, monkeypatch):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {"listChanged": True}},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_health", "arguments": {},
        }},
        {"jsonrpc": "2.0", "id": "tavotto-roots-1", "result": {
            "roots": [{"uri": first.resolve().as_uri()}],
        }},
        {"jsonrpc": "2.0", "method": "notifications/roots/list_changed"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "tavotto_health", "arguments": {},
        }},
        {"jsonrpc": "2.0", "id": "tavotto-roots-2", "result": {
            "roots": [{"uri": second.resolve().as_uri()}],
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    assert server.Server(rpc.StdioConnection(io.BytesIO(wire), out)).serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [frame.get("method") for frame in frames] == [
        None, "roots/list", None, "roots/list", None,
    ]
    assert frames[2]["result"]["structuredContent"]["roots"] == [str(first.resolve())]
    refreshed = frames[4]["result"]["structuredContent"]["root_authority"]
    assert refreshed["roots"] == [str(second.resolve())]
    assert refreshed["mcp_roots"]["state"] == "ready"


def test_real_protocol_roundtrip_elicits_one_connection_scoped_root_and_opens_canvas(
        project, fake_pool, monkeypatch):
    """当前 Codex 不声明 roots，但声明 elicitation：用户确认必须成为授权边界。"""
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    candidate = str(project.resolve())
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"elicitation": {}},
            "clientInfo": {"name": "codex-mcp-client", "version": "0.149.1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_open_figure",
            "arguments": {"project_path": candidate},
        }},
        {"jsonrpc": "2.0", "id": "tavotto-elicitation-1", "result": {
            "action": "accept", "content": {"approve": True},
        }},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "tavotto_health", "arguments": {},
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(wire), out))
    assert s.serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]

    prompt = frames[1]
    assert prompt["method"] == "elicitation/create"
    assert candidate in prompt["params"]["message"]
    assert prompt["params"]["requestedSchema"]["properties"]["approve"] == {
        "type": "boolean",
        "title": "允许 Tavotto 访问这个目录",
        "description": "请核对上方完整路径；不确定时保持关闭。",
        "default": False,
    }
    opened = frames[2]["result"]
    assert not opened.get("isError"), opened.get("structuredContent")
    assert opened["structuredContent"]["stem"] == "Fig1"
    if widget.available():
        assert opened["_meta"]["ui"]["resourceUri"] == widget.RESOURCE_URI
        assert opened["_meta"]["openai/outputTemplate"] == widget.RESOURCE_URI
    authority = frames[3]["result"]["structuredContent"]["root_authority"]
    assert authority["source"] == "user_elicitation"
    assert authority["roots"] == [candidate]
    assert authority["workspace_confirmation"]["lifetime"] == "mcp_connection"


@pytest.mark.parametrize("action,state", [
    ("decline", "declined"),
    ("cancel", "cancelled"),
])
def test_workspace_elicitation_refusal_fails_closed(
        project, monkeypatch, action, state):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"elicitation": {}},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_open_figure",
            "arguments": {"project_path": str(project.resolve())},
        }},
        {"jsonrpc": "2.0", "id": "tavotto-elicitation-1", "result": {
            "action": action,
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    assert server.Server(rpc.StdioConnection(io.BytesIO(wire), out)).serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    opened = frames[2]["result"]
    assert opened["isError"] is True
    assert opened["structuredContent"]["code"] == f"workspace_confirmation_{state}"
    assert state in opened["structuredContent"]["error"]
    assert "不要自动循环重试" in opened["structuredContent"]["error"]
    assert bridge.sessions() == {}


def test_rootless_elicitation_requires_an_absolute_existing_candidate(
        monkeypatch):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"elicitation": {}},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_open_figure",
            "arguments": {"project_path": "figures"},
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    assert server.Server(rpc.StdioConnection(io.BytesIO(wire), out)).serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(frames) == 2, "没有稳定基准时不应向用户展示一个猜出来的路径"
    payload = frames[1]["result"]["structuredContent"]
    assert payload["code"] == "workspace_confirmation_required"
    assert "绝对" in payload["recovery"]


def test_roots_client_that_disconnects_fails_closed_without_internal_error(
        tmp_path, monkeypatch):
    """声明 capability 却不回答的 host 不得锁死，也不得退回插件 cwd。"""
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {"listChanged": False}},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "tavotto_open_figure",
            "arguments": {"project_path": str(tmp_path)},
        }},
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(wire), out))
    assert s.serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert frames[1]["method"] == "roots/list"
    opened = frames[2]["result"]
    assert opened["isError"] is True
    assert opened["structuredContent"]["code"] == "no_workspace_root"
    assert all(frame.get("error", {}).get("code") != rpc.INTERNAL_ERROR
               for frame in frames)


def test_handler_exceptions_do_not_kill_the_connection(monkeypatch):
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(), out))
    monkeypatch.setattr(server, "_tools", lambda: 1 / 0)
    s.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    msg = json.loads(out.getvalue())
    assert msg["id"] == 7 and msg["error"]["code"] == rpc.INTERNAL_ERROR


def test_protocol_owns_the_real_stdout(monkeypatch):
    """`hijack_stdout` 之后再取 `sys.stdout.buffer` 拿到的是 stderr。

    这条曾经真的写错过：协议帧全落到 stderr 上，host 那边表现为
    「initialize 永远等不到响应」，而且没有任何报错。
    """
    real = io.BytesIO()
    fake_stdout = io.TextIOWrapper(real, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(rpc, "_REAL_STDOUT", None)
    rpc.StdioConnection.hijack_stdout()
    assert sys.stdout is sys.stderr          # 后续 print 一律去 stderr
    conn = rpc.StdioConnection(io.BytesIO())
    conn.result(1, {"ok": True})
    assert b'"ok"' in real.getvalue()        # 协议帧仍走真正的 stdout


def test_a_flood_of_blank_lines_does_not_blow_the_stack():
    """跳过空行必须是循环，不能是递归。

    递归会让栈深度跟着对端输入走：连续上千个裸换行（管道拥塞、被截断的
    写入都会产生）就抛 `RecursionError`，而 `serve_forever()` 只捕获
    RpcError/OSError/ValueError，于是整个 server 进程当场终止——host 那边
    看到的是「服务器意外断开」，JSON-RPC 层面一条错误响应都没有。
    """
    flood = b"\n" * 5000 + b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    conn = rpc.StdioConnection(io.BytesIO(flood), io.BytesIO())
    assert conn.read() == {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert conn.read() is None                # 空行吃完即 EOF，不是无限循环


# ------------------------------ 路径范围 ------------------------------------
def test_paths_outside_the_allowed_roots_are_refused(tmp_path, monkeypatch):
    """越界一律拒，并如实回报**规范化后的那个路径**。

    这里不拿 `/etc` 当反例：Windows 上 `os.path.realpath("/etc")` 是 `D:\\etc`，
    断言里写死 POSIX 路径只会让用例在那条腿上假红（CI 实测撞到）。
    改用一个明确落在允许根之外的兄弟目录，两个平台语义一致。
    """
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, str(inside))

    assert bridge.check_scope(str(inside)) == os.path.realpath(str(inside))
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(outside))
    payload = exc.value.payload()
    assert exc.value.code == "path_out_of_scope"
    # 越界绝不「就近找一个能用的」：回的是用户给的那个路径，不是某个替代品
    assert payload["path"] == os.path.realpath(str(outside))
    assert payload["roots"] == [os.path.realpath(str(inside))]


def test_allowed_roots_default_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert bridge.allowed_roots() == [str(Path(tmp_path).resolve())]


def test_relative_paths_resolve_from_one_trusted_root(monkeypatch, tmp_path):
    figures = tmp_path / "figures"
    figures.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.chdir(PLUGIN / "mcp")
    assert bridge.check_scope("figures") == os.path.realpath(str(figures))


def test_relative_paths_are_not_guessed_across_multiple_roots(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, os.pathsep.join((str(first), str(second))))
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope("figures")
    assert exc.value.code == "ambiguous_workspace_root"


def test_symlink_cannot_escape_a_trusted_root(monkeypatch, tmp_path):
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    link = inside / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建目录 symlink: {exc}")
    monkeypatch.setenv(bridge.ROOTS_ENV, str(inside))
    with pytest.raises(bridge.BridgeError) as caught:
        bridge.check_scope(str(link))
    assert caught.value.code == "path_out_of_scope"


def test_open_refuses_out_of_scope(project, fake_pool):
    res = _call("tavotto_open_figure", {"project_path": "/etc"})
    assert res["isError"] and _body(res)["code"] == "path_out_of_scope"


# ------------------------------ 会话生命周期 ---------------------------------
def test_open_apply_preflight_export_close_without_any_ui(project, fake_pool, tmp_path):
    """**没有 UI 的 host 里这条链必须完整**——这就是 fallback 的验收。"""
    res = _call("tavotto_open_figure", {"project_path": str(project)})
    assert not res.get("isError"), _body(res)
    opened = _body(res)
    sid = opened["session_id"]
    assert opened["stem"] == "Fig1"
    assert opened["manifest"]["size_mm"] == [80.0, 60.0]
    assert opened["patch_hash"].startswith("sha256:")
    assert opened["profile"]["profile_id"] == "lab-publication-v1"
    assert "counts" in opened["preflight"]
    assert res["content"][0]["text"]

    patches = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}]
    applied = _body(_call("tavotto_apply_overrides",
                          {"session_id": sid, "patches": patches}))
    assert applied["applied"] == 1 and applied["rejected"] == []
    # worker 拿到的是**过滤后仍保持原始顺序**的那份，与 Flask 走的完全一样
    assert fake_pool.calls[-1][2] == patches

    checks = _body(_call("tavotto_preflight", {"session_id": sid}))
    assert checks["blocking"] is True          # 7pt 撞绝对下限
    assert any(i["id"] == "font-below-absolute-floor" for i in checks["errors"])
    assert "阻断" in checks["report"]

    out_dir = tmp_path / "out"
    blocked = _call("tavotto_export", {"session_id": sid, "formats": ["pdf"],
                                       "out_dir": str(out_dir)})
    assert blocked["isError"] and _body(blocked)["code"] == "preflight_blocked"
    assert not out_dir.exists(), "被阻断时一张图都不该出"

    done = _body(_call("tavotto_export",
                       {"session_id": sid, "formats": ["pdf", "png"], "dpi": 300,
                        "out_dir": str(out_dir), "explicit_confirm": True}))
    assert [f["format"] for f in done["files"]] == ["pdf", "png"]
    assert done["forced"] is True
    assert [f["vector"] for f in done["files"]] == [True, False]
    assert done["files"][1]["dpi"] == 300

    closed = _body(_call("tavotto_close_session", {"session_id": sid}))
    assert closed["closed"] is True
    # 重复关闭不算错（Codex 可能重试）
    assert _body(_call("tavotto_close_session", {"session_id": sid}))["closed"] is False


def test_unknown_session_says_which_ones_exist(project, fake_pool):
    res = _call("tavotto_apply_overrides", {"session_id": "s-nope", "patches": []})
    assert res["isError"] and _body(res)["code"] == "unknown_session"


def test_patches_are_a_full_list_and_dirty_entries_are_reported(project, fake_pool):
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    patches = [
        {"gid": "axes_0.xticks", "prop": "fontsize", "value": 9.0},
        {"gid": "", "prop": "x", "value": 1},                    # 坏 gid
        {"gid": "a", "prop": "b", "value": float("inf")},        # 非有限浮点
    ]
    body = _body(_call("tavotto_apply_overrides",
                       {"session_id": sid, "patches": patches}))
    # 脏条目**不静默丢**：连同原因一起交出来
    assert [d["reason"] for d in body["rejected"]] == ["bad_gid", "non_finite_float"]
    assert body["applied"] == 1
    assert fake_pool.calls[-1][2] == [patches[0]]


def test_patch_hash_is_the_canonical_one(project, fake_pool):
    from tavotto.engine import patchspec
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    a = [{"gid": "g2", "prop": "p", "value": 1}, {"gid": "g1", "prop": "p", "value": 2}]
    b = [{"gid": "g1", "prop": "p", "value": 2}, {"gid": "g2", "prop": "p", "value": 1}]
    ha = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": a}))["patch_hash"]
    hb = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": b}))["patch_hash"]
    # 顺序不同、内容相同 → 同一个身份（与 patchspec 的规范化一致）
    assert ha == hb == patchspec.patch_hash(a)


def test_export_defaults_to_the_project_export_dir(project, fake_pool, monkeypatch):
    """与画布导出同一条规则（engine/config.project_export_dir），不另写一份。"""
    from tavotto.engine import config as engine_config
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    _call("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    done = _body(_call("tavotto_export", {"session_id": sid, "formats": ["pdf"],
                                          "explicit_confirm": True}))
    assert Path(done["export_dir"]) == engine_config.project_export_dir(str(project))
    assert Path(done["export_dir"]).name == "export"


def test_export_writes_a_proof_report(project, fake_pool, tmp_path):
    from tavotto.engine.brand import PROOF_KIND
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    _call("tavotto_apply_overrides",
          {"session_id": sid,
           "patches": [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}]})
    done = _body(_call("tavotto_export",
                       {"session_id": sid, "formats": ["pdf"], "explicit_confirm": True,
                        "out_dir": str(tmp_path / "out")}))
    proof = json.loads(Path(done["proof_path"]).read_text(encoding="utf-8"))
    assert proof["kind"] == PROOF_KIND and proof["version"] == 2
    assert proof["source"] == "codex-mcp"
    assert proof["profile"]["profile_id"] == "lab-publication-v1"
    assert proof["profile"]["profile_version"]
    assert proof["forced"] is True and proof["acknowledged"]
    assert proof["patch_hash"] == done["patch_hash"]
    assert any(c["id"] == "font-below-absolute-floor" for c in proof["checks"])
    assert "not_verifiable" in proof


def test_journal_override_reaches_preflight(project, fake_pool):
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    base = _body(_call("tavotto_preflight", {"session_id": sid}))
    assert not any(i["id"] == "page-width" for i in base["errors"])
    tight = _body(_call("tavotto_preflight",
                        {"session_id": sid,
                         "journal": {"widths_mm": {"single": 55.0, "double": 120.0}}}))
    assert any(i["id"] == "page-width" for i in tight["errors"])
    assert tight["profile"]["journal"]["widths_mm"]["double"] == 120.0


def test_bad_format_and_dpi_are_refused(project, fake_pool):
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    assert _body(_call("tavotto_export", {"session_id": sid, "formats": ["docx"]}))["code"] \
        == "bad_format"
    assert _body(_call("tavotto_export", {"session_id": sid, "dpi": 0}))["code"] == "bad_dpi"


def test_a_directory_without_a_registry_is_a_clear_error(tmp_path, monkeypatch, fake_pool):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _call("tavotto_open_figure", {"project_path": str(empty)})
    assert res["isError"]
    assert _body(res)["code"] in ("no_registry", "no_figure")


def test_session_eviction_keeps_the_lid_on(project, fake_pool, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_SESSIONS", 2)
    ids = [_body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
           for _ in range(4)]
    assert len(bridge.sessions()) == 2
    assert ids[-1] in bridge.sessions()


# ------------------------------ 画布产物 ------------------------------------
def test_widget_artifact_is_in_sync_with_the_frontend():
    """产物过期 = 用户装到的是旧画布。指纹算法纯 Python，CI 不需要 Node。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_mcp_widget
    have = build_mcp_widget.current_fingerprint()
    if have is None:
        pytest.skip("画布产物未构建（源码检出后跑一次 scripts/build_mcp_widget.py）")
    assert have == build_mcp_widget.source_fingerprint(), (
        "codex-plugin/mcp/widget/canvas.html 与 web/src 不同步，"
        "跑一次 python scripts/build_mcp_widget.py")


def test_widget_resource_is_self_contained():
    """CSP 声明的是空 connectDomains：画布不许发任何跨源请求。"""
    if not widget.available():
        pytest.skip("画布产物未构建")
    meta = widget.resource_meta()
    assert meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    assert meta["ui"]["resourceUri"] == widget.RESOURCE_URI
    assert meta["openai/outputTemplate"] == widget.RESOURCE_URI
    html = widget.html()
    # 单文件才塞得进 MCP 资源：外链的 <script src> / <link href> 在 iframe 里
    # 根本没有可寻址的来源（我们也刻意把 resourceDomains 留空）。
    # 只看**标签**，不看正文——压缩过的 JS 里满地都是 `src=`。
    import re as _re
    externals = [m.group(0) for m in
                 _re.finditer(r"<(?:script|link)\b[^>]*>", html, _re.I)
                 if _re.search(r'\b(?:src|href)\s*=\s*"(?!data:)', m.group(0), _re.I)]
    assert not externals, f"画布 HTML 里有外链: {externals[:3]}"
    assert "<script" in html and "<style" in html
    contents = widget.resource_contents()
    assert contents["mimeType"] == "text/html;profile=mcp-app"


def test_server_degrades_cleanly_without_the_widget(monkeypatch):
    """没有 UI 产物时**不声明资源、不挂 _meta**，五个工具照常可用。"""
    monkeypatch.setattr(widget, "available", lambda: False)
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    assert s.dispatch("resources/list", {})["resources"] == []
    assert "resources" not in s.dispatch("initialize", {})["capabilities"]
    assert all("_meta" not in t for t in s.dispatch("tools/list", {})["tools"])


def test_missing_widget_is_said_out_loud_on_open(project, fake_pool, monkeypatch):
    """产物缺失时 open 照常干活，但**必须把「这次没有画布、为什么」说出口**
    ——不说的话用户看到的是「说好的画布呢」，零线索（C5 假成功修复）。"""
    monkeypatch.setattr(widget, "available", lambda: False)
    res = _call("tavotto_open_figure", {"project_path": str(project)})
    body = _body(res)
    assert body["ok"] is True                      # 工具本身没坏
    assert body["canvas_ui"]["available"] is False
    assert body["canvas_ui"]["code"] == "widget_missing"
    assert "内嵌画布不可用" in res["content"][0]["text"]
    assert "_meta" not in res


def test_resources_read_for_a_missing_widget_is_an_explicit_error(monkeypatch):
    """URI 对、文件不在：报「缺失 + 怎么修」，绝不回一段空 HTML（白框）。"""
    monkeypatch.setattr(widget, "available", lambda: False)
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    with pytest.raises(rpc.RpcError, match="画布资源缺失"):
        s.dispatch("resources/read", {"uri": widget.RESOURCE_URI})


def test_health_tool_reports_capabilities(project, fake_pool):
    """能力体检：引擎版本、画布可用性、允许的根——出图前的第一站。"""
    res = _call("tavotto_health", {})
    body = _body(res)
    assert body["ok"] is True and body["engine"]["available"] is True
    assert body["canvas"]["resource_uri"] == widget.RESOURCE_URI
    assert body["roots"], "允许的根要能看见"
    assert "health_ms" in body["timings"]
    assert "_meta" not in res, "体检不挂 UI——它的产出是文字"


# ===========================================================================
# Codex review (#8/#10) 抓到的那批：桥接层的边界、会话生命周期与导出判据
# ===========================================================================

# ------------------------------ 允许的项目根 ---------------------------------
def test_plugin_cwd_is_not_a_workspace(monkeypatch):
    """装好的插件里 cwd 就是**插件自己的目录**（`./mcp/server.py` 靠它解析）。

    「不给 TAVOTTO_MCP_ROOTS 就用 cwd」在真实安装下于是把用户工作区里的
    每一张图都判成 `path_out_of_scope`——默认流程根本跑不起来，而报出来的
    话又与真实原因（谁都没设过那个变量）毫不相干。
    """
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    assert bridge.allowed_roots() == []
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(PLUGIN / "mcp"))
    # 说清楚要设什么，而不是甩一句「不在允许范围内」
    assert exc.value.code == "no_workspace_root"
    assert bridge.ROOTS_ENV in str(exc.value)


def test_deleted_plugin_cwd_is_a_structured_missing_root(monkeypatch, tmp_path):
    """插件热更新会替换缓存目录，旧 MCP 进程的 cwd 随后可能已被删除。

    ``resources/list`` 不读 cwd，所以画布资源仍能被发现；旧实现直到
    ``tools/call`` 才在 ``os.getcwd()`` 抛 ``ENOENT``，host 最终只看见
    ``-32603 Internal error``。体检必须继续可用，而任何文件操作都应保持
    fail-closed，返回可恢复的 ``no_workspace_root``。
    """
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)

    def deleted_cwd():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(bridge.os, "getcwd", deleted_cwd)

    def must_not_resolve_without_a_root(_path):
        raise AssertionError("没有安全边界时不应规范化目标路径")

    monkeypatch.setattr(bridge.os.path, "realpath", must_not_resolve_without_a_root)

    health = _call("tavotto_health", {})
    assert not health.get("isError")
    assert _body(health)["roots"] == []

    opened = _call("tavotto_open_figure", {"project_path": str(tmp_path)})
    assert opened["isError"] is True
    assert _body(opened)["code"] == "no_workspace_root"


@pytest.mark.parametrize("name", bridge.WORKSPACE_ENVS)
def test_host_workspace_env_supplies_the_default_root(monkeypatch, tmp_path, name):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for other in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    monkeypatch.setenv(name, str(tmp_path))
    assert bridge.allowed_roots() == [os.path.realpath(str(tmp_path))]


def test_protocol_root_survives_a_deleted_plugin_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    nested = tmp_path / "nested"
    nested.mkdir()
    resolved_tmp_path = str(tmp_path.resolve())
    resolved_nested = str(nested.resolve())
    bridge.observe_mcp_client("2025-11-25", {"roots": {}}, {})
    bridge.accept_protocol_roots({"roots": [{"uri": tmp_path.as_uri()}]})

    def deleted_cwd():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(bridge.os, "getcwd", deleted_cwd)
    assert bridge.allowed_roots() == [resolved_tmp_path]
    assert bridge.check_scope(str(nested)) == resolved_nested


def test_open_session_is_revoked_when_host_switches_workspace(
        project, fake_pool, monkeypatch, tmp_path):
    opened = _body(_call("tavotto_open_figure", {"project_path": str(project)}))
    sid = opened["session_id"]
    other = tmp_path / "other-workspace"
    other.mkdir()

    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    bridge.observe_mcp_client("2025-11-25", {"roots": {"listChanged": True}}, {})
    bridge.accept_protocol_roots({"roots": [{"uri": other.resolve().as_uri()}]})
    result = _call("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    assert result["isError"] is True
    assert _body(result)["code"] == "workspace_root_changed"
    assert sid not in bridge.sessions()


def test_registry_outside_the_root_is_never_written(tmp_path, monkeypatch, fake_pool):
    """**范围校验要在 `ensure_registered` 之前**——后者是写操作。

    允许的根嵌套在一个本身已是图库的目录下面时，`resolve_target` 会向上走到
    那个父目录。旧顺序是「先登记再校验」：调用最终被拒，可范围外那份注册表
    **已经被改过了**，边界形同虚设。
    """
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    reg = outer / "tavotto_registry.json"
    before = json.dumps({"scripts": {"a.py": {"entry": "main", "stems": ["A"]}}})
    reg.write_text(before, encoding="utf-8")
    # 脚本必须是**能被静态扫描认出来**的形态：那样 ensure_registered 才真的
    # 会去写 outer/tavotto_registry.json（实测 status=merged）。写不动的脚本会让
    # 这条用例变成空转的门禁——它还在报平安。
    (inner / "fig1.py").write_text(
        "import matplotlib.pyplot as plt\n\n\n"
        "def main():\n    fig = plt.figure()\n    fig.savefig(\"Fig1.pdf\")\n",
        encoding="utf-8")
    (inner / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv(bridge.ROOTS_ENV, str(inner))       # 只允许 inner

    with pytest.raises(bridge.BridgeError) as exc:
        bridge.open_figure(str(inner / "Fig1.pdf"))
    assert exc.value.code == "path_out_of_scope"
    assert reg.read_text(encoding="utf-8") == before, "范围外的注册表被改写了"


# ------------------------------ 会话生命周期 ---------------------------------
def test_failed_first_render_leaves_no_session_behind(project, monkeypatch):
    """渲染失败时调用方只拿到错误，**永远拿不到 session_id**，也就关不掉它。

    先登记后渲染的话，反复失败的 open 会把账本堆满，再靠 `_evict_if_needed()`
    把**真正在用的**会话挤出去。
    """
    class Boom:
        rev = 0
        generation = 1

        def override(self, *a, **k):
            raise bridge.engine_pool.WorkerError("脚本自己抛了", code="build_failed")

    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: Boom())
    for _ in range(bridge.MAX_SESSIONS + 3):
        with pytest.raises(bridge.BridgeError):
            bridge.open_figure(str(project))
    assert bridge.sessions() == {}, "失败的 open 在账本里留下了够不着的会话"


def test_preview_png_failure_still_hands_back_the_session(project, fake_pool,
                                                          monkeypatch):
    """位图那一跳失败不能把会话变成「够不着的幽灵」。

    主渲染成功后会话已经登记，而 `include_png` 的位图是**第二次独立的
    worker 调用**（超时/崩溃/磁盘错误都可能）。让它抛出去的话，调用方拿到
    的是一条 isError 结果、里面没有 session_id——这条会话谁也关不掉，占着
    账本直到被 `_evict_if_needed()` 挤掉，而被挤掉的往往是真正在用的那条。
    位图是顺带产物：降级，但如实回一个 code（不静默）。
    """
    def boom(*a, **k):
        raise bridge.BridgeError("位图挂了", code="preview_failed")

    monkeypatch.setattr(bridge, "preview_png", boom)
    out = bridge.open_figure(str(project), include_png=True)
    assert out["ok"] is True
    assert out["session_id"] in bridge.sessions(), "会话必须还够得着"
    assert "preview_png_base64" not in out
    assert out["preview_png_error"] == "preview_failed"     # 不静默


def test_unreadable_preview_file_is_still_a_bridge_error(project, fake_pool,
                                                          tmp_path):
    """worker 说成了、文件却读不出来，也必须走降级而不是裸 OSError。

    `Path(path).read_bytes()` 那一步在 try 之外，抛的是 OSError（被杀毒隔离、
    磁盘满、缓存目录被清都会）。`open_figure` 的降级只接 BridgeError，接不住
    它——那条刚登记的会话又变回谁也够不着的幽灵，正是这次修复要堵的洞。
    """
    # worker 报告成功、文件却不在（被杀毒隔离、缓存目录被清、磁盘满写了个寂寞）
    fake_pool.preview_png = lambda stem, patches, width, tag: str(
        tmp_path / "gone" / "nope.png")
    out = bridge.open_figure(str(project), include_png=True)
    assert out["ok"] is True
    assert out["session_id"] in bridge.sessions()
    assert out["preview_png_error"] == "preview_unreadable"


def test_every_operation_reacquires_the_worker_from_the_pool(project, monkeypatch):
    """池按 MAX_ALIVE 淘汰，桥的会话上限是另一个数——两者必然打架。

    抱着 worker 引用的话，第 4 个脚本一开，第 1 个会话手里那个已经被池
    shutdown 了，可它在账本里还「开着」：用户看到「会话还在，一 apply 就说
    worker 死了」，且没有任何办法恢复。
    """
    workers = [FakeWorker()]
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: workers[-1])
    sid = bridge.open_figure(str(project))["session_id"]
    assert workers[0].calls, "第一次渲染应当用当时池里那个"

    # 池把它淘汰后重建了：现在 get() 回的是另一个对象
    workers.append(FakeWorker())
    bridge.apply_overrides(sid, [])
    assert workers[1].calls, "后续操作没有向池重新要 worker，还抱着旧引用"

    # 导出与位图预览同理
    workers.append(FakeWorker())
    bridge.export(sid, formats=["pdf"])
    assert workers[2].exported, "导出还在用旧的 worker 引用"


# -------------------------------- 导出判据 ----------------------------------
def _open(project) -> str:
    return bridge.open_figure(str(project))["session_id"]


def test_export_default_dir_goes_through_check_scope(project, fake_pool, tmp_path,
                                                     monkeypatch):
    """项目设置里的 `export_dir` 可以是任意绝对路径（桌面版下完全合法）。

    默认值不过尺的话，一个对桌面版有效的项目就能让导出落到 MCP 的边界之外
    ——而调用方**显式**传同一个路径反而会被拒。
    """
    sid = _open(project)
    outside = tmp_path.parent / "elsewhere-export"
    monkeypatch.setattr(bridge.engine_config, "project_export_dir",
                        lambda *a, **k: outside)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.export(sid, formats=["pdf"])
    assert exc.value.code == "path_out_of_scope"
    assert not outside.exists(), "被拒之前就已经把目录建出来了"


def test_png_below_the_profile_dpi_needs_explicit_confirm(project, fake_pool):
    """`dpi=72` 以前只要是正数就放行，proof 里还盖着「已按规范检查」的章。"""
    sid = _open(project)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.export(sid, formats=["png"], dpi=72)
    assert exc.value.code == "preflight_blocked"
    ids = [i["id"] for i in exc.value.extra["preflight"]["errors"]]
    assert "raster-dpi" in ids, "低 dpi 没进预检结论"
    # 明确要求后可以出，但要记进 proof
    done = bridge.export(sid, formats=["png"], dpi=72, explicit_confirm=True)
    assert done["forced"] is True
    assert "raster-dpi" in done["acknowledged"]
    # 矢量格式不吃这条
    assert bridge.export(sid, formats=["pdf"], dpi=72)["files"][0]["format"] == "pdf"


def test_default_formats_follow_the_profile_of_this_call(project, fake_pool):
    """带了 journal 覆盖时，预检与 proof 盖的是新 profile 的章——格式也必须是。"""
    sid = _open(project)
    done = bridge.export(sid, formats=[], journal={
        "name": "Some Journal",
        "preferred_formats": {"export_default": ["svg"]},
    })
    assert [f["format"] for f in done["files"]] == ["svg"]


# ------------------------------ 重放自检 ------------------------------------
def test_replay_reports_missing_and_extra_elements():
    """结构分歧也是分歧。

    静默跳过「重放里没有这个元素」会让 `ok: true` 出现在两张画得完全不一样
    的图上——而脚本不确定 / 重放有 bug 正是这个自检唯一要抓的东西。
    """
    hot = {"size_mm": [80.0, 60.0], "elements": [
        {"gid": "figure", "bbox": [0, 0, 1, 1]},
        {"gid": "axes_0.line_0", "bbox": [0.1, 0.1, 0.5, 0.5]},
    ]}
    fresh = {"size_mm": [80.0, 60.0], "elements": [
        {"gid": "figure", "bbox": [0, 0, 1, 1]},
        {"gid": "axes_0.line_1", "bbox": [0.1, 0.1, 0.5, 0.5]},
    ]}
    diffs, compared = bridge.compare_manifests(hot, fresh)
    fields = {d["field"] for d in diffs}
    assert "missing_in_fresh" in fields, "热态有、重放没有的元素被静默跳过了"
    assert "missing_in_hot" in fields, "只在重放里出现的元素也要报"
    assert compared == 1


def test_open_with_an_unknown_profile_carries_a_code(project, fake_pool):
    """`run_preflight` 早就把 ProfileError 翻成 unknown_profile，open 这条漏了。"""
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.open_figure(str(project), profile_id="没有这个规范")
    assert exc.value.code == "unknown_profile"
