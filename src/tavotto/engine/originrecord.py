"""来源记录：AI 交接时「这份原件是谁、用什么环境画出来的」的**弱线索**（ADR 0139 PR3，第一片）。

只记录事实与只读查询：不改任何执行、不换解释器、不参与 exact / needs_confirmation 的判定。

* **键是原件字节的 sha256**，不是路径、不是 stem：原件改一个字节，线索自动失效（查不到）。
* 落盘 `config.data_dir()/origin/<sha256>.json`，一份原件一个文件，不同原件的写入互不覆盖。
* **只由插件交接那次真的跑了脚本的进程写**（`recorded_by="handoff"`）；产物没重跑就不写（诚实缺失）。
* **不存**解释器绝对路径、argv、环境变量、cwd：隐私，且本片不需要。版本串 / 后端名也在入口处白名单校验，
  形似路径的值一律拒收。

**为什么这里 fail-open，与写回闸（`artifactcontext` 的采用记录）相反：** 采用记录是安全闸——读不了
就得拒绝（宁拒不放，绝不覆盖用户原件）；来源记录只是展示用的线索，读不了 / 畸形 / 缺失一律当作
「没有记录」，不报错、不阻断任何流程。反过来也决定了它**不许**被用作任何放行或判定的依据。

纯标准库 + `atomicio` / `config`；不 import matplotlib / Flask。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import atomicio, config, layoutsession

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RECORDED_BY_HANDOFF = "handoff"

#: 磁盘上最多多少份记录文件；满了先清过期的（文件没了 / 字节变了），仍满再挤掉最旧的。
#: 挤掉是安全的：这只是线索，丢了就回到「没有记录」。
MAX_RECORDS = 256
#: 同一份字节（同 sha）最多记几个项目 × 文件
MAX_ENTRIES_PER_SHA = 8
#: 清理时核对原件字节的预算；超了按「已失效」
_LIVE_CHECK_MAX_BYTES = 256 * 1024 * 1024

_LOCK = threading.Lock()
LOCK_FILENAME = ".lock"
LOCK_TIMEOUT_S = 5.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_\-]{0,63}$")
_BACKEND_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}(://[A-Za-z0-9_.\-]{1,96})?$")
_FORMAT_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def origin_dir() -> Path:
    return config.data_dir() / "origin"


def _record_path(sha: str) -> Path:
    return origin_dir() / f"{sha}.json"


def project_key(project_root) -> str:
    """与项目系统（`app._project_id` / `pool._norm_dir`）同一个规范化身份。"""
    return config.normalize_path_identity(str(Path(project_root).resolve()))


def lock_path() -> Path:
    return origin_dir() / LOCK_FILENAME


class _WriteLock:
    """线程锁 + 跨进程文件锁（`layoutsession.acquire_file_lock`，同一份 flock / msvcrt.locking 实现）。

    每个 `tavotto open --origin-json` 都是独立进程，只有线程锁管不到跨进程的读改写（丢条目 / 总数超 MAX_RECORDS）。
    与会话状态锁的取舍相反：拿不到锁（超时 / 文件系统不支持咨询锁）**不写**并记日志——来源只是弱线索，
    丢一次记录 = 回到「没有记录」，不能为它阻断交接，也不能退回无锁的读改写去互相覆盖。
    """

    def __enter__(self) -> bool:
        self._fd = None
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        if not _LOCK.acquire(timeout=LOCK_TIMEOUT_S):
            LOG.warning(
                "来源记录被本进程的另一个线程占着超过 %s 秒（忽略，只是线索）", LOCK_TIMEOUT_S
            )
            self._held = False
            return False
        self._held = True
        try:
            self._fd = layoutsession.acquire_file_lock(lock_path(), deadline)
        except layoutsession.SessionStateUnavailable as exc:
            LOG.warning("来源记录拿不到跨进程锁（忽略，只是线索）：%s", exc)
            return False
        except BaseException:
            _LOCK.release()
            raise
        return True

    def __exit__(self, *exc: object) -> None:
        try:
            if self._fd is not None:
                layoutsession.release_file_lock(self._fd)
        finally:
            if self._held:
                _LOCK.release()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 校验 ----
def _number(value, *, lo: float, hi: float):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or not (lo <= value <= hi):  # NaN / 越界
        return None
    return value


def sanitize_process_facts(raw) -> dict | None:
    """脚本进程自报的事实 → 白名单后的闭集；缺关键项 / 形似路径 / 类型不对 → None（不记）。

    关键项是三个：python_version、matplotlib_version、backend。其余缺了就缺着。
    """
    if not isinstance(raw, dict):
        return None
    py, mpl, backend = raw.get("python_version"), raw.get("matplotlib_version"), raw.get("backend")
    if not (isinstance(py, str) and _VERSION_RE.match(py)):
        return None
    if not (isinstance(mpl, str) and _VERSION_RE.match(mpl)):
        return None
    if not (isinstance(backend, str) and _BACKEND_RE.match(backend)):
        return None
    if ".." in backend:
        return None
    out: dict = {"python_version": py, "matplotlib_version": mpl, "backend": backend}
    # figure_dpi = 脚本进程退出时刻 rcParams["figure.dpi"] 的值；不是某次 savefig 的实参（那个看不到，所以不记）
    dpi = _number(raw.get("figure_dpi"), lo=1, hi=10000)
    if dpi is not None:
        out["figure_dpi"] = dpi
    return out


def _valid_entry(e) -> bool:
    """读回来的条目是否可信到可以展示：结构、版本、白名单字段全对。"""
    if not isinstance(e, dict) or e.get("recorded_by") != RECORDED_BY_HANDOFF:
        return False
    for k in ("project", "source_id", "recorded_at"):
        if not isinstance(e.get(k), str) or not e[k]:
            return False
    return sanitize_process_facts(e) is not None


# ---------------------------------------------------------------- 读 ------
def _read_file(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # fail-open：见模块文档
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return None
    sha = data.get("bytes_sha256")
    entries = data.get("entries")
    if not (isinstance(sha, str) and _SHA256_RE.match(sha) and isinstance(entries, list)):
        return None
    return sha, [e for e in entries if _valid_entry(e)]


def lookup(sha: str, project_root=None) -> dict | None:
    """按原件字节 sha256 查记录。同 sha 多条时优先同项目的，其次最新的。没有 / 读坏了 → None。

    返回**展示用**的闭集：`{python_version, matplotlib_version, backend, figure_dpi, recorded_by}`（缺的键不出现）。
    """
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        return None
    parsed = _read_file(_record_path(sha))
    if parsed is None or parsed[0] != sha or not parsed[1]:
        return None
    entries = parsed[1]
    entry = None
    if project_root is not None:
        try:
            key = project_key(project_root)
        except OSError:
            key = None
        same = [e for e in entries if e["project"] == key]
        if same:
            entry = max(same, key=lambda e: e["recorded_at"])
    if entry is None:
        entry = max(entries, key=lambda e: e["recorded_at"])
    out = {
        "python_version": entry["python_version"],
        "matplotlib_version": entry["matplotlib_version"],
        "backend": entry["backend"],
        "recorded_by": entry["recorded_by"],
    }
    if "figure_dpi" in entry:
        out["figure_dpi"] = entry["figure_dpi"]
    return out


# ---------------------------------------------------------------- 写 ------
def _hash_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ValueError("file exceeds hashing budget")
            h.update(chunk)
    return h.hexdigest(), size


def _entry_live(entry: dict, sha: str) -> bool:
    try:
        return (
            _hash_file(
                Path(entry["project"]) / entry["source_id"], max_bytes=_LIVE_CHECK_MAX_BYTES
            )[0]
            == sha
        )
    except (OSError, ValueError, KeyError):
        return False


def _prune_locked() -> None:
    """记录文件数到上限时：先删过期的（所有条目对应的文件都没了或字节变了），仍满再删最旧的。"""
    try:
        files = sorted(origin_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    if len(files) < MAX_RECORDS:
        return
    alive: list[Path] = []
    for p in files:
        parsed = _read_file(p)
        if parsed is None or not any(_entry_live(e, parsed[0]) for e in parsed[1]):
            try:
                p.unlink()
            except OSError:
                alive.append(p)
        else:
            alive.append(p)
    for p in alive[: max(0, len(alive) - MAX_RECORDS + 1)]:  # 仍满：挤掉最旧的（给新的腾一个位）
        try:
            p.unlink()
        except OSError:
            pass


def record(
    *,
    project_root,
    source_id: str,
    bytes_sha256: str,
    size_bytes: int,
    facts: dict,
    script_rel: str | None = None,
    script_sha1: str | None = None,
) -> bool:
    """写一条记录；成功 True。任何失败返回 False（弱线索，写不进去不阻断交接）。"""
    if not _SHA256_RE.match(bytes_sha256 or ""):
        return False
    clean = sanitize_process_facts(facts)
    if clean is None or not source_id or os.path.isabs(source_id) or ".." in Path(source_id).parts:
        return False
    entry: dict = {
        "project": project_key(project_root),
        "source_id": source_id.replace(os.sep, "/"),
        **clean,
        "size_bytes": int(size_bytes),
        "recorded_by": RECORDED_BY_HANDOFF,
        "recorded_at": _now(),
    }
    if script_rel and not os.path.isabs(script_rel) and ".." not in Path(script_rel).parts:
        entry["script"] = {"rel": script_rel.replace(os.sep, "/")}
        if script_sha1 and _SHA1_RE.match(script_sha1):
            entry["script"]["sha1"] = script_sha1
    path = _record_path(bytes_sha256)
    with _WriteLock() as locked:
        if not locked:
            return False
        try:
            prior = _read_file(path)
            others = [
                e
                for e in (prior[1] if prior and prior[0] == bytes_sha256 else [])
                if (e["project"], e["source_id"]) != (entry["project"], entry["source_id"])
            ]
            if prior is None and not path.exists():
                _prune_locked()
            entries = [*others[-(MAX_ENTRIES_PER_SHA - 1) :], entry]
            atomicio.write_json(
                path,
                {"version": SCHEMA_VERSION, "bytes_sha256": bytes_sha256, "entries": entries},
            )
        except (OSError, ValueError, atomicio.AtomicWriteError) as exc:
            LOG.warning("来源记录写不进去（忽略，只是线索）：%s", exc)
            return False
    return True


def record_handoff(project_root, payload) -> dict:
    """`tavotto open --origin-json` 的落地：payload = 插件写的 JSON（见 handoff.py `_origin_payload`）。

    `payload = {"schema": 1, "process": <脚本进程自报>, "products": [<项目相对路径>…], "script": <项目相对路径>}`。
    只给 PNG 原件记（来源驱动策略只采用 PNG）；路径必须落在项目内且是真文件，字节由**这里**现算。
    返回 `{"recorded": n, "skipped": m}`；整体拒收时 `recorded=0` 并带 `reason`。
    """
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        return {"recorded": 0, "skipped": 0, "reason": "bad_payload"}
    facts = sanitize_process_facts(payload.get("process"))
    products = payload.get("products")
    if facts is None or not isinstance(products, list):
        return {"recorded": 0, "skipped": 0, "reason": "bad_payload"}
    root = Path(project_root).resolve()
    script_rel = payload.get("script") if isinstance(payload.get("script"), str) else None
    script_sha1 = None
    if script_rel and not os.path.isabs(script_rel):
        try:
            script_sha1 = hashlib.sha1((root / script_rel).read_bytes()).hexdigest()  # noqa: S324 - 与 pool.script_sha1 同口径，非安全用途
        except OSError:
            script_rel = None
    else:
        script_rel = None
    done = skipped = 0
    for rel in products[:64]:
        try:
            if not isinstance(rel, str) or Path(rel).suffix.lower() != ".png" or os.path.isabs(rel):
                raise ValueError("not a relative png")
            target = (root / rel).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("outside project")
            sha, size = _hash_file(target, max_bytes=_LIVE_CHECK_MAX_BYTES)
            ok = record(
                project_root=root,
                source_id=target.relative_to(root).as_posix(),
                bytes_sha256=sha,
                size_bytes=size,
                facts=facts,
                script_rel=script_rel,
                script_sha1=script_sha1,
            )
        except (OSError, ValueError):
            ok = False
        done += 1 if ok else 0
        skipped += 0 if ok else 1
    return {"recorded": done, "skipped": skipped}
