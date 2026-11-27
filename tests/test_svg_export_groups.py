"""SVG 导出给 Illustrator 精修（ADR 0208 §七之三 第 1–5 条）：组原样导出、名字写进 `id`、剪切组只写一次、
第一个字体族写真实字体、一个文字对象一个 `<text>`。

两条口径：

* 共享样本 `tests/golden/svg_export_groups.json`（前端 `lib/svgExportGroups.golden.test.ts` 用真实的
  `toExportObjects` 写）：载荷走真实的 `plan.compile_page` → `svgwriter.write_svg`，解析写出的 SVG，核 `<g>` 树
  与文档的组树一致、名字一个不少、剪切组的蒙版只出现一次、`font-family` 第一项在映射表里。
* 逐字节不变：PDF（含 PNG 的来源）一律不看组与名字；没有组也没有名字的文档，SVG 里一个新属性也不多。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore import ir, plan, svgfonts
from tavotto.rendercore.svgwriter import SVG, write_svg, xml_id

ROOT = Path(__file__).resolve().parent
GOLDEN = json.loads((ROOT / "golden" / "svg_export_groups.json").read_text(encoding="utf-8"))
HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "fontTools"))
needs_rc = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")
NS = {"s": SVG}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"not_run: approved fonts missing: {reg.missing}")
    return HbFaceProvider(reg)


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    """中文的第一个族按平台写：这里钉成 macOS，让用例在三个平台上同一个结果（平台分支另有用例）。"""
    monkeypatch.setattr(sys, "platform", "darwin")


def _svg(tmp_path, objects, provider, *, page=(120, 90), name="o.svg"):
    compiled = plan.compile_page(page[0], page[1], objects, sources=None, faces=provider)
    path = tmp_path / name
    write_svg(compiled.svg_page or compiled.page, path, provider, {})
    return path, ET.parse(path).getroot(), compiled


def _local(el):
    return el.tag.rsplit("}", 1)[-1]


def _is_doc_group(el) -> bool:
    """文档里的组 `<g>`：只可能带 id / clip-path（对象自己的 `<g>` 一定带变换或填充）。"""
    return _local(el) == "g" and set(el.attrib) <= {"id", "clip-path"}


def _tree(container) -> list:
    out = []
    for el in container:
        if _is_doc_group(el):
            out.append({"g": _tree(el), "clip": "clip-path" in el.attrib})
        elif _local(el) in ("path", "text", "g", "image"):
            out.append({"o": el.get("id")})
    return out


def _canvas(root):
    """页面最外层那个翻转 y 的 `<g>`。"""
    return root.find("s:g", NS)


# ---------------------------------------------------------------------------
# 共享样本
# ---------------------------------------------------------------------------
def _names(tree) -> list[str | None]:
    out = []
    for n in tree:
        if "o" in n:
            out.append(n["o"])
        else:
            out += _names(n["g"])
    return out


def _expected_tree_with_ids(tree, ids):
    """期望树里的名字（文档里的原名）换成 `xml_id` 之后的 id，重名按出现顺序加序号。"""
    out = []
    for n in tree:
        if "o" in n:
            out.append({"o": next(ids)})
        else:
            out.append({"g": _expected_tree_with_ids(n["g"], ids), "clip": n["clip"]})
    return out


@needs_rc
@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_the_exported_g_tree_matches_the_documents_group_tree(case, tmp_path, provider):
    _, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
    got = _tree(_canvas(root))
    # 名字只在对象身上：期望树的叶子名字是文档里的原名，写进 SVG 的是清洗并去重后的 `id`
    uniq: dict[str, int] = {}

    def expect_ids():
        for name in _names(case["tree"]):
            base = xml_id(name) if name else None
            if base is None:
                yield None
                continue
            uniq[base] = uniq.get(base, 0) + 1
            yield base if uniq[base] == 1 else f"{base}_{uniq[base]}"

    want = _expected_tree_with_ids(case["tree"], expect_ids())
    assert got == want


@needs_rc
@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_every_object_name_survives_as_a_unique_id_and_ids_are_unique(case, tmp_path, provider):
    _, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids)), "整份文档的 id 必须唯一"
    named = [o["name"] for o in case["payload"] if o.get("name")]
    user_ids = [i for i in ids if not i.startswith("tavotto-")]
    # 对象名一个不少（组没有名字，不占 id）
    assert len(user_ids) >= len(named)
    for name in named:
        base = xml_id(name)
        assert base is not None and any(i == base or i.startswith(base + "_") for i in user_ids)


@needs_rc
def test_a_clip_group_writes_its_mask_exactly_once(tmp_path, provider):
    case = next(c for c in GOLDEN["cases"] if c["name"].startswith("一个剪切组"))
    path, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
    text = path.read_text(encoding="utf-8")
    assert text.count("<clipPath") == 1
    assert text.count("clip-path=") == 1
    (group,) = [g for g in root.iter(f"{{{SVG}}}g") if "clip-path" in g.attrib]
    # 三个被裁的对象都在这个组里，自己身上没有蒙版
    inside = [c for c in group if _local(c) in ("path", "g")]
    assert len(inside) == 3
    assert all("clip-path" not in c.attrib for c in list(group.iter())[1:])


@needs_rc
def test_nested_clip_groups_each_write_their_own_mask_once(tmp_path, provider):
    case = next(c for c in GOLDEN["cases"] if c["name"].startswith("外层与内层"))
    path, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
    text = path.read_text(encoding="utf-8")
    assert text.count("<clipPath") == 2 and text.count("clip-path=") == 2


@needs_rc
def test_interleaved_group_members_keep_the_paint_order(tmp_path, provider):
    """组成员在 z 序里不连续：拆成几段，绘制顺序一个都不挪（与图层树里并成一个组的显示不同）。"""
    case = next(c for c in GOLDEN["cases"] if c["name"].startswith("组成员在 z 序里不连续"))
    _, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
    order = [e.get("id") for e in _canvas(root).iter() if e.get("id") and _local(e) == "path"]
    assert order == ["甲", "夹在中间的", "乙", "丙"]
    top = _tree(_canvas(root))
    assert [("g" in n) for n in top] == [True, False, True]


@needs_rc
def test_groups_carry_no_appearance_so_ungrouping_changes_nothing(tmp_path, provider):
    """§七之二 第 2 条：组上不放变换 / 不透明度 / 剪切（真剪切组除外）。主语只量**文档组**对应的 `<g>`。"""
    for case in GOLDEN["cases"]:
        _, root, _ = _svg(tmp_path, case["payload"], provider, page=case["page_mm"])
        for g in root.iter(f"{{{SVG}}}g"):
            if _is_doc_group(g):
                assert "transform" not in g.attrib and "opacity" not in g.attrib
        clip_groups = [g for g in root.iter(f"{{{SVG}}}g") if "clip-path" in g.attrib]
        assert all(_is_doc_group(g) for g in clip_groups)


@needs_rc
def test_ungrouping_renders_the_same_pixels_as_the_flat_page(tmp_path, provider):
    """拆组后外观不变：把 SVG 页里所有文档组原地展开（保留 z 序），逐对象的 IR 与「平铺页」逐个相同。"""
    case = next(c for c in GOLDEN["cases"] if c["name"].startswith("两层嵌套组"))
    compiled = plan.compile_page(120, 90, case["payload"], sources=None, faces=provider)
    assert compiled.svg_page is not None

    def leaves(nodes):
        for n in nodes:
            if isinstance(n, ir.Group) and n.semantic:
                yield from leaves(n.children)
            else:
                yield n

    flat = [
        (n.object_id, n.transform, n.opacity)
        for n in compiled.page.children
        if isinstance(n, ir.Group)
    ]
    svg = [
        (n.object_id, n.transform, n.opacity)
        for n in leaves(compiled.svg_page.children)
        if isinstance(n, ir.Group)
    ]
    assert svg == flat


# ---------------------------------------------------------------------------
# 逐字节不变
# ---------------------------------------------------------------------------
def _box(x, y, w, h):
    return {"x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h}


def _rect(i, **kw):
    return {
        **_box(10, 10, 30, 20),
        "id": i,
        "type": "shape",
        "shape": "rect",
        "stroke_pt": 1,
        "color": "#333333",
        "fill": "#E69F00",
        **kw,
    }


def _text(i, t, **kw):
    return {
        **_box(10, 40, 40, 8),
        "id": i,
        "type": "text",
        "text": t,
        "size_pt": 8,
        "bold": False,
        "italic": False,
        "color": "#000000",
        "align": "left",
        **kw,
    }


def _pdf(compiled, tmp_path, provider, name):
    from tavotto.rendercore import pdfwriter

    out = tmp_path / name
    pdfwriter.write_pdf(compiled.page, out, provider, {})
    return hashlib.sha256(out.read_bytes()).hexdigest()


@needs_rc
def test_pdf_bytes_do_not_depend_on_names_or_groups(tmp_path, provider):
    base = [_rect("r1"), _text("t1", "Ca^{2+} 受体"), _rect("r2", x_mm=50)]
    dressed = [
        {**base[0], "name": "细胞膜", "group_path": ["g1", "g2"], "group_names": ["甲", "乙"]},
        {**base[1], "name": "标签", "group_path": ["g1"], "group_names": ["甲"]},
        {**base[2], "name": "第三个"},
    ]
    a = plan.compile_page(120, 90, base, sources=None, faces=provider)
    b = plan.compile_page(120, 90, dressed, sources=None, faces=provider)
    assert a.svg_page is None and b.svg_page is not None
    assert a.page == b.page, "PDF / EPS / 位图用的页面一个字段都不能变"
    assert _pdf(a, tmp_path, provider, "a.pdf") == _pdf(b, tmp_path, provider, "b.pdf")


@needs_rc
def test_a_clip_group_pdf_still_clips_every_object_on_its_own(tmp_path, provider):
    """PDF 不读文档的组：剪切组的蒙版照旧复制到每个被裁对象上（逐字节同旧写法）。"""
    m = {
        "type": "path",
        **_box(10, 10, 15, 10),
        "subpaths": [
            {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}
        ],
    }
    old = [_rect("c1", clip=[m]), _rect("c2", clip=[m], x_mm=20)]
    new = [
        _rect("c1", clip=[{**m, "group": "g"}], group_path=["g"], name="甲"),
        _rect("c2", clip=[{**m, "group": "g"}], group_path=["g"], x_mm=20),
    ]
    a = plan.compile_page(120, 90, old, sources=None, faces=provider)
    b = plan.compile_page(120, 90, new, sources=None, faces=provider)
    assert a.page == b.page
    assert _pdf(a, tmp_path, provider, "a.pdf") == _pdf(b, tmp_path, provider, "b.pdf")


@needs_rc
def test_a_document_without_groups_names_or_text_writes_the_same_svg_as_before(tmp_path, provider):
    """没有组、没有名字、没有文字：SVG 一个字节不多（基线 sha256 是改动前的树在同一份夹具上量的）。"""
    objs = [
        _rect("r1"),
        {**_rect("e1", x_mm=50), "shape": "ellipse", "rotation_deg": 20},
        {
            **_box(10, 40, 40, 6),
            "id": "a1",
            "type": "arrow",
            "start": {"rx": 0, "ry": 0.5},
            "end": {"rx": 1, "ry": 0.5},
            "stroke_pt": 1,
            "color": "#000000",
            "head": "end",
        },
    ]
    path, _, compiled = _svg(tmp_path, objs, provider)
    assert compiled.svg_page is None
    text = path.read_text(encoding="utf-8")
    assert "id=" not in text.replace("tavotto-", "")
    # 每个对象恰好一层带变换的 `<g>`（框 → 页面），没有多余的包装
    assert text.count("<g ") == 1 + 3 and "<g>" not in text


# ---------------------------------------------------------------------------
# 名字 → id
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("细胞膜", "细胞膜"),
        ("受体 EGFR", "受体_EGFR"),
        ("  首尾空白  ", "首尾空白"),
        ("1 号样品", "_1_号样品"),
        ("-负号开头", "_-负号开头"),
        ("激活箭头 EGFR→RAS", "激活箭头_EGFR_x2192_RAS"),
        ("a/b", "a_x2F_b"),
        ("tavotto-clip-1", "_tavotto-clip-1"),
        ("ΔG", "ΔG"),
        ("", None),
        ("   ", None),
    ],
)
def test_names_become_valid_xml_ids(name, want):
    assert xml_id(name) == want


@needs_rc
def test_duplicate_names_get_a_serial_and_never_collide_with_generated_ids(tmp_path, provider):
    objs = [
        _rect("a", name="受体", x_mm=10),
        _rect("b", name="受体", x_mm=20),
        _rect("c", name="受体 ", x_mm=30),
        _rect(
            "d",
            name="tavotto-clip-1",
            x_mm=40,
            clip=[
                {
                    "type": "path",
                    **_box(0, 0, 90, 90),
                    "subpaths": [
                        {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}]}
                    ],
                }
            ],
        ),
    ]
    _, root, _ = _svg(tmp_path, objs, provider)
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    assert {"受体", "受体_2", "受体_3"} <= set(ids)
    # 用户把对象叫 `tavotto-clip-1` 不会劫持写入器自己的 clipPath：前面补 `_`
    assert "_tavotto-clip-1" in ids
    clip_ids = [e.get("id") for e in root.iter(f"{{{SVG}}}clipPath")]
    assert clip_ids and all(c.startswith("tavotto-clip-") and c in ids for c in clip_ids)


@needs_rc
def test_an_unnamed_object_and_an_unnamed_group_write_no_id(tmp_path, provider):
    objs = [_rect("a", group_path=["g"]), _rect("b", group_path=["g"], x_mm=40)]
    _, root, _ = _svg(tmp_path, objs, provider)
    assert not [e for e in root.iter() if e.get("id")]
    (group,) = [g for g in root.iter(f"{{{SVG}}}g") if _is_doc_group(g)]
    assert len(group) == 2


@needs_rc
def test_group_names_become_group_ids(tmp_path, provider):
    objs = [
        _rect("a", group_path=["g1", "g2"], group_names=["机理图", "细胞膜"], name="受体"),
        _rect("b", group_path=["g1"], group_names=["机理图"], x_mm=40),
    ]
    _, root, _ = _svg(tmp_path, objs, provider)
    tree = _tree(_canvas(root))
    outer = _canvas(root)[0]
    assert outer.get("id") == "机理图" and outer[0].get("id") == "细胞膜"
    assert tree[0]["g"][0]["g"] == [{"o": "受体"}]


@needs_rc
def test_bad_group_fields_are_refused(provider):
    with pytest.raises(plan.PlanError):
        plan.compile_page(120, 90, [_rect("a", group_path="g")], sources=None, faces=provider)
    with pytest.raises(plan.PlanError):
        plan.compile_page(
            120, 90, [_rect("a", group_path=["g", "g"])], sources=None, faces=provider
        )
    with pytest.raises(plan.PlanError):
        plan.compile_page(
            120,
            90,
            [_rect("a", group_path=["g"], group_names=["x", "y"])],
            sources=None,
            faces=provider,
        )
    with pytest.raises(plan.PlanError):
        plan.compile_page(120, 90, [_rect("a", name=3)], sources=None, faces=provider)


# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------
FAMILY_FIRST = {
    "'Times New Roman'",
    "'Arial'",
    "'Courier New'",
    "'Songti SC'",
    "'Heiti SC'",
    "'Noto Sans SC'",
    "'Noto Serif SC'",
}


@needs_rc
def test_every_text_family_list_starts_with_a_real_font(tmp_path, provider):
    objs = [
        _text("t1", "Receptor"),
        _text("t2", "Receptor", font_family="sans-serif"),
        _text("t3", "x = 1", font_family="monospace"),
        _text("t4", "Ca^{2+} 受体", y_mm=60),
    ]
    _, root, _ = _svg(tmp_path, objs, provider)
    families = [t.get("font-family") for t in root.iter(f"{{{SVG}}}text")]
    assert families
    for fam in families:
        first = fam.split(",")[0].strip()
        assert first in FAMILY_FIRST, fam
        assert "Liberation" not in first
    # 原来的回退链还在，批准的 Liberation 族紧随真实字体之后
    assert any("'Times New Roman', 'Liberation Serif', Times, serif" == f for f in families)
    assert any("'Arial', 'Liberation Sans', Helvetica, sans-serif" == f for f in families)
    assert any("'Courier New', 'Liberation Mono', Courier, monospace" == f for f in families)


@needs_rc
def test_a_text_object_is_one_text_and_split_runs_share_a_named_group(tmp_path, provider):
    objs = [
        _text("one", "Receptor", name="标签"),
        _text("sup", "Ca^{2+}", name="钙离子", y_mm=55),
        _text("mixed", "Receptor 受体", name="受体标签", y_mm=70),
        _text("rot", "Receptor", name="旋转的", rotation_deg=30, y_mm=20, x_mm=60),
    ]
    _, root, _ = _svg(tmp_path, objs, provider)
    by_id = {e.get("id"): e for e in root.iter() if e.get("id")}
    # 一个字体族的一行 = 一个 `<text>`，名字和填充就在它自己身上，没有 `<g fill>` 包着
    assert _local(by_id["标签"]) == "text" and by_id["标签"].get("fill")
    # 旋转并进它自己的变换：仍是一个 `<text>`
    assert _local(by_id["旋转的"]) == "text"
    # 有上下标 / 字体回退的几段：一个以对象名命名的组，组里每段一个 `<text>`
    for name, n in (("钙离子", 2), ("受体标签", 2)):
        g = by_id[name]
        assert _local(g) == "g" and len(g.findall("s:text", NS)) >= n
        assert g.get("fill"), "填充落在这个组上，不再多套一层 `<g fill>`"
        assert not [c for c in g if _local(c) == "g"]


# ---------------------------------------------------------------------------
# 校验与检视：我们自己写出的产物
# ---------------------------------------------------------------------------
@needs_rc
def test_inspector_accepts_the_names_it_was_written_with(tmp_path, provider):
    """导出的 SVG 经产物检视（`inspector.observe_svg`）、`svg_vector` 都认中文 id；导入口仍严格。"""
    from tavotto.engine import artifactinspect
    from tavotto.rendercore import svgcheck

    path, _, _ = _svg(tmp_path, [_rect("a", name="细胞膜", group_path=["g"])], provider)
    assert artifactinspect.svg_vector(path) is True
    svgcheck.parse_svg(path.read_bytes(), unicode_ids=True)
    with pytest.raises(svgcheck.SVGError, match="svg_invalid_id"):
        svgcheck.parse_svg(path.read_bytes())  # 导入边界不放宽


def test_the_font_table_covers_every_approved_latin_family():
    assert set(svgfonts.REAL_FIRST_FAMILY) == {
        "Liberation Serif",
        "Liberation Sans",
        "Liberation Mono",
    }
    assert re.fullmatch(r"[A-Za-z ]+", " ".join(svgfonts.REAL_FIRST_FAMILY.values()))
