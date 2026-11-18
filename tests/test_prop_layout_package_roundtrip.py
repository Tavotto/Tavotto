"""属性测试：排版文件 / 项目包的保存—重开往返（ADR 0096 / 可复现项目包）。

主语：**用户保存的那份排版文档**（schema 2 / 3：位置、尺寸、文字、箭头、图形、图板对象）：

1. ⌘S 写进项目 `tavottofile/<名>.json` 之后，**磁盘上的 JSON** 与 **`GET /api/layouts/<名>` 重开读到的文档**都逐字段等于保存时的文档；
   再带着返回的 `revision` 存第二次，磁盘上只剩第二份（没有第一份的残留键、没有临时文件）；
2. `POST /api/package` 打出的项目包，经 `POST /api/package/open` 检视取回的 `doc` 等于打包时的文档，素材清单齐全（无缺失 / 漂移）。

生成域只给产品声称支持的东西：前端 `ExportObject` / 文档类型里有的对象与取值——有限浮点（`NaN` / `Infinity` 写侧直接拒，
另有用例）、任意 Unicode 文字（不含孤立代理）、颜色 / 线型 / 端型取枚举。面板对象只引用夹具里真存在的 PDF 且不带
`overrides` / `figureSnapshot`（带它们的面板在保存时会被 `_freeze_document_svg_sources` 有意改写，不是往返的主语）。

**没有 worker**：热态 == 写进文件的 == 重开后重放的 这条链的「重放」那一腿在 `test_prop_export_py_roundtrip.py`
（编辑值 → 导出脚本 → 全新 worker 重放）；这里钉的是文档字节往返这一腿。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pymupdf
import pytest
from hypothesis import given, settings, strategies as st

from tavotto import app as m
from tavotto.engine import atomicio

# ---------------------------------------------------------------------------
# 生成域
# ---------------------------------------------------------------------------
_num = st.floats(min_value=-5000, max_value=5000, allow_nan=False, allow_infinity=False)
_pos = st.floats(min_value=0.1, max_value=2000, allow_nan=False, allow_infinity=False)
_frac = st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False)
_color = st.from_regex(r"#[0-9a-f]{6}", fullmatch=True)
_text = st.text(max_size=30)  # 含换行 / 控制字符 / 中日韩 / 组合记号：用户真能输入的都算
_dash = st.sampled_from(["dashed", "dotted", "longdash", "dashdot", "longdashdot"])
_head = st.sampled_from(["none", "triangle", "open", "bar", "dot", "diamond", "stealth"])
_id = st.text(alphabet="abcdef0123456789-", min_size=1, max_size=8)


def _box(draw, **extra):
    return {"x": draw(_num), "y": draw(_num), "w": draw(_pos), "h": draw(_pos), **extra}


@st.composite
def _text_obj(draw):
    o = _box(
        draw,
        id=draw(_id),
        type="text",
        text=draw(_text),
        size_pt=draw(_pos),
        bold=draw(st.booleans()),
        italic=draw(st.booleans()),
        color=draw(_color),
        align=draw(st.sampled_from(["left", "center", "right"])),
    )
    if draw(st.booleans()):
        o["font_family"] = draw(st.sampled_from(["serif", "sans-serif", "monospace"]))
    if draw(st.booleans()):
        o["rotation_deg"] = draw(_num)
    return o


@st.composite
def _arrow_obj(draw):
    o = _box(
        draw,
        id=draw(_id),
        type="arrow",
        start={"rx": draw(_frac), "ry": draw(_frac)},
        end={"rx": draw(_frac), "ry": draw(_frac)},
        stroke_pt=draw(_pos),
        color=draw(_color),
        head=draw(st.sampled_from(["none", "end", "both"])),
    )
    for k, s in (("head_start", _head), ("head_end", _head), ("dash", _dash)):
        if draw(st.booleans()):
            o[k] = draw(s)
    return o


@st.composite
def _shape_obj(draw):
    return _box(
        draw,
        id=draw(_id),
        type="shape",
        shape=draw(st.sampled_from(["rect", "ellipse", "line", "polygon", "brace"])),
        stroke_pt=draw(_pos),
        color=draw(_color),
        fill=draw(st.one_of(st.none(), _color)),
    )


@st.composite
def _panel_obj(draw):
    return _box(
        draw,
        id=draw(_id),
        type="panel",
        fileId="p1.pdf",
        fileKind="pdf",
        nativeW=35,
        nativeH=17,
        overrides=[],
    )


_obj = st.one_of(_text_obj(), _arrow_obj(), _shape_obj(), _panel_obj())
_guides = st.lists(
    st.fixed_dictionaries({"axis": st.sampled_from(["x", "y"]), "pos": _num}), max_size=3
)
_page = st.fixed_dictionaries({"w": _pos, "h": _pos})


@st.composite
def _canvas(draw, cid: str):
    return {
        "id": cid,
        "name": draw(st.text(min_size=1, max_size=10)),
        "page": draw(_page),
        "objects": draw(st.lists(_obj, max_size=6)),
        "guides": draw(_guides),
    }


@st.composite
def _document(draw):
    """schema 3（多画布项目）或 schema 2（单画布旧文档）。"""
    if draw(st.booleans()):
        n = draw(st.integers(1, 3))
        canvases = [draw(_canvas(f"c{i}")) for i in range(n)]
        return {
            "schema": 3,
            "project": {"id": "p", "name": draw(st.text(min_size=1, max_size=10))},
            "canvases": canvases,
            "activeCanvasId": "c0",
            "createdAt": draw(st.integers(0, 2**40)),
            "updatedAt": draw(st.integers(0, 2**40)),
        }
    c = draw(_canvas("c0"))
    return {
        "schema": 2,
        "name": draw(st.text(min_size=1, max_size=10)),
        "page": c["page"],
        "objects": c["objects"],
        "guides": c["guides"],
    }


_names = st.sampled_from(["排版一", "fig a", "A.b", "x"])

# ---------------------------------------------------------------------------
# 环境：每个例子一个全新的项目目录（`base_revision=absent` 只对不存在的文件成立）
# ---------------------------------------------------------------------------
_PDF = None


def _pdf_bytes() -> bytes:
    global _PDF
    if _PDF is None:
        d = pymupdf.open()
        d.new_page(width=100, height=50)
        _PDF = d.tobytes()
        d.close()
    return _PDF


@pytest.fixture(scope="module")
def sandbox():
    """模块级：一个根目录 + 隔离的数据目录；例子各自在根下建项目目录。不用函数级 fixture（Hypothesis 不为每个例子重建它）。"""
    root = Path(tempfile.mkdtemp(prefix="tavotto-prop-layout-"))
    mp = pytest.MonkeyPatch()
    mp.setattr(m, "LAYOUT_DIR", root / "data_layouts")
    mp.setattr(m, "AUTOSAVE_DIR", root / "data_layouts" / m.engine_documents.AUTOSAVE_DIRNAME)
    mp.setenv("TAVOTTO_DATA_DIR", str(root / "userdata"))
    m.app.config["TESTING"] = True
    m.reset_projects()
    try:
        yield root
    finally:
        m.reset_projects()
        mp.undo()
        shutil.rmtree(root, ignore_errors=True)


_COUNTER = iter(range(10**9))


def _project(root: Path) -> tuple[Path, str]:
    proj = root / f"proj{next(_COUNTER)}"
    proj.mkdir()
    (proj / "p1.pdf").write_bytes(_pdf_bytes())
    return proj, m.open_project(str(proj))["id"]


_SETTINGS = dict(
    max_examples=int(os.environ.get("TAVOTTO_PROP_EXAMPLES", "0")) or 80,
    deadline=None,
    derandomize=not int(os.environ.get("TAVOTTO_PROP_EXAMPLES", "0")),
    database=None,
)


# ---------------------------------------------------------------------------
# 性质
# ---------------------------------------------------------------------------
def test_saved_layout_reopens_to_the_same_document(sandbox):
    client = m.app.test_client()

    @settings(**_SETTINGS)
    @given(first=_document(), second=_document(), name=_names)
    def prop(first, second, name):
        proj, pj = _project(sandbox)
        hdr = {"X-Tavotto-Project": pj}
        url = f"/api/layouts/{name}?target=project"
        r1 = client.post(f"{url}&base_revision={m.REVISION_ABSENT}", json=first, headers=hdr)
        assert r1.status_code == 200, r1.get_json()
        target = proj / r1.get_json()["file"]
        # 磁盘上的字节 == 保存的文档；重开（GET）读到的 == 保存的文档
        assert json.loads(target.read_text(encoding="utf-8")) == first
        got = client.get(f"/api/layouts/{name}", headers=hdr)
        assert got.status_code == 200 and got.get_json() == first
        # 再存一次（带上次的修订号）：磁盘上只剩第二份，没有第一份的残留键，没有临时文件
        r2 = client.post(
            f"{url}&base_revision={r1.get_json()['revision']}", json=second, headers=hdr
        )
        assert r2.status_code == 200, r2.get_json()
        assert json.loads(target.read_text(encoding="utf-8")) == second
        assert client.get(f"/api/layouts/{name}", headers=hdr).get_json() == second
        assert r2.get_json()["revision"] == atomicio.content_revision(target)
        assert [p.name for p in target.parent.iterdir() if p.is_file()] == [target.name]

    prop()


def test_packed_project_opens_to_the_same_document(sandbox, monkeypatch):
    client = m.app.test_client()

    @settings(
        **{
            **_SETTINGS,
            "max_examples": 50 if _SETTINGS["derandomize"] else _SETTINGS["max_examples"],
        }
    )
    @given(doc=_document())
    def prop(doc):
        proj, pj = _project(sandbox)
        monkeypatch.setattr(m, "EXPORT_DIR", proj / "exports")
        hdr = {"X-Tavotto-Project": pj}
        packed = json.loads(json.dumps(doc))  # 请求体是一份拷贝：服务端怎么改它都不许串回 `doc`
        resp = client.post("/api/package", json={"stem": "pk", "doc": packed}, headers=hdr)
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        n_panels = sum(1 for o in m._doc_objects(doc) if o.get("type") == "panel")
        # 同一个文件被几个面板引用，只入包一份
        assert body["missing"] == []
        assert body["assets"] == (1 if n_panels else 0)
        zpath = proj / "tavottofile" / "export" / body["name"]
        with zipfile.ZipFile(zpath) as z:
            assert json.loads(z.read("layout.json")) == doc
        reopened = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(zpath.read_bytes()), "pk.zip")},
            content_type="multipart/form-data",
            headers=hdr,
        )
        assert reopened.status_code == 200, reopened.get_json()
        out = reopened.get_json()
        assert out["doc"] == doc
        assert out["missing"] == [] and out["drifted"] == []
        if doc["schema"] == 2:
            assert out["manifest"]["page"] == doc["page"]

    prop()
