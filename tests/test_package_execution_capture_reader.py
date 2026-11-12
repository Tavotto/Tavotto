"""The package reader derives current source gaps from the actual document."""

from __future__ import annotations

import copy
import io
import json

from tavotto.engine import documentpackage
from test_svg_input_package_e2e import (
    INSPECT,
    _cold_package,
    _prepare,
    _remove_source_and_cache,
    _rewrite,
    _run_safe,
    project as project,
)


def test_reader_does_not_let_capture_hide_uncaptured_same_script_when_context_is_omitted(
    project, tmp_path
):
    client, root, counter = project
    run, _argv_log = _prepare(project)
    document, _references, worker = _run_safe(project, run, name="CaptureReader")
    _remove_source_and_cache(project, worker.base)
    _report, manifest, members = _cold_package(root, document, tmp_path)
    assert manifest["execution"]["version"] == 2
    assert manifest["execution"]["contexts"] == []
    assert not any(row["kind"] == "script" for row in manifest["assets"])
    assert not (root / "plot.py").exists()

    # A sender can omit current contexts. The extra panel is a current use even
    # when its same-named sibling carries valid historical script/input bytes.
    changed_document = copy.deepcopy(document)
    panels = changed_document["canvases"][0]["objects"]
    current = copy.deepcopy(panels[0])
    current["id"] = "uncaptured_current_panel"
    current.pop("figureSnapshot")
    panels.append(current)
    changed_manifest = copy.deepcopy(manifest)
    changed_manifest["missing_at_pack_time"] = ["plot.py"]
    package = _rewrite(
        members,
        {
            documentpackage.LAYOUT_MEMBER: json.dumps(changed_document).encode(),
            documentpackage.MANIFEST_MEMBER: json.dumps(changed_manifest).encode(),
        },
    )
    response = client.post(
        INSPECT,
        data={"package": (io.BytesIO(package), "missing-current.tavotto")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    inspected = response.get_json()
    assert len(inspected["captured_inputs"]) == 1
    assert "plot.py" in inspected["missing"]
    assert counter.read_bytes() == b"run\n"
