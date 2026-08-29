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
