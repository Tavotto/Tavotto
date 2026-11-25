"""导出自检看**最终产物**里投稿会出事的三样（backlog T-004）：Type 3 字体、缺字方块、有脚本的图被整张位图嵌入。

主语是**交出去的那份 PDF 的字节**（不是 manifest 里 Tavotto 重画的那份）：

* Type 3 字体 → 「留意」（`advisories` 里的 `type3_fonts`）：IEEE、Elsevier 等会退稿，但文件本身能用，不算失败；
* 画成 `.notdef` 或用了 LastResort 的字 → `glyphs` 检查 failed（「要改」：图上是方块，是错图）；
* 有脚本的图按磁盘上的位图原件整张嵌进 PDF → 「留意」（`scripted_raster`）：用脚本重画就是矢量。

每一条都有反证：同一张图换成 Type 42 / 字都在 / 没有脚本，就不报。
"""

from __future__ import annotations

import importlib.util
import re
import struct
import warnings
import zlib
from pathlib import Path

import pytest

from tavotto.rendercore import inspector as I

needs_pikepdf = pytest.mark.skipif(
    importlib.util.find_spec("pikepdf") is None, reason="pikepdf 未装（not_run，不是绿）"
)
needs_mpl = pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None, reason="matplotlib 未装（not_run，不是绿）"
)

PLAN = {"page_pt": [144.0, 72.0], "vector": True}


def _mpl_pdf(path: Path, text: str, *, fonttype: int) -> Path:
    """用户机器上 matplotlib 直出的 PDF（`pdf.fonttype` 3 是 matplotlib 的默认）。"""
    import matplotlib

    matplotlib.use("pdf", force=True)
    import matplotlib.pyplot as plt

    with matplotlib.rc_context({"pdf.fonttype": fonttype}), warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 缺字时 matplotlib 会警告，这正是要造的情形
        fig = plt.figure(figsize=(2, 1))
        fig.text(0.1, 0.5, text, family="DejaVu Sans")
        fig.savefig(path)
        plt.close(fig)
    return path


@needs_pikepdf
@needs_mpl
def test_type3_fonts_are_an_advisory_and_type42_of_the_same_figure_is_not(tmp_path):
    m3 = I.inspect(_mpl_pdf(tmp_path / "t3.pdf", "AB", fonttype=3), "pdf", plan=PLAN)
    (adv,) = m3["advisories"]
    assert adv["code"] == I.ADVISORY_TYPE3_FONTS and adv["fonts"] == ["DejaVuSans"]
    # 留意不是失败：standard 照常交付，glyphs 量得到（Type 3 的 Differences 名字都在 CharProcs 里）
    assert m3["policy"]["verdict"] == "accepted" and m3["checks"]["glyphs"] == "verified"
    assert I.summary(m3)["advisories"] == m3["advisories"]
    # 反证：同一张图 Type 42 → 没有这条留意
    m42 = I.inspect(_mpl_pdf(tmp_path / "t42.pdf", "AB", fonttype=42), "pdf", plan=PLAN)
    assert m42["advisories"] == [] and m42["checks"]["glyphs"] == "verified"


@needs_pikepdf
@needs_mpl
@pytest.mark.parametrize("fonttype", [3, 42])
def test_a_figure_drawn_without_a_cjk_font_fails_the_glyph_check(tmp_path, fonttype):
    """作者机器上没有中文字体：matplotlib 退到 LastResort（每个字一个方块）——成品是错图。"""
    bad = I.inspect(_mpl_pdf(tmp_path / "cjk.pdf", "A浓", fonttype=fonttype), "pdf", plan=PLAN)
    assert bad["checks"]["glyphs"] == "failed", bad["observed"]["fonts_used"]
    assert any("缺字" in n for n in bad["notes"])
    # matplotlib ≥ 3.11 退到 LastResort；更早的版本画成 DejaVu 的 `.notdef`（Type 3 是一个字形 Form XObject）
    assert any(f["last_resort"] or f["notdef"] for f in bad["observed"]["fonts_used"])
    # standard 下照样交付（文件能打开），但失败要说出来：可选项失败
    assert "glyphs" in bad["policy"]["optional_failed"]
    # 反证：字都在 → glyphs verified
    ok = I.inspect(_mpl_pdf(tmp_path / "ok.pdf", "AB", fonttype=fonttype), "pdf", plan=PLAN)
    assert ok["checks"]["glyphs"] == "verified"


def _type0_pdf(path: Path, codes: bytes, *, c2g=None, mode: int = 0) -> Path:
    """最小的 Type0 / CIDFontType2 / Identity-H：字符码就是 CID，`c2g` 给映射流（None = Identity）。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(144, 72))
    kid = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name("/ABCDEF+Probe"),
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name("/ABCDEF+Probe"),
            FontFile2=pikepdf.Stream(pdf, b"\x00\x01\x00\x00"),
        ),
        CIDToGIDMap=pikepdf.Stream(pdf, c2g) if c2g is not None else pikepdf.Name.Identity,
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name("/ABCDEF+Probe"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=[pdf.make_indirect(kid)],
        )
    )
    page.obj["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    stream = b"BT /F1 10 Tf %d Tr 5 5 Td <%s> Tj ET" % (mode, codes.hex().encode())
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, stream))
    pdf.save(str(path))
    return path


@needs_pikepdf
def test_notdef_is_read_through_the_cid_to_gid_map(tmp_path):
    # Identity：CID 0 = GID 0 = .notdef
    assert (
        I.inspect(_type0_pdf(tmp_path / "a.pdf", b"\x00\x05\x00\x00"), "pdf", plan=PLAN)["checks"][
            "glyphs"
        ]
        == "failed"
    )
    assert (
        I.inspect(_type0_pdf(tmp_path / "b.pdf", b"\x00\x05\x00\x06"), "pdf", plan=PLAN)["checks"][
            "glyphs"
        ]
        == "verified"
    )
    # 映射流（matplotlib Type 42 的写法）：CID 2 → GID 0 是缺字，CID 1 → GID 7 不是；越出映射表也是缺字
    table = struct.pack(">HHH", 0, 7, 0)
    for codes, want in (
        (b"\x00\x01", "verified"),
        (b"\x00\x02", "failed"),
        (b"\x00\x09", "failed"),
    ):
        got = I.inspect(_type0_pdf(tmp_path / "c.pdf", codes, c2g=table), "pdf", plan=PLAN)
        assert got["checks"]["glyphs"] == want, codes
    # 只进文字层、不上墨（Tr 3）的 .notdef 不是图上的方块
    hidden = I.inspect(_type0_pdf(tmp_path / "d.pdf", b"\x00\x00", mode=3), "pdf", plan=PLAN)
    assert hidden["checks"]["glyphs"] == "verified"


def _two_font_pdf(path: Path, stream: bytes, *, f1, f2) -> Path:
    """两个 Type0 / Identity-H 字体的最小 PDF：`f1` / `f2` = (BaseFont, CIDToGIDMap 流)。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(144, 72))

    def font(base: str, c2g: bytes):
        kid = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            BaseFont=pikepdf.Name("/" + base),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            FontDescriptor=pikepdf.Dictionary(
                Type=pikepdf.Name.FontDescriptor,
                FontName=pikepdf.Name("/" + base),
                FontFile2=pikepdf.Stream(pdf, b"\x00\x01\x00\x00"),
            ),
            CIDToGIDMap=pikepdf.Stream(pdf, c2g),
        )
        return pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type0,
                BaseFont=pikepdf.Name("/" + base),
                Encoding=pikepdf.Name("/Identity-H"),
                DescendantFonts=[pdf.make_indirect(kid)],
            )
        )

    page.obj["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font(*f1), F2=font(*f2)))
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, stream))
    pdf.save(str(path))
    return path


@needs_pikepdf
def test_after_q_the_glyph_check_uses_the_restored_font(tmp_path):
    """`/F1 Tf q /F2 Tf (B) Tj Q (A) Tj`：A 是 F1 画的（字体属于图形状态，Q 还原）。
    F1 认得码 1、不认得码 2；F2 反过来。两个字都画得出来，核验不能说缺字；反过来摆就是真缺字。"""
    only1 = struct.pack(">HHH", 0, 7, 0)  # CID 1 → GID 7，CID 2 → .notdef
    only2 = struct.pack(">HHH", 0, 0, 7)  # CID 2 → GID 7，CID 1 → .notdef
    ok = b"BT /F1 10 Tf q /F2 10 Tf 5 5 Td <0002> Tj Q <0001> Tj ET"
    bad = b"BT /F1 10 Tf q /F2 10 Tf 5 5 Td <0002> Tj Q <0002> Tj ET"
    fonts = {"f1": ("AAAAAA+One", only1), "f2": ("BBBBBB+Two", only2)}
    good = I.inspect(_two_font_pdf(tmp_path / "ok.pdf", ok, **fonts), "pdf", plan=PLAN)
    assert good["checks"]["glyphs"] == "verified", good["notes"]
    wrong = I.inspect(_two_font_pdf(tmp_path / "bad.pdf", bad, **fonts), "pdf", plan=PLAN)
    assert wrong["checks"]["glyphs"] == "failed"


@needs_pikepdf
def test_last_resort_only_counts_when_it_paints(tmp_path):
    """LastResort 只被选中、或只用 Tr 3 写进文字层：图上没有方块，不报缺字；上墨画了一个字才报。"""
    ident = struct.pack(">HHH", 0, 7, 7)
    fonts = {"f1": ("AAAAAA+Plain", ident), "f2": ("CCCCCC+LastResort", ident)}
    hidden = b"BT /F1 10 Tf 5 5 Td <0001> Tj /F2 10 Tf 3 Tr <0001> Tj ET"
    selected = b"BT /F1 10 Tf 5 5 Td <0001> Tj /F2 10 Tf ET"
    painted = b"BT /F1 10 Tf 5 5 Td <0001> Tj /F2 10 Tf <0001> Tj ET"
    for name, stream, want in (
        ("hidden", hidden, "verified"),
        ("selected", selected, "verified"),
        ("painted", painted, "failed"),
    ):
        got = I.inspect(_two_font_pdf(tmp_path / f"{name}.pdf", stream, **fonts), "pdf", plan=PLAN)
        assert got["checks"]["glyphs"] == want, (name, got["notes"])


@needs_pikepdf
def test_a_simple_font_whose_glyphs_cannot_be_checked_is_not_counted_as_unknown(tmp_path):
    """简单 TrueType 没有 Differences：码 → 字形要读字体程序的 cmap，这里判不了。2026-11-25 用户选了
    不为它记 unknown（不再让文件进「未核验」名单）：只有这种字体时缺字一项「不适用」，附一句说明；
    和能判的字体放在一起时只看能判的那个。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(144, 72))
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.TrueType,
            BaseFont=pikepdf.Name("/Plain"),
            FontDescriptor=pikepdf.Dictionary(FontFile2=pikepdf.Stream(pdf, b"x")),
        )
    )
    page.obj["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"BT /F1 10 Tf (hi) Tj ET"))
    pdf.save(str(tmp_path / "plain.pdf"))
    m = I.inspect(tmp_path / "plain.pdf", "pdf", plan=PLAN)
    assert m["checks"]["glyphs"] == "not_applicable"
    assert any("判不了缺字" in n for n in m["notes"]), m["notes"]

    # 和一个能判的 Type0 放在一起：只看 Type0——它画的是缺字方块就报 failed，不是就 verified
    for codes, want in ((b"\x00\x05", "verified"), (b"\x00\x00", "failed")):
        mixed = _type0_pdf(tmp_path / f"mixed-{want}.pdf", codes)
        with pikepdf.open(mixed, allow_overwriting_input=True) as doc:
            pg = doc.pages[0]
            plain = doc.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.TrueType,
                    BaseFont=pikepdf.Name("/Plain"),
                    FontDescriptor=pikepdf.Dictionary(FontFile2=pikepdf.Stream(doc, b"x")),
                )
            )
            pg.obj["/Resources"]["/Font"]["/F2"] = plain
            body = bytes(pg.obj["/Contents"].read_bytes()) + b" BT /F2 10 Tf 5 30 Td (hi) Tj ET"
            pg.obj["/Contents"] = doc.make_indirect(pikepdf.Stream(doc, body))
            doc.save(mixed)
        got = I.inspect(mixed, "pdf", plan=PLAN)
        assert got["checks"]["glyphs"] == want, got["notes"]


def _png(path: Path, w: int = 40, h: int = 30) -> Path:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    raw = b"".join(b"\x00" + bytes([200, 30, 30]) * w for _ in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


@pytest.fixture
def canvas_env(tmp_path, monkeypatch):
    from tavotto import app as m
    from tavotto.engine import exportjob

    figs = tmp_path / "figs"
    figs.mkdir()
    _png(figs / "plot.png")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    yield m, m.app.test_client()
    m.reset_projects()
    exportjob.reset_for_tests()


def _canvas_export(client, *, scope: str = "canvas") -> dict:
    panel = {"type": "panel", "id": "plot.png", "x_mm": 5, "y_mm": 5, "w_mm": 40, "h_mm": 30}
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {"page_w_mm": 60, "page_h_mm": 45, "objects": [panel]},
    }
    if scope == "original":
        spec = {
            "scope": "original",
            "filename": "Fig 1",
            "formats": ["pdf", "png"],
            "ppi": 150,
            "overwrite": "replace",
            "original": {"figure_id": "plot.png", "w_mm": 40, "h_mm": 30},
        }
    r = client.post("/api/export", json=spec)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _advisories(body: dict, fmt: str) -> list[dict]:
    out = next(o for o in body["outputs"] if o["format"] == fmt)
    return out["manifest"]["advisories"]


@needs_pikepdf
@pytest.mark.parametrize("scope", ["canvas", "original"])
def test_a_scripted_figure_embedded_as_a_whole_bitmap_is_an_advisory(
    canvas_env, monkeypatch, scope
):
    m, client = canvas_env
    monkeypatch.setattr(m, "_has_runnable_script", lambda rel_id: rel_id == "plot.png")
    body = _canvas_export(client, scope=scope)
    (adv,) = _advisories(body, "pdf")
    assert adv == {"code": I.ADVISORY_SCRIPTED_RASTER, "panels": ["plot.png"], "count": 1}
    # PNG 本来就是位图：不说「可以重画成矢量」
    assert _advisories(body, "png") == []
    # 反证：同一张 PNG 没有脚本（重画不了）→ 不报
    monkeypatch.setattr(m, "_has_runnable_script", lambda rel_id: False)
    assert _advisories(_canvas_export(client, scope=scope), "pdf") == []


def test_advisory_codes_are_a_closed_set_mirrored_by_the_frontend():
    """前端按码出文案（`web/src/lib/artifactInspection.ts` 的 `ADVISORY_CODES`）：两边同一个闭集。"""
    ts = (
        Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "artifactInspection.ts"
    ).read_text(encoding="utf-8")
    block = re.search(r"ADVISORY_CODES = \[(.*?)\] as const", ts, re.S)
    assert block, "artifactInspection.ts 里找不到 ADVISORY_CODES"
    assert re.findall(r"'([a-z0-9_]+)'", block.group(1)) == list(I.ADVISORIES)
