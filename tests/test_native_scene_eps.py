"""完整原图 EPS：独立 Ghostscript 读取；缺读取器时明确 skip，绝不冒充像素通过。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tavotto.rendercore import epscontainer, epswriter, ir, originaleps, svgir
from tavotto.rendercore.svgcheck import SVGError


def svg(body, width=100, height=100, attrs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" '
        f'viewBox="0 0 {width} {height}" {attrs}>{body}</svg>'
    ).encode()


@pytest.fixture(scope="module")
def faces():
    from tavotto.rendercore.fonts import FontRegistry
    from tavotto.rendercore.hbshaper import HbFaceProvider

    pytest.importorskip("uharfbuzz")
    pytest.importorskip("fontTools")
    registry = FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"approved fonts missing (not-run): {registry.missing}")
    return HbFaceProvider(registry)


@pytest.fixture(scope="module")
def gs():
    path = os.environ.get("TAVOTTO_TEST_GHOSTSCRIPT") or next(
        (value for name in ("gswin64c", "gs") if (value := shutil.which(name))), None
    )
    if not path:
        pytest.skip("independent PS reader unavailable; EPS pixels not-run")
    result = subprocess.run([path, "-version"], capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return path


def ps_image(tmp_path, gs, blob, name="scene", scale=4):
    from PIL import Image

    source = tmp_path / f"{name}.eps"
    output = tmp_path / f"{name}.png"
    source.write_bytes(blob)
    result = subprocess.run(
        [
            gs,
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-dEPSCrop",
            "-sDEVICE=png16m",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            f"-r{72 * scale}",
            f"-sOutputFile={output}",
            str(source),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(
        errors="replace"
    )
    with Image.open(output) as image:
        return image.convert("RGB")


def pdf_image(tmp_path, data, w, h, faces, name="reference"):
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("pikepdf")
    from tavotto.rendercore.pdfwriter import write_pdf

    files = {}
    page, _ = svgir.svg_to_ir(data, w, h, faces, files=files)
    output = tmp_path / f"{name}.pdf"
    write_pdf(page, output, faces, files=files)
    document = pdfium.PdfDocument(str(output))
    pdf_page = document[0]
    bitmap = pdf_page.render(scale=4)
    image = bitmap.to_pil().convert("RGB")
    textpage = pdf_page.get_textpage()
    text = textpage.get_text_range()
    textpage.close()
    bitmap.close()
    pdf_page.close()
    document.close()
    return image, text


def assert_same_ink(actual, reference):
    """Two independent AA rasterizers: every ink pixel has a <=1px counterpart.

    At 288dpi this bounds the outline difference to .25pt, rather than making
    a global MAE tolerate a missing small decoration/text region.
    """
    from PIL import ImageChops, ImageFilter

    assert actual.size == reference.size
    a = actual.convert("L").point(lambda p: 255 if p < 240 else 0)
    b = reference.convert("L").point(lambda p: 255 if p < 240 else 0)
    for ink, other in ((a, b), (b, a)):
        missing = ImageChops.subtract(ink, other.filter(ImageFilter.MaxFilter(3)))
        assert missing.getbbox() is None, missing.getbbox()


def test_payload_is_closed_and_binds_complete_source_and_ps(faces):
    source = svg('<rect id="unbound" x="10" y="20" width="7" height="8" fill="green"/>')
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    payload = json.loads(json.dumps(prepared.to_payload()))
    assert set(payload) == epscontainer._FIELDS
    assert payload["source_svg_sha256"] == hashlib.sha256(source).hexdigest()
    assert payload["ps_sha256"] == hashlib.sha256(payload["ps_ascii"].encode()).hexdigest()
    assert prepared.facts["paths"] == 1
    assert prepared.expanded_cost > 1
    assert source == bytes(source)


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda p: p.update(extra=True), "native_eps_invalid_payload"),
        (lambda p: p.update(schema="scene-ps/2"), "native_eps_invalid_payload"),
        (lambda p: p.update(width_pt=True), "native_eps_invalid_payload"),
        (lambda p: p.update(width_pt=float("inf")), "native_eps_invalid_payload"),
        (lambda p: p.update(width_pt=10**1000), "native_eps_invalid_payload"),
        (lambda p: p.update(height_pt=0), "native_eps_invalid_payload"),
        (lambda p: p.update(source_svg_sha256="x" * 64), "native_eps_invalid_payload"),
        (lambda p: p.update(ps_ascii="showpage"), "native_eps_invalid_payload"),
        (lambda p: p.update(ps_ascii="中文"), "native_eps_invalid_payload"),
        (lambda p: p.update(expanded_cost=True), "native_eps_budget"),
        (
            lambda p: p.update(expanded_cost=epscontainer.MAX_EXPANDED_COST + 1),
            "native_eps_budget",
        ),
    ],
)
def test_worker_rejects_invalid_payload_before_matplotlib(faces, monkeypatch, mutate, code):
    prepared = originaleps.prepare_scene_ps(svg(""), 100, 100, faces=faces)
    payload = prepared.to_payload()
    mutate(payload)
    with pytest.raises(epscontainer.SceneEPSError) as caught:
        epscontainer.pack_prepared_eps(payload)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "body,operation",
    [
        ('<rect width="50" height="50" fill="red" opacity=".5"/>', "group_opacity"),
        ('<rect width="50" height="50" fill="red" fill-opacity=".5"/>', "object_alpha"),
        (
            '<g opacity=".5"><rect width="60" height="60" fill="red"/>'
            '<rect x="30" width="60" height="60" fill="blue"/></g>',
            "group_opacity",
        ),
        (
            '<defs><clipPath id="c"><rect width="50" height="50"/>'
            '<circle cx="50" cy="50" r="20"/></clipPath></defs>'
            '<g clip-path="url(#c)" opacity=".5"><rect width="100" height="100"/></g>',
            "group_opacity",
        ),
    ],
)
def test_partial_alpha_cannot_become_opaque_or_per_child_alpha(faces, body, operation):
    with pytest.raises(epswriter.UnsupportedCapability) as caught:
        originaleps.prepare_scene_ps(svg(body), 100, 100, faces=faces)
    assert caught.value.code == "unsupported_capability"
    assert caught.value.params["operation"] == operation


@pytest.mark.parametrize(
    "body,code",
    [
        (
            '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/>'
            '<stop offset="1" stop-color="blue"/></linearGradient></defs>'
            '<rect width="100" height="100" fill="url(#g)"/>',
            "svgir_gradient",
        ),
        (
            '<defs><mask id="m"><rect width="100" height="100" fill="white"/></mask></defs>'
            '<rect width="100" height="100" mask="url(#m)"/>',
            "svgir_mask",
        ),
        (
            '<defs><clipPath id="c" clipPathUnits="objectBoundingBox">'
            '<circle cx=".5" cy=".5" r=".5"/></clipPath></defs>'
            '<rect width="100" height="100" clip-path="url(#c)"/>',
            "svgir_unsupported_element",
        ),
    ],
)
def test_shared_svgir_limit_is_preserved_without_second_parser(faces, body, code):
    with pytest.raises((svgir.SVGIRError, SVGError)) as caught:
        originaleps.prepare_scene_ps(svg(body), 100, 100, faces=faces)
    assert caught.value.code == code


def test_exact_zero_contribution_elision_preserves_remaining_paint(faces):
    source = svg(
        '<g opacity="0"><g opacity=".5"><rect width="100" height="100" fill="red"/>'
        '</g></g><rect width="100" height="100" fill="red" fill-opacity="0"/>'
        '<path d="M10 50H90" fill="none" stroke="blue" stroke-width="4"/>'
    )
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert prepared.facts["paths"] == 1
    assert "0 0 1 setrgbcolor stroke" in prepared.ps_ascii
    assert "1 0 0 setrgbcolor" not in prepared.ps_ascii
    # A group with no visible paint contributes nothing even with partial alpha.
    empty = ir.Group((ir.Path((("M", 0, 0),), fill=ir.Paint((1, 0, 0), 0)),), opacity=0.5)
    assert originaleps._opaque_node(empty) is None


def test_both_fill_and_stroke_are_checked_before_transparent_elision(faces):
    source = svg(
        '<rect width="50" height="50" fill="red" fill-opacity="0" '
        'stroke="blue" stroke-opacity=".5"/>'
    )
    with pytest.raises(epswriter.UnsupportedCapability) as caught:
        originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert caught.value.operation == "object_alpha"


def test_union_budget_preflight_happens_before_duplicate_allocation(faces, monkeypatch):
    source = svg(
        '<defs><clipPath id="c"><rect width="60" height="100"/>'
        '<rect x="40" width="60" height="100"/></clipPath></defs>'
        '<g clip-path="url(#c)"><path d="M0 0H100V100H0Z"/></g>'
    )
    monkeypatch.setattr(originaleps, "MAX_EXPANDED_COST", 10)
    monkeypatch.setattr(originaleps, "_lower", lambda _: pytest.fail("allocated before budget"))
    with pytest.raises(epscontainer.SceneEPSError) as caught:
        originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert caught.value.code == "native_eps_budget"


def test_ps_defs_and_body_share_byte_budget_and_worker_counts_json_escaping(faces, monkeypatch):
    source = svg('<text x="5" y="40" font-family="sans-serif" font-size="30">A</text>')
    monkeypatch.setattr(originaleps, "MAX_BYTES", 64)
    with pytest.raises(epscontainer.SceneEPSError) as caught:
        originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert caught.value.code == "native_eps_budget"
    monkeypatch.setattr(epscontainer, "MAX_BYTES", 400)
    ps = "\n" * 100
    payload = {
        "schema": epscontainer.SCHEMA,
        "ps_ascii": ps,
        "ps_sha256": hashlib.sha256(ps.encode()).hexdigest(),
        "source_svg_sha256": "0" * 64,
        "width_pt": 100,
        "height_pt": 100,
        "expanded_cost": 0,
    }
    assert len(ps) < epscontainer.MAX_BYTES
    with pytest.raises(epscontainer.SceneEPSError) as caught:
        epscontainer.validate_payload(payload)
    assert caught.value.code == "native_eps_budget"


def test_worker_container_has_existing_matplotlib_only_closure(tmp_path, faces):
    prepared = originaleps.prepare_scene_ps(
        svg('<text x="5" y="40" font-family="sans-serif" font-size="20">Á</text>'),
        100,
        100,
        faces=faces,
    )
    script = r"""
import importlib.abc, json, sys
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *_args):
        if fullname.split('.')[0] in {'uharfbuzz','pikepdf','pypdfium2','fitz','pymupdf'}:
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, Missing())
from tavotto.rendercore.epscontainer import pack_prepared_eps
blob = pack_prepared_eps(json.loads(sys.stdin.buffer.read()))
assert not {'uharfbuzz','pikepdf','pypdfium2','fitz','pymupdf'}.intersection(sys.modules)
sys.stdout.buffer.write(blob)
"""
    env = dict(os.environ, SOURCE_DATE_EPOCH="0", MPLCONFIGDIR=str(tmp_path / "mpl"))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        input=json.dumps(prepared.to_payload()).encode(),
        capture_output=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.startswith(b"%!PS-Adobe-3.0 EPSF-3.0")
    assert prepared.ps_ascii.encode() in result.stdout


def test_host_rc_cannot_enable_tex_distiller_or_crop_scene(tmp_path, faces, monkeypatch):
    import matplotlib as mpl
    from matplotlib.artist import Artist

    prepared = originaleps.prepare_scene_ps(
        svg('<rect width="30" height="20"/>'), 100, 100, faces=faces
    )
    original = Artist.__init__
    checks = []

    def init(self, *args, **kwargs):
        checks.append((mpl.rcParams["text.usetex"], mpl.rcParams["ps.usedistiller"]))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Artist, "__init__", init)
    with mpl.rc_context(
        {"text.usetex": True, "ps.usedistiller": "ghostscript", "savefig.bbox": "tight"}
    ):
        blob = epscontainer.pack_prepared_eps(prepared.to_payload())
        assert mpl.rcParams["text.usetex"] is True
        assert mpl.rcParams["ps.usedistiller"] == "ghostscript"
    assert checks and all(value == (False, None) for value in checks)
    assert b"%%HiResBoundingBox: 0.000000 0.000000 100.000000 100.000000" in blob


def test_final_eps_bytes_are_bounded_including_matplotlib_container(faces, monkeypatch):
    prepared = originaleps.prepare_scene_ps(svg(""), 100, 100, faces=faces)
    payload = prepared.to_payload()
    limit = len(json.dumps(payload, separators=(",", ":"))) + 10
    assert len(epscontainer.pack_prepared_eps(payload)) > limit
    monkeypatch.setattr(epscontainer, "MAX_BYTES", limit)
    with pytest.raises(epscontainer.SceneEPSError) as caught:
        epscontainer.pack_prepared_eps(payload)
    assert caught.value.code == "native_eps_budget"


def test_native_and_scientific_adapters_are_not_loaded_when_preparer_is_imported():
    # The worker container must not require shaping; the parent preparer must
    # not start the scientific stack. Test fresh processes, not our pytest cache.
    script = r"""
import sys
import tavotto.rendercore.originaleps
assert not {'matplotlib','numpy','uharfbuzz','pikepdf','pypdfium2'}.intersection(sys.modules)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        timeout=10,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize(
    "body,probes",
    [
        (
            '<path fill="blue" fill-rule="evenodd" d="M10 10H90V90H10Z M40 40H60V60H40Z"/>',
            [(20, 50, (0, 0, 255)), (50, 50, (255, 255, 255))],
        ),
        (
            '<defs><clipPath id="c"><path clip-rule="evenodd" '
            'd="M0 0H100V100H0Z M40 40H60V60H40Z"/></clipPath></defs>'
            '<rect width="100" height="100" fill="blue" clip-path="url(#c)"/>',
            [(20, 50, (0, 0, 255)), (50, 50, (255, 255, 255))],
        ),
        (
            '<g transform="translate(10 5) scale(2 1)"><path d="M10 10V80" '
            'fill="none" stroke="blue" stroke-width="2"/></g>',
            [(28.5, 50, (0, 0, 255)), (32.5, 50, (255, 255, 255))],
        ),
        (
            '<defs><clipPath id="c"><circle cx="50" cy="50" r="35"/></clipPath></defs>'
            '<g clip-path="url(#c)"><path d="M10 20L50 80L90 20" fill="none" '
            'stroke="blue" stroke-width="6" stroke-linecap="round" stroke-linejoin="miter" '
            'stroke-miterlimit="2" stroke-dasharray="8 3"/></g>',
            [(10, 10, (255, 255, 255))],
        ),
    ],
)
def test_independent_ps_pixels_holes_clips_nonuniform_stroke(tmp_path, gs, faces, body, probes):
    source = svg(body)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    image = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    reference, _ = pdf_image(tmp_path, source, 100, 100, faces)
    assert image.size == (400, 400)
    for x, y, color in probes:
        assert image.getpixel((int(x * 4), int(y * 4))) == color
        assert reference.getpixel((int(x * 4), int(y * 4))) == color
    assert_same_ink(image, reference)


def test_independent_union_pixels_preserve_member_holes_order_and_cached_glyph_defs(
    tmp_path, gs, faces
):
    source = svg(
        '<defs><clipPath id="c"><path clip-rule="evenodd" '
        'd="M10 20H90V80H10Z M40 40H60V60H40Z"/>'
        '<path d="M45 40V60H55V40Z"/></clipPath></defs>'
        '<g clip-path="url(#c)"><rect width="100" height="100" fill="red"/>'
        '<circle cx="50" cy="50" r="20" fill="blue"/>'
        '<text x="15" y="35" font-family="sans-serif" font-size="10">AAA</text></g>'
    )
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert prepared.ps_ascii.count("/tvg0 {") == 1
    assert prepared.facts["outlined_glyphs"] == 6  # replay subtree, shared outline definition
    image = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    reference, _ = pdf_image(tmp_path, source, 100, 100, faces)
    for x, color in (
        (42, (255, 255, 255)),
        (50, (0, 0, 255)),
        (65, (0, 0, 255)),
        (80, (255, 0, 0)),
    ):
        assert image.getpixel((x * 4, 50 * 4)) == color
        assert reference.getpixel((x * 4, 50 * 4)) == color
    assert_same_ink(image, reference)
    # The old annotation writer is unchanged; this capability is original-scene lowering.
    page, _ = svgir.svg_to_ir(source, 100, 100, faces)
    with pytest.raises(epswriter.UnsupportedCapability):
        epswriter.overlay_ps(page, faces)


def test_nested_union_intersects_ancestor_clips_in_transformed_space(tmp_path, gs, faces):
    source = svg(
        '<defs><clipPath id="a"><rect x="5" y="5" width="45" height="75"/>'
        '<circle cx="50" cy="45" r="30"/></clipPath>'
        '<clipPath id="b"><path clip-rule="evenodd" '
        'd="M0 0H80V80H0Z M20 20H60V60H20Z"/>'
        '<rect x="30" y="30" width="15" height="15"/></clipPath></defs>'
        '<g transform="translate(8 3)" clip-path="url(#a)">'
        '<g transform="rotate(8 40 40)" clip-path="url(#b)">'
        '<rect width="100" height="100" fill="red"/>'
        '<circle cx="40" cy="40" r="25" fill="blue"/></g></g>'
    )
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    assert prepared.facts["paths"] == 8
    image = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    reference, _ = pdf_image(tmp_path, source, 100, 100, faces)
    assert_same_ink(image, reference)


def test_independent_full_native_scene_keeps_unbound_rotated_text_decor_and_physical_size(
    tmp_path, gs, faces
):
    from tavotto.engine import chartmodel, epsfile, epsoverlay
    from tavotto.svgcompose import compose_native_scene
    from test_chart_native import native, setup_chart

    source, model, binding, frame = setup_chart(
        '<rect id="keep-green" x="52" y="22" width="7" height="6" fill="#00aa00"/>'
        '<g id="original"><path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g>'
        '<path id="keep-star" d="M135 25L140 35L130 35Z" fill="#aa00aa"/>'
        '<g id="keep-label" transform="rotate(-12 70 116)"><text x="70" y="116" '
        'font-family="Liberation Sans" font-size="5">UNBOUND Á 42</text></g>'
        '<text id="keep-cjk" x="60" y="112" font-family="Noto Sans SC" font-size="4">原图</text>',
        size=(200.25, 120.5),
        vb=(50, 20, 100, 100),
    )
    model["dataset"]["columns"]["y1"] = [0.25, 0.25]
    model["dataset"]["columns"]["y2"] = [0.75, 0.75]
    model["data_digest"] = chartmodel.digest_of(model["dataset"])
    scaffold = native(
        model,
        frame,
        [
            {"gid": "axes_0.xlabel", "prop": "text", "value": "Native EPS"},
            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5},
        ],
    )
    result = compose_native_scene(source, binding, scaffold, model)
    assert result["composition"]["composed"]
    prepared = originaleps.prepare_scene_ps(result["svg"], 200.25, 120.5, faces=faces)
    blob = epscontainer.pack_prepared_eps(prepared.to_payload())
    image = ps_image(tmp_path, gs, blob)
    assert image.size == (801, 482)
    reference, text = pdf_image(tmp_path, result["svg"], 200.25, 120.5, faces)
    assert "UNBOUND" in text and "原图" in text and "Native EPS" in text
    assert prepared.facts["outlined_glyphs"] >= 60
    assert_same_ink(image, reference)
    assert image.getpixel((5 * 4, 5 * 4)) == (0, 170, 0)
    assert image.getpixel((170 * 4, 13 * 4)) == (170, 0, 170)
    # Native fragment alone really loses unbound decor; an EPS header cannot pass this gate.
    fragment = originaleps.prepare_scene_ps(scaffold["fragment"], 200.25, 120.5, faces=faces)
    wrong = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(fragment.to_payload()), "wrong")
    with pytest.raises(AssertionError):
        assert_same_ink(wrong, reference)
    padded = epsoverlay.compose(blob, "", 230.25, 150.5, offset_pt=(10, 20), clip_page=True)
    padded_image = ps_image(tmp_path, gs, padded, "padded")
    assert padded_image.size == (921, 602)
    assert padded_image.getpixel((15 * 4, 15 * 4)) == (0, 170, 0)
    assert blob in padded  # prior byte-bound embed contract
    out = tmp_path / "padded.eps"
    out.write_bytes(padded)
    assert epsfile.bounding_box_pt(out) == (230.25, 150.5)


def test_original_image_is_not_silently_dropped_or_full_page_rasterized(faces):
    import base64

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (2, 2), "green").save(out, format="PNG")
    source = svg(
        '<image width="100" height="100" href="data:image/png;base64,'
        + base64.b64encode(out.getvalue()).decode()
        + '"/>'
    )
    files = {"sentinel": b"unchanged"}
    with pytest.raises(epswriter.UnsupportedCapability) as caught:
        originaleps.prepare_scene_ps(source, 100, 100, faces=faces, files=files)
    assert caught.value.operation == "image"
    assert files == {"sentinel": b"unchanged"}


def test_canvas_capability_and_legacy_overlay_rejections_are_unchanged():
    assert ir.CAPABILITIES["eps"]["path_fill"].level == ir.CAP_UNSUPPORTED
    path = ir.Path((("M", 0, 0), ("L", 10, 0)), stroke=ir.Stroke(ir.Paint((0, 0, 0), 0.5), 1))
    with pytest.raises(epswriter.UnsupportedCapability) as caught:
        epswriter.overlay_ps(ir.Page(100, 100, (path,)), None)
    assert caught.value.operation == "object_alpha"
    with pytest.raises(epswriter.UnsupportedCapability):
        originaleps._opaque_node(
            replace(path, stroke=replace(path.stroke, paint=ir.Paint((0, 0, 0), 0.5)))
        )
