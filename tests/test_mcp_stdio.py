"""MCP server 的**真子进程 stdio** 集成：协议层，不需要 matplotlib。

`tests/test_mcp_roundtrip.py` 盯真渲染链路（缺科学栈会跳过）；这里盯的是
两种启动形态本身——一台机器上无论装没装引擎，Codex 连上来都必须拿到诚实
的回答：

* **engine 模式**（本仓库 .venv，import 得到 tavotto）：serverInfo.version
  是真实版本号（不是 "0"），tools/list 里 open/apply 挂着
  `ui://tavotto/canvas/v1.html` 的 UI metadata，resources/list 能发现画布、
  resources/read 能读回完整 HTML；
* **degraded 模式**（专门造一个没有 tavotto 的解释器）：version 是 "0"，
  tools/list 只有 tavotto_health，六个正常工具回结构化错误，没有资源——
  **desktop-only 环境绝不假装有 Codex 画布**。

顺带记录启动延迟（体检门槛的性能预算）。
"""

import json
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "codex-plugin" / "mcp" / "server.py"
WIDGET_URI = "ui://tavotto/canvas/v1.html"


def _widget_available() -> bool:
    """server 会加载的那份画布在不在（`TAVOTTO_MCP_WIDGET` 可指到候选产物）。"""
    override = os.environ.get("TAVOTTO_MCP_WIDGET")
    path = Path(override) if override else ROOT / "codex-plugin" / "mcp" / "widget" / "canvas.html"
    return path.is_file() and path.stat().st_size > 0


class Client:
    def __init__(self, argv_python: str, env: dict, cwd: str | None = None):
        self.proc = subprocess.Popen(
            [argv_python, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd or str(ROOT),
        )
        self.n = 0

    def write(self, msg: dict) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(
                "server 挂了:\n" + self.proc.stderr.read().decode("utf-8", "replace")[-4000:]
            )
        return json.loads(line.decode("utf-8"))

    def call(self, method: str, params=None) -> dict:
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.write(msg)
        return self.read()

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=60)


@pytest.fixture()
def engine_client(tmp_path):
    env = {
        **os.environ,
        "TAVOTTO_MCP_ROOTS": str(tmp_path),
        "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
    }
    c = Client(sys.executable, env)
    yield c
    c.close()


@pytest.fixture()
def rootless_engine_client(tmp_path):
    """模拟真实安装：cwd 在插件包内，且没有任何手工根环境变量。"""
    env = {**os.environ, "TAVOTTO_DATA_DIR": str(tmp_path / "data")}
    for name in (
        "TAVOTTO_MCP_ROOTS",
        "TAVOTTO_MCP_WORKSPACE",
        "CODEX_WORKSPACE_ROOT",
        "CODEX_PROJECT_ROOT",
        "CODEX_WORKSPACE_DIR",
    ):
        env.pop(name, None)
    c = Client(sys.executable, env, cwd=str(ROOT / "codex-plugin"))
    yield c
    c.close()


@pytest.fixture()
def degraded_client(tmp_path):
    """一个真的 import 不到 tavotto 的解释器 + 空到底的发现环境。"""
    venv_dir = tmp_path / "bare-venv"
    venv.EnvBuilder(with_pip=False).create(str(venv_dir))
    bare = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = {
        **os.environ,
        "PATH": str(empty),  # PATH 兜底也不给
        "HOME": str(tmp_path),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
        "LOCALAPPDATA": str(tmp_path / "lapp"),
        "PROGRAMFILES": str(tmp_path / "pf"),
    }
    for name in (
        "TAVOTTO_CLI",
        "TAVOTTO_MCP_PYTHON",
        "TAVOTTO_WORKER_PYTHON",
        "MM_WORKER_PYTHON",
        "TAVOTTO_MCP_EXECED",
        "PYTHONPATH",
    ):
        env.pop(name, None)
    c = Client(str(bare), env)
    yield c
    c.close()


# -------------------------------- engine 模式 -------------------------------
def test_engine_mode_reports_the_real_version_and_ui(engine_client):
    import tavotto

    t0 = time.monotonic()
    res = engine_client.call(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )
    startup_ms = int((time.monotonic() - t0) * 1000)
    info = res["result"]["serverInfo"]
    assert info["version"] == tavotto.__version__ and info["version"] != "0"
    # 启动预算：协议层不该拖秒级（真渲染另算）。CI 宽限 10s，本地实测 <1s。
    assert startup_ms < 10_000, f"initialize 花了 {startup_ms}ms"
    print(f"\n[perf] mcp initialize: {startup_ms}ms")

    tools = engine_client.call("tools/list")["result"]["tools"]
    by_name = {t["name"]: t for t in tools}
    assert "tavotto_health" in by_name
    for name in ("tavotto_open_figure", "tavotto_apply_overrides"):
        meta = by_name[name].get("_meta") or {}
        # 画布不入库（ADR 0043）：有产物时必须挂 UI，没有时必须不挂——两头都不许撒谎
        assert ("_meta" in by_name[name]) is _widget_available(), name
        if _widget_available():
            assert meta.get("ui", {}).get("resourceUri") == WIDGET_URI
            assert meta.get("openai/outputTemplate") == WIDGET_URI
    for name in ("tavotto_preflight", "tavotto_export", "tavotto_close_session"):
        assert "_meta" not in by_name[name], f"{name} 不该挂 UI（画布会不停重建）"


def test_engine_mode_serves_the_canvas_resource(engine_client):
    if not _widget_available():
        pytest.skip(
            "画布产物未构建：本地跑一次 scripts/build_mcp_widget.py；CI 在 plugin-candidate job 上对真产物执行"
        )
    engine_client.call("initialize", {"protocolVersion": "2025-11-25"})
    listed = engine_client.call("resources/list")["result"]["resources"]
    assert [r["uri"] for r in listed] == [WIDGET_URI]
    read = engine_client.call("resources/read", {"uri": WIDGET_URI})["result"]
    text = read["contents"][0]["text"]
    assert text.startswith("<!-- tavotto-mcp-widget")
    assert len(text) > 100_000, "画布是自包含单文件，不该是个空壳"
    assert read["contents"][0]["mimeType"] == "text/html;profile=mcp-app"


def test_engine_mode_health_tool_says_ready(engine_client, tmp_path):
    engine_client.call("initialize", {"protocolVersion": "2025-11-25"})
    res = engine_client.call("tools/call", {"name": "tavotto_health", "arguments": {}})["result"]
    assert not res.get("isError")
    body = res["structuredContent"]
    assert body["ok"] is True and body["engine"]["available"] is True
    assert body["canvas"]["available"] is _widget_available(), "体检必须如实报画布在不在"
    assert str(tmp_path) in body["roots"]


def test_real_stdio_client_supplies_workspace_through_roots(rootless_engine_client, tmp_path):
    """真子进程验证双向 JSON-RPC：不是直接调用 RootAuthority 的假绿。"""
    c = rootless_engine_client
    c.write(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "fake-codex", "version": "1"},
            },
        }
    )
    assert c.read()["result"]["protocolVersion"] == "2025-11-25"
    c.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
    c.write(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tavotto_health",
                "arguments": {},
            },
        }
    )
    request = c.read()
    assert request["method"] == "roots/list"
    c.write(
        {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"roots": [{"uri": tmp_path.resolve().as_uri()}]},
        }
    )
    response = c.read()["result"]["structuredContent"]
    assert response["roots"] == [str(tmp_path.resolve())]
    assert response["root_authority"]["source"] == "mcp_roots"
    assert response["root_authority"]["client"]["name"] == "fake-codex"


def test_real_stdio_client_confirms_workspace_through_elicitation(rootless_engine_client, tmp_path):
    """真子进程双向握手：模型给候选，host 代表用户确认后才成为根。"""
    c = rootless_engine_client
    init = c.call(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {"elicitation": {}},
            "clientInfo": {"name": "codex-mcp-client", "version": "test"},
        },
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    c.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
    c.n += 1
    c.write(
        {
            "jsonrpc": "2.0",
            "id": c.n,
            "method": "tools/call",
            "params": {
                "name": "tavotto_open_figure",
                "arguments": {"project_path": str(tmp_path.resolve())},
            },
        }
    )
    request = c.read()
    assert request["method"] == "elicitation/create"
    assert str(tmp_path.resolve()) in request["params"]["message"]
    assert request["params"]["requestedSchema"]["required"] == ["approve"]
    c.write(
        {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "action": "accept",
                "content": {"approve": True},
            },
        }
    )
    # 目录为空，所以业务层应诚实报 no_registry/no_figure；关键是授权已完成，
    # 不是 no_workspace_root，也不是传输死锁。
    opened = c.read()["result"]
    assert opened["isError"] is True
    assert opened["structuredContent"]["code"] in ("no_registry", "no_figure")

    health = c.call(
        "tools/call",
        {
            "name": "tavotto_health",
            "arguments": {},
        },
    )["result"]["structuredContent"]
    assert health["roots"] == [str(tmp_path.resolve())]
    assert health["root_authority"]["source"] == "user_elicitation"
    assert health["root_authority"]["workspace_confirmation"]["state"] == "accepted"


def test_engine_mode_never_reaches_for_a_browser():
    """内嵌画布与浏览器是两条路：MCP 桥的代码里不许出现 webbrowser /
    「开 localhost」——那是把外部窗口冒充画布。"""
    mcp_pkg = ROOT / "codex-plugin" / "mcp" / "tavotto_mcp"
    for py in mcp_pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "webbrowser" not in src, f"{py.name} 里出现了 webbrowser"
        assert "open_new_tab" not in src


# ------------------------------- degraded 模式 ------------------------------
def test_degraded_mode_over_real_stdio(degraded_client):
    t0 = time.monotonic()
    res = degraded_client.call("initialize", {"protocolVersion": "2025-06-18"})
    startup_ms = int((time.monotonic() - t0) * 1000)
    assert res["result"]["serverInfo"]["version"] == "0"
    # 失败要**快**：以前是「先出图、后发现 desktop_only」，几分钟白干。
    # 降级判定只有本机文件探测，预算 10s（CI 宽限；本地 <1s）。
    assert startup_ms < 10_000, f"降级判定花了 {startup_ms}ms"
    print(f"\n[perf] degraded initialize: {startup_ms}ms")

    tools = degraded_client.call("tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["tavotto_health"]

    res = degraded_client.call(
        "tools/call", {"name": "tavotto_open_figure", "arguments": {"script_path": "/tmp/fig.py"}}
    )["result"]
    assert res["isError"] is True
    body = res["structuredContent"]
    assert body["ok"] is False
    assert body["code"] in (
        "tavotto_missing",
        "desktop_only",
        "desktop_found_cli_missing",
        "engine_unavailable",
    )
    assert body["canvas"]["available"] is False
    assert body["recovery"]
    text = res["content"][0]["text"]
    assert "已打开" not in text and "已就绪" not in text

    resources = degraded_client.call("resources/list")["result"]["resources"]
    assert resources == [], "desktop-only 环境不能假装有 Codex 画布资源"


def test_degraded_mode_health_check_is_actionable(degraded_client):
    degraded_client.call("initialize", {"protocolVersion": "2025-06-18"})
    res = degraded_client.call("tools/call", {"name": "tavotto_health", "arguments": {}})["result"]
    assert not res.get("isError")
    body = res["structuredContent"]
    assert body["engine"]["available"] is False
    joined = " ".join(body["recovery"])
    assert "--provision" in joined and "新开" in joined
