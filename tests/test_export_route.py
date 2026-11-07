"""ExportRoute（#1209 / A2）：「这次导出用哪份来源」只有 `engine/exportroute.py` 一处回答。

三层看护：
* 纯函数对金向量：`embeds_disk_bitmap` 与前端同源对用的同一份 `tests/golden/export_route_vectors.json`；
* 纯函数对真实导出路径：eps / svg 的不可用原因码就是导出时真会报的那个码；
* `/api/export/validate` 在响应里带 `route`，且与执行路径读同一个谓词（`wants_execution`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportroute as er, runtimeasset
from tavotto.rendercore import sources as rc_sources

VECTORS = json.loads(
    (Path(__file__).parent / "golden" / "export_route_vectors.json").read_text(encoding="utf-8")
)["vectors"]
KIND = {"raster": er.KIND_RASTER, "pdf": er.KIND_PDF, "runtime": er.KIND_RUNTIME}


@pytest.mark.parametrize(
    "vec", [v for v in VECTORS if v["backend_overrides"] is not None], ids=lambda v: v["name"]
)
def test_route_agrees_with_the_golden_vectors(vec):
    route = er.panel_route(
        scope=vec["scope"],
        formats=vec["formats"],
        file_kind=KIND[vec["file_kind"]],
        overrides=vec["backend_overrides"],
        has_script=vec["has_script"],
    )
    assert route["embeds_disk_bitmap"] is vec["embeds_disk_bitmap"]


def route(scope, formats, kind, *, overrides=(), script=True, snapshot=False):
    return er.panel_route(
        scope=scope,
        formats=formats,
        file_kind=kind,
        overrides=list(overrides),
        has_script=script,
        has_snapshot=snapshot,
    )


def test_original_scope_unedited_pdf_is_copied_and_png_converted():
    r = route("original", ["pdf", "png", "tiff"], er.KIND_PDF)
    assert r["source"] == er.SOURCE_DISK
    assert {f: v["how"] for f, v in r["formats"].items()} == {
        "pdf": "copy",
        "png": "convert",
        "tiff": "convert",
    }


def test_eps_makes_every_format_come_from_one_redraw():
    r = route("original", ["eps", "pdf", "png"], er.KIND_PDF)
    assert r["source"] == er.SOURCE_REDRAW
    assert {v["how"] for v in r["formats"].values()} == {"redraw"}
    # 同一作业还要 svg：eps 只取已有的活 Figure，不再重画换来源；pdf 不跟着重画
    r = route("original", ["eps", "svg", "pdf"], er.KIND_PDF)
    assert r["source"] == er.SOURCE_DISK
    assert r["formats"]["eps"] == {"how": "redraw", "conditions": [er.COND_LIVE_FIGURE]}


def test_unavailable_reasons_are_the_codes_the_export_reports():
    assert route("canvas", ["eps"], er.KIND_PDF)["formats"]["eps"] == {
        "how": "unavailable",
        "reason": "eps_not_for_canvas",
    }
    assert route("original", ["eps"], er.KIND_PDF, script=False)["formats"]["eps"]["reason"] == (
        "eps_needs_script"
    )
    # 画布范围：要现画却没有脚本——后端报 source_needs_execution，不退回磁盘文件
    r = route("canvas", ["pdf"], er.KIND_PDF, overrides=[{"x": 1}], script=False)
    assert r["source"] == er.SOURCE_UNAVAILABLE
    assert r["formats"]["pdf"] == {"how": "unavailable", "reason": "source_needs_execution"}
    # 原图范围同样的处境退回磁盘文件
    assert route("original", ["pdf"], er.KIND_PDF, overrides=[{"x": 1}], script=False)[
        "source"
    ] == (er.SOURCE_DISK)


def test_svg_asset_basis_is_the_file_bytes():
    r = route("original", ["svg", "eps", "pdf"], er.KIND_SVG)
    assert r["formats"]["svg"] == {"how": "copy"}
    assert r["formats"]["eps"] == {
        "how": "redraw",
        "conditions": [er.COND_SVG_ASSET_BINDING, er.COND_LIVE_FIGURE],
    }
    assert r["formats"]["pdf"] == {"how": "convert"}
    assert route("canvas", ["svg"], er.KIND_SVG)["formats"]["svg"] == {"how": "convert"}


def test_raster_without_execution_has_no_svg():
    r = route("original", ["svg"], er.KIND_RASTER)
    assert r["formats"]["svg"]["reason"] == "svg_source_unsupported"
    assert route("original", ["svg"], er.KIND_RASTER, overrides=[{"x": 1}])["formats"]["svg"] == {
        "how": "redraw"
    }


def test_the_execution_predicate_is_shared_with_the_real_code():
    for rid in ("a.png", runtimeasset.RUNTIME_PREFIX + "x#1"):
        for ov in ([], [{"p": 1}]):
            expect = er.wants_execution(runtime=runtimeasset.is_runtime_id(rid), overrides=ov)
            assert rc_sources.needs_execution({"id": rid, "overrides": ov}) is expect
    for fmts in (("eps",), ("eps", "svg"), ("pdf",), ("eps", "pdf")):
        assert m._original_rerender(fmts) is er.original_rerender(fmts)


def test_equal_instances_merge_and_different_ones_are_kept_apart():
    base = {"id": "a.png", "file_kind": "raster", "overrides": [], "has_script": True}
    edited = {**base, "overrides": [{"p": 1}]}
    got = er.build_route(scope="canvas", formats=["pdf"], panels=[base, base, edited])
    assert set(got["panels"]) == {"a.png", "a.png#2"}


# ------------------------------- validate 端点 -------------------------------


def _write_pdf(path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=200, height=150)
    doc.save(path)
    doc.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    _write_pdf(figs / "p1.pdf")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    return m.app.test_client()


def _validate(client, spec):
    resp = client.post("/api/export/validate", json=spec)
    assert resp.status_code == 200
    return resp.get_json()


def test_validate_publishes_route_for_original_scope(client):
    body = _validate(
        client,
        {
            "scope": "original",
            "formats": ["pdf", "eps"],
            "filename": "out",
            "original": {"figure_id": "p1.pdf", "overrides": []},
        },
    )
    panel = body["route"]["panels"]["p1.pdf"]
    # 注册表里没有这张图的脚本：eps 逐项不可用，原因与导出时报的同一个码；pdf 取磁盘原件
    assert panel["formats"]["eps"] == {"how": "unavailable", "reason": "eps_needs_script"}
    assert panel["formats"]["pdf"] == {"how": "copy"}
    assert panel["embeds_disk_bitmap"] is False


def test_validate_publishes_route_for_canvas_scope(client):
    body = _validate(
        client,
        {
            "scope": "canvas",
            "formats": ["pdf", "eps"],
            "filename": "out",
            "canvas": {
                "page_w_mm": 100,
                "page_h_mm": 80,
                "objects": [
                    {"type": "panel", "id": "p1.pdf", "x": 0, "y": 0, "w": 50, "h": 40},
                    {"type": "panel", "id": "p2.pdf", "hidden": True},
                ],
            },
        },
    )
    assert list(body["route"]["panels"]) == ["p1.pdf"]
    assert body["route"]["panels"]["p1.pdf"]["formats"]["eps"]["reason"] == "eps_not_for_canvas"


def test_canvas_scope_never_claims_a_verbatim_copy():
    """画布范围不论格式都经 RenderCore 重新合成页面，没有「逐字复制」。"""
    r = route("canvas", ["pdf", "png"], er.KIND_PDF)
    assert {v["how"] for v in r["formats"].values()} == {"convert"}
    assert route("original", ["pdf"], er.KIND_PDF)["formats"]["pdf"]["how"] == "copy"


@pytest.mark.parametrize("scope", ["original", "canvas"])
@pytest.mark.parametrize("script", [True, False])
def test_edited_panel_with_a_snapshot_reference_loads_the_snapshot(scope, script):
    """`app._execution_source` 先查引用、加载快照，不跑脚本——有没有脚本都一样；引用有效是导出的前提。"""
    r = route(
        scope, ["pdf", "svg"], er.KIND_RASTER, overrides=[{"p": 1}], script=script, snapshot=True
    )
    assert r["source"] == er.SOURCE_SNAPSHOT and r["embeds_disk_bitmap"] is False
    for fmt in ("pdf", "svg"):
        assert r["formats"][fmt] == {"how": "convert", "conditions": [er.COND_FIGURE_SNAPSHOT]}
    # 明确引用选择同一历史配对；同名磁盘原件可以已有更新。
    r = route(scope, ["pdf"], er.KIND_PDF, snapshot=True)
    assert r["source"] == er.SOURCE_SNAPSHOT
    assert r["formats"]["pdf"]["how"] == "convert"
    assert er.COND_FIGURE_SNAPSHOT in r["formats"]["pdf"]["conditions"]


def test_execution_source_and_route_share_the_snapshot_predicate(monkeypatch):
    seen = []
    monkeypatch.setattr(er, "executes_from_snapshot", lambda **kw: seen.append(kw) or False)
    monkeypatch.setattr(m, "_load_svg_snapshot", lambda *a, **k: pytest.fail("snapshot loaded"))
    monkeypatch.setattr(m, "_serialize_figure_with_worker", lambda *a, **k: None)
    with pytest.raises(rc_sources.SourceError):
        m._execution_source(
            {"id": "a.png", "overrides": [{"p": 1}], "figure_snapshot": {"x": 1}}, 150, None, None
        )
    assert seen == [{"has_reference": True}]


@pytest.mark.parametrize("scope", ["original", "canvas"])
@pytest.mark.parametrize("kind", [er.KIND_RASTER, er.KIND_PDF, er.KIND_RUNTIME])
def test_the_snapshot_reference_check_applies_to_every_format(scope, kind):
    """引用核验在任何格式开始前就发生，过期整次拒绝——eps（含画布范围逐项不可用的 eps）也不例外。"""
    r = route(
        scope, ["pdf", "png", "tiff", "eps", "svg"], kind, overrides=[{"p": 1}], snapshot=True
    )
    for fmt, entry in r["formats"].items():
        assert er.COND_FIGURE_SNAPSHOT in entry.get("conditions", []), (fmt, entry)
    if scope == "original":
        assert r["formats"]["eps"]["how"] == "redraw"  # _produce_original_eps 仍由活 Figure 现画
    # 没有引用就没有这条前提
    r = route(scope, ["pdf", "eps"], kind, overrides=[{"p": 1}])
    assert all(
        er.COND_FIGURE_SNAPSHOT not in e.get("conditions", []) for e in r["formats"].values()
    )


def test_offers_say_whether_eps_is_available_without_it_being_requested():
    assert route("original", ["pdf"], er.KIND_PDF)["offers"]["eps"]["how"] == "redraw"
    assert route("original", ["pdf"], er.KIND_PDF, script=False)["offers"]["eps"] == {
        "how": "unavailable",
        "reason": "eps_needs_script",
    }
    assert route("canvas", ["pdf"], er.KIND_PDF)["offers"]["eps"]["reason"] == "eps_not_for_canvas"
    # 勾了 eps 时 offers 与 formats 里那一项是同一个答案
    r = route("original", ["eps", "svg"], er.KIND_PDF)
    assert r["offers"]["eps"] == r["formats"]["eps"]


@pytest.mark.parametrize("scope", ["original", "canvas"])
@pytest.mark.parametrize("script", [False, True])
@pytest.mark.parametrize(
    "formats", [["pdf"], ["eps"], ["eps", "svg"], ["pdf", "png", "eps", "tiff", "svg"]]
)
def test_declarative_scene_uses_conversion_without_script_or_live_figure(scope, script, formats):
    result = er.panel_route(
        scope=scope,
        formats=formats,
        file_kind=er.KIND_SVG,
        overrides=[{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}],
        has_script=script,
        has_snapshot=True,
        has_chart=True,
    )
    assert result["source"] == er.SOURCE_DECLARATIVE
    assert result["embeds_disk_bitmap"] is False
    eps = (
        {"how": "unavailable", "reason": "eps_not_for_canvas"}
        if scope == "canvas"
        else {"how": "convert"}
    )
    assert result["offers"]["eps"] == eps
    assert result["formats"] == {
        fmt: eps if fmt == "eps" else {"how": "convert"} for fmt in formats
    }
    # No figure snapshot, source-ref verification or paint support is invented.
    assert all("conditions" not in entry for entry in result["formats"].values())


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_chart_fact_does_not_change_legacy_false_default_or_input(scope):
    inputs = dict(
        scope=scope,
        formats=["eps", "svg", "pdf"],
        file_kind=er.KIND_SVG,
        overrides=[],
        has_script=False,
        has_snapshot=False,
    )
    assert er.panel_route(**inputs) == er.panel_route(**inputs, has_chart=False)
    assert inputs["overrides"] == []


def test_build_route_keeps_legacy_call_shape_and_only_passes_true_chart_fact(monkeypatch):
    called = []
    real = er.panel_route

    def capture(**kw):
        called.append(kw)
        return real(**kw)

    monkeypatch.setattr(er, "panel_route", capture)
    base = {"id": "scene.svg", "file_kind": "svg", "overrides": [], "has_script": False}
    result = er.build_route(
        scope="original",
        formats=["pdf"],
        panels=[base, {**base, "has_chart": False}, {**base, "has_chart": True}],
    )
    expected = dict(
        scope="original",
        formats=["pdf"],
        file_kind="svg",
        overrides=[],
        has_script=False,
        has_snapshot=False,
    )
    assert called == [expected, expected, {**expected, "has_chart": True}]
    assert set(result["panels"]) == {"scene.svg", "scene.svg#2"}
    assert result["panels"]["scene.svg"]["offers"]["eps"]["reason"] == "eps_needs_script"
    assert result["panels"]["scene.svg#2"]["offers"]["eps"] == {"how": "convert"}
