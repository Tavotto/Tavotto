"""统一项目刷新（Prompt 04 / `engine/project_refresh.py`）。

守四件事：

1. **唯一入口**——手动刷新 / `/api/registry/scan` / probe 成功 / 手工登记
   走的是同一条编排，事件里的 `reason` 就是它们各自的身份；
2. **结构化 diff**——脚本名之外，entry / cost / notes / stems 归属都要认出来；
3. **不执行用户脚本**——刷新只读 AST 与 `stat()`；
4. **失败不伤现状**——注册表读不回来时内存里那份原封不动，事件一条不发。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    discover as engine_discover,
    pool as engine_pool,
    probe as engine_probe,
    project_refresh as engine_refresh,
    project_watch as engine_watch,
    registry as engine_registry,
)

# 静态可识别：有 main、有字面量 savefig。**同时是一枚运行探针**——真跑起来
# 会在图库里留下 `RAN.txt`，于是"刷新没有执行用户脚本"这条不靠桩的存在与否
# 来证明，而是靠磁盘上有没有那个文件。
CANARY = """\
from pathlib import Path


def main():
    Path("RAN.txt").write_text("executed", encoding="utf-8")
    fig.savefig("{stem}.pdf")
"""


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture
def sse_spy(monkeypatch):
    """记下 sse_publish 实际发出的 (事件, payload)。"""
    events: list[tuple[str, dict]] = []
    real = m.sse_publish
    monkeypatch.setattr(
        m, "sse_publish", lambda ev, data: (events.append((ev, data)), real(ev, data))[1]
    )
    return events


def _pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(path)
    doc.close()


def _raster(path, width=8):
    """**真的**位图。随手写几个字节的话 `probe_asset` 会抛错，`/api/panels`
    当场跳过它——于是「两把尺一致」那条用例量的就成了「两边都认不出这个坏
    文件」，而不是「两边对素材的判据相同」。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, width), False)
    pix.clear_with(200)
    pix.save(path)


def _project(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    return figs


def _script(figs, name, stem):
    (figs / name).write_text(CANARY.format(stem=stem), encoding="utf-8")


def _open(client, figs, default=True):
    body = client.post(
        "/api/projects/open", json={"path": str(figs), "default": default}
    ).get_json()
    return m.PROJECTS[body["id"]]


def _registry_file(figs) -> dict:
    return json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))


def _write_registry(figs, scripts: dict) -> None:
    engine_discover.write_config(figs, {"version": 1, "scripts": scripts})


# ---------------------------------------------------------------------------
# diff：registry
# ---------------------------------------------------------------------------
class TestRegistryDiff:
    def test_no_change_refresh_is_an_empty_diff_and_no_events(self, client, tmp_path, sse_spy):
        """什么都没变的刷新：空 diff、**一条事件都不发**。

        「无差异也发一轮事件」的代价不是噪音而已——前端收到 `registry.changed`
        会重取脚本清单与 runtime 素材清单，而项目刷新是个会被反复点的按钮。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")  # 第一轮把静态发现落进注册表
        sse_spy.clear()

        result = m.refresh_project(ctx, reason="manual")
        assert result["registry"]["added_scripts"] == []
        assert result["registry"]["removed_scripts"] == []
        assert result["registry"]["changed_scripts"] == []
        assert result["assets"] == {
            "added": [],
            "removed": [],
            "changed": [],
            "baseline": False,
        }
        assert result["published"] == []
        assert sse_spy == []

    def test_a_new_statically_recognizable_script_shows_up(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _script(figs, "fig_new.py", "FigNew")
        _pdf(figs / "FigNew.pdf")
        sse_spy.clear()

        result = m.refresh_project(ctx, reason="manual")
        assert result["registry"]["added_scripts"] == ["fig_new.py"]
        assert result["registry"]["added_stems"] == ["FigNew"]
        assert ctx.registry.for_stem("FigNew")["script"] == "fig_new.py"
        # 磁盘上那份也更新了（下次打开项目就是这个结果）
        assert "fig_new.py" in _registry_file(figs)["scripts"]
        assert [ev for ev, _ in sse_spy] == ["registry.changed", "assets.changed"]

    def test_removing_a_script_from_the_registry_shows_up(self, client, tmp_path):
        figs = _project(tmp_path)
        _write_registry(figs, {"gone.py": {"entry": "main", "cost": "medium", "stems": ["Gone"]}})
        ctx = _open(client, figs)
        assert ctx.registry.all_scripts() == ["gone.py"]

        _write_registry(figs, {})  # 用户在编辑器外把它删了
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["removed_scripts"] == ["gone.py"]
        assert result["registry"]["removed_stems"] == ["Gone"]
        assert ctx.registry.all_scripts() == []

    def test_stems_added_and_removed_on_an_existing_script(self, client, tmp_path):
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S1"]}})
        ctx = _open(client, figs)

        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S1", "S2"]}})
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["changed_scripts"] == ["a.py"]
        assert result["registry"]["script_changes"]["a.py"] == ["stems"]
        assert result["registry"]["added_stems"] == ["S2"]

        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S2"]}})
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["removed_stems"] == ["S1"]

    def test_entry_and_cost_and_notes_changes_are_seen(self, client, tmp_path):
        """**只比脚本名是不够的。** 这三样任何一个变了，热 worker 手里那份
        就已经不是用户的注册表说的那份了。"""
        figs = _project(tmp_path)
        _write_registry(
            figs, {"a.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["S"]}}
        )
        ctx = _open(client, figs)

        _write_registry(
            figs, {"a.py": {"entry": "render", "cost": "heavy", "notes": "3d", "stems": ["S"]}}
        )
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["changed_scripts"] == ["a.py"]
        assert result["registry"]["script_changes"]["a.py"] == ["entry", "cost", "notes"]
        # 脚本清单一个字没变，所以「只比名字」的 diff 在这里是全空的
        assert result["registry"]["added_scripts"] == []
        assert result["registry"]["removed_scripts"] == []

    def test_a_stem_changing_owner_is_reported_as_a_move(self, client, tmp_path):
        figs = _project(tmp_path)
        _write_registry(
            figs,
            {
                "a.py": {"entry": "main", "cost": "medium", "stems": ["S"]},
                "b.py": {"entry": "main", "cost": "medium", "stems": []},
            },
        )
        ctx = _open(client, figs)
        _write_registry(
            figs,
            {
                "a.py": {"entry": "main", "cost": "medium", "stems": []},
                "b.py": {"entry": "main", "cost": "medium", "stems": ["S"]},
            },
        )
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["moved_stems"] == [{"stem": "S", "from": "a.py", "to": "b.py"}]
        # 归属换了 = 两边的 worker 都过期了
        assert result["registry"]["changed_scripts"] == ["a.py", "b.py"]
        # 「换了个主人」不是「新增/删除」——它一直在注册表里
        assert result["registry"]["added_stems"] == []
        assert result["registry"]["removed_stems"] == []

    def test_reordering_stems_is_not_a_change(self, client, tmp_path):
        """注册表里 stem 的先后没有语义（`register()` 写 sorted、`merge()` 追加）。
        按列表比的话，一次纯重排会作废一批本来好好的 worker。"""
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S1", "S2"]}})
        ctx = _open(client, figs)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S2", "S1"]}})
        result = m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert result["registry"]["changed_scripts"] == []
        assert result["published"] == []

    def test_manual_entries_win_over_the_static_draft(self, client, tmp_path):
        """手工裁决就是权威：静态扫描**永远不覆盖**已有条目。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(
            figs,
            {"fig_a.py": {"entry": "render", "cost": "heavy", "notes": "手工", "stems": ["FigA"]}},
        )
        ctx = _open(client, figs)

        result = m.refresh_project(ctx, reason="manual")
        assert result["registry"]["changed_scripts"] == []
        assert ctx.registry.entries()["fig_a.py"] == {
            "entry": "render",
            "cost": "heavy",
            "notes": "手工",
            "stems": ["FigA"],
        }

    def test_conflicts_are_reported_and_never_auto_resolved(self, client, tmp_path):
        """两个脚本都声称产出同一个 stem：报出来，**谁都不给**。"""
        figs = _project(tmp_path)
        _script(figs, "one.py", "Shared")
        _script(figs, "two.py", "Shared")
        _pdf(figs / "Shared.pdf")
        ctx = _open(client, figs)

        result = m.refresh_project(ctx, reason="manual")
        assert result["registry"]["conflicts"] == {"Shared": ["one.py", "two.py"]}
        assert ctx.registry.for_stem("Shared") is None
        assert "Shared" not in json.dumps(_registry_file(figs)["scripts"])

    def test_conflicts_are_unknown_not_empty_when_no_static_scan_ran(self, client, tmp_path):
        """没扫过 ≠ 没有冲突。合并成 `{}` 的话，调用方会把「不知道」读成
        「已确认没有冲突」——同一形状 Session 03 已经踩过三次（T-12）。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        assert (
            m.refresh_project(ctx, reason="probe", allow_static_merge=False)["registry"][
                "conflicts"
            ]
            is None
        )
        assert m.refresh_project(ctx, reason="manual")["registry"]["conflicts"] == {}


# ---------------------------------------------------------------------------
# diff：素材
# ---------------------------------------------------------------------------
class TestAssetDiff:
    def test_added_changed_and_removed_assets(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")

        _raster(figs / "photo.png", width=8)
        result = m.refresh_project(ctx, reason="manual")
        assert result["assets"]["added"] == ["photo.png"]
        assert result["assets"]["baseline"] is False
        assert dict(sse_spy)["assets.changed"]["ids"] == ["photo.png"]

        _raster(figs / "photo.png", width=32)  # 尺寸也变，不依赖 mtime 精度
        assert m.refresh_project(ctx, reason="manual")["assets"]["changed"] == ["photo.png"]

        (figs / "photo.png").unlink()
        assert m.refresh_project(ctx, reason="manual")["assets"]["removed"] == ["photo.png"]

    def test_same_size_content_change_is_still_a_change(self, client, tmp_path):
        """签名是 (kind, size, mtime_ns) 三样。只比 size 的话，一次**等长**的
        重画（同尺寸位图换个颜色、脚本重跑产出同样大小的 PDF）在刷新眼里
        什么都没发生——而那正是用户最常做的那件事。"""
        figs = _project(tmp_path)
        _raster(figs / "same_size.png", width=16)
        ctx = _open(client, figs)
        before = (figs / "same_size.png").stat().st_size

        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
        pix.clear_with(40)  # 换个颜色，字节数一模一样
        pix.save(figs / "same_size.png")
        assert (figs / "same_size.png").stat().st_size == before, "这条用例要的是等长改动"

        assert m.refresh_project(ctx, reason="manual")["assets"]["changed"] == ["same_size.png"]

    def test_inventory_and_api_panels_agree_on_the_id_set(self, client, tmp_path):
        """**同一把尺**：refresh 报的素材必须正好是 `/api/panels` 列出来的那些。

        两份判据分叉的表现是"刷新说有一张新图、素材库里找不到"，或者反过来
        ——用户看得见的图改了却不刷新。
        """
        figs = _project(tmp_path)
        _pdf(figs / "FigA.pdf")
        _raster(figs / "FigA.png")  # 同名位图：有矢量版就不重复列出
        _raster(figs / "photo.jpg")
        _raster(figs / ".hidden.png")
        (figs / "notes.txt").write_text("not an asset", encoding="utf-8")
        (figs / "tavottofile").mkdir()
        _pdf(figs / "tavottofile" / "exported.pdf")  # 导出目录：剪掉，否则导一次多一堆
        _open(client, figs)

        panels = {p["id"] for p in client.get("/api/panels").get_json()["panels"]}
        assert panels == set(engine_refresh.asset_inventory(figs))
        assert panels == {"FigA.pdf", "photo.jpg"}


# ---------------------------------------------------------------------------
# 不执行用户脚本
# ---------------------------------------------------------------------------
class TestNeverRunsUserCode:
    def test_refresh_never_probes_and_never_executes_a_script(self, client, tmp_path, monkeypatch):
        """两条证据，缺一不可：

        * **磁盘上的证据**——脚本真跑起来会写下 `RAN.txt`；
        * **调用点的证据**——probe / worker 池的入口全部换成会炸的桩。

        只有后者的话，"没运行"证明的是"我们桩住的那几个入口没被调用"，
        绕开它们的第五条路照样能跑起脚本。
        """

        def boom(*_a, **_kw):
            raise AssertionError("刷新路径执行了用户脚本")

        monkeypatch.setattr(engine_probe, "probe_and_register", boom)
        monkeypatch.setattr(engine_pool, "get", boom)
        monkeypatch.setattr(engine_pool, "one_shot", boom)
        monkeypatch.setattr(engine_pool, "_new_worker", boom)

        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")
        assert client.post("/api/project/refresh", json={"reason": "manual"}).status_code == 200

        assert not (figs / "RAN.txt").exists(), "刷新把用户的脚本跑了一遍"
        assert ctx.registry.for_stem("FigA")["script"] == "fig_a.py"  # 静态发现照常成立


# ---------------------------------------------------------------------------
# HTTP 面
# ---------------------------------------------------------------------------
class TestEndpoint:
    def test_refresh_returns_the_structured_diff(self, client, tmp_path):
        figs = _project(tmp_path)
        _open(client, figs)
        # 打开项目会**起草**一份注册表：脚本要在那之后出现，才轮得到刷新去发现
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")

        body = client.post("/api/project/refresh", json={"reason": "manual"}).get_json()
        assert body["reason"] == "manual"
        assert body["registry"]["added_scripts"] == ["fig_a.py"]
        assert set(body["assets"]) == {"added", "removed", "changed", "baseline"}
        assert body["assets"]["added"] == ["FigA.pdf"]
        assert body["scripts"]["fig_a.py"]["stems"] == ["FigA"]

    def test_unknown_reasons_are_normalized_not_passed_through(self, client, tmp_path):
        """`reason` 进日志、进事件、以后还会进遥测维度：客户端传什么就记什么
        等于让外面往我们的指标里写自由文本。"""
        figs = _project(tmp_path)
        _open(client, figs)
        for raw in ("既不是枚举也不是英文", "", "MANUAL; DROP TABLE", 42, None):
            body = client.post("/api/project/refresh", json={"reason": raw}).get_json()
            assert body["reason"] == "manual", raw
        assert (
            client.post("/api/project/refresh", json={"reason": "codex"}).get_json()["reason"]
            == "codex"
        )

    def test_the_endpoint_ignores_client_supplied_paths(self, client, tmp_path):
        """`changed_paths` 只给进程内的调用方（watcher / MCP）。HTTP 上认它
        等于让客户端把一段绝对路径写进我们的日志与事件。"""
        figs = _project(tmp_path)
        (figs / "inside.py").write_text("x = 1\n", encoding="utf-8")
        _open(client, figs)
        body = client.post(
            "/api/project/refresh",
            json={
                "reason": "manual",
                # **项目内**那条是关键：项目外的路径本来就会被规整器丢掉，
                # 只拿它当判据的话，"端点认不认这个字段"根本没被量到
                # （实测：那条变异活下来了）。
                "changed_paths": [str(figs / "inside.py"), "/etc/passwd", str(tmp_path)],
            },
        ).get_json()
        assert body["changed_paths"] == []

    def test_changed_paths_are_normalized_to_project_relative(self, client, tmp_path):
        """进程内的调用方（watcher / MCP）给的路径要规整成项目相对；
        项目外的一律丢掉——它们既指不回任何素材，又会把别处的绝对路径带进
        事件与日志。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        (figs / "sub").mkdir()
        result = m.refresh_project(
            ctx,
            reason="watcher",
            changed_paths=[
                "sub/fig.py",
                str(figs / "top.py"),
                str(tmp_path / "outside.py"),  # 项目外
                "",
                None,  # type: ignore[list-item]
            ],
        )
        assert result["changed_paths"] == ["sub/fig.py", "top.py"]
        assert result["reason"] == "watcher"

    def test_refresh_needs_an_open_project(self, client):
        resp = client.post("/api/project/refresh", json={"reason": "manual"})
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "no_project"

    def test_refresh_is_project_isolated(self, client, tmp_path, sse_spy):
        """指名 B 就只刷 B：事件的 pj 是 B，A 的注册表一个字没动。"""
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        ctx_a = _open(client, a)
        ctx_b = _open(client, b, default=False)
        _script(b, "only_in_b.py", "FigB")
        _pdf(b / "FigB.pdf")
        sse_spy.clear()

        resp = client.post(
            "/api/project/refresh",
            json={"reason": "manual"},
            headers={"X-Tavotto-Project": ctx_b.id},
        )
        assert resp.status_code == 200
        assert resp.get_json()["registry"]["added_scripts"] == ["only_in_b.py"]
        assert {payload["pj"] for _, payload in sse_spy} == {ctx_b.id}
        assert ctx_a.registry.all_scripts() == []

    def test_scan_keeps_its_old_response_shape(self, client, tmp_path):
        """RegistryDialog 读的是 `changes.added_scripts.length`——换个形状
        等于让存量前端当场坏掉。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")

        body = client.post("/api/registry/scan").get_json()
        assert body["changes"]["added_scripts"] == ["fig_a.py"]
        assert isinstance(body["changes"]["added_stems"], dict)
        assert body["conflicts"] == {}
        assert body["scripts"] == ctx.registry.entries()
        # 新的结构化 diff 另给一处，不挤占旧字段
        assert body["refresh"]["registry"]["added_scripts"] == ["fig_a.py"]
        assert body["refresh"]["reason"] == "registry"

    def test_scan_failure_keeps_its_stable_code(self, client, tmp_path):
        figs = _project(tmp_path)
        _open(client, figs)
        (figs / "tavotto_registry.json").write_text('{"scripts": "不是一张表"}', encoding="utf-8")
        resp = client.post("/api/registry/scan")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "scan_failed"
        assert resp.get_json()["params"]["reason"]

    def test_refresh_failure_is_400_with_a_stable_code(self, client, tmp_path):
        figs = _project(tmp_path)
        _open(client, figs)
        (figs / "tavotto_registry.json").write_text("{不是 JSON", encoding="utf-8")
        resp = client.post("/api/project/refresh", json={"reason": "manual"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["code"] == "scan_failed"
        assert body["error"]  # 中文原文照旧作为回退

    def test_manual_registry_write_goes_through_the_unified_refresh(
        self, client, tmp_path, sse_spy
    ):
        """手工登记不再自己发事件：`reason` 就是它走了统一入口的证据。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        sse_spy.clear()

        resp = client.put(
            "/api/registry",
            json={"script": "manual.py", "stems": ["ManualStem"], "entry": "render"},
        )
        assert resp.status_code == 200
        (event, payload) = next((e, p) for e, p in sse_spy if e == "registry.changed")
        assert event == "registry.changed"
        assert payload["reason"] == "registry"
        assert payload["pj"] == ctx.id
        assert payload["script"] == "manual.py"  # 单脚本兼容字段照旧
        assert payload["stems"] == ["ManualStem"]
        assert payload["added_scripts"] == ["manual.py"]
        assert ctx.registry.entries()["manual.py"]["entry"] == "render"

    def test_probe_success_goes_through_the_unified_refresh(
        self, client, tmp_path, monkeypatch, sse_spy
    ):
        """probe 成功后不再自己 reload + 自己发事件。

        这里桩掉的是 probe **本身**（它要真跑用户脚本），不是刷新——要证明的
        是"登记完之后走的是哪条路"，而那条路的身份就是 `reason="probe"`。
        """
        figs = _project(tmp_path)
        (figs / "runner.py").write_text("print('hi')\n", encoding="utf-8")
        ctx = _open(client, figs)

        def fake_probe(figures_dir, script, cost="medium", should_cancel=None):
            engine_discover.register(figures_dir, script, ["Probed"], entry="main", cost=cost)
            return {"registered": True, "entry": "main", "stems": ["Probed"], "descriptors": []}

        monkeypatch.setattr(engine_probe, "probe_and_register", fake_probe)
        monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **kw: None)
        sse_spy.clear()

        resp = client.post("/api/registry/probe", json={"script": "runner.py"})
        assert resp.status_code == 200
        payload = dict(sse_spy)["registry.changed"]
        assert payload["reason"] == "probe"
        assert payload["script"] == "runner.py"
        assert payload["stems"] == ["Probed"]
        assert ctx.registry.for_stem("Probed")["script"] == "runner.py"


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------
class TestEvents:
    def test_a_batch_of_scripts_is_one_event_not_a_dozen(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        for i in range(4):
            _script(figs, f"fig_{i}.py", f"Fig{i}")
            _pdf(figs / f"Fig{i}.pdf")
        sse_spy.clear()

        m.refresh_project(ctx, reason="manual")
        registry_events = [p for e, p in sse_spy if e == "registry.changed"]
        assert len(registry_events) == 1
        payload = registry_events[0]
        assert payload["scripts"] == [f"fig_{i}.py" for i in range(4)]
        assert payload["added_scripts"] == payload["scripts"]
        assert "script" not in payload  # 批量不给单脚本字段，否则调用方只看得见一个
        assert sorted(payload["stems"]) == [f"Fig{i}" for i in range(4)]

    def test_asset_only_change_does_not_publish_a_registry_event(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")
        sse_spy.clear()

        _raster(figs / "photo.png")
        m.refresh_project(ctx, reason="manual")
        assert [e for e, _ in sse_spy] == ["assets.changed"]

    def test_publish_false_still_returns_the_diff(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        sse_spy.clear()

        result = m.refresh_project(ctx, reason="manual", publish=False)
        assert result["registry"]["added_scripts"] == ["fig_a.py"]
        assert result["published"] == []
        assert sse_spy == []


# ---------------------------------------------------------------------------
# worker 失效
# ---------------------------------------------------------------------------
class _FakeWorker:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


class TestWorkerInvalidation:
    def _plant(self, path, script):
        worker = _FakeWorker()
        key = (engine_pool._norm_dir(str(path)), script)
        with engine_pool._lock:
            engine_pool._workers[key] = worker
        return key, worker

    def test_only_the_refreshed_project_loses_its_worker(self, client, tmp_path):
        """两个项目里同名的 `shared.py`（到处都是的 fig1.py）：刷新 A 不该把
        B 正在用的会话打掉。"""
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        for figs in (a, b):
            _write_registry(
                figs, {"shared.py": {"entry": "main", "cost": "medium", "stems": ["S"]}}
            )
        ctx_a = _open(client, a)
        _open(client, b, default=False)
        key_a, _ = self._plant(a, "shared.py")
        key_b, _ = self._plant(b, "shared.py")
        try:
            _write_registry(a, {"shared.py": {"entry": "render", "cost": "medium", "stems": ["S"]}})
            m.refresh_project(ctx_a, reason="external", allow_static_merge=False)

            with engine_pool._lock:
                assert key_a not in engine_pool._workers, "A 的过期会话没被作废"
                assert key_b in engine_pool._workers, "B 的会话被别的项目的刷新打掉了"
        finally:
            with engine_pool._lock:
                engine_pool._workers.pop(key_a, None)
                engine_pool._workers.pop(key_b, None)

    def test_an_unrelated_new_image_invalidates_nothing(self, client, tmp_path):
        """新增一张不相干的图片就作废全部 worker = 用户平白等一次冷启动。"""
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S"]}})
        ctx = _open(client, figs)
        key, _ = self._plant(figs, "a.py")
        try:
            _raster(figs / "unrelated.png")
            result = m.refresh_project(ctx, reason="manual")
            assert result["assets"]["added"] == ["unrelated.png"]
            with engine_pool._lock:
                assert key in engine_pool._workers
        finally:
            with engine_pool._lock:
                engine_pool._workers.pop(key, None)


# ---------------------------------------------------------------------------
# 并发与失败
# ---------------------------------------------------------------------------
class TestConcurrencyAndFailure:
    def test_two_projects_refresh_in_parallel(self, client, tmp_path, monkeypatch):
        """锁是**每项目一把**。一把全局大锁的表现是：另一个标签页刷新时，
        我这边的刷新一直转圈。

        判据从**里面**卡住 A：让 A 的刷新停在自己的临界区，然后要求 B 刷完。
        换成"在测试里拿住 A 的那把锁"就假设了被测代码用的正是那把——换成
        全局锁之后测试照样绿（实测：那条变异活下来了）。**判据不能预设它
        要证明的那件事。**
        """
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        ctx_a = _open(client, a)
        ctx_b = _open(client, b, default=False)

        inside_a, b_done, a_done = threading.Event(), threading.Event(), threading.Event()
        real_merge = engine_discover.merge

        def blocking_merge(path):
            if Path(path) == Path(a):
                inside_a.set()
                b_done.wait(10)  # A 停在临界区里不出来
            return real_merge(path)

        monkeypatch.setattr(engine_refresh.discover, "merge", blocking_merge)

        def refresh_a():
            m.refresh_project(ctx_a, reason="manual")
            a_done.set()

        holder = threading.Thread(target=refresh_a, daemon=True)
        holder.start()
        assert inside_a.wait(5), "A 的刷新没能进到临界区"
        try:
            m.refresh_project(ctx_b, reason="manual")
            # 关键在这一句：B 刷完的**那一刻**，A 还停在自己的临界区里。
            # 只断言"B 最终回来了"是不够的——A 的等待迟早会超时放行，于是
            # 全局大锁下 B 也会回来，只是慢了十秒（实测：那条变异活了下来）。
            assert not a_done.is_set(), "B 只能等 A 出来 = 全局大锁"
        finally:
            b_done.set()
            holder.join(10)
        assert a_done.wait(5), "A 的刷新没能收尾"

    def test_the_same_project_serializes_and_the_registry_survives(self, client, tmp_path):
        """同一项目的刷新必须串行。

        判据不靠 sleep：临界区里放一个两人的 barrier，**真并行**时两个线程
        会当场碰面，串行时谁都等不到对方。
        """
        figs = _project(tmp_path)
        for i in range(3):
            _script(figs, f"fig_{i}.py", f"Fig{i}")
            _pdf(figs / f"Fig{i}.pdf")
        ctx = _open(client, figs)

        barrier = threading.Barrier(2)
        met: list[bool] = []
        real_merge = engine_discover.merge

        def instrumented(path):
            try:
                barrier.wait(timeout=0.5)
                met.append(True)
            except threading.BrokenBarrierError:
                met.append(False)
            return real_merge(path)

        engine_refresh.discover.merge = instrumented
        try:
            threads = [
                threading.Thread(target=lambda: m.refresh_project(ctx, reason="manual"))
                for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(15)
        finally:
            engine_refresh.discover.merge = real_merge

        assert met and not any(met), "两次刷新在临界区里碰面了 = 没串行"
        # 注册表既没坏也没丢东西
        assert sorted(ctx.registry.all_scripts()) == [f"fig_{i}.py" for i in range(3)]
        assert sorted(_registry_file(figs)["scripts"]) == [f"fig_{i}.py" for i in range(3)]

    def test_a_failed_refresh_keeps_the_registry_it_had(self, client, tmp_path, sse_spy):
        """刷新失败 ≠ 注册表空了。已打开的项目照常能用，事件一条不发。"""
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S"]}})
        ctx = _open(client, figs)
        before = ctx.registry.entries()
        sse_spy.clear()

        (figs / "tavotto_registry.json").write_text('{"scripts": {"b.py": 42}}', encoding="utf-8")
        with pytest.raises(engine_refresh.RefreshError) as exc:
            m.refresh_project(ctx, reason="external", allow_static_merge=False)

        assert exc.value.code == "registry_reload_failed"
        assert exc.value.params["reason"]
        assert ctx.registry.entries() == before
        assert ctx.registry.for_stem("S")["script"] == "a.py"
        assert sse_spy == []

    def test_a_missing_registry_file_does_not_empty_the_one_in_memory(self, client, tmp_path):
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S"]}})
        ctx = _open(client, figs)
        (figs / "tavotto_registry.json").unlink()

        with pytest.raises(engine_refresh.RefreshError) as exc:
            m.refresh_project(ctx, reason="external", allow_static_merge=False)
        assert exc.value.code == "registry_reload_failed"
        assert ctx.registry.all_scripts() == ["a.py"]


# ---------------------------------------------------------------------------
# 给 Prompt 05 的 watcher 留的两样东西
# ---------------------------------------------------------------------------
class TestWatcherHandoff:
    def test_a_refresh_that_writes_nothing_leaves_the_file_untouched(self, client, tmp_path):
        """无变化的刷新**不回写**注册表。

        以前 `/api/registry/scan` 无条件重写：文件内容一样、mtime 变了，而
        mtime 一变，项目 watcher 就会看到一次"外部修改"，于是刷新自己触发
        下一次刷新。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")

        path = figs / "tavotto_registry.json"
        before = path.stat().st_mtime_ns
        m.refresh_project(ctx, reason="manual")
        assert path.stat().st_mtime_ns == before

    def test_self_written_registry_is_recognizable_by_content(self, client, tmp_path):
        """watcher 认自己写的那一下靠**内容修订号**，不靠"写完忽略两秒"：
        时间窗口在慢磁盘上不够，在快机器上又会吞掉用户真实的外部修改。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _open(client, figs)
        m.refresh_project(ctx, reason="manual")

        assert engine_refresh.is_self_written(ctx) is True
        cfg = _registry_file(figs)
        cfg["scripts"]["fig_a.py"]["notes"] = "用户在编辑器外改的"
        (figs / "tavotto_registry.json").write_text(json.dumps(cfg), encoding="utf-8")
        assert engine_refresh.is_self_written(ctx) is False

    def test_refresh_no_longer_re_arms_a_watcher(self, client, tmp_path):
        """刷新**不再有** watcher 重挂这个动作。

        老的脚本 watcher 按注册表里那张清单逐个盯 mtime，清单一变就得重挂；
        项目 watcher（`engine/project_watch.py`）盯的是整棵树，没有"盯谁"
        这个状态。这条量的是那个钩子确实没了——留着一个没人调的形状，下一个
        人会以为它还在守什么。

        它换来的能力（**新建**一个还没登记的脚本也能被发现）在
        `tests/test_project_watch.py` 里量，那正是重挂机制永远做不到的事。
        """
        assert not hasattr(engine_refresh.RefreshSink(), "watch")
        figs = _project(tmp_path)
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "medium", "stems": ["S"]}})
        ctx = _open(client, figs)

        before = engine_watch.watched_dirs()
        _write_registry(
            figs,
            {
                "a.py": {"entry": "main", "cost": "medium", "stems": ["S"]},
                "b.py": {"entry": "main", "cost": "medium", "stems": ["T"]},
            },
        )
        m.refresh_project(ctx, reason="external", allow_static_merge=False)
        # 项目 watcher 是打开项目时挂上的那一个，刷新不换、也不添第二个
        assert engine_watch.watched_dirs() == before
        assert len(engine_watch.watched_dirs()) == 1


def test_registry_snapshot_is_not_a_live_view(tmp_path):
    """快照要在装载之后仍然代表**刷新前**的事实。共享同一个 list 的话，
    diff 永远是空的——而"永远是空的"看起来和"什么都没变"一模一样。

    两个维度都要量：

    1. `load_data()` 之后——今天它把每个容器都重建，所以浅拷贝碰巧也够；
    2. **原地改同一个 list** 之后——这一维今天没有代码会走到，量的是
       "哪天 Registry 多一个原地写入的方法时，快照仍然是快照"。

    只量第一维的话，把 `registry_snapshot()` 换成 `reg.entries()` 照样绿
    （实测：那条变异活下来了）——判据量到的是 `Registry` 的实现细节，
    不是这个函数的承诺。
    """
    reg = engine_registry.Registry()
    reg.load_data({"scripts": {"a.py": {"entry": "main", "cost": "light", "stems": ["S1"]}}})

    before = engine_refresh.registry_snapshot(reg)
    reg.load_data({"scripts": {"a.py": {"entry": "main", "cost": "light", "stems": ["S1", "S2"]}}})
    assert before["a.py"]["stems"] == ["S1"]
    assert engine_refresh.diff_registry(before, engine_refresh.registry_snapshot(reg))[
        "added_stems"
    ] == ["S2"]

    # 第二维：直接改注册表自己那个 list（模拟将来的原地写入）
    snapshot = engine_refresh.registry_snapshot(reg)
    reg.entries()["a.py"]["stems"].append("S3")
    assert snapshot["a.py"]["stems"] == ["S1", "S2"], "快照跟着注册表一起变了 = 它是活视图"
