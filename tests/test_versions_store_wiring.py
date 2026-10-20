"""版本时间线端点经 `_Timeline` 读写旧单文件 / 新存储（ADR 0140，D1b）。

* 同一组操作在两种存储上跑，**响应逐字段对拍**（读兼容的证据）；
* 默认仍写旧格式：没有新存储的文档，一次写入也不会冒出 `.vx` 目录；
* 新存储已存在：读它、写它，旧文件不动；
* 裁剪两边共用 `_version_policy`（改 `m.VERSION_KEEP_*` 对新存储同样生效）；
* 索引读不出来 → 409 `versions_unreadable` 且零改动；单个信封坏只赔那一条。
"""

from __future__ import annotations

import itertools
import os
import time as real_time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import versionstore as vs

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\x9d\xe1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


class _Clock:
    """确定的时钟：只在测试脚本每发一个请求时前进 1 秒（`time()` 本身不动，
    存储侧多取几次时间也不会让两种后端的时间戳分叉）；其余照抄真 `time`。"""

    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def strftime(self, fmt, t=None):
        return real_time.strftime(fmt, real_time.gmtime(self.now) if t is None else t)

    def localtime(self, secs=None):
        return real_time.gmtime(self.now if secs is None else secs)

    def __getattr__(self, name):
        return getattr(real_time, name)


def _use(monkeypatch, root: Path) -> None:
    """让 app 在 `root` 里读写，ids 与时钟确定。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(root / "data"))
    monkeypatch.setattr(m, "VERSIONS_DIR", root / "_versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(m, "time", _Clock())
    counter = itertools.count(1)
    monkeypatch.setattr(m, "_new_version_id", lambda: f"v{next(counter):x}-aa{next(counter):x}")
    m.app.config["TESTING"] = True


def _seed_versions() -> list[dict]:
    vers = []
    spec = [
        ("auto", {"auto": True}),
        ("auto", {"auto": True, "moment": "export"}),
        ("manual", {"auto": False}),
        ("named", {"auto": False, "named": True, "name": "初稿"}),
        ("auto", {"auto": True, "canvasId": "c2", "canvasName": "第二张"}),
        ("manual", {"auto": False, "description": "手动存的"}),
    ]
    for i, (_kind, extra) in enumerate(spec):
        v = {
            "id": f"v{0x100 + i:x}-{i:x}",
            "name": f"07-0{i + 1} 10:00",
            "ts": 1_700_000_000_000 + i * 60_000,
            "auto": False,
            "named": False,
            "description": "",
            "doc": _doc(i + 1, f"s{i}"),
        }
        v.update(extra)
        vers.append(v)
    return vers


def _seed(mode: str, doc_id: str) -> None:
    vers = _seed_versions()
    if mode == "legacy":
        m._save_versions(doc_id, vers)
        return
    path = m._versions_path(doc_id)
    for i, v in enumerate(vers):
        vs.append(path.parent, path.stem, v, now=i, policy=m._version_policy)


def _run_script(client, doc_id: str, monkeypatch) -> list:
    """一组覆盖全部端点的操作；返回 (标签, 状态码, 响应) 的转录。"""
    log: list = []

    def call(label, method, url, **kw):
        m.time.now += 1.0
        r = getattr(client, method)(url, **kw)
        body = r.get_json(silent=True)
        if body is None and r.mimetype.startswith("image/"):
            body = {"image_bytes": len(r.data)}
        log.append((label, r.status_code, body))
        return body

    u = f"/api/versions/{doc_id}"
    listing = call("list", "get", u + "?sketch=40&sketchText=20")
    ids = [v["id"] for v in listing["versions"]]
    for i, vid in enumerate(ids):
        call(f"get{i}", "get", f"{u}/{vid}")
    call("get-missing", "get", f"{u}/v999-ff")
    # 创建：与最近一版内容相同的自动检查点 → 跳过；不同 → 新增；命名；关键时刻；坏时刻
    last_doc = _seed_versions()[-1]["doc"]
    call("create-dup-auto", "post", u, json={"doc": last_doc, "auto": True, "name": "a"})
    call("create-auto", "post", u, json={"doc": _doc(2, "n1"), "auto": True, "name": "b"})
    call(
        "create-named",
        "post",
        u,
        json={"doc": _doc(2, "n2"), "name": "里程碑", "named": True, "description": "d"},
    )
    call(
        "create-moment",
        "post",
        u,
        json={"doc": _doc(2, "n2"), "auto": True, "moment": "export", "canvasId": "c1"},
    )
    call("create-bad-moment", "post", u, json={"doc": _doc(), "moment": "nope"})
    call("create-long-name", "post", u, json={"doc": _doc(), "name": "长" * 101})
    # 改名 / 取消命名 / 说明 / 转正 / 不存在 / 名字太长
    call("rename", "patch", f"{u}/{ids[0]}", json={"name": "起名"})
    call("rename-empty", "patch", f"{u}/{ids[1]}", json={"name": "  "})
    call("unname", "patch", f"{u}/{ids[3]}", json={"named": False})
    call("describe", "patch", f"{u}/{ids[2]}", json={"description": "说明"})
    call("promote", "patch", f"{u}/{ids[4]}", json={"auto": False})
    call("rename-missing", "patch", f"{u}/v999-ff", json={"name": "x"})
    call("rename-long", "patch", f"{u}/{ids[2]}", json={"name": "长" * 101})
    # 复制（命名 / 未命名 / 不存在）
    call("dup-named", "post", f"{u}/{ids[0]}/duplicate")
    call("dup-plain", "post", f"{u}/{ids[2]}/duplicate")
    call("dup-missing", "post", f"{u}/v999-ff/duplicate")
    # 缩略图：挂上、读出、节点删除后随之被清
    call("thumb-put", "put", f"{u}/{ids[2]}/thumb", data=PNG, content_type="image/png")
    call("thumb-put-missing", "put", f"{u}/v999-ff/thumb", data=PNG, content_type="image/png")
    call("thumb-get", "get", f"{u}/{ids[2]}/thumb")
    call("list-with-thumb", "get", u)
    call("delete", "delete", f"{u}/{ids[2]}")
    call("delete-again", "delete", f"{u}/{ids[2]}")
    call("thumb-get-after-delete", "get", f"{u}/{ids[2]}/thumb")
    # 命名节点预算：把字节上限压到很小，再命名一条 → 409，磁盘零改动
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 1_500)
    call("budget-list", "get", u)
    call(
        "create-named-over-budget",
        "post",
        u,
        json={"doc": _doc(3, "n3"), "name": "又一个", "named": True},
    )
    call("rename-over-budget", "patch", f"{u}/{ids[5]}", json={"name": "把它命名"})
    call("dup-named-over-budget", "post", f"{u}/{ids[0]}/duplicate")
    monkeypatch.setattr(m, "VERSION_KEEP_BYTES", 24 * 1024 * 1024)
    # 条数上限：压到 5 条未命名 / 3 条自动，连续追加让两条上限都咬
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 5)
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 3)
    for i in range(8):
        call(f"auto{i}", "post", u, json={"doc": _doc(2, f"k{i}"), "auto": True, "name": f"k{i}"})
    call("final-list", "get", u + "?sketch=10")
    return log


def _final_state(mode: str, doc_id: str) -> list[dict]:
    if mode == "legacy":
        return m._load_versions(doc_id)
    path = m._versions_path(doc_id)
    got = vs.read_all(path.parent, path.stem)
    assert got is not None
    return got[1]


def test_the_same_operations_give_the_same_responses_on_both_storages(tmp_path, monkeypatch):
    transcripts = {}
    states = {}
    for mode in ("legacy", "store"):
        root = tmp_path / mode
        _use(monkeypatch, root)
        _seed(mode, "cmp")
        path = m._versions_path("cmp")
        store = vs.store_dir(path.parent, path.stem)
        assert store.is_dir() == (mode == "store"), "摆场景失败：存储形态不对"
        assert path.is_file() == (mode == "legacy")
        client = m.app.test_client()
        transcripts[mode] = _run_script(client, "cmp", monkeypatch)
        states[mode] = _final_state(mode, "cmp")
        # 新存储在场时旧文件从不出现；旧存储在场时新存储从不出现
        assert store.is_dir() == (mode == "store")
        assert path.is_file() == (mode == "legacy")
        monkeypatch.undo()
    assert [t[:2] for t in transcripts["legacy"]] == [t[:2] for t in transcripts["store"]]
    for a, b in zip(transcripts["legacy"], transcripts["store"], strict=True):
        assert a == b, f"{a[0]}: 旧单文件与新存储的响应不同\n旧: {a}\n新: {b}"
    assert states["legacy"] == states["store"]
    log = {t[0]: t for t in transcripts["legacy"]}
    # 判据不空：脚本真的走到了这些分支
    assert log["create-dup-auto"][2].get("skipped") is True
    assert log["create-named-over-budget"][1] == 409
    assert log["create-named-over-budget"][2]["code"] == "named_budget_exceeded"
    assert log["rename-over-budget"][1] == 409 and log["dup-named-over-budget"][1] == 409
    assert log["thumb-put"][1] == 200 and log["thumb-get-after-delete"][1] == 404
    assert any("thumb" in v for v in log["list-with-thumb"][2]["versions"])
    assert log["get-missing"][1] == log["rename-missing"][1] == log["dup-missing"][1] == 404
    final_ids = [v["id"] for v in log["final-list"][2]["versions"]]
    assert len(final_ids) <= 5 + sum(1 for v in states["legacy"] if m._is_named(v)), (
        "条数上限没咬到"
    )


def test_no_store_means_writes_stay_in_the_legacy_file(tmp_path, monkeypatch):
    """默认仍写旧格式：没有新存储的文档，创建 / 改名 / 复制 / 删除 / 缩略图都不会冒出 `.vx`。"""
    _use(monkeypatch, tmp_path)
    client = m.app.test_client()
    path = m._versions_path("fresh")
    created = client.post("/api/versions/fresh", json={"doc": _doc(), "name": "初稿"}).get_json()
    vid = created["version"]["id"]
    assert client.patch(f"/api/versions/fresh/{vid}", json={"name": "二稿"}).status_code == 200
    dup = client.post(f"/api/versions/fresh/{vid}/duplicate").get_json()["version"]["id"]
    client.put(f"/api/versions/fresh/{vid}/thumb", data=PNG, content_type="image/png")
    assert client.delete(f"/api/versions/fresh/{dup}").status_code == 200
    assert path.is_file()
    assert not vs.store_dir(path.parent, path.stem).exists(), "默认写路径误写了新格式"
    assert (
        m.engine_atomicio.dumps_json({"versions": m._load_versions("fresh")}) == path.read_bytes()
    )


def test_an_existing_store_is_read_and_written_and_the_legacy_file_is_left_alone(
    tmp_path, monkeypatch
):
    _use(monkeypatch, tmp_path)
    m._save_versions("both", [{**v, "name": f"旧{i}"} for i, v in enumerate(_seed_versions())])
    path = m._versions_path("both")
    frozen = path.read_bytes()
    for i, v in enumerate(_seed_versions()[:2]):
        vs.append(path.parent, path.stem, {**v, "name": f"新{i}"}, now=i)
    client = m.app.test_client()
    names = [v["name"] for v in client.get("/api/versions/both").get_json()["versions"]]
    assert names == ["新0", "新1"], "有新存储却还在读旧文件"
    r = client.post("/api/versions/both", json={"doc": _doc(2, "z"), "name": "追加"})
    assert r.status_code == 200
    names = [v["name"] for v in client.get("/api/versions/both").get_json()["versions"]]
    assert names == ["新0", "新1", "追加"]
    assert path.read_bytes() == frozen, "旧文件被写了"
    assert [e["name"] for e in vs.entries(path.parent, path.stem)] == names


def test_store_pruning_follows_the_single_policy_and_the_app_knobs(tmp_path, monkeypatch):
    """`m.VERSION_KEEP_*` 仍是旋钮，且对新存储同样生效；被裁版本的信封与缩略图随之清掉。"""
    _use(monkeypatch, tmp_path)
    path = m._versions_path("pol")
    vs.append(path.parent, path.stem, _seed_versions()[0], now=0)  # 先造出新存储
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 3)
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 100)
    client = m.app.test_client()
    first = None
    for i in range(6):
        body = client.post("/api/versions/pol", json={"doc": _doc(1, str(i)), "name": f"p{i}"})
        assert body.status_code == 200
        if first is None:
            first = body.get_json()["version"]["id"]
            client.put(f"/api/versions/pol/{first}/thumb", data=PNG, content_type="image/png")
    names = [v["name"] for v in client.get("/api/versions/pol").get_json()["versions"]]
    assert names == ["p3", "p4", "p5"]
    root = vs.store_dir(path.parent, path.stem)
    assert {p.stem for p in (root / "s").glob("*.json")} == {
        v["id"] for v in client.get("/api/versions/pol").get_json()["versions"]
    }
    assert not any((path.parent / "thumbs" / path.stem).glob(f"{first}.*")), "被裁节点的缩略图还在"


def test_an_unreadable_index_refuses_everything_and_changes_nothing(tmp_path, monkeypatch):
    _use(monkeypatch, tmp_path)
    path = m._versions_path("u")
    for i, v in enumerate(_seed_versions()[:2]):
        vs.append(path.parent, path.stem, v, now=i)
    root = vs.store_dir(path.parent, path.stem)
    future = (root / "index.json").read_bytes().replace(b'"format": 1', b'"format": 99')
    (root / "index.json").write_bytes(future)
    before = sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file())
    client = m.app.test_client()
    vid = _seed_versions()[0]["id"]
    for r in (
        client.get("/api/versions/u"),
        client.post("/api/versions/u", json={"doc": _doc(), "name": "x"}),
        client.patch(f"/api/versions/u/{vid}", json={"name": "y"}),
        client.delete(f"/api/versions/u/{vid}"),
    ):
        assert r.status_code == 409, r.get_json()
        assert r.get_json()["code"] == "versions_unreadable"
    after = sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file())
    assert after == before


def test_one_corrupt_envelope_costs_one_version_not_the_timeline(tmp_path, monkeypatch):
    _use(monkeypatch, tmp_path)
    path = m._versions_path("c")
    vers = _seed_versions()[:3]
    for i, v in enumerate(vers):
        vs.append(path.parent, path.stem, v, now=i)
    root = vs.store_dir(path.parent, path.stem)
    bad = root / "s" / f"{vers[1]['id']}.json"
    bad.write_bytes(bad.read_bytes()[:-5])
    client = m.app.test_client()
    listing = client.get("/api/versions/c?sketch=10").get_json()["versions"]
    assert [v["id"] for v in listing] == [v["id"] for v in vers]
    assert [bool(v.get("damaged")) for v in listing] == [False, True, False]
    assert "sketch" in listing[0] and "sketch" not in listing[1]
    assert client.get(f"/api/versions/c/{vers[0]['id']}").status_code == 200
    assert client.get(f"/api/versions/c/{vers[1]['id']}").status_code == 409
    # 坏的这一条不挡着别的写入
    assert client.post("/api/versions/c", json={"doc": _doc(), "name": "新"}).status_code == 200


@pytest.mark.skipif(os.name != "posix", reason="用 fcntl 模拟另一个持锁者")
def test_store_writes_are_inside_the_versions_lock(tmp_path, monkeypatch):
    import fcntl

    from tavotto.engine import layoutsession

    _use(monkeypatch, tmp_path)
    path = m._versions_path("lk")
    vs.append(path.parent, path.stem, _seed_versions()[0], now=0)
    root = vs.store_dir(path.parent, path.stem)
    before = sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file())
    vid = _seed_versions()[0]["id"]
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.3)
    lock_file = layoutsession.doclock_path(m._lock_stripe(path.with_suffix(".versions")))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    client = m.app.test_client()
    try:
        for r in (
            client.post("/api/versions/lk", json={"doc": _doc(), "name": "n"}),
            client.patch(f"/api/versions/lk/{vid}", json={"name": "r"}),
            client.post(f"/api/versions/lk/{vid}/duplicate"),
            client.delete(f"/api/versions/lk/{vid}"),
            client.put(f"/api/versions/lk/{vid}/thumb", data=PNG, content_type="image/png"),
        ):
            assert r.status_code == 503, r.get_json()
    finally:
        os.close(fd)
    assert sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file()) == before


# --------------------------------------------------------------- Codex #1215


def _seeded_store(doc_id: str = "cx") -> tuple[Path, Path]:
    path = m._versions_path(doc_id)
    for i, v in enumerate(_seed_versions()[:3]):
        vs.append(path.parent, path.stem, v, now=i)
    return path, vs.store_dir(path.parent, path.stem)


def _tree(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    )


@pytest.mark.parametrize("how", ["index-stat", "envelope-listing"])
def test_a_store_that_cannot_be_probed_fails_closed_and_never_falls_back_to_legacy_writes(
    tmp_path, monkeypatch, how
):
    """P1：`.vx` 在而探测不了（权限变化 / 网络盘瞬时 I/O 错误）不是「没有新存储」。
    折成没有的话 POST 会写 `<doc_id>.json`，`.vx` 恢复后新存储重新成为权威，刚确认的检查点就没了。
    读写一律 409 `versions_unreadable`，旧格式文件一个也不许冒出来。注入 OSError，不依赖 chmod。"""
    _use(monkeypatch, tmp_path)
    path, root = _seeded_store()
    if how == "envelope-listing":
        (root / "index.json").unlink()  # 索引丢了：是否有存储要靠列信封目录判断
    before = _tree(root)
    real_stat, real_listdir = os.stat, os.listdir

    def stat(p, *a, **kw):
        if str(p).endswith(os.path.join(".vx", "index.json")):
            raise PermissionError(13, "injected")
        return real_stat(p, *a, **kw)

    def listdir(p="."):
        if str(p).endswith(os.path.join(".vx", "s")):
            raise PermissionError(13, "injected")
        return real_listdir(p)

    client = m.app.test_client()
    vid = _seed_versions()[0]["id"]
    real_read_bytes = Path.read_bytes

    def read_bytes(self):  # 真实的权限丢失连读都读不了；D2 的写路径不探测、直接读索引
        if how == "index-stat" and str(self).endswith(os.path.join(".vx", "index.json")):
            raise PermissionError(13, "injected")
        return real_read_bytes(self)

    with monkeypatch.context() as inj:
        inj.setattr(Path, "read_bytes", read_bytes)
        inj.setattr(
            os,
            "stat" if how == "index-stat" else "listdir",
            stat if how == "index-stat" else listdir,
        )
        for r in (
            client.get("/api/versions/cx"),
            client.get(f"/api/versions/cx/{vid}"),
            client.post("/api/versions/cx", json={"doc": _doc(), "name": "不能落旧格式"}),
            client.patch(f"/api/versions/cx/{vid}", json={"name": "x"}),
            client.post(f"/api/versions/cx/{vid}/duplicate"),
            client.delete(f"/api/versions/cx/{vid}"),
        ):
            assert r.status_code == 409, r.get_json()
            assert r.get_json()["code"] == "versions_unreadable"
        assert not path.exists(), "判不了就退回旧格式写——这正是 P1"
    assert _tree(root) == before
    # 恢复之后历史完好
    assert len(client.get("/api/versions/cx").get_json()["versions"]) == 3


def test_a_missing_envelope_is_corrupt_not_a_500_for_every_write_that_reads_it(
    tmp_path, monkeypatch
):
    """P2：索引里有、信封文件没了。`update()` 读旧信封的 FileNotFoundError 不许漏成 500。"""
    _use(monkeypatch, tmp_path)
    path, root = _seeded_store()
    vid = _seed_versions()[1]["id"]
    (root / "s" / f"{vid}.json").unlink()
    before = _tree(root)
    with pytest.raises(vs.EnvelopeCorrupt):
        vs.update(path.parent, path.stem, vid, {"name": "x"}, now=0)
    client = m.app.test_client()
    r = client.patch(f"/api/versions/cx/{vid}", json={"name": "x"})
    assert r.status_code == 409 and r.get_json()["code"] == "versions_unreadable", r.get_json()
    assert client.post(f"/api/versions/cx/{vid}/duplicate").status_code == 409
    assert _tree(root) == before, "失败的写动了磁盘"
    # 别的版本不受影响，坏的这一条仍可删（用户的出口）
    other = _seed_versions()[0]["id"]
    assert client.patch(f"/api/versions/cx/{other}", json={"name": "好的"}).status_code == 200
    assert client.delete(f"/api/versions/cx/{vid}").status_code == 200


def test_a_failure_after_the_commit_does_not_turn_a_persisted_write_into_an_error(
    tmp_path, monkeypatch
):
    """P2：写已经提交，收尾的额外读取遇到瞬时错误不能让请求报错——否则客户端重试 append 就多一个检查点。"""
    _use(monkeypatch, tmp_path)
    path, _root = _seeded_store()
    client = m.app.test_client()
    vid = _seed_versions()[0]["id"]

    # 追加：提交之后不许再读索引
    real_list = vs.list_index
    calls = {"n": 0}

    def list_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] > 1:  # 构造时那一次之后，任何读索引都炸
            raise OSError("injected transient I/O error")
        return real_list(*a, **kw)

    with monkeypatch.context() as inj:
        inj.setattr(vs, "list_index", list_once)
        r = client.post("/api/versions/cx", json={"doc": _doc(2, "n"), "name": "追加"})
    assert r.status_code == 200, r.get_json()
    assert [e["name"] for e in vs.entries(path.parent, path.stem)].count("追加") == 1

    # 改名：提交之后不许再读信封
    real_read = vs.read
    seen = {"n": 0}

    def read_once(*a, **kw):
        seen["n"] += 1
        if seen["n"] > 1:
            raise OSError("injected transient I/O error")
        return real_read(*a, **kw)

    with monkeypatch.context() as inj:
        inj.setattr(vs, "read", read_once)
        r = client.patch(f"/api/versions/cx/{vid}", json={"name": "改过"})
    assert r.status_code == 200 and r.get_json()["version"]["name"] == "改过"
    assert r.get_json()["version"]["objects"] == len(_seed_versions()[0]["doc"]["objects"])

    # 删除：提交之后的策略收尾失败只记日志
    def boom(*a, **kw):
        raise OSError("injected transient I/O error")

    with monkeypatch.context() as inj:
        inj.setattr(vs, "prune", boom)
        r = client.delete(f"/api/versions/cx/{vid}")
    assert r.status_code == 200
    assert vid not in [e["id"] for e in vs.entries(path.parent, path.stem)]
