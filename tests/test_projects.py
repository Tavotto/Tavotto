"""项目系统：用户配置、无项目守卫、打开/切换/最近列表/目录浏览/项目设置。"""
import json
from pathlib import Path

import pymupdf
import pytest

from magplot import app as m
from magplot.engine import config as engine_config
from magplot.engine import pool as engine_pool


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    # 保存/恢复全局项目状态，测试间互不污染
    old = m.FIGURES_DIR
    yield m.app.test_client()
    m.FIGURES_DIR = old
    engine_pool.stop_watcher()


def _make_figs(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    return figs


# ---------------- engine/config.py ------------------------------------------

def test_config_defaults_when_missing():
    assert engine_config.load() == {"recent_projects": [], "projects": {},
                                    "ai": {}, "updates": {}, "worker": {}}


def test_config_corrupted_file_falls_back():
    engine_config.config_dir().mkdir(parents=True, exist_ok=True)
    engine_config.config_path().write_text("{not json", encoding="utf-8")
    assert engine_config.load()["recent_projects"] == []


def test_recent_ordering_and_remove(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    engine_config.touch_recent(str(a))
    engine_config.touch_recent(str(b))
    assert [e["path"] for e in engine_config.recent_projects()] == [str(b), str(a)]
    engine_config.touch_recent(str(a))  # 重开提到首位，不重复
    assert [e["path"] for e in engine_config.recent_projects()] == [str(a), str(b)]
    assert engine_config.remove_recent(str(b)) is True
    assert engine_config.remove_recent(str(b)) is False
    assert [e["path"] for e in engine_config.recent_projects()] == [str(a)]


def test_project_settings_roundtrip(tmp_path):
    p = str(tmp_path / "proj")
    assert engine_config.project_settings(p) == {}
    merged = engine_config.set_project_settings(p, {"allow_write_back": False})
    assert merged == {"allow_write_back": False}
    # None = 清除
    merged = engine_config.set_project_settings(p, {"allow_write_back": None})
    assert merged == {}


# ---------------- 无项目守卫 -------------------------------------------------

def test_endpoints_409_without_project(client, monkeypatch):
    monkeypatch.setattr(m, "FIGURES_DIR", None)
    for path in ("/api/panels", "/api/render?id=x.pdf&w=200"):
        resp = client.get(path)
        assert resp.status_code == 409, path
        assert resp.get_json()["code"] == "no_project"
    assert client.get("/api/project").get_json() == {"open": False}


# ---------------- 打开 / 切换 / 最近 ------------------------------------------

def test_open_project_and_recent(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    resp = client.post("/api/projects/open", json={"path": str(figs)})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["open"] is True and body["figures_dir"] == str(figs)
    # 无注册表的目录自动起草
    assert (figs / "mm_registry.json").exists()
    # 面板能列出来
    panels = client.get("/api/panels").get_json()["panels"]
    assert [p["id"] for p in panels] == ["p1.pdf"]
    # 进入最近列表且标记 current
    recent = client.get("/api/projects/recent").get_json()["recent"]
    assert recent[0]["path"] == str(figs)
    assert recent[0]["current"] is True and recent[0]["exists"] is True


def test_open_missing_dir_keeps_current(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    monkeypatch.setattr(m, "FIGURES_DIR", figs)
    resp = client.post("/api/projects/open", json={"path": str(tmp_path / "nope")})
    assert resp.status_code == 400
    assert m.FIGURES_DIR == figs  # 当前项目不变


def test_create_project(client, tmp_path):
    target = tmp_path / "new_proj"
    resp = client.post("/api/projects/open",
                       json={"path": str(target), "create": True})
    assert resp.status_code == 200
    assert target.is_dir()
    assert resp.get_json()["open"] is True


def test_remove_recent_keeps_disk(client, tmp_path):
    figs = _make_figs(tmp_path)
    engine_config.touch_recent(str(figs))
    resp = client.post("/api/projects/remove", json={"path": str(figs)})
    assert resp.get_json()["ok"] is True
    assert figs.exists()  # 磁盘内容不动
    assert engine_config.recent_projects() == []


# ---------------- 切换清理协议 -----------------------------------------------

def test_watcher_replacement_stops_old(tmp_path):
    figs = _make_figs(tmp_path, "w1")
    engine_pool.start_watcher(str(figs), [], lambda c: None, interval=0.05)
    first = engine_pool._watcher_stop
    assert first is not None and not first.is_set()
    engine_pool.start_watcher(str(figs), [], lambda c: None, interval=0.05)
    assert first.is_set()  # 旧 watcher 已被叫停
    engine_pool.stop_watcher()
    assert engine_pool._watcher_stop is None


def test_ai_interrupt_all_marks_running_sessions():
    from magplot.engine import ai_bridge

    class FakeProc:
        killed = False

        def kill(self):
            self.killed = True

    proc = FakeProc()
    ai_bridge.SESSIONS["_t1"] = {"id": "_t1", "status": "running", "proc": proc}
    ai_bridge.SESSIONS["_t2"] = {"id": "_t2", "status": "done", "proc": FakeProc()}
    try:
        assert ai_bridge.interrupt_all() == 1
        assert proc.killed is True
        assert ai_bridge.SESSIONS["_t1"]["status"] == "interrupted"
        assert ai_bridge.SESSIONS["_t2"]["status"] == "done"
    finally:
        ai_bridge.SESSIONS.pop("_t1", None)
        ai_bridge.SESSIONS.pop("_t2", None)


def test_switch_between_two_projects(client, tmp_path):
    a = _make_figs(tmp_path, "proj_a")
    b = _make_figs(tmp_path, "proj_b")
    assert client.post("/api/projects/open", json={"path": str(a)}).status_code == 200
    assert client.get("/api/panels").get_json()["figures_dir"] == str(a)
    assert client.post("/api/projects/open", json={"path": str(b)}).status_code == 200
    body = client.get("/api/panels").get_json()
    assert body["figures_dir"] == str(b)
    # 最近列表首位是 b，a 仍在
    recent = client.get("/api/projects/recent").get_json()["recent"]
    assert [e["path"] for e in recent[:2]] == [str(b), str(a)]


# ---------------- 目录浏览 ---------------------------------------------------

def test_browse_lists_dirs_only(client, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    body = client.get(f"/api/projects/browse?path={tmp_path}").get_json()
    assert [d["name"] for d in body["dirs"]] == ["sub"]
    assert body["parent"]


def test_browse_missing_dir(client, tmp_path):
    resp = client.get(f"/api/projects/browse?path={tmp_path}/nope")
    assert resp.status_code == 400


# ---------------- 项目只读（写回权限） ----------------------------------------

def test_write_back_forbidden_when_read_only(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    monkeypatch.setattr(m, "FIGURES_DIR", figs)
    engine_config.set_project_settings(str(figs), {"allow_write_back": False})
    resp = client.post("/api/engine/update_source",
                       json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "write_back_disabled"
    resp = client.post("/api/engine/history/restore",
                       json={"id": "p1.pdf", "n": -1})
    assert resp.status_code == 403
    # 原文件未被改动
    assert (figs / "p1.pdf").exists()


# ---------------- 项目设置（导出/备份目录） -----------------------------------

def test_settings_export_dir_used_by_export(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    monkeypatch.setattr(m, "FIGURES_DIR", figs)
    custom = tmp_path / "my_exports"
    resp = client.patch("/api/project/settings",
                        json={"export_dir": str(custom)})
    assert resp.status_code == 200
    assert resp.get_json()["export_dir"] == str(custom)

    resp = client.post("/api/export", json={
        "page_w_mm": 50, "page_h_mm": 30, "formats": ["pdf"],
        "stem": "s", "objects": []})
    body = resp.get_json()
    assert body["export_dir"] == str(custom)
    assert (custom / body["files"][0]["name"]).exists()

    # 空字符串恢复默认
    resp = client.patch("/api/project/settings", json={"export_dir": ""})
    assert resp.get_json()["settings"].get("export_dir") is None


# ---------------- 诊断 --------------------------------------------------------

def test_diagnostics_reports_checks(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    monkeypatch.setattr(m, "FIGURES_DIR", figs)
    checks = {c["id"]: c for c in client.get("/api/diagnostics").get_json()["checks"]}
    assert "cli_codex" in checks and "cli_claude" in checks
    assert checks["project_readable"]["ok"] is True
    assert checks["project_writable"]["ok"] is True
    assert "registry_conflicts" in checks


def test_diagnostics_without_project(client, monkeypatch):
    monkeypatch.setattr(m, "FIGURES_DIR", None)
    checks = {c["id"]: c for c in client.get("/api/diagnostics").get_json()["checks"]}
    assert checks["project_open"]["ok"] is False


def test_hidden_dirs_and_files_are_not_assets(client, tmp_path):
    """隐藏目录/文件不进素材库。

    真实图库旁边常年躺着工具产物：.venv、.git、渲染快照 .rendered/、
    .qa_no_survey/page-1.png……全都列出来的话素材库会被淹没（论文的
    supporting_information 曾经因此列出 51 个「面板」，真正有用的只有 17 个）。
    """
    figs = _make_figs(tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    for rel in (".rendered/page-1.pdf", ".qa/contact.pdf", "sub/real.pdf"):
        target = figs / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.save(target)
    doc.close()
    (figs / ".DS_Store").write_bytes(b"junk")

    client.post("/api/projects/open", json={"path": str(figs)})
    ids = {p["id"] for p in client.get("/api/panels").get_json()["panels"]}
    assert ids == {"p1.pdf", str(Path("sub/real.pdf"))}


# ---------------- 端口占用（双击启动的应用不能无声退出） ----------------------

def test_resolve_port_uses_preferred_when_free(monkeypatch):
    monkeypatch.setattr(m, "port_is_free", lambda p: True)
    assert m.resolve_port(5089) == 5089


def test_resolve_port_returns_none_when_magplot_already_running(monkeypatch):
    """端口上是另一个 Magplot：不再起第二个，调用方把浏览器指过去即可。"""
    monkeypatch.setattr(m, "port_is_free", lambda p: False)
    monkeypatch.setattr(m, "magplot_is_serving", lambda p: True)
    assert m.resolve_port(5089) is None


def test_resolve_port_steps_aside_for_other_programs(monkeypatch):
    """端口被别的程序占了就顺延——窗口化应用报不出 traceback，不能直接崩。"""
    monkeypatch.setattr(m, "magplot_is_serving", lambda p: False)
    monkeypatch.setattr(m, "port_is_free", lambda p: p >= 5092)
    assert m.resolve_port(5089) == 5092


def test_resolve_port_gives_up_gracefully(monkeypatch):
    monkeypatch.setattr(m, "magplot_is_serving", lambda p: False)
    monkeypatch.setattr(m, "port_is_free", lambda p: False)
    assert m.resolve_port(5089, tries=3) == 5089   # 交给 app.run 报错，有日志可查
