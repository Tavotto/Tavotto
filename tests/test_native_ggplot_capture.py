"""Real R acceptance for an edit of an already built ggplot2 scene."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import (
    desktopproc,
    envlease,
    execspec,
    figcapture,
    nativeggplot,
    nativesession,
    pool,
)


def _rscript():
    explicit = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    executable = explicit or shutil.which("Rscript")
    if executable is None:
        pytest.skip("real Rscript runtime is unavailable")
    return executable, explicit


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "src" / "tavotto" / "resources" / "native" / "ggplot2.R"
X_BOTTOM = "axis.x.bottom.title"
Y_LEFT = "axis.y.left.title"
LEGEND = "legend.right.1.title"
FACET = "facet.t.2.2.label"


def _pdf_text_colors(path):
    with pymupdf.open(path) as doc:
        return {
            "".join(span["text"] for span in line["spans"]): {
                span["color"] for span in line["spans"]
            }
            for page in doc
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
        }


PROBE = r"""
adapter <- new.env()
sys.source(commandArgs(trailingOnly = TRUE)[[1L]], envir = adapter)
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) {
  quit(status = 77L)
}

summary_calls <- 0L
tracked_mean <- function(values) {
  summary_calls <<- summary_calls + 1L
  mean(values)
}
set.seed(32026)
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point(ggplot2::aes(shape = factor(cyl)),
                      position = ggplot2::position_jitter(width = .12, height = 0)) +
  ggplot2::stat_summary(fun = tracked_mean, geom = "point", shape = 4) +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type", shape = "Cylinders") +
  ggplot2::guides(
    colour = ggplot2::guide_legend(order = 1, theme = ggplot2::theme(
      legend.title = ggplot2::element_text(colour = "#667788"))),
    shape = ggplot2::guide_legend(order = 2, theme = ggplot2::theme(
      legend.title = ggplot2::element_text(colour = "#778899")))
  ) +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis(name = "Secondary horizontal")) +
  ggplot2::scale_y_continuous(sec.axis = ggplot2::dup_axis(name = "Secondary vertical")) +
  ggplot2::theme_bw() +
  ggplot2::theme(
    plot.title = ggplot2::element_text(colour = "#112233"),
    axis.title.x.bottom = ggplot2::element_text(colour = "#223344"),
    axis.title.x.top = ggplot2::element_text(colour = "#334455"),
    axis.title.y.left = ggplot2::element_text(colour = "#445566"),
    axis.title.y.right = ggplot2::element_text(colour = "#556677"),
    strip.text = ggplot2::element_text(colour = "#202040")
  )

baseline <- adapter$capture_ggplot(plot, c(432, 288))
labels <- c("plot.title" = "Fuel economy",
            "axis.x.bottom.title" = "Engine displacement",
            "axis.x.top.title" = "Secondary horizontal",
            "axis.y.left.title" = "Highway mileage",
            "axis.y.right.title" = "Secondary vertical",
            "legend.right.1.title" = "Drive type",
            "legend.right.2.title" = "Cylinders",
            "facet.t.2.2.label" = "Cylinder 8")
facets <- c("facet.t.1.2.label" = "Cylinder 6", "facet.t.2.2.label" = "Cylinder 8",
            "facet.t.1.1.label" = "Cylinder 4", "facet.t.2.1.label" = "Cylinder 5")
original <- c("#112233", "#223344", "#334455", "#445566", "#556677", "#667788", "#778899", "#202040")
colors <- setNames(as.list(c("#6C3799", "#8C3011", "#2370B1", "#2A7654",
                            "#923190", "#3373A7", "#C75052", "#BE3D47")),
                   names(labels))
stopifnot(summary_calls > 0L, length(baseline$state$layers) == 2L,
          length(baseline$state$panel_ranges) == 4L,
          identical(names(baseline$state$text_targets), c(head(names(labels), -1L), names(facets))),
          identical(vapply(baseline$state$text_targets[names(facets)], function(t) t$label,
                           character(1L)), facets))
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
edited <- adapter$render_ggplot(baseline$state, colors)
undone <- adapter$render_ggplot(baseline$state)
restored <- edited
for (i in seq_along(labels)) {
  gid <- names(labels)[[i]]
  label <- paste0(">", labels[[i]], "</text>")
  baseline_line <- grep(label, strsplit(baseline$svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
  edited_line <- grep(label, strsplit(edited, "\n")[[1L]], value = TRUE, fixed = TRUE)
  stopifnot(length(baseline_line) == 1L, grepl(original[[i]], baseline_line, fixed = TRUE),
            identical(edited_line, sub(original[[i]], colors[[gid]], baseline_line, fixed = TRUE)))
  # Use the SVG's text anchor and the measured extents to check each hit region.
  bbox <- baseline$state$text_targets[[gid]]$bbox * c(432, 288, 432, 288)
  if (startsWith(gid, "axis.y.")) {
    anchor <- as.numeric(strsplit(sub(".*translate\\(([^)]+)\\).*", "\\1", edited_line), ",")[[1L]])
    stopifnot(bbox[[4L]] > bbox[[3L]], abs(anchor[[2L]] - bbox[[2L]] - bbox[[4L]] / 2) < 1)
  } else {
    anchor <- as.numeric(sub(".* x='([^']+)'.*", "\\1", edited_line))
    centered <- startsWith(gid, "axis.x.") || startsWith(gid, "facet.")
    expected_x <- bbox[[1L]] + if (centered) bbox[[3L]] / 2 else 0
    stopifnot(bbox[[3L]] > bbox[[4L]], abs(anchor - expected_x) < 1)
    if (startsWith(gid, "legend.")) {
      y <- as.numeric(sub(".* y='([^']+)'.*", "\\1", edited_line))
      stopifnot(y >= bbox[[2L]] - 1, y <= bbox[[2L]] + bbox[[4L]] + 1)
    }
  }
  restored <- sub(colors[[gid]], original[[i]], restored, fixed = TRUE)
}
stopifnot(summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))
stopifnot(lengths(gregexpr("<svg ", baseline$svg, fixed = TRUE)) == 1L,
          identical(restored, baseline$svg),
          identical(undone, baseline$svg))

bad_value <- tryCatch(adapter$render_ggplot(baseline$state, list("plot.title" = "not-a-colour")),
                      error = conditionMessage)
stopifnot(identical(bad_value, "The native R text color must be an RGB hex color."))
limited <- adapter$capture_ggplot(
  plot + ggplot2::labs(title = NULL, x = expression(alpha), colour = "Shared", shape = "Shared") +
    ggplot2::theme(axis.title.y.left = ggplot2::element_blank()),
  c(432, 288))
stopifnot(identical(names(limited$state$text_targets),
                    c("axis.x.top.title", "axis.y.right.title", names(facets))),
          length(grep(">Shared</text>", strsplit(limited$svg, "\n")[[1L]], fixed = TRUE)) == 2L)
bad_target <- tryCatch(adapter$render_ggplot(limited$state, colors),
                       error = conditionMessage)
stopifnot(identical(bad_target, "The requested native R text is unavailable."),
          identical(grDevices::dev.cur(), c("null device" = 1L)))

hidden <- adapter$capture_ggplot(plot + ggplot2::guides(
  colour = ggplot2::guide_legend(title = expression(mu)),
  shape = ggplot2::guide_legend(title = NULL)
), c(432, 288))
stopifnot(identical(names(hidden$state$text_targets), c(head(names(labels), 5L), names(facets))))

bar <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = cty)) +
  ggplot2::geom_point() + ggplot2::labs(colour = "City mileage") +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom"))
bar_state <- adapter$capture_ggplot(bar, c(432, 288))$state
gid <- "legend.bottom.1.title"
stopifnot(identical(bar_state$text_targets[[gid]]$label, "City mileage"))
bar_edited <- adapter$render_ggplot(bar_state, setNames(list("#26838F"), gid))
bar_title <- grep(">City mileage</text>", strsplit(bar_edited, "\n")[[1L]],
                  value = TRUE, fixed = TRUE)
stopifnot(length(bar_title) == 1L, grepl("#26838F", bar_title, fixed = TRUE))

data <- data.frame(x = 1:4, y = 4:1, row = rep(c("Sample", "Shared"), each = 2),
                   column = rep(c("Control", "Shared"), 2))
points <- ggplot2::ggplot(data, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::theme(strip.text.x = ggplot2::element_text(colour = "#375994"),
                 strip.text.y = ggplot2::element_text(colour = "#4F7152"))
capture <- function(plot) adapter$capture_ggplot(plot, c(432, 288))
facet_targets <- function(plot) {
  targets <- capture(plot)$state$text_targets
  targets[startsWith(names(targets), "facet.")]
}
grid <- capture(points + ggplot2::facet_grid(ggplot2::vars(row), ggplot2::vars(column)))
left <- facet_targets(points + ggplot2::facet_wrap(ggplot2::vars(row), strip.position = "left"))
bottom <- facet_targets(points + ggplot2::facet_wrap(ggplot2::vars(column), strip.position = "bottom"))
stopifnot(identical(names(left), c("facet.l.1.2.label", "facet.l.1.1.label")),
          identical(names(bottom), c("facet.b.1.1.label", "facet.b.2.1.label")))
across <- grid$state$text_targets[["facet.t.2.label"]]
down <- grid$state$text_targets[["facet.r.2.label"]]
stopifnot(across$label == "Shared", down$label == "Shared",
          across$color == "#375994", down$color == "#4F7152")
grid_edit <- adapter$render_ggplot(grid$state, list("facet.r.2.label" = "#BC4964"))
row <- grep("#BC4964", strsplit(grid_edit, "\n")[[1L]], value = TRUE, fixed = TRUE)
bbox <- down$bbox * c(432, 288, 432, 288)
anchor <- as.numeric(strsplit(sub(".*translate\\(([^)]+)\\).*", "\\1", row), ",")[[1L]])
stopifnot(length(row) == 1L, grepl(">Shared</text>", row, fixed = TRUE),
          abs(anchor[[2L]] - bbox[[2L]] - bbox[[4L]] / 2) < 1,
          identical(sub("#BC4964", "#4F7152", grid_edit, fixed = TRUE), grid$svg))

repeat_labels <- ggplot2::labeller(column = function(values) rep("Repeated facet", length(values)))
repeated <- capture(points + ggplot2::facet_wrap(ggplot2::vars(column), labeller = repeat_labels))
stacked <- points + ggplot2::facet_wrap(ggplot2::vars(row, column))
formula <- points + ggplot2::facet_wrap(ggplot2::vars(column), labeller = ggplot2::label_parsed)
blank <- points + ggplot2::facet_wrap(ggplot2::vars(column)) +
  ggplot2::theme(strip.text.x = ggplot2::element_blank())
stopifnot(!any(startsWith(names(repeated$state$text_targets), "facet.")),
          length(grep(">Repeated facet</text>", strsplit(repeated$svg, "\n")[[1L]],
                      fixed = TRUE)) == 2L,
          !length(facet_targets(stacked)), !length(facet_targets(formula)),
          !length(facet_targets(blank)))

if (capabilities("cairo")) {
  cairo_pdf <- grDevices::cairo_pdf
  utils::assignInNamespace("cairo_pdf", function(...) warning("fixture missing Cairo"), "grDevices")
  failed_pdf <- suppressWarnings(tryCatch(
    adapter$export_ggplot_pdf(baseline$state, colors, "unavailable.pdf"),
    error = conditionMessage
  ))
  utils::assignInNamespace("cairo_pdf", cairo_pdf, "grDevices")
  stopifnot(grepl("fixture missing Cairo", failed_pdf, fixed = TRUE),
            !file.exists("unavailable.pdf"), !file.exists("Rplots.pdf"),
            identical(grDevices::dev.cur(), c("null device" = 1L)))
}
"""


def test_ggplot_title_colors_keep_the_built_layers_and_replay(tmp_path):
    rscript, explicit_rscript = _rscript()
    driver = tmp_path / "probe.R"
    driver.write_text(PROBE, encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(driver), str(ADAPTER)],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=40,
    )
    if result.returncode == 77 and not explicit_rscript:
        pytest.skip("Rscript does not have ggplot2 and svglite")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "Rplots.pdf").exists()


def test_owned_r_uses_the_native_registry_transport_and_lease(tmp_path):
    rscript, explicit_rscript = _rscript()
    available = subprocess.run(
        [
            rscript,
            "--vanilla",
            "-e",
            'quit(status = if (all(vapply(c("ggplot2", "svglite", "jsonlite"), '
            "requireNamespace, logical(1), quietly = TRUE))) 0L else 77L)",
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )
    if available.returncode == 77 and not explicit_rscript:
        pytest.skip("Rscript does not have the native adapter packages")
    assert available.returncode == 0, available.stdout + available.stderr
    script = tmp_path / "数据 图.R"
    script.write_text(
        """
stopifnot(!nzchar(Sys.getenv("TAVOTTO_BRIDGE_TOKEN")))
cat('{"bridge_event":"ended"}\n')
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy)) +
  ggplot2::geom_point() +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage") +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis()) +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"),
                 axis.title.x = ggplot2::element_text(colour = "grey42"))
""",
        encoding="utf-8",
    )
    spec = execspec.desktop_native_spec(
        str(script), interpreter=rscript, cwd=str(tmp_path), project_root=str(tmp_path)
    )
    registry = nativesession.NativeSessionRegistry()
    target = nativeggplot.GgplotTarget("plot", (432, 288))
    stem = target.stem(spec.target)
    session = desktopproc.start(
        desktopproc.OwnedLaunch(spec, ggplot_target=target),
        registry=registry,
    )
    try:
        state = session.wait_for_state(
            {nativesession.BARRIER, *nativesession.TERMINAL_STATES}, timeout=20
        )
        assert state == nativesession.BARRIER, session.public_state()
        assert registry.list(str(tmp_path)) == [session]
        assert session.session_id in envlease.native_sessions_on(spec.interpreter)
        baseline = session.ensure_built()
        assert baseline["source"] == "ggplot2"
        assert list(baseline["stems"]) == [stem]
        (descriptor,) = [
            figcapture.descriptor_from_payload(desc) for desc in baseline["descriptors"]
        ]
        assert descriptor.capture_source == "ggplot2"
        assert descriptor.original_artifact is None
        assert not descriptor.can_writeback_artifact
        assert not registry.bind_assets(session)
        assert registry.route_for(str(tmp_path), descriptor.script, stem) is session
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        manifest = baseline["manifest"]
        assert manifest["size_mm"] == pytest.approx([152.4, 101.6])
        elements = {element["gid"]: element for element in manifest["elements"]}
        title = elements["plot.title"]
        assert title["label"] == "Fuel economy"
        assert title["identity"] == "r1:568c4e10e2a02cd8"
        x, y, width, height = title["bbox"]
        assert 0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        assert title["editable"] == [{"prop": "color", "type": "color", "value": "#112233"}]
        top = elements["axis.x.top.title"]
        bottom = elements[X_BOTTOM]
        assert top["label"] == bottom["label"] == "Engine displacement"
        assert top["identity"] != bottom["identity"]
        assert bottom["editable"][0]["value"] == "#6B6B6B"
        invalid = [{"gid": "plot.title", "prop": "text", "value": "Unchanged"}]
        with pytest.raises(
            pool.WorkerError, match="supports plain-text title and facet-label colors"
        ):
            session.override(stem, invalid, inline_svg=True)
        with pytest.raises(pool.WorkerError, match="text is unavailable"):
            session.override(
                stem,
                [{"gid": "axis.y.right.title", "prop": "color", "value": "#6C3799"}],
                inline_svg=True,
            )
        color = [
            {
                "gid": "plot.title",
                "prop": "color",
                "value": "#6C3799",
                "identity": title["identity"],
            }
        ]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(stem, [{**color[0], "identity": "r1:another-title"}], inline_svg=True)
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                stem,
                [{**color[0], "gid": X_BOTTOM, "identity": top["identity"]}],
                inline_svg=True,
            )
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        changed = session.override(stem, color, inline_svg=True)
        edited = changed["svg"]
        changed_title = next(
            element for element in changed["manifest"]["elements"] if element["gid"] == "plot.title"
        )
        assert changed_title["bbox"] == title["bbox"]
        assert changed_title["editable"][0]["value"] == "#6C3799"
        assert edited != baseline["svg"]
        assert edited.replace("#6C3799", "#112233", 1) == baseline["svg"]
        pdf = tmp_path / "rendered.pdf"
        companion = tmp_path / "rendered.svg"
        exported = session.export(stem, color, str(pdf), svg_path=str(companion))
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert companion.read_text(encoding="utf-8") == edited
        assert exported["svg"]["size_bytes"] == len(edited.encode("utf-8"))
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
    finally:
        assert session.cancel_owned().confirmed
    assert session.session_id not in envlease.native_sessions_on(spec.interpreter)


def test_desktop_ggplot_edit_uses_the_layout_archive_and_mixed_export(tmp_path, monkeypatch):
    from tavotto import app as service, security
    from tavotto.engine import documentassets
    from test_chart_endpoints import BIND_REQUEST, CORPUS

    rscript, explicit = _rscript()
    if explicit is None:
        pytest.skip("set TAVOTTO_TEST_RSCRIPT to verify the full desktop route")
    home = tmp_path / "warm"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(home))
    root = tmp_path / "混合 项目"
    root.mkdir()
    script = root / "Fuel economy.R"
    script.write_text(
        """
cat("computed", file = "r-witness")
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point() +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))
""",
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; "
            "plt.plot([1, 2, 3], [4, 6, 5]); plt.savefig('reference.pdf')",
        ],
        cwd=root,
        check=True,
        timeout=40,
    )
    nonce = "native-ggplot-test"
    auth = security.SessionState(nonce)
    monkeypatch.setitem(service.app.config, "TESTING", True)
    monkeypatch.setitem(service.app.config, "TAVOTTO_DESKTOP_MODE", True)
    monkeypatch.setitem(service.app.config, security.STATE_KEY, auth)
    original_safe_worker = service._safe_worker

    def safe_worker(script, *args, **kwargs):
        assert not script.endswith(".R"), "An R asset was dispatched to a Python worker"
        return original_safe_worker(script, *args, **kwargs)

    monkeypatch.setattr(service, "_safe_worker", safe_worker)
    client = service.app.test_client()
    service.open_project(str(root))
    session = None
    try:
        target = {
            "script": script.name,
            "object": "plot",
            "frame_pt": [432, 288],
            "rscript": rscript,
        }
        unauthorized = client.post("/api/native/ggplot2", json=target)
        assert unauthorized.status_code != 202
        assert not (root / "r-witness").exists()
        client.set_cookie(security.COOKIE_NAME, auth.redeem(nonce))
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state((nativesession.BARRIER,), timeout=20) == nativesession.BARRIER
        build_url = f"/api/native/sessions/{sid}/build"
        built = client.post(build_url)
        assert built.status_code == 200, built.get_json()
        (descriptor,) = built.get_json()["descriptors"]
        source = descriptor["asset_id"]
        assert (root / "r-witness").read_text() == "computed"

        def render(patches):
            result = client.post(
                "/api/engine/render", json={"id": source, "patches": patches, "inline_svg": True}
            )
            assert result.status_code == 200, result.get_json()
            return result.get_json()

        elements = {element["gid"]: element for element in render([])["manifest"]["elements"]}
        assert elements[LEGEND]["role"] == "legend_text"
        assert elements[LEGEND]["label"] == "Drive type"
        assert elements[FACET]["role"] == "text"
        assert elements[FACET]["label"] == "Cylinder 8"
        expected = {
            "plot.title": "#6C3799",
            X_BOTTOM: "#8C3011",
            Y_LEFT: "#2A7654",
            LEGEND: "#2370B1",
            FACET: "#BE3D47",
        }
        patches = [
            {"gid": gid, "prop": "color", "value": color, "identity": elements[gid]["identity"]}
            for gid, color in expected.items()
        ]

        edit = render(patches)
        svg = edit["svg"]
        for element in edit["manifest"]["elements"]:
            gid = element["gid"]
            assert element["editable"][0]["value"] == expected.get(
                gid, elements[gid]["editable"][0]["value"]
            )
        other_facet = elements["facet.t.1.1.label"]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                descriptor["stem"],
                [*patches[:-1], {**patches[-1], "identity": other_facet["identity"]}],
                inline_svg=True,
            )
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        without_facet = render(patches[:-1])["svg"]
        assert "#2370B1" in without_facet and "#BE3D47" not in without_facet
        undo_x_and_y = render(patches[:1])["svg"]
        assert (
            "#6C3799" in undo_x_and_y
            and "#8C3011" not in undo_x_and_y
            and "#2A7654" not in undo_x_and_y
        )
        assert render(patches)["svg"] == svg
        # The second window's build cannot relabel the edited SVG as baseline.
        assert client.post(build_url).status_code == 200
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        cache = client.post("/api/runtime/preview", json={"id": source, "patches": patches})
        assert cache.headers["X-Tavotto-Preview-Exact"] == "1"
        assert cache.get_data(as_text=True) == svg

        panels = [
            {
                "id": "mpl",
                "type": "panel",
                "fileId": "reference.pdf",
                "fileKind": "pdf",
                "x": 4,
                "y": 8,
                "w": 68,
                "h": 50,
                "nativeW": 68,
                "nativeH": 50,
                "figureFrame": 1,
                "overrides": [],
            },
            {
                "id": "r",
                "type": "panel",
                "fileId": source,
                "fileKind": "runtime",
                "x": 80,
                "y": 8,
                "w": 75,
                "h": 50,
                "nativeW": 75,
                "nativeH": 50,
                "figureFrame": 1,
                "overrides": patches,
                "source": {
                    "captureSource": "ggplot2",
                    "script": descriptor["script"],
                    "entry": descriptor["entry"],
                    "stem": descriptor["stem"],
                },
            },
        ]
        layout = "/api/layouts/Mixed%20native"
        saved = client.post(
            layout + "?target=project",
            json={
                "schema": 3,
                "project": {"id": "mixed", "name": "Mixed native"},
                "canvases": [
                    {
                        "id": "page",
                        "name": "Matplotlib and ggplot2",
                        "schema": 2,
                        "page": {"w": 160, "h": 65},
                        "objects": panels,
                        "guides": [],
                    },
                ],
                "activeCanvasId": "page",
                "createdAt": 1,
                "updatedAt": 1,
            },
        )
        assert saved.status_code == 200, saved.get_json()
        assert "r" in saved.get_json()["figure_snapshots"]
        saved_document = client.get(layout).get_json()
        reference = saved_document["canvases"][0]["objects"][1]["figureSnapshot"]
        assert session.cancel_owned().confirmed
        session = None
        # Reopening can autosave before the sidecar itself restarts.
        slot = "/api/autosave/d_native_ggplot_stopped"
        autosaved = client.put(slot, json=saved_document)
        assert autosaved.status_code == 200, autosaved.get_json()
        assert (
            client.get(slot).get_json()["canvases"][0]["objects"][1]["figureSnapshot"] == reference
        )
        original_request = {
            "scope": "original",
            "filename": "Archived original",
            "formats": ["pdf"],
            "original": {
                "figure_id": source,
                "overrides": patches,
                "figure_snapshot": reference,
            },
        }
        exported = client.post("/api/export", json=original_request)
        assert exported.status_code == 200, exported.get_json()
        original = exported.get_json()
        assert original["status"] == "done", original
        colors = _pdf_text_colors(Path(original["export_dir"]) / original["outputs"][0]["name"])
        assert colors["Cylinder 8"] == {int(expected[FACET][1:], 16)}

        script.write_text('stop("new execution failed")\n', encoding="utf-8")
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state(nativesession.TERMINAL_STATES, timeout=20)
        assert session.execution_id
        stale = client.post("/api/export", json={**original_request, "filename": "Stale original"})
        assert stale.get_json().get("error", {}).get("code") == "svg_snapshot_missing"
        assert not (root / "tavottofile" / "export" / "Stale original.pdf").exists()
        session = None
        service.reset_projects()
        script.rename(root / "source-moved")
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "cold"))
        service.open_project(str(root))

        opened = client.get(layout)
        assert opened.status_code == 200, opened.get_json()
        mpl, r = opened.get_json()["canvases"][0]["objects"]
        assert r["overrides"] == patches
        reference = r["figureSnapshot"]
        archive = {"id": source, "patches": patches, "figure_snapshot": reference}
        preview = client.post("/api/figure-preview", json=archive)
        assert preview.status_code == 200, preview.get_json()
        assert preview.get_json()["svg"] == svg
        mismatch = client.post("/api/figure-preview", json={**archive, "patches": []})
        assert mismatch.status_code == 422, mismatch.get_json()

        exported = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": "Mixed native",
                "formats": ["pdf"],
                "canvas": {
                    "page_w_mm": 160,
                    "page_h_mm": 65,
                    "objects": [
                        {
                            "type": "panel",
                            "id": mpl["fileId"],
                            "x_mm": 4,
                            "y_mm": 8,
                            "w_mm": 68,
                            "h_mm": 50,
                        },
                        {
                            "type": "panel",
                            "id": source,
                            "x_mm": 80,
                            "y_mm": 8,
                            "w_mm": 75,
                            "h_mm": 50,
                            "overrides": patches,
                            "figure_snapshot": reference,
                        },
                    ],
                },
            },
        )
        assert exported.status_code == 200, exported.get_json()
        result = exported.get_json()
        assert result["status"] == "done" and result["outputs"][0]["format"] == "pdf"
        colors = _pdf_text_colors(Path(result["export_dir"]) / result["outputs"][0]["name"])
        for gid, color in expected.items():
            assert colors[elements[gid]["label"]] == {int(color[1:], 16)}
        assert all(not active.alive() for active in nativesession.REGISTRY.list(str(root)))

        (root / "chart.svg").write_bytes((CORPUS / "line_two_series.svg").read_bytes())
        binding = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": "chart.svg"})
        assert binding.status_code == 200, binding.get_json()
        chart = {key: binding.get_json()[key] for key in ("model", "binding")}
        mixed_document = opened.get_json()
        mixed_document["schema"] = 4
        mixed_document["canvases"].append(
            {
                "id": "bound-chart",
                "schema": 2,
                "name": "Computed SVG",
                "page": {"w": 140, "h": 80},
                "guides": [],
                "objects": [
                    {
                        "id": "chart-panel",
                        "type": "panel",
                        "fileId": "chart.svg",
                        "fileKind": "svg",
                        "chart": chart,
                        "x": 5,
                        "y": 5,
                        "w": 110,
                        "h": 60,
                        "nativeW": 110,
                        "nativeH": 60,
                        "figureFrame": 1,
                        "overrides": [],
                    }
                ],
            }
        )
        named = "/api/layouts/Portable%20mixed"
        saved = client.post(named + "?target=project", json=mixed_document)
        assert saved.status_code == 200, saved.get_json()
        mixed_document = client.get(named).get_json()
        chart_reference = mixed_document["canvases"][1]["objects"][0]["chartSource"]
        original_chart = (root / "chart.svg").read_bytes()
        (root / "chart.svg").unlink()
        packed = client.post("/api/package", json={"doc": mixed_document})
        assert packed.status_code == 200, packed.get_json()
        package = client.get(packed.get_json()["url"]).get_data()

        service.reset_projects()
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "receiver data"))
        receiver = tmp_path / "收件人 项目"

        def upload(action, **fields):
            return client.post(
                f"/api/package/{action}",
                data={"package": (io.BytesIO(package), "portable.tavotto"), **fields},
            )

        assert upload("open").get_json() == {"document_format": "v4"}
        inspection = upload("inspect")
        assert inspection.status_code == 200, inspection.get_json()
        assert inspection.get_json()["missing"] == [] and not receiver.exists()
        result = upload("install", options=json.dumps({"destination": str(receiver)}))
        assert result.status_code == 201, result.get_json()
        assert not (receiver / script.name).exists() and not (receiver / "chart.svg").exists()
        assert (
            documentassets.source_path(receiver / "tavottofile", chart_reference).read_bytes()
            == original_chart
        )
        service.open_project(str(receiver))
        assert client.post("/api/figure-preview", json=archive).get_json()["svg"] == svg
        persisted = client.post(named + "?target=project", json=result.get_json()["doc"])
        assert persisted.status_code == 200, persisted.get_json()
        assert not nativesession.REGISTRY.list(str(receiver))
    finally:
        if session is not None:
            session.cancel_owned()
        service.reset_projects()
