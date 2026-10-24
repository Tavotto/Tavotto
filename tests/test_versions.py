"""布局版本时间线 API：创建 / 去重 / 列表 / 重命名 / 复制 / 删除 / 裁剪。"""

import pytest

from tavotto import app as m
from tavotto.engine import versionstore as vs
from tests.support import versionfiles


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    # 清掉别的测试模块残留的已打开项目：版本历史现在优先写进项目的
    # tavottofile/versions/，不隔离的话状态会串到那个项目里
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _doc(n_objects=1, name="fig_layout"):
    return {
        "schema": 2,
        "name": name,
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": f"o{i}",
                "type": "text",
                "text": f"t{i}",
                "x": 0,
                "y": 0,
                "w": 10,
                "h": 5,
                "sizePt": 9,
                "bold": False,
                "color": "#000",
                "align": "left",
            }
            for i in range(n_objects)
        ],
        "guides": [],
    }


def _create(client, doc_id="d1", **kw):
    resp = client.post(f"/api/versions/{doc_id}", json={"doc": _doc(), **kw})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_create_and_list(client):
    _create(client, name="初稿")
    _create(client, doc=_doc(2), auto=True)
    resp = client.get("/api/versions/d1")
    versions = resp.get_json()["versions"]
    assert len(versions) == 2
    assert versions[0]["name"] == "初稿"
    assert versions[0]["objects"] == 1
    assert versions[1]["auto"] is True
    assert versions[1]["objects"] == 2
    # 完整快照可取回
    full = client.get(f"/api/versions/d1/{versions[0]['id']}").get_json()
    assert full["doc"]["schema"] == 2


def test_auto_checkpoint_dedup(client):
    _create(client, auto=True)
    second = client.post("/api/versions/d1", json={"doc": _doc(), "auto": True})
    assert second.get_json().get("skipped") is True
    assert len(client.get("/api/versions/d1").get_json()["versions"]) == 1


def test_rejects_bad_doc(client):
    resp = client.post("/api/versions/d1", json={"doc": {"schema": 1}})
    assert resp.status_code == 400


def test_accepts_schema3_snapshot(client):
    pd = {
        "schema": 3,
        "project": {"id": "p", "name": "n"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 10, "h": 10},
                "objects": [{"id": "o1", "type": "text"}],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 0,
    }
    resp = client.post("/api/versions/d1", json={"doc": pd, "name": "v3"})
    assert resp.status_code == 200
    meta = resp.get_json()["version"]
    assert meta["objects"] == 1  # schema 3 也能数出对象数


def test_rename_promote_duplicate_delete(client):
    vid = _create(client, name="a")["version"]["id"]
    # 重命名 + 描述
    resp = client.patch(f"/api/versions/d1/{vid}", json={"name": "b", "description": "调整后"})
    assert resp.get_json()["version"]["name"] == "b"
    # 复制
    copy = client.post(f"/api/versions/d1/{vid}/duplicate").get_json()["version"]
    assert copy["name"] == "b 副本"
    # 删除原版
    assert client.delete(f"/api/versions/d1/{vid}").get_json()["ok"] is True
    left = client.get("/api/versions/d1").get_json()["versions"]
    assert [v["id"] for v in left] == [copy["id"]]
    # 删不存在的 → 404
    assert client.delete(f"/api/versions/d1/{vid}").status_code == 404


def test_docs_are_isolated(client):
    _create(client, doc_id="d1")
    _create(client, doc_id="d2")
    assert len(client.get("/api/versions/d1").get_json()["versions"]) == 1
    assert len(client.get("/api/versions/d2").get_json()["versions"]) == 1


def test_prune_keeps_manual_over_auto(client, monkeypatch):
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 3)
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 10)
    manual = _create(client, name="手动")["version"]["id"]
    for i in range(6):
        client.post("/api/versions/d1", json={"doc": _doc(i + 2), "auto": True})
    versions = client.get("/api/versions/d1").get_json()["versions"]
    autos = [v for v in versions if v["auto"]]
    assert len(autos) == 3  # 自动检查点滚动清理
    assert any(v["id"] == manual for v in versions)  # 手动版本保留


# ------------------- 字节上限（issue #221 §1） -------------------------------
#
# 条数上限（120）管不住体积：每条版本条目里塞的是**整份文档**，于是文件大小
# = 条数 × 文档大小，而每次追加都要把整个文件「读 → 追加 → 裁 → 整写」。
# 实测（`scripts/bench_document.py`，2026-09-03）1.16 MB 的文档塞满 120 条 =
# 约 140 MB / 单次追加 547 ms。


def _timeline_bytes(doc_id="d1"):
    """旧单文件此刻会是的字节（D2 起历史在新存储里；字节预算量的仍是这个口径）。"""
    return versionfiles.legacy_equivalent_bytes(doc_id)


def test_timeline_stops_growing_at_the_byte_budget(client, monkeypatch):
    """追加的代价必须只跟预算有关，与追加了多少次无关。"""
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 4000)
    for i in range(40):
        _create(client, name=f"v{i}")
        # 上限守的是**文件**的字节数：外壳与分隔符也算，所以这里没有任何宽限项。
        # （第一版给了个 `len(dumps_json({"id": "x"}))` 的宽限，那是拿一个与
        #  外壳无关的量当余量——它在整模块跑的时候恰好成立，单跑这一条就红。）
        assert len(_timeline_bytes()) <= 4000, f"第 {i} 次追加之后文件超出了预算"
    names = [v["name"] for v in client.get("/api/versions/d1").get_json()["versions"]]
    assert names[-1] == "v39", "最新的那条没留住"
    assert "v0" not in names, "预算没咬到任何一条（这条判据量不到东西）"


def test_the_newest_version_survives_even_when_it_alone_exceeds_the_budget(client, monkeypatch):
    """单条就超预算时仍然留下最新那条：否则 `api_versions_create` 会交回一个
    磁盘上根本不存在的版本。"""
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 1)
    _create(client, name="big", doc_id="d2")
    versions = client.get("/api/versions/d2").get_json()["versions"]
    assert [v["name"] for v in versions] == ["big"]


def test_the_assembled_file_is_byte_identical_to_dumping_the_kept_list(client):
    """`_save_versions` 是逐条序列化再拼起来的（为了量大小与写文件只序列化
    一次）。**拼出来的必须与整份 dump 逐字节相同**——否则那就是第二个
    序列化器，迟早与 `atomicio.dumps_json` 分叉。"""
    for i in range(3):
        _create(client, name=f"v{i}", doc_id="d3")
    kept = versionfiles.versions("d3")
    # 旧格式写入器（产品代码已不再调用，留作参照）拼出来的字节 == 整份 dump == 新存储按旧口径记的账
    m._save_versions("oracle", kept)
    written = m._versions_path("oracle").read_bytes()
    assert written == m.engine_atomicio.dumps_json({"versions": kept})
    path = m._versions_path("d3")
    idx = vs.list_index(path.parent, path.stem)
    assert len(written) == vs.legacy_file_size(idx["entries"])


def test_the_count_cap_still_governs_small_documents(client, monkeypatch):
    """预算是**第二条**上限，不是替换：小文档下仍然由条数上限说了算。"""
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 5)
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 64 * 1024 * 1024)
    for i in range(9):
        _create(client, name=f"v{i}", doc_id="d4")
    versions = client.get("/api/versions/d4").get_json()["versions"]
    assert [v["name"] for v in versions] == ["v4", "v5", "v6", "v7", "v8"]


def _auto(client, doc_id, n):
    """内容各不相同、**大小基本相同**的自动检查点。

    内容要不同：相同内容会被去重跳过，那样就什么都没测到。大小要相同：靠
    对象数拉开差异的话，条目大小随之变化，「预算装得下几条」就不再是个能
    预先算出来的数，判据只好写得很松——松到量不出优先级。
    """
    r = client.post(
        f"/api/versions/{doc_id}", json={"doc": _doc(2, name=f"c{n:02d}"), "auto": True}
    )
    assert r.status_code == 200 and not r.get_json().get("skipped")


def test_the_byte_cap_sacrifices_auto_checkpoints_before_manual_ones(client, monkeypatch):
    """两条上限**同一套优先级**：先裁自动、再裁最旧。

    改造中途它们各有一套——条数那边按自动/手动分档，字节那边是纯粹的
    「留最新一段」。后果是用户主动按下的那一版，被 1 秒防抖攒出来的自动检查点
    顶掉了（同一份契约在同一个文件里有两个答案）。
    """
    manual = _create(client, name="手动", doc_id="d6")["version"]["id"]
    one = len(m.engine_atomicio.dumps_json(versionfiles.versions("d6")[0]))
    # 预算按**实测的一条**算，正好装得下六条上下：太紧的话除最新那条之外全被
    # 裁掉，优先级这一维就量不到了（第一版正是这么写的，手动那条当然也没了）。
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", one * 6)
    for i in range(20):
        _auto(client, "d6", i)
    versions = client.get("/api/versions/d6").get_json()["versions"]
    assert 1 < len(versions) <= 7, f"预算没咬到，或者咬得只剩一条：{len(versions)}"
    assert any(v["id"] == manual for v in versions), "手工检查点被自动检查点挤掉了"


def test_the_count_cap_sacrifices_auto_checkpoints_before_manual_ones(client, monkeypatch):
    """条数上限那一侧同理。以前它是 `versions[-N:]`——纯按时间，手工检查点
    在自动检查点面前没有任何优待。"""
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 5)
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 40)  # 让自动环不先咬，量的是总数那一档
    manual = _create(client, name="手动", doc_id="d7")["version"]["id"]
    for i in range(8):
        _auto(client, "d7", i)
    versions = client.get("/api/versions/d7").get_json()["versions"]
    assert len(versions) == 5
    assert any(v["id"] == manual for v in versions), "手工检查点被自动检查点挤掉了"


def test_the_newest_entry_never_gets_sacrificed_even_when_it_is_auto(client, monkeypatch):
    """最新那条不参与牺牲——哪怕它是自动的。裁掉它等于交回一个磁盘上不存在
    的版本。"""
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 1)
    _create(client, name="手动", doc_id="d8")
    _auto(client, "d8", 1)
    versions = client.get("/api/versions/d8").get_json()["versions"]
    assert [v["auto"] for v in versions] == [True], "留下的不是最新那条"


# --------------------------- 列表里的缩略图草图 -------------------------------
# 列表要能画出缩略图，但**不许退化成「打开面板就拉全部版本正文」**。草图是
# 列表端点本来就已经解析出来的那份内存数据的投影（`_load_versions` 为了数
# 对象数就得整份解析），所以它既不多读一次盘也不多解析一遍。


def _panel(i, file_id="a.pdf"):
    return {
        "id": f"p{i}",
        "type": "panel",
        "fileId": file_id,
        "fileKind": "pdf",
        "nativeW": 40,
        "nativeH": 30,
        "x": float(i),
        "y": 2.0,
        "w": 40.0,
        "h": 30.0,
        # 正文里最大的一块：草图一条都不该带
        "overrides": [{"gid": "g1", "prop": "pos_frac", "value": [0.1, 0.2]}],
        "script": "print('hi')",
    }


def _text(i, s="标题文字"):
    return {
        "id": f"t{i}",
        "type": "text",
        "text": s,
        "x": 1.0,
        "y": 1.0,
        "w": 30.0,
        "h": 6.0,
        "sizePt": 9,
        "bold": False,
        "color": "#000",
        "align": "left",
    }


def _doc_with(objects, page=None):
    return {
        "schema": 2,
        "name": "fig",
        "page": page if page is not None else {"w": 150, "h": 100},
        "objects": objects,
        "guides": [],
    }


def test_the_list_carries_no_sketch_unless_the_caller_asks_for_one(client):
    """默认不带草图：只有需要画缩略图的调用方才付那份字节。"""
    client.post("/api/versions/d1", json={"doc": _doc_with([_panel(0), _text(0)])})
    plain = client.get("/api/versions/d1").get_json()["versions"]
    assert "sketch" not in plain[0]
    asked = client.get("/api/versions/d1?sketch=40").get_json()["versions"]
    assert "sketch" in asked[0]


def test_the_sketch_draws_a_thumbnail_without_carrying_the_body(client):
    """草图够画一张缩略图，而 overrides / 脚本 / 整份正文一个字节都不在里面。"""
    client.post(
        "/api/versions/d1",
        json={
            "doc": _doc_with(
                [
                    _panel(0),
                    _text(0),
                    {
                        "id": "s0",
                        "type": "shape",
                        "shape": "ellipse",
                        "x": 5,
                        "y": 5,
                        "w": 9,
                        "h": 9,
                    },
                ]
            )
        },
    )
    resp = client.get("/api/versions/d1?sketch=40&sketchText=24")
    meta = resp.get_json()["versions"][0]
    sketch = meta["sketch"]
    assert sketch["page"] == {"w": 150.0, "h": 100.0}
    kinds = [o["type"] for o in sketch["objects"]]
    assert kinds == ["panel", "text", "shape"]
    panel, text, shape = sketch["objects"]
    # 画得出来所需的：落位 + 面板的素材身份 + 文字 + 形状种类
    assert (panel["x"], panel["y"], panel["w"], panel["h"]) == (0.0, 2.0, 40.0, 30.0)
    assert (panel["fileId"], panel["fileKind"]) == ("a.pdf", "pdf")
    assert text["text"] == "标题文字"
    assert shape["shape"] == "ellipse"
    # 正文没有跟着来：整条响应里既没有版本文档，也没有 overrides / 脚本
    assert "doc" not in meta
    body = resp.get_data(as_text=True)
    assert "overrides" not in body
    assert "pos_frac" not in body
    assert "print(" not in body


def test_a_schema3_snapshot_sketches_the_canvas_the_checkpoint_came_from(client):
    """schema 3 也出得了摘要，而且出的是**这个检查点那张画布**，不是第一张。"""
    pd = {
        "schema": 3,
        "project": {"id": "p", "name": "n"},
        "canvases": [
            {
                "id": "c1",
                "name": "A",
                "page": {"w": 80, "h": 60},
                "objects": [_text(1, "第一张")],
                "guides": [],
            },
            {
                "id": "c2",
                "name": "B",
                "page": {"w": 200, "h": 120},
                "objects": [_text(2, "第二张"), _panel(2)],
                "guides": [],
            },
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 0,
    }
    client.post("/api/versions/d1", json={"doc": pd, "canvasId": "c2", "canvasName": "B"})
    sketch = client.get("/api/versions/d1?sketch=40&sketchText=24").get_json()["versions"][0][
        "sketch"
    ]
    assert sketch["page"] == {"w": 200.0, "h": 120.0}
    assert [o.get("text") for o in sketch["objects"] if o["type"] == "text"] == ["第二张"]


def test_a_broken_document_costs_one_thumbnail_not_the_whole_list(client):
    """一份坏文档只赔掉自己那张缩略图。整份时间线打不开比少一张图坏得多。"""
    # 页面尺寸缺席 → 这一条没有草图（不替它编一个 A4 出来）
    client.post("/api/versions/d1", json={"doc": _doc_with([_text(0)], page={"w": "宽"})})
    # 对象缺 x/y/w/h、type 不是字符串、根本不是对象 → 画得出的照画，画不出的跳过
    client.post(
        "/api/versions/d1",
        json={"doc": _doc_with(["不是对象", {"id": "x", "type": 7}, {"id": "y", "type": "text"}])},
    )
    resp = client.get("/api/versions/d1?sketch=40&sketchText=24")
    assert resp.status_code == 200
    first, second = resp.get_json()["versions"]
    assert "sketch" not in first
    assert second["sketch"]["objects"] == [{"type": "text", "x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}]


def test_the_sketch_is_cut_to_the_size_the_caller_asked_for(client):
    """「一张缩略图画几个对象」是调用方说了算——后端不写第二份权威。"""
    client.post("/api/versions/d1", json={"doc": _doc_with([_panel(i) for i in range(50)])})
    assert (
        len(client.get("/api/versions/d1?sketch=3").get_json()["versions"][0]["sketch"]["objects"])
        == 3
    )
    assert (
        len(client.get("/api/versions/d1?sketch=9").get_json()["versions"][0]["sketch"]["objects"])
        == 9
    )


def test_the_text_in_a_sketch_is_cut_to_the_length_the_caller_asked_for(client):
    client.post("/api/versions/d1", json={"doc": _doc_with([_text(0, "一二三四五六七八九十")])})
    got = client.get("/api/versions/d1?sketch=40&sketchText=4").get_json()
    assert got["versions"][0]["sketch"]["objects"][0]["text"] == "一二三四"
    # 没要文字就不带文字（缩略图不画文字时那些字节纯属浪费）
    silent = client.get("/api/versions/d1?sketch=40").get_json()
    assert "text" not in silent["versions"][0]["sketch"]["objects"][0]


def test_hidden_objects_are_dropped_before_the_cut_not_after(client):
    """先滤隐藏再截断。反过来的话「前 N 个全是隐藏对象」的版本得到一张空图。"""
    objects = [{**_panel(i), "hidden": True} for i in range(5)] + [_text(9, "看得见")]
    client.post("/api/versions/d1", json={"doc": _doc_with(objects)})
    sketch = client.get("/api/versions/d1?sketch=2&sketchText=24").get_json()["versions"][0][
        "sketch"
    ]
    assert [o["type"] for o in sketch["objects"]] == ["text"]


def test_the_transport_cap_holds_even_when_the_caller_asks_for_everything(client):
    """`?sketch=99999` 不许把 120 条版本的全部对象一次发出去。"""
    n = m.VERSION_SKETCH_MAX_OBJECTS + 20
    client.post("/api/versions/d1", json={"doc": _doc_with([_panel(i) for i in range(n)])})
    got = client.get(f"/api/versions/d1?sketch={n}&sketchText=99999").get_json()
    assert len(got["versions"][0]["sketch"]["objects"]) == m.VERSION_SKETCH_MAX_OBJECTS
    # 非法取值当成「不要草图」，不是当成「要最大的那份」
    assert "sketch" not in client.get("/api/versions/d1?sketch=abc").get_json()["versions"][0]
    assert "sketch" not in client.get("/api/versions/d1?sketch=-5").get_json()["versions"][0]


# ---------------------------------------------------------------------------
# issue #264：旧文件读不出来的出路（ADR 0140 §五，D2）
# ---------------------------------------------------------------------------
# 原来：`_load_versions` 把「截断 / 乱码 / 不是 JSON」折成 `[]`，下一次写入会在这个空列表上
# 追加并**整份写回旧文件**——所以必须有一道闸，回 409 把用户卡死（D0 之前的数据丢失面）。
# 现在写入不再碰旧文件：第一次写入迁移，旧文件解析失败 → 新存储开一份空历史，旧文件**原样保留**，
# 列表带 `legacy_unreadable` 标记（只有文件名与大小，不暴露绝对路径）。守的是两件事：
# 不再 409 卡死；坏文件逐字节没被动过（这条 bug 的伤害在文件上，不在响应上）。

_CORRUPT = b'{"versions": [{"id": "v1", "doc": {"schema": 2, "nam'  # 写到一半断电


def _write_timeline(path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def test_the_first_checkpoint_still_works_when_there_is_no_history_file(client):
    """最常走的那条路：磁盘上根本没有这份历史 → 照常建第一条，进新存储，没有旧文件冒出来。"""
    assert m._versions_source_path("d264a") is None, "摆场景失败：磁盘上已经有这份历史了"
    resp = client.post("/api/versions/d264a", json={"doc": _doc(), "name": "初稿"})
    assert resp.status_code == 200, resp.get_json()
    assert not m._versions_path("d264a").exists(), "新历史写进了旧单文件"
    assert versionfiles.store_root("d264a").is_dir()
    listed = client.get("/api/versions/d264a").get_json()
    assert [v["name"] for v in listed["versions"]] == ["初稿"]
    assert "legacy_unreadable" not in listed


@pytest.mark.parametrize(
    "raw",
    [
        _CORRUPT,
        b'{"schema": 2}',  # 是对象但没有 versions 键：`_save_versions` 写不出的形状
        b'{"versions": {}}',
        b'{"versions": [{"id": "v1-1", "doc": {"x": NaN}}]}',  # 非有限数
    ],
    ids=["truncated", "no-versions-key", "versions-not-a-list", "non-finite"],
)
def test_an_unreadable_legacy_file_opens_an_empty_history_and_is_left_byte_for_byte_alone(
    client, tmp_path, raw
):
    """核心用例：坏文件躺在磁盘上 → 列表 200 + 空 + 可恢复标记；写入 200；**坏文件逐字节没被动过**。"""
    path = m._versions_path("d264b")
    _write_timeline(path, raw)
    listed = client.get("/api/versions/d264b")
    assert listed.status_code == 200, listed.get_json()
    body = listed.get_json()
    assert body["versions"] == []
    assert body["legacy_unreadable"] == {"name": "d264b.json", "size": len(raw)}
    assert str(tmp_path) not in listed.get_data(as_text=True), "标记里带出了本机绝对路径"
    assert path.read_bytes() == raw, "只是读一下，旧文件不该被动"

    resp = client.post("/api/versions/d264b", json={"doc": _doc(), "auto": True})
    assert resp.status_code == 200, resp.get_json()
    assert path.read_bytes() == raw, "坏文件被整份覆盖了——这正是 #264 的数据丢失"
    after = client.get("/api/versions/d264b").get_json()
    assert len(after["versions"]) == 1
    assert after["legacy_unreadable"] == {"name": "d264b.json", "size": len(raw)}
    # 之后的写入照常，标记仍在
    assert (
        client.post("/api/versions/d264b", json={"doc": _doc(2), "name": "二"}).status_code == 200
    )
    assert path.read_bytes() == raw


def test_a_history_emptied_by_deleting_every_version_can_still_grow_again(client):
    """反方向的边：用户把最后一版删光了 → 仍能存新的（空历史不是死锁）。"""
    created = _create(client, doc_id="d264c")["version"]
    resp = client.delete(f"/api/versions/d264c/{created['id']}")
    assert resp.status_code == 200, resp.get_json()
    assert client.get("/api/versions/d264c").get_json()["versions"] == []
    assert client.post("/api/versions/d264c", json={"doc": _doc()}).status_code == 200
    assert len(client.get("/api/versions/d264c").get_json()["versions"]) == 1


def test_the_unreadable_file_named_is_the_one_the_reader_actually_read(
    client, monkeypatch, tmp_path
):
    """判据的主语：**读侧此刻读的那个文件**。升级前的历史躺在旧位置（`layouts/_versions/`）、
    项目里还没有这份文档的版本文件时，标记指的是旧位置那份；新历史建在项目里，旧位置那份原样。"""
    monkeypatch.setattr(m, "project_store_dir", lambda *a, **k: tmp_path / "store")
    legacy = m.VERSIONS_DIR / "d264e.json"
    _write_timeline(legacy, _CORRUPT)
    target = m._versions_path("d264e")
    assert not target.exists(), "摆场景失败：写入路径上已经有文件了"
    assert m._versions_source_path("d264e") == legacy, "读侧读的不是旧位置那份"

    assert client.get("/api/versions/d264e").get_json()["legacy_unreadable"]["name"] == "d264e.json"
    resp = client.post("/api/versions/d264e", json={"doc": _doc()})
    assert resp.status_code == 200, resp.get_json()
    assert legacy.read_bytes() == _CORRUPT
    assert not target.exists(), "新历史写进了项目里的旧单文件"
    assert vs.store_dir(target.parent, target.stem).is_dir()


def test_the_list_says_which_figures_each_version_has_derived_not_stored(client):
    """`figures` = 那一版画布上面板的 fileId（读时从整份文档派生，旧节点同样有；没有图就不带）。"""
    client.post("/api/versions/d1", json={"doc": _doc_with([_text(0)])})
    client.post("/api/versions/d1", json={"doc": _doc_with([_panel(0, "a.pdf"), _text(0)])})
    client.post(
        "/api/versions/d1",
        json={"doc": _doc_with([_panel(0, "b.pdf"), _panel(1, "a.pdf"), _panel(2, "b.pdf")])},
    )
    metas = client.get("/api/versions/d1").get_json()["versions"]
    got = {tuple(m.get("figures") or ()) for m in metas}
    assert got == {(), ("a.pdf",), ("b.pdf", "a.pdf")}
    assert [m for m in metas if "figures" not in m]  # 没有图的那一版不带这个键
