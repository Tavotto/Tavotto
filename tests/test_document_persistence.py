"""文档落盘的唯一实现（engine/atomicio + engine/documents）与它在 HTTP 层的出口。

覆盖 Prompt 02 §七/§八 的四件事：写入是原子的、非有限数进不去、
收纳目录里 Tavotto 自己的文件不是用户文档、修订号能给外部修改检测当基线。
"""

import json
import os
import stat

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path)
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / documents.VERSIONS_DIRNAME)
    monkeypatch.setattr(m, "STYLES_PATH", tmp_path / documents.STYLES_FILENAME)
    m.app.config["TESTING"] = True
    return m.app.test_client()


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


# --------------------------- atomicio：写入本身 ------------------------------


def test_write_json_replaces_atomically_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "sub" / "doc.json"
    atomicio.write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(target.parent.glob("*.tmp"))


def test_write_json_fsyncs_the_file_before_replacing(tmp_path, monkeypatch):
    """`os.replace` 只保证「要么旧要么新」，不保证新内容已经离开页缓存。

    掉电时少了这一步，replace 出来的会是一个**空文件**——比旧内容还糟。
    """
    # **判据要说清主语。** 只断言「有人被 fsync 了」是空的：写完之后还会
    # fsync 一次目录，所以哪怕把文件那次删掉，计数照样非零（本判据第一版
    # 正是这么写的，变异跑完全绿）。这里量的是「被 fsync 的里面有一个是
    # 普通文件」。
    regular: list[bool] = []
    real_fsync = os.fsync

    def spy(fd):
        regular.append(stat.S_ISREG(os.fstat(fd).st_mode))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    atomicio.write_json(tmp_path / "doc.json", {"a": 1})
    assert any(regular), "落盘的文件本身没有被 fsync（只 fsync 了目录不算）"


def test_write_json_rejects_non_finite_before_touching_disk(tmp_path):
    """NaN / ∞ 不是 JSON。写出去的文件浏览器 `JSON.parse` 读不动，
    表现是「这份文档打不开」而磁盘上看起来好端端的。"""
    target = tmp_path / "doc.json"
    atomicio.write_json(target, {"w": 1})

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(atomicio.AtomicWriteError) as e:
            atomicio.write_json(target, {"w": bad})
        assert e.value.code == "non_finite_number"

    # 原文件一字未动，也没有半成品
    assert json.loads(target.read_text(encoding="utf-8")) == {"w": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_replace_failure_keeps_the_old_file_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "doc.json"
    atomicio.write_json(target, {"v": "old"})

    def boom(_src, _dst):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(atomicio.AtomicWriteError) as e:
        atomicio.write_json(target, {"v": "new"})
    assert e.value.code == "replace_failed"

    assert json.loads(target.read_text(encoding="utf-8")) == {"v": "old"}
    assert not list(tmp_path.glob("*.tmp"))


def test_content_revision_tracks_content_not_mtime(tmp_path):
    """修订号回答「内容变了没有」。掺进 mtime 的话，一次 touch、一次从备份
    原样恢复都会变出新修订号，外部修改检测就会对着逐字节相同的文件报冲突。"""
    target = tmp_path / "doc.json"
    atomicio.write_json(target, {"a": 1})
    first = atomicio.content_revision(target)

    os.utime(target, (0, 0))
    assert atomicio.content_revision(target) == first

    atomicio.write_json(target, {"a": 2})
    assert atomicio.content_revision(target) != first

    assert atomicio.content_revision(tmp_path / "nope.json") is None


# --------------------------- documents：格式判据 ------------------------------


def test_validate_rejects_future_schema_with_its_own_code():
    """更新版本写出的文档不能「尽力打开」——那是用旧规则重写用户的新数据。"""
    with pytest.raises(documents.DocumentError) as e:
        documents.validate_document({"schema": documents.SCHEMA_CURRENT + 1})
    assert e.value.code == "schema_too_new"


@pytest.mark.parametrize(
    "raw",
    [
        [1, 2],
        {"schema": 1},
        {"schema": 3},  # 项目文档没有画布
        {"schema": 3, "canvases": []},
    ],
)
def test_validate_rejects_non_documents(raw):
    with pytest.raises(documents.DocumentError) as e:
        documents.validate_document(raw)
    assert e.value.code == "invalid_document"


def test_reserved_stems_are_derived_from_the_real_filenames():
    """枚举而不是前缀规则：画布名净化后可能以 `_` 开头（`（图一）` → `_图一_`），
    前缀规则会把用户的文档藏起来。"""
    assert not documents.is_user_document_stem("_styles")
    assert documents.is_user_document_stem("_图一_")
    assert documents.is_user_document_stem("主图")


# --------------------------- HTTP 出口 ---------------------------------------


def test_autosave_put_rejects_non_finite_and_keeps_disk(client, tmp_path):
    assert client.put("/api/autosave/d1", json=PD).status_code == 200
    saved = (tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json").read_text(encoding="utf-8")

    bad = json.dumps({**PD, "updatedAt": 2}).replace('"updatedAt": 2', '"updatedAt": NaN')
    resp = client.put("/api/autosave/d1", data=bad, content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "non_finite_number"
    assert (tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json").read_text(encoding="utf-8") == saved


def test_autosave_exposes_revision_on_write_and_read(client):
    put = client.put("/api/autosave/d2", json=PD).get_json()
    assert put["revision"]
    get = client.get("/api/autosave/d2")
    assert get.headers["X-Tavotto-Revision"] == put["revision"]

    same = client.put("/api/autosave/d2", json=PD).get_json()
    assert same["revision"] == put["revision"]
    changed = client.put("/api/autosave/d2", json={**PD, "updatedAt": 99}).get_json()
    assert changed["revision"] != put["revision"]


def test_autosave_put_reports_future_schema(client):
    resp = client.put("/api/autosave/d3", json={**PD, "schema": documents.SCHEMA_CURRENT + 1})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "schema_too_new"


def test_layout_save_is_atomic(client, tmp_path, monkeypatch):
    """用户的「另存为」。改成原子写之前，这里是 `write_text` 直接盖：
    写到一半失败留下截断文件，而好的那一份已经被顶掉了。"""
    assert client.post("/api/layouts/主图", json={"schema": 2, "objects": []}).status_code == 200
    good = (tmp_path / "主图.json").read_text(encoding="utf-8")

    def boom(_src, _dst):
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(os, "replace", boom)
    resp = client.post("/api/layouts/主图", json={"schema": 2, "objects": [{"type": "text"}]})
    assert resp.status_code == 500
    assert resp.get_json()["code"] == "replace_failed"

    assert (tmp_path / "主图.json").read_text(encoding="utf-8") == good
    assert not list(tmp_path.glob("*.tmp"))


def test_layout_save_rejects_non_finite(client, tmp_path):
    bad = '{"schema": 2, "page": {"w": Infinity}}'
    resp = client.post("/api/layouts/坏图", data=bad, content_type="application/json")
    assert resp.status_code == 400 and resp.get_json()["code"] == "non_finite_number"
    assert not (tmp_path / "坏图.json").exists()


def test_styles_file_is_not_listed_as_a_document(client):
    """样式预设存在 `LAYOUT_DIR/_styles.json`，而画布列表是对同一个目录
    `glob("*.json")`——不剔掉的话「打开画布」里会多出一条叫 `_styles` 的东西。"""
    assert client.post("/api/styles", json={"name": "S1"}).status_code == 200
    assert client.post("/api/layouts/主图", json={"schema": 2}).status_code == 200

    names = client.get("/api/layouts").get_json()["layouts"]
    assert "主图" in names
    assert "_styles" not in names


def test_reserved_name_is_not_reachable_through_the_document_api(client):
    """否则一份画布能把样式表整个盖掉。"""
    assert client.post("/api/styles", json={"name": "S1"}).status_code == 200
    styles_before = client.get("/api/styles").get_json()

    resp = client.post("/api/layouts/_styles", json={"schema": 2, "objects": []})
    assert resp.status_code == 409 and resp.get_json()["code"] == "reserved_name"
    assert client.get("/api/layouts/_styles").status_code == 409
    assert client.get("/api/styles").get_json() == styles_before


# ------------------- 外部修改检测（Prompt 03 / R-08） ------------------------


def test_revision_baseline_blocks_a_write_over_someone_elses_content(client, tmp_path):
    """外部工具改过磁盘上那份之后，带旧修订号来的整份 PUT 必须被挡下。

    这是 `base`（updatedAt 比较）看不见的那一类：外部工具改完文档往往一个
    字节的 updatedAt 都不动，甚至写回一个更小的值。
    """
    first = client.put("/api/autosave/d1", json=PD).get_json()
    slot = tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json"

    # 编辑器外的改动：内容变了，**updatedAt 反而更旧**
    theirs = {**PD, "updatedAt": 0, "project": {"id": "p", "name": "theirs"}}
    slot.write_text(json.dumps(theirs), encoding="utf-8")

    r = client.put(f"/api/autosave/d1?base_revision={first['revision']}", json=PD)
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "external_change"
    # 磁盘上对方那份一个字节没动
    assert json.loads(slot.read_text(encoding="utf-8"))["project"]["name"] == "theirs"
    # 摘要要能回答「那边现在是什么」，并带上当下的修订号（显式覆盖拿它当基线）
    assert body["summary"]["objects"] == 0
    assert body["summary"]["name"] == "theirs"
    assert body["revision"] == body["summary"]["revision"] != first["revision"]

    # 拿 409 里回的那个修订号再写一次 = 明确覆盖，放行
    ok = client.put(f"/api/autosave/d1?base_revision={body['revision']}", json=PD)
    assert ok.status_code == 200
    assert json.loads(slot.read_text(encoding="utf-8"))["project"]["name"] == "n"


def test_matching_revision_passes_and_advances(client):
    put = client.put("/api/autosave/d1", json=PD).get_json()
    again = client.put(f"/api/autosave/d1?base_revision={put['revision']}", json=PD)
    assert again.status_code == 200
    # 内容一样 → 修订号一样；基线因此不需要额外推进
    assert again.get_json()["revision"] == put["revision"]


def test_absent_sentinel_blocks_the_second_tab_creating_the_same_document(client, tmp_path):
    """两个标签页同时新建同一份文档：后写的那个不许整份盖掉先写的。

    这是判据的**另一条边**。少了 `absent` 哨兵，双方都拿不出修订号，
    后端一律放行——而这正是这条判据要挡的事。
    """
    client.put("/api/autosave/dup", json={**PD, "project": {"id": "p", "name": "first"}})
    r = client.put(
        f"/api/autosave/dup?base_revision={m.REVISION_ABSENT}",
        json={**PD, "project": {"id": "p", "name": "second"}},
    )
    assert r.status_code == 409
    assert r.get_json()["code"] == "external_change"
    slot = tmp_path / documents.AUTOSAVE_DIRNAME / "dup.json"
    assert json.loads(slot.read_text(encoding="utf-8"))["project"]["name"] == "first"


def test_absent_sentinel_passes_when_the_slot_really_is_empty(client):
    r = client.put(f"/api/autosave/fresh?base_revision={m.REVISION_ABSENT}", json=PD)
    assert r.status_code == 200


def test_a_hash_baseline_still_recreates_a_file_deleted_outside(client, tmp_path):
    """两侧故意不对称：挡的是「覆盖别人的内容」，不是「重建被删掉的文件」。

    此刻磁盘上没有任何内容会因为这次写入而消失，而内存里那份是用户真实的工作。
    """
    put = client.put("/api/autosave/d1", json=PD).get_json()
    (tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json").unlink()
    r = client.put(f"/api/autosave/d1?base_revision={put['revision']}", json=PD)
    assert r.status_code == 200


def test_base_revision_wins_over_base_when_both_are_sent(client, tmp_path):
    """两个基线同时带来时以修订号为准：它强，且两条判据不该各判各的。"""
    put = client.put("/api/autosave/d1", json=PD).get_json()
    slot = tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json"
    # 磁盘上被外部改成 updatedAt 更小的一份：`base` 放行，`base_revision` 挡下
    slot.write_text(json.dumps({**PD, "updatedAt": 0, "createdAt": 7}), encoding="utf-8")
    r = client.put(f"/api/autosave/d1?base=1&base_revision={put['revision']}", json=PD)
    assert r.status_code == 409
    assert r.get_json()["code"] == "external_change"


def test_stale_write_still_guards_clients_that_send_no_revision(client):
    """不发修订号的调用方（旧前端）仍然走 updatedAt 那条判据。"""
    client.put("/api/autosave/d1", json={**PD, "updatedAt": 500})
    r = client.put("/api/autosave/d1?base=100", json={**PD, "updatedAt": 200})
    assert r.status_code == 409
    assert r.get_json()["code"] == "stale_write"


def test_document_summary_reports_two_time_dimensions_and_none_when_unreadable(client, tmp_path):
    client.put("/api/autosave/d1", json={**PD, "updatedAt": 4242})
    slot = tmp_path / documents.AUTOSAVE_DIRNAME / "d1.json"
    summary = client.get("/api/autosave/d1/summary").get_json()
    assert summary["updatedAt"] == 4242  # 文档自报的编辑时刻
    assert summary["mtime"] >= 0 and summary["mtime"] != 4242  # 文件系统记的写入时刻
    assert (summary["schema"], summary["canvases"], summary["objects"]) == (3, 1, 0)

    # 读不出来 = 「磁盘上没有可比较的东西」，不是「各项为 0」
    slot.write_text("{ not json", encoding="utf-8")
    assert m.document_summary(slot) is None
    assert client.get("/api/autosave/d1/summary").status_code == 404


# ------------------- 版本检查点的画布身份（Prompt 03 / R-03） -----------------


def _version_doc(name, objects=()):
    return {
        "schema": 2,
        "name": name,
        "page": {"w": 10, "h": 10},
        "objects": list(objects),
        "guides": [],
    }


def test_version_records_canvas_identity_and_omits_it_when_absent(client):
    with_id = client.post(
        "/api/versions/dv",
        json={"doc": _version_doc("Fig 2"), "canvasId": "c2", "canvasName": "Fig 2"},
    ).get_json()["version"]
    assert (with_id["canvasId"], with_id["canvasName"]) == ("c2", "Fig 2")

    # 没给身份就**不填**：缺席的含义是「不知道来自哪张画布」，
    # 补一个默认值等于替它编一个身份出来
    without = client.post("/api/versions/dv", json={"doc": _version_doc("x")}).get_json()
    assert "canvasId" not in without["version"]
    assert "canvasName" not in without["version"]

    listed = client.get("/api/versions/dv").get_json()["versions"]
    assert [v.get("canvasId") for v in listed] == ["c2", None]


def test_auto_checkpoint_dedup_is_per_canvas(client):
    """内容相同但来自另一张画布，不是「与最近一版相同」。

    复制一张画布之后两张内容逐字节相同：只比 doc 的话，第二张画布的检查点
    会被判成重复而跳过，于是它在时间线上一个检查点都没有。
    """
    doc = _version_doc("Fig 1", [{"id": "t1", "type": "text"}])
    first = client.post(
        "/api/versions/dv", json={"doc": doc, "auto": True, "canvasId": "c1"}
    ).get_json()
    assert not first.get("skipped")

    same_canvas = client.post(
        "/api/versions/dv", json={"doc": doc, "auto": True, "canvasId": "c1"}
    ).get_json()
    assert same_canvas["skipped"] is True

    other_canvas = client.post(
        "/api/versions/dv", json={"doc": doc, "auto": True, "canvasId": "c2"}
    ).get_json()
    assert not other_canvas.get("skipped")
    assert other_canvas["version"]["canvasId"] == "c2"


# ------------------- 评审 P1/P2（PR #201）：判据与写入之间的缝 -------------


def test_directory_fsync_failure_is_not_swallowed(tmp_path, monkeypatch):
    """目录项落不了盘要**响亮地失败**。

    以前这里连同 Windows「打不开目录」一起 `pass` 掉了：调用方于是收到一个
    成功，而前端拿到成功就会把本机兜底副本删掉——用户手上从此只剩这一份
    可能撑不过掉电的文件。ADR 0023 与 `src/tavotto/AGENTS.md` 写的是
    「失败清 tmp + 抛 AtomicWriteError」。
    """
    import errno

    real_fsync = os.fsync
    target = tmp_path / "doc.json"

    def boom(fd):
        # 只让**目录** fd 的 fsync 失败：文件那一步照常，否则测的就成了另一件事
        if os.fstat(fd).st_mode & stat.S_IFDIR:
            raise OSError(errno.EIO, "模拟目录项落盘时的 I/O 错误")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(atomicio.AtomicWriteError) as exc:
        atomicio.write_json(target, {"a": 1})
    assert exc.value.code == "dir_fsync_failed"


def test_directory_fsync_unsupported_is_still_ignored(tmp_path, monkeypatch):
    """「这个文件系统没有目录 fsync 这一步」不是失败。

    部分网络盘 / 旧 FAT 家族对目录 fd 直接回 EINVAL。把它也当成 I/O 错误的话，
    那些机器上**每一次保存都会报错**——判据比它要守的东西宽了。
    """
    import errno

    real_fsync = os.fsync
    target = tmp_path / "doc.json"

    def unsupported(fd):
        if os.fstat(fd).st_mode & stat.S_IFDIR:
            raise OSError(errno.EINVAL, "该文件系统不支持目录 fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", unsupported)
    atomicio.write_json(target, {"a": 1})  # 不抛
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_two_concurrent_creates_cannot_both_win(client, monkeypatch):
    """两个标签页同时**新建**同一份文档：只有一个能落盘，另一个必须拿到 409。

    `absent` 哨兵本来就是为这个场景加的，但判据与写入之间放开一瞬就绕过去了
    ——双方都在对方落盘之前读到「磁盘上没有」，双方都判「没冲突」，后写的把
    先写的整份盖掉，**而两边都收到 200**。

    判据把缝**撑开**：让第一个请求在读完修订号之后、写之前停住，等第二个请求
    整个跑完。串行执行下这个交错根本不会发生，所以不撑开就等于没测。
    """
    import threading

    first_checked, second_done = threading.Event(), threading.Event()
    real_revision = m.engine_atomicio.content_revision
    calls = {"n": 0}

    def slow_revision(path):
        value = real_revision(path)
        calls["n"] += 1
        if calls["n"] == 1:  # 只掰开第一个请求的那条缝
            first_checked.set()
            second_done.wait(10)
        return value

    monkeypatch.setattr(m.engine_atomicio, "content_revision", slow_revision)

    results: list[int] = []

    def put(doc):
        results.append(client.put("/api/autosave/race?base_revision=absent", json=doc).status_code)

    a = threading.Thread(target=put, args=({**PD, "updatedAt": 1},), daemon=True)
    a.start()
    assert first_checked.wait(5), "第一个请求没能停在读完修订号之后"
    put({**PD, "updatedAt": 2})  # 第二个请求整个跑完
    second_done.set()
    a.join(10)

    assert sorted(results) == [200, 409], f"两个新建都成功了 = 有一份被静默盖掉：{results}"


# ------------------------- 严格同源：schema 版本 -----------------------------


def test_frontend_and_backend_agree_on_the_current_schema():
    """`documents.SCHEMA_CURRENT` ↔ `web/src/types/document.ts` 的同名常量。

    两侧对「当前 schema 是几」意见不一时，后端会拒绝前端刚写出来的文档，
    或者前端会默默打开一份自己读不懂的。看护放在这里而不是靠人记得。
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "web" / "src" / "types" / "document.ts"
    match = re.search(r"export const SCHEMA_CURRENT = (\d+)", src.read_text(encoding="utf-8"))
    assert match, "web/src/types/document.ts 里找不到 SCHEMA_CURRENT"
    assert int(match.group(1)) == documents.SCHEMA_CURRENT
