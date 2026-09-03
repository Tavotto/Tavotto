"""布局版本时间线 API：创建 / 去重 / 列表 / 重命名 / 复制 / 删除 / 裁剪。"""

import pytest

from tavotto import app as m


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
    return m._versions_path(doc_id).read_bytes()


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
    kept = m._load_versions("d3")
    assert _timeline_bytes("d3") == m.engine_atomicio.dumps_json({"versions": kept})


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
    one = len(m.engine_atomicio.dumps_json(m._load_versions("d6")[0]))
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
