"""MSIX 宿主私有区旧数据补回真实目录（`engine/msixmigrate.py`，#1574 善后）。

假的目录树 + monkeypatch（平台判定、包身份、LOCALAPPDATA）在 mac / Linux 上测全部逻辑；
真机行为（Windows 把写入重定向进 `Packages\\<pfn>\\LocalCache`、MAX_PATH、杀软占文件）只能在 MSIX 真机验，见 PR 说明。
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import config, layoutsession, managedcli, managedenv, msixmigrate

PFN_A = "OpenAI.Codex_2p2nqsd0c76g0"
PFN_B = "Anthropic.Claude_pzs8sxrjxfjjc"


class World:
    """一份假的 Windows 用户目录：真实 config / data 目录 + 若干包私有区。"""

    def __init__(self, root: Path, monkeypatch) -> None:
        self.localappdata = root / "LocalAppData"
        self.data = root / "real-data"
        self.config = root / "real-config"
        self.localappdata.mkdir()
        monkeypatch.setenv("LOCALAPPDATA", str(self.localappdata))
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(self.data))
        monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(self.config))
        monkeypatch.setattr(msixmigrate, "_is_windows", lambda: True)
        self.identity = False
        self.ages: dict[tuple[Path, Path], float] = {}
        monkeypatch.setattr(
            msixmigrate.msixbreakaway, "has_package_identity", lambda **_k: self.identity
        )
        msixmigrate._ADOPT_CHECKED.clear()

    def pkg(self, pfn: str, age: float = 0.0) -> tuple[Path, Path]:
        """建一个包的 (Roaming/Tavotto, Local/Tavotto)；`age` 秒越大越旧。"""
        base = self.localappdata / "Packages" / pfn / "LocalCache"
        roaming, local = base / "Roaming" / "Tavotto", base / "Local" / "Tavotto"
        roaming.mkdir(parents=True)
        local.mkdir(parents=True)
        self.ages[(roaming, local)] = age
        self.settle()
        return roaming, local

    def settle(self) -> None:
        """往目录里放文件会刷新 mtime：跑迁移前按各包声明的年龄重设一遍。"""
        now = time.time()
        for dirs, age in self.ages.items():
            for d in dirs:
                os.utime(d, (now - age, now - age))


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    # 同一进程里更早的用例跑过 AI 任务的话，`ai_bridge` 的属主心跳线程还挂着，每个 tick 用**全局** `ai_history.DB_PATH`
    # 开库（库不存在就新建）。本文件的用例会把 DB_PATH 指到自己的临时目录，再断言「此刻还没有库」——心跳恰好落在
    # 中间就凭空建出库来（11-27 合并组 Windows 第 14 片，#2283 那条打断回滚用例）。停掉它：`run()` 会按需重起。
    bridge = sys.modules.get("tavotto.engine.ai_bridge")
    if bridge is not None:
        bridge.stop_owner_loop()
    return World(tmp_path, monkeypatch)


def put(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_cfg() -> dict:
    return json.loads(config.config_path().read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 资格
def test_not_windows_does_nothing(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    monkeypatch.setattr(msixmigrate, "_is_windows", lambda: False)
    assert msixmigrate.run_once() is None
    assert not (world.data / "origin").exists()
    assert not msixmigrate.marker_path().exists()


def test_with_package_identity_never_migrates(world):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    world.identity = True
    assert msixmigrate.run_once() is None
    assert not (world.data / "origin").exists()
    assert not msixmigrate.marker_path().exists()  # 也不写标记：脱包后的下次启动还要迁
    assert msixmigrate.adopt_dir("environments", "abc") is False


def test_no_sources_writes_complete_marker_and_stops(world):
    out = msixmigrate.run_once()
    assert out["sources"] == 0 and out["complete"] is True
    assert msixmigrate.diagnostic_facts()["state"] == "done"
    assert msixmigrate.run_once() is None


# ------------------------------------------------------------------ 文件补缺
def test_copies_missing_never_overwrites_keeps_original(world):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "new.json", "from-pkg")
    put(local / "origin" / "both.json", "from-pkg")
    put(world.data / "origin" / "both.json", "real")
    put(local / "state" / "layout-sessions.json", "{}")
    put(local / "cache" / "ai_snapshots" / "s1" / "a.json", "snap")
    put(local / "cache" / "fix_snapshots" / "s2" / "b.json", "fix")
    put(local / "ai" / "fix_audit.jsonl", "line\n")
    put(local / "source-backed" / "adoptions.json", "[]")
    out = msixmigrate.run_once()
    assert (world.data / "origin" / "new.json").read_text() == "from-pkg"
    assert (world.data / "origin" / "both.json").read_text() == "real"  # 不覆盖
    assert (world.data / "state" / "layout-sessions.json").is_file()
    assert (world.data / "cache" / "ai_snapshots" / "s1" / "a.json").is_file()
    assert (world.data / "cache" / "fix_snapshots" / "s2" / "b.json").is_file()
    assert (world.data / "ai" / "fix_audit.jsonl").is_file()
    assert (world.data / "source-backed" / "adoptions.json").is_file()
    # 原件一个不删
    assert (local / "origin" / "new.json").read_text() == "from-pkg"
    assert (local / "origin" / "both.json").read_text() == "from-pkg"
    assert out["skipped_existing"] == 1 and out["failed"] == 0 and out["copied"] == 6
    assert not list(world.data.rglob("*.tmp"))


def test_regenerable_cache_and_runtime_state_not_copied(world):
    _r, local = world.pkg(PFN_A)
    for rel in (
        "cache/engine/x.bin",
        "cache/mathtext/h.json",
        "cache/runtime/slug/meta.json",
        "cache/probe/p.json",
        "cache/native/n/out.pdf",
        "cache/app.log",
        "cache/diagnostics.log",
        "cache/exec-trace/t.json",
        "probe-results/r.json",
        "perf-reports/p.json",
        "session/port-5089.json",
        "codex-marketplace/m.json",
        "ai_models_cache.json",
        "state/tutorial-cleanup-pending",
        "state/msix-migration-v1.json",
        "state/layout-sessions.lock",
        "tutorial/t.py",
    ):
        put(local / rel)
    put(local / "origin" / "keep.json")
    msixmigrate.run_once()
    got = sorted(p.relative_to(world.data).as_posix() for p in world.data.rglob("*") if p.is_file())
    assert got == [
        "origin/keep.json",
        "state/msix-migration-v1.json",
        "state/msix-migration.lock",
    ]


def test_sqlite_history_copied_consistently_including_wal(world):
    _r, local = world.pkg(PFN_A)
    db = local / "cache" / "ai_history.sqlite3"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table t(x)")
    con.execute("insert into t values (42)")
    con.commit()  # 数据可能还在 -wal 里；连接保持打开
    msixmigrate.run_once()
    con.close()
    got = sqlite3.connect(world.data / "cache" / "ai_history.sqlite3")
    assert got.execute("select x from t").fetchall() == [(42,)]
    got.close()


def test_real_sqlite_history_not_overwritten(world):
    _r, local = world.pkg(PFN_A)
    put(local / "cache" / "ai_history.sqlite3", "pkg")
    put(world.data / "cache" / "ai_history.sqlite3", "real")
    msixmigrate.run_once()
    assert (world.data / "cache" / "ai_history.sqlite3").read_text() == "real"


def test_midcopy_failure_leaves_no_partial_file(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "big.json", "payload")
    put(local / "origin" / "ok.json", "ok")
    real_copy2 = shutil.copy2

    def flaky(src, dst, **kw):
        if str(src).endswith("big.json"):
            Path(dst).write_text("par")  # 写了一半
            raise OSError("disk full")
        return real_copy2(src, dst, **kw)

    monkeypatch.setattr(msixmigrate.shutil, "copy2", flaky)
    out = msixmigrate.run_once()
    assert out["failed"] == 1 and out["copied"] == 1
    assert not (world.data / "origin" / "big.json").exists()
    assert (world.data / "origin" / "ok.json").is_file()
    assert not list((world.data / "origin").glob("*.tmp"))
    assert (local / "origin" / "big.json").read_text() == "payload"  # 原件还在


# ------------------------------------------------------------------ 多个包
def test_multiple_packages_newest_first_later_only_fills_gaps(world):
    _ra, la = world.pkg(PFN_A, age=10)  # 新
    _rb, lb = world.pkg(PFN_B, age=1000)  # 旧
    put(la / "origin" / "x.json", "new-pkg")
    put(lb / "origin" / "x.json", "old-pkg")
    put(lb / "origin" / "y.json", "old-pkg-only")
    world.settle()
    out = msixmigrate.run_once()
    assert (world.data / "origin" / "x.json").read_text() == "new-pkg"
    assert (world.data / "origin" / "y.json").read_text() == "old-pkg-only"
    assert out["sources"] == 2 and out["skipped_existing"] == 1


def test_sources_order_by_mtime_newest_first(world):
    world.pkg(PFN_B, age=1000)
    world.pkg(PFN_A, age=10)
    order = [s[1].parts[-4] for s in msixmigrate.sources()]
    assert order == [PFN_A, PFN_B]


# ------------------------------------------------------------------ 幂等
def test_idempotent_marker_skips_second_scan(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    first = msixmigrate.run_once()
    assert first["complete"] is True
    monkeypatch.setattr(msixmigrate, "sources", lambda: pytest.fail("完成标记之后不该再扫"))
    assert msixmigrate.run_once() is None
    facts = msixmigrate.diagnostic_facts()
    assert facts["state"] == "done" and facts["copied"] == 1


def test_budget_exhausted_is_partial_and_next_run_resumes(world):
    _r, local = world.pkg(PFN_A)
    for i in range(5):
        put(local / "origin" / f"{i}.json")
    out = msixmigrate.run_once(budget_s=-1)
    assert out["complete"] is False and out["copied"] == 0
    assert msixmigrate.diagnostic_facts()["state"] == "partial"
    again = msixmigrate.run_once()
    assert again["complete"] is True and again["copied"] == 5


def test_lock_held_by_another_process_skips_without_blocking(world):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    lock = world.data / "state" / "msix-migration.lock"
    fd = layoutsession.acquire_file_lock(lock, time.monotonic() + 1)
    try:
        t0 = time.monotonic()
        assert msixmigrate.run_once() is None
        assert time.monotonic() - t0 < 2
        assert not (world.data / "origin" / "a.json").exists()
    finally:
        layoutsession.release_file_lock(fd)
    assert msixmigrate.run_once()["copied"] == 1


def test_run_once_never_raises(world, monkeypatch):
    world.pkg(PFN_A)
    monkeypatch.setattr(msixmigrate, "sources", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert msixmigrate.run_once() is None


# ------------------------------------------------------------------ config
def test_config_copied_whole_when_real_absent(world):
    roaming, _l = world.pkg(PFN_A)
    put(
        roaming / "config.json",
        json.dumps(
            {
                "recent_projects": [{"path": "C:/p1", "name": "p1"}],
                "pinned_projects": [{"path": "C:/p2", "name": "p2"}],
                "projects": {"C:/p1": {"export_dir": "D:/out"}},
                "ai": {"providers": [{"id": "relay", "api_key": "sk-1"}]},
                "telemetry": {"consent": "yes"},
            }
        ),
    )
    out = msixmigrate.run_once()
    cfg = read_cfg()
    assert cfg["recent_projects"][0]["path"] == "C:/p1"
    assert cfg["pinned_projects"][0]["path"] == "C:/p2"
    assert cfg["projects"]["C:/p1"] == {"export_dir": "D:/out"}
    assert cfg["ai"]["providers"][0]["api_key"] == "sk-1"
    assert cfg["telemetry"] == {"consent": "yes"}
    assert out["config_merged"] is True


def test_config_conservative_merge_only_fills_gaps(world):
    roaming, _l = world.pkg(PFN_A)
    put(
        roaming / "config.json",
        json.dumps(
            {
                "recent_projects": [
                    {"path": "C:/shared", "name": "pkg-name"},
                    {"path": "C:/only-pkg", "name": "o"},
                ],
                "pinned_projects": [{"path": "C:/pin-pkg", "name": "pp"}],
                "projects": {
                    "C:/shared": {"export_dir": "PKG", "extra": 1},
                    "C:/only-pkg": {"export_dir": "E"},
                },
                "ai": {
                    "providers": [
                        {"id": "relay", "api_key": "PKG-KEY", "base_url": "pkg"},
                        {"id": "second", "api_key": "k2"},
                    ],
                    "agents": {"codex": {"enabled": False}, "claude": {"enabled": True}},
                    "default_agent": "claude",
                },
                "telemetry": {"consent": "PKG"},
                "worker": {"python": "PKG-PY"},
            }
        ),
    )
    put(
        config.config_path(),
        json.dumps(
            {
                "recent_projects": [{"path": "C:/shared", "name": "real-name"}],
                "pinned_projects": [{"path": "C:/pin-real", "name": "pr"}],
                "projects": {"C:/shared": {"export_dir": "REAL"}},
                "ai": {
                    "providers": [{"id": "relay", "api_key": "REAL-KEY"}],
                    "agents": {"codex": {"enabled": True}},
                    "default_agent": "codex",
                },
                "telemetry": {"consent": "REAL"},
            }
        ),
    )
    out = msixmigrate.run_once()
    cfg = read_cfg()
    assert [e["path"] for e in cfg["recent_projects"]] == ["C:/shared", "C:/only-pkg"]
    assert cfg["recent_projects"][0]["name"] == "real-name"  # 已有的不改，真实在前
    assert [e["path"] for e in cfg["pinned_projects"]] == ["C:/pin-real", "C:/pin-pkg"]
    assert cfg["projects"]["C:/shared"] == {"export_dir": "REAL"}  # 整条已有条目不碰
    assert cfg["projects"]["C:/only-pkg"] == {"export_dir": "E"}
    ids = {p["id"]: p for p in cfg["ai"]["providers"]}
    assert ids["relay"]["api_key"] == "REAL-KEY" and "base_url" not in ids["relay"]
    assert ids["second"]["api_key"] == "k2"
    assert cfg["ai"]["agents"]["codex"] == {"enabled": True}
    assert cfg["ai"]["agents"]["claude"] == {"enabled": True}
    assert cfg["ai"]["default_agent"] == "codex"
    assert cfg["telemetry"] == {"consent": "REAL"}  # 同意态等其它段不碰
    assert "python" not in cfg.get("worker", {})
    assert out["config_merged"] is True


def test_config_merge_noop_when_nothing_to_add(world):
    roaming, _l = world.pkg(PFN_A)
    same = {"recent_projects": [{"path": "C:/a", "name": "a"}]}
    put(roaming / "config.json", json.dumps(same))
    put(config.config_path(), json.dumps(same))
    out = msixmigrate.run_once()
    assert out["config_merged"] is False


def test_unreadable_real_config_is_never_touched(world):
    roaming, _l = world.pkg(PFN_A)
    put(roaming / "config.json", json.dumps({"recent_projects": [{"path": "C:/a", "name": "a"}]}))
    put(config.config_path(), "{not json")
    out = msixmigrate.run_once()
    assert config.config_path().read_text() == "{not json"
    assert out["failed"] == 1 and out["config_merged"] is False


def test_merge_uses_config_transaction(world, monkeypatch):
    roaming, _l = world.pkg(PFN_A)
    put(roaming / "config.json", json.dumps({"recent_projects": [{"path": "C:/a", "name": "a"}]}))
    calls = []
    real_tx = config.transaction

    def spy():
        calls.append(1)
        return real_tx()

    monkeypatch.setattr(config, "transaction", spy)
    msixmigrate.run_once()
    assert calls


def test_two_package_configs_merge_newest_first(world):
    ra, _la = world.pkg(PFN_A, age=10)
    rb, _lb = world.pkg(PFN_B, age=1000)
    put(ra / "config.json", json.dumps({"projects": {"C:/p": {"export_dir": "NEW"}}}))
    put(
        rb / "config.json",
        json.dumps({"projects": {"C:/p": {"export_dir": "OLD"}, "C:/q": {"export_dir": "Q"}}}),
    )
    world.settle()
    msixmigrate.run_once()
    cfg = read_cfg()
    assert cfg["projects"]["C:/p"] == {"export_dir": "NEW"}
    assert cfg["projects"]["C:/q"] == {"export_dir": "Q"}


# ------------------------------------------------------------------ 按需搬：受管环境
def _make_managed_env(local: Path, project: Path, *, base_home: Path, python: bool = True):
    fp = managedenv.project_fingerprint(project)
    env = local / "environments" / fp
    put(
        env / "environment.json",
        json.dumps(
            {
                "schema": managedenv.SCHEMA,
                "created_by_tavotto": True,
                "state": "ready",
                "installed_by_tavotto": [],
            }
        ),
    )
    if python:
        # 与 `managedenv.venv_python` 同一布局：Windows 是 Scripts\python.exe，其它平台 bin/python
        exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
        put(env / "venv" / exe[0] / exe[1], "#!py")
    put(env / "venv" / "pyvenv.cfg", f"home = {base_home}\nversion = 3.12.1\n")
    return env


def test_managed_env_adopted_on_demand_and_usable(world, tmp_path):
    _r, local = world.pkg(PFN_A)
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "base-python"
    base.mkdir()
    _make_managed_env(local, project, base_home=base)
    assert not (world.data / "environments").exists()  # 启动同步阶段不搬
    msixmigrate.run_once()
    assert not (world.data / "environments").exists()
    assert managedenv.python_of(project) is not None  # 用到时才搬，且完整性通过
    assert (world.data / "environments" / managedenv.project_fingerprint(project)).is_dir()
    assert (local / "environments").is_dir()  # 原件保留
    assert msixmigrate.diagnostic_facts()["adopted_dirs"] == 1


def test_managed_env_missing_python_falls_back_to_none(world, tmp_path):
    _r, local = world.pkg(PFN_A)
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "base-python"
    base.mkdir()
    _make_managed_env(local, project, base_home=base, python=False)
    assert managedenv.python_of(project) is None
    fp = managedenv.project_fingerprint(project)
    assert not (world.data / "environments" / fp).exists()  # 校验不过，不留半截
    assert not list((world.data / "environments").iterdir())
    assert msixmigrate.diagnostic_facts()["adopt_failed"] == 1


def test_managed_env_with_vanished_base_python_not_adopted(world, tmp_path):
    _r, local = world.pkg(PFN_A)
    project = tmp_path / "proj"
    project.mkdir()
    _make_managed_env(local, project, base_home=tmp_path / "uninstalled-python")
    assert managedenv.python_of(project) is None


def test_managed_env_base_private_python_adopted_with_it(world, tmp_path):
    _r, local = world.pkg(PFN_A)
    project = tmp_path / "proj"
    project.mkdir()
    rid = "3.12.7-abcdef123456"
    home = world.data / "private-python" / "runtimes" / rid / "bin"
    put(local / "private-python" / "runtimes" / rid / "bin" / "python3", "py")
    _make_managed_env(local, project, base_home=home)
    assert managedenv.python_of(project) is not None
    assert home.is_dir()


def test_existing_real_env_is_never_replaced(world, tmp_path):
    _r, local = world.pkg(PFN_A)
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "base-python"
    base.mkdir()
    _make_managed_env(local, project, base_home=base)
    real = world.data / "environments" / managedenv.project_fingerprint(project)
    put(real / "marker.txt", "real")
    managedenv.env_dir(project)
    assert [p.name for p in real.iterdir()] == ["marker.txt"]


# ------------------------------------------------------------------ 按需搬：受管 CLI
def _make_cli(local: Path, name: str, *, with_entry: bool = True) -> str:
    cli = managedcli.cli_for(name, platform_key=managedcli.host_platform() or "linux-x64")
    assert cli is not None
    ident = cli.id
    root = local / "cli" / name
    put(root / managedcli.POINTER_NAME, json.dumps({"id": ident, "version": cli.version}))
    if with_entry:
        put(root / ident / Path(*cli.entrypoint.split("/")), "bin")
    return ident


def _cli_name() -> str | None:
    try:
        lock = managedcli.load_lock()
    except Exception:  # noqa: BLE001
        return None
    plat = managedcli.host_platform()
    for name, c in (lock.get("clis") or {}).items():
        if plat and plat in (c.get("platforms") or {}):
            return name
    return None


@pytest.mark.skipif(_cli_name() is None, reason="本平台锁里没有托管 CLI")
def test_managed_cli_adopted_on_demand(world):
    name = _cli_name()
    _r, local = world.pkg(PFN_A)
    _make_cli(local, name)
    msixmigrate.run_once()
    assert not (world.data / "cli").exists()
    assert managedcli.entrypoint(name) is not None
    assert (local / "cli" / name).is_dir()


@pytest.mark.skipif(_cli_name() is None, reason="本平台锁里没有托管 CLI")
def test_managed_cli_without_entry_not_adopted(world):
    name = _cli_name()
    _r, local = world.pkg(PFN_A)
    _make_cli(local, name, with_entry=False)
    assert managedcli.entrypoint(name) is None
    assert not (world.data / "cli" / name).exists()


# ------------------------------------------------------------------ 诊断事实
def test_diagnostic_facts_are_closed_numbers_without_paths_or_package_names(world):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    assert msixmigrate.diagnostic_facts() == {"state": "none"}
    msixmigrate.run_once()
    facts = msixmigrate.diagnostic_facts()
    assert set(facts) == {
        "state",
        "sources",
        "copied",
        "skipped_existing",
        "failed",
        "attempts",
        "config_merged",
        "adopted_dirs",
        "adopt_failed",
    }
    blob = json.dumps(facts)
    assert PFN_A not in blob and str(world.data) not in blob
    assert all(isinstance(v, (int, bool, str)) for v in facts.values())


def test_report_json_carries_migration_facts(world, monkeypatch):
    from tavotto.engine import diagnostics

    facts = {"state": "done", "copied": 3}
    monkeypatch.setattr(msixmigrate, "diagnostic_facts", lambda: facts)
    report = diagnostics.build_report()  # 真实的报告构建函数，不是看源码里有没有这串字
    assert report["system"]["msix_data_migration"] == facts


# ------------------------------------------------------------------ 入口挂载（AST，不用子串）
def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _calls(src: str, func: str) -> list[tuple[int, int, str]]:
    """函数 `func` 体内所有真实 Call 节点：(行, 列, 点分名)，按源码顺序。注释 / 字符串里的不算。"""
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    out = [
        (n.lineno, n.col_offset, _dotted(n.func))
        for stmt in fn.body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
    ]
    return sorted(out)


def _first(calls: list[tuple[int, int, str]], pred) -> tuple[int, int] | None:
    return next(((ln, col) for ln, col, name in calls if pred(name)), None)


def _is_run_once(name: str) -> bool:
    return name in ("engine_msixmigrate.run_once", "msixmigrate.run_once")


def app_main_problems(src: str) -> list[str]:
    """`app.main()` 里 run_once 的挂载问题（空 = 合格）：必须真有调用、在 setup_logging() 之后、
    在读上次项目 / 标记中断会话（建 ai_history 库）之前。"""
    calls = _calls(src, "main")
    run = _first(calls, _is_run_once)
    if run is None:
        return ["main() 里没有 run_once() 调用"]
    problems = []
    log = _first(calls, lambda n: n.rsplit(".", 1)[-1] == "setup_logging")
    if log is None or not log < run:
        problems.append("run_once() 必须在 setup_logging() 之后")
    for late in ("last_project", "mark_interrupted_running"):
        pos = _first(calls, lambda n, late=late: n.rsplit(".", 1)[-1] == late)
        if pos is None:
            problems.append(f"main() 里找不到 {late}()（判据失效）")
        elif not run < pos:
            problems.append(f"run_once() 必须在 {late}() 之前")
    return problems


def handoff_cli_problems(src: str) -> list[str]:
    calls = _calls(src, "cli")
    run = _first(calls, _is_run_once)
    relaunch = _first(calls, lambda n: n.rsplit(".", 1)[-1] == "relaunch_cli")
    if run is None:
        return ["cli() 里没有 run_once() 调用"]
    if relaunch is None or not relaunch < run:
        return ["run_once() 必须在 relaunch_cli() 之后"]
    return []


def _src(rel: str) -> str:
    root = Path(__file__).resolve().parents[1] / "src" / "tavotto"
    return (root / rel).read_text(encoding="utf-8")


def test_entry_points_call_run_once_after_breakaway():
    assert app_main_problems(_src("app.py")) == []
    assert handoff_cli_problems(_src("engine/handoff.py")) == []


def test_entry_check_fails_when_call_commented_out_or_misplaced():
    app = _src("app.py")
    line = next(ln for ln in app.splitlines() if ln.strip() == "engine_msixmigrate.run_once()")
    # 1. 注释掉调用：子串检查会放过，AST 不会
    commented = app.replace(line, line.replace("engine_msixmigrate", "# engine_msixmigrate", 1), 1)
    assert app_main_problems(commented)
    # 2. 挪到 setup_logging() 之前
    log = next(ln for ln in app.splitlines() if ln.strip() == "setup_logging()")
    early = app.replace(line + "\n", "", 1).replace(log, line + "\n" + log, 1)
    assert any("setup_logging" in p for p in app_main_problems(early))
    # 3. 挪到读上次项目之后
    anchor = next(ln for ln in app.splitlines() if "engine_config.last_project()" in ln)
    late = app.replace(line + "\n", "", 1).replace(anchor, anchor + "\n" + line, 1)
    assert any("last_project" in p for p in app_main_problems(late))
    # 4. 只在字符串里提到
    stringy = app.replace(line, '    _ = "engine_msixmigrate.run_once()"', 1)
    assert app_main_problems(stringy)

    ho = _src("engine/handoff.py")
    hline = next(ln for ln in ho.splitlines() if ln.strip() == "msixmigrate.run_once()")
    assert handoff_cli_problems(
        ho.replace(hline, hline.replace("msixmigrate", "# msixmigrate", 1), 1)
    )
    rl = next(ln for ln in ho.splitlines() if "msixbreakaway.relaunch_cli(" in ln)
    before = ho.replace(hline + "\n", "", 1).replace(rl, hline + "\n" + rl, 1)
    assert handoff_cli_problems(before)


# ------------------------------------------------------------------ 预算耗尽 / 历史库 / 超时 / 重试
def _make_history(local: Path, value: int = 42) -> Path:
    db = local / "cache" / "ai_history.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table t(x)")
    con.execute("insert into t values (?)", (value,))
    con.commit()
    con.close()
    return db


def test_history_migrated_first_even_when_sync_budget_exhausted(world, monkeypatch):
    # 历史库走独立的 CRITICAL_BUDGET_S（产品值 4 s 墙钟）。本用例要证明的是「主预算耗尽时历史库仍排在最前迁过去」，
    # 不是「4 s 内迁完」：慢 Windows runner 上一次 sqlite backup 可以超过 4 s，于是被记成 deferred、目标库没建出来，
    # 读的时候 connect 新建了空库 → no such table（11-26 #2266 合并组，backend-platforms (windows, 14)，用例耗时 4.9 s）。
    monkeypatch.setattr(msixmigrate, "CRITICAL_BUDGET_S", 120.0)
    _r, local = world.pkg(PFN_A)
    _make_history(local)
    put(local / "origin" / "a.json")
    out = msixmigrate.run_once(budget_s=-1)  # 主预算立刻耗尽
    assert out["complete"] is False
    got = sqlite3.connect(world.data / "cache" / "ai_history.sqlite3")
    assert got.execute("select x from t").fetchall() == [(42,)]  # 历史库排在最前、不吃主预算
    got.close()


def test_history_visible_after_startup_creates_db_and_second_migration(world, monkeypatch):
    """复现 P1：库没迁成时启动早期的 mark_interrupted_running 新建了空库，之后旧历史也要能回来。"""
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _make_history(local, 7)
    db = world.data / "cache" / "ai_history.sqlite3"
    monkeypatch.setattr(ai_history, "DB_PATH", db)
    # 第一次迁移里历史库没迁成（模拟被占 / 超时）
    real = msixmigrate._copy_sqlite
    monkeypatch.setattr(
        msixmigrate,
        "_copy_sqlite",
        lambda s, d, t, dl: (setattr(t, "deferred", t.deferred + 1), False)[1],
    )
    first = msixmigrate.run_once(budget_s=-1)
    assert first["complete"] is False and not db.exists()
    # 启动继续：消费者初始化。此时迁移恢复可用，消费者建库前会先补迁
    monkeypatch.setattr(msixmigrate, "_copy_sqlite", real)
    msixmigrate._SQLITE_CHECKED.clear()
    ai_history.mark_interrupted_running()
    con = sqlite3.connect(db)
    assert con.execute("select x from t").fetchall() == [(7,)]
    con.close()
    # 第二次启动的同步迁移也不会破坏它
    msixmigrate.run_once()
    con = sqlite3.connect(db)
    assert con.execute("select x from t").fetchall() == [(7,)]
    con.close()


def test_sqlite_backup_does_not_block_on_exclusive_lock(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    db = _make_history(local)
    monkeypatch.setattr(msixmigrate, "CRITICAL_BUDGET_S", 0.5)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.monotonic()
        out = msixmigrate.run_once()
        assert time.monotonic() - t0 < 5
    finally:
        holder.close()
    assert out["complete"] is False and out["failed"] == 0
    assert not (world.data / "cache" / "ai_history.sqlite3").exists()
    assert not list((world.data / "cache").glob("*.tmp"))  # 半截临时文件已清
    again = msixmigrate.run_once()  # 锁放开后下次接着补
    assert again["complete"] is True
    got = sqlite3.connect(world.data / "cache" / "ai_history.sqlite3")
    assert got.execute("select x from t").fetchall() == [(42,)]
    got.close()


def test_a_permanently_locked_history_stops_retrying_after_max_attempts(world, monkeypatch):
    """Codex #1603 复核：源库一直被独占事务占着时，备份每次都推迟——推迟也要计入次数，到上限就停止自动重试，
    否则每次启动都要等一遍备份超时。"""
    _r, local = world.pkg(PFN_A)
    db = _make_history(local)
    monkeypatch.setattr(msixmigrate, "CRITICAL_BUDGET_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        outs = [msixmigrate.run_once() for _ in range(msixmigrate.MAX_ATTEMPTS)]
        assert [o["attempts"] for o in outs] == list(range(1, msixmigrate.MAX_ATTEMPTS + 1))
        assert [o["complete"] for o in outs] == [False] * (msixmigrate.MAX_ATTEMPTS - 1) + [True]
        assert msixmigrate.run_once() is None  # 已标完成，之后的启动不再等
    finally:
        holder.close()


def test_running_out_of_the_sync_budget_is_not_counted_as_an_attempt(world):
    """预算到点、目录没走完不算失败（每次都有进展），不吃重试次数。"""
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json", "A")
    first = msixmigrate.run_once(budget_s=-1)
    assert first["complete"] is False and first["attempts"] == 0
    second = msixmigrate.run_once()
    assert second["complete"] is True and second["attempts"] == 0


def test_failure_is_retried_next_start_then_succeeds(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json", "A")
    real_copy2 = shutil.copy2
    monkeypatch.setattr(
        msixmigrate.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("busy"))
    )
    first = msixmigrate.run_once()
    assert first["failed"] == 1 and first["complete"] is False and first["attempts"] == 1
    assert msixmigrate.diagnostic_facts()["state"] == "partial"
    monkeypatch.setattr(msixmigrate.shutil, "copy2", real_copy2)
    second = msixmigrate.run_once()
    assert second["complete"] is True and second["failed"] == 0
    assert (world.data / "origin" / "a.json").read_text() == "A"


def test_permanent_failure_stops_retrying_after_max_attempts(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    put(local / "origin" / "a.json")
    monkeypatch.setattr(
        msixmigrate.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("never"))
    )
    outs = [msixmigrate.run_once() for _ in range(msixmigrate.MAX_ATTEMPTS)]
    assert [o["complete"] for o in outs] == [False] * (msixmigrate.MAX_ATTEMPTS - 1) + [True]
    facts = msixmigrate.diagnostic_facts()
    assert facts["state"] == "done" and facts["failed"] == 1  # 失败计数留在诊断里
    assert msixmigrate.run_once() is None


def test_module_is_stdlib_only_for_flask_parent():
    code = (
        "import sys, tavotto.engine.msixmigrate;"
        "assert not any(m.split('.')[0] in ('flask','matplotlib','numpy','pymupdf') for m in sys.modules)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0, r.stderr


def test_concurrent_consumers_wait_for_the_first_adoption_instead_of_creating_an_empty_db(
    world, monkeypatch
):
    """两个线程同时首次开库（如属主心跳线程与启动线程）：先到的在搬，后到的不能因为「已查过」就返回、新建空库
    ——那样先到者的改名会因目标已存在而放弃，旧历史永远回不来。后到的要等先到的搬完。"""
    import threading

    _r, local = world.pkg(PFN_A)
    _make_history(local, 7)
    db = world.data / "cache" / "ai_history.sqlite3"
    real = msixmigrate._copy_sqlite
    started = threading.Event()

    def slow(src, dst, tally, deadline):
        started.set()
        time.sleep(0.4)
        return real(src, dst, tally, deadline)

    monkeypatch.setattr(msixmigrate, "_copy_sqlite", slow)
    first = threading.Thread(target=msixmigrate.adopt_sqlite, args=("cache", "ai_history.sqlite3"))
    first.start()
    assert started.wait(5)
    msixmigrate.adopt_sqlite("cache", "ai_history.sqlite3")  # 后到：必须等到先到的搬完才返回
    assert db.exists()
    first.join(5)
    con = sqlite3.connect(db)
    assert con.execute("select x from t").fetchall() == [(7,)]
    con.close()


# ------------------------------------------------------------------ 待补旧历史（#2282）
# 慢机器上同步迁移与消费者补迁都超时 → 消费者新建空库 → 下次迁移见目标已存在就跳过并写 complete → 旧历史永远回不来。
# 修法：补迁没搬成时记「待补」，完成标记 / 重试上限都拦不住它，后台把旧库按主键合并回来（只补缺、新版本号、收尾未结束行）。
def _legacy_history(local: Path, ids: list[str], *, running: str | None = None) -> Path:
    """在旧安装位置建一个**真 schema** 的历史库：每个 id 一条已结束的会话 + 一条请求账本；`running` 那条留成运行中。"""
    from tavotto.engine import ai_history

    db = local / "cache" / "ai_history.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    for i, sid in enumerate(ids):
        ai_history.record_start(
            {
                "id": sid,
                "project": "p",
                "provider": "codex",
                "prompt": f"旧的 {sid}",
                "client_request_id": f"r-{sid}",
                "owner_pid": 999999,
            },
            db_path=db,
        )
        if sid != running:
            con = sqlite3.connect(db)
            con.execute(
                "UPDATE sessions SET status='done', ended_ms=started_ms+1, started_ms=? WHERE id=?",
                (1000 + i, sid),
            )
            con.commit()
            con.close()
    return db


def _deferring(s, d, t, dl):
    t.deferred += 1
    return False


def _strand_history_behind_an_empty_db(world, monkeypatch) -> Path:
    """复现 #2282：两个阶段都超时，消费者新建了空库。回真实库路径。"""
    from tavotto.engine import ai_history

    db = world.data / "cache" / "ai_history.sqlite3"
    monkeypatch.setattr(ai_history, "DB_PATH", db)
    monkeypatch.setattr(
        msixmigrate, "_kick_pending_merge", lambda: None
    )  # 后台合并由各用例自己显式触发
    real = msixmigrate._copy_sqlite
    monkeypatch.setattr(msixmigrate, "_copy_sqlite", _deferring)
    first = msixmigrate.run_once(budget_s=-1)
    assert first["complete"] is False and not db.exists()
    msixmigrate._SQLITE_CHECKED.clear()
    ai_history.mark_interrupted_running()  # 补迁也超时 → 新建空库
    assert db.exists()
    monkeypatch.setattr(msixmigrate, "_copy_sqlite", real)
    msixmigrate._SQLITE_CHECKED.clear()
    return db


def _session_ids(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in con.execute("SELECT id FROM sessions"))
    finally:
        con.close()


def test_history_stranded_behind_an_empty_db_is_recorded_as_pending(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["a1", "a2"])
    _strand_history_behind_an_empty_db(world, monkeypatch)
    assert msixmigrate._read_pending() == {
        "schema": 1,
        "items": {"cache/ai_history.sqlite3": {"failures": 0}},
    }
    # 下一次同步迁移见目标已存在照旧跳过、写 complete——但「待补」不受它影响
    assert msixmigrate.run_once()["complete"] is True
    assert msixmigrate._read_pending() is not None


def test_pending_history_is_merged_back_with_fresh_revs_and_the_new_sessions_kept(
    world, monkeypatch
):
    """d2 第 2 点：补迁期间用户已经在新库里写了会话——合并后新旧两边都在、不重复。"""
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1", "old2", "old3"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    ai_history.record_start({"id": "new1", "project": "p", "provider": "codex", "prompt": "新的"})
    con = sqlite3.connect(db)
    rev_before = con.execute("SELECT rev FROM ai_rev_counter").fetchone()[0]
    con.close()
    assert msixmigrate.run_once()["complete"] is True  # 完成标记已写
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["new1", "old1", "old2", "old3"]
    con = sqlite3.connect(db)
    revs = dict(con.execute("SELECT id, rev FROM sessions"))
    reqs = {r[0] for r in con.execute("SELECT client_request_id FROM ai_requests")}
    con.close()
    # 补回来的会话取新的版本号：都比合并前的计数器大、两两不同——`changes?since=` 的客户端才看得到它们
    assert all(revs[s] > rev_before for s in ("old1", "old2", "old3"))
    assert len({revs[s] for s in revs}) == len(revs)
    assert {"r-old1", "r-old2", "r-old3"} <= reqs  # 幂等账本一并补回，旧请求不会被再执行一次
    assert msixmigrate._read_pending() is None
    # 再合并一次：没有待补，不重复
    assert msixmigrate.merge_pending() is None
    assert _session_ids(db) == ["new1", "old1", "old2", "old3"]


def test_pending_survives_the_attempts_cap_and_merges_once_the_machine_keeps_up(world, monkeypatch):
    """审阅意见 1：合并本身在慢机器上一直到点——超过 MAX_ATTEMPTS 次启动也不放弃，哪次跟上了哪次补回。"""
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    for _ in range(msixmigrate.MAX_ATTEMPTS + 2):
        msixmigrate.run_once()
        assert msixmigrate.merge_pending(budget_s=-1) == {"cache/ai_history.sqlite3": "deferred"}
    assert (
        msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 0
    )  # 到点不计失败
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["old1"]


def test_a_merge_interrupted_midway_rolls_back_and_the_next_run_completes(world, monkeypatch):
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1", "old2"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    real = ai_history._stamp
    calls = []

    def die_after_first(con, sid):
        calls.append(sid)
        real(con, sid)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")  # 合并到一半出错

    monkeypatch.setattr(ai_history, "_stamp", die_after_first)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "failed"}
    assert _session_ids(db) == []  # 整体回滚：不留半合并
    monkeypatch.setattr(ai_history, "_stamp", real)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["old1", "old2"]


def test_a_legacy_session_left_running_is_closed_when_merged(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1", "stuck"], running="stuck")
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    con = sqlite3.connect(db)
    status, ended = con.execute("SELECT status, ended_ms FROM sessions WHERE id='stuck'").fetchone()
    con.close()
    assert status == "interrupted" and ended is not None  # 属主是旧安装的进程，早就不在了


def test_a_broken_legacy_db_is_given_up_after_the_failure_cap(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    _strand_history_behind_an_empty_db(world, monkeypatch)
    from tavotto.engine import ai_history

    monkeypatch.setattr(
        ai_history,
        "merge_legacy",
        lambda *a: (_ for _ in ()).throw(sqlite3.DatabaseError("malformed")),
    )
    for _ in range(msixmigrate.PENDING_MAX_FAILURES - 1):
        assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "failed"}
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "gave_up"}
    assert msixmigrate._read_pending() is None


def test_a_healthy_copy_never_brings_back_sessions_the_user_deleted(world, monkeypatch):
    """反面：正常迁过来的用户删掉一条会话、之后再启动——没有「待补」，绝不从旧位置把它救回来。"""
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1", "old2"])
    db = world.data / "cache" / "ai_history.sqlite3"
    monkeypatch.setattr(ai_history, "DB_PATH", db)
    assert msixmigrate.run_once()["complete"] is True
    assert _session_ids(db) == ["old1", "old2"]
    assert ai_history.delete("old1")
    assert msixmigrate._read_pending() is None
    msixmigrate.run_once()
    msixmigrate.merge_pending()
    assert _session_ids(db) == ["old2"]


def test_run_once_starts_the_background_merge_even_after_complete(world, monkeypatch):
    """端到端：真走后台线程。完成标记早已写下，下一次启动的 run_once 仍把待补的旧历史合并回来。"""
    real_kick = msixmigrate._kick_pending_merge
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    assert msixmigrate.run_once()["complete"] is True
    monkeypatch.setattr(msixmigrate, "_kick_pending_merge", real_kick)
    assert msixmigrate.run_once() is None  # 完成标记：同步部分直接返回
    msixmigrate._PENDING_THREAD.join(30)
    assert _session_ids(db) == ["old1"]
    assert msixmigrate._read_pending() is None


def test_a_destination_held_locked_by_another_process_is_deferred_not_counted_as_failure(
    world, monkeypatch
):
    """Codex #2283：另一个 Tavotto 进程占着新库写锁超过 busy_timeout——暂时的，不计失败、不会被当坏库放弃。"""
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    real = ai_history.merge_legacy
    monkeypatch.setattr(
        ai_history,
        "merge_legacy",
        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    for _ in range(msixmigrate.PENDING_MAX_FAILURES + 2):
        assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "deferred"}
    assert msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 0
    monkeypatch.setattr(ai_history, "merge_legacy", real)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["old1"]


def test_lock_classification_works_without_the_311_sqlite_constants(monkeypatch):
    """Codex #2283：3.10 的 sqlite3 没有 SQLITE_BUSY / SQLITE_LOCKED 常量、异常也没有 sqlite_errorcode——
    判据不能依赖它们，否则 AttributeError 把真失败甩出处理器。"""
    monkeypatch.delattr(sqlite3, "SQLITE_BUSY", raising=False)
    monkeypatch.delattr(sqlite3, "SQLITE_LOCKED", raising=False)
    assert msixmigrate._is_lock_contention(sqlite3.OperationalError("database is locked")) is True
    assert msixmigrate._is_lock_contention(sqlite3.OperationalError("disk I/O error")) is False
    busy_snapshot = sqlite3.OperationalError("x")
    busy_snapshot.sqlite_errorcode = 517  # SQLITE_BUSY_SNAPSHOT：扩展码，低 8 位是 BUSY
    assert msixmigrate._is_lock_contention(busy_snapshot) is True


def test_a_permanent_operational_error_is_still_counted_as_a_failure(world, monkeypatch):
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    _strand_history_behind_an_empty_db(world, monkeypatch)
    monkeypatch.delattr(sqlite3, "SQLITE_BUSY", raising=False)  # 3.10 的样子
    monkeypatch.setattr(
        ai_history,
        "merge_legacy",
        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")),
    )
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "failed"}
    assert msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 1


def test_no_registered_merger_yet_is_deferred_not_failed(world, monkeypatch):
    """后台线程比 ai_history 的 import 先跑、截止前也没等到登记：当 deferred，不计失败。"""
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    _strand_history_behind_an_empty_db(world, monkeypatch)
    monkeypatch.setattr(msixmigrate, "_SQLITE_MERGERS", {})
    assert msixmigrate.merge_pending(budget_s=0.05) == {"cache/ai_history.sqlite3": "deferred"}
    assert msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 0


def test_a_process_without_ai_history_does_not_start_the_merge_or_hold_the_lock(world, monkeypatch):
    """d2 #2283 复核：CLI 也调 run_once 但不 import ai_history。不能起线程拿着迁移锁干等登记、挡住随后桌面版的同步迁移。"""
    real_kick = msixmigrate._kick_pending_merge
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    _strand_history_behind_an_empty_db(world, monkeypatch)
    monkeypatch.setattr(msixmigrate, "_SQLITE_MERGERS", {})
    monkeypatch.setattr(msixmigrate, "_PENDING_THREAD", None)
    monkeypatch.setattr(msixmigrate, "_kick_pending_merge", real_kick)
    msixmigrate.run_once()
    assert msixmigrate._PENDING_THREAD is None  # 没起线程
    fd = msixmigrate.layoutsession.acquire_file_lock(
        msixmigrate.config.data_path(*msixmigrate.LOCK_REL), time.monotonic() + 0.2
    )
    assert fd is not None  # 迁移锁没被占
    msixmigrate.layoutsession.release_file_lock(fd)
    # 直接调用也不在等登记期间持锁：等不到就 deferred
    assert msixmigrate.merge_pending(budget_s=0.05) == {"cache/ai_history.sqlite3": "deferred"}


# ------------------------------------------------------------------ 诊断包：旧库比新库多几条（#2282 补救 A）
def _digest_dir(d: Path) -> dict[str, str]:
    import hashlib

    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.iterdir())
        if p.is_file()
    }


def test_legacy_gap_counts_what_the_real_history_is_missing(world, monkeypatch):
    from tavotto.engine import ai_history

    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["a1", "a2", "a3"])
    real = _legacy_history(world.data, ["a1", "n1"])  # 新库：留着 a1，另有一条新会话
    monkeypatch.setattr(ai_history, "DB_PATH", real)
    facts = msixmigrate.legacy_gap_facts()
    assert facts == {
        "state": "ok",
        "dbs": {
            "cache/ai_history.sqlite3": [
                {
                    "state": "ok",
                    "legacy_sessions": 3,
                    "missing_sessions": 2,
                    "legacy_requests": 3,
                    "missing_requests": 2,
                }
            ]
        },
    }
    blob = json.dumps(facts)
    assert PFN_A not in blob and str(world.data) not in blob and str(world.localappdata) not in blob


def test_legacy_gap_reads_both_dbs_without_changing_them(world):
    _r, local = world.pkg(PFN_A)
    legacy = _legacy_history(local, ["a1", "a2"])
    real = _legacy_history(world.data, ["a1"])
    before = (_digest_dir(legacy.parent), _digest_dir(real.parent))
    msixmigrate.legacy_gap_facts()
    assert (_digest_dir(legacy.parent), _digest_dir(real.parent)) == before


def test_legacy_gap_without_a_real_db_counts_everything_and_creates_nothing(world):
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["a1", "a2"])
    [row] = msixmigrate.legacy_gap_facts()["dbs"]["cache/ai_history.sqlite3"]
    assert row["missing_sessions"] == 2 and row["missing_requests"] == 2
    assert not (world.data / "cache" / "ai_history.sqlite3").exists()


def test_legacy_gap_lists_each_old_package_newest_first(world):
    _ra, local_a = world.pkg(PFN_A, age=0)
    _rb, local_b = world.pkg(PFN_B, age=100)
    _legacy_history(local_a, ["a1"])
    _legacy_history(local_b, ["b1", "b2"])
    world.settle()
    _legacy_history(world.data, ["a1"])
    rows = msixmigrate.legacy_gap_facts()["dbs"]["cache/ai_history.sqlite3"]
    assert [r["missing_sessions"] for r in rows] == [0, 2]


def test_packages_without_a_history_db_do_not_crowd_out_the_one_that_has_it(world):
    for i in range(msixmigrate.LEGACY_GAP_MAX_SOURCES):
        _r, local = world.pkg(f"Pkg.Settings{i}_x", age=i)
        put(local / "origin" / "a.json")  # 只有设置，没有历史库
    _r, old = world.pkg(PFN_B, age=1000)
    _legacy_history(old, ["b1", "b2"])
    world.settle()
    rows = msixmigrate.legacy_gap_facts()["dbs"]["cache/ai_history.sqlite3"]
    assert [r["missing_sessions"] for r in rows] == [2]


def test_legacy_gap_out_of_time_or_broken_is_a_closed_state(world):
    _ra, local_a = world.pkg(PFN_A, age=0)
    _rb, local_b = world.pkg(PFN_B, age=100)
    _legacy_history(local_a, ["a1"])
    put(local_b / "cache" / "ai_history.sqlite3", "not a database")
    world.settle()
    assert [
        r["state"] for r in msixmigrate.legacy_gap_facts()["dbs"]["cache/ai_history.sqlite3"]
    ] == [
        "ok",
        "error",
    ]
    assert [
        r for r in msixmigrate.legacy_gap_facts(budget_s=-1)["dbs"]["cache/ai_history.sqlite3"]
    ][0] == {"state": "timeout"}


def test_legacy_gap_off_windows_says_so(world, monkeypatch):
    monkeypatch.setattr(msixmigrate, "_is_windows", lambda: False)
    assert msixmigrate.legacy_gap_facts() == {"state": "not_windows"}


def test_report_json_carries_the_legacy_history_gap(world, monkeypatch):
    from tavotto.engine import diagnostics

    facts = {"state": "ok", "dbs": {"cache/ai_history.sqlite3": []}}
    monkeypatch.setattr(msixmigrate, "legacy_gap_facts", lambda: facts)
    report = diagnostics.build_report()
    assert report["system"]["msix_legacy_history"] == facts


def test_building_a_report_registers_the_history_counter():
    """诊断包进程不一定先 import 过 ai_history：diagnostics 自己 import 它，计数才一定登记上。"""
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    code = (
        "from tavotto.engine import diagnostics, msixmigrate; "
        "print('cache/ai_history.sqlite3' in msixmigrate._SQLITE_GAP_COUNTERS)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=120,
    )
    assert out.stdout.strip() == "True"


# ------------------------------------------------------------------ 手动找回之前的任务（#2282 补救 B）
def _history_for(db: Path, project: str, ids: list[str]) -> Path:
    from tavotto.engine import ai_history

    db.parent.mkdir(parents=True, exist_ok=True)
    for i, sid in enumerate(ids):
        ai_history.record_start(
            {
                "id": sid,
                "project": project,
                "provider": "codex",
                "prompt": f"旧的 {sid}",
                "client_request_id": f"r-{sid}",
                "owner_pid": 999999,
            },
            db_path=db,
        )
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE sessions SET status='done', ended_ms=started_ms+1, started_ms=? WHERE id=?",
            (1000 + i, sid),
        )
        con.commit()
        con.close()
    return db


@pytest.fixture
def real_db(world, monkeypatch) -> Path:
    from tavotto.engine import ai_history

    db = world.data / "cache" / "ai_history.sqlite3"
    monkeypatch.setattr(ai_history, "DB_PATH", db)
    return db


def test_missing_count_is_this_projects_union_across_old_packages(world, real_db):
    from tavotto.engine import ai_history

    _ra, a = world.pkg(PFN_A, age=0)
    _rb, b = world.pkg(PFN_B, age=100)
    _history_for(a / "cache" / "ai_history.sqlite3", "/p", ["s1", "s2"])
    _history_for(a / "cache" / "ai_history.sqlite3", "/q", ["other"])
    _history_for(b / "cache" / "ai_history.sqlite3", "/p", ["s2", "s3"])
    world.settle()
    _history_for(real_db, "/p", ["s1"])
    assert ai_history.legacy_missing_count("/p") == 2  # s2（两个包都有，只算一次）+ s3
    assert ai_history.legacy_missing_count("/q") == 1
    assert ai_history.legacy_missing_count("/elsewhere") == 0


def test_missing_count_is_zero_when_there_is_nothing_to_find(world, real_db, monkeypatch):
    from tavotto.engine import ai_history

    assert ai_history.legacy_missing_count("/p") == 0  # 没有旧包
    _r, a = world.pkg(PFN_A)
    put(a / "cache" / "ai_history.sqlite3", "not a database")
    assert ai_history.legacy_missing_count("/p") == 0  # 坏库：数不出来就不出那一行
    monkeypatch.setattr(msixmigrate, "_is_windows", lambda: False)
    assert ai_history.legacy_missing_count("/p") == 0


def test_restore_brings_back_what_is_missing_including_what_was_deleted(world, real_db):
    from tavotto.engine import ai_history

    _r, a = world.pkg(PFN_A)
    _history_for(a / "cache" / "ai_history.sqlite3", "/p", ["s1", "s2", "s3"])
    _history_for(real_db, "/p", ["s1", "new"])
    con = sqlite3.connect(real_db)
    con.execute("DELETE FROM sessions WHERE id='s1'")  # 用户在新库里删掉的
    con.commit()
    con.close()
    head_before = max(r for (r,) in sqlite3.connect(real_db).execute("SELECT rev FROM sessions"))
    assert ai_history.legacy_missing_count("/p") == 3
    assert ai_history.restore_legacy("/p") == "done"
    assert _session_ids(real_db) == ["new", "s1", "s2", "s3"]
    revs = dict(sqlite3.connect(real_db).execute("SELECT id, rev FROM sessions"))
    assert all(revs[s] > head_before for s in ("s1", "s2", "s3")), (
        "补回的要取新版本号，changes?since= 才看得见"
    )
    assert ai_history.legacy_missing_count("/p") == 0
    assert (
        ai_history.restore_legacy("/p") == "done" and len(_session_ids(real_db)) == 4
    )  # 再点一次不重复


def test_restore_touches_only_the_confirmed_project(world, real_db):
    """确认句只算当前项目：在 /p 点找回，不能把 /q 的旧任务（含用户在 /q 删掉的）也带回来（Codex #2317）。"""
    from tavotto.engine import ai_history

    _r, a = world.pkg(PFN_A)
    _history_for(a / "cache" / "ai_history.sqlite3", "/p", ["p1"])
    _history_for(a / "cache" / "ai_history.sqlite3", "/q", ["q1"])
    _history_for(
        real_db, "/q", ["kept"]
    )  # 能打开任务历史，真实库就已经在了；q1 当作用户在 /q 里删掉的
    assert ai_history.legacy_missing_count("/p") == 1
    assert ai_history.restore_legacy("/p") == "done"
    assert _session_ids(real_db) == ["kept", "p1"]
    reqs = sorted(
        r for (r,) in sqlite3.connect(real_db).execute("SELECT client_request_id FROM ai_requests")
    )
    assert reqs == ["r-kept", "r-p1"]
    assert ai_history.legacy_missing_count("/q") == 1


def test_a_project_restore_leaves_the_pending_merge_for_the_rest(world, monkeypatch):
    _r, local = world.pkg(PFN_A)
    _legacy_history(local, ["a1"])  # 项目 "p"
    _strand_history_behind_an_empty_db(world, monkeypatch)
    assert msixmigrate.restore_legacy("cache/ai_history.sqlite3", "p") == "done"
    assert msixmigrate._read_pending() is not None, "只补了一个项目：别的项目还等后台合并"


def test_restore_is_refused_off_windows_and_waits_out_a_held_lock(world, real_db, monkeypatch):
    _r, a = world.pkg(PFN_A)
    _history_for(a / "cache" / "ai_history.sqlite3", "/p", ["s1"])
    fd = layoutsession.acquire_file_lock(
        config.data_path(*msixmigrate.LOCK_REL), time.monotonic() + 5
    )
    try:
        assert (
            msixmigrate.restore_legacy("cache/ai_history.sqlite3", "/p", budget_s=0.2) == "deferred"
        )
    finally:
        layoutsession.release_file_lock(fd)
    monkeypatch.setattr(msixmigrate, "_is_windows", lambda: False)
    assert msixmigrate.restore_legacy("cache/ai_history.sqlite3", "/p") == "not_eligible"


def test_history_legacy_endpoints_count_and_restore_for_the_open_project(world, real_db, tmp_path):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    try:
        d = tmp_path / "proj"
        d.mkdir()
        (d / "tavotto_registry.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
        client = m.app.test_client()
        pj = client.post("/api/projects/open", json={"path": str(d), "default": True}).get_json()[
            "id"
        ]
        h = {"X-Tavotto-Project": pj}
        _r, a = world.pkg(PFN_A)
        _history_for(a / "cache" / "ai_history.sqlite3", str(d.resolve()), ["s1", "s2"])
        assert client.get("/api/ai/history/legacy", headers=h).get_json() == {"missing": 2}
        body = client.post("/api/ai/history/legacy/restore", headers=h).get_json()
        assert body == {"outcome": "done", "missing": 0}
        listed = client.get("/api/ai/history", headers=h).get_json()
        assert sorted(s["id"] for s in listed["sessions"]) == ["s1", "s2"]
    finally:
        m.reset_projects()


@pytest.mark.parametrize("target_exists", [True, False], ids=["merge", "copy"])
def test_pending_history_never_substitutes_the_main_file_for_a_failed_wal_snapshot(
    world, monkeypatch, target_exists
):
    _r, local = world.pkg(PFN_A)
    source = _legacy_history(local, ["checkpointed"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    if not target_exists:
        db.unlink()
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO sessions (id, project, provider, prompt, status, started_ms, ended_ms) "
            "VALUES ('wal-only', 'p', 'codex', 'synthetic', 'done', 1001, 1002)"
        )
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        assert _session_ids(source) == ["checkpointed", "wal-only"]
        real_backup = msixmigrate._backup_sqlite

        def fail_backup(src, snapshot, _deadline):
            assert src == source
            snapshot.write_bytes(b"partial snapshot")
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(msixmigrate, "_backup_sqlite", fail_backup)
        assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "failed"}
        assert msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 1
        assert not list(db.parent.glob(f"*{msixmigrate.TMP_SUFFIX}"))
        if target_exists:
            assert _session_ids(db) == []
        else:
            assert not db.exists()

        monkeypatch.setattr(msixmigrate, "_backup_sqlite", real_backup)
        assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
        assert _session_ids(db) == ["checkpointed", "wal-only"]
        assert _session_ids(source) == ["checkpointed", "wal-only"]
        assert msixmigrate._read_pending() is None
    finally:
        writer.close()


def test_retrying_a_second_package_never_restores_a_deleted_import(world, monkeypatch):
    from tavotto.engine import ai_history

    _, newer = world.pkg(PFN_A)
    _, older = world.pkg(PFN_B, age=100)
    _legacy_history(newer, ["shared", "newer-only"])
    older_db = _legacy_history(older, ["shared", "older-only"])
    world.settle()
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    real_backup = msixmigrate._backup_sqlite

    def defer_older(src, snapshot, deadline):
        if src == older_db:
            raise msixmigrate._Deadline
        real_backup(src, snapshot, deadline)

    monkeypatch.setattr(msixmigrate, "_backup_sqlite", defer_older)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "deferred"}
    assert _session_ids(db) == ["newer-only", "shared"]
    assert ai_history.delete("shared")
    assert _session_ids(db) == ["newer-only"]

    monkeypatch.setattr(msixmigrate, "_backup_sqlite", real_backup)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["newer-only", "older-only"]
    assert ai_history.find_request_record("p", "r-shared")["session"] == "shared"
    assert msixmigrate._read_pending() is None


@pytest.mark.parametrize("target_exists", [True, False], ids=["merge", "copy"])
def test_pending_history_retries_a_busy_source_without_counting_a_failure(
    world, monkeypatch, target_exists
):
    _, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    if not target_exists:
        db.unlink()
    real_backup = msixmigrate._backup_sqlite

    def busy(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(msixmigrate, "_backup_sqlite", busy)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "deferred"}
    assert msixmigrate._read_pending()["items"]["cache/ai_history.sqlite3"]["failures"] == 0
    if not target_exists:
        assert not db.exists()
    monkeypatch.setattr(msixmigrate, "_backup_sqlite", real_backup)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["old1"]


def test_pending_copy_merges_if_the_consumer_creates_the_database_during_backup(world, monkeypatch):
    from tavotto.engine import ai_history

    _, local = world.pkg(PFN_A)
    _legacy_history(local, ["old1"])
    db = _strand_history_behind_an_empty_db(world, monkeypatch)
    db.unlink()
    real_backup = msixmigrate._backup_sqlite

    def consumer_wins(src, snapshot, deadline):
        real_backup(src, snapshot, deadline)
        if not db.exists():
            ai_history.record_start(
                {"id": "new1", "project": "p", "provider": "codex", "prompt": "new"},
                db_path=db,
            )

    monkeypatch.setattr(msixmigrate, "_backup_sqlite", consumer_wins)
    assert msixmigrate.merge_pending() == {"cache/ai_history.sqlite3": "done"}
    assert _session_ids(db) == ["new1", "old1"]


def test_manual_restore_into_fresh_cache_only_merges_confirmed_project(world, real_db):
    from tavotto.engine import ai_history

    _, newer = world.pkg(PFN_A)
    _, older = world.pkg(PFN_B, age=100)
    _history_for(newer / ai_history.LEGACY_REL, "/p", ["p1"])
    _history_for(newer / ai_history.LEGACY_REL, "/q", ["q1"])
    _history_for(older / ai_history.LEGACY_REL, "/p", ["p2"])
    world.settle()

    assert not real_db.parent.exists()
    assert ai_history.legacy_missing_count("/p") == 2
    assert ai_history.restore_legacy("/p") == "done"
    assert ai_history.legacy_missing_count("/p") == 0
    assert ai_history.legacy_missing_count("/q") == 1
    with contextlib.closing(sqlite3.connect(real_db)) as con:
        assert con.execute("SELECT project, id FROM sessions ORDER BY id").fetchall() == [
            ("/p", "p1"),
            ("/p", "p2"),
        ]
        assert con.execute(
            "SELECT project, client_request_id FROM ai_requests ORDER BY client_request_id"
        ).fetchall() == [("/p", "r-p1"), ("/p", "r-p2")]


def test_manual_restore_can_restore_a_deleted_session_but_background_cannot(
    world, monkeypatch, real_db
):
    from tavotto.engine import ai_history

    _, source = world.pkg(PFN_A)
    _history_for(source / ai_history.LEGACY_REL, "/p", ["p1"])
    _history_for(source / ai_history.LEGACY_REL, "/q", ["q1"])
    assert _strand_history_behind_an_empty_db(world, monkeypatch) == real_db

    assert ai_history.restore_legacy("/p") == "done"
    assert ai_history.delete("p1")
    assert msixmigrate._read_pending() is not None
    assert msixmigrate.merge_pending() == {ai_history.LEGACY_REL: "done"}
    assert _session_ids(real_db) == ["q1"]
    assert ai_history.restore_legacy("/p") == "done"
    assert _session_ids(real_db) == ["p1", "q1"]
