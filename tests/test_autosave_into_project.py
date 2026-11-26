"""自动保存写进项目（ADR 0211 第二期 PR 2）：`PUT /api/autosave/<id>?project_write=…` 在开关后面把工作副本写进
`tavottofile/<名>.tavotto/layout.json`；本机槽位始终先写、写失败时仍在。开关关着时与改造前逐字相同。"""

from __future__ import annotations

import errno
import json
import sys
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents, layoutdir

OBJ = {"id": "r1", "type": "rect", "x": 0, "y": 0, "w": 5, "h": 5}


def _doc(name="排版一", objects=(OBJ,), updated=1):
    return {
        "schema": 3,
        "project": {"id": "p", "name": name},
        "canvases": [
            {
                "id": "c1",
                "name": "Figure 1",
                "page": {"w": 10, "h": 10},
                "objects": list(objects),
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": updated,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    data = tmp_path / "data"
    monkeypatch.setattr(m, "LAYOUT_DIR", data / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", data / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "VERSIONS_DIR", data / "layouts" / "_versions")
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    m.app.config["TESTING"] = True
    project = tmp_path / "proj"
    project.mkdir()
    pj = m.open_project(str(project))["id"]
    yield m.app.test_client(), project, {"X-Tavotto-Project": pj}
    m.reset_projects()


@pytest.fixture
def writer_on(monkeypatch):
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", True)


def _put(client, h, doc_id, doc=None, spec=None, **extra):
    qs = dict(extra)
    if spec is not None:
        qs["project_write"] = json.dumps(spec)
    return client.put(f"/api/autosave/{doc_id}", query_string=qs, json=doc or _doc(), headers=h)


def _slot(doc_id):
    return m._autosave_path(doc_id)


def test_writer_off_ignores_project_write_and_matches_old_response(env):
    client, project, h = env
    r = _put(client, h, "d1", spec={})
    assert r.status_code == 200
    assert "project_layout" not in r.get_json()
    assert _slot("d1").is_file()
    assert [p.name for p in (project / "tavottofile").iterdir() if p.name != "export"] == []
    assert client.get("/api/project", headers=h).get_json().get("autosave_into_project") is False


def test_writer_on_status_advertises_it(env, writer_on):
    client, _p, h = env
    assert client.get("/api/project", headers=h).get_json()["autosave_into_project"] is True


def test_without_project_write_nothing_is_written_even_with_writer_on(env, writer_on):
    client, project, h = env
    r = _put(client, h, "d1")
    assert r.status_code == 200
    assert "project_layout" not in r.get_json()
    assert not list((project / "tavottofile").glob("*.tavotto"))


def test_first_write_names_the_layout_after_the_document_and_records_doc_id(env, writer_on):
    client, project, h = env
    r = _put(client, h, "d1", spec={})
    out = r.get_json()["project_layout"]
    assert out["state"] == "saved" and out["name"] == "排版一"
    assert out["file"] == "tavottofile/排版一.json"
    d = project / "tavottofile" / "排版一.tavotto"
    assert out["revision"] == atomicio.revision_of((d / "layout.json").read_bytes())
    assert json.loads((d / "package_manifest.json").read_text(encoding="utf-8"))["doc_id"] == "d1"
    assert _slot("d1").is_file(), "本机槽位是崩溃缓冲，先写、不能少"
    # 另一份同名文档：重名加序号
    r2 = _put(client, h, "d2", doc=_doc(), spec={})
    assert r2.get_json()["project_layout"]["name"] == "排版一_2"


def test_followup_write_uses_the_binding_and_updates_in_place(env, writer_on):
    client, project, h = env
    first = _put(client, h, "d1", spec={}).get_json()["project_layout"]
    r = _put(
        client,
        h,
        "d1",
        doc=_doc(updated=2),
        spec={"name": first["name"], "base": first["revision"]},
        base_revision=m.engine_atomicio.existing_revision(_slot("d1")),
    )
    out = r.get_json()["project_layout"]
    assert out["state"] == "saved" and out["name"] == first["name"]
    layout = project / "tavottofile" / "排版一.tavotto" / "layout.json"
    assert json.loads(layout.read_bytes())["updatedAt"] == 2
    assert sorted(p.name for p in (project / "tavottofile").glob("*.tavotto")) == ["排版一.tavotto"]


def test_emptying_an_existing_working_copy_is_written_and_not_resurrected(env, writer_on):
    """Codex #2277 P1：「空白不写」只管**新建**。删光最后一个对象后，已有的工作副本必须收到空文档，
    否则 layout.json 停在删除之前，换电脑 / 挪走重开会把删掉的对象找回来。"""
    client, project, h = env
    first = _put(client, h, "d1", spec={}).get_json()["project_layout"]
    layout = project / "tavottofile" / "排版一.tavotto" / "layout.json"
    assert json.loads(layout.read_bytes())["canvases"][0]["objects"]
    spec = {"name": first["name"], "base": first["revision"]}
    out = _put(client, h, "d1", doc=_doc(objects=(), updated=5), spec=spec).get_json()[
        "project_layout"
    ]
    assert out["state"] == "saved" and out["revision"] != first["revision"]
    assert json.loads(layout.read_bytes())["canvases"][0]["objects"] == []
    # 全新的本机（槽位与会话状态都没了）：候选就是这份空的工作副本，不会退回删除之前的内容
    _slot("d1").unlink()
    items = client.get("/api/layout-session/project-history", headers=h).get_json()["items"]
    mine = [i for i in items if i["doc_id"] == "d1"]
    assert len(mine) == 1 and mine[0]["objects"] == 0
    assert mine[0]["work"]["revision"] == out["revision"]


def test_empty_document_creates_nothing(env, writer_on):
    client, project, h = env
    out = _put(client, h, "d1", doc=_doc(objects=()), spec={}).get_json()["project_layout"]
    assert out == {"state": "skipped"}
    assert not list((project / "tavottofile").glob("*.tavotto"))
    assert _slot("d1").is_file()


def test_external_change_is_not_overwritten_and_slot_keeps_our_version(env, writer_on):
    client, project, h = env
    first = _put(client, h, "d1", spec={}).get_json()["project_layout"]
    layout = project / "tavottofile" / "排版一.tavotto" / "layout.json"
    theirs = atomicio.dumps_json(_doc(name="git pull 来的", updated=50), indent=1)
    layout.write_bytes(theirs)
    mine = _doc(updated=3)
    r = _put(client, h, "d1", doc=mine, spec={"name": first["name"], "base": first["revision"]})
    out = r.get_json()["project_layout"]
    assert out["state"] == "external_change" and out["revision"] == atomicio.revision_of(theirs)
    assert out["summary"]["schema"] == 3
    assert layout.read_bytes() == theirs, "覆盖了外部修改"
    assert json.loads(_slot("d1").read_bytes())["updatedAt"] == 3, "本机缓冲保不住这边的版本"
    # 带上外部那份的修订号再写（用户在冲突岔口选了覆盖之后）：通过
    again = _put(client, h, "d1", doc=mine, spec={"name": first["name"], "base": out["revision"]})
    assert again.get_json()["project_layout"]["state"] == "saved"


def test_lost_binding_is_recognised_by_manifest_doc_id_only_while_the_hint_matches(env, writer_on):
    client, project, h = env
    first = _put(client, h, "d1", spec={}).get_json()["project_layout"]
    # 绑定没了（不带 name / base）：按清单 docId 找回同一个目录，提示修订号对得上 → 写进去，不另起一份
    r = _put(client, h, "d1", doc=_doc(updated=2), spec={}).get_json()["project_layout"]
    assert r["state"] == "saved" and r["name"] == first["name"]
    # 目录被外部改过（提示对不上）→ 不覆盖
    layout = project / "tavottofile" / "排版一.tavotto" / "layout.json"
    layout.write_bytes(atomicio.dumps_json(_doc(updated=99), indent=1))
    r = _put(client, h, "d1", doc=_doc(updated=3), spec={}).get_json()["project_layout"]
    assert r["state"] == "external_change"
    assert json.loads(layout.read_bytes())["updatedAt"] == 99


def test_read_only_failure_is_reported_and_the_local_slot_survives(env, writer_on, monkeypatch):
    client, _p, h = env

    def refuse(*a, **k):
        raise atomicio.AtomicWriteError("replace_failed", "ro", Path("x")) from OSError(
            errno.EROFS, "ro"
        )

    monkeypatch.setattr(layoutdir, "write", refuse)
    r = _put(client, h, "d1", spec={})
    assert r.status_code == 200
    assert r.get_json()["project_layout"] == {"state": "read_only"}
    assert _slot("d1").is_file(), "写进项目失败时本机那份必须还在"


def test_unwritable_project_folder_is_read_only_without_touching_it(env, writer_on, monkeypatch):
    client, project, h = env
    monkeypatch.setattr(m.os, "access", lambda *a, **k: False)
    assert _put(client, h, "d1", spec={}).get_json()["project_layout"] == {"state": "read_only"}
    assert not list((project / "tavottofile").glob("*.tavotto"))


def test_windows_transient_denial_is_retried_and_never_judged_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(layoutdir.sys, "platform", "win32")
    calls = []
    sleeps = []

    def flaky(store, stem, data, *, doc_id=None):
        calls.append(1)
        if len(calls) < 3:
            raise atomicio.AtomicWriteError(
                "replace_failed", "denied", Path("x")
            ) from PermissionError(errno.EACCES, "denied")
        return "rev"

    monkeypatch.setattr(layoutdir, "write", flaky)
    assert layoutdir.write_retrying(tmp_path, "a", {}, sleep=sleeps.append) == "rev"
    assert sleeps == list(layoutdir.RETRY_DELAYS)
    exc = atomicio.AtomicWriteError("replace_failed", "d", Path("x"))
    exc.__cause__ = PermissionError(errno.EACCES, "d")
    assert layoutdir.classify_write_failure(exc, platform="win32") == "transient"
    assert layoutdir.classify_write_failure(exc, platform="linux") == "read_only"
    assert layoutdir.classify_write_failure(exc, platform="darwin") == "read_only"
    ro = atomicio.AtomicWriteError("replace_failed", "d", Path("x"))
    ro.__cause__ = OSError(errno.EROFS, "ro")
    assert layoutdir.classify_write_failure(ro, platform="win32") == "read_only"


def test_persistent_transient_failure_reports_busy_not_read_only(env, writer_on, monkeypatch):
    client, _p, h = env
    monkeypatch.setattr(layoutdir.sys, "platform", "win32")
    monkeypatch.setattr(layoutdir, "RETRY_DELAYS", (0, 0))

    def denied(*a, **k):
        raise atomicio.AtomicWriteError("replace_failed", "d", Path("x")) from PermissionError(
            errno.EACCES, "d"
        )

    monkeypatch.setattr(layoutdir, "write", denied)
    r = _put(client, h, "d1", spec={})
    assert r.get_json()["project_layout"] == {"state": "busy"}
    assert _slot("d1").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要权限")
def test_symlinked_layout_directory_is_refused(env, writer_on, tmp_path):
    client, project, h = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "tavottofile" / "排版一.tavotto").symlink_to(outside, target_is_directory=True)
    r = _put(client, h, "d1", spec={"name": "排版一", "base": None})
    assert r.get_json()["project_layout"]["state"] == "refused"
    assert list(outside.iterdir()) == []


def test_history_lists_the_working_copy_when_the_local_slot_is_gone(env, writer_on):
    client, project, h = env
    first = _put(client, h, "d1", spec={}).get_json()["project_layout"]
    # 挪走 / 换电脑：本机槽位与会话状态都没了，只剩项目里的目录
    _slot("d1").unlink()
    items = client.get("/api/layout-session/project-history", headers=h).get_json()["items"]
    mine = [i for i in items if i["doc_id"] == "d1"]
    assert len(mine) == 1
    work = mine[0]["work"]
    assert work["name"] == first["name"] and work["revision"] == first["revision"]
    assert mine[0]["mode"] in ("node", "copy") and mine[0]["objects"] == 1


def test_history_has_no_working_copy_without_a_directory_layout(env):
    client, _p, h = env
    _put(client, h, "d1")
    items = client.get("/api/layout-session/project-history", headers=h).get_json()["items"]
    assert all("work" not in i for i in items)


@pytest.mark.skipif(
    sys.platform == "win32" or __import__("os").geteuid() == 0, reason="需要 POSIX 权限位且非 root"
)
def test_real_read_only_store_directory_is_read_only_and_slot_survives(env, writer_on):
    """真文件系统：`tavottofile/` 没有写权限时，暂存目录 `mkdir` 直接抛 PermissionError（不是 AtomicWriteError）——
    也要判成 read_only，而不是笼统的 failed（界面那行灰字靠它）。"""
    client, project, h = env
    store = project / "tavottofile"
    store.chmod(0o555)
    try:
        r = _put(client, h, "d1", spec={})
    finally:
        store.chmod(0o755)
    assert r.get_json()["project_layout"] == {"state": "read_only"}
    assert _slot("d1").is_file()
    assert not list(store.glob("*.tavotto"))
