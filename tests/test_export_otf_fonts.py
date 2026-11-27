"""CFF 轮廓的 OpenType（`.otf`）字体在导出 PDF 里的结构（摸底 T-003）。

matplotlib 的 Type 42 嵌入（导出强制 `pdf.fonttype = 42`）对 `.otf` 也写 `CIDFontType2` + `FontFile2`，塞进去的却是
`OTTO` 开头的 CFF 程序：poppler 报「Mismatch between font type and embedded font file」，投稿系统的转换可能拒收
或掉字，而导出自检照样记 `fonts_embedded: verified`。两半各一把尺子：

* 检查器：TrueType 槽里装着非 TrueType 程序 → `fonts_embedded: failed`（先在 worker 原样产物上验证它会响）；
* 导出：`/api/export` 交出去的 PDF 里不再有错配（CFF 转成同字形序号的 TrueType），文字层照读回，检查器给 verified。

字体是测试现做的（fontTools FontBuilder 出一张 CFF 小字体），不依赖机器上装没装 `.otf`。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pikepdf
import pytest

from tavotto import app as m
from tavotto.engine import artifactinspect, exportjob, figcapture, pool, project_watch
from tavotto.rendercore import inspector, pdfwriter

FAMILY = "Tvt Cff Probe"
TEXT = "ABBA"

SCRIPT = f"""import matplotlib.pyplot as plt
from matplotlib import font_manager
from pathlib import Path

font_manager.fontManager.addfont(str(Path(__file__).with_name("probe.otf")))


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [0, 1])
    ax.set_title({TEXT!r}, fontfamily={FAMILY!r})
    fig.savefig("F.pdf")
"""


def _cff_font_bytes() -> bytes:
    """一张只有 A / B 的 CFF OpenType：两个字形画成不同的矩形，宽度不同（宽度表错位会被量到）。"""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    def box(w: int, h: int):
        pen = T2CharStringPen(w, None)
        pen.moveTo((50, 0))
        pen.lineTo((50, h))
        pen.curveTo((w / 2, h + 80), (w / 2, h + 80), (w - 50, h))
        pen.lineTo((w - 50, 0))
        pen.closePath()
        return pen.getCharString()

    order = [".notdef", "space", "A", "B"]
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x20: "space", 0x41: "A", 0x42: "B"})
    fb.setupCFF(
        "TvtCffProbe-Regular",
        {"FullName": FAMILY},
        {
            ".notdef": box(500, 600),
            "space": T2CharStringPen(250, None).getCharString(),
            "A": box(600, 700),
            "B": box(720, 500),
        },
        {},
    )
    fb.setupHorizontalMetrics(
        {".notdef": (500, 50), "space": (250, 0), "A": (600, 50), "B": (720, 50)}
    )
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": FAMILY, "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    data = out.getvalue()
    assert data.startswith(b"OTTO")
    return data


def _font_programs(pdf_path: Path) -> list[bytes]:
    with pikepdf.open(str(pdf_path)) as pdf:
        return [
            bytes(o["/FontFile2"].read_bytes()[:4])
            for o in pdf.objects
            if isinstance(o, pikepdf.Dictionary)
            and o.get("/Type") == "/FontDescriptor"
            and "/FontFile2" in o
        ]


def test_cff_program_becomes_truetype_with_the_same_glyph_order_and_advances():
    from fontTools.ttLib import TTFont

    src = _cff_font_bytes()
    out = pdfwriter.cff_program_as_truetype(src)
    assert out is not None and out[:4] == b"\x00\x01\x00\x00"
    a, b = TTFont(io.BytesIO(src)), TTFont(io.BytesIO(out))
    assert b.getGlyphOrder() == a.getGlyphOrder()
    assert "glyf" in b and "CFF " not in b
    assert [b["hmtx"][g][0] for g in b.getGlyphOrder()] == [
        a["hmtx"][g][0] for g in a.getGlyphOrder()
    ]
    # 转完的轮廓还是原来那块：外接框与 CFF 一致（cu2qu 误差 ≤ 1 个字体单位）
    gs_a, gs_b = a.getGlyphSet(), b.getGlyphSet()
    from fontTools.pens.boundsPen import BoundsPen

    for g in ("A", "B"):
        pa, pb = BoundsPen(gs_a), BoundsPen(gs_b)
        gs_a[g].draw(pa)
        gs_b[g].draw(pb)
        assert all(abs(x - y) <= 1.0 for x, y in zip(pa.bounds, pb.bounds)), (
            g,
            pa.bounds,
            pb.bounds,
        )
    # 不是 CFF 程序就不碰
    assert pdfwriter.cff_program_as_truetype(out) is None


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    m.app.config["TESTING"] = True
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    root = tmp_path / "实验 图"
    root.mkdir()
    (root / "probe.otf").write_bytes(_cff_font_bytes())
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "plot.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["F"]}
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield m.app.test_client(), root
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    project_watch.stop()
    exportjob.reset_for_tests()
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()


def _export_edited(client) -> tuple[Path, dict]:
    probe = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert probe.status_code == 200 and probe.get_json().get("registered"), probe.get_json()
    resp = client.post(
        "/api/export",
        json={
            "scope": "original",
            "filename": "otf",
            "formats": ["pdf"],
            "overwrite": "replace",
            "original": {
                "figure_id": figcapture.runtime_asset_id("plot.py", "F"),
                # 改一处 = 由脚本重画（worker 写 PDF 的那条路）
                "overrides": [{"gid": "axes_0.title", "prop": "text", "value": TEXT + " BA"}],
                "source_kind": "vector",
            },
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["status"] == "done", body
    (out,) = [o for o in body["outputs"] if o["format"] == "pdf"]
    return Path(body["export_dir"]) / out["name"], out["manifest"]


def test_worker_pdf_with_an_otf_font_is_flagged_by_the_inspector(project, tmp_path):
    """尺子先验活：worker 原样写出的 PDF（不经导出边界）里，检查器必须报 `fonts_embedded: failed`。"""
    _, root = project
    w = pool.one_shot("plot.py", str(root), "main")
    try:
        w.ensure_built()
        raw = tmp_path / "raw.pdf"
        w.export("F", [], str(raw), "pdf")
    finally:
        pool.discard(w)
    assert b"OTTO" in _font_programs(raw), (
        "matplotlib 不再把 CFF 写进 FontFile2 了：这条用例要换形状"
    )
    man = inspector.inspect(raw, "pdf", plan={})
    assert man["checks"]["fonts_embedded"] == inspector.FAILED, man["checks"]
    assert any(f.get("program_mismatch") for f in man["observed"]["fonts_used"])
    # 修过的同一份：错配没了、检查器转绿、文字层照读回
    # 至少修了探针字体那一张（机器上 sans-serif 解析到的若也是 .otf，会一起修）
    assert artifactinspect.normalize_worker_pdf(raw) >= 1
    assert b"OTTO" not in _font_programs(raw)
    fixed = inspector.inspect(raw, "pdf", plan={})
    assert fixed["checks"]["fonts_embedded"] == inspector.VERIFIED, fixed
    assert TEXT in " ".join(fixed["observed"]["text_lines"])
    # 幂等：没东西可修时一个字节不动
    before = raw.read_bytes()
    assert artifactinspect.normalize_worker_pdf(raw) == 0
    assert raw.read_bytes() == before


def test_exported_pdf_with_an_otf_font_has_a_truetype_program_and_reads_back(project):
    client, _ = project
    pdf, manifest = _export_edited(client)
    heads = _font_programs(pdf)
    assert heads and b"OTTO" not in heads, heads
    assert manifest["checks"]["fonts_embedded"] == inspector.VERIFIED, manifest
    with pikepdf.open(str(pdf)) as doc:
        assert len(doc.pages) == 1
    text = " ".join(inspector.observe_pdf(pdf)["text_lines"])
    assert TEXT + " BA" in text, text


def test_without_the_export_boundary_fix_the_export_is_reported_failed(project, monkeypatch):
    """反证：拿掉导出边界的修复，同一次导出的 manifest 必须说 `failed`（不是 verified）。"""
    client, _ = project
    monkeypatch.setattr(artifactinspect, "normalize_worker_pdf", lambda path: 0)
    pdf, manifest = _export_edited(client)
    assert b"OTTO" in _font_programs(pdf)
    assert manifest["checks"]["fonts_embedded"] == inspector.FAILED, manifest


def _write_back_edited(client, root: Path) -> Path:
    """写回（「更新原图」）：一次性 worker 现画 staging，替换用户图库里的 `F.pdf`。"""
    original = pikepdf.new()
    original.add_blank_page(page_size=(200, 100))
    original.save(root / "F.pdf")
    probe = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert probe.status_code == 200 and probe.get_json().get("registered"), probe.get_json()

    def render(patches):
        r = client.post("/api/engine/render", json={"id": "F.pdf", "patches": patches})
        assert r.status_code == 200, r.get_json()
        return r.get_json()["manifest"]

    gid = next(
        el["gid"]
        for el in render([])["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f.get("value") == TEXT
    )
    patches = [{"gid": gid, "prop": "text", "value": TEXT + " BA"}]
    render(patches)
    r = client.post(
        "/api/engine/update_source",
        json={
            "id": "F.pdf",
            "patches": patches,
            "expected_mtime": int((root / "F.pdf").stat().st_mtime),
        },
    )
    assert r.status_code == 200, r.get_json()
    assert "F.pdf" in str(r.get_json().get("updated")), r.get_json()
    return root / "F.pdf"


def test_write_back_with_an_otf_font_puts_a_truetype_program_into_the_original(project):
    """写回刻进用户原件的 PDF 与导出同一道边界：没有 CFF 塞在 TrueType 槽里，检查器 verified、文字层读得回。"""
    client, root = project
    pdf = _write_back_edited(client, root)
    heads = _font_programs(pdf)
    assert heads and b"OTTO" not in heads, heads
    man = inspector.inspect(pdf, "pdf", plan={})
    assert man["checks"]["fonts_embedded"] == inspector.VERIFIED, man["checks"]
    assert TEXT + " BA" in " ".join(man["observed"]["text_lines"])


def test_without_the_boundary_fix_the_written_back_original_carries_cff(project, monkeypatch):
    """反证：拿掉边界修复，同一次写回落进原件的就是 `OTTO`（尺子量得到这条路）。"""
    client, root = project
    monkeypatch.setattr(artifactinspect, "normalize_worker_pdf", lambda path: 0)
    pdf = _write_back_edited(client, root)
    assert b"OTTO" in _font_programs(pdf)
    man = inspector.inspect(pdf, "pdf", plan={})
    assert man["checks"]["fonts_embedded"] == inspector.FAILED, man["checks"]


def test_repair_leaves_a_file_without_a_pdf_header_byte_for_byte(tmp_path):
    """不是 PDF 的文件（假 worker 的半成品、截断的输出）不碰：一个字节不动，交给检查器报。"""
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"NEW-pdf")
    assert pdfwriter.repair_cff_in_truetype_slots(junk) == 0
    assert junk.read_bytes() == b"NEW-pdf"
