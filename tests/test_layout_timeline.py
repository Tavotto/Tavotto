"""排版时间线（ADR 0101）：命名节点、关键时刻、裁剪顺序、字节上限提示、节点缩略图。

`tests/test_versions.py` 守的是时间线原有的契约（去重、条数 / 字节两条上限、
整份写回的闸、草图）；这里守 ADR 0101 新加的那几条：

* 命名节点**永不被自动清理**——条数上限不数它、字节上限不删它；
* 未命名节点的牺牲顺序：普通自动 → 关键时刻 → 手动；
* 命名节点自己超出字节上限时，**拒绝再命名**（409，磁盘零改动），已有的一条不动；
* 缩略图单独存、随节点一起删，孤儿图下一次写入顺手清掉。
"""

from __future__ import annotations

import ast
import json
import re
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _doc(tag: str = "x", pad: int = 0):
    return {
        "schema": 2,
        "name": "fig",
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": "t",
                "type": "text",
                "text": tag + "·" * pad,
                "x": 0,
                "y": 0,
                "w": 10,
                "h": 5,
                "sizePt": 9,
                "bold": False,
                "color": "#000",
                "align": "left",
            }
        ],
        "guides": [],
    }


_seq = 0


def _create(client, doc_id="d1", **kw):
    """每次一份不同的内容：自动检查点的去重不该让用例数错条数。"""
    global _seq
    _seq += 1
    body = {"doc": kw.pop("doc", _doc(f"v{_seq}")), **kw}
    resp = client.post(f"/api/versions/{doc_id}", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["version"]


def _list(client, doc_id="d1"):
    return client.get(f"/api/versions/{doc_id}").get_json()


def _names(client, doc_id="d1"):
    return [v["name"] for v in _list(client, doc_id)["versions"]]


def _file(doc_id="d1"):
    return m.VERSIONS_DIR / f"{doc_id}.json"


# --------------------------------------------------------------------------- 类型


def test_each_node_reports_its_kind(client):
    _create(client, auto=True)
    _create(client, auto=True, moment="export")
    _create(client)  # 手动、没起名字
    _create(client, name="投稿前", named=True)
    kinds = [(v["kind"], v["named"], v.get("moment")) for v in _list(client)["versions"]]
    assert kinds == [
        ("auto", False, None),
        ("moment", False, "export"),
        ("manual", False, None),
        ("named", True, None),
    ]


def test_a_name_without_named_is_not_a_named_node(client):
    """「恢复前 10:32」是程序起的名字：名字是谁起的才是判据，不是有没有名字。"""
    v = _create(client, name="恢复前 10:32", auto=True, moment="before_restore")
    assert v["kind"] == "moment" and v["named"] is False


def test_an_unknown_moment_is_refused_not_silently_downgraded(client):
    resp = client.post("/api/versions/d1", json={"doc": _doc(), "auto": True, "moment": "exprot"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "version_moment_invalid"
    assert not _file().exists()


def test_key_moments_are_not_deduplicated_against_the_previous_node(client):
    """导出成功那一刻内容没变也是一件发生过的事；普通自动检查点照旧去重。"""
    doc = _doc("same")
    _create(client, doc=doc, auto=True)
    again = client.post("/api/versions/d1", json={"doc": doc, "auto": True}).get_json()
    assert again.get("skipped") is True
    _create(client, doc=doc, auto=True, moment="export")
    assert [v["kind"] for v in _list(client)["versions"]] == ["auto", "moment"]


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        ({"auto": False, "name": "投稿前"}, "named"),
        ({"auto": False, "name": "09-27 10:32"}, "manual"),
        ({"auto": False, "name": ""}, "manual"),
        ({"auto": True, "name": "恢复前 10:32"}, "auto"),
    ],
)
def test_legacy_entries_without_the_named_field(client, entry, kind):
    """ADR 0101 之前的条目：用户敲过名字的手动版本认作命名节点，升级不让它变得可裁。"""
    _file().parent.mkdir(parents=True, exist_ok=True)
    legacy = {"id": "v1-1", "ts": 1, "doc": _doc(), **entry}
    _file().write_text(json.dumps({"versions": [legacy]}), encoding="utf-8")
    assert _list(client)["versions"][0]["kind"] == kind


# --------------------------------------------------------------------------- 命名


def test_rename_names_the_node_and_unname_turns_it_back(client):
    v = _create(client, auto=True)
    r = client.patch(f"/api/versions/d1/{v['id']}", json={"name": "投稿前"}).get_json()["version"]
    assert (r["kind"], r["named"], r["name"]) == ("named", True, "投稿前")
    r = client.patch(f"/api/versions/d1/{v['id']}", json={"named": False}).get_json()["version"]
    assert r["named"] is False and r["kind"] == "auto"
    # 名字回到它**自己那一刻**的时间串，不是「现在」
    assert r["name"] == time.strftime("%m-%d %H:%M", time.localtime(v["ts"] / 1000))


def test_an_empty_rename_changes_nothing(client):
    v = _create(client, auto=True)
    r = client.patch(f"/api/versions/d1/{v['id']}", json={"name": "   "}).get_json()["version"]
    assert r["named"] is False and r["name"] == v["name"]


def test_duplicating_a_named_node_keeps_it_named_but_not_its_moment_or_thumb(client):
    v = _create(client, auto=True, moment="export")
    client.put(f"/api/versions/d1/{v['id']}/thumb", data=b"\x89PNGx", content_type="image/png")
    client.patch(f"/api/versions/d1/{v['id']}", json={"name": "投稿前"})
    dup = client.post(f"/api/versions/d1/{v['id']}/duplicate").get_json()["version"]
    assert dup["named"] is True and "moment" not in dup and "thumb" not in dup


# --------------------------------------------------------------------------- 裁剪


def test_named_nodes_are_never_pruned_by_the_count_caps(client, monkeypatch):
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 2)
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 3)
    _create(client, name="初稿", named=True)
    for _ in range(8):
        _create(client, auto=True)
    _create(client, name="投稿前", named=True)
    for _ in range(8):
        _create(client)  # 未命名的手动
    kinds = [v["kind"] for v in _list(client)["versions"]]
    assert kinds.count("named") == 2
    # 命名节点**不计入**总数：未命名的正好留到上限
    assert len(kinds) - kinds.count("named") == 3
    assert _names(client)[:1] == ["初稿"]


def test_named_nodes_are_never_dropped_by_the_byte_cap(client, monkeypatch):
    big = len(json.dumps(_doc("n", pad=2000), ensure_ascii=False).encode())
    # 三条命名节点（条目比文档本身略大）正好装得下，再多一条未命名的就不行
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", int(big * 3.3))
    for i in range(3):
        _create(client, doc=_doc(f"n{i}", pad=2000), name=f"命名{i}", named=True)
    for _ in range(6):
        _create(client, doc=_doc(f"a{_seq}", pad=2000), auto=True)
    kinds = [v["kind"] for v in _list(client)["versions"]]
    assert kinds.count("named") == 3
    # 预算被命名节点占满：未命名的只剩最新一条（「至少留一条」）
    assert kinds.count("auto") == 1


def test_sacrifice_order_is_auto_then_moment_then_manual(client, monkeypatch):
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 100)
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 100)
    _create(client, name="manual-0")
    _create(client, auto=True, moment="open", name="moment-0")
    _create(client, auto=True, name="auto-0")
    _create(client, name="newest")
    versions = json.loads(_file().read_text(encoding="utf-8"))["versions"]
    order = [versions[i]["name"] for i in m._sacrifice_order(versions)]
    assert order == ["auto-0", "moment-0", "manual-0"]


def test_named_nodes_are_not_in_the_sacrifice_order_at_all(client):
    _create(client, name="投稿前", named=True)
    _create(client, auto=True)
    versions = json.loads(_file().read_text(encoding="utf-8"))["versions"]
    assert m._sacrifice_order(versions) == []


# --------------------------------------------------------------------------- 字节上限


def test_naming_past_the_byte_cap_is_refused_and_the_file_is_untouched(client, monkeypatch):
    one = len(json.dumps(_doc("n", pad=2000), ensure_ascii=False).encode())
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", int(one * 2.5))
    _create(client, doc=_doc("n0", pad=2000), name="一", named=True)
    _create(client, doc=_doc("n1", pad=2000), name="二", named=True)
    before = _file().read_bytes()
    resp = client.post(
        "/api/versions/d1", json={"doc": _doc("n2", pad=2000), "name": "三", "named": True}
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "named_budget_exceeded"
    assert set(body["params"]) == {"used", "limit"}
    assert _file().read_bytes() == before  # 写回事务不变式：拒了就零改动


def test_renaming_past_the_byte_cap_is_refused_too(client, monkeypatch):
    one = len(json.dumps(_doc("n", pad=2000), ensure_ascii=False).encode())
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", int(one * 1.5))
    _create(client, doc=_doc("n0", pad=2000), name="一", named=True)
    v = _create(client, doc=_doc("a0", pad=2000), auto=True)
    before = _file().read_bytes()
    resp = client.patch(f"/api/versions/d1/{v['id']}", json={"name": "二"})
    assert resp.status_code == 409 and resp.get_json()["code"] == "named_budget_exceeded"
    assert _file().read_bytes() == before


def test_the_list_reports_when_named_nodes_are_over_the_cap(client, monkeypatch):
    """上限被调小（或旧版本升级上来）时命名节点已经超了：**不删**，列表说出来。"""
    _create(client, doc=_doc("n0", pad=2000), name="一", named=True)
    _create(client, doc=_doc("n1", pad=2000), name="二", named=True)
    assert _list(client)["budget"]["namedOver"] is False
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 100)
    _create(client, auto=True)  # 一次写入：裁剪照跑
    listed = _list(client)
    assert [v["kind"] for v in listed["versions"]].count("named") == 2
    budget = listed["budget"]
    assert budget["namedOver"] is True and budget["limit"] == 100
    assert budget["namedBytes"] > 100


def test_removing_names_and_deleting_nodes_are_always_allowed_over_the_cap(client, monkeypatch):
    """超限时的出口必须畅通：删名字、删节点都不许被 409 挡住。"""
    a = _create(client, doc=_doc("n0", pad=2000), name="一", named=True)
    b = _create(client, doc=_doc("n1", pad=2000), name="二", named=True)
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 100)
    assert client.patch(f"/api/versions/d1/{a['id']}", json={"named": False}).status_code == 200
    assert client.delete(f"/api/versions/d1/{b['id']}").status_code == 200


# --------------------------------------------------------------------------- 缩略图


def _thumbs_dir(doc_id="d1"):
    return m.VERSIONS_DIR / "thumbs" / doc_id


def test_a_thumbnail_round_trips_and_is_recorded_on_the_node(client):
    v = _create(client, auto=True)
    png = b"\x89PNG\r\n\x1a\nfake"
    r = client.put(f"/api/versions/d1/{v['id']}/thumb", data=png, content_type="image/png")
    assert r.status_code == 200 and r.get_json()["thumb"] == "png"
    got = client.get(f"/api/versions/d1/{v['id']}/thumb")
    assert got.status_code == 200 and got.data == png and got.mimetype == "image/png"
    assert (_thumbs_dir() / f"{v['id']}.png").read_bytes() == png
    assert _list(client)["versions"][0]["thumb"] == "png"


def test_a_node_without_a_thumbnail_is_404_not_an_empty_image(client):
    v = _create(client, auto=True)
    assert client.get(f"/api/versions/d1/{v['id']}/thumb").status_code == 404
    assert "thumb" not in _list(client)["versions"][0]


@pytest.mark.parametrize(
    ("data", "ctype"),
    [
        (b"x", "image/jpeg"),
        (b"", "image/png"),
        (b"x" * (m.VERSION_THUMB_MAX_BYTES + 1), "image/png"),
    ],
)
def test_bad_thumbnails_are_refused(client, data, ctype):
    v = _create(client, auto=True)
    r = client.put(f"/api/versions/d1/{v['id']}/thumb", data=data, content_type=ctype)
    assert r.status_code == 400 and r.get_json()["code"] == "version_thumb_invalid"
    assert not _thumbs_dir().exists() or not any(_thumbs_dir().iterdir())


def test_a_thumbnail_for_a_node_that_is_gone_is_not_written(client):
    """拍图与裁剪赛跑：节点已经不在了就 404，图不落盘。"""
    _create(client, auto=True)
    r = client.put("/api/versions/d1/vdead-1/thumb", data=b"png", content_type="image/png")
    assert r.status_code == 404
    assert not _thumbs_dir().exists() or not any(_thumbs_dir().iterdir())


def test_the_thumbnail_is_deleted_with_its_node(client):
    v = _create(client, auto=True)
    keep = _create(client, auto=True)
    for x in (v, keep):
        client.put(f"/api/versions/d1/{x['id']}/thumb", data=b"png", content_type="image/png")
    assert client.delete(f"/api/versions/d1/{v['id']}").status_code == 200
    assert sorted(p.name for p in _thumbs_dir().iterdir()) == [f"{keep['id']}.png"]


def test_the_thumbnail_is_pruned_with_its_node(client, monkeypatch):
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 2)
    first = _create(client, auto=True)
    client.put(f"/api/versions/d1/{first['id']}/thumb", data=b"png", content_type="image/png")
    for _ in range(3):
        _create(client, auto=True)
    ids = {v["id"] for v in _list(client)["versions"]}
    assert first["id"] not in ids
    assert not (_thumbs_dir() / f"{first['id']}.png").exists()


def test_switching_thumbnail_format_leaves_one_file(client):
    v = _create(client, auto=True)
    client.put(f"/api/versions/d1/{v['id']}/thumb", data=b"png", content_type="image/png")
    client.put(f"/api/versions/d1/{v['id']}/thumb", data=b"webp", content_type="image/webp")
    assert sorted(p.name for p in _thumbs_dir().iterdir()) == [f"{v['id']}.webp"]


def test_orphan_thumbnails_are_swept_on_the_next_write_and_strangers_are_left_alone(client):
    v = _create(client, auto=True)
    _thumbs_dir().mkdir(parents=True)
    (_thumbs_dir() / "vdead-1.png").write_bytes(b"orphan")  # 进程死在删图之前留下的
    (_thumbs_dir() / "notes.txt").write_bytes(b"not ours")  # 不是我们起的名字：不碰
    client.put(f"/api/versions/d1/{v['id']}/thumb", data=b"png", content_type="image/png")
    assert sorted(p.name for p in _thumbs_dir().iterdir()) == sorted(
        [f"{v['id']}.png", "notes.txt"]
    )


def test_thumbnails_of_one_layout_do_not_touch_another(client):
    a = _create(client, "d1", auto=True)
    b = _create(client, "d2", auto=True)
    client.put(f"/api/versions/d1/{a['id']}/thumb", data=b"a", content_type="image/png")
    client.put(f"/api/versions/d2/{b['id']}/thumb", data=b"b", content_type="image/png")
    client.delete(f"/api/versions/d1/{a['id']}")
    assert (_thumbs_dir("d2") / f"{b['id']}.png").read_bytes() == b"b"


# --------------------------------------------------------------------------- 并发


def test_concurrent_writers_do_not_lose_named_nodes(client):
    """自动检查点、命名、拍图同时进来：锁把「读 → 改 → 整写」串起来，一条都不丢。"""
    errors: list = []

    def worker(k: int):
        c = m.app.test_client()
        try:
            for j in range(5):
                body = {"doc": _doc(f"w{k}-{j}"), "auto": j % 2 == 0}
                if j == 4:
                    body.update(name=f"命名{k}", named=True)
                r = c.post("/api/versions/d1", json=body)
                assert r.status_code == 200, r.get_json()
                vid = r.get_json()["version"]["id"]
                r = c.put(f"/api/versions/d1/{vid}/thumb", data=b"p", content_type="image/png")
                assert r.status_code in (200, 404), r.get_json()
        except Exception as exc:  # noqa: BLE001 —— 线程里的断言要带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    listed = _list(client)["versions"]
    assert sorted(v["name"] for v in listed if v["named"]) == [f"命名{k}" for k in range(6)]
    assert len(listed) == 30
    # 文件仍是一份合法的时间线，每张图都有主人
    ids = {v["id"] for v in json.loads(_file().read_text(encoding="utf-8"))["versions"]}
    assert {p.stem for p in _thumbs_dir().iterdir()} <= ids


# --------------------------------------------------------------------------- 同源对


def test_key_moments_are_the_same_closed_set_on_both_sides():
    """关键时刻闭集：后端 `VERSION_MOMENTS` ↔ 前端 `LAYOUT_MOMENTS`（顺序无关、集合相等）。

    前端多一个，那个时刻的每一次打点都 400、静默不见；后端多一个，界面上没有它的标记。
    """
    src = (Path(__file__).resolve().parents[1] / "web/src/lib/api.ts").read_text(encoding="utf-8")
    hit = re.search(r"export const LAYOUT_MOMENTS = (\[[^\]]*\]) as const", src)
    assert hit, "api.ts 里找不到 LAYOUT_MOMENTS 的字面量"
    front = ast.literal_eval(hit.group(1))
    assert len(front) == len(set(front))
    assert set(front) == set(m.VERSION_MOMENTS)
