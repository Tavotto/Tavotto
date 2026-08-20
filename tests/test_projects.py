"""项目系统：用户配置、无项目守卫、打开/切换/最近列表/目录浏览/项目设置。"""
import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import config as engine_config
from tavotto.engine import pool as engine_pool


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()          # 测试间互不污染：进来就是「一个项目都没开」
    yield m.app.test_client()
    m.reset_projects()
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
                                    "ai": {}, "updates": {}, "worker": {},
                                    "telemetry": {}}


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
    m.reset_projects()
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
    assert (figs / "tavotto_registry.json").exists()
    # 面板能列出来
    panels = client.get("/api/panels").get_json()["panels"]
    assert [p["id"] for p in panels] == ["p1.pdf"]
    # 进入最近列表且标记 current
    recent = client.get("/api/projects/recent").get_json()["recent"]
    assert recent[0]["path"] == str(figs)
    assert recent[0]["current"] is True and recent[0]["exists"] is True


def test_open_missing_dir_keeps_current(client, tmp_path, monkeypatch):
    figs = _make_figs(tmp_path)
    m.open_project(str(figs))
    resp = client.post("/api/projects/open", json={"path": str(tmp_path / "nope")})
    assert resp.status_code == 400
    assert m.default_project_path() == figs  # 当前项目不变


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
    """同一目录重开 watcher 换掉旧的；**不同目录的 watcher 各自独立**——
    多标签页各开各的项目时，两个图库都得继续被盯着。"""
    figs = _make_figs(tmp_path, "w1")
    other = _make_figs(tmp_path, "w2")
    engine_pool.start_watcher(str(figs), [], lambda c: None, interval=0.05)
    first = engine_pool._watchers[engine_pool._norm_dir(str(figs))]
    assert not first.is_set()

    engine_pool.start_watcher(str(other), [], lambda c: None, interval=0.05)
    assert not first.is_set()          # 另一个项目的 watcher 不受影响
    assert len(engine_pool.watched_dirs()) == 2

    engine_pool.start_watcher(str(figs), [], lambda c: None, interval=0.05)
    assert first.is_set()              # 同目录的旧 watcher 已被叫停

    engine_pool.stop_watcher(str(figs))
    assert engine_pool.watched_dirs() == [engine_pool._norm_dir(str(other))]
    engine_pool.stop_watcher()
    assert engine_pool.watched_dirs() == []


def test_ai_interrupt_all_marks_running_sessions():
    from tavotto.engine import ai_bridge

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
    m.open_project(str(figs))
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
    m.open_project(str(figs))
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
    m.open_project(str(figs))
    checks = {c["id"]: c for c in client.get("/api/diagnostics").get_json()["checks"]}
    assert "cli_codex" in checks and "cli_claude" in checks
    assert checks["project_readable"]["ok"] is True
    assert checks["project_writable"]["ok"] is True
    assert "registry_conflicts" in checks


def test_diagnostics_without_project(client, monkeypatch):
    m.reset_projects()
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


def test_resolve_port_returns_none_when_tavotto_already_running(monkeypatch):
    """端口上是另一个 Tavotto：不再起第二个，调用方把浏览器指过去即可。"""
    monkeypatch.setattr(m, "port_is_free", lambda p: False)
    monkeypatch.setattr(m, "tavotto_is_serving", lambda p: True)
    assert m.resolve_port(5089) is None


def test_resolve_port_steps_aside_for_other_programs(monkeypatch):
    """端口被别的程序占了就顺延——窗口化应用报不出 traceback，不能直接崩。"""
    monkeypatch.setattr(m, "tavotto_is_serving", lambda p: False)
    monkeypatch.setattr(m, "port_is_free", lambda p: p >= 5092)
    assert m.resolve_port(5089) == 5092


def test_resolve_port_gives_up_gracefully(monkeypatch):
    monkeypatch.setattr(m, "tavotto_is_serving", lambda p: False)
    monkeypatch.setattr(m, "port_is_free", lambda p: False)
    assert m.resolve_port(5089, tries=3) == 5089   # 交给 app.run 报错，有日志可查


# ---------------- 多项目并存（不同标签页开不同图库） --------------------------

def test_two_projects_open_at_once_and_pj_routes_requests(client, tmp_path):
    """一个进程同时端着两个图库；请求靠 pj 认领，互不串。

    以前每次打开项目都会 stop_watcher + shutdown_all + interrupt_all，
    等于把另一个标签页正在用的渲染会话和 AI 任务一起打掉——「不同标签页开
    不同项目」根本无从谈起。
    """
    a, b = _make_figs(tmp_path, "proj_a"), _make_figs(tmp_path, "proj_b")
    (b / "extra.pdf").write_bytes((a / "p1.pdf").read_bytes())

    ida = client.post("/api/projects/open", json={"path": str(a)}).get_json()["id"]
    # default=false：只给本标签页用，不改新标签页的默认落点
    idb = client.post("/api/projects/open",
                      json={"path": str(b), "default": False}).get_json()["id"]
    assert ida != idb
    assert m.default_project_path() == a

    # 请求头与查询参数两条路都要认（<img src> / EventSource 加不了请求头）
    via_header = client.get("/api/panels", headers={"X-Tavotto-Project": idb})
    assert via_header.get_json()["figures_dir"] == str(b)
    assert client.get(f"/api/panels?pj={ida}").get_json()["figures_dir"] == str(a)
    # 不带 pj = 默认项目
    assert client.get("/api/panels").get_json()["figures_dir"] == str(a)

    listed = client.get("/api/projects").get_json()
    assert sorted(p["id"] for p in listed["projects"]) == sorted([ida, idb])
    assert listed["default"] == ida


def test_unknown_pj_is_409_not_silently_another_project(client, tmp_path):
    """指名一个不存在的项目必须报错。

    悄悄落到默认项目上的话，标签页会对着**另一个图库**继续编辑——用户看到
    的是「面板莫名其妙全变了」，比一个 409 难查一百倍。
    """
    figs = _make_figs(tmp_path)
    client.post("/api/projects/open", json={"path": str(figs)})
    resp = client.get("/api/panels", headers={"X-Tavotto-Project": "deadbeef0000"})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "no_project"


def test_reopening_same_project_reuses_context(client, tmp_path):
    figs = _make_figs(tmp_path)
    first = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    again = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    assert again["id"] == first["id"] and again["reused"] is True


def test_close_project_leaves_others_alone(client, tmp_path):
    a, b = _make_figs(tmp_path, "close_a"), _make_figs(tmp_path, "close_b")
    ida = client.post("/api/projects/open", json={"path": str(a)}).get_json()["id"]
    idb = client.post("/api/projects/open", json={"path": str(b)}).get_json()["id"]
    assert client.post("/api/projects/close", json={"id": ida}).get_json()["ok"] is True
    ids = [p["id"] for p in client.get("/api/projects").get_json()["projects"]]
    assert ids == [idb]
    assert client.get("/api/panels", headers={"X-Tavotto-Project": ida}).status_code == 409


class _FakeWorker:
    """够 api_engine_render 用的最小 worker：不碰科学栈，也不起子进程。"""

    def __init__(self, exc=None):
        self.built = True
        self.rev = 7
        self._exc = exc

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        if self._exc is not None:
            raise self._exc
        return {"manifest": {"elements": []}, "warnings": []}


@pytest.fixture
def sse_spy(monkeypatch):
    """记下 sse_publish 实际发出的 (事件, payload)。"""
    events: list[tuple[str, dict]] = []
    real = m.sse_publish
    monkeypatch.setattr(m, "sse_publish", lambda ev, data: (
        events.append((ev, data)), real(ev, data))[1])
    return events


def _stub_engine(monkeypatch, worker):
    monkeypatch.setattr(
        m.engine_registry.Registry, "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"})
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **kw: worker)


def test_render_events_carry_pj(client, tmp_path, monkeypatch, sse_spy):
    """render.started/done 必须带 pj。

    renderStore 按 fileId 索引且不分项目，事件不带 pj 时前端一律不过滤——
    另一个标签页里同名的面板（到处都是的 Fig1.pdf）会跟着显示「正在构建…」
    且不会自己消失。
    """
    a, b = _make_figs(tmp_path, "sse_a"), _make_figs(tmp_path, "sse_b")
    ida = client.post("/api/projects/open", json={"path": str(a)}).get_json()["id"]
    idb = client.post("/api/projects/open",
                      json={"path": str(b), "default": False}).get_json()["id"]
    _stub_engine(monkeypatch, _FakeWorker())

    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []},
                       headers={"X-Tavotto-Project": idb})
    assert resp.status_code == 200
    sent = dict(sse_spy)
    assert sent["render.started"]["pj"] == idb
    assert sent["render.done"]["pj"] == idb
    # 认领的是请求指名的项目，不是默认项目
    assert idb != ida


def test_render_response_carries_timings(client, tmp_path, monkeypatch, sse_spy):
    """`/api/engine/render` 的响应必须带 `timings`，且里面有 `worker_get_ms`。

    取会话（必要时 spawn 解释器 + import matplotlib）既不在 worker 的计时里
    也不在 build 里——只有 app 层量得到。少了它，冷启动的十几秒在数据里
    凭空消失，性能判断就全建立在一个漏项上。
    """
    figs = _make_figs(tmp_path, "timing")
    client.post("/api/projects/open", json={"path": str(figs)})
    _stub_engine(monkeypatch, _FakeWorker())

    body = client.post("/api/engine/render",
                       json={"id": "p1.pdf", "patches": []}).get_json()
    assert isinstance(body["timings"], dict)
    assert isinstance(body["timings"]["worker_get_ms"], (int, float))


def test_render_rejects_a_bogus_preview_dpi(client, tmp_path, monkeypatch, sse_spy):
    """preview_dpi 写错是调用方的错（400），不能变成一次 500 渲染失败。"""
    figs = _make_figs(tmp_path, "dpi")
    client.post("/api/projects/open", json={"path": str(figs)})
    _stub_engine(monkeypatch, _FakeWorker())

    for bad in ("很高", 0, -5):
        resp = client.post("/api/engine/render",
                           json={"id": "p1.pdf", "patches": [], "preview_dpi": bad})
        assert resp.status_code == 400, (bad, resp.get_json())
    assert client.post("/api/engine/render",
                       json={"id": "p1.pdf", "patches": [],
                             "preview_dpi": 96}).status_code == 200


def test_render_failed_event_carries_pj(client, tmp_path, monkeypatch, sse_spy):
    """失败路径同理：不带 pj 的话别的标签页会永远卡在一条不属于它的错误上。"""
    figs = _make_figs(tmp_path, "sse_fail")
    pid = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
    _stub_engine(monkeypatch, _FakeWorker(
        engine_pool.WorkerError("脚本报错", code="script_error")))

    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 500
    sent = dict(sse_spy)
    assert sent["render.started"]["pj"] == pid
    assert sent["render.failed"]["pj"] == pid
    assert "render.done" not in sent


# ---------------- 目录浏览：跨盘符与手输路径 ----------------------------------

def test_browse_exposes_roots_and_shortcuts(client, tmp_path):
    """驱动器一层必须给出来。

    Windows 上 `C:\\` 的 parent 就是它自己，只能从主目录往下钻的话
    **永远到不了 D 盘**（朋友那台机器上就是这样）。
    """
    body = client.get(f"/api/projects/browse?path={tmp_path}").get_json()
    assert body["roots"] and all(r["path"] for r in body["roots"])
    assert any(s["name"] == "主目录" for s in body["shortcuts"])
    assert body["is_roots"] is False

    roots = client.get("/api/projects/browse?path=@roots").get_json()
    assert roots["is_roots"] is True and roots["dirs"] == roots["roots"]


def test_browse_missing_path_reports_nearest_existing(client, tmp_path):
    """路径可以手输/粘贴，打错一个字符不该只换来一句死报错。"""
    (tmp_path / "real").mkdir()
    resp = client.get(f"/api/projects/browse?path={tmp_path}/real/typo/deeper")
    assert resp.status_code == 400
    assert resp.get_json()["nearest"] == str(tmp_path / "real")


# ---------------- 脚本注册表的界面入口 ----------------------------------------

def test_registry_lists_unregistered_candidates(client, tmp_path):
    """空注册表 + 在存图的脚本 = 必须被报成候选，不能让用户对着空列表猜。"""
    figs = _make_figs(tmp_path)
    (figs / "tavotto_registry.json").write_text('{"version":1,"scripts":{}}', encoding="utf-8")
    (figs / "plot_it.py").write_text(
        'from pathlib import Path\n'
        'OUT = Path(__file__).parent\n'
        'def main():\n'
        '    fig.savefig((OUT / "Fig_new").with_suffix(".pdf"))\n',
        encoding="utf-8")
    client.post("/api/projects/open", json={"path": str(figs)})

    body = client.get("/api/registry").get_json()
    assert body["scripts"] == {}
    cand = {c["script"]: c for c in body["candidates"]}
    assert cand["plot_it.py"]["new_stems"] == ["Fig_new"]

    # 扫描 → 写入注册表 → 立即生效
    scanned = client.post("/api/registry/scan").get_json()
    assert scanned["changes"]["added_scripts"] == ["plot_it.py"]
    assert client.get("/api/registry").get_json()["scripts"]["plot_it.py"]["stems"] == ["Fig_new"]


def test_registry_manual_write_resolves_conflict(client, tmp_path):
    """手工裁决：把 stem 判给某个脚本，其它脚本对它的认领一并摘掉。
    否则 registry.load 会因 stem 重复直接报错，整个项目打不开。"""
    figs = _make_figs(tmp_path)
    (figs / "tavotto_registry.json").write_text(json.dumps({"version": 1, "scripts": {
        "one.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Shared", "Own"]},
    }}), encoding="utf-8")
    client.post("/api/projects/open", json={"path": str(figs)})

    resp = client.put("/api/registry", json={"script": "two.py", "entry": "render",
                                             "stems": ["Shared"]})
    scripts = resp.get_json()["scripts"]
    assert scripts["two.py"]["stems"] == ["Shared"]
    assert scripts["one.py"]["stems"] == ["Own"]      # 冲突的那个被摘掉了


def test_registry_probe_rejects_paths_outside_project(client, tmp_path):
    """这个端点会真的执行代码，越权路径必须挡死。"""
    figs = _make_figs(tmp_path)
    (tmp_path / "outside.py").write_text("def main():\n    pass\n", encoding="utf-8")
    client.post("/api/projects/open", json={"path": str(figs)})
    for bad in ("../outside.py", "nope.py", "tavotto_registry.json"):
        assert client.post("/api/registry/probe", json={"script": bad}).status_code == 404


# --------------------- 路径身份：按卷判，不按 os.name --------------------------
def test_case_probe_agrees_with_the_actual_filesystem(tmp_path):
    """探测结果必须与这台机器上**真实**的行为一致（平台无关的写法）。

    在大小写不敏感的卷上，换个大小写拼出来的路径本来就指向同一个目录；
    敏感的卷上则不存在。两边都拿真实文件系统当答案，Linux/macOS/Windows
    上跑的是同一条断言。
    """
    d = tmp_path / "Probe"
    d.mkdir()
    reality = (tmp_path / "pROBE").exists()
    assert engine_config.path_is_case_insensitive(d) is reality


def test_project_identity_follows_the_volume_not_the_os_name(tmp_path, monkeypatch):
    """同一个图库换个大小写打开，必须还是同一个项目——判据是**卷**。

    这里以前写的是 `os.name == "nt"`，而 macOS 默认的 APFS/HFS+ 同样是
    大小写不敏感的（那儿 `os.name` 是 "posix"）。于是从 Finder 拖进来一次、
    从「最近项目」里手输一次，同一个图库会得到两个不同的 pid 与两个不同的
    池键：两套 worker、两份 `baked_overrides/<项目id>.json` 写回基线，
    用户在一边做的事另一边完全看不见。
    `Path.resolve()` 救不了——POSIX 上它只解析符号链接与 . / ..，不会向
    文件系统问规范大小写。
    """
    a, b = tmp_path / "Figs", tmp_path / "FIGS"

    monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: True)
    assert m._project_id(a) == m._project_id(b)
    assert engine_pool._norm_dir(a) == engine_pool._norm_dir(b)

    monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: False)
    assert m._project_id(a) != m._project_id(b)
    assert engine_pool._norm_dir(a) != engine_pool._norm_dir(b)


def test_case_probe_walks_up_past_uncaseable_components(tmp_path):
    """末段没有字母可翻时要往上走，**不能就此认定平台默认值**。

    `/Volumes/CaseSensitive/Foo/123` 这种目录名全是数字的，翻大小写翻不出
    另一个名字，探测无从下手。以前这时直接留兜底值——而 macOS 的兜底是
    「不敏感」，于是在**大小写敏感**的 macOS 卷上，`/Foo/123` 与 `/foo/123`
    会共用一个项目 id、一个 worker 池、一份写回基线，可它们是两个目录。
    往上找一个有字母的祖先来探，同一个卷答案是一样的。
    """
    parent = tmp_path / "Probe"          # 这一层有字母，探得动
    leaf = parent / "123"                # 这一层没有
    leaf.mkdir(parents=True)
    reality = (tmp_path / "pROBE").exists()

    # 两层问出来的答案必须一致——它们在同一个卷上
    assert engine_config.path_is_case_insensitive(leaf) is reality
    assert engine_config.path_is_case_insensitive(parent) is reality


def test_case_probe_answers_the_same_for_paths_that_do_not_exist_yet(tmp_path):
    """还没建出来的路径按最近的存在祖先探——项目目录可能刚被删掉。"""
    reality = engine_config.path_is_case_insensitive(tmp_path)
    assert engine_config.path_is_case_insensitive(tmp_path / "Nope" / "Deeper") is reality
