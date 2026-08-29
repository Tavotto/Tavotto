"""safe_resolve 越权防护、layout_path 清洗、baked 历史的迁移与截断。"""

import json

import pytest
from werkzeug.exceptions import HTTPException

from tavotto import app as m
from tavotto.pdfbackend import pymupdf_backend as pb


@pytest.fixture
def figs(tmp_path, monkeypatch):
    (tmp_path / "ok.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "evil.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "img.png").write_bytes(b"\x89PNG fake")
    (tmp_path.parent / "outside.pdf").write_bytes(b"%PDF-1.4 fake")
    m.open_project(str(tmp_path))
    return tmp_path


def _code(fn, *args):
    with pytest.raises(HTTPException) as e:
        fn(*args)
    return e.value.code


def test_safe_resolve_ok(figs):
    assert m.safe_resolve("ok.pdf") == figs / "ok.pdf"
    assert m.safe_resolve("sub/img.png") == figs / "sub" / "img.png"


def test_safe_resolve_blocks_traversal(figs):
    assert _code(m.safe_resolve, "../outside.pdf") == 403


def test_safe_resolve_blocks_wrong_ext(figs):
    assert _code(m.safe_resolve, "evil.txt") == 403


def test_safe_resolve_404_on_missing(figs):
    assert _code(m.safe_resolve, "missing.pdf") == 404


def test_layout_path_sanitizes_separators():
    p = m.layout_path("我的布局/../x")
    assert p.parent == m.LAYOUT_DIR
    assert "/" not in p.name and ".." not in p.name
    assert p.suffix == ".json"


def test_layout_path_rejects_empty():
    assert _code(m.layout_path, "") == 400


def test_layout_path_collapses_illegal_chars_to_underscore():
    # 现状：非法字符折叠为 "_"（可能同名碰撞，但不构成路径逃逸）
    assert m.layout_path("###").name == "_.json"


def _make_project(tmp_path, name: str, stems: list[str]):
    """建一个带注册表的项目目录并打开，返回它的 ProjectCtx。"""
    figs = tmp_path / name
    figs.mkdir(parents=True, exist_ok=True)
    (figs / "fig.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig.py": {"entry": "main", "cost": "light", "notes": "", "stems": stems},
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(figs))
    return m.PROJECTS[m._project_id(figs.resolve())]


@pytest.fixture
def baked(tmp_path, monkeypatch):
    """baked 基线按项目分键（DATA_ROOT/baked_overrides/<项目id>.json）。

    旧的全局 baked_overrides.json 只作为一次性迁移源，测试里也指到临时文件，
    绝不碰真实数据目录。
    """
    from tavotto.engine import project_watch as engine_watch

    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    yield tmp_path / "_legacy_baked.json"
    m.reset_projects()
    engine_watch.stop()


def test_load_baked_missing_file(baked, tmp_path):
    ctx = _make_project(tmp_path, "pa", ["Fig1"])
    assert m.load_baked(ctx) == {}


def test_load_baked_migrates_legacy_single_version(baked, tmp_path):
    ctx = _make_project(tmp_path, "pa", ["Fig1"])
    m._baked_path(ctx).parent.mkdir(parents=True, exist_ok=True)
    m._baked_path(ctx).write_text(
        json.dumps(
            {
                "Fig1": {
                    "patches": [{"gid": "g", "prop": "p", "value": 1}],
                    "updated_at": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    data = m.load_baked(ctx)
    assert data["Fig1"]["versions"] == [
        {"ts": "2026-01-01", "patches": [{"gid": "g", "prop": "p", "value": 1}]}
    ]


def test_append_baked_concurrent_no_lost_updates(baked, tmp_path):
    """threaded Flask 下多线程并发追加，30 条版本一条不丢。"""
    from concurrent.futures import ThreadPoolExecutor

    ctx = _make_project(tmp_path, "pa", ["Fig1"])

    def add(i: int) -> None:
        m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": i}], ctx)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(add, range(30)))
    versions = m.load_baked(ctx)["Fig1"]["versions"]
    assert len(versions) == 30
    assert {v["patches"][0]["value"] for v in versions} == set(range(30))


def test_append_baked_appends_and_trims_to_50(baked, tmp_path):
    ctx = _make_project(tmp_path, "pa", ["Fig1"])
    for i in range(55):
        m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": i}], ctx)
    versions = m.load_baked(ctx)["Fig1"]["versions"]
    assert len(versions) == 50
    assert versions[-1]["patches"][0]["value"] == 54  # 末位 = 当前基线
    assert versions[0]["patches"][0]["value"] == 5


def test_baked_is_scoped_per_project(baked, tmp_path):
    """两个项目里的同名 stem 互不可见——曾经共用一个全局文件按 stem 索引，
    B 项目新拖入的 Fig1 会继承 A 项目的 override，写回时把别人的改动烙进图。"""
    a = _make_project(tmp_path, "pa", ["Fig1"])
    b = _make_project(tmp_path, "pb", ["Fig1"])
    assert a.id != b.id

    m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": "A"}], a)
    assert m.load_baked(b) == {}
    assert m._baseline_patches("Fig1", m.load_baked(b)) == []

    m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": "B"}], b)
    assert m._baseline_patches("Fig1", m.load_baked(a))[0]["value"] == "A"
    assert m._baseline_patches("Fig1", m.load_baked(b))[0]["value"] == "B"


def test_legacy_global_baked_migrates_once_filtered_by_registry(baked, tmp_path):
    """旧全局文件按注册表过滤搬进分键文件，且**只搬一次**。

    别的项目的 stem 留在旧文件里等它自己迁移（所以旧文件不删）；迁移之后
    分键文件即唯一权威——再往旧文件里塞东西也不会漏进来。
    """
    baked.write_text(
        json.dumps(
            {
                "Fig1": {
                    "versions": [{"ts": "t", "patches": [{"gid": "g", "prop": "p", "value": 1}]}]
                },
                "Other": {
                    "versions": [{"ts": "t", "patches": [{"gid": "x", "prop": "p", "value": 2}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    ctx = _make_project(tmp_path, "pa", ["Fig1"])

    data = m.load_baked(ctx)
    assert set(data) == {"Fig1"}  # 本项目认得的 stem 才搬
    assert m._baked_path(ctx).is_file()
    assert baked.is_file()  # 旧文件保留：别的项目还要迁

    # 迁移只发生一次：旧文件后来变了也不再影响本项目
    legacy = json.loads(baked.read_text(encoding="utf-8"))
    legacy["Fig1"]["versions"].append({"ts": "t2", "patches": []})
    baked.write_text(json.dumps(legacy), encoding="utf-8")
    assert len(m.load_baked(ctx)["Fig1"]["versions"]) == 1


def test_legacy_migration_writes_empty_file_when_nothing_matches(baked, tmp_path):
    """一条都没搬也要落一个空文件——否则「本项目没有基线」与「还没迁移」
    分不开，每次读都要再翻一遍旧文件。"""
    baked.write_text(
        json.dumps({"Other": {"versions": [{"ts": "t", "patches": []}]}}), encoding="utf-8"
    )
    ctx = _make_project(tmp_path, "pa", ["Fig1"])
    assert m.load_baked(ctx) == {}
    assert json.loads(m._baked_path(ctx).read_text(encoding="utf-8")) == {}


def test_crop_clip_maps_normalized_rect():
    import pymupdf

    src = pymupdf.Rect(0, 0, 100, 200)
    clip = pb._crop_clip(src, {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25})
    assert (clip.x0, clip.y0, clip.x1, clip.y1) == (25, 100, 75, 150)
    assert pb._crop_clip(src, None) is None


def test_hex2rgb():
    assert pb.hex2rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert pb.hex2rgb("#0f0") == (0.0, 1.0, 0.0)
    assert pb.hex2rgb("garbage") == (0.0, 0.0, 0.0)
    assert pb.hex2rgb(None) == (0.0, 0.0, 0.0)


def test_tavottofile_store(figs, monkeypatch):
    """项目内 tavottofile/ 统一收纳（2026-08-17 产品决定）：

    - 命名画布保存进 `tavottofile/`，旧位置（项目 canvases/、数据目录
      layouts/）只读兼容、合并列出；
    - 布局版本历史写进 `tavottofile/versions/`，数据目录旧历史仍可见；
    - 素材扫描剪掉整个 tavottofile/——导出的成图绝不能混进素材面板。
    """
    monkeypatch.setattr(m, "LAYOUT_DIR", figs / "_data_layouts")
    monkeypatch.setattr(m, "VERSIONS_DIR", figs / "_data_layouts" / "_versions")
    m.app.config["TESTING"] = True
    client = m.app.test_client()

    # 保存画布 → 项目 tavottofile/
    r = client.post("/api/layouts/主图", json={"schema": 2})
    assert r.status_code == 200
    assert (figs / "tavottofile" / "主图.json").is_file()

    # 旧位置只读兼容：canvases/ 与数据目录 layouts/ 都列得出、读得到
    (figs / "canvases").mkdir()
    (figs / "canvases" / "旧画布.json").write_text('{"schema": 2}', encoding="utf-8")
    (figs / "_data_layouts").mkdir(parents=True, exist_ok=True)
    (figs / "_data_layouts" / "更早.json").write_text('{"schema": 2}', encoding="utf-8")
    names = client.get("/api/layouts").get_json()["layouts"]
    assert {"主图", "旧画布", "更早"} <= set(names)
    assert client.get("/api/layouts/旧画布").status_code == 200

    # 版本历史 → tavottofile/versions/
    ok = client.post(
        "/api/versions/doc9",
        json={"doc": {"schema": 2, "page": {"w": 10, "h": 10}, "objects": [], "guides": []}},
    )
    assert ok.status_code == 200, ok.get_json()
    assert (figs / "tavottofile" / "versions" / "doc9.json").is_file()

    # 数据目录里升级前的历史只读兜底
    m.VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (m.VERSIONS_DIR / "docold.json").write_text(
        json.dumps({"versions": [{"id": "v1", "name": "n", "ts": 0, "doc": {}}]}), encoding="utf-8"
    )
    got = client.get("/api/versions/docold").get_json()["versions"]
    assert [v["id"] for v in got] == ["v1"]

    # 素材扫描剪掉 tavottofile/：同一份真 PDF，根目录的收录、export/ 里的不收
    import pymupdf

    exp = figs / "tavottofile" / "export"
    exp.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "real.pdf")
    doc.save(exp / "out.pdf")
    doc.close()
    ids = {p["id"] for p in client.get("/api/panels").get_json()["panels"]}
    assert not any("tavottofile" in i for i in ids), ids
    assert "real.pdf" in ids
