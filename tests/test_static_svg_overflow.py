"""A placed static source cannot paint outside its physical viewport via CSS."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore import pdfwriter, plan, sources, svgir, svgwriter
from test_static_svg_canvas import _read_pdf, provider as provider


@pytest.mark.parametrize("rule", ["inline", "stylesheet"])
def test_static_svg_visible_overflow_keeps_canvas_physical_clip(tmp_path, provider, rule):
    attr = ' style="overflow:visible"' if rule == "inline" else ""
    css = "<style>svg { overflow:visible }</style>" if rule == "stylesheet" else ""
    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="50pt" '
        f'viewBox="0 0 100 50"{attr}>{css}'
        '<rect x="-40" y="-40" width="180" height="130" fill="red"/></svg>'
    ).encode()
    (tmp_path / "source.svg").write_bytes(raw)
    compiled = plan.compile_page(
        60,
        30,
        [{"type": "panel", "id": "source.svg", "x_mm": 20, "y_mm": 10, "w_mm": 20, "h_mm": 10}],
        sources=sources.StaticSourceResolver(tmp_path),
        faces=provider,
        background=(1, 1, 1),
    )
    files = {key: sources.read_frozen(value) for key, value in compiled.sources.items()}
    canonical = tmp_path / "canonical.pdf"
    assembled = tmp_path / "assembled.svg"
    pdfwriter.write_pdf(compiled.page, canonical, provider, files)
    svgwriter.write_svg(compiled.page, assembled, provider, files, imported_svgs=files)
    # Source overflow remains meaningful inside its own DOM. Canvas placement
    # owns the separate physical frame; overriding the source CSS is no fix.
    dom = ET.fromstring(assembled.read_bytes())
    nested = next(
        element for element in dom.iter() if element is not dom and element.tag.endswith("}svg")
    )
    if rule == "inline":
        assert nested.get("style") == "overflow:visible"
    else:
        assert any(
            "overflow:visible" in (element.text or "")
            for element in nested.iter()
            if element.tag.endswith("}style")
        )
    images = {}
    page, _ = svgir.svg_to_ir(
        assembled.read_bytes(), 60 * 72 / 25.4, 30 * 72 / 25.4, provider, files=images
    )
    svg_pdf = tmp_path / "assembled.pdf"
    pdfwriter.write_pdf(page, svg_pdf, provider, images)
    # Independent physical coordinates: x=15 is left of the x=20 panel,
    # x=25 is inside. Both representations must respect the same source frame.
    for output in (canonical, svg_pdf):
        _read_pdf(output, [(15, 15, (255, 255, 255)), (25, 15, (255, 0, 0))], page_mm=(60, 30))
