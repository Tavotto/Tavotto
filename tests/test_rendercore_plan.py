"""RenderPlan 编译（统一实施包 U06，ADR 0059）：画布 → IR 的换算只在一处、paint order 原样、
冻结源的身份与竞态（RC-014）、无 override 静态源不跑脚本（RC-019）、同图两实例不串资源
（RC-015）、身份复用 U01 的 `render_plan_ref`、能力缺口按格式列出。

几何期望**在本文件里手算**（毫米 → pt、y 翻转、旋转矩阵），不调 `plan.py` 的换算函数
（RC-095：写入 / 读取不同源）。用合成脸；面板用 U00 夹具 `pdf_png_assets` 的真实文件。
"""

from __future__ import annotations

import hashlib
import math
import shutil
import sys
from pathlib import Path

import pytest

from tavotto.engine import exportreq, figcapture
from tavotto.rendercore import ir, plan, sources

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import fakeface  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foundation" / "pdf_png_assets"
PROVIDER = fakeface.FakeProvider()
MM = 72.0 / 25.4


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "figs").mkdir(parents=True)
    shutil.copy(FIXTURE / "page.pdf", root / "figs" / "Fig1.pdf")
    shutil.copy(FIXTURE / "original.png", root / "figs" / "Fig2.png")
    return root


def _panel(fid: str, x=10.0, y=20.0, w=60.0, h=40.0, **kw) -> dict:
    return {"type": "panel", "id": fid, "x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h, **kw}


def _text(text="Hi", x=5.0, y=5.0, w=30.0, h=8.0, **kw) -> dict:
    return {
        "type": "text",
        "id": kw.pop("id", "t1"),
        "text": text,
        "x_mm": x,
        "y_mm": y,
        "w_mm": w,
        "h_mm": h,
        "size_pt": 10.0,
        **kw,
    }


def _shape(kind="rect", x=10.0, y=10.0, w=20.0, h=10.0, **kw) -> dict:
    return {
        "type": "shape",
        "id": kw.pop("id", "s1"),
        "shape": kind,
        "x_mm": x,
        "y_mm": y,
        "w_mm": w,
        "h_mm": h,
        **kw,
    }


def _compile(root: Path, objects: list[dict], page=(180.0, 120.0), **kw) -> plan.CompiledPage:
    return plan.compile_page(
        page[0], page[1], objects, sources=sources.StaticSourceResolver(root), faces=PROVIDER, **kw
    )


# ---------------------------------------------------------------- 单位 / 原点 / y 翻转（RC-008）


def test_a_panel_lands_at_mm_to_pt_with_y_flipped_exactly_once(project: Path):
    """页 180×120 mm；面板 (10, 20, 60, 40) mm → pt 后 y 向上的矩形：
    x = 10·k，y = (120 − 20 − 40)·k，w = 60·k，h = 40·k。"""
    compiled = _compile(project, [_panel("figs/Fig1.pdf")])
    page = compiled.page
    assert (page.width_pt, page.height_pt) == pytest.approx((180 * MM, 120 * MM))
    node = page.children[0]
    assert isinstance(node, ir.ImportedPage)
    assert node.rect == pytest.approx((10 * MM, 60 * MM, 60 * MM, 40 * MM))
    assert node.internal == "unknown"


def test_text_baseline_and_shape_box_are_flipped_once_and_rotation_is_negated(project: Path):
    """文字：基线 y_up = H − baseline_down；旋转 30°（画布顺时针）→ y 向上空间里绕框中心 −30°。
    形状：框空间 → 页面的矩阵是 (1 0 0 −1 x_pt H−y_pt)，再乘同一个旋转。"""
    H = 120 * MM
    compiled = _compile(project, [_text(rotation_deg=30), _shape(rotation_deg=30)])
    text_grp, shape_grp = compiled.page.children
    assert isinstance(text_grp, ir.Group) and isinstance(shape_grp, ir.Group)
    # 文字块中心（pt，y 向上）
    cx, cy = (5 + 30 / 2) * MM, H - (5 + 8 / 2) * MM
    c, s = math.cos(math.radians(-30)), math.sin(math.radians(-30))
    want = (c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy)
    assert text_grp.transform == pytest.approx(want, abs=1e-9)
    st = next(n for n in text_grp.children if isinstance(n, ir.ShapedText))
    faces = PROVIDER.face_for("serif", False, False)
    asc, desc = faces.ascender, faces.descender
    baseline_down = 5 * MM + 10.0 * ((1.25 - (asc - desc)) / 2 + asc)
    assert st.y == pytest.approx(H - baseline_down)
    assert st.x == pytest.approx(5 * MM)
    # 形状
    bx, by = 10 * MM, 10 * MM
    flip = (1.0, 0.0, 0.0, -1.0, bx, H - by)
    scx, scy = bx + 10 * MM, H - (by + 5 * MM)
    rot = (c, s, -s, c, scx - c * scx + s * scy, scy - s * scx - c * scy)
    # 先 flip 后 rot：手算合成（行向量）
    a1, b1, c1, d1, e1, f1 = flip
    a2, b2, c2, d2, e2, f2 = rot
    want_shape = (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )
    assert shape_grp.transform == pytest.approx(want_shape, abs=1e-9)
    # 框空间里的矩形描边内缩半线宽（1 pt 线宽 → 0.5）
    rect = shape_grp.children[0]
    assert isinstance(rect, ir.Path)
    assert rect.segments[0] == ("M", 0.5, 0.5)


def test_no_rotation_means_identity_for_text_and_pure_flip_for_shapes(project: Path):
    compiled = _compile(project, [_text(), _shape()])
    assert compiled.page.children[0].transform == ir.IDENTITY
    assert compiled.page.children[1].transform == (1.0, 0.0, 0.0, -1.0, 10 * MM, 120 * MM - 10 * MM)


# ---------------------------------------------------------------- paint order（RC-009）


def test_paint_order_is_the_object_list_order_and_hidden_objects_are_dropped(project: Path):
    objs = [
        _shape(id="z"),
        _text(id="a"),
        _panel("figs/Fig1.pdf", hidden=True),
        _shape(id="m", kind="ellipse"),
    ]
    compiled = _compile(project, objs)
    assert [n.object_id for n in compiled.page.children] == ["z", "a", "m"]
    assert compiled.dropped_hidden == ("figs/Fig1.pdf",)
    assert compiled.page.resources == {
        "font:serif-Regular": PROVIDER.face_for("serif", False, False).resource
    }


# ---------------------------------------------------------------- 冻结源（RC-014 / 015 / 016 / 019）


def test_static_sources_are_frozen_by_bytes_and_shared_when_identical(project: Path):
    """两个面板指向同一份文件：一条资源、一份冻结源；hash 是文件字节的 sha256。"""
    compiled = _compile(project, [_panel("figs/Fig1.pdf"), _panel("figs/Fig1.pdf", x=90)])
    keys = {n.resource for n in compiled.page.children}
    assert len(keys) == 1
    res = compiled.page.resources[next(iter(keys))]
    assert isinstance(res, ir.FileResource)
    assert res.sha256 == hashlib.sha256((project / "figs" / "Fig1.pdf").read_bytes()).hexdigest()
    assert res.origin == "static" and res.kind == "pdf" and res.source_id == "figs/Fig1.pdf"
    assert list(compiled.sources) == list(keys)


def test_a_raster_panel_becomes_an_image_node(project: Path):
    compiled = _compile(
        project, [_panel("figs/Fig2.png", crop={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5})]
    )
    node = compiled.page.children[0]
    assert isinstance(node, ir.Image)
    assert node.crop == (0.1, 0.2, 0.5, 0.5)
    assert compiled.page.resources[node.resource].kind == "png"


def test_a_panel_with_overrides_is_not_executed_by_the_static_resolver(project: Path):
    """RC-019 的另一面：静态解析器绝不跑脚本——带 override 的面板是结构化错误，不是子进程。"""
    with pytest.raises(plan.PlanError) as ei:
        _compile(project, [_panel("figs/Fig1.pdf", overrides=[{"gid": "ax0", "prop": "xlim"}])])
    assert ei.value.code == "source" and ei.value.params["source_code"] == "source_needs_execution"
    with pytest.raises(plan.PlanError) as ei2:
        _compile(project, [_panel("runtime:script.py#fig")])
    assert ei2.value.params["source_code"] == "source_needs_execution"


@pytest.mark.parametrize(
    "fid,code",
    [
        ("figs/Nope.pdf", "source_missing"),
        ("../outside.pdf", "source_outside_root"),
        ("figs/data.csv", "source_kind_unsupported"),
    ],
)
def test_static_resolver_rejects_missing_escaping_and_unsupported_sources(project, fid, code):
    (project / "figs" / "data.csv").write_text("a,b\n", encoding="utf-8")
    (project.parent / "outside.pdf").write_bytes(b"%PDF-1.4\n")
    with pytest.raises(sources.SourceError) as ei:
        sources.StaticSourceResolver(project).resolve(_panel(fid))
    assert ei.value.code == code


def test_frozen_bytes_are_verified_at_read_time(project: Path):
    """冻结之后改写源文件：`read_frozen` 报 `source_changed`，绝不把新字节交给写入器（RC-014）。"""
    fs = sources.StaticSourceResolver(project).resolve(_panel("figs/Fig1.pdf"))
    assert sources.read_frozen(fs) == (project / "figs" / "Fig1.pdf").read_bytes()
    (project / "figs" / "Fig1.pdf").write_bytes(b"%PDF-1.4\n% tampered\n")
    with pytest.raises(sources.SourceError) as ei:
        sources.read_frozen(fs)
    assert ei.value.code == "source_changed"


def test_two_instances_with_different_execution_identity_get_two_resources(project: Path):
    """RC-015：同一张图、两套 override（两份不同的执行产物）→ 两条资源、两份冻结源，不按 stem 串用。"""
    art_a = figcapture.source_artifact_from_file(
        project / "figs" / "Fig1.pdf",
        source_id="figs/Fig1.pdf",
        origin="execution",
        receipt_id="r1",
        receipt_identity="sha256:" + "a" * 64,
        patch_hash="p1",
    )
    shutil.copy(FIXTURE / "page.pdf", project / "figs" / "Fig1-b.pdf")
    art_b = figcapture.source_artifact_from_file(
        project / "figs" / "Fig1-b.pdf",
        source_id="figs/Fig1.pdf",
        origin="execution",
        receipt_id="r1",
        receipt_identity="sha256:" + "a" * 64,
        patch_hash="p2",
    )

    class Two:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, obj: dict) -> sources.FrozenSource:
            self.calls += 1
            art = art_a if obj["id"] == "A" else art_b
            return sources.FrozenSource(
                art, project / "figs" / ("Fig1.pdf" if art is art_a else "Fig1-b.pdf")
            )

    two = Two()
    compiled = plan.compile_page(
        100, 100, [_panel("A"), _panel("B", x=50)], sources=two, faces=PROVIDER
    )
    keys = [n.resource for n in compiled.page.children]
    assert len(set(keys)) == 2 and two.calls == 2
    assert {compiled.page.resources[k].origin for k in keys} == {"execution"}


# ---------------------------------------------------------------- compile_plan：身份与能力


def _request(objects: list[dict], **spec) -> exportreq.ExportRequest:
    payload = {
        "scope": "canvas",
        "formats": ["pdf", "png"],
        "filename": "Fig",
        "canvas": {"page_w_mm": 180, "page_h_mm": 120, "objects": objects},
        **spec,
    }
    return exportreq.normalize(payload)


def test_compile_plan_reuses_the_u01_render_plan_ref_for_identity(project: Path):
    req = _request([_panel("figs/Fig1.pdf"), _text("Hi 图")])
    resolver = sources.StaticSourceResolver(project)
    rp = plan.compile_plan(req, sources=resolver, faces=PROVIDER)
    art = resolver.resolve(_panel("figs/Fig1.pdf")).artifact
    assert rp.plan_ref == exportreq.render_plan_ref(req, [art.to_payload()])
    assert rp.plan_identity.startswith("sha256:")
    assert rp.formats == ("pdf", "png") and rp.ppi == 600 and rp.background == "white"
    assert rp.page.background == (1.0, 1.0, 1.0)
    # 同一请求两次编译 → 同一身份；换 override 语义（另一个 patch）→ 身份变
    assert (
        plan.compile_plan(req, sources=resolver, faces=PROVIDER).plan_identity == rp.plan_identity
    )
    other = _request([_panel("figs/Fig1.pdf", x=20), _text("Hi 图")])
    assert (
        plan.compile_plan(other, sources=resolver, faces=PROVIDER).plan_identity != rp.plan_identity
    )


def test_compile_plan_lists_capability_gaps_per_format_and_problems(project: Path):
    req = _request([_panel("figs/Fig1.pdf", opacity=0.5), _text("∇ 图")], background="transparent")
    rp = plan.compile_plan(req, sources=sources.StaticSourceResolver(project), faces=PROVIDER)
    assert rp.page.background is None
    # 导入页在 PDF 里是 U07 的事；整体 opacity（透明组）本切片已是 native，所以不在缺口里
    assert [g["operation"] for g in rp.unsupported["pdf"]] == ["imported_page"]
    assert "page_background" not in {g["operation"] for g in rp.unsupported["png"]}
    assert {g["operation"] for g in rp.unsupported["png"]} >= {"imported_page", "text"}
    assert rp.problems == (
        {"code": "glyph_missing", "object_id": "t1", "chars": ["∇"]},
        {"code": "cjk_face", "object_id": "t1", "chars": ["图"]},
    )


def test_original_scope_is_not_compiled_in_this_slice(project: Path):
    req = exportreq.normalize(
        {
            "scope": "original",
            "formats": ["pdf"],
            "filename": "F",
            "original": {"figure_id": "figs/Fig1.pdf"},
        }
    )
    with pytest.raises(plan.PlanError) as ei:
        plan.compile_plan(req, sources=sources.StaticSourceResolver(project), faces=PROVIDER)
    assert ei.value.code == "scope_not_compiled"


def test_missing_fonts_surface_as_a_structured_plan_error(project: Path):
    class Unavailable(RuntimeError):
        code = "fonts_dir_missing"

    class NoFonts:
        def face_for(self, family, bold, italic):
            raise Unavailable("no fonts here")

        def cjk_face(self):
            return None

    with pytest.raises(plan.PlanError) as ei:
        plan.compile_page(
            100, 100, [_text()], sources=sources.StaticSourceResolver(project), faces=NoFonts()
        )
    assert ei.value.code == "fonts" and ei.value.params["font_code"] == "fonts_dir_missing"


def test_bad_objects_are_rejected_before_any_ir_is_built(project: Path):
    with pytest.raises(plan.PlanError) as ei:
        _compile(project, [{"type": "text", "id": "t", "text": "x", "x_mm": "nan"}])
    assert ei.value.code == "bad_object"
    with pytest.raises(plan.PlanError) as ei2:
        _compile(project, [{"type": "widget", "id": "w"}])
    assert ei2.value.code == "bad_object"
    with pytest.raises(ir.IRError) as ei3:  # 页面尺寸非法在 validate 那一层拒
        _compile(project, [], page=(0.0, 10.0))
    assert ei3.value.code == "non_positive_size"
