"""Commit a verified style edit to a script and its named layout as one operation.

The journal needs neither a second document store nor a copy of the rewritten
script. When the script is still at the before hash there is nothing to apply.
After the script write, the verified document is sufficient to finish the job.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (
    atomicio,
    config,
    doclock,
    documents,
    layoutannotate,
    layoutdir,
    layoutsession,
    scriptbackup,
    scriptlock,
)

KIND = "figure_style"


class Conflict(ValueError):
    """The current script or layout needs an explicit user decision."""

    code = "script_style_conflict"


class Pending(Conflict):
    code = "script_style_pending"


ERROR_CODES = (Conflict.code, Pending.code)


@dataclass(frozen=True)
class Change:
    script: str
    layout: str
    script_before: str
    script_after: bytes
    layout_before: str
    layout_after: bytes | Callable[[], bytes]
    working_doc_id: str | None = None
    canvas_id: str | None = None
    panel_id: str | None = None


def select_panel(document: dict, canvas_id: str | None, panel_id: str | None) -> dict:
    panels = [
        item
        for canvas in document.get("canvases", [])
        if isinstance(canvas, dict) and canvas.get("id") == canvas_id
        for item in canvas.get("objects", [])
        if isinstance(item, dict) and item.get("type") == "panel" and item.get("id") == panel_id
    ]
    if len(panels) != 1:
        raise Conflict("Select a current figure from the saved layout.")
    return panels[0]


def _journal_directory(root: Path) -> Path:
    project = config.normalize_path_identity(root.resolve())
    key = scriptbackup.sha256(project.encode("utf-8"))
    return config.data_path("script_styles", key)


def _journal_for(root: Path, script: str) -> Path:
    key = scriptbackup.sha256(script.encode("utf-8"))
    return _journal_directory(root) / f"{key}.json"


def pending(root: Path, script: str) -> bool:
    return _journal_for(root, script).is_file()


def journals(root: Path) -> list[Path]:
    return sorted(_journal_directory(root).glob("*.json"))


def _discard(journal: Path) -> None:
    journal.unlink()
    atomicio.fsync_dir(journal.parent)


def _layout_revision(layout: Path) -> str | None:
    located = layoutdir.locate(layout.parent, layout.stem)
    if located.state == layoutdir.CONFLICT or located.problem is not None:
        raise Conflict("The layout has a conflicting on-disk version.")
    return located.current_revision()


def _layout_content_revision(document: dict) -> str:
    # Automatic saves can advance the timestamp without changing the layout.
    contents = {key: value for key, value in document.items() if key != "updatedAt"}
    return atomicio.revision_of(
        json.dumps(contents, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
    )


def matches_saved_layout(document: dict, revision: str, backup: dict) -> bool:
    expected = backup.get("layout_after_content_revision")
    return revision == backup["layout_after_revision"] or (
        isinstance(expected, str) and _layout_content_revision(document) == expected
    )


def _working_plan(root: Path, layout: Path, change: Change, autosave_path) -> dict | None:
    doc_id = change.working_doc_id
    if doc_id is None:
        return None
    if not layoutsession.valid_doc_id(doc_id) or autosave_path is None:
        raise Conflict("The working layout could not be verified.")
    key = layoutsession.project_key(root)
    slot = autosave_path(doc_id)
    with doclock.document_lock(slot):
        if not slot.is_file() or slot.is_symlink():
            raise Conflict("The working layout could not be verified.")
        data = slot.read_bytes()
        baseline = atomicio.revision_of(data)
        state = layoutsession.read_project_file(key, doc_id, actual_autosave_revision=baseline)
        binding = state.get("binding")
        if (
            layoutsession.owner_verdict(doc_id, key, baseline) != "this"
            or state.get("status") != "ok"
            or (
                binding is not None
                and (
                    binding["dirty"]
                    or binding["file"] != layout.relative_to(root).as_posix()
                    or binding["revision"] != change.layout_before
                )
            )
        ):
            raise Conflict("Save the working layout before writing to the script.")
        working = documents.loads_document(data)
        active = select_panel(working, change.canvas_id, change.panel_id)
        prior_layout = layoutannotate.open_layout(root, change.layout)
        prior = select_panel(prior_layout.doc, change.canvas_id, change.panel_id)
        if (
            active.get("fileId") != prior.get("fileId")
            or active.get("figureSnapshot") != prior.get("figureSnapshot")
            or (active.get("overrides") or []) != (prior.get("overrides") or [])
        ):
            raise Conflict("Save the current figure in the working layout before continuing.")
        return {
            "id": doc_id,
            "before": baseline,
            "binding": binding,
            "axis": state["edit"]["axis"],
            "document": working,
        }


def _replace_working_panel(working: dict, document: dict, change: Change) -> None:
    active = select_panel(working["document"], change.canvas_id, change.panel_id)
    target = select_panel(document, change.canvas_id, change.panel_id)
    for prop in ("figureSnapshot", "overrides"):
        if prop in target:
            active[prop] = target[prop]
        else:
            active.pop(prop, None)
    working["after"] = atomicio.revision_of(atomicio.dumps_json(working["document"], indent=1))


def _finish_working(root: Path, record: dict, autosave_path) -> str | None:
    working = record.get("working")
    if working is None:
        return
    doc_id = working["id"]
    if not layoutsession.valid_doc_id(doc_id) or autosave_path is None:
        raise Conflict("The working layout could not be recovered.")
    slot = autosave_path(doc_id)
    key = layoutsession.project_key(root)
    with doclock.document_lock(slot):
        state = layoutsession.read_project_file(key, doc_id)
        if state.get("status") != "ok":
            raise Pending("The working layout could not be verified during recovery.")
        binding = state.get("binding")
        axis = working["axis"]
        actual = None if slot.is_symlink() else atomicio.existing_revision(slot)
        old, new = working["before"], working["after"]
        if (
            actual not in (old, new)
            or layoutsession.owner_verdict(doc_id, key) != "this"
            or binding != working["binding"]
            or state["edit"]["axis"] != axis
            or state["edit"]["autosave_revision"] not in (old, new)
        ):
            return  # preserve a working copy that another edit or open has superseded
        data = atomicio.dumps_json(working["document"], indent=1)
        if atomicio.revision_of(data) != new:
            raise Conflict("The pending working layout could not be verified.")
        if actual != new:
            atomicio.write_bytes(slot, data)
        if binding is None:
            layoutsession.commit_slot(doc_id, key, new)
            return new
        result = layoutsession.commit_slot(
            doc_id,
            key,
            new,
            project_file_edit={
                "axis": axis,
                "base_axis": axis,
                "base_revision": state["edit"]["autosave_revision"],
            },
            actual_base_revision=state["edit"]["autosave_revision"],
        )
        if result["project_file"]["state"] != "accepted":
            return
        reservation = layoutsession.reserve_project_file(
            key,
            doc_id,
            name=binding["name"],
            file=binding["file"],
            autosave_revision=new,
            actual_autosave_revision=new,
            axis=axis,
            expected_binding_sequence=binding["sequence"],
        )
        if reservation["state"] == "reserved":
            layoutsession.commit_project_file(
                key, doc_id, reservation["ticket"], revision=record["layout_after"]
            )
        return new


def commit(
    store: scriptbackup.Store,
    change: Change,
    *,
    require_idle: Callable[[Path, str], None],
    metadata: dict,
    autosave_path: Callable[[str], Path] | None = None,
) -> dict:
    """Save both backups before replacing the script and updating the layout."""
    root = Path(store.root)
    script = scriptbackup.resolve(root, change.script)
    layout = layoutannotate.layout_target(root, change.layout)
    journal = _journal_for(root, change.script)
    with scriptlock.script_guard(script):
        require_idle(script, change.script)
        if journal.exists():
            raise Pending("This script has a pending source style change.")
        working = _working_plan(root, layout, change, autosave_path)
        operation = secrets.token_hex(16)
        with doclock.document_lock(layout):
            if scriptbackup.sha256(script.read_bytes()) != change.script_before:
                raise Conflict("The original script changed after preview.")
            if _layout_revision(layout) != change.layout_before:
                raise Conflict("The saved layout changed after preview.")
            require_idle(script, change.script)
            target = change.layout_after() if callable(change.layout_after) else change.layout_after
            document = documents.loads_document(target)
            wanted_revision = atomicio.revision_of(target)
            if working is not None:
                _replace_working_panel(working, document, change)
            record = {
                "operation": operation,
                "script": change.script,
                "layout": change.layout,
                "canvas_id": change.canvas_id,
                "panel_id": change.panel_id,
                "script_before": change.script_before,
                "script_after": scriptbackup.sha256(change.script_after),
                "layout_before": change.layout_before,
                "layout_after": wanted_revision,
                "document": document,
                "working": working,
            }
            journal.parent.mkdir(parents=True, exist_ok=True)
            atomicio.write_json(journal, record, indent=1)
            atomicio.fsync_dir(journal.parent.parent)
            atomicio.fsync_dir(journal.parent.parent.parent)
            try:
                backup = scriptbackup.replace(
                    store,
                    change.script,
                    change.script_after,
                    kind=KIND,
                    expect_before=change.script_before,
                    meta={
                        **metadata,
                        "style_change": operation,
                        "layout": change.layout,
                        "layout_after_revision": wanted_revision,
                        "layout_after_content_revision": _layout_content_revision(document),
                    },
                )
            except scriptbackup.ScriptEditError:
                _discard(journal)
                raise
            if not backup.get("durable", True):
                raise Pending("The source replacement could not be confirmed on disk.")
            try:
                revision = layoutdir.write(layout.parent, layout.stem, target)
                if revision != wanted_revision:
                    raise Conflict("Could not verify the resulting saved layout.")
            except (OSError, layoutdir.LayoutDirError, Conflict) as exc:
                raise Pending(
                    "The source changed and the saved layout still needs recovery."
                ) from exc
        try:
            working_revision = _finish_working(root, record, autosave_path)
            _discard(journal)
        except (OSError, Conflict) as exc:
            raise Pending("The source changed and working layout recovery is pending.") from exc
    return {"revision": revision, "backup": backup, "working_revision": working_revision}


def recover(
    root: Path, journal: Path, *, require_idle, autosave_path=None, on_completed=None
) -> str:
    """Apply a prepared layout after the verified source replacement."""
    pending = journal.read_bytes()
    record = json.loads(pending)
    script = scriptbackup.resolve(root, record["script"])
    if journal != _journal_for(root, record["script"]):
        raise Conflict("Invalid source style change journal.")
    layout = layoutannotate.layout_target(root, record["layout"])
    data = atomicio.dumps_json(record["document"], indent=1)
    if atomicio.revision_of(data) != record["layout_after"]:
        raise Conflict("The pending layout could not be verified.")
    documents.loads_document(data)
    with scriptlock.script_guard(script):
        require_idle(script, record["script"])
        if not journal.exists():
            return "closed"
        if journal.read_bytes() != pending:
            raise Conflict("A newer source style change is pending.")
        with doclock.document_lock(layout):
            actual = scriptbackup.sha256(script.read_bytes())
            revision = _layout_revision(layout)
            if actual == record["script_before"] and revision == record["layout_before"]:
                _discard(journal)
                return "untouched"
            if actual != record["script_after"] or revision not in (
                record["layout_before"],
                record["layout_after"],
            ):
                raise Conflict("The source or its saved layout was edited after the interruption.")
            if revision == record["layout_before"]:
                layoutdir.write(layout.parent, layout.stem, data)
        _finish_working(root, record, autosave_path)
        _discard(journal)
        if on_completed is not None:
            on_completed(record)
        return "completed"
