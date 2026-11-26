"""Real R acceptance for an edit of an already built ggplot2 scene."""

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
import pytest
from PIL import Image

from support.foundation_app import sidecar_app
from tavotto.engine import (
    desktopproc,
    envlease,
    execspec,
    figcapture,
    nativeggplot,
    nativeggplot_source,
    nativesession,
    pool,
)
from tavotto.engine.fixtools import cmdsandbox, winappcontainer

# Cold graphics-device and font discovery can exceed 40 seconds on hosted Intel Macs.
_COLD_RENDER_TIMEOUT_SECONDS = 120


def _rscript():
    explicit = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    executable = explicit or shutil.which("Rscript")
    if executable is None:
        pytest.skip("real Rscript runtime is unavailable")
    return executable, explicit


def _rscript_with_capture_packages():
    rscript, explicit = _rscript()
    dependencies = subprocess.run(
        [
            rscript,
            "--vanilla",
            "-e",
            'quit(status = if (all(vapply(c("ggplot2", "svglite", "jsonlite"), '
            "requireNamespace, logical(1L), quietly = TRUE))) 0L else 77L)",
        ],
        capture_output=True,
        check=False,
        timeout=45,
    )
    if dependencies.returncode == 77 and not explicit:
        pytest.skip("Native R capture dependencies are unavailable")
    assert dependencies.returncode == 0, dependencies.stderr
    return rscript


def test_native_r_source_candidate_replays_successive_edits(tmp_path):
    rscript = _rscript_with_capture_packages()
    object_name = "signal 📈"
    source = (
        'd <- read.csv("data.csv")\r\n'
        "`signal 📈` <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +\r\n"
        '  ggplot2::geom_point() + ggplot2::labs(title = "Signal", x = "Input")  # Keep\r\n'
        'ggplot2::ggsave("standalone.pdf", `signal 📈`, width = 4, height = 3)\r\n'
    ).encode()
    original = tmp_path / "original.R"
    original.write_bytes(source)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "data.csv").write_text("x,y\n1,2\n2,6\n3,4\n", encoding="utf-8")
    staged = stage / "original.R"
    staged.write_bytes(source)
    result = stage / "result.json"
    request = stage / "request.json"
    native = ADAPTER.parent

    def run(operation, **extra):
        request.write_text(
            json.dumps(
                {
                    "operation": operation,
                    "script": staged.as_posix(),
                    "object": object_name,
                    "result": result.as_posix(),
                    "adapter": ADAPTER.as_posix(),
                    "bridge": (native / "ggplot_bridge.R").as_posix(),
                    **extra,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [rscript, "--vanilla", str(native / "ggplot_source_replay.R"), str(request)],
            cwd=stage,
            capture_output=True,
            check=False,
            timeout=_COLD_RENDER_TIMEOUT_SECONDS,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(result.read_text(encoding="utf-8"))

    anchor = run("anchor")
    old = {"gid": "plot.title", "prop": "color", "value": "#572B82"}
    first = nativeggplot_source.build(
        source, object_name, [old], anchor["line"], anchor["byte"], themes=anchor["themes"]
    )
    assert first.written == 1 and not first.residual
    staged.write_bytes(first.data)
    assert run("anchor") == anchor
    changes = [
        {**old, "value": "#206A30"},
        {"gid": "axis.x.bottom.title", "prop": "color", "value": "#892854"},
    ]
    untouched = {"gid": "plot.title", "prop": "rotation", "value": 15}
    second = nativeggplot_source.build(
        first.data,
        object_name,
        [*changes, untouched],
        anchor["line"],
        anchor["byte"],
        themes=anchor["themes"],
    )
    assert second.written == 2 and second.residual == [untouched]
    assert b"# Keep\r\n" in second.data and b"\n" not in second.data.replace(b"\r\n", b"")
    staged.write_bytes(second.data)
    run(
        "replay",
        palettes=(native.parent / "color_palettes.json").as_posix(),
        frame_pt=[288, 216],
        stem="signal",
        pdf=(stage / "native.pdf").as_posix(),
    )
    for pdf in (stage / "standalone.pdf", stage / "native.pdf"):
        colours = _pdf_text_colors(pdf)
        assert colours["Signal"] == {0x206A30}
        assert colours["Input"] == {0x892854}
    assert original.read_bytes() == source


def test_owned_r_source_export_checks_the_captured_scene(tmp_path, monkeypatch):
    from tavotto import app as service, security

    rscript = _rscript_with_capture_packages()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "local data"))

    root = tmp_path / "研究 项目"
    graphs = root / "figure scripts"
    graphs.mkdir(parents=True)
    numbers = root / "numbers.csv"
    numbers.write_text("x,y\n1,2\n2,4\n3,3\n", encoding="utf-8")
    source = graphs / "Fig.R"
    original = b"""protected <- Sys.getenv("TAVOTTO_R_REPLAY_PROTECTED_FILE")
if (nzchar(protected)) {
  attempted <- try(suppressWarnings(writeLines("must not land", protected)), silent = TRUE)
  if (!inherits(attempted, "try-error")) stop("The protected file could be changed.")
}
port <- Sys.getenv("TAVOTTO_R_REPLAY_PROBE_PORT")
if (nzchar(port)) {
  network <- try(suppressWarnings(socketConnection("127.0.0.1", as.integer(port),
                 blocking = TRUE, timeout = 2)), silent = TRUE)
  if (!inherits(network, "try-error")) stop("The protected replay reached a local server.")
}
d <- read.csv("../numbers.csv")
p <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_point() + ggplot2::labs(title = "Signal", x = "Input") +
  ggplot2::theme(axis.title.x.bottom = ggplot2::element_text(face = "italic"))
ggplot2::ggsave("submitted.pdf", p, width = 4, height = 3)
"""
    source.write_bytes(original)
    submitted = graphs / "submitted.pdf"
    nonce = "native-r-source-test"
    auth = security.SessionState(nonce)
    monkeypatch.setitem(service.app.config, "TESTING", True)
    monkeypatch.setitem(service.app.config, "TAVOTTO_DESKTOP_MODE", True)
    monkeypatch.setitem(service.app.config, security.STATE_KEY, auth)
    client = service.app.test_client()
    client.set_cookie(security.COOKIE_NAME, auth.redeem(nonce))
    service.open_project(str(root))
    worker = None
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        launched = client.post(
            "/api/native/ggplot2",
            json={
                "script": source.relative_to(root).as_posix(),
                "object": "p",
                "frame_pt": [288, 216],
                "rscript": rscript,
            },
        )
        assert launched.status_code == 202, launched.get_json()
        sid = launched.json["session"]["session_id"]
        worker = nativesession.REGISTRY.get(sid)
        assert worker.wait_for_state({nativesession.BARRIER}, timeout=30) == nativesession.BARRIER
        built = client.post(f"/api/native/sessions/{sid}/build")
        assert built.status_code == 200, built.get_json()
        (descriptor,) = built.json["descriptors"]
        figure = descriptor["asset_id"]
        original_pdf = submitted.read_bytes()
        rendered = client.post("/api/engine/render", json={"id": figure, "patches": []})
        assert rendered.status_code == 200, rendered.get_json()
        elements = {item["gid"]: item for item in rendered.json["manifest"]["elements"]}
        edits = [
            ("plot.title", "color", "#572B82"),
            ("axis.x.bottom.ticks", "color", "#A64184"),
            ("axis.x.bottom.ticks", "fontsize", 12.5),
            ("axis.x.bottom.title", "weight", "bold"),
            ("axis.x.bottom.title", "fontsize", 18.5),
        ]
        patches = [
            {"gid": gid, "identity": elements[gid]["identity"], "prop": prop, "value": value}
            for gid, prop, value in edits
        ]
        request = {
            "filename": "Signal",
            "scope": "original",
            "formats": [],
            "include_edited_script": True,
            "original": {"figure_id": figure, "overrides": patches},
        }
        validated = client.post("/api/export/validate", json=request)
        assert validated.status_code == 200 and validated.json.get("ok"), validated.json
        offer = validated.json["route"]["panels"][figure]["offers"]["script"]
        protected = tmp_path / "keep-private"
        protected.write_bytes(b"external file")
        monkeypatch.setenv("TAVOTTO_R_REPLAY_PROTECTED_FILE", str(protected))
        monkeypatch.setenv("TAVOTTO_R_REPLAY_PROBE_PORT", str(listener.getsockname()[1]))
        with monkeypatch.context() as unavailable:
            if sys.platform == "win32":
                unavailable.setattr(winappcontainer, "available", lambda: False)
            else:
                unavailable.setattr(cmdsandbox, "sandbox_kind", lambda **_options: None)
            rejected = client.post("/api/export/validate", json=request)
            rejected_offer = rejected.json["route"]["panels"][figure]["offers"]["script"]
            assert rejected_offer == {
                "how": "unavailable",
                "reason": "script_export_r_source_unavailable",
                "suffix": "_edited.R",
            }
            refused = client.post("/api/export", json=request)
            assert refused.json["outputs"][0]["error"]["code"] == rejected_offer["reason"], (
                refused.json
            )
            assert not (Path(refused.json["export_dir"]) / "Signal_edited.R").exists()
            assert protected.read_bytes() == b"external file"
            assert source.read_bytes() == original and submitted.read_bytes() == original_pdf
        assert offer == {"how": "redraw", "suffix": "_edited.R"}
        exported = client.post("/api/export", json=request)
        assert exported.status_code == 200, exported.get_json()
        (output,) = exported.json["outputs"]
        assert output["name"] == "Signal_edited.R"
        assert output["script_report"] == {"written": 5, "skipped": []}
        edited = Path(exported.json["export_dir"]) / output["name"]
        assert b'colour = "#572B82"' in edited.read_bytes()
        assert (
            b'axis.text.x.bottom = ggplot2::element_text(colour = "#A64184", size = 12.5)'
            in edited.read_bytes()
        )
        assert b'face = "bold.italic"' in edited.read_bytes()
        assert b"size = 18.5" in edited.read_bytes()
        assert protected.read_bytes() == b"external file"
        assert source.read_bytes() == original and submitted.read_bytes() == original_pdf
        assert client.post("/api/export/validate", json=request).json["conflicts"] == [edited.name]

        # Geometry changes caused by data on disk must not become an edited source export.
        numbers.write_text("x,y\n1,2\n2,40\n3,3\n", encoding="utf-8")
        mismatch = client.post("/api/export", json={**request, "filename": "Changed"})
        assert mismatch.json["outputs"][0]["error"]["code"] == "script_export_diverged", (
            mismatch.json
        )
        assert not (edited.parent / "Changed_edited.R").exists()
        assert source.read_bytes() == original and submitted.read_bytes() == original_pdf

        # Writeback uses the same pixel gate and requires an additional confirmation.
        numbers.write_text("x,y\n1,2\n2,4\n3,3\n", encoding="utf-8")
        width, height = rendered.json["manifest"]["size_mm"]
        layout = {
            "schema": 3,
            "project": {"id": "source-style", "name": "Source style"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "guides": [],
                    "objects": [
                        {
                            "type": "panel",
                            "id": "signal",
                            "fileId": figure,
                            "fileKind": "pdf",
                            "x": 5,
                            "y": 5,
                            "w": width,
                            "h": height,
                            "nativeW": width,
                            "nativeH": height,
                            "figureFrame": 1,
                            "overrides": patches,
                        }
                    ],
                }
            ],
        }
        layout_url = "/api/layouts/Source_style"
        saved = client.post(layout_url + "?target=project", json=layout)
        assert saved.status_code == 200, saved.json
        assert "signal" in saved.json["figure_snapshots"]
        preview = client.post(
            "/api/script-edit/figure-style/preview",
            json={
                "layout": "Source_style",
                "revision": saved.json["revision"],
                "canvas_id": "main",
                "panel_id": "signal",
            },
        )
        assert preview.status_code == 200, preview.json
        assert preview.json["written"] == 5 and not preview.json["skipped"]
        commit = "/api/script-edit/figure-style/commit"
        token = preview.json["token"]
        refused = client.post(commit, json={"token": token})
        assert refused.status_code == 409 and source.read_bytes() == original
        applied = client.post(commit, json={"token": token, "confirmed": True})
        assert applied.status_code == 200, applied.json
        assert source.read_bytes() == edited.read_bytes()
        assert submitted.read_bytes() == original_pdf and protected.read_bytes() == b"external file"
        current = client.get(layout_url).json["canvases"][0]["objects"][0]
        assert current["overrides"] == []
        assert current["figureSnapshot"] != saved.json["figure_snapshots"]["signal"]
        archived = client.post(
            "/api/figure-preview",
            json={
                "id": figure,
                "patches": [],
                "figure_snapshot": current["figureSnapshot"],
            },
        )
        assert archived.status_code == 200
        assert all(color in archived.json["svg"].upper() for color in ("#572B82", "#A64184"))
        committed_layout = client.get(layout_url).json
        repeated = client.post(
            "/api/layouts/Source_style_copy?target=project", json=committed_layout
        )
        assert repeated.status_code == 200, repeated.json
        assert repeated.json["figure_snapshots"]["signal"] == current["figureSnapshot"]
        restored = client.post(
            "/api/script-backups/restore",
            json={
                "backup_id": applied.json["backup_id"],
                "mode": "full",
                "expected_sha256": applied.json["script_revision"],
            },
        )
        assert restored.status_code == 200, restored.json
        assert source.read_bytes() == original
        previous = client.get(layout_url).json["canvases"][0]["objects"][0]
        assert previous["overrides"] == patches
        assert previous["figureSnapshot"] == saved.json["figure_snapshots"]["signal"]
    finally:
        listener.close()
        if worker is not None:
            worker.cancel_owned()
        service.reset_projects()


def test_connect_to_existing_r_and_release_its_console(tmp_path):
    rscript = _rscript_with_capture_packages()
    project = tmp_path / "R project"
    project.mkdir()
    (project / "source.R").write_text('stop("the desktop must not run this file")\n')
    active = tmp_path / "already-running.R"
    active.write_text(
        r"""
calls <- 0L
record <- function(data) { calls <<- calls + 1L; data }
data <- data.frame(x = 1:3, y = c(2, 4, 3))
name <- "\U000056fe\U0001f4c8"
assign(name, ggplot2::ggplot(data, ggplot2::aes(x, y)) +
       ggplot2::geom_point(data = record) + ggplot2::labs(title = "Already running"),
       envir = .GlobalEnv)
grDevices::pdf("existing-device.pdf")
device <- grDevices::dev.cur()
before <- list(cwd = getwd(), libraries = .libPaths())
cat(Sys.getpid(), file = "console-ready")
stdin <- file("stdin", open = "r")
command <- readLines(stdin, n = 1L)
eval(parse(text = command), envir = .GlobalEnv)
stopifnot(calls == 1L, identical(before$cwd, getwd()), identical(before$libraries, .libPaths()),
          identical(device, grDevices::dev.cur()), exists(name, envir = .GlobalEnv),
          inherits(try(eval(parse(text = command), envir = .GlobalEnv), silent = TRUE), "try-error"))
grDevices::dev.off(device)
close(stdin)
cat(Sys.getpid(), file = "console-resumed")
""",
        encoding="utf-8",
    )
    ready = tmp_path / "console-ready"
    resumed = tmp_path / "console-resumed"
    with (
        sidecar_app(project, tmp_path / "desktop") as app,
        subprocess.Popen(
            [rscript, "--vanilla", str(active)],
            cwd=tmp_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as console,
    ):
        try:
            deadline = time.monotonic() + _COLD_RENDER_TIMEOUT_SECONDS
            while not ready.is_file() or not ready.stat().st_size:
                assert console.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            pid = int(ready.read_text())
            status, offer = app.call(
                "/api/native/ggplot2/invitation",
                {"script": "source.R", "object": "图📈", "frame_pt": [320, 240]},
            )
            assert status == 201
            assert "token" not in offer and set(offer) == {"ok", "command", "expires_in"}
            console.stdin.write(offer["command"] + "\n")
            console.stdin.flush()
            deadline = time.monotonic() + _COLD_RENDER_TIMEOUT_SECONDS
            while True:
                _, result = app.call("/api/native/sessions")
                sessions = result["sessions"]
                if sessions and sessions[0]["state"] in {"barrier", "ended", "failed"}:
                    break
                assert console.poll() is None and time.monotonic() < deadline, sessions
                time.sleep(0.05)
            (session,) = sessions
            assert session["state"] == "barrier", session
            assert session["process_pid"] == pid
            assert session["owner"] == "terminal" and session["capture_source"] == "ggplot2"
            route = "/api/native/sessions/" + session["session_id"]
            _, result = app.call(route + "/build", {}, timeout=_COLD_RENDER_TIMEOUT_SECONDS)
            (descriptor,) = result["descriptors"]
            assert descriptor["script"] == "source.R"
            request = {"id": descriptor["asset_id"], "inline_svg": True, "patches": []}
            _, base = app.call("/api/engine/render", request)
            assert "Already running" in base["svg"]
            patch = {"gid": "plot.title", "prop": "color", "value": "#572B82"}
            _, edited = app.call("/api/engine/render", {**request, "patches": [patch]})
            assert "#572B82" in edited["svg"] and "#572B82" not in base["svg"]
            _, undone = app.call("/api/engine/render", request)
            assert undone["svg"] == base["svg"]
            _, replayed = app.call("/api/engine/render", {**request, "patches": [patch]})
            assert replayed["svg"] == edited["svg"]
            width, height = 320 * 25.4 / 72, 240 * 25.4 / 72
            layout = {
                "schema": 3,
                "project": {"id": "console", "name": "Console"},
                "activeCanvasId": "first",
                "canvases": [
                    {
                        "id": "first",
                        "name": "Console",
                        "schema": 2,
                        "page": {"w": width + 10, "h": height + 10},
                        "guides": [],
                        "objects": [
                            {
                                "id": "r",
                                "type": "panel",
                                "fileId": descriptor["asset_id"],
                                "fileKind": "runtime",
                                "name": "Already running",
                                "x": 5,
                                "y": 5,
                                "w": width,
                                "h": height,
                                "nativeW": width,
                                "nativeH": height,
                                "overrides": [patch],
                            }
                        ],
                    }
                ],
            }
            layout_route = "/api/layouts/Console"
            _, saved = app.call(layout_route + "?target=project", layout)
            snapshot = saved["figure_snapshots"]["r"]
            app.call(route + "/continue", {})
            stdout, stderr = console.communicate(timeout=30)
            assert console.returncode == 0, stdout + stderr
            assert int(resumed.read_text()) == pid
        finally:
            if console.poll() is None:
                console.terminate()
                console.wait(timeout=15)
    (project / "source.R").rename(project / "source.R.moved")
    with sidecar_app(project, tmp_path / "cold") as app:
        _, reopened = app.call(layout_route)
        (panel,) = reopened["canvases"][0]["objects"]
        assert panel["figureSnapshot"] == snapshot
        assert panel["overrides"] == [patch]
        _, archived = app.call(
            "/api/figure-preview",
            {"id": panel["fileId"], "patches": panel["overrides"], "figure_snapshot": snapshot},
        )
        assert "#572B82" in archived["svg"]


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "src" / "tavotto" / "resources" / "native" / "ggplot2.R"
SUBTITLE = "plot.subtitle"
CAPTION = "plot.caption"
TICKS_BOTTOM = "axis.x.bottom.ticks"
TICKS_TOP = "axis.x.top.ticks"
COLOURBAR_TICKS = "legend.bottom.1.colourbar.ticks"
X_BOTTOM = "axis.x.bottom.title"
Y_LEFT = "axis.y.left.title"
LEGEND = "legend.right.1.title"
LEGEND_ENTRY = "legend.right.1.entry.3.2"
LEGEND_BOX = "legend.right"
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


R_PROBE_SETUP = r"""
adapter <- new.env(parent = baseenv())
adapter_file <- commandArgs(trailingOnly = TRUE)[[1L]]
native <- dirname(adapter_file)
parts <- c("ggplot2.R", "ggplot_colour_scale.R", "ggplot_points.R", "ggplot_bars.R",
           "ggplot_ribbons.R", "ggplot_axes.R", "ggplot_frame.R")
for (part in parts) sys.source(file.path(native, part), envir = adapter)
"""


PROBE = r"""

if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) {
  quit(status = 77L)
}

color_edits <- function(colors) lapply(colors, function(value) list(color = value))
render_colors <- function(state, colors = list()) adapter$render_ggplot(state, color_edits(colors))

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
  ggplot2::scale_colour_discrete(labels = c("Four wheel drive", "Front wheel drive",
                                           "Rear wheel drive")) +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type", shape = "Cylinders") +
  ggplot2::guides(
    colour = ggplot2::guide_legend(order = 1, theme = ggplot2::theme(
      legend.title = ggplot2::element_text(colour = "#667788"),
      legend.text = ggplot2::element_text(colour = "#7D6439"))),
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
            "legend.right.1.entry.3.2" = "Front wheel drive",
            "facet.t.2.2.label" = "Cylinder 8")
facets <- c("facet.t.1.2.label" = "Cylinder 6", "facet.t.2.2.label" = "Cylinder 8",
            "facet.t.1.1.label" = "Cylinder 4", "facet.t.2.1.label" = "Cylinder 5")
original <- c("#112233", "#223344", "#334455", "#445566", "#556677",
              "#667788", "#778899", "#7D6439", "#202040")
colors <- setNames(as.list(c("#6C3799", "#8C3011", "#2370B1", "#2A7654",
                            "#923190", "#3373A7", "#C75052", "#E058A9", "#BE3D47")),
                   names(labels))
entries <- function(state) {
  state$text_targets[grepl("\\.entry\\.", names(state$text_targets))]
}
stopifnot(summary_calls > 0L, length(baseline$state$layers) == 2L,
          length(baseline$state$panel_ranges) == 4L,
          identical(setdiff(names(baseline$state$text_targets), names(entries(baseline$state))),
                    c(head(names(labels), -2L), names(facets))),
          length(entries(baseline$state)) == 7L,
          identical(baseline$state$text_targets[["legend.right.1.entry.3.2"]]$legend_title,
                    "Drive type"),
          identical(vapply(baseline$state$text_targets[names(facets)], function(t) t$label,
                           character(1L)), facets))
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
edited <- render_colors(baseline$state, colors)
undone <- render_colors(baseline$state)
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

axis_titles <- names(labels)[2:5]
faces <- setNames(rep(list(list(weight = "bold", style = "italic")), 4L), axis_titles)
faced_svg <- adapter$render_ggplot(baseline$state, faces)
faced <- adapter$ggplot_manifest(baseline$state, "baseline", faces)$elements
for (gid in axis_titles) {
  item <- Filter(function(el) identical(el$gid, gid), faced)[[1L]]
  target <- baseline$state$text_targets[[gid]]
  line <- grep(paste0(">", target$label, "</text>"),
               strsplit(faced_svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
  stopifnot(length(line) == 1L, grepl("font-weight: bold", line, fixed = TRUE),
            grepl("font-style: italic", line, fixed = TRUE),
            !identical(item$bbox, as.list(target$bbox)),
            identical(item$editable[[2L]]$value, "bold"),
            identical(item$editable[[3L]]$value_original, "normal"))
}
axis_variant <- function(face) {
  svg <- adapter$render_ggplot(baseline$state, list("axis.x.bottom.title" = face))
  grep(">Engine displacement</text>", strsplit(svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
}
stopifnot(grepl("font-weight: bold", axis_variant(list(weight = "bold")), fixed = TRUE),
          !grepl("font-style: italic", axis_variant(list(weight = "bold")), fixed = TRUE),
          grepl("font-style: italic", axis_variant(list(style = "italic")), fixed = TRUE),
          !grepl("font-weight: bold", axis_variant(list(style = "italic")), fixed = TRUE),
          identical(adapter$render_ggplot(baseline$state), baseline$svg),
          summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))

sizes <- setNames(rep(list(list(fontsize = 21.25)), length(axis_titles)), axis_titles)
sized_svg <- adapter$render_ggplot(baseline$state, sizes)
sized <- adapter$ggplot_manifest(baseline$state, "baseline", sizes)$elements
for (gid in axis_titles) {
  item <- Filter(function(el) identical(el$gid, gid), sized)[[1L]]
  original <- baseline$state$text_targets[[gid]]
  size <- Filter(function(field) identical(field$prop, "fontsize"), item$editable)[[1L]]
  stopifnot(identical(size$value, 21.25), identical(size$value_original, original$paint$fontsize),
            prod(unlist(item$bbox[3:4])) > prod(original$bbox[3:4]))
  line <- grep(paste0(">", original$label, "</text>"), strsplit(sized_svg, "\n")[[1L]],
               value = TRUE, fixed = TRUE)
  stopifnot(length(line) == 1L, grepl("font-size: 21.25px", line, fixed = TRUE))
}
stopifnot(identical(adapter$render_ggplot(baseline$state), baseline$svg),
          summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))

bad_value <- tryCatch(render_colors(baseline$state, list("plot.title" = "not-a-colour")),
                      error = conditionMessage)
stopifnot(identical(bad_value, "The native R text color must be an RGB hex color."))
limited <- adapter$capture_ggplot(
  plot + ggplot2::labs(title = NULL, x = expression(alpha), colour = "Shared", shape = "Shared") +
    ggplot2::theme(axis.title.y.left = ggplot2::element_blank()),
  c(432, 288))
stopifnot(identical(names(limited$state$text_targets),
                    c("axis.x.top.title", "axis.y.right.title", names(facets))),
          length(grep(">Shared</text>", strsplit(limited$svg, "\n")[[1L]], fixed = TRUE)) == 2L)
bad_target <- tryCatch(render_colors(limited$state, colors),
                       error = conditionMessage)
stopifnot(identical(bad_target, "The requested native R text is unavailable."),
          identical(grDevices::dev.cur(), c("null device" = 1L)))

hidden <- adapter$capture_ggplot(plot + ggplot2::guides(
  colour = ggplot2::guide_legend(title = expression(mu)),
  shape = ggplot2::guide_legend(title = NULL)
), c(432, 288))
stopifnot(identical(names(hidden$state$text_targets), c(head(names(labels), 5L), names(facets))))

secondary_plot <- plot +
  ggplot2::labs(subtitle = "Comparing drive configurations", caption = "Source: ggplot2 mpg") +
  ggplot2::theme(plot.subtitle = ggplot2::element_text(hjust = .5, colour = "#635479"),
                 plot.caption = ggplot2::element_text(hjust = 1, colour = "#426154"))
secondary <- adapter$capture_ggplot(secondary_plot, c(432, 288))
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
secondary_edits <- list("plot.subtitle" = "#B04964", "plot.caption" = "#2B7984")
edited <- render_colors(secondary$state, secondary_edits)
restored <- edited
anchors <- c("plot.subtitle" = .5, "plot.caption" = 1)
for (gid in names(secondary_edits)) {
  target <- secondary$state$text_targets[[gid]]
  stopifnot(identical(target$role, "text"))
  label <- paste0(">", target$label, "</text>")
  before <- grep(label, strsplit(secondary$svg, "\n")[[1L]], fixed = TRUE, value = TRUE)
  after <- grep(label, strsplit(edited, "\n")[[1L]], fixed = TRUE, value = TRUE)
  stopifnot(length(before) == 1L, grepl(target$color, before, fixed = TRUE),
            identical(after, sub(target$color, secondary_edits[[gid]], before, fixed = TRUE)))
  bbox <- target$bbox * c(432, 288, 432, 288)
  svg_x <- as.numeric(sub(".* x='([^']+)'.*", "\\1", after))
  stopifnot(abs(svg_x - bbox[[1L]] - bbox[[3L]] * anchors[[gid]]) < 1)
  restored <- sub(secondary_edits[[gid]], target$color, restored, fixed = TRUE)
}
stopifnot(identical(restored, secondary$svg),
          identical(render_colors(secondary$state), secondary$svg),
          count_at_capture == summary_calls, identical(rng_at_capture, .Random.seed))
unsupported_secondary <- adapter$capture_ggplot(
  secondary_plot + ggplot2::labs(subtitle = expression(alpha)) +
    ggplot2::theme(plot.caption = ggplot2::element_blank()), c(432, 288))
stopifnot(!any(names(secondary_edits) %in% names(unsupported_secondary$state$text_targets)))

ticks_plot <- ggplot2::ggplot(data.frame(x = 1:3, y = 1:3), ggplot2::aes(x, y)) +
  ggplot2::geom_point() +
  ggplot2::scale_x_continuous(breaks = 1:3, labels = c("Jan", "Feb", "Mar"),
    sec.axis = ggplot2::dup_axis(labels = c("First", "Second", "Third"))) +
  ggplot2::scale_y_continuous(breaks = 1:3, sec.axis = ggplot2::dup_axis()) +
  ggplot2::theme(axis.text.x.bottom = ggplot2::element_text(colour = "#314259", angle = 30, hjust = 1),
    axis.text.x.top = ggplot2::element_text(colour = "#674284"),
    axis.text.y.left = ggplot2::element_text(colour = "#735829"),
    axis.text.y.right = ggplot2::element_text(colour = "#245477"),
    axis.ticks = ggplot2::element_line(colour = "#050829"))
axis_capture <- function(plot) adapter$capture_ggplot(plot, c(432, 288))
tick_state <- axis_capture(ticks_plot)
tick_edits <- list("axis.x.bottom.ticks" = "#A64184", "axis.x.top.ticks" = "#A12529",
                   "axis.y.left.ticks" = "#834038", "axis.y.right.ticks" = "#254F29")
tick_text <- list(c("Jan", "Feb", "Mar"), c("First", "Second", "Third"), c("1", "2", "3"), c("1", "2", "3"))
stopifnot(all(names(tick_edits) %in% names(tick_state$state$text_targets)))
tick_svg <- render_colors(tick_state$state, tick_edits)
restored <- tick_svg
for (index in seq_along(tick_edits)) {
  gid <- names(tick_edits)[[index]]
  target <- tick_state$state$text_targets[[gid]]
  lines <- grep(paste0("fill: ", tick_edits[[gid]]), strsplit(tick_svg, "\n")[[1L]],
                 fixed = TRUE, value = TRUE)
  stopifnot(length(lines) == 3L, all(startsWith(lines, "<text ")),
            identical(sub("^.*>([^<]*)</text>$", "\\1", lines), tick_text[[index]]),
            identical(unlist(target$tick_labels), tick_text[[index]]))
  bbox <- target$bbox * c(432, 288, 432, 288)
  if (index == 1L) {
    xy <- sub("^.*translate\\(([^)]+)\\).*", "\\1", lines)
    anchors <- do.call(rbind, lapply(strsplit(xy, ","), as.numeric))
  } else {
    anchors <- cbind(as.numeric(sub(".* x='([^']+)'.*", "\\1", lines)),
                     as.numeric(sub(".* y='([^']+)'.*", "\\1", lines)))
  }
  stopifnot(all(anchors[, 1] >= bbox[1] - 1 & anchors[, 1] <= bbox[1] + bbox[3] + 1),
            all(anchors[, 2] >= bbox[2] - 1 & anchors[, 2] <= bbox[2] + bbox[4] + 1))
  restored <- gsub(tick_edits[[gid]], target$color, restored, fixed = TRUE)
}
stopifnot(identical(restored, tick_state$svg),
          identical(render_colors(tick_state$state), tick_state$svg))
single_axis <- render_colors(tick_state$state, tick_edits[1L])
stopifnot(identical(gsub(tick_edits[[1L]], "#314259", single_axis, fixed = TRUE), tick_state$svg))
for (guide in list(ggplot2::guide_axis(n.dodge = 2L), ggplot2::guide_axis(check.overlap = TRUE))) {
  irregular <- axis_capture(suppressWarnings(ticks_plot + ggplot2::guides(x = guide)))
  stopifnot(is.null(irregular$state$text_targets[["axis.x.bottom.ticks"]]))
}
formula_ticks <- axis_capture(suppressWarnings(ticks_plot +
  ggplot2::scale_x_continuous(breaks = 1:3, labels = expression(alpha, beta, gamma))))
stopifnot(is.null(formula_ticks$state$text_targets[["axis.x.bottom.ticks"]]))
stopifnot(!any(names(tick_edits) %in% names(baseline$state$text_targets)))

bar <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = cty)) +
  ggplot2::geom_point() + ggplot2::labs(colour = "City mileage") +
  ggplot2::scale_colour_continuous(breaks = 20) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom"))
bar_state <- adapter$capture_ggplot(bar, c(432, 288))$state
gid <- "legend.bottom.1.title"
stopifnot(identical(bar_state$text_targets[[gid]]$label, "City mileage"),
          !length(entries(bar_state)))
bar_edited <- render_colors(bar_state, setNames(list("#26838F"), gid))
bar_title <- grep(">City mileage</text>", strsplit(bar_edited, "\n")[[1L]],
                  value = TRUE, fixed = TRUE)
stopifnot(length(bar_title) == 1L, grepl("#26838F", bar_title, fixed = TRUE))

bar_data <- data.frame(x = 1:6, y = 6:1, signal = 0:5, reference = seq(0, 50, 10))
bar_points <- ggplot2::ggplot(bar_data, ggplot2::aes(x, y, colour = signal, fill = reference)) +
  ggplot2::geom_point(shape = 21)
bars <- bar_points +
  ggplot2::scale_colour_continuous(name = "Signal", breaks = c(1, 3, 5),
    labels = c("Low", "Middle", "High")) +
  ggplot2::scale_fill_continuous(name = "Reference", breaks = c(10, 30, 50),
    labels = c("Ten", "Thirty", "Fifty")) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom", angle = 25,
    theme = ggplot2::theme(legend.text = ggplot2::element_text(colour = "#614270"))),
    fill = ggplot2::guide_colourbar(position = "right", display = "rectangles",
    theme = ggplot2::theme(legend.text = ggplot2::element_text(colour = "#345E77"))))
captured_bars <- adapter$capture_ggplot(bars, c(432, 288))
bar_ids <- c("legend.bottom.1.colourbar.ticks", "legend.right.1.colourbar.ticks")
bar_colours <- c("#C32146", "#187958")
bar_labels <- list(c("Low", "Middle", "High"), c("Ten", "Thirty", "Fifty"))
for (index in seq_along(bar_ids)) {
  id <- bar_ids[[index]]
  target <- captured_bars$state$text_targets[[id]]
  stopifnot(identical(target$legend_title, c("Signal", "Reference")[[index]]),
            identical(unlist(target$tick_labels), bar_labels[[index]]),
            all(target$bbox[3:4] > 0))
  color <- bar_colours[[index]]
  svg <- render_colors(captured_bars$state, setNames(list(color), id))
  lines <- grep(paste0("fill: ", color), strsplit(svg, "\n")[[1L]], fixed = TRUE, value = TRUE)
  stopifnot(all(startsWith(lines, "<text ")),
            identical(sub("^.*>([^<]*)</text>$", "\\1", lines), bar_labels[[index]]),
            identical(gsub(color, target$color, svg, fixed = TRUE), captured_bars$svg))
}
stopifnot(identical(render_colors(captured_bars$state), captured_bars$svg))
bar_ticks <- function(plot) {
  captured <- adapter$capture_ggplot(plot, c(432, 288))
  grep("\\.colourbar\\.ticks$", names(captured$state$text_targets), value = TRUE)
}
stepped <- suppressWarnings(bars + ggplot2::scale_colour_steps(name = "Signal") +
  ggplot2::guides(colour = ggplot2::guide_coloursteps(position = "bottom")))
stopifnot(identical(bar_ticks(stepped), bar_ids[[2L]]))
duplicate <- bars +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom", title = "Shared"),
                 fill = ggplot2::guide_colourbar(position = "right", title = "Shared"))
stopifnot(!length(bar_ticks(duplicate)))
unlabelled <- bars + ggplot2::guides(
  colour = ggplot2::guide_colourbar(position = "bottom", title = NULL),
  fill = ggplot2::guide_colourbar(position = "right", theme = ggplot2::theme(
    legend.text = ggplot2::element_blank())))
stopifnot(!length(bar_ticks(unlabelled)))
bar_formula <- suppressWarnings(bars + ggplot2::scale_colour_continuous(name = "Signal",
  breaks = c(1, 3, 5), labels = expression(alpha, beta, gamma)))
stopifnot(identical(bar_ticks(bar_formula), bar_ids[[2L]]))

drive_plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point()
repeated_legend <- drive_plot +
  ggplot2::scale_colour_discrete(name = "Groups", labels = c("Repeated", "Repeated", "Other"))
labels <- entries(adapter$capture_ggplot(repeated_legend, c(432, 288))$state)
stopifnot(length(labels) == 1L, identical(labels[[1L]]$label, "Other"))
separate_guides <- drive_plot + ggplot2::geom_point(ggplot2::aes(shape = factor(cyl))) +
  ggplot2::scale_colour_discrete(name = "Drive", labels = c("Shared entry", "Front", "Rear")) +
  ggplot2::scale_shape_discrete(name = "Cylinders", labels = c("Shared entry", "Five", "Six", "Eight")) +
  ggplot2::guides(colour = ggplot2::guide_legend(order = 1), shape = ggplot2::guide_legend(order = 2))
separate <- adapter$capture_ggplot(separate_guides, c(432, 288))
shared <- Filter(function(target) identical(target$label, "Shared entry"), entries(separate$state))
stopifnot(length(shared) == 2L, identical(shared[[1L]]$legend_title, "Drive"),
          identical(shared[[2L]]$legend_title, "Cylinders"))
separate_edited <- render_colors(separate$state, setNames(list("#934896"), names(shared)[[2L]]))
shared_lines <- function(svg) grep(">Shared entry</text>", strsplit(svg, "\n")[[1L]],
                                   fixed = TRUE, value = TRUE)
stopifnot(identical(shared_lines(separate_edited)[[1L]], shared_lines(separate$svg)[[1L]]),
          grepl("#934896", shared_lines(separate_edited)[[2L]], fixed = TRUE))
wrap <- drive_plot + ggplot2::labs(colour = "Drive type") +
  ggplot2::guides(colour = ggplot2::guide_legend(ncol = 2, position = "bottom"))
wrapped <- adapter$capture_ggplot(wrap, c(432, 288))
bottom_entries <- entries(wrapped$state)
stopifnot(length(bottom_entries) == 3L, all(startsWith(names(bottom_entries), "legend.bottom.")))
bottom_gid <- names(bottom_entries)[[3L]]
bottom_label <- paste0(">", bottom_entries[[3L]]$label, "</text>")
wrapped_edited <- render_colors(wrapped$state, setNames(list("#6F2A9B"), bottom_gid))
wrapped_line <- grep(bottom_label, strsplit(wrapped_edited, "\n")[[1L]], fixed = TRUE, value = TRUE)
stopifnot(length(wrapped_line) == 1L, grepl("#6F2A9B", wrapped_line, fixed = TRUE))

for (side in c("left", "top", "inside")) {
  positioned <- drive_plot + ggplot2::theme(legend.position = side, legend.position.inside = c(.7, .8))
  captured <- adapter$capture_ggplot(positioned, c(432, 288))
  gid <- paste0("legend.", side)
  target <- captured$state$legend_targets[[gid]]
  stopifnot(!is.null(target), grepl(paste0("id='", gid, "'"), captured$svg, fixed = TRUE))
  edits <- setNames(list(list(loc_frac = c(.37, .68))), gid)
  manifest <- adapter$ggplot_manifest(captured$state, "guide-test", edits)
  moved <- Filter(function(element) element$gid == gid, manifest$elements)[[1L]]
  stopifnot(isTRUE(all.equal(unlist(moved$anchor), c(.37, .68))),
            identical(captured$svg, adapter$render_ggplot(captured$state)))
}
# A single outside box owns both guides that ggplot2 placed on that side.
group_edits <- list("legend.right" = list(loc_frac = c(.32, .71)))
two_guides <- adapter$ggplot_manifest(separate$state, "guide-test", group_edits)$elements
shared_titles <- Filter(function(element) identical(element$label, "Shared entry"), two_guides)
stopifnot(length(shared_titles) == 2L,
          all(vapply(shared_titles, function(element) element$drag_host == "legend.right", logical(1L))))

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
grid_edit <- render_colors(grid$state, list("facet.r.2.label" = "#BC4964"))
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
    adapter$export_ggplot_pdf(baseline$state, color_edits(colors), "unavailable.pdf"),
    error = conditionMessage
  ))
  utils::assignInNamespace("cairo_pdf", cairo_pdf, "grDevices")
  stopifnot(grepl("fixture missing Cairo", failed_pdf, fixed = TRUE),
            !file.exists("unavailable.pdf"), !file.exists("Rplots.pdf"),
            identical(grDevices::dev.cur(), c("null device" = 1L)))
}
"""

PRECISION_PROBE = r"""

if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)

values <- data.frame(x = c(0, 2, 4, 6), y = c(1, 4, 3, 5), colour = c(-12, -4, 4, 12))
summary_calls <- 0L
tracked_mean <- function(v) {
  summary_calls <<- summary_calls + 1L
  mean(v)
}
plot <- ggplot2::ggplot(values, ggplot2::aes(x, y)) +
  ggplot2::geom_line(colour = "#123456", alpha = .6) +
  ggplot2::geom_point(ggplot2::aes(colour = colour)) +
  ggplot2::stat_summary(fun = tracked_mean, geom = "point", shape = 4) +
  ggplot2::scale_colour_gradient(name = "Direction", breaks = c(-12, 0, 12)) +
  ggplot2::scale_x_continuous(breaks = c(0, 2, 4, 6))
frame <- c(432, 288)
ticks <- "axis.x.bottom.ticks"
bar <- "legend.right.1.colourbar.ticks"
curve <- "layer.1.line"
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
stopifnot(identical(state$tick_targets[[bar]]$values, c(-12, 0, 12)),
          identical(names(state$line_targets), curve))
pre_calls <- summary_calls

edits <- list()
edits[[ticks]] <- list(format = "%.2f", major_mode = "step", major_step = 1.5, fontsize = 18)
edits[[bar]] <- list(format = "%.1f")
edits[[curve]] <- list(linewidth = 2.4, linestyle = "--", color = "#538E25")
svg <- adapter$render_ggplot(state, edits)
stopifnot(length(svg) == 1L, grepl(">1.50</text>", svg, fixed = TRUE),
          !grepl(">2.00</text>", svg, fixed = TRUE),
          grepl(">0.0</text>", svg, fixed = TRUE),
          grepl(">12.0</text>", svg, fixed = TRUE))

# Inspect the actually assembled native grid, curve and palette. Changing
# captions can resize the layout, so compare within their native viewports.
probe <- function(table) {
  panel <- table$grobs[[which(table$layout$name == "panel")]]
  grill <- panel$children[[grep("^grill", names(panel$children))]]
  major <- grill$children[[grep("^panel.grid.major.x", names(grill$children))]]
  line <- panel$children[[state$line_targets[[curve]]$child]]
  points <- panel$children[[grep("^geom_point.points", names(panel$children))[[1L]]]]
  box <- table$grobs[[which(table$layout$name == "guide-box-right")]]
  guide <- box$grobs[[which(box$layout$name == "guides")]]
  bar <- guide$grobs[[which(guide$layout$name == "bar")]]
  label <- guide$grobs[[grep("^label", guide$layout$name)]]$children[[1L]]
  tick_source <- state$text_targets[[ticks]]
  tick_paint <- adapter$ggplot_text_target(table, tick_source$slots, "ticks",
                                          group_label = tick_source$label)$paint
  list(grid_x = unique(as.numeric(major$x)), line = line, points = points,
       raster = bar$raster, label_y = as.numeric(label$y), tick_size = tick_paint$fontsize)
}
before <- probe(state$table)
after <- adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  probe(adapter$ggplot_table_for(state, edits)$table)
})$value
view <- state$built$layout$panel_params[[1L]]$x
stopifnot(isTRUE(all.equal(after$grid_x, view$rescale(seq(0, 6, by = 1.5)))),
          length(before$grid_x) == 4L,
          after$tick_size == 18, before$tick_size != after$tick_size,
          identical(after$line$x, before$line$x), identical(after$line$y, before$line$y),
          identical(after$points, before$points),
          identical(after$raster, before$raster),
          identical(after$label_y, before$label_y),
          after$line$gp$col == "#538E2599", after$line$gp$lty == "dashed",
          after$line$gp$lwd * 72 / 96 == 2.4)
stopifnot(identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, edits), svg),
          identical(adapter$render_ggplot(state, list(
            "axis.x.bottom.ticks" = list(major_mode = "auto", major_step = 1.5))), captured$svg),
          summary_calls == pre_calls)

for (invalid in list(list(major_step = 0), list(major_step = 1e-300),
                     list(major_mode = "unknown"), list(major_values = c(1, Inf)),
                     list(major_values = list(1, "2")), list(format = "%n"),
                     list(fontsize = 0))) {
  bad <- stats::setNames(list(invalid), ticks)
  stopifnot(inherits(try(adapter$render_ggplot(state, bad), silent = TRUE), "try-error"))
}
stopifnot(inherits(try(adapter$render_ggplot(state, stats::setNames(
  list(list(linewidth = -1)), curve)), silent = TRUE), "try-error"))
custom <- adapter$capture_ggplot(plot +
  ggplot2::scale_x_continuous(breaks = c(0, 2, 4, 6), labels = c("Zero", "Two", "Four", "Six")),
  frame)
stopifnot(is.null(custom$state$tick_targets[[ticks]]),
          !is.null(custom$state$text_targets[[ticks]]))
secondary <- adapter$capture_ggplot(plot +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis()), frame)
stopifnot(is.null(secondary$state$tick_targets[[ticks]]$spacing))
"""

COLOR_SCALE_PROBE = r"""

if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
palettes <- jsonlite::fromJSON(
  file.path(dirname(dirname(adapter_file)), "color_palettes.json"), simplifyVector = FALSE)

data <- data.frame(x = 1:4, y = c(2, 4, 1, 3), z = c(0, 2, 4, 6), tile = c(8, 2, 5, 4))
computations <- 0L
tracked <- function(x) {
  computations <<- computations + 1L
  mean(x)
}
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_tile(ggplot2::aes(fill = tile), width = .12, height = .12) +
  ggplot2::geom_point(ggplot2::aes(colour = z)) +
  ggplot2::geom_point(ggplot2::aes(y = y + .1, colour = z), shape = 15) +
  ggplot2::stat_summary(fun = tracked, geom = "point", shape = 4) +
  ggplot2::scale_colour_gradient(name = "Exposure", guide = ggplot2::guide_colourbar(position = "right")) +
  ggplot2::scale_fill_gradient(name = "Independent", guide = ggplot2::guide_colourbar(position = "left"))
captured <- adapter$capture_ggplot(plot, c(432, 288), palettes)
state <- captured$state
gid <- "legend.right.1.colourbar"
other <- "legend.left.1.colourbar"
target <- state$colour_scales[[gid]]
stopifnot(identical(target$limits, c(0, 6)), identical(names(target$members), c("2", "3")),
          identical(names(state$colour_scales[[other]]$members), "1"))
calls <- computations
scale_edit <- list(vmin = 1, vmax = 5, cmap = "viridis")
edits <- stats::setNames(list(scale_edit, list(format = "%.1f")), c(gid, paste0(gid, ".ticks")))
result <- adapter$ggplot_remap_colour_scales(state, edits[gid], state$built$plot$guides$params)
upper <- "#FDE725"
lower <- "#440154"
expected <- tolower(c(lower, "#3B528B", "#5EC962", upper))
for (idx in c(2L, 3L)) {
  stopifnot(identical(result$data[[idx]]$colour, expected),
            identical(result$data[[idx]][c("x", "y")], state$layers[[idx]][c("x", "y")]))
}
stopifnot(identical(result$data[[1L]], state$layers[[1L]]),
          identical(result$data[[4L]], state$layers[[4L]]))
decor <- result$guides[[target$index]]$decor
stopifnot(identical(decor$value[c(1L, nrow(decor))], c(1, 5)),
          identical(toupper(decor$colour[c(1L, nrow(decor))]), c(lower, upper)))
svg <- adapter$render_ggplot(state, edits)
stopifnot(grepl(paste0("fill: ", lower), svg, fixed = TRUE),
          grepl(paste0("fill: ", upper), svg, fixed = TRUE),
          grepl(">1.0</text>", svg, fixed = TRUE), grepl(">5.0</text>", svg, fixed = TRUE))
replay <- adapter$ggplot_manifest(state, "test", edits)$elements
element <- Filter(function(el) el$gid == gid, replay)[[1L]]
stopifnot(identical(element$color_scale$limits, as.list(c(1, 5))),
          identical(element$color_scale$cmap, "viridis"),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, edits), svg),
          computations == calls)

# The same saved document can style the series and its colour scale together.
point_gid <- "layer.2.scatter"
stopifnot(identical(names(state$point_targets[[point_gid]]$values),
                    c("size", "marker", "alpha")))
point_edit <- list(size = 64, marker = "s", alpha = .5)
mixed <- c(edits, stats::setNames(list(point_edit), point_gid))
scene <- adapter$with_svg_frame(state$frame_pt, function(capture) {
  adapter$ggplot_table_for(state, mixed)
})$value
target <- state$point_targets[[point_gid]]
baseline_points <- adapter$ggplot_point_grob(state$table$grobs[[target$slot]], target)
actual_points <- adapter$ggplot_point_grob(scene$table$grobs[[target$slot]], target)
sibling <- state$point_targets[["layer.3.scatter"]]
sibling_points <- adapter$ggplot_point_grob(scene$table$grobs[[sibling$slot]], sibling)
stopifnot(identical(actual_points$x, baseline_points$x),
          identical(actual_points$y, baseline_points$y),
          identical(actual_points$pch, 15L),
          all(abs(actual_points$gp$fontsize - actual_points$gp$lwd - 8) < 1e-10),
          identical(tolower(actual_points$gp$col), paste0(expected, "80")),
          identical(tolower(sibling_points$gp$col), expected))
point_elements <- adapter$ggplot_manifest(state, "test", mixed)$elements
point <- Filter(function(el) el$gid == point_gid, point_elements)[[1L]]
stopifnot(identical(point$point_series$current, point_edit),
          !identical(point$bbox, target$bbox),
          identical(adapter$render_ggplot(state), captured$svg),
          computations == calls)
point_svg <- adapter$render_ggplot(state, mixed)
stopifnot(grepl("fill: #440154; fill-opacity: 0.50", point_svg, fixed = TRUE),
          !identical(point_svg, svg))
bad_point <- function(patch) stats::setNames(list(patch), point_gid)
for (bad in list(list(size = 0), list(marker = "unknown"), list(alpha = -1))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, bad_point(bad)), silent = TRUE), "try-error"))
}
mapped <- adapter$capture_ggplot(ggplot2::ggplot(data, ggplot2::aes(x, y, size = z)) +
  ggplot2::geom_point(show.legend = FALSE), c(432, 288), palettes)$state
mapped_points <- mapped$point_targets[["layer.1.scatter"]]
stopifnot(is.null(mapped_points$values$size), !is.null(mapped_points$values$marker))

for (bad in list(list(vmin = 6, vmax = 6), list(vmin = 10), list(vmax = Inf),
                 list(cmap = "invented"), list(orientation = "diagonal"), list(extend = "far"))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, stats::setNames(list(bad), gid)),
                        silent = TRUE), "try-error"))
}
stopifnot(identical(adapter$render_ggplot(state), captured$svg))
unstable <- plot + ggplot2::geom_point(ggplot2::aes(colour = z, alpha = ggplot2::after_scale(alpha)))
unstable_state <- adapter$capture_ggplot(unstable, c(432, 288), palettes)$state
stopifnot(is.null(unstable_state$colour_scales[[gid]]$scale),
          identical(unstable_state$colour_scales[[gid]]$orientation, "vertical"),
          length(adapter$render_ggplot(unstable_state,
            stats::setNames(list(list(orientation = "horizontal")), gid))) == 1L)

paired_plot <- ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z, fill = z)) +
  ggplot2::geom_point(shape = 21) +
  ggplot2::scale_colour_gradient(aesthetics = c("colour", "fill")) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(title = "Edge", position = "right"),
                 fill = ggplot2::guide_colourbar(title = "Interior", position = "left"))
paired <- adapter$capture_ggplot(paired_plot, c(432, 288), palettes)$state
mapping <- Filter(function(x) !is.null(x$scale), paired$colour_scales)
stopifnot(length(paired$colour_scales) == 2L, length(mapping) == 1L, length(mapping[[1L]]$guides) == 2L)
independent <- setdiff(names(paired$colour_scales), names(mapping))
paired_edits <- c(stats::setNames(list(scale_edit), names(mapping)),
                  stats::setNames(list(list(orientation = "horizontal", extend = "min")), independent))
paired_result <- adapter$ggplot_remap_colour_scales(paired, paired_edits, paired$built$plot$guides$params)
stopifnot(identical(paired_result$data[[1L]]$colour, expected),
          identical(paired_result$data[[1L]]$fill, expected),
          all(vapply(paired_result$guides, function(guide) {
            identical(toupper(guide$decor$colour[c(1L, nrow(guide$decor))]), c(lower, upper))
          }, logical(1L))))
stopifnot(identical(paired_result$extensions[[paired$colour_scales[[independent]]$index]], "min"),
          identical(paired_result$guides[[paired$colour_scales[[independent]]$index]]$direction, "horizontal"))
stopifnot(length(adapter$render_ggplot(paired, paired_edits)) == 1L)

scene_for <- function(state, overrides) adapter$with_svg_frame(state$frame_pt, function(capture) {
  adapter$ggplot_table_for(state, overrides)
})$value
guide_for <- function(state, table, gid) {
  slots <- state$colour_scales[[gid]]$slots
  table$grobs[[slots[[1L]]]]$grobs[[slots[[2L]]]]
}
element_for <- function(state, overrides, id = gid) {
  Filter(function(el) el$gid == id, adapter$ggplot_manifest(state, "colourbar", overrides)$elements)[[1L]]
}
layout_only <- stats::setNames(list(list(orientation = "horizontal", extend = "both")), gid)
before_layout <- computations
layout_data <- adapter$ggplot_remap_colour_scales(state, layout_only, state$built$plot$guides$params)
stopifnot(identical(layout_data$data, state$layers), !length(layout_data$changed))
combined <- edits
combined[[gid]] <- c(combined[[gid]], layout_only[[gid]])
base_case <- combined
base_case[[gid]]$extend <- "neither"
base <- element_for(state, base_case)
current <- element_for(state, combined)
stopifnot(identical(current$color_scale$context, state$colour_scales[[gid]]$context),
          identical(current$color_scale$orientation, "horizontal"),
          identical(current$color_scale$extend, "both"),
          identical(current$color_scale$layout_original, list(orientation = "vertical", extend = "neither")),
          isTRUE(all.equal(current$bbox[[3L]], base$bbox[[3L]] * 1.1)),
          isTRUE(all.equal(current$bbox[[4L]], base$bbox[[4L]])),
          current$bbox[[3L]] * state$frame_pt[[1L]] > current$bbox[[4L]] * state$frame_pt[[2L]])
combined_svg <- adapter$render_ggplot(state, combined)
stopifnot(identical(adapter$render_ggplot(state, edits), svg), computations == before_layout)
moved_layout <- c(combined, list(legend.right = list(loc_frac = c(.45, .8))))
moved <- element_for(state, moved_layout)
stopifnot(!identical(moved$bbox[1:2], current$bbox[1:2]),
          isTRUE(all.equal(moved$bbox[3:4], current$bbox[3:4])))

faceted <- plot + ggplot2::facet_wrap(ggplot2::vars(x)) +
  ggplot2::scale_colour_gradient(name = NULL, transform = "log1p",
    guide = ggplot2::guide_colourbar(position = "right", direction = "horizontal"))
faceted_state <- adapter$capture_ggplot(faceted, c(480, 300), palettes)$state
faceted_edit <- stats::setNames(list(list(orientation = "vertical", extend = "max")), gid)
faceted_bar <- element_for(faceted_state, faceted_edit)
stopifnot(is.null(faceted_bar$color_scale$limits),
          identical(faceted_bar$color_scale$orientation, "vertical"),
          identical(faceted_bar$color_scale$layout_original$orientation, "horizontal"))
descending <- plot + ggplot2::scale_colour_gradient(
  name = "Exposure", transform = "reverse",
  guide = ggplot2::guide_colourbar(position = "right", display = "rectangles"))
desc_state <- adapter$capture_ggplot(descending, c(432, 288), palettes)$state
desc_edit <- stats::setNames(list(list(orientation = "horizontal", extend = "min")), gid)
desc_guide <- guide_for(desc_state, scene_for(desc_state, desc_edit)$table, gid)
desc_cells <- desc_guide$layout$name
desc_tip <- which(desc_cells == paste0(adapter$ggplot_colourbar_tip_prefix, "min"))
stopifnot(element_for(desc_state, desc_edit)$color_scale$reversed,
          desc_guide$layout$l[[desc_tip]] == desc_guide$layout$r[desc_cells == "bar"] + 1L)
orientation_cases <- expand.grid(direction = c("horizontal", "vertical"),
                                 display = c("rectangles", "raster", "gradient"))
for (j in seq_len(nrow(orientation_cases))) {
  direction <- as.character(orientation_cases$direction[[j]])
  display <- as.character(orientation_cases$display[[j]])
  reversed_plot <- plot + ggplot2::guides(colour = ggplot2::guide_colourbar(
    position = "right", reverse = TRUE, display = display))
  reversed <- adapter$capture_ggplot(reversed_plot, c(432, 288), palettes)$state
  for (mode in c("min", "max", "both", "neither")) {
    extra <- stats::setNames(list(list(orientation = direction, extend = mode)), gid)
    scene <- scene_for(reversed, extra)
    guide <- guide_for(reversed, scene$table, gid)
    meta <- attr(guide, "tavotto_colourbar", exact = TRUE)
    stopifnot(identical(meta$orientation, direction))
    cells <- guide$layout$name
    bar <- guide$layout[cells == "bar", ]
    tips <- guide$layout[startsWith(cells, adapter$ggplot_colourbar_tip_prefix), ]
    end <- c(min = 9L, max = 1L)
    requested <- intersect(names(end), switch(mode, both = c("min", "max"), mode))
    stopifnot(identical(tips$name, paste0(adapter$ggplot_colourbar_tip_prefix, requested, recycle0 = TRUE)))
    for (side in requested) {
      slot <- which(cells == paste0(adapter$ggplot_colourbar_tip_prefix, side))
      tip <- guide$layout[slot, ]
      stopifnot(identical(guide$grobs[[slot]]$gp$fill, reversed$colour_scales[[gid]]$stops[[end[[side]]]]))
      if (direction == "horizontal") {
        stopifnot(if (side == "min") tip$l == bar$r + 1L else tip$r == bar$l - 1L)
      } else {
        stopifnot(if (side == "min") tip$b == bar$t - 1L else tip$t == bar$b + 1L)
      }
    }
  }
}
after_captures <- computations
stopifnot(identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, combined), combined_svg), computations == after_captures)
"""


BAR_PROBE = r"""

if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
frame <- c(216, 144)
data <- data.frame(cat = c("One", "Three", "Two", "Three"), height = c(4, -1, 2, -3),
                   group = c("control", "control", "control", "other"))
runs <- 0L
observed <- function(data) { runs <<- runs + 1L; data }
plot <- ggplot2::ggplot(data, ggplot2::aes(cat)) +
  ggplot2::geom_col(ggplot2::aes(y = height, group = group), data = observed,
                    fill = "#226688", colour = "#101112", linewidth = .6) +
  ggplot2::geom_bar(fill = "#77889A", width = .3)
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
first <- "layer.1.bars"
second <- "layer.2.bars"
stopifnot(identical(names(state$bar_targets), c(first, second)), runs == 1L)
stopifnot(identical(sort(state$layers[[2L]]$count), c(1, 1, 2)))
element <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
original <- adapter$ggplot_manifest(state, "bars")
baseline <- element(original, first)
stopifnot(baseline$role == "bar_series", length(baseline$geometry$paths) == 4L,
          length(element(original, second)$geometry$paths) == 3L)
patch <- setNames(list(list(facecolor = "#E25832", edgecolor = "#663399", linewidth = 2.2,
                            alpha = .75)), first)
scene <- adapter$ggplot_table_for(state, patch)
target <- state$bar_targets[[first]]
before <- adapter$ggplot_bar_rect(state$table$grobs[[target$slot]], target)
after <- adapter$ggplot_bar_rect(scene$table$grobs[[target$slot]], target)
stopifnot(identical(before[c("x", "y", "width", "height")], after[c("x", "y", "width", "height")]),
          all(abs(after$gp$lwd * adapter$ggplot_lwd_pt - 2.2) < 1e-9),
          identical(toupper(after$gp$fill), "#E25832BF"),
          identical(toupper(after$gp$col), "#663399BF"))
edited_svg <- adapter$render_ggplot(state, patch)
stopifnot(grepl("fill: #E25832", edited_svg, fixed = TRUE),
          grepl("stroke: #663399", edited_svg, fixed = TRUE),
          grepl("stroke-width: 2.20", edited_svg, fixed = TRUE),
          grepl("fill-opacity: 0.75", edited_svg, fixed = TRUE))
edited <- adapter$ggplot_manifest(state, "bars", patch)
stopifnot(identical(element(edited, first)$geometry$paths, baseline$geometry$paths),
          identical(element(edited, second), element(original, second)),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, patch), edited_svg), runs == 1L)

thinner <- stats::setNames(list(list(bar_width = .4)), first)
thin_scene <- adapter$ggplot_table_for(state, thinner)
thin <- adapter$ggplot_bar_rect(thin_scene$table$grobs[[target$slot]], target)
centres <- function(rect) as.numeric(rect$x) + as.numeric(rect$width) / 2
stopifnot(isTRUE(all.equal(centres(thin), centres(before))),
          isTRUE(all.equal(as.numeric(thin$width) / as.numeric(before$width), rep(.4 / .9, 4))),
          identical(thin[c("y", "height")], before[c("y", "height")]),
          identical(element(adapter$ggplot_manifest(state, "bars", thinner), second),
                    element(original, second)), runs == 1L,
          identical(adapter$render_ggplot(state), captured$svg))
bad <- list(facecolor = c(rgb(1, 0, 0), rgb(0, 1, 0)), edgecolor = "yellow",
            linewidth = 5.1, alpha = -.1, bar_width = 0)
for (prop in names(bad)) {
  stopifnot(inherits(try(adapter$render_ggplot(state, setNames(list(bad[prop]), first)),
                        silent = TRUE), "try-error"))
}
boundaries <- setNames(list(list(edgecolor = "none", linewidth = 0, alpha = 0)), first)
invisible_bar <- element(adapter$ggplot_manifest(state, "bars", boundaries), first)$geometry
stopifnot(length(adapter$render_ggplot(state, boundaries)) == 1L,
          !invisible_bar$fill, is.null(invisible_bar$fill_alpha))
horizontal <- ggplot2::ggplot(data, ggplot2::aes(height, cat)) +
  ggplot2::geom_col(fill = "#226688", linetype = 0)
h <- adapter$capture_ggplot(horizontal, frame)$state
h_target <- h$bar_targets[[first]]
h_original <- adapter$ggplot_bar_rect(h$table$grobs[[h_target$slot]], h_target)
h_edit <- setNames(list(list(bar_thickness = .4)), first)
h_scene <- adapter$ggplot_table_for(h, h_edit)
h_rect <- adapter$ggplot_bar_rect(h_scene$table$grobs[[h_target$slot]], h_target)
ycentre <- function(rect) as.numeric(rect$y) - as.numeric(rect$height) / 2
stopifnot(identical(names(h_target$values), c("facecolor", "bar_thickness", "alpha")),
          isTRUE(all.equal(ycentre(h_rect), ycentre(h_original))),
          isTRUE(all.equal(as.numeric(h_rect$height) / as.numeric(h_original$height), rep(.4 / .9, 4))),
          identical(h_rect[c("x", "width", "gp")], h_original[c("x", "width", "gp")]),
          grepl("fill-opacity: 0.75", adapter$render_ggplot(h, setNames(list(list(alpha = .75)), first)),
                fixed = TRUE),
          identical(adapter$render_ggplot(h), adapter$capture_ggplot(horizontal, frame)$svg))
mapped <- ggplot2::ggplot(data, ggplot2::aes(cat, height, fill = group)) + ggplot2::geom_col()
for (mapped_plot in list(mapped, mapped + ggplot2::guides(fill = "none"))) {
  mapped_capture <- adapter$capture_ggplot(mapped_plot, frame)
  mapped_state <- mapped_capture$state
  mapped_target <- mapped_state$bar_targets[[first]]
  mapped_before <- adapter$ggplot_bar_rect(mapped_state$table$grobs[[mapped_target$slot]], mapped_target)
  mapped_scene <- adapter$ggplot_table_for(mapped_state, thinner)
  mapped_after <- adapter$ggplot_bar_rect(mapped_scene$table$grobs[[mapped_target$slot]], mapped_target)
  mapped_legend <- grep("^guide-box", mapped_state$table$layout$name)
  stopifnot(identical(names(mapped_target$values), "bar_width"), length(unique(mapped_before$gp$fill)) == 2L,
            identical(mapped_after$gp, mapped_before$gp),
            identical(mapped_scene$table$grobs[mapped_legend], mapped_state$table$grobs[mapped_legend]),
            isTRUE(all.equal(centres(mapped_after), centres(mapped_before))),
            isTRUE(all.equal(as.numeric(mapped_after$width) / as.numeric(mapped_before$width), rep(.4 / .9, 4))),
            identical(mapped_after[c("y", "height")], mapped_before[c("y", "height")]),
            identical(adapter$render_ggplot(mapped_state), mapped_capture$svg))
}
faceted_data <- data.frame(
  x = c(2, 5, 10, 16), height = c(2, 3, 5, 6), group = rep(c("Alpha", "Beta"), 2L),
  experiment = factor(rep(c("Reference", "Treatment"), each = 2L),
                      levels = c("Reference", "Treatment", "Empty")))
faceted_plot <- ggplot2::ggplot(faceted_data, ggplot2::aes(x, height, fill = group)) +
  ggplot2::geom_col(width = .8)
for (facet in list(
  ggplot2::facet_wrap(ggplot2::vars(experiment), ncol = 1L, scales = "free_x", drop = FALSE),
  ggplot2::facet_grid(cols = ggplot2::vars(experiment), scales = "free_x", drop = FALSE))) {
  captured_facet <- adapter$capture_ggplot(faceted_plot + facet, frame)
  facets <- captured_facet$state
  keys <- paste0("layer.1.panel.", 1:2, ".bars")
  stopifnot(identical(names(facets$bar_targets), keys))
  reference <- facets$bar_targets[[keys[[1L]]]]
  treatment <- facets$bar_targets[[keys[[2L]]]]
  f_edit <- stats::setNames(list(list(bar_width = .55)), keys[[2L]])
  f_scene <- adapter$ggplot_table_for(facets, f_edit)
  untreated <- adapter$ggplot_bar_rect(facets$table$grobs[[treatment$slot]], treatment)
  treated <- adapter$ggplot_bar_rect(f_scene$table$grobs[[treatment$slot]], treatment)
  f_manifest <- adapter$ggplot_manifest(facets, "facets", f_edit)
  stopifnot(identical(treated$gp, untreated$gp), length(unique(treated$gp$fill)) == 2L,
            identical(f_scene$table$grobs[[reference$slot]], facets$table$grobs[[reference$slot]]),
            isTRUE(all.equal(centres(treated), centres(untreated))),
            isTRUE(all.equal(as.numeric(treated$width) / as.numeric(untreated$width), rep(.55 / .8, 2))),
            identical(treated[c("y", "height")], untreated[c("y", "height")]),
            endsWith(element(f_manifest, keys[[2L]])$label, "Treatment"),
            identical(adapter$render_ggplot(facets), captured_facet$svg))
}
make_bars <- function(n) {
  ggplot2::ggplot(data.frame(x = seq_len(n), y = rep(1, n)), ggplot2::aes(x, y)) + ggplot2::geom_col()
}
many <- make_bars(501L)
large <- adapter$capture_ggplot(many, frame)$state
stopifnot(is.null(large$bar_geometry[[first]]$geometry), length(large$bar_geometry[[first]]$bbox) == 4L)

for (bars in list(many, make_bars(1L))) {
  plain <- adapter$capture_ggplot(bars, frame)$state
  authored <- adapter$capture_ggplot(bars + ggplot2::coord_cartesian(reverse = "xy"), frame)$state
  stopifnot(first %in% names(authored$bar_targets))
  plain_bbox <- unlist(plain$bar_geometry[[first]]$bbox)
  authored_bbox <- unlist(authored$bar_geometry[[first]]$bbox)
  plain_panel <- unlist(element(adapter$ggplot_manifest(plain, "plain"), "axes_0")$bbox)
  authored_panel <- unlist(element(adapter$ggplot_manifest(authored, "reverse"), "axes_0")$bbox)
  relative <- function(box, panel) c((box[1:2] - panel[1:2]) / panel[3:4], box[3:4] / panel[3:4])
  normal <- relative(plain_bbox, plain_panel)
  reflected <- relative(authored_bbox, authored_panel)
  stopifnot(isTRUE(all.equal(reflected, c(1 - normal[1:2] - normal[3:4], normal[3:4]))))
  changes <- list(axes_0 = list(invert_x = TRUE, invert_y = TRUE))
  changes[[first]] <- patch[[first]]["facecolor"]
  moved <- adapter$ggplot_manifest(plain, "plain", changes)
  moved_bbox <- unlist(element(moved, first)$bbox)
  moved_panel <- unlist(element(moved, "axes_0")$bbox)
  stopifnot(isTRUE(all.equal(relative(moved_bbox, moved_panel), reflected)))
}

dodged <- data.frame(x = rep(c(1, 4, 7), each = 2), y = c(3, 4, 2, 1, 5, 6),
                     group = rep(c("a", "b"), 3))
dodged_plot <- ggplot2::ggplot(dodged, ggplot2::aes(x, y, group = group)) +
  ggplot2::geom_col(width = .8, position = "dodge")
dodged_state <- adapter$capture_ggplot(dodged_plot, frame)$state
dodged_grob <- function(patches) adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  scene <- adapter$ggplot_table_for(dodged_state, patches)
  target <- scene$bar_targets[[first]]
  adapter$ggplot_bar_rect(scene$table$grobs[[target$slot]], target)
})$value
xy <- dodged_grob(list())
zoomin <- list(axes_0 = list(xlim = c(.5, 7.5), xscale = "log", invert_x = TRUE))
zoomed_baseline <- dodged_grob(zoomin)
zoomin[[first]] <- list(bar_width = .25)
log_grob <- dodged_grob(zoomin)
raw_centres <- c(.8, 1.2, 3.8, 4.2, 6.8, 7.2)
left <- 1 - log10((raw_centres - .125) / .5) / log10(7.5 / .5)
right <- 1 - log10((raw_centres + .125) / .5) / log10(7.5 / .5)
stopifnot(isTRUE(all.equal(as.numeric(xy$width),
                          rep(.4 / diff(dodged_state$built$layout$panel_params[[1L]]$x.range), 6))),
          isTRUE(all.equal(as.numeric(log_grob$x), left)),
          isTRUE(all.equal(as.numeric(log_grob$x + log_grob$width), right)),
          identical(log_grob[c("y", "height")], zoomed_baseline[c("y", "height")]),
          identical(dodged_grob(list()), xy))
zoomin[[first]]$bar_width <- 2
stopifnot(inherits(try(dodged_grob(zoomin), silent = TRUE), "try-error"))

horizontal_dodged <- ggplot2::ggplot(dodged, ggplot2::aes(y, x, group = group)) +
  ggplot2::geom_col(width = .8, position = "dodge", orientation = "y")
h_dodge <- adapter$capture_ggplot(horizontal_dodged, frame)$state
h_dodge_target <- h_dodge$bar_targets[[first]]
h_zoom <- list(axes_0 = list(ylim = c(.5, 7.5), yscale = "log", invert_y = TRUE))
h_zoom_thick <- h_zoom
h_zoom_thick[[first]] <- list(bar_thickness = .25)
h_dodge_grob <- function(patches) adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  scene <- adapter$ggplot_table_for(h_dodge, patches)
  adapter$ggplot_bar_rect(scene$table$grobs[[h_dodge_target$slot]], scene$bar_targets[[first]])
})$value
h_before <- h_dodge_grob(h_zoom)
h_after <- h_dodge_grob(h_zoom_thick)
stopifnot(isTRUE(all.equal(as.numeric(h_after$y - h_after$height), left)),
          isTRUE(all.equal(as.numeric(h_after$y), right)),
          identical(h_before[c("x", "width", "gp")], h_after[c("x", "width", "gp")]))
"""

ERRORBAR_PROBE = r"""

if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
data <- data.frame(x = c(1, 2), y = c(3, 4), lower = c(2, 2.5), upper = c(5, 6))
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_errorbar(ggplot2::aes(ymin = lower, ymax = upper), data = observed,
                         linewidth = .5, colour = "#123456", width = .3) +
  ggplot2::geom_point() + ggplot2::labs(y = "Diffusion (mm)")
frame <- c(216, 144)
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
gid <- "layer.1.errorbar"
element <- function(edits = list()) {
  Filter(function(e) e$gid == gid, adapter$ggplot_manifest(state, "interval", edits)$elements)[[1L]]
}
original <- element()
stopifnot(original$role == "errorbar", length(original$geometry$paths) == 6L,
          all(lengths(lapply(original$geometry$paths, `[[`, "points")) == 2L))
style <- list(color = "#8A2530", linewidth = 2.1, alpha = .6)
patch <- setNames(list(style), gid)
svg <- adapter$render_ggplot(state, patch)
stopifnot(identical(element(patch)$geometry$paths, original$geometry$paths),
          identical(element(patch)$errorbar_series$current[names(style)], style),
          any(grepl("stroke: #8A2530", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE) &
              grepl("stroke-width: 2.10", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE) &
              grepl("stroke-opacity: 0.60", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE)),
          identical(adapter$render_ggplot(state), captured$svg), calls == 1L)
endpoint_style <- c(style, list(capsize = 9, cap_thickness = 3.25))
capped <- stats::setNames(list(endpoint_style), gid)
resized <- element(capped)
paths <- resized$geometry$paths
physical_span <- function(path, axis) {
  abs(diff(vapply(path$points, `[[`, numeric(1L), axis))) * frame[[axis]]
}
stopifnot(identical(resized$errorbar_series$current, endpoint_style),
          identical(paths[c(2L, 5L)], original$geometry$paths[c(2L, 5L)]),
          all(abs(vapply(paths[c(1L, 3L, 4L, 6L)], physical_span, numeric(1L), 1L) - 9) < 1e-10))
capped_rows <- strsplit(adapter$render_ggplot(state, capped), "\n", fixed = TRUE)[[1L]]
colored <- capped_rows[startsWith(capped_rows, "<polyline ") &
                         grepl("stroke: #8A2530", capped_rows, fixed = TRUE)]
stopifnot(sum(grepl("stroke-width: 3.25", colored, fixed = TRUE)) == 4L,
          sum(grepl("stroke-width: 2.10", colored, fixed = TRUE)) == 2L)
uncapped <- stats::setNames(list(list(capsize = 0)), gid)
stopifnot(identical(element(uncapped)$geometry$paths, original$geometry$paths[c(2L, 5L)]))
horizontal <- ggplot2::ggplot(data, ggplot2::aes(y, x)) +
  ggplot2::geom_errorbar(ggplot2::aes(xmin = lower, xmax = upper), width = .3)
horizontal_state <- adapter$capture_ggplot(horizontal, frame)$state
horizontal_frame <- adapter$ggplot_manifest(horizontal_state, "horizontal", capped)
horizontal_paths <- Filter(function(e) e$gid == gid, horizontal_frame$elements)[[1L]]$geometry$paths
stopifnot(all(abs(vapply(horizontal_paths[c(1L, 3L, 4L, 6L)], physical_span,
                         numeric(1L), 2L) - 9) < 1e-10))

facet_data <- rbind(transform(data, experiment = "Reference"),
                    transform(data, x = x * 5, y = y + 1, lower = lower + 1,
                              upper = upper + 1, experiment = "Trial"))
facet_plot <- ggplot2::ggplot(facet_data, ggplot2::aes(x, y)) +
  ggplot2::geom_errorbar(ggplot2::aes(ymin = lower, ymax = upper), width = .3) +
  ggplot2::geom_line(colour = "#301060") +
  ggplot2::facet_wrap(ggplot2::vars(experiment), scales = "free_x")
facet_capture <- adapter$capture_ggplot(facet_plot, c(300, 180))
facet_state <- facet_capture$state
trial <- "layer.1.panel.2.errorbar"
reference <- "layer.1.panel.1.errorbar"
curve <- "layer.2.panel.2.line"
left_curve <- "layer.2.panel.1.line"
stopifnot(identical(names(facet_state$line_targets), c(reference, trial, left_curve, curve)))
facet_edits <- stats::setNames(list(list(capsize = 7, cap_thickness = 3), list(linewidth = 3)),
                               c(trial, curve))
by_gid <- function(manifest, key) Filter(function(e) e$gid == key, manifest$elements)[[1L]]
f_original <- adapter$ggplot_manifest(facet_state, "intervals")
f_edited <- adapter$ggplot_manifest(facet_state, "intervals", facet_edits)
original_interval <- by_gid(f_original, trial)
edited_interval <- by_gid(f_edited, trial)
stopifnot(endsWith(edited_interval$label, "Trial"),
          identical(edited_interval$geometry$paths[c(2L, 5L)], original_interval$geometry$paths[c(2L, 5L)]),
          identical(edited_interval$errorbar_series$current$capsize, 7),
          identical(edited_interval$errorbar_series$current$cap_thickness, 3),
          identical(by_gid(f_original, reference), by_gid(f_edited, reference)),
          identical(by_gid(f_original, left_curve), by_gid(f_edited, left_curve)),
          isTRUE(all.equal(by_gid(f_edited, curve)$geometry$stroke_pt, 3)),
          identical(adapter$render_ggplot(facet_state), facet_capture$svg))

mapped <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_errorbar(ggplot2::aes(ymin = lower, ymax = upper, colour = x, alpha = y),
                         width = .3, show.legend = FALSE) +
  ggplot2::scale_alpha_continuous(range = c(.4, .8))
mapped_capture <- adapter$capture_ggplot(mapped, frame)
mapped_state <- mapped_capture$state
mapped_target <- mapped_state$line_targets[[gid]]
mapped_size <- stats::setNames(list(endpoint_style[c("linewidth", "capsize", "cap_thickness")]), gid)
mapped_scene <- adapter$ggplot_table_for(mapped_state, mapped_size)
mapped_colour <- mapped_state$table$grobs[[mapped_target$slot]]$children[[mapped_target$child]]$gp$col
mapped_edited <- mapped_scene$table$grobs[[mapped_target$slot]]$children[[mapped_target$child]]
mapped_element <- by_gid(adapter$ggplot_manifest(mapped_state, "mapped", mapped_size), gid)
stopifnot(length(unique(mapped_colour)) == 2L,
          identical(mapped_edited$gp$col, rep(mapped_colour, each = 3L)),
          identical(mapped_element$errorbar_series$current, mapped_size[[gid]]),
          identical(adapter$render_ggplot(mapped_state), mapped_capture$svg),
          inherits(try(adapter$render_ggplot(mapped_state, patch), silent = TRUE), "try-error"))
for (bad in list(list(alpha = 1.01), list(linewidth = 5.01), list(capsize = -2),
                 list(cap_thickness = .01))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, setNames(list(bad), gid)),
                        silent = TRUE), "try-error"))
}

family_gid <- "axis.y.left.title"
font <- setNames(list(list(fontfamily = "monospace")), family_gid)
text <- function(edits) {
  Filter(function(e) e$gid == family_gid, adapter$ggplot_manifest(state, "interval", edits)$elements)[[1L]]
}
field <- Filter(function(e) e$prop == "fontfamily", text(font)$editable)[[1L]]
stopifnot(identical(field$value, "monospace"), identical(field$value_original, "sans-serif"),
          !identical(text(font)$bbox, text(list())$bbox),
          identical(adapter$ggplot_text_paint(state$text_targets[[family_gid]], font[[1L]])$paint$fontfamily,
                    unname(systemfonts::font_info(family = "mono")$family)),
          identical(adapter$render_ggplot(state), captured$svg), calls == 1L)
"""

RANGE_AND_GROUPED_LINE_PROBE = r"""

if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
d <- data.frame(x = rep(1:5, 2), y = c(2, 3, 2.7, 3.5, 4, 5, 5.2, 6, 5.9, 7),
                group = rep(c("Control", "Treated"), each = 5))
d$lower <- d$y - .2
d$upper <- d$y + .38
gapped <- d
gapped$y[3L] <- NA_real_
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
base <- ggplot2::ggplot(d, ggplot2::aes(x, y, group = group))
band <- ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper),
                             fill = "#559944", alpha = .4, data = observed)
line <- ggplot2::geom_line(ggplot2::aes(colour = group), data = gapped, linewidth = .7)
points <- ggplot2::geom_point(ggplot2::aes(colour = group))
palette <- ggplot2::scale_colour_manual(values = c(Control = "#2369A3", Treated = "#A53658"))
frame <- c(316, 212)
captured <- adapter$capture_ggplot(base + band + line + points + palette, frame)
state <- captured$state
range_id <- "layer.1.ribbon"
line_id <- "layer.2.line"
stopifnot(calls == 1L, length(state$line_targets[[line_id]]$guide_keys) == 1L)
element <- function(all, gid) Filter(function(e) e$gid == gid, all$elements)[[1L]]
baseline <- adapter$ggplot_manifest(state, "plot")
initial_line <- element(baseline, line_id)
initial_band <- element(baseline, range_id)
stopifnot(initial_line$geometry$kind == "multi_path",
          identical(unname(lengths(lapply(initial_line$geometry$paths, `[[`, "points"))), c(2L, 2L, 5L)),
          length(initial_band$geometry$paths) == 2L,
          all(vapply(initial_band$geometry$paths, `[[`, logical(1L), "closed")),
          initial_band$geometry$paint_order < initial_line$geometry$paint_order,
          !("color" %in% vapply(initial_line$editable, `[[`, character(1L), "prop")))
patches <- setNames(list(list(facecolor = "#695DB2", alpha = .6),
                         list(linewidth = 2.5, linestyle = "--")), c(range_id, line_id))
actual <- adapter$ggplot_manifest(state, "plot", patches)
svg <- adapter$render_ggplot(state, patches)
rows <- strsplit(svg, "\n", fixed = TRUE)[[1L]]
for (color in c("#2369A3", "#A53658")) {
  painted <- grepl(paste0("stroke: ", color), rows, fixed = TRUE)
  styled <- grepl("stroke-width: 2.50", rows, fixed = TRUE) &
    grepl("stroke-dasharray:", rows, fixed = TRUE)
  stopifnot(any(startsWith(rows, "<polyline ") & painted & styled),
            sum(startsWith(rows, "<line ") & painted & styled) == 1L,
            sum(startsWith(rows, "<circle ") & painted & !styled) == 6L)
}
stopifnot(sum(grepl("fill: #695DB2; fill-opacity: 0.60", rows, fixed = TRUE)) == 2L,
          identical(element(actual, range_id)$geometry$paths, initial_band$geometry$paths),
          identical(element(actual, line_id)$geometry$paths, initial_line$geometry$paths),
          element(actual, range_id)$geometry$paint_order < element(actual, line_id)$geometry$paint_order,
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, patches), svg), calls == 1L)
covered <- adapter$capture_ggplot(base + line + band + palette, frame)$state
covered_manifest <- adapter$ggplot_manifest(covered, "plot")
stopifnot(element(covered_manifest, "layer.1.line")$geometry$paint_order <
          element(covered_manifest, "layer.2.ribbon")$geometry$paint_order)
outline <- base + ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper),
                                      fill = "#559944", colour = "#551144", alpha = .4)
outline_state <- adapter$capture_ggplot(outline, frame)$state
outline_svg <- adapter$render_ggplot(outline_state, setNames(list(list(facecolor = "#695DB2")), range_id))
stopifnot(is.null(outline_state$ribbon_targets[[range_id]]$values$alpha),
          grepl("stroke: #551144", outline_svg, fixed = TRUE),
          grepl("fill: #695DB2; fill-opacity: 0.40", outline_svg, fixed = TRUE))
area <- base + ggplot2::geom_area(fill = "#559944", alpha = .4)
stopifnot(length(adapter$capture_ggplot(area, frame)$state$ribbon_targets) == 1L)
for (plot in list(base + band, area)) {
  facet <- ggplot2::facet_grid(cols = ggplot2::vars(group), scales = "free_y")
  faceted <- adapter$capture_ggplot(plot + facet, frame)
  facet_state <- faceted$state
  left <- "layer.1.panel.1.ribbon"
  right <- "layer.1.panel.2.ribbon"
  stopifnot(identical(names(facet_state$ribbon_targets), c(left, right)))
  facet_change <- stats::setNames(list(list(facecolor = "#695DB2", alpha = .65)), right)
  plain <- adapter$ggplot_manifest(facet_state, "range")
  changed <- adapter$ggplot_manifest(facet_state, "range", facet_change)
  find <- function(scene, gid) Filter(function(e) e$gid == gid, scene$elements)[[1L]]
  left_plain <- find(plain, left)
  right_changed <- find(changed, right)
  stopifnot(identical(left_plain, find(changed, left)),
            identical(find(plain, right)$geometry$paths, right_changed$geometry$paths),
            endsWith(right_changed$label, "Treated"), right_changed$ribbon$current$alpha == .65,
            grepl("fill: #695DB2", adapter$render_ggplot(facet_state, facet_change), fixed = TRUE),
            identical(adapter$render_ggplot(facet_state), faceted$svg))
}
mapped <- base + ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper, fill = group))
stopifnot(length(adapter$capture_ggplot(mapped, frame)$state$ribbon_targets) == 0L)
duplicated_key <- base + line + line + palette
stopifnot(length(adapter$capture_ggplot(duplicated_key, frame)$state$line_targets) == 0L)
intentional_key <- base + line + palette +
  ggplot2::guides(colour = ggplot2::guide_legend(override.aes = list(linetype = 2)))
stopifnot(length(adapter$capture_ggplot(intentional_key, frame)$state$line_targets) == 0L)
for (edit in list(setNames(list(list(color = "#115500")), line_id),
                  setNames(list(list(alpha = 2)), range_id))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, edit), silent = TRUE), "try-error"))
}
"""


AXES_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
frame <- c(312, 224)
values <- data.frame(x = rep(c(1, 10, 100, 1000), each = 2),
                     y = c(3, 5, 5, 7, 7, 9, 9, 11))
calls <- 0L
counted_mean <- function(y) { calls <<- calls + 1L; mean(y) }
plot <- ggplot2::ggplot(values, ggplot2::aes(x, y)) +
  ggplot2::stat_summary(geom = "line", fun = counted_mean, colour = "#3658AC") +
  ggplot2::geom_point() + ggplot2::labs(x = "Dose", y = "Response")
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
layers <- state$built$data
stopifnot(calls == 4L, identical(layers[[1L]]$y, c(4, 6, 8, 10)))
element <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
baseline <- adapter$ggplot_manifest(state, "dose")
regular <- element(baseline, "axes_0")
stopifnot(regular$role == "axes",
          identical(regular$native_axes$original$x$options, list("linear", "log", "symlog")))
edits <- list(axes_0 = list(xlim = list(1, 1000), ylim = c(2, 12), xscale = "log"),
              layer.1.line = list(color = "#695DB2", linewidth = 1.8))
rendered <- adapter$ggplot_manifest(state, "dose", edits)
axes <- element(rendered, "axes_0")
curve <- element(rendered, "layer.1.line")
stopifnot(identical(axes$native_axes$current$x, list(mode = "log", range = list(1, 1000), inverted = FALSE)),
          identical(axes$native_axes$current$y, list(mode = "linear", range = list(2, 12), inverted = FALSE)),
          identical(axes$native_axes$context, regular$native_axes$context),
          identical(curve$line_context, element(baseline, "layer.1.line")$line_context))
xy <- do.call(rbind, lapply(curve$geometry$paths[[1L]]$points, unlist))
bbox <- unlist(axes$bbox)
stopifnot(isTRUE(all.equal((xy[, 1L] - bbox[[1L]]) / bbox[[3L]], c(0, 1/3, 2/3, 1))),
          isTRUE(all.equal(1 - (xy[, 2L] - bbox[[2L]]) / bbox[[4L]], c(.2, .4, .6, .8))),
          identical(curve$geometry$clip, axes$bbox))
svg <- adapter$render_ggplot(state, edits)
rows <- strsplit(svg, "\n", fixed = TRUE)[[1L]]
stopifnot(any(grepl("stroke: #695DB2", rows, fixed = TRUE) & grepl("stroke-width: 1.80", rows, fixed = TRUE)),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, edits), svg),
          identical(state$built$data, layers), calls == 4L)
png <- adapter$preview_ggplot_png(state, edits, "axis-log.png", 800, TRUE)
png_axes <- element(adapter$ggplot_manifest(state, "dose", edits, png), "axes_0")
stopifnot(identical(png_axes$native_axes$current, axes$native_axes$current), file.info("axis-log.png")$size > 0)
outside <- list(axes_0 = list(xlim = c(2000, 3000)))
outside_scene <- adapter$ggplot_manifest(state, "dose", outside)
stopifnot(identical(element(outside_scene, "layer.2.scatter")$bbox, list(-1, -1, 0, 0)))
interval <- edits
interval$axis.x.bottom.ticks <- list(format = "%.0f", major_mode = "step", major_step = 250)
stopifnot(grepl(">250</text>", adapter$render_ggplot(state, interval), fixed = TRUE), calls == 4L)

reversed <- interval
reversed$axes_0$invert_x <- TRUE
reversed$axes_0$invert_y <- TRUE
flip <- adapter$ggplot_manifest(state, "dose", reversed)
flipped_axes <- element(flip, "axes_0")$native_axes
stopifnot(flipped_axes$current$x$inverted, flipped_axes$current$y$inverted,
          !flipped_axes$original$x$inverted,
          identical(flipped_axes$current$x$range, list(1, 1000)),
          identical(flipped_axes$context, regular$native_axes$context))
flipped <- element(flip, "layer.1.line")
flipped_bbox <- unlist(element(flip, "axes_0")$bbox)
projected <- do.call(rbind, lapply(flipped$geometry$paths[[1L]]$points, unlist))
stopifnot(isTRUE(all.equal((projected[, 1L] - flipped_bbox[[1L]]) / flipped_bbox[[3L]], c(1, 2/3, 1/3, 0))),
          isTRUE(all.equal((projected[, 2L] - flipped_bbox[[2L]]) / flipped_bbox[[4L]], c(.2, .4, .6, .8))),
          identical(flipped$geometry$clip, element(flip, "axes_0")$bbox))
guide_positions <- function(patches) adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  table <- adapter$ggplot_table_for(state, patches)$table
  panel <- table$grobs[[which(table$layout$name == "panel")]]
  grill <- panel$children[[grep("^grill", names(panel$children))]]
  major <- grill$children[[grep("^panel.grid.major.x", names(grill$children))]]
  tick <- state$text_targets[["axis.x.bottom.ticks"]]
  axis <- table$grobs[[tick$slots[[1L]]]]$children[["axis"]]
  text <- axis$grobs[[tick$slots[[3L]]]]$children[[1L]]
  list(grid = unique(as.numeric(major$x)), ticks = as.numeric(text$x), labels = unname(text$label))
})$value
positions <- guide_positions(reversed)
expected <- 1 - log10(c(250, 500, 750, 1000)) / 3
stopifnot(isTRUE(all.equal(positions$grid, expected)),
          isTRUE(all.equal(positions$ticks, expected)),
          identical(positions$labels, c("250", "500", "750", "1000")),
          calls == 4L, identical(adapter$render_ggplot(state), captured$svg))

tick_gid <- "axis.x.bottom.ticks"
fixed <- list(axes_0 = reversed$axes_0)
marks <- c(-5, 1, 2, 50, 150, 1000, 2000)
fixed[[tick_gid]] <- list(major_mode = "fixed", major_values = as.list(marks), format = "%.0f")
fixed_scene <- adapter$ggplot_manifest(state, "dose", fixed)
fixed_axes <- element(fixed_scene, "axes_0")
field <- function(manifest, prop) {
  Filter(function(f) f$prop == prop, element(manifest, tick_gid)$editable)[[1L]]
}
stopifnot(identical(field(fixed_scene, "major_values")$value, as.list(marks)),
          identical(field(fixed_scene, "major_mode")$value, "fixed"),
          identical(fixed_axes$native_axes$current, flipped_axes$current))
fixed_positions <- guide_positions(fixed)
visible <- c(1, 2, 50, 150, 1000)
expected_fixed <- 1 - log10(visible) / 3
stopifnot(isTRUE(all.equal(fixed_positions$grid, expected_fixed)),
          isTRUE(all.equal(fixed_positions$ticks, expected_fixed)),
          identical(fixed_positions$labels, as.character(visible)))

fixed_svg <- adapter$render_ggplot(state, fixed)
empty <- fixed
empty[[tick_gid]] <- list(major_mode = "fixed", major_values = c(-5, 2000),
                          format = "%.2f", color = "#A52A55", fontsize = 15)
empty_svg <- adapter$render_ggplot(state, empty)
empty_png <- adapter$preview_ggplot_png(state, empty, "empty-ticks.png", 800, TRUE)
empty_scene <- adapter$ggplot_manifest(state, "dose", empty, empty_png)
stopifnot(identical(element(empty_scene, tick_gid)$bbox, as.list(c(-1, -1, 0, 0))),
          identical(field(empty_scene, "major_values")$value, list(-5, 2000)),
          !grepl("#A52A55", empty_svg, fixed = TRUE),
          identical(adapter$render_ggplot(state, fixed), fixed_svg),
          identical(adapter$render_ggplot(state), captured$svg), calls == 4L)
empty$axes_0$xlim <- c(1900, 2100)
stopifnot(grepl(">2000.00</text>", adapter$render_ggplot(state, empty), fixed = TRUE))

# An authored logarithmic scale also accepts explicit positions in data units.
authored <- adapter$capture_ggplot(ggplot2::ggplot(data.frame(x = c(1, 10, 100), y = 1:3),
  ggplot2::aes(x, y)) + ggplot2::geom_point() + ggplot2::scale_x_log10() +
  ggplot2::coord_cartesian(xlim = c(1, 100), expand = FALSE), frame)
authored_manifest <- adapter$ggplot_manifest(authored$state, "authored")
stopifnot(identical(field(authored_manifest, "major_mode")$options, list("auto", "fixed")),
          identical(field(authored_manifest, "major_values")$value, list(1, 10, 100)))
authored_fixed <- stats::setNames(list(list(major_values = list(3), format = "%.1f")), tick_gid)
stopifnot(grepl(">3.0</text>", adapter$render_ggplot(authored$state, authored_fixed), fixed = TRUE))

grid_patch <- reversed
grid_patch$axes_0 <- c(grid_patch$axes_0, list(grid_x = TRUE, grid_y = FALSE,
    grid_color = "#356897", grid_linewidth = 2.3, grid_alpha = .5, grid_linestyle = "--"))
grill_for <- function(table) {
  panel <- table$grobs[[which(table$layout$name == "panel")]]
  panel$children[[grep("^grill", names(panel$children))]]
}
major_grid <- function(grill, axis) {
  grobs <- grill$children[startsWith(names(grill$children), paste0("panel.grid.major.", axis))]
  Filter(function(grob) inherits(grob, "polyline"), grobs)
}
device_grobs <- function(state, edits, axis) {
  adapter$with_svg_frame(frame, function(capture) {
    grid::grid.newpage()
    major_grid(grill_for(adapter$ggplot_table_for(state, edits)$table), axis)
  })$value
}
x_grid <- device_grobs(state, grid_patch, "x")[[1L]]
stopifnot(isTRUE(all.equal(unique(as.numeric(x_grid$x)), expected)),
          identical(toupper(x_grid$gp$col), "#35689780"),
          identical(x_grid$gp$lty, "dashed"),
          isTRUE(all.equal(x_grid$gp$lwd * 72 / 96, 2.3)),
          !length(device_grobs(state, grid_patch, "y")),
          identical(adapter$render_ggplot(state), captured$svg), calls == 4L)
grid_y_off <- list(axes_0 = list(grid_y = FALSE))
grid_y_before <- grill_for(state$table)
grid_y_after <- adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  grill_for(adapter$ggplot_table_for(state, grid_y_off)$table)
})$value
minor_y <- function(grill) {
  groove <- grill$children[[grep("^panel.grid.minor.y", names(grill$children))]]
  groove[c("x", "y", "gp")]
}
stopifnot(!length(major_grid(grid_y_after, "y")),
          identical(minor_y(grid_y_before), minor_y(grid_y_after)))
for (bad in list(list(grid_x = 1), list(grid_alpha = 1.1), list(grid_linestyle = "foo"),
                 list(grid_linewidth = 0), list(grid_color = c("#000000", "#FFFFFF")))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, list(axes_0 = bad)), silent = TRUE), "try-error"))
}
categorical_plot <- ggplot2::ggplot(data.frame(x = c("A", "B"), y = c(2, 4)), ggplot2::aes(x, y)) +
  ggplot2::geom_col() + ggplot2::theme_minimal() + ggplot2::theme(panel.grid = ggplot2::element_blank())
categorical <- adapter$capture_ggplot(categorical_plot, frame)
grid_spec <- categorical$state$axes_target$grid$original
stopifnot(!grid_spec$grid_x, !grid_spec$grid_y)
activate <- list(axes_0 = list(grid_x = TRUE, grid_color = "#016CA5"))
stopifnot(length(device_grobs(categorical$state, activate, "x")) == 1L,
          !length(device_grobs(categorical$state, activate, "y")),
          identical(adapter$render_ggplot(categorical$state), categorical$svg))

source_log <- adapter$capture_ggplot(plot + ggplot2::scale_x_log10(), frame)
native_log <- element(adapter$ggplot_manifest(source_log$state, "dose"), "axes_0")
native_linear <- element(adapter$ggplot_manifest(source_log$state, "dose",
                         list(axes_0 = list(xscale = "linear", xlim = c(1, 1000)))), "axes_0")
stopifnot(native_log$native_axes$current$x$mode == "log", native_linear$native_axes$current$x$mode == "linear",
          identical(native_linear$native_axes$current$x$range, list(1, 1000)), calls == 8L)

source_reversed <- adapter$capture_ggplot(plot + ggplot2::coord_cartesian(reverse = "xy"), frame)
reversed_state <- source_reversed$state
authored <- element(adapter$ggplot_manifest(reversed_state, "dose"), "axes_0")$native_axes
back <- element(adapter$ggplot_manifest(reversed_state, "dose",
                        list(axes_0 = list(invert_x = FALSE))), "axes_0")$native_axes
stopifnot(authored$current$x$inverted, authored$current$y$inverted,
          !identical(authored$context, regular$native_axes$context),
          !back$current$x$inverted, back$current$y$inverted, back$original$x$inverted,
          !is.null(reversed_state$tick_targets[["axis.x.bottom.ticks"]]$spacing),
          identical(adapter$render_ggplot(reversed_state), source_reversed$svg))
for (invalid in list("true", 1, NA, c(TRUE, FALSE))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, list(axes_0 = list(invert_x = invalid))),
                        silent = TRUE), "try-error"))
}

raster_plot <- ggplot2::ggplot(expand.grid(x = 1:3, y = 1:3), ggplot2::aes(x, y, fill = x + y)) +
  ggplot2::geom_raster()
raster <- adapter$capture_ggplot(raster_plot, frame)$state
raster_control <- element(adapter$ggplot_manifest(raster, "raster"), "axes_0")$native_axes
stopifnot(!raster_control$original$x$inverted, !raster_control$original$y$inverted)
raster_reversed <- element(adapter$ggplot_manifest(raster, "raster",
                                    list(axes_0 = list(invert_x = TRUE))), "axes_0")$native_axes
stopifnot(raster_reversed$current$x$inverted, !raster_reversed$current$y$inverted)
raster_crop <- element(adapter$ggplot_manifest(raster, "raster",
                                list(axes_0 = list(xlim = c(1, 3)))), "axes_0")$native_axes
stopifnot(identical(raster_crop$current$x$range, list(1, 3)))

with_zero <- plot + ggplot2::geom_hline(yintercept = 0)
zero_state <- adapter$capture_ggplot(with_zero, frame)$state
stopifnot(identical(zero_state$axes_target$axes$y$options, c("linear", "symlog")),
          identical(zero_state$axes_target$axes$x$options, c("linear", "log", "symlog")))
invalid <- list(list(xlim = c(10, 2)), list(xlim = list("1", "10")),
                list(xscale = "log", xlim = c(0, 100)), list(yscale = "log"))
for (change in invalid) {
  stopifnot(inherits(try(adapter$render_ggplot(zero_state, list(axes_0 = change)), silent = TRUE), "try-error"))
}
secondary <- plot + ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis())
stopifnot(!length(adapter$capture_ggplot(secondary, frame)$state$axes_target$axes))
constant <- ggplot2::ggplot(data.frame(x = c(10, 10), y = c(2, 3)), ggplot2::aes(x, y)) +
  ggplot2::geom_point()
flat <- adapter$capture_ggplot(constant, frame)$state
flat_axes <- element(adapter$ggplot_manifest(flat, "flat", list(axes_0 = list(xscale = "log"))), "axes_0")
stopifnot(flat_axes$native_axes$current$x$mode == "log")
"""


FRAME_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
frame <- c(216, 144)
plot <- ggplot2::ggplot(data.frame(x = c(1, 2, 4), y = c(2, 4, 3)), ggplot2::aes(x, y)) +
  ggplot2::geom_line(colour = "#2021A7")
state <- adapter$capture_ggplot(plot + ggplot2::theme_classic(), frame)
target <- state$state$axes_target
base <- adapter$ggplot_frame_value(target$frame, list())
stopifnot(base$spine_bottom, base$spine_left, !base$spine_top, !base$spine_right)

patch <- list(axes_0 = list(spine_top = TRUE, spine_left = FALSE, spine_color = "#708019",
                            spine_bottom_linewidth = 2, spine_top_color = "#164623"))
scene <- adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  adapter$ggplot_table_for(state$state, patch)$table
})$value
drawn <- scene$grobs[[target$slot]]
top <- drawn$children[[grep("^panel.border", drawn$childrenOrder)]]$children[["tavotto.frame.top"]]
bottom <- scene$grobs[[which(scene$layout$name == "axis-b")]]$children[[1L]]
left <- scene$grobs[[which(scene$layout$name == "axis-l")]]$children[[1L]]
stopifnot(identical(toupper(top$gp$col), "#164623FF"),
          identical(toupper(bottom$gp$col), "#708019FF"),
          isTRUE(all.equal(bottom$gp$lwd * 72 / 96, 2)),
          inherits(left, "null"),
          identical(adapter$render_ggplot(state$state), state$svg))
with_grid <- patch
with_grid$axes_0 <- c(with_grid$axes_0, list(grid_x = TRUE, grid_color = "#A51545",
                                            xlim = c(1, 4), invert_x = TRUE))
svg <- adapter$render_ggplot(state$state, with_grid)
stopifnot(all(vapply(c("#708019", "#164623", "#A51545", "#2021A7"), grepl, logical(1L),
                  x = svg, fixed = TRUE)),
          identical(adapter$render_ggplot(state$state), state$svg))

boxed <- adapter$capture_ggplot(plot + ggplot2::theme_bw(), frame)
packed <- adapter$ggplot_frame_value(boxed$state$axes_target$frame, list())
stopifnot(all(unlist(packed[c("spine_top", "spine_right", "spine_bottom", "spine_left")])))
box_edit <- list(axes_0 = list(spine_right = FALSE, spine_top_color = "#203181", spine_linewidth = 2.1,
                              spine_top_linewidth = 3.2))
box <- Filter(function(e) e$gid == "axes_0", adapter$ggplot_manifest(boxed$state, "box", box_edit)$elements)[[1L]]
stopifnot(box$native_axes$frame$current$spine_color == packed$spine_color,
          box$native_axes$frame$current$spine_left_linewidth == 2.1,
          box$native_axes$frame$current$spine_top_linewidth == 3.2,
          !box$native_axes$frame$current$spine_right)
for (bad in list(list(spine_top = 1), list(spine_left_linewidth = 0), list(spine_color = "bogus"))) {
  stopifnot(inherits(try(adapter$render_ggplot(boxed$state, list(axes_0 = bad)), silent = TRUE), "try-error"))
}
"""


RASTER_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
cells <- expand.grid(x = 1:3, y = 1:2)
cells$color <- c("#D60000", "#059355", "#183ED0", "#6A1692", "#EA5303", "#6EE7D7")
plot <- ggplot2::ggplot(cells, ggplot2::aes(x, y, fill = color)) +
  ggplot2::geom_raster(hjust = .25, vjust = .75) + ggplot2::scale_fill_identity() +
  ggplot2::theme_void() + ggplot2::theme(plot.background = ggplot2::element_rect(fill = "white"))
coords <- function(reverse) ggplot2::coord_cartesian(xlim = c(0, 8), ylim = c(0, 5),
                                                     expand = FALSE, reverse = reverse)
frame <- c(576, 360)
captured <- adapter$capture_ggplot(plot + coords("none"), frame)
for (mode in c("none", "x", "y", "xy")) {
  changes <- if (mode != "none") list(axes_0 = list(invert_x = grepl("x", mode),
                                                   invert_y = grepl("y", mode))) else list()
  png <- adapter$preview_ggplot_png(captured$state, changes, paste0(mode, ".png"), 800, TRUE)
  stopifnot(file.info(paste0(mode, ".png"))$size > 0)
}
authored <- adapter$capture_ggplot(plot + coords("xy"), frame)
adapter$preview_ggplot_png(authored$state, list(), "authored.png", 800, TRUE)
adapter$preview_ggplot_png(authored$state, list(axes_0 = list(invert_x = FALSE, invert_y = FALSE)),
                           "normalised.png", 800, TRUE)
stopifnot(identical(adapter$render_ggplot(captured$state), captured$svg),
          identical(adapter$render_ggplot(authored$state), authored$svg))
"""


SCALE_MODES_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
frame <- c(320, 240)
calls <- 0L
observe <- function(data) { calls <<- calls + 1L; data }
p <- c(.07, .15, .5, .82, .91)
data <- data.frame(probability = p, score = c(2, 4, 3, 6, 8))
chart <- function(data) ggplot2::ggplot(data, ggplot2::aes(probability, score)) +
  ggplot2::geom_line(data = observe, colour = "#3366AA")
capture <- adapter$capture_ggplot(chart(data), frame)
state <- capture$state
element <- function(manifest, gid) Filter(function(el) el$gid == gid, manifest$elements)[[1L]]
manifest <- function(state, patch = list()) adapter$ggplot_manifest(state, "probability", patch)
axes <- function(state, patch = list()) element(manifest(state, patch), "axes_0")
original <- axes(state)$native_axes
stopifnot(identical(original$current$x$options, list("linear", "log", "symlog", "logit")),
          identical(original$current$y$options, list("linear", "log", "symlog")))
patch <- list(axes_0 = list(xscale = "logit", xlim = c(.05, .95), invert_x = TRUE))
patch$axis.x.bottom.ticks <- list(major_mode = "fixed", major_values = c(0, .1, .5, .9, 1),
                                  format = "%.2f")
after <- manifest(state, patch)
output <- element(after, "axes_0")
curve <- element(after, "layer.1.line")
physical <- do.call(rbind, lapply(curve$geometry$paths[[1L]]$points, unlist))
bbox <- unlist(output$bbox)
reference <- 1 - (stats::qlogis(p) - stats::qlogis(.05)) /
  (stats::qlogis(.95) - stats::qlogis(.05))
stopifnot(identical(output$native_axes$current$x$mode, "logit"),
          identical(output$native_axes$current$x$range, list(.05, .95)),
          identical(output$native_axes$context, original$context),
          isTRUE(all.equal((physical[, 1L] - bbox[[1L]]) / bbox[[3L]], reference)))
svg <- adapter$render_ggplot(state, patch)
stopifnot(grepl(">0.10</text>", svg, fixed = TRUE), grepl(">0.90</text>", svg, fixed = TRUE),
          !grepl(">0.00</text>", svg, fixed = TRUE), !grepl(">1.00</text>", svg, fixed = TRUE),
          identical(adapter$render_ggplot(state), capture$svg),
          identical(adapter$render_ggplot(state, patch), svg), calls == 1L)
outside <- patch
outside$axes_0$xlim <- c(.05, 1)
error <- try(adapter$render_ggplot(state, outside), silent = TRUE)
stopifnot(inherits(error, "try-error"), grepl("between zero and one", as.character(error), fixed = TRUE))

explicit <- adapter$capture_ggplot(chart(data) + ggplot2::scale_x_continuous(transform = "logit"), frame)
stopifnot(identical(axes(explicit$state)$native_axes$current$x$mode, "logit"),
          identical(adapter$render_ggplot(explicit$state), explicit$svg),
          identical(axes(explicit$state, list(axes_0 = list(xscale = "linear")))$native_axes$current$x$mode,
                    "linear"))
data$probability[[1L]] <- 0
invalid <- adapter$capture_ggplot(chart(data) + ggplot2::coord_cartesian(xlim = c(.1, .9)), frame)$state
stopifnot(!"logit" %in% axes(invalid)$native_axes$current$x$options,
          inherits(try(adapter$render_ggplot(invalid, patch), silent = TRUE), "try-error"))

crossing <- c(-200, -2, -.5, 0, .5, 2, 200)
signed_chart <- chart(data.frame(probability = crossing, score = seq_along(crossing)))
signed <- adapter$capture_ggplot(signed_chart, frame)
signed_base <- axes(signed$state)$native_axes
stopifnot(identical(signed_base$current$x$options, list("linear", "symlog")))
symlog <- list(axes_0 = list(xscale = "symlog", xlim = c(-200, 200)))
symlog$axis.x.bottom.ticks <- list(major_mode = "fixed", major_values = c(-20, 0, 20), format = "%.0f")
log_canvas <- manifest(signed$state, symlog)
plot <- element(log_canvas, "axes_0")
points <- element(log_canvas, "layer.1.line")$geometry$paths[[1L]]$points
coords <- do.call(rbind, lapply(points, unlist))
origin <- unlist(plot$bbox)
# Matplotlib's default preset maps the bounds to +/- (2*10/9 + 4)
# and is linear to +/- 2. The reference does not read the adapter's transform.
magnitudes <- c(56/9, 20/9, 5/9, 0, 5/9, 20/9, 56/9)
expect <- .5 + sign(crossing) * magnitudes / (112/9)
rendered <- adapter$render_ggplot(signed$state, symlog)
stopifnot(identical(plot$native_axes$current$x$mode, "symlog"),
          identical(plot$native_axes$current$x$range, as.list(c(-200, 200))),
          isTRUE(all.equal((coords[, 1L] - origin[[1L]]) / origin[[3L]], expect)),
          grepl(">0</text>", rendered, fixed = TRUE), grepl(">20</text>", rendered, fixed = TRUE),
          identical(adapter$render_ggplot(signed$state), signed$svg),
          identical(adapter$render_ggplot(signed$state, symlog), rendered))
"""


FACET_POINT_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
frame <- c(500, 350)
calls <- 0L
observe <- function(data) { calls <<- calls + 1L; data }
data <- data.frame(x = c(1, 2, 4, 5, 7), y = c(1, 4, 3, 2, 5), z = 1:5,
                   row = factor(c("Beta", "Beta", "Alpha", "Alpha", "Alpha"),
                                levels = c("Beta", "Empty", "Alpha")),
                   col = factor(c("c", "c", "c", "d", "d")))
chart <- function(data) ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z)) +
  ggplot2::geom_point(data = observe, alpha = .6,
                     position = ggplot2::position_jitter(width = .15, height = 0, seed = 18)) +
  ggplot2::scale_colour_gradient(name = "Exposure")
facet <- ggplot2::facet_wrap(ggplot2::vars(row), drop = FALSE, ncol = 2, as.table = FALSE)
capture <- adapter$capture_ggplot(chart(data) + facet, frame)
state <- capture$state
beta <- "layer.1.panel.1.scatter"
alpha <- "layer.1.panel.3.scatter"
targets <- state$point_targets
stopifnot(identical(names(targets), c(beta, alpha)), identical(targets[[beta]]$facet$row, "Beta"),
          identical(targets[[alpha]]$facet$row, "Alpha"), targets[[beta]]$count == 2L,
          targets[[alpha]]$count == 3L, targets[[beta]]$bbox[[2L]] > targets[[alpha]]$bbox[[2L]],
          calls == 1L)
grob <- function(table, gid) {
  dest <- targets[[gid]]
  adapter$ggplot_point_grob(table$grobs[[dest$slot]], dest)
}
points <- lapply(c(beta, alpha), function(gid) grob(state$table, gid))
patch <- stats::setNames(list(list(size = 64, marker = "D", alpha = .8)), alpha)
inspect <- function(edits) adapter$with_svg_frame(frame, function(device) {
  grid::grid.newpage()
  table <- adapter$ggplot_table_for(state, edits)$table
  lapply(c(beta, alpha), function(gid) grob(table, gid))
})$value
changed <- inspect(patch)
stopifnot(identical(changed[[1L]], points[[1L]]),
          identical(changed[[2L]][c("x", "y")], points[[2L]][c("x", "y")]),
          identical(changed[[2L]]$pch, 18L),
          all(abs(changed[[2L]]$gp$fontsize - changed[[2L]]$gp$lwd - 8) < 1e-10),
          identical(substr(changed[[2L]]$gp$col, 1L, 7L), substr(points[[2L]]$gp$col, 1L, 7L)),
          all(endsWith(changed[[2L]]$gp$col, "CC")))
svg <- adapter$render_ggplot(state, patch)
stopifnot(!identical(svg, capture$svg), identical(adapter$render_ggplot(state), capture$svg),
          identical(adapter$render_ggplot(state, patch), svg), calls == 1L)

colourbar <- "legend.right.1.colourbar"
with_layout <- c(patch, stats::setNames(list(list(orientation = "horizontal", extend = "min")), colourbar))
stopifnot(identical(inspect(with_layout), changed))

table <- adapter$capture_ggplot(
  chart(data) + ggplot2::facet_grid(row ~ col, drop = FALSE, as.table = FALSE), frame)$state
labels <- lapply(table$point_targets, function(t) unlist(t$facet, use.names = FALSE))
stopifnot(length(labels) == 3L, any(vapply(labels, identical, logical(1L), c("Beta", "c"))),
          any(vapply(labels, identical, logical(1L), c("Alpha", "c"))),
          any(vapply(labels, identical, logical(1L), c("Alpha", "d"))))
one <- adapter$capture_ggplot(chart(data[data$row == "Alpha", ]) +
                              ggplot2::facet_wrap(ggplot2::vars(row)), frame)$state
stopifnot(identical(names(one$point_targets), "layer.1.panel.1.scatter"))
data$row <- stats::relevel(data$row, "Alpha")
reordered <- adapter$capture_ggplot(chart(data) + facet, frame)$state
stopifnot(identical(reordered$point_targets[[beta]]$facet$row, "Alpha"),
          !identical(reordered$point_targets[[beta]]$context, targets[[beta]]$context))
mapped <- chart(data) + ggplot2::aes(size = z)
stopifnot(!"size" %in% names(adapter$capture_ggplot(mapped + facet, frame)$state$point_targets[[beta]]$values))
"""


MINOR_TICK_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
frame <- c(320, 200)
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
data <- data.frame(x = 1:10, y = 1:10)
base <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_point(data = observed) +
  ggplot2::theme(axis.ticks.x.bottom = ggplot2::element_line(colour = "#228833"),
                 axis.minor.ticks.x.bottom = ggplot2::element_line(colour = "#3366AA"),
                 panel.grid.minor.x = ggplot2::element_line(colour = "#CC2266"))
gid <- "axis.x.bottom.ticks"
fields <- function(state, edits = list()) {
  elements <- adapter$ggplot_manifest(state, "points", edits)$elements
  fields <- Filter(function(element) element$gid == gid, elements)[[1L]]$editable
  stats::setNames(fields, vapply(fields, function(field) field$prop, character(1L)))
}
axis_ink <- function(svg, colour) grepl(paste0("stroke: ", colour), svg, fixed = TRUE)
positions <- function(state, patch) adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  table <- adapter$ggplot_table_for(state, patch)$table
  panel <- table$grobs[[which(table$layout$name == "panel")]]
  grill <- panel$children[[grep("^grill", names(panel$children))]]
  grid <- grill$children[[grep("^panel.grid.minor.x", names(grill$children))]]
  axis <- table$grobs[[which(table$layout$name == "axis-b")]]$children[["axis"]]
  strokes <- Filter(function(grob) inherits(grob, "polyline"), axis$grobs[[1L]]$children)
  coords <- lapply(strokes, function(stroke) unique(as.numeric(stroke$x)))
  labels <- axis$grobs[[2L]]$children[[1L]]$label
  list(grid = unique(as.numeric(grid$x)), ticks = unname(coords), labels = labels)
})$value

capture <- adapter$capture_ggplot(base, frame)
state <- capture$state
original <- fields(state)
stopifnot(!original$minor_visible$value, !axis_ink(capture$svg, "#3366AA"))
patch <- list(axes_0 = list(xlim = c(1, 10), invert_x = TRUE))
patch[[gid]] <- list(minor_visible = TRUE, minor_mode = "step", minor_step = .75,
                     major_mode = "fixed", major_values = c(1, 3, 6, 9, 10), format = "%.1f")
actual <- positions(state, patch)
major <- c(1, 3, 6, 9, 10)
minor <- setdiff(seq(1.5, 9.75, by = .75), major)
reversed <- function(values) 1 - (values - 1) / 9
stopifnot(length(actual$ticks) == 2L,
          isTRUE(all.equal(actual$ticks[[1L]], reversed(major))),
          isTRUE(all.equal(actual$ticks[[2L]], reversed(minor))),
          isTRUE(all.equal(actual$grid, reversed(minor))),
          identical(actual$labels, sprintf("%.1f", major)))
edited <- fields(state, patch)
painted <- adapter$render_ggplot(state, patch)
stopifnot(edited$minor_visible$value, !edited$minor_visible$value_original,
          identical(edited$minor_step$value, .75),
          axis_ink(painted, "#3366AA"), axis_ink(painted, "#CC2266"),
          identical(adapter$render_ggplot(state), capture$svg),
          identical(adapter$render_ggplot(state, patch), painted), calls == 1L)
bad <- patch
bad[[gid]]$minor_step <- .001
error <- try(adapter$render_ggplot(state, bad), silent = TRUE)
stopifnot(inherits(error, "try-error"), grepl("minor tick interval", as.character(error), fixed = TRUE))

native <- ggplot2::guides(x = ggplot2::guide_axis(minor.ticks = TRUE))
authored <- adapter$capture_ggplot(base + native, frame)
off <- stats::setNames(list(list(minor_visible = FALSE, format = "%.2f")), gid)
without <- adapter$render_ggplot(authored$state, off)
stopifnot(fields(authored$state)$minor_visible$value,
          axis_ink(authored$svg, "#3366AA"), !axis_ink(without, "#3366AA"),
          axis_ink(without, "#CC2266"), grepl(">2.50</text>", without, fixed = TRUE),
          identical(adapter$render_ggplot(authored$state), authored$svg))

log_plot <- base + ggplot2::scale_x_log10() +
  ggplot2::coord_cartesian(xlim = c(1, 10), expand = FALSE, reverse = "x")
log_state <- adapter$capture_ggplot(log_plot, frame)$state
native_log <- adapter$capture_ggplot(log_plot + native, frame)
auto <- stats::setNames(list(list(minor_visible = TRUE)), gid)
stopifnot(identical(adapter$render_ggplot(log_state, auto), native_log$svg),
          identical(fields(log_state)$minor_mode$options, list("auto")))
"""


def _assert_r_probe(tmp_path, program):
    rscript, explicit_rscript = _rscript()
    driver = tmp_path / "probe.R"
    driver.write_text(R_PROBE_SETUP + program, encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(driver), str(ADAPTER)],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=_COLD_RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode == 77 and not explicit_rscript:
        pytest.skip("Rscript does not have ggplot2 and svglite")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "Rplots.pdf").exists()


def test_ggplot_title_colors_keep_the_built_layers_and_replay(tmp_path):
    _assert_r_probe(tmp_path, PROBE)


def test_ggplot_minor_tick_controls_use_native_positions_and_grid(tmp_path):
    _assert_r_probe(tmp_path, MINOR_TICK_PROBE)


def test_ggplot_faceted_point_edits_stay_on_the_selected_native_panel(tmp_path):
    _assert_r_probe(tmp_path, FACET_POINT_PROBE)


def test_ggplot_probability_and_symmetric_log_scales_reproject_captured_values(tmp_path):
    _assert_r_probe(tmp_path, SCALE_MODES_PROBE)


def test_ggplot_tick_and_curve_edits_preserve_native_values_and_colours(tmp_path):
    _assert_r_probe(tmp_path, PRECISION_PROBE)


def test_ggplot_colour_scale_replays_all_mapped_layers_with_the_same_guide(tmp_path):
    _assert_r_probe(tmp_path, COLOR_SCALE_PROBE)


def test_ggplot_bar_paint_preserves_counts_positions_and_siblings(tmp_path):
    _assert_r_probe(tmp_path, BAR_PROBE)


def test_ggplot_errorbar_stroke_and_title_family_preserve_native_data(tmp_path):
    _assert_r_probe(tmp_path, ERRORBAR_PROBE)


def test_range_and_grouped_line_edits_preserve_geometry_and_sync_the_legend(tmp_path):
    _assert_r_probe(tmp_path, RANGE_AND_GROUPED_LINE_PROBE)


def test_axis_windows_and_transforms_reproject_captured_data_without_recalculating(tmp_path):
    _assert_r_probe(tmp_path, AXES_PROBE)


def test_ggplot_frame_uses_native_borders_and_axis_strokes(tmp_path):
    _assert_r_probe(tmp_path, FRAME_PROBE)


def test_raster_reversal_preserves_the_color_and_position_of_every_cell(tmp_path):
    _assert_r_probe(tmp_path, RASTER_PROBE)
    with Image.open(tmp_path / "none.png") as file:
        original = file.convert("RGB")
    palette = ["d60000", "059355", "183ed0", "6a1692", "ea5303", "6ee7d7"]
    for mode in ("x", "y", "xy", "authored", "normalised"):
        with Image.open(tmp_path / f"{mode}.png") as file:
            actual = file.convert("RGB")
        expected = original
        if "x" in mode or mode == "authored":
            expected = expected.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if "y" in mode or mode == "authored":
            expected = expected.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        assert actual.tobytes() == expected.tobytes(), mode
        counts = actual.getcolors()
        assert {tuple(bytes.fromhex(code)) for code in palette} <= {rgb for _, rgb in counts}


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
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = cty)) +
  ggplot2::geom_point() +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage") +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis()) +
  ggplot2::scale_colour_continuous(name = "City mileage", breaks = c(10, 20, 30),
    labels = c("Ten", "Twenty", "Thirty"),
    guide = ggplot2::guide_colourbar(position = "bottom")) +
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
        assert state == nativesession.BARRIER, session.script_error or session.public_state()
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
        axes_props = {field["prop"] for field in elements["axes_0"]["editable"]}
        assert {prop for prop in axes_props if prop.startswith("grid_")} == {
            "grid_x",
            "grid_y",
            "grid_color",
            "grid_linestyle",
            "grid_linewidth",
            "grid_alpha",
        }
        scatter = elements["layer.1.scatter"]
        assert scatter["role"] == "scatter" and scatter["identity"].startswith("r6:")
        assert {field["prop"] for field in scatter["editable"]} == {"size", "marker", "alpha"}
        point_patch = {"gid": scatter["gid"], "prop": "marker", "value": "s"}
        for invalid_point in (point_patch, {**point_patch, "identity": "r6:old-series"}):
            with pytest.raises(pool.WorkerError, match="point series changed"):
                session.override(stem, [invalid_point], inline_svg=True)
        changed = session.override(
            stem, [{**point_patch, "identity": scatter["identity"]}], inline_svg=True
        )
        changed_scatter = next(
            e for e in changed["manifest"]["elements"] if e["gid"] == scatter["gid"]
        )
        marker = next(field for field in changed_scatter["editable"] if field["prop"] == "marker")
        assert marker["value"] == "s" and marker["value_original"] == "original"
        assert "<polygon " in changed["svg"] and changed["svg"] != baseline["svg"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        title = elements["plot.title"]
        assert title["label"] == "Fuel economy"
        assert title["identity"] == "r1:568c4e10e2a02cd8"
        x, y, width, height = title["bbox"]
        assert 0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        assert {item["prop"]: item["value"] for item in title["editable"]} == {
            "color": "#112233",
            "weight": "normal",
            "style": "normal",
            "fontfamily": "sans-serif",
            "fontsize": 13.2,
        }
        top = elements["axis.x.top.title"]
        bottom = elements[X_BOTTOM]
        assert top["label"] == bottom["label"] == "Engine displacement"
        assert top["identity"] != bottom["identity"]
        assert bottom["editable"][0]["value"] == "#6B6B6B"
        typography_patches = [
            {"gid": X_BOTTOM, "prop": prop, "value": value, "identity": bottom["identity"]}
            for prop, value in (("weight", "bold"), ("style", "italic"), ("fontsize", 19.25))
        ]
        plain_png = session.preview_png_snapshot(stem, [], 400)
        edited_png = session.preview_png_snapshot(stem, typography_patches, 400)
        with (
            Image.open(io.BytesIO(base64.b64decode(plain_png["png"]))) as plain,
            Image.open(io.BytesIO(base64.b64decode(edited_png["png"]))) as edited,
        ):
            assert plain.size == edited.size == (400, 267)
            assert plain.tobytes() != edited.tobytes()
        assert session.preview_png(stem, typography_patches, 400, "font-preview").read_bytes() == (
            base64.b64decode(edited_png["png"])
        )
        axis = next(
            element for element in edited_png["manifest"]["elements"] if element["gid"] == X_BOTTOM
        )
        assert axis["identity"] == bottom["identity"]
        assert {item["prop"]: item["value"] for item in axis["editable"]} == {
            "color": "#6B6B6B",
            "weight": "bold",
            "style": "italic",
            "fontfamily": "sans-serif",
            "fontsize": 19.25,
        }
        size_field = next(field for field in axis["editable"] if field["prop"] == "fontsize")
        assert size_field["value_original"] == 11
        original_ticks = next(
            el for el in plain_png["manifest"]["elements"] if el["gid"] == TICKS_BOTTOM
        )
        resized_ticks = next(
            el for el in edited_png["manifest"]["elements"] if el["gid"] == TICKS_BOTTOM
        )
        assert resized_ticks["bbox"][1] < original_ticks["bbox"][1]
        assert axis["bbox"][1] > resized_ticks["bbox"][1] + resized_ticks["bbox"][3]
        for invalid in (0, -2.5, "19.25"):
            with pytest.raises(pool.WorkerError, match="font size must be a positive number"):
                session.override(
                    stem, [{**typography_patches[-1], "value": invalid}], inline_svg=True
                )
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        tick_group = elements[TICKS_BOTTOM]
        assert tick_group["label"] == "下轴刻度文字"
        assert tick_group["role"] == "ticks" and not tick_group["draggable"]
        assert tick_group["identity"].startswith("r2:")
        assert "tick_labels" not in tick_group
        assert tick_group["identity"] != elements[TICKS_TOP]["identity"]
        tick_patch = {"gid": TICKS_BOTTOM, "prop": "color", "value": "#A64184"}
        for invalid_tick in (
            tick_patch,
            {**tick_patch, "identity": elements[TICKS_TOP]["identity"]},
        ):
            with pytest.raises(pool.WorkerError, match="text target changed"):
                session.override(stem, [invalid_tick], inline_svg=True)
        tick_patch["identity"] = tick_group["identity"]
        tick_render = session.override(stem, [tick_patch], inline_svg=True)
        assert "#A64184" in tick_render["svg"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        bar_ticks = elements[COLOURBAR_TICKS]
        assert bar_ticks["label"] == "色条刻度 “City mileage”"
        assert bar_ticks["role"] == "legend_text" and not bar_ticks["draggable"]
        assert bar_ticks["identity"].startswith("r2:")
        assert "tick_labels" not in bar_ticks and "legend_title" not in bar_ticks
        bar_patch = {"gid": COLOURBAR_TICKS, "prop": "color", "value": "#C32146"}
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(stem, [bar_patch], inline_svg=True)
        bar_patch["identity"] = bar_ticks["identity"]
        bar_preview = session.override(stem, [bar_patch], inline_svg=True)["svg"]
        assert bar_preview.count("#C32146") == 3
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        legend = elements["legend.bottom"]
        move = {"gid": legend["gid"], "prop": "loc_frac", "identity": legend["identity"]}
        for invalid in ([], [0.4], [0.4, "0.6"], {"x": 0.4, "y": 0.6}):
            with pytest.raises(pool.WorkerError, match="two finite numbers"):
                session.override(stem, [{**move, "value": invalid}], inline_svg=True)
        moved_png = session.preview_png_snapshot(stem, [{**move, "value": [0.32, 0.8]}], 400)
        moved_legend = next(
            el for el in moved_png["manifest"]["elements"] if el["gid"] == legend["gid"]
        )
        assert moved_legend["anchor"] == pytest.approx([0.32, 0.8], abs=1e-7)
        assert moved_legend["identity"] == legend["identity"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        for prop, value in (("text", "Unchanged"), ("weight", "semibold")):
            with pytest.raises(pool.WorkerError, match="text style is unavailable"):
                session.override(
                    stem, [{"gid": "plot.title", "prop": prop, "value": value}], inline_svg=True
                )
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
  ggplot2::scale_colour_discrete(labels = c("Four wheel drive", "Front wheel drive",
                                           "Rear wheel drive")) +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", subtitle = "Comparing drive configurations",
                 caption = "Source: ggplot2 mpg", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"),
                 legend.text = ggplot2::element_text(colour = "#294052"))
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
        timeout=_COLD_RENDER_TIMEOUT_SECONDS,
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
        assert elements[LEGEND_ENTRY]["role"] == "legend_text"
        assert elements[LEGEND_ENTRY]["label"] == "Front wheel drive"
        assert elements[FACET]["role"] == "text"
        assert elements[FACET]["label"] == "Cylinder 8"
        assert elements[SUBTITLE]["role"] == elements[CAPTION]["role"] == "text"
        assert elements[SUBTITLE]["label"] == "Comparing drive configurations"
        assert elements[CAPTION]["label"] == "Source: ggplot2 mpg"
        expected = {
            "plot.title": "#6C3799",
            SUBTITLE: "#B04964",
            CAPTION: "#2B7984",
            X_BOTTOM: "#8C3011",
            Y_LEFT: "#2A7654",
            LEGEND: "#2370B1",
            LEGEND_ENTRY: "#E058A9",
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
            if gid == LEGEND_BOX:
                assert element["editable"] == []
                continue
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
        entry_patch = next(patch for patch in patches if patch["gid"] == LEGEND_ENTRY)
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                descriptor["stem"],
                [{key: value for key, value in entry_patch.items() if key != "identity"}],
                inline_svg=True,
            )
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        without_entry = render([patch for patch in patches if patch["gid"] != LEGEND_ENTRY])["svg"]
        assert "#E058A9" not in without_entry and "#2370B1" in without_entry
        without_caption = render([patch for patch in patches if patch["gid"] != CAPTION])["svg"]
        assert "#2B7984" not in without_caption and "#B04964" in without_caption
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

        legend = elements[LEGEND_BOX]
        assert (legend["role"], legend["draggable"], legend["drag_prop"]) == (
            "legend",
            True,
            "loc_frac",
        )
        move = {
            "gid": LEGEND_BOX,
            "prop": "loc_frac",
            "value": [0.24, 0.6],
            "identity": legend["identity"],
        }
        prior_svg = svg
        moved = render([*patches, move])
        by_id = {element["gid"]: element for element in moved["manifest"]["elements"]}
        assert by_id[LEGEND_BOX]["anchor"] == pytest.approx(move["value"], abs=1e-7)
        assert by_id[LEGEND_BOX]["bbox"][2:] == pytest.approx(legend["bbox"][2:])
        assert by_id[LEGEND_ENTRY]["drag_host"] == LEGEND_BOX
        assert by_id[FACET]["bbox"] == elements[FACET]["bbox"]
        dx, dy = [end - start for end, start in zip(move["value"], legend["anchor"], strict=True)]
        for gid in (LEGEND, LEGEND_ENTRY):
            x, y, w, h = elements[gid]["bbox"]
            assert by_id[gid]["bbox"] == pytest.approx([x + dx, y + dy, w, h], abs=1e-7)

        svg = moved["svg"]
        ns = "{http://www.w3.org/2000/svg}"
        old_group = ElementTree.fromstring(prior_svg).find(f".//{ns}g[@id='{LEGEND_BOX}']")
        group = ElementTree.fromstring(svg).find(f".//{ns}g[@id='{LEGEND_BOX}']")
        assert old_group is not None and group is not None
        old_labels = {text.text: text for text in old_group.iter(ns + "text")}
        assert set(old_labels) >= {"Drive type", "Front wheel drive", "Rear wheel drive"}
        assert {text.text for text in group.iter(ns + "text")} == set(old_labels)
        for text in group.iter(ns + "text"):
            previous = old_labels[text.text]
            assert float(text.attrib["x"]) - float(previous.attrib["x"]) == pytest.approx(
                dx * 432, abs=0.02
            )
            assert float(text.attrib["y"]) - float(previous.attrib["y"]) == pytest.approx(
                dy * 288, abs=0.02
            )
        assert render(patches)["svg"] == prior_svg
        patches.append(move)
        assert render(patches)["svg"] == svg

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
        assert colors["Front wheel drive"] == {int(expected[LEGEND_ENTRY][1:], 16)}
        assert colors["Rear wheel drive"] == {0x294052}
        assert colors["Comparing drive configurations"] == {int(expected[SUBTITLE][1:], 16)}
        assert colors["Source: ggplot2 mpg"] == {int(expected[CAPTION][1:], 16)}

        original_script = script.read_text(encoding="utf-8")
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
        # The same GID and label in a renamed guide must reject the old edit.
        script.write_text(
            original_script.replace('"Drive type"', '"Other drive grouping"'), encoding="utf-8"
        )
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state((nativesession.BARRIER,), timeout=20) == nativesession.BARRIER
        assert client.post(f"/api/native/sessions/{sid}/build").status_code == 200
        regrouped = {element["gid"]: element for element in render([])["manifest"]["elements"]}
        assert regrouped[LEGEND_ENTRY]["label"] == elements[LEGEND_ENTRY]["label"]
        assert regrouped[LEGEND_ENTRY]["identity"] != entry_patch["identity"]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(descriptor["stem"], [entry_patch], inline_svg=True)
        with pytest.raises(pool.WorkerError, match="legend changed"):
            session.override(descriptor["stem"], [move], inline_svg=True)
        assert session.cancel_owned().confirmed
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
        for endpoint in ("/api/figure-preview", "/api/documents/v4/figure-preview"):
            preview = client.post(endpoint, json=archive)
            assert preview.status_code == 200, preview.get_json()
            assert preview.get_json()["svg"] == svg
            mismatch = client.post(endpoint, json={**archive, "patches": []})
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
        inspected = client.get("/api/svg/chart/inspect?id=chart.svg")
        assert inspected.status_code == 200, inspected.get_json()
        binding = client.post(
            "/api/svg/chart/bind",
            json={
                **BIND_REQUEST,
                "file": "chart.svg",
                "scene_digest": inspected.json["scene_digest"],
            },
        )
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
