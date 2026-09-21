"""ArtifactInspector —— 重新打开**封口的** staging 产物，量可观察的事实，按政策给出结论（统一实施包 U08，ADR 0068）。

04_ARCHITECTURE §1 那一行：*封口的最终文件 → ArtifactInspector → ArtifactManifest / 有范围的 Proof → 原有发布事务*。
这里做的就是那句话，三件事分得很开：

* **plan**（计划）——生产者说它写了什么：页面尺寸 / 像素 / ppi / 是不是矢量 / 期望的文字行 / 源产物与回执引用。
  由调用方交进来（候选路是 `job.produce` 从 RenderPlan 里填的；旧后端只有请求级的那几样）。本模块**不改**它。
* **observed**（观测）——重新打开文件量到的：PDF 的页盒 / 旋转 / UserUnit → 可见尺寸、内容流普查（路径 / 文字 / 位图 /
  Form / 着色）、字体（声明 vs **实际用到**、嵌入 / 子集 / ToUnicode）、抽回来的文字层、位图的实际有效 ppi（像素 ÷ 累计 CTM）；
  PNG / TIFF 的头、尺寸、密度标签、块完整性。全部由本模块**自己读字节**得到，不信生产者、不信客户端（RC-063 / RC-074）。
* **policy**（政策）——`standard`（普通导出）只要求完整性与核心尺寸，可选检查 unknown 只说明不拒绝（D08）；`strict`
  （用户选择严格规范）的必需项失败 **或 unknown** 都阻断，阈值（`min_raster_dpi`）来自调用方交进来的出版规范
  （`profiles` / `profilestore` 是唯一权威，这里不复制一个数——RC-071）。

四值判据（`VERDICTS`）：`verified` / `failed` / `unknown`（量不到：预算耗尽、结构不支持、没有期望值）/ `not_applicable`
（这个格式没有这一维）。**unknown 不是 verified**，也不是 failed——它单独一档（RC-070）。

## 边界（写在明处）

* PDF 的内容流遍历有**预算**（深度 / Form 数 / 指令数，`Budget`）：恶意递归 Form 或超大内容流不会让检查失控——预算
  耗尽时普查 / 文字 / 位图 / 字体使用全部 `unknown`（RC-072），不假装看完了。
* Type 3 字体（matplotlib 默认 `pdf.fonttype = 3`）的嵌入判据是 `unknown`（它没有 FontFile，字形在 CharProcs 里）；
  复杂裁剪、着色、透明组的内部不分析（`carrier` 只按顶层对象种类分：vector / mixed / raster / empty / unknown）。
* 「有没有被裁掉」只对**计划里知道包围盒的对象**判（画布上的文字 / 形状 / 箭头 / 面板框 vs 页面），外来页内部是
  `unknown`（RC-069：碰不到的不声称 no clipping）。
* 文字层的判据是「期望的每一行都能从 ToUnicode（或 ActualText）抽回来」——ToUnicode 存在 ≠ 映射正确，所以比的是**抽出来的
  字符串**（05 §5）。没有期望值（旧后端路）→ `unknown`。

native 适配层：pikepdf 只在函数里 import；PNG / TIFF 读取是纯标准库（与 `tiffwrite` / `originalspec` 同一口径，
不 import 它们——检查器要与写入侧不同源）。
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_VERSION = 1

VERIFIED = "verified"
FAILED = "failed"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"
VERDICTS = (VERIFIED, FAILED, UNKNOWN, NOT_APPLICABLE)

POLICY_STANDARD = "standard"
POLICY_STRICT = "strict"
POLICIES = (POLICY_STANDARD, POLICY_STRICT)

ACCEPTED = "accepted"
REJECTED = "rejected"

CARRIER_VECTOR = "vector"
CARRIER_MIXED = "mixed"
CARRIER_RASTER = "raster"
CARRIER_EMPTY = "empty"
CARRIER_UNKNOWN = "unknown"

#: 每个格式的检查项（闭集）。`standard` 必需的只有前两项；`strict` 必需的见 `REQUIRED`。
CHECKS_PDF = (
    "integrity",
    "size",
    "carrier",
    "fonts_embedded",
    "text_layer",
    "image_ppi",
    "clipping",
)
CHECKS_RASTER = ("integrity", "size", "dpi_tag", "raster_density")
REQUIRED = {
    POLICY_STANDARD: {"pdf": ("integrity", "size"), "raster": ("integrity", "size")},
    POLICY_STRICT: {
        "pdf": ("integrity", "size", "carrier", "fonts_embedded", "text_layer", "image_ppi"),
        "raster": ("integrity", "size", "raster_density"),
    },
}

#: PDF 页面尺寸容差（pt）：0.05 mm（`artifactcheck.SIZE_TOL_MM`）≈ 0.14 pt。
SIZE_TOL_PT = 0.2
#: 位图像素容差：旧后端报 `round()` 而 PyMuPDF 的位图是 ceil，差 1 px 是既有事实（U07 交接 ⑤）。
PX_TOL = 1
DPI_TOL = 0.5


@dataclass(frozen=True)
class Budget:
    max_depth: int = 8
    max_forms: int = 256
    max_ops: int = 500_000


class _BudgetExceeded(Exception):
    pass


# ---------------------------------------------------------------------------
# PNG / TIFF（纯标准库）
# ---------------------------------------------------------------------------
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def observe_png(path: Path) -> dict:
    """签名、逐块 CRC、IHDR、pHYs、IEND。坏一块就是 `integrity: failed`，不猜。"""
    data = Path(path).read_bytes()
    out: dict = {"format": "png", "bytes": len(data), "integrity": FAILED, "problems": []}
    if data[:8] != _PNG_SIG:
        out["problems"].append("not_png_signature")
        return out
    pos, saw_iend, saw_ihdr = 8, False, False
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        crc = data[pos + 8 + length : pos + 12 + length]
        if len(body) != length or len(crc) != 4:
            out["problems"].append(f"truncated_chunk:{tag!r}")
            return out
        if struct.unpack(">I", crc)[0] != (zlib.crc32(tag + body) & 0xFFFFFFFF):
            out["problems"].append(f"bad_crc:{tag!r}")
            return out
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            out.update(px=[w, h], bit_depth=depth, color_type=ctype)
            out["alpha"] = ctype in (4, 6)
            saw_ihdr = True
        elif tag == b"pHYs":
            x, y, unit = struct.unpack(">IIB", body[:9])
            out["dpi"] = round(x * 0.0254, 2) if unit == 1 else None
            out["dpi_unit"] = "meter" if unit == 1 else "unknown"
        elif tag == b"tRNS":
            out["alpha"] = True
        elif tag == b"IEND":
            saw_iend = True
            break
        pos += 12 + length
    if not (saw_ihdr and saw_iend):
        out["problems"].append("missing_ihdr_or_iend")
        return out
    out["integrity"] = VERIFIED
    out.setdefault("dpi", None)
    return out


_TIFF_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def observe_tiff(path: Path) -> dict:
    """第一个 IFD 的尺寸 / 通道 / 分辨率 / 条带；条带越界或 IFD 越界就是 `integrity: failed`。"""
    data = Path(path).read_bytes()
    out: dict = {"format": "tiff", "bytes": len(data), "integrity": FAILED, "problems": []}
    if data[:2] == b"II":
        e = "<"
    elif data[:2] == b"MM":
        e = ">"
    else:
        out["problems"].append("not_tiff_signature")
        return out
    try:
        magic, ifd = struct.unpack(e + "HI", data[2:8])
        if magic != 42:
            out["problems"].append("bad_magic")
            return out
        (n,) = struct.unpack(e + "H", data[ifd : ifd + 2])
        tags: dict[int, object] = {}
        for i in range(n):
            base = ifd + 2 + 12 * i
            tag, typ, count = struct.unpack(e + "HHI", data[base : base + 8])
            size = _TIFF_TYPE_SIZE.get(typ, 1) * count
            if size <= 4:
                raw = data[base + 8 : base + 8 + size]
            else:
                (off,) = struct.unpack(e + "I", data[base + 8 : base + 12])
                raw = data[off : off + size]
                if len(raw) != size:
                    out["problems"].append(f"tag_out_of_file:{tag}")
                    return out
            if typ == 3:
                val: object = list(struct.unpack(e + f"{count}H", raw))
            elif typ == 4:
                val = list(struct.unpack(e + f"{count}I", raw))
            elif typ == 5:
                pairs = struct.unpack(e + f"{2 * count}I", raw)
                val = [
                    pairs[k] / pairs[k + 1] if pairs[k + 1] else None
                    for k in range(0, len(pairs), 2)
                ]
            else:
                val = raw
            tags[tag] = val
    except struct.error:
        out["problems"].append("truncated_ifd")
        return out

    def one(tag: int):
        v = tags.get(tag)
        return v[0] if isinstance(v, list) and v else v

    w, h = one(256), one(257)
    if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
        out["problems"].append("bad_dimensions")
        return out
    offsets, counts = tags.get(273), tags.get(279)
    if not isinstance(offsets, list) or not isinstance(counts, list) or len(offsets) != len(counts):
        out["problems"].append("bad_strips")
        return out
    for off, cnt in zip(offsets, counts):
        if off + cnt > len(data):
            out["problems"].append("strip_out_of_file")
            return out
    samples = one(277) or 1
    extra = tags.get(338)
    unit = one(296)
    out.update(
        px=[w, h],
        bits_per_sample=tags.get(258),
        samples_per_pixel=samples,
        compression=one(259),
        alpha=bool(extra) and (extra[0] if isinstance(extra, list) else extra) in (1, 2),
        alpha_premultiplied=bool(extra) and (extra[0] if isinstance(extra, list) else extra) == 1,
        dpi=(round(float(one(282)), 2) if unit == 2 and one(282) else None),
        dpi_unit={1: "none", 2: "inch", 3: "cm"}.get(unit, "unknown"),
        strips=len(offsets),
    )
    out["integrity"] = VERIFIED
    return out


# ---------------------------------------------------------------------------
# PDF（pikepdf 只在这里 import）
# ---------------------------------------------------------------------------
_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")


def _utf16(hexstr: bytes) -> str:
    raw = bytes.fromhex(hexstr.decode("ascii").replace(" ", "").replace("\n", ""))
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        return raw.decode("utf-16-be", "replace")


def parse_tounicode(cmap: bytes) -> tuple[dict[int, str], int]:
    """ToUnicode CMap → (code → 文本, 码长字节数)。只认 bfchar / bfrange（含数组形）与 codespacerange 的码长。"""
    mapping: dict[int, str] = {}
    code_len = 2
    m = re.search(rb"begincodespacerange(.*?)endcodespacerange", cmap, re.S)
    if m:
        first = _HEX.search(m.group(1))
        if first:
            code_len = max(1, len(first.group(1).replace(b" ", b"")) // 2)
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        items = _HEX.findall(block)
        for i in range(0, len(items) - 1, 2):
            mapping[int(items[i], 16)] = _utf16(items[i + 1])
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for line in re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block
        ):
            lo, hi, dst = int(line[0], 16), int(line[1], 16), line[2]
            if hi - lo > 65535:
                continue
            if dst.startswith(b"["):
                dsts = _HEX.findall(dst)
                for k, code in enumerate(range(lo, hi + 1)):
                    if k < len(dsts):
                        mapping[code] = _utf16(dsts[k])
            else:
                base = _utf16(dst[1:-1])
                for k, code in enumerate(range(lo, hi + 1)):
                    if base:
                        mapping[code] = base[:-1] + chr(ord(base[-1]) + k)
    return mapping, code_len


@dataclass
class _Font:
    name: str
    base: str
    subtype: str
    embedded: str  # VERIFIED / FAILED / UNKNOWN
    subset: bool
    tounicode: dict[int, str] | None
    code_len: int
    type3: bool
    key: tuple
    used: bool = False


@dataclass
class _Walk:
    budget: Budget
    ops: int = 0
    forms_seen: set = field(default_factory=set)
    forms_visited: int = 0
    paths: int = 0
    texts: int = 0
    images: list = field(default_factory=list)
    shadings: int = 0
    forms: int = 0
    lines: list = field(default_factory=list)
    undecodable_runs: int = 0
    fonts: dict = field(default_factory=dict)  # key → _Font


def _font_record(name: str, obj, seen: dict) -> _Font:
    key = obj.objgen if obj.is_indirect else ("direct", id(obj))
    if key in seen:
        return seen[key]
    subtype = str(obj.get("/Subtype", "")).lstrip("/")
    base = str(obj.get("/BaseFont", "")).lstrip("/")
    subset = "+" in base and len(base.split("+", 1)[0]) == 6
    if subset:
        base = base.split("+", 1)[1]
    desc = obj.get("/FontDescriptor")
    if subtype == "Type0":
        kids = obj.get("/DescendantFonts")
        if kids is not None and len(kids) > 0:
            desc = kids[0].get("/FontDescriptor")
    type3 = subtype == "Type3"
    if type3:
        embedded = UNKNOWN
    elif desc is None:
        embedded = FAILED
    else:
        embedded = (
            VERIFIED
            if any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            else FAILED
        )
    tu = obj.get("/ToUnicode")
    mapping: dict[int, str] | None = None
    code_len = 1 if subtype in ("Type1", "TrueType", "Type3", "MMType1") else 2
    if tu is not None:
        try:
            mapping, code_len = parse_tounicode(tu.read_bytes())
        except Exception:  # noqa: BLE001 —— 坏 CMap = 没有可用映射
            mapping = None
    rec = _Font(name, base, subtype, embedded, subset, mapping, code_len, type3, key)
    seen[key] = rec
    return rec


def _decode(font: _Font, raw: bytes) -> str | None:
    if font.tounicode is None:
        # 简单字体没有 ToUnicode：只把 ASCII 段按 StandardEncoding 读（那一段与 ASCII 同形），≥ 0x80 的字节
        # 不猜（WinAnsi / MacRoman / Differences 都可能改它）——猜错比记 unknown 更坏
        if font.subtype in ("Type1", "TrueType", "MMType1") and all(b < 0x80 for b in raw):
            return raw.decode("ascii")
        return None
    n = font.code_len
    out = []
    for i in range(0, len(raw) - n + 1, n):
        code = int.from_bytes(raw[i : i + n], "big")
        piece = font.tounicode.get(code)
        if piece is None:
            return None
        out.append(piece)
    return "".join(out)


def _mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _walk(pikepdf, container, resources, ctm, depth: int, w: _Walk, fonts_seen: dict) -> None:
    if depth > w.budget.max_depth:
        raise _BudgetExceeded("depth")
    try:
        instructions = pikepdf.parse_content_stream(container)
    except Exception as exc:  # noqa: BLE001
        raise _BudgetExceeded(f"unparseable:{type(exc).__name__}") from exc
    stack: list = []
    font: _Font | None = None
    in_text = False
    line: list[str] = []
    actual: list[list] = []  # [text, consumed]
    res_fonts = resources.get("/Font") if resources is not None else None
    res_xobj = resources.get("/XObject") if resources is not None else None
    for ins in instructions:
        w.ops += 1
        if w.ops > w.budget.max_ops:
            raise _BudgetExceeded("ops")
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            w.images.append(_image_facts(None, ctm, inline=True))
            continue
        op = str(ins.operator)
        args = ins.operands
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(args) == 6:
            ctm = _mul(tuple(float(a) for a in args), ctm)
        elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
            w.paths += 1
        elif op == "sh":
            w.shadings += 1
        elif op == "BT":
            in_text, line = True, []
        elif op == "ET":
            if in_text and line:
                w.lines.append("".join(line))
            in_text = False
        elif op == "Tf" and args:
            name = str(args[0])
            fobj = res_fonts.get(name) if res_fonts is not None else None
            if fobj is None:
                font = None
                w.undecodable_runs += 1
            else:
                font = _font_record(name, fobj, fonts_seen)
                font.used = True
                w.fonts[font.key] = font
        elif op == "BDC" and len(args) == 2 and isinstance(args[1], pikepdf.Dictionary):
            at = args[1].get("/ActualText")
            actual.append([str(at) if at is not None else None, False])
        elif op == "EMC":
            if actual:
                actual.pop()
        elif op in ("Tj", "'", '"', "TJ"):
            w.texts += 1
            pieces: list[bytes] = []
            if op == "TJ" and args and isinstance(args[-1], pikepdf.Array):
                pieces = [bytes(a) for a in args[-1] if isinstance(a, pikepdf.String)]
            elif args and isinstance(args[-1], pikepdf.String):
                pieces = [bytes(args[-1])]
            scope = actual[-1] if actual else None
            if scope is not None and scope[0] is not None:
                if not scope[1]:
                    line.append(scope[0])
                    scope[1] = True
                continue
            if font is None:
                w.undecodable_runs += 1
                continue
            for raw in pieces:
                text = _decode(font, raw)
                if text is None:
                    w.undecodable_runs += 1
                else:
                    line.append(text)
        elif op == "Do" and args:
            name = str(args[0])
            xobj = res_xobj.get(name) if res_xobj is not None else None
            if xobj is None:
                continue
            sub = str(xobj.get("/Subtype", ""))
            if sub == "/Image":
                w.images.append(_image_facts(xobj, ctm))
            elif sub == "/Form":
                w.forms += 1
                key = xobj.objgen if xobj.is_indirect else ("direct", id(xobj))
                w.forms_visited += 1
                if w.forms_visited > w.budget.max_forms:
                    raise _BudgetExceeded("forms")
                matrix = xobj.get("/Matrix")
                inner = _mul(tuple(float(v) for v in matrix), ctm) if matrix is not None else ctm
                sub_res = xobj.get("/Resources")
                _walk(
                    pikepdf,
                    xobj,
                    sub_res if sub_res is not None else resources,
                    inner,
                    depth + 1,
                    w,
                    fonts_seen,
                )
                w.forms_seen.add(key)


def _image_facts(xobj, ctm, *, inline: bool = False) -> dict:
    a, b, c, d, _e, _f = ctm
    width_pt, height_pt = math.hypot(a, b), math.hypot(c, d)
    facts: dict = {
        "inline": inline,
        "width_pt": round(width_pt, 3),
        "height_pt": round(height_pt, 3),
    }
    if xobj is not None:
        try:
            px_w, px_h = int(xobj.get("/Width", 0)), int(xobj.get("/Height", 0))
        except (TypeError, ValueError):
            px_w = px_h = 0
        facts.update(
            px=[px_w, px_h], mask=bool(xobj.get("/ImageMask", False)), smask="/SMask" in xobj
        )
        if px_w > 0 and px_h > 0 and width_pt > 1e-9 and height_pt > 1e-9:
            facts["ppi"] = [round(px_w / (width_pt / 72.0), 1), round(px_h / (height_pt / 72.0), 1)]
        else:
            facts["ppi"] = None
    else:
        facts.update(px=None, ppi=None)
    return facts


def _visible_size(page, pikepdf) -> tuple[float, float, dict]:
    media = [float(v) for v in page.mediabox]
    crop_obj = page.obj.get("/CropBox")
    crop = [float(v) for v in crop_obj] if crop_obj is not None else list(media)
    x0, y0 = (
        max(min(crop[0], crop[2]), min(media[0], media[2])),
        max(min(crop[1], crop[3]), min(media[1], media[3])),
    )
    x1, y1 = (
        min(max(crop[0], crop[2]), max(media[0], media[2])),
        min(max(crop[1], crop[3]), max(media[1], media[3])),
    )
    w, h = max(0.0, x1 - x0), max(0.0, y1 - y0)
    rotate = int(page.obj.get("/Rotate", 0) or 0) % 360
    if rotate in (90, 270):
        w, h = h, w
    uu = page.obj.get("/UserUnit")
    uu = float(uu) if uu is not None else 1.0
    return w * uu, h * uu, {"media_box": media, "crop_box": crop, "rotate": rotate, "user_unit": uu}


def observe_pdf_basic(path: Path, probe) -> dict:
    """没有 pikepdf 时的基本观测：经调用方给的 `probe(path, "pdf")`（契约层的 `probe_asset`，按选中的后端
    打开）核「打得开、有页、有尺寸」；普查 / 字体 / 文字 / 位图全部 unknown 并写明原因。"""
    data = Path(path).read_bytes()
    out: dict = {"format": "pdf", "bytes": len(data), "integrity": FAILED, "problems": []}
    if data[:5] != b"%PDF-":
        out["problems"].append("not_pdf_signature")
        return out
    try:
        facts = probe(Path(path), "pdf")
        w, h = float(facts["w_pt"]), float(facts["h_pt"])
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"unreadable:{type(exc).__name__}")
        return out
    if not (w > 0 and h > 0):
        out["problems"].append("empty_page_box")
        return out
    out.update(
        integrity=VERIFIED,
        pages=None,
        size_pt=[round(w, 3), round(h, 3)],
        budget_exceeded="no_reader",
        carrier=CARRIER_UNKNOWN,
        fonts_declared=[],
        fonts_used=[],
        images=[],
        text_lines=[],
        undecodable_runs=0,
        reader="probe",
    )
    return out


def observe_pdf_any(path: Path, *, probe=None, budget: Budget | None = None) -> dict:
    """有 pikepdf 走完整观测；没有就退到 `probe` 的基本观测；两者都没有 → integrity unknown（不是 verified）。"""
    try:
        import pikepdf  # noqa: F401
    except ImportError:
        if probe is None:
            data = Path(path).read_bytes()
            return {
                "format": "pdf",
                "bytes": len(data),
                "integrity": UNKNOWN,
                "problems": ["no_reader"],
            }
        return observe_pdf_basic(path, probe)
    return observe_pdf(path, budget=budget)


def observe_pdf(path: Path, *, budget: Budget | None = None) -> dict:
    """打开、`check()`、首页可见尺寸、内容流普查（含 Form 递归，有预算）、字体声明 vs 使用、文字层、位图有效 ppi。"""
    import pikepdf

    budget = budget or Budget()
    data = Path(path).read_bytes()
    out: dict = {"format": "pdf", "bytes": len(data), "integrity": FAILED, "problems": []}
    try:
        pdf = pikepdf.open(str(path))
    except pikepdf.PasswordError:
        out["problems"].append("encrypted")
        return out
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"unreadable:{type(exc).__name__}")
        return out
    with pdf:
        try:
            issues = list(pdf.check_pdf_syntax())  # qpdf --check 的那一半：语法 / 交叉引用 / 流长度
        except Exception as exc:  # noqa: BLE001
            out["problems"].append(f"check_failed:{type(exc).__name__}")
            return out
        if issues:
            out["problems"].extend(f"qpdf:{str(i)[:120]}" for i in issues[:5])
            return out
        out["pages"] = len(pdf.pages)
        if not pdf.pages:
            out["problems"].append("no_pages")
            return out
        page = pdf.pages[0]
        w_pt, h_pt, boxes = _visible_size(page, pikepdf)
        out.update(size_pt=[round(w_pt, 3), round(h_pt, 3)], **boxes)
        out["integrity"] = VERIFIED
        walk = _Walk(budget)
        fonts_seen: dict = {}
        declared: list[str] = []
        res = page.obj.get("/Resources")
        fonts_dict = res.get("/Font") if res is not None else None
        if fonts_dict is not None:
            for name, fobj in fonts_dict.items():
                rec = _font_record(str(name), fobj, fonts_seen)
                declared.append(rec.base or str(name))
        exceeded: str | None = None
        try:
            _walk(pikepdf, page, res, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 0, walk, fonts_seen)
        except _BudgetExceeded as exc:
            exceeded = str(exc)
        out["budget_exceeded"] = exceeded
        out["census"] = {
            "paths": walk.paths,
            "text": walk.texts,
            "images": len(walk.images),
            "shadings": walk.shadings,
            "forms": walk.forms,
            "ops": walk.ops,
        }
        if exceeded:
            out["carrier"] = CARRIER_UNKNOWN
        elif walk.images and not (walk.paths or walk.texts or walk.shadings):
            out["carrier"] = CARRIER_RASTER
        elif walk.images:
            out["carrier"] = CARRIER_MIXED
        elif walk.paths or walk.texts or walk.shadings:
            out["carrier"] = CARRIER_VECTOR
        else:
            out["carrier"] = CARRIER_EMPTY
        out["fonts_declared"] = declared
        out["fonts_used"] = [
            {
                "name": f.base,
                "subtype": f.subtype,
                "embedded": f.embedded,
                "subset": f.subset,
                "tounicode": f.tounicode is not None,
                "type3": f.type3,
            }
            for f in walk.fonts.values()
            if f.used
        ]
        out["images"] = walk.images
        out["text_lines"] = walk.lines
        out["undecodable_runs"] = walk.undecodable_runs
    return out


# ---------------------------------------------------------------------------
# 政策与 manifest
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(str(s).split())


def _check_pdf(observed: dict, plan: dict, profile: dict | None) -> tuple[dict, list[str]]:
    checks: dict[str, str] = {}
    notes: list[str] = []
    checks["integrity"] = observed["integrity"]
    if observed["integrity"] != VERIFIED:
        for k in CHECKS_PDF[1:]:
            checks[k] = UNKNOWN
        notes.append(
            "没有可用的 PDF 读取器，完整性无从检查"
            if observed["integrity"] == UNKNOWN
            else "文件打不开或结构损坏，其余项无从检查"
        )
        return checks, notes
    if observed.get("reader") == "probe":
        notes.append("pikepdf 不在：只核了打得开与页面尺寸，普查 / 字体 / 文字 / 位图未核验")
    want = plan.get("page_pt")
    if want is None:
        checks["size"] = UNKNOWN
        notes.append("计划里没有页面尺寸，尺寸无从比对")
    else:
        got = observed["size_pt"]
        ok = (
            abs(got[0] - float(want[0])) <= SIZE_TOL_PT
            and abs(got[1] - float(want[1])) <= SIZE_TOL_PT
        )
        checks["size"] = VERIFIED if ok else FAILED
    exceeded = observed.get("budget_exceeded")
    if exceeded:
        for k in ("carrier", "fonts_embedded", "text_layer", "image_ppi"):
            checks[k] = UNKNOWN
        notes.append(f"内容流遍历预算耗尽（{exceeded}），普查 / 字体 / 文字 / 位图未核验")
    else:
        carrier = observed["carrier"]
        want_vector = plan.get("vector")
        if want_vector is None:
            checks["carrier"] = UNKNOWN
        elif carrier == CARRIER_UNKNOWN:
            checks["carrier"] = UNKNOWN
        elif want_vector:
            checks["carrier"] = (
                VERIFIED if carrier in (CARRIER_VECTOR, CARRIER_MIXED, CARRIER_EMPTY) else FAILED
            )
            if carrier == CARRIER_MIXED:
                notes.append("载体是 mixed：矢量之外还有位图对象（面板里的位图源或位图面板）")
        else:
            checks["carrier"] = VERIFIED if carrier in (CARRIER_RASTER, CARRIER_MIXED) else FAILED
        used = observed["fonts_used"]
        if not used:
            checks["fonts_embedded"] = NOT_APPLICABLE
        elif any(f["embedded"] == FAILED for f in used):
            checks["fonts_embedded"] = FAILED
        elif any(f["embedded"] == UNKNOWN for f in used):
            checks["fonts_embedded"] = UNKNOWN
            notes.append("有 Type 3 字体（字形在 CharProcs 里），嵌入这一维记 unknown")
        else:
            checks["fonts_embedded"] = VERIFIED
        expected = plan.get("text")
        if expected is None:
            checks["text_layer"] = UNKNOWN if used else NOT_APPLICABLE
            if used:
                notes.append("计划里没有期望的文字行，文字层只抽不比")
        elif not expected:
            checks["text_layer"] = NOT_APPLICABLE
        else:
            got_text = _norm(" ".join(observed["text_lines"]))
            missing = [line for line in expected if _norm(line) and _norm(line) not in got_text]
            if not missing:
                checks["text_layer"] = VERIFIED
            elif observed.get("undecodable_runs"):
                checks["text_layer"] = UNKNOWN
                notes.append("有文字段没有 ToUnicode 映射，抽不回来的行记 unknown")
            else:
                checks["text_layer"] = FAILED
                notes.append(f"期望的文字行没能从文字层抽回来：{missing[:3]}")
        images = [im for im in observed["images"] if im.get("ppi")]
        min_ppi = (
            float(profile.get("min_raster_dpi"))
            if profile and profile.get("min_raster_dpi")
            else None
        )
        if not observed["images"]:
            checks["image_ppi"] = NOT_APPLICABLE
        elif any(im.get("ppi") is None for im in observed["images"]):
            checks["image_ppi"] = UNKNOWN
            notes.append("有位图的有效 ppi 算不出（内联图或退化 CTM）")
        elif min_ppi is None:
            checks["image_ppi"] = UNKNOWN
            notes.append("没有规范给出最低 ppi，位图密度只记录不判")
        else:
            low = [im for im in images if min(im["ppi"]) + 1e-6 < min_ppi]
            checks["image_ppi"] = FAILED if low else VERIFIED
            if low:
                notes.append(f"{len(low)} 张位图的有效 ppi 低于规范 {min_ppi}")
    boxes = plan.get("object_boxes")
    if not boxes:
        checks["clipping"] = UNKNOWN
        notes.append("没有对象包围盒，裁切只对计划里的对象可判——本次无对象可判")
    else:
        w, h = observed["size_pt"]
        outside = [
            b["id"]
            for b in boxes
            if b["bbox"][0] < -SIZE_TOL_PT
            or b["bbox"][1] < -SIZE_TOL_PT
            or b["bbox"][2] > w + SIZE_TOL_PT
            or b["bbox"][3] > h + SIZE_TOL_PT
        ]
        checks["clipping"] = FAILED if outside else VERIFIED
        if outside:
            notes.append(f"这些对象的包围盒越出页面（会被裁掉）：{outside[:5]}")
        notes.append("裁切只对计划里的对象判；外来页内部不分析（unknown）")
    return checks, notes


def _check_raster(observed: dict, plan: dict, profile: dict | None) -> tuple[dict, list[str]]:
    checks: dict[str, str] = {"integrity": observed["integrity"]}
    notes: list[str] = []
    if observed["integrity"] != VERIFIED:
        for k in CHECKS_RASTER[1:]:
            checks[k] = UNKNOWN
        notes.append("文件损坏，其余项无从检查")
        return checks, notes
    want = plan.get("px")
    if want is None:
        checks["size"] = UNKNOWN
    else:
        got = observed["px"]
        ok = abs(got[0] - int(want[0])) <= PX_TOL and abs(got[1] - int(want[1])) <= PX_TOL
        checks["size"] = VERIFIED if ok else FAILED
    ppi = plan.get("ppi")
    dpi = observed.get("dpi")
    if dpi is None:
        checks["dpi_tag"] = UNKNOWN
        notes.append("文件没有声明密度（没有 pHYs / 分辨率标签）")
    elif ppi is None:
        checks["dpi_tag"] = UNKNOWN
    else:
        checks["dpi_tag"] = VERIFIED if abs(float(dpi) - float(ppi)) <= DPI_TOL else FAILED
        if checks["dpi_tag"] == FAILED:
            notes.append(f"文件声明的密度 {dpi} 与本次请求的 {ppi} ppi 不符")
    min_ppi = (
        float(profile.get("min_raster_dpi")) if profile and profile.get("min_raster_dpi") else None
    )
    # 有效密度 = 文件里的像素 ÷ 计划的页面尺寸（观测的像素、计划的物理尺寸），不是请求的数、也不是文件
    # 自己声明的标签（那一维归 `dpi_tag`）；页面尺寸不知道时才退到请求的 ppi
    page_pt = plan.get("page_pt")
    if page_pt and observed.get("px"):
        effective = min(
            observed["px"][0] / (float(page_pt[0]) / 72.0),
            observed["px"][1] / (float(page_pt[1]) / 72.0),
        )
        observed["effective_ppi"] = round(effective, 1)
    else:
        effective = float(ppi) if ppi is not None else None
    if min_ppi is None or effective is None:
        checks["raster_density"] = UNKNOWN
        if min_ppi is None:
            notes.append("没有规范给出最低 ppi，密度只记录不判")
    else:
        checks["raster_density"] = VERIFIED if effective + 1e-6 >= min_ppi else FAILED
        if checks["raster_density"] == FAILED:
            notes.append(f"有效密度 {effective:.1f} ppi 低于规范 {min_ppi}")
    return checks, notes


def inspect(
    path: Path,
    fmt: str,
    *,
    plan: dict,
    policy: str = POLICY_STANDARD,
    profile: dict | None = None,
    budget: Budget | None = None,
    probe=None,
    run_id: str | None = None,
) -> dict:
    """封口产物 → ArtifactManifest（plan / observed / checks / policy 分开；`artifact.sha256` 是文件字节）。

    `plan` 由生产者给（不改）；`profile` 是出版规范（严格政策的阈值来源，调用方从 `profilestore` 解析）；
    `run_id` 是这次作业的 id（U09 四身份里的 `run`——只进 `identity.run`，不进语义 / render 身份）。
    """
    if policy not in POLICIES:
        raise ValueError(f"policy 非法: {policy!r}")
    fmt = str(fmt).lower()
    path = Path(path)
    data = path.read_bytes()
    if fmt == "pdf":
        observed = observe_pdf_any(path, probe=probe, budget=budget)
        checks, notes = _check_pdf(observed, plan, profile)
        kind = "pdf"
    elif fmt == "png":
        observed = observe_png(path)
        checks, notes = _check_raster(observed, plan, profile)
        kind = "raster"
    elif fmt in ("tif", "tiff"):
        observed = observe_tiff(path)
        checks, notes = _check_raster(observed, plan, profile)
        kind = "raster"
    else:
        observed = {"format": fmt, "bytes": len(data), "integrity": UNKNOWN, "problems": []}
        checks, notes, kind = (
            {"integrity": UNKNOWN},
            [f"{fmt}：本模块没有这个格式的读取器"],
            "other",
        )
    required = list(REQUIRED[policy].get(kind, ("integrity",)))
    failed = [k for k in required if checks.get(k) == FAILED]
    unknown = [k for k in required if checks.get(k) == UNKNOWN]
    optional_failed = [k for k, v in checks.items() if v == FAILED and k not in required]
    optional_unknown = [k for k, v in checks.items() if v == UNKNOWN and k not in required]
    if policy == POLICY_STRICT:
        verdict = REJECTED if failed or unknown else ACCEPTED
    else:
        verdict = REJECTED if failed else ACCEPTED
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "manifest_version": MANIFEST_VERSION,
        "format": fmt,
        "artifact": {"sha256": sha256, "bytes": len(data)},
        "identity": _identity_block(plan, sha256, run_id),
        "provenance": _provenance_block(plan),
        "plan": dict(plan),
        "observed": observed,
        "checks": checks,
        "policy": {
            "mode": policy,
            "profile_id": (profile or {}).get("profile_id"),
            "required": required,
            "verdict": verdict,
            "failed": failed,
            "unknown": unknown,
            "optional_failed": optional_failed,
            "optional_unknown": optional_unknown,
        },
        "notes": notes,
        "scope": {
            "budget": {
                "max_depth": (budget or Budget()).max_depth,
                "max_forms": (budget or Budget()).max_forms,
                "max_ops": (budget or Budget()).max_ops,
            },
            "limits": [
                "Type 3 字体的嵌入记 unknown",
                "外来页内部与复杂裁剪 / 透明组不分析",
                "裁切只对计划里的对象判",
            ],
        },
    }


def uninspected(
    path: Path,
    fmt: str,
    *,
    plan: dict,
    policy: str = POLICY_STANDARD,
    reason: str = "",
    run_id: str | None = None,
) -> dict:
    """检查器自己炸了：所有项 unknown、按政策裁决（standard 交付并说明；strict 阻断）。不是通过。"""
    data = Path(path).read_bytes()
    kind = "pdf" if fmt == "pdf" else ("raster" if fmt in ("png", "tif", "tiff") else "other")
    names = CHECKS_PDF if kind == "pdf" else (CHECKS_RASTER if kind == "raster" else ("integrity",))
    checks = {k: UNKNOWN for k in names}
    required = list(REQUIRED[policy].get(kind, ("integrity",)))
    verdict = REJECTED if policy == POLICY_STRICT else ACCEPTED
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "manifest_version": MANIFEST_VERSION,
        "format": fmt,
        "artifact": {"sha256": sha256, "bytes": len(data)},
        "identity": _identity_block(plan, sha256, run_id),
        "provenance": _provenance_block(plan),
        "plan": dict(plan),
        "observed": {"format": fmt, "bytes": len(data), "integrity": UNKNOWN, "problems": [reason]},
        "checks": checks,
        "policy": {
            "mode": policy,
            "profile_id": None,
            "required": required,
            "verdict": verdict,
            "failed": [],
            "unknown": required,
            "optional_failed": [],
            "optional_unknown": [k for k in checks if k not in required],
        },
        "notes": [f"检查器异常，所有项未核验：{reason}"],
        "scope": {"budget": None, "limits": []},
    }


def _identity_block(plan: dict, sha256: str, run_id: str | None) -> dict:
    """四身份并列（U09，ADR 0070）：semantic / render 来自计划半张（旧后端没有 RenderPlan → None，如实），
    artifact 是刚算的文件字节，run 是作业 id。谁也不含谁。"""
    from .identity import identities

    return identities(
        semantic=plan.get("plan_identity"),
        render=plan.get("render_identity"),
        artifact_sha256=sha256,
        run=run_id,
    )


def _provenance_block(plan: dict) -> dict:
    """来源段：源产物公开身份 + 回执公开事实 + 节点表（都是计划半张里生产者给的；这里只搬，不改）。"""
    return {
        "sources": [dict(s) for s in plan.get("sources") or []],
        "execution_receipts": list(plan.get("execution_receipts") or []),
        "receipts": [dict(r) for r in plan.get("receipts") or []],
        "nodes": [dict(n) for n in plan.get("nodes") or []],
        "nodes_truncated": bool(plan.get("nodes_truncated", False)),
    }


def summary(manifest: dict) -> dict:
    """给回执 / 前端的投影：verdict + 每项四值 + 说明；**不带 observed 的大块**（图片表、文字行）。
    U09 起带 `identity`（四身份）与 `provenance`（来源 / 回执公开事实 / 节点表）。"""
    return {
        "manifest_version": manifest["manifest_version"],
        "format": manifest["format"],
        "sha256": manifest["artifact"]["sha256"],
        "bytes": manifest["artifact"]["bytes"],
        "policy": manifest["policy"]["mode"],
        "verdict": manifest["policy"]["verdict"],
        "required": list(manifest["policy"]["required"]),
        "checks": dict(manifest["checks"]),
        "notes": list(manifest["notes"]),
        "carrier": manifest["observed"].get("carrier"),
        "size_pt": manifest["observed"].get("size_pt"),
        "px": manifest["observed"].get("px"),
        "dpi": manifest["observed"].get("dpi"),
        "fonts_used": [f["name"] for f in manifest["observed"].get("fonts_used", [])],
        "plan_identity": manifest["plan"].get("plan_identity"),
        "backend": manifest["plan"].get("backend"),
        "identity": dict(manifest.get("identity") or {}),
        "provenance": dict(manifest.get("provenance") or {}),
    }


#: 公开投影里源产物 / 回执 / 节点各自允许带出的键（闭集）：身份与结论进，名字与内容不进。
_PUBLIC_SOURCE_KEYS = ("origin", "kind", "bytes_sha256", "receipt_identity", "patch_hash")
_PUBLIC_RECEIPT_KEYS = (
    "receipt_identity",
    "completeness",
    "runtime_rejected",
    "pid_check",
    "control_plane",
    "python_source",
    "generation",
    "source_revision",
    "python_version",
    "python_implementation",
    "platform",
    "machine",
    "packages",
    "cwd_origin",
    "observation",
    "binding",
)
_PUBLIC_NODE_KEYS = ("kind", "origin", "receipt_identity", "internal")


def public_projection(manifest: dict, *, trace: dict | None = None) -> dict:
    """可以离开本机的那一份（XMP / 报告 / 遥测 / 诊断包的公开面，U09，RC-081 / FO-062）。

    **只带身份与结论**：四身份、格式与字节数、政策与裁决、每项四值、载体 / 尺寸 / 像素 / 密度、用到的字体名、
    源产物的（origin / kind / 字节 hash / 回执身份 / patch hash）、回执的公开事实（版本号 / 包版本 / 绑定核对
    的计数）、节点表的（种类 / 来源关系）、可复现性口径、有界的阶段轨迹。

    **不带**：`notes`（里面可能引用期望的文字行——科研正文）、`plan.text`、`object_boxes`、`source_id` /
    节点 id / 面板 id（用户起的文件名与对象名）、`observed` 的大块、任何路径 / argv / 环境变量。
    `tests/test_export_identity.py` 用一组「针」钉住：data_dir / home / prefix / 解释器 / 临时目录 / 脚本正文
    片段一个都不许出现。
    """
    from .identity import REPRODUCIBILITY

    prov = manifest.get("provenance") or {}
    return {
        "manifest_version": manifest["manifest_version"],
        "format": manifest["format"],
        "bytes": manifest["artifact"]["bytes"],
        "identity": dict(manifest.get("identity") or {}),
        "policy": {
            "mode": manifest["policy"]["mode"],
            "profile_id": manifest["policy"].get("profile_id"),
            "verdict": manifest["policy"]["verdict"],
            "required": list(manifest["policy"]["required"]),
            "failed": list(manifest["policy"].get("failed") or []),
            "unknown": list(manifest["policy"].get("unknown") or []),
        },
        "checks": dict(manifest["checks"]),
        "carrier": manifest["observed"].get("carrier"),
        "size_pt": manifest["observed"].get("size_pt"),
        "px": manifest["observed"].get("px"),
        "dpi": manifest["observed"].get("dpi"),
        "fonts_used": [f["name"] for f in manifest["observed"].get("fonts_used", [])],
        "backend": manifest["plan"].get("backend"),
        "sources": [{k: s.get(k) for k in _PUBLIC_SOURCE_KEYS} for s in prov.get("sources") or []],
        "receipts": [
            {k: r.get(k) for k in _PUBLIC_RECEIPT_KEYS} for r in prov.get("receipts") or []
        ],
        "nodes": [{k: n.get(k) for k in _PUBLIC_NODE_KEYS} for n in prov.get("nodes") or []],
        "nodes_truncated": bool(prov.get("nodes_truncated", False)),
        "reproducibility": dict(REPRODUCIBILITY),
        "trace": dict(trace) if isinstance(trace, dict) else None,
    }


__all__ = [
    "ACCEPTED",
    "Budget",
    "CHECKS_PDF",
    "CHECKS_RASTER",
    "FAILED",
    "MANIFEST_VERSION",
    "NOT_APPLICABLE",
    "POLICIES",
    "POLICY_STANDARD",
    "POLICY_STRICT",
    "REJECTED",
    "REQUIRED",
    "UNKNOWN",
    "VERDICTS",
    "VERIFIED",
    "inspect",
    "observe_pdf",
    "observe_pdf_any",
    "observe_pdf_basic",
    "observe_png",
    "observe_tiff",
    "parse_tounicode",
    "public_projection",
    "summary",
    "uninspected",
]
