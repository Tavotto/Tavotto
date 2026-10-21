"""版本时间线 D2：第一次写入在锁内把旧单文件迁进新存储，之后一律写新存储（ADR 0140 §四、§五）。

Flask test client + 真文件系统 + 临时目录：

* 首写迁移后旧文件**字节不变**，列表与迁移前逐字段相同；读（GET）从不迁移；
* 迁移在三个崩溃点被打断后，读侧一直看到同一份历史，下一次写入重跑到一致；
* 回滚后的旧程序在冻结的旧文件上追加，再升级：只导入新增、按 id 去重，不覆盖、不让已删的复活；
* 迁移在版本锁内（确定性用例：迁移中途进来的第二个写请求必须等）；
* 写入的文件集合恒为「一个信封 + 索引」，字节与历史长度无关。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, versionstore as vs
from tests.support import versionfiles

PNG = b"\x89PNGx"


class Crash(Exception):
    pass


def _doc(n: int = 1, tag: str = "x") -> dict:
    return {
        "schema": 2,
        "name": f"fig-{tag}",
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": f"o{i}",
                "type": "text",
                "text": f"{tag}{i}中文",
                "x": i,
                "y": 0,
                "w": 10,
                "h": 5,
                "sizePt": 9,
                "bold": False,
                "color": "#000",
                "align": "left",
            }
            for i in range(n)
        ],
        "guides": [],
    }


def _ver(i: int, **kw) -> dict:
    base = {
        "id": f"v{0x100 + i:x}-{i:x}",
        "name": f"旧{i}",
        "ts": 1_700_000_000_000 + i * 1000,
        "auto": False,
        "named": False,
        "description": "",
        "doc": _doc(i + 1, f"s{i}"),
    }
    base.update(kw)
    return base


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _legacy(doc_id: str = "d", n: int = 6) -> tuple[Path, bytes]:
    vers = [_ver(i) for i in range(n)]
    vers[1].update(auto=True, name="自动")
    vers[2].update(named=True, name="里程碑", description="d")
    vers[3].update(auto=True, moment="export", canvasId="c2", canvasName="第二张")
    m._save_versions(doc_id, vers)
    path = m._versions_path(doc_id)
    return path, path.read_bytes()


def _list(client, doc_id="d"):
    r = client.get(f"/api/versions/{doc_id}?sketch=40&sketchText=20")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _post(client, doc_id="d", **kw):
    r = client.post(f"/api/versions/{doc_id}", json={"doc": _doc(2, "new"), **kw})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_reading_never_migrates_and_the_first_write_does(client):
    path, frozen = _legacy()
    root = vs.store_dir(path.parent, path.stem)
    before = _list(client)
    client.get(f"/api/versions/d/{before['versions'][0]['id']}")
    assert not root.exists(), "读也迁移了（读不写盘）"

    created = _post(client, name="新的", auto=True)["version"]
    assert path.read_bytes() == frozen, "迁移改写了旧文件"
    assert root.is_dir() and vs.has_store(path.parent, path.stem)
    after = _list(client)
    assert after["versions"][:-1] == before["versions"], "迁移前后的历史不是逐字段相同"
    assert after["versions"][-1]["id"] == created["id"]
    assert after["budget"] == before["budget"]
    rec = json.loads((root / vs.MIGRATION_NAME).read_text(encoding="utf-8"))
    assert rec["state"] == "committed" and rec["count"] == 6
    # 旧文件迁移前后读出来的版本（含正文）与新存储逐条相同
    assert versionfiles.versions("d")[:6] == m._load_versions("d")


@pytest.mark.parametrize("point", ["mig_after_blobs", "mig_before_index", "mig_after_index"])
def test_a_crash_inside_the_migration_leaves_the_same_history_and_the_retry_converges(
    client, monkeypatch, point
):
    path, frozen = _legacy()
    before = _list(client)

    def hook(name):
        if name == point:
            raise Crash(name)

    monkeypatch.setattr(vs, "_checkpoint", hook)
    crashed = client.post("/api/versions/d", json={"doc": _doc(2, "new"), "name": "崩了"})
    assert crashed.status_code == 500, "注入的崩溃没有走到（这条用例量不到东西）"
    monkeypatch.setattr(vs, "_checkpoint", lambda name: None)

    assert path.read_bytes() == frozen
    assert _list(client)["versions"] == before["versions"], "崩溃之后读侧看到的历史变了"
    created = _post(client, name="重试")["version"]
    after = _list(client)
    assert after["versions"][:-1] == before["versions"]
    assert after["versions"][-1]["id"] == created["id"]
    assert path.read_bytes() == frozen
    root = vs.store_dir(path.parent, path.stem)
    assert (
        json.loads((root / vs.MIGRATION_NAME).read_text(encoding="utf-8"))["state"] == "committed"
    )
    # 一个信封也不多（崩溃前写下的被复用，不重复）
    assert {p.stem for p in (root / "s").glob("*.json")} == {v["id"] for v in after["versions"]}


def test_downgrade_then_upgrade_imports_only_what_is_new_and_overwrites_nothing(client):
    path, _ = _legacy()
    first = _post(client, name="升级后第一条")["version"]
    renamed = _list(client)["versions"][1]["id"]
    deleted = _list(client)["versions"][2]["id"]
    assert (
        client.patch(f"/api/versions/d/{renamed}", json={"name": "新程序改的名"}).status_code == 200
    )
    assert client.delete(f"/api/versions/d/{deleted}").status_code == 200

    # 回滚到上一个版本：旧程序读冻结的旧文件、追加一条、顺手改了个名，整份写回
    old_program = m._load_versions("d")
    assert first["id"] not in [v["id"] for v in old_program], "旧程序看到了新程序的版本"
    old_program[3]["name"] = "旧程序改的名"
    old_program.append(_ver(50, name="旧程序追加的"))
    m._save_versions("d", old_program)
    rolled_back = path.read_bytes()

    # 再升级：下一次写入把迁移点之后新增的导入
    _post(client, name="再升级第一条")
    names = {v["id"]: v["name"] for v in _list(client)["versions"]}
    assert names[_ver(50)["id"]] == "旧程序追加的", "旧程序期间追加的版本丢了"
    assert deleted not in names, "已经在新存储里删掉的版本被导入复活了"
    assert names[renamed] == "新程序改的名", "导入覆盖了新存储里的改名"
    assert names[_ver(3)["id"]] == "旧3", "旧程序对已有条目的改名流回了新存储（只导入新增）"
    assert first["id"] in names
    assert path.read_bytes() == rolled_back, "导入改了旧文件"
    # 幂等：再写一次不会再导入
    n = len(names)
    _post(client, name="第三次")
    assert len(_list(client)["versions"]) == n + 1


def test_a_second_writer_arriving_mid_migration_waits_for_the_lock(client, monkeypatch):
    """迁移在版本锁内：迁移卡在「块写完、索引未提交」的缝里时，第二个写请求必须等，
    而不是并发迁移——并发的话先迁完又追加了的那一条会被后到的索引提交盖回去。

    确定性：缝里直接在另一个线程发第二个请求；有锁时它阻塞到第一个请求结束，没锁时它当场
    完成并写下自己的版本，随后被第一个请求的索引提交盖掉（`names` 里就缺 B）。"""
    _legacy()
    seen: dict = {}

    def other():
        c = m.app.test_client()
        seen["r"] = c.post("/api/versions/d", json={"doc": _doc(2, "B"), "name": "B"})

    def hook(point):
        if point == "mig_before_index" and "thread" not in seen:
            seen["thread"] = t = threading.Thread(target=other)
            t.start()
            t.join(timeout=1.5)  # 有锁：这里超时返回，B 仍在等；没锁：B 已完成
            seen["b_done_inside_seam"] = not t.is_alive()

    monkeypatch.setattr(vs, "_checkpoint", hook)
    a = client.post("/api/versions/d", json={"doc": _doc(2, "A"), "name": "A"})
    seen["thread"].join(30)
    monkeypatch.setattr(vs, "_checkpoint", lambda name: None)

    assert a.status_code == 200 and seen["r"].status_code == 200
    assert seen["b_done_inside_seam"] is False, "第二个写请求在迁移途中就完成了：迁移没在锁内"
    names = [v["name"] for v in _list(client)["versions"]]
    assert "A" in names and "B" in names and len(names) == 8, names


def test_append_writes_one_envelope_and_the_index_whatever_the_history_length(client, monkeypatch):
    """大文档追加：写入的文件集合恒为 {新信封, 索引}，字节只跟这一版有关，与历史长度无关
    （断言写入的文件与字节，不靠计时）。旧格式每次追加整份重写：几十版就是几十倍。"""
    writes: list[tuple[str, int]] = []
    real = atomicio.write_bytes

    def spy(path, data, *a, **kw):
        writes.append((str(path), len(data)))
        return real(path, data, *a, **kw)

    measured = {}
    for history in (3, 40):
        doc_id = f"big{history}"
        for i in range(history):
            _post(client, doc_id, name=f"h{i}", doc=_doc(300, f"h{i}"))
        writes.clear()
        monkeypatch.setattr(atomicio, "write_bytes", spy)
        created = _post(client, doc_id, name="measured", doc=_doc(300, "measured"))["version"]
        monkeypatch.setattr(atomicio, "write_bytes", real)
        r = vs.store_dir(m._versions_path(doc_id).parent, m._versions_path(doc_id).stem)
        rel = sorted(Path(p).relative_to(r).as_posix() for p, _ in writes)
        assert rel == sorted(["index.json", f"s/{created['id']}.json"]), rel
        measured[history] = sum(n for _, n in writes)
    one_doc = len(atomicio.dumps_json(_doc(300, "measured")))
    legacy_would_write = 41 * one_doc
    assert measured[40] < one_doc + 40 * 400 + 2000, "写入量跟着历史长度走了"
    assert measured[40] - measured[3] < 37 * 400, "历史每长一条只该多出索引里的一行"
    assert measured[40] * 10 < legacy_would_write


def test_unreadable_legacy_does_not_block_an_existing_store(client):
    """已迁移的文档，旧文件之后被回滚的旧程序写坏：新存储不受影响，写入照常。"""
    path, _ = _legacy()
    _post(client, name="迁移触发")
    path.write_bytes(b'{"versions": [')  # 旧程序写到一半断电
    after = _post(client, name="仍能写")["version"]
    assert after["name"] == "仍能写"
    names = [v["name"] for v in _list(client)["versions"]]
    assert names[-2:] == ["迁移触发", "仍能写"] and len(names) == 8


def test_thumbnails_survive_the_migration(client):
    path, _ = _legacy()
    vid = _ver(0)["id"]
    assert (
        client.put(f"/api/versions/d/{vid}/thumb", data=PNG, content_type="image/png").status_code
        == 200
    )
    _post(client, name="触发迁移")
    row = next(v for v in _list(client)["versions"] if v["id"] == vid)
    assert row["thumb"] == "png"
    assert client.get(f"/api/versions/d/{vid}/thumb").data == PNG
    assert vs.has_store(path.parent, path.stem)


def test_a_store_born_document_imports_what_a_rolled_back_program_wrote_to_a_fresh_legacy_file(
    client,
):
    """新存储里出生的文档没有迁移记录；降级后旧程序在 `<doc>.json` 里写了版本，再升级时不能漏掉。"""
    first = _post(client, "born", name="新1")["version"]
    second = _post(client, "born", name="新2")["version"]
    assert not m._versions_path("born").exists()
    # 旧程序：不知道 `.vx`，从空起步写旧文件；其中一条与新存储同 id 但内容不同
    old_program = [
        _ver(60, name="旧程序一"),
        _ver(61, name="旧程序二"),
        {**_ver(62, name="撞id"), "id": first["id"]},
    ]
    m._save_versions("born", old_program)
    rolled_back = m._versions_path("born").read_bytes()

    _post(client, "born", name="再升级")
    names = {v["id"]: v["name"] for v in _list(client, "born")["versions"]}
    assert names[_ver(60)["id"]] == "旧程序一" and names[_ver(61)["id"]] == "旧程序二"
    assert names[first["id"]] == "新1", "导入覆盖了新存储里已有的条目"
    assert second["id"] in names
    assert m._versions_path("born").read_bytes() == rolled_back, "导入改了旧文件"
    n = len(names)
    _post(client, "born", name="再写一次")
    assert len(_list(client, "born")["versions"]) == n + 1, "旧文件被重复导入"


def test_an_unserializable_legacy_file_leaves_no_trace_and_the_repaired_file_is_fully_imported(
    client,
):
    """旧文件读得进、写不出（`1e400` 读成 inf）：迁移零写入，开空历史；修好旧文件后再写，旧 id 必须全数导入。"""
    path, good = _legacy("bad", n=4)
    data = json.loads(good)
    broken = good.replace(b'"sizePt": 9', b'"sizePt": 1e400', 1)
    assert broken != good
    path.write_bytes(broken)
    _post(client, "bad", name="空历史上的第一条")
    root = vs.store_dir(path.parent, "bad")
    assert not (root / "migration.json").exists(), "失败的迁移留下了 pending 记录"
    assert path.read_bytes() == broken, "旧文件被动了"
    assert [v["name"] for v in _list(client, "bad")["versions"]] == ["空历史上的第一条"]

    path.write_bytes(good)  # 用户修好旧文件
    _post(client, "bad", name="修好后再写")
    ids = {v["id"] for v in _list(client, "bad")["versions"]}
    assert {v["id"] for v in data["versions"]} <= ids, "修好的旧历史被静默丢弃"
    assert path.read_bytes() == good


def test_a_crash_after_the_index_commit_then_a_rolled_back_append_keeps_pruned_versions_pruned(
    client, monkeypatch
):
    """首次迁移在索引提交后崩溃（pending 留着，策略已裁掉一部分旧版本），重试前旧程序又追加了旧文件：
    重试按分叉只导入新增 id，被裁掉的旧版本不复活（否则手动档会反过来挤掉较新的检查点）。"""
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 3)
    path = m._versions_path("d")
    m._versions_path("d").parent.mkdir(parents=True, exist_ok=True)
    olds = [_ver(i) for i in range(5)]
    path.write_bytes(json.dumps({"versions": olds}).encode())

    def hook(name):
        if name == "mig_after_index":
            raise Crash(name)

    monkeypatch.setattr(vs, "_checkpoint", hook)
    crashed = client.post("/api/versions/d", json={"doc": _doc(2, "new"), "name": "崩了"})
    assert crashed.status_code == 500, "注入的崩溃没有走到（这条用例量不到东西）"
    monkeypatch.setattr(vs, "_checkpoint", lambda name: None)

    root = vs.store_dir(path.parent, path.stem)
    rec = json.loads((root / vs.MIGRATION_NAME).read_text(encoding="utf-8"))
    assert rec["state"] == "pending", "前置条件：崩溃点应把 pending 留在磁盘上"
    kept = {e["id"] for e in vs.entries(path.parent, path.stem)}
    pruned = {v["id"] for v in olds} - kept
    assert pruned, "前置条件：策略应已裁掉一部分旧版本"

    newer = [
        _ver(10, auto=True),
        _ver(11, auto=True),
    ]  # 旧程序在重试前追加（自动档，先于手动档被牺牲）
    path.write_bytes(json.dumps({"versions": olds + newer}).encode())
    _post(client, name="重试")
    ids = {v["id"] for v in _list(client)["versions"]}
    assert not (ids & pruned), "被裁掉的旧版本复活了"
    assert ids & kept, "较新的手动检查点被被裁掉的旧版本挤掉了"
