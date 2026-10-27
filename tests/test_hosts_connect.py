"""`tavotto hosts`：「接入 AI 编辑器」的安装器（用户 2026-10-27）。

主语：**用户机器上宿主的配置文件**（这里在临时 HOME / APPDATA 里造）——写之前备份、只动 tavotto 一条、读不懂不写、
断开只删 tavotto；以及写进去的那条命令真的起得来（health 用源码树的 `python -m tavotto mcp` 真跑一次）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tavotto.engine import cli as engine_cli, hostconnect as H


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(h / ".config"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "tavotto-config"))
    monkeypatch.setattr(H, "health", lambda launch, env: H._step("health", ok=True, detail="stub"))
    return h


@pytest.fixture()
def project(home):
    p = home / "papers" / "figs"
    p.mkdir(parents=True)
    return p


def test_hosts_is_a_cli_command():
    assert "hosts" in engine_cli.COMMANDS


def test_project_root_must_be_a_specific_folder(home, project, tmp_path):
    assert H.check_project_root(str(project))[0] == str(project.resolve())
    for bad in (
        str(home),
        str(home.parent),
        str(Path(home.anchor)),
        str(tmp_path / "nope"),
        "rel/dir",
    ):
        got, why = H.check_project_root(bad)
        assert got is None and why, bad


def test_connect_cursor_adds_only_tavotto_backs_up_and_is_idempotent(home, project):
    cfg = home / ".cursor" / "mcp.json"
    cfg.parent.mkdir()
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}), encoding="utf-8"
    )
    res = H.connect("cursor", str(project))
    assert res["ok"] and res["state"] == "needs_new_session", res
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["keep"] == 1 and data["mcpServers"]["other"] == {"command": "x"}
    entry = data["mcpServers"]["tavotto"]
    launch = H.mcp_launch()
    assert [entry["command"], *entry["args"]] == [launch["command"], *launch["args"]]
    assert entry["env"] == {H.ROOTS_ENV: str(project.resolve())}
    backups = list(cfg.parent.glob("mcp.json.tavotto-backup-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["mcpServers"] == {
        "other": {"command": "x"}
    }
    again = H.connect("cursor")  # 文件夹记住了，所有宿主共用
    assert again["ok"] and again["state"] == "connected"
    assert next(s for s in again["steps"] if s["step"] == "config")["skipped"]
    assert H.connected("cursor", launch, H._env_for(str(project.resolve())))


def test_disconnect_removes_only_tavotto(home, project):
    cfg = home / ".cursor" / "mcp.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert H.connect("cursor", str(project))["ok"]
    res = H.disconnect("cursor")
    assert res["ok"] and res["state"] == "disconnected"
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"] == {"other": {"command": "x"}}


def test_a_config_it_cannot_read_is_left_untouched(home, project):
    cfg = home / ".cursor" / "mcp.json"
    cfg.parent.mkdir()
    jsonc = '{\n  // my servers\n  "mcpServers": {}\n}\n'
    cfg.write_text(jsonc, encoding="utf-8")
    res = H.connect("cursor", str(project))
    assert not res["ok"] and res["error_code"] == H.ERR_UNREADABLE
    assert cfg.read_text(encoding="utf-8") == jsonc
    assert not list(cfg.parent.glob("*.tavotto-backup-*"))


def test_vscode_user_config_uses_servers_with_stdio_type(home, project):
    user = H._appdata() / "Code" / "User"
    user.mkdir(parents=True)
    assert H.connect("vscode", str(project))["ok"]
    entry = json.loads((user / "mcp.json").read_text(encoding="utf-8"))["servers"]["tavotto"]
    assert entry["type"] == "stdio"


def test_zcode_writes_both_places_it_reads(home, project):
    (home / ".zcode").mkdir()
    assert H.connect("zcode", str(project))["ok"]
    for f in (
        home / ".zcode" / "cli" / "config.json",
        home / ".zcode" / "workspace" / "default" / ".zcode" / "config.json",
    ):
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["features"]["mcp"] is True
        assert d["mcp"]["servers"]["tavotto"]["enabled"] is True
    assert H.disconnect("zcode")["ok"]
    assert (
        "tavotto"
        not in json.loads((home / ".zcode" / "cli" / "config.json").read_text())["mcp"]["servers"]
    )


def test_workbuddy_trusts_only_the_tavotto_entry(home, project):
    (home / ".workbuddy").mkdir()
    approvals = home / ".workbuddy" / "mcp-approvals.json"
    approvals.write_text(json.dumps({"old::other": 1}), encoding="utf-8")
    assert H.connect("workbuddy", str(project))["ok"]
    launch, env = H.mcp_launch(), H._env_for(str(project.resolve()))
    got = json.loads(approvals.read_text(encoding="utf-8"))
    assert set(got) == {"old::other", f"{H.workbuddy_trust_hash(launch, env)}::tavotto"}


def test_workbuddy_trust_hash_matches_workbuddys_own_algorithm():
    # 期望值由 WorkBuddy 5.8.3 app.asar 里 computeTrustConfigHash 的原样 JS 在 node 里算出（与 Windows Lab 那份同一向量）
    launch = {
        "command": "C:\\py\\python.exe",
        "args": ["-I", "C:\\r\\mcp_recorder.py", "--log", "x"],
    }
    env = {"TAVOTTO_NO_TELEMETRY": "1", "TAVOTTO_MCP_ROOTS": "C:\\p"}
    assert (
        H.workbuddy_trust_hash(launch, env)
        == "bb3649d452650514f027a7cc610f88cf40cbc3d4a43371365080e189f63fec14"
    )


def test_claude_desktop_standard_config(home, project):
    d = H._appdata() / "Claude"
    d.mkdir(parents=True)
    assert H.connect("claude-desktop", str(project))["ok"]
    assert "tavotto" in json.loads((d / "claude_desktop_config.json").read_text())["mcpServers"]


def test_not_installed_and_missing_folder_are_reported_not_guessed(home, project):
    res = H.connect("cursor", str(project))
    assert (
        not res["ok"]
        and res["state"] == "not_installed"
        and res["error_code"] == H.ERR_NOT_INSTALLED
    )
    assert not (home / ".cursor").exists()  # 没装就不建它的目录
    res = H.connect("trae")  # 上一条已记住文件夹；trae 没装
    assert res["error_code"] == H.ERR_NOT_INSTALLED


def test_first_connect_without_a_folder_asks_for_one(home):
    (home / ".cursor").mkdir()
    res = H.connect("cursor")
    assert not res["ok"] and res["error_code"] == H.ERR_NO_ROOT


def test_list_reports_each_host(home, project):
    (home / ".cursor").mkdir()
    assert H.connect("cursor", str(project))["ok"]
    out = H.list_hosts()
    by = {h["id"]: h for h in out["hosts"]}
    assert list(by) == list(H.HOST_IDS)
    assert by["cursor"] == {"id": "cursor", "label": "Cursor", "installed": True, "connected": True}
    assert by["zcode"]["installed"] is False
    assert out["project_root"] == str(project.resolve())


def test_health_really_starts_the_command_it_writes(tmp_path, monkeypatch):
    """不打桩：源码树里 `mcp_launch()` 就是 `python -m tavotto mcp`，真握手、列出插件那一份工具。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    root = tmp_path / "proj"
    root.mkdir()
    launch = H.mcp_launch()
    assert launch == {"command": sys.executable, "args": ["-m", "tavotto", "mcp"]}
    step = H.health(launch, {H.ROOTS_ENV: str(root)})
    assert step["ok"], step


@pytest.mark.skipif(
    os.pathsep != ":", reason="POSIX 上的分隔符是冒号（Windows 是分号，同一条规则）"
)
def test_a_folder_whose_name_contains_the_roots_separator_is_rejected(home):
    """`/x/project:archive` 写进 TAVOTTO_MCP_ROOTS 会被切成两个根、放行用户没选的目录（Codex #1455 P1）。"""
    bad = home / "papers" / "project:archive"
    bad.mkdir(parents=True)
    got, why = H.check_project_root(str(bad))
    assert got is None and os.pathsep in why
    (home / ".cursor").mkdir()
    res = H.connect("cursor", str(bad))
    assert res["error_code"] == H.ERR_BAD_ROOT and not (home / ".cursor" / "mcp.json").exists()


def test_codex_connect_needs_neither_the_bundled_server_nor_a_folder(home, monkeypatch):
    """pip / pipx 装的没有 MCP 服务端，但 Codex 走插件：照样委托 codexinstall（Codex #1455 P2）。"""
    monkeypatch.setattr(H, "mcp_launch", lambda: None)
    calls = []

    def fake_connect():
        calls.append(1)
        return [H._step("plugin", ok=True)]

    monkeypatch.setattr(H, "_codex_connect", fake_connect)
    res = H.connect("codex")
    assert calls == [1] and res["ok"] and res["state"] == "needs_new_session"


def test_cli_managed_hosts_report_their_connection_state(home, monkeypatch):
    """Codex / Claude Code 接好后列表里不能永远是「未接入」（Codex #1455 P2）。"""
    monkeypatch.setattr(H, "_codex_connected", lambda: True)
    monkeypatch.setattr(H, "_claude_argv", lambda: ["claude"])
    monkeypatch.setattr(
        H,
        "_run",
        lambda argv, timeout=120: (
            0,
            "plugin:tavotto:tavotto: /x/launch - ✓ Connected\nother: y\n",
        ),
    )
    assert H.connected("codex", None, {}) is True
    assert H.connected("claude-code", None, {}) is True
    monkeypatch.setattr(H, "_run", lambda argv, timeout=120: (0, "other: y\nnot-tavotto: z\n"))
    assert H.connected("claude-code", None, {}) is False
