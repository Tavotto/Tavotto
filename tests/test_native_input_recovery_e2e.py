"""Disposable, real-app experiment: user-reviewed edit for a static input path."""

from __future__ import annotations

import json
from types import SimpleNamespace

from support import nativekit
from tavotto import app as app_module
from tavotto.engine import pool, preparation
from test_execution_fallback_e2e import ENV, _create, _run, _settled, sidecar_app

pytestmark = nativekit.needs_user_python


def test_a_reviewed_static_path_edit_can_recover_the_two_attempt_sequence(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    data = root / "data" / "values.csv"
    data.parent.mkdir()
    data.write_text("7", encoding="utf-8")
    missing = tmp_path / "missing" / "values.csv"
    (root / "cache.csv").write_text("1", encoding="utf-8")
    original = (
        "import os\nfrom pathlib import Path\nimport matplotlib.pyplot as plt\n"
        f"data = int(open({str(missing)!r}, encoding='utf-8').read())\n"
        "root = Path(__file__).resolve().parent\n"
        "name = os.environ.get('CACHE_NAME', 'cache')\n"
        "tmp, final = root / (name + '.tmp'), root / (name + '.csv')\n"
        "tmp.write_text('2')\n"
        "os.replace(tmp, final)\n"
        "value = int(final.read_text())\n"
        "plt.plot([0, data], [0, value])\n"
    )
    script = root / "fig.py"
    script.write_text(original, encoding="utf-8")
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        status, link = app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        assert status == 200, link
        first = _settled(app, _run(app, _create(app)))
        assert [(a["profile"], a["code"]) for a in first["attempt_history"]] == [
            ("safe", "safe_file_operation_blocked"),
            ("native", "script_error"),
        ]
        (requirement,) = [r for r in first["requirements"] if r["kind"] == "input_location"]
        assert requirement["payload"]["requested"] == str(missing)
        assert requirement["payload"]["via"] == "native"
        attempt = first["attempt_history"][-1]["attempt_id"]
        status, diagnostic = app.call(f"/api/diagnostics/task?kind=preparation&ref={attempt}")
        assert status == 200
        assert str(missing) not in json.dumps(diagnostic)
        assert (root / "cache.csv").read_text() == "1"
        status, preview = app.call(
            "/api/script-edit/input-path/preview",
            {"script": "fig.py", "entry": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        assert status == 200, preview
        assert script.read_text(encoding="utf-8") == original
        assert len(preview["rows"]) == 1
        # This simulates an explicit user approval in an isolated test project.
        status, committed = app.call("/api/script-edit/commit", {"token": preview["token"]})
        assert status == 200, committed
        assert script.read_text(encoding="utf-8") != original
        status, report = app.call("/api/engine/preparation-sessions", {"script": "fig.py"})
        assert status in (200, 201), report
        final = _settled(app, _run(app, report))
        assert final["phase"] == "completed", final
        assert [a["profile"] for a in final["attempt_history"][-2:]] == ["safe", "native"]
        assert (root / "cache.csv").read_text() == "2"


def test_a_caught_read_then_a_failed_write_never_offers_a_data_rewrite(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    missing = tmp_path / "missing" / "values.csv"
    (root / "fig.py").write_text(
        "try:\n"
        f"    open({str(missing)!r}, encoding='utf-8')\n"
        "except FileNotFoundError:\n    pass\n"
        f"open({str(missing)!r}, 'w', encoding='utf-8')\n",
        encoding="utf-8",
    )
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        status, report = app.call(
            "/api/engine/preparation-sessions", {"script": "fig.py", "execution": "native"}
        )
        assert status == 201, report
        final = _settled(app, _run(app, report))
        assert final["outcome"]["code"] == "script_error"
        assert not [r for r in final["requirements"] if r["kind"] == "input_location"]


def test_nested_opener_offers_the_inner_missing_input_not_the_outer_path(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    data = tmp_path / "absent" / "data.csv"
    key = tmp_path / "absent" / "key.csv"
    script = root / "fig.py"
    script.write_text(
        "import os\n"
        f"key = {str(key)!r}\n"
        "def opener(p, flags):\n"
        "    open(key, encoding='utf-8').read()\n"
        "    return os.open(p, flags)\n"
        f"open({str(data)!r}, encoding='utf-8', opener=opener)\n",
        encoding="utf-8",
    )
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        status, report = app.call(
            "/api/engine/preparation-sessions", {"script": "fig.py", "execution": "native"}
        )
        assert status == 201, report
        final = _settled(app, _run(app, report))
        (requirement,) = [r for r in final["requirements"] if r["kind"] == "input_location"]
        assert requirement["payload"]["requested"] == str(key)
        assert requirement["payload"]["requested"] != str(data)


def test_sensitive_native_run_does_not_offer_the_private_path(tmp_path):
    failure = pool.WorkerError("failed", code="script_error")
    session = SimpleNamespace(
        exception_category="file_not_found",
        take_missing_read=lambda: {"requested": str(tmp_path / "secret"), "cwd": str(tmp_path)},
    )
    plan = SimpleNamespace(
        script="fig.py",
        project_root=tmp_path,
        script_revision="present",
        run=SimpleNamespace(sensitive=True),
    )
    assert app_module._native_missing_input_offer(failure, session, plan).missing_input is None


def test_source_changed_after_the_run_never_offers_a_stale_rewrite(tmp_path):
    missing = tmp_path / "absent" / "input.csv"
    script = tmp_path / "fig.py"
    script.write_text(f"open({str(missing)!r}).read()\n", encoding="utf-8")
    revision = preparation._script_revision(str(tmp_path), "fig.py")
    script.write_text(f"open({str(missing)!r}).read()\nprint('changed')\n", encoding="utf-8")
    failure = pool.WorkerError("failed", code="script_error")
    session = SimpleNamespace(
        exception_category="file_not_found",
        take_missing_read=lambda: {"requested": str(missing), "cwd": str(tmp_path)},
    )
    plan = SimpleNamespace(
        script="fig.py", project_root=tmp_path, script_revision=revision, run=None
    )
    assert app_module._native_missing_input_offer(failure, session, plan).missing_input is None


def test_an_output_literal_does_not_become_a_rewrite_candidate(tmp_path):
    missing = tmp_path / "absent" / "output.csv"
    script = tmp_path / "fig.py"
    script.write_text(f"open({str(missing)!r}, 'w').write('output')\n", encoding="utf-8")
    failure = pool.WorkerError("failed", code="script_error")
    session = SimpleNamespace(
        exception_category="file_not_found",
        take_missing_read=lambda: {"requested": str(missing), "cwd": str(tmp_path)},
    )
    plan = SimpleNamespace(
        script="fig.py",
        project_root=tmp_path,
        script_revision=preparation._script_revision(str(tmp_path), "fig.py"),
        run=None,
    )
    assert app_module._native_missing_input_offer(failure, session, plan).missing_input is None
