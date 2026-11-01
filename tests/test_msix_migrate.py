"""MSIX 宿主私有区旧数据补回真实目录（`engine/msixmigrate.py`，#1574 善后）。

假的目录树 + monkeypatch（平台判定、包身份、LOCALAPPDATA）在 mac / Linux 上测全部逻辑；
真机行为（Windows 把写入重定向进 `Packages\\<pfn>\\LocalCache`、MAX_PATH、杀软占文件）只能在 MSIX 真机验，见 PR 说明。
"""

from __future__ import annotations

import ast
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


def test_history_migrated_first_even_when_sync_budget_exhausted(world):
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
