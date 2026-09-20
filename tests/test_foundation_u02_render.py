"""U02 render_spike 的 evidence 用**纯标准库**再读一遍（`docs/implementation/tavotto-foundation/evidence/u02/render/`）。

写入侧是 pikepdf + fontTools + HarfBuzz，spike 脚本里的三把尺子是 pypdfium2 / pdfminer.six /
pypdf——它们都不在主仓库 `.venv` 里。这里是第四把尺子，也是唯一一把每次 CI 都会量的：
用 `re` / `zlib` / `struct` 直接读 `spike.pdf` 与 `spike_pdfium.png`，与手写的 `truth.json` 对。

判据的主语：**进了 git 的那份 evidence 文件此刻的字节**（不是写入器的返回值）。几何期望
（导入页的 `cm` 矩阵）由本文件按 truth 里的 rect / rotation / crop **自己重算**，不用
`pdfwrite.py` 的矩阵函数（RC-095：写入 / 读取不同源）。ToUnicode 的解码也是自己写的。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u02" / "render"
TRUTH = json.loads((EVIDENCE / "truth.json").read_text(encoding="utf-8"))
PDF = (EVIDENCE / "spike.pdf").read_bytes()
PNG = (EVIDENCE / "spike_pdfium.png").read_bytes()
REPORT = json.loads((EVIDENCE / "report.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 极简 PDF 读取（只认 qpdf 写出的这一种形状：无对象流、/Length 直接量）
# ---------------------------------------------------------------------------
_OBJ = re.compile(rb"(?m)^(\d+) 0 obj\s*(.*?)\s*^endobj", re.S)
_NUM = r"-?\d+(?:\.\d+)?"


def _objects() -> dict[int, tuple[bytes, bytes | None]]:
    """{obj 号: (字典文本, 解码后的流 或 None)}。"""
    out: dict[int, tuple[bytes, bytes | None]] = {}
    for m in _OBJ.finditer(PDF):
        num, body = int(m.group(1)), m.group(2)
        if b"stream" in body:
            head, _, rest = body.partition(b"stream")
            length = int(re.search(rb"/Length (\d+)", head).group(1))
            data = rest.lstrip(b"\r\n")[:length]
            if b"/FlateDecode" in head and data:
                data = zlib.decompress(data)
            out[num] = (head, data)
        else:
            out[num] = (body, None)
    assert out, "一个 obj 都没切出来——PDF 形状变了？"
    return out


OBJS = _objects()


def _ref(head: bytes, key: bytes) -> int:
    m = re.search(rb"/" + key + rb"\s+(\d+) 0 R", head)
    assert m, f"{key!r} 不在 {head[:200]!r}"
    return int(m.group(1))


def _nums(s: bytes) -> list[float]:
    return [float(x) for x in re.findall(_NUM.encode(), s)]


def _page() -> tuple[bytes, bytes]:
    for head, data in OBJS.values():
        if b"/Type /Page" in head and b"/Pages" not in head:
            return head, OBJS[_ref(head, b"Contents")][1]
    raise AssertionError("没有页面对象")


def _resources(head: bytes) -> dict[str, dict[str, int]]:
    """/Resources << /XObject << /Fm1 N 0 R … >> /ExtGState … /Font … >> → 各类名字 → obj 号。
    qpdf 会把页面资源写成直接字典；forms 的 /Resources 也是直接字典。"""
    res: dict[str, dict[str, int]] = {}
    for cat in ("XObject", "ExtGState", "Font"):
        m = re.search(rb"/" + cat.encode() + rb"\s*<<(.*?)>>", head, re.S)
        res[cat] = (
            {k.decode(): int(v) for k, v in re.findall(rb"/(\w+)\s+(\d+) 0 R", m.group(1))}
            if m
            else {}
        )
    return res


# ---------------------------------------------------------------------------
# 几何：本文件自己的矩阵（不与 pdfwrite 同源）
# ---------------------------------------------------------------------------
def _expected_matrix(imp: dict) -> tuple[float, ...]:
    """源可见框（或 crop 后的子框）→ 目标 rect，再绕目标中心顺时针转 rotate_cw_deg。
    直接按「点怎么走」写公式：p → (p - src0) * s + tgt0 → 绕中心旋转。"""
    bx0, by0, bx1, by1 = TRUTH["source_visible_bbox"]
    bw, bh = bx1 - bx0, by1 - by0
    if imp.get("crop"):
        cx, cy, cw, ch = imp["crop"]
        sx0, sw = bx0 + cx * bw, cw * bw
        sy1 = by1 - cy * bh
        sh = ch * bh
        sy0 = sy1 - sh
    else:
        sx0, sy0, sw, sh = bx0, by0, bw, bh
    tx, ty, tw, th = imp["rect"]
    kx, ky = tw / sw, th / sh
    th_rad = -math.radians(imp.get("rotate_cw_deg", 0))  # PDF 空间 y 向上：顺时针 = 负角
    c, s = math.cos(th_rad), math.sin(th_rad)
    cx_, cy_ = tx + tw / 2, ty + th / 2
    # 先缩放平移：x' = kx*(x - sx0) + tx；再绕 (cx_, cy_) 旋转
    # 组合成 [a b c d e f]：x'' = a*x + c*y + e, y'' = b*x + d*y + f
    a, b = kx * c, kx * s
    cc, d = -ky * s, ky * c
    ex = tx - kx * sx0
    fy = ty - ky * sy0
    e = c * (ex - cx_) - s * (fy - cy_) + cx_
    f = s * (ex - cx_) + c * (fy - cy_) + cy_
    return (a, b, cc, d, e, f)


def _apply(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


# ---------------------------------------------------------------------------
# PNG 解码（标准库）
# ---------------------------------------------------------------------------
def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    h = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert (depth, ctype) == (8, 6), (depth, ctype)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (
                    line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))
                ) & 0xFF
        out += line
        prev = line
    return w, h, bytes(out)


PNG_W, PNG_H, PNG_RGBA = _decode_png(PNG)


def _pixel(x_pt: float, y_pt: float) -> tuple[int, int, int]:
    scale = TRUTH["raster"]["scale"]
    px, py = int(x_pt * scale), int((TRUTH["page_pt"][1] - y_pt) * scale)
    i = (py * PNG_W + px) * 4
    return PNG_RGBA[i], PNG_RGBA[i + 1], PNG_RGBA[i + 2]


def _close(a, b, tol=None) -> bool:
    tol = TRUTH["rgb_tolerance"] if tol is None else tol
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_evidence_files_match_the_report_hashes():
    """report.json 说的就是磁盘上这两个文件；三者要一起重生成。"""
    assert REPORT["all_ok"] is True
    assert REPORT["outputs"]["spike.pdf"]["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert REPORT["outputs"]["spike_pdfium.png"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert (
        REPORT["inputs"]["truth_sha256"]
        == hashlib.sha256((EVIDENCE / "truth.json").read_bytes()).hexdigest()
    )
    assert (PNG_W, PNG_H) == (
        int(TRUTH["page_pt"][0] * TRUTH["raster"]["scale"]),
        int(TRUTH["page_pt"][1] * TRUTH["raster"]["scale"]),
    )


def test_page_size_and_source_page_are_the_asymmetric_fixture():
    head, _ = _page()
    assert _nums(re.search(rb"/MediaBox \[(.*?)\]", head).group(1)) == [0, 0, *TRUTH["page_pt"]]
    src = ROOT / TRUTH["source_page"]
    assert hashlib.sha256(src.read_bytes()).hexdigest() == REPORT["inputs"]["source_page_sha256"]
    truth = json.loads((src.parent / "truth.json").read_text(encoding="utf-8"))
    assert truth["pdf"]["crop_box"] == TRUTH["source_visible_bbox"]
    assert truth["pdf"]["media_box"] == TRUTH["source_media_box"]


def _imported_forms() -> dict[int, bytes]:
    """BBox == 源页 CropBox 的 Form XObject（导入页），obj 号 → 字典。"""
    want = TRUTH["source_visible_bbox"]
    found = {}
    for num, (head, data) in OBJS.items():
        if b"/Subtype /Form" in head and data is not None:
            m = re.search(rb"/BBox \[(.*?)\]", head)
            if m and _nums(m.group(1)) == want:
                found[num] = head
    return found


def _placements() -> list[dict]:
    """页面上每处导入：{matrix, clip, form_obj, opacity, via_group}。opacity<1 的那一处在包装组里。"""
    page_head, content = _page()
    res = _resources(page_head)
    imported = _imported_forms()
    pat = re.compile(
        rb"q ("
        + _NUM.encode()
        + rb"(?: "
        + _NUM.encode()
        + rb"){5}) cm ("
        + _NUM.encode()
        + rb"(?: "
        + _NUM.encode()
        + rb"){3}) re W n /(\w+) Do Q"
    )
    out = []

    def scan(text: bytes, xobjs: dict[str, int], opacity: float, via_group: str | None) -> None:
        for m in pat.finditer(text):
            obj = xobjs[m.group(3).decode()]
            if obj in imported:
                out.append(
                    {
                        "matrix": tuple(_nums(m.group(1))),
                        "clip": _nums(m.group(2)),
                        "form_obj": obj,
                        "opacity": opacity,
                        "via_group": via_group,
                    }
                )

    scan(content, res["XObject"], 1.0, None)
    for m in re.finditer(rb"q /(\w+) gs /(\w+) Do Q", content):
        gs, name = m.group(1).decode(), m.group(2).decode()
        gs_head = OBJS[res["ExtGState"][gs]][0]
        ca = float(re.search(rb"/ca (" + _NUM.encode() + rb")", gs_head).group(1))
        head, data = OBJS[res["XObject"][name]]
        assert re.search(rb"/Group <<[^>]*?/S /Transparency", head, re.S), (
            f"{name} 不是透明组：{head!r}"
        )
        scan(data, _resources(head)["XObject"], ca, name)
    return out


@pytest.mark.parametrize("imp", TRUTH["imports"], ids=lambda i: i["id"])
def test_each_import_has_the_matrix_and_clip_the_truth_predicts(imp):
    """导入页的 `cm` 矩阵 == 本文件按 rect / rotation / crop 重算的；clip 矩形 == 源框（或 crop 子框）；
    opacity<1 的那处经透明组包装，ExtGState 的 /ca 等于 opacity。"""
    want = _expected_matrix(imp)
    hits = [p for p in _placements() if all(abs(a - b) < 1e-3 for a, b in zip(p["matrix"], want))]
    assert len(hits) == 1, (
        f"{imp['id']}: 期望矩阵 {[round(v, 4) for v in want]} 在页面上出现 {len(hits)} 次"
    )
    p = hits[0]
    bx0, by0, bx1, by1 = TRUTH["source_visible_bbox"]
    if imp.get("crop"):
        cx, cy, cw, ch = imp["crop"]
        bw, bh = bx1 - bx0, by1 - by0
        clip = [bx0 + cx * bw, by1 - cy * bh - ch * bh, cw * bw, ch * bh]
    else:
        clip = [bx0, by0, bx1 - bx0, by1 - by0]
    assert p["clip"] == clip
    assert abs(p["opacity"] - imp.get("opacity", 1.0)) < 1e-9
    assert (p["via_group"] is not None) == (imp.get("opacity", 1.0) < 1.0)


@pytest.mark.parametrize("imp", TRUTH["imports"], ids=lambda i: i["id"])
def test_pdfium_pixels_of_each_import_match_the_hand_computed_colors(imp):
    """源页坐标 → 本文件算的矩阵 → 页面坐标 → PDFium 栅格里的像素 == 手算颜色。"""
    m = _expected_matrix(imp)
    for smp in imp["samples"]:
        x, y = _apply(m, *smp["src"])
        got = _pixel(x, y)
        assert _close(got, smp["rgb"]), (
            f"{imp['id']} src={smp['src']} page=({x:.1f},{y:.1f}) 期望 {smp['rgb']} 实得 {got}：{smp.get('why')}"
        )


def test_transparency_group_composites_as_a_whole_and_differs_from_per_object_alpha():
    grp_d, grp_e = TRUTH["groups"]
    assert grp_d["kind"] == "transparency_group" and grp_e["kind"] == "per_object_alpha"
    page_head, content = _page()
    res = _resources(page_head)
    # D：一个带 /Group 的 form，内容是两块不带 alpha 的矩形；页面上以 /ca=opacity 的 gs 画它
    forms = []
    for m in re.finditer(rb"q /(\w+) gs /(\w+) Do Q", content):
        head, data = OBJS[res["XObject"][m.group(2).decode()]]
        if all(
            f"{r['rect'][0]} {r['rect'][1]} {r['rect'][2]} {r['rect'][3]} re f".encode() in data
            for r in grp_d["rects"]
        ):
            assert re.search(rb"/Group <<[^>]*?/S /Transparency", head, re.S)
            assert b" gs" not in data, "组内不该再有 alpha：整体 opacity 只在组外那一次"
            ca = float(
                re.search(
                    rb"/ca (" + _NUM.encode() + rb")",
                    OBJS[res["ExtGState"][m.group(1).decode()]][0],
                ).group(1)
            )
            assert ca == grp_d["opacity"]
            forms.append(head)
    assert len(forms) == 1, "透明组 D 应恰好一个"
    # E：页面级逐对象 alpha
    for r in grp_e["rects"]:
        assert re.search(
            rb"q /GSa\d+ gs [\d. ]+ rg "
            + f"{r['rect'][0]} {r['rect'][1]} {r['rect'][2]} {r['rect'][3]} re f".encode()
            + rb" Q",
            content,
        )
    for grp in (grp_d, grp_e):
        for smp in grp["samples"]:
            got = _pixel(*smp["page"])
            assert _close(got, smp["rgb"]), (
                f"{grp['id']} {smp['page']} 期望 {smp['rgb']} 实得 {got}：{smp.get('why')}"
            )
    assert not _close(_pixel(*grp_d["samples"][0]["page"]), _pixel(*grp_e["samples"][0]["page"])), (
        "D 与 E 的重叠区一样 = 透明组没起作用"
    )


# ---------------------------------------------------------------------------
# 字体与文字层
# ---------------------------------------------------------------------------
def _fonts() -> dict[str, dict]:
    page_head, _ = _page()
    res = _resources(page_head)
    out = {}
    for name, num in res["Font"].items():
        head, _ = OBJS[num]
        assert b"/Subtype /Type0" in head and b"/Encoding /Identity-H" in head, head
        base = re.search(rb"/BaseFont /(\w+)\+([\w-]+)", head)
        assert base and len(base.group(1)) == 6, head
        desc_head, _ = OBJS[int(re.search(rb"/DescendantFonts \[ (\d+) 0 R \]", head).group(1))]
        fd_head, _ = OBJS[_ref(desc_head, b"FontDescriptor")]
        tu_head, tu_data = OBJS[_ref(head, b"ToUnicode")]
        info = {"tag": base.group(1).decode(), "base": base.group(2).decode(), "tounicode": tu_data}
        if b"/Subtype /CIDFontType2" in desc_head:
            assert b"/CIDToGIDMap /Identity" in desc_head
            prog_head, prog = OBJS[_ref(fd_head, b"FontFile2")]
            assert prog[:4] == b"\x00\x01\x00\x00", "FontFile2 不是 sfnt（TrueType）"
            assert int(re.search(rb"/Length1 (\d+)", prog_head).group(1)) == len(prog)
            info["kind"] = "truetype"
        elif b"/Subtype /CIDFontType0" in desc_head:
            prog_head, prog = OBJS[_ref(fd_head, b"FontFile3")]
            assert b"/Subtype /CIDFontType0C" in prog_head
            assert prog[0] == 1, "CFF 头的 major 版本应为 1"
            info["kind"] = "cff-cid"
        else:
            raise AssertionError(desc_head)
        info["program_bytes"] = len(prog)
        info["w_entries"] = len(
            re.findall(rb"\d+ \[", re.search(rb"/W \[(.*?)\]\s*>>", desc_head, re.S).group(1))
        )
        out[name] = info
    return out


def _decode_tounicode(cmap: bytes) -> dict[int, str]:
    """只读 begin/endbfchar 与 begin/endbfrange 之间的行——codespacerange 那行 `<0000> <FFFF>`
    长得和 bfchar 一样，整段扫会把 code 0 解成 U+FFFF。"""
    out: dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]{4})> <([0-9A-Fa-f]+)>", block):
            out[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be")
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst in re.findall(
            rb"<([0-9A-Fa-f]{4})> <([0-9A-Fa-f]{4})> <([0-9A-Fa-f]+)>", block
        ):
            base = int(dst, 16)
            for i, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                out[code] = chr(base + i)
    return out


def _text_lines() -> list[str]:
    """按 BT…ET 段解码内容流里的文字（只认本 spike 写的 `/F size Tf` + `[…] TJ` 形状）。"""
    _, content = _page()
    fonts = _fonts()
    maps = {name: _decode_tounicode(info["tounicode"]) for name, info in fonts.items()}
    lines = []
    for block in re.findall(rb"BT(.*?)ET", content, re.S):
        cur, text = None, ""
        for tok in re.finditer(rb"/(F\d+) [\d.]+ Tf|\[(.*?)\] TJ", block, re.S):
            if tok.group(1):
                cur = tok.group(1).decode()
                continue
            for hexstr in re.findall(rb"<([0-9A-Fa-f]+)>", tok.group(2)):
                raw = bytes.fromhex(hexstr.decode())
                for i in range(0, len(raw), 2):
                    text += maps[cur].get(int.from_bytes(raw[i : i + 2], "big"), "�")
        lines.append(text)
    return lines


def test_fonts_are_real_subsets_with_tounicode_and_two_program_formats():
    fonts = _fonts()
    kinds = {f["kind"] for f in fonts.values()}
    assert kinds == {"truetype", "cff-cid"}, kinds
    bases = {f["base"] for f in fonts.values()}
    expected = {face for t in TRUTH["text"] for face in t["expect_faces"]}
    assert expected <= bases, (expected, bases)
    for name, f in fonts.items():
        assert 0 < f["program_bytes"] < 20_000, (
            f"{name} 的字体程序 {f['program_bytes']} 字节——不像子集"
        )
        assert f["w_entries"] >= 1
        assert _decode_tounicode(f["tounicode"]), f"{name} 的 ToUnicode 解出来是空的"
    # 报告里记的源字体 glyph 数远大于子集：子集是真子集，不是整字体
    for ff in REPORT["writer_facts"]["fonts"]:
        assert ff["glyphs_in_subset"] < ff["glyphs_in_source"] / 10, ff


@pytest.mark.parametrize("t", TRUTH["text"], ids=lambda t: t["id"])
def test_text_layer_decodes_back_to_the_input_through_tounicode(t):
    """内容流里的 code 经本文件解码的 ToUnicode 回到原文：连字 `fi` 回两个字、汉字回汉字、
    上下标回它们的数字（合成上下标的文本层是「E = mc2」——与旧后端一致，ActualText 归 U06）。"""
    lines = _text_lines()
    joined = "\n".join(lines)
    flat = "".join(ch for ch in joined if not ch.isspace())
    for exp in t.get("expect_extract", []):
        assert exp in joined, (exp, lines)
    for exp in t.get("expect_extract_nospace", []):
        assert "".join(ch for ch in exp if not ch.isspace()) in flat, (exp, lines)
    assert "�" not in joined, "有 code 在 ToUnicode 里查不到"


def test_a_composed_cluster_maps_one_glyph_back_to_two_codepoints():
    """`cafe\u0301`（e + 组合重音，分解形式）经 HarfBuzz 合成为**一个** eacute 字形：那个 2 字节 code
    的 ToUnicode 目标必须是两个码位（原文），不是 `é` 一个、更不是 glyph 名。这是 RC-036
    「cluster 与逻辑文字匹配」在 evidence 上的最小实例。Liberation 2.1.5 **没有 `liga`**（实测
    GSUB 只有 ccmp / dlig / subs / sups），`efficient` 里的 fi 不连字——所以 fi 不能当这条的主语，
    真值里也没把它当成主语。"""
    fonts = _fonts()
    multi = {
        name: [v for v in _decode_tounicode(f["tounicode"]).values() if len(v) > 1]
        for name, f in fonts.items()
    }
    assert any("e\u0301" in vs for vs in multi.values()), multi
    # 反面：fi 没连字——两个独立 code 各回一个字母；连字了会出现 "fi" 这个双码位目标
    assert not any("fi" in vs for vs in multi.values()), "Liberation 连了 fi？字体换了就更新这条"


def _sfnt_cmap(prog: bytes) -> dict[int, int]:
    """嵌入的 TrueType 子集自己的 cmap（format 4 / 12）→ {码位: GID}。纯标准库，不用 fontTools。"""
    num_tables = struct.unpack(">H", prog[4:6])[0]
    tables = {}
    for i in range(num_tables):
        off = 12 + 16 * i
        tag, _, toff, tlen = struct.unpack(">4sIII", prog[off : off + 16])
        tables[tag] = (toff, tlen)
    coff, _ = tables[b"cmap"]
    n = struct.unpack(">H", prog[coff + 2 : coff + 4])[0]
    out: dict[int, int] = {}
    for i in range(n):
        pid, eid, soff = struct.unpack(">HHI", prog[coff + 4 + 8 * i : coff + 12 + 8 * i])
        sub = coff + soff
        fmt = struct.unpack(">H", prog[sub : sub + 2])[0]
        if fmt == 4:
            segx2 = struct.unpack(">H", prog[sub + 6 : sub + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(f">{seg}H", prog[sub + 14 : sub + 14 + segx2])
            starts = struct.unpack(f">{seg}H", prog[sub + 16 + segx2 : sub + 16 + 2 * segx2])
            deltas = struct.unpack(f">{seg}h", prog[sub + 16 + 2 * segx2 : sub + 16 + 3 * segx2])
            ro_base = sub + 16 + 3 * segx2
            ros = struct.unpack(f">{seg}H", prog[ro_base : ro_base + segx2])
            for k in range(seg):
                if starts[k] == 0xFFFF:
                    continue
                for cp in range(starts[k], ends[k] + 1):
                    if ros[k] == 0:
                        gid = (cp + deltas[k]) & 0xFFFF
                    else:
                        gaddr = ro_base + 2 * k + ros[k] + 2 * (cp - starts[k])
                        gid = struct.unpack(">H", prog[gaddr : gaddr + 2])[0]
                        if gid:
                            gid = (gid + deltas[k]) & 0xFFFF
                    if gid:
                        out.setdefault(cp, gid)
        elif fmt == 12:
            ngroups = struct.unpack(">I", prog[sub + 12 : sub + 16])[0]
            for g in range(ngroups):
                sc, ec, sg = struct.unpack(">III", prog[sub + 16 + 12 * g : sub + 28 + 12 * g])
                for cp in range(sc, ec + 1):
                    out.setdefault(cp, sg + cp - sc)
    return out


def test_truetype_codes_are_the_subset_gids_the_embedded_cmap_agrees_with():
    """RC-034 的反例是「子集之后继续引用旧 GID」。判据不走写入器的 remap：把嵌入的 TrueType 子集
    自己的 cmap 解出来，对每个内容流里的 code（= 子集 GID），ToUnicode 说它是码位 U，那么子集
    cmap 里 U 必须映到这同一个 GID。单码位 cluster 逐个查；多码位 cluster（组合序列）没有
    cmap 条目，跳过但要求至少查过 20 个。"""
    page_head, _ = _page()
    res = _resources(page_head)
    checked = 0
    for name, num in res["Font"].items():
        head, _ = OBJS[num]
        desc_head, _ = OBJS[int(re.search(rb"/DescendantFonts \[ (\d+) 0 R \]", head).group(1))]
        if b"/CIDFontType2" not in desc_head:
            continue
        fd_head, _ = OBJS[_ref(desc_head, b"FontDescriptor")]
        _, prog = OBJS[_ref(fd_head, b"FontFile2")]
        cmap = _sfnt_cmap(prog)
        tounicode = _decode_tounicode(OBJS[_ref(head, b"ToUnicode")][1])
        for code, text in tounicode.items():
            if len(text) != 1:
                continue
            assert cmap.get(ord(text)) == code, (
                f"{name}: code {code} 的 ToUnicode 是 U+{ord(text):04X}，"
                f"但子集 cmap 把它映到 GID {cmap.get(ord(text))}——内容流引用的不是子集 GID"
            )
            checked += 1
    assert checked >= 20, checked


def test_text_ink_is_present_where_the_truth_puts_each_line():
    scale = TRUTH["raster"]["scale"]
    for t in TRUTH["text"]:
        dark = 0
        for xp in range(int(t["x"] * scale), int((t["x"] + 60) * scale)):
            for yp in range(
                int((TRUTH["page_pt"][1] - t["y"] - t["size"] * 0.7) * scale),
                int((TRUTH["page_pt"][1] - t["y"]) * scale),
            ):
                i = (yp * PNG_W + xp) * 4
                if PNG_RGBA[i] < 100 and PNG_RGBA[i + 1] < 100 and PNG_RGBA[i + 2] < 100:
                    dark += 1
        assert dark > 20, (t["id"], dark)


def test_the_candidate_reader_stack_in_the_report_is_pinned():
    """report.json 记的是**钉死的版本**（与 requirements.txt 同一组数字），不是「最新」。"""
    req = (ROOT / "scripts" / "dev" / "u02_spikes" / "requirements.txt").read_text(encoding="utf-8")
    pins = dict(re.findall(r"(?m)^([A-Za-z0-9_.\-]+)==([^\s#]+)", req))
    v = REPORT["versions"]
    assert v["pypdfium2"] == pins["pypdfium2"]
    assert v["pikepdf"] == pins["pikepdf"]
    assert v["fonttools"] == pins["fonttools"]
    assert v["uharfbuzz"] == pins["uharfbuzz"]
    assert v["pdfminer.six"] == pins["pdfminer.six"]
    assert v["pypdf"] == pins["pypdf"]
