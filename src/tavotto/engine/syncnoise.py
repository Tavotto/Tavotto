"""同步盘 / 系统自己写出来的文件：项目指纹不看它们，并认得项目是否放在同步盘里（ADR 0143 修订）。

“校验运行没改动项目”的复核比的是项目文件指纹。项目放在坚果云 / Dropbox / OneDrive / iCloud 这类同步盘里时，
同步客户端自己会在文件夹里创建、改名、改 mtime 它的临时文件与冲突副本，系统也会写 `.DS_Store`——这些不是校验运行
写的，把它们算进去会把一次干净的校验误判成“动了项目文件”。

**闭集**（只收明显是同步客户端 / 系统写的名字，宁缺毋滥；每一条都带来源）：
* 只按**文件名**（basename）判，不看目录层级；大小写不敏感的只有标明的几条。
* 刻意**不收**裸 `*.tmp`：用户脚本自己写的 `result.tmp` 是真实的项目改动。只收带客户端前缀的临时名。
纯标准库。
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

#: 精确名字（casefold 后比）。目录名与文件名共用这一张表。
_EXACT = frozenset(
    {
        # macOS / Finder
        ".ds_store",
        "icon\r",  # 自定义文件夹图标，名字里真有一个回车
        ".localized",
        ".apdisk",
        ".spotlight-v100",
        ".trashes",
        ".fseventsd",
        ".temporaryitems",
        ".documentrevisions-v100",
        # Windows
        "thumbs.db",
        "desktop.ini",
        # Dropbox
        ".dropbox",
        ".dropbox.attr",
        ".dropbox.cache",
        ".dropbox.device",
        # Google Drive for desktop
        ".tmp.driveupload",
        ".tmp.drivedownload",
        # Syncthing
        ".stfolder",
        ".stignore",
        ".stversions",
        # OneDrive 在文件夹根放的隐藏标记
        ".849c9593-d756-4e56-8d6e-42412f2a707b",
    }
)

#: fnmatch 模式（对 casefold 后的名字）。
_PATTERNS = (
    "._*",  # AppleDouble：非 HFS 卷 / 网络盘上 macOS 为扩展属性写的伴生文件
    "~$*",  # Office 的打开锁文件（Word / Excel / PowerPoint，OneDrive 会同步它）
    ".~lock.*#",  # LibreOffice 锁文件
    "~*.tmp",  # 同上一族（~WRD0000.tmp）：以 ~ 开头的 .tmp 是 Office，不是用户脚本的命名习惯
    "*.icloud",  # iCloud 未下载的占位文件（.name.icloud）
    ".icloud",
    ".syncthing.*.tmp",  # Syncthing 的传输中临时文件
    ".nutstore*",  # 坚果云：隐藏的客户端数据（.nutstore_*、.nutstorecache 等）
    "*-nsconflict*",  # 坚果云冲突副本：linux / mac 上文件名带 “-NSConflict”
    "*-冲突*",  # 坚果云冲突副本：Windows 上文件名带 “-冲突”
    "* (conflicted copy*)*",  # Dropbox 冲突副本
    "* (case conflict*)*",  # Dropbox 大小写冲突副本
    "*.sb-????????-??????",  # Dropbox 原子保存的临时名（foo.txt.sb-1a2b3c4d-AbCdEf）；casefold 后仍匹配
)


def is_sync_noise(name: str) -> bool:
    """这个文件名（或目录名）是不是同步客户端 / 系统写的、不该算作“用户项目被改动”的东西。"""
    key = name.casefold()
    if key in _EXACT:
        return True
    return any(fnmatch.fnmatchcase(key, pat) for pat in _PATTERNS)


# --------------------------------------------------------------------------- 同步盘位置
def _home() -> Path:
    return Path(os.path.expanduser("~"))


def sync_roots() -> list[Path]:
    """已知的同步盘根目录（存在的）。`~/Library/CloudStorage/*` 是 macOS 上 Dropbox / OneDrive / Google Drive 的
    “文件提供者”挂载点，下面每个子目录各是一家。"""
    home = _home()
    cands = [
        home / "Nutstore Files",
        home / "Nutstore",
        home / "坚果云",
        home / "Dropbox",
        home / "OneDrive",
        home / "iCloudDrive",
        home / "Library" / "Mobile Documents",
        home / "Google Drive",
        home / "My Drive",
        home / "Box",
        home / "Box Sync",
        home / "Sync",
        home / "Syncthing",
    ]
    out = [p for p in cands if p.exists()]
    cloud = home / "Library" / "CloudStorage"
    try:
        out += [p for p in cloud.iterdir() if p.is_dir()]
    except OSError:
        pass
    # Windows：OneDrive 的环境变量根（含 “OneDrive - 公司名”）
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(var)
        if v:
            out.append(Path(v))
    return out


#: 即使目录还不存在也按名字认的位置段（用户把同步根放在别处时，路径里仍会带这些名字）
_NAMED_SEGMENTS = frozenset(
    {"nutstore files", "nutstore", "坚果云", "dropbox", "onedrive", "icloud drive", "clouddocs"}
)


def in_sync_folder(path: str | os.PathLike) -> bool:
    """项目目录是否位于已知同步盘：落在某个已知同步根之下，或路径里有同步盘的典型目录名
    （`Nutstore Files` / `Dropbox` / `OneDrive*` / `Mobile Documents` / `CloudStorage` …）。只看路径，不访问网络。"""
    try:
        real = Path(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError):
        return False
    norm = os.path.normcase
    for root in sync_roots():
        try:
            r = Path(os.path.realpath(root))
        except OSError:
            continue
        if norm(str(real)) == norm(str(r)) or norm(str(real)).startswith(norm(str(r)) + os.sep):
            return True
    for part in real.parts:
        low = part.casefold()
        if low in _NAMED_SEGMENTS or low.startswith("onedrive -") or low == "mobile documents":
            return True
        if low == "cloudstorage" and sys.platform == "darwin":
            return True
    return False
