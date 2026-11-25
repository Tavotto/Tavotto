"""ADR 0211 第一期 1–4：项目自己的时间线作为恢复证据（后端合同）。

真实 Flask test client + 隔离数据目录；项目是 `tmp_path` 下的真目录，「挪走」= 真的 `rename`，
「复制」= 真的 `copytree`。

* 挪走（旧路径不在）：候选 `mode=slot owner=moved`，`claim` 把槽位归属改到新项目，之后 `/owner`
  与 `PUT last` 都认它；
* 复制（旧路径还在）：`mode=copy owner=other`，`claim` 拒绝，旧项目的归属与正文一个字节不动；
* 没有本机槽位、也没有归属记录（换电脑 / 换数据目录）：`mode=node`；
* 只有空节点的 `.vx` 不进候选；空白排版的 `open` 不再建 `.vx`；
* 只读：列候选不写会话状态、不建目录。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tavotto import app as m, security
from tavotto.engine import documents, layoutsession, project_watch as engine_watch

TEXT = {
    "id": "t1",
    "type": "text",
    "text": "hello",
    "x": 1,
    "y": 2,
    "w": 10,
    "h": 5,
    "sizePt": 9,
    "bold": False,
    "color": "#000",
    "align": "left",
}


def _canvas_doc(objects: list | None = None) -> dict:
    return {
        "schema": 2,
        "name": "Fig 1",
        "page": {"w": 150, "h": 100},
        "objects": list(objects or []),
        "guides": [],
    }


def _project_doc(objects: list | None = None, updated: int = 1) -> dict:
    return {
        "schema": 3,
        "project": {"id": "p", "name": "排版一"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": list(objects or []),
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": updated,
    }


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setattr(m, "LAYOUT_DIR", d / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    monkeypatch.setattr(m, "VERSIONS_DIR", d / "layouts" / "_versions")
    return d


@pytest.fixture
def client(data_dir):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _open(client, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    body = client.post("/api/projects/open", json={"path": str(root)}).get_json()
    assert body.get("id"), body
    return body


def _h(pj: dict) -> dict:
    return {"X-Tavotto-Project": pj["id"]}


def _history(client, pj: dict) -> list[dict]:
    r = client.get("/api/layout-session/project-history", headers=_h(pj))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["items"]


def _edit_in(client, pj: dict, doc_id: str, *, node: bool = True, updated: int = 1) -> str:
    """在项目 `pj` 里编辑排版 `doc_id`：自动保存一份有内容的正文，再打一个时间线节点。回槽位 revision。"""
    r = client.put(
        f"/api/autosave/{doc_id}", json=_project_doc([TEXT], updated=updated), headers=_h(pj)
    )
    assert r.status_code == 200, r.get_json()
    if node:
        v = client.post(
            f"/api/versions/{doc_id}",
            json={
                "doc": _canvas_doc([TEXT]),
                "auto": True,
                "canvasId": "c1",
                "canvasName": "Fig 1",
            },
            headers=_h(pj),
        )
        assert v.status_code == 200 and v.get_json().get("version"), v.get_json()
    return r.get_json()["revision"]


def _owners() -> dict:
    return layoutsession.snapshot()["owners"]


def test_moved_project_offers_the_local_slot_and_claim_moves_ownership(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    rev = _edit_in(client, a, "d_move")
    assert _owners()["d_move"] == layoutsession.project_key(tmp_path / "projA")
    (tmp_path / "projA").rename(tmp_path / "projB")  # 整夹挪走
    b = _open(client, tmp_path / "projB")
    items = _history(client, b)
    assert [i["doc_id"] for i in items] == ["d_move"]
    item = items[0]
    assert item["mode"] == "slot" and item["owner"] == "moved"
    assert item["revision"] == rev and item["objects"] == 1 and item["name"] == "排版一"
    assert item["node_newer"] is False  # 槽位那张画布与节点一字不差：槽位不旧
    # 认领之前：后端仍按旧归属拒绝把它记成新项目的 last（这就是以前「新路径是空白」的那一道）
    before = client.put(
        "/api/layout-session/last", json={"doc_id": "d_move", "name": "x"}, headers=_h(b)
    )
    assert before.status_code == 409 and before.get_json()["code"] == "layout_foreign"
    r = client.post(
        "/api/layout-session/project-history/claim",
        json={"doc_id": "d_move", "revision": rev},
        headers=_h(b),
    )
    assert r.status_code == 200, r.get_json()
    assert _owners()["d_move"] == layoutsession.project_key(tmp_path / "projB")
    ev = client.get("/api/layout-session/owner?doc_id=d_move", headers=_h(b)).get_json()
    assert ev["owner"] == "this" and ev["evidence"] == "owners" and ev["verified"] is True
    after = client.put(
        "/api/layout-session/last", json={"doc_id": "d_move", "name": "x"}, headers=_h(b)
    )
    assert after.status_code == 200 and after.get_json()["state"] == "durable"
    assert _history(client, b)[0]["owner"] == "this"


def test_copied_project_is_a_copy_and_claim_is_refused(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    rev = _edit_in(client, a, "d_copy")
    slot_before = (m.AUTOSAVE_DIR / "d_copy.json").read_bytes()
    shutil.copytree(tmp_path / "projA", tmp_path / "projC")  # 复制：旧路径还在
    c = _open(client, tmp_path / "projC")
    item = _history(client, c)[0]
    assert item["mode"] == "copy" and item["owner"] == "other" and item["revision"] is None
    r = client.post(
        "/api/layout-session/project-history/claim",
        json={"doc_id": "d_copy", "revision": rev},
        headers=_h(c),
    )
    assert r.status_code == 409 and r.get_json()["code"] == "claim_refused"
    assert _owners()["d_copy"] == layoutsession.project_key(tmp_path / "projA")
    assert (m.AUTOSAVE_DIR / "d_copy.json").read_bytes() == slot_before
    # 原项目照旧认得它
    assert _history(client, a)[0]["owner"] == "this"


def test_claim_rebinds_to_the_revision_the_client_saw(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_rev")
    (tmp_path / "projA").rename(tmp_path / "projB")
    b = _open(client, tmp_path / "projB")
    r = client.post(
        "/api/layout-session/project-history/claim",
        json={"doc_id": "d_rev", "revision": "not-the-slot"},
        headers=_h(b),
    )
    assert r.status_code == 409
    assert _owners()["d_rev"] == layoutsession.project_key(tmp_path / "projA")


def test_no_slot_and_no_owner_restores_from_the_node(client, tmp_path):
    """换电脑 / 换数据目录：本机没有这个 docId 的槽位、也没有归属记录，只剩项目里的时间线。"""
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_node")
    (m.AUTOSAVE_DIR / "d_node.json").unlink()
    layoutsession.forget_documents(["d_node"])
    item = _history(client, a)[0]
    assert item["mode"] == "node" and item["owner"] == "none"
    node = client.get(f"/api/versions/d_node/{item['vid']}", headers=_h(a)).get_json()
    assert node["doc"]["objects"][0]["text"] == "hello"


def test_node_newer_than_slot_is_reported(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_new", updated=1)  # 槽位 updatedAt=1（远早于节点的 ts）
    # 槽位那张画布与节点不同、节点又更晚：节点更新（例如历史在别处接着写过）
    other = dict(TEXT, text="newer")
    client.post(
        "/api/versions/d_new",
        json={"doc": _canvas_doc([other]), "auto": True, "canvasId": "c1"},
        headers=_h(a),
    )
    item = _history(client, a)[0]
    assert item["mode"] == "slot" and item["node_newer"] is True


def test_only_empty_nodes_are_not_candidates(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    # 旧版留下的：只有一个空 `open` 节点的 `.vx`（直接用存储原语造，模拟升级前的现场）
    from tavotto.engine import versionstore

    vdir = tmp_path / "projA" / "tavottofile" / "versions"
    vdir.mkdir(parents=True)
    versionstore.append(
        vdir,
        "d_empty",
        {
            "id": "v1-1",
            "name": "11-25 10:00",
            "ts": 1,
            "auto": True,
            "named": False,
            "description": "",
            "moment": "open",
            "doc": _canvas_doc(),
        },
        now=1,
    )
    assert (vdir / "d_empty.vx").is_dir()
    assert _history(client, a) == []


@pytest.mark.parametrize("moment", ["open", "close"])
def test_blank_open_and_close_moments_leave_no_store(client, tmp_path, moment):
    """空白排版的「打开」「离开」不写（ADR 0211 第一期 4）：新路径每加载 / 离开一次都多一个空 `.vx` 的现象。"""
    a = _open(client, tmp_path / "projA")
    for _ in range(3):
        r = client.post(
            "/api/versions/d_blank",
            json={"doc": _canvas_doc(), "auto": True, "moment": moment},
            headers=_h(a),
        )
        assert r.status_code == 200 and r.get_json().get("skipped") is True
    vdir = tmp_path / "projA" / "tavottofile" / "versions"
    assert not (vdir / "d_blank.vx").exists()
    # 有内容的照常记
    r = client.post(
        "/api/versions/d_blank",
        json={"doc": _canvas_doc([TEXT]), "auto": True, "moment": moment},
        headers=_h(a),
    )
    assert r.get_json().get("version"), r.get_json()


def test_other_moments_on_blank_still_record(client, tmp_path):
    """「恢复前」这类别的时刻不动：空画布照旧记（Codex #679）。"""
    a = _open(client, tmp_path / "projA")
    r = client.post(
        "/api/versions/d_blank2",
        json={"doc": _canvas_doc(), "auto": True, "moment": "before_restore", "name": "恢复前"},
        headers=_h(a),
    )
    assert r.get_json().get("version"), r.get_json()


def test_vocabulary_mirrors_are_literal():
    """同源对（`docs/rules/repo/same-origin-pairs.md`）：候选 `mode` / `owner` 的取值闭集唯一出处在
    `engine/projecthistory`，`web/src/lib/api.ts` 与假后端 `web/src/test/fakeSessionBackend.ts` 逐字镜像。"""
    import re

    from tavotto.engine import projecthistory

    root = Path(__file__).resolve().parent.parent / "web" / "src"
    for rel in ("lib/api.ts", "test/fakeSessionBackend.ts"):
        text = (root / rel).read_text(encoding="utf-8")
        for name, want in (
            ("HISTORY_MODES", projecthistory.HISTORY_MODES),
            ("HISTORY_OWNERS", projecthistory.HISTORY_OWNERS),
        ):
            m = re.search(rf"export const {name} = \[([^\]]*)\] as const", text)
            assert m, f"{rel} 里没有 `export const {name} = [...] as const`"
            got = tuple(re.findall(r"'([^']*)'", m.group(1)))
            assert got == want, f"{rel} 的 {name} 与后端不一致：{got} != {want}"


def test_listing_values_stay_inside_the_vocabulary(client, tmp_path):
    from tavotto.engine import projecthistory

    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_v1")
    _edit_in(client, a, "d_v2")
    (m.AUTOSAVE_DIR / "d_v2.json").unlink()
    shutil.copytree(tmp_path / "projA", tmp_path / "projC")
    for pj in (a, _open(client, tmp_path / "projC")):
        for item in _history(client, pj):
            assert item["mode"] in projecthistory.HISTORY_MODES
            assert item["owner"] in projecthistory.HISTORY_OWNERS


def test_listing_is_read_only(client, tmp_path, data_dir):
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_ro")
    (tmp_path / "projA").rename(tmp_path / "projB")
    b = _open(client, tmp_path / "projB")
    state = layoutsession.state_path()
    before = state.read_bytes()
    vx = tmp_path / "projB" / "tavottofile" / "versions" / "d_ro.vx"
    listing = sorted(p.name for p in vx.rglob("*"))
    _history(client, b)
    assert state.read_bytes() == before
    assert sorted(p.name for p in vx.rglob("*")) == listing


def test_no_project_lists_nothing(client):
    r = client.get("/api/layout-session/project-history")
    assert r.status_code == 200 and r.get_json()["items"] == []


def test_endpoints_stay_behind_session_auth():
    for path in (
        "/api/layout-session/project-history",
        "/api/layout-session/project-history/claim",
    ):
        assert path not in security._PUBLIC_PATHS
        assert not path.startswith(security._PUBLIC_PREFIXES)


def test_response_never_carries_paths(client, tmp_path):
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_path")
    (tmp_path / "projA").rename(tmp_path / "projB")
    b = _open(client, tmp_path / "projB")
    raw = json.dumps(client.get("/api/layout-session/project-history", headers=_h(b)).get_json())
    assert str(tmp_path) not in raw


def test_moved_without_slot_is_a_copy(client, tmp_path):
    """挪走了但本机槽位已不在、owners 还记着旧路径：沿用 docId 会被 `PUT last` 按旧归属拒（`owner_conflict`），
    所以另开一份（复制），不碰旧记录。"""
    a = _open(client, tmp_path / "projA")
    _edit_in(client, a, "d_gone")
    (m.AUTOSAVE_DIR / "d_gone.json").unlink()
    (tmp_path / "projA").rename(tmp_path / "projB")
    b = _open(client, tmp_path / "projB")
    item = _history(client, b)[0]
    assert item["mode"] == "copy" and item["owner"] == "moved"


def test_empty_stores_do_not_crowd_out_the_real_one(client, tmp_path, monkeypatch):
    """挪过的项目堆着成批只有空节点的 `.vx`：先筛掉没内容的再截个数，真的那份不会被截掉。"""
    from tavotto.engine import projecthistory, versionstore

    monkeypatch.setattr(projecthistory, "MAX_STORES", 1)
    a = _open(client, tmp_path / "projA")
    vdir = tmp_path / "projA" / "tavottofile" / "versions"
    vdir.mkdir(parents=True)
    for i in range(5):  # 名字都排在 d_zz_real 前面
        versionstore.append(
            vdir,
            f"d_a{i}",
            {
                "id": "v1-1",
                "name": "x",
                "ts": 1,
                "auto": True,
                "named": False,
                "description": "",
                "moment": "open",
                "doc": _canvas_doc(),
            },
            now=1,
        )
    _edit_in(client, a, "d_zz_real")
    r = client.get("/api/layout-session/project-history", headers=_h(a)).get_json()
    assert [i["doc_id"] for i in r["items"]] == ["d_zz_real"]
    assert r["truncated"] is False
