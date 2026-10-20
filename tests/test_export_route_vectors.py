"""「面板在本次导出里嵌入的是不是磁盘位图」严格同源对的**后端那一侧**。

前端 `web/src/lib/exportRoute.ts` 的 `embedsDiskBitmap` ↔ 后端真实代码：

* 原图范围：真调 `app._resolve_panel_source`（`rerender` 取自 `app._original_rerender(所选格式)`；真 `_serialize_figure_with_worker`、真 `safe_resolve`），
  只桩两样环境：注册表（有没有脚本）与 worker（写一份空 PDF）。嵌入的是不是磁盘位图 =
  「回来的路径就是磁盘上那张非 PDF 文件」。
* 画布范围：真调 `rendercore.sources.needs_execution`——要不要 worker 现画；不要才嵌磁盘原件。

共享向量 `tests/golden/export_route_vectors.json`，前端那侧是 `web/src/lib/exportRoute.test.ts`。

盲点（判不出就不判，不伪造）：

* `backend_overrides` 为 `null` 的向量（受守卫 PNG 的守卫无效）：前端在发请求之前就抛错、导出被拒，
  后端根本收不到这个面板，没有后端行为可对；约定按位图口径，只由前端侧断言。
* 画布范围里「没有脚本」：后端 `source_needs_execution` 失败而不是产出文件，向量按
  `needs_execution` 判（不嵌磁盘位图），不去驱动整条画布合成。
* runtime 素材在原图范围里只桩了 `_serialize_figure`（它对 runtime 无条件走 worker，没有脚本分支）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import runtimeasset
from tavotto.rendercore import sources as rc_sources

VECTORS = json.loads(
    (Path(__file__).parent / "golden" / "export_route_vectors.json").read_text(encoding="utf-8")
)["vectors"]
ORIGINAL = [v for v in VECTORS if v["scope"] == "original" and v["backend_overrides"] is not None]
CANVAS = [v for v in VECTORS if v["scope"] == "canvas"]
FILE_IDS = {"raster": "r1.png", "pdf": "p1.pdf", "runtime": runtimeasset.RUNTIME_PREFIX + "x#1"}


def _write_pdf(path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=200, height=150)
    doc.save(path)
    doc.close()


@pytest.fixture
def project(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    _write_pdf(figs / "p1.pdf")
    raster = pymupdf.open()
    page = raster.new_page(width=120, height=80)
    page.draw_rect(pymupdf.Rect(0, 0, 60, 80), color=None, fill=(0, 0, 0))
    page.get_pixmap(alpha=False).save(figs / "r1.png")
    raster.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    return figs, tmp_path / "job"


def _install_worker(monkeypatch, *, has_script: bool) -> None:
    class FakeWorker:
        export_dir = None

        def export(self, stem, patches, path, fmt="pdf", dpi=600):
            _write_pdf(path)
            return {"warnings": []}

    info = {"script": "s.py", "entry": "main"} if has_script else None
    monkeypatch.setattr(m, "_safe_worker", lambda *a, **kw: FakeWorker())
    monkeypatch.setattr(
        m, "current_registry", lambda: type("R", (), {"for_stem": lambda self, s: info})()
    )


def test_the_vectors_cover_both_scopes_and_both_outcomes():
    assert {v["scope"] for v in ORIGINAL} == {"original"} and CANVAS
    assert {v["embeds_disk_bitmap"] for v in ORIGINAL} == {True, False}
    assert {v["embeds_disk_bitmap"] for v in CANVAS} == {True, False}
    # 原图范围的格式维度：同一个「未编辑 + 有脚本的 PNG」随所选格式两种结论都出现
    unedited = [
        v for v in ORIGINAL if v["name"].startswith("original/raster/script/guard-none/none")
    ]
    assert {v["embeds_disk_bitmap"] for v in unedited} == {True, False}
    assert {tuple(v["formats"]) for v in unedited} >= {
        ("pdf",),
        ("eps", "pdf"),
        ("eps", "svg"),
        ("eps",),
        ("svg", "pdf"),
    }
    # 每个守卫无效的向量都被明确排除在后端对拍之外（盲点见模块 docstring）
    assert [v["guard"] for v in VECTORS if v["backend_overrides"] is None] == ["invalid"] * 2


@pytest.mark.parametrize("vec", ORIGINAL, ids=lambda v: v["name"])
def test_original_scope_matches_resolve_panel_source(vec, project, monkeypatch):
    figs, out_dir = project
    out_dir.mkdir(exist_ok=True)
    file_id = FILE_IDS[vec["file_kind"]]
    obj = {"id": file_id, "overrides": vec["backend_overrides"]}
    if vec["file_kind"] == "runtime":
        monkeypatch.setattr(
            m,
            "_serialize_figure",
            lambda *a, **kw: (_write_pdf(out_dir / "rt.pdf"), out_dir / "rt.pdf")[1],
        )
    else:
        _install_worker(monkeypatch, has_script=vec["has_script"])
    got = Path(
        m._resolve_panel_source(
            obj, 150, None, out_dir, rerender=m._original_rerender(tuple(vec["formats"]))
        )
    )
    embeds_disk_bitmap = (
        vec["file_kind"] != "runtime"
        and got == (figs / file_id).resolve()
        and got.suffix.lower() != ".pdf"
    )
    assert embeds_disk_bitmap is vec["embeds_disk_bitmap"], got


@pytest.mark.parametrize("vec", CANVAS, ids=lambda v: v["name"])
def test_canvas_scope_matches_needs_execution(vec):
    obj = {"id": FILE_IDS[vec["file_kind"]], "overrides": vec["backend_overrides"]}
    embeds_disk_bitmap = vec["file_kind"] == "raster" and not rc_sources.needs_execution(obj)
    assert embeds_disk_bitmap is vec["embeds_disk_bitmap"]
