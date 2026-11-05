"""用户本机字体：画布文字「用原图那个字体」的底座（ADR 0164，修订 ADR 0060）。

ADR 0060 的批准字体集合（Liberation + Noto Sans SC）仍是**默认**也是**回退**：文档里的
`TextObject.fontFamily` 继续是三个通用族。本模块只管一件新事——一个文字对象可以带一条**可选**的
`source_font`（字体身份：名字 + 文件 sha256 + face 序号 + 粗斜），导出时若本机有这个字节一模一样的
字体文件，就用它排版、子集嵌入；没有，就**保留名字**、用 `fontFamily` 对应的批准脸画，并报问题码
（与图内文字「装不上的字体：保留名字 + warning，绝不换掉再改文档」同一条规则）。

## 不变量（每条都有看护用例，`tests/test_rendercore_userfonts.py`）

1. **只按 sha256 认。** 不按族名猜同名文件（同名不同字节 = 另一张脸，RC-023）。
2. **文档里没有字体文件，也没有路径。** 路径只存在于本机的定位缓存（`data_dir()/font-locator.json`）
   与内存里；不进文档、不进遥测、不进诊断包（见 `docs/rules/backend/diagnostics.md`）。
3. **许可：禁止嵌入的脸不嵌入。** OS/2 `fsType` 的 Restricted License（bit 1）/ 位图嵌入（bit 9）/
   禁止子集（bit 8，而我们只嵌子集）→ 按缺失处理，原因 `embedding_restricted`。
4. **只读、有上限、只认 sfnt 结构。** 文件超过 `MAX_FONT_BYTES` 不读；签名不是 TrueType / OpenType /
   TTC 的不认；`fvar` / `COLR` / `SVG ` / 位图字体与非 CID 的 CFF 本轮显式不支持（与批准字体同一条边界）。
5. **失败不改文档、不换脸冒充。** 本模块只回答「这张脸能不能用、不能的原因」，回退由调用方（`plan`）做，
   并留下问题码。

纯标准库（native 包只在 `hbshaper` 里）。
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

from . import fonts
from .sourcefont import (  # noqa: F401
    HEX64 as _HEX64,
    REASONS,
    FaceRef,
    SourceFont,
    UserFontUnavailable,
    parse_source_font,
)

#: 单个字体文件的读取上限。CJK 大字库（`.ttc`）几十 MB 是常态；再大的不是字体，是别的东西。
MAX_FONT_BYTES = 192 * 1024 * 1024

#: 定位时走多深 / 看多少个文件——字体目录不该深，上限防的是用户把整盘当字体目录。
SCAN_MAX_DEPTH = 6
SCAN_MAX_FILES = 40000

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")

#: fsType 里「不许嵌入」的位。位 1 = Restricted License；位 8 = No subsetting（我们只嵌子集）；
#: 位 9 = Bitmap embedding only（嵌不了轮廓）。位 2（Preview & Print）与位 3（Editable）允许在
#: 只读文档里嵌入——PDF 的文字不是「可编辑嵌入」，OFL 之外的商用字体多是这一档。
FS_TYPE_REFUSE_MASK = 0x0002 | 0x0100 | 0x0200


# ---------------------------------------------------------------------------
# 入场检查：这张脸能不能用
# ---------------------------------------------------------------------------
def embedding_refusal(fs_type: int) -> str | None:
    """OS/2 fsType → 不许嵌入的原因（人读，进日志）；允许 → None。"""
    if fs_type & 0x0002:
        return "Restricted License（fsType bit 1）"
    if fs_type & 0x0200:
        return "Bitmap embedding only（fsType bit 9）"
    if fs_type & 0x0100:
        return "No subsetting（fsType bit 8）：我们只嵌子集"
    return None


def admit(ref: FaceRef, *, locator: FontLocator | None = None) -> fonts.FaceRecord:
    """sha256 → 本机文件 → 核对 → 可排版的记录；任何一环不过抛 `UserFontUnavailable`。

    核对包括：大小上限、**重算 sha256 与引用逐字相同**（定位缓存陈旧 / 文件被换过都在这里被拦）、
    sfnt 结构、字体程序格式、cmap 可读、嵌入许可。"""
    loc = locator or default_locator()
    path = loc.locate(ref.sha256)
    if path is None:
        raise UserFontUnavailable("not_found")
    try:
        # 用到它的这一刻**读一次字节**：大小上限、sha256、sfnt 解析、之后的排版与子集化都用这同一份字节
        # （不信索引里的 (size, mtime) 快照；检查与使用之间文件被换也无从插入）。读完即关，进程不留句柄
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            if st.st_size > MAX_FONT_BYTES:
                raise UserFontUnavailable("too_large", f"{path}: {st.st_size}")
            data = fh.read(MAX_FONT_BYTES + 1)
        if len(data) > MAX_FONT_BYTES:
            raise UserFontUnavailable("too_large", f"{path}: >{MAX_FONT_BYTES}")
        digest = hashlib.sha256(data).hexdigest()
        loc.note_hash(path, st.st_size, st.st_mtime_ns, digest)
    except OSError as exc:
        raise UserFontUnavailable("unreadable", f"{path}: {exc}") from exc
    if digest != ref.sha256:  # 定位缓存说是它，字节已经不是了
        loc.forget(path)
        raise UserFontUnavailable("not_found", f"{path}: 字节已变")
    try:
        info = fonts.read_sfnt(path, ref.face, data)
    except (ValueError, OSError, struct.error) as exc:
        raise UserFontUnavailable("unreadable", f"{path}: {exc}") from exc
    kind = fonts.font_kind(info.tables)
    if kind is None:
        raise UserFontUnavailable("unsupported_format", f"{path}: 表 {sorted(info.tables)[:12]}")
    if not info.cmap:
        raise UserFontUnavailable(
            "unsupported_format", f"{path}: cmap 读不出（只认 format 4 / 12）"
        )
    why = embedding_refusal(info.fs_type)
    if why is not None:
        raise UserFontUnavailable("embedding_restricted", f"{path}: {why}")
    bold = bool(info.fs_selection & 0x20) or bool(info.mac_style & 0x1)
    italic = bool(info.fs_selection & 0x01) or bool(info.mac_style & 0x2)
    approved = fonts.ApprovedFace(
        face_id=f"user:{ref.sha256[:12]}:{ref.face}",
        file=path.name,
        sha256=ref.sha256,
        family="user",  # 不在批准族闭集里：`record_for` 永远不会把它当成某个族
        bold=bold,
        italic=italic,
        format=kind,
        license="user-installed",
        license_file="",
        source={},
        notice="",
    )
    return fonts.FaceRecord(approved, path, info, face_index=ref.face, data=data)


# ---------------------------------------------------------------------------
# 定位：sha256 → 本机文件
# ---------------------------------------------------------------------------
CACHE_FILE = "font-locator.json"
CACHE_SCHEMA = 1


def system_font_dirs() -> list[Path]:
    """本机的系统 / 用户字体目录（只列，不判存在）。**Windows 与 Linux 的分支未在 macOS 上实测**——
    见 ADR 0164 §验证与 Windows Lab 清单。"""
    home = Path.home()
    out: list[Path] = []
    if sys.platform == "darwin":
        out += [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            home / "Library" / "Fonts",
        ]
    elif os.name == "nt":
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        out.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        out += [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home / ".fonts",
            (Path(xdg) if xdg else home / ".local" / "share") / "fonts",
        ]
    return out


def matplotlib_font_dirs(python: str | os.PathLike | None = None) -> list[Path]:
    """某个解释器的 matplotlib 自带字体目录（DejaVu 等），**只算路径、不 import、不执行**。

    原图最常见的字体就是 DejaVu Sans，它不在任何系统目录里，而在用户环境的
    `site-packages/matplotlib/mpl-data/fonts/ttf`。给了 `python` 就从它的 venv / 前缀往下找；
    另外总是带上本进程自己能找到的那份（开发机 / 装了 matplotlib 的环境）。"""
    out: list[Path] = []
    roots: list[Path] = []
    if python:
        p = Path(python)
        for cand in (p, Path(os.path.realpath(p))):
            roots += [cand.parent, cand.parent.parent]
    for root in dict.fromkeys(roots):
        for pattern in (
            "lib/python*/site-packages/matplotlib/mpl-data/fonts/ttf",
            "Lib/site-packages/matplotlib/mpl-data/fonts/ttf",
            "site-packages/matplotlib/mpl-data/fonts/ttf",
        ):
            try:
                out += [d for d in root.glob(pattern) if d.is_dir()]
            except OSError:
                continue
    try:
        import importlib.util

        spec = importlib.util.find_spec("matplotlib")
        if spec and spec.origin:
            d = Path(spec.origin).parent / "mpl-data" / "fonts" / "ttf"
            if d.is_dir():
                out.append(d)
    except (ImportError, ValueError, AttributeError):
        pass
    return list(dict.fromkeys(out))


def approved_font_dir() -> Path:
    """批准字体所在目录：app 层登记定位线索用（HTTP 层不 import `fonts`，只认这一个路径）。"""
    return fonts.fonts_dir()


class FontLocator:
    """sha256 → 本机字体文件。

    * **索引**：`path → (size, mtime_ns, sha256)`，持久化在 `cache_path`（`data_dir()` 下），
      只存本机路径、不出机器。文件的 (size, mtime_ns) 没变就不重算 sha256。
    * **查找**：先查索引（并核 stat），再扫 `hint_dirs`（调用方给的：原图所在解释器的 matplotlib 字体目录、
      项目目录）与系统字体目录；扫到谁算谁的 sha256，命中即返回。
    * **只按 sha256 认**：族名 / 文件名从不参与。
    """

    def __init__(self, cache_path: Path | None = None, system_dirs=None) -> None:
        self._cache_path = cache_path
        self._system_dirs = system_dirs or system_font_dirs
        self._lock = threading.RLock()
        self._index: dict[str, tuple[int, int, str]] = {}
        self._loaded = False
        self._dirty = False
        self._hints: dict[str, None] = {}
        #: 刚扫过一遍还没找到的 sha256 → 时刻；短时间内不重扫（用户装了字体后几秒内就会再试）
        self._negative: dict[str, float] = {}

    NEGATIVE_TTL_S = 20.0

    # -- 持久化 ---------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._cache_path is None:
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if data.get("schema") == CACHE_SCHEMA:
                for path, ent in (data.get("files") or {}).items():
                    size, mtime, sha = ent
                    if isinstance(path, str) and isinstance(sha, str) and _HEX64.match(sha):
                        self._index[path] = (int(size), int(mtime), sha)
        except (OSError, ValueError, TypeError):
            self._index = {}

    def flush(self) -> None:
        with self._lock:
            if not self._dirty or self._cache_path is None:
                return
            try:
                from ..engine import atomicio

                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(
                    {"schema": CACHE_SCHEMA, "files": {k: list(v) for k, v in self._index.items()}},
                    ensure_ascii=False,
                )
                atomicio.write_bytes(self._cache_path, payload.encode("utf-8"))
                self._dirty = False
            except Exception:  # noqa: BLE001 —— 缓存写不出去只是慢一点，不是错误
                pass

    # -- 哈希 -----------------------------------------------------------------
    def hash_of(self, path: Path) -> str:
        """文件的 sha256（(size, mtime_ns) 没变就用索引里的）。读不出抛 `OSError`。"""
        with self._lock:
            self._load()
            st = path.stat()
            key = str(path)
            hit = self._index.get(key)
            if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
                return hit[2]
        digest = fonts.sha256_of(path)
        with self._lock:
            self._index[str(path)] = (st.st_size, st.st_mtime_ns, digest)
            self._dirty = True
        return digest

    def rehash(self, path: Path) -> str:
        """无视索引、现读字节算 sha256，并把结果写回索引。读不出抛 `OSError`。"""
        st = path.stat()
        digest = fonts.sha256_of(path)
        with self._lock:
            self._load()
            self._index[str(path)] = (st.st_size, st.st_mtime_ns, digest)
            self._dirty = True
        return digest

    def note_hash(self, path: Path, size: int, mtime_ns: int, digest: str) -> None:
        """调用方已读过整份字节算出 sha256：写回索引（省得再读一遍）。"""
        with self._lock:
            self._load()
            self._index[str(path)] = (size, mtime_ns, digest)
            self._dirty = True

    def forget(self, path: Path) -> None:
        with self._lock:
            if self._index.pop(str(path), None) is not None:
                self._dirty = True

    # -- 查找 -----------------------------------------------------------------
    def add_hint_dirs(self, dirs) -> None:
        with self._lock:
            for d in dirs:
                self._hints.setdefault(str(d), None)

    def hint_dirs(self) -> list[str]:
        with self._lock:
            return list(self._hints)

    def walk(self, root: Path):
        count = 0
        base_depth = len(root.parts)
        try:
            for cur, subdirs, files in os.walk(root, followlinks=False):
                if len(Path(cur).parts) - base_depth >= SCAN_MAX_DEPTH:
                    subdirs[:] = []
                subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
                for name in sorted(files):
                    if name.lower().endswith(FONT_SUFFIXES):
                        count += 1
                        if count > SCAN_MAX_FILES:
                            return
                        yield Path(cur) / name
        except OSError:
            return

    def locate(self, sha256: str, extra_dirs=()) -> Path | None:
        """本机上**字节就是这个 sha256**的字体文件；没有 → None。不读超过上限的文件。"""
        if not _HEX64.match(sha256 or ""):
            return None
        with self._lock:
            self._load()
            for path, (size, mtime, sha) in list(self._index.items()):
                if sha != sha256:
                    continue
                try:
                    st = Path(path).stat()
                except OSError:
                    del self._index[path]
                    self._dirty = True
                    continue
                if st.st_size == size and st.st_mtime_ns == mtime:
                    return Path(path)
                del self._index[path]  # 文件被换过：这条不算数，往下重扫
                self._dirty = True
            if (
                self._negative.get(sha256, 0) > time.monotonic() - self.NEGATIVE_TTL_S
                and not extra_dirs
            ):
                return None
            roots = [*map(Path, extra_dirs), *map(Path, self._hints), *self._system_dirs()]
        found: Path | None = None
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in self.walk(root):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size = path.stat().st_size
                    if size > MAX_FONT_BYTES:
                        continue
                    if self.hash_of(path) == sha256:
                        found = path
                        break
                except OSError:
                    continue
            if found is not None:
                break
        with self._lock:
            if found is None:
                self._negative[sha256] = time.monotonic()
            else:
                self._negative.pop(sha256, None)
        self.flush()
        return found


_LOCATOR: FontLocator | None = None
_LOCATOR_LOCK = threading.Lock()


def default_locator() -> FontLocator:
    """进程级共享的定位器；缓存文件在 `config.data_dir()`（不往包目录写，ADR 0120 同一条）。"""
    global _LOCATOR
    with _LOCATOR_LOCK:
        if _LOCATOR is None:
            try:
                from ..engine import config

                cache = config.data_dir() / CACHE_FILE
            except Exception:  # noqa: BLE001
                cache = None
            _LOCATOR = FontLocator(cache)
        return _LOCATOR


def reset_for_tests() -> None:
    global _LOCATOR
    with _LOCATOR_LOCK:
        _LOCATOR = None
