"""统一 ExportRequest（ADR 0031）：规范化、文件名规则、覆盖策略。

文件名规则是**严格同源对**（`web/src/lib/exportName.ts`）。这里跑 Python 侧，
`web/src/lib/exportName.golden.test.ts` 跑 TS 侧，两边**同一份向量**。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tavotto.engine import chartprofile, documents, exportreq, patchspec

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "filename_vectors.json"


def _chart_export_spec(scope="original"):
    vectors = json.loads((GOLDEN.parent / "chart_model_vectors.json").read_text(encoding="utf-8"))
    valid = next(v for v in vectors["vectors"] if v["expect"] == "ok")
    chart = {"model": valid["model"], "binding": valid["binding"]}
    source = {"figure_id": "chart.svg", "chart": chart}
    if scope == "canvas":
        source = {
            "page_w_mm": 180,
            "page_h_mm": 120,
            "objects": [{"type": "panel", "id": "chart.svg", "chart": chart}],
        }
    return {"scope": scope, "formats": ["svg"], scope: source}, chart


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_chart_export_normalization_freezes_and_identifies_model(scope):
    spec, chart = _chart_export_spec(scope)
    request = exportreq.normalize(spec)
    frozen = request.original.chart if scope == "original" else request.canvas.objects[0]["chart"]
    assert frozen == chart and frozen is not chart
    before = deepcopy(frozen)
    identity = exportreq.render_plan_ref(request, [])["plan_identity"]
    chart["model"]["axes"][0]["xlim"][1] += 1
    assert frozen == before
    changed = exportreq.normalize(spec)
    assert exportreq.render_plan_ref(changed, [])["plan_identity"] != identity


@pytest.mark.parametrize("scope", ["original", "canvas"])
@pytest.mark.parametrize("bad_chart", [True, [], {}, {"model": {}}, {"model": {}, "binding": {}}])
def test_invalid_chart_does_not_silently_export_the_original(scope, bad_chart):
    spec, _ = _chart_export_spec(scope)
    target = spec[scope] if scope == "original" else spec[scope]["objects"][0]
    target["chart"] = bad_chart
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code.startswith("chart_")


def test_merge_pdf_identity_includes_every_chart_page():
    spec, chart = _chart_export_spec()
    spec["formats"] = ["pdf"]
    page = spec.pop("original")
    spec["merge_pdf"] = {"pages": [page, deepcopy(page)]}
    before = exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    spec["merge_pdf"]["pages"][1]["chart"]["model"]["axes"][0]["ylim"][1] += 1
    after = exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    assert before != after


def _native_chart_spec(scope):
    spec, _chart = _chart_export_spec("canvas" if scope == "canvas" else "original")
    if scope == "merge":
        spec["formats"] = ["pdf"]
        target = spec.pop("original")
        spec["merge_pdf"] = {"pages": [target]}
    else:
        target = spec[scope] if scope == "original" else spec[scope]["objects"][0]
    target["chart_render_profile"] = chartprofile.NATIVE_PROFILE
    return spec, target


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
def test_native_chart_freezes_complete_order_identity_and_profile_in_plan(scope):
    spec, target = _native_chart_spec(scope)
    edits = [
        {
            "gid": "axes_0.lines_0",
            "prop": "color",
            "value": "red",
            "identity": "l1:0123456789abcdef",
        },
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "blue"},
    ]
    target["overrides"] = deepcopy(edits)
    parsed = exportreq.normalize(spec)
    if scope == "canvas":
        frozen = parsed.canvas.objects[0]["overrides"]
        assert parsed.canvas.objects[0]["chart_render_profile"] == chartprofile.NATIVE_PROFILE
    else:
        frozen = parsed.original.overrides
        assert parsed.original.chart_render_profile == chartprofile.NATIVE_PROFILE
    assert frozen == edits
    first = exportreq.render_plan_ref(parsed, [])["plan_identity"]
    target["overrides"][0]["value"] = "green"
    assert frozen == edits
    assert exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"] != first
    target["overrides"] = list(reversed(edits[:2]))
    reversed_identity = exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    target["overrides"] = edits[:2]
    assert patchspec.patch_hash(edits[:2]) == patchspec.patch_hash(list(reversed(edits[:2])))
    assert (
        exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
        != reversed_identity
    )
    target["overrides"] = []
    native = exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    target["chart_render_profile"] = chartprofile.LEGACY_PROFILE
    assert exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"] != native


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize(
    "edits",
    [
        None,
        False,
        {},
        "",
        [None],
        [1],
        [{"gid": "a", "prop": "color"}],
        [{"gid": "a", "prop": "color", "value": "red", "extra": 1}],
        [{"gid": "a", "prop": "linewidth", "value": float("nan")}],
    ],
)
def test_native_chart_invalid_edits_are_not_silently_filtered_or_defaulted(scope, edits):
    spec, target = _native_chart_spec(scope)
    target["overrides"] = edits
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize("profile", [None, "native", "native-manifest/3", True])
def test_export_rejects_unknown_chart_render_profile(scope, profile):
    # native-manifest/2 自本步起是受支持 profile（PROFILES 扩容，exportreq
    # 共享同一闭集）；仍不存在的 /3 继续钉「闭集外拒收」。
    spec, target = _native_chart_spec(scope)
    target["chart_render_profile"] = profile
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_unsupported"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
def test_export_accepts_native_manifest_2_chart_render_profile(scope):
    """PROFILES 扩容后 exportreq.validate_profile 自动收 /2（PLAN §一：不加码，加测试）。"""
    spec, target = _native_chart_spec(scope)
    target["chart_render_profile"] = chartprofile.COMPLETE_PROFILE
    normalized = exportreq.normalize(spec)
    if scope == "canvas":
        assert normalized.canvas.objects[0]["chart_render_profile"] == (
            chartprofile.COMPLETE_PROFILE
        )
    else:
        assert normalized.original.chart_render_profile == chartprofile.COMPLETE_PROFILE


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
def test_chart_profile_requires_chart_and_legacy_cannot_lose_nonempty_edits(scope):
    spec, target = _native_chart_spec(scope)
    chart = target.pop("chart")
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"
    target["chart"] = chart
    target.pop("chart_render_profile")
    target["overrides"] = [{"gid": "axes_0", "prop": "xlim", "value": [0, 10]}]
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"
    target["overrides"] = []
    legacy = exportreq.normalize(spec)
    if scope == "canvas":
        assert "chart_render_profile" not in legacy.canvas.objects[0]
    else:
        assert legacy.original.chart_render_profile is None


def test_native_profile_on_nonpanel_canvas_object_is_not_admitted():
    spec, target = _native_chart_spec("canvas")
    target["type"] = "text"
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


def _referenced_chart_spec(scope):
    spec, target = _native_chart_spec(scope if scope in ("canvas", "merge") else "original")
    target["chart_source"] = {
        "schema": 1,
        "sourceId": "chart.svg",
        "bytesSha256": "b" * 64,
        "sizeBytes": 123,
        "sceneDigest": target["chart"]["model"]["scene_digest"],
    }
    if scope in ("annotated", "pad"):
        target.update(w_mm=100, h_mm=80)
        if scope == "annotated":
            target["objects"] = [{"type": "text", "text": "Caption", "x": 1, "y": 2}]
        else:
            target["pad_mm"] = [2, 3, 4, 5]
    if scope == "merge":
        second = deepcopy(target)
        second["chart_source"]["bytesSha256"] = "c" * 64
        spec["merge_pdf"]["pages"].append(second)
    return spec, target


@pytest.mark.parametrize("scope", ["original", "annotated", "pad", "canvas", "merge"])
@pytest.mark.parametrize("native", [False, True])
def test_chart_source_is_validated_frozen_and_identified_in_each_export_scope(scope, native):
    spec, target = _referenced_chart_spec(scope)
    if not native:
        target.pop("chart_render_profile")
    else:
        target["overrides"] = [
            {"gid": "axes_0", "prop": "xlim", "value": [0, 3]},
            {
                "gid": "axes_0.lines_0",
                "prop": "color",
                "value": "red",
                "identity": "l1:0123456789abcdef",
            },
            {"gid": "axes_0", "prop": "xlim", "value": [0, 5]},
        ]
    before = deepcopy(target)
    parsed = exportreq.normalize(spec)
    frozen = (
        parsed.canvas.objects[0]["chart_source"]
        if scope == "canvas"
        else parsed.original.chart_source
    )
    assert frozen == before["chart_source"] and frozen is not target["chart_source"]
    identity = exportreq.render_plan_ref(parsed, [])["plan_identity"]
    for key, changed in (("bytesSha256", "d" * 64), ("sizeBytes", 124)):
        target["chart_source"][key] = changed
        assert exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"] != identity
        target["chart_source"][key] = before["chart_source"][key]
    target["chart_source"]["bytesSha256"] = "d" * 64
    assert frozen == before["chart_source"]
    assert exportreq.render_plan_ref(parsed, [])["plan_identity"] == identity
    if native:
        target["overrides"][0]["value"][1] = 99
        edits = (
            parsed.canvas.objects[0]["overrides"]
            if scope == "canvas"
            else parsed.original.overrides
        )
        assert edits == before["overrides"]
    if scope == "canvas":
        assert "chart_source" not in parsed.to_payload()  # 既有摘要不展开 objects。
    else:
        assert parsed.to_payload()["chart_source"] == before["chart_source"]
    if scope == "merge":
        assert parsed.merge_pdf.pages[1].chart_source["bytesSha256"] == "c" * 64
        assert set(parsed.to_payload()["merge_pdf"]) == {"page_count", "figure_ids"}


def test_chart_source_identity_includes_nonfirst_merge_pages():
    spec, _target = _referenced_chart_spec("merge")
    before = exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    spec["merge_pdf"]["pages"][1]["chart_source"]["bytesSha256"] = "d" * 64
    assert exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"] != before


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize(
    "bad",
    [None, True, [], {}, "reference", {"path": "chart_sources/anything.svg"}],
)
def test_chart_source_explicit_null_or_nonreference_is_not_silently_ignored(scope, bad):
    spec, target = _referenced_chart_spec(scope)
    target["chart_source"] = bad
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", True),
        ("schema", 2),
        ("sourceId", ""),
        ("sourceId", " \t"),
        ("sourceId", "bad\x00source"),
        ("bytesSha256", "B" * 64),
        ("bytesSha256", "b" * 63),
        ("sizeBytes", True),
        ("sizeBytes", 0),
        ("sizeBytes", 1.5),
        ("sizeBytes", float("inf")),
        ("sizeBytes", 10**12),
        ("sceneDigest", "g" * 64),
        ("path", "../../outside.svg"),
    ],
)
def test_chart_source_closed_fields_and_bounds_come_from_documents(scope, key, value):
    spec, target = _referenced_chart_spec(scope)
    target["chart_source"][key] = value
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


@pytest.mark.parametrize("key", ["schema", "sourceId", "bytesSha256", "sizeBytes", "sceneDigest"])
def test_chart_source_cannot_omit_any_required_field(key):
    spec, target = _referenced_chart_spec("original")
    del target["chart_source"][key]
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize("key,value", [("sourceId", "other.svg"), ("sceneDigest", "d" * 64)])
def test_chart_source_must_match_the_panel_source_and_chart_scene(scope, key, value):
    spec, target = _referenced_chart_spec(scope)
    target["chart_source"][key] = value
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_source_mismatch"


@pytest.mark.parametrize("source_id", [None, "", 4, False])
def test_referenced_canvas_chart_requires_an_existing_string_source_id(source_id):
    spec, target = _referenced_chart_spec("canvas")
    target["id"] = source_id
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_source_mismatch"
    target.pop("id")
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_source_mismatch"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize("chart_is_null", [False, True])
def test_chart_source_requires_chart_even_without_a_render_profile(scope, chart_is_null):
    spec, target = _referenced_chart_spec(scope)
    target.pop("chart_render_profile")
    target.pop("chart")
    if chart_is_null:
        target["chart"] = None
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


def test_chart_source_cannot_be_attached_to_nonpanel_canvas_objects():
    spec, target = _referenced_chart_spec("canvas")
    target.pop("chart_render_profile")
    target["type"] = "text"
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_model_invalid"


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
def test_chart_source_validation_delegates_identity_and_errors_to_documents(scope, monkeypatch):
    spec, target = _referenced_chart_spec(scope)
    calls = []

    def reject(raw, *, source_id=None, scene_digest=None):
        calls.append((raw, source_id, scene_digest))
        raise documents.DocumentError("chart_source_mismatch", "authoritative validator rejection")

    monkeypatch.setattr(documents, "validate_chart_source", reject)
    with pytest.raises(exportreq.ExportRequestError) as caught:
        exportreq.normalize(spec)
    assert caught.value.code == "chart_source_mismatch"
    assert caught.value.message == "authoritative validator rejection"
    assert calls == [
        (target["chart_source"], "chart.svg", target["chart"]["model"]["scene_digest"])
    ]


@pytest.mark.parametrize("scope", ["original", "canvas", "merge"])
@pytest.mark.parametrize("with_chart", [False, True])
def test_absent_chart_source_does_not_add_optional_fields_to_legacy_payloads(scope, with_chart):
    spec, target = _native_chart_spec(scope)
    target.pop("chart_render_profile")
    if not with_chart:
        target.pop("chart")
    parsed = exportreq.normalize(spec)
    summary = parsed.to_payload()
    plan = exportreq.render_plan_ref(parsed, [])
    assert "chart_source" not in json.dumps(summary)
    assert "chart_source" not in json.dumps(plan)
    if scope == "canvas":
        assert "chart_source" not in parsed.canvas.objects[0]
    else:
        assert parsed.original.chart_source is None
    assert plan["request"] == summary


# ----------------------------- golden 向量 ----------------------------------
def test_golden_vectors_match_this_implementation():
    """向量文件与本实现一致（vitest 断言 TS 侧也一致，两边同一份输入）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_filename_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_golden_vectors_are_asserted_on_the_typescript_side_too():
    """光有 Python 一侧等于没看护——分叉正是从「只改了一边」开始的。"""
    ts = ROOT / "web" / "src" / "lib" / "exportName.golden.test.ts"
    assert ts.is_file()
    assert "tests/golden/filename_vectors.json" in ts.read_text(encoding="utf-8")


def test_golden_vectors_cover_every_rule():
    """每一条判据都得被向量碰过。碰不到的规则可以两侧分叉而没人发现。"""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = {c["reason"] for c in data["check"] if c["reason"]}
    assert seen == {
        "empty",
        "whitespace_edge",
        "too_long",
        "control_char",
        "illegal_char",
        "trailing_dot",
        "dot_only",
        "reserved_name",
    }


def test_edge_whitespace_is_not_the_language_builtin():
    """判据不许退回 `str.strip()`。

    `str.strip()` 与 JS 的 `String.trim()` 认的字符集**不一样**：U+FEFF 只有
    JS 认，`\\x1c`–`\\x1f` 只有 Python 认。谁用了自己语言的内建函数，
    两侧就会对同一个名字给出不同答案——而那种分叉在单侧用例里永远看不见。
    """
    assert exportreq.check_filename("﻿Fig") == "whitespace_edge"
    assert exportreq.check_filename("Fig﻿") == "whitespace_edge"
    # `\x1c` 是 Python 的 strip() 认、JS 的 trim() 不认的那一类：两侧都必须
    # 把它判成控制字符（而不是一边"首尾空白"、一边"控制字符"）
    assert exportreq.check_filename("Fig\x1c") == "control_char"


# ------------------------------ 规范化 --------------------------------------
def _canvas_spec(**over):
    base = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 600,
        "canvas": {"page_w_mm": 80, "page_h_mm": 60, "objects": []},
    }
    base.update(over)
    return base


def test_ppi_is_none_when_no_raster_format():
    """**「这次 PPI 没有意义」与「PPI 是 600」是两个不同的答案。**

    压成一个默认数字的话，界面就会去显示一个不影响任何东西的设置，
    而用户会以为改它有用（T-49 同一个形状）。
    """
    req = exportreq.normalize(_canvas_spec(formats=["pdf"]))
    assert req.ppi is None
    assert req.has_raster is False
    assert exportreq.normalize(_canvas_spec(formats=["png"])).ppi == 600


def test_format_order_is_stable_not_click_order():
    """结果里的 outputs[] 要与界面上的清单逐项对上，而点勾选框的先后不是身份。"""
    assert exportreq.normalize(_canvas_spec(formats=["png", "pdf"])).formats == ("pdf", "png")
    assert exportreq.normalize(_canvas_spec(formats=["pdf", "pdf"])).formats == ("pdf",)
    # 新格式**追加**在老的两个后面：老客户端只勾 pdf+png 时的顺序一个字节不变
    assert exportreq.normalize(_canvas_spec(formats=["tiff", "eps", "png", "pdf"])).formats == (
        "pdf",
        "png",
        "eps",
        "tiff",
    )


def test_tiff_is_raster_and_eps_is_vector_for_the_ppi_rule():
    """「PPI 只在有位图格式时是数字」这句话对新格式同样成立（ADR 0046）。

    `tiff` 单独一个就该让 ppi 变成数字；`eps` 单独一个必须让它是 `None`——
    把 EPS 误归进位图，界面就会为一份矢量文件摆出一个不起作用的分辨率选择。
    """
    assert exportreq.normalize(_canvas_spec(formats=["tiff"])).ppi == 600
    assert exportreq.normalize(_canvas_spec(formats=["eps"])).ppi is None
    assert exportreq.normalize(_canvas_spec(formats=["pdf", "eps"])).ppi is None
    assert exportreq.normalize(_canvas_spec(formats=["eps", "tiff"], ppi=300)).ppi == 300
    req = exportreq.normalize(_canvas_spec(formats=["eps", "tiff"]))
    assert req.has_raster and req.has_vector
    assert exportreq.FORMAT_TIFF in exportreq.RASTER_FORMATS
    assert exportreq.FORMAT_EPS in exportreq.VECTOR_FORMATS
    # 两条入口认同一个格式集合，但各自保持已经发布的输出顺序。
    assert set(exportreq.ENGINE_FORMATS) == set(exportreq.FORMATS)
    assert set(exportreq.FORMATS) == exportreq.RASTER_FORMATS | exportreq.VECTOR_FORMATS


def test_svg_is_appended_without_reordering_existing_formats_or_engine_outputs():
    assert exportreq.FORMATS == ("pdf", "png", "eps", "tiff", "svg")
    spec = _canvas_spec(formats=["svg", "tiff", "eps", "png", "pdf", "svg"])
    assert exportreq.normalize(spec).formats == ("pdf", "png", "eps", "tiff", "svg")
    assert exportreq.ENGINE_FORMATS == ("pdf", "png", "svg", "eps", "tiff")
    assert exportreq.normalize(spec, allowed_formats=exportreq.ENGINE_FORMATS).formats == (
        "pdf",
        "png",
        "svg",
        "eps",
        "tiff",
    )


@pytest.mark.parametrize("scope", ["canvas", "original"])
def test_svg_request_is_vector_and_round_trips_with_no_ppi(scope):
    spec = _canvas_spec(formats=["svg"], filename="Fig 1.SVG", ppi="unused")
    if scope == "original":
        spec.update(scope=scope, original={"figure_id": "Fig 1.pdf"})
        spec.pop("canvas")
    req = exportreq.normalize(spec)
    assert req.formats == ("svg",)
    assert req.has_vector and not req.has_raster
    assert req.ppi is None
    assert req.filename == "Fig 1"
    assert exportreq.output_name(req.filename, "svg") == "Fig 1.svg"
    assert req.to_payload()["ppi"] is None
    assert req.to_payload()["formats"] == ["svg"]
    assert exportreq.normalize(_canvas_spec(formats=["svg", "png"], ppi=300)).ppi == 300


def test_tif_alias_typed_by_the_user_is_stripped_too():
    """我们只产出 `.tiff`，但用户顺手打的 `.tif` / `.eps` 也得吃掉。"""
    assert (
        exportreq.normalize(_canvas_spec(filename="Fig 1.tif", formats=["tiff"])).filename
        == "Fig 1"
    )
    assert (
        exportreq.normalize(_canvas_spec(filename="Fig 1.eps", formats=["pdf"])).filename == "Fig 1"
    )
    assert exportreq.output_name("Fig 1", "tiff") == "Fig 1.tiff"


@pytest.mark.parametrize(
    "spec, code",
    [
        ({"formats": []}, "no_format"),
        ({"formats": ["docx"]}, "unsupported_format"),
        ({"ppi": "x"}, "bad_ppi"),
        ({"ppi": 5}, "ppi_out_of_range"),
        ({"ppi": 99999}, "ppi_out_of_range"),
        ({"filename": "Fig?1"}, "bad_filename"),
        ({"filename": ""}, "bad_filename"),
        ({"scope": "half"}, "bad_scope"),
        ({"overwrite": "maybe"}, "bad_overwrite"),
        ({"background": "plaid"}, "bad_background"),
    ],
)
def test_bad_requests_fail_before_touching_the_disk(spec, code):
    with pytest.raises(exportreq.ExportRequestError) as exc:
        exportreq.normalize(_canvas_spec(**spec))
    assert exc.value.code == code


def test_extension_typed_by_the_user_is_stripped_once():
    assert exportreq.normalize(_canvas_spec(filename="Fig 1.pdf")).filename == "Fig 1"
    # 一次只剥一层：剥到底的话 `data.tar.gz.png` 会变成 `data.tar`
    assert exportreq.normalize(_canvas_spec(filename="v1.2")).filename == "v1.2"


def test_original_scope_has_no_layout_fields_at_all():
    """`scope=original` 里**根本没有** x/y/w/h 与页面尺寸。

    不是"记得别用"——那几个键不在 `OriginalSource` 上。想让画布缩放漏进
    原图导出，得先改这个结构，而改结构会当场撞上这条用例。
    """
    req = exportreq.normalize(
        {
            "scope": "original",
            "filename": "Fig 1",
            "formats": ["pdf"],
            "original": {
                "figure_id": "Fig1.pdf",
                "w_mm": 80,
                "h_mm": 60,
                "ignored": ["scale", "crop"],
                # 混进来的布局字段被规范化层原样丢掉
                "x_mm": 10,
                "w": 40,
                "page_w_mm": 180,
            },
        }
    )
    assert req.canvas is None
    assert req.original is not None
    assert not hasattr(req.original, "x_mm")
    assert not hasattr(req.original, "page_w_mm")
    assert req.original.ignored == ("scale", "crop")


def test_legacy_payload_still_normalizes():
    """老标签页（`stem` + `items[]` + `texts[]`）抬成同一个作业。"""
    req = exportreq.normalize(
        {
            "page_w_mm": 100,
            "page_h_mm": 50,
            "dpi": 300,
            "formats": ["pdf"],
            "stem": "老 布局/名",
            "items": [{"id": "a.pdf", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10}],
            "texts": [{"text": "hi", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 5, "size_pt": 9}],
        }
    )
    assert req.legacy_naming is True
    assert req.filename == "老_布局_名"  # 旧路径清洗，不当场报错
    assert req.scope == "canvas"
    assert [o["type"] for o in req.canvas.objects] == ["panel", "text"]
    # 旧路径带时间戳，天生撞不了车，所以默认是 replace 而不是 ask
    assert req.overwrite == "replace"


def test_new_payload_defaults_to_asking_before_overwriting():
    """**静默覆盖用户上一次的成果是不可逆的**，所以默认先问一句。"""
    assert exportreq.normalize(_canvas_spec()).overwrite == "ask"


def test_legacy_overwrite_boolean_is_still_understood():
    """`scripts/smoke_app.py` 至今发的是 `overwrite: true`。"""
    assert exportreq.normalize(_canvas_spec(overwrite=True)).overwrite == "replace"


def test_dedupe_numbering():
    taken = {"Fig 1.pdf", "Fig 1 (2).pdf"}
    assert exportreq.dedupe_name("Fig 1", "pdf", lambda n: n in taken) == "Fig 1 (3).pdf"
    assert exportreq.dedupe_name("Fig 1", "png", lambda n: n in taken) == "Fig 1.png"


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_saved_figure_reference_is_a_closed_request_field(scope):
    reference = {
        "version": 1,
        "sourceId": "runtime:plot.py#F",
        "patchHash": "sha256:" + "1" * 64,
        "receiptId": "sha256:" + "2" * 64,
        "snapshotId": "3" * 64,
    }
    spec = {"scope": scope, "filename": "saved", "formats": ["svg"]}
    if scope == "original":
        source = {"figure_id": reference["sourceId"], "figure_snapshot": reference}
        spec["original"] = source
    else:
        source = {"type": "panel", "id": reference["sourceId"], "figure_snapshot": reference}
        spec["canvas"] = {"page_w_mm": 100, "page_h_mm": 50, "objects": [source]}
    req = exportreq.normalize(spec)
    assert (
        req.original.figure_snapshot
        if scope == "original"
        else req.canvas.objects[0]["figure_snapshot"]
    ) == reference
    source["figure_snapshot"] = {**reference, "argv": ["private"]}
    with pytest.raises(exportreq.ExportRequestError, match="Invalid Figure snapshot"):
        exportreq.normalize(spec)
