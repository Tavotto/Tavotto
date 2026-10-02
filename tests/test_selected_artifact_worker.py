"""The selected-file policy reuses live editing without historical scene guessing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import artifactcontext, figcapture, pool
from test_savefig_capture_params import WORKER_PY, needs_worker

SOURCE = """\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([0,1,2], [2,0,3])
ax.set(xlabel='Time', ylabel='Response', title='Selected artifact')
LAYOUT
fig.savefig('same.pdf', bbox_inches='tight', pad_inches=.02)
fig.savefig('same.png', dpi=100)
plt.close('all')
"""


def context(root, name):
    return {
        **figcapture.source_artifact_from_file(
            root / name, source_id=name, origin="static"
        ).to_payload(),
        "render_policy": figcapture.SELECTED_ARTIFACT_POLICY,
    }


def project(tmp_path, layout=""):
    script = tmp_path / "figure.py"
    script.write_text(SOURCE.replace("LAYOUT", layout), encoding="utf-8")
    proc = subprocess.run(
        [WORKER_PY, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return tmp_path


@needs_worker
@pytest.mark.parametrize(
    "layout",
    ["", "fig.tight_layout()", "ax.clear(); ax.bar([0,1],[1,2])"],
    ids=["fixed", "one-shot", "bar"],
)
def test_source_context_selects_its_own_frame_and_preserves_initial_pixels(tmp_path, layout):
    root = project(tmp_path, layout)
    original = (root / "same.png").read_bytes()
    workers = []
    try:
        frames = {}
        for name in ("same.pdf", "same.png"):
            worker = pool.one_shot(
                "figure.py", str(root), "__main__", artifact_source=context(root, name)
            )
            workers.append(worker)
            result = worker.ensure_built()
            probe = result["artifact_probe"]
            assert probe["context"]["source_id"] == name
            assert probe["occurrence"] == (1 if name.endswith(".pdf") else 2)
            rendered = worker.override("same", [], inline_svg=True)
            assert not rendered["warnings"]
            frames[name] = rendered["manifest"]["size_mm"]
            if name.endswith(".png"):
                assert Path(probe["initialized"]).read_bytes() == original
        assert frames["same.png"] == [101.6, 76.2]
        assert frames["same.pdf"] != frames["same.png"]
        assert (root / "same.png").read_bytes() == original
    finally:
        for worker in workers:
            pool.discard(worker)


@needs_worker
def test_active_layout_is_refused_before_an_edit(tmp_path):
    root = project(tmp_path, "fig.set_layout_engine('tight')")
    worker = pool.one_shot(
        "figure.py", str(root), "__main__", artifact_source=context(root, "same.png")
    )
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.override("same", [{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}])
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "layout_history_required"
    finally:
        pool.discard(worker)


def test_policy_and_bytes_are_part_of_identity_but_legacy_key_is_unchanged(tmp_path):
    (tmp_path / "same.png").write_bytes(b"first")
    first = context(tmp_path, "same.png")
    (tmp_path / "same.png").write_bytes(b"second")
    second = context(tmp_path, "same.png")
    assert pool._worker_key(str(tmp_path), "figure.py") == (
        pool._norm_dir(str(tmp_path)),
        "figure.py",
    )
    assert pool._worker_key(str(tmp_path), "figure.py", first) != pool._worker_key(
        str(tmp_path), "figure.py", second
    )
    assert pool._worker_base(str(tmp_path), "figure.py", first) != pool._worker_base(
        str(tmp_path), "figure.py", second
    )
    assert figcapture.selected_artifact_context(json.loads(json.dumps(first))) == first


def test_preparation_cancellation_does_not_kill_selected_context(tmp_path, monkeypatch):
    class Worker:
        def __init__(self):
            self.killed = False

        def force_kill(self):
            self.killed = True

    legacy, selected = Worker(), Worker()
    (tmp_path / "same.png").write_bytes(b"first")
    src = context(tmp_path, "same.png")
    monkeypatch.setattr(
        pool,
        "_workers",
        {
            pool._worker_key(str(tmp_path), "figure.py"): legacy,
            pool._worker_key(str(tmp_path), "figure.py", src): selected,
        },
    )
    assert pool.force_cancel("figure.py", str(tmp_path))
    assert legacy.killed and not selected.killed
    assert pool.force_cancel("figure.py", str(tmp_path), artifact_source=src)
    assert selected.killed


@needs_worker
@pytest.mark.parametrize(
    "hook",
    [
        "fig.canvas.mpl_connect('draw_event', lambda event: None)",
        "ax.lines[0].add_callback(lambda artist: None)",
        "ax.lines[0].stale_callback = lambda artist, stale: None",
        "from matplotlib.ticker import FuncFormatter; ax.xaxis.set_major_formatter(FuncFormatter(lambda x,p: str(x)))",
        "ax.xaxis.get_major_formatter().set_useLocale(True)",
        "from matplotlib.ticker import AutoLocator\nclass CustomLocator(AutoLocator): pass\nax.xaxis.set_major_locator(CustomLocator())",
        "from matplotlib.transforms import Affine2D\nclass CustomTransform(Affine2D): pass\nax.lines[0].set_transform(CustomTransform())",
        "from matplotlib.transforms import Affine2D\nclass CustomTransform(Affine2D): pass\nax.scatter([0], [1]).set_offset_transform(CustomTransform())",
        "ax.lines[0].draw = lambda renderer: None",
        "fig.canvas.draw = lambda: None",
        "ax.set_xscale('log')",
        "import threading; timer = threading.Timer(60, lambda: ax.lines[0].set_color('blue')); timer.daemon = True; timer.start()",
    ],
)
def test_custom_or_state_sensitive_render_hooks_refuse(tmp_path, hook):
    root = project(tmp_path)
    script = root / "figure.py"
    # Source remains a valid original. The replay adds the unsupported hook.
    script.write_text(SOURCE.replace("LAYOUT", hook).replace("plt.close('all')", ""))
    worker = pool.one_shot(
        "figure.py", str(root), "__main__", artifact_source=context(root, "same.png")
    )
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.ensure_built()
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "unsupported_render_state"
    finally:
        pool.discard(worker)


@needs_worker
@pytest.mark.parametrize("second_figure", [False, True])
def test_repeated_target_is_ambiguous_even_if_the_final_pixels_match(tmp_path, second_figure):
    root = project(tmp_path)
    script = root / "figure.py"
    extra = "fig,ax=plt.subplots();ax.plot([0,1],[1,0])\n" if second_figure else ""
    script.write_text(
        SOURCE.replace("LAYOUT", "").replace(
            "plt.close('all')", extra + "fig.savefig('same.png', dpi=100)"
        )
    )
    worker = pool.one_shot(
        "figure.py", str(root), "__main__", artifact_source=context(root, "same.png")
    )
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.ensure_built()
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "save_ambiguous"
    finally:
        pool.discard(worker)


def test_input_stop_targets_the_exact_context_directory(tmp_path, monkeypatch):
    class Worker:
        def __init__(self, name):
            self.out_dir = tmp_path / name
            self.killed = False

        def force_kill(self):
            self.killed = True

    root = pool._norm_dir(str(tmp_path))
    legacy, selected, other = Worker("legacy"), Worker("selected"), Worker("other")
    monkeypatch.setattr(
        pool,
        "_workers",
        {
            (root, "f.py"): legacy,
            (root, "f.py", "context"): selected,
            ("other-project", "f.py"): other,
        },
    )
    assert pool.force_cancel_input(selected.out_dir / "script-input", str(tmp_path))
    assert selected.killed and not legacy.killed and not other.killed
    assert not pool.force_cancel_input(selected.out_dir / "script-input", str(tmp_path))


@needs_worker
def test_selected_validation_budget_precedes_renderer_allocation(tmp_path):
    root = project(tmp_path)
    script = root / "figure.py"
    script.write_text(SOURCE.replace("LAYOUT", "fig.set_size_inches(1000,1000)"))
    original = (root / "same.png").read_bytes()
    worker = pool.one_shot(
        "figure.py", str(root), "__main__", artifact_source=context(root, "same.png")
    )
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.ensure_built()
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "validation_budget_exceeded"
        assert (root / "same.png").read_bytes() == original
    finally:
        pool.discard(worker)


@needs_worker
@pytest.mark.parametrize("change", ["after_save", "missing_parent_exception"])
def test_source_validation_refuses_changed_scene_and_suppressed_exception(tmp_path, change):
    source = SOURCE.replace("LAYOUT", "")
    if change == "after_save":
        source = source.replace(
            "plt.close('all')", "ax.lines[0].set_color('#0000aa')\nplt.close('all')"
        )
    else:
        source = source.replace(
            "fig.savefig('same.pdf'",
            "try:\n    fig.savefig('missing/blocked.png')\nexcept OSError:\n    ax.lines[0].set_color('#0000aa')\nfig.savefig('same.pdf'",
        )
    script = tmp_path / "figure.py"
    script.write_text(source)
    subprocess.run(
        [WORKER_PY, str(script)], cwd=tmp_path, check=True, capture_output=True, timeout=60
    )
    original = (tmp_path / "same.png").read_bytes()
    selected = context(tmp_path, "same.png")
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=selected)
    try:
        probe = worker.ensure_built()["artifact_probe"]
        with pytest.raises(artifactcontext.ArtifactContextError) as error:
            artifactcontext.validate_candidate(
                tmp_path / "same.png", Path(probe["initialized"]), selected
            )
        assert error.value.reason == "source_mismatch"
        assert (tmp_path / "same.png").read_bytes() == original
    finally:
        pool.discard(worker)


@pytest.mark.parametrize("operation", ["render", "export"])
def test_selected_workerd_restart_cannot_bypass_admission(tmp_path, monkeypatch, operation):
    from tavotto.engine import workerd_client
    from test_workerd_pool import _worker

    worker, client = _worker(
        monkeypatch,
        tmp_path,
        [
            {"ok": True, "session_id": "old"},
            workerd_client.WorkerdError("expired", code="unknown_session"),
            {"ok": True, "session_id": "new"},
            {"ok": True},
        ],
    )
    (tmp_path / "same.png").write_bytes(b"original")
    worker.artifact_source = context(tmp_path, "same.png")
    worker.built = True
    with pytest.raises(pool.WorkerError) as error:
        worker._call(
            operation,
            10,
            stem="same",
            payload={"patches": [{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}]},
        )
    assert error.value.code == "artifact_source_unavailable"
    assert error.value.extra["reason"] == "source_changed"
    assert not worker.alive()
    assert [op for op, _ in client.calls] == ["open_session", operation]


def test_failed_old_worker_does_not_cancel_replacement(tmp_path, monkeypatch):
    class Worker:
        killed = False

        def force_kill(self):
            self.killed = True

    old, replacement = Worker(), Worker()
    (tmp_path / "same.png").write_bytes(b"original")
    selected = context(tmp_path, "same.png")
    key = pool._worker_key(str(tmp_path), "f.py", selected)
    monkeypatch.setattr(pool, "_workers", {key: replacement})
    assert not pool.force_cancel(
        "f.py", str(tmp_path), artifact_source=selected, expected_worker=old
    )
    assert pool._workers[key] is replacement and not replacement.killed
    assert pool.force_cancel(
        "f.py", str(tmp_path), artifact_source=selected, expected_worker=replacement
    )
    assert replacement.killed and key not in pool._workers


@needs_worker
def test_real_selected_api_preserves_other_registration_and_refuses_writeback(
    tmp_path, monkeypatch
):
    from tavotto import app as app_module

    root = project(tmp_path)
    registry = root / "tavotto_registry.json"
    registry.write_text(
        json.dumps(
            {
                "scripts": {
                    "figure.py": {"entry": "__main__", "stems": ["same", "other"], "cost": "light"}
                }
            }
        )
    )
    monkeypatch.setattr(app_module, "PROJECTS", {})
    monkeypatch.setattr(app_module, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(app_module.engine_inputremap, "registration_stale", lambda *a: True)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    app_module.open_project(str(root))
    original = (root / "same.png").read_bytes()
    try:
        response = client.post(
            "/api/engine/render",
            json={
                "id": "./same.png",
                "source_policy": figcapture.SELECTED_ARTIFACT_POLICY,
                "patches": [],
                "inline_svg": True,
            },
        )
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert data["manifest"]["size_mm"] == [101.6, 76.2]
        assert data["artifact_source"]["source_id"] == "same.png"
        assert json.loads(registry.read_text())["scripts"]["figure.py"]["stems"] == [
            "same",
            "other",
        ]
        response = client.post(
            "/api/engine/update_source",
            json={
                "id": "same.png",
                "source_policy": figcapture.SELECTED_ARTIFACT_POLICY,
                "patches": [],
            },
        )
        assert response.status_code == 409
        assert response.get_json()["params"]["reason"] == "writeback_not_supported"
        assert (root / "same.png").read_bytes() == original
    finally:
        for project_id in list(app_module.PROJECTS):
            app_module.close_project(project_id, wait=True)
