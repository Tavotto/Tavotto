"""The two actual attempts retain separate, privacy-safe failure facts."""

from __future__ import annotations

import json

from support import nativekit
from test_execution_fallback_e2e import ENV, _create, _run, _settled, sidecar_app

pytestmark = nativekit.needs_user_python


def test_linked_safe_input_then_native_missing_input_preserves_both_failure_facts(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    selected = project / "data" / "values.csv"
    selected.parent.mkdir()
    selected.write_text("7", encoding="utf-8")
    missing = tmp_path / "absent" / "values.csv"
    (project / "cache.csv").write_text("1", encoding="utf-8")
    (project / "fig.py").write_text(
        "import os\nfrom pathlib import Path\nimport matplotlib.pyplot as plt\n"
        "source = Path(os.environ['SYNTHETIC_INPUT_DIR']) / ('values' + '.csv')\n"
        "data = int(source.read_text())\n"
        "root = Path(__file__).resolve().parent\n"
        "tmp, final = root / 'cache.tmp', root / 'cache.csv'\n"
        "tmp.write_text('2')\n"
        "os.replace(tmp, final)\n"
        "value = int(final.read_text())\n"
        "plt.plot([0, data], [0, value])\n",
        encoding="utf-8",
    )
    env = {**ENV, "SYNTHETIC_INPUT_DIR": str(missing.parent)}
    with sidecar_app(project, tmp_path / "work", env_overrides=env) as app:
        status, linked = app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(selected), "chosen_kind": "file"},
        )
        assert status == 200, linked
        final = _settled(app, _run(app, _create(app)))
        safe, native = final["attempt_history"]
        assert (safe["profile"], safe["code"]) == ("safe", "safe_file_operation_blocked")
        assert (native["profile"], native["code"]) == ("native", "script_error")
        assert not [r for r in final["requirements"] if r["kind"] == "input_location"]
        snapshots = []
        for attempt in (safe, native):
            status, document = app.call(
                f"/api/diagnostics/task?kind=preparation&ref={attempt['attempt_id']}"
            )
            assert status == 200, document
            snapshots.append(document["snapshot"])
        fidelity = snapshots[0]["error"]["fidelity"]
        assert fidelity["level"] == "deviated"
        assert any(row["op"] == "os.replace" for row in fidelity["blocked"])
        assert snapshots[1]["error"]["exception_category"] == "file_not_found"
        shared = json.dumps(snapshots)
        assert str(tmp_path) not in shared
        assert "values.csv" not in shared and "cache.csv" not in shared
        assert (project / "cache.csv").read_text(encoding="utf-8") == "1"
