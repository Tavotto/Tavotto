"""版本时间线新存储 `engine/versionstore.py`（ADR 0140，D1a：模块已交付、**尚未接线**）。

真实文件系统 + 临时目录，不碰用户数据。几组看护：

* 往返 / 追加只写一个信封 + 索引（断言写入的路径集合，不靠计时）；
* 确定性崩溃点（注入失败，不靠 kill）+ 一组短的真 SIGKILL 回归（POSIX）；
* 索引删除 / 截断 / 乱码 → 重建等价（含改名与顺序）；
* 迁移：三个崩溃点、旧文件字节不变、分叉导入、与现行 `_load_versions` 逐字段一致；
* 现行裁剪策略逐条对拍 `app._save_versions`（D1 保持语义不变的证据）。
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, versionstore as vs

SRC = Path(m.__file__).resolve().parent.parent
POSIX = os.name == "posix"


class Crash(Exception):
    """注入的崩溃：在指定检查点之后什么都没再发生。"""


def _doc(n: int = 1, tag: str = "x") -> dict:
    return {
        "schema": 2,
        "name": f"doc-{tag}",
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
            }
            for i in range(n)
        ],
        "guides": [],
    }


def _ver(i: int, **kw) -> dict:
    base = {
        "id": f"v{0x1000 + i:x}-{i:x}",
        "name": f"{i:02d}-01 10:00",
        "ts": 1_000_000 + i,
        "auto": False,
        "named": False,
        "description": "",
        "doc": _doc(2, str(i)),
    }
    base.update(kw)
    return base


@pytest.fixture
def vdir(tmp_path):
    d = tmp_path / "versions"
    d.mkdir()
    return d


@pytest.fixture
def crash_at(monkeypatch):
    def arm(name: str):
        def hook(point: str):
            if point == name:
                raise Crash(point)

        monkeypatch.setattr(vs, "_checkpoint", hook)

    return arm


@pytest.fixture
def legacy_app(tmp_path, monkeypatch):
    """用**现行** `app._save_versions` 写旧文件（项目外：`layouts/_versions/`）。"""
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    return tmp_path / "versions"


def _write_legacy(doc_id: str, versions: list[dict]) -> Path:
    m._save_versions(doc_id, versions)
    return m._versions_path(doc_id)


def _seed(vdir: Path, doc_id: str, n: int, **kw) -> list[dict]:
    out = []
    for i in range(n):
        v = _ver(i, **kw)
        vs.append(vdir, doc_id, v, now=i)
        out.append(v)
    return out


# --------------------------------------------------------------- 往返


def test_append_read_update_delete_roundtrip(vdir):
    vers = _seed(vdir, "d1", 3)
    assert vs.entries(vdir, "d1") == [{k: x for k, x in v.items() if k != "doc"} for v in vers]
    for v in vers:
        assert vs.read(vdir, "d1", v["id"]) == v
    out = vs.update(vdir, "d1", vers[1]["id"], {"name": "改名", "named": True}, now=9)
    assert out["name"] == "改名" and out["named"] is True
    again = vs.read(vdir, "d1", vers[1]["id"])
    assert again["name"] == "改名" and again["doc"] == vers[1]["doc"]
    assert vs.update(vdir, "d1", vers[1]["id"], {}, remove=["description"], now=10)
    assert "description" not in vs.read(vdir, "d1", vers[1]["id"])
    assert vs.delete(vdir, "d1", vers[0]["id"], now=11) is True
    assert vs.delete(vdir, "d1", vers[0]["id"], now=12) is False
    assert vs.read(vdir, "d1", vers[0]["id"]) is None
    assert [e["id"] for e in vs.entries(vdir, "d1")] == [vers[1]["id"], vers[2]["id"]]
    assert not (vs.store_dir(vdir, "d1") / "s" / f"{vers[0]['id']}.json").exists()
    assert vs.update(vdir, "d1", "v1-1", {"name": "x"}, now=13) is None
    assert vs.entries(vdir, "nope") is None and vs.read(vdir, "nope", "v1-1") is None


def test_duplicate_id_and_bad_id_are_refused(vdir):
    _seed(vdir, "d1", 1)
    with pytest.raises(vs.VersionStoreError):
        vs.append(vdir, "d1", _ver(0), now=1)
    for bad in ("../x", "v1-1/../../y", "V1-1", "", "v1-1\n"):
        with pytest.raises(vs.VersionStoreError):
            vs.append(vdir, "d1", _ver(5, id=bad), now=1)
    assert [e["id"] for e in vs.entries(vdir, "d1")] == [_ver(0)["id"]]


def test_append_writes_one_envelope_and_the_index_regardless_of_history(vdir, monkeypatch):
    written: list[Path] = []
    real = atomicio.write_bytes

    def spy(path, data, **kw):
        written.append((Path(path), len(data)))
        return real(path, data, **kw)

    monkeypatch.setattr(atomicio, "write_bytes", spy)
    root = vs.store_dir(vdir, "d1")
    sizes = []
    for n in (1, 5, 60):
        _seed(vdir, "other%d" % n, n - 1)  # 把历史垫到 n-1 条
        written.clear()
        v = _ver(n + 100)
        vs.append(vdir, "other%d" % n, v, now=1)
        r = vs.store_dir(vdir, "other%d" % n)
        assert {p for p, _ in written} == {r / "s" / f"{v['id']}.json", r / "index.json"}
        env = next(sz for p, sz in written if p.parent.name == "s")
        sizes.append(env)
    assert max(sizes) - min(sizes) <= 2, "信封字节数与历史长度无关（仅序号位数不同）"
    assert not root.exists()


# --------------------------------------------------------------- 崩溃


def test_crash_between_envelope_and_index_leaves_the_version_invisible(vdir, crash_at):
    _seed(vdir, "d1", 3)
    crash_at("after_envelope")
    with pytest.raises(Crash):
        vs.append(vdir, "d1", _ver(7), now=50)
    ids = [e["id"] for e in vs.entries(vdir, "d1")]
    assert _ver(7)["id"] not in ids and len(ids) == 3
    assert vs.read(vdir, "d1", _ver(7)["id"]) is None
    orphan = vs.store_dir(vdir, "d1") / "s" / f"{_ver(7)['id']}.json"
    assert orphan.exists()
    assert vs.gc_orphans(vdir, "d1", now=time.time() + 7200) == [orphan.name]
    assert not orphan.exists() and len(vs.entries(vdir, "d1")) == 3


def test_crash_before_first_index_commit_is_empty_history_not_adoption(vdir, crash_at):
    crash_at("after_envelope")
    with pytest.raises(Crash):
        vs.append(vdir, "d1", _ver(1), now=1)
    assert vs.has_store(vdir, "d1")
    assert vs.entries(vdir, "d1") == []  # 空索引已是权威：孤儿不会被「重建」收养
    assert len(vs.gc_orphans(vdir, "d1", now=time.time() + 7200)) == 1


def test_crash_after_index_commit_keeps_the_version(vdir, crash_at):
    _seed(vdir, "d1", 2)
    crash_at("after_index")
    with pytest.raises(Crash):
        vs.append(vdir, "d1", _ver(9), now=5)
    assert [e["id"] for e in vs.entries(vdir, "d1")][-1] == _ver(9)["id"]
    assert vs.read(vdir, "d1", _ver(9)["id"]) == _ver(9)


def test_delete_crash_after_index_leaves_only_an_orphan(vdir, crash_at):
    vers = _seed(vdir, "d1", 3)
    crash_at("after_index")
    with pytest.raises(Crash):
        vs.delete(vdir, "d1", vers[0]["id"], now=5)
    assert vers[0]["id"] not in [e["id"] for e in vs.entries(vdir, "d1")]
    assert (vs.store_dir(vdir, "d1") / "s" / f"{vers[0]['id']}.json").exists()
    assert vs.gc_orphans(vdir, "d1", now=time.time() + 7200)


_KILL_WORKER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tavotto.engine import versionstore as vs
vdir = Path(sys.argv[2])
i = int(sys.argv[3])
doc = {"schema": 2, "name": "k", "page": {"w": 1, "h": 1}, "objects": [{"id": "o%d" % j} for j in range(200)], "guides": []}
while True:
    i += 1
    v = {"id": "v%x-%x" % (0x2000 + i, i), "name": "n%d" % i, "ts": i, "auto": False, "named": False, "description": "", "doc": doc}
    vs.append(vdir, "k", v, now=i)
    print(v["id"], flush=True)
"""


@pytest.mark.skipif(not POSIX, reason="SIGKILL 回归只在 POSIX 上")
def test_random_sigkill_never_loses_an_acknowledged_append(vdir):
    acked: list[str] = []
    counter = 0
    for rnd in range(6):
        proc = subprocess.Popen(
            [sys.executable, "-c", _KILL_WORKER, str(SRC), str(vdir), str(counter)],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SRC)},
        )
        time.sleep(0.25 + 0.05 * rnd)
        proc.kill()
        out, _ = proc.communicate()
        got = [ln.strip() for ln in out.splitlines() if ln.strip()]
        acked += got
        counter += len(got) + 5
    idx = vs.list_index(vdir, "k")
    assert idx is not None and not idx.get("recovered")
    live = {e["id"] for e in idx["entries"]}
    assert set(acked) <= live, "已回执的追加一条都不许丢"
    for e in idx["entries"]:  # 每个索引条目的信封都完整可读
        assert vs.read(vdir, "k", e["id"])["id"] == e["id"]


# --------------------------------------------------------------- 索引损坏 / 重建


def _built(vdir):
    vers = _seed(vdir, "d1", 6)
    vs.update(vdir, "d1", vers[2]["id"], {"name": "用户改的名字", "named": True}, now=20)
    return vers, vs.entries(vdir, "d1")


@pytest.mark.parametrize("damage", ["delete", "truncate", "garbage", "wrong_shape"])
def test_rebuild_after_index_damage_is_equivalent_including_rename(vdir, damage):
    _, before = _built(
        vdir,
    )
    idxp = vs.store_dir(vdir, "d1") / "index.json"
    raw = idxp.read_bytes()
    if damage == "delete":
        idxp.unlink()
    elif damage == "truncate":
        idxp.write_bytes(raw[: len(raw) // 2])
    elif damage == "garbage":
        idxp.write_bytes(b"\x00\xffnot json")
    else:
        idxp.write_bytes(b'{"format": 1, "entries": "x"}')
    # 只读：内存重建，不落盘
    snap = {p.name: p.read_bytes() for p in vs.store_dir(vdir, "d1").iterdir() if p.is_file()}
    idx = vs.list_index(vdir, "d1")
    assert idx["recovered"] is True
    assert [vs.public_entry(x) for x in idx["entries"]] == before
    assert snap == {
        p.name: p.read_bytes() for p in vs.store_dir(vdir, "d1").iterdir() if p.is_file()
    }
    # 写路径：重建落盘，坏字节留副本
    vs.append(vdir, "d1", _ver(50), now=30)
    after = vs.entries(vdir, "d1")
    assert after[:-1] == before and after[-1]["id"] == _ver(50)["id"]
    assert not vs.list_index(vdir, "d1").get("recovered")
    corrupt = list(vs.store_dir(vdir, "d1").glob("index.corrupt-*.json"))
    assert len(corrupt) == (0 if damage == "delete" else 1)


def test_index_unreadable_or_from_the_future_refuses_without_rebuilding(vdir):
    _built(vdir)
    idxp = vs.store_dir(vdir, "d1") / "index.json"
    idxp.write_bytes(b'{"format": 99, "entries": []}')
    with pytest.raises(vs.IndexUnreadable):
        vs.list_index(vdir, "d1")
    with pytest.raises(vs.IndexUnreadable):
        vs.append(vdir, "d1", _ver(60), now=1)
    idxp.unlink()
    idxp.mkdir()  # 读它是 OSError（目录）
    with pytest.raises(vs.IndexUnreadable):
        vs.list_index(vdir, "d1")
    assert idxp.is_dir() and not list(vs.store_dir(vdir, "d1").glob("index.corrupt-*"))


def test_one_corrupt_envelope_only_costs_that_version(vdir):
    vers = _seed(vdir, "d1", 4)
    bad = vs.store_dir(vdir, "d1") / "s" / f"{vers[1]['id']}.json"
    raw = bad.read_bytes()
    bad.write_bytes(raw[:-20])
    with pytest.raises(vs.EnvelopeCorrupt):
        vs.read(vdir, "d1", vers[1]["id"])
    assert vs.read(vdir, "d1", vers[2]["id"]) == vers[2]
    bad.unlink()
    with pytest.raises(vs.EnvelopeCorrupt):
        vs.read(vdir, "d1", vers[1]["id"])
    # 重建：缺的赔掉自己，其余在
    (vs.store_dir(vdir, "d1") / "index.json").unlink()
    assert [e["id"] for e in vs.entries(vdir, "d1")] == [vers[i]["id"] for i in (0, 2, 3)]


def test_gc_orphans_age_and_both_tmp_directories(vdir):
    vers = _seed(vdir, "d1", 2)
    root = vs.store_dir(vdir, "d1")
    young = root / "s" / "v9-9.json"
    old = root / "s" / "va-a.json"
    for p in (young, old):
        p.write_bytes(b"x")
    os.utime(old, (1, 1))
    tmp_s = root / "s" / f"{vers[0]['id']}.json.99999999.1.tmp"
    tmp_r = root / "index.json.99999999.2.tmp"
    for p in (tmp_s, tmp_r):
        p.write_bytes(b"x")
        os.utime(p, (1, 1))
    removed = vs.gc_orphans(vdir, "d1", now=time.time())
    assert sorted(removed) == sorted([old.name, tmp_s.name, tmp_r.name])
    assert young.exists() and not old.exists() and not tmp_s.exists() and not tmp_r.exists()
    assert all(vs.read(vdir, "d1", v["id"]) == v for v in vers)


def test_gc_never_deletes_envelopes_while_the_index_is_missing(vdir):
    vers = _seed(vdir, "d1", 2)
    (vs.store_dir(vdir, "d1") / "index.json").unlink()
    for p in (vs.store_dir(vdir, "d1") / "s").iterdir():
        os.utime(p, (1, 1))
    assert vs.gc_orphans(vdir, "d1", now=time.time()) == []
    assert [e["id"] for e in vs.entries(vdir, "d1")] == [v["id"] for v in vers]


# --------------------------------------------------------------- 迁移


def _legacy_versions(n: int = 6) -> list[dict]:
    out = []
    for i in range(n):
        v = _ver(i, canvasId="c1", canvasName="图一") if i % 2 else _ver(i)
        if i == 2:
            v.update(auto=True, moment="export")
        if i == 3:
            v.update(named=True, name="命名节点")
        out.append(v)
    return out


def test_migration_matches_current_loader_field_by_field_and_freezes_the_legacy_file(
    legacy_app, vdir
):
    versions = _legacy_versions()
    legacy = _write_legacy("d1", versions)
    before = legacy.read_bytes()
    stat = legacy.stat()
    res = vs.migrate_legacy(legacy.parent, "d1", legacy, now=1)
    assert res == {"status": "migrated", "imported": 0}
    loaded = m._load_versions("d1")
    assert vs.entries(legacy.parent, "d1") == [
        {k: x for k, x in v.items() if k != "doc"} for v in loaded
    ]
    for v in loaded:
        assert vs.read(legacy.parent, "d1", v["id"]) == v
    assert legacy.read_bytes() == before, "旧文件冻结：字节不变"
    assert legacy.stat().st_mtime_ns == stat.st_mtime_ns
    assert vs.migrate_legacy(legacy.parent, "d1", legacy, now=2) == {
        "status": "already",
        "imported": 0,
    }
    rec = json.loads((vs.store_dir(legacy.parent, "d1") / "migration.json").read_bytes())
    assert rec["state"] == "committed" and rec["count"] == len(versions)
    assert rec["sha_full"] == __import__("hashlib").sha256(before).hexdigest()


@pytest.mark.parametrize("point", ["mig_after_blobs", "mig_before_index", "mig_after_index"])
def test_migration_crash_points_rerun_to_the_same_result(legacy_app, point, monkeypatch):
    legacy = _write_legacy("d1", _legacy_versions())
    vd = legacy.parent
    before = legacy.read_bytes()

    def boom(p):
        if p == point:
            raise Crash(p)

    monkeypatch.setattr(vs, "_checkpoint", boom)
    with pytest.raises(Crash):
        vs.migrate_legacy(vd, "d1", legacy, now=1)
    assert legacy.read_bytes() == before
    if point == "mig_after_index":
        assert vs.has_store(vd, "d1")  # 索引已提交：新存储已是权威且完整
    else:
        assert not vs.has_store(vd, "d1")  # 迁移进行中：读侧仍以旧文件为权威
        assert vs.entries(vd, "d1") is None
        with pytest.raises(vs.VersionStoreError):
            vs.append(vd, "d1", _ver(99), now=1)
    monkeypatch.setattr(vs, "_checkpoint", lambda p: None)
    res = vs.migrate_legacy(vd, "d1", legacy, now=2)
    assert res["status"] == ("already" if point == "mig_after_index" else "migrated")
    loaded = m._load_versions("d1")
    assert [vs.read(vd, "d1", v["id"]) for v in loaded] == loaded
    assert [e["id"] for e in vs.entries(vd, "d1")] == [v["id"] for v in loaded]
    assert legacy.read_bytes() == before
    rec = json.loads((vs.store_dir(vd, "d1") / "migration.json").read_bytes())
    assert rec["state"] == "committed"


def test_rolled_back_program_appending_to_the_frozen_file_is_imported_not_lost(legacy_app):
    versions = _legacy_versions(5)
    legacy = _write_legacy("d1", versions)
    vd = legacy.parent
    vs.migrate_legacy(vd, "d1", legacy, now=1)
    # 新存储里：追加一版、删一个迁移时就有的版本
    vs.append(vd, "d1", _ver(70), now=2)
    vs.delete(vd, "d1", versions[0]["id"], now=3)
    # 降级到旧程序：它在冻结文件上追加两版（并仍带着被新存储删掉的 versions[0]）
    old = m._load_versions("d1")
    old += [_ver(80), _ver(81, canvasId="c9")]
    frozen_before = legacy.read_bytes()
    m._save_versions("d1", old)
    assert legacy.read_bytes() != frozen_before
    res = vs.migrate_legacy(vd, "d1", legacy, now=4)
    assert res == {"status": "imported", "imported": 2}
    ids = [e["id"] for e in vs.entries(vd, "d1")]
    assert versions[0]["id"] not in ids, "新存储里删掉的不许因分叉导入而复活"
    assert ids[-2:] == [_ver(80)["id"], _ver(81)["id"]] and _ver(70)["id"] in ids
    assert vs.read(vd, "d1", _ver(81)["id"]) == _ver(81, canvasId="c9")
    assert vs.migrate_legacy(vd, "d1", legacy, now=5) == {"status": "already", "imported": 0}
    # 同一个旧文件再改一次（又追加）：只导入新增的
    m._save_versions("d1", [*m._load_versions("d1"), _ver(82)])
    assert vs.migrate_legacy(vd, "d1", legacy, now=6)["imported"] == 1


def test_lost_index_after_migration_rebuilds_and_does_not_remigrate(legacy_app):
    legacy = _write_legacy("d1", _legacy_versions(4))
    vd = legacy.parent
    vs.migrate_legacy(vd, "d1", legacy, now=1)
    vs.delete(vd, "d1", _legacy_versions(4)[0]["id"], now=2)
    expect = vs.entries(vd, "d1")
    (vs.store_dir(vd, "d1") / "index.json").unlink()
    res = vs.migrate_legacy(vd, "d1", legacy, now=3)
    assert res["status"] == "already"
    assert vs.entries(vd, "d1") == expect
    assert _legacy_versions(4)[0]["id"] not in [e["id"] for e in vs.entries(vd, "d1")]


def test_unreadable_legacy_is_refused_and_creates_nothing(legacy_app):
    legacy = _write_legacy("d1", _legacy_versions(2))
    vd = legacy.parent
    for payload in (
        b'{"versions": [{"id": "v1-1", "doc": NaN}]}',
        legacy.read_bytes()[:50],
        b'{"versions": "x"}',
        b'{"versions": [{"id": "../evil", "doc": {}}]}',
        b'{"versions": [{"id": "v1-1"}]}',
    ):
        legacy.write_bytes(payload)
        with pytest.raises(vs.LegacyUnreadable):
            vs.migrate_legacy(vd, "d1", legacy, now=1)
        assert legacy.read_bytes() == payload
        assert not vs.store_dir(vd, "d1").exists()
    legacy.write_bytes(b'{"versions": [{"id": "v1-1", "doc": 1e400}]}')
    with pytest.raises(vs.LegacyUnreadable):
        vs.migrate_legacy(vd, "d1", legacy, now=1)
    assert vs.migrate_legacy(vd, "ghost", vd / "ghost.json", now=1)["status"] == "no_legacy"


def test_init_empty_beside_an_unreadable_legacy_file_marks_it_recoverable(legacy_app):
    legacy = _write_legacy("d1", _legacy_versions(2))
    legacy.write_bytes(legacy.read_bytes()[:40])
    vd, frozen = legacy.parent, legacy.read_bytes()
    idx = vs.init_empty(vd, "d1", now=1, legacy_path=legacy)
    assert idx["entries"] == [] and idx["legacy_unreadable"]["name"] == "d1.json"
    assert idx["legacy_unreadable"]["size"] == len(frozen)
    vs.append(vd, "d1", _ver(1), now=2)
    assert len(vs.entries(vd, "d1")) == 1
    assert vs.list_index(vd, "d1")["legacy_unreadable"]["name"] == "d1.json"
    assert legacy.read_bytes() == frozen


def test_duplicate_ids_in_a_legacy_file_keep_the_first(legacy_app):
    legacy = _write_legacy("d1", _legacy_versions(2))
    data = json.loads(legacy.read_bytes())
    data["versions"].append({**data["versions"][0], "name": "撞 id 的后来者"})
    legacy.write_bytes(json.dumps(data).encode())
    vd = legacy.parent
    vs.migrate_legacy(vd, "d1", legacy, now=1)
    assert len(vs.entries(vd, "d1")) == 2
    assert vs.entries(vd, "d1")[0]["name"] != "撞 id 的后来者"
    rec = json.loads((vs.store_dir(vd, "d1") / "migration.json").read_bytes())
    assert rec["dropped_duplicates"] == 1


@pytest.mark.parametrize("doc_id", ["d1", "index", "migration", "s", "thumbs", "v1-1"])
def test_index_never_shares_a_name_with_the_legacy_file(legacy_app, monkeypatch, doc_id):
    """新索引绝不与旧文件同名：旧程序把 `<doc_id>.json` 里没有 `doc` 的条目当历史，
    下一次整写就静默毁掉它（ADR 0140 §二）。量的是**迁移 + 追加 + 改名 + 删除全程写过的路径**。"""
    legacy = _write_legacy(doc_id, _legacy_versions(3))
    vd = legacy.parent
    before = legacy.read_bytes()
    paths: list[Path] = []
    real = atomicio.write_bytes

    def spy(path, data, **kw):
        paths.append(Path(path))
        return real(path, data, **kw)

    monkeypatch.setattr(atomicio, "write_bytes", spy)
    vs.migrate_legacy(vd, doc_id, legacy, now=1)
    vs.append(vd, doc_id, _ver(40), now=2)
    vs.update(vd, doc_id, _ver(40)["id"], {"name": "n"}, now=3)
    vs.delete(vd, doc_id, _ver(40)["id"], now=4)
    assert paths and legacy not in paths
    assert vs.store_dir(vd, doc_id) != legacy and vs.store_dir(vd, doc_id) != vd / "thumbs"
    assert all(vs.store_dir(vd, doc_id) in p.parents for p in paths)
    assert legacy.read_bytes() == before


# --------------------------------------------------------------- 现行策略对拍


def test_constants_mirror_app():
    assert vs.KEEP_AUTO == m.VERSION_KEEP_AUTO
    assert vs.KEEP_TOTAL == m.VERSION_KEEP_TOTAL
    assert vs.KEEP_BYTES == m.VERSION_KEEP_BYTES
    assert vs.MOMENTS == m.VERSION_MOMENTS
    assert vs.VERSION_ID_RE.pattern == m._VERSION_ID_RE.pattern
    assert vs._DEFAULT_NAME.pattern == m._DEFAULT_VERSION_NAME.pattern


def _random_versions(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        v = _ver(i, doc=_doc(rng.randint(1, 30), str(i)))
        roll = rng.random()
        if roll < 0.45:
            v.update(auto=True)
        elif roll < 0.6:
            v.update(auto=True, moment=rng.choice(sorted(vs.MOMENTS)))
        elif roll < 0.75:
            v.update(named=True, name=f"命名{i}")
        elif roll < 0.85:
            for k in ("named",):  # 旧条目（ADR 0101 之前）没有 named 字段
                v.pop(k)
            v["name"] = rng.choice([f"{i % 12 + 1:02d}-01 10:00", f"用户起的名{i}"])
        out.append(v)
    return out


def _meta_of(v: dict, seq: int) -> dict:
    entry = {k: x for k, x in v.items() if k != "doc"}
    env, doc_len = vs._envelope(entry, v["doc"], seq)
    return vs._meta(entry, len(env), doc_len, vs._head_sha(env), seq)


def test_legacy_size_equals_dumps_and_kind_matches_app():
    rng = random.Random(7)
    for v in _random_versions(rng, 80):
        meta = _meta_of(v, 1)
        assert meta["_legacy_bytes"] == len(atomicio.dumps_json(v))
        assert vs.kind(v) == m._version_kind(v) and vs.is_named(v) == m._is_named(v)


@pytest.mark.parametrize("seed", range(12))
def test_legacy_policy_matches_save_versions(legacy_app, monkeypatch, seed):
    """D1 保持语义逐字不变的证据：同一组输入，新策略保留的 id 与现行 `_save_versions` 逐条相同。"""
    rng = random.Random(seed)
    auto, total, size = rng.choice(
        [(3, 8, 6_000), (5, 12, 9_000), (60, 120, 24_000), (2, 4, 1_500)]
    )
    for mod in (m, vs):
        monkeypatch.setattr(mod, "VERSION_KEEP_AUTO" if mod is m else "KEEP_AUTO", auto)
        monkeypatch.setattr(mod, "VERSION_KEEP_TOTAL" if mod is m else "KEEP_TOTAL", total)
        monkeypatch.setattr(mod, "VERSION_KEEP_BYTES" if mod is m else "KEEP_BYTES", size)
    versions = _random_versions(rng, rng.randint(1, 45))
    keep_i = rng.choice([None, rng.randrange(len(versions))])
    keep_v = None if keep_i is None else versions[keep_i]
    m._save_versions("p", versions, keep=keep_v)
    expect = [v["id"] for v in m._load_versions("p")]
    metas = [_meta_of(v, i + 1) for i, v in enumerate(versions)]
    got = [x["id"] for x in vs.legacy_policy(metas, None if keep_v is None else keep_v["id"])]
    assert got == expect


def test_incremental_append_with_legacy_policy_tracks_the_current_save_versions(
    legacy_app, vdir, monkeypatch
):
    for mod, names in (
        (m, ("VERSION_KEEP_AUTO", "VERSION_KEEP_TOTAL", "VERSION_KEEP_BYTES")),
        (vs, ("KEEP_AUTO", "KEEP_TOTAL", "KEEP_BYTES")),
    ):
        for name, val in zip(names, (4, 9, 7_000)):
            monkeypatch.setattr(mod, name, val)
    rng = random.Random(3)
    cur: list[dict] = []
    for i, v in enumerate(_random_versions(rng, 40)):
        cur.append(v)
        m._save_versions("p", cur)
        cur = m._load_versions("p")
        vs.append(vdir, "p", v, now=i, policy=vs.legacy_policy)
        assert [e["id"] for e in vs.entries(vdir, "p")] == [x["id"] for x in cur], f"第 {i} 步"
        survivors = {x["id"] for x in cur}
        files = {p.stem for p in (vs.store_dir(vdir, "p") / "s").glob("*.json")}
        assert files == survivors, "被裁的版本信封也随之删掉"


def test_update_runs_the_policy_and_protects_the_edited_entry(vdir, monkeypatch):
    monkeypatch.setattr(vs, "KEEP_TOTAL", 3)
    vers = [
        _ver(i, named=(i == 0), name=f"命名{i}" if i == 0 else f"{i:02d}-01 10:00")
        for i in range(4)
    ]
    for i, v in enumerate(vers):
        vs.append(vdir, "d1", v, now=i)  # 不带策略：4 条里 3 条未命名，没超
    monkeypatch.setattr(vs, "KEEP_TOTAL", 2)
    # 取消命名：旧节点变回未命名，未命名数 4 > 2；被改的这一条受护，别的最旧未命名先裁
    vs.update(vdir, "d1", vers[0]["id"], {"named": False}, now=9, policy=vs.legacy_policy)
    ids = [e["id"] for e in vs.entries(vdir, "d1")]
    assert vers[0]["id"] in ids and vers[3]["id"] in ids and len(ids) == 2


# --------------------------------------------------------------- Codex #1213 的三条


def test_metadata_update_that_never_commits_is_not_visible(vdir, crash_at, monkeypatch):
    """P1：信封不可变。改名写新一代信封，索引提交前崩溃时 read() 仍读已提交的那一代。"""
    vers = _seed(vdir, "d1", 2)
    vid = vers[0]["id"]
    vs.update(vdir, "d1", vid, {"name": "已提交"}, now=5)
    crash_at("after_envelope")
    with pytest.raises(Crash):
        vs.update(vdir, "d1", vid, {"name": "未提交"}, now=6)
    assert vs.read(vdir, "d1", vid)["name"] == "已提交"
    assert vs.entries(vdir, "d1")[0]["name"] == "已提交"
    # 下一次成功的更新照常生效，且每个 id 只剩生效的那一代
    monkeypatch.setattr(vs, "_checkpoint", lambda p: None)
    vs.update(vdir, "d1", vid, {"name": "第三次"}, now=7)
    assert vs.read(vdir, "d1", vid)["name"] == "第三次"
    files = sorted(p.name for p in (vs.store_dir(vdir, "d1") / "s").iterdir())
    assert files == sorted(
        f"{x}.json" if x != vid else f"{vid}.2.json" for x in (v["id"] for v in vers)
    )


def test_uncommitted_generation_is_orphan_garbage_and_rebuild_keeps_committed_rename(
    vdir, crash_at
):
    vers = _seed(vdir, "d1", 1)
    vid = vers[0]["id"]
    vs.update(vdir, "d1", vid, {"name": "已提交"}, now=5)
    crash_at("after_envelope")
    with pytest.raises(Crash):
        vs.update(vdir, "d1", vid, {"name": "未提交"}, now=6)
    s = vs.store_dir(vdir, "d1") / "s"
    assert sorted(p.name for p in s.iterdir()) == [f"{vid}.1.json", f"{vid}.2.json"]
    assert vs.gc_orphans(vdir, "d1", now=time.time() + 7200) == [f"{vid}.2.json"]
    (vs.store_dir(vdir, "d1") / "index.json").unlink()
    assert vs.entries(vdir, "d1")[0]["name"] == "已提交"
    assert vs.read(vdir, "d1", vid)["name"] == "已提交"


def test_fork_import_crash_between_commit_and_record_still_records_every_legacy_id(
    legacy_app, monkeypatch
):
    """P2：崩在提交与迁移记录之间，重试时 fresh 为空；记录仍要含观察到的全部旧文件 id。"""
    versions = _legacy_versions(3)
    legacy = _write_legacy("d1", versions)
    vd = legacy.parent
    vs.migrate_legacy(vd, "d1", legacy, now=1)
    m._save_versions("d1", [*m._load_versions("d1"), _ver(80), _ver(81)])

    def boom(p):
        if p == "after_index":
            raise Crash(p)

    monkeypatch.setattr(vs, "_checkpoint", boom)
    with pytest.raises(Crash):
        vs.migrate_legacy(vd, "d1", legacy, now=2)
    monkeypatch.setattr(vs, "_checkpoint", lambda p: None)
    assert vs.migrate_legacy(vd, "d1", legacy, now=3)["status"] in ("imported", "already")
    rec = json.loads((vs.store_dir(vd, "d1") / "migration.json").read_bytes())
    assert {_ver(80)["id"], _ver(81)["id"]} <= set(rec["ids"])
    vs.delete(vd, "d1", _ver(80)["id"], now=4)
    m._save_versions("d1", [*m._load_versions("d1"), _ver(82)])
    assert vs.migrate_legacy(vd, "d1", legacy, now=5)["imported"] == 1
    ids = [e["id"] for e in vs.entries(vd, "d1")]
    assert _ver(80)["id"] not in ids and _ver(82)["id"] in ids


@pytest.mark.parametrize(
    "bad_entry",
    [
        None,
        1,
        "x",
        {},
        {"id": "v1-1"},
        {"id": "../x", "_seq": 1, "_bytes": 1, "_legacy_bytes": 1, "_doc_sha": "a"},
        {"id": "v1-1", "_seq": "1", "_bytes": 1, "_legacy_bytes": 1, "_doc_sha": "a"},
        {"id": "v1-1", "_seq": 1, "_bytes": True, "_legacy_bytes": 1, "_doc_sha": "a"},
        {"id": "v1-1", "_seq": 1, "_bytes": 1, "_legacy_bytes": 1, "_doc_sha": 5},
    ],
)
def test_index_with_a_malformed_entry_is_treated_as_corrupt_and_rebuilt(vdir, bad_entry):
    """P2：语法对、结构不对的索引也走重建，不让 public_entry / read 抛 AttributeError。"""
    vers = _seed(vdir, "d1", 3)
    idxp = vs.store_dir(vdir, "d1") / "index.json"
    idx = json.loads(idxp.read_bytes())
    idx["entries"].insert(1, bad_entry)
    idxp.write_bytes(json.dumps(idx).encode())
    got = vs.list_index(vdir, "d1")
    assert got["recovered"] is True
    assert [e["id"] for e in vs.entries(vdir, "d1")] == [v["id"] for v in vers]
    assert vs.read(vdir, "d1", vers[1]["id"]) == vers[1]
    vs.append(vdir, "d1", _ver(9), now=9)  # 写路径：留副本并重建
    assert len(vs.entries(vdir, "d1")) == 4
    assert len(list(vs.store_dir(vdir, "d1").glob("index.corrupt-*.json"))) == 1


def test_rebuild_takes_the_highest_generation_of_each_id(vdir, crash_at):
    """索引丢失时取每个 id 最高的一代（ADR 0140 §三：最后一次尝试过的元数据可能被采用）。"""
    vers = _seed(vdir, "d1", 2)
    vid = vers[0]["id"]
    crash_at("after_envelope")
    with pytest.raises(Crash):
        vs.update(vdir, "d1", vid, {"name": "尝试过"}, now=6)
    s = vs.store_dir(vdir, "d1") / "s"
    assert sorted(p.name for p in s.iterdir()) == sorted(
        [f"{vid}.json", f"{vid}.1.json", f"{vers[1]['id']}.json"]
    )
    (vs.store_dir(vdir, "d1") / "index.json").unlink()
    got = vs.entries(vdir, "d1")
    assert [e["id"] for e in got] == [v["id"] for v in vers] and got[0]["name"] == "尝试过"
    assert vs.read(vdir, "d1", vid)["doc"] == vers[0]["doc"]
