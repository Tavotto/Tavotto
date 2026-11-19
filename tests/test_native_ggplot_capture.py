"""Real R acceptance for an edit of an already built ggplot2 scene."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import desktopproc, envlease, execspec, nativeggplot, nativesession, pool

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
"""


def test_ggplot_title_paint_keeps_the_built_layers_and_replays(tmp_path):
    explicit_rscript = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    rscript = explicit_rscript or shutil.which("Rscript")
    if not rscript:
        pytest.skip("real Rscript runtime is unavailable")
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
    explicit_rscript = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    rscript = explicit_rscript or shutil.which("Rscript")
    if not rscript:
        pytest.skip("real Rscript runtime is unavailable")
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
    session = desktopproc.start(
        desktopproc.OwnedLaunch(spec, ggplot_target=nativeggplot.GgplotTarget("plot", (432, 288))),
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
        assert list(baseline["stems"]) == ["plot"]
        manifest = baseline["manifest"]
        assert manifest["size_mm"] == pytest.approx([152.4, 101.6])
        (title,) = manifest["elements"]
        assert title["label"] == "Fuel economy"
        x, y, width, height = title["bbox"]
        assert 0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        assert title["editable"] == [{"prop": "color", "type": "color", "value": "#112233"}]
        invalid = [{"gid": "plot.title", "prop": "text", "value": "Unchanged"}]
        with pytest.raises(pool.WorkerError, match="supports the plain-text title color"):
            session.override("plot", invalid, inline_svg=True)
        color = [{"gid": "plot.title", "prop": "color", "value": "#6C3799"}]
        changed = session.override("plot", color, inline_svg=True)
        edited = changed["svg"]
        (changed_title,) = changed["manifest"]["elements"]
        assert changed_title["bbox"] == title["bbox"]
        assert changed_title["editable"][0]["value"] == "#6C3799"
        assert edited != baseline["svg"]
        assert edited.replace("#6C3799", "#112233", 1) == baseline["svg"]
        pdf = tmp_path / "rendered.pdf"
        companion = tmp_path / "rendered.svg"
        session.export("plot", color, str(pdf), svg_path=str(companion))
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert companion.read_text(encoding="utf-8").rstrip("\n") == edited.rstrip("\n")
        assert session.override("plot", [], inline_svg=True)["svg"] == baseline["svg"]
    finally:
        assert session.cancel_owned().confirmed
    assert session.session_id not in envlease.native_sessions_on(spec.interpreter)
