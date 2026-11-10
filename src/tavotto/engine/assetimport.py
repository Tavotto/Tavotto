"""把一个文件作为**素材**放进当前项目文件夹（ADR 0176：拖 / 粘贴文件进画布）。

只管「字节 → 项目里的一个素材文件」这一步的纯规则：类型按**内容**判、文件名清洗、同名处置、
不覆盖地落盘。「这个文件能不能成为素材并显示出来」的最终判据仍是素材扫描自己的探针
（`app._asset_probe`，与 `scan_panels` 同一份）——这里不另写一套。纯标准库（Flask 父进程一侧）。

不变式：
  * **只写项目根**（调用方给的目录），文件名只取 basename 并清洗；客户端永远不指定目录。
  * **绝不覆盖用户已有的文件**：同名同内容复用已有那份，同名异内容改名 `名 (2).ext`；
    还要避开「同一张图」的矢量让位规则（同 stem 已有 PDF 时位图会被素材扫描藏起来）。
  * **绝不出现「文件复制进去了、素材库里却没有」**：落盘前先在隐藏临时文件上验证，落盘后再核一遍它真在素材清单里。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import atomicio, exportreq

#: 单个文件上限。依据：整份字节在内存里过一遍（验证 + 哈希 + 写盘），所以要有界；比联系作者附件的
#: 50 MiB 宽一倍，因为期刊图常见 600–1200 dpi 的 TIFF（双栏宽 1200 dpi 灰度约 70 MB）；再大的位图
#: 也过不了渲染后端的像素预算（`raster_too_large`），放进来只会变成一张画不出来的素材。
MAX_BYTES = 100 * 1024 * 1024

#: 内容类型 → 允许的扩展名（第一个是缺省）。**与 `engine/project_refresh` 的素材扩展名同一份**，
#: 由 `tests/test_asset_import.py` 对账（素材扫描认的位图 / PDF / SVG 全在，且只有它们）。
EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "svg": (".svg",),
    "png": (".png",),
    "jpeg": (".jpg", ".jpeg"),
    "tiff": (".tif", ".tiff"),
}

#: 同一把锁串行化「挑名字 → 落盘」：并发的两次导入不会挑中同一个名字
LOCK = threading.Lock()

_ASSET_SUFFIXES = frozenset(e for exts in EXTENSIONS.values() for e in exts)
_FALLBACK_STEM = "image"
#: 基名上限（字符）与 UTF-8 字节上限：留出 ` (999)` 与扩展名，也不让中文名把 255 字节的文件名上限撑破
_STEM_CHARS = 100
_STEM_BYTES = 180
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SUFFIX_N = re.compile(r" \((\d+)\)$")


#: 本模块与 `POST /api/assets/import` 会发出的**全部**错误码及其中文原文（`error` 字段的回退；文案归前端 errors.json）。
#: `tests/test_error_codes.py` 读 `ERROR_CODES` 要求两种语言都有文案。
ERRORS = {
    "asset_import_too_large": "文件太大，放不进项目",
    "asset_import_unsupported_type": "这不是能放进画布的图片（支持 PNG / JPG / TIFF / PDF / SVG）",
    "asset_import_unreadable": "这个文件打不开，可能已损坏或设了密码",
    "asset_import_empty": "文件是空的",
    "asset_import_not_writable": "项目文件夹不允许写入",
    "asset_import_disk_full": "磁盘空间不够",
    "asset_import_write_failed": "写入项目文件夹时出错",
    "asset_import_not_visible": "放进项目文件夹后没能显示出来，已撤回",
    "asset_import_name_exhausted": "项目里同名的文件太多了",
}
ERROR_CODES = tuple(ERRORS)


class ImportRefused(ValueError):
    """`choose` 挑不出名字。`code` 是稳定码，文案归前端。"""

    def __init__(self, code: str, status: int, params: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params or {}


def sniff(data: bytes) -> str | None:
    """按**内容**（魔数 / 文本形状）判类型：`pdf` / `svg` / `png` / `jpeg` / `tiff` / None。

    只是粗判，决定「这是什么」；它能不能显示由素材扫描的探针说了算。后缀不参与：改了后缀的东西、
    没有后缀的剪贴板图片都按内容走。
    """
    head = data[:2048]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if b"%PDF-" in head[:1024]:
        return "pdf"
    text = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if text.startswith(b"<") and b"<svg" in data[:8192]:
        return "svg"
    return None


def clean_name(raw: str) -> tuple[str, str]:
    """客户端给的名字 → `(基名, 它自带的图片扩展名或 "")`。

    只取 basename（`/` 与 `\\` 两种分隔都认），NFC 归一，非法 / 控制字符换成 `_`，首尾空白与点去掉
    （隐藏文件不是素材，Windows 会吃掉尾点），Windows 保留设备名前缀 `_`，过长截断；清洗后仍过不了
    `exportreq.check_filename`（与导出文件名同一道判据）就回退 `image`。
    """
    name = unicodedata.normalize("NFC", raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = ""
    low = name.lower()
    for ext in sorted(_ASSET_SUFFIXES, key=len, reverse=True):
        if low.endswith(ext) and len(name) > len(ext):
            suffix, name = ext, name[: -len(ext)]
            break
    name = _ILLEGAL.sub("_", name).strip(" .\t\r\n 　")
    name = name.lstrip(".")
    if len(name) > _STEM_CHARS:
        name = name[:_STEM_CHARS]
    while len(name.encode("utf-8")) > _STEM_BYTES:
        name = name[:-1]
    name = name.strip(" .")
    if not name:
        name = _FALLBACK_STEM
    if exportreq.check_filename(name) == "reserved_name":
        name = "_" + name
    if exportreq.check_filename(name) is not None:
        name = _FALLBACK_STEM
    return name, suffix


def extension_for(kind: str, given: str) -> str:
    """内容类型决定扩展名；用户给的扩展名是同一类型的别写法（`.jpeg` / `.tiff`）才沿用"""
    allowed = EXTENSIONS[kind]
    return given if given in allowed else allowed[0]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stem_key(stem: str) -> str:
    return unicodedata.normalize("NFC", stem).casefold()


@dataclass(frozen=True)
class Placement:
    """挑好的落点：`reuse` 非空 = 已有一份内容相同的，不写第二份"""

    name: str
    reuse: Path | None = None


def choose(
    directory: Path, stem: str, ext: str, digest: str, size: int, *, allow_reuse: bool = True
) -> Placement:
    """在 `directory`（只看这一层）里为 `stem + ext` 挑落点。**调用方须持有 `LOCK`**。

    1. 已有同基名（或 `基名 (n)`）、同扩展名、**同内容**的文件 → 复用它（用户把同一张图拖了两次）；
    2. 否则取第一个「基名没被任何素材占用」的名字：`stem`、`stem (2)`、`stem (3)`…
       「占用」= 同层有同基名（不分大小写、NFC）的任一素材扩展名的文件——这同时避开两件事：
       覆盖 / 撞名（大小写不敏感的文件系统上 `A.png` 与 `a.png` 是同一个文件），以及与已有 PDF / 位图
       同 stem 导致其中一份被素材扫描让位藏起来。
    """
    try:
        names = [e.name for e in os.scandir(directory) if e.is_file()]
    except OSError:
        names = []
    key = _stem_key(stem)
    taken: set[str] = set()
    for name in sorted(names):
        p = Path(name)
        if p.suffix.lower() not in _ASSET_SUFFIXES:
            continue
        taken.add(_stem_key(p.stem))
        # 复用：基名一致（含 ` (n)` 后缀的同族）、扩展名一致、大小一致、哈希一致
        if not allow_reuse or p.suffix.lower() != ext.lower():
            continue
        if _SUFFIX_N.sub("", _stem_key(p.stem)) != key:
            continue
        try:
            same = (directory / name).stat().st_size == size and _digest(directory / name) == digest
        except OSError:
            continue
        if same:
            return Placement(name=name, reuse=directory / name)
    candidate, n = stem, 1
    while _stem_key(candidate) in taken:
        n += 1
        if n > 9999:
            raise ImportRefused("asset_import_name_exhausted", 409)
        candidate = f"{stem} ({n})"
    return Placement(name=candidate + ext)


def temp_path(directory: Path, ext: str) -> Path:
    """验证用的隐藏临时文件（素材扫描不认隐藏文件）；扩展名是真的，探针按扩展名挑解码器"""
    return directory / f".tavotto-import-{secrets.token_hex(6)}{ext}"


def write_temp(path: Path, data: bytes) -> None:
    """原子写临时文件（`atomicio` 是父进程侧唯一的落盘实现）"""
    atomicio.write_bytes(path, data)


def place_exclusive(tmp: Path, dest: Path) -> None:
    """把临时文件放到最终名字上，**目标已存在就失败**（`FileExistsError`），绝不覆盖。

    首选硬链接再删临时名（`link` 在目标存在时由内核拒绝，没有「查了再放」的缝）；文件系统不支持硬链接
    （FAT / 部分网络盘）时退到「持锁查一次再 `atomicio.publish_file`」——同一进程内不会撞，与外部进程
    同一毫秒撞同名的窗口只剩这一条退路。
    """
    try:
        os.link(tmp, dest)
    except FileExistsError:
        raise
    except OSError:
        if dest.exists():
            raise FileExistsError(str(dest)) from None
        atomicio.publish_file(tmp, dest)
        return
    discard(tmp)


def discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
