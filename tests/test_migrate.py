"""Magplot 0.7 → Tavotto 迁移（P1-08）的验收。

审计的退出条件逐条对应：dry-run、只复制不覆盖、幂等、冲突报告、不删旧数据、
可回滚、`doctor` 里有产品化入口（用户不需要理解内部目录命名）。
fixture 是按 0.7.x 真实布局合成的用户目录。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import cli as engine_cli, migrate


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """一套 0.7.x 形状的 Magplot 用户目录 + 隔离的 Tavotto 目标目录。"""
    lroot = tmp_path / "Magplot"
    (lroot / "layouts" / "_versions").mkdir(parents=True)
    (lroot / "layouts" / "_autosave").mkdir(parents=True)
    (lroot / "baked_overrides").mkdir()
    (lroot / "cache").mkdir()
    (lroot / "ai_snapshots").mkdir()

    (lroot / "config.json").write_text(json.dumps({
        "recent_projects": ["/papers/figs-a", "/papers/figs-b"],
        "projects": {"/papers/figs-a": {"export_dir": "/papers/out"}},
        "worker": {"python": "/opt/py/bin/python"},
    }), encoding="utf-8")
    (lroot / "layouts" / "论文一.json").write_text(
        json.dumps({"schema": 2, "name": "论文一", "objects": []}),
        encoding="utf-8")
    (lroot / "layouts" / "_versions" / "v1.json").write_text("{}", "utf-8")
    (lroot / "layouts" / "_autosave" / "doc1.json").write_text(
        json.dumps({"schema": 3}), "utf-8")
    (lroot / "layouts" / "_styles.json").write_text("[]", "utf-8")
    (lroot / "baked_overrides.json").write_text("{}", "utf-8")
    (lroot / "baked_overrides" / "proj1.json").write_text("{}", "utf-8")
    (lroot / "ai_history.sqlite3").write_bytes(b"SQLite format 3\x00fake")
    (lroot / "ai_snapshots" / "s1.py").write_text("print()", "utf-8")
    # cache 与隐藏文件不该被带走
    (lroot / "cache" / "big.png").write_bytes(b"x" * 10)
    (lroot / "layouts" / ".DS_Store").write_bytes(b"junk")

    target = tmp_path / "Tavotto"
    monkeypatch.setenv("TAVOTTO_MIGRATE_LEGACY_CONFIG_DIR", str(lroot))
    monkeypatch.setenv("TAVOTTO_MIGRATE_LEGACY_DATA_DIR", str(lroot))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(target))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(target))
    return lroot


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_dry_run_plans_everything_and_writes_nothing(legacy, tmp_path):
    before = _snapshot(legacy)
    report = migrate.execute(dry_run=True)
    plan = report["plan"]
    assert "layouts/论文一.json" in plan["copies"]
    assert "ai_history.sqlite3" in plan["copies"]
    assert not any(c.startswith("cache") for c in plan["copies"])
    assert not any(".DS_Store" in c for c in plan["copies"])
    assert plan["config_merge"]
    # 一个字节没写：目标目录不存在、旧目录逐字节没变
    assert not (tmp_path / "Tavotto").exists()
    assert _snapshot(legacy) == before


def test_migrate_copies_merges_and_never_touches_legacy(legacy, tmp_path):
    before = _snapshot(legacy)
    report = migrate.execute()
    target = tmp_path / "Tavotto"
    assert (target / "layouts" / "论文一.json").is_file()
    assert (target / "layouts" / "_versions" / "v1.json").is_file()
    assert (target / "ai_history.sqlite3").read_bytes().startswith(b"SQLite")
    assert not (target / "cache").exists()
    cfg = json.loads((target / "config.json").read_text("utf-8"))
    assert cfg["recent_projects"] == ["/papers/figs-a", "/papers/figs-b"]
    assert cfg["worker"] == {"python": "/opt/py/bin/python"}
    assert _snapshot(legacy) == before, "旧数据被动了——迁移必须只读旧侧"
    assert migrate.report_path().is_file()
    assert report["created"]


def test_idempotent_second_run_does_nothing(legacy, tmp_path):
    migrate.execute()
    snap = _snapshot(tmp_path / "Tavotto")
    report2 = migrate.execute()
    assert report2["created"] == []
    assert report2["plan"]["copies"] == []
    # 内容一致的都记在 identical，不算冲突
    assert report2["plan"]["conflicts"] == []
    assert "layouts/论文一.json" in report2["plan"]["identical"]
    assert _snapshot(tmp_path / "Tavotto") == snap


def test_conflicting_target_is_reported_not_overwritten(legacy, tmp_path):
    target = tmp_path / "Tavotto"
    (target / "layouts").mkdir(parents=True)
    (target / "layouts" / "论文一.json").write_text(
        json.dumps({"schema": 2, "name": "我自己的新版本"}), "utf-8")
    report = migrate.execute()
    assert "layouts/论文一.json" in report["plan"]["conflicts"]
    kept = json.loads((target / "layouts" / "论文一.json").read_text("utf-8"))
    assert kept["name"] == "我自己的新版本", "目标侧被覆盖了"


def test_config_merge_never_overwrites_existing_values(legacy, tmp_path):
    target = tmp_path / "Tavotto"
    target.mkdir()
    (target / "config.json").write_text(json.dumps({
        "recent_projects": ["/new/figs"],
        "worker": {"python": "/tavotto/py"},
    }), "utf-8")
    migrate.execute()
    cfg = json.loads((target / "config.json").read_text("utf-8"))
    assert cfg["recent_projects"][0] == "/new/figs"          # 现有的在前
    assert "/papers/figs-a" in cfg["recent_projects"]        # 旧的补在后
    assert cfg["worker"] == {"python": "/tavotto/py"}        # 绝不被旧值顶掉
    assert cfg["projects"] == {"/papers/figs-a": {"export_dir": "/papers/out"}}


def test_rollback_removes_only_created_files(legacy, tmp_path):
    target = tmp_path / "Tavotto"
    (target / "layouts").mkdir(parents=True)
    mine = target / "layouts" / "我的.json"
    mine.write_text("{}", "utf-8")
    migrate.execute()
    before_legacy = _snapshot(legacy)
    result = migrate.rollback()
    assert result["rolled_back"] is True
    assert not (target / "layouts" / "论文一.json").exists()
    assert mine.exists(), "回滚删掉了不是它创建的文件"
    assert _snapshot(legacy) == before_legacy
    # 报告已消费；再回滚要有明确说法而不是安静成功
    assert migrate.rollback()["rolled_back"] is False


def test_nothing_to_migrate_is_a_clean_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_MIGRATE_LEGACY_CONFIG_DIR",
                       str(tmp_path / "nope"))
    monkeypatch.setenv("TAVOTTO_MIGRATE_LEGACY_DATA_DIR",
                       str(tmp_path / "nope"))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "t"))
    report = migrate.execute()
    assert report["plan"]["nothing_to_migrate"] is True
    assert not (tmp_path / "t").exists()


# ------------------------------ CLI 入口 -----------------------------------
def _run_doctor(capsys, *argv) -> tuple[int, str]:
    rc = engine_cli.doctor(list(argv))
    return rc, capsys.readouterr().out


def test_doctor_migrate_json_roundtrip(legacy, capsys):
    rc, out = _run_doctor(capsys, "--migrate", "--dry-run", "--json")
    assert rc == 0
    data = json.loads(out.strip().splitlines()[-1])
    assert data["ok"] is True and data["dry_run"] is True
    rc, out = _run_doctor(capsys, "--migrate", "--json")
    assert rc == 0
    data = json.loads(out.strip().splitlines()[-1])
    assert data["created"]
    rc, out = _run_doctor(capsys, "--rollback-migration", "--json")
    assert rc == 0


def test_doctor_migrate_exit_code_flags_conflicts(legacy, tmp_path, capsys):
    target = tmp_path / "Tavotto"
    (target / "layouts").mkdir(parents=True)
    (target / "layouts" / "论文一.json").write_text("{\"mine\": 1}", "utf-8")
    rc, out = _run_doctor(capsys, "--migrate")
    assert rc == 1
    assert "跳过" in out


def test_doctor_migrate_rejects_conflicting_flags(legacy, capsys):
    rc, _ = _run_doctor(capsys, "--migrate", "--rollback-migration", "--json")
    assert rc == 2


def test_plain_doctor_notes_legacy_data(legacy, capsys):
    """用户不需要知道任何内部目录：`tavotto doctor` 自己会说。"""
    rc, out = _run_doctor(capsys, )
    assert "magplot_data_found" in out and "--migrate" in out
