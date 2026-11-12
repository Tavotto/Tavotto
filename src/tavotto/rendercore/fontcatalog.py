"""这台机器上画布文字能用的字体清单（ADR 0169，建在 ADR 0164 的本机字体通道上）。

清单只回答「有哪些脸可以点名」：每张脸给出身份（名字 + sha256 + 面序号 + 粗斜），文档与导出走的仍是
`source_font` 那一条路，`userfonts.admit` 在用到的那一刻照旧重新核一遍。所以这里判错只会多一行或少一行选项，
不会让一张不许嵌入的脸进 PDF。

判据与 `admit` 对齐：同一个 `fonts.font_kind`、同一个 `embedding_refusal`、同一个大小上限。列不出来的脸
（变量字体、彩色字体、非 CID 的 CFF、禁止嵌入）不进清单：选了也嵌不进去，不如不给。

**响应里没有路径**。路径只在本机缓存 `font-catalog.json`（`data_dir()` 下，与 `font-locator.json` 同一条：
不进文档、不进诊断包、不进遥测）。
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import fonts
from .userfonts import (
    MAX_FONT_BYTES,
    FontLocator,
    default_locator,
    embedding_refusal,
    system_font_dirs,
)

CATALOG_FILE = "font-catalog.json"
CATALOG_SCHEMA = 1

#: 同一进程里隔多久才重新走一遍字体目录（用户装了新字体，下次打开下拉就有）
RESCAN_AFTER_S = 60.0

#: 这几个常用字都有，才算「自带中文字形」
CJK_PROBE = (0x4E00, 0x5B57, 0x56FE)

#: 三类通用族（`TextObject.fontFamily`）→ 批准脸的族名。中文格要配批准的英文脸时用
BUILTIN_FAMILIES = {
    "serif": "Liberation Serif",
    "sans-serif": "Liberation Sans",
    "monospace": "Liberation Mono",
}

_ZH_LANGS = (0x0804, 0x0404, 0x0C04, 0x1004, 0x1404)
_MAC_ZH = {33: "gb18030", 19: "big5"}


@dataclass(frozen=True)
class _Face:
    """一张能用的脸。`family` 是 name 表 1 号名字（RIBBI 模型：一族最多四张，「Arial Narrow」是另一族）。"""

    family: str
    zh: str | None
    sha256: str
    face: int
    bold: bool
    italic: bool
    cjk: bool
    generic: str | None

    def to_json(self) -> list:
        return [
            self.family,
            self.zh,
            self.sha256,
            self.face,
            self.bold,
            self.italic,
            self.cjk,
            self.generic,
        ]

    @classmethod
    def from_json(cls, v: list) -> "_Face":
        return cls(str(v[0]), v[1], str(v[2]), int(v[3]), bool(v[4]), bool(v[5]), bool(v[6]), v[7])


def _face_count(data: bytes) -> int:
    if data[:4] == b"ttcf" and len(data) >= 12:
        return struct.unpack(">I", data[8:12])[0]
    return 1


def _zh_name(data: bytes, off: int) -> str | None:
    """name 表里的中文族名（简体优先）。没有 → None，下拉就显示英文名。"""
    count, string_off = struct.unpack(">HH", data[off + 2 : off + 6])
    found: dict[int, str] = {}
    for i in range(count):
        rec = off + 6 + 12 * i
        pid, eid, lang, nid, length, soff = struct.unpack(">HHHHHH", data[rec : rec + 12])
        if nid != 1:
            continue
        raw = data[off + string_off + soff : off + string_off + soff + length]
        if pid == 3 and eid in (0, 1, 10) and lang in _ZH_LANGS:
            found[lang] = raw.decode("utf-16-be", errors="replace")
        elif pid == 1 and lang in _MAC_ZH:
            found[0x0804 if lang == 33 else 0x0404] = raw.decode(_MAC_ZH[lang], errors="replace")
    for lang in _ZH_LANGS:
        name = found.get(lang, "").strip("\x00 ")
        if name:
            return name
    return None


def _generic_of(data: bytes, tables: dict[bytes, tuple[int, int]]) -> str | None:
    """衬线 / 无衬线 / 等宽：这张脸在别的机器上缺失时回退到哪个批准族。说不准 → None（保持对象原来的族）。"""
    post = tables.get(b"post")
    if post and struct.unpack(">I", data[post[0] + 12 : post[0] + 16])[0]:
        return "monospace"
    ooff = tables[b"OS/2"][0]
    panose = data[ooff + 32 : ooff + 42]
    if panose[:1] == b"\x02":
        if panose[3] == 9:
            return "monospace"
        if 2 <= panose[1] <= 10:
            return "serif"
        if 11 <= panose[1] <= 15:
            return "sans-serif"
    family_class = data[ooff + 30]
    if family_class in (1, 2, 3, 4, 5, 7):
        return "serif"
    if family_class == 8:
        return "sans-serif"
    return None


def _cff_is_cid(data: bytes, face: int) -> bool:
    """写入器只有 CFF CID 一条 CFF 路（`font_kind` 只按表名粗分）；非 CID 的 CFF 嵌不进去。"""
    import io

    from fontTools.ttLib import TTFont

    try:
        tt = TTFont(io.BytesIO(data), fontNumber=face, lazy=True)
        return hasattr(tt["CFF "].cff.topDictIndex[0], "ROS")
    except Exception:  # noqa: BLE001 —— 读不出来就是用不了
        return False


def faces_in(path: Path, data: bytes, sha256: str) -> list[_Face]:
    """一个字体文件里能用的脸；判据与 `userfonts.admit` 同一组函数。"""
    out: list[_Face] = []
    for i in range(_face_count(data)):
        try:
            info = fonts.read_sfnt(path, i, data)
            tables = fonts._tables(data, i)  # noqa: SLF001 —— 同一子包里的表目录，name / OS/2 / post 要再读
            kind = fonts.font_kind(info.tables)
            if kind is None or not info.cmap or embedding_refusal(info.fs_type) is not None:
                continue
            if kind == "cff-cid" and not _cff_is_cid(data, i):
                continue
            family = info.family_name.strip("\x00 ")
            if not family or family.startswith("."):  # 点开头是系统私有字体（macOS）
                continue
            out.append(
                _Face(
                    family=family,
                    zh=_zh_name(data, tables[b"name"][0]),
                    sha256=sha256,
                    face=i,
                    bold=bool(info.fs_selection & 0x20) or bool(info.mac_style & 0x1),
                    italic=bool(info.fs_selection & 0x01) or bool(info.mac_style & 0x2),
                    cjk=all(cp in info.cmap for cp in CJK_PROBE),
                    generic=_generic_of(data, tables),
                )
            )
        except (ValueError, OSError, struct.error, IndexError):
            continue
    return out


def _family_rows(faces: list[_Face]) -> list[dict]:
    """脸 → 下拉一行一族。同族同粗斜先到先得（系统目录排在前面）。"""
    rows: dict[str, dict] = {}
    seen: set[tuple[str, bool, bool]] = set()
    for f in faces:
        key = (f.family.casefold(), f.bold, f.italic)
        if key in seen:
            continue
        seen.add(key)
        row = rows.setdefault(
            f.family.casefold(),
            {"name": f.family, "zh": None, "cjk": False, "generic": None, "faces": []},
        )
        row["zh"] = row["zh"] or f.zh
        row["cjk"] = row["cjk"] or f.cjk
        row["generic"] = row["generic"] or f.generic
        row["faces"].append(
            {"sha256": f.sha256, "face": f.face, "bold": f.bold, "italic": f.italic}
        )
    return sorted(rows.values(), key=lambda r: (r["zh"] is None, r["name"].casefold()))


def builtin_rows() -> dict[str, dict]:
    """批准的三族，按通用族索引：英文留在内置字体、中文选本机字体时，`source_font` 的正文脸点名它们。"""
    out: dict[str, dict] = {}
    for face in fonts.load_allowlist().faces.values():
        name = BUILTIN_FAMILIES.get(face.family)
        if name is None:
            continue
        row = out.setdefault(face.family, {"name": name, "faces": []})
        row["faces"].append(
            {"sha256": face.sha256, "face": 0, "bold": face.bold, "italic": face.italic}
        )
    return out


class FontCatalog:
    """本机字体清单：缓存按 (size, mtime_ns) 复用，后台线程走目录，请求永远不等扫描。"""

    def __init__(self, cache_path: Path | None = None, locator: FontLocator | None = None) -> None:
        self._cache_path = cache_path
        self._locator = locator
        self._lock = threading.Lock()
        self._files: dict[str, tuple[int, int, list[_Face]]] = {}
        self._order: list[str] = []
        self._loaded = False
        self._scanning = False
        self._scanned_at: float | None = None

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._cache_path is None:
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if data.get("schema") == CATALOG_SCHEMA:
                for path, (size, mtime, faces) in (data.get("files") or {}).items():
                    self._files[path] = (int(size), int(mtime), [_Face.from_json(v) for v in faces])
                self._order = list(self._files)
        except (OSError, ValueError, TypeError, IndexError):
            self._files = {}
            self._order = []

    def _flush(self) -> None:
        if self._cache_path is None:
            return
        try:
            from ..engine import atomicio

            payload = {
                "schema": CATALOG_SCHEMA,
                "files": {
                    p: [s, m, [f.to_json() for f in faces]]
                    for p, (s, m, faces) in self._files.items()
                },
            }
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomicio.write_bytes(
                self._cache_path, json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
        except Exception:  # noqa: BLE001 —— 缓存写不出去只是下次慢一点
            pass

    def scan(self) -> None:
        """走一遍字体目录，更新清单。文件没变就不重读。系统目录排在线索目录前面。"""
        loc = self._locator or default_locator()
        roots = [*system_font_dirs(), *map(Path, loc.hint_dirs())]
        fresh: dict[str, tuple[int, int, list[_Face]]] = {}
        with self._lock:
            old = dict(self._files)
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in loc.walk(root):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    st = path.stat()
                    if st.st_size > MAX_FONT_BYTES:
                        continue
                    hit = old.get(key)
                    if hit is not None and hit[:2] == (st.st_size, st.st_mtime_ns):
                        fresh[key] = hit
                        continue
                    data = path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    loc.note_hash(path, st.st_size, st.st_mtime_ns, digest)
                    fresh[key] = (st.st_size, st.st_mtime_ns, faces_in(path, data, digest))
                except OSError:
                    continue
        with self._lock:
            self._files = fresh
            self._order = list(fresh)
            self._scanned_at = time.monotonic()
            self._flush()
        loc.flush()

    def _scan_in_background(self) -> None:
        try:
            self.scan()
        finally:
            with self._lock:
                self._scanning = False
                self._scanned_at = self._scanned_at or time.monotonic()

    def snapshot(self) -> dict:
        """当前清单。`state` 是 `scanning` 时清单可能不全，调用方过一会儿再问。"""
        with self._lock:
            self._load()
            stale = self._scanned_at is None or time.monotonic() - self._scanned_at > RESCAN_AFTER_S
            if stale and not self._scanning:
                self._scanning = True
                threading.Thread(
                    target=self._scan_in_background, name="font-catalog", daemon=True
                ).start()
            scanning = self._scanning
            faces = [f for p in self._order for f in self._files[p][2]]
        approved = set(fonts.load_allowlist().by_sha256())
        return {
            "state": "scanning" if scanning else "ready",
            "families": _family_rows([f for f in faces if f.sha256 not in approved]),
            "builtin": builtin_rows(),
        }


_CATALOG: FontCatalog | None = None
_CATALOG_LOCK = threading.Lock()


def default_catalog() -> FontCatalog:
    global _CATALOG
    with _CATALOG_LOCK:
        if _CATALOG is None:
            try:
                from ..engine import config

                cache = config.data_dir() / CATALOG_FILE
            except Exception:  # noqa: BLE001
                cache = None
            _CATALOG = FontCatalog(cache)
        return _CATALOG


def reset_for_tests() -> None:
    global _CATALOG
    with _CATALOG_LOCK:
        _CATALOG = None
