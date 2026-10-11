"""会话状态以后端为准（#715 PR-B）：「这个项目上次开着哪份排版」与「这个槽位属于哪个项目」。

为什么要放在后端：桌面壳每次启动 sidecar 都可能换端口，而 localStorage 按 origin（协议 + 主机 +
端口）隔离——换了端口就是一份全新的空存储，前端记在本机的「上次打开的排版」随之丢失，用户每次
启动都落在一份空白排版上（#715，Windows 实测 5 次启动 5 个 origin）。稳定端口（PR-A #718，ADR
0108 §一—§二）能让 origin 尽量不变，但端口被占时仍然会变；所以这类**丢了用户就找不回工作**的状态
以数据目录里的这份文件为准，前端的 localStorage 只当缓存（ADR 0108 §三）。

文件 `data_dir()/state/layout-sessions.json`，只由本模块读写：

```json
{
  "version": 1,
  "projects": {"<normalize_path_identity(项目路径)>": {"last": {"doc_id", "name", "at"}, "prev": {...}, "pending": {...}}},
  "no_project": {"last": {...}, "prev": {...}, "pending": {...}},
  "owners": {"<槽位 doc_id>": "<项目键>" | null},
  "owner_revisions": {"<槽位 doc_id>": "<该次提交归属时槽位正文的 revision>"}
}
```

* 键用 `config.normalize_path_identity(项目路径)`——与 `app._project_id()`、`pool._norm_dir()`
  同一份判断；短 id 是它的 sha1 前缀、反解不回路径，所以落盘存的是路径本身。
* `no_project` 是「没开项目」那一组（后端没有打开的项目时的排版）；`owners` 里值为 `null`
  表示「确认过：写它的时候没开项目」，与「没有这条记录（不知道）」是两档。
* **每组三个指针**（文档恢复可靠性修复，2026-10-11）：`last` 只指向**正文已经可恢复**的槽位
  （durable）；`prev` 是上一份 durable（容量 1）；`pending` 是最新的「想打开」意图但槽位还没有
  有效正文（容量 1，不替换 durable last）。正文落盘成功的那一次提交里 `commit_slot` 把匹配的
  pending 晋升为 last。
* `owner_revisions[doc]` = 记归属那一刻槽位正文的 revision。它与槽位当前 revision 不一致 =
  进程死在「正文已落盘、元数据未提交」之间（或正文被不认识这条记录的人改过）→ 该归属**待核对**，
  不当 `this` 也不当 `other`。没有这条（旧记录）按原样信任——兼容边界，不是保证。
* **不能放的位置**：`layouts/_autosave/`（槽位目录，清理按文件名扫）、`layouts/`（命名画布）、
  项目的 `tavottofile/`（随项目包、网盘流动，而这是本机的会话状态）。

纯标准库。写入一律 `atomicio`（ADR 0023）。

**读取结果有四档**（`_read()` 回 `(state, status)`），不再无差别当「空的」：

* `missing`：文件不存在，首次空状态；
* `ok`：读到了；单个字段坏（NaN、类型错）只丢那个字段，未知键原样保留，写回时带上；
* `recovered`：主文件的字节坏了，原字节已存成 `state/layout-sessions.corrupt-<ms>.json`
  （最多留 3 份），改用 `layout-sessions.json.bak`（每次成功写主文件后同 payload 再写的一份）；
* `degraded`：读不到可信的状态——① 读失败（没拿到字节）；② 字节坏且没有可用的 bak（已备份原字节，
  当空）；③ `version` 比本程序认识的新。①③ **拒绝一切写回**（`SessionStateUnavailable`）：
  拿空状态覆盖一份可能完整、或比我们新的文件，就是把一次读故障变成永久丢数据；②原字节已备份，写入放行。

并发：模块锁 `_LOCK` = 进程内线程锁 + 跨进程文件锁（`state/layout-sessions.lock`，POSIX `fcntl.flock`、
Windows `msvcrt.locking`，纯标准库），多个进程共用一个数据目录时读改写不丢更新；拿不到锁（5 s）
抛 `SessionStateUnavailable`。锁文件打不开（只读卷等）时退回只有线程锁并记日志。
**锁顺序全局固定：槽位的 `_document_lock`（app）→ 会话锁**。会话锁内不读任何正文、不调需要文档锁的函数；
要槽位 revision 的判据一律由调用方在锁外算好传进来。

导出默认值（`tavotto.export.defaults`，丢了会静默从 1200 ppi 退回 600 ppi，#715 证据）同理
迁到数据目录，按用户一份：`state/export-defaults.json`。内容由前端定义（`lib/exportDefaults.ts`
是它的语义唯一出处），这里只守「是个不大的 JSON 对象」。
"""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from . import atomicio, config, documents

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
try:  # Windows
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

LOG = logging.getLogger(__name__)

STATE_DIRNAME = "state"
FILENAME = "layout-sessions.json"
BACKUP_SUFFIX = ".bak"
CORRUPT_PREFIX = "layout-sessions.corrupt-"
MAX_CORRUPT_COPIES = 3
LOCK_FILENAME = "layout-sessions.lock"
LOCK_TIMEOUT_S = 5.0
EXPORT_DEFAULTS_FILENAME = "export-defaults.json"
FORMAT_VERSION = 1

#: 读取结果四档（`_read()` 的第二项、`GET /api/layout-session` 的 `index`）
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_DEGRADED = "degraded"
STATUS_RECOVERED = "recovered"

#: 前端 documentId 的形状：`newId('d')` 出来的都在这个字符集里。与 `app._autosave_path`
#: 的净化同一个字符集——owners 的键就是槽位文件名去掉 `.json`。
_DOC_ID_RE = re.compile(r"^[\w\-]{1,128}$")
#: 排版名是用户内容，只用来在「找不到上次排版」那句话里指名；截一个上限防止粘错。
MAX_NAME_CHARS = 500
#: 导出默认值的序列化上限（字节）。现在的形状不到 200 字节。
MAX_EXPORT_DEFAULTS_BYTES = 16 * 1024

#: 每组里本模块认识的指针；组内其余键原样保留
POINTERS = ("last", "prev", "pending")
_TOP_KNOWN = frozenset({"version", "projects", "no_project", "owners", "owner_revisions"})
_MAX_SCRUB_DEPTH = 32
_MAX_REVISION_CHARS = 128

#: `commit_slot` 的 key：这次保存的 pj 已失效，归属是「不知道」——不记、不晋升
UNKNOWN: Any = object()

_PREFS_LOCK = threading.Lock()


class SessionStateUnavailable(OSError):
    """会话状态此刻不能安全写（读不到可信的状态 / 未来格式 / 跨进程锁拿不到）。

    是 `OSError`：调用方对「元数据写不进去」本来就按 `except OSError` 处理，正文照旧保存。
    """


# ------------------------------- 锁 -----------------------------------------


def lock_path() -> Path:
    return config.data_path(STATE_DIRNAME, LOCK_FILENAME)


_LOCK_WARNED = False


def _try_file_lock(fd: int) -> None:
    """非阻塞地拿文件锁；被别人占着抛 `BlockingIOError` / `PermissionError`。"""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover
        raise OSError(errno.ENOSYS, "没有文件锁")


def release_file_lock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError as exc:  # pragma: no cover
        LOG.warning("会话状态文件锁释放失败（%s）", exc)
    finally:
        os.close(fd)


def acquire_file_lock(path: Path, deadline: float) -> int | None:
    """拿 `path` 上的跨进程文件锁，回 fd；锁文件根本打不开 / 平台不支持时回 `None`（只剩线程锁）。

    会话锁与 app 的槽位文档锁（`app._document_lock`，按条带分文件）共用这一份实现。
    超过 `deadline`（`time.monotonic()` 刻度）抛 `SessionStateUnavailable`。"""
    global _LOCK_WARNED
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        if not _LOCK_WARNED:
            _LOCK_WARNED = True
            LOG.warning("会话状态锁文件打不开，退回只有进程内的锁（%s）", exc)
        return None
    while True:
        try:
            _try_file_lock(fd)
            return fd
        except OSError as exc:
            busy = exc.errno in (errno.EAGAIN, errno.EACCES, errno.EDEADLK) or isinstance(
                exc, (BlockingIOError, PermissionError)
            )
            if not busy:
                os.close(fd)
                if not _LOCK_WARNED:
                    _LOCK_WARNED = True
                    LOG.warning("这个文件系统不支持文件锁，退回只有进程内的锁（%s）", exc)
                return None
            if time.monotonic() >= deadline:
                os.close(fd)
                raise SessionStateUnavailable("文件锁被别的 Tavotto 进程占着，等待超时") from exc
            time.sleep(0.01)


class _StateLock:
    """线程锁 + 跨进程文件锁的组合上下文。不可重入（持锁期间别再进来）。"""

    def __init__(self) -> None:
        self._thread = threading.Lock()
        self._fd: int | None = None

    def __enter__(self) -> _StateLock:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        if not self._thread.acquire(timeout=LOCK_TIMEOUT_S):
            raise SessionStateUnavailable("会话状态被本进程的另一个请求占着超过 5 秒")
        try:
            self._fd = acquire_file_lock(lock_path(), deadline)
        except BaseException:
            self._thread.release()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                release_file_lock(fd)
        finally:
            self._thread.release()


_LOCK = _StateLock()


def state_path() -> Path:
    """会话状态文件在哪。**唯一出处**。"""
    return config.data_path(STATE_DIRNAME, FILENAME)


def backup_path() -> Path:
    return state_path().with_name(FILENAME + BACKUP_SUFFIX)


def export_defaults_path() -> Path:
    return config.data_path(STATE_DIRNAME, EXPORT_DEFAULTS_FILENAME)


def project_key(path: str | Path) -> str:
    """项目在这份文件里的键。"""
    return config.normalize_path_identity(path)


def valid_doc_id(doc_id: object) -> bool:
    return isinstance(doc_id, str) and bool(_DOC_ID_RE.match(doc_id))


# ------------------------------- 读 / 写 ------------------------------------

_DROP = object()


def _scrub(value: Any, depth: int = 0) -> Any:
    """未知键里的内容原样保留，但非有限数（写出去会让整份写盘失败）整个丢掉。"""
    if depth > _MAX_SCRUB_DEPTH:
        return _DROP
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cleaned = _scrub(v, depth + 1)
            if cleaned is not _DROP:
                out[k] = cleaned
        return out
    if isinstance(value, list):
        return [c for c in (_scrub(v, depth + 1) for v in value) if c is not _DROP]
    return value


def _clean_last(raw: object) -> dict | None:
    if not isinstance(raw, dict) or not valid_doc_id(raw.get("doc_id")):
        return None
    name = raw.get("name")
    at = raw.get("at")
    return {
        "doc_id": raw["doc_id"],
        "name": name[:MAX_NAME_CHARS] if isinstance(name, str) else "",
        # 非有限数（`1e400` 解析成 inf、`NaN`）按坏值处理：int(inf) 会抛 OverflowError，让每一次读写都
        # 失败（#719 Codex P2）；只丢这一个字段，不让整份状态作废
        "at": int(at)
        if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)
        else 0,
    }


def _clean_group(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for name in POINTERS:
        entry = _clean_last(raw.get(name))
        if entry is not None:
            out[name] = entry
    for k, v in raw.items():
        if isinstance(k, str) and k not in POINTERS:
            cleaned = _scrub(v)
            if cleaned is not _DROP:
                out[k] = cleaned
    return out


def _empty() -> dict:
    return {"projects": {}, "no_project": {}, "owners": {}, "owner_revisions": {}, "_extra": {}}


def _parse_state(data: dict) -> dict:
    """顶层是对象时逐字段清洗；坏字段丢，其余（含未知键）保留。"""
    out = _empty()
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, group in projects.items():
            if not isinstance(key, str) or not key or not isinstance(group, dict):
                continue
            cleaned = _clean_group(group)
            if cleaned:
                out["projects"][key] = cleaned
    out["no_project"] = _clean_group(data.get("no_project"))
    owners = data.get("owners")
    if isinstance(owners, dict):
        out["owners"] = {
            k: v
            for k, v in owners.items()
            if valid_doc_id(k) and (v is None or (isinstance(v, str) and v))
        }
    revisions = data.get("owner_revisions")
    if isinstance(revisions, dict):
        out["owner_revisions"] = {
            k: v
            for k, v in revisions.items()
            if valid_doc_id(k) and isinstance(v, str) and 0 < len(v) <= _MAX_REVISION_CHARS
        }
    for k, v in data.items():
        if isinstance(k, str) and k not in _TOP_KNOWN:
            cleaned = _scrub(v)
            if cleaned is not _DROP:
                out["_extra"][k] = cleaned
    return out


def _is_future(data: dict) -> bool:
    version = data.get("version")
    return isinstance(version, int) and not isinstance(version, bool) and version > FORMAT_VERSION


def _parse_bytes(raw: bytes) -> dict | None:
    """字节 → 顶层对象；不是 JSON / 顶层不是对象回 `None`。**接受 NaN**（逐字段清洗，不整份作废）。"""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _preserve_corrupt(raw: bytes) -> None:
    """把读不懂的原字节存成 `state/layout-sessions.corrupt-<ms>.json`（同样的字节只存一份，最多 3 份）。"""
    directory = state_path().parent
    try:
        existing = sorted(
            p
            for p in directory.glob(CORRUPT_PREFIX + "*.json")
            if p.is_file() and not p.is_symlink()
        )
        for p in existing:
            try:
                if p.read_bytes() == raw:
                    return
            except OSError:
                continue
        atomicio.write_bytes(
            directory / f"{CORRUPT_PREFIX}{int(time.time() * 1000)}.json",
            raw,
        )
        existing = sorted(directory.glob(CORRUPT_PREFIX + "*.json"))
        for stale in existing[:-MAX_CORRUPT_COPIES]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError as exc:
        LOG.warning("会话状态的损坏副本存不下（%s）", exc)


class _Loaded(NamedTuple):
    state: dict
    status: str


def _load(*, preserve: bool = True) -> _Loaded:
    """读整份状态。`preserve=False` 时不写任何东西（候选发现、诊断）。

    不能安全写回的情形在 `state["_readonly"]` 里记原因，`_write` 据此拒写。
    """
    path = state_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _Loaded(_empty(), STATUS_MISSING)
    except OSError as exc:
        LOG.warning("会话状态读不动，本次拒绝写回（%s）", exc)
        state = _empty()
        state["_readonly"] = "unreadable"
        return _Loaded(state, STATUS_DEGRADED)
    data = _parse_bytes(raw)
    if data is not None:
        state = _parse_state(data)
        if _is_future(data):
            LOG.warning("会话状态来自更新的 Tavotto（version %s），只读不写", data.get("version"))
            state["_readonly"] = "future_version"
            return _Loaded(state, STATUS_DEGRADED)
        return _Loaded(state, STATUS_OK)
    # 字节坏：先留原字节，再看 bak
    LOG.warning("会话状态文件损坏，已保留原字节")
    if preserve:
        _preserve_corrupt(raw)
    try:
        bak = _parse_bytes(backup_path().read_bytes())
    except OSError:
        bak = None
    if bak is not None and not _is_future(bak):
        return _Loaded(_parse_state(bak), STATUS_RECOVERED)
    return _Loaded(_empty(), STATUS_DEGRADED)


def _read() -> tuple[dict, str]:
    """读整份状态，回 `(state, status)`，status 见模块文档的四档。"""
    loaded = _load()
    return loaded.state, loaded.status


def _write(state: dict) -> None:
    reason = state.get("_readonly")
    if reason:
        raise SessionStateUnavailable(f"会话状态此刻不能安全写回（{reason}）")
    payload = {
        **dict(sorted(state.get("_extra", {}).items())),
        "version": FORMAT_VERSION,
        "projects": dict(sorted(state["projects"].items())),
        "no_project": state["no_project"],
        "owners": dict(sorted(state["owners"].items())),
        "owner_revisions": dict(sorted(state["owner_revisions"].items())),
    }
    atomicio.write_json(state_path(), payload, indent=1)
    # 同 payload 再留一份：主文件日后坏了，这是上一份好状态。bak 写不出来不影响这次提交
    try:
        atomicio.write_json(backup_path(), payload, indent=1)
    except OSError as exc:
        LOG.warning("会话状态备份写不出来（%s）", exc)


def _group(state: dict, key: str | None) -> dict:
    if key is None:
        return state["no_project"]
    return state["projects"].setdefault(key, {})


def _group_view(state: dict, key: str | None) -> dict:
    """只读地取一组；没有就回一个临时空组（不往 state 里建）。"""
    return state["no_project"] if key is None else state["projects"].get(key, {})


def _public(state: dict) -> dict:
    return {k: v for k, v in state.items() if not k.startswith("_")}


def snapshot() -> dict:
    """整份状态的一份拷贝（测试与诊断用；不回给前端——里面有项目路径）。"""
    with _LOCK:
        state, _status = _read()
    return _public(state)


def read_status() -> str:
    """当前读取结果四档之一。不写任何东西（不留损坏副本、不建锁文件）。

    只读路径**不拿锁**：主文件只会被 `atomicio` 整份原子替换，读到的永远是完整的一份（旧或新）。"""
    return _load(preserve=False).status


def read_readonly() -> tuple[dict, str]:
    """`(state, status)`，**不写任何东西、不拿锁**——候选发现这类只读端点用。"""
    loaded = _load(preserve=False)
    return _public(loaded.state), loaded.status


# ------------------------------- 归属判据 ----------------------------------


class ForeignLayoutError(Exception):
    """这份排版的槽位记在**别的项目**名下，不能记成这个项目「上次开着的」。"""


def owner_conflict(state: dict, doc_id: str, key: str | None) -> bool:
    """槽位 `doc_id` **确知**属于别的项目（`key` 为 `None` = 没开项目那一组）。

    「确知」= owners 里有这条记录且不是 `key`；没有记录是「不知道」，不算冲突（第一次自动保存
    之前就会先记 last）。前端的同一条判据是 `web/src/lib/docOwnership.isForeignDocument`（按本机
    「最近文档」索引），两侧互为纵深（#715 验收 P1：稳定端口之后两个项目共用一个 origin，前端把
    项目 F 的排版当成新项目 G 的「上次开着的」装进来并记下，而这里的 owners 明明记着它属于 F）。

    本函数只看记录；**记录是否还可信**（`owner_revisions` 与槽位当前 revision）由调用方先用
    `owner_is_stale` 判，待核对的归属按「不知道」处理。
    """
    owners_ = state["owners"]
    return doc_id in owners_ and owners_[doc_id] != key


def owner_is_stale(state: dict, doc_id: str, current_revision: str | None) -> bool:
    """归属记录与槽位正文对不上：记下归属那一刻的 revision ≠ 槽位现在的 revision。

    没有 `owner_revisions` 记录（旧记录）或拿不到当前 revision（槽位不在 / 读不出）回 False：
    按现状信任——这是兼容边界，不是保证。"""
    recorded = state["owner_revisions"].get(doc_id)
    return recorded is not None and current_revision is not None and recorded != current_revision


def owner_status_in(
    state: dict, doc_id: str, key: str | None, current_revision: str | None = None
) -> tuple[str | None, bool]:
    """owners 对「槽位是不是 `key` 的」怎么说：`(verdict, verified)`。

    verdict：`"this"` / `"other"` / `"unverified"`（有记录但与槽位正文对不上，待核对）/
    `None`（没有记录 = 不知道）。`verified`：`this`/`other` 且 revision 一致或为旧记录。"""
    owners_ = state["owners"]
    if doc_id not in owners_:
        return None, False
    if owner_is_stale(state, doc_id, current_revision):
        return "unverified", False
    return ("this" if owners_[doc_id] == key else "other"), True


def owner_verdict(doc_id: str, key: str | None, current_revision: str | None = None) -> str | None:
    """`owner_status_in` 的取状态版：`"this"` / `"other"` / `"unverified"` / `None`。"""
    with _LOCK:
        state, _status = _read()
    return owner_status_in(state, doc_id, key, current_revision)[0]


# ------------------------------- last --------------------------------------


def view_for(key: str | None, rev_of: Callable[[str], str | None] | None = None) -> dict:
    """这个项目（`None` = 没开项目）的 `{last, prev, pending, index}`。

    每个指针指着的槽位**确知属于别的项目**时回 `None`（修复之前的版本可能已经把别的项目的排版
    记到这个项目名下，#715 验收 P1）；归属待核对的（`owner_is_stale`）不算「确知」，照回。
    `rev_of(doc_id)` 回槽位当前 revision，在会话锁**外**调用（不在锁内读正文）；不给就不核新鲜度。
    给了 `rev_of` 时每个指针多一个 `revision`：校验用的那份快照的 revision，见上。
    """
    with _LOCK:
        state, status = _read()
    group = _group_view(state, key)
    out: dict[str, Any] = {}
    for name in POINTERS:
        entry = group.get(name)
        if not entry:
            out[name] = None
            continue
        current = None
        if rev_of is not None:
            try:
                current = rev_of(entry["doc_id"])
            except OSError:
                current = None
        # 确知别人的 → 不回；归属待核对（正文与记录对不上）→ 对**任何**项目都不回：不自动恢复
        # 未核对的内容，既不当老主人的也不当别人的（候选发现里 scope=unknown 可找回）
        unusable = owner_conflict(state, entry["doc_id"], key) or owner_is_stale(
            state, entry["doc_id"], current
        )
        if unusable:
            out[name] = None
        else:
            out[name] = dict(entry)
            if rev_of is not None:
                # 校验归属时读到的那一份正文的 revision（槽位不在 / 读不出为 None）。前端取正文时
                # 必须比对它：校验之后别的项目可能又保存了同一个槽位，那份内容没被这次校验认过
                out[name]["revision"] = current
    out["index"] = status
    return out


def last_for(key: str | None, rev_of: Callable[[str], str | None] | None = None) -> dict | None:
    """这个项目上次开着的（durable）排版 `{doc_id, name, at}`；没记过 / 指着别的项目的回 `None`。"""
    return view_for(key, rev_of)["last"]


def _promote(group: dict, entry: dict) -> None:
    """`entry` 成为 durable last：旧 last（若是另一份）退成 prev；pending 清掉（它要么就是 `entry`，
    要么已被这次更晚的 durable 取代）。"""
    cur = group.get("last")
    if cur and cur["doc_id"] != entry["doc_id"]:
        group["prev"] = cur
    group["last"] = entry
    group.pop("pending", None)


def _now_ms() -> int:
    return int(time.time() * 1000)


def set_last(
    key: str | None,
    doc_id: str,
    name: str,
    *,
    slot_ok: bool = True,
    current_revision: str | None = None,
    slot_check: Callable[[], bool] | None = None,
) -> dict:
    """记「这个项目现在想打开这份排版」。

    `slot_ok`：调用方（app，在槽位的文档锁里、会话锁外）验证过槽位文件存在且过了
    `loads_document` + `validate_document`。

    * True → durable：`prev ← 旧 last`（若是另一份）、`last ← 新`，指着同一份的 pending 清掉；
      回 `{"last": ..., "state": "durable"}`；
    * False → 只写 `pending`（替换旧 pending），**durable last 不动**——否则旧前端「先 PUT last、
      再首次自动保存」的正常顺序会留下一个指着不存在槽位的 last；回
      `{"last": <durable 或 None>, "pending": ..., "state": "pending"}`。

    `current_revision`：保留的参数，不再影响冲突判据（待核对的归属照样算冲突）。
    `slot_check`：在**持着跨进程会话锁、发布 durable last 之前**再看一眼槽位还是不是验证过的那一份
    （app 传一个只 stat 的回调：文件在、`(mtime_ns, size)` 没变）；不是 → 按 pending 记，不发布 durable。
    它和槽位删除（`delete_slot` / `remove_slot_unless_protected` 在会话锁里 unlink）被同一把跨进程锁串行：
    另一个进程在「验证」与「发布」之间删掉槽位，这里要么看到它已没了（记 pending），要么它在我们之后才删
    （此时 last 已发布、删除方在锁里重判保护，删不掉）。回调里不许解析正文、不许取文档锁。
    `ValueError`：doc_id 形状不对 / name 不是字符串。`ForeignLayoutError`：槽位确知属于别的项目
    （`owner_conflict`），一个字节都不写。`SessionStateUnavailable` / `atomicio.AtomicWriteError`：写不进去。
    """
    if not valid_doc_id(doc_id):
        raise ValueError("doc_id 形状不对")
    if not isinstance(name, str):
        raise ValueError("name 必须是字符串")
    name = name[:MAX_NAME_CHARS]
    with _LOCK:
        state, _status = _read()
        # 待核对的归属**不**解除冲突：记录说是别的项目的，正文被动过也不能让另一个项目认领它
        if owner_conflict(state, doc_id, key):
            raise ForeignLayoutError(doc_id)
        group = _group(state, key)
        cur = group.get("last")
        pend = group.get("pending")
        if slot_ok and slot_check is not None and not slot_check():
            slot_ok = False
        if slot_ok:
            changed = False
            if cur and cur["doc_id"] == doc_id:
                if cur["name"] != name:
                    group["last"] = {"doc_id": doc_id, "name": name, "at": _now_ms()}
                    changed = True
            else:
                _promote(group, {"doc_id": doc_id, "name": name, "at": _now_ms()})
                changed = True
            # pending 只是「最新的、还没 durable 的意图」：更晚的 durable 打开取代它，不管它指着谁
            # （否则 A→B(pending)→A(durable) 之后 B 的首次保存会把过期的 pending 晋升、顶掉 A）
            if pend:
                group.pop("pending", None)
                changed = True
            if changed:
                _write(state)
            return {"last": dict(group["last"]), "state": "durable"}
        if not (pend and pend["doc_id"] == doc_id and pend["name"] == name):
            group["pending"] = {"doc_id": doc_id, "name": name, "at": _now_ms()}
            _write(state)
        return {
            "last": dict(cur) if cur else None,
            "pending": dict(group["pending"]),
            "state": "pending",
        }


# ------------------------------- owners ------------------------------------


def commit_slot(doc_id: str, key: str | None | Any, revision: str) -> dict:
    """正文刚落盘成功的**同一个提交**：记归属、记归属对应的正文 revision、晋升匹配的 pending。

    调用方（`PUT /api/autosave/<id>`）在槽位的 `_document_lock` 里调，所以「这份正文是谁的」与
    「正文本身」按同一个顺序提交——旧请求的元数据不会在新正文之后把归属改回去（锁顺序：文档锁→
    会话锁）。**这不是跨文件崩溃事务**：进程死在两个文件之间时 `owner_revisions` 与槽位对不上，
    该归属被当成待核对（`owner_is_stale`）。

    `key` 是 `UNKNOWN`（pj 已失效，归属不知道）：什么都不记、不晋升。
    回 `{"owner": "recorded"|"unchanged"|"skipped", "promoted": bool}`。
    `SessionStateUnavailable` / `AtomicWriteError`（都是 `OSError`）：元数据没提交，正文不受影响。
    """
    if key is UNKNOWN or not valid_doc_id(doc_id) or not isinstance(revision, str) or not revision:
        return {"owner": "skipped", "promoted": False}
    with _LOCK:
        state, _status = _read()
        owners_ = state["owners"]
        revisions = state["owner_revisions"]
        changed = False
        result = "unchanged"
        if not (doc_id in owners_ and owners_[doc_id] == key and revisions.get(doc_id) == revision):
            owners_[doc_id] = key
            revisions[doc_id] = revision
            changed = True
            result = "recorded"
        promoted = False
        group = _group_view(state, key)
        pend = group.get("pending")
        if pend and pend["doc_id"] == doc_id:
            _promote(group, {**pend, "at": _now_ms()})
            changed = True
            promoted = True
        if changed:
            _write(state)
        return {"owner": result, "promoted": promoted}


def record_owner(doc_id: str, key: str | None, revision: str | None = None) -> bool:
    """记下自动保存槽位 `doc_id` 属于哪个项目（`None` = 没开项目）。已经是它就不写，回 False。

    不带 `revision` 的是旧接口（没有正文对照的归属）：归属变了就丢掉旧的 `owner_revisions`。
    app 的保存路径走 `commit_slot`，不走这里。
    """
    if not valid_doc_id(doc_id):
        return False
    with _LOCK:
        state, _status = _read()
        owners_ = state["owners"]
        revisions = state["owner_revisions"]
        same_owner = doc_id in owners_ and owners_[doc_id] == key
        if same_owner and (revision is None or revisions.get(doc_id) == revision):
            return False
        owners_[doc_id] = key
        if revision is not None:
            revisions[doc_id] = revision
        else:
            revisions.pop(doc_id, None)
        _write(state)
        return True


def owners() -> dict[str, str | None]:
    with _LOCK:
        state, _status = _read()
    return dict(state["owners"])


def _protected(state: dict) -> set[str]:
    ids: set[str] = set()
    for group in [*state["projects"].values(), state["no_project"]]:
        for name in POINTERS:
            entry = group.get(name)
            if entry:
                ids.add(entry["doc_id"])
    return ids


def protected_doc_ids() -> set[str]:
    """各组 `last` / `prev` / `pending` 指着的排版：槽位清理永不删它们（删了就是下次启动的「找不到上次排版」）。

    这只是**一张快照**：拿到之后别的请求随时可能把某个旧槽位记成新的 last。真正删槽位的
    那一下要走 `remove_slot_unless_protected`，在同一把锁里重判。"""
    with _LOCK:
        state, _status = _read()
    return _protected(state)


def _forget_locked(state: dict, gone: set[str]) -> tuple[list[str], bool]:
    """在 `_LOCK` 里改 `state`：删掉这些槽位的归属与指向它们的 last / prev / pending。回（删掉的归属，改没改）。"""
    removed = sorted(d for d in gone if d in state["owners"])
    for d in removed:
        del state["owners"][d]
    changed = bool(removed)
    for d in gone:
        if d in state["owner_revisions"]:
            del state["owner_revisions"][d]
            changed = True
    for group in [*state["projects"].values(), state["no_project"]]:
        for name in POINTERS:
            entry = group.get(name)
            if entry and entry["doc_id"] in gone:
                del group[name]
                changed = True
    state["projects"] = {k: g for k, g in state["projects"].items() if g}
    return removed, changed


def forget_documents(doc_ids: Iterable[str]) -> list[str]:
    """这些槽位没了（被删 / 被清理）：删掉它们的归属，以及指向它们的 last / prev / pending 记录。"""
    gone = {d for d in doc_ids if isinstance(d, str)}
    if not gone:
        return []
    with _LOCK:
        state, _status = _read()
        removed, changed = _forget_locked(state, gone)
        if changed:
            _write(state)
        return removed


def remove_slot_unless_protected(doc_id: str, remove: Callable[[], bool]) -> bool:
    """清理删一个槽位：**同一把锁里**重判「它此刻是不是某组的 last / prev / pending」、删文件、忘掉它的记录。

    `protected_doc_ids()` 的快照拿到之后，并发的 `set_last` 可能正把这个旧槽位记成某个项目
    的新 last；快照判、事后删，就会删掉那份并在 `forget_documents` 里把刚写的 last 一起抹掉
    （#719 Codex P1）。在这里判与删之间 `set_last` 插不进来：要么它先写（这里看到、不删），
    要么它后写（槽位已删、记录已忘，它记下的 last 由那个标签页下一次自动保存重新落盘）。

    读不到可信的状态（`degraded`）时**不删**：不知道哪些槽位受保护，就当都受保护。

    `remove()` 真删文件，回是否删了（调用方在文件自己的锁里、带 mtime 重判）。回是否删了。
    会话状态写不进去不影响「文件已删」这个事实：只记日志，孤儿记录由下一次清理的
    `drop_owners_not_in` 收拾。
    """
    with _LOCK:
        state, status = _read()
        if status == STATUS_DEGRADED or doc_id in _protected(state):
            return False
        if not remove():
            return False
        _removed, changed = _forget_locked(state, {doc_id})
        if changed:
            try:
                _write(state)
            except OSError as exc:
                LOG.warning("清理后会话状态没跟上（%s）", exc)
        return True


def delete_slot(doc_id: str, remove: Callable[[], bool]) -> bool:
    """用户明确删一个槽位（`DELETE /api/autosave/<id>`）：**在跨进程会话锁里** unlink、忘掉它的记录。

    与 `remove_slot_unless_protected` 的区别：显式删除不受保护（用户要它没了），但同样把「删」放进
    会话锁，和别的进程的 `set_last` 发布串行。`remove()` 真删文件，回是否已不在；会话状态写不进去
    只记日志（文件已删是事实）。锁拿不到抛 `SessionStateUnavailable`，此时文件没动。"""
    with _LOCK:
        state, _status = _read()
        gone = remove()
        if gone:
            _removed, changed = _forget_locked(state, {doc_id})
            if changed:
                try:
                    _write(state)
                except OSError as exc:
                    LOG.warning("删除槽位后会话状态没跟上（%s）", exc)
        return gone


def drop_owners_not_in(existing: set[str], still_exists: Callable[[str], bool]) -> int:
    """把磁盘上已经不在的槽位从 owners 里拿掉（槽位被外部删掉时 owners 不会自己缩）。

    `existing` 是调用方扫描目录时的快照，只用来挑**候选**；真正删之前在会话状态锁里用
    `still_exists(doc_id)` 当场再看一眼文件：快照之后并发的自动保存可能刚建出那个槽位并记下
    归属，按快照删会让它永远没有归属、从此不进配额（#719 Codex P2）。`commit_slot` 持同一把锁，
    所以「重看 → 删」之间插不进新的归属。
    """
    with _LOCK:
        state, status = _read()
        if status == STATUS_DEGRADED:
            return 0
        stale = [d for d in state["owners"] if d not in existing and not still_exists(d)]
        if not stale:
            return 0
        for d in stale:
            del state["owners"][d]
            state["owner_revisions"].pop(d, None)
        _write(state)
        return len(stale)


def forget_project(key: str | None, doc_ids: Iterable[str] = ()) -> bool:
    """清掉一个项目的 last / prev / pending 与给定槽位的归属（教程重置用，ADR 0039 §5）。"""
    gone = {d for d in doc_ids if isinstance(d, str)}
    with _LOCK:
        state, _status = _read()
        changed = False
        if key is None:
            if state["no_project"]:
                state["no_project"] = {}
                changed = True
        elif key in state["projects"]:
            del state["projects"][key]
            changed = True
        for d in gone:
            if d in state["owners"]:
                del state["owners"][d]
                changed = True
            if d in state["owner_revisions"]:
                del state["owner_revisions"][d]
                changed = True
        if changed:
            _write(state)
        return changed


# ------------------------------- 导出默认值 ----------------------------------


def read_export_defaults() -> dict | None:
    """数据目录里的导出默认值；没存过 / 坏了回 `None`（前端退回它自己的默认值）。"""
    with _PREFS_LOCK:
        try:
            raw = export_defaults_path().read_bytes()
        except OSError:
            return None
        try:
            data = documents.loads_document(raw)
        except ValueError:
            return None
    value = data.get("defaults") if isinstance(data, dict) else None
    return value if isinstance(value, dict) else None


def write_export_defaults(value: object) -> dict:
    """整份替换导出默认值。`ValueError`：不是对象 / 太大 / 含非有限数。"""
    if not isinstance(value, dict):
        raise ValueError("导出默认值必须是 JSON 对象")
    try:
        size = len(atomicio.dumps_json(value))
    except atomicio.AtomicWriteError as exc:
        raise ValueError(str(exc)) from exc
    if size > MAX_EXPORT_DEFAULTS_BYTES:
        raise ValueError("导出默认值太大")
    with _PREFS_LOCK:
        atomicio.write_json(
            export_defaults_path(), {"version": FORMAT_VERSION, "defaults": value}, indent=1
        )
    return value
