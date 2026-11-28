"""项目文件绑定通过统一 HTTP 路由核对槽位、命名文件与操作顺序。"""

import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, pool, project_watch
from test_chart_native_api import SOURCE, _bind


def _axis(editor="editor_A", sequence=1):
    return {"editor_id": editor, "sequence": sequence}


def _document(title="A", *, schema=3, updated=1):
    return {
        "schema": schema,
        "project": {"id": "same_project_label", "name": title},
        "canvases": [{"id": "c1", "name": "Figure 1", "page": {"w": 100, "h": 80}, "objects": []}],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": updated,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    data = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    monkeypatch.setattr(m, "LAYOUT_DIR", data / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", data / "layouts/_autosave")
    monkeypatch.setattr(m, "VERSIONS_DIR", data / "layouts/_versions")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setitem(m.app.config, "TESTING", True)
    root = tmp_path / "project"
    root.mkdir()
    project_id = m.open_project(str(root))["id"]
    try:
        yield m.app.test_client(), root, {"X-Tavotto-Project": project_id}
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects()
        project_watch.stop()


def _read(env, doc_id="working"):
    client, _root, headers = env
    response = client.get(f"/api/layout-session/project-file/{doc_id}", headers=headers)
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    return response.get_json()


def _put(env, document, axis, base_axis, base_revision, *, doc_id="working"):
    client, _root, headers = env
    return client.put(
        f"/api/autosave/{doc_id}",
        query_string={
            "base_revision": base_revision or "absent",
            "project_file_edit": json.dumps(
                {"axis": axis, "base_axis": base_axis, "base_revision": base_revision}
            ),
        },
        json=document,
        headers=headers,
    )


def _saved(response):
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["ok"] is True
    return result


def _reserve(env, name="A", *, doc_id="working", expected=None):
    current = _read(env, doc_id)
    client, _root, headers = env
    response = client.post(
        f"/api/layout-session/project-file/{doc_id}/reserve",
        json={
            "name": name,
            "autosave_revision": current["edit"]["autosave_revision"],
            "axis": current["edit"]["axis"],
            "expected_binding_sequence": expected,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["state"] == "reserved", result
    return result["ticket"]


def _named(env, document, name="A", *, base="absent", ticket=None, doc_id="working"):
    client, _root, headers = env
    query = {"target": "project", "base_revision": base}
    if ticket is not None:
        query.update(working_doc_id=doc_id, binding_intent=str(ticket["sequence"]))
    return client.post(
        f"/api/layouts/{quote(name)}", query_string=query, json=document, headers=headers
    )


def _confirm(env, name, ticket, revision, *, doc_id="working"):
    client, _root, headers = env
    return client.post(
        f"/api/layout-session/project-file/{doc_id}/confirm",
        json={"name": name, "intent_sequence": ticket["sequence"], "revision": revision},
        headers=headers,
    )


def _binding(env, doc_id="working"):
    return _read(env, doc_id)["binding"]


def _bound(env):
    document = _document()
    revision = _saved(_put(env, document, _axis(), None, None))["revision"]
    ticket = _reserve(env)
    named = _saved(_named(env, document, ticket=ticket))
    assert named["project_file"]["state"] == "bound", named
    return document, revision, named


@pytest.mark.parametrize("cancel_b", [False, True])
def test_late_named_A_receipt_does_not_bind_over_newer_B_intent(env, cancel_b):
    document, _revision, original = _bound(env)
    before = _binding(env)
    a = _reserve(env, "A", expected=before["sequence"])
    b = _reserve(env, "B", expected=before["sequence"])
    # A 的正文确实成功；元信息只能报告过期，不能抢回更晚 B 意图。
    late_a = _saved(_named(env, document, base=original["revision"], ticket=a))
    assert late_a["project_file"]["state"] == "superseded"
    assert _binding(env) == before
    if cancel_b:
        client, _root, headers = env
        cancelled = client.delete(
            f"/api/layout-session/project-file/working/intent/{b['sequence']}", headers=headers
        )
        assert cancelled.status_code == 200, cancelled.get_json()
        assert cancelled.get_json()["state"] == "cancelled"
        assert _binding(env) == before
    else:
        saved_b = _saved(_named(env, document, "B", ticket=b))
        assert saved_b["project_file"]["state"] == "bound"
        assert _binding(env)["name"] == "B"
    final = _binding(env)
    again = _saved(_named(env, document, base=late_a["revision"], ticket=a))
    assert again["project_file"]["state"] == "superseded"
    assert _binding(env) == final


def test_open_confirm_verifies_actual_file_and_ABA_ticket_cannot_rebind(env):
    document, _revision, original = _bound(env)
    old_a = _reserve(env, "A", expected=1)
    b = _reserve(env, "B", expected=1)
    b_file = _saved(_named(env, document, "B"))
    confirmed = _confirm(env, "B", b, b_file["revision"])
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()["state"] == "bound"
    new_a = _reserve(env, "A", expected=b["sequence"])
    confirmed_a = _confirm(env, "A", new_a, original["revision"])
    assert confirmed_a.status_code == 200, confirmed_a.get_json()
    assert confirmed_a.get_json()["state"] == "bound"
    before = _binding(env)
    late = _confirm(env, "A", old_a, original["revision"])
    assert late.status_code == 200, late.get_json()
    assert late.get_json()["state"] == "superseded"
    assert _binding(env) == before and before["sequence"] == new_a["sequence"]

    valid = _reserve(env, "A", expected=before["sequence"])
    target = env[1] / "tavottofile/A.json"
    target.write_bytes(atomicio.dumps_json(_document("external", updated=100)))
    changed = _confirm(env, "A", valid, original["revision"])
    assert _binding(env) == before
    assert changed.status_code == 409, changed.get_json()
    assert changed.get_json()["code"] == "external_change"


@pytest.mark.parametrize("location", ["canvases", "data", "tavottofile"])
def test_open_confirm_uses_read_source_then_first_save_uses_project_target(env, location):
    client, root, headers = env
    directory = m.LAYOUT_DIR if location == "data" else root / location
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "A.json"
    original = atomicio.dumps_json(_document())
    source.write_bytes(original)
    target = root / "tavottofile/A.json"

    opened = client.get("/api/layouts/A", headers=headers)
    assert opened.status_code == 200, opened.get_json()
    assert opened.data == original
    assert opened.headers["X-Tavotto-Layout-File"] == "tavottofile/A.json"
    revision = opened.headers["X-Tavotto-Revision"]
    autosave = _saved(_put(env, opened.get_json(), _axis(), None, None))
    ticket = _reserve(env)
    confirmed = _confirm(env, "A", ticket, revision)
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()["state"] == "bound"
    binding = _binding(env)
    assert binding["file"] == "tavottofile/A.json"
    assert binding["revision"] == revision == atomicio.revision_of(original)
    assert binding["dirty"] is False
    assert source.read_bytes() == original
    assert target.exists() is (location == "tavottofile")

    # 首写只写规范位置；旧来源保留，之后读取 / 确认应优先采用新文件。
    edited = _document("edited", updated=2)
    _saved(_put(env, edited, _axis(sequence=2), _axis(), autosave["revision"]))
    save_ticket = _reserve(env, expected=binding["sequence"])
    saved = _saved(_named(env, edited, base=revision, ticket=save_ticket))
    assert saved["project_file"]["state"] == "bound"
    assert saved["file"] == "tavottofile/A.json"
    assert saved["revision"] == atomicio.content_revision(target) != revision
    if source != target:
        assert source.read_bytes() == original
    reopened = client.get("/api/layouts/A", headers=headers)
    assert reopened.status_code == 200, reopened.get_json()
    assert reopened.get_json() == edited
    assert reopened.headers["X-Tavotto-Revision"] == saved["revision"]
    reopen_ticket = _reserve(env, expected=save_ticket["sequence"])
    reconfirmed = _confirm(env, "A", reopen_ticket, reopened.headers["X-Tavotto-Revision"])
    assert reconfirmed.status_code == 200, reconfirmed.get_json()
    assert reconfirmed.get_json()["state"] == "bound"
    assert _binding(env)["revision"] == saved["revision"]
    assert _binding(env)["dirty"] is False


@pytest.mark.parametrize("location", ["canvases", "data", "tavottofile"])
def test_open_confirm_rejects_external_change_to_the_read_source(env, location):
    client, root, headers = env
    directory = m.LAYOUT_DIR if location == "data" else root / location
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "A.json"
    source.write_bytes(atomicio.dumps_json(_document()))
    opened = client.get("/api/layouts/A", headers=headers)
    assert opened.status_code == 200, opened.get_json()
    _saved(_put(env, opened.get_json(), _axis(), None, None))
    ticket = _reserve(env)
    external = atomicio.dumps_json(_document("external", updated=100))
    source.write_bytes(external)

    changed = _confirm(env, "A", ticket, opened.headers["X-Tavotto-Revision"])
    assert changed.status_code == 409, changed.get_json()
    assert changed.get_json()["code"] == "external_change"
    assert changed.get_json()["revision"] == atomicio.revision_of(external)
    assert _binding(env) is None
    assert source.read_bytes() == external


@pytest.mark.parametrize(
    ("location", "link_kind"),
    [
        ("canvases", "directory"),
        ("data", "directory"),
        ("canvases", "file"),
        ("data", "file"),
        ("canvases", "different-stripe"),
    ],
)
def test_open_confirm_ignores_unsafe_fallback_links(
    env, tmp_path, monkeypatch, location, link_kind
):
    client, root, headers = env
    directory = m.LAYOUT_DIR if location == "data" else root / location
    # 模拟后备目录在 reserve 后被换成链接，自动保存槽位仍是独立的普通目录。
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / "slots" / "_autosave")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "A.json"
    source.write_bytes(atomicio.dumps_json(_document()))
    opened = client.get("/api/layouts/A", headers=headers)
    assert opened.status_code == 200, opened.get_json()
    _saved(_put(env, opened.get_json(), _axis(), None, None))
    ticket = _reserve(env)

    destination = (root if link_kind == "different-stripe" else tmp_path) / "other"
    destination.mkdir()
    linked_file = destination / ("A.json" if link_kind == "directory" else "other.json")
    linked_file.write_bytes(atomicio.dumps_json(_document("other", updated=100)))
    source.unlink()
    if link_kind == "directory":
        directory.rmdir()
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(destination), str(directory))
        else:
            directory.symlink_to(destination, target_is_directory=True)
    else:
        try:
            source.symlink_to(linked_file)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("file symlinks require Windows privileges")
            raise
    linked_file = linked_file.resolve(strict=True)
    assert source.resolve(strict=True) == linked_file
    if link_kind == "different-stripe":
        assert m._lock_stripe(source) != m._lock_stripe(linked_file)

    reads = []
    read_bytes = Path.read_bytes

    def record_reads(path):
        if path.resolve() == linked_file:
            reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", record_reads)
    confirmed = _confirm(env, "A", ticket, opened.headers["X-Tavotto-Revision"])
    assert confirmed.status_code == 409, confirmed.get_json()
    assert confirmed.get_json()["code"] == "external_change"
    assert reads == []
    assert confirmed.get_json()["revision"] is None
    assert confirmed.get_json()["summary"] is None
    assert _binding(env) is None
    assert client.get("/api/layouts/A", headers=headers).status_code == 404
    listing = client.get("/api/layouts", headers=headers)
    assert listing.status_code == 200, listing.get_json()
    assert listing.get_json()["layouts"] == []


def test_external_autosave_drift_is_unknown_and_old_axis_cannot_write_before_body(env):
    _, revision, named = _bound(env)
    saved = _read(env)
    assert saved["edit"]["known"] is True
    assert saved["edit"]["autosave_revision"] == revision
    assert saved["binding"]["dirty"] is False
    path = m.AUTOSAVE_DIR / "working.json"
    external = atomicio.dumps_json(_document("external", updated=99))
    path.write_bytes(external)
    current = _read(env)
    assert current["edit"]["known"] is False
    assert current["binding"]["dirty"] is True
    assert current["binding"]["revision"] == named["revision"]
    client, _, headers = env
    stale = client.post(
        "/api/layout-session/project-file/working/reserve",
        json={
            "name": "B",
            "axis": _axis(),
            "autosave_revision": revision,
            "expected_binding_sequence": current["binding"]["sequence"],
        },
        headers=headers,
    )
    assert stale.status_code == 200
    assert stale.get_json()["state"] == "unconfirmed"
    assert _read(env)["intent_sequence"] == current["intent_sequence"]
    response = _put(
        env,
        _document("old editor", updated=100),
        _axis(sequence=2),
        _axis(),
        atomicio.revision_of(external),
    )
    assert path.read_bytes() == external
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "stale_write"
    assert response.get_json()["project_file"]["state"] == "unconfirmed"
    assert _binding(env)["revision"] == named["revision"]


def test_schema_upgrade_updates_the_existing_binding(env):
    _, old_revision, named = _bound(env)
    before = _binding(env)
    client, root, _ = env
    (root / "chart.svg").write_bytes(SOURCE)
    four = _document(schema=4)
    four["canvases"][0]["objects"].append(
        {
            "id": "chart",
            "type": "panel",
            "fileId": "chart.svg",
            "fileKind": "svg",
            "chart": _bind(client),
        }
    )
    changed = _saved(_put(env, four, _axis(sequence=2), _axis(), old_revision))
    current = _read(env)
    assert current["edit"]["autosave_revision"] == changed["revision"]
    assert current["binding"] == {**before, "dirty": True}
    ticket = _reserve(env, expected=before["sequence"])
    result = _saved(_named(env, four, base=named["revision"], ticket=ticket))
    assert result["project_file"]["state"] == "bound"
    assert _binding(env)["revision"] == result["revision"]
    assert json.loads((env[1] / before["file"]).read_bytes())["schema"] == 4


@pytest.fixture(autouse=True)
def _legacy_layout_file_form(monkeypatch):
    """ADR 0211 第二期 PR 3 起「排版写成目录形态」默认开；这份用例钉的是改造前 `tavottofile/<名>.json` 的形态与语义，
    显式关开关来跑（与 `TAVOTTO_LAYOUT_DIR_WRITER=0` 应急回退同一档；环境变量也让子进程一并关）。目录形态的对应用例在 `tests/test_layoutdir.py`。"""
    from tavotto.engine import layoutdir

    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    monkeypatch.setenv(layoutdir.WRITER_ENV, "0")
