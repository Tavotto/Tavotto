"""A writeback and its recovery update the same selected figure in both saved copies."""

import copy

import pytest

from tavotto.engine import (
    atomicio,
    layoutannotate,
    layoutdir,
    layoutsession,
    scriptbackup,
    scriptstyle,
)

PATCH = {"gid": "plot.title", "prop": "color", "value": "#572B82"}
BEFORE = b"p <- ggplot2::ggplot()\n"
AFTER = (
    BEFORE + b'p <- p + ggplot2::theme(plot.title = ggplot2::element_text(colour = "#572B82"))\n'
)


@pytest.mark.parametrize("directory_writer", ["0", "1"])
@pytest.mark.parametrize("concurrent_working_edit", [False, True])
def test_recover_r_style_writeback(
    tmp_path, monkeypatch, directory_writer, concurrent_working_edit
):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_LAYOUT_DIR_WRITER", directory_writer)
    project = tmp_path / "project"
    project.mkdir()
    source = project / "figure.R"
    source.write_bytes(BEFORE)
    store = scriptbackup.Store(project, project / "tavottofile/script-backups", tmp_path / "mirror")
    layout = layoutannotate.layout_target(project, "Draft")
    layout.parent.mkdir()
    panel = {
        "id": "signal",
        "type": "panel",
        "fileId": "runtime:signal",
        "fileKind": "pdf",
        "x": 5,
        "y": 5,
        "w": 55,
        "h": 45,
        "overrides": [PATCH],
    }
    other = dict(panel, id="other", fileId="another.pdf")
    before = {
        "schema": 3,
        "project": {"id": "project", "name": "Draft"},
        "activeCanvasId": "main",
        "canvases": [
            {
                "id": "main",
                "schema": 2,
                "name": "Main",
                "page": {"w": 120, "h": 80},
                "guides": [],
                "objects": [panel, other],
            }
        ],
    }
    original_revision = layoutdir.write(layout.parent, layout.stem, before)
    working_id = "d_working"
    working = dict(before, updatedAt=100)
    slot = tmp_path / f"{working_id}.json"
    working_revision = atomicio.write_json(slot, working, indent=1)
    key = layoutsession.project_key(project)
    layoutsession.commit_slot(working_id, key, working_revision)
    after = copy.deepcopy(before)
    after["canvases"][0]["objects"][0]["overrides"] = []
    target = atomicio.dumps_json(after, indent=1)
    request = scriptstyle.Change(
        source.name,
        layout.stem,
        scriptbackup.sha256(BEFORE),
        AFTER,
        original_revision,
        target,
        working_doc_id=working_id,
        canvas_id="main",
        panel_id="signal",
    )

    def require_idle(*_):
        pass

    def interrupt(*_):
        raise InterruptedError("Interrupted after publishing the named layout.")

    def autosave_path(doc_id):
        assert doc_id == working_id
        return slot

    with monkeypatch.context() as interrupted:
        interrupted.setattr(scriptstyle, "_finish_working", interrupt)
        with pytest.raises(scriptstyle.Pending):
            scriptstyle.commit(
                store, request, require_idle=require_idle, autosave_path=autosave_path, metadata={}
            )
    assert source.read_bytes() == AFTER
    assert layoutannotate.open_layout(project, layout.stem).doc == after
    assert atomicio.existing_revision(slot) == working_revision
    backup_meta = scriptbackup.history(store, source.name)[0]
    assert scriptstyle.matches_saved_layout(dict(after, updatedAt=200), "changed", backup_meta)
    renamed = copy.deepcopy(after)
    renamed["canvases"][0]["objects"][1]["x"] = 47
    assert not scriptstyle.matches_saved_layout(renamed, "changed", backup_meta)
    for backup_root in (store.project_dir, store.mirror_dir):
        (backup,) = backup_root.glob("*/*/original.R")
        assert backup.read_bytes() == BEFORE

    if concurrent_working_edit:
        working = copy.deepcopy(working)
        working["canvases"][0]["objects"][1]["x"] = 47
        revision = atomicio.write_json(slot, working, indent=1)
        layoutsession.commit_slot(working_id, key, revision)
        current = slot.read_bytes()
    (journal,) = scriptstyle.journals(project)
    assert (
        scriptstyle.recover(
            project,
            journal,
            require_idle=require_idle,
            autosave_path=autosave_path,
        )
        == "completed"
    )
    assert not scriptstyle.journals(project)
    if concurrent_working_edit:
        assert slot.read_bytes() == current
    else:
        assert slot.read_bytes() == atomicio.dumps_json(dict(after, updatedAt=100), indent=1)
        assert (
            layoutsession.owner_verdict(working_id, key, atomicio.existing_revision(slot)) == "this"
        )
