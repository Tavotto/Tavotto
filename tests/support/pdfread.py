"""**纯标准库**的极简 PDF 读取（统一实施包 U06 的独立读取器之一；RC-095：写入 / 读取不同源）。

只认 qpdf 写出的这一种形状（无对象流、`/Length` 直接量、FlateDecode），只读 RenderCore 写入器
会写的那几样：页面 / 资源 / 内容流 / Type0 字体结构 / ToUnicode / 嵌入子集的 sfnt cmap /
ActualText 标记 / 内容流里的路径与 `cm`。它**不是** parser 的替代品，是每次 CI 都会量的第四把尺子
（另外三把：pypdfium2 / pdfminer.six / poppler，要装候选包或系统工具）。

与 `tests/test_foundation_u02_render.py` 里那份读取器同源（那份先写，这里搬成可复用的模块）。
本模块不 import 任何产品代码。
"""

from __future__ import annotations

import re
import struct
import zlib

_OBJ = re.compile(rb"(?m)^(\d+) 0 obj\s*(.*?)\s*^endobj", re.S)
_NUM = rb"-?\d+(?:\.\d+)?"


def objects(pdf: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """{obj 号: (字典文本, 解码后的流 或 None)}。"""
    out: dict[int, tuple[bytes, bytes | None]] = {}
    for m in _OBJ.finditer(pdf):
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
    if not out:
        raise AssertionError("一个 obj 都没切出来——PDF 形状变了？")
    return out


def ref(head: bytes, key: bytes) -> int:
    m = re.search(rb"/" + key + rb"\s+(\d+) 0 R", head)
    if not m:
        raise AssertionError(f"{key!r} 不在 {head[:200]!r}")
    return int(m.group(1))


def nums(s: bytes) -> list[float]:
    return [float(x) for x in re.findall(_NUM, s)]


def page(objs: dict[int, tuple[bytes, bytes | None]]) -> tuple[bytes, bytes]:
    """(页面字典, 内容流)。"""
    for head, _ in objs.values():
        if b"/Type /Page" in head and b"/Pages" not in head:
            return head, objs[ref(head, b"Contents")][1] or b""
    raise AssertionError("没有页面对象")


def media_box(page_head: bytes) -> list[float]:
    return nums(re.search(rb"/MediaBox \[(.*?)\]", page_head).group(1))


def resources(head: bytes) -> dict[str, dict[str, int]]:
    """/Resources << /XObject << /Fm1 N 0 R … >> /ExtGState … /Font … >> → 各类名字 → obj 号。"""
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
# 字体
# ---------------------------------------------------------------------------
def fonts(objs: dict, page_head: bytes) -> dict[str, dict]:
    """页面 /Font 里每张脸的结构事实：Type0 / Identity-H / 子集前缀 / 字体程序种类与字节 / W 条目 / ToUnicode。"""
    res = resources(page_head)
    out: dict[str, dict] = {}
    for name, num in res["Font"].items():
        head, _ = objs[num]
        info: dict = {
            "type0": b"/Subtype /Type0" in head,
            "identity_h": b"/Encoding /Identity-H" in head,
        }
        base = re.search(rb"/BaseFont /(\w+)\+([\w-]+)", head)
        info["tag"] = base.group(1).decode() if base else None
        info["base"] = base.group(2).decode() if base else None
        desc_head, _ = objs[int(re.search(rb"/DescendantFonts \[ (\d+) 0 R \]", head).group(1))]
        fd_head, _ = objs[ref(desc_head, b"FontDescriptor")]
        tu = re.search(rb"/ToUnicode\s+(\d+) 0 R", head)
        info["tounicode"] = objs[int(tu.group(1))][1] if tu else None
        if b"/Subtype /CIDFontType2" in desc_head:
            info["kind"] = "truetype"
            info["cid_to_gid_identity"] = b"/CIDToGIDMap /Identity" in desc_head
            ff = re.search(rb"/FontFile2\s+(\d+) 0 R", fd_head)
            prog_head, prog = objs[int(ff.group(1))] if ff else (b"", None)
            info["program"] = prog
            info["length1_ok"] = bool(prog) and int(
                re.search(rb"/Length1 (\d+)", prog_head).group(1)
            ) == len(prog)
        elif b"/Subtype /CIDFontType0" in desc_head:
            info["kind"] = "cff-cid"
            ff = re.search(rb"/FontFile3\s+(\d+) 0 R", fd_head)
            prog_head, prog = objs[int(ff.group(1))] if ff else (b"", None)
            info["program"] = prog
            info["fontfile3_subtype_ok"] = b"/Subtype /CIDFontType0C" in prog_head
        else:
            info["kind"] = None
            info["program"] = None
        w = re.search(rb"/W \[(.*?)\]\s*>>", desc_head, re.S)
        info["widths"] = {}
        if w:
            for code, width in re.findall(rb"(\d+)\s*\[\s*(" + _NUM + rb")\s*\]", w.group(1)):
                info["widths"][int(code)] = float(width)
        info["w_entries"] = len(info["widths"])
        out[name] = info
    return out


def decode_tounicode(cmap: bytes | None) -> dict[int, str]:
    """只读 begin/endbfchar 与 begin/endbfrange 之间的行。目标可以是空串（`<>`）。"""
    out: dict[int, str] = {}
    if not cmap:
        return out
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]{4})> <([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be") if dst else ""
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst in re.findall(
            rb"<([0-9A-Fa-f]{4})> <([0-9A-Fa-f]{4})> <([0-9A-Fa-f]+)>", block
        ):
            base = int(dst, 16)
            for i, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                out[code] = chr(base + i)
    return out


def sfnt_cmap(prog: bytes) -> dict[int, int]:
    """嵌入的 TrueType 子集自己的 cmap（format 4 / 12）→ {码位: GID}。"""
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
        _pid, _eid, soff = struct.unpack(">HHI", prog[coff + 4 + 8 * i : coff + 12 + 8 * i])
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


# ---------------------------------------------------------------------------
# 内容流里的文字
# ---------------------------------------------------------------------------
_TOKEN = re.compile(
    rb"/(F\d+) (" + _NUM + rb") Tf"  # 1,2 字体 / 字号
    rb"|/Span <</ActualText <([0-9A-Fa-f]*)>>> BDC"  # 3 ActualText
    rb"|(EMC)"  # 4
    rb"|\[(.*?)\] TJ"  # 5 TJ
    rb"|(" + _NUM + rb") Ts"  # 6 rise
    rb"|1 0 0 1 (" + _NUM + rb") (" + _NUM + rb") Tm",  # 7,8 Tm
    re.S,
)


def text_runs(content: bytes, font_maps: dict[str, dict[int, str]]) -> list[dict]:
    """按 BT…ET 段解码文字：每段 → {text（ToUnicode 视角）, logical（ActualText 优先）, codes, x, y, rises}。
    只认 RenderCore 写入器的形状（`/F size Tf` + `1 0 0 1 x y Tm` + `[…] TJ` + `Ts` + Span/EMC）。"""
    out: list[dict] = []
    for block in re.findall(rb"BT(.*?)ET", content, re.S):
        cur = None
        size = 0.0
        text = ""
        logical = ""
        codes: list[tuple[str, int]] = []
        actual: str | None = None
        actual_buf = ""
        rises: set[float] = set()
        x = y = None
        tms: list[tuple[float, float]] = []
        for tok in _TOKEN.finditer(block):
            if tok.group(1):
                cur, size = tok.group(1).decode(), float(tok.group(2))
            elif tok.group(3) is not None:
                actual = bytes.fromhex(tok.group(3).decode()).decode("utf-16")
                actual_buf = ""
            elif tok.group(4):
                logical += actual if actual is not None else actual_buf
                actual = None
                actual_buf = ""
            elif tok.group(5) is not None:
                for hexstr in re.findall(rb"<([0-9A-Fa-f]+)>", tok.group(5)):
                    raw = bytes.fromhex(hexstr.decode())
                    for i in range(0, len(raw), 2):
                        code = int.from_bytes(raw[i : i + 2], "big")
                        codes.append((cur or "", code))
                        piece = font_maps.get(cur or "", {}).get(code, "�")
                        text += piece
                        if actual is not None:
                            actual_buf += piece
                        else:
                            logical += piece
            elif tok.group(6) is not None:
                rises.add(float(tok.group(6)))
            elif tok.group(7) is not None:
                tms.append((float(tok.group(7)), float(tok.group(8))))
                if x is None:
                    x, y = tms[-1]
        out.append(
            {
                "text": text,
                "logical": logical,
                "codes": codes,
                "x": x,
                "y": y,
                "tms": tms,
                "rises": rises,
                "size": size,
            }
        )
    return out


def paths_and_cms(content: bytes) -> tuple[list[str], list[tuple[float, ...]]]:
    """内容流里的路径操作序列（去掉数值）与所有 `cm` 矩阵。"""
    ops = re.findall(rb"(?<![\w/])([mlchfBSWn]\*?|re)(?=\s|$)", content)
    cms = [tuple(nums(m)) for m in re.findall(rb"((?:" + _NUM + rb"\s+){6})cm", content)]
    return [o.decode() for o in ops], cms


# ---------------------------------------------------------------------------
# PNG（RGBA 8 位）
# ---------------------------------------------------------------------------
def decode_png(data: bytes) -> tuple[int, int, bytes]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("不是 PNG")
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if (depth, ctype) != (8, 6):
                raise AssertionError(f"只认 8 位 RGBA：{(depth, ctype)}")
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    for row in range(h):
        f = raw[row * (stride + 1)]
        line = bytearray(raw[row * (stride + 1) + 1 : (row + 1) * (stride + 1)])
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
