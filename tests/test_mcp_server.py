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

import base64
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

from tavotto.engine import previewbudget  # noqa: E402
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
            {
                "gid": "figure",
                "role": "figure",
                "label": "整图",
                "draggable": False,
                "bbox": [0, 0, 1, 1],
                "editable": [],
            },
            {
                "gid": "axes_0",
                "role": "axes",
                "label": "子图",
                "draggable": False,
                "bbox": [0.1, 0.1, 0.8, 0.8],
                "editable": [
                    {"prop": "spine_top", "value": True},
                    {"prop": "spine_right", "value": True},
                    {"prop": "spine_bottom", "value": True},
                    {"prop": "spine_left", "value": True},
                    {"prop": "spine_linewidth", "value": 0.75},
                ],
            },
            {
                "gid": "axes_0.xticks",
                "role": "ticks",
                "label": "x 刻度",
                "draggable": False,
                "bbox": [0.1, 0.9, 0.8, 0.05],
                "editable": [
                    {"prop": "direction", "value": "in"},
                    {
                        "prop": "fontsize",
                        "value": next(
                            (
                                p["value"]
                                for p in patches
                                if p["gid"] == "axes_0.xticks" and p["prop"] == "fontsize"
                            ),
                            9.0,
                        ),
                    },
                ],
            },
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
def _clean_sessions(monkeypatch):
    bridge.reset_root_authority()
    bridge.sessions().clear()
    bridge._REFRESH_CTX.clear()
    # 刷新工具默认先探 127.0.0.1:5089 上有没有运行中的 Tavotto。**用例里绝不
    # 真的去探**——开发机上很可能真开着一个，tmp 项目会被打开进用户的应用。
    monkeypatch.setattr(bridge.engine_handoff, "http_json_status", lambda *a, **k: (None, None))
    yield
    bridge.sessions().clear()
    bridge._REFRESH_CTX.clear()
    bridge.reset_root_authority()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """一个最小图库：脚本 + 产物 + 注册表，落在允许的范围内。"""
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
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
    tools = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO())).dispatch(
        "tools/list", {}
    )["tools"]
    names = [t["name"] for t in tools]
    assert names == [
        "tavotto_health",
        "tavotto_open_figure",
        "tavotto_apply_overrides",
        "tavotto_preflight",
        "tavotto_export",
        "tavotto_verify_replay",
        "tavotto_refresh_project",
        "tavotto_close_session",
    ]
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_only_canvas_tools_carry_the_ui_resource():
    """每个工具调用都拖一块 iframe 出来 = 画布不停重建。"""
    if not widget.available():
        pytest.skip("画布产物未构建")
    tools = {t["name"]: t for t in server._tools()}
    for name in server.UI_TOOLS:
        assert tools[name]["_meta"]["ui"]["resourceUri"] == widget.RESOURCE_URI
    for name in (
        "tavotto_preflight",
        "tavotto_export",
        "tavotto_refresh_project",
        "tavotto_close_session",
    ):
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
    project, fake_pool, monkeypatch
):
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
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "codex-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_health",
                "arguments": {},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "tavotto-roots-1",
            "result": {"roots": [{"uri": root_uri, "name": "fixture"}]},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": "figures"},
            },
        },
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


def test_roots_change_notification_refreshes_only_inside_the_next_tool_call(tmp_path, monkeypatch):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    incoming = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": True}},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_health",
                "arguments": {},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "tavotto-roots-1",
            "result": {
                "roots": [{"uri": first.resolve().as_uri()}],
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/roots/list_changed"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "tavotto_health",
                "arguments": {},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "tavotto-roots-2",
            "result": {
                "roots": [{"uri": second.resolve().as_uri()}],
            },
        },
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    assert server.Server(rpc.StdioConnection(io.BytesIO(wire), out)).serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [frame.get("method") for frame in frames] == [
        None,
        "roots/list",
        None,
        "roots/list",
        None,
    ]
    assert frames[2]["result"]["structuredContent"]["roots"] == [str(first.resolve())]
    refreshed = frames[4]["result"]["structuredContent"]["root_authority"]
    assert refreshed["roots"] == [str(second.resolve())]
    assert refreshed["mcp_roots"]["state"] == "ready"


def test_real_protocol_roundtrip_elicits_one_connection_scoped_root_and_opens_canvas(
    project, fake_pool, monkeypatch
):
    """当前 Codex 不声明 roots，但声明 elicitation：用户确认必须成为授权边界。"""
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    candidate = str(project.resolve())
    incoming = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
                "clientInfo": {"name": "codex-mcp-client", "version": "0.149.1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": candidate},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "tavotto-elicitation-1",
            "result": {
                "action": "accept",
                "content": {"approve": True},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "tavotto_health",
                "arguments": {},
            },
        },
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


@pytest.mark.parametrize(
    "action,state",
    [
        ("decline", "declined"),
        ("cancel", "cancelled"),
    ],
)
def test_workspace_elicitation_refusal_fails_closed(project, monkeypatch, action, state):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": str(project.resolve())},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "tavotto-elicitation-1",
            "result": {
                "action": action,
            },
        },
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


def test_rootless_elicitation_requires_an_absolute_existing_candidate(monkeypatch):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": "figures"},
            },
        },
    ]
    wire = b"".join((json.dumps(msg) + "\n").encode() for msg in incoming)
    out = io.BytesIO()
    assert server.Server(rpc.StdioConnection(io.BytesIO(wire), out)).serve_forever() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(frames) == 2, "没有稳定基准时不应向用户展示一个猜出来的路径"
    payload = frames[1]["result"]["structuredContent"]
    assert payload["code"] == "workspace_confirmation_required"
    assert "绝对" in payload["recovery"]


def test_roots_client_that_disconnects_fails_closed_without_internal_error(tmp_path, monkeypatch):
    """声明 capability 却不回答的 host 不得锁死，也不得退回插件 cwd。"""
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    for name in bridge.WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(PLUGIN / "mcp")
    incoming = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": False}},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": str(tmp_path)},
            },
        },
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
    assert all(frame.get("error", {}).get("code") != rpc.INTERNAL_ERROR for frame in frames)


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
    assert sys.stdout is sys.stderr  # 后续 print 一律去 stderr
    conn = rpc.StdioConnection(io.BytesIO())
    conn.result(1, {"ok": True})
    assert b'"ok"' in real.getvalue()  # 协议帧仍走真正的 stdout


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
    assert conn.read() is None  # 空行吃完即 EOF，不是无限循环


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
    applied = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": patches}))
    assert applied["applied"] == 1 and applied["rejected"] == []
    # worker 拿到的是**过滤后仍保持原始顺序**的那份，与 Flask 走的完全一样
    assert fake_pool.calls[-1][2] == patches

    checks = _body(_call("tavotto_preflight", {"session_id": sid}))
    assert checks["blocking"] is True  # 7pt 撞绝对下限
    assert any(i["id"] == "font-below-absolute-floor" for i in checks["errors"])
    assert "阻断" in checks["report"]

    out_dir = tmp_path / "out"
    blocked = _call(
        "tavotto_export", {"session_id": sid, "formats": ["pdf"], "out_dir": str(out_dir)}
    )
    assert blocked["isError"] and _body(blocked)["code"] == "preflight_blocked"
    assert not out_dir.exists(), "被阻断时一张图都不该出"

    done = _body(
        _call(
            "tavotto_export",
            {
                "session_id": sid,
                "formats": ["pdf", "png"],
                "dpi": 300,
                "out_dir": str(out_dir),
                "explicit_confirm": True,
            },
        )
    )
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
        {"gid": "", "prop": "x", "value": 1},  # 坏 gid
        {"gid": "a", "prop": "b", "value": float("inf")},  # 非有限浮点
    ]
    body = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": patches}))
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
    done = _body(
        _call("tavotto_export", {"session_id": sid, "formats": ["pdf"], "explicit_confirm": True})
    )
    assert Path(done["export_dir"]) == engine_config.project_export_dir(str(project))
    assert Path(done["export_dir"]).name == "export"


def test_export_writes_a_proof_report(project, fake_pool, tmp_path):
    from tavotto.engine.brand import PROOF_KIND

    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    _call(
        "tavotto_apply_overrides",
        {
            "session_id": sid,
            "patches": [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}],
        },
    )
    done = _body(
        _call(
            "tavotto_export",
            {
                "session_id": sid,
                "formats": ["pdf"],
                "explicit_confirm": True,
                "out_dir": str(tmp_path / "out"),
            },
        )
    )
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
    tight = _body(
        _call(
            "tavotto_preflight",
            {"session_id": sid, "journal": {"widths_mm": {"single": 55.0, "double": 120.0}}},
        )
    )
    assert any(i["id"] == "page-width" for i in tight["errors"])
    assert tight["profile"]["journal"]["widths_mm"]["double"] == 120.0


def test_bad_format_and_dpi_are_refused(project, fake_pool):
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    assert (
        _body(_call("tavotto_export", {"session_id": sid, "formats": ["docx"]}))["code"]
        == "bad_format"
    )
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
    ids = [
        _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
        for _ in range(4)
    ]
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
        "跑一次 python scripts/build_mcp_widget.py"
    )


def test_widget_check_tells_missing_apart_from_stale(tmp_path, monkeypatch, capsys):
    """`--check` 的三档必须分得开：**不存在** ≠ **过期**。

    上一版把两者并成一句「产物过期……产物里是 None」。措辞不是重点，**处置
    不同才是**：刚 clone 下来还没跑过构建的人看到「过期」会去找自己改坏了
    什么；而在发布链上「不存在」意味着打出去的插件没有画布，是致命的。
    调用方按退出码分流（0 / 1 / 2），不靠读那句中文。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_mcp_widget

    out = tmp_path / "canvas.html"
    monkeypatch.setattr(build_mcp_widget, "OUT", out)

    # 不存在 → 2，而且那句话要说「还没构建」，不是「过期」
    assert build_mcp_widget.main(["--check"]) == 2, "产物不存在时必须是 2，不能与过期共用 1"

    # 存在但指纹对不上 → 1
    out.write_text(f"{build_mcp_widget.STAMP}deadbeefdeadbeef -->\n", encoding="utf-8")
    assert build_mcp_widget.main(["--check"]) == 1, "指纹不符是「过期」，退 1"

    # **存在但指纹读不出来 → 也是 1，不是 2**：截断的产物、或早于打戳那一版的
    # 旧产物，`current_fingerprint()` 同样回 None。拿那个返回值当「文件在不在」
    # 的代理，就会对着一个明明躺在磁盘上的文件说「它不存在」，并让调用方按
    # 「还没构建」处置——而正确动作是重建。评审 P2 抓的就是这一条。
    out.write_text("<!-- 截断了", encoding="utf-8")
    capsys.readouterr()  # 先清空：前两步的 stderr 还在缓冲里，不清就是量错对象
    rc = build_mcp_widget.main(["--check"])
    assert rc == 1, f"存在但读不出指纹是「过期」，退 1；实得 {rc}"
    err = capsys.readouterr().err
    assert "不存在" not in err, f"文件明明在，不许说它不存在：{err}"

    # 指纹对上 → 0
    out.write_text(
        f"{build_mcp_widget.STAMP}{build_mcp_widget.source_fingerprint()} -->\n",
        encoding="utf-8",
    )
    assert build_mcp_widget.main(["--check"]) == 0


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

    externals = [
        m.group(0)
        for m in _re.finditer(r"<(?:script|link)\b[^>]*>", html, _re.I)
        if _re.search(r'\b(?:src|href)\s*=\s*"(?!data:)', m.group(0), _re.I)
    ]
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
    assert body["ok"] is True  # 工具本身没坏
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
    project, fake_pool, monkeypatch, tmp_path
):
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


def test_open_session_is_revoked_when_project_retargets_outside_workspace(
    project, fake_pool, tmp_path, tmp_path_factory
):
    """会话不能在项目目录被换成越界 symlink 后继续复用旧的词法路径。"""
    opened = _body(_call("tavotto_open_figure", {"project_path": str(project)}))
    sid = opened["session_id"]
    worker_calls = len(fake_pool.calls)

    original = tmp_path / "figures-before-retarget"
    project.rename(original)
    outside = tmp_path_factory.mktemp("outside-workspace")
    try:
        project.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建目录 symlink: {exc}")

    result = _call("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    assert result["isError"] is True
    assert _body(result)["code"] == "workspace_root_changed"
    assert sid not in bridge.sessions()
    assert len(fake_pool.calls) == worker_calls


def test_session_project_is_recanonicalized_before_worker_access(
    project, fake_pool, monkeypatch, tmp_path_factory
):
    """Windows 无 symlink 权限时也要确定会话检查真的重新解析当前目标。"""
    opened = _body(_call("tavotto_open_figure", {"project_path": str(project)}))
    sid = opened["session_id"]
    worker_calls = len(fake_pool.calls)
    stored_project = os.path.normcase(os.path.normpath(opened["project"]))
    outside = str(tmp_path_factory.mktemp("simulated-retarget").resolve())
    original_canonical_path = bridge.canonical_path

    def retargeted(path):
        if os.path.normcase(os.path.normpath(str(path))) == stored_project:
            return outside
        return original_canonical_path(path)

    monkeypatch.setattr(bridge, "canonical_path", retargeted)
    result = _call("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    assert result["isError"] is True
    assert _body(result)["code"] == "workspace_root_changed"
    assert _body(result)["resolved_project"] == outside
    assert sid not in bridge.sessions()
    assert len(fake_pool.calls) == worker_calls


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
        'def main():\n    fig = plt.figure()\n    fig.savefig("Fig1.pdf")\n',
        encoding="utf-8",
    )
    (inner / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv(bridge.ROOTS_ENV, str(inner))  # 只允许 inner

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


def test_preview_png_failure_still_hands_back_the_session(project, fake_pool, monkeypatch):
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
    assert out["preview_png_error"] == "preview_failed"  # 不静默


def test_unreadable_preview_file_is_still_a_bridge_error(project, fake_pool, tmp_path):
    """worker 说成了、文件却读不出来，也必须走降级而不是裸 OSError。

    `Path(path).read_bytes()` 那一步在 try 之外，抛的是 OSError（被杀毒隔离、
    磁盘满、缓存目录被清都会）。`open_figure` 的降级只接 BridgeError，接不住
    它——那条刚登记的会话又变回谁也够不着的幽灵，正是这次修复要堵的洞。
    """
    # worker 报告成功、文件却不在（被杀毒隔离、缓存目录被清、磁盘满写了个寂寞）
    fake_pool.preview_png = lambda stem, patches, width, tag: str(tmp_path / "gone" / "nope.png")
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


def test_export_default_dir_goes_through_check_scope(project, fake_pool, tmp_path, monkeypatch):
    """项目设置里的 `export_dir` 可以是任意绝对路径（桌面版下完全合法）。

    默认值不过尺的话，一个对桌面版有效的项目就能让导出落到 MCP 的边界之外
    ——而调用方**显式**传同一个路径反而会被拒。
    """
    sid = _open(project)
    outside = tmp_path.parent / "elsewhere-export"
    monkeypatch.setattr(bridge.engine_config, "project_export_dir", lambda *a, **k: outside)
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
    done = bridge.export(
        sid,
        formats=[],
        journal={
            "name": "Some Journal",
            "preferred_formats": {"export_default": ["svg"]},
        },
    )
    assert [f["format"] for f in done["files"]] == ["svg"]


# ------------------------------ 重放自检 ------------------------------------
def test_replay_reports_missing_and_extra_elements():
    """结构分歧也是分歧。

    静默跳过「重放里没有这个元素」会让 `ok: true` 出现在两张画得完全不一样
    的图上——而脚本不确定 / 重放有 bug 正是这个自检唯一要抓的东西。
    """
    hot = {
        "size_mm": [80.0, 60.0],
        "elements": [
            {"gid": "figure", "bbox": [0, 0, 1, 1]},
            {"gid": "axes_0.line_0", "bbox": [0.1, 0.1, 0.5, 0.5]},
        ],
    }
    fresh = {
        "size_mm": [80.0, 60.0],
        "elements": [
            {"gid": "figure", "bbox": [0, 0, 1, 1]},
            {"gid": "axes_0.line_1", "bbox": [0.1, 0.1, 0.5, 0.5]},
        ],
    }
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


# --------------------- raster 档：内嵌画布不能变成空白 -----------------------
#
# 内嵌画布里**没有可连的 HTTP 服务**（sidecar 端口是动态的，MCP Apps 的 CSP
# 也不许连），所以 `svg=None` 那一刻要是响应里再没有别的东西，Codex 那边就是
# 一张全白的画布。ADR 0022 不变量 5：降级是**换一种画法**，不是不给画。
class RasterWorker(FakeWorker):
    """超过硬闸的那种图：manifest 照给，`svg` 一个字节都不给。"""

    def __init__(self) -> None:
        super().__init__()
        self.png_calls: list[tuple] = []

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        out = super().override(stem, patches, preview_dpi, inline_svg)
        out.pop("svg", None)
        out["preview"] = previewbudget.metadata(
            svg_bytes=126_132_735,
            mode=previewbudget.MODE_RASTER,
            reason=previewbudget.REASON_SVG_HARD_LIMIT,
        )
        return out

    def preview_png(self, stem, patches, width, tag):
        self.png_calls.append((stem, list(patches), width, tag))
        path = Path(self.png_dir) / f"{tag}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-pixels")
        return path


@pytest.fixture
def raster_pool(monkeypatch, tmp_path):
    worker = RasterWorker()
    worker.png_dir = tmp_path / "png"
    worker.png_dir.mkdir()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: worker)
    return worker


def test_raster_open_carries_a_bounded_png_instead_of_the_svg(project, raster_pool):
    out = bridge.open_figure(str(project))
    assert out["svg"] is None
    assert out["preview"]["mode"] == "raster"
    # **同一次响应**里就有画面，不必再跳一次
    assert out["preview_png_base64"]
    assert base64.b64decode(out["preview_png_base64"]).startswith(b"\x89PNG")
    # 尺寸受控：绝不把 giant SVG 转成 base64 塞回来
    assert raster_pool.png_calls[-1][2] == previewbudget.RASTER_PREVIEW_WIDTH_PX


def test_raster_apply_pairs_the_png_with_this_variant(project, raster_pool):
    """位图必须是**这一组 patches** 的。拿错一张就是「一个面板显示了另一个
    面板的图」——HTTP 那条路上正是为此才不再用 `/api/engine/png`。"""
    session = bridge.open_figure(str(project))["session_id"]
    patches = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 11}]
    out = bridge.apply_overrides(session, patches)

    assert out["svg"] is None
    assert out["preview_png_base64"]
    assert raster_pool.png_calls[-1][1] == patches


def test_raster_png_failure_does_not_turn_a_good_render_into_an_error(project, raster_pool):
    """manifest 是对的、编辑语义是完整的，缺的只是画面——如实回一个 code，
    别把整次渲染判成失败（那条会话就再也编辑不了了）。"""
    session = bridge.open_figure(str(project))["session_id"]

    def boom(*a, **k):
        raise bridge.BridgeError("画不出来", code="preview_failed")

    raster_pool.preview_png = boom
    out = bridge.apply_overrides(session, [])
    assert out["ok"] is True
    assert out["manifest"]["elements"]
    assert "preview_png_base64" not in out
    assert out["preview_png_error"] == "preview_failed"


def test_vector_render_never_pays_for_a_png(project, fake_pool):
    """普通图**一分钱都不多付**：`FakeWorker.preview_png` 一被调用就 assert。"""
    out = bridge.open_figure(str(project))
    assert out["svg"]
    assert "preview_png_base64" not in out


# ------------------------------ 刷新工具（ADR 0041） -------------------------
def _refresh(**args) -> dict:
    return _call("tavotto_refresh_project", args)


def _second_project(tmp_path, name: str, stem: str) -> Path:
    figs = tmp_path / name
    figs.mkdir()
    (figs / f"{stem.lower()}.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / f"{stem}.pdf").write_bytes(b"%PDF-1.4\n")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {f"{stem.lower()}.py": {"entry": "main", "cost": "light", "stems": [stem]}}}
        ),
        encoding="utf-8",
    )
    return figs


CANARY_SCRIPT = (
    "from pathlib import Path\n"
    "Path('IMPORTED.txt').write_text('x')\n\n\n"
    "def main():\n    Path('RAN.txt').write_text('x')\n"
    "    fig.savefig('Fig2.pdf')\n"
)


def test_refresh_tool_schema_and_description():
    """(1) schema (13) description：说清「不是运行脚本」「needs_probe 别猜」「conflict 别裁决」。"""
    tools = {t["name"]: t for t in server._tools()}
    tool = tools["tavotto_refresh_project"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"session_id", "project_path", "reason"}
    assert schema.get("required", []) == [], "输入尽量少：三个都可选"
    assert schema["properties"]["reason"]["enum"] == ["codex"]
    desc = tool["description"]
    for needle in (
        "不是运行脚本",
        "不 probe",
        "needs_probe",
        "conflict",
        "不需要手动刷新",
        "session_id",
    ):
        assert needle in desc, needle
    # (15) 不挂 UI：它的产出是文字与结构化结果，挂 UI 只会让画布不停重建
    assert "_meta" not in tool and "tavotto_refresh_project" not in server.UI_TOOLS
    # initialize 的 instructions 也告诉模型改完 .py 之后调它
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    assert (
        "tavotto_refresh_project"
        in s.dispatch("initialize", {"protocolVersion": "2025-06-18"})["instructions"]
    )


def test_refresh_via_the_authorized_session(project, fake_pool):
    """(2) 项目来自会话，不来自模型的文本。"""
    sid = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    res = _refresh(session_id=sid)
    assert not res.get("isError"), res
    body = _body(res)
    assert body["ok"] is True and body["reason"] == "codex"
    assert body["project_id"] == bridge.project_id(str(project))
    assert body["delivered"] == "local"  # 用例里 Tavotto 不可达
    assert body["sessions"] == [sid]
    assert "已刷新" in res["content"][0]["text"]


def test_refresh_rejects_an_unauthorized_path_and_writes_nothing(tmp_path, monkeypatch, fake_pool):
    """(3) 越界路径当场拒，且它的注册表一个字节都没动。"""
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, str(inside))
    outside = _second_project(tmp_path, "outside", "Fig9")
    (outside / "fig_new.py").write_text("def main():\n    pass\n", encoding="utf-8")
    before = (outside / "tavotto_registry.json").read_bytes()
    res = _refresh(project_path=str(outside))
    assert res["isError"] and _body(res)["code"] == "path_out_of_scope"
    assert (outside / "tavotto_registry.json").read_bytes() == before


def test_refresh_with_nothing_changed_is_an_empty_diff(project, fake_pool):
    """(4) 第二次起素材 diff 是真的跨轮比（第一次如实报 baseline）。"""
    first = _body(_refresh(project_path=str(project)))
    assert first["assets"]["baseline"] is True
    second = _body(_refresh(project_path=str(project)))
    assert second["assets"] == {"added": [], "removed": [], "changed": [], "baseline": False}
    assert second["registry"]["added_scripts"] == []
    assert second["registry"]["removed_scripts"] == []
    assert second["registry"]["changed_scripts"] == []


def test_refresh_sees_a_new_script_and_a_new_asset(project, fake_pool):
    """(5) Codex 新建了脚本与产物：diff 里都有，注册表已登记。"""
    _body(_refresh(project_path=str(project)))
    (project / "fig2.py").write_text("def main():\n    fig.savefig('Fig2.pdf')\n", encoding="utf-8")
    (project / "Fig2.pdf").write_bytes(b"%PDF-1.4\n")
    res = _refresh(project_path=str(project))
    body = _body(res)
    assert body["registry"]["added_scripts"] == ["fig2.py"]
    assert body["assets"]["added"] == ["Fig2.pdf"]
    reg = json.loads((project / "tavotto_registry.json").read_text(encoding="utf-8"))
    assert "fig2.py" in reg["scripts"]
    assert "fig2.py" in res["content"][0]["text"]


def test_refresh_returns_a_readiness_summary(project, fake_pool):
    """(6) readiness 只带 summary + 每张图的状态 / 原因 / 脚本，都是项目相对名。"""
    (project / "Orphan.pdf").write_bytes(b"%PDF-1.4\n")
    body = _body(_refresh(project_path=str(project)))
    ready = body["readiness"]
    assert ready["summary"]["total"] == 2 and ready["summary"]["editable"] == 1
    by_id = {p["id"]: p for p in ready["panels"]}
    assert by_id["Fig1.pdf"]["status"] == "editable" and by_id["Fig1.pdf"]["script"] == "fig1.py"
    assert by_id["Orphan.pdf"]["status"] != "editable"
    assert set(by_id["Orphan.pdf"]) == {
        "id",
        "stem",
        "status",
        "reason_code",
        "script",
        "candidates",
    }


def test_refresh_never_probes_or_runs_user_code(project, fake_pool, monkeypatch):
    """(7)(8) 刷新只读 AST：不起 worker、不 import、不调 main()。"""
    from tavotto.engine import probe as engine_probe

    monkeypatch.setattr(engine_probe, "probe", lambda *a, **k: pytest.fail("不许 probe"))
    monkeypatch.setattr(
        engine_probe, "probe_and_register", lambda *a, **k: pytest.fail("不许 probe")
    )
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: pytest.fail("不许起 worker"))
    (project / "fig2.py").write_text(CANARY_SCRIPT, encoding="utf-8")
    body = _body(_refresh(project_path=str(project)))
    assert "fig2.py" in body["registry"]["added_scripts"]
    assert not (project / "IMPORTED.txt").exists() and not (project / "RAN.txt").exists()


def test_refresh_falls_back_to_local_when_the_app_is_unreachable(project, fake_pool):
    """(9a) 后端不可达：本地完成，`delivered=local` 说出口。"""
    res = _refresh(project_path=str(project))
    assert _body(res)["delivered"] == "local"
    assert "未在运行" in res["content"][0]["text"]


def test_refresh_delegates_to_a_running_app(project, fake_pool):
    """(9b) 后端可达：开项目（default=false）→ /api/project/refresh?pj= reason=codex → readiness。"""
    calls: list[tuple[str, dict | None]] = []

    def http(url, payload=None, timeout=10.0):
        calls.append((url, payload))
        if url.endswith("/api/version"):
            return 200, {"version": "x"}
        if url.endswith("/api/projects/open"):
            return 200, {"id": "pj-app"}
        if "/api/project/refresh" in url:
            return 200, {
                "reason": "codex",
                "registry": {
                    "added_scripts": ["fig2.py"],
                    "removed_scripts": [],
                    "changed_scripts": [],
                    "script_changes": {},
                    "added_stems": ["Fig2"],
                    "removed_stems": [],
                    "moved_stems": [],
                    "conflicts": {},
                    "conflicts_changed": False,
                },
                "assets": {"added": [], "removed": [], "changed": [], "baseline": False},
                "published": ["registry.changed"],
            }
        if "/api/project/readiness" in url:
            return 200, {"summary": {"total": 1, "editable": 1}, "panels": [], "conflicts": []}
        raise AssertionError(url)

    body = bridge.refresh_project(project_path=str(project), http_status=http)
    assert body["delivered"] == "app"
    assert body["registry"]["added_scripts"] == ["fig2.py"]
    assert body["readiness"]["summary"] == {"total": 1, "editable": 1}
    urls = [u for u, _ in calls]
    assert urls[1].endswith("/api/projects/open") and calls[1][1] == {
        "path": str(project),
        "default": False,
    }
    assert "/api/project/refresh?pj=pj-app" in urls[2] and calls[2][1] == {"reason": "codex"}
    assert "/api/project/readiness?pj=pj-app" in urls[3]


def test_refresh_surfaces_the_running_apps_error_code(project, fake_pool):
    """(9c) 运行中的 Tavotto 刷不成：原样带回它的 code，不退回本地再试一遍。"""

    def http(url, payload=None, timeout=10.0):
        if url.endswith("/api/version"):
            return 200, {}
        if url.endswith("/api/projects/open"):
            return 200, {"id": "pj-app"}
        return 400, {"error": "扫描失败", "code": "scan_failed", "params": {"reason": "x"}}

    with pytest.raises(bridge.BridgeError) as exc:
        bridge.refresh_project(project_path=str(project), http_status=http)
    assert exc.value.code == "scan_failed"


def test_refresh_without_a_project_is_no_project(project, fake_pool):
    """(10) 没会话、没路径：说清要先开图或传路径。"""
    res = _refresh()
    assert res["isError"] and _body(res)["code"] == "no_project"
    assert "tavotto_open_figure" in _body(res)["error"]


def test_refresh_is_ambiguous_with_two_projects_and_isolated_with_a_session(
    project, tmp_path, fake_pool
):
    """(11) 两个项目都开着会话：不传就拒；传 session_id 只刷那一个。"""
    other = _second_project(tmp_path, "other", "Fig7")
    sid_a = _body(_call("tavotto_open_figure", {"project_path": str(project)}))["session_id"]
    sid_b = _body(_call("tavotto_open_figure", {"project_path": str(other)}))["session_id"]
    res = _refresh()
    assert res["isError"] and _body(res)["code"] == "ambiguous_project"
    # 错误里列的是会话 id → 项目短 id，不是路径
    assert str(tmp_path) not in json.dumps(_body(res))

    (project / "fig_a.py").write_text(
        "def main():\n    fig.savefig('FigA.pdf')\n", encoding="utf-8"
    )
    (other / "fig_b.py").write_text("def main():\n    fig.savefig('FigB.pdf')\n", encoding="utf-8")
    body = _body(_refresh(session_id=sid_a))
    assert body["registry"]["added_scripts"] == ["fig_a.py"]
    assert body["sessions"] == [sid_a]
    reg_b = json.loads((other / "tavotto_registry.json").read_text(encoding="utf-8"))
    assert "fig_b.py" not in reg_b["scripts"], "另一个项目的注册表不该被这次刷新碰到"
    assert sid_b in bridge.sessions()


def test_refresh_result_contains_no_absolute_paths(project, tmp_path, fake_pool):
    """(12) 结果里只有项目短 id 与相对名。"""
    (project / "sub").mkdir()
    (project / "sub" / "fig3.py").write_text(
        "def main():\n    fig.savefig('Fig3.pdf')\n", encoding="utf-8"
    )
    res = _refresh(project_path=str(project))
    blob = json.dumps(res, ensure_ascii=False)
    assert str(tmp_path) not in blob and str(project) not in blob
    assert "sub/fig3.py" in json.dumps(_body(res)["registry"]["added_scripts"])


def test_refresh_reason_is_fixed_to_codex_whatever_the_model_passes(
    project, fake_pool, monkeypatch
):
    """来由进日志、事件与遥测维度：模型传什么都不透传。"""
    seen: list[str] = []
    real = bridge.engine_refresh.refresh_project_index

    def spy(ctx, **kw):
        seen.append(kw["reason"])
        return real(ctx, **kw)

    monkeypatch.setattr(bridge.engine_refresh, "refresh_project_index", spy)
    _refresh(project_path=str(project), reason="manual")
    assert seen == ["codex"]


def test_refresh_on_a_directory_without_a_registry_is_a_clear_error(
    tmp_path, monkeypatch, fake_pool
):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "fig.py").write_text("def main():\n    pass\n", encoding="utf-8")
    res = _refresh(project_path=str(empty))
    assert res["isError"] and _body(res)["code"] in ("no_registry", "handoff_failed")
