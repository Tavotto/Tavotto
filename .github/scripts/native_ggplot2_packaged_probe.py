"""Exercise the packaged desktop sidecar, using real authentication and a real Rscript."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from contextlib import contextmanager
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pypdfium2

R_SOURCE = """
stopifnot(!nzchar(Sys.getenv("TAVOTTO_BRIDGE_TOKEN")))
set.seed(8884)
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point(position = ggplot2::position_jitter(width = .1)) +
  ggplot2::stat_summary(fun = mean, geom = "point", shape = 4) +
  ggplot2::facet_wrap(ggplot2::vars(cyl)) +
  ggplot2::labs(title = "Fuel economy") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))
"""


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.25)
    raise AssertionError("Timed out waiting for the expected desktop state")


@contextmanager
def running_app(executable, project, stage):
    stage.mkdir()
    handshake = stage / "handshake.json"
    nonce = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(stage / "data"),
        "TAVOTTO_CONFIG_DIR": str(stage / "config"),
        "TAVOTTO_DESKTOP_HANDSHAKE": str(handshake),
        "TAVOTTO_NO_TELEMETRY": "1",
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_JOURNAL_SYNC": "1",
        "TAVOTTO_NO_CONTACT": "1",
    }
    env.pop("PYTHONPATH", None)
    env.pop("TAVOTTO_WORKER_PYTHON", None)
    with (stage / "process.log").open("wb") as log:
        child = subprocess.Popen(
            [str(executable), "--desktop-sidecar", "--figures", str(project)],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            child.stdin.write(json.dumps({"nonce": nonce}).encode() + b"\n")
            child.stdin.flush()

            def read_handshake():
                assert child.poll() is None, f"Sidecar exited with {child.returncode}"
                return json.loads(handshake.read_bytes()) if handshake.exists() else None

            state = wait_for(read_handshake, 90)
            assert state.get("ready"), state
            base = f"http://127.0.0.1:{state['port']}"
            opener = build_opener(HTTPCookieProcessor(CookieJar()))

            def request(route, body=None):
                headers = {"Origin": base}
                data = None
                if body is not None:
                    headers["Content-Type"] = "application/json"
                    data = json.dumps(body).encode("utf-8")
                with opener.open(
                    Request(base + route, data=data, headers=headers), timeout=90
                ) as response:
                    return json.load(response)

            request("/api/session/bootstrap", {"nonce": nonce})
            yield request
        except BaseException:
            print((stage / "process.log").read_text(encoding="utf-8", errors="replace")[-12000:])
            raise
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=15)
            assert child.returncode == 0, child.returncode


def expect_colour(pdf, color):
    with pypdfium2.PdfDocument(pdf) as doc:
        assert len(doc) == 1
        assert all(abs(a - b * 72 / 25.4) < 0.02 for a, b in zip(doc[0].get_size(), (160, 65)))
        bitmap = doc[0].render(scale=4).to_pil().convert("RGB")
        region = bitmap.crop((bitmap.width // 2, 0, bitmap.width, bitmap.height))
        assert any(item == color for _, item in region.getcolors(region.width * region.height))


def verify(executable, runtime, folder):
    project = folder / "图表 项目"
    project.mkdir()
    source = project / "参考 结果.R"
    source.write_text(R_SOURCE, encoding="utf-8")
    bundled = executable.parent / "_internal" / "runtime"
    worker_python = bundled / "python.exe" if os.name == "nt" else bundled / "bin" / "python3.13"
    assert worker_python.is_file(), worker_python
    subprocess.run(
        [
            str(worker_python),
            "-c",
            "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt;"
            "plt.plot([1,2,3],[1,4,2]); plt.savefig('reference.pdf')",
        ],
        cwd=project,
        check=True,
        timeout=60,
    )
    with running_app(executable, project, folder / "warm") as request:
        launch = request(
            "/api/native/ggplot2",
            {
                "script": source.name,
                "object": "plot",
                "frame_pt": [432, 288],
                "rscript": str(runtime),
            },
        )
        sid = launch["session"]["session_id"]

        def at_barrier():
            session = request(f"/api/native/sessions/{sid}")["session"]
            assert session["state"] not in {"failed", "ended"}, session
            return session["state"] == "barrier"

        wait_for(at_barrier, 50)
        (descriptor,) = request(f"/api/native/sessions/{sid}/build", {})["descriptors"]
        assert descriptor["capture_source"] == "ggplot2"
        target = descriptor["asset_id"]

        def render(patches):
            return request(
                "/api/engine/render", {"id": target, "patches": patches, "inline_svg": True}
            )

        baseline = render([])
        (title,) = baseline["manifest"]["elements"]
        edit = [
            {
                "gid": title["gid"],
                "prop": "color",
                "value": "#6C3799",
                "identity": title["identity"],
            }
        ]
        changed = render(edit)
        assert changed["svg"].replace("#6C3799", "#112233", 1) == baseline["svg"]
        assert render([])["svg"] == baseline["svg"]
        assert render(edit)["svg"] == changed["svg"]
        panels = [
            {
                "id": "matplotlib",
                "type": "panel",
                "fileKind": "pdf",
                "fileId": "reference.pdf",
                "x": 5,
                "y": 8,
                "w": 69,
                "h": 50,
            },
            {
                "id": "ggplot2",
                "type": "panel",
                "fileKind": "runtime",
                "fileId": target,
                "source": {
                    "captureSource": "ggplot2",
                    "script": descriptor["script"],
                    "entry": descriptor["entry"],
                    "stem": descriptor["stem"],
                },
                "x": 80,
                "y": 8,
                "w": 75,
                "h": 50,
                "overrides": edit,
            },
        ]
        saved = request(
            "/api/layouts/Native-mixed?target=project",
            {
                "schema": 3,
                "project": {"id": "mixed", "name": "Native mixed"},
                "canvases": [
                    {
                        "id": "first",
                        "schema": 2,
                        "page": {"w": 160, "h": 65},
                        "objects": panels,
                        "guides": [],
                    }
                ],
                "activeCanvasId": "first",
            },
        )
        assert "ggplot2" in saved["figure_snapshots"], saved
    source.rename(project / "R-source-moved")

    with running_app(executable, project, folder / "cold") as request:
        restored = request("/api/layouts/Native-mixed")
        panels = restored["canvases"][0]["objects"]
        archived = request(
            "/api/figure-preview",
            {
                "id": panels[1]["fileId"],
                "patches": panels[1]["overrides"],
                "figure_snapshot": panels[1]["figureSnapshot"],
            },
        )
        assert archived["svg"] == changed["svg"]
        assert request("/api/native/sessions")["sessions"] == []
        exported = request(
            "/api/export",
            {
                "scope": "canvas",
                "filename": "Native mixed",
                "formats": ["pdf"],
                "canvas": {
                    "page_w_mm": 160,
                    "page_h_mm": 65,
                    "objects": [
                        {
                            "type": "panel",
                            "id": panel["fileId"],
                            "x_mm": panel["x"],
                            "y_mm": panel["y"],
                            "w_mm": panel["w"],
                            "h_mm": panel["h"],
                            **(
                                {
                                    "overrides": panel["overrides"],
                                    "figure_snapshot": panel["figureSnapshot"],
                                }
                                if panel.get("figureSnapshot")
                                else {}
                            ),
                        }
                        for panel in panels
                    ],
                },
            },
        )
        assert exported["status"] == "done", exported
        pdf = Path(exported["export_dir"]) / exported["outputs"][0]["name"]
        expect_colour(pdf, (108, 55, 153))
        assert request("/api/native/sessions")["sessions"] == []
        print(
            f"Packaged backend restored {panels[1]['figureSnapshot']['snapshotId']} and exported the native edit."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=lambda path: Path(path).resolve(strict=True))
    parser.add_argument("rscript", type=lambda path: Path(path).absolute())
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tavotto-native-qualification-") as temporary:
        verify(options.executable, options.rscript, Path(temporary))
