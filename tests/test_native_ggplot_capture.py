"""Real R acceptance for an edit of an already built ggplot2 scene."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

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
  ggplot2::geom_point(position = ggplot2::position_jitter(width = .12, height = 0)) +
  ggplot2::stat_summary(fun = tracked_mean, geom = "point", shape = 4) +
  ggplot2::facet_wrap(ggplot2::vars(cyl)) +
  ggplot2::labs(title = "Fuel economy") +
  ggplot2::theme_bw() +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))

baseline <- adapter$capture_ggplot(plot, c(432, 288))
stopifnot(summary_calls > 0L, length(baseline$state$layers) == 2L,
          length(baseline$state$panel_ranges) == 4L,
          length(baseline$state$title_bbox) == 4L)
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
edited <- adapter$render_ggplot(baseline$state, "#6C3799")
undone <- adapter$render_ggplot(baseline$state)
stopifnot(summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))
stopifnot(lengths(gregexpr("<svg ", baseline$svg, fixed = TRUE)) == 1L,
          grepl("#6C3799", edited, fixed = TRUE),
          !grepl("#6C3799", baseline$svg, fixed = TRUE),
          identical(sub("#6C3799", "#112233", edited, fixed = TRUE), baseline$svg),
          identical(undone, baseline$svg))

bad_value <- tryCatch(adapter$render_ggplot(baseline$state, "not-a-colour"),
                      error = conditionMessage)
stopifnot(identical(bad_value, "The plot title color must be an RGB hex color."))
no_title <- adapter$capture_ggplot(plot + ggplot2::labs(title = NULL), c(432, 288))
stopifnot(is.null(no_title$state$title))
bad_target <- tryCatch(adapter$render_ggplot(no_title$state, "#6C3799"),
                       error = conditionMessage)
stopifnot(identical(bad_target, "A single plain-text plot title is required."),
          identical(grDevices::dev.cur(), c("null device" = 1L)))

if (capabilities("cairo")) {
  cairo_pdf <- grDevices::cairo_pdf
  utils::assignInNamespace("cairo_pdf", function(...) warning("fixture missing Cairo"), "grDevices")
  failed_pdf <- suppressWarnings(tryCatch(
    adapter$export_ggplot_pdf(baseline$state, "#6C3799", "unavailable.pdf"),
    error = conditionMessage
  ))
  utils::assignInNamespace("cairo_pdf", cairo_pdf, "grDevices")
  stopifnot(grepl("fixture missing Cairo", failed_pdf, fixed = TRUE),
            !file.exists("unavailable.pdf"), !file.exists("Rplots.pdf"),
            identical(grDevices::dev.cur(), c("null device" = 1L)))
}
"""


def test_ggplot_title_paint_keeps_the_built_layers_and_replays(tmp_path):
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
  ggplot2::labs(title = "Fuel economy") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))
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
        (title,) = manifest["elements"]
        assert title["label"] == "Fuel economy"
        x, y, width, height = title["bbox"]
        assert 0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        assert title["editable"] == [{"prop": "color", "type": "color", "value": "#112233"}]
        invalid = [{"gid": "plot.title", "prop": "text", "value": "Unchanged"}]
        with pytest.raises(pool.WorkerError, match="supports the plain-text title color"):
            session.override(stem, invalid, inline_svg=True)
        color = [
            {
                "gid": "plot.title",
                "prop": "color",
                "value": "#6C3799",
                "identity": title["identity"],
            }
        ]
        with pytest.raises(pool.WorkerError, match="plot title changed"):
            session.override(stem, [{**color[0], "identity": "r1:another-title"}], inline_svg=True)
        changed = session.override(stem, color, inline_svg=True)
        edited = changed["svg"]
        (changed_title,) = changed["manifest"]["elements"]
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
  ggplot2::geom_point() + ggplot2::facet_wrap(ggplot2::vars(cyl)) +
  ggplot2::labs(title = "Fuel economy") +
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

        patches = [{"gid": "plot.title", "prop": "color", "value": "#6C3799"}]
        render_url = "/api/engine/render"
        edit = client.post(render_url, json={"id": source, "patches": patches, "inline_svg": True})
        assert edit.status_code == 200, edit.get_json()
        svg = edit.get_json()["svg"]
        # The second window's build cannot relabel the edited SVG as baseline.
        assert client.post(build_url).status_code == 200
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        assert "#6C3799" in svg
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
                        "schema": 2,
                        "page": {"w": 160, "h": 65},
                        "objects": panels,
                        "guides": [],
                    },
                ],
                "activeCanvasId": "page",
            },
        )
        assert saved.status_code == 200, saved.get_json()
        assert "r" in saved.get_json()["figure_snapshots"]
        assert session.cancel_owned().confirmed
        session = None
        service.reset_projects()
        script.rename(root / "source-moved")
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "cold"))
        service.open_project(str(root))

        opened = client.get(layout)
        assert opened.status_code == 200, opened.get_json()
        mpl, r = opened.get_json()["canvases"][0]["objects"]
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
        assert (Path(result["export_dir"]) / result["outputs"][0]["name"]).is_file()
        assert all(not active.alive() for active in nativesession.REGISTRY.list(str(root)))
    finally:
        if session is not None:
            session.cancel_owned()
        service.reset_projects()
