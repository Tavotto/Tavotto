"""文档恢复可靠性（2026-10-11）后端合同：`engine/layoutsession.py` 与 `app.py` 的自动保存 / 会话 / 候选发现。

对应审计验收 A14/A15/A24–A27/A29/C34 与风险 R3/R5/R6/C2。**全部走真实 Flask test client + 隔离数据目录**，
正文是能过生产 validator、能从 `GET /api/autosave/<id>` 读回的真文档。

* 读取结果四档：缺失 / 读失败 / 坏字节 / 未来格式 与「首次空状态」区分，原字节保留，其他组与 owners 不被抹（A26）；
* last 只指向可恢复正文：槽位没有有效正文 → 只记 pending，首次保存成功才晋升；prev/pending 受清理保护（R3/A27/A29）；
* owner 与正文同一把文档锁提交，旧请求的元数据不会晚于新正文把归属改回去（A24）；元数据失败正文照 200（A25）；
* 候选发现是只读的、认证下的、不回路径、分页有界、坏文件不阻塞（A14/A15）；
* 跨进程文件锁：两个真进程交错读改写不丢更新（C34；Windows 腿留给 CI——`msvcrt.locking` 本机没跑过）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents, layoutsession, project_watch as engine_watch

SRC = Path(m.__file__).resolve().parent.parent

PD = {
    "schema": 3,
    "project": {"id": "p", "name": "n"},
    "canvases": [
        {"id": "c1", "name": "Fig 1", "page": {"w": 10, "h": 10}, "objects": [], "guides": []}
    ],
    "activeCanvasId": "c1",
    "createdAt": 0,
    "updatedAt": 1,
}


def _doc(n: int = 1, name: str = "n", objects: list | None = None) -> dict:
    d = json.loads(json.dumps(PD))
    d["updatedAt"] = n
    d["project"]["name"] = name
    if objects is not None:
        d["canvases"][0]["objects"] = objects
    return d


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setattr(m, "LAYOUT_DIR", d / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    return d


@pytest.fixture
def client(data_dir):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _project(client, tmp_path, name: str, default: bool = False) -> dict:
    root = tmp_path / name
    root.mkdir()
    body = client.post(
        "/api/projects/open", json={"path": str(root), "default": default}
    ).get_json()
    assert body.get("id"), body
    return body


def _pj(pid: str) -> dict:
    return {"X-Tavotto-Project": pid}


def _slot(doc_id: str) -> Path:
    return m.AUTOSAVE_DIR / f"{doc_id}.json"


def _rev(doc_id: str) -> str | None:
    return atomicio.content_revision(_slot(doc_id))


def _put(client, doc_id: str, body: dict, pj: dict | None = None, base: str | None = None):
    url = f"/api/autosave/{doc_id}" + (f"?base_revision={base}" if base else "")
    return client.put(url, json=body, headers=_pj(pj["id"]) if pj else None)


def _get_session(client, pj: dict | None = None) -> dict:
    r = client.get("/api/layout-session", headers=_pj(pj["id"]) if pj else None)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _put_last(client, doc_id: str, name: str = "x", pj: dict | None = None):
    return client.put(
        "/api/layout-session/last",
        json={"doc_id": doc_id, "name": name},
        headers=_pj(pj["id"]) if pj else None,
    )


def _owner(client, pj: dict, doc_id: str) -> dict:
    r = client.get(f"/api/layout-session/owner?doc_id={doc_id}", headers=_pj(pj["id"]))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    body.pop("revision")  # revision 另有专门用例（test_layout_recovery_backend）
    return body


def _candidates(client, pj: dict | None, scope: str = "project", **q) -> dict:
    qs = "&".join([f"scope={scope}", *[f"{k}={v}" for k, v in q.items()]])
    r = client.get(f"/api/layout-session/candidates?{qs}", headers=_pj(pj["id"]) if pj else None)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """目录树快照 {相对路径: (mtime_ns, size)}——证明一次请求什么都没写。"""
    out = {}
    for p in sorted(root.rglob("*")):
        # 锁文件是互斥用的基础设施（空文件），不是状态：只读端点为了在槽位锁里读一致快照也会建它们
        if p.is_file() and p.suffix != ".lock":
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


# ============================ 1.1 读取结果四档（A26） ===========================


def test_a26_missing_file_is_the_first_run_state_not_degraded(client):
    assert layoutsession.read_status() == "missing"
    view = _get_session(client)
    assert view == {"last": None, "prev": None, "pending": None, "index": "missing"}
    # 首次写之后是 ok，且没有任何损坏副本
    assert _put(client, "d_1", _doc()).status_code == 200
    assert _get_session(client)["index"] == "ok"
    assert not list(layoutsession.state_path().parent.glob("layout-sessions.corrupt-*"))


def test_a26_corrupt_bytes_without_bak_are_preserved_and_distinguished_from_first_run(client):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    raw = b'{"projects": {"/a": {"last": '  # 截断的 JSON
    path.write_bytes(raw)
    assert _get_session(client)["index"] == "degraded"  # 不是首次空的 "missing"
    # 保存正文照常 200，元数据写得进去（原字节已留）
    r = _put(client, "d_1", _doc())
    assert r.status_code == 200 and r.get_json()["metadata"] == "ok"
    copies = list(path.parent.glob("layout-sessions.corrupt-*.json"))
    assert [c.read_bytes() for c in copies] == [raw], "损坏的原字节没有留下"
    assert _get_session(client)["index"] == "ok"
    assert layoutsession.owners()["d_1"] is None


def test_a26_corrupt_main_recovers_from_the_bak_without_losing_other_groups(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    b = _project(client, tmp_path, "b")
    for pj, doc_id in ((a, "d_a"), (b, "d_b")):
        assert _put(client, doc_id, _doc(), pj).status_code == 200
        assert _put_last(client, doc_id, pj=pj).status_code == 200
    path = layoutsession.state_path()
    good = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(layoutsession.backup_path().read_text(encoding="utf-8")) == good
    path.write_bytes(b"\x00\x01 not json")
    view = _get_session(client, b)
    assert view["index"] == "recovered" and view["last"]["doc_id"] == "d_b"
    assert _get_session(client, a)["last"]["doc_id"] == "d_a"
    # 恢复后的下一次写带着所有组和 owners
    assert _put(client, "d_c", _doc(), a).status_code == 200
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["projects"].keys() == good["projects"].keys()
    assert {"d_a", "d_b", "d_c"} <= set(after["owners"])
    assert [c.read_bytes() for c in path.parent.glob("layout-sessions.corrupt-*.json")] == [
        b"\x00\x01 not json"
    ]


@pytest.mark.parametrize("project_name", ["a", r"a\project"])
def test_a26_unrelated_nan_field_does_not_void_the_file(client, tmp_path, project_name):
    # POSIX 也用带反斜杠的真实目录，避免 Windows 路径转义的回归只在平台腿才被发现。
    (tmp_path / project_name).parent.mkdir(parents=True, exist_ok=True)
    a = _project(client, tmp_path, project_name, default=True)
    key = layoutsession.project_key(a["figures_dir"])
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        # NaN 是本用例的坏值，Windows 项目路径的反斜杠仍须正确转义。
        json.dumps(
            {
                "version": 1,
                "projects": {
                    key: {
                        "last": {"doc_id": "d_a", "name": "A", "at": 5},
                        "future_flag": {"x": float("nan"), "keep": 1},
                    }
                },
                "no_project": {},
                "owners": {"d_a": key},
                "x_top": float("nan"),
                "y_top": [1, 2],
            }
        ),
        encoding="utf-8",
    )
    _put(client, "d_a", _doc(), a)
    view = _get_session(client, a)
    assert view["index"] == "ok" and view["last"]["doc_id"] == "d_a"
    assert _put(client, "d_b", _doc(), a).status_code == 200  # 触发一次整份写回
    after = json.loads(path.read_text(encoding="utf-8"))  # 写回的是严格 JSON（无 NaN）
    assert after["projects"][key]["last"]["doc_id"] == "d_a"
    assert after["projects"][key]["future_flag"] == {"keep": 1}  # 未知键保留，只丢非有限数
    assert after["y_top"] == [1, 2] and "x_top" not in after
    assert {"d_a", "d_b"} <= set(after["owners"])


def test_a26_unreadable_state_refuses_to_overwrite_and_keeps_the_bytes(
    client, tmp_path, monkeypatch
):
    a = _project(client, tmp_path, "a", default=True)
    _put(client, "d_a", _doc(), a)
    _put_last(client, "d_a", pj=a)
    path = layoutsession.state_path()
    before = path.read_bytes()
    real = Path.read_bytes

    def flaky(self):
        if self.name == layoutsession.FILENAME:
            raise PermissionError(13, "boom")
        return real(self)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "read_bytes", flaky)
        view = _get_session(client, a)
        assert view["index"] == "degraded" and view["last"] is None  # 读不到 ≠ 没有
        r = _put(client, "d_b", _doc(2), a)
        assert r.status_code == 200 and r.get_json()["metadata"] == "failed"
        assert r.get_json()["revision"] == _rev("d_b")  # 正文 revision 如实回
        r = _put_last(client, "d_b", pj=a)
        assert r.status_code == 503 and r.get_json()["code"] == "session_unavailable"
    assert path.read_bytes() == before, "读失败的那几次把一份可能完整的状态覆盖了"
    assert _get_session(client, a)["last"]["doc_id"] == "d_a"
    assert not list(path.parent.glob("layout-sessions.corrupt-*"))


def test_a26_future_format_is_read_best_effort_but_never_written(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    key = layoutsession.project_key(a["figures_dir"])
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    future = json.dumps(
        {
            "version": 99,
            "projects": {key: {"last": {"doc_id": "d_a", "name": "A", "at": 1}}},
            "owners": {"d_a": key},
            "newer": {"shape": [1]},
        }
    ).encode()
    path.write_bytes(future)
    _put(client, "d_a", _doc(), a)  # 槽位在，owners 不被改
    view = _get_session(client, a)
    assert view == {"last": None, "prev": None, "pending": None, "index": "degraded"}
    r = _put(client, "d_a", _doc(2), a)
    assert r.status_code == 200 and r.get_json()["metadata"] == "failed"
    assert _put_last(client, "d_a", pj=a).status_code == 503
    assert client.delete("/api/autosave/d_a").status_code == 200  # 删槽位不致崩
    assert path.read_bytes() == future


def test_a26_corrupt_copies_are_bounded_to_three(data_dir):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    for i in range(5):
        path.write_bytes(b"{broken %d" % i)
        layoutsession.record_owner(f"d_{i}", None)  # 写入替换掉坏文件
        time.sleep(0.01)
    copies = sorted(path.parent.glob("layout-sessions.corrupt-*.json"))
    assert len(copies) == 3
    assert [c.read_bytes() for c in copies] == [b"{broken 2", b"{broken 3", b"{broken 4"]


def test_a26_unknown_keys_are_kept_through_writes(data_dir):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {"/a": {"last": {"doc_id": "d_a", "name": "A", "at": 1}, "note": [1]}},
                "owners": {},
                "extra_top": {"a": 1},
            }
        ),
        encoding="utf-8",
    )
    layoutsession.record_owner("d_a", "/a")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["extra_top"] == {"a": 1} and after["projects"]["/a"]["note"] == [1]


# ============================ 1.2 last 只指向可恢复正文（R3 / A29） ==============


def test_r3_new_document_without_a_body_only_becomes_pending(client):
    assert _put(client, "d_a", _doc()).status_code == 200
    assert _put_last(client, "d_a", "A").get_json()["state"] == "durable"
    r = _put_last(client, "d_b", "B")  # B 还没有任何正文
    assert r.status_code == 200
    body = r.get_json()
    assert body["state"] == "pending" and body["last"]["doc_id"] == "d_a"
    assert body["pending"]["doc_id"] == "d_b"
    view = _get_session(client)
    assert view["last"]["doc_id"] == "d_a", "durable last 被一个还没有正文的 B 顶掉了"
    assert view["pending"]["doc_id"] == "d_b"


@pytest.mark.parametrize("junk", [b"{not json", json.dumps({"schema": 99}).encode(), b"[1]"])
def test_r3_a_slot_without_a_valid_body_does_not_count_as_recoverable(client, junk):
    m.AUTOSAVE_DIR.mkdir(parents=True)
    _slot("d_bad").write_bytes(junk)
    body = _put_last(client, "d_bad").get_json()
    assert body["state"] == "pending" and body["last"] is None
    assert _get_session(client)["last"] is None


def test_a29_old_frontend_early_last_then_first_autosave_promotes(client):
    """旧前端的正常顺序：先 PUT last、再首次自动保存。不能留悬空 last，也不能只看 2xx。"""
    assert _put(client, "d_a", _doc()).status_code == 200
    _put_last(client, "d_a", "A")
    early = _put_last(client, "d_b", "B")
    assert early.status_code == 200 and early.get_json()["state"] == "pending"
    assert _get_session(client)["last"]["doc_id"] == "d_a"  # 正文还没落盘：不晋升
    r = _put(client, "d_b", _doc(2, "B"))
    assert r.status_code == 200 and r.get_json()["metadata"] == "ok"
    view = _get_session(client)
    assert view["last"]["doc_id"] == "d_b" and view["pending"] is None
    assert view["prev"]["doc_id"] == "d_a"
    assert _get_session(client)["last"]["doc_id"] == "d_b"
    # 晋升的是 pending 指着的那一份：别的槽位保存不会顺手晋升
    _put_last(client, "d_c", "C")
    _put(client, "d_other", _doc(3))
    assert _get_session(client)["last"]["doc_id"] == "d_b"
    assert _get_session(client)["pending"]["doc_id"] == "d_c"


def test_a29_explicitly_opening_an_existing_slot_is_durable_without_a_new_save(client):
    _put(client, "d_a", _doc())
    _put(client, "d_b", _doc())
    _put_last(client, "d_a")
    r = _put_last(client, "d_b")
    assert r.get_json()["state"] == "durable"
    assert _get_session(client)["prev"]["doc_id"] == "d_a"


def test_prev_and_pending_have_capacity_one(data_dir):
    layoutsession.set_last(None, "d_1", "1")
    layoutsession.set_last(None, "d_2", "2")
    layoutsession.set_last(None, "d_3", "3")
    layoutsession.set_last(None, "d_x", "x", slot_ok=False)
    layoutsession.set_last(None, "d_y", "y", slot_ok=False)
    group = layoutsession.snapshot()["no_project"]
    assert (group["last"]["doc_id"], group["prev"]["doc_id"], group["pending"]["doc_id"]) == (
        "d_3",
        "d_2",
        "d_y",
    )
    # A→B→A：prev 是 B，last 是 A（同一份不会既是 last 又是 prev）；更晚的 durable 取代 pending
    layoutsession.set_last(None, "d_2", "2")
    group = layoutsession.snapshot()["no_project"]
    assert (group["last"]["doc_id"], group["prev"]["doc_id"]) == ("d_2", "d_3")
    assert "pending" not in group
    layoutsession.set_last(None, "d_y", "y", slot_ok=False)
    # 同值不重写
    path = layoutsession.state_path()
    os.utime(path, ns=(0, 10**9))
    layoutsession.set_last(None, "d_y", "y", slot_ok=False)
    layoutsession.set_last(None, "d_y", "y", slot_ok=False)
    assert path.stat().st_mtime_ns == 10**9


def test_duplicate_prev_from_existing_state_is_not_exposed_as_another_recovery_slot(data_dir):
    """旧状态里同一槽位重复记成 last/prev 时，恢复候选不能把它当成两份。"""
    layoutsession.set_last(None, "d_1", "first")
    path = layoutsession.state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    state["no_project"]["prev"] = {**state["no_project"]["last"], "at": 1}
    path.write_text(json.dumps(state), encoding="utf-8")

    group = layoutsession.snapshot()["no_project"]
    assert group["last"]["doc_id"] == "d_1"
    assert "prev" not in group
    assert layoutsession.view_for(None)["prev"] is None


def test_commit_slot_return_values(data_dir):
    assert layoutsession.commit_slot("d_a", "/a", "r1") == {"owner": "recorded", "promoted": False}
    assert layoutsession.commit_slot("d_a", "/a", "r1") == {"owner": "unchanged", "promoted": False}
    assert layoutsession.commit_slot("d_a", "/a", "r2")["owner"] == "recorded"  # revision 变了
    assert layoutsession.commit_slot("d_a", layoutsession.UNKNOWN, "r3") == {
        "owner": "skipped",
        "promoted": False,
    }
    assert layoutsession.snapshot()["owner_revisions"]["d_a"] == "r2"
    layoutsession.set_last("/a", "d_a", "A", slot_ok=False)
    assert layoutsession.commit_slot("d_a", "/a", "r2") == {"owner": "unchanged", "promoted": True}
    assert layoutsession.last_for("/a")["doc_id"] == "d_a"


def test_stale_pj_save_records_nothing_and_does_not_promote(client):
    _put_last(client, "d_a", "A")  # pending（无项目组）
    r = client.put("/api/autosave/d_a", json=_doc(), headers=_pj("gone"))
    assert r.status_code == 200 and r.get_json()["metadata"] == "owner_unknown"
    assert r.get_json()["revision"] == _rev("d_a")
    assert "d_a" not in layoutsession.owners()
    assert _get_session(client)["last"] is None


def test_get_session_filters_prev_and_pending_by_owner(client, tmp_path):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kg = layoutsession.project_key(g["figures_dir"])
    layoutsession.set_last(kg, "d_f", "F")  # 模拟修复前污染
    layoutsession.set_last(kg, "d_f2", "F2", slot_ok=False)
    layoutsession.set_last(kg, "d_g", "G")
    layoutsession.set_last(kg, "d_g2", "G2", slot_ok=False)
    layoutsession.record_owner("d_f", layoutsession.project_key(f["figures_dir"]))
    layoutsession.record_owner("d_f2", layoutsession.project_key(f["figures_dir"]))
    view = _get_session(client, g)
    assert view["last"]["doc_id"] == "d_g" and view["prev"] is None  # prev=d_f 是 F 的
    assert view["pending"]["doc_id"] == "d_g2"


# ============================ 1.3 owner 与正文同序（R6 / A24 / A25） ==============


def test_a24_same_docid_two_projects_owner_follows_the_latest_body(client, tmp_path, monkeypatch):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kf = layoutsession.project_key(f["figures_dir"])
    kg = layoutsession.project_key(g["figures_dir"])
    r0 = _put(client, "d_x", _doc(1), f)
    rev0 = r0.get_json()["revision"]

    entered, release = threading.Event(), threading.Event()
    armed = [False]
    real = layoutsession.commit_slot

    def gated(doc_id, key, revision):
        if armed[0] and key == kf:
            entered.set()
            assert release.wait(15), "测试自己没放行"
        return real(doc_id, key, revision)

    monkeypatch.setattr(layoutsession, "commit_slot", gated)
    armed[0] = True
    out: dict = {}

    def first():  # F 的保存：正文落盘后，元数据提交被卡住
        out["f"] = _put(m.app.test_client(), "d_x", _doc(2, "from F"), f, base=rev0)

    t1 = threading.Thread(target=first)
    t1.start()
    assert entered.wait(15)
    rev1 = _rev("d_x")  # F 的正文已在盘上；G 带着它读到的合法 revision 来保存
    assert rev1 != rev0

    def second():
        out["g"] = _put(m.app.test_client(), "d_x", _doc(3, "from G"), g, base=rev1)

    t2 = threading.Thread(target=second)
    t2.start()
    t2.join(0.5)  # 修复后 G 在文档锁上排队；若归属提交在锁外，G 此刻已经完成
    release.set()
    t1.join(15)
    t2.join(15)
    assert not t1.is_alive() and not t2.is_alive()
    assert out["f"].status_code == 200 and out["g"].status_code == 200
    final = out["g"].get_json()["revision"]
    assert final == _rev("d_x")
    snap = layoutsession.snapshot()
    assert snap["owners"]["d_x"] == kg, "晚到的 F 元数据把归属改回了 F"
    assert snap["owner_revisions"]["d_x"] == final
    assert _owner(client, g, "d_x") == {"owner": "this", "evidence": "owners", "verified": True}
    assert _owner(client, f, "d_x")["owner"] == "other"


def test_a25_metadata_failure_keeps_the_body_and_the_next_save_repairs_it(
    client, tmp_path, monkeypatch
):
    f = _project(client, tmp_path, "projF", default=True)
    kf = layoutsession.project_key(f["figures_dir"])
    real = atomicio.write_json

    def failing(path, obj, *, indent=None):
        if Path(path).parent == layoutsession.state_path().parent:
            raise atomicio.AtomicWriteError("write_failed", "disk full", Path(path))
        return real(path, obj, indent=indent)

    monkeypatch.setattr(atomicio, "write_json", failing)
    r = _put(client, "d_1", _doc(1, "body"), f)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["metadata"] == "failed" and body["revision"] == _rev("d_1")
    got = client.get("/api/autosave/d_1")  # 正文真的在、能读回、revision 一致
    assert got.status_code == 200 and got.headers["X-Tavotto-Revision"] == body["revision"]
    assert got.get_json()["project"]["name"] == "body"
    assert "d_1" not in layoutsession.owners()
    monkeypatch.setattr(atomicio, "write_json", real)
    r2 = _put(client, "d_1", _doc(2, "body2"), f, base=body["revision"])
    assert r2.get_json()["metadata"] == "ok"  # 下一次保存补记
    snap = layoutsession.snapshot()
    assert (
        snap["owners"]["d_1"] == kf and snap["owner_revisions"]["d_1"] == r2.get_json()["revision"]
    )


def test_a25_owner_that_no_longer_matches_the_body_is_unverified_not_this_nor_other(
    client, tmp_path, monkeypatch
):
    """进程死在「正文已落盘、元数据未提交」之间的窗口：F 的旧归属记录与新正文对不上。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kf = layoutsession.project_key(f["figures_dir"])
    assert _put(client, "d_1", _doc(1), f).status_code == 200
    _put_last(client, "d_1", pj=f)
    real = atomicio.write_json

    def failing(path, obj, *, indent=None):
        if Path(path).parent == layoutsession.state_path().parent:
            raise atomicio.AtomicWriteError("write_failed", "x", Path(path))
        return real(path, obj, indent=indent)

    monkeypatch.setattr(atomicio, "write_json", failing)
    assert _put(client, "d_1", _doc(2), f, base=_rev("d_1")).get_json()["metadata"] == "failed"
    monkeypatch.setattr(atomicio, "write_json", real)
    expect = {"owner": "unknown", "evidence": None, "verified": False}
    assert _owner(client, f, "d_1") == expect
    assert _owner(client, g, "d_1") == expect  # 不当 other 也不当 this
    # 待核对的内容不自动恢复：对任何项目都不回（F 自己也不）；G 也不能凭它认领 F 的槽位
    assert _get_session(client, f)["last"] is None
    assert _get_session(client, g)["last"] is None
    assert _put_last(client, "d_1", pj=g).status_code == 409
    # 旧记录（没有 owner_revisions）按现状信任——兼容边界
    state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    state["owner_revisions"].pop("d_1")
    state["owners"]["d_1"] = kf
    layoutsession.state_path().write_text(json.dumps(state), encoding="utf-8")
    assert _owner(client, g, "d_1") == {"owner": "other", "evidence": "owners", "verified": True}


def test_autosave_response_always_carries_the_metadata_verdict(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    r = _put(client, "d_1", _doc(), a)
    assert r.get_json()["metadata"] == "ok"
    assert set(r.get_json()) == {"ok", "saved_at", "revision", "metadata", "figure_snapshots"}
    assert r.get_json()["figure_snapshots"] == {}


# ============================ 1.2/A27 清理保护 ===================================


def _legacy(doc_id: str, mtime_s: int) -> Path:
    m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    p = _slot(doc_id)
    p.write_text(json.dumps(PD), encoding="utf-8")
    os.utime(p, ns=(0, mtime_s * 10**9))
    return p


def test_a27_pruning_protects_last_prev_and_pending_but_still_trims_the_rest(client, monkeypatch):
    for i, d in enumerate(["d_last", "d_prev", "d_pend", "d_free"]):
        assert _put(client, d, _doc(i)).status_code == 200
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    unknown = _legacy("d_unowned", 1)  # 没有归属的老槽位
    layoutsession.set_last(None, "d_prev", "P")
    layoutsession.set_last(None, "d_last", "L")
    layoutsession.set_last(None, "d_pend", "Q", slot_ok=False)
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    assert _put(client, "d_new1", _doc(9)).status_code == 200
    assert _put(client, "d_new2", _doc(9)).status_code == 200
    names = {p.stem for p in m.AUTOSAVE_DIR.glob("*.json")}
    assert {"d_last", "d_prev", "d_pend"} <= names, f"受保护的槽位被删了：{names}"
    assert unknown.is_file(), "未知 owner 的老槽位被放宽删了"
    assert "d_free" not in names, "没人保护的槽位该照常被裁"
    assert "d_free" not in layoutsession.owners()


def test_a27_pruning_rechecks_prev_protection_after_a_racing_set_last(client, monkeypatch):
    """清理拿快照之后，两次 set_last 把最旧的槽位推成了 prev：快照里它不受保护，删之前要重判。"""
    _put(client, "d_cur", _doc())
    layoutsession.set_last(None, "d_cur", "cur")
    for i, d in enumerate(["d_old", "d_mid"]):
        _put(client, d, _doc(i))
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    real = layoutsession.protected_doc_ids
    fired = []

    def snapshot_then_race():
        ids = real()
        if not fired:
            fired.append(1)
            layoutsession.set_last(None, "d_old", "old")  # d_cur → prev
            layoutsession.set_last(None, "d_mid", "mid")  # d_old → prev
        return ids

    monkeypatch.setattr(layoutsession, "protected_doc_ids", snapshot_then_race)
    _put(client, "d_newest", _doc(9))
    assert fired
    group = layoutsession.snapshot()["no_project"]
    assert group["last"]["doc_id"] == "d_mid" and group["prev"]["doc_id"] == "d_old"
    assert _slot("d_old").is_file() and _slot("d_mid").is_file(), (
        "快照之后成了 last/prev 的槽位被删了"
    )


def test_a27_set_last_cannot_point_at_a_slot_that_pruning_deletes_in_between(client, monkeypatch):
    """验槽位通过之后、记 last 之前，清理想删掉这个（最旧、有归属、当时不受保护的）槽位：
    验证与记录在槽位的同一把文档锁里，清理只能排在后面，之后又被 last 保护——last 不悬空。"""
    for i, d in enumerate(["d_target", "d_keep"]):
        assert _put(client, d, _doc(i)).status_code == 200
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    real = m._slot_recoverable
    pruners: list[threading.Thread] = []

    def verify_then_prune(path):
        verdict = real(path)
        t = threading.Thread(target=m._prune_autosave_slots, args=(_slot("d_keep"),))
        t.start()
        t.join(1.0)  # 修复后它在这个槽位的文档锁上排队，这里等不到它
        pruners.append(t)
        return verdict

    monkeypatch.setattr(m, "_slot_recoverable", verify_then_prune)
    assert _put_last(client, "d_target", "T").get_json()["state"] == "durable"
    for t in pruners:
        t.join(15)
    last = _get_session(client)["last"]
    assert last and last["doc_id"] == "d_target"
    assert _slot("d_target").is_file(), "last 指着一个刚被清理删掉的槽位"


def test_a27_pruning_never_deletes_when_the_state_cannot_be_read(client, monkeypatch):
    for i, d in enumerate(["d_a", "d_b", "d_c"]):
        _put(client, d, _doc(i))
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    layoutsession.set_last(None, "d_c", "c")
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    real = Path.read_bytes

    def flaky(self):
        if self.name == layoutsession.FILENAME:
            raise PermissionError(13, "boom")
        return real(self)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "read_bytes", flaky)
        _put(client, "d_d", _doc(9))
    assert all(_slot(d).is_file() for d in ["d_a", "d_b", "d_c"]), "不知道谁受保护时删了槽位"


def test_delete_forgets_last_prev_pending_and_owner_revision(client):
    for d in ("d_1", "d_2", "d_3"):
        _put(client, d, _doc())
    layoutsession.set_last(None, "d_1", "1")
    layoutsession.set_last(None, "d_2", "2")  # d_1 → prev
    layoutsession.set_last(None, "d_3", "3", slot_ok=False)
    for d in ("d_1", "d_2", "d_3"):
        client.delete(f"/api/autosave/{d}")
    snap = layoutsession.snapshot()
    assert snap["owners"] == {} and snap["owner_revisions"] == {}
    assert snap["no_project"] == {}


# ============================ 1.4 候选发现（P03 / A14 / A15） ====================


def _runtime_panel(file_id: str = "runtime:fig1.py#Fig1_PRISMA_Flowchart") -> dict:
    return {
        "id": "o1",
        "type": "panel",
        "fileId": file_id,
        "x": 0,
        "y": 0,
        "w": 5,
        "h": 5,
        "overrides": {"title": {"text": "手改过的标题"}},
    }


def test_a14_owners_this_without_last_is_listed_from_the_real_slot(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    panel = {"id": "p1", "type": "panel", "fileId": "figs/a.pdf", "x": 0, "y": 0, "w": 3, "h": 3}
    assert _put(client, "d_mine", _doc(7, "我的排版", [panel]), a).status_code == 200
    layoutsession.state_path().unlink()
    layoutsession.backup_path().unlink()
    layoutsession.record_owner("d_mine", layoutsession.project_key(a["figures_dir"]))
    assert _get_session(client, a)["last"] is None  # last 丢了
    listing = _candidates(client, a)
    assert [i["doc_id"] for i in listing["items"]] == ["d_mine"]
    item = listing["items"][0]
    assert item["status"] == "ok" and item["owner"] == "this" and item["evidence"] == "owners"
    assert item["name"] == "我的排版" and item["updatedAt"] == 7
    assert item["canvases"] == 1 and item["objects"] == 1 and item["schema"] == 3
    assert item["revision"] == _rev("d_mine") and item["panels"] == ["figs/a.pdf"]
    assert item["sketch"]["objects"][0]["fileId"] == "figs/a.pdf"
    assert "overrides" not in json.dumps(item)
    assert _candidates(client, a, "unknown")["items"] == []
    # 不回项目路径
    assert str(tmp_path) not in json.dumps(listing)
    # owners 说有、但没有真实槽位：不列
    layoutsession.record_owner("d_ghost", layoutsession.project_key(a["figures_dir"]))
    assert [i["doc_id"] for i in _candidates(client, a)["items"]] == ["d_mine"]


def test_a15_pure_runtime_document_is_discoverable_as_unknown_only(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    (Path(a["figures_dir"]) / "fig1.py").write_text("def Fig1_PRISMA_Flowchart(): ...\n")
    m.AUTOSAVE_DIR.mkdir(parents=True)
    _slot("d_rt").write_text(json.dumps(_doc(3, "runtime 排版", [_runtime_panel()])))
    slot_bytes = _slot("d_rt").read_bytes()
    before = _tree(tmp_path)
    unknown = _candidates(client, a, "unknown")
    after = _tree(tmp_path)
    assert after == before, "候选发现写了东西"
    assert [i["doc_id"] for i in unknown["items"]] == ["d_rt"]
    item = unknown["items"][0]
    assert item["owner"] == "unknown" and item["evidence"] is None
    assert item["runtime"] == [{"script": "fig1.py", "figure": "Fig1_PRISMA_Flowchart"}]
    assert "手改过的标题" not in json.dumps(item) and "overrides" not in json.dumps(item)
    assert _candidates(client, a, "project")["items"] == []
    # /owner 照旧不为 runtime 面板作证
    assert _owner(client, a, "d_rt")["owner"] == "unknown"
    assert _slot("d_rt").read_bytes() == slot_bytes
    assert not layoutsession.state_path().exists()


def test_candidates_owned_by_another_project_appear_in_neither_scope(client, tmp_path):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _put(client, "d_f", _doc(), f)
    _put(client, "d_g", _doc(), g)
    for scope in ("project", "unknown"):
        ids = {i["doc_id"] for i in _candidates(client, g, scope)["items"]}
        assert "d_f" not in ids
    assert [i["doc_id"] for i in _candidates(client, g)["items"]] == ["d_g"]


def test_candidates_without_any_project_see_the_no_project_group_only(client, tmp_path):
    _put(client, "d_loose", _doc())
    layoutsession.record_owner("d_elsewhere", "/some/project")
    (m.AUTOSAVE_DIR / "d_elsewhere.json").write_text(json.dumps(_doc()))
    assert [i["doc_id"] for i in _candidates(client, None)["items"]] == ["d_loose"]
    assert _candidates(client, None, "unknown")["items"] == []


def test_candidates_stale_owner_is_unknown_not_hidden(client, tmp_path):
    """正文被动过但归属元数据没跟上（A25 的窗口）：在 unknown 里能找到，而不是消失。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _put(client, "d_1", _doc(1), f)
    _slot("d_1").write_text(json.dumps(_doc(2, "changed")))  # 绕过保存链
    assert [i["doc_id"] for i in _candidates(client, g, "unknown")["items"]] == ["d_1"]
    assert _candidates(client, f, "project")["items"] == []


def test_a15_bad_files_do_not_block_the_listing(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    _put(client, "d_ok", _doc(5), a)
    m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    (m.AUTOSAVE_DIR / "d_bad.json").write_bytes(b"{nope")
    (m.AUTOSAVE_DIR / "d_nan.json").write_bytes(b'{"schema": 3, "x": NaN}')
    (m.AUTOSAVE_DIR / "d_new.json").write_text(json.dumps({"schema": 99, "project": {"name": "n"}}))
    (m.AUTOSAVE_DIR / "not a docid.json").write_text("{}")
    (m.AUTOSAVE_DIR / "readme.txt").write_text("x")
    link = m.AUTOSAVE_DIR / "d_link.json"
    link.symlink_to(_slot("d_ok"))
    listing = _candidates(client, a, "unknown")
    by_id = {i["doc_id"]: i for i in listing["items"]}
    assert set(by_id) == {"d_bad", "d_nan", "d_new"}
    assert by_id["d_bad"]["status"] == "invalid" and by_id["d_nan"]["status"] == "invalid"
    assert by_id["d_new"]["status"] == "too_new" and by_id["d_new"]["schema"] == 99
    assert [i["doc_id"] for i in _candidates(client, a, "project")["items"]] == ["d_ok"]


def test_candidates_too_large_is_reported_without_reading(client, tmp_path, monkeypatch):
    a = _project(client, tmp_path, "a", default=True)
    _put(client, "d_big", _doc(), a)
    monkeypatch.setattr(m, "CANDIDATE_MAX_BYTES", 10)
    real = Path.read_bytes

    def no_read(self):
        if self.name == "d_big.json":
            raise AssertionError("读了超限的文件")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", no_read)
    items = _candidates(client, a)["items"]
    assert [(i["doc_id"], i["status"], i["revision"]) for i in items] == [
        ("d_big", "too_large", None)
    ]


def test_candidates_pagination_is_bounded_stable_and_complete(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    ids = [f"d_{i}" for i in range(7)]
    for i, d in enumerate(ids):
        _put(client, d, _doc(i), a)
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        q = {"limit": 3}
        if cursor:
            q["cursor"] = cursor
        page = _candidates(client, a, **q)
        assert len(page["items"]) <= 3
        seen += [i["doc_id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == list(reversed(ids)) and pages == 3  # 新→旧，无重复无遗漏
    assert client.get("/api/layout-session/candidates?cursor=@@@").status_code == 400
    assert client.get("/api/layout-session/candidates?scope=all").status_code == 400
    assert client.get("/api/layout-session/candidates?limit=zz").status_code == 400
    # limit 封顶 100
    assert len(_candidates(client, a, limit=100000)["items"]) == 7


def test_candidates_never_write_even_with_a_corrupt_state_file(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    _put(client, "d_1", _doc(), a)
    path = layoutsession.state_path()
    layoutsession.backup_path().unlink()
    path.write_bytes(b"{broken")
    before = _tree(tmp_path)
    listing = _candidates(client, a, "unknown")
    assert listing["index"] == "degraded"
    assert [i["doc_id"] for i in listing["items"]] == ["d_1"]  # 归属丢了：在 unknown 里能找到
    assert _tree(tmp_path) == before, "只读端点留下了损坏副本或别的写入"


def test_candidates_route_is_not_public():
    from tavotto import security

    assert "/api/layout-session/candidates" not in security._PUBLIC_PATHS


# ============================ 1.5 / C34 跨进程与锁顺序 ===========================

_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tavotto.engine import layoutsession as ls
tag, n, go = sys.argv[2], int(sys.argv[3]), sys.argv[4]
while not os.path.exists(go):
    time.sleep(0.001)
for i in range(n):
    doc = "d_%s_%d" % (tag, i)
    ls.commit_slot(doc, "/p/" + tag, "rev%d" % i)
    ls.set_last("/p/" + tag, doc, "name")
"""


def test_c34_two_real_processes_interleaving_lose_no_update(tmp_path):
    """两个真进程共用一个数据目录、同时读改写同一份状态：谁的更新都不能丢。
    Windows 腿（`msvcrt.locking`）本机没跑过，留给 CI。"""
    data = tmp_path / "data"
    go = tmp_path / "go"
    n = 30
    env = {**os.environ, "TAVOTTO_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(SRC), tag, str(n), str(go)],
            env=env,
            stderr=subprocess.PIPE,
        )
        for tag in ("a", "b")
    ]
    time.sleep(1.0)  # 两个子进程都已 import 完、在等发令
    go.write_text("go")
    errs = []
    for p in procs:
        _out, err = p.communicate(timeout=120)
        errs.append(err.decode())
        assert p.returncode == 0, err.decode()
    state = json.loads((data / "state" / "layout-sessions.json").read_text(encoding="utf-8"))
    want = {f"d_{t}_{i}" for t in ("a", "b") for i in range(n)}
    assert set(state["owners"]) == want, f"丢了 {len(want - set(state['owners']))} 条归属"
    for t in ("a", "b"):
        group = state["projects"][f"/p/{t}"]
        assert group["last"]["doc_id"] == f"d_{t}_{n - 1}"
        assert group["prev"]["doc_id"] == f"d_{t}_{n - 2}"


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_c34_a_held_file_lock_times_out_instead_of_hanging(data_dir, monkeypatch):
    import fcntl

    monkeypatch.setattr(layoutsession, "LOCK_TIMEOUT_S", 0.3)
    layoutsession.lock_path().parent.mkdir(parents=True)
    fd = os.open(layoutsession.lock_path(), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        with pytest.raises(layoutsession.SessionStateUnavailable):
            layoutsession.record_owner("d_1", None)
        assert time.monotonic() - t0 < 5
    finally:
        os.close(fd)
    assert layoutsession.record_owner("d_1", None) is True  # 释放后恢复


def test_lock_order_has_no_deadlock_under_mixed_load(client, tmp_path, monkeypatch):
    """自动保存 / DELETE / PUT last / 清理 / GET 同时打同一批槽位：全部在限时内结束（文档锁 → 会话锁）。"""
    a = _project(client, tmp_path, "a", default=True)
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 2)
    stop = time.monotonic() + 3
    errors: list[str] = []

    def worker(k: int):
        c = m.app.test_client()
        i = 0
        try:
            while time.monotonic() < stop:
                d = f"d_{(i + k) % 5}"
                for r in (
                    _put(c, d, _doc(i), a),
                    _put_last(c, d, pj=a),
                    c.get("/api/layout-session", headers=_pj(a["id"])),
                    c.delete(f"/api/autosave/d_{(i + k + 2) % 5}"),
                    c.get("/api/layout-session/candidates?scope=project", headers=_pj(a["id"])),
                ):
                    if r.status_code >= 500:
                        errors.append(f"{r.status_code} {r.get_json()}")
                i += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "死锁：线程没有在限时内结束"
    assert not errors, errors[:3]
    snap = layoutsession.snapshot()
    for group in [snap["no_project"], *snap["projects"].values()]:
        last, prev = group.get("last"), group.get("prev")
        assert not (last and prev and last["doc_id"] == prev["doc_id"])


def test_f1_a_later_durable_open_supersedes_an_older_pending(client):
    """A durable → 想开 B(pending) → 又回到 A(durable) → B 的首次保存迟到：不能把过期的 pending 晋升。"""
    _put(client, "d_a", _doc(1))
    _put_last(client, "d_a", "A")
    assert _put_last(client, "d_b", "B").get_json()["state"] == "pending"
    assert _put_last(client, "d_a", "A").get_json()["state"] == "durable"
    assert _get_session(client)["pending"] is None
    assert _put(client, "d_b", _doc(2, "B")).get_json()["metadata"] == "ok"
    view = _get_session(client)
    assert view["last"]["doc_id"] == "d_a" and view["pending"] is None
    # 别的 durable 也一样取代（pending 指着谁都清）
    _put(client, "d_c", _doc(3))
    _put_last(client, "d_x", "X")  # pending
    _put_last(client, "d_c", "C")
    assert _get_session(client)["pending"] is None
    assert _get_session(client)["prev"]["doc_id"] == "d_a"


def test_f2_stale_owner_never_lets_another_project_claim_the_slot(client, tmp_path):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kf = layoutsession.project_key(f["figures_dir"])
    _put(client, "d_x", _doc(1), f)
    _slot("d_x").write_text(
        json.dumps(_doc(2, "外部改过"))
    )  # 正文被外部改了，owner_revisions 对不上
    assert layoutsession.snapshot()["owners"]["d_x"] == kf
    r = _put_last(client, "d_x", "X", pj=g)
    assert r.status_code == 409 and r.get_json()["code"] == "layout_foreign"
    assert _get_session(client, g)["last"] is None and _get_session(client, g)["pending"] is None
    # 待核对：F 自己的指针也不自动恢复；/owner 仍是 unknown
    layoutsession.set_last(kf, "d_x", "X")
    assert _get_session(client, f)["last"] is None
    assert _owner(client, g, "d_x") == {"owner": "unknown", "evidence": None, "verified": False}
    # 候选：对每个项目都在 unknown 里，从不在 project 里
    for pj in (f, g):
        assert [i["doc_id"] for i in _candidates(client, pj, "unknown")["items"]] == ["d_x"]
        assert _candidates(client, pj, "project")["items"] == []


def test_pointers_carry_the_revision_they_were_validated_against(client, tmp_path):
    """Codex P1:F 读到指针后 G 又保存了同一个槽位——指针必须带校验时的 revision，前端取正文时比对。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    r1 = _put(client, "d_x", _doc(1), f).get_json()["revision"]
    _put_last(client, "d_x", "X", pj=f)
    _put_last(client, "d_y", "Y", pj=f)  # pending，槽位还不在
    view = _get_session(client, f)
    assert view["last"]["revision"] == r1 == _rev("d_x")
    assert view["pending"]["revision"] is None and view["prev"] is None
    # G 带着合法 base_revision 保存同一个槽位：F 再读，要么是新 revision，要么被过滤，绝不是旧 revision 配新正文
    r2 = _put(client, "d_x", _doc(2, "G 的"), g, base=r1).get_json()["revision"]
    after = _get_session(client, f)["last"]
    assert after is None or after["revision"] == r2 == _rev("d_x")
    assert layoutsession.view_for(layoutsession.project_key(f["figures_dir"]))["last"] is None or (
        "revision"
        not in layoutsession.view_for(layoutsession.project_key(f["figures_dir"]))["last"]
    )


def test_candidates_listing_failure_is_unavailable_not_an_empty_page(client, tmp_path, monkeypatch):
    """槽位目录存在但列不出来（权限 / 网络盘）：不能回一页空的成功——前端会当成空项目。目录不存在才是空页。"""
    a = _project(client, tmp_path, "a", default=True)
    assert _candidates(client, a)["items"] == []  # 目录还不存在：空页（首次）
    _put(client, "d_1", _doc(), a)

    def boom(path):
        raise PermissionError(13, "denied")

    with monkeypatch.context() as mp:
        mp.setattr(m.os, "scandir", boom)
        r = client.get("/api/layout-session/candidates?scope=project", headers=_pj(a["id"]))
    assert r.status_code == 503 and r.get_json()["code"] == "candidates_unavailable"
    assert [i["doc_id"] for i in _candidates(client, a)["items"]] == ["d_1"]


def test_candidates_a_transient_stat_failure_yields_an_invalid_item_not_a_missing_one(
    client, tmp_path, monkeypatch
):
    a = _project(client, tmp_path, "a", default=True)
    _put(client, "d_ok", _doc(), a)
    _put(client, "d_flaky", _doc(), a)
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self._e = e
            self.name, self.path = e.name, e.path

        def is_symlink(self):
            return self._e.is_symlink()

        def is_file(self, **kw):
            return self._e.is_file(**kw)

        def stat(self, **kw):
            if self.name == "d_flaky.json":
                raise PermissionError(13, "transient")
            return self._e.stat(**kw)

    class Scan:
        def __init__(self, path):
            self._it = real(path)

        def __enter__(self):
            return (Entry(e) for e in self._it.__enter__())

        def __exit__(self, *exc):
            return self._it.__exit__(*exc)

    with monkeypatch.context() as mp:
        mp.setattr(m.os, "scandir", Scan)
        items = {i["doc_id"]: i for i in _candidates(client, a)["items"]}
    assert set(items) == {"d_ok", "d_flaky"}
    flaky = items["d_flaky"]
    assert flaky["status"] == "invalid" and flaky["revision"] is None
    assert flaky["canvases"] is None and flaky["objects"] is None
    assert items["d_ok"]["status"] == "ok"


def test_cross_process_removal_between_validate_and_publish_leaves_pending_not_a_dangling_last(
    client, monkeypatch
):
    """另一个进程（只持跨进程会话锁、不持本进程的文档锁）在「验槽位」之后、「发布 last」之前删掉槽位：
    发布时在会话锁里再看一眼，槽位没了就只记 pending。"""
    _put(client, "d_keep", _doc())
    _put_last(client, "d_keep", "K")
    _put(client, "d_t", _doc(2))
    real = m._slot_signature
    calls = []

    def signature_then_other_process_removes(path):
        sig = real(path)
        calls.append(sig)
        if len(calls) == 2:  # 验证已通过、签名已取好，马上要发布
            layoutsession.delete_slot("d_t", lambda: (_slot("d_t").unlink(), True)[1])
        return sig

    monkeypatch.setattr(m, "_slot_signature", signature_then_other_process_removes)
    body = _put_last(client, "d_t", "T").get_json()
    assert body["state"] == "pending" and body["last"]["doc_id"] == "d_keep"
    assert not _slot("d_t").exists()
    assert _get_session(client)["last"]["doc_id"] == "d_keep", "last 指着一个被删掉的槽位"


def test_slot_removal_and_publish_are_serialized_by_the_session_lock(client, tmp_path, monkeypatch):
    """DELETE / 教程重置 / 清理的 unlink 都发生在会话锁里（否则另一个进程的发布与删除会交错）。"""
    held: list[bool] = []
    real_unlink = Path.unlink

    def watching(self, *a, **k):
        if self.name.endswith(".json") and self.parent == m.AUTOSAVE_DIR:
            held.append(layoutsession._LOCK._thread.locked())
        return real_unlink(self, *a, **k)

    # DELETE
    _put(client, "d_del", _doc())
    # 清理（上限在槽位都建好之后才收紧，免得建的时候就被裁了）
    for i, d in enumerate(["d_old", "d_mid"]):
        _put(client, d, _doc(i))
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    monkeypatch.setattr(Path, "unlink", watching)
    assert client.delete("/api/autosave/d_del").status_code == 200
    _put(client, "d_new", _doc(9))
    assert not _slot("d_old").exists()
    assert held and all(held), f"有槽位在会话锁之外被删：{held}"
    assert len(held) >= 2


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_tutorial_reset_never_unlinks_the_slot_outside_the_session_lock(client, monkeypatch):
    """别的进程占着跨进程会话锁：重置不绕过它硬删槽位（否则已通过 slot_check 的 set_last 会发布悬空 last），
    如实报失败（tutorial_reset_incomplete），槽位原样留着；锁放开后再重置就成功。"""
    import fcntl

    monkeypatch.setattr(m.engine_pool, "build", lambda *a, **k: pytest.fail("不许执行脚本"))
    monkeypatch.setattr(layoutsession, "LOCK_TIMEOUT_S", 0.1)
    monkeypatch.setattr(m, "_TUTORIAL_CLEAR_PAUSE_S", 0)
    tut = client.post("/api/tutorial/open", json={"default": False}).get_json()
    pid, doc_id = tut["project"]["id"], tut["tutorial"]["document_id"]
    assert _put(client, doc_id, _doc(), {"id": pid}).status_code == 200
    slot_bytes = _slot(doc_id).read_bytes()
    layoutsession.lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(layoutsession.lock_path(), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = client.post("/api/tutorial/reset", json={})
        assert r.status_code == 500 and r.get_json()["code"] == "tutorial_reset_incomplete"
        assert _slot(doc_id).read_bytes() == slot_bytes, "槽位在会话锁之外被删了"
    finally:
        os.close(fd)
    r = client.post("/api/tutorial/reset", json={})
    assert r.status_code == 200 and r.get_json()["reset"] is True
    assert not _slot(doc_id).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_tutorial_first_open_retries_deferred_slot_cleanup(client, monkeypatch):
    """首次副本建好但文档锁被占：报未完成，下一次 created=False 仍清掉旧槽位才打开。"""
    import fcntl

    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.1)
    monkeypatch.setattr(m, "_TUTORIAL_CLEAR_PAUSE_S", 0)
    doc_id = m.engine_tutorial.tutorial_metadata()["document_id"]
    slot = _slot(doc_id)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps(_doc(1, "旧教程排版")), encoding="utf-8")
    old_bytes = slot.read_bytes()
    lock_file = layoutsession.doclock_path(m._lock_stripe(slot))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    marker = m.engine_config.data_dir() / "state" / "tutorial-cleanup-pending"
    try:
        first = client.post("/api/tutorial/open", json={})
        assert first.status_code == 500 and first.get_json()["code"] == "tutorial_reset_incomplete"
        assert slot.read_bytes() == old_bytes
        assert marker.is_file()
    finally:
        os.close(fd)
    second = client.post("/api/tutorial/open", json={})
    assert second.status_code == 200 and second.get_json()["created"] is False
    assert not slot.exists() and not marker.exists()


def test_tutorial_cleanup_intent_precedes_publishing_new_copy(client, monkeypatch):
    """副本发布后、端点来得及清槽位前退出：下次 created=False 仍能重试。"""
    doc_id = m.engine_tutorial.tutorial_metadata()["document_id"]
    slot = _slot(doc_id)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps(_doc(1, "旧教程排版")), encoding="utf-8")
    marker = m.engine_tutorial.cleanup_pending_path()
    real_ensure = m.engine_tutorial.ensure_tutorial_copy

    def publish_then_exit(*args, **kwargs):
        copy = real_ensure(*args, **kwargs)
        assert copy.created and copy.path.is_dir()
        assert marker.is_file(), "发布副本之前必须持久记录清理意图"
        raise RuntimeError("模拟副本发布后进程退出")

    monkeypatch.setattr(m.engine_tutorial, "ensure_tutorial_copy", publish_then_exit)
    interrupted = client.post("/api/tutorial/open", json={})
    assert interrupted.status_code == 500
    monkeypatch.setattr(m.engine_tutorial, "ensure_tutorial_copy", real_ensure)
    assert slot.exists() and marker.exists()
    again = client.post("/api/tutorial/open", json={})
    assert again.status_code == 200 and again.get_json()["created"] is False
    assert not slot.exists() and not marker.exists()


def test_tutorial_reset_completes_a_pending_cleanup_without_clearing_future_progress(client):
    """发布后中断留下的标记由重置清掉，后续正常打开不能再次删除新进度。"""
    copy = m.engine_tutorial.ensure_tutorial_copy()  # 模拟发布后、端点清理前退出
    assert copy.created
    marker = m.engine_tutorial.cleanup_pending_path()
    doc_id = copy.metadata["document_id"]
    slot = _slot(doc_id)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps(_doc(1, "旧教程排版")), encoding="utf-8")
    assert marker.exists()
    reset = client.post("/api/tutorial/reset", json={})
    assert reset.status_code == 200 and not marker.exists() and not slot.exists()
    slot.write_text(json.dumps(_doc(2, "新教程进度")), encoding="utf-8")
    again = client.post("/api/tutorial/open", json={})
    assert again.status_code == 200 and again.get_json()["created"] is False
    assert slot.exists(), "已完成的清理意图不能在下次打开时再次删除用户进度"


def test_tutorial_reset_publishes_cleanup_intent_before_replacement(client, monkeypatch):
    """重置副本发布后、端点清理前退出，下一次打开先清旧固定 ID 槽位。"""
    first = client.post("/api/tutorial/open", json={}).get_json()
    doc_id = first["tutorial"]["document_id"]
    slot = _slot(doc_id)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps(_doc(1, "重置前的排版")), encoding="utf-8")
    marker = m.engine_tutorial.cleanup_pending_path()
    real_ensure = m.engine_tutorial.ensure_tutorial_copy

    def replace_then_exit(*args, **kwargs):
        copy = real_ensure(*args, **kwargs)
        assert kwargs.get("reset") and copy.reset and marker.is_file()
        raise RuntimeError("模拟重置发布后进程退出")

    monkeypatch.setattr(m.engine_tutorial, "ensure_tutorial_copy", replace_then_exit)
    interrupted = client.post("/api/tutorial/reset", json={})
    assert interrupted.status_code == 500
    monkeypatch.setattr(m.engine_tutorial, "ensure_tutorial_copy", real_ensure)
    assert slot.exists() and marker.exists()
    reopened = client.post("/api/tutorial/open", json={})
    assert reopened.status_code == 200 and reopened.get_json()["created"] is False
    assert not slot.exists() and not marker.exists()


def test_failed_tutorial_reset_does_not_leave_cleanup_intent_for_old_copy(client, monkeypatch):
    """新目录放置失败并回滚旧副本：下一次打开保留旧排版进度。"""
    first = client.post("/api/tutorial/open", json={}).get_json()
    slot = _slot(first["tutorial"]["document_id"])
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps(_doc(1, "仍属于旧副本")), encoding="utf-8")
    old_bytes = slot.read_bytes()
    marker = m.engine_tutorial.cleanup_pending_path()
    dest = Path(first["project"]["figures_dir"])
    real_rename = os.rename

    def fail_publish(src, dst, *args, **kwargs):
        if Path(dst) == dest and Path(src).name.endswith(".tmp"):
            raise OSError(5, "无法放置新副本")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(m.engine_tutorial.os, "rename", fail_publish)
    failed = client.post("/api/tutorial/reset", json={})
    assert failed.status_code != 200 and not marker.exists()
    monkeypatch.setattr(m.engine_tutorial.os, "rename", real_rename)
    reopened = client.post("/api/tutorial/open", json={})
    assert reopened.status_code == 200 and slot.read_bytes() == old_bytes


_SAVE_CHILD = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
tag, proj, base, go, ready, data = sys.argv[2:8]
from tavotto import app as m
from tavotto.engine import atomicio
real = atomicio.write_json
def slow(path, obj, *, indent=None):  # 撑开「判冲突 → 写」之间的缝：两个进程都先判完才有人落盘
    if "_autosave" in str(path):
        time.sleep(0.5)
    return real(path, obj, indent=indent)
atomicio.write_json = slow
m.app.config["TESTING"] = True
c = m.app.test_client()
pid = c.post("/api/projects/open", json={"path": proj, "default": True}).get_json()["id"]
open(ready, "w").write("1")
while not os.path.exists(go):
    time.sleep(0.001)
doc = json.loads(sys.argv[8]); doc["project"]["name"] = "from " + tag
r = c.put("/api/autosave/d_x?base_revision=" + base, json=doc, headers={"X-Tavotto-Project": pid})
print(json.dumps({"tag": tag, "status": r.status_code, "revision": (r.get_json() or {}).get("revision")}))
sys.stdout.flush()
os._exit(0)
"""


def test_two_real_processes_saving_the_same_slot_from_one_baseline_exactly_one_wins(tmp_path):
    """两个真后端进程共用一个数据目录、从同一个 base_revision 保存同一个 doc id：槽位文档锁是跨进程的，
    恰好一个 200、另一个 409，归属与 owner_revisions 对着最终正文。Windows 腿（`msvcrt`）留给 CI。"""
    data = tmp_path / "data"
    slot = data / "layouts" / documents.AUTOSAVE_DIRNAME / "d_x.json"
    atomicio.write_json(slot, _doc(1, "seed"))
    base = atomicio.content_revision(slot)
    projects = {}
    for tag in ("F", "G"):
        projects[tag] = tmp_path / f"proj{tag}"
        projects[tag].mkdir()
    go = tmp_path / "go"
    env = {**os.environ, "TAVOTTO_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SAVE_CHILD,
                str(SRC),
                tag,
                str(projects[tag]),
                base,
                str(go),
                str(tmp_path / f"ready{tag}"),
                str(data),
                json.dumps(_doc(2)),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for tag in ("F", "G")
    ]
    deadline = time.monotonic() + 120
    while not all((tmp_path / f"ready{t}").exists() for t in ("F", "G")):
        assert time.monotonic() < deadline and all(p.poll() is None for p in procs), [
            p.stderr.read().decode() for p in procs if p.poll() is not None
        ]
        time.sleep(0.05)
    go.write_text("go")
    results = {}
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
        row = json.loads(out.decode().strip().splitlines()[-1])
        results[row["tag"]] = row
    statuses = sorted(r["status"] for r in results.values())
    assert statuses == [200, 409], f"跨进程没有互斥：{results}"
    winner = next(t for t, r in results.items() if r["status"] == 200)
    final = atomicio.content_revision(slot)
    assert final == results[winner]["revision"]
    assert json.loads(slot.read_text(encoding="utf-8"))["project"]["name"] == f"from {winner}"
    state = json.loads((data / "state" / "layout-sessions.json").read_text(encoding="utf-8"))
    assert state["owners"]["d_x"] == layoutsession.project_key(projects[winner])
    assert state["owner_revisions"]["d_x"] == final


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_document_lock_held_by_another_process_times_out_with_503_and_writes_nothing(
    client, monkeypatch
):
    import fcntl

    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.3)
    slot = m._autosave_path("d_held")
    lock_file = layoutsession.doclock_path(m._lock_stripe(slot))
    lock_file.parent.mkdir(parents=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = _put(client, "d_held", _doc())
        assert r.status_code == 503 and r.get_json()["code"] == "session_unavailable"
        assert not slot.exists(), "拿不到槽位锁还是写了正文"
        assert client.delete("/api/autosave/d_held").status_code == 503
    finally:
        os.close(fd)
    assert _put(client, "d_held", _doc()).status_code == 200


def _enolcks(monkeypatch, only_session: bool = False):
    import errno

    real_try = layoutsession._try_file_lock
    if only_session:
        layoutsession.lock_path().parent.mkdir(parents=True, exist_ok=True)
        layoutsession.lock_path().touch()

    def refuse(fd):
        if only_session:
            same = os.fstat(fd).st_ino == os.stat(layoutsession.lock_path()).st_ino
            if not same:
                return real_try(fd)
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(layoutsession, "_try_file_lock", refuse)


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX 的 errno / inode 判断；Windows 腿留给 CI"
)
def test_filesystem_without_advisory_locks_fails_closed_not_to_thread_only_locking(
    client, monkeypatch
):
    """文件系统拒绝咨询锁（网络盘 ENOLCK）：槽位文档锁拿不到 → 保存 503、一个字节没写；绝不退回只有进程内的锁。"""
    _put(client, "d_ok", _doc(1))
    before = _slot("d_ok").read_bytes()
    _enolcks(monkeypatch)
    r = _put(client, "d_ok", _doc(2, "changed"), base=_rev("d_ok"))
    assert r.status_code == 503 and r.get_json()["code"] == "session_unavailable"
    assert _slot("d_ok").read_bytes() == before
    r = _put(client, "d_new", _doc())
    assert r.status_code == 503 and not _slot("d_new").exists()
    assert client.delete("/api/autosave/d_ok").status_code == 503 and _slot("d_ok").exists()
    assert _put_last(client, "d_ok").status_code == 503
    with pytest.raises(layoutsession.SessionStateUnavailable):
        layoutsession.record_owner("d_x", None)
    view = _get_session(client)  # 读不到可信状态 = degraded，不是首次空
    assert view["index"] == "degraded"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX 的 errno / inode 判断；Windows 腿留给 CI"
)
def test_session_lock_unsupported_keeps_the_body_save_and_reports_metadata_failed(
    client, tmp_path, monkeypatch
):
    a = _project(client, tmp_path, "a", default=True)
    _enolcks(monkeypatch, only_session=True)  # 槽位锁文件能锁，会话锁文件被拒
    r = _put(client, "d_1", _doc(1), a)
    assert r.status_code == 200 and r.get_json()["metadata"] == "failed"
    assert r.get_json()["revision"] == _rev("d_1")
    assert _put_last(client, "d_1", pj=a).status_code == 503
    assert not layoutsession.state_path().exists()


def test_corrupt_bytes_that_cannot_be_preserved_close_the_door_until_they_can(
    client, tmp_path, monkeypatch
):
    """损坏的会话文件、没有可用 bak，而损坏副本又存不下：下一次写入会把唯一一份原字节替换掉——所以拒写，
    直到某次读取真的把它存下来。"""
    a = _project(client, tmp_path, "a", default=True)
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    raw = b'{"projects": {"/a": '
    path.write_bytes(raw)
    real = atomicio.write_bytes

    def refuse_copy(p, data, **kw):
        if Path(p).name.startswith(layoutsession.CORRUPT_PREFIX):
            raise atomicio.AtomicWriteError("write_failed", "disk full", Path(p))
        return real(p, data, **kw)

    with monkeypatch.context() as mp:
        mp.setattr(atomicio, "write_bytes", refuse_copy)
        r = _put(client, "d_1", _doc(), a)
        assert r.status_code == 200 and r.get_json()["metadata"] == "failed"
        assert _put_last(client, "d_1", pj=a).status_code == 503
        with pytest.raises(layoutsession.SessionStateUnavailable):
            layoutsession.commit_slot("d_2", "/a", "rev")
        assert path.read_bytes() == raw, "副本没存下就把原字节替换了"
    # 副本存得下了：这次读取保存原字节，门重新打开，写入放行
    r = _put(client, "d_2", _doc(), a)
    assert r.get_json()["metadata"] == "ok"
    assert [c.read_bytes() for c in path.parent.glob("layout-sessions.corrupt-*.json")] == [raw]


def test_owner_response_carries_the_revision_it_judged(client, tmp_path):
    """Codex P1:判断之后别的项目可能又保存了这个槽位——`/owner` 交回它判断所依据的那份正文的 revision。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")

    def raw(pj, doc_id):
        url = f"/api/layout-session/owner?doc_id={doc_id}"
        return client.get(url, headers=_pj(pj["id"])).get_json()

    r1 = _put(client, "d_x", _doc(1), f).get_json()["revision"]
    body = raw(f, "d_x")
    assert body["owner"] == "this" and body["revision"] == r1 == _rev("d_x")
    r2 = _put(client, "d_x", _doc(2), g, base=r1).get_json()["revision"]
    after = raw(f, "d_x")
    assert after["owner"] == "other" and after["revision"] == r2 != r1
    assert raw(g, "d_nothing") == {
        "owner": "unknown",
        "evidence": None,
        "verified": False,
        "revision": None,
    }
    root = Path(f["figures_dir"])  # 素材旁证路径的 revision 也是同一份字节的
    (root / "a.pdf").write_bytes(b"%PDF-1.4")
    panel = {"id": "p", "type": "panel", "fileId": "a.pdf", "x": 0, "y": 0, "w": 1, "h": 1}
    _slot("d_assets").write_text(json.dumps(_doc(1, "a", [panel])))
    got = raw(f, "d_assets")
    assert got["evidence"] == "assets" and got["revision"] == _rev("d_assets")


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_tutorial_reset_with_the_slot_document_lock_held_reopens_the_new_copy(client, monkeypatch):
    """目录已换、项目还关着的时候，槽位文档锁等不到：和会话锁等不到同一档——重新打开新副本，回
    tutorial_reset_incomplete，不是泛泛的 503 把项目留在关闭状态。"""
    import fcntl

    monkeypatch.setattr(m.engine_pool, "build", lambda *a, **k: pytest.fail("不许执行脚本"))
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(m, "_TUTORIAL_CLEAR_PAUSE_S", 0)
    tut = client.post("/api/tutorial/open", json={"default": False}).get_json()
    doc_id = tut["tutorial"]["document_id"]
    assert _put(client, doc_id, _doc(), {"id": tut["project"]["id"]}).status_code == 200
    slot_bytes = _slot(doc_id).read_bytes()
    lock_file = layoutsession.doclock_path(m._lock_stripe(m._autosave_path(doc_id)))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = client.post("/api/tutorial/reset", json={})
        assert r.status_code == 500 and r.get_json()["code"] == "tutorial_reset_incomplete"
        assert m._tutorial_ctx() is not None, "项目被留在了关闭状态"
        assert _slot(doc_id).read_bytes() == slot_bytes
    finally:
        os.close(fd)
    # 新副本虽然已重新打开，清理标记仍在：前端不能把之后的新进度写进
    # 同一个固定 ID 槽位，再被下一次 /tutorial/open 当旧排版删掉。
    attempted = _put(client, doc_id, _doc(2, "重置后的新进度"), {"id": tut["project"]["id"]})
    assert attempted.status_code == 500
    assert attempted.get_json()["code"] == "tutorial_reset_incomplete"
    assert _slot(doc_id).read_bytes() == slot_bytes
    reopened = client.post("/api/tutorial/open", json={})
    assert reopened.status_code == 200
    assert not _slot(doc_id).exists()
    assert (
        _put(client, doc_id, _doc(3, "已清理后可保存"), {"id": tut["project"]["id"]}).status_code
        == 200
    )


@pytest.mark.parametrize("damage", ["corrupt", "future"])
def test_degraded_session_state_never_infers_ownership_from_assets_or_versions(
    client, tmp_path, damage
):
    """会话状态读不到可信内容时，空的 owners 不是「没人认领」：/owner 与候选发现都不翻 versions / assets 旁证，
    否则别的项目的槽位会被报成 this。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    root = Path(g["figures_dir"])
    (root / "a.pdf").write_bytes(b"%PDF-1.4")
    panel = {"id": "p", "type": "panel", "fileId": "a.pdf", "x": 0, "y": 0, "w": 1, "h": 1}
    assert (
        _put(client, "d_f", _doc(1, "F 的", [panel]), f).status_code == 200
    )  # F 的槽位，素材名恰在 G 里也有
    versions = root / "tavottofile" / "versions"
    versions.mkdir(parents=True)
    (versions / "d_f.json").write_text("{}", encoding="utf-8")
    # 状态正常时 G 看到的是 owners=other
    assert _owner(client, g, "d_f")["owner"] == "other"
    path = layoutsession.state_path()
    layoutsession.backup_path().unlink()
    path.write_bytes(b"{broken" if damage == "corrupt" else b'{"version": 99, "owners": {}}')
    body = client.get("/api/layout-session/owner?doc_id=d_f", headers=_pj(g["id"])).get_json()
    assert body == {
        "owner": "unknown",
        "evidence": None,
        "verified": False,
        "revision": _rev("d_f"),
    }
    assert _candidates(client, g, "project")["items"] == []
    unknown = _candidates(client, g, "unknown")
    assert [i["doc_id"] for i in unknown["items"]] == ["d_f"] and unknown["index"] == "degraded"
    assert unknown["items"][0]["owner"] == "unknown" and unknown["items"][0]["evidence"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接别名需要特权；Windows 腿留给 CI")
def test_lock_identity_does_not_depend_on_how_the_data_dir_or_slot_is_spelled(
    data_dir, tmp_path, monkeypatch
):
    """两个进程经符号链接别名 / 大小写不同的写法走到同一个槽位：同一个条带、同一个锁文件。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    alias = tmp_path / "alias"
    alias.symlink_to(data_dir, target_is_directory=True)
    real_slot = data_dir / "layouts" / "_autosave" / "d_x.json"
    alias_slot = alias / "layouts" / "_autosave" / "D_X.json"  # 别名 + 大小写不同
    assert m._document_lock(real_slot)._stripe == m._document_lock(alias_slot)._stripe
    stripe = m._document_lock(real_slot)._stripe
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data_dir))
    via_real = layoutsession.doclock_path(stripe), layoutsession.lock_path()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(alias))
    via_alias = layoutsession.doclock_path(stripe), layoutsession.lock_path()
    assert via_real == via_alias, "别名写法打开了另一个锁文件"


def test_candidates_scan_cap_is_reported_as_truncated_not_as_complete(
    client, tmp_path, monkeypatch
):
    a = _project(client, tmp_path, "a", default=True)
    for i in range(5):
        _put(client, f"d_{i}", _doc(i), a)
    full = _candidates(client, a)
    assert full["truncated"] is False and len(full["items"]) == 5
    monkeypatch.setattr(m, "CANDIDATE_MAX_ENTRIES", 3)
    cut = _candidates(client, a)
    assert cut["truncated"] is True and cut["next_cursor"] is None  # 最后一页也不等于「列完了」
    assert len(cut["items"]) == 3
    monkeypatch.setattr(m, "CANDIDATE_MAX_ENTRIES", 5)  # 恰好等于上限：没有被截
    assert _candidates(client, a)["truncated"] is False


def test_owner_verdict_and_revision_come_from_one_snapshot_under_the_slot_lock(
    client, tmp_path, monkeypatch
):
    """Codex P1:`/owner` 读完会话状态（还没有归属）之后、读正文之前，另一个项目保存了同一个槽位并提交归属。
    状态与正文必须出自同一个快照：交回的 revision 就是判断所依据的那份正文的，绝不是 G 的新正文配旧状态。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    panel = {"id": "p", "type": "panel", "fileId": "a.pdf", "x": 0, "y": 0, "w": 1, "h": 1}
    for pj in (f, g):  # 同名素材在两个项目里都有
        (Path(pj["figures_dir"]) / "a.pdf").write_bytes(b"%PDF-1.4")
    m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    _slot("d_x").write_text(json.dumps(_doc(1, "旧", [panel])))  # 没有归属记录的升级前槽位
    r0 = _rev("d_x")
    real = layoutsession.read_readonly
    box: dict = {}

    def read_then_other_project_saves(*a, **k):
        out = real(*a, **k)
        if "t" not in box:
            box["t"] = threading.Thread(
                target=lambda: _put(m.app.test_client(), "d_x", _doc(2, "G 的新", [panel]), g)
            )
            box["t"].start()
            box["t"].join(0.5)  # 修复后 G 在槽位锁上排队；未修复时它此刻已写完正文并提交了归属
        return out

    monkeypatch.setattr(layoutsession, "read_readonly", read_then_other_project_saves)
    got = client.get("/api/layout-session/owner?doc_id=d_x", headers=_pj(f["id"])).get_json()
    box["t"].join(15)
    assert got["revision"] == r0, "判断用了 G 的新正文（revision 对不上它所依据的快照）"
    assert got["owner"] in ("this", "unknown") and got["revision"] != _rev("d_x")
    assert layoutsession.snapshot()["owners"]["d_x"] == layoutsession.project_key(g["figures_dir"])


def test_session_pointer_owner_and_revision_come_from_one_slot_snapshot(
    client, tmp_path, monkeypatch
):
    """旧 owners 无 owner_revisions：F 读状态后 G 覆盖同一槽位，不能把 G 的 revision 当作 F 的指针返回。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _put(client, "d_x", _doc(1, "F 的"), f)
    assert _put_last(client, "d_x", pj=f).status_code == 200
    state = layoutsession.snapshot()
    state["owner_revisions"].pop("d_x")  # 升级前旧记录按现状信任
    layoutsession._write(state)
    real_group = layoutsession._group_view
    crossed = False

    def group_then_other_project_saves(state, key):
        nonlocal crossed
        group = real_group(state, key)
        if not crossed:
            crossed = True
            result = _put(m.app.test_client(), "d_x", _doc(2, "G 的"), g, base=_rev("d_x"))
            assert result.status_code == 200
        return group

    monkeypatch.setattr(layoutsession, "_group_view", group_then_other_project_saves)
    view = _get_session(client, f)
    assert view["last"] is None, "旧 F 归属不能和 G 的新正文 revision 组成可恢复指针"
    assert layoutsession.snapshot()["owners"]["d_x"] == layoutsession.project_key(g["figures_dir"])


def test_session_view_retries_when_last_and_prev_change_mid_read(client, tmp_path, monkeypatch):
    """last=A/prev=B 读了一半时切到 C/A，回包不能拼成 A/A 并遗漏 C。"""
    f = _project(client, tmp_path, "projF", default=True)
    for doc_id in ("d_a", "d_b", "d_c"):
        _put(client, doc_id, _doc(1, doc_id), f)
    assert _put_last(client, "d_b", pj=f).status_code == 200
    assert _put_last(client, "d_a", pj=f).status_code == 200
    real_revision = m._slot_revision
    rotated = False

    def rotate_after_first_revision(doc_id):
        nonlocal rotated
        if doc_id == "d_a" and not rotated:
            rotated = True
            assert _put_last(m.app.test_client(), "d_c", pj=f).status_code == 200
        return real_revision(doc_id)

    monkeypatch.setattr(m, "_slot_revision", rotate_after_first_revision)
    view = _get_session(client, f)
    assert rotated
    assert view["index"] == "ok"
    assert view["last"]["doc_id"] == "d_c"
    assert view["prev"]["doc_id"] == "d_a"


def test_candidate_items_are_classified_from_a_per_item_snapshot(client, tmp_path, monkeypatch):
    """候选条目的状态与正文在它自己的槽位锁里读：页级快照之后别的项目保存了这个槽位，条目也不能拿新正文配旧状态。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    panel = {"id": "p", "type": "panel", "fileId": "a.pdf", "x": 0, "y": 0, "w": 1, "h": 1}
    for pj in (f, g):
        (Path(pj["figures_dir"]) / "a.pdf").write_bytes(b"%PDF-1.4")
    m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    _slot("d_x").write_text(json.dumps(_doc(1, "旧", [panel])))
    r0 = _rev("d_x")
    real = layoutsession.read_readonly
    calls: list[int] = []
    box: dict = {}

    def hook(*a, **k):
        out = real(*a, **k)
        calls.append(1)
        if len(calls) == 2:  # 第 1 次是页级快照，第 2 次是条目在自己锁里的那次读
            box["t"] = threading.Thread(
                target=lambda: _put(m.app.test_client(), "d_x", _doc(2, "G 的新", [panel]), g)
            )
            box["t"].start()
            box["t"].join(0.5)
        return out

    monkeypatch.setattr(layoutsession, "read_readonly", hook)
    items = _candidates(client, f)["items"]
    box["t"].join(15)
    assert [i["doc_id"] for i in items] == ["d_x"]
    assert items[0]["revision"] == r0 and items[0]["name"] == "旧"


def test_candidate_page_reports_degradation_seen_by_a_row(client, tmp_path, monkeypatch):
    """页级状态还好、逐行读时索引变坏：project 空页必须带 degraded，恢复入口才会给重试。"""
    f = _project(client, tmp_path, "projF", default=True)
    _put(client, "d_x", _doc(1), f)
    real = layoutsession.read_readonly
    calls = 0

    def page_ok_row_degraded():
        nonlocal calls
        calls += 1
        state, status = real()
        return state, layoutsession.STATUS_DEGRADED if calls == 2 else status

    monkeypatch.setattr(layoutsession, "read_readonly", page_ok_row_degraded)
    page = _candidates(client, f, "project")
    assert calls >= 2
    assert page["items"] == [] and page["index"] == "degraded"


def test_lock_stripe_ignores_unicode_normalization_and_case():
    """macOS 上组合 / 分解写法是同一个文件，大小写不敏感卷上大小写写法也是：它们必须落在同一个条带。"""
    nfc = "café.json"
    nfd = "café.json"
    assert nfc != nfd
    d = Path("/data/layouts")
    stripes = {m._lock_stripe(d / n) for n in (nfc, nfd, nfc.upper(), nfd.upper(), "CAFÉ.json")}
    assert len(stripes) == 1
    assert m._lock_stripe(d / "straße.json") == m._lock_stripe(d / "STRASSE.json")


def test_candidate_cursor_marks_the_scan_incomplete_when_the_directory_changed_between_pages(
    client, tmp_path
):
    """游标按 (mtime, 名字) 排；翻页期间一个还没翻到的行被自动保存，会跳到游标前面被漏掉。
    游标带着第一页的目录指纹，后面的页对不上就标 truncated（仍返回这一页）。"""
    a = _project(client, tmp_path, "a", default=True)
    ids = [f"d_{i}" for i in range(6)]
    for i, d in enumerate(ids):
        _put(client, d, _doc(i), a)
        os.utime(_slot(d), ns=(0, (i + 1) * 10**9))
    # 稳定目录：每一页都不截断
    first = _candidates(client, a, limit=2)
    assert first["truncated"] is False and first["next_cursor"]
    second = _candidates(client, a, limit=2, cursor=first["next_cursor"])
    assert second["truncated"] is False
    # 第一页之后，最老的（还没翻到的）d_0 被保存 → 它现在是最新的，跳到了游标前面
    first = _candidates(client, a, limit=2)
    assert [i["doc_id"] for i in first["items"]] == ["d_5", "d_4"]
    assert _put(client, "d_0", _doc(9), a, base=_rev("d_0")).status_code == 200
    rest, cursor = [], first["next_cursor"]
    flags = []
    while cursor:
        page = _candidates(client, a, limit=2, cursor=cursor)
        rest += [i["doc_id"] for i in page["items"]]
        flags.append(page["truncated"])
        cursor = page["next_cursor"]
    assert "d_0" not in rest  # 确实被漏了——所以必须标记
    assert flags and all(flags), "目录变了却没标 truncated，前端会以为扫描是完整的"


def _future_bak(key: str) -> bytes:
    return json.dumps(
        {
            "version": 99,
            "projects": {key: {"last": {"doc_id": "d_a", "name": "A", "at": 1}}},
            "owners": {"d_a": key},
        }
    ).encode()


def test_corrupt_primary_with_a_future_format_backup_is_read_only(client, tmp_path):
    """主文件坏、bak 是更新版本写的：不能用 v1 的空状态写回，更不能接着覆盖那份唯一完整的 bak。"""
    a = _project(client, tmp_path, "a", default=True)
    key = layoutsession.project_key(a["figures_dir"])
    path, bak = layoutsession.state_path(), layoutsession.backup_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{broken")
    bak.write_bytes(_future_bak(key))
    bak_bytes = bak.read_bytes()
    r = _put(client, "d_1", _doc(), a)
    assert r.status_code == 200 and r.get_json()["metadata"] == "failed"
    assert r.get_json()["revision"] == _rev("d_1")
    assert _put_last(client, "d_1", pj=a).status_code == 503
    with pytest.raises(layoutsession.SessionStateUnavailable):
        layoutsession.commit_slot("d_2", key, "rev")
    assert _get_session(client, a) == {
        "last": None,
        "prev": None,
        "pending": None,
        "index": "degraded",
    }
    assert bak.read_bytes() == bak_bytes, "未来格式的备份被覆盖了"
    assert path.read_bytes() == b"{broken"


def test_other_fallbacks_past_an_uninterpretable_file_also_refuse_to_write(client, tmp_path):
    """扫一遍：主文件不在而 bak 来自未来、bak 读不动；；导出默认值来自未来。"""
    a = _project(client, tmp_path, "a", default=True)
    key = layoutsession.project_key(a["figures_dir"])
    path, bak = layoutsession.state_path(), layoutsession.backup_path()
    path.parent.mkdir(parents=True)
    # 1) 主文件不在、好的 v1 bak 在：主文件被删就是想重来，按首次处理（写入放行）
    good = {"version": 1, "owners": {"d_a": key}, "projects": {}}
    bak.write_text(json.dumps(good), encoding="utf-8")
    assert layoutsession.read_status() == "missing"
    # 2) 主文件不在、bak 来自未来：只读
    bak.write_bytes(_future_bak(key))
    with pytest.raises(layoutsession.SessionStateUnavailable):
        layoutsession.record_owner("d_c", key)
    assert bak.read_bytes() == _future_bak(key)
    # 3) 主文件坏、bak 存在却读不动：只读，不当没有
    path.write_bytes(b"{broken")
    real = Path.read_bytes

    def flaky(self):
        if self.name == layoutsession.FILENAME + layoutsession.BACKUP_SUFFIX:
            raise PermissionError(13, "denied")
        return real(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes", flaky)
        with pytest.raises(layoutsession.SessionStateUnavailable):
            layoutsession.record_owner("d_c", key)
    # 4) 导出默认值来自未来 / 读不动：不整份替换
    exp = layoutsession.export_defaults_path()
    exp.write_text(json.dumps({"version": 99, "defaults": {"dpi": "2400"}}), encoding="utf-8")
    assert client.put("/api/preferences/export-defaults", json={"dpi": "1200"}).status_code == 503
    assert json.loads(exp.read_text(encoding="utf-8"))["version"] == 99


def test_primary_and_bak_both_corrupt_in_the_same_millisecond_keep_two_distinct_snapshots(
    data_dir, monkeypatch
):
    """Codex P1:同一毫秒里主文件和 bak 都读不懂、字节不同：两份副本各自存在，字节都等于各自的来源。"""
    path, bak = layoutsession.state_path(), layoutsession.backup_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"primary": broken')
    bak.write_bytes(b'{"bak": also broken')
    monkeypatch.setattr(layoutsession.time, "time", lambda: 1_700_000_000.123)
    layoutsession.record_owner("d_1", None)  # 触发一次加载（保存副本）再写入替换
    copies = sorted(path.parent.glob("layout-sessions.corrupt-*.json"))
    assert len(copies) == 2, [c.name for c in copies]
    assert sorted(c.read_bytes() for c in copies) == sorted(
        [b'{"primary": broken', b'{"bak": also broken']
    )
    assert any(c.name.endswith("-primary.json") for c in copies)
    assert any(c.name.endswith("-bak.json") for c in copies)


def test_corrupt_snapshot_is_not_reported_saved_unless_it_reads_back_identical(
    data_dir, monkeypatch
):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    raw = b"{broken"
    path.write_bytes(raw)
    real = atomicio.write_bytes

    def lossy(p, data, **kw):
        if Path(p).name.startswith(layoutsession.CORRUPT_PREFIX):
            data = data[:-1]  # 写进去的和来源不一样
        return real(p, data, **kw)

    monkeypatch.setattr(atomicio, "write_bytes", lossy)
    with pytest.raises(layoutsession.SessionStateUnavailable):
        layoutsession.record_owner("d_1", None)
    assert path.read_bytes() == raw


def test_snapshots_made_in_this_load_are_never_pruned_even_if_the_clock_went_backwards(
    data_dir, monkeypatch
):
    path, bak = layoutsession.state_path(), layoutsession.backup_path()
    path.parent.mkdir(parents=True)
    for i in range(3):  # 名字排在新副本之后的老副本（时钟回拨）
        (
            path.parent / f"layout-sessions.corrupt-99999999999{i}-aaaaaaaaaaaa-primary.json"
        ).write_bytes(b"old %d" % i)
    path.write_bytes(b"{primary broken")
    bak.write_bytes(b"{bak broken")
    monkeypatch.setattr(layoutsession.time, "time", lambda: 1_700_000_000.0)
    layoutsession.record_owner("d_1", None)
    contents = {c.read_bytes() for c in path.parent.glob("layout-sessions.corrupt-*.json")}
    assert {b"{primary broken", b"{bak broken"} <= contents, "本次加载存下的副本被裁掉了"


def test_durable_set_last_always_advances_at_even_for_the_same_id_and_name(data_dir, monkeypatch):
    """再次明确打开同一份排版：`at`（前端的排序令牌）必须严格前进并写盘，哪怕同一毫秒、时钟回拨。
    否则别的 profile 里还悬着的 pending 会被当成更新的而翻案。prev 不动，pending 清掉。"""
    clock = [1_700_000_000.0]
    monkeypatch.setattr(layoutsession.time, "time", lambda: clock[0])
    layoutsession.set_last(None, "d_prev", "P")
    first = layoutsession.set_last(None, "d_a", "A")["last"]["at"]
    path = layoutsession.state_path()
    ats = [first]
    for step in (0.0, 0.0, -5.0):  # 同一毫秒两次，再回拨
        clock[0] += step
        os.utime(path, ns=(0, 10**9))
        ats.append(layoutsession.set_last(None, "d_a", "A")["last"]["at"])
        assert path.stat().st_mtime_ns != 10**9, "同值的 durable 打开没有写盘"
    assert all(b > a for a, b in zip(ats, ats[1:])), ats
    group = layoutsession.snapshot()["no_project"]
    assert group["prev"]["doc_id"] == "d_prev" and "pending" not in group
    # pending 之后的 durable 重开：令牌比 pending 的更大，pending 被取代
    pend_at = layoutsession.set_last(None, "d_b", "B", slot_ok=False)["pending"]["at"]
    again = layoutsession.set_last(None, "d_a", "A")["last"]["at"]
    assert again > pend_at and "pending" not in layoutsession.snapshot()["no_project"]
    # 晋升（commit_slot）同样严格前进
    layoutsession.set_last(None, "d_c", "C", slot_ok=False)
    pend_c = layoutsession.snapshot()["no_project"]["pending"]["at"]
    layoutsession.commit_slot("d_c", None, "rev")
    assert layoutsession.snapshot()["no_project"]["last"]["at"] > pend_c


def test_tutorial_copy_publication_is_serialized_so_a_competitor_cannot_drop_our_marker(
    client, monkeypatch
):
    """Codex P2:两个后端进程同时首次打开教程。我们写完共享清理标记、还没 rename 时，竞争者发布了副本——
    没有锁时我们的 rename 失败分支会把共享标记删掉。锁让竞争者排在我们后面（复用我们发布的副本），标记保留。"""
    marker = m.engine_tutorial.cleanup_pending_path()
    real_write = atomicio.write_bytes
    state: dict = {"hooked": False}
    other: dict = {}

    def write_then_compete(path, data, **kw):
        out = real_write(path, data, **kw)
        if Path(path) == marker and not state["hooked"]:
            state["hooked"] = True

            def competitor():
                other["copy"] = m.engine_tutorial.ensure_tutorial_copy()

            state["t"] = threading.Thread(target=competitor)
            state["t"].start()
            state["t"].join(0.8)  # 有锁:竞争者在这里排队;没有锁:它已经把副本发布了
        return out

    monkeypatch.setattr(atomicio, "write_bytes", write_then_compete)
    mine = m.engine_tutorial.ensure_tutorial_copy()
    state["t"].join(30)
    assert mine.created and mine.path.is_dir()
    assert other["copy"].created is False, "竞争者没有排在后面复用我们发布的副本"
    assert marker.is_file(), "共享的清理意图标记被竞争者的回滚删掉了"


@pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者；Windows 腿留给 CI")
def test_tutorial_copy_never_proceeds_without_the_cross_process_lock(client, monkeypatch):
    import fcntl

    monkeypatch.setattr(m.engine_tutorial, "TUTORIAL_LOCK_TIMEOUT_S", 0.2)
    path = m.engine_tutorial.tutorial_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = client.post("/api/tutorial/open", json={})
        assert r.status_code == 409 and r.get_json()["code"] == "tutorial_locked"
        assert not m.engine_tutorial.cleanup_pending_path().exists()
    finally:
        os.close(fd)
    assert client.post("/api/tutorial/open", json={}).status_code == 200
