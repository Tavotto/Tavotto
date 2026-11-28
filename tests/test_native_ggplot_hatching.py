"""Native bar hatching through the common fields, R source and mixed export."""

from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pikepdf
from PIL import Image

from support.foundation_app import sidecar_app
from test_core_v02_release_candidate import (
    _canvas_export_request,
    _export_files,
    _panel,
    _render,
    _run_ggplot,
)
from test_native_ggplot_capture import _rscript_with_capture_packages, _write_native_figure_style

R_PLOT = """
d <- expand.grid(x = 1:4, arm = c("Baseline", "Target"), region = c("A", "B"))
d$height <- d$x + (d$arm == "Target") * 2
plot <- ggplot2::ggplot(d, ggplot2::aes(x, height, fill = arm)) +
  ggplot2::geom_col(position = "dodge2", colour = "#28383A") +
  ggplot2::scale_fill_manual(values = c(Baseline = "#6C541E", Target = "#6C541E")) +
  ggplot2::facet_wrap(ggplot2::vars(region))
"""
GID = "layer.1.panel.2.bars.group.2"
ROUTE = "/api/layouts/Pattern"
FIELDS = {"hatch": "o", "hatchcolor": "#135BA0", "hatch_linewidth": 0.8}


def _target(render):
    return next(el for el in render["manifest"]["elements"] if el["gid"] == GID)


def _paint(svg):
    root = ElementTree.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    return [
        (node.get("x"), node.get("y"), node.get("width"), node.get("height"))
        for node in root.iter(ns + "rect")
        if "fill: url(#" in node.get("style", "")
    ]


def _write(app, figure, source, changes):
    baseline = _render(app, figure, [])
    owner = _target(baseline)
    patches = [
        {"gid": GID, "identity": owner["identity"], "prop": prop, "value": value}
        for prop, value in changes.items()
    ]
    hot = _render(app, figure, patches)
    assert not hot["warnings"]
    latest = _target(hot)
    if "bar_width" not in changes:
        assert latest["bbox"] == owner["bbox"]
    else:
        assert latest["bbox"][2] < owner["bbox"][2]
    assert _render(app, figure, [])["svg"] == baseline["svg"]
    _write_native_figure_style(app, source, ROUTE, figure, hot, patches)
    return hot


def test_r_bar_category_hatch_style_survives_source_and_cold_reopen(tmp_path: Path):
    rscript = _rscript_with_capture_packages()
    project = tmp_path / "R hatch"
    project.mkdir()
    source = project / "bars.R"
    source.write_text(R_PLOT, encoding="utf-8")
    with sidecar_app(project, tmp_path / "initial") as app:
        figure = _run_ggplot(app, source.name, rscript)
        expected = _write(app, figure, source, FIELDS)
        assert len(_paint(expected["svg"])) == 4
        assert "#135BA0" in expected["svg"]
        assert b"ggplot_hatch_tile" in source.read_bytes()

    with sidecar_app(project, tmp_path / "reopened") as app:
        figure = _run_ggplot(app, source.name, rscript)
        snapshot = _render(app, figure, [])
        assert snapshot["svg"] == expected["svg"]
        editable = {field["prop"]: field["value"] for field in _target(snapshot)["editable"]}
        assert all(editable[prop] == value for prop, value in FIELDS.items())
        followed = _write(app, figure, source, {"hatchcolor": "edge", "hatch_linewidth": 0})
        field = next(
            field for field in _target(followed)["editable"] if field["prop"] == "hatchcolor"
        )
        assert field["follows_edge"] is True and field["value"] == "#28383A"
        ns = "{http://www.w3.org/2000/svg}"
        patterns = list(ElementTree.fromstring(followed["svg"]).iter(ns + "pattern"))
        assert patterns and all(
            any("#28383A" in node.get("style", "").upper() for node in pattern.iter())
            for pattern in patterns
        )

    with sidecar_app(project, tmp_path / "followed") as app:
        figure = _run_ggplot(app, source.name, rscript)
        assert _render(app, figure, [])["svg"] == followed["svg"]
        edited = _write(app, figure, source, {"hatch": "", "bar_width": 0.22})
        assert not _paint(edited["svg"])

    with sidecar_app(project, tmp_path / "removed") as app:
        figure = _run_ggplot(app, source.name, rscript)
        assert _render(app, figure, [])["svg"] == edited["svg"]


MPL_PLOT = """
import matplotlib.pyplot as plt

figure, axis = plt.subplots()
axis.bar([1, 2, 3], [3, 1, 2], color="#DC4669", edgecolor="#183264", hatch="/")
axis.set_title("Matplotlib vector hatches")
figure.savefig("Bars.pdf")
"""


def _patterns(pdf):
    src = "/Resources"
    return [
        pattern
        for obj in [*pdf.pages, *pdf.objects]
        if (isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Page)))
        and obj.get(src) is not None
        for pattern in obj[src].get("/Pattern", {}).values()
    ]


def test_hatching_mixed_r_mpl_svg_pdf_png(tmp_path):
    project = tmp_path / "Hatches mixed"
    project.mkdir()
    (project / "bars.R").write_text(R_PLOT, encoding="utf-8")
    (project / "mpl.py").write_text(MPL_PLOT, encoding="utf-8")
    with sidecar_app(project, tmp_path / "hot") as app:
        r = _run_ggplot(app, "bars.R", _rscript_with_capture_packages())
        result = app.call("/api/registry/probe", {"script": "mpl.py"}, timeout=120)[1]
        (mpl,) = [item["asset_id"] for item in result["descriptors"] if item["stem"] == "Bars"]
        baseline = _render(app, r, [])
        owner = next(
            element for element in baseline["manifest"]["elements"] if element["gid"] == GID
        )
        patches = [
            {"gid": GID, "identity": owner["identity"], "prop": k, "value": v}
            for k, v in FIELDS.items()
        ]
        changed = _render(app, r, patches)
        assert not changed["warnings"]
        rw, rh = baseline["manifest"]["size_mm"]
        mw, mh = _render(app, mpl, [])["manifest"]["size_mm"]
        panels = [
            {**_panel("mpl", mpl, 5, [], native_width=mw), "h": mh, "nativeH": mh},
            {**_panel("r", r, mw + 9, patches, native_width=rw), "h": rh, "nativeH": rh},
        ]
        layout = {
            "schema": 3,
            "project": {"id": "texture-mixed", "name": "Pattern"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": mw + rw + 15, "h": max(mh, rh) + 10},
                    "objects": panels,
                    "guides": [],
                }
            ],
        }
        assert app.call(ROUTE + "?target=project", layout)[1]["ok"]
        warm = _export_files(app, _canvas_export_request(app.call(ROUTE)[1], "Warm vector hatches"))

    with sidecar_app(project, tmp_path / "cold") as app:
        layout = app.call(ROUTE)[1]
        assert layout["canvases"][0]["objects"][1]["overrides"] == patches
        cold = _export_files(app, _canvas_export_request(layout, "Cold vector hatches"))
        with pikepdf.open(cold["pdf"]) as pdf:
            assert len(_patterns(pdf)) >= 2
        svg = cold["svg"].read_text(encoding="utf-8")
        ns = "{http://www.w3.org/2000/svg}"
        all_patterns = list(ElementTree.fromstring(svg).iter(ns + "pattern"))
        assert len(all_patterns) >= 2
        assert any(
            "#135BA0" in node.get("style", "").upper()
            for pattern in all_patterns
            for node in pattern.iter()
        )
        cold_png = Image.open(BytesIO(cold["png"].read_bytes())).convert("RGB")
        warm_png = Image.open(BytesIO(warm["png"].read_bytes())).convert("RGB")
        assert cold_png.size == warm_png.size
        assert cold_png.tobytes() == warm_png.tobytes()
