import subprocess
from xml.etree import ElementTree

import pymupdf
import pytest

from support.foundation_app import sidecar_app
from tavotto.engine import layoutannotate
from test_core_v02_release_candidate import (
    MPL_SCRIPT,
    R_FRAME_PT,
    _archived_previews,
    _canvas_export_request,
    _export_files,
    _panel,
    _render,
    _run_ggplot,
    _title,
)
from test_native_ggplot_capture import _rscript_with_capture_packages, _write_native_figure_style

R_PLOT = """
rows <- data.frame(x = rep(c(1, 4, 16), 2), y = c(2, 5, 3, 3, 3.5, 2),
  group = rep(c("A", "B"), each = 3), label = c("First", "second\nline", "third", "fourth", "fifth", "last"),
  angle = c(0, 45, 0, 20, 20, 0), size = c(3, 4, 5, 5, 4, 3))
plot <- ggplot2::ggplot(rows, ggplot2::aes(x, y)) +
  ggplot2::geom_text(ggplot2::aes(label = label, angle = angle, size = size),
    colour = "#55A0A8", show.legend = FALSE) +
  ggplot2::scale_size_identity() + ggplot2::facet_wrap(ggplot2::vars(group))
"""
BOXED_R_PLOT = R_PLOT.replace("geom_text(", "geom_label(").replace(
    'colour = "#55A0A8", show.legend',
    'text.colour = "#55A0A8", border.colour = "#194567", fill = "#F8E3D3", show.legend',
)
GID = "layer.1.panel.2.text.2"
ROUTE = "/api/layouts/Labels"
EDITS = {"text": "Reviewed", "fontsize": 24, "color": "#C83155", "weight": "bold"}


def _element(result, gid):
    return next(item for item in result["manifest"]["elements"] if item["gid"] == gid)


def _device_text(svg, pigment):
    return [
        ElementTree.tostring(node)
        for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")
        if pigment.lower() in node.attrib.get("style", "").lower()
    ]


@pytest.mark.parametrize("program", [R_PLOT, BOXED_R_PLOT], ids=["text", "boxed"])
def test_data_label_style_and_text_cold_mixed_export(tmp_path, program):
    project = tmp_path / "Native text"
    project.mkdir()
    script = project / "labels.R"
    script.write_text(program, encoding="utf-8")
    (project / "mpl_response.py").write_text(MPL_SCRIPT, encoding="utf-8")
    with sidecar_app(project, tmp_path / "hot") as app:
        figure = _run_ggplot(app, script.name, _rscript_with_capture_packages())
        registry = app.call("/api/registry/probe", {"script": "mpl_response.py"}, timeout=120)[1]
        (mpl,) = [item for item in registry["descriptors"] if item["stem"] == "Signal"]
        mpl_asset = mpl["asset_id"]
        mpl_original = _render(app, mpl_asset, [])
        mpl_title = _title(mpl_original, "text")
        mpl_edits = [{"gid": mpl_title["gid"], "prop": "text", "value": "Matplotlib reference"}]
        mpl_w, mpl_h = mpl_original["manifest"]["size_mm"]
        mpl_panel = {
            **_panel("mpl", mpl_asset, 5, mpl_edits, native_width=mpl_w),
            "h": mpl_h,
            "nativeH": mpl_h,
        }
        baseline = _render(app, figure, [])
        picked = _element(baseline, GID)
        assert picked["role"] == "text" and picked["label"] == "fifth"
        props = {field["prop"]: field["value"] for field in picked["editable"]}
        assert props["text"] == "fifth" and props["fontsize"] > 0
        edits = [
            {"gid": GID, "identity": picked["identity"], "prop": prop, "value": value}
            for prop, value in EDITS.items()
        ]
        changed = _render(app, figure, edits)
        assert changed["warnings"] == []
        live = _element(changed, GID)
        assert live["bbox"][2] > picked["bbox"][2]
        assert live["identity"] == picked["identity"] and live["label"] == "Reviewed"
        assert live["identity"].startswith("r12:")
        before_labels = _device_text(baseline["svg"], "#55A0A8")
        after_labels = _device_text(changed["svg"], "#55A0A8")
        assert len(before_labels) == 7 and after_labels == [
            row for row in before_labels if b">fifth<" not in row
        ]
        (reviewed,) = _device_text(changed["svg"], "#C83155")
        assert b"Reviewed" in reviewed and b"font-weight: bold" in reviewed
        if program == BOXED_R_PLOT:

            def boxes(svg):
                return [
                    ElementTree.tostring(node)
                    for node in ElementTree.fromstring(svg).iter()
                    if "#194567" in node.attrib.get("style", "").upper()
                    and "#F8E3D3" in node.attrib.get("style", "").upper()
                ]

            previous = boxes(baseline["svg"])
            latest = boxes(changed["svg"])
            assert len(previous) == len(latest) == 6
            assert [
                index
                for index, pair in enumerate(zip(previous, latest, strict=True))
                if pair[0] != pair[1]
            ] == [4]
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        # Redraw on different native positions while retaining the original label identity.
        axis = _element(baseline, "axes_2")
        edits += [
            {"gid": axis["gid"], "identity": axis["identity"], "prop": "xscale", "value": "log"}
        ]
        projected = _render(app, figure, edits)
        assert not projected["warnings"]
        after = _element(projected, GID)
        assert after["identity"] == picked["identity"] and after["label"] == "Reviewed"
        assert _device_text(projected["svg"], "#C83155") != [reviewed]
        w, h = projected["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 10 + mpl_w, edits, native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "label-edit", "name": "Labels"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": mpl_w + w + 15, "h": max(h, mpl_h) + 10},
                    "objects": [panel, mpl_panel],
                    "guides": [],
                }
            ],
        }
        assert app.call(ROUTE + "?target=project", doc)[1]["ok"]
        saved = app.call(ROUTE)[1]
        before_close = _archived_previews(app, saved)
        initial_pdf = _export_files(app, _canvas_export_request(saved, "Warm labels"))["pdf"]
    with sidecar_app(project, tmp_path / "cold") as app:
        cold = app.call(ROUTE)[1]
        assert _archived_previews(app, cold) == before_close
        assert cold["canvases"][0]["objects"][0]["overrides"] == edits
        output = _export_files(app, _canvas_export_request(cold, "Cold labels"))
        with pymupdf.open(output["pdf"]) as pdf, pymupdf.open(initial_pdf) as warm:
            assert pdf[0].get_pixmap().samples == warm[0].get_pixmap().samples
            assert "Reviewed" in pdf[0].get_text() and "fifth" not in pdf[0].get_text()
            assert "Matplotlib reference" in pdf[0].get_text()


@pytest.mark.parametrize("program", [R_PLOT, BOXED_R_PLOT], ids=["text", "boxed"])
def test_data_label_source_tracks_the_original_row_if_two_labels_have_the_same_text(
    tmp_path, program
):
    project = tmp_path / "Labels source"
    project.mkdir()
    script = project / "labels.R"
    original = program.replace('"fourth", "fifth"', '"fifth", "fifth"').encode()
    script.write_bytes(original)
    rscript = _rscript_with_capture_packages()

    with sidecar_app(project, tmp_path / "hot") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        label = _element(baseline, GID)
        peer = _element(baseline, "layer.1.panel.2.text.1")
        assert label["label"] == peer["label"] == "fifth"
        axis = _element(baseline, "axes_2")
        edits = [
            {"gid": GID, "identity": label["identity"], "prop": prop, "value": value}
            for prop, value in EDITS.items()
        ] + [{"gid": axis["gid"], "identity": axis["identity"], "prop": "xscale", "value": "log"}]
        hot = _render(app, figure, edits)
        assert _element(hot, peer["gid"])["label"] == "fifth"
        w, h = hot["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 5, edits, native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "label-write", "name": "Labels"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": w + 10, "h": h + 10},
                    "objects": [panel],
                    "guides": [],
                }
            ],
        }
        assert app.call(ROUTE + "?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Labels")
        _, plan = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert plan["written"] == len(edits) and not plan["skipped"], plan
        assert script.read_bytes() == original
        receipt = app.call(
            "/api/script-edit/figure-style/commit",
            {
                "confirmed": True,
                "token": plan["token"],
            },
            timeout=120,
        )[1]
        assert receipt["ok"]
        assert app.call(ROUTE)[1]["canvases"][0]["objects"][0]["overrides"] == []
        written = script.read_bytes()
        assert b"ggplot_with_data_labels" in written

    fresh = tmp_path / "Clean R source"
    fresh.mkdir()
    (fresh / script.name).write_bytes(written)
    with sidecar_app(fresh, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        cold = _render(app, figure, [])
        assert cold["svg"] == hot["svg"]
        assert _element(cold, GID)["label"] == "Reviewed"
        assert _element(cold, GID)["identity"] == label["identity"]
        peer_svg = [
            node.text
            for node in ElementTree.fromstring(cold["svg"]).iter("{http://www.w3.org/2000/svg}text")
        ]
        assert peer_svg.count("fifth") == 1
        reopened_label = _element(cold, GID)
        changed = _render(
            app,
            figure,
            [
                {
                    "gid": GID,
                    "prop": "text",
                    "value": "Again",
                    "identity": reopened_label["identity"],
                }
            ],
        )
        assert _element(changed, GID)["label"] == "Again"


NEW = "#CB345F"
OLD = "#6C541E"
BAR_GID = "layer.1.panel.2.bars.group.2"


def _bar_rows(svg, color):
    return [
        node.attrib
        for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}rect")
        if f"fill: {color};" in node.get("style", "")
    ]


def _bar_groups(view):
    return {
        node["gid"]: node
        for node in view["manifest"]["elements"]
        if node["role"] == "bar_series" and ".group." in node["gid"]
    }


def test_edit_and_write_back_one_mapped_bar_category_with_a_shared_colour(tmp_path):
    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Mapped bar fill"
    project.mkdir()
    script = project / "Bars.R"
    script.write_text(
        f"""\
d <- expand.grid(x = 1:5, arm = c("Baseline", "Target"), trial = c("Early", "Late"))
d$y <- d$x / 2 + 2 * as.integer(d$arm) + 2 * (d$trial == "Late")
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, fill = arm)) +
  ggplot2::geom_col(position = "dodge2", colour = "#3A2311", alpha = .7) +
  ggplot2::scale_fill_manual(values = c(Baseline = "{OLD}", Target = "{OLD}")) +
  ggplot2::facet_wrap(ggplot2::vars(trial))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Bars"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    fingerprint = (project / "built-data.txt").read_bytes()

    def check_and_write(app, asset, before, color):
        all_groups = _bar_groups(before)
        current = all_groups[BAR_GID]
        assert len(all_groups) == 4 and current["label"].endswith("Target")
        patches = [
            {"gid": BAR_GID, "identity": current["identity"], "prop": "facecolor", "value": color}
        ]
        hot = _render(app, asset, patches)
        assert not hot["warnings"]
        assert _bar_groups(hot)[BAR_GID]["geometry"] == current["geometry"] or color == "none"
        assert _bar_groups(hot)[BAR_GID]["bbox"] == current["bbox"]
        for peer in all_groups.keys() - {BAR_GID}:
            assert _bar_groups(hot)[peer] == all_groups[peer]
        assert _render(app, asset, [])["svg"] == before["svg"]
        _write_native_figure_style(app, script, route, asset, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert (project / "built-data.txt").read_bytes() == fingerprint
        written = (project / "standalone.svg").read_text(encoding="utf-8")
        for pigment in [NEW, OLD]:
            assert _bar_rows(written, pigment) == _bar_rows(hot["svg"], pigment)
        return hot

    with sidecar_app(project, tmp_path / "warm") as app:
        asset = _run_ggplot(app, script.name, rscript)
        warm = _render(app, asset, [])
        changed = check_and_write(app, asset, warm, NEW)
        assert len(_bar_rows(warm["svg"], OLD)) == 22
        assert len(_bar_rows(changed["svg"], OLD)) == 17
        assert len(_bar_rows(changed["svg"], NEW)) == 5
        assert all("fill-opacity: 0.70" in r["style"] for r in _bar_rows(changed["svg"], NEW))
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        current = _render(app, asset, [])
        assert current["svg"] == changed["svg"]
        values = {f["prop"]: f["value"] for f in _bar_groups(current)[BAR_GID]["editable"]}
        assert values["facecolor"] == NEW
        assert all("fill-opacity: 0.70" in row["style"] for row in _bar_rows(current["svg"], NEW))
        without_fill = check_and_write(app, asset, current, "none")
        assert not _bar_rows(without_fill["svg"], NEW)
        assert len(_bar_rows(without_fill["svg"], OLD)) == 17


def test_style_writeback_combines_category_bar_fill_and_one_boxed_label(tmp_path):
    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Combined R styles"
    project.mkdir()
    script = project / "summary.R"
    script.write_text(
        """\
d <- expand.grid(x = 1:4, arm = c("Baseline", "Target"), region = c("A", "B"))
d$height <- d$x + (d$arm == "Target") * 2
lab <- data.frame(x = c(2, 3), y = c(6, 8), region = c("A", "B"), text = c("Keep", "Review"))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, height, fill = arm)) +
  ggplot2::geom_col(position = "dodge2", colour = "#28383A") +
  ggplot2::geom_label(data = lab, ggplot2::aes(x, y, label = text),
                      inherit.aes = FALSE, fill = "#FBEEE2", colour = "#21567A") +
  ggplot2::scale_fill_manual(values = c(Baseline = "#6C541E", Target = "#6C541E")) +
  ggplot2::facet_wrap(ggplot2::vars(region))
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Summary"
    with sidecar_app(project, tmp_path / "warm") as app:
        asset = _run_ggplot(app, script.name, rscript)
        before = _render(app, asset, [])
        baseline = {element["gid"]: element for element in before["manifest"]["elements"]}
        label = next(item for item in baseline.values() if item["label"] == "Review")
        fields = [
            (baseline[BAR_GID], "facecolor", NEW),
            (label, "text", "Updated"),
            (label, "fontsize", 23),
        ]
        patches = [
            {"gid": item["gid"], "identity": item["identity"], "prop": field, "value": value}
            for item, field, value in fields
        ]
        hot = _render(app, asset, patches)
        assert not hot["warnings"] and len(_bar_rows(hot["svg"], NEW)) == 4
        assert ">Updated</text>" in hot["svg"] and ">Keep</text>" in hot["svg"]
        assert _render(app, asset, [])["svg"] == before["svg"]
        _write_native_figure_style(app, script, route, asset, hot, patches)
        assert b"ggplot_with_data_labels" in script.read_bytes()
        assert b"ggplot_edit_bar_fill" in script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        assert _render(app, asset, [])["svg"] == hot["svg"]


def _end_tips(svg):
    return [
        node.attrib
        for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}polygon")
        if "stroke: none; fill: #" in node.attrib.get("style", "")
    ]


def _tile_paint(svg):
    return [
        node.attrib["style"]
        for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}rect")
        if "stroke-linecap: butt; stroke-linejoin: miter; fill:" in node.attrib.get("style", "")
    ]


def test_binned_guide_layout_writes_r_source_without_changing_the_bins(tmp_path):
    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Binned guide"
    project.mkdir()
    script = project / "measurement.R"
    script.write_text(
        """\
d <- expand.grid(x = 1:4, y = 1:4)
d$mapped <- d$x + d$y
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, fill = mapped)) + ggplot2::geom_tile() +
  ggplot2::scale_fill_stepsn(colours = c("#002D54", "#E85E36", "#FFDFA1"),
    breaks = c(3, 5, 7), limits = c(2, 8), name = "Binned dose",
    guide = ggplot2::guide_coloursteps(reverse = TRUE))
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Steps"
    with sidecar_app(project, tmp_path / "hot") as app:
        asset = _run_ggplot(app, script.name, rscript)
        original = _render(app, asset, [])
        (guide,) = [item for item in original["manifest"]["elements"] if item["role"] == "colorbar"]
        props = {field["prop"]: field["value"] for field in guide["editable"]}
        assert props["orientation"] == "vertical" and props["extend"] == "neither"
        assert not set(props) & {"cmap", "vmin", "vmax"}
        patches = [
            {"gid": guide["gid"], "identity": guide["identity"], "prop": k, "value": v}
            for k, v in {"orientation": "horizontal", "extend": "both"}.items()
        ]
        hot = _render(app, asset, patches)
        assert not hot["warnings"]
        assert len(_tile_paint(original["svg"])) == 16
        assert _tile_paint(hot["svg"]) == _tile_paint(original["svg"])
        before_tips = _end_tips(original["svg"])
        after_tips = _end_tips(hot["svg"])
        assert len(after_tips) == len(before_tips) + 2
        low, high = after_tips[-2:]
        assert "#403651" in low["style"] and "#FECA8E" in high["style"]
        assert float(low["points"].split(",")[0]) > float(high["points"].split(",")[0])
        assert _render(app, asset, [])["svg"] == original["svg"]
        _write_native_figure_style(app, script, route, asset, hot, patches)
        assert b'kind = "GuideColoursteps"' in script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        recaptured = _render(app, asset, [])
        assert recaptured["svg"] == hot["svg"]
        (current,) = [
            item for item in recaptured["manifest"]["elements"] if item["role"] == "colorbar"
        ]
        fields = {field["prop"]: field["value"] for field in current["editable"]}
        assert fields["orientation"] == "horizontal" and fields["extend"] == "both"
