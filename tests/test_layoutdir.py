"""排版的目录形态（ADR 0211 第二期 PR 1）：`engine/layoutdir.py` 的状态机、占位、守卫，与各读取端的认占位。

写入端默认**关**；这里凡要写目录形态的用例都显式打开 `layoutdir.WRITER_ENABLED`。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents, layoutannotate, layoutdir, runtimeasset, tutorial

PD = {
    "schema": 3,
    "project": {"id": "p", "name": "排版一"},
    "canvases": [
        {"id": "c1", "name": "Figure 1", "page": {"w": 10, "h": 10}, "objects": [], "guides": []}
    ],
    "activeCanvasId": "c1",
    "createdAt": 0,
    "updatedAt": 1,
}


def _bytes(doc=None, **over) -> bytes:
    return atomicio.dumps_json({**(doc or PD), **over}, indent=1)


def _rev(data: bytes) -> str:
    return atomicio.revision_of(data)


@pytest.fixture
def store(tmp_path) -> Path:
    s = tmp_path / "tavottofile"
    s.mkdir()
    return s


@pytest.fixture
def writer_on(monkeypatch):
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", True)


@pytest.fixture
def writer_off(monkeypatch):
    """PR 3 起开关默认开；对拍改造前行为的用例显式关掉（`TAVOTTO_LAYOUT_DIR_WRITER=0` 的应急回退同一档）。"""
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    monkeypatch.delenv(layoutdir.WRITER_ENV, raising=False)


def _snapshot(root: Path) -> dict:
    """整棵树的 (相对路径 → 字节 + mtime_ns)：用来证明「读不写」。"""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = (
            None if p.is_dir() else p.read_bytes(),
            p.lstat().st_mtime_ns,
        )
    return out


# ------------------------------------------------------------------ 占位


def test_placeholder_is_detected_by_content_and_size():
    data = layoutdir.placeholder_bytes("图一")
    assert layoutdir.placeholder_target(data) == "图一.tavotto"
    doc = json.loads(data)
    assert doc["schema"] == layoutdir.PLACEHOLDER_SCHEMA > documents.SCHEMA_CURRENT
    # 真排版（哪怕 schema 一样）不是占位；超大的不读
    assert layoutdir.placeholder_target(_bytes()) is None
    assert layoutdir.placeholder_target(b"x" * 2000) is None
    assert layoutdir.placeholder_target(b"not json") is None
    # 占位在读侧判据里就是「来自更新的 Tavotto」：旧版据此提示更新
    with pytest.raises(documents.DocumentError) as exc:
        documents.validate_document(json.loads(data))
    assert exc.value.code == "schema_too_new"


# ------------------------------------------------------------------ 状态


def test_new_and_legacy_and_orphan_states(store):
    assert layoutdir.locate(store, "a").state == layoutdir.NEW
    (store / "a.json").write_bytes(_bytes())
    loc = layoutdir.locate(store, "a")
    assert (loc.state, loc.read_path) == (layoutdir.LEGACY, store / "a.json")
    (store / "o.json").write_bytes(layoutdir.placeholder_bytes("o"))
    orphan = layoutdir.locate(store, "o")
    assert (orphan.state, orphan.read_path) == (layoutdir.ORPHAN, None)
    # 占位指向别的目录名（文件被改名拷贝）：不认它是这个名字的占位 = 当真文件
    (store / "z.json").write_bytes(layoutdir.placeholder_bytes("other"))
    assert layoutdir.locate(store, "z").state == layoutdir.LEGACY


def test_dir_state_reads_layout_json_and_revision_is_of_layout_json(store, writer_on):
    layoutdir.write(store, "a", _bytes())
    loc = layoutdir.locate(store, "a")
    assert loc.state == layoutdir.DIR
    assert loc.read_path == store / "a.tavotto" / "layout.json"
    assert loc.current_revision() == _rev(_bytes())
    # 占位被删（同步盘 / 用户）：目录还在 = 仍是 DIR
    (store / "a.json").unlink()
    assert layoutdir.locate(store, "a").state == layoutdir.DIR


def test_blocked_when_directory_name_is_taken_by_a_file_or_has_no_manifest(store, writer_on):
    (store / "f.json").write_bytes(_bytes())
    (store / "f.tavotto").write_bytes(b"PK zip package")  # 同名的包文件
    loc = layoutdir.locate(store, "f")
    assert (loc.state, loc.read_path) == (layoutdir.BLOCKED, store / "f.json")
    # 写入退回改造前的 `<名>.json`，包文件原封不动
    layoutdir.write(store, "f", _bytes(updatedAt=2))
    assert json.loads((store / "f.json").read_bytes())["updatedAt"] == 2
    assert (store / "f.tavotto").read_bytes() == b"PK zip package"
    # 目录在但没有清单：不是完整目录
    (store / "g.json").write_bytes(_bytes())
    (store / "g.tavotto").mkdir()
    assert layoutdir.locate(store, "g").state == layoutdir.BLOCKED


# ------------------------------------------------------------------ 写：往返与开关


def test_round_trip_creates_dir_manifest_and_placeholder(store, writer_on):
    rev = layoutdir.write(store, "图一", _bytes(), doc_id="doc_1")
    d = store / "图一.tavotto"
    assert rev == _rev(_bytes())
    assert (d / "layout.json").read_bytes() == _bytes()
    assert (store / "图一.json").read_bytes() == layoutdir.placeholder_bytes("图一")
    manifest = json.loads((d / "package_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == set(layoutdir.MANIFEST_KEYS)
    assert manifest["format"] == layoutdir.CONTAINER_FORMAT
    assert manifest["container_version"] == layoutdir.CONTAINER_VERSION
    assert manifest["doc_id"] == "doc_1"
    assert manifest["layout_revision"] == rev
    assert manifest["placeholder_revision"] == _rev(layoutdir.placeholder_bytes("图一"))
    assert manifest["legacy"] is None
    # 第二次：只更新正文与清单里的提示；doc_id 不给就保留
    rev2 = layoutdir.write(store, "图一", _bytes(updatedAt=2))
    manifest = json.loads((d / "package_manifest.json").read_text(encoding="utf-8"))
    assert manifest["layout_revision"] == rev2 and manifest["doc_id"] == "doc_1"
    assert layoutdir.locate(store, "图一").current_revision() == rev2
    assert sorted(p.name for p in store.iterdir()) == ["图一.json", "图一.tavotto"], "留下了暂存物"


def test_writer_off_is_byte_identical_to_the_legacy_write(store, writer_off):
    assert layoutdir.writer_enabled() is False
    rev = layoutdir.write(store, "a", _bytes())
    assert rev == _rev(_bytes())
    assert [p.name for p in store.iterdir()] == ["a.json"]
    assert (store / "a.json").read_bytes() == _bytes()
    # 已有旧文件：同样原样覆盖，不迁移
    layoutdir.write(store, "a", _bytes(updatedAt=2))
    assert [p.name for p in store.iterdir()] == ["a.json"]
    # 孤立占位 + 关着：当普通文件盖掉（目录不存在，没有任何东西被架空）
    (store / "o.json").write_bytes(layoutdir.placeholder_bytes("o"))
    layoutdir.write(store, "o", _bytes())
    assert layoutdir.locate(store, "o").state == layoutdir.LEGACY


def test_writer_off_still_writes_into_an_existing_directory_layout(store, monkeypatch):
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", True)
    layoutdir.write(store, "a", _bytes())
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    layoutdir.write(store, "a", _bytes(updatedAt=3))
    assert json.loads((store / "a.tavotto" / "layout.json").read_bytes())["updatedAt"] == 3
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a"), "占位被正文盖了"


def test_default_is_on_and_env_switch_both_ways(store, monkeypatch):
    monkeypatch.delenv(layoutdir.WRITER_ENV, raising=False)
    assert layoutdir.WRITER_ENABLED is True and layoutdir.writer_enabled() is True
    monkeypatch.setenv(layoutdir.WRITER_ENV, "0")  # 应急回退
    assert layoutdir.writer_enabled() is False
    layoutdir.write(store, "off", _bytes())
    assert [p.name for p in store.iterdir()] == ["off.json"]
    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    monkeypatch.setenv(layoutdir.WRITER_ENV, "1")
    layoutdir.write(store, "a", _bytes())
    assert (store / "a.tavotto").is_dir()


# ------------------------------------------------------------------ 迁移状态机


def test_legacy_migration_keeps_keepsake_and_swaps_placeholder_last(store, writer_on):
    legacy = _bytes(updatedAt=7)
    (store / "a.json").write_bytes(legacy)
    layoutdir.write(store, "a", _bytes(updatedAt=8), doc_id="d1")
    d = store / "a.tavotto"
    assert (d / "legacy" / "a.json").read_bytes() == legacy
    if os.name != "nt":
        assert not os.access(d / "legacy" / "a.json", os.W_OK) or os.geteuid() == 0
    assert json.loads((d / "layout.json").read_bytes())["updatedAt"] == 8
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a")
    manifest = json.loads((d / "package_manifest.json").read_text(encoding="utf-8"))
    assert manifest["legacy"] == {"file": "legacy/a.json", "revision": _rev(legacy)}


@pytest.mark.parametrize("point", ["dir_published", "placeholder_swapped"])
def test_migration_interrupted_stays_readable_and_next_write_resumes(
    store, writer_on, monkeypatch, point
):
    legacy = _bytes(updatedAt=7)
    (store / "a.json").write_bytes(legacy)

    def boom(p):
        if p == point:
            raise RuntimeError("进程在这里死了")

    monkeypatch.setattr(layoutdir, "FAULT_HOOK", boom)
    with pytest.raises(RuntimeError):
        layoutdir.write(store, "a", _bytes(updatedAt=8))
    monkeypatch.setattr(layoutdir, "FAULT_HOOK", None)

    # 这次保存没有成功：读到的仍是旧内容，修订号仍是客户端手里的那个基线
    loc = layoutdir.locate(store, "a")
    assert loc.state == (layoutdir.PENDING if point == "dir_published" else layoutdir.DIR)
    assert loc.current_revision() == _rev(legacy)
    assert loc.read_path.read_bytes() == legacy
    assert [loc2.stem for loc2 in layoutdir.list_layouts(store)] == ["a"]

    # 用基线再写一次：补完迁移并落下这次保存
    rev = layoutdir.write(store, "a", _bytes(updatedAt=8))
    done = layoutdir.locate(store, "a")
    assert done.state == layoutdir.DIR and done.current_revision() == rev
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a")
    assert (store / "a.tavotto" / "legacy" / "a.json").read_bytes() == legacy


def test_failed_publish_leaves_no_half_directory(store, writer_on, monkeypatch):
    (store / "a.json").write_bytes(_bytes())

    def failing(tmp, dest):
        raise atomicio.AtomicWriteError("replace_failed", "boom", dest)

    monkeypatch.setattr(atomicio, "publish_directory_if_absent", failing)
    with pytest.raises(atomicio.AtomicWriteError):
        layoutdir.write(store, "a", _bytes(updatedAt=2))
    assert sorted(p.name for p in store.iterdir()) == ["a.json"], "暂存目录没清掉"
    assert (store / "a.json").read_bytes() == _bytes()


def test_old_build_overwrote_placeholder_is_an_external_change(store, writer_on):
    layoutdir.write(store, "a", _bytes(updatedAt=1))
    mine = (store / "a.tavotto" / "layout.json").read_bytes()
    # 旧版在占位上「仍然覆盖」另存：真排版换掉了占位
    foreign = _bytes(updatedAt=99)
    (store / "a.json").write_bytes(foreign)
    loc = layoutdir.locate(store, "a")
    assert loc.state == layoutdir.CONFLICT
    # 修订号与读到的是同一份（外部那份）：重新加载 / 明确覆盖拿它当基线，不会在 409 上转圈
    assert loc.current_revision() == _rev(foreign) and loc.read_path.read_bytes() == foreign
    assert m.engine_doclock.revision_conflict(_rev(mine), loc.current_revision())
    # 明确覆盖：两边都留在 legacy/，再换回占位
    layoutdir.write(store, "a", _bytes(updatedAt=2))
    kept = {p.name: p.read_bytes() for p in (store / "a.tavotto" / "legacy").iterdir()}
    assert foreign in kept.values() and mine in kept.values()
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a")
    assert layoutdir.locate(store, "a").state == layoutdir.DIR


def test_pending_vs_conflict_is_decided_by_recorded_revision(store, writer_on, monkeypatch):
    legacy = _bytes(updatedAt=7)
    (store / "a.json").write_bytes(legacy)
    monkeypatch.setattr(
        layoutdir,
        "FAULT_HOOK",
        lambda p: (_ for _ in ()).throw(RuntimeError()) if p == "dir_published" else None,
    )
    with pytest.raises(RuntimeError):
        layoutdir.write(store, "a", _bytes())
    assert layoutdir.locate(store, "a").state == layoutdir.PENDING
    (store / "a.json").write_bytes(_bytes(updatedAt=8))  # 留底之后别人又改了 N.json
    assert layoutdir.locate(store, "a").state == layoutdir.CONFLICT


def test_new_layout_with_fault_before_placeholder_is_readable(store, writer_on, monkeypatch):
    def boom(p):
        if p == "dir_published":
            raise RuntimeError

    monkeypatch.setattr(layoutdir, "FAULT_HOOK", boom)
    with pytest.raises(RuntimeError):
        layoutdir.write(store, "n", _bytes())
    monkeypatch.setattr(layoutdir, "FAULT_HOOK", None)
    loc = layoutdir.locate(store, "n")
    assert loc.state == layoutdir.DIR and loc.current_revision() == _rev(_bytes())
    layoutdir.write(store, "n", _bytes(updatedAt=2))
    assert (store / "n.json").read_bytes() == layoutdir.placeholder_bytes("n")


# ------------------------------------------------------------------ 列表


def test_listing_shows_dir_layouts_and_hides_placeholders_and_orphans(store, writer_on):
    layoutdir.write(store, "dirform", _bytes())
    (store / "legacy.json").write_bytes(_bytes())
    (store / "orphan.json").write_bytes(layoutdir.placeholder_bytes("orphan"))
    (store / "_styles.json").write_bytes(b"{}")  # Tavotto 自己的文件
    assert [loc.stem for loc in layoutdir.list_layouts(store)] == ["dirform", "legacy"]
    # 占位被删后目录形态仍列出
    (store / "dirform.json").unlink()
    assert "dirform" in [loc.stem for loc in layoutdir.list_layouts(store)]


# ------------------------------------------------------------------ 守卫（纯函数 + 真文件系统）


def test_unsafe_names_are_a_pure_function():
    for bad in ("CON", "nul", "Com1", "LPT9", "aux", "name.", "name ", "", "x" * 300):
        assert layoutdir.unsafe_dir_name(bad), bad
    for good in ("图一", "figure_1", "CONSOLE", "com10", "a-b"):
        assert not layoutdir.unsafe_dir_name(good), good


def test_unsafe_name_falls_back_to_the_legacy_file(store, writer_on):
    layoutdir.write(store, "CON", _bytes())
    assert [p.name for p in store.iterdir()] == ["CON.json"]


def test_collision_key_ignores_case_and_unicode_form():
    assert layoutdir.collision_key("A.tavotto") == layoutdir.collision_key("a.tavotto")
    assert layoutdir.collision_key("é") == layoutdir.collision_key("é")


def test_case_collision_between_directory_names_is_refused(store, writer_on):
    layoutdir.write(store, "Fig", _bytes())
    # 另一个只差大小写的名字：不论盘区不区分大小写，都不许落进同一个/相邻的目录名
    loc = layoutdir.locate(store, "fig")
    assert loc.problem == layoutdir.PROBLEM_COLLISION
    with pytest.raises(layoutdir.LayoutDirError) as exc:
        layoutdir.write(store, "fig", _bytes())
    assert exc.value.code == layoutdir.PROBLEM_COLLISION
    # 区分大小写的盘上 `A.json` 与 `a.json` 是两份排版：没有目录形态时互不干扰
    (store / "Up.json").write_bytes(_bytes())
    assert layoutdir.locate(store, "Up").state == layoutdir.LEGACY


def test_suggest_name_uses_canvas_name_and_dedupes(store, writer_on):
    assert layoutdir.suggest_name(store, "Figure 1") == "Figure_1"
    layoutdir.write(store, "Figure_1", _bytes())
    assert layoutdir.suggest_name(store, "Figure 1") == "Figure_1_2"
    (store / "figure_1_2.json").write_bytes(_bytes())  # 只差大小写也算重名
    assert layoutdir.suggest_name(store, "Figure 1") == "Figure_1_3"
    assert layoutdir.suggest_name(store, None) == layoutdir.DEFAULT_NEW_NAME
    assert layoutdir.suggest_name(store, "CON") == "CON_"
    assert (
        layoutdir.suggest_name(store, "_styles") == "styles"
    )  # 边上的下划线去掉，不会撞上 Tavotto 自己的文件名
    # 净化与后端 `layout_path` 同一条正则
    for raw in ("a b/c..d", "图 一", "x:y*z", "  "):
        got = layoutdir.suggest_name(store, raw)
        assert m.layout_path(got).stem == got


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要权限")
def test_symlinked_directory_or_inner_file_is_refused(store, writer_on, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (store / "a.json").write_bytes(layoutdir.placeholder_bytes("a"))
    (store / "a.tavotto").symlink_to(outside, target_is_directory=True)
    loc = layoutdir.locate(store, "a")
    assert loc.problem == layoutdir.PROBLEM_SYMLINK and loc.read_path is None
    with pytest.raises(layoutdir.LayoutDirError):
        layoutdir.write(store, "a", _bytes())
    assert list(outside.iterdir()) == [], "写穿了链接"
    # 目录是真的、layout.json 是链接
    (store / "a.tavotto").unlink()
    layoutdir.write(store, "b", _bytes())
    target = tmp_path / "elsewhere.json"
    target.write_bytes(_bytes())
    (store / "b.tavotto" / "layout.json").unlink()
    (store / "b.tavotto" / "layout.json").symlink_to(target)
    assert layoutdir.locate(store, "b").problem == layoutdir.PROBLEM_SYMLINK
    with pytest.raises(layoutdir.LayoutDirError):
        layoutdir.write(store, "b", _bytes(updatedAt=5))
    assert target.read_bytes() == _bytes()
    assert "b" not in [loc.stem for loc in layoutdir.list_layouts(store)]


def test_newer_container_version_is_read_only(store, writer_on):
    layoutdir.write(store, "a", _bytes())
    mp = store / "a.tavotto" / "package_manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["container_version"] = layoutdir.CONTAINER_VERSION + 1
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    loc = layoutdir.locate(store, "a")
    assert loc.read_path is not None and loc.problem == layoutdir.PROBLEM_TOO_NEW
    with pytest.raises(layoutdir.LayoutDirError) as exc:
        layoutdir.write(store, "a", _bytes(updatedAt=2))
    assert exc.value.code == layoutdir.PROBLEM_TOO_NEW


# ------------------------------------------------------------------ HTTP：app 的读写两端


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path / "data_layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / "data_layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "userdata"))
    m.app.config["TESTING"] = True
    project = tmp_path / "proj"
    project.mkdir()
    pj = m.open_project(str(project))["id"]
    yield m.app.test_client(), project, {"X-Tavotto-Project": pj}
    m.reset_projects()


def _save(client, headers, name, doc=None, **params):
    qs = {"target": "project", **params}
    return client.post(f"/api/layouts/{name}", query_string=qs, json=doc or PD, headers=headers)


def test_http_round_trip_with_writer_on(env, writer_on):
    client, project, h = env
    first = _save(client, h, "排版一", base_revision=m.REVISION_ABSENT)
    assert first.status_code == 200, first.get_json()
    body = first.get_json()
    store = project / "tavottofile"
    assert body["file"] == "tavottofile/排版一.json", "会话绑定的身份仍是逻辑路径"
    assert (store / "排版一.tavotto" / "layout.json").is_file()
    assert body["revision"] == _rev((store / "排版一.tavotto" / "layout.json").read_bytes())
    # GET：读目录里的正文，修订号头是 layout.json 的，布局文件头仍是逻辑路径
    got = client.get("/api/layouts/排版一", headers=h)
    assert got.status_code == 200
    assert got.headers["X-Tavotto-Revision"] == body["revision"]
    assert json.loads(got.data) == PD
    assert "tavottofile/" in got.headers["X-Tavotto-Layout-File"].replace("%2F", "/")
    # 列表：占位不算，目录形态算，且只有一条
    assert client.get("/api/layouts", headers=h).get_json()["layouts"] == ["排版一"]
    # 带基线再存
    second = _save(client, h, "排版一", doc={**PD, "updatedAt": 2}, base_revision=body["revision"])
    assert second.status_code == 200, second.get_json()
    assert json.loads((store / "排版一.tavotto" / "layout.json").read_bytes())["updatedAt"] == 2


def test_http_writer_off_matches_the_pre_change_behaviour(env, writer_off):
    client, project, h = env
    r = _save(client, h, "排版一", base_revision=m.REVISION_ABSENT)
    assert r.status_code == 200
    store = project / "tavottofile"
    # `export/` 是打开项目时就建好的收纳目录，与排版无关
    assert sorted(p.name for p in store.iterdir() if p.name != "export") == ["排版一.json"]
    # （Flask 测试客户端发出去的键是排过序的）
    # 改造前的写法：校验后的文档 `write_json(indent=1)`——字节必须逐字相同
    expected = atomicio.dumps_json(
        m._validate_storage_document(
            json.loads(json.dumps(PD, sort_keys=True)), require_sources=False
        ),
        indent=1,
    )
    assert (store / "排版一.json").read_bytes() == expected
    assert r.get_json()["revision"] == _rev(expected)


def test_http_external_change_on_directory_layout_is_refused_and_summarises_layout_json(
    env, writer_on
):
    client, project, h = env
    base = _save(client, h, "a", base_revision=m.REVISION_ABSENT).get_json()["revision"]
    layout = project / "tavottofile" / "a.tavotto" / "layout.json"
    layout.write_bytes(_bytes(updatedAt=50, project={"id": "p", "name": "git pull 来的"}))
    after = layout.read_bytes()
    r = _save(client, h, "a", doc={**PD, "updatedAt": 3}, base_revision=base)
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "external_change"
    assert body["revision"] == _rev(after)
    assert body["summary"] is not None and body["summary"]["schema"] == 3, "摘要读成了占位"
    assert layout.read_bytes() == after, "被拒的那次写了盘"
    # 明确覆盖：基线 = 409 回的 revision
    assert _save(client, h, "a", base_revision=body["revision"]).status_code == 200


def test_http_old_build_overwrote_placeholder_conflict_round_trip(env, writer_on):
    client, project, h = env
    base = _save(client, h, "a", base_revision=m.REVISION_ABSENT).get_json()["revision"]
    store = project / "tavottofile"
    foreign = _bytes(updatedAt=77)
    (store / "a.json").write_bytes(foreign)  # 旧版在占位上另存
    # 手里是旧基线 → 409，revision 指向外来的那份
    r = _save(client, h, "a", base_revision=base)
    assert r.status_code == 409 and r.get_json()["revision"] == _rev(foreign)
    # 重新加载读到的就是外来的那份，且修订号与 409 里的一致
    got = client.get("/api/layouts/a", headers=h)
    assert got.data == foreign and got.headers["X-Tavotto-Revision"] == _rev(foreign)
    # 带这个基线保存（明确覆盖 / 合并后保存）：通过，占位换回，两边都留在 legacy/
    ok = _save(client, h, "a", doc={**PD, "updatedAt": 5}, base_revision=_rev(foreign))
    assert ok.status_code == 200, ok.get_json()
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a")
    kept = [p.read_bytes() for p in (store / "a.tavotto" / "legacy").iterdir()]
    assert foreign in kept


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要权限")
def test_http_symlinked_directory_is_refused(env, writer_on, tmp_path):
    client, project, h = env
    store = project / "tavottofile"
    store.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store / "a.tavotto").symlink_to(outside, target_is_directory=True)
    r = _save(client, h, "a", base_revision=m.REVISION_ABSENT)
    assert r.status_code == 400 and r.get_json()["code"] == "layout_outside_project"
    assert list(outside.iterdir()) == []


def test_http_case_collision_is_refused(env, writer_on):
    client, project, h = env
    assert _save(client, h, "Fig", base_revision=m.REVISION_ABSENT).status_code == 200
    r = _save(client, h, "fig", base_revision=m.REVISION_ABSENT)
    assert r.status_code == 409 and r.get_json()["code"] == "layout_name_collision"


def test_opening_and_reading_never_write(env, writer_on):
    """首开不变式：打开项目、列出、读取，在每一种磁盘状态下都不写一个字节（也不动 mtime）。"""
    client, project, h = env
    store = project / "tavottofile"
    store.mkdir(exist_ok=True)
    # DIR / LEGACY / ORPHAN / PENDING / CONFLICT 都摆一份
    layoutdir.write(store, "dirform", _bytes())
    (store / "legacy.json").write_bytes(_bytes())
    (store / "orphan.json").write_bytes(layoutdir.placeholder_bytes("orphan"))
    (store / "pend.json").write_bytes(_bytes(updatedAt=7))
    try:
        layoutdir.FAULT_HOOK = lambda p: (
            (_ for _ in ()).throw(RuntimeError()) if p == "dir_published" else None
        )
        with pytest.raises(RuntimeError):
            layoutdir.write(store, "pend", _bytes(updatedAt=8))
    finally:
        layoutdir.FAULT_HOOK = None
    layoutdir.write(store, "conf", _bytes())
    (store / "conf.json").write_bytes(_bytes(updatedAt=9))
    before = _snapshot(project)
    m.open_project(str(project))
    assert client.get("/api/layouts", headers=h).status_code == 200
    for name in ("dirform", "legacy", "orphan", "pend", "conf"):
        client.get(f"/api/layouts/{name}", headers=h)
        client.get(
            f"/api/layouts/{name}/agent-additions", query_string={"base_revision": "x"}, headers=h
        )
    after = _snapshot(project)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    # 打开项目只允许写注册表
    assert changed <= {"tavotto_registry.json"}, changed


# ------------------------------------------------------------------ 其它读取端认占位


def _write_dir_layout(store: Path, name: str, doc: dict) -> None:
    layoutdir.write(store, name, atomicio.dumps_json(doc, indent=1), enable_new=True)


def test_runtimeasset_scan_sees_directory_layouts_and_skips_placeholders(store):
    doc = {**PD, "canvases": [{**PD["canvases"][0], "marker": "runtime:fig.py#main"}]}
    _write_dir_layout(store, "uses", doc)
    seen: list[list[str]] = []

    def fake_uses(strings, registry, script, stem):
        seen.append(strings)
        return any("runtime:fig.py#main" in x for x in strings)

    orig = runtimeasset.uses_stem
    runtimeasset.uses_stem = fake_uses
    try:
        assert runtimeasset.saved_layouts_use_stem([store], None, "fig.py", "main")
    finally:
        runtimeasset.uses_stem = orig
    # 占位本身从不被当文档扫（它没有任何引用）
    assert all("tavotto_layout_dir" not in " ".join(s) for s in seen)


def test_tutorial_saved_layouts_counts_directory_layouts(tmp_path):
    dest = tmp_path / "copy"
    store = dest / "tavottofile"
    store.mkdir(parents=True)
    _write_dir_layout(store, "mine", PD)
    (store / "plain.json").write_bytes(_bytes())
    (dest / "canvases").mkdir()
    assert tutorial.saved_layouts(dest, {"document_name": "tutorial"}) == ["mine", "plain"]
    (store / "mine.json").unlink()  # 占位被删，目录还在
    assert tutorial.saved_layouts(dest, {"document_name": "tutorial"}) == ["mine", "plain"]


def test_layoutannotate_reads_lists_and_writes_through_the_store(tmp_path):
    project = tmp_path / "proj"
    store = project / "tavottofile"
    store.mkdir(parents=True)
    _write_dir_layout(store, "a", PD)
    (store / "legacy.json").write_bytes(_bytes())
    assert layoutannotate.list_layouts(project) == ["a", "legacy"]
    lay = layoutannotate.open_layout(project, "a")
    assert lay.revision == _rev(_bytes()) and lay.doc["canvases"]

    def rename(canvas):
        canvas["name"] = "改过"

    after, token = layoutannotate.write_canvas(project, "a", lay, "c1", rename)
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a"), "正文盖在了占位上"
    layout = store / "a.tavotto" / "layout.json"
    assert json.loads(layout.read_bytes())["canvases"][0]["name"] == "改过"
    assert after == _rev(layout.read_bytes())
    # 一步撤销：恢复的是 layout.json，占位仍在
    undone = layoutannotate.undo(project, "a", token)
    assert undone["revision"] == _rev(_bytes())
    assert layout.read_bytes() == _bytes()
    assert (store / "a.json").read_bytes() == layoutdir.placeholder_bytes("a")


def test_layoutannotate_refuses_a_symlinked_layout_directory(tmp_path):
    if sys.platform == "win32":
        pytest.skip("符号链接需要权限")
    project = tmp_path / "proj"
    store = project / "tavottofile"
    store.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store / "a.json").write_bytes(layoutdir.placeholder_bytes("a"))
    (store / "a.tavotto").symlink_to(outside, target_is_directory=True)
    with pytest.raises(layoutannotate.LayoutError) as exc:
        layoutannotate.open_layout(project, "a")
    assert exc.value.code == "layout_outside_project"


def test_svg_reference_scan_covers_directory_layouts(env, writer_on):
    _client, project, _h = env
    sid = "ab" * 32
    doc = {**PD, "note": sid}
    store = project / "tavottofile"
    store.mkdir(exist_ok=True)
    _write_dir_layout(store, "refs", doc)
    corpus = m._svg_reference_corpus(project)
    assert corpus is not None and sid.encode() in corpus
