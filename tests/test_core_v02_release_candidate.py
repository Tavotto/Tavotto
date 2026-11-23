"""U10 against the authenticated desktop sidecar, optionally in a built app."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf
import pytest
from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart

from support.foundation_app import HttpError, sidecar_app

MPL_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    plt.rcParams["pdf.fonttype"] = 42
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [1, 4, 2], color="#317A75", marker="o")
    ax.set(xlabel="Time (min)", ylabel="Signal (mV)", title="Original signal")
    fig.tight_layout()
    fig.savefig("Signal.pdf")
"""
R_SCRIPT = r"""
cat("built\n", file = "r-builds.txt", append = TRUE)
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy)) +
  ggplot2::geom_point(colour = "#317A75", alpha = .5) +
  ggplot2::labs(x = "Displacement (L)", y = "Highway mileage", title = "Fuel economy") +
  ggplot2::theme_minimal() +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))
"""
LAYOUT = "/api/layouts/Mixed%20native"
MPL_EDIT = "Reviewed signal"
R_EDIT = "#6C3799"


def _run_ggplot(app, script, rscript):
    status, started = app.call(
        "/api/native/ggplot2",
        {"script": script, "object": "plot", "frame_pt": [216, 144], "rscript": rscript},
    )
    assert status == 202, started
    session = started["session"]
    assert session["owner"] == "desktop"
    route = f"/api/native/sessions/{session['session_id']}"
    deadline = time.monotonic() + 45
    while session["state"] not in {"barrier", "ended", "detached", "failed"}:
        assert time.monotonic() < deadline, session
        time.sleep(0.2)
        _, state = app.call(route, timeout=5)
        session = state["session"]
    assert session["state"] == "barrier", session
    _, built = app.call(f"{route}/build", {}, timeout=60)
    (descriptor,) = built["descriptors"]
    return descriptor["asset_id"]


def _render(app, asset, patches):
    _, body = app.call(
        "/api/engine/render",
        {"id": asset, "patches": patches, "inline_svg": True},
        timeout=120,
    )
    return body


def _title(body, prop):
    return next(
        element
        for element in body["manifest"]["elements"]
        if element["role"] == "title" and any(item["prop"] == prop for item in element["editable"])
    )


def _panel(id_, asset, x, patches):
    return {
        "id": id_,
        "type": "panel",
        "fileId": asset,
        "fileKind": "runtime",
        "x": x,
        "y": 5,
        "w": 76.2,
        "h": 50.8,
        "nativeW": 76.2,
        "nativeH": 50.8,
        "figureFrame": 1,
        "overrides": patches,
    }


def _export_pdf(app, doc, filename):
    (canvas,) = doc["canvases"]
    _, result = app.call(
        "/api/export",
        {
            "scope": "canvas",
            "filename": filename,
            "formats": ["pdf"],
            "canvas": {
                "page_w_mm": canvas["page"]["w"],
                "page_h_mm": canvas["page"]["h"],
                "objects": [
                    {
                        "type": "panel",
                        "id": panel["fileId"],
                        "x_mm": panel["x"],
                        "y_mm": panel["y"],
                        "w_mm": panel["w"],
                        "h_mm": panel["h"],
                        "overrides": panel["overrides"],
                        "figure_snapshot": panel["figureSnapshot"],
                    }
                    for panel in canvas["objects"]
                ],
            },
        },
    )
    assert result["status"] == "done", result
    (output,) = result["outputs"]
    assert output["status"] == "done" and output["format"] == "pdf", output
    return Path(result["export_dir"]) / output["name"]


def _pdf_pixels(path):
    with pymupdf.open(path) as pdf:
        assert len(pdf) == 1
        (page,) = pdf
        assert page.rect.width == pytest.approx(170 * 72 / 25.4, abs=0.01)
        assert page.rect.height == pytest.approx(61 * 72 / 25.4, abs=0.01)
        lines = [
            line["spans"]
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
        ]
        by_text = {"".join(span["text"] for span in line): line for line in lines}
        assert "Original signal" not in by_text
        mpl = by_text[MPL_EDIT]
        ggplot = by_text["Fuel economy"]
        assert max(span["bbox"][2] for span in mpl) < page.rect.width / 2
        assert min(span["bbox"][0] for span in ggplot) > page.rect.width / 2
        assert {span["color"] for span in ggplot} == {int(R_EDIT[1:], 16)}
        return page.get_pixmap().samples


def _install(app, package, destination):
    with package.open("rb") as source:
        boundary, payload = encode_multipart(
            {
                "package": FileStorage(source, filename=package.name),
                "options": json.dumps({"destination": str(destination)}),
            }
        )
    request = urllib.request.Request(
        f"{app.base}/api/package/install",
        data=payload,
        headers={**app.auth, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            assert response.status == 201
            return json.load(response)["doc"]
    except urllib.error.HTTPError as exc:
        pytest.fail(f"Package installation returned HTTP {exc.code}: {exc.read().decode()}")


def _archived_previews(app, doc):
    (canvas,) = doc["canvases"]
    previews = {}
    for panel in canvas["objects"]:
        route = "/api/figure-preview"
        request = {
            "id": panel["fileId"],
            "patches": panel["overrides"],
            "figure_snapshot": panel["figureSnapshot"],
        }
        _, preview = app.call(route, request)
        assert preview["svg"]
        previews[panel["id"]] = preview["svg"]
        with pytest.raises(HttpError) as mismatch:
            app.call(route, {**request, "patches": []})
        assert mismatch.value.status == 422
    return previews


def test_mixed_native_project_survives_portable_install(tmp_path):
    executable = os.environ.get("TAVOTTO_TEST_EXE")
    rscript = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    if not executable and not rscript:
        pytest.skip("set TAVOTTO_TEST_RSCRIPT to qualify a real R runtime")
    assert rscript and Path(rscript).is_file(), "A real Rscript is required"
    app_binary = Path(executable).resolve(strict=True) if executable else None
    project = tmp_path / "原始 项目"
    project.mkdir()
    (project / "mpl_response.py").write_text(MPL_SCRIPT, encoding="utf-8")
    (project / "fuel.R").write_text(R_SCRIPT, encoding="utf-8")
    (project / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "mpl_response.py": {"entry": "main", "cost": "light", "stems": ["Signal"]}
                },
            }
        ),
        encoding="utf-8",
    )
    with sidecar_app(project, tmp_path / "warm", executable=app_binary) as app:
        if app_binary is not None:
            _, environment = app.call("/api/engine/environment?probe=matplotlib", timeout=120)
            assert environment["source"] == "bundled", environment
        status, run = app.call("/api/registry/probe", {"script": "mpl_response.py"}, timeout=120)
        assert status == 200 and run["registered"], run
        (mpl,) = [item for item in run["descriptors"] if item["stem"] == "Signal"]
        mpl_asset = mpl["asset_id"]
        r_asset = _run_ggplot(app, "fuel.R", rscript)
        mpl_title = _title(_render(app, mpl_asset, []), "text")
        r_title = _title(_render(app, r_asset, []), "color")
        mpl_patch = {"gid": mpl_title["gid"], "prop": "text", "value": MPL_EDIT}
        r_patch = {
            "gid": r_title["gid"],
            "prop": "color",
            "value": R_EDIT,
            "identity": r_title["identity"],
        }
        for asset, patch in ((mpl_asset, mpl_patch), (r_asset, r_patch)):
            after = _title(_render(app, asset, [patch]), patch["prop"])
            assert (
                next(item["value"] for item in after["editable"] if item["prop"] == patch["prop"])
                == patch["value"]
            )
        document = {
            "schema": 3,
            "project": {"id": "mixed", "name": "Mixed native"},
            "activeCanvasId": "first",
            "canvases": [
                {
                    "id": "first",
                    "name": "Mixed native",
                    "schema": 2,
                    "page": {"w": 170, "h": 61},
                    "guides": [],
                    "objects": [
                        _panel("mpl", mpl_asset, 5, [mpl_patch]),
                        _panel("r", r_asset, 88, [r_patch]),
                    ],
                }
            ],
        }
        _, saved = app.call(LAYOUT + "?target=project", document)
        assert set(saved["figure_snapshots"]) == {"mpl", "r"}
        _, confirmed = app.call(LAYOUT)
        expected_previews = _archived_previews(app, confirmed)
        expected = _pdf_pixels(_export_pdf(app, confirmed, "Before transfer"))
        canvas_ids = [canvas["id"] for canvas in confirmed["canvases"]]
        _, packed = app.call("/api/package", {"doc": confirmed, "canvas_ids": canvas_ids})
        assert packed["document_format"] == "v4", packed
        package = project / "tavottofile" / "export" / packed["name"]
        assert (project / "r-builds.txt").read_text(encoding="utf-8").splitlines() == ["built"]
        assert app.call(LAYOUT)[1] == confirmed
    assert app.proc.returncode == 0

    empty = tmp_path / "空白 项目"
    empty.mkdir()
    receiver = tmp_path / "接收 项目"
    with sidecar_app(empty, tmp_path / "receiving", executable=app_binary) as app:
        installed = _install(app, package, receiver)
        for script in ("mpl_response.py", "fuel.R"):
            (receiver / script).unlink(missing_ok=True)
        _, opened = app.call("/api/projects/open", {"path": str(receiver)})
        assert opened["open"], opened
        _, saved = app.call(LAYOUT + "?target=project", installed)
        assert saved["ok"], saved
    assert app.proc.returncode == 0

    with sidecar_app(receiver, tmp_path / "reopened", executable=app_binary) as app:
        _, reopened = app.call(LAYOUT)
        assert reopened["canvases"] == confirmed["canvases"]
        assert _archived_previews(app, reopened) == expected_previews
        exported = _export_pdf(app, reopened, "After transfer")
        assert _pdf_pixels(exported) == expected
        query = urllib.parse.urlencode({"project_root": str(receiver.resolve())})
        _, sessions = app.call(f"/api/native/sessions?{query}")
        assert sessions["sessions"] == []
        assert not (receiver / "r-builds.txt").exists()
    assert app.proc.returncode == 0
