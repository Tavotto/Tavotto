"""项目接入就绪度（Prompt 07 / `engine/readiness.py` + `/api/project/readiness`）。

守五件事：

1. **六个状态互斥且唯一**——每张图恰好落在一个上，summary 加起来等于图数；
2. **reason code 是闭集**，且与状态的组合在 `REASONS_BY_STATUS` 里备了案；
3. **只诊断**——不跑用户脚本、不 probe、不起 worker、不写盘、不改注册表；
4. **fingerprint 是报告的内容哈希**——同一份事实下不变，事实一变就变，
   `generated_at` 与无关文件进不来；
5. **capability 与就绪度同源**，`/api/panels` 的老字段一个不动。
"""

from __future__ import annotations

import json
import os
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
    readiness as engine_readiness,
    registry as engine_registry,
)

# 静态可识别的绘图脚本。**同时是一枚运行探针**：真跑起来会写下 `RAN.txt`，
# 于是"就绪度没有执行用户脚本"这条不靠桩的存在与否来证明，而是靠磁盘。
STATIC = """\
from pathlib import Path


def main():
    Path("RAN.txt").write_text("executed", encoding="utf-8")
    fig.savefig("{stem}.pdf")
"""

# 有存图调用，但文件名来自运行期数据——静态永远解不出，只有试运行能确认。
DYNAMIC = """\
import sys
from pathlib import Path


def main():
    Path("RAN.txt").write_text("executed", encoding="utf-8")
    fig.savefig(sys.argv[1] + ".pdf")
"""


class _Ctx:
    """刷新与就绪度都只要这三样：`path` / `id` / `registry`。

    引擎侧的用例用它，不经 Flask——判定表本身与 HTTP、认证、watcher 无关，
    把它们拖进来只会让一条分类用例可能因为别的原因红。
    """

    def __init__(self, path: Path, pid: str = "pj-test") -> None:
        self.path, self.id = Path(path), pid
        self.registry = engine_registry.Registry()
        try:
            self.registry.load(path)
        except (FileNotFoundError, RuntimeError):
            pass


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(path)
    doc.close()


def _raster(path: Path, width: int = 8) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, width), False)
    pix.clear_with(200)
    pix.save(path)


def _project(tmp_path, name: str = "figs") -> Path:
    figs = tmp_path / name
    figs.mkdir()
    return figs


def _script(figs: Path, name: str, stem: str = "", body: str = STATIC) -> None:
    path = figs / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.format(stem=stem), encoding="utf-8")


def _write_registry(figs: Path, scripts: dict) -> None:
    engine_discover.write_config(figs, {"version": 1, "scripts": scripts})


def _open(client, figs, default=True):
    body = client.post(
        "/api/projects/open", json={"path": str(figs), "default": default}
    ).get_json()
    return m.PROJECTS[body["id"]]


def _by_id(body: dict) -> dict[str, dict]:
    return {p["id"]: p for p in body["panels"]}


def _status(body: dict, panel_id: str) -> tuple[str, str]:
    p = _by_id(body)[panel_id]
    return p["status"], p["reason_code"]


# ---------------------------------------------------------------------------
# 六个状态的判定
# ---------------------------------------------------------------------------
class TestClassification:
    def test_registered_script_on_disk_is_editable(self, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})

        body = engine_readiness.compute(_Ctx(figs))
        panel = _by_id(body)["FigA.pdf"]
        assert (panel["status"], panel["reason_code"]) == ("editable", "registered_source")
        assert panel["script"] == "fig_a.py"
        # 已经绑定了，没有"待确认的候选"——试运行不是这张图的下一步
        assert panel["candidates"] == []
        assert panel["can_probe"] is False
        assert panel["details"] == {"entry": "main", "cost": "light"}

    def test_a_unique_static_candidate_that_is_not_registered_yet_is_auto_linkable(self, tmp_path):
        """静态已经能唯一确定脚本，只是还没写进注册表。

        可写项目里这一档是**过渡态**（下一次统一刷新就会变成 editable），
        但它必须存在：只读项目和写失败的项目会**长期**停在这里，而那时
        报 `layout_only` 等于告诉用户"这张图没有源脚本"——那是错的。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})

        body = engine_readiness.compute(_Ctx(figs))
        panel = _by_id(body)["FigA.pdf"]
        assert (panel["status"], panel["reason_code"]) == (
            "auto_linkable",
            "static_unique_candidate",
        )
        assert panel["candidates"] == ["fig_a.py"]
        assert panel["script"] is None  # 还没绑定，**不能**拿候选冒充已连接
        assert panel["can_probe"] is True
        assert panel["details"]["candidate_scope"] == "panel"

    def test_dynamic_output_names_need_a_probe(self, tmp_path):
        """有产图脚本，但输出文件名只有跑起来才知道。

        候选是**项目级**的（`candidate_scope: project`）：静态解不出这些脚本
        的产物，所以说不出"这张图来自其中哪一个"，只能说"跑一个就知道"。
        """
        figs = _project(tmp_path)
        _script(figs, "dyn.py", body=DYNAMIC)
        _pdf(figs / "Mystery.pdf")
        _write_registry(figs, {})

        body = engine_readiness.compute(_Ctx(figs))
        panel = _by_id(body)["Mystery.pdf"]
        assert (panel["status"], panel["reason_code"]) == ("needs_probe", "runtime_output_unknown")
        assert panel["candidates"] == ["dyn.py"]
        assert panel["can_probe"] is True
        assert panel["details"]["candidate_scope"] == "project"

    def test_two_scripts_claiming_one_stem_is_a_conflict_and_is_never_auto_resolved(self, tmp_path):
        """**绝不按文件名相似度、修改时间或任何猜测替用户选一个。**"""
        figs = _project(tmp_path)
        _script(figs, "old_version.py", "Dup")
        _script(figs, "z_newer.py", "Dup")
        # 一个明显"更新"、名字也更像的候选：机器仍然不许挑
        os.utime(figs / "z_newer.py", (10_000_000, 10_000_000))
        _pdf(figs / "Dup.pdf")
        _write_registry(figs, {})

        body = engine_readiness.compute(_Ctx(figs))
        panel = _by_id(body)["Dup.pdf"]
        assert (panel["status"], panel["reason_code"]) == ("conflict", "multiple_source_candidates")
        assert panel["candidates"] == ["old_version.py", "z_newer.py"]
        assert panel["script"] is None
        assert body["conflicts"] == [
            {"stem": "Dup", "candidates": ["old_version.py", "z_newer.py"], "resolved_by": None}
        ]

    def test_a_registered_script_that_vanished_is_source_missing_not_a_broken_file(self, tmp_path):
        """图片还在，脚本没了：**仍然能排版**，不是"文件损坏"。"""
        figs = _project(tmp_path)
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {"gone.py": {"entry": "render", "cost": "heavy", "stems": ["FigA"]}})

        body = engine_readiness.compute(_Ctx(figs))
        panel = _by_id(body)["FigA.pdf"]
        assert (panel["status"], panel["reason_code"]) == (
            "source_missing",
            "registered_script_missing",
        )
        # 注册表指着谁要说出来，否则用户只看到一句"它不见了"
        assert panel["script"] == "gone.py"
        assert panel["candidates"] == []
        assert panel["details"] == {"entry": "render", "cost": "heavy"}

    def test_source_missing_offers_a_new_claimant_as_a_candidate(self, tmp_path):
        """脚本被改名/重构：注册表指着的那份没了，另一份此刻正好认领同一个
        stem。状态照旧是 source_missing（注册表说的还是那个不存在的文件），
        但用户手里要有一条可执行的出路。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a_renamed.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {"gone.py": {"entry": "main", "cost": "medium", "stems": ["FigA"]}})

        panel = _by_id(engine_readiness.compute(_Ctx(figs)))["FigA.pdf"]
        assert panel["status"] == "source_missing"
        assert panel["candidates"] == ["fig_a_renamed.py"]
        assert panel["can_probe"] is True

    def test_a_plain_image_with_no_candidate_is_layout_only(self, tmp_path):
        figs = _project(tmp_path)
        _raster(figs / "photo.jpg")
        _write_registry(figs, {})

        panel = _by_id(engine_readiness.compute(_Ctx(figs)))["photo.jpg"]
        assert (panel["status"], panel["reason_code"]) == ("layout_only", "no_source_candidate")
        assert panel["candidates"] == []
        assert panel["can_probe"] is False
        # 只排版不等于不能用：缩放/裁剪/对齐/标注/导出照旧，就绪度不参与那些
        assert panel["can_manual_link"] is True

    def test_a_read_only_project_is_a_state_not_a_failure(self, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})
        ctx = _Ctx(figs)
        # **同一个 ctx 上翻转**：可写性是缓存键的一维，先热一遍才量得到它。
        assert _status(engine_readiness.compute(ctx), "FigA.pdf")[1] == "static_unique_candidate"
        os.chmod(figs, 0o555)
        try:
            body = engine_readiness.compute(ctx)
        finally:
            os.chmod(figs, 0o755)

        panel = _by_id(body)["FigA.pdf"]
        # 候选照旧看得见——看不见的是"我们能替你登记上"
        assert (panel["status"], panel["reason_code"]) == ("auto_linkable", "project_read_only")
        assert panel["candidates"] == ["fig_a.py"]
        assert panel["can_manual_link"] is False  # 手工登记要落盘，落不了
        assert body["project"]["writable"] is False
        assert {i["code"] for i in body["issues"]} == {"project_read_only"}

    def test_an_invalid_registry_on_disk_does_not_lose_the_assets(self, tmp_path):
        """注册表被手改坏：**素材一张都不少**，只是解释不出关系。

        内存里那份仍然是合法的（`Registry.load_data` 校验通过前不碰自己的
        字段），所以已登记的图照旧 editable——就绪度报告的是"磁盘上那份坏了"，
        不是"你的项目没了"。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "fig_b.py", "FigB")
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "FigB.pdf")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})
        ctx = _Ctx(figs)
        # **先热一遍缓存**：坏掉发生在第一次读之后，才量得到"缓存跟不跟得上
        # 合法性的变化"。上来就改坏的话，缓存键含不含这一维都是同一个结果。
        assert _status(engine_readiness.compute(ctx), "FigB.pdf")[1] == "static_unique_candidate"
        (figs / "tavotto_registry.json").write_text("{ 这不是 JSON", encoding="utf-8")

        body = engine_readiness.compute(ctx)
        assert body["summary"]["total"] == 2
        assert _status(body, "FigA.pdf") == ("editable", "registered_source")
        # 没登记的那张：候选还在，但"登记不上"的成因是注册表本身坏了
        assert _status(body, "FigB.pdf") == ("auto_linkable", "registry_invalid")
        assert body["project"]["registry_valid"] is False
        assert {i["code"] for i in body["issues"]} == {"registry_invalid"}

    def test_a_failed_registry_write_is_told_apart_from_not_written_yet(self, tmp_path):
        """「还没登记」有两种成因：下一次刷新就好了，和刷多少次都一样。

        合并成一个 reason code 的话，用户会一直点刷新，而每一次都失败。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})
        ctx = _Ctx(figs)
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "auto_linkable",
            "static_unique_candidate",
        )

        engine_refresh.state_of(ctx).registry_write_failed = True
        engine_readiness.invalidate(ctx)
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "auto_linkable",
            "registry_write_failed",
        )

    def test_a_write_failure_during_refresh_is_recorded_and_then_cleared(self, tmp_path):
        """`registry_write_failed` 不是测试自己置的旗——真的走一遍失败的刷新。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})
        ctx = _Ctx(figs)
        os.chmod(figs, 0o555)
        try:
            with pytest.raises(engine_refresh.RefreshError) as exc:
                engine_refresh.refresh_project_index(ctx, reason="manual")
            # 对外的 code 不能改（老 `/api/registry/scan` 的契约）
            assert exc.value.code == "scan_failed"
            assert engine_refresh.state_of(ctx).registry_write_failed is True
        finally:
            os.chmod(figs, 0o755)

        engine_refresh.refresh_project_index(ctx, reason="manual")
        assert engine_refresh.state_of(ctx).registry_write_failed is False
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "editable",
            "registered_source",
        )

    def test_a_structurally_invalid_registry_counts_as_invalid(self, tmp_path):
        """ "JSON 解得动"不等于"这是一份注册表"。

        `entry` 不是合法标识符、`stems` 不是字符串列表、同一个 stem 登记两遍
        ——三种都能 `json.loads` 成功，而 `Registry.load_data()` 会拒。少了这
        一层，一份结构坏掉的注册表会被报成"合法"，用户看到的现象是"它说没
        问题，可就是打不开"。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})
        ctx = _Ctx(figs)
        assert engine_readiness.compute(ctx)["project"]["registry_valid"] is True

        (figs / "tavotto_registry.json").write_text(
            json.dumps(
                {"version": 1, "scripts": {"fig_a.py": {"entry": "不是 标识符", "stems": []}}}
            ),
            encoding="utf-8",
        )
        body = engine_readiness.compute(ctx)
        assert body["project"]["registry_valid"] is False
        assert {i["code"] for i in body["issues"]} == {"registry_invalid"}

    def test_a_legacy_registry_file_still_counts(self, tmp_path):
        """`mm_registry.json`（改名前的名字）：读取端唯一的兼容点。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        (figs / "mm_registry.json").write_text(
            json.dumps(
                {"version": 1, "scripts": {"fig_a.py": {"entry": "main", "stems": ["FigA"]}}}
            ),
            encoding="utf-8",
        )

        body = engine_readiness.compute(_Ctx(figs))
        assert _status(body, "FigA.pdf") == ("editable", "registered_source")
        assert body["project"]["registry_valid"] is True

    def test_a_project_with_no_registry_file_at_all(self, tmp_path):
        """`registry_valid` 的第三个取值：`null` = 根本没有这个文件。

        与"有、但坏了"合并成一个 false 的话，界面会对着一个还没起草过的
        新项目喊"你的注册表损坏了"。
        """
        figs = _project(tmp_path)
        _raster(figs / "photo.png")

        body = engine_readiness.compute(_Ctx(figs))
        assert body["project"]["registry_valid"] is None
        assert body["issues"] == []
        assert _status(body, "photo.png") == ("layout_only", "no_source_candidate")

    def test_scripts_in_subdirectories(self, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "panels/fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})

        panel = _by_id(engine_readiness.compute(_Ctx(figs)))["FigA.pdf"]
        # 注册表键的写法（POSIX 相对路径）是唯一的一种，候选也用它
        assert panel["candidates"] == ["panels/fig_a.py"]

    def test_one_script_many_stems(self, tmp_path):
        figs = _project(tmp_path)
        (figs / "multi.py").write_text(
            "def main():\n    fig.savefig('FigA.pdf')\n    fig.savefig('FigB.pdf')\n",
            encoding="utf-8",
        )
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "FigB.pdf")
        _write_registry(
            figs, {"multi.py": {"entry": "main", "cost": "medium", "stems": ["FigA", "FigB"]}}
        )

        body = engine_readiness.compute(_Ctx(figs))
        assert _status(body, "FigA.pdf") == ("editable", "registered_source")
        assert _status(body, "FigB.pdf") == ("editable", "registered_source")
        assert body["summary"]["editable"] == 2

    def test_the_same_stem_in_two_formats_counts_twice_and_shares_one_source(self, tmp_path):
        """同 stem 的 PDF 与 PNG 分处两个目录：素材层视作两个 panel，
        **各计一次数**，但来源关系是同一条。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        (figs / "raster").mkdir()
        _raster(figs / "raster" / "FigA.png")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})

        body = engine_readiness.compute(_Ctx(figs))
        pdf_panel = _by_id(body)["FigA.pdf"]
        png_panel = _by_id(body)[str(Path("raster/FigA.png"))]
        assert pdf_panel["status"] == png_panel["status"] == "editable"
        assert pdf_panel["script"] == png_panel["script"] == "fig_a.py"
        assert body["summary"]["editable"] == 2
        assert body["summary"]["total"] == 2

    def test_the_registry_wins_over_a_static_conflict(self, tmp_path):
        """人工裁决记在注册表文件里（"勿改"）。静态报告仍然看得见那两个
        声称者，但它**不许推翻**裁决——否则每刷新一次就把用户的决定掀一遍。"""
        figs = _project(tmp_path)
        _script(figs, "a.py", "Dup")
        _script(figs, "b.py", "Dup")
        _pdf(figs / "Dup.pdf")
        _write_registry(figs, {"a.py": {"entry": "main", "cost": "light", "stems": ["Dup"]}})

        body = engine_readiness.compute(_Ctx(figs))
        assert _status(body, "Dup.pdf") == ("editable", "registered_source")
        # 冲突本身照旧报出来，附上"谁裁决了它"
        assert body["conflicts"] == [
            {"stem": "Dup", "candidates": ["a.py", "b.py"], "resolved_by": "a.py"}
        ]


# ---------------------------------------------------------------------------
# 枚举的完整性
# ---------------------------------------------------------------------------
class TestEnums:
    def test_every_reported_combination_is_declared(self, tmp_path):
        """状态 × reason 的组合必须在 `REASONS_BY_STATUS` 里备过案。

        判定分支以后还会长，这条挡的是"悄悄冒出一个前端没见过的组合"——
        那时后端全绿，而用户看到的是一串英文 key。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")  # editable
        _script(figs, "fig_b.py", "FigB")  # auto_linkable
        _script(figs, "d1.py", "Dup")
        _script(figs, "d2.py", "Dup")  # conflict
        _script(figs, "dyn.py", body=DYNAMIC)  # needs_probe
        for stem in ("FigA", "FigB", "Dup", "Mystery", "Orphan"):
            _pdf(figs / f"{stem}.pdf")
        _raster(figs / "photo.jpg")
        _write_registry(
            figs,
            {
                "fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]},
                "gone.py": {"entry": "main", "cost": "light", "stems": ["Orphan"]},
            },
        )

        body = engine_readiness.compute(_Ctx(figs))
        seen = {(p["status"], p["reason_code"]) for p in body["panels"]}
        # 一次就把六个状态全覆盖到（photo.jpg 与 Mystery.pdf 都没有专属候选，
        # 但项目里有 dyn.py，所以它们是 needs_probe 而不是 layout_only）
        assert {s for s, _ in seen} == {
            "editable",
            "auto_linkable",
            "needs_probe",
            "conflict",
            "source_missing",
        }
        for status, reason in seen:
            assert status in engine_readiness.STATUSES
            assert reason in engine_readiness.REASONS_BY_STATUS[status], (status, reason)

    def test_the_declared_table_has_no_dead_rows(self):
        """备案表里不许有永远不会出现的行——它会让前端为一个不存在的状态
        写一份文案，而没有任何东西会告诉他们那份是白写的。"""
        assert set(engine_readiness.REASONS_BY_STATUS) == set(engine_readiness.STATUSES)
        declared = [r for rs in engine_readiness.REASONS_BY_STATUS.values() for r in rs]
        assert len(declared) == len(set(declared)), "同一个 reason 挂在两个状态下"


# ---------------------------------------------------------------------------
# 安全：只诊断
# ---------------------------------------------------------------------------
class TestNeverRunsUserCode:
    def test_readiness_never_probes_and_never_executes_a_script(
        self, client, tmp_path, monkeypatch
    ):
        """两条证据，缺一不可：

        * **磁盘上的证据**——脚本真跑起来会写下 `RAN.txt`；
        * **调用点的证据**——probe 与 worker 池的入口全部换成会炸的桩。

        只有后者的话，"没运行"证明的是"我们桩住的那几个入口没被调用"。
        """

        def boom(*_a, **_kw):
            raise AssertionError("就绪度执行了用户脚本")

        monkeypatch.setattr(engine_probe, "probe_and_register", boom)
        monkeypatch.setattr(engine_probe, "probe_script", boom, raising=False)
        monkeypatch.setattr(engine_pool, "get", boom)
        monkeypatch.setattr(engine_pool, "one_shot", boom)
        monkeypatch.setattr(engine_pool, "_new_worker", boom)

        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "dyn.py", body=DYNAMIC)
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "Mystery.pdf")
        ctx = _open(client, figs)

        assert client.get("/api/project/readiness").status_code == 200
        engine_readiness.compute(ctx)
        assert client.get("/api/panels").status_code == 200

        assert not (figs / "RAN.txt").exists(), "就绪度把用户的脚本跑了一遍"

    def test_readiness_does_not_touch_the_registry_on_disk_or_in_memory(self, client, tmp_path):
        """只读诊断：磁盘上的字节与内存里那份索引都必须一模一样。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "fig_new.py", "FigNew")  # 静态可发现、故意不登记
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "FigNew.pdf")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})
        ctx = _Ctx(figs)
        before_bytes = (figs / "tavotto_registry.json").read_bytes()
        before_entries = ctx.registry.entries()

        body = engine_readiness.compute(ctx)
        assert _status(body, "FigNew.pdf") == ("auto_linkable", "static_unique_candidate")
        assert (figs / "tavotto_registry.json").read_bytes() == before_bytes
        assert ctx.registry.entries() == before_entries

    def test_the_endpoint_does_not_write_anything_to_the_project(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _open(client, figs)
        before = {p.name: p.stat().st_mtime_ns for p in figs.iterdir()}

        assert client.get("/api/project/readiness").status_code == 200
        assert {p.name: p.stat().st_mtime_ns for p in figs.iterdir()} == before

    def test_no_absolute_path_and_no_file_content_leaks_into_the_report(self, client, tmp_path):
        """路径是本机信息，图内文字与科研数据更是。整份报告序列化之后
        **一次都不许出现**项目的绝对路径或脚本源码。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "dyn.py", body=DYNAMIC)
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "Mystery.pdf")
        ctx = _open(client, figs)
        # 注册表在项目**打开之后**才被改坏——打开时就坏掉的项目根本打不开
        # （`open_project` 抛 RuntimeError → open_project_failed）。
        (figs / "tavotto_registry.json").write_text("{ 坏掉的注册表", encoding="utf-8")

        raw = json.dumps(client.get("/api/project/readiness").get_json(), ensure_ascii=False)
        assert str(figs) not in raw
        assert str(figs.resolve()) not in raw
        assert "savefig" not in raw  # 脚本内容一个字都不带
        assert "坏掉的注册表" not in raw  # 注册表原文同理
        assert ctx.registry.loaded()  # 内存里那份照旧能用


# ---------------------------------------------------------------------------
# summary / 排序 / fingerprint
# ---------------------------------------------------------------------------
class TestSummaryAndFingerprint:
    def test_every_panel_counts_exactly_once(self, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "d1.py", "Dup")
        _script(figs, "d2.py", "Dup")
        for stem in ("FigA", "Dup", "Orphan"):
            _pdf(figs / f"{stem}.pdf")
        _raster(figs / "photo.jpg")
        _write_registry(
            figs,
            {
                "fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]},
                "gone.py": {"entry": "main", "cost": "light", "stems": ["Orphan"]},
            },
        )

        body = engine_readiness.compute(_Ctx(figs))
        summary = body["summary"]
        assert set(summary) == {"total", *engine_readiness.STATUSES}
        assert summary["total"] == len(body["panels"])
        assert sum(summary[s] for s in engine_readiness.STATUSES) == summary["total"]

    def test_an_empty_project_is_all_zeros(self, tmp_path):
        figs = _project(tmp_path)
        body = engine_readiness.compute(_Ctx(figs))
        assert body["panels"] == []
        assert body["summary"] == {"total": 0, **{s: 0 for s in engine_readiness.STATUSES}}
        assert body["conflicts"] == []  # 扫过了、确实没有——与"没扫"的 None 不同

    def test_panel_order_is_stable(self, tmp_path):
        """排序按 **id 字符串**，不是"素材遍历碰巧给的那个顺序"。

        `a.pdf` 与 `a/z.pdf` 这一对刚好把两种顺序分开：`iter_assets()` 排的是
        `Path`（按路径分量比，`"a" < "a.pdf"` → 目录里那份在前），而 id 是
        字符串（`"a.pdf" < "a/z.pdf"`，因为 `.` 小于 `/`）。少了这一对，
        「按 id 排序」这句话在任何输入上都恒等成立，用例就量不到它。
        """
        figs = _project(tmp_path)
        for name in ("c", "a", "b"):
            _pdf(figs / f"{name}.pdf")
        (figs / "a").mkdir()
        _pdf(figs / "a" / "z.pdf")
        (figs / "sub").mkdir()
        _pdf(figs / "sub" / "a.pdf")
        ctx = _Ctx(figs)

        ids = [p["id"] for p in engine_readiness.compute(ctx)["panels"]]
        assert ids == sorted(ids)
        assert ids[:2] == ["a.pdf", str(Path("a") / "z.pdf")]
        engine_readiness.invalidate(ctx)
        assert [p["id"] for p in engine_readiness.compute(ctx)["panels"]] == ids

    def test_the_same_facts_give_the_same_fingerprint(self, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)

        first = engine_readiness.compute(ctx)
        engine_readiness.invalidate(ctx)  # 连缓存都不给它，重算一遍
        second = engine_readiness.compute(ctx)
        assert first["fingerprint"] == second["fingerprint"]

    def test_generated_at_is_not_part_of_the_fingerprint(self, client, tmp_path):
        """两次请求的 `generated_at` 必然不同；fingerprint 必须一样，
        否则前端每一次轮询都会以为项目变了。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _open(client, figs)

        a = client.get("/api/project/readiness").get_json()
        b = client.get("/api/project/readiness").get_json()
        assert a["fingerprint"] == b["fingerprint"]
        assert "generated_at" in a and "generated_at" in b
        # fingerprint 是"报告去掉时间戳之后"的哈希：自己算一遍对上
        assert (
            engine_readiness.fingerprint(
                {k: v for k, v in a.items() if k not in ("generated_at", "fingerprint")}
            )
            == (a["fingerprint"])
        )

    def test_a_status_change_changes_the_fingerprint(self, tmp_path):
        figs = _project(tmp_path)
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        before = engine_readiness.compute(ctx)
        assert _status(before, "FigA.pdf") == ("layout_only", "no_source_candidate")

        _script(figs, "fig_a.py", "FigA")  # 现在有唯一候选了
        after = engine_readiness.compute(ctx)
        assert _status(after, "FigA.pdf") == ("auto_linkable", "static_unique_candidate")
        assert after["fingerprint"] != before["fingerprint"]

    def test_an_unrelated_file_does_not_move_the_fingerprint(self, tmp_path):
        """fingerprint 是**报告的**内容哈希，不是输入的。素材的 mtime、
        一个不相干的 .txt、缓存目录里的东西都不在报告里，所以进不来。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        before = engine_readiness.compute(ctx)

        (figs / "notes.txt").write_text("随手记", encoding="utf-8")
        (figs / "README.md").write_text("# 说明", encoding="utf-8")
        os.utime(figs / "FigA.pdf", (20_000_000, 20_000_000))  # 素材被 touch 了一下
        assert engine_readiness.compute(ctx)["fingerprint"] == before["fingerprint"]


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
class _CountingScan:
    """数 `discover.discover()` 真的跑了几遍——缓存命中与否的**唯一**判据。
    比"两次返回同一个对象"强：深拷贝之后那条判据恒假，而它要挡的从来不是
    对象身份，是那一遍 AST。"""

    def __init__(self, real):
        self.real, self.n = real, 0

    def __call__(self, root):
        self.n += 1
        return self.real(root)


@pytest.fixture
def scan_count(monkeypatch):
    counter = _CountingScan(engine_discover.discover)
    monkeypatch.setattr(engine_discover, "discover", counter)
    return counter


class TestCache:
    def test_a_second_request_does_not_rescan(self, tmp_path, scan_count):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)

        engine_readiness.compute(ctx)
        engine_readiness.compute(ctx)
        engine_readiness.compute(ctx)
        assert scan_count.n == 1

    def test_the_returned_report_is_never_the_cached_object(self, tmp_path):
        """调用方拿到的是副本。共享一个可变 dict 出去的话，某个消费者往
        `candidates` 里 append 一下，之后每一次请求都带着那条脏数据——
        而它看起来完全像是后端算出来的。"""
        figs = _project(tmp_path)
        _script(figs, "a.py", "Dup")
        _script(figs, "b.py", "Dup")
        _pdf(figs / "Dup.pdf")
        ctx = _Ctx(figs)

        # ① 存进缓存那一刻要拷（否则第一个调用方改的就是缓存里那份）
        first = engine_readiness.compute(ctx)
        first["panels"][0]["candidates"].append("evil.py")
        first["summary"]["total"] = 999
        second = engine_readiness.compute(ctx)
        assert second["panels"][0]["candidates"] == ["a.py", "b.py"]
        assert second["summary"]["total"] == 1

        # ② 命中缓存交出去那一刻**也**要拷。两处各守一半：只拷入口的话，
        #    第二个调用方拿到的就是缓存本体，它一改，第三个调用方看到脏数据。
        second["panels"][0]["candidates"].append("evil.py")
        second["summary"]["total"] = 777
        third = engine_readiness.compute(ctx)
        assert third["panels"][0]["candidates"] == ["a.py", "b.py"]
        assert third["summary"]["total"] == 1

    def test_a_new_script_invalidates_the_cache(self, tmp_path, scan_count):
        figs = _project(tmp_path)
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        assert _status(engine_readiness.compute(ctx), "FigA.pdf")[0] == "layout_only"

        _script(figs, "fig_a.py", "FigA")
        assert _status(engine_readiness.compute(ctx), "FigA.pdf")[0] == "auto_linkable"
        assert scan_count.n == 2

    def test_an_in_place_edit_of_the_same_length_still_invalidates(self, tmp_path):
        """签名的 **mtime 维**：文件集合没变、长度一个字节都没变
        （`FigA` → `FigB`），只有内容与 mtime 变了。只按 size 签名的话缓存
        会命中旧那份，而界面上的现象是"我改了脚本，它就是不认"。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "FigB.pdf")
        ctx = _Ctx(figs)
        before = engine_readiness.compute(ctx)
        assert _status(before, "FigA.pdf")[0] == "auto_linkable"
        assert _status(before, "FigB.pdf") == ("layout_only", "no_source_candidate")

        path = figs / "fig_a.py"
        old_size = path.stat().st_size
        _script(figs, "fig_a.py", "FigB")
        assert path.stat().st_size == old_size, "这条用例要的是同尺寸改写"

        after = engine_readiness.compute(ctx)
        assert _status(after, "FigA.pdf") == ("layout_only", "no_source_candidate")
        assert _status(after, "FigB.pdf")[0] == "auto_linkable"

    def test_an_edit_that_keeps_the_mtime_still_invalidates(self, tmp_path):
        """签名的 **size 维**：把 mtime 按回去（网盘同步、`cp -p`、
        备份还原都会这样），只有长度变了。两维各守一半，缺一维会静默漏。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "FigLonger.pdf")
        ctx = _Ctx(figs)
        assert _status(engine_readiness.compute(ctx), "FigLonger.pdf")[0] == "layout_only"

        path = figs / "fig_a.py"
        st = path.stat()
        _script(figs, "fig_a.py", "FigLonger")
        assert path.stat().st_size != st.st_size, "这条用例要的是长度真的变了"
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert path.stat().st_mtime_ns == st.st_mtime_ns

        assert _status(engine_readiness.compute(ctx), "FigLonger.pdf")[0] == "auto_linkable"

    def test_the_in_memory_registry_is_part_of_the_cache_key(self, tmp_path):
        """注册表是判定的**头号输入**，而它既不在脚本签名里
        （那只扫 `.py`）也不在素材集合里。它自己变了缓存就得跟着变——
        另一个标签页刚 reload 过、probe 刚登记完，都是这个形状。
        """
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        assert _status(engine_readiness.compute(ctx), "FigA.pdf")[0] == "auto_linkable"

        # 磁盘一个字节没动，只有内存里那份索引变了
        ctx.registry.load_data(
            {"version": 1, "scripts": {"fig_a.py": {"entry": "main", "stems": ["FigA"]}}}
        )
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "editable",
            "registered_source",
        )

    def test_a_new_asset_invalidates_the_cache(self, tmp_path):
        figs = _project(tmp_path)
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        assert engine_readiness.compute(ctx)["summary"]["total"] == 1

        _pdf(figs / "FigB.pdf")
        assert engine_readiness.compute(ctx)["summary"]["total"] == 2

    def test_a_refresh_that_changed_something_drops_the_cache(self, tmp_path):
        """签名之外的**第二道**判据：刷新自己写注册表那一下，最容易撞上
        「同尺寸 + 同一个 mtime_ns 刻度」的盲区。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {})
        ctx = _Ctx(figs)
        assert _status(engine_readiness.compute(ctx), "FigA.pdf")[0] == "auto_linkable"

        engine_refresh.refresh_project_index(ctx, reason="manual")
        assert engine_refresh.state_of(ctx).readiness is None, "刷新改了事实，缓存该丢"
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "editable",
            "registered_source",
        )

    def test_a_refresh_that_changed_nothing_keeps_the_cache(self, tmp_path):
        """不变式：无差异 = 零事件、零写盘、零失效。白丢一次缓存就是白跑
        一遍全项目 AST，而项目刷新是个会被反复点的按钮。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)
        engine_refresh.refresh_project_index(ctx, reason="manual")  # 第一轮把发现落进注册表
        engine_readiness.compute(ctx)

        engine_refresh.refresh_project_index(ctx, reason="manual")
        assert engine_refresh.state_of(ctx).readiness is not None

    def test_two_projects_do_not_share_a_cache(self, tmp_path):
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        _script(a, "fig_a.py", "FigA")
        _pdf(a / "FigA.pdf")
        _pdf(b / "FigA.pdf")
        ctx_a, ctx_b = _Ctx(a, "pj-a"), _Ctx(b, "pj-b")

        body_a = engine_readiness.compute(ctx_a)
        body_b = engine_readiness.compute(ctx_b)
        assert body_a["project_id"] == "pj-a"
        assert body_b["project_id"] == "pj-b"
        assert _status(body_a, "FigA.pdf")[0] == "auto_linkable"
        assert _status(body_b, "FigA.pdf") == ("layout_only", "no_source_candidate")
        assert body_a["fingerprint"] != body_b["fingerprint"]

    def test_a_failed_scan_is_not_cached_and_recovers(self, tmp_path, monkeypatch):
        """一次瞬时的目录读错误**不许**把就绪度永久钉死在"没测量"。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _Ctx(figs)

        def boom(_root):
            raise OSError("目录读不动")

        monkeypatch.setattr(engine_discover, "discover", boom)
        broken = engine_readiness.compute(ctx)
        assert broken["project"]["scan_ok"] is False
        # **不是** no_source_candidate——那会是一句错的断言
        assert _status(broken, "FigA.pdf") == ("layout_only", "source_scan_unavailable")
        assert broken["conflicts"] is None, "没测量必须与「测量结果是零」分得开"
        assert {i["code"] for i in broken["issues"]} == {"source_scan_unavailable"}

        monkeypatch.undo()
        assert _status(engine_readiness.compute(ctx), "FigA.pdf") == (
            "auto_linkable",
            "static_unique_candidate",
        )

    def test_the_cache_survives_concurrent_readers(self, tmp_path, scan_count):
        """并发读不许把缓存搅坏，也不该各扫一遍。"""
        figs = _project(tmp_path)
        for i in range(5):
            _script(figs, f"fig_{i}.py", f"Fig{i}")
            _pdf(figs / f"Fig{i}.pdf")
        ctx = _Ctx(figs)
        engine_readiness.compute(ctx)  # 先热一遍缓存

        out: list[str] = []
        threads = [
            threading.Thread(
                target=lambda: out.append(engine_readiness.compute(ctx)["fingerprint"])
            )
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(out)) == 1
        assert scan_count.n == 1


# ---------------------------------------------------------------------------
# HTTP 面 + /api/panels 集成
# ---------------------------------------------------------------------------
class TestEndpoint:
    def test_readiness_needs_an_open_project(self, client):
        resp = client.get("/api/project/readiness")
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "no_project"

    def test_the_response_shape(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        ctx = _open(client, figs)

        body = client.get("/api/project/readiness").get_json()
        assert body["project_id"] == ctx.id
        assert set(body) == {
            "project_id",
            "fingerprint",
            "generated_at",
            "summary",
            "panels",
            "conflicts",
            "project",
            "issues",
        }
        panel = body["panels"][0]
        assert set(panel) == {
            "id",
            "status",
            "reason_code",
            "script",
            "candidates",
            "can_probe",
            "can_manual_link",
            "details",
        }

    def test_readiness_is_project_isolated(self, client, tmp_path):
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        _script(a, "fig_a.py", "FigA")
        _pdf(a / "FigA.pdf")
        _raster(b / "photo.png")
        _open(client, a)
        ctx_b = _open(client, b, default=False)

        body = client.get(
            "/api/project/readiness", headers={"X-Tavotto-Project": ctx_b.id}
        ).get_json()
        assert body["project_id"] == ctx_b.id
        assert [p["id"] for p in body["panels"]] == ["photo.png"]

    def test_an_invalid_registry_is_not_a_500_and_keeps_every_asset(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _raster(figs / "photo.png")
        _open(client, figs)
        (figs / "tavotto_registry.json").write_text("{ 坏了", encoding="utf-8")

        resp = client.get("/api/project/readiness")
        assert resp.status_code == 200
        body = resp.get_json()
        assert {p["id"] for p in body["panels"]} == {"FigA.pdf", "photo.png"}
        assert body["project"]["registry_valid"] is False

    def test_readiness_publishes_no_sse_event(self, client, tmp_path, monkeypatch):
        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(m, "sse_publish", lambda ev, data: events.append((ev, data)))
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _open(client, figs)
        events.clear()

        assert client.get("/api/project/readiness").status_code == 200
        assert client.get("/api/panels").status_code == 200
        assert events == []


class TestPanelsIntegration:
    def test_capability_comes_from_the_same_report(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _script(figs, "d1.py", "Dup")
        _script(figs, "d2.py", "Dup")
        _pdf(figs / "FigA.pdf")
        _pdf(figs / "Dup.pdf")
        _raster(figs / "photo.png")
        _open(client, figs)

        readiness = {p["id"]: p for p in client.get("/api/project/readiness").get_json()["panels"]}
        panels = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}
        assert set(panels) == set(readiness)
        for pid, panel in panels.items():
            cap = panel["capability"]
            assert set(cap) == set(engine_readiness.CAPABILITY_FIELDS)
            for field in engine_readiness.CAPABILITY_FIELDS:
                assert cap[field] == readiness[pid][field], (pid, field)

    def test_editable_panels_keep_the_old_script_field(self, client, tmp_path):
        """旧前端只认 `script`。它的语义一个字没改（"注册表声明了映射"），
        `capability` 是**增强**信息，不是替代品。"""
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _write_registry(figs, {"fig_a.py": {"entry": "main", "cost": "light", "stems": ["FigA"]}})
        _open(client, figs)

        panel = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}["FigA.pdf"]
        assert panel["script"] == "fig_a.py"
        assert panel["cost"] == "light"
        assert panel["capability"]["status"] == "editable"

    def test_a_layout_only_panel_never_gets_a_fake_script(self, client, tmp_path):
        figs = _project(tmp_path)
        _raster(figs / "photo.png")
        _open(client, figs)

        panel = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}["photo.png"]
        assert "script" not in panel
        assert panel["capability"]["status"] == "layout_only"
        assert panel["capability"]["script"] is None

    def test_a_panel_with_candidates_still_has_no_script(self, client, tmp_path):
        """`auto_linkable` / `conflict` 有候选，但候选**不是**来源。
        为了 UI 好看把候选塞进 `script`，旧前端会当场给它画上 ⚡。"""
        figs = _project(tmp_path)
        _script(figs, "a.py", "Dup")
        _script(figs, "b.py", "Dup")
        _pdf(figs / "Dup.pdf")
        _write_registry(figs, {})
        _open(client, figs)

        panel = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}["Dup.pdf"]
        assert "script" not in panel
        assert panel["capability"]["status"] == "conflict"
        assert panel["capability"]["candidates"] == ["a.py", "b.py"]

    def test_the_old_panel_fields_do_not_regress(self, client, tmp_path):
        figs = _project(tmp_path)
        _script(figs, "fig_a.py", "FigA")
        _pdf(figs / "FigA.pdf")
        _raster(figs / "photo.png")
        _open(client, figs)

        panels = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}
        base = {"id", "name", "folder", "mtime", "kind", "native_w_mm", "native_h_mm"}
        assert base <= set(panels["FigA.pdf"])
        assert base | {"px_w", "px_h"} <= set(panels["photo.png"])
        assert panels["photo.png"]["kind"] == "raster"
