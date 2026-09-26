"""⌘S 写回项目里的排版文件（ADR 0096）：`POST /api/layouts/<名>?target=project`。

这条路与「另存为」是同一个端点、同一份冲突判据（`_revision_conflict`）、同一个
原子写（`atomicio`）。这里钉住的是 ⌘S 这一档**多出来**的四条：

1. 落点只能是**当前项目**的 `tavottofile/`——没打开项目不许退回数据目录；
2. 外部修改（git pull、另一台电脑）用内容修订号挡，被拒的那次一个字节都不写；
3. 符号链接逃逸（`tavottofile/` 或目标文件本身是链接）一律拒；
4. 只读的项目文件夹给一个说得清的 code，不是一句可重试的 500。
"""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents

PD = {
    "schema": 3,
    "project": {"id": "p", "name": "排版一"},
    "canvases": [
        {"id": "c1", "name": "Figure 1", "page": {"w": 10, "h": 10}, "objects": [], "guides": []}
    ],
    "activeCanvasId": "c1",
    "createdAt": 0,
    "updatedAt": 1,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path / "data_layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / "data_layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "userdata"))
    m.app.config["TESTING"] = True
    project = tmp_path / "proj"
    project.mkdir()
    yield m.app.test_client(), project, tmp_path
    m.reset_projects()


def _open(project: Path) -> str:
    return m.open_project(str(project))["id"]


def _save(client, name, pj=None, doc=None, **params):
    qs = "&".join(f"{k}={v}" for k, v in {"target": "project", **params}.items())
    headers = {"X-Tavotto-Project": pj} if pj else {}
    return client.post(f"/api/layouts/{name}?{qs}", json=doc or PD, headers=headers)


def test_bound_save_writes_into_the_project_and_reports_where(env):
    client, project, _ = env
    pj = _open(project)
    first = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT)
    assert first.status_code == 200, first.get_json()
    body = first.get_json()
    target = project / "tavottofile" / "排版一.json"
    assert body["file"] == "tavottofile/排版一.json"
    assert body["name"] == "排版一"
    assert body["revision"] == atomicio.content_revision(target)

    # 第二次 ⌘S：带上次写成的修订号，写回同一个文件，内容更新
    second = _save(client, "排版一", pj, doc={**PD, "updatedAt": 2}, base_revision=body["revision"])
    assert second.status_code == 200, second.get_json()
    assert json.loads(target.read_text(encoding="utf-8"))["updatedAt"] == 2
    assert second.get_json()["revision"] == atomicio.content_revision(target)
    assert [p.name for p in target.parent.iterdir() if p.is_file()] == ["排版一.json"], (
        "留下了临时文件"
    )


def test_project_target_never_falls_back_to_the_data_dir(env):
    """没打开项目时 `project_layout_dir()` 会退回数据目录——⌘S 那一档不许。"""
    client, _, tmp_path = env
    r = _save(client, "排版一", base_revision=m.REVISION_ABSENT)
    assert r.status_code == 409
    assert r.get_json()["code"] == "no_project"
    assert not (tmp_path / "data_layouts").exists(), "没有项目时写进了数据目录"


def test_external_change_is_refused_and_leaves_the_file_alone(env):
    """git pull / 另一台电脑改过项目里那份：带旧修订号的 ⌘S 必须 409，磁盘零改动。"""
    client, project, _ = env
    pj = _open(project)
    base = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT).get_json()["revision"]
    target = project / "tavottofile" / "排版一.json"
    theirs = json.dumps({**PD, "updatedAt": 99}).encode("utf-8")
    target.write_bytes(theirs)

    r = _save(client, "排版一", pj, doc={**PD, "updatedAt": 3}, base_revision=base)
    assert r.status_code == 409
    got = r.get_json()
    assert got["code"] == "external_change"
    assert got["revision"] == atomicio.revision_of(theirs)
    assert target.read_bytes() == theirs, "被拒的那次写动了项目里的文件"


def test_a_name_with_path_segments_stays_inside_tavottofile(env):
    client, project, tmp_path = env
    pj = _open(project)
    r = _save(client, "..%5C..%5Cescape", pj, base_revision=m.REVISION_ABSENT)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["file"].startswith("tavottofile/")
    assert "/" not in r.get_json()["file"].removeprefix("tavottofile/")
    assert not (tmp_path / "escape.json").exists()
    written = [p for p in (project / "tavottofile").iterdir() if p.is_file()]
    assert [p.name for p in written] == [r.get_json()["file"].removeprefix("tavottofile/")]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_symlinked_store_dir_pointing_outside_is_refused(env):
    client, project, tmp_path = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "tavottofile").symlink_to(outside, target_is_directory=True)
    pj = _open(project)
    r = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT)
    assert r.status_code == 400
    assert r.get_json()["code"] == "layout_outside_project"
    assert list(outside.glob("*.json")) == [], "跟着符号链接写到了项目外"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_symlinked_target_file_is_refused_and_not_replaced(env):
    client, project, tmp_path = env
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(PD), encoding="utf-8")
    store = project / "tavottofile"
    store.mkdir()
    (store / "排版一.json").symlink_to(outside)
    pj = _open(project)
    r = _save(client, "排版一", pj, base_revision=atomicio.content_revision(outside))
    assert r.status_code == 400
    assert r.get_json()["code"] == "layout_outside_project"
    assert (store / "排版一.json").is_symlink(), "用户摆好的链接被换成了普通文件"


def test_read_only_failure_has_its_own_code(env, monkeypatch):
    """只读卷（EROFS）：原子写抛出来的是 `write_failed`/500——用户读到的是一句
    「可重试」，而重试一百次也一样。项目这一档要说清是只读。"""
    client, project, _ = env
    pj = _open(project)

    def refuse(path, obj, indent=None):
        raise atomicio.AtomicWriteError("write_failed", "ro", Path(path)) from OSError(
            errno.EROFS, "Read-only file system"
        )

    monkeypatch.setattr(m.engine_atomicio, "write_json", refuse)
    r = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT)
    assert r.status_code == 403
    assert r.get_json()["code"] == "layout_read_only"


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX 权限位；root 无视权限位",
)
def test_real_read_only_store_dir_is_reported_and_untouched(env):
    client, project, _ = env
    store = project / "tavottofile"
    store.mkdir()
    pj = _open(project)
    store.chmod(0o555)
    try:
        r = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT)
        assert r.status_code == 403, r.get_json()
        assert r.get_json()["code"] == "layout_read_only"
        assert list(store.glob("*.json*")) == []
    finally:
        store.chmod(0o755)


def test_other_write_failures_stay_generic(env, monkeypatch):
    """反向那条边：磁盘满不是「只读」，照旧走 atomicio 的通用映射（500）。"""
    client, project, _ = env
    pj = _open(project)

    def full(path, obj, indent=None):
        raise atomicio.AtomicWriteError("write_failed", "full", Path(path)) from OSError(
            errno.ENOSPC, "No space left on device"
        )

    monkeypatch.setattr(m.engine_atomicio, "write_json", full)
    r = _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT)
    assert r.status_code == 500
    assert r.get_json()["code"] == "write_failed"


def test_bound_save_goes_through_atomicio_and_the_one_conflict_predicate(env, monkeypatch):
    """单一权威：⌘S 这一档不许有第二份原子写 / 第二份冲突判据。"""
    client, project, _ = env
    pj = _open(project)
    seen: dict[str, int] = {"write": 0, "conflict": 0}
    real_write, real_conflict = m.engine_atomicio.write_json, m._revision_conflict

    def write(*a, **k):
        seen["write"] += 1
        return real_write(*a, **k)

    def conflict(*a):
        seen["conflict"] += 1
        return real_conflict(*a)

    monkeypatch.setattr(m.engine_atomicio, "write_json", write)
    monkeypatch.setattr(m, "_revision_conflict", conflict)
    assert _save(client, "排版一", pj, base_revision=m.REVISION_ABSENT).status_code == 200
    assert seen == {"write": 1, "conflict": 1}


def test_get_tells_where_a_bound_save_will_land(env):
    """从「项目里的排版」打开时，界面要知道 ⌘S 会写到哪——后端按 `project_layout_dir()`
    算好交出去（百分号编码），前端不自己拼。从旧位置读出来的也指向 `tavottofile/`。"""
    from urllib.parse import unquote

    client, project, _ = env
    legacy = project / "canvases"
    legacy.mkdir()
    (legacy / "旧排版.json").write_text(json.dumps(PD), encoding="utf-8")
    pj = _open(project)
    r = client.get("/api/layouts/旧排版", headers={"X-Tavotto-Project": pj})
    assert r.status_code == 200
    assert unquote(r.headers["X-Tavotto-Layout-File"]) == "tavottofile/旧排版.json"


def test_get_without_a_project_sends_no_layout_file_header(env):
    client, _, tmp_path = env
    (tmp_path / "data_layouts").mkdir()
    (tmp_path / "data_layouts" / "本机.json").write_text(json.dumps(PD), encoding="utf-8")
    r = client.get("/api/layouts/本机")
    assert r.status_code == 200
    assert "X-Tavotto-Layout-File" not in r.headers
