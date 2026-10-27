"""`tavotto mcp`：在进程内跑插件那一份 MCP 服务端（「接入 AI 编辑器」，用户 2026-10-27）。

主语：**宿主按配置起的那个进程**——`<tavotto-cli> mcp`（这里用源码树的 `python -m tavotto mcp` 代它）；
它的 stdout 是不是只有 JSON-RPC、工具是不是插件那一份、`tavotto_health` 能不能真的答。冻结产物那一侧由
`packaging/tavotto.spec` 的收集与 `scripts/smoke_app.py --exe` 的握手看护（下面两条）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import cli as engine_cli  # noqa: E402, I001
from tavotto.engine import mcpserve  # noqa: E402
from tavotto_mcp import server as mcp_server  # noqa: E402

PROTOCOL = "2025-06-18"


def _serve(tmp_path: Path, calls: list[dict]) -> tuple[list[dict], str, int]:
    """像宿主那样起 `tavotto mcp`：握手 + 列工具 + 逐个调用，stdin 关掉即收摊。"""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("TAVOTTO_", "MM_", "PYTHONPATH"))
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        TAVOTTO_DATA_DIR=str(tmp_path / "data"),
        TAVOTTO_CONFIG_DIR=str(tmp_path / "config"),
        TAVOTTO_MCP_ROOTS=str(project),
        TAVOTTO_NO_TELEMETRY="1",
        PYTHONIOENCODING="utf-8",
    )
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "mcp-serve-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for i, call in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": call})
    proc = subprocess.run(
        [sys.executable, "-m", "tavotto", "mcp"],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(project),
        timeout=300,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return [json.loads(line) for line in lines], proc.stderr, proc.returncode


def test_tavotto_mcp_serves_the_plugin_tools_over_clean_stdio(tmp_path):
    out, err, rc = _serve(tmp_path, [{"name": "tavotto_health", "arguments": {}}])
    assert rc == 0, err[-2000:]
    by_id = {m.get("id"): m for m in out}
    # stdout 每一行都是 JSON-RPC（json.loads 没抛），且都是对请求的回答
    assert all(m.get("jsonrpc") == "2.0" for m in out)
    assert by_id[1]["result"]["serverInfo"]["name"] == mcp_server.SERVER_NAME
    listed = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert listed == set(mcp_server.HANDLERS)  # 插件那一份，一个不多一个不少（不是降级 server）
    health = by_id[10]["result"]
    assert not health.get("isError"), health
    text = json.dumps(health, ensure_ascii=False)
    assert "tavotto" in text.lower()


def test_tavotto_mcp_is_dispatched_before_the_app_imports():
    assert "mcp" in engine_cli.COMMANDS


def test_without_the_server_package_it_says_so_on_stderr_and_never_touches_stdout(
    monkeypatch, capsys
):
    def missing():
        raise ImportError("No module named 'tavotto_mcp'")

    monkeypatch.setattr(mcpserve, "_import_server", missing)
    assert mcpserve.cli([]) == 2
    cap = capsys.readouterr()
    assert cap.out == ""  # stdout 归协议：宿主那边读到的是「没有回答」，而不是一行非 JSON
    assert json.loads(cap.err)["error_code"] == mcpserve.NOT_BUNDLED


def _analysis_hiddenimports(spec: Path) -> list[ast.AST]:
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis":
            for kw in node.keywords:
                if kw.arg == "hiddenimports":
                    return list(kw.value.elts)
    raise AssertionError("tavotto.spec 里没找到 Analysis(hiddenimports=…)")


def test_frozen_build_collects_the_mcp_server_package():
    """冻结产物里 mcpserve 运行时才 import tavotto_mcp，静态分析看不见：spec 必须点名收整包。"""
    spec = ROOT / "packaging" / "tavotto.spec"
    starred = {
        e.value.id
        for e in _analysis_hiddenimports(spec)
        if isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)
    }
    assert "MCP_SERVER_MODULES" in starred
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    collected = [
        n.value.args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "MCP_SERVER_MODULES" for t in n.targets)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "collect_submodules"
    ]
    assert collected == ["tavotto_mcp"]


@pytest.mark.parametrize("name", ["--help", "-h"])
def test_help_goes_to_stderr(name, capsys):
    assert mcpserve.cli([name]) == 0
    cap = capsys.readouterr()
    assert cap.out == "" and "tavotto mcp" in cap.err


def test_smoke_resolves_the_frozen_cli_before_the_handshake_changes_directory(
    tmp_path, monkeypatch
):
    """CI 传相对的 `--exe dist/Tavotto/Tavotto`，握手的 cwd 是示例项目：CLI 路径必须先变成绝对的（Codex #1451 P1）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import smoke_app

    monkeypatch.chdir(tmp_path)
    for exe in ("dist/Tavotto/Tavotto", "dist/Tavotto/Tavotto.exe"):
        cli = smoke_app.frozen_cli_for(exe)
        assert cli.is_absolute()
        assert cli.parent == (tmp_path / "dist" / "Tavotto").resolve()
        assert cli.name == "tavotto-cli" + Path(exe).suffix
