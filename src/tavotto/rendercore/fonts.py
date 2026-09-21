"""字体政策：批准字体的 allowlist、身份与注册表（统一实施包 U06，ADR 0060）。

## 三条硬规则

1. **只认 allowlist 里的字节。** `fonts_allowlist.json`（与本模块同目录，随包分发）
   登记每张脸的 sha256 / 族 / 粗斜 / 字体程序格式 / 许可证 / 来源 URL。注册表
   （`FontRegistry.discover()`）扫字体目录时逐个算 sha256，**不在 allowlist 里的文件
   一律不登记**（记进 `rejected`，理由写明）——把一份 Times New Roman 拷进目录不会
   让它变成可用字体（RC-022）。
2. **身份是字节，不是族名。** `FaceRecord.identity` = `(sha256, face_index)`，IR 的
   `FontResource` 也带 sha256。两个 PostScript 名相同、字节不同的文件是两张脸
   （RC-023）；字形缓存 / 子集缓存的键必须含它。
3. **集合外明示限制。** 找不到某族 / 某样式 → `FontsUnavailable`（结构化 code），
   **不**去摸系统字体、**不**用另一张脸冒充；没有 CJK 脸时 `cjk_face()` 回 None，
   分层把汉字判成 `missing` 进问题系统（RC-037）。

## 字体文件在哪

`fonts_dir()`：`TAVOTTO_FONTS_DIR`（**排他**覆盖：指了就只认它，与
`TAVOTTO_RUNTIME_DIR` 同一条纪律）→ 包内 `tavotto/resources/fonts/`（wheel /
PyInstaller datas 都收它；源码树里由 `scripts/fetch_fonts.py` 按 allowlist 的 sha256
取下来，**不进 git**——`tests/test_font_provenance.py` 继续成立）。

## 本模块自己读的 sfnt 表

注册表只需要四张表就能给出身份与度量：`head`（unitsPerEm）、`OS/2`（sTypoAscender /
sTypoDescender、fsSelection）、`name`（PostScript 名 / 族名）、`cmap`（format 4 / 12 的
码位 → GID，覆盖判据）。这些用 `struct` 就够，所以**覆盖表与身份不需要 fontTools**：
`coverage_ranges()` 在没装候选包的机器上也能算。shaping / 子集才是 `hbshaper` /
`pdfwriter` 的事。

纯标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .ir import FontResource

ALLOWLIST_FILE = "fonts_allowlist.json"
ALLOWLIST_SCHEMA = 1

#: `FontsUnavailable.code` 的闭集。
FONT_ERROR_CODES = (
    "fonts_dir_missing",  # 字体目录不存在（源码树没跑 fetch_fonts / 包装漏了 datas）
    "face_missing",  # 目录里没有这一族 / 这一样式的批准脸
    "allowlist_invalid",  # allowlist 文件本身不合形状
    "font_file_invalid",  # 字节在 allowlist 里，但 sfnt 表读不出来（截断 / 损坏）
)


class FontsUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        assert code in FONT_ERROR_CODES, code
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ApprovedFace:
    face_id: str
    file: str  # 相对字体目录的路径
    sha256: str
    family: str  # serif | sans-serif | monospace | cjk
    bold: bool
    italic: bool
    format: str  # truetype | cff-cid
    license: str
    license_file: str
    source: dict
    notice: str


@dataclass(frozen=True)
class Allowlist:
    faces: dict[str, ApprovedFace]
    license_files: dict[str, dict]
    obligations: tuple[str, ...]

    def by_sha256(self) -> dict[str, ApprovedFace]:
        return {f.sha256: f for f in self.faces.values()}


def allowlist_path() -> Path:
    return Path(__file__).resolve().parent / ALLOWLIST_FILE


def load_allowlist(path: Path | None = None) -> Allowlist:
    p = Path(path) if path else allowlist_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FontsUnavailable("allowlist_invalid", f"{p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != ALLOWLIST_SCHEMA:
        raise FontsUnavailable("allowlist_invalid", f"{p}: schema 不是 {ALLOWLIST_SCHEMA}")
    faces: dict[str, ApprovedFace] = {}
    seen: set[str] = set()
    for face_id, spec in (data.get("faces") or {}).items():
        try:
            face = ApprovedFace(
                face_id=face_id,
                file=str(spec["file"]),
                sha256=str(spec["sha256"]).lower(),
                family=str(spec["family"]),
                bold=bool(spec["bold"]),
                italic=bool(spec["italic"]),
                format=str(spec["format"]),
                license=str(spec["license"]),
                license_file=str(spec["license_file"]),
                source=dict(spec["source"]),
                notice=str(spec["notice"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FontsUnavailable("allowlist_invalid", f"{p}: {face_id}: {exc}") from exc
        if len(face.sha256) != 64 or any(c not in "0123456789abcdef" for c in face.sha256):
            raise FontsUnavailable("allowlist_invalid", f"{p}: {face_id}: sha256 不合形状")
        if face.sha256 in seen:
            raise FontsUnavailable("allowlist_invalid", f"{p}: {face_id}: sha256 重复")
        seen.add(face.sha256)
        faces[face_id] = face
    if not faces:
        raise FontsUnavailable("allowlist_invalid", f"{p}: 没有任何脸")
    licenses = dict(data.get("license_files") or {})
    for rel, spec in licenses.items():
        digest = str(spec.get("file_sha256", "")).lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise FontsUnavailable("allowlist_invalid", f"{p}: 许可证 {rel} 缺 file_sha256")
    for face in faces.values():
        if face.license_file not in licenses:
            raise FontsUnavailable(
                "allowlist_invalid", f"{p}: {face.face_id} 指向没登记的许可证 {face.license_file}"
            )
    return Allowlist(
        faces=faces, license_files=licenses, obligations=tuple(data.get("obligations") or ())
    )


# ---------------------------------------------------------------------------
# 字体目录
# ---------------------------------------------------------------------------
ENV_FONTS_DIR = "TAVOTTO_FONTS_DIR"


def fonts_dir() -> Path:
    """字体目录：环境变量排他覆盖 → 包内 `resources/fonts`。只算路径，不判存在。"""
    override = os.environ.get(ENV_FONTS_DIR)
    if override:
        return Path(override)
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("resources", "fonts")))
        return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        return Path(__file__).resolve().parent.parent / "resources" / "fonts"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# sfnt 读取（只读注册表需要的四张表）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SfntInfo:
    units_per_em: int
    ascender: int  # OS/2 sTypoAscender（字体单位）
    descender: int  # OS/2 sTypoDescender（负）
    postscript_name: str
    family_name: str
    tables: frozenset[str]
    cmap: dict[int, int]  # 码位 → GID（format 12 优先，其次 4）
    num_glyphs: int


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    if len(data) < 12:
        raise ValueError("不是 sfnt：不足 12 字节")
    tag = data[:4]
    if tag == b"ttcf":
        raise ValueError("TrueType Collection 本轮不支持（RC-037）")
    if tag not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise ValueError(f"不是 sfnt：签名 {tag!r}")
    n = struct.unpack(">H", data[4:6])[0]
    out: dict[bytes, tuple[int, int]] = {}
    for i in range(n):
        off = 12 + 16 * i
        if off + 16 > len(data):
            raise ValueError("表目录截断")
        t, _, toff, tlen = struct.unpack(">4sIII", data[off : off + 16])
        if toff + tlen > len(data):
            raise ValueError(f"表 {t!r} 超出文件")
        out[t] = (toff, tlen)
    return out


def _name_table(data: bytes, off: int) -> dict[int, str]:
    count, string_off = struct.unpack(">HH", data[off + 2 : off + 6])
    out: dict[int, str] = {}
    for i in range(count):
        rec = off + 6 + 12 * i
        pid, eid, lang, nid, length, soff = struct.unpack(">HHHHHH", data[rec : rec + 12])
        start = off + string_off + soff
        raw = data[start : start + length]
        if pid == 3 and eid in (0, 1, 10) and lang == 0x409:
            out.setdefault(nid, raw.decode("utf-16-be", errors="replace"))
        elif pid == 1 and eid == 0 and nid not in out:
            out[nid] = raw.decode("mac-roman", errors="replace")
    return out


def _cmap_table(data: bytes, coff: int) -> dict[int, int]:
    n = struct.unpack(">H", data[coff + 2 : coff + 4])[0]
    best4: dict[int, int] = {}
    best12: dict[int, int] = {}
    for i in range(n):
        pid, eid, soff = struct.unpack(">HHI", data[coff + 4 + 8 * i : coff + 12 + 8 * i])
        if pid not in (0, 3):
            continue
        sub = coff + soff
        fmt = struct.unpack(">H", data[sub : sub + 2])[0]
        if fmt == 4 and not best4:
            segx2 = struct.unpack(">H", data[sub + 6 : sub + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(f">{seg}H", data[sub + 14 : sub + 14 + segx2])
            starts = struct.unpack(f">{seg}H", data[sub + 16 + segx2 : sub + 16 + 2 * segx2])
            deltas = struct.unpack(f">{seg}h", data[sub + 16 + 2 * segx2 : sub + 16 + 3 * segx2])
            ro_base = sub + 16 + 3 * segx2
            ros = struct.unpack(f">{seg}H", data[ro_base : ro_base + segx2])
            for k in range(seg):
                if starts[k] == 0xFFFF:
                    continue
                for cp in range(starts[k], ends[k] + 1):
                    if ros[k] == 0:
                        gid = (cp + deltas[k]) & 0xFFFF
                    else:
                        gaddr = ro_base + 2 * k + ros[k] + 2 * (cp - starts[k])
                        gid = struct.unpack(">H", data[gaddr : gaddr + 2])[0]
                        if gid:
                            gid = (gid + deltas[k]) & 0xFFFF
                    if gid:
                        best4.setdefault(cp, gid)
        elif fmt == 12 and not best12:
            ngroups = struct.unpack(">I", data[sub + 12 : sub + 16])[0]
            for g in range(ngroups):
                sc, ec, sg = struct.unpack(">III", data[sub + 16 + 12 * g : sub + 28 + 12 * g])
                for cp in range(sc, ec + 1):
                    best12.setdefault(cp, sg + cp - sc)
    return best12 or best4


def read_sfnt(path: Path) -> SfntInfo:
    """读注册表需要的那几张表。任何一张缺失 / 截断 → `ValueError`（调用方包成
    `font_file_invalid`）。"""
    data = Path(path).read_bytes()
    tables = _tables(data)
    for need in (b"head", b"OS/2", b"name", b"cmap", b"maxp"):
        if need not in tables:
            raise ValueError(f"缺 {need!r} 表")
    hoff = tables[b"head"][0]
    upem = struct.unpack(">H", data[hoff + 18 : hoff + 20])[0]
    if upem == 0:
        raise ValueError("unitsPerEm 为 0")
    ooff = tables[b"OS/2"][0]
    asc, desc = struct.unpack(">hh", data[ooff + 68 : ooff + 72])
    names = _name_table(data, tables[b"name"][0])
    num_glyphs = struct.unpack(">H", data[tables[b"maxp"][0] + 4 : tables[b"maxp"][0] + 6])[0]
    return SfntInfo(
        units_per_em=upem,
        ascender=asc,
        descender=desc,
        postscript_name=names.get(6) or Path(path).stem,
        family_name=names.get(1) or Path(path).stem,
        tables=frozenset(t.decode("latin-1") for t in tables),
        cmap=_cmap_table(data, tables[b"cmap"][0]),
        num_glyphs=num_glyphs,
    )


def font_kind(tables: frozenset[str]) -> str | None:
    """字体程序格式：与写入器的两条路一一对应；其余回 None（显式不支持）。CFF 是否
    CID-keyed 要读 CFF 表，这里只按表名粗分，`hbshaper` 加载时再确认。"""
    if "fvar" in tables or "CFF2" in tables or tables & {"COLR", "SVG ", "CBDT", "sbix"}:
        return None
    if "glyf" in tables:
        return "truetype"
    if "CFF " in tables:
        return "cff-cid"
    return None


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FaceRecord:
    approved: ApprovedFace
    path: Path
    info: SfntInfo
    face_index: int = 0

    @property
    def identity(self) -> tuple[str, int]:
        return (self.approved.sha256, self.face_index)

    @property
    def resource(self) -> FontResource:
        return FontResource(
            face_id=self.approved.face_id,
            sha256=self.approved.sha256,
            family=self.approved.family,
            bold=self.approved.bold,
            italic=self.approved.italic,
            kind=self.approved.format,
            postscript_name=self.info.postscript_name,
            units_per_em=self.info.units_per_em,
            face_index=self.face_index,
        )

    @property
    def ascender(self) -> float:
        return self.info.ascender / self.info.units_per_em

    @property
    def descender(self) -> float:
        return self.info.descender / self.info.units_per_em

    def covers(self, cp: int) -> bool:
        return cp in self.info.cmap


@dataclass
class FontRegistry:
    """字体目录里**核过 sha256** 的脸。`rejected` 是目录里有、但不在 allowlist 里（或读不
    出）的文件——它们不会被当成字体用，只在诊断里列出。"""

    root: Path
    allowlist: Allowlist
    faces: dict[str, FaceRecord] = field(default_factory=dict)  # face_id → record
    rejected: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # allowlist 里有、目录里没有的 face_id

    @classmethod
    def discover(cls, root: Path | None = None, allowlist: Allowlist | None = None) -> FontRegistry:
        root = Path(root) if root is not None else fonts_dir()
        allow = allowlist or load_allowlist()
        reg = cls(root=root, allowlist=allow)
        if not root.is_dir():
            reg.missing = sorted(allow.faces)
            return reg
        by_sha = allow.by_sha256()
        found: dict[str, FaceRecord] = {}
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
                continue
            digest = sha256_of(path)
            approved = by_sha.get(digest)
            if approved is None:
                reg.rejected.append(
                    {
                        "file": str(path.relative_to(root)),
                        "sha256": digest,
                        "reason": "not_in_allowlist",
                    }
                )
                continue
            try:
                info = read_sfnt(path)
            except ValueError as exc:
                reg.rejected.append(
                    {
                        "file": str(path.relative_to(root)),
                        "sha256": digest,
                        "reason": f"unreadable: {exc}",
                    }
                )
                continue
            kind = font_kind(info.tables)
            if kind != approved.format:
                reg.rejected.append(
                    {
                        "file": str(path.relative_to(root)),
                        "sha256": digest,
                        "reason": f"format_mismatch: allowlist 说 {approved.format}，表里是 {kind}",
                    }
                )
                continue
            found.setdefault(approved.face_id, FaceRecord(approved, path, info))
        reg.faces = found
        reg.missing = sorted(set(allow.faces) - set(found))
        return reg

    def record_for(self, family: str, bold: bool = False, italic: bool = False) -> FaceRecord:
        for rec in self.faces.values():
            a = rec.approved
            if (a.family, a.bold, a.italic) == (family, bool(bold), bool(italic)):
                return rec
        if not self.root.is_dir():
            raise FontsUnavailable(
                "fonts_dir_missing",
                f"字体目录 {self.root} 不存在：源码树先跑 scripts/fetch_fonts.py；安装包缺它是包装问题",
            )
        raise FontsUnavailable(
            "face_missing",
            f"批准字体集合里没有 {family} bold={bool(bold)} italic={bool(italic)}"
            f"（目录 {self.root}；缺 {self.missing}）",
        )

    def cjk_record(self) -> FaceRecord | None:
        for rec in self.faces.values():
            if rec.approved.family == "cjk":
                return rec
        return None

    def to_payload(self) -> dict:
        return {
            "root": str(self.root),
            "faces": {
                fid: {
                    "file": str(rec.path.relative_to(self.root)),
                    "sha256": rec.approved.sha256,
                    "postscript_name": rec.info.postscript_name,
                    "units_per_em": rec.info.units_per_em,
                    "glyphs": rec.info.num_glyphs,
                    "license": rec.approved.license,
                }
                for fid, rec in sorted(self.faces.items())
            },
            "missing": list(self.missing),
            "rejected": list(self.rejected),
        }
