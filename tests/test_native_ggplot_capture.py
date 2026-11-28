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
import traceback
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
    layoutannotate,
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
        "`signal 📈` <- ggplot2::ggplot(d, ggplot2::aes(x, y))\r\n"
        "`signal 📈` <- `signal 📈` + ggplot2::geom_point() +\r\n"
        '  ggplot2::labs(title = "Signal", x = "Input")  # Keep\r\n'
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

    def run(operation, *, rejected=None, **extra):
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
        if rejected is not None:
            assert proc.returncode != 0 and rejected in proc.stderr
            return None
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
    before = (stage / "standalone.pdf").read_bytes()
    interleaved = source.replace(
        "`signal 📈` <- `signal 📈`".encode(),
        "print(`signal 📈`)\r\n`signal 📈` <- `signal 📈`".encode(),
    )
    assert interleaved != source
    staged.write_bytes(interleaved)
    result.unlink()
    run("anchor", rejected=b"consecutive sequence")
    assert not result.exists() and (stage / "standalone.pdf").read_bytes() == before
    assert original.read_bytes() == source


@pytest.mark.parametrize("cairo", [False, True], ids=["base-pdf", "cairo"])
def test_owned_r_source_export_checks_the_captured_scene(tmp_path, monkeypatch, cairo):
    from tavotto import app as service, security

    rscript = _rscript_with_capture_packages()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "local data"))
    monkeypatch.setenv("TAVOTTO_LAYOUT_DIR_WRITER", "1")
    problems = []
    replay = nativeggplot_source.replay

    def diagnose(*args, **kwargs):
        try:
            return replay(*args, **kwargs)
        except Exception as exc:
            problems.extend(traceback.format_exception(exc))
            if isinstance(exc.__cause__, winappcontainer.ReplayFailed):
                print("Protected R diagnostic for the test fixture:", exc.__cause__.diagnostic)
            raise

    monkeypatch.setattr(nativeggplot_source, "replay", diagnose)
    verify = service._verified_r_source

    def diagnose_verification(*args, **kwargs):
        try:
            return verify(*args, **kwargs)
        except service.engine_scriptadjust.ScriptAdjustError as exc:
            if exc.code == "script_export_diverged":
                problems.extend(traceback.format_exception(exc))
            raise

    monkeypatch.setattr(service, "_verified_r_source", diagnose_verification)

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
if (nzchar(Sys.getenv("TAVOTTO_R_REPLAY_ROOT"))) {
  inside <- normalizePath("../numbers.csv", winslash = "/", mustWork = TRUE)
  stopifnot(file.exists(inside), endsWith(inside, "/project/numbers.csv"))
  missing <- try(normalizePath("absent.csv", mustWork = TRUE), silent = TRUE)
  stopifnot(inherits(missing, "try-error"))
}
port <- Sys.getenv("TAVOTTO_R_REPLAY_PROBE_PORT")
if (nzchar(port)) {
  network <- try(suppressWarnings(socketConnection("127.0.0.1", as.integer(port),
                 blocking = TRUE, timeout = 2)), silent = TRUE)
  if (!inherits(network, "try-error")) stop("The protected replay reached a local server.")
}
d <- read.csv("../numbers.csv")
p <- ggplot2::ggplot(d, ggplot2::aes(x, y))
p <- p + ggplot2::geom_point() + ggplot2::labs(title = "Signal", x = "Input") +
  ggplot2::theme(axis.title.x.bottom = ggplot2::element_text(face = "italic"),
                 axis.text.x.bottom = ggplot2::element_text(face = "italic"))
ggplot2::ggsave("submitted.pdf", p, width = 4, height = 3)
"""
    if cairo:
        original = original.replace(b"height = 3)", b"height = 3, device = grDevices::cairo_pdf)")
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
            ("axis.x.bottom.ticks", "weight", "bold"),
            ("axis.x.bottom.ticks", "rotation", 35),
            ("axis.x.bottom.ticks", "fontfamily", "monospace"),
            ("axis.x.bottom.title", "weight", "bold"),
            ("axis.x.bottom.title", "fontsize", 18.5),
            ("axis.x.bottom.title", "fontfamily", "serif"),
        ]
        patches = [
            {"gid": gid, "identity": elements[gid]["identity"], "prop": prop, "value": value}
            for gid, prop, value in edits
        ]
        styled = client.post("/api/engine/render", json={"id": figure, "patches": patches})
        assert styled.status_code == 200 and not styled.json["warnings"], styled.json
        families = {
            item["gid"]: field["resolved_family"]
            for item in styled.json["manifest"]["elements"]
            for field in item["editable"]
            if field["prop"] == "fontfamily" and "resolved_family" in field
        }
        assert set(families) == {"axis.x.bottom.title", "axis.x.bottom.ticks"}
        assert len(set(families.values())) == 2
        residual = [] if cairo else [patch for patch in patches if patch["prop"] == "fontfamily"]
        remaining = [
            {"gid": p["gid"], "prop": p["prop"], "reason": "unsupported"} for p in residual
        ]
        written = len(patches) - len(residual)
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
        problems.clear()
        exported = client.post("/api/export", json=request)
        assert exported.status_code == 200, (exported.get_json(), "".join(problems))
        (output,) = exported.json["outputs"]
        assert output["name"] == "Signal_edited.R"
        assert output["script_report"] == {"written": written, "skipped": remaining}
        edited = Path(exported.json["export_dir"]) / output["name"]
        assert b'colour = "#572B82"' in edited.read_bytes()
        tick_family = json.dumps(families["axis.x.bottom.ticks"], ensure_ascii=False)
        title_family = json.dumps(families["axis.x.bottom.title"], ensure_ascii=False)
        candidate = edited.read_text(encoding="utf-8")
        tick_style = (
            'axis.text.x.bottom = ggplot2::element_text(colour = "#A64184", size = 12.5, '
            'face = "bold.italic", angle = 35'
        )
        title_style = (
            'axis.title.x.bottom = ggplot2::element_text(face = "bold.italic", size = 18.5'
        )
        assert tick_style + (f", family = {tick_family})" if cairo else ")") in candidate
        assert title_style + (f", family = {title_family})" if cairo else ")") in candidate
        assert b'face = "bold.italic"' in edited.read_bytes()
        assert b"size = 18.5" in edited.read_bytes()
        assert protected.read_bytes() == b"external file"
        assert source.read_bytes() == original and submitted.read_bytes() == original_pdf
        assert client.post("/api/export/validate", json=request).json["conflicts"] == [edited.name]

        without_title_resize = [
            patch for patch in patches if patch["gid"] in ("plot.title", "axis.x.bottom.ticks")
        ]
        tick_request = {
            **request,
            "filename": "Tick styles",
            "original": {"figure_id": figure, "overrides": without_title_resize},
        }
        tick_export = client.post("/api/export", json=tick_request)
        assert tick_export.status_code == 200, tick_export.get_json()
        assert tick_export.json["outputs"][0]["script_report"] == {
            "written": 6 if cairo else 5,
            "skipped": [item for item in remaining if item["gid"] == "axis.x.bottom.ticks"],
        }

        tick_face_only = [
            patch
            for patch in patches
            if patch["gid"] == "axis.x.bottom.ticks" and patch["prop"] == "weight"
        ]
        tick_face = client.post(
            "/api/engine/render", json={"id": figure, "patches": tick_face_only}
        )
        assert tick_face.status_code == 200, tick_face.json
        tick = next(
            element
            for element in tick_face.json["manifest"]["elements"]
            if element["gid"] == "axis.x.bottom.ticks"
        )
        fields = {field["prop"]: field for field in tick["editable"]}
        assert (
            fields["weight"]["value"] == "bold" and fields["weight"]["value_original"] == "normal"
        )
        assert fields["style"]["value"] == "italic"

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
        doc_id = "d_native_r_writeback"
        slot_url = f"/api/autosave/{doc_id}"
        saved = client.put(
            slot_url, query_string={"project_write": '{"name":"Source_style"}'}, json=layout
        )
        assert saved.status_code == 200, saved.json
        project_layout = saved.json["project_layout"]
        assert project_layout["state"] == "saved" and project_layout["name"] == "Source_style"
        assert "signal" in saved.json["figure_snapshots"]
        preview = client.post(
            "/api/script-edit/figure-style/preview",
            json={
                "layout": "Source_style",
                "revision": project_layout["revision"],
                "canvas_id": "main",
                "panel_id": "signal",
                "working_doc_id": doc_id,
            },
        )
        assert preview.status_code == 200, preview.json
        assert preview.json["written"] == written and preview.json["skipped"] == remaining
        commit = "/api/script-edit/figure-style/commit"
        token = preview.json["token"]
        refused = client.post(commit, json={"token": token})
        assert refused.status_code == 409 and source.read_bytes() == original
        applied = client.post(commit, json={"token": token, "confirmed": True})
        assert applied.status_code == 200, applied.json
        assert source.read_bytes() == edited.read_bytes()
        assert submitted.read_bytes() == original_pdf and protected.read_bytes() == b"external file"
        current = client.get(layout_url).json["canvases"][0]["objects"][0]
        buffered = client.get(slot_url)
        assert buffered.headers["X-Tavotto-Revision"] == applied.json["working_revision"]
        assert buffered.json["canvases"][0]["objects"][0] == current
        assert client.get(layout_url).headers["X-Tavotto-Revision"] == applied.json["revision"]
        manifest = json.loads(
            (root / "tavottofile/Source_style.tavotto/package_manifest.json").read_bytes()
        )
        assert (
            manifest["doc_id"] == doc_id and manifest["layout_revision"] == applied.json["revision"]
        )
        assert current["overrides"] == residual
        assert current["figureSnapshot"] != saved.json["figure_snapshots"]["signal"]
        archived = client.post(
            "/api/figure-preview",
            json={
                "id": figure,
                "patches": residual,
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
        invalidated_at = committed_layout.get("updatedAt", 0) + 1
        closing = client.put(
            slot_url,
            query_string={
                "base_revision": applied.json["working_revision"],
                "project_write": json.dumps(
                    {"name": "Source_style", "base": applied.json["revision"]}
                ),
            },
            json={**committed_layout, "updatedAt": invalidated_at},
        )
        assert closing.status_code == 200, closing.json
        assert closing.json["project_layout"]["state"] == "saved"
        # A close-time autosave of identical content keeps the project's revision.
        assert closing.json["project_layout"]["revision"] == applied.json["revision"]
        assert client.get(slot_url).json["updatedAt"] == invalidated_at
        restored = client.post(
            "/api/script-backups/restore",
            json={
                "backup_id": applied.json["backup_id"],
                "mode": "full",
                "expected_sha256": applied.json["script_revision"],
                "working_doc_id": doc_id,
            },
        )
        assert restored.status_code == 200, restored.json
        assert source.read_bytes() == original
        previous = client.get(layout_url).json["canvases"][0]["objects"][0]
        buffered = client.get(slot_url)
        assert buffered.headers["X-Tavotto-Revision"] == restored.json["layout"]["working_revision"]
        assert buffered.json["canvases"][0]["objects"][0] == previous
        assert previous["overrides"] == patches
        assert previous["figureSnapshot"] == saved.json["figure_snapshots"]["signal"]
    finally:
        listener.close()
        if worker is not None:
            worker.cancel_owned()
        service.reset_projects()


@pytest.mark.parametrize(
    "written_scale",
    [None, "log", "linear", "logit", "symlog"],
    ids=["write-post-stat-log", "write-log", "write-linear", "write-logit", "write-symlog"],
)
def test_native_r_coordinate_writeback_keeps_independent_edits_bound(tmp_path, written_scale):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Coordinate project"
    project.mkdir()
    script = project / "View.R"
    original = b"""\
d <- expand.grid(x = c(1, 8, 64), panel = c("Early", "Late"))
d$y <- 3 + log2(d$x) + 7 * as.integer(d$panel)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::stat_summary(geom = "point", fun = mean) +
  ggplot2::facet_wrap(ggplot2::vars(panel), scales = "free_y") +
  ggplot2::coord_cartesian(ylim = c(1, 30), expand = c(top = FALSE)) +
  ggplot2::labs(title = "Scope")
ggplot2::ggsave("original.pdf", plot, width = 4, height = 3)
"""
    if written_scale:
        original = original.replace(
            b'ggplot2::stat_summary(geom = "point", fun = mean)', b"ggplot2::geom_point()"
        )
    if written_scale == "linear":
        original = original.replace(b'ggplot2::labs(title = "Scope")', b"ggplot2::scale_x_log10()")
    if written_scale == "logit":
        original = original.replace(b"c(1, 8, 64)", b"c(.125, .25, .75)")
    if written_scale == "symlog":
        original = original.replace(b"c(1, 8, 64)", b"c(-64, -8, 0, 8, 64)")
        original = original.replace(b"log2(d$x)", b"log2(1 + abs(d$x))")
    script.write_bytes(original)
    requested_scale = written_scale or "log"
    requested_limits = {"linear": [-1, 64], "logit": [0.05, 0.95], "symlog": [-64, 64]}.get(
        written_scale, [1, 64]
    )

    def axes(manifest):
        return {e["gid"]: e for e in manifest["elements"] if e["role"] == "axes"}

    def values(targets):
        return {
            gid: {field["prop"]: field["value"] for field in elem["editable"]}
            for gid, elem in targets.items()
        }

    route = "/api/layouts/Coordinate_view"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        target = axes(before["manifest"])["axes_2"]
        original_pdf = (project / "original.pdf").read_bytes()
        edits = [("xlim", requested_limits), ("invert_x", True), ("xscale", requested_scale)]
        if not written_scale:
            edits.append(("invert_y", True))
        patches = [
            {"gid": target["gid"], "prop": prop, "value": value, "identity": target["identity"]}
            for prop, value in edits
        ]
        hot = _render(app, figure, patches)
        assert not hot["warnings"]
        w, h = before["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 5, patches, native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "source-coordinate", "name": "Coordinate view"},
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
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Coordinate_view")
        _, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert preview["written"] == len(patches) and preview["skipped"] == []
        assert script.read_bytes() == original
        _, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert saved["ok"]
        assert (project / "original.pdf").read_bytes() == original_pdf
        reauthored = script.read_bytes()
        assert reauthored != original
        _, persisted = app.call(route)
        persisted_panel = persisted["canvases"][0]["objects"][0]
        assert persisted_panel["overrides"] == []

        replay_root = tmp_path / "Fresh runtime"
        replay_root.mkdir()
        (replay_root / script.name).write_bytes(reauthored)
        with sidecar_app(replay_root, tmp_path / "fresh") as fresh:
            rebuilt = _run_ggplot(fresh, script.name, rscript)
            bare = _render(fresh, rebuilt, [])
            standalone = values(axes(bare["manifest"]))
            assert all(
                el["xlim"] == requested_limits
                and el["invert_x"]
                and el["xscale"] == requested_scale
                for el in standalone.values()
            )
            assert standalone["axes_1"]["invert_y"] is False
            assert standalone["axes_2"]["invert_y"] is (written_scale is None)
            assert not bare["warnings"] and bare["svg"] == hot["svg"]

        _, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "mode": "full",
                "expected_sha256": saved["script_revision"],
            },
            timeout=120,
        )
        assert restored["ok"]
        assert script.read_bytes() == original
        assert (project / "original.pdf").read_bytes() == original_pdf
        _, previous = app.call(route)
        assert previous["canvases"][0]["objects"][0]["overrides"] == patches


@pytest.mark.parametrize("scope", ["single", "facets", "existing", "empty", "independent"])
def test_native_r_raster_writeback_draws_the_same_cells_in_standalone_r(tmp_path, scope):
    from test_core_v02_release_candidate import R_FRAME_PT, _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Raster project"
    project.mkdir()
    script = project / "Raster.R"
    original = b"""\
d <- expand.grid(x = 1:3, y = 1:2, sample = c("Early", "Late"))
d$x <- d$x + ifelse(d$sample == "Early", 0, 3)
d$colour <- rep(c("#D60000", "#059355", "#183ED0", "#6A1692", "#EA5303", "#6EE7D7"), 2)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, fill = colour)) +
  ggplot2::geom_raster(hjust = .25, vjust = .75) + ggplot2::scale_fill_identity() +
  ggplot2::geom_point(colour = "#40272A") + ggplot2::theme_bw() +
  ggplot2::coord_cartesian(xlim = c(0, 10), ylim = c(0, 4), expand = FALSE)
"""
    if scope == "single":
        original = original.replace(b"ggplot(d,", b'ggplot(subset(d, sample == "Early"),')
    else:
        original = original.replace(
            b"ggplot2::theme_bw()", b"ggplot2::theme_bw() + ggplot2::facet_wrap(~sample)"
        )
    if scope == "existing":
        original = original.replace(b"expand = FALSE)", b'expand = FALSE, reverse = "xy")')
    if scope == "independent":
        original = original.replace(
            b"facet_wrap(~sample)", b'facet_wrap(~sample, scales = "free_x")'
        )
    if scope == "empty":
        original = original.replace(
            b"ggplot2::geom_point(colour",
            b"ggplot2::geom_raster(data = function(d) d[FALSE, ]) + ggplot2::geom_point(colour",
        )
    original += (
        f'svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)\n'
        "print(plot)\ngrDevices::dev.off()\n"
    ).encode()
    script.write_bytes(original)

    def rasters(svg):
        return [
            node.attrib
            for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}image")
        ]

    route = "/api/layouts/Raster_view"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        axes = next(el for el in before["manifest"]["elements"] if el["role"] == "axes")
        patches = [
            {"gid": axes["gid"], "identity": axes["identity"], "prop": prop, "value": value}
            for prop, value in (("xlim", [0.0, 8.0]), ("invert_x", scope != "existing"))
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        images = rasters(hot["svg"])
        assert len(images) == (1 if scope == "single" else 2)
        assert images != rasters(before["svg"])
        if scope == "independent":
            href = "{http://www.w3.org/1999/xlink}href"
            originals = rasters(before["svg"])
            assert images[0][href] != originals[0][href]
            assert images[1][href] == originals[1][href]
        width, height = before["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        doc = {
            "schema": 3,
            "project": {"id": "r-raster-source", "name": "Raster view"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                },
            ],
        }
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Raster_view")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == 2 and preview["skipped"] == [], preview
        assert script.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        _, persisted = app.call(route)
        assert persisted["canvases"][0]["objects"][0]["overrides"] == []
        authored = script.read_bytes()
        assert b"TavottoRasterReflection" in authored
        fresh_project = tmp_path / "Fresh raster project"
        fresh_project.mkdir()
        (fresh_project / script.name).write_bytes(authored)

        # Exercise ordinary R directly, so the editor's automatic reflection cannot
        # mask a generated script that points one of the colour cells at the wrong data.
        subprocess.run(
            [rscript, "--vanilla", script.name], check=True, cwd=fresh_project, timeout=45
        )
        assert rasters((fresh_project / "standalone.svg").read_text(encoding="utf-8")) == images

        with sidecar_app(fresh_project, tmp_path / "fresh") as fresh:
            rebuilt = _run_ggplot(fresh, script.name, rscript)
            replayed = _render(fresh, rebuilt, [])
            assert replayed["warnings"] == []
            assert replayed["svg"] == hot["svg"]
            if scope == "independent":
                axis_fields = [
                    {field["prop"]: field["value"] for field in obj["editable"]}
                    for obj in replayed["manifest"]["elements"]
                    if obj["role"] == "axes"
                ]
                assert [axis["invert_x"] for axis in axis_fields] == [True, False]
                assert [axis["xlim"] for axis in axis_fields] == [[0, 8], [0, 10]]

            def x_tick_controls(manifest):
                return {
                    e["gid"]
                    for e in manifest["elements"]
                    if e["role"] == "ticks"
                    and e["gid"].startswith("axis.x.bottom.ticks")
                    and any(field["prop"] == "major_mode" for field in e["editable"])
                }

            assert x_tick_controls(before["manifest"])
            assert x_tick_controls(replayed["manifest"]) == x_tick_controls(before["manifest"])
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"], restored
        assert script.read_bytes() == original


@pytest.mark.parametrize("write_category", [False, True], ids=["scale-only", "with-bar-width"])
def test_native_r_scale_writes_preserve_automatic_order_of_other_guides(tmp_path, write_category):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Two guides"
    project.mkdir()
    script = project / "Both.R"
    original = b"""\
d <- expand.grid(x = 1:4, kind = c("One", "Two"), facet = c("A", "B"))
d$y <- 2 + as.integer(d$kind) + d$x
d$dose <- as.integer(d$kind) * d$x
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_col(ggplot2::aes(fill = kind), position = "dodge", width = .65, alpha = .5) +
  ggplot2::geom_point(ggplot2::aes(colour = dose), size = 3) +
  ggplot2::scale_fill_manual(values = c("#77546A", "#77546A")) +
  ggplot2::scale_colour_gradient(name = "Dose", low = "#3099DC", high = "#975600") +
  ggplot2::facet_wrap(~facet) + ggplot2::theme_bw()
ggplot2::ggsave("original.pdf", plot, width = 4, height = 3, device = grDevices::cairo_pdf)
"""
    script.write_bytes(original)
    route = "/api/layouts/Two_guides"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        elements = baseline["manifest"]["elements"]
        (scale,) = [element for element in elements if element["role"] == "colorbar"]
        groups = [e for e in elements if e["role"] == "bar_series" and ".group." in e["gid"]]
        assert len(groups) == 4
        edits = [(scale, "vmin", 2), (scale, "vmax", 7), (scale, "cmap", "plasma")]
        if write_category:
            edits.append((groups[-2], "bar_width", 0.44))
        patches = [
            {"gid": el["gid"], "identity": el["identity"], "prop": prop, "value": value}
            for el, prop, value in edits
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        width, height = baseline["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        document = {
            "schema": 3,
            "project": {"id": "two-guides", "name": "Two guides"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                },
            ],
        }
        assert app.call(f"{route}?target=project", document)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Two_guides")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == len(patches) and preview["skipped"] == [], (
            preview
        )
        assert script.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        authored = script.read_bytes()
        assert b"ggplot_preserve_guide_order" in authored
        assert (b'category = "One"' in authored) is write_category

        independent = tmp_path / "Independent R"
        independent.mkdir()
        (independent / script.name).write_bytes(authored)
        with sidecar_app(independent, tmp_path / "replay") as fresh:
            rebuilt = _run_ggplot(fresh, script.name, rscript)
            actual = _render(fresh, rebuilt, [])
            assert actual["warnings"] == [] and actual["svg"] == hot["svg"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == original


@pytest.mark.parametrize("retain_tick_format", [False, True], ids=["write", "retain-format"])
def test_native_r_colour_mapping_writeback_recreates_data_and_guides(tmp_path, retain_tick_format):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Colour project"
    project.mkdir()
    script = project / "Colour.R"
    original = b"""\
d <- expand.grid(x = 1:7, y = 1:2)
d$value <- c(1, 2, 8, 16, 64, 128, NA)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_tile(ggplot2::aes(fill = value)) +
  ggplot2::geom_point(ggplot2::aes(colour = value), size = 4) +
  ggplot2::scale_fill_gradient(transform = "log10", na.value = "#CC8844") +
  ggplot2::guides(
    colour = ggplot2::guide_colourbar(title = "Exposure", position = "right"),
    fill = ggplot2::guide_colourbar(title = "Ground", position = "left")
  ) + ggplot2::labs(title = "Colours")
ggplot2::ggsave("original.pdf", plot, width = 5, height = 3)
"""
    script.write_bytes(original)
    route = "/api/layouts/Colour_map"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        original_pdf = (project / "original.pdf").read_bytes()
        elements = {element["gid"]: element for element in before["manifest"]["elements"]}
        edits = [
            ("legend.right.1.colourbar", "vmin", 2),
            ("legend.right.1.colourbar", "vmax", 64),
            ("legend.right.1.colourbar", "cmap", "plasma"),
            ("legend.left.1.colourbar", "vmin", 2),
            ("legend.left.1.colourbar", "vmax", 64),
            ("legend.left.1.colourbar", "cmap", "viridis"),
        ]
        if retain_tick_format:
            edits.append(("legend.left.1.colourbar.ticks", "format", "%.2f"))
        edits.append(("plot.title", "color", "#543380"))
        patches = [
            {"gid": gid, "prop": prop, "value": value, "identity": elements[gid]["identity"]}
            for gid, prop, value in edits
        ]
        hot = _render(app, figure, patches)
        assert not hot["warnings"]
        assert before["svg"] != hot["svg"]
        for color in ("#0D0887", "#F0F921", "#440154", "#FDE725", "#CC8844"):
            assert color in hot["svg"]
        w, h = hot["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 5, patches, native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "colour-source", "name": "Colour map"},
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
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Colour_map")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200, preview
        expected_residual = patches[-2:-1] if retain_tick_format else []
        assert preview["written"] == len(patches) - len(expected_residual)
        assert len(preview["skipped"]) == len(expected_residual)
        assert script.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        assert (project / "original.pdf").read_bytes() == original_pdf
        authored = script.read_bytes()
        assert authored != original
        _, persisted = app.call(route)
        residual = persisted["canvases"][0]["objects"][0]["overrides"]
        assert [
            {key: value for key, value in patch.items() if key != "identity"} for patch in residual
        ] == [
            {key: value for key, value in patch.items() if key != "identity"}
            for patch in expected_residual
        ]
        if retain_tick_format:
            assert residual[0]["identity"] != expected_residual[0]["identity"]

        replay_root = tmp_path / "Fresh runtime"
        replay_root.mkdir()
        (replay_root / script.name).write_bytes(authored)
        with sidecar_app(replay_root, tmp_path / "fresh") as fresh:
            rebuilt = _run_ggplot(fresh, script.name, rscript)
            cold = _render(fresh, rebuilt, residual)
            assert not cold["warnings"]
            assert cold["svg"] == hot["svg"]

        _, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "mode": "full",
                "expected_sha256": saved["script_revision"],
            },
            timeout=120,
        )
        assert restored["ok"]
        assert script.read_bytes() == original
        assert (project / "original.pdf").read_bytes() == original_pdf
        _, previous = app.call(route)
        assert previous["canvases"][0]["objects"][0]["overrides"] == patches


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


@pytest.mark.parametrize("scope", ["single", "both-facets", "one-facet", "different-facets"])
def test_native_r_panel_theme_writeback_matches_the_edited_scope(tmp_path, scope):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Grid project"
    project.mkdir()
    source = project / "Grid.R"
    original = b"""\
d <- expand.grid(x = 1:4, panel = c("Reference", "Trial"))
d$y <- 2 + d$x + as.integer(d$panel)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
    ggplot2::theme_bw() + ggplot2::theme(
      panel.background = ggplot2::element_rect(fill = "#DEDAE9", colour = "#687C9CFF", linewidth = .4),
      panel.border = ggplot2::element_rect(fill = NA, colour = "#3C6C9A80", linewidth = .6),
      panel.grid.major.y = ggplot2::element_blank(),
      panel.grid.minor.x = ggplot2::element_line(colour = "#54447860", linewidth = .15),
      panel.grid.minor.y = ggplot2::element_blank()) + ggplot2::labs(title = "Grid evidence")
ggplot2::ggsave("original.pdf", plot, width = 4, height = 3, device = grDevices::cairo_pdf)
"""
    if scope != "single":
        original = original.replace(
            b'ggplot2::labs(title = "Grid evidence")',
            b'ggplot2::labs(title = "Grid evidence") + ggplot2::facet_wrap(ggplot2::vars(panel), scales = "free_y")',
        )
    source.write_bytes(original)
    grid_edits = {
        "grid_x": False,
        "grid_y": True,
        "grid_color": "#552288",
        "grid_alpha": 0.45,
        "grid_linestyle": "--",
        "grid_linewidth": 1.4,
        "facecolor": "#E5E4F0",
        "spine_color": "#386070",
        "spine_linewidth": 1.6,
    }
    axes = {
        "single": ["axes_0"],
        "both-facets": ["axes_1", "axes_2"],
        "different-facets": ["axes_1", "axes_2"],
        "one-facet": ["axes_2"],
    }[scope]
    route = "/api/layouts/Grid_style"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, source.name, rscript)
        before = _render(app, figure, [])
        original_pdf = (project / "original.pdf").read_bytes()
        elements = {element["gid"]: element for element in before["manifest"]["elements"]}
        patches = [
            {"gid": gid, "prop": prop, "value": value, "identity": elements[gid]["identity"]}
            for gid in axes
            for prop, value in grid_edits.items()
        ]
        if scope == "different-facets":
            left_grid = next(
                p for p in patches if p["gid"] == "axes_1" and p["prop"] == "grid_color"
            )
            left_grid["value"] = "#442255"
        patches.append(
            {
                "gid": "plot.title",
                "prop": "color",
                "value": "#543380",
                "identity": elements["plot.title"]["identity"],
            }
        )
        hot = _render(app, figure, patches)
        assert not hot["warnings"]
        w, h = hot["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 5, patches, native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "grid-source", "name": "Grid style"},
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
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Grid_style")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200, preview
        assert preview["written"] == len(patches)
        assert preview["skipped"] == []
        assert source.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {
                "token": preview["token"],
                "confirmed": True,
            },
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        assert (project / "original.pdf").read_bytes() == original_pdf
        authored = source.read_bytes()
        assert authored != original
        _, persisted = app.call(route)
        retained = persisted["canvases"][0]["objects"][0]["overrides"]
        assert retained == []
        fresh_project = tmp_path / "fresh-project"
        fresh_project.mkdir()
        (fresh_project / source.name).write_bytes(authored)
        with sidecar_app(fresh_project, tmp_path / "fresh") as fresh:
            rebuilt = _run_ggplot(fresh, source.name, rscript)
            replayed = _render(fresh, rebuilt, retained)
            assert replayed["warnings"] == []
            assert replayed["svg"] == hot["svg"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"], restored
        assert source.read_bytes() == original
        assert (project / "original.pdf").read_bytes() == original_pdf


@pytest.mark.parametrize(
    "scope",
    [
        "single",
        "all-facets",
        "one-facet",
        "different-facets",
        "paint-facets",
        "line-all-facets",
        "line-one-facet",
        "path-single",
        "line-legend",
        "line-legend-local",
        "two-layers",
        "mixed-layers",
        "bar-col",
        "bar-col-one-facet",
        "bar-col-missing-facet",
        "bar-count",
        "bar-dodged-horizontal",
        "bar-mapped-facets",
        "bar-mapped-horizontal",
        "interval-facets",
        "interval-local",
        "interval-horizontal",
        "interval-mapped-facets",
        "interval-mapped-single",
        "bar-interval-stack",
    ],
)
def test_native_r_fixed_layer_styles_write_back_to_the_selected_scope(tmp_path, scope):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Point project"
    project.mkdir()
    source = project / "Point.R"
    original = b"""\
d <- expand.grid(x = 1:4, panel = c("Reference", "Trial"))
d$y <- 2 + d$x + as.integer(d$panel)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, colour = x)) +
    ggplot2::geom_point(size = 2, colour = "#287F6E") + ggplot2::theme_bw() +
    ggplot2::labs(title = "Point evidence")
ggplot2::ggsave("original.pdf", plot, width = 4, height = 3, device = grDevices::cairo_pdf)
"""
    curve = scope.startswith(("line-", "path-"))
    bars = scope.startswith("bar-")
    interval = scope.startswith("interval-")
    mapped_interval = scope.startswith("interval-mapped-")
    mapped_bars = scope.startswith("bar-mapped-")
    mapped_geometry = mapped_interval or mapped_bars
    horizontal_bars = scope in ("bar-dodged-horizontal", "bar-mapped-horizontal")
    faceted = scope not in (
        "single",
        "path-single",
        "line-legend",
        "line-legend-local",
        "interval-mapped-single",
    )
    if faceted:
        original = original.replace(
            b'ggplot2::labs(title = "Point evidence")',
            b'ggplot2::labs(title = "Point evidence") + ggplot2::facet_wrap(ggplot2::vars(panel))',
        )
    if scope == "bar-col-missing-facet":
        original = original.replace(b'"Reference", "Trial"', b'"Reference", NA_character_')
        original = original.replace(b"as.integer(d$panel)", b"2L")
    if scope == "paint-facets":
        original = original.replace(
            b'geom_point(size = 2, colour = "#287F6E")',
            b'geom_point(size = 2, shape = 21, fill = "#E3A45280", colour = "#287F6E")',
        )
    if curve:
        geom = (
            b'geom_path(linewidth = .8, colour = "#287F6E99")'
            if scope == "path-single"
            else b'geom_line(linewidth = .8, colour = "#287F6E99")'
        )
        if scope.startswith("line-legend"):
            geom = b"geom_line(ggplot2::aes(colour = panel), linewidth = .8)"
        original = original.replace(b'geom_point(size = 2, colour = "#287F6E")', geom)
    if scope in ("two-layers", "mixed-layers"):
        first = b'ggplot2::geom_point(size = 2, colour = "#287F6E")'
        second = (
            b"ggplot2::geom_point(data = function(d) transform(d, x = x + .3), "
            b'shape = 21, fill = "#E3A452", colour = "#723551", size = 3)'
            if scope == "two-layers"
            else b'ggplot2::geom_line(colour = "#287F6E80", linewidth = .8)'
        )
        original = original.replace(first, first + b" +\n    " + second)
    if bars:
        geom = b'geom_col(width = .6, fill = "#D59162", colour = "#287F6E", linewidth = .8)'
        if scope == "bar-count":
            original = original.replace(b"aes(x, y, colour = x)", b"aes(x)")
            geom = b'geom_bar(width = .6, fill = "#D59162", colour = "#287F6E", linewidth = .8)'
        if scope == "bar-dodged-horizontal":
            original = original.replace(
                b"aes(x, y, colour = x)", b"aes(y, factor(x), fill = panel)"
            )
            original = original.replace(
                b"d$y <- 2 + d$x + as.integer(d$panel)",
                b'd <- merge(d, data.frame(kind = c("A", "B")))\n'
                b"d$y <- 2 + d$x + as.integer(factor(d$panel)) + as.integer(factor(d$kind))",
            ).replace(b"fill = panel", b"fill = kind")
            geom = (
                b'geom_col(width = .8, position = "dodge2", orientation = "y", colour = "#287F6E")'
            )
        original = original.replace(b'geom_point(size = 2, colour = "#287F6E")', geom)
        if mapped_bars:
            original = original.replace(
                b"d$y <- 2 + d$x + as.integer(d$panel)",
                b'd <- merge(d, data.frame(kind = c("First", "Second")))\n'
                b"d$y <- 2 + d$x + as.integer(d$panel) + as.integer(factor(d$kind))",
            )
            aesthetics = (
                b"aes(y, factor(x), fill = kind)" if horizontal_bars else b"aes(x, y, fill = kind)"
            )
            original = original.replace(b"aes(x, y, colour = x)", aesthetics)
            orientation = b', orientation = "y"' if horizontal_bars else b""
            original = original.replace(
                geom,
                b'geom_col(width = .8, position = "dodge2"' + orientation + b") + "
                b'ggplot2::scale_fill_manual(values = c("#287F6E", "#287F6E"))',
            )
        if scope == "bar-interval-stack":
            original = original.replace(
                geom,
                geom + b" +\n    ggplot2::geom_errorbar(ggplot2::aes(ymin=y-.3, ymax=y+.4), "
                b'width=.35, colour="#6F1735", linewidth=.7)',
            )
    if interval:
        if mapped_interval:
            original = original.replace(
                b"d$y <- 2 + d$x + as.integer(d$panel)",
                b'd <- merge(d, data.frame(kind = c("First", "Second")))\n'
                b"d$y <- 2 + d$x + as.integer(d$panel) + as.integer(factor(d$kind))",
            )
            original = original.replace(b"aes(x, y, colour = x)", b"aes(x, y, colour = kind)")
            geom = (
                b"geom_errorbar(ggplot2::aes(ymin = y - .3, ymax = y + .4), "
                b"width = .35, position = ggplot2::position_dodge(.6)) + "
                b'ggplot2::scale_colour_manual(values = c("#287F6E", "#287F6E"))'
            )
        elif scope == "interval-horizontal":
            original = original.replace(b"aes(x, y, colour = x)", b"aes(y, x)")
            geom = (
                b"geom_errorbar(ggplot2::aes(xmin = y - .3, xmax = y + .4), "
                b'width = .35, linewidth = .8, colour = "#287F6E", orientation = "y")'
            )
        else:
            geom = (
                b"geom_errorbar(ggplot2::aes(ymin = y - .3, ymax = y + .4), "
                b'width = .35, linewidth = .8, colour = "#287F6E")'
            )
        original = original.replace(b'geom_point(size = 2, colour = "#287F6E")', geom)
    source.write_bytes(original)
    route = "/api/layouts/Point_style"
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, source.name, rscript)
        before = _render(app, figure, [])
        original_pdf = (project / "original.pdf").read_bytes()
        elements = {element["gid"]: element for element in before["manifest"]["elements"]}
        role = "errorbar" if interval else "line" if curve else "bar_series" if bars else "scatter"
        targets = [
            gid
            for gid, element in elements.items()
            if element["role"] == role and ((".group." in gid) == mapped_geometry)
        ]
        assert len(targets) == (
            4 if scope == "two-layers" else (2 if faceted else 1) * (2 if mapped_geometry else 1)
        )
        if mapped_geometry:
            targets = targets[-2:]
            assert [elements[gid]["label"].split(" · ")[-1] for gid in targets] == [
                "First",
                "Second",
            ]
        if scope in (
            "one-facet",
            "line-one-facet",
            "bar-col-one-facet",
            "bar-col-missing-facet",
            "interval-local",
        ):
            targets = targets[-1:]
        if curve:
            edits = {"linewidth": 1.8, "linestyle": "--"}
            if not scope.startswith("line-legend"):
                edits["color"] = "#613C88"
                edits["alpha"] = 0 if scope == "path-single" else 0.35
        elif bars:
            edits = {
                "facecolor": "#835275",
                "edgecolor": "#174466",
                "linewidth": 2.2,
                "alpha": 0.65,
                "bar_width": 0.41,
            }
            if horizontal_bars:
                edits = {"bar_thickness": 0.25}
            elif mapped_bars:
                edits = {"bar_width": 0.25}
        elif mapped_interval:
            edits = {"capsize": 8, "cap_thickness": 2.1}
        elif interval:
            edits = {
                "linewidth": 1.35,
                "color": "#513C88",
                "alpha": 0.65,
                "capsize": 8,
                "cap_thickness": 2.1,
            }
        else:
            edits = (
                {"size": 110, "facecolor": "none", "edgecolor": "#1E2F80", "linewidth": 2.2}
                if scope == "paint-facets"
                else {"size": 100, "marker": "s", "alpha": 0.45}
            )
        if scope == "two-layers":
            edits = {"size": 100, "alpha": 0.45}
        patches = [
            {"gid": gid, "prop": prop, "value": value, "identity": elements[gid]["identity"]}
            for gid in targets
            for prop, value in edits.items()
        ]
        if scope == "bar-dodged-horizontal" or mapped_bars:
            patches[0]["value"] = 0.32
        if scope == "interval-horizontal" or mapped_interval:
            next(patch for patch in patches if patch["prop"] == "capsize")["value"] = 0
        if scope == "different-facets":
            next(patch for patch in patches if patch["prop"] == "size")["value"] = 81
        if scope == "mixed-layers":
            patches += [
                {"gid": gid, "prop": prop, "value": value, "identity": el["identity"]}
                for gid, el in elements.items()
                if el["role"] == "line"
                for prop, value in {"linewidth": 1.8, "linestyle": "--", "color": "#613C88"}.items()
            ]
        if scope == "bar-interval-stack":
            patches.extend(
                {"gid": gid, "prop": prop, "value": value, "identity": element["identity"]}
                for gid, element in elements.items()
                if element["role"] == "errorbar"
                for prop, value in {"capsize": 8, "cap_thickness": 2.1}.items()
            )
        if scope == "line-legend-local":
            group = next(element for gid, element in elements.items() if ".line.group." in gid)
            patches.append(
                {
                    "gid": group["gid"],
                    "prop": "linewidth",
                    "value": 2.1,
                    "identity": group["identity"],
                }
            )
        title = {
            "gid": "plot.title",
            "prop": "color",
            "value": "#543380",
            "identity": elements["plot.title"]["identity"],
        }
        hot = _render(app, figure, [*patches, title])
        assert not hot["warnings"]
        w, h = hot["manifest"]["size_mm"]
        panel = {**_panel("r", figure, 5, [*patches, title], native_width=w), "h": h, "nativeH": h}
        doc = {
            "schema": 3,
            "project": {"id": "point-source", "name": "Point style"},
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
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Point_style")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200, preview
        expected_residual = patches if scope == "line-legend-local" else []
        assert preview["written"] == len(patches) + 1 - len(expected_residual)
        assert len(preview["skipped"]) == len(expected_residual)
        assert source.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        assert (project / "original.pdf").read_bytes() == original_pdf
        authored = source.read_bytes()
        if scope == "bar-col-missing-facet":
            assert b'facet = list("panel" = NA_character_)' in authored
        if mapped_geometry:
            assert b'category = "First"' in authored and b'category = "Second"' in authored
        assert (b"plot$layers[[1L]]" in authored) == (not expected_residual or bars or interval)
        assert (b"plot$layers[[2L]]" in authored) == (
            scope in ("two-layers", "mixed-layers", "bar-interval-stack")
        )
        _, persisted = app.call(route)
        retained = persisted["canvases"][0]["objects"][0]["overrides"]
        assert retained == expected_residual
        fresh_project = tmp_path / "fresh-project"
        fresh_project.mkdir()
        (fresh_project / source.name).write_bytes(authored)
        with sidecar_app(fresh_project, tmp_path / "fresh") as fresh:
            rebuilt = _run_ggplot(fresh, source.name, rscript)
            replayed = _render(fresh, rebuilt, retained)
            assert replayed["warnings"] == []
            assert replayed["svg"] == hot["svg"]
            if bars:
                size_prop = "bar_thickness" if horizontal_bars else "bar_width"
                after_elements = {e["gid"]: e for e in replayed["manifest"]["elements"]}
                expected_widths = {
                    gid: next(f["value"] for f in element["editable"] if f["prop"] == size_prop)
                    for gid, element in elements.items()
                    if element["role"] == role and ((".group." in gid) == mapped_bars)
                }
                expected_widths.update(
                    (patch["gid"], patch["value"])
                    for patch in patches
                    if patch["prop"] == size_prop
                )
                for gid, width in expected_widths.items():
                    measured = next(
                        f["value"]
                        for f in after_elements[gid]["editable"]
                        if f["prop"] == size_prop
                    )
                    assert measured == pytest.approx(width)
            if "alpha" in edits and curve:
                for gid in targets:
                    line = next(e for e in replayed["manifest"]["elements"] if e["gid"] == gid)
                    value = next(f["value"] for f in line["editable"] if f["prop"] == "alpha")
                    expected = (
                        edits["alpha"] if expected_residual else round(255 * edits["alpha"]) / 255
                    )
                    assert value == pytest.approx(expected)
                    assert line["geometry"]["stroke"] is (expected > 0)
            if interval:
                after_elements = {e["gid"]: e for e in replayed["manifest"]["elements"]}
                for patch in patches:
                    gid, prop = patch["gid"], patch["prop"]
                    measured = next(
                        f["value"] for f in after_elements[gid]["editable"] if f["prop"] == prop
                    )
                    expected = patch["value"]
                    if prop == "alpha" and patch not in expected_residual:
                        # grid stores the authored stroke's opacity as one byte.
                        expected = round(255 * expected) / 255
                    assert measured == (
                        pytest.approx(expected) if type(expected) in (int, float) else expected
                    )
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"], restored
        assert source.read_bytes() == original
        assert (project / "original.pdf").read_bytes() == original_pdf


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
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)

adapter_file <- commandArgs(trailingOnly = TRUE)[[1L]]
native <- dirname(adapter_file)
bridge <- new.env(parent = baseenv())
sys.source(file.path(native, "ggplot_bridge.R"), envir = bridge)
adapter <- bridge$ggplot_adapter(adapter_file)
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
title_and_legend_names <- function(state) {
  names(Filter(function(target) target$role != "ticks", state$text_targets))
}
stopifnot(summary_calls > 0L, length(baseline$state$layers) == 2L,
          length(baseline$state$panel_ranges) == 4L,
          identical(setdiff(title_and_legend_names(baseline$state), names(entries(baseline$state))),
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
stopifnot(identical(title_and_legend_names(limited$state),
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
stopifnot(identical(title_and_legend_names(hidden$state), c(head(names(labels), 5L), names(facets))))

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

category_data <- data.frame(x = c("January", "February", "March"), y = 1:3)
category <- ggplot2::ggplot(category_data, ggplot2::aes(x, y)) + ggplot2::geom_point()
category_state <- axis_capture(category)
bottom <- "axis.x.bottom.ticks"
left <- "axis.y.left.ticks"
rotation <- setNames(list(list(rotation = -40, weight = "bold")), bottom)
scene <- adapter$ggplot_table_for(category_state$state, rotation)
stopifnot(scene$text[[bottom]]$rotation == -40,
          identical(scene$text[[bottom]]$tick_labels,
                    category_state$state$text_targets[[bottom]]$tick_labels),
          identical(scene$text[[left]]$rotation, category_state$state$text_targets[[left]]$rotation))
original <- adapter$ggplot_manifest(category_state$state, "category")
rotated <- adapter$ggplot_manifest(category_state$state, "category", rotation)
fields <- function(manifest, gid) {
  element <- Filter(function(x) identical(x$gid, gid), manifest$elements)[[1L]]
  setNames(element$editable, vapply(element$editable, function(f) f$prop, character(1L)))
}
before <- fields(original, bottom)
after <- fields(rotated, bottom)
native <- axis_capture(category + ggplot2::theme(
  axis.text.x.bottom = ggplot2::element_text(angle = -40, face = "bold")))
stopifnot(before$rotation$value == 0, after$rotation$value == -40,
          after$rotation$value_original == before$rotation$value,
          after$weight$value == "bold", is.null(before$format),
          identical(adapter$render_ggplot(category_state$state, rotation), native$svg),
          identical(adapter$render_ggplot(category_state$state, list()), category_state$svg))
if ("monospace" %in% before$fontfamily$options) {
  font_edit <- setNames(list(list(fontfamily = "monospace")), bottom)
  started <- category_state$state$text_targets[[bottom]]
  updated <- adapter$ggplot_table_for(category_state$state, font_edit)$text
  font <- fields(adapter$ggplot_manifest(category_state$state, "category", font_edit), bottom)$fontfamily
  stopifnot(identical(updated[[bottom]]$paint$fontfamily, started$font_families[["monospace"]]),
            identical(updated[[bottom]]$tick_labels, started$tick_labels),
            identical(updated[[left]]$paint$fontfamily, category_state$state$text_targets[[left]]$paint$fontfamily),
            identical(font$value, "monospace"), identical(font$value_original, before$fontfamily$value))
}
fixed_angle <- axis_capture(category + ggplot2::guides(x = ggplot2::guide_axis(angle = 30)))
stopifnot(is.null(fields(adapter$ggplot_manifest(fixed_angle$state, "category"), bottom)$rotation))

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
custom_manifest <- adapter$ggplot_manifest(custom$state, "custom")
custom_ticks <- Filter(function(element) element$gid == ticks, custom_manifest$elements)[[1L]]
custom_props <- vapply(custom_ticks$editable, function(field) field$prop, character(1L))
bad_format <- stats::setNames(list(list(format = "%.2f")), ticks)
stopifnot(!"format" %in% custom_props, "rotation" %in% custom_props,
          inherits(try(adapter$render_ggplot(custom$state, bad_format), silent = TRUE), "try-error"))
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

# ggplot2 also merges identically titled guides into one guide with multiple
# channels. The shared scale must recolor both channels and the single guide.
merged_plot <- ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z, fill = z)) +
  ggplot2::geom_point(shape = 21) +
  ggplot2::scale_colour_gradient(aesthetics = c("colour", "fill"), name = "Both")
merged <- adapter$capture_ggplot(merged_plot, c(432, 288), palettes)
stopifnot(identical(names(merged$state$colour_scales), gid),
          identical(merged$state$colour_scales[[gid]]$aesthetic, c("colour", "fill")))
merged_result <- adapter$ggplot_remap_colour_scales(
  merged$state, edits[gid], merged$state$built$plot$guides$params
)
stopifnot(identical(merged_result$data[[1L]]$colour, expected),
          identical(merged_result$data[[1L]]$fill, expected),
          length(merged_result$guides) == 1L,
          identical(merged_result$guides[[1L]]$aesthetic, c("colour", "fill")))
merged_scale <- ggplot2::scale_colour_gradient(
  aesthetics = c("colour", "fill"), name = "Both", limits = c(1, 5), oob = scales::squish
)
merged_scale$palette <- adapter$ggplot_palette(palettes$palettes$viridis)
authored <- adapter$capture_ggplot(suppressMessages(merged_plot + merged_scale),
                                   c(432, 288), palettes)
stopifnot(identical(adapter$render_ggplot(merged$state, edits[gid]), authored$svg),
          identical(adapter$render_ggplot(merged$state), merged$svg))

# Separate source scales can happen to merge their guides before an edit.
# A single writable mapping would silently claim they share one scale.
separate <- ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z, fill = z)) +
  ggplot2::geom_point(shape = 21) +
  ggplot2::scale_colour_gradient(name = "Both") + ggplot2::scale_fill_gradient(name = "Both")
separate <- adapter$capture_ggplot(separate, c(432, 288), palettes)$state
stopifnot(length(separate$built$plot$guides$params) == 1L,
          !length(separate$colour_scales))

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
desc_edit <- stats::setNames(list(list(orientation = "horizontal", extend = "min", outline_visible = TRUE)), gid)
desc_guide <- guide_for(desc_state, scene_for(desc_state, desc_edit)$table, gid)
desc_cells <- desc_guide$layout$name
desc_tip <- which(desc_cells == paste0(adapter$ggplot_colourbar_tip_prefix, "min"))
desc_frame <- desc_guide$grobs[[which(desc_cells == "frame")]]
stopifnot(element_for(desc_state, desc_edit)$color_scale$reversed,
          desc_guide$layout$l[[desc_tip]] == desc_guide$layout$r[desc_cells == "bar"] + 1L,
          isTRUE(all.equal(as.numeric(desc_frame$x), c(0, 20/21, 1, 20/21, 0))),
          identical(as.numeric(desc_frame$y), c(0, 0, .5, 1, 1)))
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

# The frame follows the whole colour strip, including the actual extension
# cells. Its paint must not change the mapping or a second guide on the page.
outline_for <- function(guide) guide$grobs[[which(guide$layout$name == "frame")]]
stock <- ggplot2::theme(legend.frame = ggplot2::element_rect(colour = "#6D4221", linewidth = 1.3))
framed <- adapter$capture_ggplot(plot + stock, c(432, 288), palettes)
original <- framed$state
frame_edit <- stats::setNames(list(list(outline_width = 2.25)), gid)
actual <- scene_for(original, frame_edit)$table
guide <- guide_for(original, actual, gid)
frame <- outline_for(guide)
stopifnot(inherits(frame, "rect"), frame$gp$col == "#6D4221", frame$gp$lwd == 3,
          identical(outline_for(guide_for(original, actual, other))$gp,
                    outline_for(guide_for(original, original$table, other))$gp))
plain <- ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z)) + ggplot2::geom_point() +
  ggplot2::scale_colour_gradient(name = "Exposure", guide = ggplot2::guide_colourbar(position = "right"))
native_width <- ggplot2::theme(legend.frame = ggplot2::element_rect(
  colour = "#6D4221", linewidth = 2.25 / (ggplot2::.pt * 72 / 96)))
plain_state <- adapter$capture_ggplot(plain + stock, c(432, 288), palettes)$state
native_svg <- adapter$capture_ggplot(plain + native_width, c(432, 288), palettes)$svg
stopifnot(identical(adapter$render_ggplot(plain_state, frame_edit), native_svg))

for (direction in c("horizontal", "vertical")) {
  extra <- stats::setNames(list(list(orientation = direction, extend = "both", outline_width = 2.25)), gid)
  scene <- scene_for(original, extra)
  guide <- guide_for(original, scene$table, gid)
  frame <- outline_for(guide)
  axis <- if (direction == "horizontal") "x" else "y"
  cross <- if (direction == "horizontal") "y" else "x"
  stopifnot(inherits(frame, "polygon"), frame$gp$lwd == 3, is.na(frame$gp$fill),
            identical(as.numeric(frame[[cross]]), c(.5, 0, 0, .5, 1, 1)),
            isTRUE(all.equal(as.numeric(frame[[axis]]), c(0, 1/22, 21/22, 1, 21/22, 1/22))))
  visible <- element_for(original, extra)$color_scale$outline
  stopifnot(visible$original$outline_visible, visible$edited$outline_width == 2.25)
  extra[[gid]]$outline_visible <- FALSE
  hidden <- scene_for(original, extra)$table
  hidden_guide <- guide_for(original, hidden, gid)
  stopifnot(inherits(outline_for(hidden_guide), "zeroGrob"),
            identical(guide$grobs[[which(guide$layout$name == "bar")]]$gp,
                      hidden_guide$grobs[[which(hidden_guide$layout$name == "bar")]]$gp))
}
make_visible <- stats::setNames(list(list(outline_visible = TRUE, outline_width = 2.25)), gid)
ink <- outline_for(guide_for(state, scene_for(state, make_visible)$table, gid))
description <- element_for(state, make_visible)$color_scale$outline
stopifnot(inherits(ink, "rect"), !description$original$outline_visible,
          description$edited$outline_visible, identical(adapter$render_ggplot(original), framed$svg),
          identical(adapter$render_ggplot(state), captured$svg))
for (bad in list(list(outline_width = -1), list(outline_visible = "yes"))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, stats::setNames(list(bad), gid)),
                        silent = TRUE), "try-error"))
}
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
groups <- paste0(line_id, ".group.", 1:2)
control <- element(baseline, groups[[1L]])
treated <- element(baseline, groups[[2L]])
stopifnot(endsWith(control$label, "Control"), endsWith(treated$label, "Treated"),
          identical(control$geometry$paths, initial_line$geometry$paths[1:2]),
          identical(treated$geometry$paths, initial_line$geometry$paths[3L]))
limited <- stats::setNames(list(list(linewidth = 3, linestyle = ":")), groups[[2L]])
edited <- adapter$render_ggplot(state, limited)
strokes <- strsplit(edited, "\n", fixed = TRUE)[[1L]]
picked <- grepl("stroke: #A53658", strokes, fixed = TRUE) &
  (startsWith(strokes, "<polyline ") | startsWith(strokes, "<line "))
other <- grepl("stroke: #2369A3", strokes, fixed = TRUE) &
  (startsWith(strokes, "<polyline ") | startsWith(strokes, "<line "))
stopifnot(sum(picked) == 2L, any(other),
          all(grepl("stroke-width: 3.00", strokes[picked], fixed = TRUE)),
          all(grepl("stroke-dasharray:", strokes[picked], fixed = TRUE)),
          !any(grepl("stroke-width: 3.00", strokes[other], fixed = TRUE)))
stacked <- c(limited, patches[c(line_id)])
paint <- adapter$ggplot_manifest(state, "plot", stacked)
read <- function(gid, prop) Filter(function(field) field$prop == prop, element(paint, gid)$editable)[[1L]]$value
stopifnot(read(line_id, "linewidth") == 2.5, read(groups[[1L]], "linewidth") == 2.5,
          read(groups[[2L]], "linewidth") == 3, read(groups[[1L]], "linestyle") == "--",
          read(groups[[2L]], "linestyle") == ":",
          identical(adapter$render_ggplot(state, stacked), adapter$render_ggplot(state, rev(stacked))),
          identical(element(paint, groups[[2L]])$geometry$paths, treated$geometry$paths),
          identical(adapter$render_ggplot(state), captured$svg), calls == 1L)
projected <- c(limited, list(axes_0 = list(xlim = c(1, 5), xscale = "log", invert_x = TRUE)))
projected_manifest <- adapter$ggplot_manifest(state, "plot", projected)
selected <- element(projected_manifest, groups[[2L]])
host <- element(projected_manifest, "axes_0")$bbox
positions <- vapply(selected$geometry$paths[[1L]]$points, `[[`, numeric(1L), 1L)
expected <- host[[1L]] + host[[3L]] * (1 - log10(1:5) / log10(5))
stopifnot(isTRUE(all.equal(positions, expected)), identical(selected$line_context, treated$line_context),
          calls == 1L)
alias <- ggplot2::scale_colour_manual(values = c(Control = "#2369A3", Treated = "#2369A3"))
alias_targets <- adapter$capture_ggplot(base + line + alias, frame)$state$line_targets
stopifnot(identical(names(alias_targets), "layer.1.line"))
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


FACET_GROUPED_LINE_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
d <- expand.grid(x = 1:5, group = c("Control", "Treated"), facet = c("Early", "Middle", "Late"))
d <- d[!(d$group == "Control" & d$facet == "Late"), ]
d$y <- as.integer(d$facet) * d$x + as.integer(d$group)
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, colour = group)) +
  ggplot2::geom_line(data = observed, linewidth = .7) + ggplot2::geom_point() +
  ggplot2::facet_wrap(ggplot2::vars(facet), scales = "free_y") +
  ggplot2::coord_cartesian(xlim = c(1.5, 4.5), expand = FALSE) +
  ggplot2::scale_colour_manual(values = c(Control = "#2369A3", Treated = "#A53658"))
frame <- c(420, 224)
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
untouched <- state$built$data
stopifnot(calls == 1L)
gids <- paste0("layer.1.line.group.", 1:2)
local_ids <- c("layer.1.panel.1.line.group.1", "layer.1.panel.2.line.group.1",
               "layer.1.panel.1.line.group.2", "layer.1.panel.2.line.group.2",
               "layer.1.panel.3.line.group.2")
stopifnot(setequal(names(state$line_targets), c(gids, local_ids)),
          identical(unname(lengths(lapply(state$line_targets[gids], `[[`, "parts"))), c(2L, 3L)))
change <- stats::setNames(list(list(linewidth = 3, linestyle = ":")), gids[[2L]])
element <- function(all, gid) Filter(function(e) e$gid == gid, all$elements)[[1L]]
baseline <- adapter$ggplot_manifest(state, "plot")
scene <- adapter$ggplot_manifest(state, "plot", change)
target <- element(scene, gids[[2L]])
original <- element(baseline, gids[[2L]])
stopifnot(length(target$geometry$paths) == 3L, endsWith(target$label, "Treated"),
          identical(target$geometry$paths, original$geometry$paths),
          identical(target$line_context, original$line_context),
          identical(element(scene, gids[[1L]]), element(baseline, gids[[1L]])))
rendered <- adapter$render_ggplot(state, change)
lines <- function(svg, colour) {
  rows <- strsplit(svg, "\n", fixed = TRUE)[[1L]]
  rows[(startsWith(rows, "<polyline ") | startsWith(rows, "<line ") | startsWith(rows, "<circle ")) &
       grepl(paste0("stroke: ", colour), rows, fixed = TRUE)]
}
red <- lines(rendered, "#A53658")
styled <- grepl("stroke-width: 3.00", red, fixed = TRUE) & grepl("stroke-dasharray:", red, fixed = TRUE)
stopifnot(sum(styled & startsWith(red, "<polyline ")) == 3L,
          sum(styled & startsWith(red, "<line ")) == 1L,
          identical(red[startsWith(red, "<circle ")],
                    Filter(function(row) startsWith(row, "<circle "), lines(captured$svg, "#A53658"))),
          identical(lines(rendered, "#2369A3"), lines(captured$svg, "#2369A3")),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(state$built$data, untouched), calls == 1L)
fresh <- adapter$capture_ggplot(plot, frame)$state
stopifnot(identical(adapter$render_ggplot(fresh, change), rendered),
          identical(element(adapter$ggplot_manifest(fresh, "plot", change), gids[[2L]]), target))
projected <- c(change, list(axes_2 = list(xscale = "log")))
projected_scene <- adapter$ggplot_manifest(state, "plot", projected)
projected_line <- element(projected_scene, gids[[2L]])
red <- lines(adapter$render_ggplot(state, projected), "#A53658")
styled <- grepl("stroke-width: 3.00", red, fixed = TRUE) & grepl("stroke-dasharray:", red, fixed = TRUE)
stopifnot(length(projected_line$geometry$paths) == 3L,
          !identical(projected_line$geometry$paths, target$geometry$paths),
          identical(projected_line$line_context, target$line_context),
          sum(styled & startsWith(red, "<polyline ")) == 3L,
          sum(styled & startsWith(red, "<line ")) == 1L,
          calls == 2L)
local <- "layer.1.panel.2.line.group.2"
picked <- stats::setNames(list(list(linewidth = 4, linestyle = "--", alpha = .4)), local)
local_svg <- adapter$render_ggplot(state, picked)
red_local <- lines(local_svg, "#A53658")
rows <- startsWith(red_local, "<polyline ")
stopifnot(sum(grepl("stroke-width: 4.00", red_local[rows], fixed = TRUE)) == 1L,
          sum(grepl("stroke-opacity: 0.40", red_local[rows], fixed = TRUE)) == 1L,
          identical(red_local[!rows], lines(captured$svg, "#A53658")[!rows]),
          identical(lines(local_svg, "#2369A3"), lines(captured$svg, "#2369A3")),
          identical(adapter$render_ggplot(state), captured$svg), calls == 2L)
local_scene <- adapter$ggplot_manifest(state, "plot", picked)
one <- element(local_scene, local)
stopifnot(length(one$geometry$paths) == 1L, endsWith(one$label, "Middle · Treated"),
          identical(one$geometry$paths, element(baseline, local)$geometry$paths),
          identical(element(local_scene, local_ids[[3L]]), element(baseline, local_ids[[3L]])))
both <- c(picked, change)
all_svg <- adapter$render_ggplot(state, both)
stopifnot(identical(adapter$render_ggplot(state, rev(both)), all_svg))
red_both <- lines(all_svg, "#A53658")
stopifnot(sum(startsWith(red_both, "<polyline ") & grepl("stroke-width: 4.00", red_both)) == 1L,
          sum(startsWith(red_both, "<polyline ") & grepl("stroke-width: 3.00", red_both)) == 2L,
          sum(startsWith(red_both, "<line ") & grepl("stroke-width: 3.00", red_both)) == 1L)
jsonlite::write_json(scene, "faceted-curve.json", auto_unbox = TRUE, null = "null", digits = NA)
"""


FACET_AXES_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
values <- expand.grid(x = rep(1:4, each = 2), row = c("A", "B"), column = c("X", "Y", "Z"))
values$y <- values$x * as.integer(values$column) + 10 * as.integer(values$row)
calls <- 0L
counted_mean <- function(y) { calls <<- calls + 1L; mean(y) }
plot <- ggplot2::ggplot(values, ggplot2::aes(x, y)) +
  ggplot2::stat_summary(fun = counted_mean, geom = "point") +
  ggplot2::theme_bw() + ggplot2::labs(title = "Panel grid")
wrap <- ggplot2::facet_wrap(ggplot2::vars(row, column), ncol = 3, scales = "free_y", dir = "v")
grid <- ggplot2::facet_grid(ggplot2::vars(row), ggplot2::vars(column), scales = "free")
element <- function(scene, gid) Filter(function(e) e$gid == gid, scene$elements)[[1L]]
child <- function(parent, prefix) {
  found <- which(startsWith(parent$childrenOrder, prefix))
  if (length(found) == 1L) parent$children[[found]]
}
frame <- c(420, 262)
edit <- list(axes_3 = list(grid_x = FALSE, grid_color = "#9B2762", grid_alpha = .6,
                           grid_linewidth = 1.7, grid_linestyle = "--", facecolor = "#ECD5A8",
                           spine_left = FALSE, spine_color = "#692044", spine_right_color = "#098756",
                           spine_top_linewidth = 3))
fixtures <- list(list(facet = wrap, cell = "panel-2-1"), list(facet = grid, cell = "panel-3-1"))
for (case in fixtures) {
  captured <- adapter$capture_ggplot(plot + case$facet, frame)
  state <- captured$state
  counted <- calls
  original_data <- state$built$data
  original_ranges <- state$panel_ranges
  base <- state$table
  edited <- adapter$ggplot_table_for(state, edit)$table
  slot <- match(case$cell, base$layout$name)
  stopifnot(identical(names(state$axes_targets), paste0("axes_", 1:6)),
            state$axes_targets$axes_3$slot == slot,
            identical(base$grobs[-slot], edited$grobs[-slot]))
  old <- base$grobs[[slot]]
  panel <- edited$grobs[[slot]]
  untouched <- !grepl("^(grill|panel.border)\\.", old$childrenOrder)
  stopifnot(identical(old$children[untouched], panel$children[untouched]))
  y_before <- child(child(old, "grill."), "panel.grid.major.y")
  grill <- child(panel, "grill.")
  y_after <- child(grill, "panel.grid.major.y")
  x_after <- child(grill, "panel.grid.major.x")
  stopifnot(identical(y_before$x, y_after$x), identical(y_before$y, y_after$y),
            identical(y_after$gp$col, "#9B276299"),
            isTRUE(all.equal(y_after$gp$lwd * adapter$ggplot_lwd_pt, 1.7)),
            is.null(x_after) || inherits(x_after, "zeroGrob"),
            identical(child(grill, "panel.background")$gp$fill, "#ECD5A8"))
  border <- child(panel, "panel.border.")
  stopifnot(is.null(child(border, "tavotto.frame.left")),
            identical(grDevices::col2rgb(child(border, "tavotto.frame.right")$gp$col, alpha = TRUE),
                      grDevices::col2rgb("#098756", alpha = TRUE)),
            isTRUE(all.equal(child(border, "tavotto.frame.top")$gp$lwd * adapter$ggplot_lwd_pt, 3)))
  original <- adapter$ggplot_manifest(state, "plot")
  scene <- adapter$ggplot_manifest(state, "plot", edit)
  selected <- element(scene, "axes_3")
  stopifnot(endsWith(selected$label, "A / Z"),
            identical(selected$bbox, element(original, "axes_3")$bbox),
            identical(element(original, "axes_2"), element(scene, "axes_2")),
            identical(selected$native_axes$context, element(original, "axes_3")$native_axes$context))
  svg <- adapter$render_ggplot(state, edit)
  stopifnot(grepl("#9B2762", svg, fixed = TRUE), grepl("#ECD5A8", svg, fixed = TRUE),
            identical(adapter$render_ggplot(state), captured$svg),
            identical(adapter$render_ggplot(state, edit), svg),
            identical(state$built$data, original_data), identical(state$panel_ranges, original_ranges),
            calls == counted)
  reflow <- c(edit, list(plot.title = list(fontsize = 28)))
  moved <- adapter$ggplot_manifest(state, "plot", reflow)
  stopifnot(!identical(element(moved, "axes_3")$bbox, selected$bbox))
  png <- adapter$preview_ggplot_png(state, reflow, "facets.png", 840, TRUE)
  png_scene <- adapter$ggplot_manifest(state, "plot", reflow, png)
  stopifnot(isTRUE(all.equal(element(png_scene, "axes_3")$bbox, element(moved, "axes_3")$bbox,
                            tolerance = .02)), calls == counted)
  fresh <- adapter$capture_ggplot(plot + case$facet, frame)$state
  stopifnot(identical(adapter$render_ggplot(fresh, edit), svg))
}
front <- adapter$capture_ggplot(plot + wrap + ggplot2::theme(panel.ontop = TRUE), frame)$state
stopifnot(is.null(front$axes_targets$axes_3$background), length(front$axes_targets$axes_3$grid) > 0L)
stopifnot(inherits(try(adapter$render_ggplot(state, list(axes_3 = list(xlim = c(3, 1)))),
                       silent = TRUE), "try-error"))
jsonlite::write_json(scene, "faceted-axes.json", auto_unbox = TRUE, null = "null", digits = NA)
"""


FACET_COORDINATES_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
grDevices::pdf(file = NULL)
calls <- 0L
summarize <- function(y) { calls <<- calls + 1L; mean(y) }
data <- expand.grid(x = rep(c(1, 10, 100), each = 2), row = c("A", "B"), col = c("X", "Y", "Z"))
data$y <- log10(data$x) + as.integer(data$row) * 10 + rep(c(-1, 1), nrow(data) / 2)
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::stat_summary(geom = "point", fun = summarize) + ggplot2::geom_line() +
  ggplot2::theme_bw() + ggplot2::labs(x = "Dose", y = "Response")
frame <- c(420, 288)
element <- function(scene, gid) Filter(function(e) e$gid == gid, scene$elements)[[1L]]
grid <- ggplot2::facet_grid(ggplot2::vars(row), ggplot2::vars(col), scales = "free")
capture <- adapter$capture_ggplot(plot + grid, frame)
state <- capture$state
counted <- calls
data_before <- state$built$data
baseline <- adapter$ggplot_manifest(state, "dose")
edits <- list(axes_2 = list(xscale = "log", xlim = c(1, 100), grid_x = FALSE),
              axes_4 = list(ylim = c(19, 23), invert_y = TRUE),
              layer.2.panel.5.line = list(color = "#125A90", linewidth = 2.4))
rendered <- adapter$ggplot_manifest(state, "dose", edits)
scene <- adapter$ggplot_table_for(state, edits)
for (id in seq_along(state$built$layout$panel_params)) {
  gid <- paste0("axes_", id)
  x_changed <- id %in% c(2L, 5L)
  y_changed <- id %in% 4:6
  target <- state$axes_targets[[gid]]
  axes <- element(rendered, gid)$native_axes
  previous <- element(baseline, gid)$native_axes
  stopifnot(identical(axes$context, previous$context),
            identical(axes$current$x$mode, if (x_changed) "log" else "linear"),
            identical(axes$current$x$range, if (x_changed) list(1, 100) else previous$current$x$range),
            identical(axes$current$y$range, if (y_changed) list(19, 23) else previous$current$y$range),
            identical(axes$current$y$inverted, y_changed))
  get_points <- function(table) {
    panel <- table$grobs[[target$slot]]
    panel$children[[which(startsWith(panel$childrenOrder, "geom_point."))]]
  }
  points <- get_points(scene$table)
  original <- get_points(state$table)
  stopifnot(isTRUE(all.equal(as.numeric(points$x), if (x_changed) c(0, .5, 1) else as.numeric(original$x))),
            isTRUE(all.equal(as.numeric(points$y), if (y_changed) c(.75, .5, .25) else as.numeric(original$y))))
}
stopifnot(identical(element(rendered, "axes_5")$native_axes$edited, list("xscale", "xlim", "ylim", "invert_y")),
          identical(element(rendered, "axes_2")$native_axes$links$x, list("axes_5")),
          identical(element(rendered, "axes_4")$native_axes$links$y, list("axes_5", "axes_6")),
          identical(element(rendered, "axes_6")$native_axes$links$y, list("axes_4")))
invalid <- try(adapter$ggplot_table_for(state, c(edits, list(axes_5 = list(xscale = "linear")))), silent = TRUE)
stopifnot(inherits(invalid, "try-error"), grepl("conflicting values", as.character(invalid), fixed = TRUE))
svg <- adapter$render_ggplot(state, edits)
png <- adapter$preview_ggplot_png(state, edits, "facets.png", 840, TRUE)
stopifnot(isTRUE(all.equal(element(rendered, "axes_5")$bbox, png$axes_bbox$axes_5, tolerance = .02)),
          grepl("#125A90", svg, fixed = TRUE), identical(adapter$render_ggplot(state), capture$svg),
          identical(state$built$data, data_before), calls == counted)
replayed <- adapter$capture_ggplot(plot + grid, frame)$state
stopifnot(identical(adapter$render_ggplot(replayed, edits), svg))
single_group <- list(axes_2 = list(xlim = c(1, 100), invert_x = TRUE))
for (fixed in c(TRUE, FALSE)) {
  wrap <- ggplot2::facet_wrap(ggplot2::vars(row, col), ncol = 3,
                             scales = if (fixed) "fixed" else "free")
  source <- adapter$capture_ggplot(plot + wrap, frame)$state
  views <- adapter$ggplot_table_for(source, single_group)$axes
  reversed <- names(Filter(function(panel) isTRUE(panel$x$inverted), views))
  stopifnot(identical(reversed, if (fixed) paste0("axes_", 1:6) else "axes_2"))
}
jsonlite::write_json(rendered, "coordinates.json", auto_unbox = TRUE, null = "null", digits = NA)

cells <- expand.grid(x = 1:3, y = 1:2, facet = c("A", "B"))
cells$fill <- rep(c("#D60000", "#059355", "#183ED0", "#6A1692", "#EA5303", "#6EE7D7"), 2)
raster <- ggplot2::ggplot(cells, ggplot2::aes(x, y, fill = fill)) +
  ggplot2::geom_raster(hjust = .25, vjust = .75) + ggplot2::scale_fill_identity() +
  ggplot2::facet_wrap(ggplot2::vars(facet), scales = "free") +
  ggplot2::coord_cartesian(xlim = c(0, 8), ylim = c(0, 5), expand = FALSE)
source <- adapter$capture_ggplot(raster, frame)$state
flipped <- adapter$ggplot_table_for(source, list(axes_2 = list(invert_x = TRUE, invert_y = TRUE)))
get_raster <- function(table, id) {
  panel <- table$grobs[[source$axes_targets[[id]]$slot]]
  panel$children[[which(vapply(panel$children, inherits, logical(1L), "rastergrob"))]]
}
a <- get_raster(source$table, 1L)
b <- get_raster(flipped$table, 1L)
c <- get_raster(source$table, 2L)
d <- get_raster(flipped$table, 2L)
stopifnot(identical(a$raster, b$raster), identical(a$x, b$x), identical(a$y, b$y),
          identical(as.matrix(d$raster), as.matrix(c$raster)[2:1, 3:1]),
          isTRUE(all.equal(as.numeric(c$x) + as.numeric(d$x), 1)),
          isTRUE(all.equal(as.numeric(c$y) + as.numeric(d$y), 1)))
grDevices::dev.off()
"""


AXIS_WRITEBACK_METRICS_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
grDevices::pdf(file = NULL, width = 7, height = 5)
grDevices::pdf(file = NULL, width = 5, height = 3)
original_devices <- grDevices::dev.list()
current_device <- grDevices::dev.cur()
data <- expand.grid(x = 1:3, facet = c("Left", "Right"))
data$y <- c(2, 4, 3, 6, 4, 8)
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_point() + ggplot2::labs(title = "Signal", x = "Input")
frame <- c(288, 216)
png <- function(state, edits, file) {
  measured <- adapter$preview_ggplot_png(state, edits, file, 960, TRUE)
  stopifnot(length(measured$axes_bbox) > 0L, file.info(file)$size > 0,
            identical(grDevices::dev.list(), original_devices),
            identical(grDevices::dev.cur(), current_device))
  list(axes = measured$axes_bbox, bytes = readBin(file, "raw", n = file.info(file)$size))
}
for (faceted in c(FALSE, TRUE)) {
  original <- if (faceted) plot + ggplot2::facet_wrap(ggplot2::vars(facet), scales = "free_y") else plot
  gid <- if (faceted) "axes_2" else "axes_0"
  before <- adapter$capture_ggplot(original, frame)$state
  residual <- stats::setNames(list(list(xscale = "log", xlim = c(.9, 3.5), invert_y = TRUE)), gid)
  style <- list(plot.title = list(fontfamily = "serif", color = "#572B82", fontsize = 19.5),
                axis.x.bottom.title = list(fontfamily = "monospace", color = "#A64184"))
  hot <- png(before, c(residual, style), "hot.png")
  family <- function(gid, name) before$text_targets[[gid]]$font_families[[name]]
  baked <- original + ggplot2::theme(
    plot.title = ggplot2::element_text(family = family("plot.title", "serif"),
                                      colour = "#572B82", size = 19.5),
    axis.title.x.bottom = ggplot2::element_text(
      family = family("axis.x.bottom.title", "monospace"), colour = "#A64184"
    )
  )
  reopened <- adapter$capture_ggplot(baked, frame)$state
  cold <- png(reopened, residual, "cold.png")
  baseline <- png(before, list(), "baseline.png")
  stopifnot(!identical(hot$bytes, baseline$bytes), identical(hot$axes, cold$axes),
            identical(hot$bytes, cold$bytes))
  adapter$export_ggplot_pdf(reopened, residual, "cold.pdf")
  stopifnot(file.info("cold.pdf")$size > 0, identical(grDevices::dev.cur(), current_device))
}
for (device in rev(original_devices)) grDevices::dev.off(which = device)
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

# An authored logarithmic scale exposes both interval and explicit positions in data units.
authored <- adapter$capture_ggplot(ggplot2::ggplot(data.frame(x = c(1, 10, 100), y = 1:3),
  ggplot2::aes(x, y)) + ggplot2::geom_point() + ggplot2::scale_x_log10() +
  ggplot2::coord_cartesian(xlim = c(1, 100), expand = FALSE), frame)
authored_manifest <- adapter$ggplot_manifest(authored$state, "authored")
stopifnot(identical(field(authored_manifest, "major_mode")$options, list("auto", "step", "fixed")),
          isTRUE(all.equal(field(authored_manifest, "major_step")$value, (100 - 1) / 5)),
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
grid_spec <- categorical$state$axes_targets$axes_0$grid$original
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
stopifnot(identical(zero_state$axes_targets$axes_0$axes$y$options, c("linear", "symlog")),
          identical(zero_state$axes_targets$axes_0$axes$x$options, c("linear", "log", "symlog")))
invalid <- list(list(xlim = c(10, 2)), list(xlim = list("1", "10")),
                list(xscale = "log", xlim = c(0, 100)), list(yscale = "log"))
for (change in invalid) {
  stopifnot(inherits(try(adapter$render_ggplot(zero_state, list(axes_0 = change)), silent = TRUE), "try-error"))
}
secondary <- plot + ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis())
secondary_view <- adapter$capture_ggplot(secondary, frame)$state$axes_targets$axes_0$axes
stopifnot(identical(names(secondary_view), c("x", "y")))
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
target <- state$state$axes_targets$axes_0
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
packed <- adapter$ggplot_frame_value(boxed$state$axes_targets$axes_0$frame, list())
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

theme <- ggplot2::theme_minimal() +
  ggplot2::theme(plot.background = ggplot2::element_rect(fill = "#DADCEE", colour = NA))
blank <- adapter$capture_ggplot(plot + theme, frame)
background <- function(state, changes = list()) {
  axes <- Filter(function(e) e$gid == "axes_0", adapter$ggplot_manifest(state, "box", changes)$elements)
  axes[[1L]]$native_axes$background
}
fill <- function(value) list(axes_0 = list(facecolor = value))
theme_fill <- function(value, border = NA, size = NULL) ggplot2::theme(
  panel.background = ggplot2::element_rect(fill = value, colour = border, linewidth = size))
coloured <- adapter$capture_ggplot(plot + theme + theme_fill("#CED9A3"), frame)
changed <- adapter$render_ggplot(blank$state, fill("#CED9A3"))
stopifnot(identical(changed, coloured$svg),
          identical(background(blank$state, fill("#CED9A3")), list(original = "none", current = "#CED9A3")),
          grepl("fill: #DADCEE", changed, fixed = TRUE),
          identical(adapter$render_ggplot(blank$state), blank$svg))
authored <- plot + theme + theme_fill("#CED9A3", "#283159", .7)
painted <- adapter$capture_ggplot(authored, frame)
cleared <- adapter$render_ggplot(painted$state, fill("none"))
expected <- adapter$capture_ggplot(authored + theme_fill(NA, "#283159", .7), frame)
stopifnot(identical(cleared, expected$svg), grepl("stroke: #283159", cleared, fixed = TRUE),
          identical(background(painted$state, fill("none")), list(original = "#CED9A3", current = "none")),
          inherits(try(adapter$render_ggplot(painted$state, fill("invalid")), silent = TRUE), "try-error"))
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
source_scope <- function(state) {
  axes <- Filter(function(element) element$gid == "axes_0",
                 adapter$ggplot_manifest(state, "raster")$elements)[[1L]]$native_axes
  axes$source_groups
}
native_scope <- source_scope(captured$state)
stopifnot(native_scope$x$range, native_scope$y$range,
          native_scope$x$direction, native_scope$y$direction,
          native_scope$x$raster, native_scope$y$raster)
for (mode in c("none", "x", "y", "xy")) {
  changes <- if (mode != "none") list(axes_0 = list(invert_x = grepl("x", mode),
                                                   invert_y = grepl("y", mode))) else list()
  png <- adapter$preview_ggplot_png(captured$state, changes, paste0(mode, ".png"), 800, TRUE)
  stopifnot(file.info(paste0(mode, ".png"))$size > 0)
}
authored <- adapter$capture_ggplot(plot + coords("xy"), frame)
stopifnot(source_scope(authored$state)$x$range, source_scope(authored$state)$y$range)
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
          identical(fields(log_state)$minor_mode$options, list("auto", "step")))
log_step <- stats::setNames(list(list(minor_visible = TRUE, minor_mode = "step",
    minor_step = 2.5, major_mode = "fixed", major_values = c(1, 10))), gid)
log_marks <- positions(log_state, log_step)
expected_log <- 1 - log10(c(2.5, 5, 7.5))
stopifnot(length(log_marks$ticks) == 2L,
          isTRUE(all.equal(log_marks$ticks[[1L]], c(1, 0))),
          isTRUE(all.equal(log_marks$ticks[[2L]], expected_log)),
          isTRUE(all.equal(log_marks$grid, expected_log)))
log_step[[gid]]$major_mode <- "step"
log_step[[gid]]$major_step <- 2
log_step[[gid]]$major_values <- NULL
stepped_log <- positions(log_state, log_step)
stopifnot(isTRUE(all.equal(stepped_log$ticks[[1L]], 1 - log10(seq(2, 10, by = 2)))),
          isTRUE(all.equal(stepped_log$ticks[[2L]], expected_log)),
          isTRUE(all.equal(stepped_log$grid, expected_log)))

# Separately computed decimal intervals can reach the same physical mark.
overlap_plot <- base +
  ggplot2::geom_point(data = data.frame(x = c(.12, 1.02), y = c(4, 6))) +
  ggplot2::coord_cartesian(xlim = c(.12, 1.02), expand = FALSE)
overlap <- adapter$capture_ggplot(overlap_plot, frame)$state
intervals <- stats::setNames(list(list(major_mode = "step", major_step = .3,
    minor_mode = "step", minor_step = .1, minor_visible = TRUE,
    format = "%.2f", minor_format = "%.2f")), gid)
deduped <- positions(overlap, intervals)
expected_minor <- (c(.2, .4, .5, .7, .8, 1) - .12) / .9
stopifnot(isTRUE(all.equal(deduped$ticks[[2L]], expected_minor)),
          isTRUE(all.equal(deduped$grid, expected_minor)),
          !anyDuplicated(deduped$labels))
intervals[[gid]]$minor_visible <- FALSE
hidden <- positions(overlap, intervals)
stopifnot(!axis_ink(adapter$render_ggplot(overlap, intervals), "#3366AA"),
          isTRUE(all.equal(hidden$grid, expected_minor)))

verify_transformed_ticks <- function(transform, reference, range, points, steps, major, minor) {
  data <- data.frame(x = points, y = seq_along(points))
  plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) + ggplot2::geom_point() +
    ggplot2::scale_x_continuous(transform = transform) +
    ggplot2::coord_cartesian(xlim = range, expand = FALSE) +
    ggplot2::theme(axis.ticks.x.bottom = ggplot2::element_line(colour = "#228833"),
                   axis.minor.ticks.x.bottom = ggplot2::element_line(colour = "#3366AA"),
                   panel.grid.minor.x = ggplot2::element_line(colour = "#CC2266"))
  state <- adapter$capture_ggplot(plot, frame)$state
  stopifnot("step" %in% fields(state)$major_mode$options,
            "step" %in% fields(state)$minor_mode$options)
  edits <- stats::setNames(list(list(major_mode = "step", major_step = steps[[1L]],
    minor_mode = "step", minor_step = steps[[2L]], minor_visible = TRUE)), gid)
  observed <- positions(state, edits)
  forward <- function(values) (reference(values) - reference(range[[1L]])) / diff(reference(range))
  stopifnot(isTRUE(all.equal(observed$ticks[[1L]], forward(major))),
            isTRUE(all.equal(observed$ticks[[2L]], forward(minor))),
            isTRUE(all.equal(observed$grid, forward(minor))))
}
verify_transformed_ticks("logit", stats::qlogis, c(.05, .95), c(.07, .31, .53, .91),
                         c(.2, .1), c(.2, .4, .6, .8), c(.1, .3, .5, .7, .9))
signed_reference <- function(value) ifelse(abs(value) <= 2, value * 10 / 9,
  sign(value) * (20 / 9 + 2 * log10(pmax(1, abs(value) / 2))))
verify_transformed_ticks(adapter$ggplot_transform_symlog(), signed_reference, c(-200, 200),
                         c(-100, -2, 0, 2, 100), c(100, 50),
                         c(-200, -100, 0, 100, 200), c(-150, -50, 50, 150))

# The four shared stroke controls use physical points, including native
# inward ticks. Compare edited marks with an independently authored R theme.
stroke_plot <- base + ggplot2::theme(axis.ticks.length.x.bottom = grid::unit(-4, "bigpts"),
                                    axis.ticks.x.bottom = ggplot2::element_line(linewidth = .8)) +
  native
strokes <- adapter$capture_ggplot(stroke_plot, frame)
before <- fields(strokes$state)
stopifnot(before$length$value == 4, before$minor_length$value == 3)
stroke_patch <- stats::setNames(list(list(length = 8, width = 2, minor_width = 1.4)), gid)
unit_width <- ggplot2::.pt * adapter$ggplot_lwd_pt
authored_marks <- stroke_plot + ggplot2::theme(
  axis.ticks.length.x.bottom = grid::unit(-8, "bigpts"),
  axis.ticks.x.bottom = ggplot2::element_line(linewidth = 2 / unit_width),
  axis.minor.ticks.length.x.bottom = grid::unit(-3, "bigpts"),
  axis.minor.ticks.x.bottom = ggplot2::element_line(linewidth = 1.4 / unit_width)
)
edited_marks <- adapter$render_ggplot(strokes$state, stroke_patch)
after <- fields(strokes$state, stroke_patch)
stopifnot(identical(edited_marks, adapter$capture_ggplot(authored_marks, frame)$svg),
          after$length$value == 8, after$length$value_original == 4,
          after$width$value == 2, after$minor_width$value == 1.4,
          identical(after$minor_length, before$minor_length),
          identical(adapter$render_ggplot(strokes$state), strokes$svg))
minor_only <- stats::setNames(list(list(minor_length = 0)), gid)
native_minor <- stroke_plot +
  ggplot2::theme(axis.minor.ticks.length.x.bottom = grid::unit(0, "bigpts"))
stopifnot(identical(adapter$render_ggplot(strokes$state, minor_only),
                    adapter$capture_ggplot(native_minor, frame)$svg))
out <- stats::setNames(list(list(direction = "out")), gid)
native_out <- stroke_plot + ggplot2::theme(
  axis.ticks.length.x.bottom = grid::unit(4, "bigpts"),
  axis.minor.ticks.length.x.bottom = grid::unit(3, "bigpts"))
stopifnot(before$direction$value == "in",
          identical(adapter$render_ggplot(strokes$state, out), adapter$capture_ggplot(native_out, frame)$svg))
both <- stats::setNames(list(list(direction = "inout", length = 6, minor_length = 4)), gid)
centered <- adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  table <- adapter$ggplot_table_for(strokes$state, both)$table
  axis <- table$grobs[[which(table$layout$name == "axis-b")]]$children[["axis"]]
  lapply(axis$grobs[[1L]]$children, function(mark) {
    point <- function(i) grid::convertUnit(mark$y[i], "bigpts", valueOnly = TRUE)
    c(point(1L) - point(2L), 0.5 * (point(1L) + point(2L)) -
        grid::convertUnit(grid::unit(1, "npc"), "bigpts", valueOnly = TRUE))
  })
})$value
stopifnot(length(centered) == 2L,
          isTRUE(all.equal(unname(centered[[1L]]), c(-6, 0))),
          isTRUE(all.equal(unname(centered[[2L]]), c(-4, 0))),
          fields(strokes$state, both)$direction$value == "inout",
          identical(adapter$render_ggplot(strokes$state), strokes$svg))
hidden <- stats::setNames(list(list(visible = FALSE)), gid)
native_hidden <- adapter$capture_ggplot(
  stroke_plot + ggplot2::theme(axis.text.x.bottom = ggplot2::element_blank()), frame)
manifest_hidden <- adapter$ggplot_manifest(strokes$state, "points", hidden)$elements
hidden_axis <- Filter(function(element) element$gid == gid, manifest_hidden)[[1L]]
stopifnot(!fields(strokes$state, hidden)$visible$value,
          fields(strokes$state, hidden)$visible$value_original,
          identical(unlist(hidden_axis$bbox), c(-1, -1, 0, 0)),
          identical(adapter$render_ggplot(strokes$state, hidden), native_hidden$svg),
          identical(adapter$render_ggplot(strokes$state,
            stats::setNames(list(list(visible = TRUE)), gid)), strokes$svg))
invalid <- stats::setNames(list(list(width = -1)), gid)
stopifnot(inherits(try(adapter$render_ggplot(strokes$state, invalid), silent = TRUE), "try-error"))
blank <- adapter$capture_ggplot(base +
  ggplot2::theme(axis.ticks.x.bottom = ggplot2::element_blank()), frame)
stopifnot(is.null(fields(blank$state)$width), is.null(fields(blank$state)$length))
"""


TICK_SIDES_PROBE = r"""
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L), quietly = TRUE))) quit(status = 77L)
frame <- c(360, 260)
data <- data.frame(x = 1:10, y = 1:10)
base <- ggplot2::ggplot(data, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::guides(x = ggplot2::guide_axis(minor.ticks = TRUE)) +
  ggplot2::theme(axis.ticks.x.bottom = ggplot2::element_line(colour = "#336699"))
fields <- function(state, edits = list()) {
  items <- adapter$ggplot_manifest(state, "points", edits)$elements
  axes <- Filter(function(element) element$role == "axes", items)[[1L]]$native_axes
  axes$tick_sides
}
axis_parts <- function(state, edits = list()) adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  table <- adapter$ggplot_table_for(state, edits)$table
  lapply(c(bottom = "b", top = "t", left = "l", right = "r"), function(letter) {
    axis <- table$grobs[[which(table$layout$name == paste0("axis-", letter))]]$children[["axis"]]
    is_polyline <- function(grob) inherits(grob, "polyline")
    marks <- unlist(lapply(axis$grobs, function(grob) {
      if (is_polyline(grob)) list(grob) else if (identical(grob$name, "ticks")) {
        Filter(is_polyline, as.list(grob$children))
      }
    }), recursive = FALSE)
    labels <- Filter(function(grob) inherits(grob, "titleGrob"), axis$grobs)
    along <- if (letter %in% c("b", "t")) "x" else "y"
    list(ticks = unname(lapply(marks, function(grob) unique(as.numeric(grob[[along]])))),
         labels = unlist(lapply(labels, function(grob) grob$children[[1L]]$label), use.names = FALSE))
  })
})$value

captured <- adapter$capture_ggplot(base, frame)
state <- captured$state
initial <- axis_parts(state)
original <- fields(state)$original
props <- c("ticks_bottom", "ticks_top", "ticks_left", "ticks_right")
stopifnot(identical(unname(unlist(original[props])), c(TRUE, FALSE, TRUE, FALSE)))
default <- list(axes_0 = list(ticks_top = TRUE, ticks_right = TRUE,
                              ticks_bottom = FALSE, ticks_left = FALSE))
mirrored <- axis_parts(state, default)
stopifnot(identical(mirrored$top$ticks, initial$bottom$ticks), length(mirrored$top$ticks) == 2L,
          identical(mirrored$right$ticks, initial$left$ticks), !length(mirrored$top$labels),
          !length(mirrored$right$labels), !length(mirrored$bottom$ticks), !length(mirrored$left$ticks),
          identical(mirrored$bottom$labels, initial$bottom$labels),
          identical(mirrored$left$labels, initial$left$labels),
          identical(fields(state, default)$edited, default$axes_0))

projected <- default
projected$axes_0$xlim <- c(1, 10)
projected$axes_0$invert_x <- TRUE
projected[["axis.x.bottom.ticks"]] <- list(major_mode = "fixed", major_values = c(1, 4, 7, 10))
fixed <- axis_parts(state, projected)
stopifnot(isTRUE(all.equal(fixed$top$ticks[[1L]], 1 - c(0, 3, 6, 9) / 9)),
          identical(fixed$bottom$labels, c("1", "4", "7", "10")),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(state$layers, adapter$capture_ggplot(base, frame)$state$layers))
invalid <- list(axes_0 = list(ticks_top = "false"))
stopifnot(inherits(try(adapter$render_ggplot(state, invalid), silent = TRUE), "try-error"))

blank <- base + ggplot2::theme(axis.ticks.x.bottom = ggplot2::element_blank(),
                               axis.minor.ticks.x.bottom = ggplot2::element_blank())
blank_state <- adapter$capture_ggplot(blank, frame)$state
stopifnot(!fields(blank_state)$original$ticks_bottom,
          length(axis_parts(blank_state, list(axes_0 = list(ticks_bottom = TRUE)))$bottom$ticks) == 1L)
secondary <- base + ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis())
secondary_state <- adapter$capture_ggplot(secondary, frame)$state
without <- axis_parts(secondary_state, list(axes_0 = list(ticks_top = FALSE)))
stopifnot(fields(secondary_state)$original$ticks_top, !length(without$top$ticks),
          identical(without$top$labels, axis_parts(secondary_state)$top$labels))

unavailable <- base + ggplot2::guides(x = "none")
no_guide <- adapter$capture_ggplot(unavailable, frame)$state
unsupported <- list(axes_0 = list(ticks_top = TRUE))
stopifnot(is.null(fields(no_guide)$original$ticks_bottom), is.null(fields(no_guide)$original$ticks_top),
          inherits(try(adapter$render_ggplot(no_guide, unsupported), silent = TRUE), "try-error"))
"""


FACET_TICK_APPEARANCE_PROBE = r"""
d <- expand.grid(x=c(1,5,10), area=c("A","B"), col=c("X","Y","Z"))
d$y <- as.integer(d$area) * 8 + d$x
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::facet_grid(ggplot2::vars(area), ggplot2::vars(col), scales = "free", axes="all") +
  ggplot2::scale_x_continuous(guide=ggplot2::guide_axis(minor.ticks=TRUE)) +
  ggplot2::theme_bw(base_size=8)
frame <- c(432, 315)
capture <- adapter$capture_ggplot(plot, frame)
state <- capture$state
gid <- "axis.x.bottom.ticks.axes_5"
peer <- "axis.x.bottom.ticks.axes_2"
axis_gid <- "axes_5"
changes <- stats::setNames(list(list(
  major_mode = "fixed", major_values = list(1,5,10), format = "%.2f", minor_visible = TRUE, minor_mode = "step",
  minor_step = 2, minor_format = "%.1f",
  fontsize = 18, color="#800080", weight = "bold", style = "italic", rotation = 20,
  direction = "inout", length = 7, width = 1.1, minor_length = 3, minor_width = 0.6
), list(xlim = c(1,10), invert_x = TRUE)), c(gid, axis_gid))
fields <- function(manifest,gid) {
  entry <- Filter(function(element) identical(element$gid,gid), manifest$elements)[[1L]]
  stats::setNames(lapply(entry$editable, `[[`, "value"), vapply(entry$editable,`[[`,character(1L),"prop"))
}
marks <- function(scene, gid) {
  row <- state$text_targets[[gid]]$slots[[1L]]
  grob <- scene$table$grobs[[row]]$children[["axis"]]
  slot <- which(vapply(grob$grobs, function(g) g$name == "ticks", logical(1)))
  lapply(unname(grob$grobs[[slot]]$children), function(g) {
    list(x = as.numeric(g$x), y = grid::convertY(g$y, "bigpts", valueOnly=TRUE), pg = g$gp)
  })
}
expected_labels <- as.list(c("1.00","5.00","10.00","2.0","4.0","6.0","8.0"))
hot <- adapter$ggplot_manifest(state, "probe", changes)
chosen <- fields(hot,gid)
also <- fields(hot,peer)
stopifnot(chosen$fontsize == 18, chosen$rotation == 20,
          chosen$weight == "bold", chosen$style == "italic", chosen$direction == "inout",
          also$fontsize == 6.4, also$rotation == 0,
          chosen$format == "%.2f", also$format == "%.2f",
          chosen$minor_format == "%.1f", also$minor_format == "%.1f")
rendered <- adapter$with_svg_frame(frame, function(device) {
  scene <- adapter$ggplot_table_for(state, changes)
  stopifnot(identical(scene$text[[gid]]$tick_labels, expected_labels),
            identical(scene$text[[peer]]$tick_labels, expected_labels),
            scene$text[[gid]]$paint$fontsize == 18, scene$text[[peer]]$paint$fontsize == 6.4)
  first <- marks(scene, gid)
  second <- marks(scene, peer)
  stopifnot(length(first) == 2L, length(second) == 2L,
            isTRUE(all.equal(first[[1L]]$x, second[[1L]]$x)),
            isTRUE(all.equal(diff(range(first[[1L]]$y)), 7)), isTRUE(all.equal(diff(range(first[[2L]]$y)), 3)),
            first[[1L]]$pg$lwd > second[[1L]]$pg$lwd, first[[2L]]$pg$lwd != second[[2L]]$pg$lwd)
  scene
})$value
after <- adapter$render_ggplot(state, changes)
stopifnot(!identical(capture$svg, after), identical(capture$svg, adapter$render_ggplot(state)))
hidden <- changes
hidden[[gid]]$visible <- FALSE
invisible_ticks <- adapter$ggplot_manifest(state, "probe", hidden)
stopifnot(!fields(invisible_ticks,gid)$visible, fields(invisible_ticks,peer)$visible)
cat("Verified numerical minor labels on both facets, with local typography, mark direction and sizes, visibility and exact undo.\n")
"""


COLOURBAR_APPEARANCE_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)

library(ggplot2)
palette <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector=FALSE)
d <- data.frame(x=1:8, y=c(4,5,4,5,2,4,6,8), z=1:8, group=rep(c("A","B"),4L))
plot <- ggplot(d,aes(x,y)) + geom_point(aes(colour=z,shape=group),size=3) +
  geom_tile(data=d,aes(x=x,y=0,fill=z),width=.8,height=.6) +
  scale_colour_gradient(name="Continuous", breaks=c(1,4,8), low="#303080",high="#FA9520",
                        guide=guide_colourbar(order=1)) +
  scale_fill_gradient(name="Reference", breaks=c(1,6,8),low="#112244",high="#50EEDD",
                      guide=guide_colourbar(order=2)) +
  scale_shape_discrete(name="Category", guide=guide_legend(order=3)) + theme_bw(base_size=8)
capture <- adapter$capture_ggplot(plot, c(420,300), palette)
state <- capture$state
elements <- state$colour_scales
target_gid <- names(Filter(function(target) target$title == "Continuous",elements))[[1L]]
target <- elements[[target_gid]]
target_index <- target$index
edits <- stats::setNames(list(list(orientation="horizontal",extend="both",vmin=2,vmax=7,cmap="plasma")),target_gid)

patch <- edits
patch[[target_gid]]$label <- "Measured signal"
patch[[target_gid]]$tick_fontsize <- 17
patch[[target_gid]]$tick_color <- "#782280"
fields <- function(scene, gid) {
  element <- Filter(function(x) identical(x$gid, gid), scene$elements)[[1L]]
  element$color_scale$appearance
}
style <- adapter$ggplot_manifest(state, "appearance", patch)
selected <- fields(style, target_gid)
other_gid <- names(Filter(function(target) target$title == "Reference",elements))[[1L]]
other <- fields(style, other_gid)
stopifnot(selected$current$label == "Measured signal", selected$original$label == "Continuous",
          selected$current$tick_fontsize == 17, selected$current$tick_color == "#782280",
          identical(other$current, other$original))
image <- function(updates) paste(adapter$render_ggplot(state,updates),collapse="\n")
styled <- image(patch)
stopifnot(grepl("Measured signal",styled,fixed=TRUE),
          grepl("font-size: 17.00px;fill: #782280",styled,fixed=TRUE))
hidden <- patch
hidden[[target_gid]]$visible <- FALSE
invisible_svg <- image(hidden)
stopifnot(!grepl("Measured signal", invisible_svg, fixed=TRUE),
          grepl(">Reference</text>",invisible_svg,fixed=TRUE),
          grepl(">Category</text>",invisible_svg,fixed=TRUE),
          !fields(adapter$ggplot_manifest(state,"appearance",hidden),target_gid)$current$visible)
stopifnot(identical(image(list()),paste(capture$svg,collapse="\n")))
renamed <- patch
renamed[[target_gid]]$label <- ""
empty_svg <- image(renamed)
stopifnot(!grepl("Measured signal",empty_svg,fixed=TRUE),
          !grepl(">Continuous</text>",empty_svg,fixed=TRUE))
narrow <- patch
narrow[[paste0(target_gid,".ticks")]] <- list(color = "#223344")
modified <- fields(adapter$ggplot_manifest(state,"appearance",narrow),target_gid)
stopifnot(modified$current$tick_color == "#223344",
          grepl("font-size: 17.00px;fill: #223344",image(narrow),fixed=TRUE))
cat("Verified shared R colourbar state, title rename and clear, fonts, hide and explicit undo through the adapter.\n")
"""


MAPPED_BAR_GROUP_WIDTH_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)

library(ggplot2)
palettes <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector=FALSE)
d <- expand.grid(group = c("Control", "Treatment"), facet = c("Early", "Late"), visit = c("A", "B", "C"))
d <- d[!(d$group == "Control" & d$facet == "Late" & d$visit == "C"), ]
d$n <- as.integer(d$visit) * as.integer(d$facet) + as.integer(d$group)
p <- ggplot(d, aes(visit, n, fill = group)) +
  geom_col(position = position_dodge2(preserve = "single"), colour = "#16161B", linewidth = .5) +
  scale_fill_manual(values = c(Control = "#429FC5", Treatment = "#B15E4E")) +
  facet_wrap(vars(facet)) + theme_bw()
frame <- c(432, 306)
baseline <- adapter$capture_ggplot(p, frame, palettes)
state <- baseline$state
gid <- "layer.1.panel.2.bars.group.1"
parents <- c("layer.1.panel.1.bars", "layer.1.panel.2.bars")
target <- state$bar_targets[[gid]]
stopifnot(target$group$label == "Control", length(target$group$rows) == 2L,
          target$span$width > .22, length(target$geometry$paths) == 0L)
group_rows <- target$group$rows
render_scene <- function(edits) {
  adapter$with_svg_frame(frame, function(device) adapter$ggplot_table_for(state, edits))$value
}
rect <- function(table, gid) {
  host <- state$bar_targets[[gid]]
  adapter$ggplot_bar_rect(table$grobs[[host$slot]], host)
}
before <- lapply(parents, function(gid) rect(state$table, gid))
patch <- stats::setNames(list(list(bar_width = .22)), gid)
scene <- render_scene(patch)
after <- lapply(parents, function(gid) rect(scene$table, gid))
old <- before[[2L]]
edited <- after[[2L]]
centre <- function(r) as.numeric(r$x) + as.numeric(r$width) / 2
rest <- setdiff(seq_along(old$x), group_rows)
stopifnot(identical(before[[1L]], after[[1L]]),
          isTRUE(all.equal(centre(old), centre(edited), tolerance = 1e-12)),
          identical(old$height, edited$height), identical(old$y, edited$y), identical(old$gp, edited$gp),
          identical(as.numeric(old$width)[rest], as.numeric(edited$width)[rest]),
          all(as.numeric(edited$width)[group_rows] < as.numeric(old$width)[group_rows]))
group_data <- function(applied) {
  manifest <- adapter$ggplot_manifest(state, "bar-groups", applied)
  Filter(function(el) el$gid == gid, manifest$elements)[[1L]]
}
selection <- group_data(patch)
stopifnot(length(selection$geometry$paths) == 2L, selection$bar_series$current$bar_width == .22)
broad <- stats::setNames(list(list(bar_width = .39)), parents[[2L]])
stopifnot(group_data(broad)$bar_series$current$bar_width == .39,
          group_data(c(patch, broad))$bar_series$current$bar_width == .22,
          identical(adapter$render_ggplot(state, c(broad, patch)),
                    adapter$render_ggplot(state, c(patch, broad))),
          identical(adapter$render_ggplot(state), baseline$svg))
p_ambiguous <- suppressMessages(p + scale_fill_manual(values = c(Control = "#429FC5", Treatment = "#429FC5")))
same_colours <- adapter$capture_ggplot(p_ambiguous, frame, palettes)$state
stopifnot(identical(same_colours$bar_targets[[gid]]$group$rows, group_rows),
          length(same_colours$bar_targets[["layer.1.panel.2.bars.group.2"]]$group$rows) == 3L)
cat("Verified category-specific geometry and bar width with unchanged centers, heights, colors, and sibling panel.\n")
"""


LOG_COLOUR_SCALE_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)

library(ggplot2)
catalog <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector=FALSE)
d <- data.frame(x = 1:8, y = c(2, 3, 4, 2, 1, 2, 3, 4),
                amount = c(1, 10, 100, 1000, NA, -1, 0, Inf), ref = 8:1)
p <- ggplot(d, aes(x,y)) +
  geom_point(aes(colour = amount), size = 3) +
  geom_point(aes(x=x, y=y+.3, colour = amount), shape = 15) +
  geom_tile(aes(x=x, y=0, fill=ref), width=.8, height=.35) +
  scale_colour_gradient(name="Dose", transform="log10", na.value="#CCCCCC",
                        guide=guide_colourbar(position="right")) +
  scale_fill_gradient(name="Reference",guide=guide_colourbar(position="left")) +
  theme_bw()
captured <- suppressWarnings(adapter$capture_ggplot(p, c(432, 288), catalog))
state <- captured$state
gid <- "legend.right.1.colourbar"
tick_gid <- paste0(gid, ".ticks")
target <- state$colour_scales[[gid]]
stopifnot(identical(target$limits, c(1, 1000)),
          identical(target$members[["1"]]$colour, suppressWarnings(log10(d$amount))))
test <- stats::setNames(list(list(vmin=10, vmax=100, cmap="plasma", orientation="horizontal", extend="both"),
                            list(format="%.1f")), c(gid, tick_gid))
fresh <- adapter$ggplot_remap_colour_scales(state,test[gid],state$built$plot$guides$params)
oracle <- suppressMessages(p + continuous_scale(
  aesthetics="colour", palette=adapter$ggplot_palette(catalog$palettes$plasma),
  name="Dose", transform="log10", limits=c(10, 100), oob=scales::squish, na.value="#CCCCCC"
))
known <- suppressWarnings(ggplot_build(oracle))
missing <- !is.finite(target$members[["1"]]$colour)
stopifnot(identical(fresh$data[[1L]]$colour, known$data[[1L]]$colour),
          identical(fresh$data[[2L]]$colour, known$data[[2L]]$colour),
          sum(missing) == 4L,
          identical(fresh$data[[1L]]$colour[missing], state$built$data[[1L]]$colour[missing]),
          identical(fresh$data[[2L]]$colour[missing], state$built$data[[2L]]$colour[missing]),
          identical(fresh$data[[3L]], state$built$data[[3L]]),
          identical(fresh$data[[1L]]$x, state$built$data[[1L]]$x))
svg <- adapter$render_ggplot(state,test)
stopifnot(grepl(">10.0</text>", svg, fixed=TRUE), grepl(">100.0</text>", svg,fixed=TRUE),
          identical(adapter$render_ggplot(state),captured$svg))
shown <- adapter$ggplot_manifest(state,"test",test)
item <- Filter(function(el) el$gid == gid, shown$elements)[[1L]]
stopifnot(identical(item$color_scale$limits, as.list(c(10,100))))
bad <- stats::setNames(list(list(vmin=0)), gid)
stopifnot(inherits(try(adapter$render_ggplot(state,bad),silent=TRUE),"try-error"))
cat("Verified logarithmic range in original units with stock ggplot2 color mapping, endpoints and missing values.\n")
"""


POINT_PAINT_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
frame <- c(440, 260)
d <- expand.grid(x = 1:4, facet = c("Early", "Late"))
d$y <- sqrt(d$x)
late <- d$facet == "Late"
p <- ggplot(d, aes(x, y)) + facet_wrap(vars(facet)) + theme_bw()
stock <- p + geom_point(shape = 21, fill = "#AE745B", colour = "#244A5A", size = 3, stroke = 1)
initial <- adapter$capture_ggplot(stock, frame, pal)
state <- initial$state
gid <- "layer.1.panel.2.scatter"
early <- "layer.1.panel.1.scatter"
stroke <- 2.1 / adapter$ggplot_lwd_pt * 2 / ggplot2::.stroke
size <- 121
group <- state$point_targets[[gid]]
stopifnot(identical(group$values$facecolor, "#AE745B"),
          identical(group$values$edgecolor, "#244A5A"),
          length(group$values$linewidth) == 1L, group$values$linewidth > 0, group$values$alpha == 1)
stamp <- list(facecolor = "#4C845E", edgecolor = "#452E78", linewidth = 2.1, size = size, alpha = .45)
edits <- setNames(list(stamp), gid)
authored <- p + geom_point(shape = 21, fill = ifelse(late, stamp$facecolor, "#AE745B"),
                           colour = ifelse(late, stamp$edgecolor, "#244A5A"),
                           stroke = ifelse(late, stroke, 1),
                           size = ifelse(late, sqrt(size) / ggplot2::.pt, 3),
                           alpha = ifelse(late, stamp$alpha, NA_real_))
result <- adapter$render_ggplot(state, edits)
stopifnot(identical(result, adapter$capture_ggplot(authored, frame, pal)$svg),
          identical(adapter$render_ggplot(state), initial$svg))
get_element <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
old <- adapter$ggplot_manifest(state, "dots")
new <- adapter$ggplot_manifest(state, "dots", edits)
stopifnot(identical(get_element(old, early), get_element(new, early)),
          identical(get_element(new, gid)$point_series$current[names(stamp)], stamp))
no_fill <- setNames(list(list(facecolor = "none", linewidth = 0)), gid)
hidden <- p + geom_point(shape = 21, size = 3, fill = ifelse(late, NA, "#AE745B"),
                         colour = "#244A5A", stroke = ifelse(late, 0, 1))
stopifnot(identical(adapter$render_ggplot(state, no_fill), adapter$capture_ggplot(hidden, frame, pal)$svg))
bad <- setNames(list(list(linewidth = -1)), gid)
stopifnot(inherits(try(adapter$render_ggplot(state, bad), silent = TRUE), "try-error"))

varying <- p + geom_point(aes(fill = x), shape = 21, colour = "black", stroke = 1)
mapped <- adapter$capture_ggplot(varying, frame, pal)$state$point_targets[[gid]]$values
stopifnot(!"facecolor" %in% names(mapped), all(c("edgecolor", "linewidth") %in% names(mapped)))
mapped_stroke <- p + geom_point(aes(stroke = x), shape = 21, colour = "black")
stroke_fields <- adapter$capture_ggplot(mapped_stroke, frame, pal)$state$point_targets[[gid]]$values
stopifnot(!"linewidth" %in% names(stroke_fields), "edgecolor" %in% names(stroke_fields))
solid <- p + geom_point(aes(shape = factor(x %% 2)), colour = "#87AB34")
groups <- adapter$capture_ggplot(solid, frame, pal)
group_gid <- "layer.1.panel.2.scatter.group.1"
styles <- groups$state$point_targets[[group_gid]]$values
stopifnot(identical(styles$facecolor, "#87AB34"), !"edgecolor" %in% names(styles),
          !"linewidth" %in% names(styles))
paint <- "#9C1343"
expected <- p + geom_point(aes(shape = factor(x %% 2)), colour = ifelse(d$x %% 2 == 0, paint, "#87AB34")) +
  guides(shape = guide_legend(override.aes = list(colour = c(paint, "#87AB34"))))
stopifnot(identical(adapter$render_ggplot(groups$state, setNames(list(list(facecolor = paint)), group_gid)),
                    adapter$capture_ggplot(expected, frame, pal)$svg))
"""


MAPPED_POINT_SHAPE_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)

library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector=FALSE)
d <- expand.grid(x=1:4, group=c("Control","Treatment"), panel=c("Early","Late"))
d <- d[!(d$group=="Treatment" & d$panel=="Early"), ]
d$y <- d$x + 3*as.integer(d$group)
d$z <- seq_len(nrow(d))
p <- ggplot(d, aes(x,y)) + geom_point(aes(shape=group,colour=z), size=3) +
  scale_colour_gradient(name="Dose") + facet_wrap(vars(panel)) +
  labs(shape="Group") + theme_bw()
frame <- c(420, 285)
before <- adapter$capture_ggplot(p, frame, pal)
state <- before$state
gid <- "layer.1.panel.2.scatter.group.1"
peer <- "layer.1.panel.1.scatter.group.1"
stopifnot(identical(state$point_targets[[gid]]$group$links, peer))
changed <- stats::setNames(list(list(marker="s")), gid)
other_side <- stats::setNames(list(list(marker="s")), peer)
actual <- adapter$render_ggplot(state, changed)
oracle <- adapter$capture_ggplot(p + scale_shape_manual(values=c(Control=15L,Treatment=17L)), frame, pal)
stopifnot(!identical(actual,before$svg), identical(actual,oracle$svg),
          identical(actual,adapter$render_ggplot(state,other_side)),
          identical(before$svg,adapter$render_ggplot(state)))
single <- function(bundle, name) Filter(function(el) el$gid==name,bundle$elements)[[1L]]
manifest <- adapter$ggplot_manifest(state,"dots",changed)
local <- single(manifest,gid)
sibling <- single(manifest,peer)
stopifnot(local$point_series$current$marker=="s", sibling$point_series$current$marker=="s",
          !identical(local$bbox,sibling$bbox),
          setequal(unlist(local$alias_covers$marker$marker), c(peer, paste0(c(gid, peer), ".facet"))))
colors <- stats::setNames(list(list(cmap="plasma",vmin=2,vmax=11)), "legend.right.1.colourbar")
combined <- adapter$render_ggplot(state,c(changed,colors))
stopifnot(!identical(actual,combined))
conflict <- c(changed, stats::setNames(list(list(marker="D")), peer))
stopifnot(inherits(try(adapter$render_ggplot(state,conflict),silent=TRUE), "try-error"))

clipped_data <- d
clipped_data$x[clipped_data$panel == "Early"] <- clipped_data$x[clipped_data$panel == "Early"] + 10
with_clipping <- (p %+% clipped_data) + coord_cartesian(xlim = c(0.5, 4.5), expand = FALSE)
clipped <- adapter$capture_ggplot(with_clipping, frame, pal)$state
stopifnot(identical(clipped$point_targets[[peer]]$bbox, as.list(c(-1, -1, 0, 0))))
revealed <- adapter$render_ggplot(clipped, c(changed, list(axes_2 = list(xlim = c(0.5, 14.5)))))
expected <- adapter$capture_ggplot(suppressMessages(with_clipping +
  coord_cartesian(xlim = c(0.5, 14.5), expand = FALSE) +
  scale_shape_manual(values = c(Control = 15L, Treatment = 17L))), frame, pal)
stopifnot(identical(revealed, expected$svg))
# Author the same marker, size and opacity in ggplot2 and in its guide.
# Compare the whole native SVG, including both facets, the untouched group and colorbar.
new_size <- 324
new_alpha <- .45
new_size_mm <- sqrt(new_size) / ggplot2::.pt
patch <- stats::setNames(list(list(marker = "s", size = new_size), list(alpha = new_alpha)), c(gid, peer))
stopifnot(setequal(names(state$point_targets[[gid]]$values), c("marker", "size", "alpha")),
          state$point_targets[[gid]]$values$alpha == 1)
authored <- ggplot(d, aes(x, y)) +
  geom_point(aes(shape = group, colour = z), size = ifelse(d$group == "Control", new_size_mm, 3),
             alpha = ifelse(d$group == "Control", new_alpha, NA_real_)) +
  scale_shape_manual(values = c(Control = 15L, Treatment = 17L)) +
  scale_colour_gradient(name = "Dose") + facet_wrap(vars(panel)) +
  labs(shape = "Group") + theme_bw() +
  guides(shape = guide_legend(override.aes = list(size = c(new_size_mm, 3),
                                                 alpha = c(new_alpha, NA_real_))))
styled <- adapter$capture_ggplot(authored, frame, pal)
stopifnot(identical(adapter$render_ggplot(state, patch), styled$svg),
          identical(adapter$render_ggplot(state), before$svg))
reported <- adapter$ggplot_manifest(state, "dots", patch)
oracle_manifest <- adapter$ggplot_manifest(styled$state, "dots")
stopifnot(identical(single(reported, "axes_2")$bbox, single(oracle_manifest, "axes_2")$bbox))
for (target in c(gid, peer)) {
  element <- single(reported, target)
  stopifnot(identical(element$point_series$current[c("marker", "size", "alpha")],
                      list(marker = "s", size = new_size, alpha = new_alpha)),
            setequal(unlist(element$alias_pure), c("marker", "size", "alpha")))
}
size_conflict <- stats::setNames(list(list(size = 100), list(size = 36)), c(gid, peer))
stopifnot(inherits(try(adapter$render_ggplot(state, size_conflict), silent = TRUE), "try-error"))
override <- p + guides(shape = guide_legend(override.aes = list(size = 2, alpha = .8)))
override_state <- adapter$capture_ggplot(override, frame, pal)$state
stopifnot(identical(names(override_state$point_targets[[gid]]$values), "marker"))
mapped <- ggplot(d, aes(x, y)) + geom_point(aes(shape = group, colour = z, size = z, alpha = z)) +
  scale_colour_gradient(name = "Dose") + facet_wrap(vars(panel)) + labs(shape = "Group") +
  guides(size = "none", alpha = "none")
mapped_state <- adapter$capture_ggplot(mapped, frame, pal)$state
stopifnot(identical(names(mapped_state$point_targets[[gid]]$values), "marker"),
          inherits(try(adapter$render_ggplot(mapped_state, patch), silent = TRUE), "try-error"))
scoped <- paste0(gid, ".facet")
local_patch <- stats::setNames(list(list(size = 144, marker = "D", alpha = .45)), scoped)
local_scene <- adapter$ggplot_manifest(state, "dots", local_patch)
point_local <- single(local_scene, scoped)
stopifnot(endsWith(point_local$label, "Late · Control"),
          point_local$point_series$current$size == 144,
          point_local$point_series$current$marker == "D",
          point_local$point_series$current$alpha == .45,
          single(local_scene, gid)$point_series$current$marker == state$point_targets[[gid]]$values$marker,
          single(local_scene, paste0(peer, ".facet"))$point_series$current$alpha == 1)
local_table <- adapter$with_svg_frame(frame, function(dev) adapter$ggplot_table_for(state, local_patch)$table)$value
positions <- function(table, id) {
  target <- state$point_targets[[id]]
  parent <- table$grobs[[target$slot]]
  adapter$ggplot_point_grob(parent, target)
}
start <- positions(state$table, scoped)
changed <- positions(local_table, scoped)
own <- state$point_targets[[scoped]]$group$rows
others <- setdiff(seq_along(start$pch), own)
stopifnot(identical(positions(local_table, peer), positions(state$table, peer)),
          identical(start$x, changed$x), identical(start$y, changed$y),
          all(changed$pch[own] == 18L), identical(changed$pch[others], start$pch[others]),
          all(abs(changed$gp$fontsize[own] - changed$gp$lwd - 12) < 1e-10),
          all(grDevices::col2rgb(changed$gp$col[own], alpha = TRUE)[4L, ] == 115))
guide_slots <- grep("^guide-box-", state$table$layout$name)
stopifnot(identical(local_table$grobs[guide_slots], state$table$grobs[guide_slots]),
          identical(adapter$render_ggplot(state), before$svg))
both <- c(local_patch, stats::setNames(list(list(size = 80, marker = "s")), peer))
stopifnot(identical(adapter$render_ggplot(state, both), adapter$render_ggplot(state, rev(both))))
jsonlite::write_json(local_scene, "scoped-point-manifest.json", auto_unbox = TRUE, null = "null", digits = NA)
cat("Verified grouped scatter styles and keys on both facets against authored R, including reflow and protected mappings.\n")
"""


POINT_LEGEND_CLASSES_PROBE = r"""
library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
d <- expand.grid(x = 1:3, category = c("A", "B"), panel = c("Early", "Late"))
d <- d[!(d$category == "B" & d$panel == "Early"), ]
d$y <- d$x * 2 + as.integer(d$category)
d$other <- factor(ifelse(d$x %% 2, "A", "B"))
chosen <- d$category == "A"
paint <- c(A = "#285777", B = "#285777")
frame <- c(420, 285)
base <- ggplot(d, aes(x, y)) + facet_wrap(vars(panel)) + theme_bw()
late <- "layer.1.panel.2.scatter.group.1"
early <- "layer.1.panel.1.scatter.group.1"
other <- "layer.1.panel.2.scatter.group.2"
size <- 196
size_mm <- sqrt(size) / ggplot2::.pt
alpha <- 0.6
lookup <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
verify <- function(plot, authored, edit) {
  capture <- adapter$capture_ggplot(plot, frame, pal)
  state <- capture$state
  expected <- c(early, late, other)
  stopifnot(setequal(names(state$point_targets), c(expected, paste0(expected, ".facet"))),
            identical(state$point_targets[[late]]$group$label, "A"),
            all(names(edit) %in% names(state$point_targets[[late]]$values)))
  patches <- setNames(list(edit), late)
  actual <- adapter$render_ggplot(state, patches)
  native <- adapter$capture_ggplot(authored, frame, pal)
  stopifnot(identical(actual, native$svg), !identical(actual, capture$svg),
            identical(adapter$render_ggplot(state), capture$svg))
  manifest <- adapter$ggplot_manifest(state, "points", patches)
  for (gid in c(early, late)) {
    selected <- lookup(manifest, gid)
    stopifnot(identical(selected$point_series$current[names(edit)], edit))
  }
  stopifnot(identical(lookup(manifest, other)$point_series$current, state$point_targets[[other]]$values))
  invisible(state)
}
colour <- scale_colour_manual(name = "Category", values = paint)
dots <- base + geom_point(aes(colour = category), shape = 19, size = 3) + colour
expected <- base + geom_point(aes(colour = category), shape = ifelse(chosen, 15L, 19L),
                             size = ifelse(chosen, size_mm, 3), alpha = ifelse(chosen, alpha, NA_real_)) +
  colour + guides(colour = guide_legend(override.aes = list(shape = c(15L, 19L), size = c(size_mm, 3),
                                                            alpha = c(alpha, NA_real_))))
points <- verify(dots, expected, list(size = size, marker = "s", alpha = alpha))
stopifnot(!"facecolor" %in% names(points$point_targets[[late]]$values))

fill <- scale_fill_manual(name = "Category", values = paint)
ring <- base + geom_point(aes(fill = category), shape = 21, colour = "#355072", stroke = 1) + fill
stroke <- 2.1 / adapter$ggplot_lwd_pt * 2 / ggplot2::.stroke
expected_ring <- base + geom_point(aes(fill = category), shape = 21, colour = "#355072",
                                  stroke = ifelse(chosen, stroke, 1)) +
  fill + guides(fill = guide_legend(override.aes = list(stroke = c(stroke, 1))))
rings <- verify(ring, expected_ring, list(linewidth = 2.1))
stopifnot(!"facecolor" %in% names(rings$point_targets[[late]]$values),
          "edgecolor" %in% names(rings$point_targets[[late]]$values))

shape <- scale_shape_manual(name = "Category", values = c(A = 19L, B = 17L))
both <- base + geom_point(aes(colour = category, shape = category), size = 3) + colour + shape
expected_both <- base + geom_point(aes(colour = category, shape = category), size = ifelse(chosen, size_mm, 3)) +
  colour + scale_shape_manual(name = "Category", values = c(A = 15L, B = 17L)) +
  guides(colour = guide_legend(override.aes = list(size = c(size_mm, 3))))
verify(both, expected_both, list(size = size, marker = "s"))

mismatch <- base + geom_point(aes(colour = category, shape = other), size = 3) + colour + shape
stopifnot(!length(adapter$capture_ggplot(mismatch, frame, pal)$state$point_targets))
override <- dots + guides(colour = guide_legend(override.aes = list(shape = 21)))
override_state <- adapter$capture_ggplot(override, frame, pal)$state
stopifnot("size" %in% names(override_state$point_targets[[late]]$values),
          !"marker" %in% names(override_state$point_targets[[late]]$values))
"""


MAPPED_ERRORBAR_CAPS_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)
library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
data <- expand.grid(x=1:3, treatment=c("A","B"), facet=c("Early","Late"))
data <- data[!(data$facet=="Early" & data$treatment=="B"), ]
data$y <- 2+data$x + as.integer(data$treatment)
p <- ggplot(data, aes(x, y, colour = treatment)) +
    geom_errorbar(aes(ymin=y-.5,ymax=y+.5),position=position_dodge(width=.5),width=.24) +
    geom_point() + facet_wrap(vars(facet)) + theme_bw()
capture <- new.env(parent=emptyenv())
built <- ggplot_build(adapter$ggplot_observe_colour_maps(p,capture))
params <- built$plot$guides$params[[1L]]
keys <- params$decor
stopifnot(identical(as.list(GeomErrorbar)$draw_key, draw_key_path),
          setequal(names(keys),c("geom_errorbar","geom_point")),
          identical(capture$maps[[1L]]$input$colour,data$treatment),
          identical(capture$maps[[1L]]$rows,built$data[[1L]][names(capture$maps[[1L]]$rows)]))
frame <- c(432, 300)
keys <- c("layer.1.panel.1.errorbar.group.1", "layer.1.panel.2.errorbar.group.1",
          "layer.1.panel.2.errorbar.group.2")
style <- setNames(list(list(capsize = 0), list(capsize = 8, cap_thickness = 2)), keys[-1L])
get_element <- function(view, key) Filter(function(e) e$gid == key, view$elements)[[1L]]
guides <- function(table) {
  slots <- which(startsWith(table$layout$name, "guide-box"))
  value <- lapply(table$grobs[slots], function(grob) {
    if (inherits(grob, "zeroGrob")) return(NULL)
    adapter$with_svg_frame(frame, function(device) grid::grid.draw(grob))$svg
  })
  unname(Filter(Negate(is.null), value))
}
verify <- function(plot, axis, patches = list()) {
  captured <- adapter$capture_ggplot(plot, frame, pal)
  state <- captured$state
  targets <- state$line_targets
  stopifnot(identical(names(targets), keys), identical(targets[[keys[[2L]]]]$cap_rows, 1:3),
            identical(targets[[keys[[3L]]]]$cap_rows, 4:6))
  original <- adapter$ggplot_manifest(state, "mapped errorbars", patches)
  edited <- adapter$ggplot_manifest(state, "mapped errorbars", c(patches, style))
  early <- get_element(original, keys[[1L]])
  initial <- lapply(keys[-1L], function(key) get_element(original, key))
  changed <- lapply(keys[-1L], function(key) get_element(edited, key))
  stopifnot(identical(early, get_element(edited, keys[[1L]])),
            identical(changed[[1L]]$geometry$paths, initial[[1L]]$geometry$paths[c(2L, 5L, 8L)]),
            identical(changed[[2L]]$geometry$paths[c(2L, 5L, 8L)], initial[[2L]]$geometry$paths[c(2L, 5L, 8L)]),
            identical(changed[[1L]]$errorbar_series$current$capsize, 0),
            identical(changed[[2L]]$errorbar_series$current[names(style[[2L]])], style[[2L]]))
  caps <- changed[[2L]]$geometry$paths[-c(2L, 5L, 8L)]
  lengths <- vapply(caps, function(path) abs(diff(vapply(path$points, `[[`, numeric(1L), axis))) * frame[[axis]], numeric(1L))
  stopifnot(all(abs(lengths - 8) < 1e-10))
  base_scene <- adapter$with_svg_frame(frame, function(device) adapter$ggplot_table_for(state, patches))$value
  scene <- adapter$with_svg_frame(frame, function(device) adapter$ggplot_table_for(state, c(patches, style)))$value
  t0 <- base_scene$line_targets[[keys[[2L]]]]
  t <- scene$line_targets[[keys[[2L]]]]
  initial_line <- base_scene$table$grobs[[t0$slot]]$children[[t0$child]]
  line <- scene$table$grobs[[t$slot]]$children[[t$child]]
  expected <- c(rep(1L, 3), rep(3L, 3))
  stopifnot(identical(line$tavotto_cap_rows, rep(rep(1:6, expected), each = 2L)),
            identical(line$gp$col, rep(initial_line$gp$col, expected)),
            sum(abs(line$gp$lwd * adapter$ggplot_lwd_pt - 2) < 1e-10) == 6L,
            length(guides(scene$table)) == 1L, identical(guides(base_scene$table), guides(scene$table)),
            identical(adapter$render_ggplot(state), captured$svg))
  invalid <- setNames(list(list(linewidth = 2)), keys[[2L]])
  stopifnot(inherits(try(adapter$render_ggplot(state, invalid), silent = TRUE), "try-error"))
}
verify(p, 1L)
verify(p, 1L, list(axes_2 = list(invert_x = TRUE)))
h <- ggplot(data, aes(y, x, colour = treatment)) +
    geom_errorbar(aes(xmin=y-.5,xmax=y+.5),position=position_dodge(width=.5),width=.24) +
    geom_point() + facet_wrap(vars(facet)) + theme_bw()
verify(h, 2L)
identical_palette <- scale_colour_manual(values = c(A = "#25466A", B = "#25466A"))
verify(p + identical_palette, 1L)
cat("Category endcaps preserve uncertainty, sibling panels and shared legend keys.\n")
"""


MAPPED_RIBBON_GROUP_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)
library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
data <- expand.grid(x=1:4, treatment=c("A","B"), facet=c("Early","Late"))
data <- data[!(data$facet=="Early" & data$treatment=="B"), ]
data$y <- data$x + as.integer(data$treatment)
p <- ggplot(data,aes(x,y,fill=treatment)) +
  geom_ribbon(aes(ymin=y-.25,ymax=y+.25)) +
  facet_wrap(vars(facet)) + scale_fill_manual(values=c(A="#245780",B="#245780"))
frame <- c(432,300)
keys <- paste0('layer.1.panel.',c(1,2,2),'.ribbon.group.',c(1,1,2))
style <- setNames(list(list(alpha=.4)),keys[[3L]])
by_gid <- function(state,gid) Filter(function(e) e$gid == gid, state$elements)[[1L]]
guide_art <- function(table) {
  indices <- which(startsWith(table$layout$name,'guide-box'))
  nodes <- Filter(function(g) !inherits(g,'zeroGrob'),table$grobs[indices])
  lapply(nodes,function(node) adapter$with_svg_frame(frame,function(dev) grid::grid.draw(node))$svg)
}
verify <- function(p, views=list()) {
  captured <- adapter$capture_ggplot(p,frame,pal)
  state <- captured$state
  stopifnot(identical(names(state$ribbon_targets), keys))
  stopifnot(identical(lapply(state$ribbon_targets, function(t) t$group$label) |> unname(),list('A','A','B')))
  base <- adapter$ggplot_manifest(state,'band',views)
  applied <- adapter$ggplot_manifest(state,'band',c(views,style))
  for (gid in keys[-3L]) stopifnot(identical(by_gid(base,gid),by_gid(applied,gid)))
  before <- by_gid(base,keys[[3L]])
  after <- by_gid(applied,keys[[3L]])
  stopifnot(identical(before$geometry$paths,after$geometry$paths),
            identical(after$ribbon$current,list(alpha=.4)),
            isTRUE(all.equal(as.numeric(after$ribbon$original$alpha), 1)) && is.null(after$ribbon$original$facecolor))
  scene0 <- adapter$with_svg_frame(frame,function(dev) adapter$ggplot_table_for(state,views))$value
  scene1 <- adapter$with_svg_frame(frame,function(dev) adapter$ggplot_table_for(state,c(views,style)))$value
  target <- scene1$ribbon_targets[[keys[[3L]]]]
  grob <- scene1$table$grobs[[target$slot]]$children[[target$child]]
  fills <- Filter(function(g) inherits(g,'polygon'),adapter$ggplot_ribbon_leaves(grob))
  stopifnot(identical(vapply(fills,function(g) g$gp$fill,character(1L)),c('#245780FF','#24578066')),
            length(guide_art(scene0$table)) == 1L, identical(guide_art(scene0$table),guide_art(scene1$table)),
            identical(adapter$render_ggplot(state),captured$svg))
}
verify(p)
verify(p,list(axes_2=list(invert_x=TRUE)))
h <- ggplot(data,aes(y,x,fill=treatment)) +
  geom_ribbon(aes(xmin=y-.25,xmax=y+.25),orientation='y') +
  facet_wrap(vars(facet)) + scale_fill_manual(values=c(A="#245780",B="#245780"))
verify(h)
p2 <- ggplot(data,aes(x,y,fill=treatment)) +
  geom_ribbon(aes(ymin=y-.25,ymax=y+.25),colour='red') +
  facet_wrap(vars(facet)) + scale_fill_manual(values=c(A="#245780",B="#245780"))
stopifnot(length(adapter$capture_ggplot(p2,frame,pal)$state$ribbon_targets) == 0L)
cat('Mapped category ribbon opacity preserves both path and guide, including same-color categories and reversed axes.\n')
"""


SECONDARY_AXIS_PROBE = r"""
if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status = 77L)
library(ggplot2)
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
frame <- c(324, 224)
d <- data.frame(x=c(1,2,4,8), y=c(1,4,5,7))
plain <- ggplot(d,aes(x,y)) + geom_point() + labs(title="Dual labels", y="Signal") + theme_bw()
p <- plain + scale_x_continuous(sec.axis = dup_axis(name="Mirrored input"))
seed <- adapter$capture_ggplot(p,frame,pal)
keys <- names(seed$state$axes_targets)
stopifnot(identical(seed$state$axes_targets[["axes_0"]]$context,
                    rlang::hash(adapter$ggplot_decoration_context(seed$state$built))))
stopifnot(identical(keys,"axes_0"), identical(names(seed$state$axes_targets[[keys[[1L]]]]$axes),c("x","y")))
verify <- function(edit, source) {
  actual <- adapter$render_ggplot(seed$state,list(axes_0=edit))
  oracle <- adapter$capture_ggplot(source,frame,pal)
  stopifnot(identical(actual,oracle$svg), identical(adapter$render_ggplot(seed$state),seed$svg))
}
verify(list(xlim=c(2,8)),p+coord_cartesian(xlim=c(2,8),expand=c(left=FALSE,right=FALSE)))
verify(list(invert_x=TRUE),p+coord_cartesian(reverse='x'))
verify(list(xscale='log'),plain+scale_x_log10(sec.axis=dup_axis(name='Mirrored input')))
cat('Verified native duplicated secondary axis mirrors ranges, reversal and log scaling with exact SVG at rest.')

d$panel <- c("Control", "Control", "Trial", "Trial")
panels <- ggplot(d, aes(x, y)) + geom_point() + theme_bw() + facet_wrap(vars(panel), scales="free_y")
plain_facet <- panels + scale_x_continuous(sec.axis=dup_axis(name="Source x"))
faceted <- adapter$capture_ggplot(plain_facet, frame, pal)
view <- list(axes_2=list(xlim=c(1,8), xscale="log", invert_x=TRUE))
authored <- panels + scale_x_log10(sec.axis=dup_axis(name="Source x")) +
  coord_cartesian(xlim=c(1,8), expand=c(left=FALSE,right=FALSE), reverse="x")
actual <- adapter$render_ggplot(faceted$state, view)
expected <- adapter$capture_ggplot(authored, frame, pal)$svg
stopifnot(identical(actual, expected), identical(adapter$render_ggplot(faceted$state),faceted$svg))
fixed <- dup_axis(name="Fixed x", breaks=c(1,8),labels=c("first","last"))
ordinary <- plain + scale_x_continuous(sec.axis=fixed)
before <- adapter$capture_ggplot(ordinary,frame,pal)
actual <- adapter$render_ggplot(before$state,list(axes_0=list(xscale="log")))
expected <- adapter$capture_ggplot(plain+scale_x_log10(sec.axis=dup_axis(name="Fixed x",breaks=c(1,8),labels=c("first","last"))),frame,pal)$svg
stopifnot(identical(actual,expected),identical(adapter$render_ggplot(before$state),before$svg))
cat(' A fixed mirrored tick list and shared facets replayed exactly.\n')
auto <- plain + scale_x_continuous(n.breaks=6L, sec.axis=dup_axis(name="Auto x"))
invisible(ggplot_build(auto))
auto_capture <- adapter$capture_ggplot(auto, frame, pal)
actual <- adapter$render_ggplot(auto_capture$state,list(axes_0=list(xscale="log")))
expected <- adapter$capture_ggplot(plain+scale_x_log10(n.breaks=6L,sec.axis=dup_axis(name="Auto x")),frame,pal)$svg
stopifnot(identical(actual, expected), identical(adapter$render_ggplot(auto_capture$state),auto_capture$svg))
cat(' The stock n.breaks locator was also recalculated in the new mode.\n')
transforms <- list(percent=function(x) 100*x, decreasing=function(x) 10-x, nonlinear=function(x) sqrt(x))
for (name in names(transforms)) {
  transform <- transforms[[name]]
  author <- function(mode="linear", reverse="none") {
    plain + scale_x_continuous(transform=mode, sec.axis=sec_axis(transform,name=name)) +
      coord_cartesian(xlim=c(1,8), expand=c(left=FALSE,right=FALSE), reverse=reverse)
  }
  captured <- adapter$capture_ggplot(author("identity"),frame,pal)
  edited <- adapter$render_ggplot(captured$state,list(axes_0=list(xscale="log",invert_x=TRUE)))
  expected <- adapter$capture_ggplot(author("log10","x"),frame,pal)$svg
  stopifnot(identical(edited,expected), identical(adapter$render_ggplot(captured$state),captured$svg))
}

squared <- adapter$capture_ggplot(plain + scale_x_continuous(sec.axis=sec_axis(~ .^2)), frame, pal)
invalid <- try(adapter$render_ggplot(squared$state, list(axes_0=list(xlim=c(-4,8)))), silent=TRUE)
stopifnot(inherits(invalid,"try-error"),
          identical(conditionMessage(attr(invalid,"condition")),
                    "The native R secondary axis must stay strictly monotonic across this range."),
          identical(adapter$render_ggplot(squared$state),squared$svg))

# Formatting the primary tick group must not replace the units of an
# independently transformed upper guide, even in a shared facet group.
units <- panels + scale_x_continuous(sec.axis=sec_axis(~ . * 10, name="Converted x"))
state <- adapter$capture_ggplot(units,frame,pal)$state
bottom <- names(Filter(function(target) target$position == "bottom", state$tick_targets))[[1L]]
top <- names(Filter(function(target) target$position == "top", state$tick_targets))[[1L]]
stopifnot(!is.null(state$tick_targets[[bottom]]$scope), is.null(state$tick_targets[[top]]$scope))
formatted <- adapter$with_svg_frame(frame, function(device) {
  adapter$ggplot_table_for(state, setNames(list(list(format="%.1f")), bottom))
})$value
original_top <- state$text_targets[[top]]
updated_top <- formatted$text[[top]]
stopifnot(identical(original_top$tick_labels, updated_top$tick_labels),
          !identical(formatted$text[[bottom]]$tick_labels, state$text_targets[[bottom]]$tick_labels))

cat("Stock secondary-axis conversions match authored R and reject a nonmonotonic view before rendering.\n")
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
        pytest.skip("Rscript does not have the required native capture packages")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "Rplots.pdf").exists()


def test_ggplot_secondary_axes_follow_native_ranges_and_scales(tmp_path):
    _assert_r_probe(tmp_path, SECONDARY_AXIS_PROBE)


def test_faceted_tick_locators_are_shared_and_appearance_is_local(tmp_path):
    _assert_r_probe(tmp_path, FACET_TICK_APPEARANCE_PROBE)


def test_native_colorbar_appearance_keeps_other_guides_and_mappings(tmp_path):
    _assert_r_probe(tmp_path, COLOURBAR_APPEARANCE_PROBE)


def test_mapped_bar_group_edits_keep_dodged_centers_and_sibling_panels(tmp_path):
    _assert_r_probe(tmp_path, MAPPED_BAR_GROUP_WIDTH_PROBE)


def test_log_colourbar_ranges_match_native_mapping_in_data_units(tmp_path):
    _assert_r_probe(tmp_path, LOG_COLOUR_SCALE_PROBE)


def test_native_scatter_groups_follow_discrete_colour_fill_and_combined_guides(tmp_path):
    _assert_r_probe(tmp_path, POINT_LEGEND_CLASSES_PROBE)


def test_native_scatter_paint_preserves_mapped_pigments_and_uses_point_units(tmp_path):
    _assert_r_probe(tmp_path, POINT_PAINT_PROBE)


def test_mapped_point_groups_update_one_style_key_across_facets(tmp_path):
    _assert_r_probe(tmp_path, MAPPED_POINT_SHAPE_PROBE)


def test_ggplot_title_colors_keep_the_built_layers_and_replay(tmp_path):
    _assert_r_probe(tmp_path, PROBE)


def test_ggplot_minor_tick_controls_use_native_positions_and_grid(tmp_path):
    _assert_r_probe(tmp_path, MINOR_TICK_PROBE)


def test_ggplot_tick_side_switches_use_projected_strokes_without_duplicating_labels(tmp_path):
    _assert_r_probe(tmp_path, TICK_SIDES_PROBE)


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


def test_mapped_ribbon_opacity_keeps_other_groups_and_guides(tmp_path):
    _assert_r_probe(tmp_path, MAPPED_RIBBON_GROUP_PROBE)


def test_mapped_errorbar_caps_keep_stems_and_shared_legend_keys(tmp_path):
    _assert_r_probe(tmp_path, MAPPED_ERRORBAR_CAPS_PROBE)


def test_ggplot_errorbar_stroke_and_title_family_preserve_native_data(tmp_path):
    _assert_r_probe(tmp_path, ERRORBAR_PROBE)


def test_range_and_grouped_line_edits_preserve_geometry_and_sync_the_legend(tmp_path):
    _assert_r_probe(tmp_path, RANGE_AND_GROUPED_LINE_PROBE)


def test_faceted_curve_group_updates_one_key_and_respects_each_panel_clip(tmp_path):
    _assert_r_probe(tmp_path, FACET_GROUPED_LINE_PROBE)
    scene = json.loads((tmp_path / "faceted-curve.json").read_text(encoding="utf-8"))
    group = next(e for e in scene["elements"] if e["gid"] == "layer.1.line.group.2")
    source = group["geometry"]["paths"]
    nativeggplot.GgplotTarget.annotate_manifest(scene)
    actual = group["geometry"]["paths"]
    assert group["identity"].startswith("r4:")
    assert len(actual) == len(source) == 3
    assert len({tuple(path["clip"]) for path in source}) == 3
    for raw, visible in zip(source, actual, strict=True):
        left, top, width, height = raw["clip"]
        right, bottom = left + width, top + height
        assert raw["points"][0][0] < left < right < raw["points"][-1][0]
        assert visible["points"][0][0] == pytest.approx(left)
        assert visible["points"][-1][0] == pytest.approx(right)
        assert all(top <= y <= bottom for _, y in visible["points"])
        assert "clip" not in visible


def test_axis_windows_and_transforms_reproject_captured_data_without_recalculating(tmp_path):
    _assert_r_probe(tmp_path, AXES_PROBE)


def test_native_r_coordinate_overrides_keep_writeback_geometry_and_pixels(tmp_path):
    _assert_r_probe(tmp_path, AXIS_WRITEBACK_METRICS_PROBE)


def test_faceted_coordinates_reproject_axes_and_raster_by_native_scale_group(tmp_path):
    _assert_r_probe(tmp_path, FACET_COORDINATES_PROBE)
    scene = json.loads((tmp_path / "coordinates.json").read_text(encoding="utf-8"))
    nativeggplot.GgplotTarget.annotate_manifest(scene)
    by_gid = {e["gid"]: e for e in scene["elements"]}
    assert by_gid["axes_2"]["alias_covers"]["xlim"] == {"xlim": ["axes_5"]}
    assert by_gid["axes_5"]["alias_covers"]["invert_y"] == {"invert_y": ["axes_4"]}
    fields = {field["prop"]: field for field in by_gid["axes_5"]["editable"]}
    assert fields["xscale"]["value"] == "log"
    assert fields["xscale"]["value_original"] == "linear"
    assert fields["invert_y"]["value"] is True
    assert fields["invert_y"]["value_original"] is False


def test_faceted_axes_edit_only_the_target_panel_grid_background_and_border(tmp_path):
    _assert_r_probe(tmp_path, FACET_AXES_PROBE)
    scene = json.loads((tmp_path / "faceted-axes.json").read_text(encoding="utf-8"))
    nativeggplot.GgplotTarget.annotate_manifest(scene)
    axes = {item["gid"]: item for item in scene["elements"] if item["role"] == "axes"}
    assert len(axes) == 6
    assert len({item["identity"] for item in axes.values()}) == 6
    first = {field["prop"]: field for field in axes["axes_2"]["editable"]}
    selected = {field["prop"]: field for field in axes["axes_3"]["editable"]}
    assert selected["grid_x"]["value"] is False
    assert selected["grid_x"]["value_original"] is first["grid_x"]["value"] is True
    assert selected["spine_right_color"]["value"] == "#098756"
    assert selected["facecolor"]["value"] == "#ECD5A8"
    assert first["facecolor"]["value"] != selected["facecolor"]["value"]


ASPECT_PROBE = r"""
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)
frame <- c(360, 324)
values <- data.frame(x = c(1, 2, 4), y = c(1, 2, 3))
calls <- 0L
summarize <- function(y) { calls <<- calls + 1L; mean(y) }
plot <- ggplot2::ggplot(values, ggplot2::aes(x, y)) +
  ggplot2::stat_summary(fun = summarize, geom = "line") + ggplot2::geom_point()
capture <- function(plot) adapter$capture_ggplot(plot, frame)
axes <- function(captured, edit = list()) {
  m <- adapter$ggplot_manifest(captured$state, "ratio", list(axes_0 = edit))
  Filter(function(e) identical(e$gid, "axes_0"), m$elements)[[1L]]
}
physical_ratio <- function(element) {
  box <- unlist(element$bbox, use.names = FALSE)
  box[[4L]] * frame[[2L]] / (box[[3L]] * frame[[1L]])
}
patch <- function(edit) list(axes_0 = edit)
render <- function(captured, edit) adapter$render_ggplot(captured$state, patch(edit))
auto <- capture(plot)
before <- axes(auto)
data <- auto$state$layers
equal <- axes(auto, list(aspect = "equal"))
ratio <- axes(auto, list(aspect = "0.5"))
stopifnot(calls == 3L, identical(auto$state$layers, data),
          identical(equal$native_axes$aspect, list(original = "auto", current = "equal")),
          identical(ratio$native_axes$aspect, list(original = "auto", current = "0.5")),
          identical(before$native_axes$context, equal$native_axes$context),
          isTRUE(all.equal(physical_ratio(equal), 2 / 3)),
          isTRUE(all.equal(physical_ratio(ratio), 1 / 3)),
          !isTRUE(all.equal(physical_ratio(before), physical_ratio(equal))))
equal_svg <- render(auto, list(aspect = "equal"))
stopifnot(identical(render(auto, list()), auto$svg),
          identical(render(auto, list(aspect = "equal")), equal_svg), calls == 3L)

fixed <- capture(plot + ggplot2::coord_fixed(.5))
old_identity <- rlang::hash(list("grid", fixed$state$layers, fixed$state$built$layout$coord$limits))
stopifnot(identical(ratio$bbox, axes(fixed)$bbox), identical(render(auto, list(aspect = "0.5")), fixed$svg),
          identical(axes(fixed)$native_axes$context, old_identity),
          identical(axes(fixed)$native_axes$aspect$original, "0.5"),
          identical(render(fixed, list(aspect = "auto")), auto$svg),
          identical(render(fixed, list()), fixed$svg), calls == 6L)
combined <- list(aspect = "equal", xlim = c(1, 10), xscale = "log", ylim = c(1, 3), invert_x = TRUE)
combined_axes <- axes(fixed, combined)
stopifnot(combined_axes$native_axes$current$x$inverted,
          combined_axes$native_axes$current$x$mode == "log",
          isTRUE(all.equal(physical_ratio(combined_axes), 2)), calls == 6L)

hard_layout <- capture(plot + ggplot2::theme(aspect.ratio = .75))
stopifnot(is.null(axes(hard_layout)$native_axes$aspect),
          identical(render(hard_layout, list()), hard_layout$svg),
          inherits(try(render(hard_layout, list(aspect = "auto")), silent = TRUE), "try-error"))
for (invalid in list("0", "Inf", "-1", "fixed", "1e309", 2, TRUE)) {
  stopifnot(inherits(try(render(auto, list(aspect = invalid)), silent = TRUE), "try-error"))
}
overflow <- combined
overflow$aspect <- "1e308"
stopifnot(inherits(try(render(fixed, overflow), silent = TRUE), "try-error"))
"""


def test_ggplot_cartesian_aspect_matches_native_coordinates_without_refitting(tmp_path):
    _assert_r_probe(tmp_path, ASPECT_PROBE)


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
        revision = session.rev
        proof = session.preview_svg_snapshot(
            stem,
            [],
            include_fonts=True,
            expected_execution_id=session.execution_id,
            expected_snapshot_epoch=session.snapshot_epoch,
        )
        assert proof["fonts"] and all(isinstance(font, str) for font in proof["fonts"])
        assert proof["manifest"] == baseline["manifest"]
        canonical = nativeggplot._write_ggplot_svg(tmp_path / "proof.svg", proof["svg"])
        assert canonical == baseline["svg"].encode() and revision == session.rev
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
        axes = elements["axes_0"]
        original_fill = next(field for field in axes["editable"] if field["prop"] == "facecolor")
        background = session.override(
            stem,
            [
                {
                    "gid": axes["gid"],
                    "identity": axes["identity"],
                    "prop": "facecolor",
                    "value": "#C0DDFA",
                }
            ],
            inline_svg=True,
        )
        painted_axes = next(
            e for e in background["manifest"]["elements"] if e["gid"] == axes["gid"]
        )
        painted_fill = next(
            field for field in painted_axes["editable"] if field["prop"] == "facecolor"
        )
        assert painted_fill == {
            **original_fill,
            "value": "#C0DDFA",
            "value_original": original_fill["value"],
        }
        assert "fill: #C0DDFA" in background["svg"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
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
            "text": "Fuel economy",
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
            "text": "Engine displacement",
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
        with pytest.raises(pool.WorkerError, match="text style is unavailable"):
            session.override(
                stem,
                [{"gid": "plot.title", "prop": "weight", "value": "semibold"}],
                inline_svg=True,
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


def test_native_r_tick_text_follows_a_new_break_through_projection_and_replay(tmp_path):
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Native tick labels"
    project.mkdir()
    source = project / "View.R"
    source.write_text(
        """
d <- data.frame(x = c(1, 2, 4, 8, 16), y = c(1, 4, 8, 16, 32))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::theme_bw() + ggplot2::theme(axis.text.x = ggplot2::element_text(angle = 26))
""",
        encoding="utf-8",
    )
    group = "axis.x.bottom.ticks"

    def lookup(view, gid):
        return next(e for e in view["manifest"]["elements"] if e["gid"] == gid)

    def labels(view):
        return [e for e in view["manifest"]["elements"] if e.get("parent_gid") == group]

    def patch(element, prop, value):
        return {
            "gid": element["gid"],
            "identity": element["identity"],
            "prop": prop,
            "value": value,
        }

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, source.name, rscript)
        original = _render(app, figure, [])
        initial = [e["label"] for e in labels(original)]
        assert initial == ["4", "8", "12", "16"]
        axis = lookup(original, "axes_0")
        scope = lookup(original, group)
        fixed = [
            patch(scope, "major_mode", "fixed"),
            patch(scope, "major_values", [2, 4, 8, 11, 16]),
        ]
        newly_visible = _render(app, figure, fixed)
        target = next(e for e in labels(newly_visible) if e["label"] == "11")
        assert target["role"] == "ticklabel"
        assert target["bbox"][2] > 0 and target["bbox"][3] > 0
        edited = patch(target, "text", "Selected mark")
        hot = _render(app, figure, [*fixed, edited])
        assert hot["warnings"] == []
        assert [e["label"] for e in labels(hot)] == ["2", "4", "8", "Selected mark", "16"]
        assert lookup(hot, target["gid"])["editable"] == [
            {"prop": "text", "type": "text", "value": "Selected mark", "value_original": "11"}
        ]
        assert hot["svg"].count(">Selected mark</text>") == 1
        assert _render(app, figure, fixed)["svg"] == newly_visible["svg"]
        log_patches = [*fixed, edited, patch(axis, "xscale", "log")]
        projected = _render(app, figure, log_patches)
        assert projected["warnings"] == []
        assert lookup(projected, target["gid"])["label"] == "Selected mark"
        crop = _render(app, figure, [*log_patches, patch(axis, "xlim", [1, 6])])
        assert crop["warnings"] == [] and target["gid"] not in {e["gid"] for e in labels(crop)}
        assert _render(app, figure, log_patches)["svg"] == projected["svg"]

        blank = _render(app, figure, [*fixed, patch(target, "text", "")])
        assert lookup(blank, target["gid"])["bbox"] == [-1, -1, 0, 0]
        width, height = original["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, log_patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        route = "/api/layouts/Individual_ticks"
        document = {
            "schema": 3,
            "project": {"id": "native-tick-members", "name": "Individual ticks"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                }
            ],
        }
        assert app.call(f"{route}?target=project", document)[1]["ok"]

    with sidecar_app(project, tmp_path / "cold") as app:
        replay = _run_ggplot(app, source.name, rscript)
        _, saved = app.call("/api/layouts/Individual_ticks")
        persisted = saved["canvases"][0]["objects"][0]["overrides"]
        assert persisted == log_patches
        after = _render(app, replay, persisted)
        assert after["warnings"] == [] and after["svg"] == projected["svg"]


@pytest.mark.parametrize("scope", ["single", "continuous-facets", "categorical-facets"])
def test_native_r_tick_text_writeback_uses_the_actual_side_and_facet(tmp_path, scope):
    from test_core_v02_release_candidate import R_FRAME_PT, _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Tick writeback"
    project.mkdir()
    script = project / "Tick marks.R"
    faceted = scope != "single"
    guide = (
        'ggplot2::facet_wrap(~sample, axes = "all")'
        if faceted
        else "ggplot2::scale_x_continuous(breaks = c(1, 4, 8, 16), sec.axis = ggplot2::dup_axis())"
    )
    category = (
        "d$x <- factor(d$x, levels = c(1, 4, 8, 16))" if scope == "categorical-facets" else ""
    )
    original = f"""\
d <- expand.grid(x = c(1, 4, 8, 16), sample = c("Early", "Later"))
d$y <- d$x / 2
{category}
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  {guide} +
  ggplot2::theme_bw() + ggplot2::theme(axis.text.x = ggplot2::element_text(angle = 20))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Tick_writeback"

    def x_labels(response, text):
        return [
            (node.text, node.get("x"), node.get("y"), node.get("transform"))
            for node in ElementTree.fromstring(response).iter("{http://www.w3.org/2000/svg}text")
            if node.text in text
        ]

    def save_label(app, figure, baseline, target, value):
        patches = [
            {"gid": target["gid"], "identity": target["identity"], "prop": "text", "value": value}
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        peers = [
            element
            for element in hot["manifest"]["elements"]
            if element["role"] == "ticklabel" and element["label"] == value
        ]
        assert [element["gid"] for element in peers] == [target["gid"]]
        width, height = baseline["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        document = {
            "schema": 3,
            "project": {"id": "r-tick-writeback", "name": "Tick writeback"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                },
            ],
        }
        assert app.call(f"{route}?target=project", document)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Tick_writeback")
        before = script.read_bytes()
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == 1 and preview["skipped"] == [], preview
        assert script.read_bytes() == before
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        assert app.call(route)[1]["canvases"][0]["objects"][0]["overrides"] == []
        return hot, saved

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        same_text = [
            el
            for el in baseline["manifest"]["elements"]
            if el["role"] == "ticklabel"
            and el["label"] == "8"
            and el["parent_gid"].startswith("axis.x.bottom")
        ]
        assert len(same_text) == (2 if faceted else 1)
        target = same_text[-1]
        if scope == "categorical-facets":
            parent = next(
                el for el in baseline["manifest"]["elements"] if el["gid"] == target["parent_gid"]
            )
            assert all(f["prop"] != "major_mode" for f in parent["editable"])
        value = 'Selected "mark"'
        expected, saved = save_label(app, figure, baseline, target, value)

    authored = script.read_bytes()
    assert b"ggplot_with_tick_labels" in authored
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    actual = (project / "standalone.svg").read_text(encoding="utf-8")
    assert x_labels(actual, {"8", value}) == x_labels(expected["svg"], {"8", value})
    with sidecar_app(project, tmp_path / "cold") as app:
        rebuilt = _run_ggplot(app, script.name, rscript)
        replay = _render(app, rebuilt, [])
        assert replay["warnings"] == [] and replay["svg"] == expected["svg"]
        current = next(el for el in replay["manifest"]["elements"] if el["gid"] == target["gid"])
        assert current["label"] == value and current["identity"] == target["identity"]
        if faceted:
            final, saved = save_label(app, rebuilt, replay, current, "Final mark")
            subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
            actual = (project / "standalone.svg").read_text(encoding="utf-8")
            assert x_labels(actual, {value, "Final mark"}) == x_labels(final["svg"], {"Final mark"})
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"]
        assert script.read_bytes() == (authored if faceted else original)


@pytest.mark.parametrize("change_mapping", [False, True], ids=["layout", "with-range"])
def test_native_r_colourbar_layout_writes_selected_guides_and_replays(
    tmp_path, monkeypatch, change_mapping
):
    import test_core_v02_release_candidate as release
    from test_core_v02_release_candidate import _panel, _render, _run_ggplot

    frame = (420, 280)
    monkeypatch.setattr(release, "R_FRAME_PT", frame)

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Colourbar layout"
    project.mkdir()
    script = project / "Guides.R"
    original = f"""\
d <- expand.grid(x = 1:5, y = 1:3)
d$colour <- d$x^2 + d$y
d$fill <- d$x + d$y^2
d$category <- factor(ifelse(d$y == 1L, "Early", "Late"))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_tile(ggplot2::aes(fill = fill)) +
  ggplot2::geom_point(ggplot2::aes(colour = colour, shape = category), size = 3) +
  ggplot2::scale_fill_gradient(name = "Primary") +
  ggplot2::scale_colour_gradient(name = "Secondary") +
  ggplot2::guides(fill = ggplot2::guide_colourbar(position = "bottom", reverse = TRUE),
                  colour = ggplot2::guide_colourbar(position = "right")) +
  ggplot2::theme_bw() + ggplot2::theme(legend.frame = ggplot2::element_rect(colour = "#804080"))
svglite::svglite("standalone.svg", {frame[0]}/72, {frame[1]}/72)
print(plot)
grDevices::dev.off()
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Colourbar_layout"

    def settings(scene):
        return {
            e["gid"]: {f["prop"]: f["value"] for f in e["editable"]}
            for e in scene["manifest"]["elements"]
            if e["role"] == "colorbar"
        }

    def arrow_shapes(svg):
        return sorted(
            node.get("points")
            for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}polygon")
            if len((node.get("points") or "").split()) == 3
        )

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        original_vector = (project / "standalone.svg").read_text(encoding="utf-8")
        controls = {
            e["label"]: e for e in baseline["manifest"]["elements"] if e["role"] == "colorbar"
        }
        assert set(controls) == {"Primary", "Secondary"}
        edits = (
            ("Primary", "orientation", "vertical"),
            ("Primary", "extend", "max"),
            ("Secondary", "orientation", "horizontal"),
            ("Secondary", "extend", "both"),
        )
        if change_mapping:
            edits += (("Primary", "vmin", 4), ("Primary", "vmax", 11))
        patches = [
            {
                "gid": controls[label]["gid"],
                "identity": controls[label]["identity"],
                "prop": prop,
                "value": value,
            }
            for label, prop, value in edits
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        expected = settings(hot)
        for label, prop, value in edits:
            assert expected[controls[label]["gid"]][prop] == value
        width, height = baseline["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        document = {
            "schema": 3,
            "project": {"id": "r-colourbar-layout", "name": "Colourbar layout"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                },
            ],
        }
        assert app.call(f"{route}?target=project", document)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Colourbar_layout")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == len(patches) and not preview["skipped"], (
            preview
        )
        assert script.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"], saved
        assert app.call(route)[1]["canvases"][0]["objects"][0]["overrides"] == []

    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    standalone = (project / "standalone.svg").read_text(encoding="utf-8")
    # The app groups legend vectors in SVG, so compare after reopening through
    # its normal adapter and check triangle count in the independently drawn file.
    assert len(arrow_shapes(standalone)) == len(arrow_shapes(original_vector)) + 3
    with sidecar_app(project, tmp_path / "fresh") as app:
        fresh = _run_ggplot(app, script.name, rscript)
        rebuilt = _render(app, fresh, [])
        assert rebuilt["warnings"] == []
        assert rebuilt["svg"] == hot["svg"]
        assert settings(rebuilt) == expected
        guide = next(
            el for el in rebuilt["manifest"]["elements"] if el["gid"] == controls["Primary"]["gid"]
        )
        remove_extension = {
            "gid": guide["gid"],
            "identity": guide["identity"],
            "prop": "extend",
            "value": "neither",
        }
        drawn = _render(app, fresh, [remove_extension])
        assert drawn["warnings"] == []
        assert settings(drawn)[guide["gid"]]["extend"] == "neither"
        assert _render(app, fresh, [])["svg"] == rebuilt["svg"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == original


def test_native_r_title_text_reflows_and_writes_the_displayed_axis_side(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Title text"
    project.mkdir()
    script = project / "Titles.R"
    original = f"""\
d <- data.frame(x = 1:6, y = exp(1:6/4), group = rep(c("First", "Second"), each = 3))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::scale_x_continuous(name = "Distance", sec.axis = ggplot2::sec_axis(~.*1000, name = "Other x")) +
  ggplot2::scale_y_continuous(name = "Concentration", sec.axis = ggplot2::dup_axis(name = "Other y")) +
  ggplot2::labs(title = "Before", subtitle = "Sub", caption = "By Team") +
  ggplot2::facet_wrap(~group) + ggplot2::theme_bw()
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Title_text"
    new_labels = {
        "plot.title": 'Measured "orbit"',
        "plot.subtitle": "First caption line\nContinues",
        "plot.caption": "",
        "axis.x.bottom.title": "Time (s)",
        "axis.x.top.title": "Time (ms)",
        "axis.y.right.title": "New reference",
    }

    def fields(response):
        return {
            el["gid"]: el
            for el in response["manifest"]["elements"]
            if el["gid"] in {*new_labels, "axis.y.left.title"}
        }

    def svg_labels(svg):
        return [
            node.text
            for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")
        ]

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        targets = fields(before)
        changes = [(gid, "text", value) for gid, value in new_labels.items()]
        changes.append(("axis.x.bottom.title", "fontsize", 13))
        patches = [
            {"gid": gid, "identity": targets[gid]["identity"], "prop": prop, "value": value}
            for gid, prop, value in changes
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        actual = fields(hot)
        for gid, value in new_labels.items():
            assert (
                actual[gid]["label"] == value
                and actual[gid]["identity"] == targets[gid]["identity"]
            )
        assert actual["plot.caption"]["bbox"] == [-1, -1, 0, 0]
        assert actual["axis.y.left.title"]["label"] == targets["axis.y.left.title"]["label"]
        assert actual["plot.subtitle"]["bbox"][3] > targets["plot.subtitle"]["bbox"][3]
        assert _render(app, figure, [])["svg"] == before["svg"]
        width, height = before["manifest"]["size_mm"]
        obj = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        document = {
            "schema": 3,
            "project": {"id": "r-title-text", "name": "Title text"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [obj],
                    "guides": [],
                },
            ],
        }
        assert app.call(f"{route}?target=project", document)[1]["ok"]
        layout = layoutannotate.open_layout(project, "Title_text")
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": layout.name,
                "revision": layout.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == len(patches) and not preview["skipped"], (
            preview
        )
        assert script.read_bytes() == original
        status, saved = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and saved["ok"]
        assert app.call(route)[1]["canvases"][0]["objects"][0]["overrides"] == []
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    assert svg_labels((project / "standalone.svg").read_text(encoding="utf-8")) == svg_labels(
        hot["svg"]
    )
    with sidecar_app(project, tmp_path / "cold") as app:
        rebuilt = _run_ggplot(app, script.name, rscript)
        replay = _render(app, rebuilt, [])
        assert replay["warnings"] == [] and replay["svg"] == hot["svg"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == original


@pytest.mark.parametrize("scope", ["linear-step", "shared-log-breaks", "free-log-breaks"])
def test_native_r_major_locators_restore_from_standalone_source(tmp_path, scope):
    from test_core_v02_release_candidate import R_FRAME_PT, _panel, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Native locator"
    project.mkdir()
    script = project / "Measured.R"
    faceted = scope != "linear-step"
    free = scope == "free-log-breaks"
    dimension = "free_x" if free else "fixed"
    scale = (
        f'+ ggplot2::scale_x_log10() + ggplot2::facet_wrap(~sample, scales = "{dimension}", axes = "all")'
        if faceted
        else ""
    )
    bind_x = 'd$x <- d$x * ifelse(d$sample == "Above", 1, 3)' if free else ""
    original = f"""\
d <- expand.grid(x = c(1, 2, 4, 8, 16, 24), sample = c("Below", "Above"))
{bind_x}
d$y <- d$x / 2 + as.integer(d$sample)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, group = sample)) + ggplot2::geom_line() +
  ggplot2::theme_bw() {scale}
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Major_locator"
    first_change = (
        {"major_mode": "fixed", "major_values": [2, 4, 8, 11, 16, 25], "format": "%.1f"}
        if faceted
        else {"major_mode": "step", "major_step": 3.5, "format": "%.1f"}
    )

    def marks(svg):
        return [
            (node.text, node.get("x"), node.get("y"))
            for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")
        ]

    def axis_fields(rendered):
        return [
            element
            for element in rendered["manifest"]["elements"]
            if element["role"] == "ticks" and element["gid"].startswith("axis.x.bottom.ticks")
        ]

    def save(app, figure, baseline, change, target=None):
        if target is None:
            target = axis_fields(baseline)[0]
        patches = [
            {"gid": target["gid"], "identity": target["identity"], "prop": prop, "value": value}
            for prop, value in change.items()
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        width, height = hot["manifest"]["size_mm"]
        panel = {
            **_panel("r", figure, 5, patches, native_width=width),
            "h": height,
            "nativeH": height,
        }
        doc = {
            "schema": 3,
            "project": {"id": "major-locator", "name": "Major locator"},
            "activeCanvasId": "main",
            "canvases": [
                {
                    "id": "main",
                    "name": "Main",
                    "schema": 2,
                    "page": {"w": width + 10, "h": height + 10},
                    "objects": [panel],
                    "guides": [],
                }
            ],
        }
        assert app.call(f"{route}?target=project", doc)[1]["ok"]
        saved = layoutannotate.open_layout(project, "Major_locator")
        before = script.read_bytes()
        status, preview = app.call(
            "/api/script-edit/figure-style/preview",
            {
                "layout": saved.name,
                "revision": saved.revision,
                "canvas_id": "main",
                "panel_id": "r",
            },
            timeout=120,
        )
        assert status == 200 and preview["written"] == len(patches) and preview["skipped"] == [], (
            preview
        )
        assert script.read_bytes() == before
        status, committed = app.call(
            "/api/script-edit/figure-style/commit",
            {"token": preview["token"], "confirmed": True},
            timeout=120,
        )
        assert status == 200 and committed["ok"], committed
        assert app.call(route)[1]["canvases"][0]["objects"][0]["overrides"] == []
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert marks((project / "standalone.svg").read_text(encoding="utf-8")) == marks(hot["svg"])
        return hot, committed

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        initial = _render(app, figure, [])
        edited, _ = save(app, figure, initial, first_change)
        assert initial["svg"] != edited["svg"]
        values = [key for key, _, _ in marks(edited["svg"])]
        if faceted:
            assert values.count("11.0") == (1 if free else 2)
            assert values.count("25.0") == (1 if free else 2)
        else:
            assert "3.5" in values and "17.5" in values
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        replayed = _render(app, figure, [])
        assert replayed["svg"] == edited["svg"]
        fields = [
            {field["prop"]: field["value"] for field in target["editable"]}
            for target in axis_fields(replayed)
        ]
        assert len(fields) == (2 if faceted else 1)
        assert {key: fields[0][key] for key in first_change} == first_change
        if free:
            assert fields[1]["major_mode"] == "auto" and fields[1]["format"] == "auto"
        elif faceted:
            assert {key: fields[1][key] for key in first_change} == first_change
        if free:
            member = next(
                element
                for element in replayed["manifest"]["elements"]
                if element["role"] == "ticklabel"
                and element["parent_gid"] == axis_fields(replayed)[0]["gid"]
                and element["label"] == "11.0"
            )
            renamed, label_write = save(app, figure, replayed, {"text": "Selected break"}, member)
            assert sum(text == "Selected break" for text, _, _ in marks(renamed["svg"])) == 1
            status, restored = app.call(
                "/api/script-backups/restore",
                {
                    "backup_id": label_write["backup_id"],
                    "expected_sha256": label_write["script_revision"],
                    "mode": "full",
                },
                timeout=120,
            )
            assert status == 200 and restored["ok"] and script.read_bytes() == authored
            figure = _run_ggplot(app, script.name, rscript)
            replayed = _render(app, figure, [])
            assert replayed["svg"] == edited["svg"]
        reset, committed = save(app, figure, replayed, {"major_mode": "auto", "format": "auto"})
        assert reset["svg"] == initial["svg"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": committed["backup_id"],
                "expected_sha256": committed["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


@pytest.mark.parametrize("case", ["reproduces", "hidden-difference", "undrawn-data"])
def test_borrowed_r_source_writes_only_a_reproduced_console_scene(tmp_path, case):
    from support.foundation_app import HttpError
    from test_core_v02_release_candidate import _panel, _render

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "R project"
    project.mkdir()
    data_root = project / "Data and analysis"
    data_root.mkdir()
    (data_root / "observations.csv").write_text("x,y\n1,2\n2,4\n3,3\n", encoding="utf-8")
    source = data_root / "source.R"
    original = b"""\
data <- utils::read.csv("observations.csv")
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_point() + ggplot2::geom_blank(data = data.frame(x=2,y=3)) +
  ggplot2::labs(title = "Field observations") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#102030"))
svglite::svglite("standalone.svg", 320/72, 240/72)
print(plot)
grDevices::dev.off()
"""
    source.write_bytes(original)
    console_program = tmp_path / "start-console.R"
    console_program.write_text(
        """
sys.source("source.R", envir = .GlobalEnv)
case <- commandArgs(trailingOnly = TRUE)[[1L]]
if (case == "hidden-difference") {
  plot <- plot + ggplot2::theme(plot.title = ggplot2::element_text(colour = "#8090A0"))
}
if (case == "undrawn-data") {
  plot$layers[[2L]] <- ggplot2::geom_blank(data = data.frame(x=2.5,y=3.25))
}
grDevices::pdf("console-device.pdf")
device <- grDevices::dev.cur()
before <- list(cwd = getwd(), libraries = .libPaths(), colour = plot$theme$plot.title$colour)
cat(Sys.getpid(), file = "console-ready")
input <- file("stdin", open = "r")
eval(parse(text = readLines(input, n = 1L)), envir = .GlobalEnv)
stopifnot(identical(device, grDevices::dev.cur()),
          identical(getwd(), before$cwd), identical(.libPaths(), before$libraries),
          identical(plot$theme$plot.title$colour, before$colour))
cat(Sys.getpid(), file = "console-resumed")
stopifnot(identical(readLines(input, n = 1L), "close test"))
grDevices::dev.off(device)
close(input)
""",
        encoding="utf-8",
    )
    ready = data_root / "console-ready"
    resumed = data_root / "console-resumed"
    relative = source.relative_to(project).as_posix()
    layout_route = "/api/layouts/Console_source"

    with (
        sidecar_app(project, tmp_path / "desktop") as app,
        subprocess.Popen(
            [rscript, "--vanilla", str(console_program), case],
            cwd=data_root,
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
                {"script": relative, "object": "plot", "frame_pt": [320, 240]},
            )
            assert status == 201, offer
            console.stdin.write(offer["command"] + "\n")
            console.stdin.flush()
            while True:
                _, result = app.call("/api/native/sessions")
                sessions = result["sessions"]
                if sessions and sessions[0]["state"] in {"barrier", "ended", "failed"}:
                    break
                assert console.poll() is None and time.monotonic() < deadline, sessions
                time.sleep(0.05)
            (session,) = sessions
            assert session["state"] == "barrier" and session["owner"] == "terminal", session
            assert session["process_pid"] == pid
            control = f"/api/native/sessions/{session['session_id']}"
            _, result = app.call(control + "/build", {}, timeout=_COLD_RENDER_TIMEOUT_SECONDS)
            (descriptor,) = result["descriptors"]
            asset = descriptor["asset_id"]
            initial = _render(app, asset, [])
            target = next(e for e in initial["manifest"]["elements"] if e["gid"] == "plot.title")
            patches = [
                {
                    "gid": target["gid"],
                    "identity": target["identity"],
                    "prop": "color",
                    "value": "#C46210",
                }
            ]
            hot = _render(app, asset, patches)
            assert hot["warnings"] == [] and "#C46210" in hot["svg"]
            width, height = initial["manifest"]["size_mm"]
            panel = {
                **_panel("r", asset, 5, patches, native_width=width),
                "h": height,
                "nativeH": height,
            }
            doc = {
                "schema": 3,
                "project": {"id": "console-source", "name": "Console source"},
                "activeCanvasId": "main",
                "canvases": [
                    {
                        "id": "main",
                        "name": "Main",
                        "schema": 2,
                        "page": {"w": width + 10, "h": height + 10},
                        "objects": [panel],
                        "guides": [],
                    }
                ],
            }
            assert app.call(layout_route + "?target=project", doc)[1]["ok"]
            saved = layoutannotate.open_layout(project, "Console_source")

            def preview_source():
                return app.call(
                    "/api/script-edit/figure-style/preview",
                    {
                        "layout": saved.name,
                        "revision": saved.revision,
                        "canvas_id": "main",
                        "panel_id": "r",
                    },
                    timeout=120,
                )

            if case != "reproduces":
                with pytest.raises(HttpError) as rejected:
                    preview_source()
                assert rejected.value.status == 409
                assert rejected.value.body["code"] == "script_export_diverged"
                if case == "undrawn-data":
                    assert "different plot data" in rejected.value.body["error"]
                assert "token" not in rejected.value.body and not resumed.exists()
                assert source.read_bytes() == original
                assert (
                    layoutannotate.open_layout(project, "Console_source").revision == saved.revision
                )
                assert _render(app, asset, [])["svg"] == initial["svg"]
                app.call(control + "/continue", {})
            else:
                status, preview = preview_source()
                assert source.read_bytes() == original
                assert status == 200 and preview["written"] == 1, preview
                status, committed = app.call(
                    "/api/script-edit/figure-style/commit",
                    {"token": preview["token"], "confirmed": True},
                    timeout=120,
                )
                assert status == 200 and committed["ok"], committed
                assert source.read_bytes() != original
                saved_panel = app.call(layout_route)[1]["canvases"][0]["objects"][0]
                assert saved_panel["overrides"] == [] and saved_panel["figureSnapshot"]
            deadline = time.monotonic() + 45
            while not resumed.is_file() or not resumed.stat().st_size:
                assert console.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            assert int(resumed.read_text()) == pid and console.poll() is None
            stdout, stderr = console.communicate("close test\n", timeout=30)
            assert console.returncode == 0, stdout + stderr
        finally:
            if console.poll() is None:
                console.terminate()
                console.wait(timeout=15)

    if case == "reproduces":
        subprocess.run([rscript, "--vanilla", source.name], check=True, cwd=data_root, timeout=45)
        standalone = (data_root / "standalone.svg").read_text(encoding="utf-8")
        assert "#C46210" in standalone and "#102030" not in standalone
        source.rename(data_root / "source.R.moved")
        with sidecar_app(project, tmp_path / "cold") as app:
            old = app.call(layout_route)[1]["canvases"][0]["objects"][0]
            assert old == saved_panel
            code, archived = app.call(
                "/api/figure-preview",
                {"id": asset, "patches": [], "figure_snapshot": old["figureSnapshot"]},
                timeout=120,
            )
            assert code == 200 and "#C46210" in archived["svg"], archived


def test_mapped_curve_visibility_and_opacity_follow_data_even_with_same_colour(tmp_path):
    _assert_r_probe(
        tmp_path,
        r"""
pal <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
d <- expand.grid(x = 1:5, treatment = c("Control", "Sample"), facet = c("Early", "Later"))
d$trace <- as.integer(factor(d$treatment)) * d$x
plot <- ggplot2::ggplot(d, ggplot2::aes(x, trace, colour = treatment)) +
  ggplot2::geom_line() + ggplot2::geom_point() +
  ggplot2::scale_colour_manual(values = c(Control = "#445566", Sample = "#445566")) +
  ggplot2::facet_wrap(~facet)
image <- adapter$capture_ggplot(plot, c(420, 224), pal)
state <- image$state
control_gid <- "layer.1.line.group.1"
sample_gid <- "layer.1.line.group.2"
initial <- adapter$ggplot_manifest(state, "figure")
target <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
fields <- function(target) stats::setNames(lapply(target$editable, `[[`, "value"),
                                          vapply(target$editable, `[[`, character(1L), "prop"))
control <- target(initial, control_gid)
sample <- target(initial, sample_gid)
stopifnot(length(control$geometry$paths) == 2L, length(sample$geometry$paths) == 2L,
          !identical(control$geometry$paths, sample$geometry$paths),
          isTRUE(fields(sample)$visible), fields(sample)$alpha == 1)
faded <- stats::setNames(list(list(alpha = .4)), sample_gid)
hidden <- stats::setNames(list(list(visible = FALSE, alpha = .4)), sample_gid)
a <- adapter$ggplot_manifest(state, "figure", faded)
b <- adapter$ggplot_manifest(state, "figure", hidden)
stopifnot(identical(control, target(a, control_gid)), identical(control, target(b, control_gid)),
          fields(target(a, sample_gid))$alpha == .4, isTRUE(target(a, sample_gid)$geometry$stroke),
          isFALSE(fields(target(b, sample_gid))$visible),
          isFALSE(target(b, sample_gid)$geometry$stroke),
          identical(sample$geometry$paths, target(b, sample_gid)$geometry$paths))
svg <- adapter$render_ggplot(state, faded)
masked <- adapter$render_ggplot(state, hidden)
stopifnot(length(gregexpr("stroke-opacity: 0.40", svg, fixed = TRUE)[[1L]]) == 3L,
          length(gregexpr("stroke-opacity: 0.40", masked, fixed = TRUE)[[1L]]) == 1L,
          identical(adapter$render_ggplot(state), image$svg))
""",
    )


def _write_native_figure_style(app, script, route, asset, hot, patches):
    from test_core_v02_release_candidate import _panel

    layout_name = route.rsplit("/", 1)[-1]
    width, height = hot["manifest"]["size_mm"]
    panel = {
        **_panel("r", asset, 5, patches, native_width=width),
        "h": height,
        "nativeH": height,
    }
    doc = {
        "schema": 3,
        "project": {
            "id": layout_name.lower().replace("_", "-"),
            "name": layout_name.replace("_", " "),
        },
        "activeCanvasId": "main",
        "canvases": [
            {
                "id": "main",
                "name": "Main",
                "page": {"w": width + 10, "h": height + 10},
                "objects": [panel],
                "guides": [],
            }
        ],
    }
    assert app.call(route + "?target=project", doc)[1]["ok"]
    saved = layoutannotate.open_layout(script.parent, layout_name)
    before = script.read_bytes()
    status, preview = app.call(
        "/api/script-edit/figure-style/preview",
        {
            "layout": saved.name,
            "revision": saved.revision,
            "canvas_id": "main",
            "panel_id": "r",
        },
        timeout=120,
    )
    assert status == 200 and preview["written"] == len(patches) and preview["skipped"] == [], (
        preview
    )
    assert script.read_bytes() == before
    status, committed = app.call(
        "/api/script-edit/figure-style/commit",
        {"token": preview["token"], "confirmed": True},
        timeout=120,
    )
    assert status == 200 and committed["ok"], committed
    assert app.call(route)[1]["canvases"][0]["objects"][0]["overrides"] == []
    return committed


@pytest.mark.parametrize("facet", ["wrap", "grid"])
def test_native_r_independent_facet_views_write_and_reopen_without_moving_neighbours(
    tmp_path, facet
):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Independent facet axes"
    project.mkdir()
    script = project / "Facets.R"
    facet_code = (
        'ggplot2::facet_wrap(~sample, scales = "free_x", axes = "all")'
        if facet == "wrap"
        else 'ggplot2::facet_grid(block ~ sample, scales = "free_x", axes = "all")'
    )
    blocks = '"First"' if facet == "wrap" else '"First", "Second"'
    original = f"""\
d <- expand.grid(x = c(2, 4, 8, 16, 32), sample = c("Early", "Late"), block = c({blocks}))
d$x <- d$x * ifelse(d$sample == "Late", 3, 1)
d$y <- d$x / 4 + as.integer(d$block)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_line(colour = "#18556C") +
  {facet_code} + ggplot2::theme_bw()
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
b <- ggplot2::ggplot_build(plot)
panels <- lapply(seq_len(nrow(b$layout$layout)), function(i) {{
  panel <- b$layout$panel_params[[i]]
  trans <- panel$x$scale$get_transformation()
  data <- b$data[[1]][as.integer(b$data[[1]]$PANEL) == i, ]
  list(mode = trans$name, range = trans$inverse(panel$x.range), reverse = panel$reverse,
       x = trans$inverse(data$x), y = data$y)
}})
jsonlite::write_json(panels, "views.json", auto_unbox = TRUE, digits = NA)
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Independent_axes"
    standalone = project / "standalone.svg"
    facts = project / "views.json"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    initial_facts = json.loads(facts.read_text(encoding="utf-8"))
    early = (0,) if facet == "wrap" else (0, 2)
    late = set(range(len(initial_facts))) - set(early)

    def marks(svg):
        return [
            (node.tag, node.attrib, node.text or "")
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.endswith("}text")
            or (node.tag.endswith("}polyline") and "#18556C" in node.get("style", ""))
        ]

    def save(app, asset, known, changes):
        target = next(
            e for e in known["manifest"]["elements"] if e["gid"] == f"axes_{early[-1] + 1}"
        )
        patches = [
            {"gid": target["gid"], "identity": target["identity"], "prop": prop, "value": value}
            for prop, value in changes.items()
        ]
        hot = _render(app, asset, patches)
        assert hot["warnings"] == []
        assert _render(app, asset, [])["svg"] == known["svg"]
        saved = _write_native_figure_style(app, script, route, asset, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert marks(standalone.read_text(encoding="utf-8")) == marks(hot["svg"])
        current = json.loads(facts.read_text(encoding="utf-8"))
        for i in late:
            assert current[i] == initial_facts[i]
        for i in early:
            item = current[i]
            assert item["mode"] == ("log-10" if changes["xscale"] == "log" else "identity")
            assert item["range"] == pytest.approx(changes["xlim"])
            assert item["reverse"] == ("x" if changes["invert_x"] else "none")
            assert item["x"] == pytest.approx(initial_facts[i]["x"])
            assert item["y"] == initial_facts[i]["y"]
        return hot, saved

    with sidecar_app(project, tmp_path / "warm") as app:
        asset = _run_ggplot(app, script.name, rscript)
        initial = _render(app, asset, [])
        edited, _ = save(app, asset, initial, {"xscale": "log", "xlim": [3, 24], "invert_x": True})
    first_write = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, asset, [])
        assert reopened["svg"] == edited["svg"]
        _, saved = save(
            app, asset, reopened, {"xscale": "linear", "xlim": [2, 32], "invert_x": False}
        )
        status, result = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and result["ok"] and script.read_bytes() == first_write


def test_native_r_mapped_curve_source_matches_the_hot_scene_and_remains_editable(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Mapped R curves"
    project.mkdir()
    script = project / "Series.R"
    original = f"""\
d <- expand.grid(x = 1:5, arm = c("Reference", "Test"), round = c("Start", "End"))
d$y <- d$x * as.integer(factor(d$arm))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, colour = arm)) +
  ggplot2::geom_line() + ggplot2::geom_point() +
  ggplot2::scale_colour_manual(values = c(Reference = "#445566", Test = "#445566"),
    labels = c(Reference = "Baseline", Test = "Target")) +
  ggplot2::facet_wrap(~round)
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Curve_categories"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    original_data = (project / "built-data.txt").read_bytes()

    def target(view, label):
        return next(
            e
            for e in view["manifest"]["elements"]
            if e["role"] == "line" and e["label"].endswith(label)
        )

    def styles(svg):
        return [
            (node.tag, node.get("style"))
            for node in ElementTree.fromstring(svg).iter()
            if "stroke: #445566;" in node.get("style", "")
            and node.tag.rsplit("}", 1)[-1] in {"line", "polyline"}
        ]

    def points(svg):
        return [
            node.attrib
            for node in ElementTree.fromstring(svg).iter("{http://www.w3.org/2000/svg}circle")
        ]

    def save(app, asset, baseline, changes):
        element = target(baseline, "Target")
        patches = [
            {"gid": element["gid"], "identity": element["identity"], "prop": prop, "value": value}
            for prop, value in changes.items()
        ]
        hot = _render(app, asset, patches)
        assert hot["warnings"] == []
        assert target(hot, "Baseline") == target(baseline, "Baseline")
        assert points(hot["svg"]) == points(baseline["svg"])
        assert _render(app, asset, [])["svg"] == baseline["svg"]
        committed = _write_native_figure_style(app, script, route, asset, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert (project / "built-data.txt").read_bytes() == original_data
        assert sorted(styles((project / "standalone.svg").read_text(encoding="utf-8"))) == sorted(
            styles(hot["svg"])
        )
        return hot, committed

    first = {"linewidth": 2.8, "linestyle": "--", "alpha": 0.4, "visible": False}
    with sidecar_app(project, tmp_path / "warm") as app:
        asset = _run_ggplot(app, script.name, rscript)
        initial = _render(app, asset, [])
        edited, _ = save(app, asset, initial, first)
        assert len(styles(initial["svg"])) == 6
        assert len(styles(edited["svg"])) == 4
        assert sum("stroke-opacity: 0.40" in rule for _, rule in styles(edited["svg"])) == 1
        assert target(edited, "Target")["geometry"]["stroke"] is False

    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        replayed = _render(app, asset, [])
        assert replayed["svg"] == edited["svg"]
        assert {
            field["prop"]: field["value"] for field in target(replayed, "Target")["editable"]
        } == first
        axis = next(e for e in replayed["manifest"]["elements"] if e["role"] == "axes")
        projected = _render(
            app,
            asset,
            [{"gid": axis["gid"], "identity": axis["identity"], "prop": "xscale", "value": "log"}],
        )
        assert projected["warnings"] == []
        assert target(projected, "Target")["geometry"]["stroke"] is False
        assert (
            target(projected, "Baseline")["geometry"]["paths"]
            != target(replayed, "Baseline")["geometry"]["paths"]
        )
        shown, committed = save(app, asset, replayed, {"visible": True})
        assert sum("stroke-opacity: 0.40" in rule for _, rule in styles(shown["svg"])) == 3
        assert target(shown, "Target")["geometry"]["stroke"] is True
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": committed["backup_id"],
                "expected_sha256": committed["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


@pytest.mark.parametrize("mapping", ["colour-shape", "fill"])
@pytest.mark.parametrize("scope", ["all", "facet"])
def test_native_r_mapped_point_source_matches_the_hot_scene_and_remains_editable(
    tmp_path, mapping, scope
):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Mapped R points"
    project.mkdir()
    script = project / "Scatter.R"
    labels = 'labels = c(Reference = "Baseline", Test = "Target")'
    colours = 'values = c(Reference = "#445566", Test = "#445566")'
    if mapping == "fill":
        aesthetics = "fill = arm"
        layer = 'ggplot2::geom_point(shape = 21, colour = "#789012")'
        scale = f"ggplot2::scale_fill_manual({colours}, {labels})"
        changes = {"edgecolor": "#326699", "linewidth": 2.1}
    else:
        aesthetics = "colour = arm, shape = arm"
        layer = "ggplot2::geom_point()"
        scale = (
            f"ggplot2::scale_colour_manual({colours}, {labels}) + "
            f"ggplot2::scale_shape_manual(values = c(Reference = 19, Test = 19), {labels})"
        )
        changes = {"marker": "^"}
    original = f"""\
d <- expand.grid(x = 1:5, arm = c("Reference", "Test"), round = c("Start", "End"))
d$y <- d$x * as.integer(factor(d$arm))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, {aesthetics})) +
  {layer} + {scale} + ggplot2::facet_wrap(~round)
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Point_categories"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    original_data = (project / "built-data.txt").read_bytes()

    def target(view, label):
        return next(
            e
            for e in view["manifest"]["elements"]
            if e["role"] == "scatter"
            and e["label"].endswith(label)
            and e["gid"].endswith(".facet") == (scope == "facet")
            and (scope == "all" or e["label"].endswith(f"End · {label}"))
        )

    def styles(svg):
        return [
            (node.tag, node.get("style"))
            for node in ElementTree.fromstring(svg).iter()
            if "#445566" in node.get("style", "")
            and node.tag.rsplit("}", 1)[-1] in {"circle", "polygon"}
        ]

    def save(app, asset, baseline, changes):
        element = target(baseline, "Target")
        patches = [
            {"gid": element["gid"], "identity": element["identity"], "prop": prop, "value": value}
            for prop, value in changes.items()
        ]
        hot = _render(app, asset, patches)
        assert hot["warnings"] == []
        assert target(hot, "Baseline") == target(baseline, "Baseline")
        if scope == "facet":
            peer = next(
                e
                for e in baseline["manifest"]["elements"]
                if e["gid"].endswith(".facet") and e["label"].endswith("Start · Target")
            )
            assert next(e for e in hot["manifest"]["elements"] if e["gid"] == peer["gid"]) == peer
        assert _render(app, asset, [])["svg"] == baseline["svg"]
        committed = _write_native_figure_style(app, script, route, asset, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert (project / "built-data.txt").read_bytes() == original_data
        assert sorted(styles((project / "standalone.svg").read_text(encoding="utf-8"))) == sorted(
            styles(hot["svg"])
        )
        return hot, committed

    first = {"size": 86, "alpha": 0.45, **changes}
    affected = 5 if scope == "facet" else 11
    with sidecar_app(project, tmp_path / "warm") as app:
        asset = _run_ggplot(app, script.name, rscript)
        initial = _render(app, asset, [])
        edited, _ = save(app, asset, initial, first)
        assert len(styles(initial["svg"])) == 22
        assert len(styles(edited["svg"])) == 22
        assert sum("opacity: 0.45" in rule for _, rule in styles(edited["svg"])) == affected

    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        asset = _run_ggplot(app, script.name, rscript)
        replayed = _render(app, asset, [])
        assert replayed["svg"] == edited["svg"]
        fields = {field["prop"]: field for field in target(replayed, "Target")["editable"]}
        if mapping == "fill":
            assert fields["edgecolor"]["value"] == first["edgecolor"]
            assert fields["linewidth"]["value"] == pytest.approx(first["linewidth"])
        else:
            assert fields["marker"]["marker_current"] == {"kind": "named", "name": "^"}
        assert fields["size"]["value"] == pytest.approx(first["size"])
        assert fields["alpha"]["value"] == first["alpha"]
        axis = next(e for e in replayed["manifest"]["elements"] if e["role"] == "axes")
        projected = _render(
            app,
            asset,
            [{"gid": axis["gid"], "identity": axis["identity"], "prop": "xscale", "value": "log"}],
        )
        assert projected["warnings"] == []

        def point_centers(view):
            return [
                node.get("cx")
                for node in ElementTree.fromstring(view["svg"]).iter(
                    "{http://www.w3.org/2000/svg}circle"
                )
                if "#445566" in node.get("style", "")
            ]

        expected_circles = 22 if mapping == "fill" else 22 - affected
        assert len(point_centers(projected)) == len(point_centers(replayed)) == expected_circles
        assert point_centers(projected) != point_centers(replayed)
        revised, committed = save(app, asset, replayed, {"alpha": 0.25})
        assert sum("opacity: 0.25" in rule for _, rule in styles(revised["svg"])) == affected
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": committed["backup_id"],
                "expected_sha256": committed["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


@pytest.mark.parametrize("layout_kind", ["classic", "faceted"])
def test_native_r_individual_frame_sides_write_reopen_and_remain_editable(tmp_path, layout_kind):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Edited R frame"
    project.mkdir()
    script = project / "Edges.R"
    faceted = layout_kind == "faceted"
    axis_id = "axes_2" if faceted else "axes_0"
    theme = (
        'ggplot2::theme_bw() + ggplot2::facet_wrap(~block, scales = "free_x", axes = "all")'
        if faceted
        else "ggplot2::theme_classic()"
    )
    standalone = project / "standalone.svg"
    original = f"""\
d <- expand.grid(x = 1:5, block = c("Early", "Late"))
d$y <- d$x + as.integer(d$block)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_line(colour = "#183F62") +
  {theme}
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""".encode()
    script.write_bytes(original)
    route = "/api/layouts/Frame_sides"

    def target(view, gid=axis_id):
        return next(e for e in view["manifest"]["elements"] if e["gid"] == gid)

    def fields(view):
        return {field["prop"]: field["value"] for field in target(view)["editable"]}

    def strokes(svg):
        return [
            (node.tag, node.attrib)
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] in {"line", "polyline", "rect"}
        ]

    def save(app, figure, baseline, changes):
        selected = target(baseline)
        patches = [
            {"gid": axis_id, "identity": selected["identity"], "prop": prop, "value": value}
            for prop, value in changes.items()
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        if faceted:
            assert target(hot, "axes_1") == target(baseline, "axes_1")
        saved = _write_native_figure_style(app, script, route, figure, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert strokes(standalone.read_text(encoding="utf-8")) == strokes(hot["svg"])
        return hot, saved

    first = {
        "spine_left": False,
        "spine_top": True,
        "spine_top_color": "#D67534",
        "spine_top_linewidth": 3.3,
    }
    if faceted:
        first.update(xlim=[1.25, 4.75], xscale="log", invert_x=True)
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        changed, _ = save(app, figure, baseline, first)
        assert sum("#D67534" in attr.get("style", "") for _, attr in strokes(changed["svg"])) == 1
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == changed["svg"]
        assert {prop: fields(reopened)[prop] for prop in first} == first
        new_style = {"spine_left": True, "spine_color": "#207A85", "spine_linewidth": 2.4}
        revised, saved = save(app, figure, reopened, new_style)
        styles = fields(revised)
        assert styles["spine_left"]
        assert styles["spine_top_color"] == "#207A85"
        assert styles["spine_top_linewidth"] == 2.4
        _, result = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert result["ok"] and script.read_bytes() == authored


def test_native_r_facet_grid_and_background_write_remain_editable(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Facet grid styles"
    project.mkdir()
    script = project / "Grid.R"
    script.write_text(
        f"""\
d <- expand.grid(x = 1:6, block = c("Early", "Late"))
d$y <- d$x + as.integer(d$block)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_line(colour = "#184765") + ggplot2::theme_bw() +
  ggplot2::facet_wrap(~block, scales = "free_x", axes = "all")
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Facet_grid_styles"

    def panel(view, gid):
        return next(e for e in view["manifest"]["elements"] if e["gid"] == gid)

    def fields(view, gid):
        return {field["prop"]: field["value"] for field in panel(view, gid)["editable"]}

    def scene(svg):
        return [
            (node.tag, node.attrib, node.text)
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] in {"line", "polyline", "rect", "text"}
        ]

    selected, neighbour = "axes_2", "axes_1"

    def save(app, figure, before, changes):
        patches = [
            {
                "gid": selected,
                "identity": panel(before, selected)["identity"],
                "prop": prop,
                "value": value,
            }
            for prop, value in changes.items()
        ]
        edited = _render(app, figure, patches)
        assert edited["warnings"] == []
        assert panel(edited, neighbour) == panel(before, neighbour)
        assert _render(app, figure, [])["svg"] == before["svg"]
        result = _write_native_figure_style(app, script, route, figure, edited, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert scene((project / "standalone.svg").read_text(encoding="utf-8")) == scene(
            edited["svg"]
        )
        return edited, result

    first = {
        "facecolor": "#F7EAB6",
        "grid_x": False,
        "grid_color": "#3B719A",
        "grid_linewidth": 1.3,
    }
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        rendered, _ = save(app, figure, before, first)
        nodes = scene(rendered["svg"])
        backgrounds = [attr for _, attr, _ in nodes if "fill: #F7EAB6;" in attr.get("style", "")]
        grids = [attr for _, attr, _ in nodes if "stroke: #3B719A;" in attr.get("style", "")]
        assert len(backgrounds) == 1
        assert len(grids) > 1
        assert all(
            min(float(x.split(",")[0]) for x in attr["points"].split())
            >= float(backgrounds[0]["x"]) - 0.01
            for attr in grids
        )
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == rendered["svg"]
        assert {prop: fields(reopened, selected)[prop] for prop in first} == pytest.approx(first)
        second = {"facecolor": "#E3F2FA", "grid_x": True, "grid_y": False}
        revised, saved = save(app, figure, reopened, second)
        assert {prop: fields(revised, selected)[prop] for prop in second} == second
        assert fields(revised, selected)["grid_color"] == first["grid_color"]
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


def test_native_r_facet_layer_paint_replays_and_preserves_built_data(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Facet paint"
    project.mkdir()
    script = project / "Paint.R"
    script.write_text(
        f"""\
d <- expand.grid(x = 1:4, region = c("Left", "Right"))
d$y <- 3 + d$x + as.integer(d$region)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) +
  ggplot2::geom_col(fill = "#E3A452", width = .45) +
  ggplot2::geom_point(colour = "#3773FF", size = 2.1, alpha = .65) +
  ggplot2::facet_wrap(ggplot2::vars(region))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "data-hash.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Facet_paint"
    evidence = project / "data-hash.txt"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    fingerprint = evidence.read_bytes()

    def marks(svg):
        return [
            (node.tag, node.attrib)
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] in {"circle", "rect", "polygon"}
        ]

    def element(view, layer, panel):
        return next(
            item
            for item in view["manifest"]["elements"]
            if item["gid"].startswith(f"layer.{layer}.panel.{panel}.")
        )

    def save(app, figure, baseline, edits):
        overrides = []
        for layer, panel, styles in edits:
            target = element(baseline, layer, panel)
            overrides.extend(
                {"gid": target["gid"], "identity": target["identity"], "prop": prop, "value": value}
                for prop, value in styles.items()
            )
        hot = _render(app, figure, overrides)
        assert hot["warnings"] == []
        for layer in (1, 2):
            assert element(hot, layer, 1) == element(baseline, layer, 1)
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        saved = _write_native_figure_style(app, script, route, figure, hot, overrides)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert evidence.read_bytes() == fingerprint
        assert marks((project / "standalone.svg").read_text(encoding="utf-8")) == marks(hot["svg"])
        return hot, saved

    initial = [
        (1, 2, {"facecolor": "#D81439", "bar_width": 0.3}),
        (2, 2, {"facecolor": "#249751", "alpha": 0.3, "size": 72}),
    ]
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        changed, _ = save(app, figure, baseline, initial)
        styles = [node.get("style", "") for _, node in marks(changed["svg"])]
        assert sum("fill: #E3A452;" in style for style in styles) == 4
        assert sum("fill: #D81439;" in style for style in styles) == 4
        assert sum("stroke: #249751;" in style for style in styles) == 4
        assert sum("stroke: #3773FF;" in style for style in styles) == 4
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == changed["svg"]
        fields = {field["prop"]: field["value"] for field in element(reopened, 2, 2)["editable"]}
        assert fields["alpha"] == 0.3
        later = [(1, 2, {"bar_width": 0.68}), (2, 2, {"facecolor": "#9D37A4"})]
        updated, saved = save(app, figure, reopened, later)
        styles = [node.get("style", "") for _, node in marks(updated["svg"])]
        assert (
            sum("stroke: #9D37A4;" in style and "stroke-opacity: 0.30" in style for style in styles)
            == 4
        )
        assert sum("fill: #D81439;" in style for style in styles) == 4
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


def test_native_r_mapped_ribbon_writeback_can_reveal_one_hidden_member(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Mapped ribbons"
    project.mkdir()
    script = project / "Ribbon.R"
    script.write_text(
        f"""\
d <- expand.grid(x = 1:5, arm = c("Baseline", "Target"), trial = c("Early", "Late"))
d$lower <- d$x / 2 + 2 * as.integer(d$arm)
d$upper <- d$lower + .9
plot <- ggplot2::ggplot(d, ggplot2::aes(x, ymin = lower, ymax = upper, fill = arm)) +
  ggplot2::geom_ribbon(alpha = .65, colour = NA) +
  ggplot2::scale_fill_manual(values = c(Baseline = "#6C541E", Target = "#6C541E")) +
  ggplot2::facet_wrap(ggplot2::vars(trial))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "data-state.txt")
""",
        encoding="utf-8",
    )
    data = project / "data-state.txt"
    route = "/api/layouts/Ribbons"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    fingerprint = data.read_bytes()

    def fills(svg):
        return [
            (node.tag, node.attrib)
            for node in ElementTree.fromstring(svg).iter()
            if "fill: #6C541E;" in node.get("style", "")
        ]

    def fill_layers(view):
        return {e["gid"]: e for e in view["manifest"]["elements"] if e["role"] == "fill"}

    gid = "layer.1.panel.2.ribbon.group.2"

    def save(app, fig, before, alpha):
        layers = fill_layers(before)
        assert len(layers) == 4 and layers[gid]["label"].endswith("Target")
        patches = [
            {"gid": gid, "identity": layers[gid]["identity"], "prop": "alpha", "value": alpha}
        ]
        hot = _render(app, fig, patches)
        assert hot["warnings"] == []
        for neighbour in layers.keys() - {gid}:
            assert fill_layers(hot)[neighbour] == layers[neighbour]
        assert _render(app, fig, [])["svg"] == before["svg"]
        saved = _write_native_figure_style(app, script, route, fig, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert data.read_bytes() == fingerprint
        assert fills((project / "standalone.svg").read_text(encoding="utf-8")) == fills(hot["svg"])
        return hot, saved

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        hidden, _ = save(app, figure, baseline, 0)
        assert not fill_layers(hidden)[gid]["geometry"]["fill"]
        assert len(fills(hidden["svg"])) == len(fills(baseline["svg"])) - 1
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == hidden["svg"]
        assert fill_layers(reopened)[gid]["editable"][0]["value"] == 0
        restored, saved = save(app, figure, reopened, 0.4)
        assert fill_layers(restored)[gid]["geometry"]["fill"]
        assert len(fills(restored["svg"])) == len(fills(baseline["svg"]))
        status, receipt = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and receipt["ok"] and script.read_bytes() == authored


@pytest.mark.parametrize("mode", ["single", "free-facet", "log10", "logit", "symlog"])
def test_native_r_minor_locators_reopen_and_restore_automatic_ticks(tmp_path, mode):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Minor source"
    project.mkdir()
    script = project / "Minor.R"
    facet = mode == "free-facet"
    facets = '+ ggplot2::facet_wrap(ggplot2::vars(panel), scales = "free_x")' if facet else ""
    log_axis = {
        "log10": "+ ggplot2::scale_x_log10(breaks = c(2, 10))",
        "logit": (
            '+ ggplot2::scale_x_continuous(transform = "logit", breaks = c(.1, .9))'
            " + ggplot2::coord_cartesian(xlim = c(.05, .95), expand = FALSE)"
        ),
        "symlog": (
            "+ ggplot2::scale_x_continuous(transform = ggplot_transform_symlog(), "
            "breaks = c(-100, 0, 100))"
            " + ggplot2::coord_cartesian(xlim = c(-120, 120), expand = FALSE)"
        ),
    }.get(mode, "")
    bind_x = {"logit": "d$x <- d$x / 12", "symlog": "d$x <- (d$x - 6) * 25"}.get(mode, "")
    prelude = (
        ADAPTER.with_name("ggplot_transforms.R").read_text(encoding="utf-8")
        if mode == "symlog"
        else ""
    )
    script.write_text(
        f"""\
{prelude}
d <- expand.grid(x = seq(2, 10, 2), panel = c("Early", "Late"))
d$y <- 2 * d$x + as.integer(d$panel)
{bind_x}
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, group = panel)) +
  ggplot2::geom_line(colour = "#36594E") + ggplot2::theme_bw() +
  ggplot2::theme(axis.text.x.bottom = ggplot2::element_text(colour = "#714715"),
    axis.minor.ticks.x.bottom = ggplot2::element_blank()) {facets} {log_axis}
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Minor"
    facts = project / "built-data.txt"
    subprocess.run([rscript, "--vanilla", script.name], cwd=project, timeout=45, check=True)
    original_data = facts.read_bytes()
    axis_id = "axes_2" if facet else "axes_0"

    def target(view):
        return next(
            e
            for e in view["manifest"]["elements"]
            if e.get("tick_host") == {"axes_gid": axis_id, "axis": "x"}
            and any(field["prop"] == "minor_mode" for field in e["editable"])
        )

    def graphics(svg):
        return [
            (node.tag, node.attrib, node.text)
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] in {"text", "polyline", "line"}
        ]

    def save(app, figure, baseline, styles):
        ticks = target(baseline)
        edits = [
            {"gid": ticks["gid"], "identity": ticks["identity"], "prop": prop, "value": value}
            for prop, value in styles.items()
        ]
        if facet and styles["minor_mode"] == "auto":
            axis = next(e for e in baseline["manifest"]["elements"] if e["gid"] == axis_id)
            edits.extend(
                {"gid": axis_id, "identity": axis["identity"], "prop": prop, "value": value}
                for prop, value in {"xlim": [1.5, 10.5], "invert_x": True}.items()
            )
        edited = _render(app, figure, edits)
        assert edited["warnings"] == []
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        committed = _write_native_figure_style(app, script, route, figure, edited, edits)
        subprocess.run([rscript, "--vanilla", script.name], cwd=project, timeout=45, check=True)
        assert facts.read_bytes() == original_data
        assert graphics((project / "standalone.svg").read_text(encoding="utf-8")) == graphics(
            edited["svg"]
        )
        return edited, committed

    settings = {
        "minor_visible": True,
        "minor_mode": "step",
        "minor_step": 2.5 if mode == "log10" else 0.75,
        "minor_format": "%.2f",
    }
    reset = {"minor_mode": "auto", "minor_format": "none"}
    intervals = {"log10": (2, 2.5), "logit": (0.2, 0.1), "symlog": (50, 25)}
    if mode in intervals:
        major_step, minor_step = intervals[mode]
        settings.update({"major_mode": "step", "major_step": major_step, "minor_step": minor_step})
        reset["major_mode"] = "auto"
    minor_label = {"log10": "7.50", "logit": "0.30", "symlog": "25.00"}.get(mode, "2.25")
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        starting = _render(app, figure, [])
        annotated, _ = save(app, figure, starting, settings)
        assert not any(label == minor_label for _, _, label in graphics(starting["svg"]))
        labels = [attr for _, attr, label in graphics(annotated["svg"]) if label == minor_label]
        assert len(labels) == 1
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == annotated["svg"]
        fields = {field["prop"]: field["value"] for field in target(reopened)["editable"]}
        assert {prop: fields[prop] for prop in settings} == settings
        edited, saved = save(app, figure, reopened, reset)
        assert not any(label == minor_label for _, _, label in graphics(edited["svg"]))
        status, restored = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and restored["ok"] and script.read_bytes() == authored


@pytest.mark.parametrize("facet", [False, True], ids=["single", "one-facet"])
def test_native_r_hidden_grid_paint_survives_source_writeback(tmp_path, facet):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Hidden grid"
    project.mkdir()
    script = project / "Hidden.R"
    cells = "+ ggplot2::facet_wrap(ggplot2::vars(region))" if facet else ""
    script.write_text(
        f"""\
d <- expand.grid(x = 1:5, region = c("Left", "Right"))
d$y <- d$x + as.integer(d$region)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::theme_bw() + ggplot2::theme(panel.grid.minor = ggplot2::element_blank(),
    panel.grid.major = ggplot2::element_line(colour = "#778998")) {cells}
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Hidden_grid"
    gid = "axes_2" if facet else "axes_0"
    pigment = "#DA297C"

    def chosen(view, element=gid):
        return next(e for e in view["manifest"]["elements"] if e["gid"] == element)

    def strokes(svg):
        return [
            node.attrib
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.endswith("}polyline") and f"stroke: {pigment};" in node.get("style", "")
        ]

    def save(app, figure, before, changes):
        patches = [
            {"gid": gid, "identity": chosen(before)["identity"], "prop": prop, "value": value}
            for prop, value in changes.items()
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        if facet:
            assert chosen(hot, "axes_1") == chosen(before, "axes_1")
        assert _render(app, figure, [])["svg"] == before["svg"]
        _write_native_figure_style(app, script, route, figure, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert strokes((project / "standalone.svg").read_text(encoding="utf-8")) == strokes(
            hot["svg"]
        )
        return hot

    changes = {
        "grid_x": False,
        "grid_y": False,
        "grid_color": pigment,
        "grid_linewidth": 2.3,
        "grid_linestyle": "--",
    }
    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        hidden = save(app, figure, _render(app, figure, []), changes)
        assert strokes(hidden["svg"]) == []
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == hidden["svg"]
        fields = {field["prop"]: field["value"] for field in chosen(reopened)["editable"]}
        assert {prop: fields[prop] for prop in changes} == pytest.approx(changes)
        enabled = save(app, figure, reopened, {"grid_x": True})
        drawn = strokes(enabled["svg"])
        assert drawn and all(
            "stroke-width: 2.30;" in item["style"] and "stroke-dasharray:" in item["style"]
            for item in drawn
        )


@pytest.mark.parametrize("kind", ["ribbon", "stacked-area"])
def test_native_r_fixed_ribbon_writeback_preserves_panel_geometry(tmp_path, kind):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Fixed range fills"
    project.mkdir()
    script = project / "Range.R"
    layer = (
        "ggplot2::geom_ribbon(ggplot2::aes(ymin = 2 * band, ymax = y + 2 * band), "
        'fill = "#486C29", alpha = .6, colour = NA)'
        if kind == "ribbon"
        else 'ggplot2::geom_area(fill = "#486C29", alpha = .6, colour = NA)'
    )
    script.write_text(
        f"""\
d <- expand.grid(x = 1:5, band = 1:2, trial = c("Early", "Late"))
d$x <- d$x + 0.4 * (d$band == 2)
d$y <- d$x / 5 + d$band
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, group = band)) +
  {layer} + ggplot2::facet_wrap(ggplot2::vars(trial))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Range"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    facts = project / "built-data.txt"
    fingerprint = facts.read_bytes()
    gid = "layer.1.panel.2.ribbon"
    neighbour = "layer.1.panel.1.ribbon"

    def polygons(svg, color):
        return [
            node.attrib
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.endswith("}polygon") and f"fill: {color};" in node.get("style", "")
        ]

    def layers(view):
        return {e["gid"]: e for e in view["manifest"]["elements"] if e["role"] == "fill"}

    def save(app, figure, baseline, edits):
        target = layers(baseline)[gid]
        patches = [
            {"gid": gid, "identity": target["identity"], "prop": prop, "value": value}
            for prop, value in edits.items()
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        assert layers(hot)[neighbour] == layers(baseline)[neighbour]
        assert layers(hot)[gid]["geometry"]["paths"] == target["geometry"]["paths"]
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        saved = _write_native_figure_style(app, script, route, figure, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert facts.read_bytes() == fingerprint
        standalone = (project / "standalone.svg").read_text(encoding="utf-8")
        for color in ["#486C29", "#8D246A"]:
            assert polygons(standalone, color) == polygons(hot["svg"], color)
        return hot, saved

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        starting = _render(app, figure, [])
        assert set(layers(starting)) == {gid, neighbour}
        old_polygons = polygons(starting["svg"], "#486C29")
        assert len(old_polygons) == 4
        settings = (
            {"facecolor": "none", "alpha": 0.4}
            if kind == "ribbon"
            else {"facecolor": "#8D246A", "alpha": 0}
        )
        hidden, _ = save(app, figure, starting, settings)
        assert not layers(hidden)[gid]["geometry"]["fill"]
        assert polygons(hidden["svg"], "#486C29") == old_polygons[:2]
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == hidden["svg"]
        values = {field["prop"]: field["value"] for field in layers(reopened)[gid]["editable"]}
        assert values == settings
        edit = {"facecolor": "#8D246A"} if kind == "ribbon" else {"alpha": 0.4}
        restored, saved = save(app, figure, reopened, edit)
        visible = polygons(restored["svg"], "#8D246A")
        assert len(visible) == 2
        assert [shape["points"] for shape in visible] == [
            shape["points"] for shape in old_polygons[2:]
        ]
        status, receipt = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and receipt["ok"] and script.read_bytes() == authored


def test_native_r_legend_anchors_write_back_without_reflowing_other_guides(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Moved legend boxes"
    project.mkdir()
    script = project / "Legends.R"
    script.write_text(
        f"""\
d <- data.frame(x = 1:8, y = (1:8)^.8, category = rep(c("Alpha", "Beta"), 4))
plot <- ggplot2::ggplot(d, ggplot2::aes(x, y, shape = category, colour = x)) +
  ggplot2::geom_point(size = 3) + ggplot2::theme_bw() +
  ggplot2::scale_shape_discrete(name = "Group") +
  ggplot2::scale_colour_continuous(name = "Measure",
    guide = ggplot2::guide_colourbar(position = "left", display = "rectangles"))
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Legends"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    data = project / "built-data.txt"
    original_data = data.read_bytes()

    def draw_marks(svg):
        return [
            (node.tag, node.attrib, node.text)
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1]
            in {"rect", "image", "text", "polygon", "polyline", "circle"}
        ]

    def by_gid(view):
        return {element["gid"]: element for element in view["manifest"]["elements"]}

    def save(app, figure, current, side, anchor):
        gid = f"legend.{side}"
        unchanged = "legend.right" if side == "left" else "legend.left"
        before = by_gid(current)
        patches = [
            {"gid": gid, "identity": before[gid]["identity"], "prop": "loc_frac", "value": anchor}
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        after = by_gid(hot)
        assert after[gid]["anchor"] == pytest.approx(anchor, abs=1e-7)
        assert after[unchanged] == before[unchanged]
        assert after["axes_0"] == before["axes_0"]
        assert _render(app, figure, [])["svg"] == current["svg"]
        saved = _write_native_figure_style(app, script, route, figure, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert data.read_bytes() == original_data
        standalone = (project / "standalone.svg").read_text(encoding="utf-8")
        assert draw_marks(standalone) == draw_marks(hot["svg"])
        return hot, saved

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        before = _render(app, figure, [])
        moved, _ = save(app, figure, before, "right", [0.43, 0.68])
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == moved["svg"]
        assert by_gid(reopened)["legend.right"]["anchor"] == pytest.approx([0.43, 0.68])
        both, saved = save(app, figure, reopened, "left", [0.68, 0.91])
        assert by_gid(both)["legend.right"] == by_gid(reopened)["legend.right"]
        status, receipt = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and receipt["ok"] and script.read_bytes() == authored


def test_native_r_mapped_curve_writes_only_one_facet_and_can_reveal_it(tmp_path):
    from test_core_v02_release_candidate import R_FRAME_PT, _render, _run_ggplot

    rscript = _rscript_with_capture_packages()
    project = tmp_path / "Local curve scope"
    project.mkdir()
    script = project / "Curves.R"
    script.write_text(
        f"""\
d <- expand.grid(x = 1:5, series = c("Reference", "Sample"), time = c("Early", "Late"))
d$value <- d$x ^ 1.1 + 2 * as.integer(d$series) + as.integer(d$time)
plot <- ggplot2::ggplot(d, ggplot2::aes(x, value, colour = series)) +
  ggplot2::geom_line(linewidth = .65, alpha = .8) +
  ggplot2::scale_colour_manual(values = c(Reference = "#534981", Sample = "#534981")) +
  ggplot2::facet_wrap(ggplot2::vars(time)) + ggplot2::theme_bw()
svglite::svglite("standalone.svg", {R_FRAME_PT[0]}/72, {R_FRAME_PT[1]}/72)
print(plot)
grDevices::dev.off()
writeLines(rlang::hash(ggplot2::ggplot_build(plot)$data), "built-data.txt")
""",
        encoding="utf-8",
    )
    route = "/api/layouts/Curves"
    subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
    data = project / "built-data.txt"
    original_data = data.read_bytes()
    gid = "layer.1.panel.2.line.group.2"
    peer = "layer.1.panel.1.line.group.2"
    shared = "layer.1.line.group.2"

    def stroke_records(svg, tag):
        return [
            node.attrib
            for node in ElementTree.fromstring(svg).iter()
            if node.tag.endswith("}" + tag) and "stroke: #534981;" in node.get("style", "")
        ]

    def lines(view):
        return {e["gid"]: e for e in view["manifest"]["elements"] if e["role"] == "line"}

    def values(view, member):
        return {field["prop"]: field["value"] for field in lines(view)[member]["editable"]}

    def save(app, figure, baseline, edits, shared_edits=None):
        targets = lines(baseline)
        patches = [
            {"gid": owner, "identity": targets[owner]["identity"], "prop": prop, "value": value}
            for owner, styles in ((gid, edits), (shared, shared_edits or {}))
            for prop, value in styles.items()
        ]
        hot = _render(app, figure, patches)
        assert hot["warnings"] == []
        if not shared_edits:
            assert lines(hot)[peer] == targets[peer]
            assert values(hot, shared) == values(baseline, shared)
            assert stroke_records(hot["svg"], "line") == stroke_records(baseline["svg"], "line")
        assert lines(hot)[gid]["geometry"]["paths"] == targets[gid]["geometry"]["paths"]
        assert _render(app, figure, [])["svg"] == baseline["svg"]
        receipt = _write_native_figure_style(app, script, route, figure, hot, patches)
        subprocess.run([rscript, "--vanilla", script.name], check=True, cwd=project, timeout=45)
        assert data.read_bytes() == original_data
        standalone = (project / "standalone.svg").read_text(encoding="utf-8")
        for tag in ("line", "polyline"):
            assert stroke_records(standalone, tag) == stroke_records(hot["svg"], tag)
        return hot, receipt

    with sidecar_app(project, tmp_path / "warm") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        assert lines(baseline)[gid]["label"].endswith("Late · Sample")
        assert lines(baseline)[shared]["alias_covers"]["linewidth"] == {"linewidth": [peer, gid]}
        initial_values = values(baseline, shared)
        initial_strokes = stroke_records(baseline["svg"], "polyline")
        assert len(initial_strokes) == 4
        hidden, _ = save(
            app,
            figure,
            baseline,
            {"linewidth": 3.6, "linestyle": "--", "alpha": 0.4, "visible": False},
        )
        assert len(stroke_records(hidden["svg"], "polyline")) == 3
        assert not lines(hidden)[gid]["geometry"]["stroke"]
    authored = script.read_bytes()
    with sidecar_app(project, tmp_path / "cold") as app:
        figure = _run_ggplot(app, script.name, rscript)
        reopened = _render(app, figure, [])
        assert reopened["svg"] == hidden["svg"]
        assert values(reopened, shared) == initial_values
        assert values(reopened, gid) == {
            "linewidth": 3.6,
            "linestyle": "--",
            "alpha": 0.4,
            "visible": False,
        }
        restored, saved = save(app, figure, reopened, {"visible": True})
        strokes = stroke_records(restored["svg"], "polyline")
        assert [stroke["points"] for stroke in strokes] == [
            stroke["points"] for stroke in initial_strokes
        ]
        assert sum("stroke-width: 3.60" in stroke["style"] for stroke in strokes) == 1
        assert sum("stroke-opacity: 0.40" in stroke["style"] for stroke in strokes) == 1
        status, receipt = app.call(
            "/api/script-backups/restore",
            {
                "backup_id": saved["backup_id"],
                "expected_sha256": saved["script_revision"],
                "mode": "full",
            },
            timeout=120,
        )
        assert status == 200 and receipt["ok"] and script.read_bytes() == authored

    with sidecar_app(project, tmp_path / "combined") as app:
        figure = _run_ggplot(app, script.name, rscript)
        baseline = _render(app, figure, [])
        result, _ = save(
            app,
            figure,
            baseline,
            {"visible": True, "linewidth": 3.6},
            {"linewidth": 2.1},
        )
        assert values(result, peer)["linewidth"] == 2.1
        assert values(result, gid)["linewidth"] == 3.6
        keys = stroke_records(result["svg"], "line")
        assert sum("stroke-width: 2.10" in key["style"] for key in keys) == 1
