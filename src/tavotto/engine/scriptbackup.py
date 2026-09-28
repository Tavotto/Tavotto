"""改用户脚本之前的备份、原子替换与历史（ADR 0108 §五；ADR 0094 §六 / §八 的单文件部分）。

Tavotto 只在用户**亲手发起、看过逐行 diff、勾选确认**之后改脚本（改写数据路径，ADR 0108；将来的
写回脚本，ADR 0094）。本模块是这两者共用的底座，只管「一份脚本文件」：

* `resolve()` —— 写哪个文件由服务端按项目 + 项目相对路径决定（`projectenv.contained_path`），并拒绝
  符号链接（脚本或它在项目内的任一父目录）、硬链接、没有写权限的目录；
* `replace()` —— 先把**原字节**备份到两处（项目内 `tavottofile/script-backups/` 与数据目录镜像），两处都
  落盘之后才原子替换原件（保留权限位）。任何一步失败原件逐字节不变；
* `history()` / `load()` —— 按脚本列出备份、取回某一份的原字节与记录。

只动一份文件，所以不需要 0094 §五.7 的跨文件日志：备份在替换之前落盘，崩在两者之间只多一份备份，
`history()` 按「磁盘此刻的 sha256 等不等于记录里的 after」标出它从未生效。

纯标准库（Flask 父进程侧）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .. import __version__
from . import atomicio, projectenv
from .runtime import CREATE_NO_WINDOW

#: 稳定错误码（协议契约；`tests/test_error_codes.py` 读这张表）。
ERROR_SCRIPT_NOT_FOUND = "script_not_found"
ERROR_SCRIPT_IS_SYMLINK = "script_is_symlink"
ERROR_SCRIPT_HARDLINKED = "script_hardlinked"
ERROR_SCRIPT_READONLY = "script_readonly"
ERROR_SCRIPT_CHANGED = "script_changed_since_preview"
ERROR_BACKUP_FAILED = "script_backup_failed"
ERROR_REPLACE_FAILED = "script_replace_failed"
ERROR_BACKUP_UNKNOWN = "script_backup_unknown"
ERROR_SCRIPT_BUSY = "script_busy"
ERROR_CODES = (
    ERROR_SCRIPT_BUSY,
    ERROR_SCRIPT_NOT_FOUND,
    ERROR_SCRIPT_IS_SYMLINK,
    ERROR_SCRIPT_HARDLINKED,
    ERROR_SCRIPT_READONLY,
    ERROR_SCRIPT_CHANGED,
    ERROR_BACKUP_FAILED,
    ERROR_REPLACE_FAILED,
    ERROR_BACKUP_UNKNOWN,
)

#: 每份脚本保留多少条非 pristine 备份（与 AI 快照同一个数，ADR 0094 §六）。
KEEP_RECENT = 20
PROJECT_DIRNAME = "script-backups"
ORIGINAL_NAME = "original.py"
META_NAME = "meta.json"

#: 备份状态：磁盘此刻就是这份备份记下的「改后」/ 还是「改前」（从未生效或已复原）/ 两者都不是。
STATE_CURRENT = "current"
STATE_BEFORE = "before"
STATE_CHANGED = "changed"


class ScriptEditError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code = code
        self.params = params


@dataclasses.dataclass(frozen=True)
class Store:
    """一个项目的两处备份位置。`project_dir` 在项目内（随项目走、用户看得见），`mirror_dir` 在数据目录
    （用户删了 `tavottofile/`、`git clean -fdx`、同步盘冲掉时仍在）。路径只由调用方（app）给出。"""

    root: Path
    project_dir: Path
    mirror_dir: Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug_of(script: str) -> str:
    """项目相对路径 → 备份目录名（一层、可读、不含分隔符）。"""
    norm = script.replace("\\", "/").strip("/")
    return re.sub(r"[^\w.\-一-鿿]+", "_", norm.replace("/", "__")) or "_"


def resolve(root: str | os.PathLike, script: str) -> Path:
    """项目相对路径 → 要改的那个文件（真实路径）；不能安全地改就抛 `ScriptEditError`。"""
    root_real = Path(os.path.realpath(root))
    real = projectenv.contained_path(root, script)
    if real is None or not Path(real).is_file():
        raise ScriptEditError(
            ERROR_SCRIPT_NOT_FOUND, f"项目里没有这个脚本：{script}", script=script
        )
    real = Path(real)
    lexical = root_real.joinpath(*[p for p in script.replace("\\", "/").split("/") if p])
    # 脚本或它在项目内的任一父目录是符号链接：写穿过去改的是别处的文件，替换则把链接换成普通文件
    if os.path.normcase(str(lexical)) != os.path.normcase(str(real)) or lexical.is_symlink():
        raise ScriptEditError(
            ERROR_SCRIPT_IS_SYMLINK, f"脚本经过符号链接，不改：{script}", script=script
        )
    st = real.stat()
    if st.st_nlink > 1:  # `os.replace` 会断开另一个名字
        raise ScriptEditError(
            ERROR_SCRIPT_HARDLINKED, f"脚本有硬链接，不改：{script}", script=script
        )
    if not (os.access(real, os.W_OK) and os.access(real.parent, os.W_OK | os.X_OK)):
        raise ScriptEditError(ERROR_SCRIPT_READONLY, f"没有写权限：{script}", script=script)
    try:
        if os.statvfs(real.parent).f_flag & getattr(os, "ST_RDONLY", 1):
            raise ScriptEditError(ERROR_SCRIPT_READONLY, f"只读卷：{script}", script=script)
    except (AttributeError, OSError):  # Windows 没有 statvfs；量不了就交给 access 那一判
        pass
    return real


def git_state(path: Path) -> dict | None:
    """脚本受不受 git 管、有没有未提交的修改（只读探测，带超时）；没有 git / 不在仓库里回 None。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
            creationflags=CREATE_NO_WINDOW,  # GUI 拥有的子进程：Windows 上不闪控制台窗
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip()
    if line.startswith("??"):
        return {"tracked": False, "dirty": True}
    return {"tracked": True, "dirty": bool(line)}


_CHECKSUM_NAMES = re.compile(r"^(CHECKSUMS.*|SHA\d*SUMS.*|MD5SUMS.*|.+\.(sha\d+|md5))$", re.I)


def checksum_manifests(path: Path) -> list[str]:
    """同目录里列着这个脚本的校验清单（改完校验值会变，ADR 0094 §7.1）。只看文件名与内容里有没有它。"""
    out: list[str] = []
    try:
        entries = sorted(path.parent.iterdir())
    except OSError:
        return out
    for entry in entries[:500]:
        if not _CHECKSUM_NAMES.match(entry.name) or not entry.is_file():
            continue
        try:
            if entry.stat().st_size > 1_000_000:
                continue
            if path.name in entry.read_text(encoding="utf-8", errors="replace"):
                out.append(entry.name)
        except OSError:
            continue
    return out


def _new_dir(parent: Path, base: str) -> Path:
    """`<月日_时分秒>`，同一秒已占用接 `-2`、`-3`；`mkdir` 本身就是占有（与写回备份同一做法）。"""
    parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        cand = parent / (base if n == 1 else f"{base}-{n}")
        try:
            cand.mkdir()
            return cand
        except FileExistsError:
            n += 1


def _metas(parent: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    if not parent.is_dir():
        return out
    for d in parent.iterdir():
        try:
            meta = json.loads((d / META_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and (d / ORIGINAL_NAME).is_file():
            out.append((d, meta))
    out.sort(key=lambda it: (it[1].get("created", 0), it[0].name))
    return out


def _prune(parent: Path) -> None:
    """留 pristine 与最近 `KEEP_RECENT` 条；只删非 pristine 的。"""
    rest = [d for d, meta in _metas(parent) if not meta.get("pristine")]
    for d in rest[: max(0, len(rest) - KEEP_RECENT)]:
        shutil.rmtree(d, ignore_errors=False)


def replace(
    store: Store,
    script: str,
    new_bytes: bytes,
    *,
    kind: str,
    expect_before: str,
    meta: dict | None = None,
) -> dict:
    """备份（两处）→ 原子替换。返回这份备份的记录（含 `id`）。

    `expect_before` 是调用方预览时看到的磁盘 sha256：替换前再核一次，对不上 `script_changed_since_preview`
    （确认界面停留期间脚本被改过）。两处备份全部写完并 fsync 才碰原件；任何一步失败原件逐字节不变、
    这次建出来的备份目录删掉。
    """
    path = resolve(store.root, script)
    old = path.read_bytes()
    if sha256(old) != expect_before:
        raise ScriptEditError(
            ERROR_SCRIPT_CHANGED, "预览之后脚本被改过了，请重新预览", script=script
        )
    st = path.stat()
    slug = slug_of(script)
    pristine = not _metas(store.project_dir / slug) and not _metas(store.mirror_dir / slug)
    record = {
        "kind": kind,
        "script": script.replace("\\", "/"),
        "script_abs": str(path),
        "created": time.time(),
        "tavotto_version": __version__,
        "pristine": pristine,
        "before": {
            "sha256": sha256(old),
            "size": len(old),
            "mtime_ns": st.st_mtime_ns,
            "mode": stat.S_IMODE(st.st_mode),
        },
        "after": {"sha256": sha256(new_bytes), "size": len(new_bytes)},
        **(meta or {}),
    }
    created: list[Path] = []
    try:
        pdir = _new_dir(store.project_dir / slug, time.strftime("%m%d_%H%M%S"))
        created.append(pdir)
        mdir = store.mirror_dir / slug / pdir.name
        mdir.mkdir(parents=True, exist_ok=False)
        created.append(mdir)
        record["id"] = f"{slug}/{pdir.name}"
        for d in (pdir, mdir):
            atomicio.write_bytes(d / ORIGINAL_NAME, old)
            atomicio.write_json(d / META_NAME, record, indent=2)
    except (OSError, atomicio.AtomicWriteError) as exc:
        for d in created:
            shutil.rmtree(d, ignore_errors=True)
        raise ScriptEditError(
            ERROR_BACKUP_FAILED, f"备份写不进去，脚本没有被修改：{exc}", script=script
        ) from exc
    # 备份与确认之间的最后一次核对：竞争窗口只剩这一次读与 replace 之间
    if sha256(path.read_bytes()) != expect_before:
        for d in created:
            shutil.rmtree(d, ignore_errors=True)
        raise ScriptEditError(
            ERROR_SCRIPT_CHANGED, "预览之后脚本被改过了，请重新预览", script=script
        )
    try:
        atomicio.write_bytes(path, new_bytes, mode=stat.S_IMODE(st.st_mode))
    except atomicio.AtomicWriteError as exc:
        raise ScriptEditError(
            ERROR_REPLACE_FAILED, f"替换失败，脚本没有被修改：{exc.message}", script=script
        ) from exc
    for parent in (store.project_dir / slug, store.mirror_dir / slug):
        try:
            _prune(parent)
        except OSError:  # 清理失败不影响这次改动；下次再清
            pass
    return record


def _state_of(meta: dict, current: str | None) -> str:
    if current == (meta.get("after") or {}).get("sha256"):
        return STATE_CURRENT
    if current == (meta.get("before") or {}).get("sha256"):
        return STATE_BEFORE
    return STATE_CHANGED


def history(store: Store, script: str | None = None) -> list[dict]:
    """备份记录（新的在前），每条多一个 `state`（相对磁盘此刻）。项目内那份不在就读镜像。

    `script` 为 None 时列整个项目的（设置里的「脚本改写备份」）。
    """
    if script is None:
        slugs: set[str] = set()
        for base in (store.project_dir, store.mirror_dir):
            if base.is_dir():
                slugs.update(d.name for d in base.iterdir() if d.is_dir())
    else:
        slugs = {slug_of(script)}
    by_id: dict[str, dict] = {}
    for slug in slugs:
        for parent in (store.mirror_dir / slug, store.project_dir / slug):
            for _d, meta in _metas(parent):
                if isinstance(meta.get("id"), str):
                    by_id[meta["id"]] = meta
    current: dict[str, str | None] = {}
    out = []
    for meta in by_id.values():
        name = meta.get("script") if isinstance(meta.get("script"), str) else ""
        if name not in current:
            try:
                current[name] = sha256(resolve(store.root, name).read_bytes())
            except (ScriptEditError, OSError):
                current[name] = None
        out.append(dict(meta, state=_state_of(meta, current[name])))
    out.sort(key=lambda m: m.get("created", 0), reverse=True)
    return out


def load(store: Store, backup_id: str) -> tuple[dict, bytes]:
    """一份备份的记录与原字节。`id` 形如 `<slug>/<时间戳>`；两段都只许是目录名（不许 `..`）。"""
    parts = backup_id.split("/") if isinstance(backup_id, str) else []
    if len(parts) != 2 or any(not p or p in (".", "..") or "\\" in p for p in parts):
        raise ScriptEditError(ERROR_BACKUP_UNKNOWN, "没有这份备份", id=str(backup_id))
    for base in (store.project_dir, store.mirror_dir):
        d = base / parts[0] / parts[1]
        try:
            meta = json.loads((d / META_NAME).read_text(encoding="utf-8"))
            data = (d / ORIGINAL_NAME).read_bytes()
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and sha256(data) == (meta.get("before") or {}).get("sha256"):
            return meta, data
    raise ScriptEditError(ERROR_BACKUP_UNKNOWN, "没有这份备份", id=backup_id)
