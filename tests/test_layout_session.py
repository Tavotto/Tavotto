"""会话状态以后端为准（#715 PR-B）：`engine/layoutsession.py` 与它的三组端点。

桌面版每次启动 sidecar 可能换端口 = 换 origin = 一份空的 localStorage，「上次打开的排版」与
导出默认值随之丢失。这里守后端那一半：

* 模块：读写往返、同值不重写、并发不丢更新、坏文件回退为空、文件不落在槽位 / 画布 / 项目目录；
* 端点：`GET /api/layout-session`、`PUT /api/layout-session/last` 按项目分组（没开项目单独一组，
  指名不存在的项目 409）；导出默认值的读写；三者都在会话认证之下；
* `PUT /api/autosave/<id>` 按 pj 记槽位归属；
* 槽位清理只动**记着归属**的槽位，没有归属的旧槽位与各组「上次开着的」那一份不删；
* 教程重置一并清掉教程项目的记录（ADR 0039 §5）。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import documents, layoutsession, project_watch as engine_watch

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


# ------------------------------- 模块 ---------------------------------------


def test_state_file_lives_in_data_dir_state_and_nowhere_else(data_dir):
    path = layoutsession.state_path()
    assert path == data_dir / "state" / "layout-sessions.json"
    # 不能放的位置：槽位目录（清理按文件名扫）、布局目录、项目 tavottofile/
    assert m.AUTOSAVE_DIR not in path.parents and m.LAYOUT_DIR not in path.parents
    assert documents.AUTOSAVE_DIRNAME not in path.parts and "tavottofile" not in path.parts


def test_last_roundtrip_per_project_and_no_project_group(data_dir):
    assert layoutsession.last_for("/a") is None
    layoutsession.set_last("/a", "d_a", "Fig A")
    layoutsession.set_last("/b", "d_b", "Fig B")
    layoutsession.set_last(None, "d_none", "Loose")
    assert layoutsession.last_for("/a")["doc_id"] == "d_a"
    assert layoutsession.last_for("/b")["name"] == "Fig B"
    assert layoutsession.last_for(None)["doc_id"] == "d_none"
    raw = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    assert raw["version"] == 1 and set(raw["projects"]) == {"/a", "/b"}


def test_same_value_does_not_rewrite_the_file(data_dir):
    layoutsession.set_last("/a", "d_a", "Fig A")
    layoutsession.record_owner("d_a", "/a")
    path = layoutsession.state_path()
    os.utime(path, ns=(0, 10**9))
    layoutsession.set_last("/a", "d_a", "Fig A")
    assert layoutsession.record_owner("d_a", "/a") is False
    assert path.stat().st_mtime_ns == 10**9, "同值又写了一遍——每次自动保存都会多 fsync 一个文件"
    assert layoutsession.record_owner("d_a", "/b") is True
    assert path.stat().st_mtime_ns != 10**9


def test_rejects_malformed_doc_ids(data_dir):
    with pytest.raises(ValueError):
        layoutsession.set_last("/a", "../evil", "x")
    with pytest.raises(ValueError):
        layoutsession.set_last("/a", "", "x")
    assert layoutsession.record_owner("../evil", "/a") is False


def test_concurrent_writers_lose_nothing(data_dir):
    ids = [f"d_{i}" for i in range(40)]
    barrier = threading.Barrier(len(ids))

    def work(doc_id):
        barrier.wait()
        layoutsession.record_owner(doc_id, f"/p/{doc_id}")

    threads = [threading.Thread(target=work, args=(d,)) for d in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert layoutsession.owners() == {d: f"/p/{d}" for d in ids}


@pytest.mark.parametrize(
    "content",
    [b"{not json", b'{"projects": {"/a": {"last": {"doc_id": "d", "at": NaN}}}}', b"[1, 2]"],
    ids=["not-json", "nan", "wrong-shape"],
)
def test_corrupt_file_reads_as_empty_and_next_write_replaces_it(data_dir, content):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    assert layoutsession.last_for("/a") is None
    assert layoutsession.owners() == {}
    layoutsession.set_last("/a", "d_a", "A")
    assert layoutsession.last_for("/a")["doc_id"] == "d_a"
    json.loads(path.read_text(encoding="utf-8"))  # 换成了合法 JSON


def test_bad_entries_are_dropped_good_ones_kept(data_dir):
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "projects": {"/a": {"last": {"doc_id": "d_a", "name": "A", "at": 5}}, "/b": 3},
                "owners": {"d_a": "/a", "../x": "/a", "d_n": None, "d_bad": 7},
            }
        ),
        encoding="utf-8",
    )
    assert layoutsession.last_for("/a") == {"doc_id": "d_a", "name": "A", "at": 5}
    assert layoutsession.owners() == {"d_a": "/a", "d_n": None}


def test_an_overflowing_timestamp_reads_as_a_bad_value_not_an_error(data_dir):
    """`1e400` 是合法 JSON，Python 解析成 inf；`int(inf)` 会抛 OverflowError——不能让它把每一次
    读写都打挂，按坏值（0）处理，下一次写整份替换（#719 Codex P2）。"""
    path = layoutsession.state_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"projects": {"/a": {"last": {"doc_id": "d_a", "name": "A", "at": 1e400}}}}',
        encoding="utf-8",
    )
    assert layoutsession.last_for("/a") == {"doc_id": "d_a", "name": "A", "at": 0}
    assert layoutsession.set_last("/a", "d_b", "B")["doc_id"] == "d_b"
    assert json.loads(path.read_text(encoding="utf-8"))["projects"]["/a"]["last"]["doc_id"] == "d_b"


def test_export_defaults_roundtrip_and_limits(data_dir):
    assert layoutsession.read_export_defaults() is None
    layoutsession.write_export_defaults({"dpi": "1200", "formats": ["pdf"]})
    assert layoutsession.read_export_defaults() == {"dpi": "1200", "formats": ["pdf"]}
    assert layoutsession.export_defaults_path().parent == data_dir / "state"
    for bad in ([1], "x", {"x": float("nan")}, {"x": "y" * 20000}):
        with pytest.raises(ValueError):
            layoutsession.write_export_defaults(bad)
    assert layoutsession.read_export_defaults() == {"dpi": "1200", "formats": ["pdf"]}


# ------------------------------- 端点 ---------------------------------------


def test_layout_session_endpoints_group_by_project(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    b = _project(client, tmp_path, "b")
    assert client.get("/api/layout-session", headers=_pj(a["id"])).get_json() == {"last": None}
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_a", "name": "A"}, headers=_pj(a["id"])
    )
    assert r.status_code == 200 and r.get_json()["last"]["doc_id"] == "d_a"
    client.put(
        "/api/layout-session/last", json={"doc_id": "d_b", "name": "B"}, headers=_pj(b["id"])
    )
    assert (
        client.get("/api/layout-session", headers=_pj(a["id"])).get_json()["last"]["doc_id"]
        == "d_a"
    )
    got = client.get(f"/api/layout-session?pj={b['id']}").get_json()["last"]
    assert got["doc_id"] == "d_b" and got["name"] == "B"
    # 回给前端的只有这三样，不带项目路径
    assert set(got) == {"doc_id", "name", "at"}
    # 键是规范化后的项目路径（与 `_project_id` 同一份判断），不是短 id
    snap = layoutsession.snapshot()
    assert set(snap["projects"]) == {
        layoutsession.project_key(a["figures_dir"]),
        layoutsession.project_key(b["figures_dir"]),
    }


def test_layout_session_without_any_project_is_its_own_group(client):
    assert client.get("/api/layout-session").get_json() == {"last": None}
    client.put("/api/layout-session/last", json={"doc_id": "d_loose", "name": "L"})
    assert client.get("/api/layout-session").get_json()["last"]["doc_id"] == "d_loose"
    assert layoutsession.snapshot()["projects"] == {}


def test_layout_session_names_a_missing_project_409(client):
    r = client.get("/api/layout-session", headers=_pj("nope"))
    assert r.status_code == 409 and r.get_json()["code"] == "no_project"
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_a", "name": "A"}, headers=_pj("nope")
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body", [{"doc_id": "../x", "name": "A"}, {"name": "A"}, {"doc_id": "d_a", "name": 3}, [1]]
)
def test_layout_session_put_rejects_bad_bodies(client, body):
    r = client.put("/api/layout-session/last", json=body)
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request"


def test_export_defaults_endpoints(client):
    assert client.get("/api/preferences/export-defaults").get_json() == {"defaults": None}
    r = client.put("/api/preferences/export-defaults", json={"dpi": "1200"})
    assert r.status_code == 200
    assert client.get("/api/preferences/export-defaults").get_json() == {
        "defaults": {"dpi": "1200"}
    }
    assert client.put("/api/preferences/export-defaults", json=[1]).status_code == 400


# ------------------------------- 槽位归属 ------------------------------------


def test_autosave_put_records_the_owner_by_pj(client, tmp_path):
    a = _project(client, tmp_path, "a", default=True)
    b = _project(client, tmp_path, "b")
    assert client.put("/api/autosave/d_1", json=PD, headers=_pj(b["id"])).status_code == 200
    assert client.put(f"/api/autosave/d_2?pj={a['id']}", json=PD).status_code == 200
    owners = layoutsession.owners()
    assert owners["d_1"] == layoutsession.project_key(b["figures_dir"])
    assert owners["d_2"] == layoutsession.project_key(a["figures_dir"])


def test_autosave_put_with_a_stale_pj_still_saves_but_records_no_owner(client):
    """自动保存从不因 pj 失效拒写（那是用户的工作）；归属此时是「不知道」，不记。"""
    assert client.put("/api/autosave/d_1", json=PD, headers=_pj("gone")).status_code == 200
    assert "d_1" not in layoutsession.owners()


def test_autosave_put_without_a_project_records_the_no_project_group(client):
    assert client.put("/api/autosave/d_1", json=PD).status_code == 200
    owners = layoutsession.owners()
    assert "d_1" in owners and owners["d_1"] is None


def test_delete_forgets_the_owner_and_any_last_pointing_at_it(client):
    client.put("/api/autosave/d_1", json=PD)
    layoutsession.set_last(None, "d_1", "x")
    client.delete("/api/autosave/d_1")
    assert "d_1" not in layoutsession.owners()
    assert layoutsession.last_for(None) is None


# ---------------------- 归属判据：别的项目的排版不认（#715 验收 P1） ----------------------
# 稳定端口之后两个项目共用一个 origin：前端曾把项目 F 的排版当成新项目 G「上次开着的」装进来、
# 往后端记 G.last = F 的槽位，而 owners 明明记着它属于 F。后端这一道是纵深：拒记、读出来不认。


def test_put_last_refuses_a_layout_owned_by_another_project(client, tmp_path):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    # 槽位在 F 里自动保存过：owners 记着它属于 F
    assert client.put("/api/autosave/d_f", json=PD, headers=_pj(f["id"])).status_code == 200
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_f", "name": "F"}, headers=_pj(g["id"])
    )
    assert r.status_code == 409 and r.get_json()["code"] == "layout_foreign"
    # 没记成 G 的 last（文件里也没有），G 也读不到它
    assert client.get("/api/layout-session", headers=_pj(g["id"])).get_json() == {"last": None}
    assert layoutsession.project_key(g["figures_dir"]) not in layoutsession.snapshot()["projects"]
    # F 自己记它照常
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_f", "name": "F"}, headers=_pj(f["id"])
    )
    assert r.status_code == 200
    # 不知道归属的槽位（还没自动保存过）不算冲突：第一次自动保存之前就会先记 last
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_new", "name": "G"}, headers=_pj(g["id"])
    )
    assert r.status_code == 200


def test_put_last_refuses_a_project_layout_for_the_no_project_group(client, tmp_path):
    f = _project(client, tmp_path, "projF")
    client.put("/api/autosave/d_f", json=PD, headers=_pj(f["id"]))
    with pytest.raises(layoutsession.ForeignLayoutError):
        layoutsession.set_last(None, "d_f", "F")
    assert layoutsession.last_for(None) is None


def test_a_polluted_last_owned_by_another_project_reads_as_none(client, tmp_path):
    """修复之前已经写下的 G.last = F 的槽位：读出来就不认（不等它被覆盖），F 那条不受影响。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kf = layoutsession.project_key(f["figures_dir"])
    kg = layoutsession.project_key(g["figures_dir"])
    layoutsession.set_last(kf, "d_f", "F")
    layoutsession.set_last(kg, "d_f", "F")  # 归属还不知道时记下（与验收机器上的状态同形）
    layoutsession.record_owner("d_f", kf)
    assert client.get("/api/layout-session", headers=_pj(g["id"])).get_json() == {"last": None}
    assert (
        client.get("/api/layout-session", headers=_pj(f["id"])).get_json()["last"]["doc_id"]
        == "d_f"
    )


def test_viewing_a_layout_from_another_project_keeps_its_owner_until_edited(client, tmp_path):
    """#773 Codex P2：在 G 里从「最近文档」看一眼 F 的排版——打开那一下的冲刷按 F 的 pj 写
    （前端 `documentStore.adoptDocumentOwner`），归属不动，F 照常恢复它；在 G 里改过再存
    （pj = G）才转到 G，此后它是 G 的、F 不再认。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    kf = layoutsession.project_key(f["figures_dir"])
    kg = layoutsession.project_key(g["figures_dir"])
    client.put("/api/autosave/d_f", json=PD, headers=_pj(f["id"]))
    client.put(
        "/api/layout-session/last", json={"doc_id": "d_f", "name": "F"}, headers=_pj(f["id"])
    )
    # 只看：G 开着，冲刷按 F 的 pj 写——归属与 F 的 last 都不动
    assert client.put("/api/autosave/d_f", json=PD, headers=_pj(f["id"])).status_code == 200
    assert layoutsession.owners()["d_f"] == kf
    assert (
        client.get("/api/layout-session", headers=_pj(f["id"])).get_json()["last"]["doc_id"]
        == "d_f"
    )
    # 打开那一下的 pj 已经失效（F 在后端关了）时同样不改归属：失效的 pj 记成「不知道」，不覆盖
    assert client.put("/api/autosave/d_f", json=PD, headers=_pj("gone")).status_code == 200
    assert layoutsession.owners()["d_f"] == kf
    # 改过再存：pj = G，归属转到 G；G 可以记它，F 不再认
    assert client.put("/api/autosave/d_f", json=PD, headers=_pj(g["id"])).status_code == 200
    assert layoutsession.owners()["d_f"] == kg
    r = client.put(
        "/api/layout-session/last", json={"doc_id": "d_f", "name": "F"}, headers=_pj(g["id"])
    )
    assert r.status_code == 200
    assert layoutsession.last_for(kf) is None


# ---------------- 归属证据：本机不知道归属的升级前排版（#773） ----------------


def _slot_with_panels(doc_id: str, *file_ids: str) -> None:
    doc = json.loads(json.dumps(PD))
    doc["canvases"][0]["objects"] = [
        {"id": f"p{i}", "type": "panel", "fileId": f, "x": 0, "y": 0, "w": 1, "h": 1}
        for i, f in enumerate(file_ids)
    ]
    m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    (m.AUTOSAVE_DIR / f"{doc_id}.json").write_text(json.dumps(doc), encoding="utf-8")


def _owner(client, pid: str, doc_id: str) -> dict:
    r = client.get(f"/api/layout-session/owner?doc_id={doc_id}", headers=_pj(pid))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_owner_evidence_owners_record_is_decisive(client, tmp_path):
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    (Path(g["figures_dir"]) / "a.pdf").write_bytes(b"%PDF-1.4")
    _slot_with_panels("d_f", "a.pdf")
    layoutsession.record_owner("d_f", layoutsession.project_key(f["figures_dir"]))
    # 素材在 G 里也有，但 owners 记着 F：别的项目的就是别的项目的（projF → projG 的形状）
    assert _owner(client, g["id"], "d_f") == {"owner": "other", "evidence": "owners"}
    assert _owner(client, f["id"], "d_f") == {"owner": "this", "evidence": "owners"}


def test_owner_evidence_from_the_projects_timeline(client, tmp_path):
    g = _project(client, tmp_path, "projG", default=True)
    versions = Path(g["figures_dir"]) / "tavottofile" / "versions"
    versions.mkdir(parents=True)
    (versions / "d_old.json").write_text("{}", encoding="utf-8")
    assert _owner(client, g["id"], "d_old") == {"owner": "this", "evidence": "versions"}


def test_owner_evidence_from_assets_all_in_the_project(client, tmp_path):
    g = _project(client, tmp_path, "projG", default=True)
    root = Path(g["figures_dir"])
    (root / "a.pdf").write_bytes(b"%PDF-1.4")
    (root / "sub").mkdir()
    (root / "sub" / "b.png").write_bytes(b"x")
    _slot_with_panels("d_all", "a.pdf", "sub/b.png", "runtime:abc")
    assert _owner(client, g["id"], "d_all") == {"owner": "this", "evidence": "assets"}
    # 有一张不在这个项目里：不算证据
    _slot_with_panels("d_some", "a.pdf", "missing.pdf")
    assert _owner(client, g["id"], "d_some")["owner"] == "unknown"
    # 越出项目目录的路径不算（文件真的在，只是不在这个项目里）
    (root.parent / "outside.pdf").write_bytes(b"%PDF-1.4")
    _slot_with_panels("d_escape", "a.pdf", "../outside.pdf")
    assert _owner(client, g["id"], "d_escape")["owner"] == "unknown"
    # 只有 runtime 面板 / 没有面板：没有可以比的东西
    _slot_with_panels("d_runtime", "runtime:abc")
    assert _owner(client, g["id"], "d_runtime")["owner"] == "unknown"
    # 槽位不在
    assert _owner(client, g["id"], "d_nothing") == {"owner": "unknown", "evidence": None}


def test_owner_evidence_rejects_bad_ids(client):
    r = client.get("/api/layout-session/owner?doc_id=../x")
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request"


def _legacy_slot(doc_id: str, mtime_s: int) -> Path:
    p = m.AUTOSAVE_DIR / f"{doc_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(PD), encoding="utf-8")
    os.utime(p, ns=(0, mtime_s * 10**9))
    return p


def test_pruning_never_touches_slots_without_an_owner(client, monkeypatch):
    """PR-B 之前留下的槽位没有归属——它们多半就是 #715 里换了 origin 后找不回来的排版。
    找回入口（PR-C）上线之前，64 条兜底清理一个都不许删。"""
    legacy = [_legacy_slot(f"old{i}", i + 1) for i in range(5)]
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 2)
    for i in range(4):
        assert client.put(f"/api/autosave/n{i}", json=PD).status_code == 200
        os.utime(m.AUTOSAVE_DIR / f"n{i}.json", ns=(0, (100 + i) * 10**9))
    client.put("/api/autosave/n3", json=PD)
    names = sorted(p.stem for p in m.AUTOSAVE_DIR.glob("*.json"))
    assert all(p.is_file() for p in legacy), f"无归属的旧槽位被删了：{names}"
    # 记着归属的照常按上限裁：n0 / n1 最旧，被裁掉
    assert "n3" in names and "n0" not in names and "n1" not in names, names
    assert "n0" not in layoutsession.owners(), "删掉的槽位归属没跟着清"


def test_pruning_never_deletes_a_projects_last_document(client, monkeypatch):
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)
    client.put("/api/autosave/keep_last", json=PD)
    os.utime(m.AUTOSAVE_DIR / "keep_last.json", ns=(0, 10**9))
    layoutsession.set_last(None, "keep_last", "上次开着的")
    client.put("/api/autosave/other", json=PD)
    client.put("/api/autosave/newest", json=PD)
    assert (m.AUTOSAVE_DIR / "keep_last.json").is_file()


def test_pruning_rechecks_protection_when_a_slot_becomes_last_after_the_snapshot(
    client, monkeypatch
):
    """清理拿到「受保护」快照之后，另一个请求把最旧的那个槽位记成了某组的 last（并发标签页
    切回旧排版）。删之前必须重判：槽位与刚写的 last 都要留着（#719 Codex P1）。"""
    client.put("/api/autosave/old_one", json=PD)
    os.utime(m.AUTOSAVE_DIR / "old_one.json", ns=(0, 10**9))
    client.put("/api/autosave/mid", json=PD)
    os.utime(m.AUTOSAVE_DIR / "mid.json", ns=(0, 2 * 10**9))
    monkeypatch.setattr(m, "AUTOSAVE_KEEP_SLOTS", 1)  # 两份都在之后才收紧上限

    real = layoutsession.protected_doc_ids
    fired = []

    def snapshot_then_race():
        ids = real()
        if not fired:
            fired.append(1)
            layoutsession.set_last(None, "old_one", "刚切回来的")  # 快照之后才落地
        return ids

    monkeypatch.setattr(layoutsession, "protected_doc_ids", snapshot_then_race)
    client.put("/api/autosave/newest", json=PD)
    assert fired, "前提：清理确实走到了拿快照那一步"
    assert (m.AUTOSAVE_DIR / "old_one.json").is_file(), "快照之后成了 last 的槽位被删了"
    assert (layoutsession.last_for(None) or {}).get("doc_id") == "old_one", "刚写的 last 被抹掉了"
    # 没受保护的照常裁：mid 被删，归属跟着清
    assert not (m.AUTOSAVE_DIR / "mid.json").exists()
    assert "mid" not in layoutsession.owners()


def test_pruning_does_not_drop_the_owner_of_a_slot_created_after_its_scan(client, monkeypatch):
    """清理扫目录之后、丢「磁盘上已不在的槽位的归属」之前，并发的自动保存刚建出槽位并记了归属：
    按扫描快照丢会让它永远没有归属、从此不进配额。丢之前要当场再看文件（#719 Codex P2）。"""
    client.put("/api/autosave/older", json=PD)
    real_scandir = m.os.scandir

    def scandir_missing_fresh(path):
        # 模拟「扫描发生在 fresh 落盘之前」：这次扫描看不到它
        return [e for e in real_scandir(path) if e.name != "fresh.json"]

    client.put("/api/autosave/fresh", json=PD)
    assert "fresh" in layoutsession.owners()
    monkeypatch.setattr(m.os, "scandir", scandir_missing_fresh)
    client.put("/api/autosave/older", json=PD)  # 触发一次清理，扫描快照里没有 fresh
    assert (m.AUTOSAVE_DIR / "fresh.json").is_file()
    assert "fresh" in layoutsession.owners(), "扫描之后才出现的槽位被丢了归属"


# ------------------------------- 教程重置 ------------------------------------


def test_tutorial_reset_forgets_the_tutorial_session_state(client, monkeypatch):
    from tavotto.engine import tutorial

    monkeypatch.setattr(m.engine_pool, "build", lambda *a, **k: pytest.fail("不许执行脚本"))
    tut = client.post("/api/tutorial/open", json={"default": False}).get_json()
    pid, tpath = tut["project"]["id"], Path(tut["project"]["figures_dir"])
    doc_id = tut["tutorial"]["document_id"]
    key = layoutsession.project_key(tpath)
    client.put("/api/autosave/" + doc_id, json=PD, headers=_pj(pid))
    client.put("/api/layout-session/last", json={"doc_id": doc_id, "name": "T"}, headers=_pj(pid))
    # 别的项目的记录不碰
    layoutsession.set_last("/elsewhere", "d_user", "U")
    layoutsession.record_owner("d_user", "/elsewhere")
    assert layoutsession.owners()[doc_id] == key

    body = client.post("/api/tutorial/reset", json={}).get_json()
    assert body["reset"] is True
    assert tutorial.is_tutorial_path(Path(body["project"]["figures_dir"]))
    assert layoutsession.last_for(key) is None
    assert doc_id not in layoutsession.owners()
    assert layoutsession.last_for("/elsewhere")["doc_id"] == "d_user"
    assert layoutsession.owners()["d_user"] == "/elsewhere"
    new_pid = body["project"]["id"]
    assert client.get("/api/layout-session", headers=_pj(new_pid)).get_json() == {"last": None}
