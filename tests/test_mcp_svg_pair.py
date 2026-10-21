"""MCP's PDF/SVG pair is one engine command, with per-format publication."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from support.bridgekit import needs_user_python  # noqa: E402
from tavotto_mcp import bridge  # noqa: E402
from test_mcp_export_inspection import (  # noqa: E402
    _body,
    _call,
    _clean_sessions,  # noqa: F401 - shared isolation fixture
    _open,
    project as project,  # shared project fixture
)
from test_mcp_server import FakeWorker  # noqa: E402


class PairWorker(FakeWorker):
    def __init__(self, fault=None):
        super().__init__()
        self.commands = []
        self.fault = fault
        self.after_export = lambda: None

    def export(self, stem, patches, path, fmt="pdf", dpi=600, *, svg_path=None):
        self.commands.append((fmt, svg_path, list(patches), dpi))
        if self.fault == fmt:
            raise bridge.engine_pool.WorkerError("draw failed", code="render_failed")
        result = super().export(stem, patches, path, fmt, dpi)
        result["warnings"] = ["worker warning"]
        if svg_path is not None:
            assert fmt == "pdf"
            # Simulate the transport acknowledgement, including a leftover file
            # when pairing was declined. Only the acknowledgement may admit it.
            super().export(stem, patches, svg_path, "svg", dpi)
            data = Path(svg_path).read_bytes()
            if self.fault == "declined":
                result["svg_error"] = {"code": "svg_export_failed", "message": "dynamic draw"}
            else:
                result["svg"] = {
                    "path": svg_path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                }
                if self.fault == "path":
                    result["svg"]["path"] += ".wrong"
                elif self.fault == "hash":
                    result["svg"]["sha256"] = "0" * 64
                elif self.fault == "length":
                    result["svg"]["size_bytes"] += 1
                elif self.fault == "missing_hash":
                    del result["svg"]["sha256"]
                elif self.fault == "metadata":
                    result["svg"] = "invalid acknowledgement"
                elif self.fault == "error_metadata":
                    result.pop("svg")
                    result["svg_error"] = "invalid failure"
                elif self.fault == "bad_pdf":
                    Path(path).write_bytes(b"broken PDF")
        self.after_export()
        return result


def _install(monkeypatch, worker):
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: worker)
    return worker


def _export(sid, out_dir, formats, **kwargs):
    return _call(
        "tavotto_export",
        {"session_id": sid, "formats": formats, "out_dir": str(out_dir), "dpi": 300, **kwargs},
    )


@pytest.mark.parametrize("formats", [["pdf", "svg"], ["svg", "pdf"], ["svg", "pdf", "svg"]])
def test_mcp_pdf_svg_uses_one_acknowledged_pair(project, monkeypatch, tmp_path, formats):
    worker = _install(monkeypatch, PairWorker())
    sid = _open(project)
    patches = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 9.0}]
    bridge.apply_overrides(sid, patches)
    out_dir = tmp_path / "out"
    result = _export(sid, out_dir, formats)
    assert not result.get("isError"), result
    done = _body(result)
    assert done["status"] == "done" and [f["format"] for f in done["files"]] == ["pdf", "svg"]
    assert len(worker.commands) == 1
    fmt, svg_path, actual_patches, dpi = worker.commands[0]
    assert fmt == "pdf" and svg_path.endswith(".svg")
    assert actual_patches == patches and dpi == 300
    assert done["warnings"].count("worker warning") == 1
    for output in done["files"]:
        published = Path(output["path"])
        assert output["manifest"]["verdict"] == "accepted"
        assert output["manifest"]["sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()
    assert not list(out_dir.glob(f"{bridge.engine_exportjob.TMP_PREFIX}*"))


@pytest.mark.parametrize(
    "fault,code",
    [
        ("declined", "svg_source_unsupported"),
        ("path", "svg_source_unsupported"),
        ("hash", "artifact_rejected"),
        ("length", "artifact_rejected"),
        ("missing_hash", "artifact_rejected"),
        ("metadata", "svg_source_unsupported"),
        ("error_metadata", "svg_source_unsupported"),
    ],
)
def test_mcp_pair_requires_its_own_byte_acknowledgement(
    project, monkeypatch, tmp_path, fault, code
):
    worker = _install(monkeypatch, PairWorker(fault))
    sid = _open(project)
    out_dir = tmp_path / "out"
    done = _body(_export(sid, out_dir, ["svg", "pdf", "png"]))
    assert done["status"] == "partial", done
    by_fmt = {f["format"]: f for f in done["files"]}
    assert by_fmt["svg"]["error"]["code"] == code
    assert by_fmt["svg"]["path"] is None and not list(out_dir.glob("*.svg"))
    assert [by_fmt[f]["status"] for f in ("pdf", "png")] == ["done", "done"]
    assert [c[0] for c in worker.commands] == ["pdf", "png"]
    assert worker.commands[0][1] is not None
    if fault == "declined":
        assert by_fmt["svg"]["error"]["params"]["error"] == "dynamic draw"


@pytest.mark.parametrize("fault,failed", [("bad_pdf", "pdf"), ("png", "png")])
def test_mcp_other_format_failure_keeps_the_acknowledged_svg(
    project, monkeypatch, tmp_path, fault, failed
):
    worker = _install(monkeypatch, PairWorker(fault))
    done = _body(_export(_open(project), tmp_path / "out", ["svg", "pdf", "png"]))
    assert done["status"] == "partial", done
    by_fmt = {f["format"]: f for f in done["files"]}
    assert by_fmt[failed]["status"] == "failed" and by_fmt[failed]["path"] is None
    assert by_fmt["svg"]["status"] == "done" and Path(by_fmt["svg"]["path"]).is_file()
    assert [c[0] for c in worker.commands] == ["pdf", "png"]


@pytest.mark.parametrize("formats", [["svg"], ["pdf", "png"], ["svg", "png"]])
def test_mcp_unpaired_formats_keep_the_existing_export_call(
    project, monkeypatch, tmp_path, formats
):
    worker = _install(monkeypatch, PairWorker())
    done = _body(_export(_open(project), tmp_path / "out", formats))
    assert done["status"] == "done", done
    assert [c[0] for c in worker.commands] == [f["format"] for f in done["files"]]
    assert all(c[1] is None for c in worker.commands)


@pytest.mark.parametrize("formats", [["pdf", "svg"], ["svg", "pdf"]])
def test_mcp_older_engine_keeps_pdf_without_independently_drawing_svg(
    project, monkeypatch, tmp_path, formats
):
    worker = _install(monkeypatch, FakeWorker())  # no svg_path in its signature
    done = _body(_export(_open(project), tmp_path / "out", formats))
    assert done["status"] == "partial", done
    assert [c[3] for c in worker.exported] == ["pdf"]
    by_fmt = {f["format"]: f for f in done["files"]}
    assert by_fmt["pdf"]["status"] == "done"
    assert by_fmt["svg"]["error"]["code"] == "svg_source_unsupported"


@pytest.mark.parametrize("formats", [["pdf", "svg"], ["svg", "pdf", "png"]])
def test_mcp_pair_command_failure_is_not_retried_as_independent_draws(
    project, monkeypatch, tmp_path, formats
):
    worker = _install(monkeypatch, PairWorker("pdf"))
    result = _export(_open(project), tmp_path / "out", formats)
    if "png" not in formats:
        assert result["isError"] and _body(result)["code"] == "render_failed"
    else:
        done = _body(result)
        assert done["status"] == "partial", done
        by_fmt = {f["format"]: f for f in done["files"]}
        assert by_fmt["png"]["status"] == "done"
        assert all(by_fmt[f]["error"]["code"] == "render_failed" for f in ("pdf", "svg"))
    assert [c[0] for c in worker.commands].count("pdf") == 1
    assert all(c[0] != "svg" for c in worker.commands)
    assert not list((tmp_path / "out").glob("*.pdf"))
    assert not list((tmp_path / "out").glob("*.svg"))


@pytest.mark.parametrize("when", ["before", "after_pair"])
def test_mcp_pair_cancellation_never_publishes_or_runs_later_formats(
    project, monkeypatch, tmp_path, when
):
    worker = _install(monkeypatch, PairWorker())
    sid = _open(project)
    original = bridge.engine_exportjob.prepare

    def prepare(*args, **kwargs):
        job = original(*args, **kwargs)
        if when == "before":
            job._cancel.set()
        else:
            worker.after_export = job._cancel.set
        return job

    monkeypatch.setattr(bridge.engine_exportjob, "prepare", prepare)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "existing.pdf"
    existing.write_bytes(b"keep existing")
    done = _body(_export(sid, out_dir, ["svg", "png", "pdf"]))
    assert done["status"] == "cancelled" and not done["files"], done
    assert [c[0] for c in worker.commands] == ([] if when == "before" else ["pdf"])
    assert list(out_dir.iterdir()) == [existing] and existing.read_bytes() == b"keep existing"


REAL_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

def main():
    with open("script_runs.txt", "a", encoding="utf-8") as stream:
        stream.write("run\\n")
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4))
    line, = ax.plot([0, 1], [0, 1])
    counter = [0]
    def dynamic_draw(event):
        counter[0] += 1
        line.set_ydata([0, counter[0] % 2])
    def dynamic_formatter(value, position):
        counter[0] += 1
        return str(counter[0])
    if MODE == "callback":
        fig.canvas.mpl_connect("draw_event", dynamic_draw)
    elif MODE == "formatter":
        ax.xaxis.set_major_formatter(FuncFormatter(dynamic_formatter))
    fig.savefig("Fig1.pdf")
"""


@needs_user_python
@pytest.mark.parametrize("mode", ["callback", "formatter", "plain"])
def test_real_mcp_hot_pair_uses_engine_guard_and_single_svg_stays_native(
    project, monkeypatch, tmp_path, mode
):
    (project / "fig1.py").write_text(f"MODE = {mode!r}\n" + REAL_SCRIPT, encoding="utf-8")
    try:
        sid = _open(project)
        worker = bridge.get_session(sid).acquire()
        runs = Path(worker.sandbox) / "script_runs.txt"
        assert runs.read_text(encoding="utf-8") == "run\n"
        result = _export(sid, tmp_path / "pair", ["svg", "pdf"], explicit_confirm=True)
        assert not result.get("isError"), result
        paired = _body(result)
        by_fmt = {f["format"]: f for f in paired["files"]}
        assert by_fmt["pdf"]["status"] == "done", paired
        if mode == "plain":
            assert paired["status"] == "done", paired
        else:
            assert paired["status"] == "partial", paired
            assert by_fmt["svg"]["error"]["code"] == "svg_source_unsupported"
            assert not list((tmp_path / "pair").glob("*.svg"))
        single = _body(_export(sid, tmp_path / "single", ["svg"], explicit_confirm=True))
        assert single["status"] == "done", single
        assert b"<path" in Path(single["files"][0]["path"]).read_bytes()
        assert runs.read_text(encoding="utf-8") == "run\n"
    finally:
        bridge.shutdown_all()


@needs_user_python
def test_real_mcp_cold_journal_restore_runs_once_then_exports_one_pair(project, tmp_path):
    (project / "fig1.py").write_text("MODE = 'plain'\n" + REAL_SCRIPT, encoding="utf-8")
    try:
        sid = _open(project)
        old_worker = bridge.get_session(sid).acquire()
        assert (Path(old_worker.sandbox) / "script_runs.txt").read_text(encoding="utf-8") == "run\n"
        old_worker.shutdown()
        bridge.sessions().clear()  # Persisted MCP sessions retain their existing restore contract.
        result = _export(sid, tmp_path / "restored", ["pdf", "svg"], explicit_confirm=True)
        assert not result.get("isError"), result
        done = _body(result)
        assert done["status"] == "done", done
        worker = bridge.get_session(sid).acquire()
        assert worker is not old_worker
        # The sandbox is intentionally reused; one initial execution plus one
        # journal restore, and neither companion introduces another execution.
        assert (Path(worker.sandbox) / "script_runs.txt").read_text(
            encoding="utf-8"
        ) == "run\nrun\n"
        proof = json.loads(Path(done["proof_path"]).read_text(encoding="utf-8"))
        assert len(proof["files"]) == 2
    finally:
        bridge.shutdown_all()
