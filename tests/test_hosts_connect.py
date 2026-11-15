"""`tavotto hosts`：「接入 AI 编辑器」的安装器（用户 2026-10-27）。

主语：**用户机器上宿主的配置文件**（这里在临时 HOME / APPDATA 里造）——写之前备份、只动 tavotto 一条、读不懂不写、
断开只删 tavotto；以及写进去的那条命令真的起得来（health 用源码树的 `python -m tavotto mcp` 真跑一次）。
点一下就接好、不选文件夹（用户 2026-11-04，ADR 0009 §1b）：会报项目目录的宿主 `env` 为空，其余写本机兜底（只拦凭据类文件）；
旧版写过的 `TAVOTTO_MCP_ROOTS=<选的文件夹>` 在 `list` 时一次性迁移。
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


LOCAL_SCOPE = {H.SCOPE_ENV: H.SCOPE_LOCAL}


def test_explicit_project_root_must_be_a_specific_folder(home, project, tmp_path):
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
    res = H.connect("cursor")  # 不选文件夹
    assert res["ok"] and res["state"] == "needs_new_session", res
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["keep"] == 1 and data["mcpServers"]["other"] == {"command": "x"}
    entry = data["mcpServers"]["tavotto"]
    launch = H.mcp_launch()
    assert [entry["command"], *entry["args"]] == [launch["command"], *launch["args"]]
    assert entry["env"] == LOCAL_SCOPE
    backups = list(cfg.parent.glob("mcp.json.tavotto-backup-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["mcpServers"] == {
        "other": {"command": "x"}
    }
    again = H.connect("cursor")
    assert again["ok"] and again["state"] == "connected"
    assert next(s for s in again["steps"] if s["step"] == "config")["skipped"]
    assert H.connected("cursor", launch)
    assert "project_root" not in H.load_state()  # 不再记文件夹


def test_hosts_that_report_their_own_project_get_no_variable(home):
    """Claude Code（roots/list）、VS Code / WorkBuddy（工作区 cwd）：写死文件夹反而把它们锁在一个项目里。"""
    assert H.HOSTS_WITH_OWN_WORKSPACE == {"claude-code", "vscode", "workbuddy"}
    for h in H.HOST_IDS:
        if h == "codex":
            continue
        want = {} if h in H.HOSTS_WITH_OWN_WORKSPACE else LOCAL_SCOPE
        assert H.env_for(h) == want, h
    user = H._appdata() / "Code" / "User"
    user.mkdir(parents=True)
    assert H.connect("vscode")["ok"]
    assert json.loads((user / "mcp.json").read_text())["servers"]["tavotto"]["env"] == {}


def test_an_explicit_folder_from_the_cli_still_works_and_counts_as_connected(home, project):
    (home / ".cursor").mkdir()
    res = H.connect("cursor", str(project))
    assert res["ok"] and res["project_root"] == str(project.resolve())
    entry = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]["tavotto"]
    assert entry["env"] == {H.ROOTS_ENV: str(project.resolve())}
    assert H.connected("cursor", H.mcp_launch())
    assert "project_root" not in H.load_state()  # 显式文件夹只管这一次，不当成以后所有宿主的默认


def test_legacy_folder_entries_are_migrated_once(home, project, monkeypatch):
    """旧按钮写的 `TAVOTTO_MCP_ROOTS=<hosts.json 里的文件夹>` → 新形状；用户手写的别的文件夹不碰；只做一次。"""
    launch = H.mcp_launch()
    legacy = str(project.resolve())
    H._save_state({"project_root": legacy})
    (home / ".cursor").mkdir()
    cursor = home / ".cursor" / "mcp.json"
    cursor.write_text(
        json.dumps({"mcpServers": {"tavotto": H._plain(launch, {H.ROOTS_ENV: legacy})}}),
        encoding="utf-8",
    )
    other = home / "papers"
    trae_user = H._appdata() / "Trae" / "User"
    trae_user.mkdir(parents=True)
    trae = trae_user / "mcp.json"
    hand = {"mcpServers": {"tavotto": H._plain(launch, {H.ROOTS_ENV: str(other)})}}
    trae.write_text(json.dumps(hand), encoding="utf-8")
    monkeypatch.setattr(H, "_claude_argv", lambda: None)

    out = H.list_hosts()
    assert json.loads(cursor.read_text())["mcpServers"]["tavotto"]["env"] == LOCAL_SCOPE
    assert list(cursor.parent.glob("mcp.json.tavotto-backup-*"))
    assert json.loads(trae.read_text()) == hand
    by = {h["id"]: h for h in out["hosts"]}
    assert by["cursor"]["connected"] and by["trae"]["connected"]
    state = H.load_state()
    assert "project_root" not in state and state["migrated_project_root"] == legacy
    assert H.migrate_legacy_root(launch) == []


def test_legacy_claude_code_registration_is_redone_without_the_folder(home, project, monkeypatch):
    H._save_state({"project_root": str(project.resolve())})
    monkeypatch.setattr(H, "_claude_argv", lambda: ["claude"])
    calls = []

    def fake_run(argv, timeout=120):
        calls.append(argv)
        return (
            (0, "tavotto: /x/tavotto-cli mcp - ✓ Connected\n")
            if argv[1:3] == ["mcp", "list"]
            else (0, "")
        )

    monkeypatch.setattr(H, "_run", fake_run)
    H.migrate_legacy_root(H.mcp_launch())
    add = next(a for a in calls if a[1:3] == ["mcp", "add"])
    assert "-e" not in add  # Claude Code 用自己的 roots/list，不再带文件夹


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
    assert H.connect("vscode")["ok"]
    entry = json.loads((user / "mcp.json").read_text(encoding="utf-8"))["servers"]["tavotto"]
    assert entry["type"] == "stdio"


def test_zcode_writes_both_places_it_reads(home, project):
    (home / ".zcode").mkdir()
    assert H.connect("zcode")["ok"]
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
    assert H.connect("workbuddy")["ok"]
    launch, env = H.mcp_launch(), H.env_for("workbuddy")
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
    assert H.connect("claude-desktop")["ok"]
    entry = json.loads((d / "claude_desktop_config.json").read_text())["mcpServers"]["tavotto"]
    assert entry["env"] == LOCAL_SCOPE  # 没有项目 cwd / roots：本机兜底


def test_not_installed_is_reported_not_guessed(home):
    res = H.connect("cursor")
    assert (
        not res["ok"]
        and res["state"] == "not_installed"
        and res["error_code"] == H.ERR_NOT_INSTALLED
    )
    assert not (home / ".cursor").exists()  # 没装就不建它的目录
    assert H.connect("trae")["error_code"] == H.ERR_NOT_INSTALLED


def test_list_reports_each_host(home):
    (home / ".cursor").mkdir()
    assert H.connect("cursor")["ok"]
    out = H.list_hosts()
    by = {h["id"]: h for h in out["hosts"]}
    assert list(by) == list(H.HOST_IDS)
    assert by["cursor"] == {
        "id": "cursor",
        "label": "Cursor",
        "installed": True,
        "connected": True,
        "skill": "current",
        "version": H.hostskills._version(),  # 技能校验记录里的版本（本机读）
    }
    assert by["codex"]["skill"] == "builtin" and by["claude-desktop"]["skill"] == "builtin"
    assert by["zcode"]["installed"] is False
    assert "project_root" not in out


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
    assert H.connected("codex", None) is True
    assert H.connected("claude-code", None) is True
    monkeypatch.setattr(H, "_run", lambda argv, timeout=120: (0, "other: y\nnot-tavotto: z\n"))
    assert H.connected("claude-code", None) is False


# ----------------------------------------------------------------------------- Antigravity
def _agy_files(home):
    return (
        home / ".gemini" / "config" / "mcp_config.json",
        home / ".gemini" / "antigravity" / "mcp_config.json",
    )


def test_antigravity_is_detected_by_its_own_dirs_not_by_gemini_cli_alone(home):
    (home / ".gemini").mkdir()  # Gemini CLI 也用这个目录，不能算装了 Antigravity
    assert H.installed("antigravity") is False
    (home / ".gemini" / "config").mkdir()
    assert H.installed("antigravity") is True


def test_antigravity_old_dir_alone_counts_as_installed(home):
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    assert H.installed("antigravity") is True


def test_antigravity_writes_both_config_files_and_disconnect_drops_both(home, project):
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    res = H.connect("antigravity")
    assert res["ok"] and res["state"] == "needs_new_session"
    for f in _agy_files(home):
        entry = json.loads(f.read_text(encoding="utf-8"))["mcpServers"]["tavotto"]
        assert set(entry) == {"command", "args", "env"}  # 与 Cursor 同形，不加 type
        assert entry["env"] == LOCAL_SCOPE
    assert H.list_hosts()["hosts"][-1] == {
        "id": "antigravity",
        "label": "Antigravity",
        "installed": True,
        "connected": True,
        "skill": "current",
        "version": H.hostskills._version(),  # 技能校验记录里的版本（本机读）
    }
    assert H.disconnect("antigravity")["ok"]
    for f in _agy_files(home):
        assert "tavotto" not in json.loads(f.read_text(encoding="utf-8"))["mcpServers"]


def test_antigravity_treats_an_empty_existing_file_as_an_empty_object(home, project):
    new, old = _agy_files(home)
    new.parent.mkdir(parents=True)
    new.write_text("", encoding="utf-8")  # 真机残留：2.0 建了 0 字节的 mcp_config.json
    assert H.connect("antigravity")["ok"]
    assert "tavotto" in json.loads(new.read_text(encoding="utf-8"))["mcpServers"]
    assert old.is_file()  # 旧目录原本不存在，也一并建出来


def test_antigravity_merge_keeps_other_servers_and_is_idempotent(home, project):
    _new, old = _agy_files(home)
    old.parent.mkdir(parents=True)
    old.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}), encoding="utf-8"
    )
    assert H.connect("antigravity")["ok"]
    got = json.loads(old.read_text(encoding="utf-8"))
    assert got["keep"] == 1 and got["mcpServers"]["other"] == {"command": "x"}
    assert len(list(old.parent.glob("mcp_config.json.tavotto-backup-*"))) == 1
    again = H.connect("antigravity")
    assert again["ok"] and again["state"] == "connected"
    assert all(s["skipped"] for s in again["steps"] if s["step"] == "config")
    assert len(list(old.parent.glob("mcp_config.json.tavotto-backup-*"))) == 1
    assert json.loads(old.read_text(encoding="utf-8")) == got


def test_antigravity_unreadable_file_is_left_alone(home, project):
    new, _old = _agy_files(home)
    new.parent.mkdir(parents=True)
    new.write_text("{ // jsonc\n}", encoding="utf-8")
    res = H.connect("antigravity")
    assert not res["ok"] and res["error_code"] == H.ERR_UNREADABLE
    assert new.read_text(encoding="utf-8") == "{ // jsonc\n}"


def test_antigravity_not_installed_is_reported(home, project):
    res = H.connect("antigravity")
    assert not res["ok"] and res["state"] == "not_installed"
    assert not (home / ".gemini").exists()


def test_antigravity_paths_follow_userprofile_on_windows(tmp_path, monkeypatch):
    """Windows 下 `~` = %USERPROFILE%（`Path.home()` 读它）；两处都落在其下的 .gemini，不走 APPDATA。"""
    prof = tmp_path / "Users" / "me"
    prof.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: prof))
    monkeypatch.setenv("APPDATA", str(prof / "AppData" / "Roaming"))
    assert H._antigravity_dirs() == (prof / ".gemini" / "config", prof / ".gemini" / "antigravity")
    fh = H._FILE_HOSTS["antigravity"]
    assert [t.path for t in fh.targets()] == [
        prof / ".gemini" / "config" / "mcp_config.json",
        prof / ".gemini" / "antigravity" / "mcp_config.json",
    ]


# ----------------------------------------------------------------------------- 技能（hostskills）
def _steps(res):
    return {s["step"]: s for s in res["steps"]}


def test_connect_writes_the_skill_as_its_own_step_and_disconnect_removes_it(home):
    (home / ".cursor").mkdir()
    res = H.connect("cursor")
    assert res["ok"] and res["state"] == "needs_new_session", res
    st = _steps(res)["skill"]
    assert st["ok"] and not st["skipped"] and st["skill"] == "written"
    assert (home / ".cursor" / "skills" / "tavotto-figure" / "SKILL.md").is_file()
    again = H.connect("cursor")
    assert _steps(again)["skill"]["skipped"] and again["state"] == "connected"
    off = H.disconnect("cursor")
    assert off["ok"] and _steps(off)["skill"]["skill"] == "removed"
    assert not (home / ".cursor" / "skills" / "tavotto-figure").exists()


def test_a_skill_the_user_edited_is_kept_and_reported_not_failed(home):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    mine = home / ".cursor" / "skills" / "tavotto-figure" / "SKILL.md"
    mine.write_text(mine.read_text(encoding="utf-8") + "\n我加的一行\n", encoding="utf-8")
    res = H.connect("cursor")
    assert res["ok"] and _steps(res)["skill"]["skill"] == "modified"
    assert "我加的一行" in mine.read_text(encoding="utf-8")
    assert H.list_hosts()["hosts"][2]["skill"] == "modified"
    off = H.disconnect("cursor")
    assert off["ok"] and _steps(off)["skill"]["skill"] == "modified"
    assert mine.is_file()


def test_skill_failure_is_reported_honestly_but_mcp_stays_connected(home, monkeypatch):
    (home / ".cursor").mkdir()
    monkeypatch.setattr(
        H.hostskills,
        "install_skill",
        lambda host, **_kw: {
            "ok": False,
            "state": "failed",
            "skipped": False,
            "detail": "x",
            "error_code": "skill_write_failed",
        },
    )
    res = H.connect("cursor")
    assert (
        not res["ok"] and res["error_code"] == "skill_write_failed" and res["mcp_connected"] is True
    )
    assert (home / ".cursor" / "mcp.json").is_file()
    assert "health" in _steps(res)  # MCP 仍然体检


def test_connecting_from_a_wheel_install_succeeds_without_skill_files(home, monkeypatch):
    """pip / pipx / uvx 装法：MCP 接上就算接上，技能一步是 builtin，整次不判失败（Codex #1897 P2）。"""
    (home / ".cursor").mkdir()
    monkeypatch.setattr(H.hostskills, "skill_source", lambda: None)
    monkeypatch.setattr(H.hostskills, "wheel_install", lambda: True)
    res = H.connect("cursor")
    assert res["ok"] and res["state"] == "needs_new_session", res
    st = _steps(res)["skill"]
    assert st["ok"] and st["skipped"] and st["skill"] == "builtin"
    assert not (home / ".cursor" / "skills" / "tavotto-figure").exists()


def test_claude_desktop_has_no_skill_dir_and_reports_builtin(home):
    (home / "AppData" / "Roaming" / "Claude").mkdir(parents=True)
    (home / "Library" / "Application Support" / "Claude").mkdir(parents=True)
    (home / ".config" / "Claude").mkdir(parents=True)
    res = H.connect("claude-desktop")
    st = _steps(res)["skill"]
    assert res["ok"] and st["skipped"] and st["skill"] == "builtin"


def test_claude_code_with_the_plugin_installed_gets_no_second_copy(home, monkeypatch):
    monkeypatch.setattr(H, "_claude_argv", lambda: ["claude"])
    monkeypatch.setattr(H, "_run", lambda argv, timeout=120: (0, "plugin:tavotto:tavotto: x - ok"))
    res = H.connect("claude-code")
    assert res["ok"] and "skill" not in _steps(res)
    assert not (home / ".claude" / "skills").exists()


def test_claude_code_without_the_plugin_gets_the_skill(home, monkeypatch):
    monkeypatch.setattr(H, "_claude_argv", lambda: ["claude"])
    monkeypatch.setattr(H, "_run", lambda argv, timeout=120: (0, ""))
    res = H.connect("claude-code")
    assert res["ok"] and _steps(res)["skill"]["skill"] == "written"
    assert (home / ".claude" / "skills" / "tavotto-figure" / "SKILL.md").is_file()
