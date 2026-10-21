"""Run-before routing changes the runner, never the evidence that owns an SVG snapshot."""

# ruff: noqa: F811 — shared project fixture, used by name in the test signature
from __future__ import annotations

import time

import pytest

import test_execution_fallback_e2e as fallback
import test_export_svg as svg_cases
import test_run_routing_e2e as routing
from tavotto import app as m
from tavotto.engine import runcodes
from test_export_svg import document, project, spec  # noqa: F401
from test_svg_execution_lifecycle import exported, save


def test_routed_native_launch_failure_does_not_invent_execution_or_retire_saved_svg(
    project, monkeypatch
):
    client, root = project
    source = svg_cases.run_script(client)
    reference = save(client, source)["figure_snapshots"]["panel"]
    log = root.parent / "compute.log"
    (root / "plot.py").write_text(routing.READBACK.format(log=str(log)), encoding="utf-8")
    routing.write_counter(root, log)
    monkeypatch.setattr(m, "_desktop_initiated", lambda: True)

    def fail_before_user_code(*args, **kwargs):
        raise runcodes.RunError(runcodes.DESKTOP_RUN_LAUNCH_INVALID)

    monkeypatch.setattr(m.engine_desktopproc, "start", fail_before_user_code)
    report = client.post(
        fallback.SESSIONS, json={"script": "plot.py", "entry": "__main__"}
    ).get_json()
    assert report["plan"]["route"]["execution"] == "native"
    (run,) = [action for action in report["actions"] if action["kind"] == "run"]
    url = f"{fallback.SESSIONS}/{report['session_id']}"
    claimed = client.post(
        f"{url}/actions",
        json={"action_id": run["id"], "expected_config_revision": report["config_revision"]},
    )
    assert claimed.status_code == 202, claimed.get_json()
    deadline = time.monotonic() + 30
    while True:
        final = client.get(url).get_json()
        if final["phase"] in fallback.SETTLED:
            break
        assert time.monotonic() < deadline, final
        time.sleep(0.02)
    (attempt,) = final["attempt_history"]
    assert (attempt["profile"], attempt["code"]) == ("native", "desktop_run_launch_invalid")
    assert "figure_snapshot_invalidation" not in final
    assert attempt["attempt_id"] not in m.current_ctx().svg_execution_attempts
    assert fallback._runs(log) == 0
    assert exported(client, source, reference)["status"] == "done"


@fallback.nativekit.needs_user_python
def test_routed_native_saves_svg_once_and_a_later_failed_execution_retires_it(tmp_path):
    proj, log = routing._project(tmp_path, routing.READBACK)
    with fallback.sidecar_app(proj, tmp_path / "work", env_overrides=fallback.ENV) as app:
        first = fallback._create(app)
        assert first["plan"]["route"]["execution"] == "native"
        final = fallback._settled(app, fallback._run(app, first))
        assert final["phase"] == "completed", final
        assert [attempt["profile"] for attempt in final["attempt_history"]] == ["native"]
        assert fallback._runs(log) == 1
        fact = final["figure_snapshot_invalidation"]
        source = final["captured"][0]["asset_id"]
        _, saved = app.call("/api/layouts/saved?target=project", document(source, []))
        reference = saved["figure_snapshots"]["panel"]
        assert reference["executionOwner"] == {"script": "fig.py", "run_config": None}
        request = spec(source, scope="original", formats=("pdf", "svg"), reference=reference)
        _, result = app.call("/api/export", request)
        assert result["status"] == "done", result
        assert all(item["status"] == "done" for item in result["outputs"]), result
        assert fallback._runs(log) == 1, "Saving and exporting must not execute user code again"

        script = proj / "fig.py"
        script.write_text(
            script.read_text(encoding="utf-8") + '\nraise RuntimeError("after readback")\n',
            encoding="utf-8",
        )
        again = routing._reopen(app)
        assert again["plan"]["route"]["execution"] == "native"
        failed = fallback._settled(app, fallback._run(app, again))
        assert failed["outcome"]["code"] == "script_error", failed
        assert fallback._runs(log) == 2
        invalidation = failed["figure_snapshot_invalidation"]
        assert invalidation["scope"] == "owner"
        assert invalidation["execution_id"] != fact["execution_id"]
        assert reference["receiptId"] in invalidation["receipt_ids"]
        with pytest.raises(fallback.fa.HttpError) as rejected:
            app.call("/api/export", request)
        assert rejected.value.status >= 400
        assert rejected.value.body["status"] == "failed", rejected.value.body
        assert rejected.value.body["code"] == "svg_source_unsupported", rejected.value.body
        assert rejected.value.body["outputs"] == []
        assert fallback._runs(log) == 2
