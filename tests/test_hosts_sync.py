"""`tavotto hosts sync`：桌面版启动时的同步（ADR 0170 裁决 6，2026-11-06）。

主语：用户机器上**已接入**宿主的技能副本与 Codex 插件（这里全在临时 HOME / 临时配置目录里造）——
未接入的不碰；技能 missing / stale 补上、modified 不动；Codex 插件**只有**带 `--upgrade-plugins`、没被环境变量关掉、
确实落后、不在 24 小时退避期、没人在更新时才联网（用假流水线钉死）；`--force-skill` 才覆盖用户改过的技能。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import time
from pathlib import Path

import pytest

from tavotto.engine import codexinstall, hostconnect as H, hostskills as K

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "codex-plugin" / "skills" / "tavotto-figure"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(h / ".config"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "tavotto-config"))
    monkeypatch.delenv(H.DISABLE_UPDATE_ENV, raising=False)
    monkeypatch.delenv(H.NO_UPDATE_ENV, raising=False)
    monkeypatch.setattr(H, "health", lambda launch, env: H._step("health", ok=True, detail="stub"))
    return h


@pytest.fixture()
def source(tmp_path, monkeypatch):
    """可改的技能来源副本（造 stale 用）。"""
    dst = tmp_path / "src-skill"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(K, "skill_source", lambda: dst)
    return dst


def _row(res, host):
    return next(r for r in res["hosts"] if r["id"] == host)


def _skill(home, host=".cursor"):
    return home / host / "skills" / "tavotto-figure" / "SKILL.md"


# ------------------------------------------------------------------ 技能
def test_hosts_that_are_not_connected_are_never_touched(home, source):
    (home / ".cursor").mkdir()  # 装了编辑器，但没接入
    res = H.sync()
    assert res["ok"] and _row(res, "cursor")["state"] == "not_connected"
    assert not (home / ".cursor" / "skills").exists()
    assert not (home / ".cursor" / "mcp.json").exists()


def test_missing_skill_is_filled_in_for_an_already_connected_host(home, source):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    shutil.rmtree(home / ".cursor" / "skills")  # 老用户：只有 MCP，没有技能
    res = H.sync()
    assert _row(res, "cursor")["skill"] == "written" and _skill(home).is_file()
    again = H.sync()
    assert _row(again, "cursor")["skill"] == "skipped"  # 幂等


def test_stale_skill_is_updated(home, source):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    (source / "SKILL.md").write_text(
        (source / "SKILL.md").read_text(encoding="utf-8") + "\n新版的一行\n", encoding="utf-8"
    )
    assert K.skill_state("cursor") == "stale"
    res = H.sync()
    assert _row(res, "cursor")["skill"] == "updated"
    assert "新版的一行" in _skill(home).read_text(encoding="utf-8")


def test_modified_skill_is_left_alone_and_reported(home, source):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    mine = _skill(home)
    mine.write_text(mine.read_text(encoding="utf-8") + "\n我加的\n", encoding="utf-8")
    res = H.sync()
    assert res["ok"] and _row(res, "cursor")["skill"] == "modified"
    assert "我加的" in mine.read_text(encoding="utf-8")


def test_only_force_skill_overwrites_a_modified_skill(home, source):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    mine = _skill(home)
    mine.write_text(mine.read_text(encoding="utf-8") + "\n我加的\n", encoding="utf-8")
    assert _steps(H.connect("cursor"))["skill"]["skill"] == "modified"
    assert "我加的" in mine.read_text(encoding="utf-8")
    forced = H.overwrite_skill("cursor")
    assert forced["ok"] and _steps(forced)["skill"]["skill"] == "updated"
    assert "我加的" not in mine.read_text(encoding="utf-8")
    assert K.skill_state("cursor") == "current"


def test_force_skill_never_touches_a_symlink(home, source, tmp_path):
    (home / ".cursor").mkdir()
    (home / ".cursor" / "skills").mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    try:
        (home / ".cursor" / "skills" / "tavotto-figure").symlink_to(
            target, target_is_directory=True
        )
    except OSError:
        pytest.skip("no symlink")
    res = K.install_skill("cursor", force=True)
    assert res["state"] == "modified" and not any(target.iterdir())


def test_claude_code_with_the_plugin_gets_no_copy_on_sync(home, source, monkeypatch):
    monkeypatch.setattr(H, "_claude_argv", lambda: ["claude"])
    monkeypatch.setattr(H, "_run", lambda argv, timeout=120: (0, "plugin:tavotto:tavotto: x - ok"))
    res = H.sync()
    assert _row(res, "claude-code")["skill"] == "plugin"
    assert not (home / ".claude" / "skills").exists()


def test_cli_sync_prints_one_json_line_and_force_skill_flag_is_wired(home, source, capsys):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    capsys.readouterr()
    assert H.cli(["sync", "--json"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and json.loads(out[0])["action"] == "sync"
    mine = _skill(home)
    mine.write_text("x", encoding="utf-8")
    H.cli(["connect", "cursor", "--force-skill", "--json"])
    assert K.skill_state("cursor") == "current"


def _steps(res):
    return {s["step"]: s for s in res["steps"]}


# ------------------------------------------------------------------ Codex 插件
class Pipeline:
    def __init__(self, ok=True, raises=False):
        self.calls = []
        self.ok, self.raises = ok, raises

    def __call__(self, *, apply, upgrade=False):
        self.calls.append((apply, upgrade))
        if self.raises:
            raise RuntimeError("boom")
        if self.ok:
            return True, [H._step("plugin", ok=True)], {}
        return False, [H._step("marketplace", ok=False, code="archive_fetch_failed")], {}


@pytest.fixture()
def codex(home, tmp_path, monkeypatch):
    """Codex 已接入、插件 0.1.0、这份 Tavotto 0.2.0。"""
    pipe = Pipeline()
    monkeypatch.setattr(H, "installed", lambda h: h == "codex")
    monkeypatch.setattr(H, "connected", lambda h, launch: h == "codex")
    ver = {"v": "0.1.0"}
    monkeypatch.setattr(H, "_codex_plugin_version", lambda: ver["v"])
    monkeypatch.setattr(K, "_version", lambda: "0.2.0")
    monkeypatch.setattr(codexinstall, "_run_pipeline", pipe)
    monkeypatch.setattr(codexinstall, "archive_marketplace_dir", lambda: tmp_path / "mk" / "a")
    pipe.ver = ver
    return pipe


def test_without_the_flag_the_plugin_is_never_downloaded(codex):
    res = H.sync()
    assert _row(res, "codex")["plugin"] == "not_requested" and codex.calls == []


def test_the_flag_upgrades_a_lagging_plugin_exactly_once_with_upgrade_true(codex):
    res = H.sync(upgrade_plugins=True)
    assert codex.calls == [(True, True)]
    assert _row(res, "codex")["plugin"] == "upgraded" and res["ok"]


def test_the_environment_switch_disables_the_upgrade(codex, monkeypatch):
    for v in H.DISABLE_UPDATE_TRUTHY:
        monkeypatch.setenv(H.DISABLE_UPDATE_ENV, v)
        assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "disabled"
    assert codex.calls == []


def test_no_update_check_env_also_disables_the_upgrade(codex, monkeypatch):
    monkeypatch.setenv(H.NO_UPDATE_ENV, "1")
    assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "disabled"
    assert codex.calls == []


def test_an_up_to_date_plugin_is_not_touched(codex):
    codex.ver["v"] = "0.2.0"
    assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "current"
    codex.ver["v"] = None  # 读不出版本也不判
    assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "current"
    assert codex.calls == []


def test_failure_is_recorded_not_raised_and_backs_off_for_24_hours(codex, monkeypatch):
    codex.ok = False
    res = H.sync(upgrade_plugins=True)
    row = _row(res, "codex")
    assert (
        not res["ok"] and row["plugin"] == "failed" and row["error_code"] == "archive_fetch_failed"
    )
    state = json.loads((H._hosts_state_path()).read_text(encoding="utf-8"))
    assert "codex" in state[H.BACKOFF_KEY]
    assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "backoff"
    assert len(codex.calls) == 1
    later = time.time() + H.PLUGIN_RETRY_SECONDS + 5
    monkeypatch.setattr(H, "_now", lambda: later)
    codex.ok = True
    assert _row(H.sync(upgrade_plugins=True), "codex")["plugin"] == "upgraded"
    assert len(codex.calls) == 2
    assert H.BACKOFF_KEY not in H.load_state()  # 成功后清掉


def test_a_pipeline_exception_is_swallowed(codex):
    codex.raises = True
    row = _row(H.sync(upgrade_plugins=True), "codex")
    assert row["plugin"] == "failed" and row["error_code"] == H.ERR_PLUGIN_UPGRADE


def test_a_running_upgrade_makes_sync_skip_quietly(codex, tmp_path):
    with codexinstall._marketplace_lock(tmp_path / "mk"):
        res = H.sync(upgrade_plugins=True)
    assert _row(res, "codex")["plugin"] == "busy" and res["ok"] and codex.calls == []


def test_backoff_lives_in_the_config_dir_not_the_package(codex, tmp_path):
    codex.ok = False
    H.sync(upgrade_plugins=True)
    assert H._hosts_state_path().is_relative_to(tmp_path / "tavotto-config")


# ------------------------------------------------------------------ list 的版本与 update
def test_list_reports_versions_and_update_without_network(home, source, monkeypatch):
    (home / ".cursor").mkdir()
    H.connect("cursor")
    monkeypatch.setattr(H, "_codex_plugin_version", lambda: "0.1.0")
    monkeypatch.setattr(H, "installed", lambda h: h in ("codex", "cursor"))
    monkeypatch.setattr(H, "connected", lambda h, launch: h in ("codex", "cursor"))
    monkeypatch.setattr(K, "_version", lambda: "0.2.0")
    rows = {r["id"]: r for r in H.list_hosts()["hosts"]}
    assert rows["codex"]["version"] == "0.1.0"
    assert rows["codex"]["update"] == {"latest": "0.2.0", "how": "codex_plugin"}
    assert "version" in rows["cursor"] and "update" not in rows["cursor"]
    assert "version" not in rows["trae"]


def test_disable_env_and_truthy_set_match_the_plugin_update_check():
    spec = importlib.util.spec_from_file_location(
        "update_check",
        ROOT / "codex-plugin" / "skills" / "tavotto-figure" / "scripts" / "update_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DISABLE_ENV == H.DISABLE_UPDATE_ENV
    for v in H.DISABLE_UPDATE_TRUTHY:  # 同一组真值：插件那边也认成「关」
        got = mod.check(environ={mod.DISABLE_ENV: v}, version="0.1.0", now=0)
        assert got["status"] == "disabled", v


def test_overwriting_a_skill_leaves_the_mcp_entry_and_its_project_root_alone(home, source):
    """「覆盖」只动技能：用户 `--project-root` 限定的文件夹不能被冲成本机任意位置（授权不许悄悄放宽）。"""
    project = home / "proj"
    project.mkdir()
    (home / ".cursor").mkdir()
    assert H.connect("cursor", str(project))["ok"]
    config = home / ".cursor" / "mcp.json"
    before = config.read_bytes()
    assert H.ROOTS_ENV in before.decode("utf-8")
    _skill(home).write_text("改过\n", encoding="utf-8")
    assert K.skill_state("cursor") == "modified"
    assert H.overwrite_skill("cursor")["ok"]
    assert K.skill_state("cursor") == "current"
    assert config.read_bytes() == before


def test_overwrite_skill_refuses_a_project_root_and_hosts_without_a_skill_folder(home, source):
    assert not H.overwrite_skill("cursor", "/some/where")["ok"]
    assert not H.overwrite_skill("claude-desktop")["ok"]
    assert not H.overwrite_skill("codex")["ok"]
