"""X03: inspect sealed SVG bytes independently of its writer and fail closed on active content."""

from __future__ import annotations

import base64
import hashlib
import io
import struct
import zlib

import pytest

from tavotto.engine import artifactcheck
from tavotto.rendercore import inspector as I
from tavotto.rendercore.svgcheck import SVGBudget, SVGError, parse_svg, strip_matplotlib_doctype

ROOT = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="72pt" height="36pt" viewBox="0 0 72 36">'
PLAN = {"page_pt": [72, 36], "vector": True, "text": ["Expected label"]}


def svg(body="", *, root=ROOT):
    return (root + body + "</svg>").encode()


def png(width=1, height=1):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + chunk(b"IEND", b"")
    )


def data_image(raw=None):
    encoded = base64.b64encode(raw or png()).decode()
    return f'<image width="10" height="10" href="data:image/png;base64,{encoded}"/>'


def test_svg_integrity_dimensions_structure_and_outline_truth(tmp_path):
    path = tmp_path / "figure.svg"
    data = svg('<defs><path id="glyph" d="M0 0L2 2"/></defs><use href="#glyph"/>')
    path.write_bytes(data)
    result = I.inspect(path, "svg", plan=PLAN, policy="strict")
    assert result["policy"]["verdict"] == "accepted"
    assert result["observed"]["size_mm"] == [25.4, 12.7]
    assert result["observed"]["size_pt"] == [72, 36]
    assert result["observed"]["carrier"] == "vector"
    assert result["observed"]["text_mode"] == "outlines_or_absent"
    assert result["observed"]["fonts_used"] == []
    assert result["checks"] == {
        "integrity": "verified",
        "size": "verified",
        "structure": "verified",
        "carrier": "verified",
        "fonts_embedded": "not_applicable",
        "text_layer": "not_applicable",
    }
    assert result["artifact"] == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    assert result["plan"] == PLAN
    assert any("轮廓" in note and "可检索" in note for note in result["notes"])


@pytest.mark.parametrize("width", ["0pt", "-1pt", "inf", "1e999pt", "..pt", "20%", ""])
def test_invalid_physical_dimensions_fail_standard_inspection(tmp_path, width):
    path = tmp_path / "bad.svg"
    path.write_bytes(
        svg('<path d="M0 0L1 1"/>', root=ROOT.replace('width="72pt"', f'width="{width}"'))
    )
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["checks"]["size"] == "failed"
    assert result["policy"]["verdict"] == "rejected"


@pytest.mark.parametrize(
    "viewbox",
    ["0 0 72 72", "0 0 0 36", "0 0 -72 36", "0 0 72 0", "NaN 0 72 36", "0 0 72 inf", "0 0 72", ""],
)
def test_bad_viewbox_is_a_required_structure_failure(tmp_path, viewbox):
    path = tmp_path / "bad.svg"
    path.write_bytes(svg(root=ROOT.replace('viewBox="0 0 72 36"', f'viewBox="{viewbox}"')))
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["checks"]["structure"] == "failed"
    assert result["policy"]["verdict"] == "rejected"


def test_size_is_observed_from_bytes_and_missing_plan_is_unknown(tmp_path):
    path = tmp_path / "figure.svg"
    path.write_bytes(svg('<path d="M0 0L1 1"/>'))
    result = I.inspect(path, "svg", plan={**PLAN, "page_pt": [100, 50]})
    assert result["checks"]["size"] == "failed"
    assert result["policy"]["verdict"] == "rejected"
    result = I.inspect(path, "svg", plan={}, policy="strict")
    assert result["checks"]["size"] == "unknown"
    assert result["policy"]["verdict"] == "rejected"


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        '<g onload="alert(1)"/>',
        '<svg onerror="alert(1)"/>',
        '<foreignObject><p xmlns="http://www.w3.org/1999/xhtml">active</p></foreignObject>',
        '<animate attributeName="href" to="https://evil.test/x"/>',
        '<set attributeName="onload" to="alert(1)"/>',
        '<image href="https://evil.test/a.png"/>',
        '<image href="//evil.test/a.png"/>',
        '<image href="file:///etc/passwd"/>',
        '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
        '<image href="data:text/html;base64,PGI+PC9iPg=="/>',
        '<use href="javascript:alert(1)"/>',
        '<use xlink:href="https://evil.test/x.svg#shape"/>',
        '<g xml:base="https://evil.test/"><path id="shape"/><use href="#shape"/></g>',
        '<style>@import "https://evil.test/style.css";</style>',
        "<style>@font-face {font-family: evil; src: url(https://evil.test/font.woff)}</style>",
        "<style>* {fill:url(https://evil.test/x.svg#shape)}</style>",
        "<style>* {fill:u/**/rl(https://evil.test/x)}</style>",
        r"<style>* {fill:u\72l(https://evil.test/x)}</style>",
        "<style>* {fill:var(--evil)}</style>",
        "<style>* {--evil:red; fill:var(--evil)}</style>",
        "<style>*:hover {fill:red}</style>",
        "<style>g {path {fill:red}}</style>",
        "<style>* {fill:red} invalid</style>",
        "<style>[id] {fill:red}</style>",
        '<path style="fill:expression(alert(1))"/>',
        '<path style="fill:url(data:image/svg+xml,evil)"/>',
        '<path filter="url(https://evil.test/x)"/>',
        '<path cursor="url(https://evil.test/x)"/>',
        '<g xmlns:h="http://www.w3.org/1999/xhtml"><h:script>alert(1)</h:script></g>',
        '<path id="same"/><path id="same"/>',
        '<use href="#missing"/>',
        "<use/>",
        "<image/>",
        '<path clip-path="url(#missing)"/>',
        "<style>* {fill:url(#missing)}</style>",
        '<g id="loop"><use href="#loop"/></g>',
        '<defs><g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g></defs>',
        '<defs><linearGradient id="a" href="#b"/><linearGradient id="b" href="#a"/></defs>',
    ],
)
def test_active_external_or_broken_references_are_rejected_in_standard(tmp_path, body):
    path = tmp_path / "unsafe.svg"
    path.write_bytes(svg(body))
    with pytest.raises(SVGError):
        parse_svg(path.read_bytes())
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["checks"]["integrity"] == "failed"
    assert result["policy"]["verdict"] == "rejected"
    assert result["observed"]["problems"]


@pytest.mark.parametrize(
    "prefix",
    [
        '<!DOCTYPE svg SYSTEM "https://evil.test/svg.dtd">',
        '<!DOCTYPE svg [<!ENTITY x "expanded">]>',
        '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
        '<?xml-stylesheet href="https://evil.test/style.css"?>',
        "<?custom processing?>",
    ],
)
def test_dtd_entities_and_processing_instructions_are_rejected(prefix):
    with pytest.raises(SVGError):
        parse_svg(prefix.encode() + svg())


def test_utf16_and_non_svg_roots_cannot_bypass_validation():
    with pytest.raises(SVGError):
        parse_svg((ROOT + "<script>alert(1)</script></svg>").encode("utf-16"))
    with pytest.raises(SVGError, match="svg_invalid_root"):
        parse_svg(b'<html xmlns="http://www.w3.org/1999/xhtml"/>')
    with pytest.raises(SVGError, match="svg_encoding_unsupported"):
        parse_svg(b'<?xml version="1.0" encoding="ISO-8859-1"?>' + svg())


def test_local_refs_static_styles_metadata_and_inline_raster_are_allowed(tmp_path):
    body = """<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#">
        <cc:Work><dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/>
        <dc:title>A &amp; B</dc:title></cc:Work></rdf:RDF></metadata>
        <defs><clipPath id="clip"><rect width="5" height="5"/></clipPath>
        <path id="glyph" d=""/><linearGradient id="gradient"><stop stop-color="red"/></linearGradient></defs>
        <style type="text/css">*{stroke-linejoin: round; stroke-linecap: butt}
        #shape, .plot > path {fill: url('#gradient'); opacity: 0.5}</style>
        <g clip-path="url(#clip)"><path id="shape" d="M0 0L1 1" style="fill:rgb(30%, 20%, 10%)"/>
        <use xlink:href="#glyph"/></g>"""
    path = tmp_path / "safe.svg"
    path.write_bytes(svg(body + data_image()))
    result = I.inspect(path, "svg", plan=PLAN, policy="strict")
    assert result["policy"]["verdict"] == "accepted"
    assert result["observed"]["carrier"] == "mixed"
    assert result["observed"]["census"]["paths"] == 2  # unused defs/clip paths are not artwork


def test_inline_jpeg_is_allowed():
    image = pytest.importorskip("PIL.Image")
    out = io.BytesIO()
    image.new("RGB", (1, 1), "red").save(out, format="JPEG")
    encoded = base64.b64encode(out.getvalue()).decode()
    parse_svg(svg(f'<image href="data:image/jpeg;base64,{encoded}"/>'))


def test_text_does_not_claim_embedded_fonts_and_compares_extracted_text(tmp_path):
    path = tmp_path / "text.svg"
    path.write_bytes(svg('<text font-family="System font">Expected <tspan>label</tspan></text>'))
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["checks"]["text_layer"] == "verified"
    assert result["checks"]["fonts_embedded"] == "unknown"
    path.write_bytes(svg("<text>Different text</text>"))
    assert I.inspect(path, "svg", plan=PLAN)["checks"]["text_layer"] == "failed"


@pytest.mark.parametrize(
    ("data", "budget"),
    [
        (svg(), SVGBudget(max_bytes=10)),
        (svg("<g><g><g/></g></g>"), SVGBudget(max_depth=3)),
        (svg("<path/><path/>"), SVGBudget(max_elements=2)),
        (svg('<path d="M0 0L1 1"/>'), SVGBudget(max_attribute_chars=5)),
        (svg("<text>long text</text>"), SVGBudget(max_text_chars=3)),
        (svg(), SVGBudget(max_attributes=1)),
        (svg(data_image()), SVGBudget(max_image_bytes=1)),
        (svg(data_image(png(100_000, 100_000))), SVGBudget()),
        (
            svg('<defs><g id="a"><path/></g></defs><use href="#a"/><use href="#a"/>'),
            SVGBudget(max_expanded_elements=7),
        ),
    ],
)
def test_xml_images_and_reference_expansion_have_hard_resource_limits(data, budget):
    with pytest.raises(SVGError, match="svg_resource_limit"):
        parse_svg(data, budget=budget)


def test_reference_expansion_bomb_is_rejected_without_expanding_it():
    groups = '<g id="n0"><path/></g>'
    for i in range(1, 40):
        groups += f'<g id="n{i}"><use href="#n{i - 1}"/><use href="#n{i - 1}"/></g>'
    with pytest.raises(SVGError, match="svg_resource_limit"):
        parse_svg(svg(f'<defs>{groups}</defs><use href="#n39"/>'))


def test_unknown_svg_inspector_exception_cannot_bypass_security_gate(tmp_path):
    path = tmp_path / "unknown.svg"
    path.write_bytes(svg())
    result = I.uninspected(path, "svg", plan=PLAN, reason="Unexpected reader failure")
    assert result["policy"]["verdict"] == "rejected"
    assert set(result["checks"]) == set(I.CHECKS_SVG)
    assert all(value == "unknown" for value in result["checks"].values())


def test_real_matplotlib_source_normalizes_only_known_inert_doctype(tmp_path):
    mpl = pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(1, 0.5))
    ax = fig.subplots()
    ax.plot([0, 1], [1, 0])
    ax.text(0.5, 0.5, "Matplotlib & XML")
    out = io.BytesIO()
    with mpl.rc_context({"svg.fonttype": "path"}):
        fig.savefig(out, format="svg")
    original = out.getvalue()
    assert b"<!DOCTYPE" in original
    with pytest.raises(SVGError, match="svg_dtd_forbidden"):
        parse_svg(original)
    clean = strip_matplotlib_doctype(original)
    parse_svg(clean)
    path = tmp_path / "matplotlib.svg"
    path.write_bytes(clean)
    result = I.inspect(path, "svg", plan=PLAN, policy="strict")
    assert result["policy"]["verdict"] == "accepted"
    assert result["observed"]["text_mode"] == "outlines_or_absent"
    changed = original.replace(
        b"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd", b"https://evil.test/evil.dtd"
    )
    assert strip_matplotlib_doctype(changed) == changed
    with pytest.raises(SVGError):
        parse_svg(strip_matplotlib_doctype(changed))


def test_dimensions_reuse_existing_artifactcheck_authority(tmp_path, monkeypatch):
    path = tmp_path / "same.svg"
    path.write_bytes(svg())
    calls = []
    real = artifactcheck.check_svg

    def record(*args, **kwargs):
        assert kwargs["root"] is not None
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(artifactcheck, "check_svg", record)
    assert I.observe_svg(path)["size_mm"] == [25.4, 12.7]
    assert calls == [True]


def test_a_raster_wrapper_cannot_pass_as_vector_svg_even_under_standard(tmp_path):
    path = tmp_path / "raster-wrapper.svg"
    path.write_bytes(svg(data_image()))
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["observed"]["carrier"] == "raster"
    assert result["checks"]["carrier"] == "failed"
    assert result["policy"]["verdict"] == "rejected"
    assert "carrier" in result["policy"]["failed"]
    # A producer that explicitly promised a raster carrier remains valid.
    assert I.inspect(path, "svg", plan={**PLAN, "vector": False})["policy"]["verdict"] == "accepted"


def test_svg_scope_reports_actual_xml_budget_and_its_visual_limits(tmp_path):
    path = tmp_path / "figure.svg"
    path.write_bytes(svg("<path/>"))
    result = I.inspect(path, "svg", plan=PLAN, svg_budget=SVGBudget(max_depth=12))
    assert result["scope"]["budget"]["max_depth"] == 12
    assert "max_image_pixels" in result["scope"]["budget"]
    assert any("视觉" in limit for limit in result["scope"]["limits"])


def test_transparent_empty_svg_does_not_require_vector_artwork(tmp_path):
    path = tmp_path / "empty.svg"
    path.write_bytes(svg())
    result = I.inspect(path, "svg", plan={**PLAN, "vector": False}, policy="strict")
    assert result["observed"]["carrier"] == "empty"
    assert result["policy"]["verdict"] == "accepted"


def test_inspect_svg_never_reads_the_artifact_with_unbounded_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / "bounded.svg"
    data = svg('<path d="M0 0L1 1"/>')
    path.write_bytes(data)
    original = type(path).read_bytes

    def guarded_read_bytes(self):
        assert self != path, "SVG inspection must use a bounded read or streaming hash"
        return original(self)

    monkeypatch.setattr(type(path), "read_bytes", guarded_read_bytes)
    result = I.inspect(path, "svg", plan=PLAN)
    assert result["policy"]["verdict"] == "accepted"
    assert result["artifact"]["sha256"] == hashlib.sha256(data).hexdigest()
    result = I.inspect(path, "svg", plan=PLAN, svg_budget=SVGBudget(max_bytes=16))
    assert result["policy"]["verdict"] == "rejected"
    assert result["observed"]["problems"] == ["svg_resource_limit"]


def test_worker_normalization_verifies_companion_hash_before_rewriting(tmp_path):
    from tavotto.engine.artifactinspect import normalize_worker_svg

    doctype = (
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"\n'
        b'  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
    )
    original = doctype + svg("<path/>")
    path = tmp_path / "worker.svg"
    path.write_bytes(original)
    expected = {"sha256": hashlib.sha256(original).hexdigest(), "size_bytes": len(original)}
    normalize_worker_svg(path, expected)
    assert path.read_bytes() == svg("<path/>")
    # A changed companion must never be silently blessed by rewriting/hash refresh.
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="SVG companion changed"):
        normalize_worker_svg(path, expected)
    assert path.read_bytes() == original + b" "


def test_worker_normalization_does_not_modify_unsafe_input(tmp_path):
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    original = svg("<script>alert(1)</script>")
    path.write_bytes(original)
    with pytest.raises(SVGError):
        normalize_worker_svg(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("body", "vector"),
    [
        ("<path/>", True),
        ("<text>Text</text>", True),
        ("", True),
        (data_image(), False),
        (data_image() + "<path/>", True),
        ('<defs><path id="unused"/></defs>' + data_image(), False),
        ('<defs><path id="glyph"/></defs><use href="#glyph"/>', True),
        (
            "<defs>"
            + data_image().replace("<image ", '<image id="pixel" ')
            + '</defs><use href="#pixel"/>',
            False,
        ),
    ],
)
def test_producer_svg_vector_fact_reports_actual_painted_structure(tmp_path, body, vector):
    from tavotto.engine.artifactinspect import svg_vector

    path = tmp_path / "figure.svg"
    path.write_bytes(svg(body))
    assert svg_vector(path) is vector
    result = I.inspect(path, "svg", plan={**PLAN, "vector": vector}, policy="strict")
    assert result["policy"]["verdict"] == "accepted"


def test_long_malformed_stylesheet_and_padded_values_are_bounded():
    # Disjoint regex tokens matter here: overlapping whitespace quantifiers would
    # backtrack quadratically before the XML byte limit could protect the caller.
    with pytest.raises(SVGError, match="svg_unsafe_css"):
        parse_svg(svg("<style>" + " " * 100_000 + "invalid</style>"))
    parse_svg(svg('<path style="fill:' + " " * 100_000 + 'red"/>'))


@pytest.mark.parametrize(
    ("node", "family"),
    [
        (
            "<text font-family=\"'Liberation Serif', 'Times New Roman', serif\">x</text>",
            "Liberation Serif",
        ),
        ('<text font-family="Arial, sans-serif">x</text>', "Arial"),
        ("<text style=\"font-family: 'DejaVu Sans', sans-serif\">x</text>", "DejaVu Sans"),
        ("<text style=\"font: 10px 'DejaVu Sans'; text-anchor: middle\">x</text>", "DejaVu Sans"),
    ],
)
def test_check_svg_reads_the_first_family_of_live_text(tmp_path, node, family):
    """ADR 0144：留成 <text> 的 SVG 能核字体族（取列表第一个，后面是回退）。"""
    path = tmp_path / "t.svg"
    path.write_bytes(svg(node))
    result = artifactcheck.check_svg(path, expect_mm=None, font_family=family)
    assert result["fonts"] == [family] and result["has_text"] is True
    assert result["font_ok"] is True and result["font_note"] is None
    assert artifactcheck.check_svg(path, expect_mm=None, font_family="Other")["font_ok"] is False


def test_check_svg_still_reports_outlines_as_unverifiable(tmp_path):
    path = tmp_path / "o.svg"
    path.write_bytes(svg('<path d="M0 0L1 1"/>'))
    result = artifactcheck.check_svg(path, expect_mm=None, font_family="Arial")
    assert result["has_text"] is False and result["font_ok"] is None and result["font_note"]


def test_check_svg_flags_matplotlib_outlined_text_but_not_plain_graphics(tmp_path):
    """QA-WIN-08：`text_outlined` 只在文字确实被画成轮廓时为真；纯图形的 SVG 不能说「文字已转成路径」。"""
    outlined = tmp_path / "o.svg"
    outlined.write_bytes(
        svg('<g id="text_1"><g transform="translate(1 2)"><path d="M0 0L1 1"/></g></g>')
    )
    assert (
        artifactcheck.check_svg(outlined, expect_mm=None, font_family=None)["text_outlined"] is True
    )
    plain = tmp_path / "p.svg"
    plain.write_bytes(svg('<path d="M0 0L1 1"/>'))
    assert (
        artifactcheck.check_svg(plain, expect_mm=None, font_family=None)["text_outlined"] is False
    )
    live = tmp_path / "t.svg"
    live.write_bytes(svg('<g id="text_1"><text>x</text></g>'))
    assert artifactcheck.check_svg(live, expect_mm=None, font_family=None)["text_outlined"] is False


def _ids_and_refs(root):
    ids = [n.get("id") for n in root.iter() if n.get("id")]
    hrefs = [v for n in root.iter() for k, v in n.attrib.items() if k.endswith("href")]
    return ids, hrefs


def test_worker_normalization_repairs_ids_the_safe_grammar_rejects(tmp_path):
    """A user `set_gid("面板 A")` is legal Matplotlib output; only the trusted worker boundary renames it.

    The rename is consistent: `href="#…"`, `url(#…)` and later duplicates follow the first definition,
    the same element every SVG renderer would pick.
    """
    from tavotto.engine.artifactinspect import normalize_worker_svg

    body = (
        '<defs><clipPath id="面板 A"><rect width="1" height="1"/></clipPath>'
        '<path id="p1" d="M0 0L1 1"/></defs>'
        '<g clip-path="url(#面板 A)"><use xlink:href="#面板 A"/></g>'
        '<g id="p1"><use xlink:href="#p1"/></g>'
    )
    original = svg(body)
    with pytest.raises(SVGError, match="svg_invalid_id"):
        parse_svg(original)
    path = tmp_path / "worker.svg"
    path.write_bytes(original)
    normalize_worker_svg(path)
    root = parse_svg(path.read_bytes())
    ids, hrefs = _ids_and_refs(root)
    assert len(ids) == len(set(ids)) == 3
    clip = next(n for n in root.iter() if n.tag.endswith("clipPath")).get("id")
    first_path = next(n for n in root.iter() if n.tag.endswith("path")).get("id")
    assert first_path == "p1"
    assert hrefs == [f"#{clip}", "#p1"]
    group = [n for n in root.iter() if n.tag.endswith("}g")][0]
    assert group.get("clip-path") == f"url(#{clip})"


def test_worker_id_repair_still_runs_the_full_gate(tmp_path):
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    original = svg('<g id="面板"/><script>alert(1)</script>')
    path.write_bytes(original)
    with pytest.raises(SVGError):
        normalize_worker_svg(path)
    assert path.read_bytes() == original


def test_import_boundary_keeps_rejecting_bad_ids():
    """Arbitrary files never borrow the worker repair: parse_svg itself stays strict."""
    with pytest.raises(SVGError, match="svg_invalid_id"):
        parse_svg(svg('<g id="面板"/>'))
    with pytest.raises(SVGError, match="svg_invalid_id"):
        parse_svg(svg('<g id="a"/><g id="a"/>'))


def test_worker_normalization_accepts_matplotlib_user_gid(tmp_path):
    mpl = pytest.importorskip("matplotlib")
    mpl.use("agg")
    import matplotlib.pyplot as plt

    from tavotto.engine.artifactinspect import normalize_worker_svg

    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1], [0, 1]).pop().set_gid("曲线 1")
    path = tmp_path / "worker.svg"
    try:
        fig.savefig(path, format="svg")
    finally:
        plt.close(fig)
    normalize_worker_svg(path)
    parse_svg(path.read_bytes())


def test_worker_id_repair_leaves_hex_colors_alone(tmp_path):
    """Codex #1893 P1: a numeric gid `"000000"` must not rewrite `stroke:#000000` into a dangling ref."""
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    path.write_bytes(
        svg(
            '<defs><clipPath id="000000"><rect width="1" height="1"/></clipPath></defs>'
            '<path d="M0 0L1 1" style="fill:none;stroke:#000000" clip-path="url(#000000)"/>'
            '<path d="M0 1L1 0" stroke="#000000"/>'
        )
    )
    normalize_worker_svg(path)
    root = parse_svg(path.read_bytes())
    clip = next(n for n in root.iter() if n.tag.endswith("clipPath")).get("id")
    first, second = [n for n in root.iter() if n.tag.endswith("path")]
    assert first.get("style") == "fill:none;stroke:#000000"
    assert first.get("clip-path") == f"url(#{clip})"
    assert second.get("stroke") == "#000000"


def test_worker_id_repair_refuses_when_a_selector_names_a_duplicate(tmp_path):
    """Codex #1893 P1: `#p {…}` styles every copy; a rename cannot keep that, so the input stays rejected."""
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    original = svg('<style>#p {fill:red}</style><path id="p" d="M0 0"/><path id="p" d="M1 1"/>')
    path.write_bytes(original)
    with pytest.raises(SVGError, match="svg_invalid_id"):
        normalize_worker_svg(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "selector", ["#面板&gt;path", "#面板.hl", "#面板 path", "#面板:first-child"]
)
def test_worker_id_repair_rewrites_selector_id_tokens(tmp_path, selector):
    """Codex #1893 P2: the renamed id follows into compound / combinator selectors."""
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    path.write_bytes(
        svg(f'<style>{selector} {{fill:red}}</style><g id="面板"><path d="M0 0"/></g>')
    )
    try:
        normalize_worker_svg(path)
    except SVGError as exc:  # `:` is outside svgcheck's selector whitelist and stays refused
        assert selector.endswith(":first-child") and exc.code == "svg_unsafe_css"
        return
    assert not selector.endswith(":first-child")
    root = parse_svg(path.read_bytes())
    gid = next(n for n in root.iter() if n.tag.endswith("}g")).get("id")
    css = next(n for n in root.iter() if n.tag.endswith("style")).text
    assert "面板" not in css and f"#{gid}" in css


def test_worker_id_repair_handles_references_before_the_definition(tmp_path):
    """The gate reports the first problem in document order: a `<use>` before its def says external_resource."""
    from tavotto.engine.artifactinspect import normalize_worker_svg

    path = tmp_path / "worker.svg"
    path.write_bytes(svg('<use xlink:href="#面板 A"/><defs><path id="面板 A" d="M0 0"/></defs>'))
    normalize_worker_svg(path)
    root = parse_svg(path.read_bytes())
    _ids, hrefs = _ids_and_refs(root)
    target = next(n for n in root.iter() if n.tag.endswith("path")).get("id")
    assert hrefs == [f"#{target}"]
