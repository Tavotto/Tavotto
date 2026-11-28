"""把「MSIX 宿主私有区」里的旧数据补回真实目录（一次性，无界面，issue #1574 的善后）。

背景：#1576 之前，从 Codex / Claude 桌面版（MSIX 包）拉起的 Tavotto 带包身份，Windows 把它对
`%APPDATA%\\Tavotto` 与 `%LOCALAPPDATA%\\Tavotto` 的**新建**写入重定向到
`%LOCALAPPDATA%\\Packages\\<包族名>\\LocalCache\\{Roaming,Local}\\Tavotto`。#1576 起 Tavotto 一律脱包运行、
写真实目录，于是升级后用户看不到原来的最近项目 / 设置 / AI 连接 / AI 历史。本模块把那些补回来。

规则（用户拍板的方案 B）：

* **只在 Windows、只在无包身份的进程里迁**（`has_package_identity()` 为真时写也会被重定向，迁了等于白迁）；
* 只扫 `%LOCALAPPDATA%\\Packages\\*\\LocalCache\\{Roaming,Local}\\Tavotto`；多个包按目录 mtime 新→旧依次处理，
  后者只补前者没补到的；
* **只补真实目录里没有的文件，从不覆盖，原件不删**；先写临时名再改名，中途失败不留半截文件；
* `config.json`：真实处没有就整份带过去；已有则**保守合并**（只补缺：最近 / 收藏并集真实在前、`projects` 缺的条目、
  `ai.providers` 按 id 缺的记录、`ai.agents` 缺的 agent、`ai` 其它缺的键），已有的值一概不改。走
  `config.transaction()`（带锁的读-改-写入口）；真实处的文件读不出来就不碰；
* **迁什么由白名单定**（`EAGER_ITEMS`），不在名单里的不搬：渲染缓存（`cache/engine|mathtext|runtime|probe|native|…`）、
  `app.log`、`probe-results/`、`perf-reports/`、`session/`（活的端口 / 令牌文件）、`codex-marketplace/`、
  `private-python/` 的下载缓存、`cache/exec-trace/` 都可再生或属于运行态，一概不搬；
* 大目录**不在启动握手前同步拷**：`environments/<项目指纹>/`、`cli/<名>/`（及受管环境所依赖的
  `private-python/runtimes/<id>/`）在**用到时**才由 `adopt_dir()` 搬——调用点是 `managedenv.env_dir` 与
  `managedcli.root_dir`；复制到临时名、校验通过后原子改名，校验不过就当没有，走原来的创建 / 下载；
* **幂等**：`state/msix-migration-v1.json` 是完成标记（闭集事实：几个源、拷了几个、跳过几个已存在、失败几个、是否合并了
  config、按需搬了几个目录）；有标记且 `complete` 就不再扫。同步阶段有时间预算，超了不写 `complete`，下次启动接着补；
* 跨进程文件锁 `state/msix-migration.lock`（与会话状态锁同一份实现）：两个进程同时启动只有一个在迁；
* 任何失败都只记计数、不抛、不阻塞启动、不弹界面。

纯标准库（Flask 父进程与 CLI 入口都会 import 它）。诊断包只带 `diagnostic_facts()` 的数字与闭集码，
不带路径、包族名。
"""

from __future__ import annotations

import contextlib
import glob
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from . import config, layoutsession, msixbreakaway

LOG = logging.getLogger("tavotto.msixmigrate")

SCHEMA = 1
MARKER_REL = ("state", "msix-migration-v1.json")
LOCK_REL = ("state", "msix-migration.lock")
#: 「旧历史待补」：消费者补迁没搬成（超时 / 锁被占 / 出错）、随即新建了空库时记下来。完成标记与重试次数上限都**拦不住**它——
#: 否则空库一建，下次迁移见目标已存在就跳过、随后写 complete，旧历史永远回不来（#2282）。合并在后台线程里做，不卡启动。
PENDING_REL = ("state", "msix-history-pending.json")
TMP_SUFFIX = ".msix-mig.tmp"
#: 同步阶段（启动早期）的时间预算；超了就停手、不写 complete，下次启动接着补
SYNC_BUDGET_S = 8.0
#: 「启动早期会被消费者新建替代文件」的项（SQLite 历史库）的独立时间上限：排在同步阶段最前、不吃上面那份预算，
#: 但自己也有上限，绝不无限阻塞启动
CRITICAL_BUDGET_S = 4.0
#: 消费者（`ai_history._connect`）发现库不存在时现场补迁的上限
ADOPT_SQLITE_BUDGET_S = 3.0
#: 后台补回「待补」旧历史的上限：不在启动关键路径上，给足；到点整体回滚，下次启动再来（不计失败）
PENDING_MERGE_BUDGET_S = 120.0
#: 「待补」合并真失败（快照取不出 / 库坏）最多试几次启动；超了放弃，免得坏源每次启动都白跑一遍
PENDING_MAX_FAILURES = 5
#: 有失败（拷贝 / config 合并出错）时最多重试几次启动；超了就标 complete（永久性失败不再每次启动都扫），失败计数留在标记里
MAX_ATTEMPTS = 3

#: 启动早期同步迁的数据目录条目（相对 `data_dir()`，POSIX 写法）。**白名单**：不在这里的不搬。
#: 用户产出 / 历史 / 项目相关状态都在这；可再生的缓存与运行态不在。
EAGER_ITEMS = (
    "origin",  # 原图备份（`originrecord`）
    "state",  # 版本会话、导出默认值等（见 EXCLUDE_NAMES 的例外）
    "ai",  # fix_audit.jsonl
    "source-backed",  # 来源驱动采用的登记
    "script_backups",  # 脚本改写前的镜像备份
    "runconfigs",
    "inputtranscripts",
    "scriptanswer-contexts",
    "profiles",  # 用户样式 / 规范库
    "layouts",  # 旧版 `_styles.json`
    "exports",  # 没有项目时的导出落点
    "contact_messages.json",
    "contact_assets",
    "mcp-sessions",
    "cache/ai_snapshots",
    "cache/fix_snapshots",
)
#: SQLite 数据库：用 sqlite3 备份接口取一致快照（源库可能还有未并入主文件的 WAL）
SQLITE_ITEMS = ("cache/ai_history.sqlite3",)
#: 按需搬的目录（相对 `data_dir()` 的前两段以内；真正的触发点见 `adopt_dir` 的调用方）
LAZY_ROOTS = ("environments", "cli", "private-python")
#: 白名单目录内部也不搬的文件名（运行态 / 临时 / 迁移自己的账）
EXCLUDE_NAMES = frozenset(
    {"tutorial-cleanup-pending", "msix-migration-v1.json", "msix-history-pending.json"}
)
EXCLUDE_SUFFIXES = (".lock", ".tmp", ".part", "-wal", "-shm", "-journal")

_ADOPT_LOCK = threading.RLock()  # 校验里可能再按需搬私有 Python（同线程重入）
_ADOPT_CHECKED: set[str] = set()
_SQLITE_CHECKED: set[str] = set()
#: 进程内串行化 `_adopt_sqlite`：第一个线程把目标标成「已查」后还在搬，第二个线程若直接读到「已查」就返回、随即新建空库，
#: 第一个线程的 `os.replace` 前看到目标已存在便放弃——旧历史永远搬不回来（心跳线程与启动线程同时首次开库时真会发生）。
#: 后到的线程在这里等先到的搬完，再看目标在不在。
_SQLITE_ADOPT_LOCK = threading.Lock()


class _Deadline(Exception):
    """SQLite 备份超过截止时间（内部信号，不外泄）。"""


# --------------------------------------------------------------- 资格与源
def _is_windows() -> bool:
    return os.name == "nt"


def _eligible() -> bool:
    return _is_windows() and not msixbreakaway.has_package_identity()


def sources() -> list[tuple[Path | None, Path | None]]:
    """所有包私有区里的 Tavotto 目录 `(Roaming 那份, Local 那份)`，按目录 mtime 新→旧。非 Windows 回空。"""
    if not _is_windows():
        return []
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    found: list[tuple[float, Path | None, Path | None]] = []
    pattern = os.path.join(glob.escape(base), "Packages", "*", "LocalCache")
    for cache in glob.glob(pattern):
        roaming = Path(cache) / "Roaming" / "Tavotto"
        local = Path(cache) / "Local" / "Tavotto"
        r = roaming if roaming.is_dir() else None
        loc = local if local.is_dir() else None
        if r is None and loc is None:
            continue
        mtime = 0.0
        for d in (r, loc):
            if d is not None:
                with contextlib.suppress(OSError):
                    mtime = max(mtime, d.stat().st_mtime)
        found.append((mtime, r, loc))
    found.sort(key=lambda item: item[0], reverse=True)
    return [(r, loc) for _m, r, loc in found]


# --------------------------------------------------------------- 完成标记
def marker_path() -> Path:
    return config.data_path(*MARKER_REL)


def _read_marker() -> dict | None:
    try:
        data = json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def _write_marker(data: dict) -> None:
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}{TMP_SUFFIX}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _pending_path() -> Path:
    return config.data_path(*PENDING_REL)


def _read_pending() -> dict | None:
    try:
        data = json.loads(_pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (
        isinstance(data, dict)
        and data.get("schema") == SCHEMA
        and isinstance(data.get("items"), dict)
    ):
        return None
    return data if data["items"] else None


def _write_pending(data: dict | None) -> None:
    path = _pending_path()
    if not data or not data.get("items"):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}{TMP_SUFFIX}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _bump_marker(**incr: int) -> None:
    """按需搬运的计数记进标记（读-改-写；没有标记就新建一份 complete=False 的）。"""
    try:
        data = _read_marker() or {
            "schema": SCHEMA,
            "complete": False,
            "sources": 0,
            "copied": 0,
            "skipped_existing": 0,
            "failed": 0,
            "config_merged": False,
        }
        for key, n in incr.items():
            data[key] = _count(data.get(key)) + n
        _write_marker(data)
    except OSError:
        pass


def diagnostic_facts() -> dict:
    """诊断包用的闭集事实：只有数字、布尔与闭集 `state`（none / partial / done），不带路径与包族名。"""
    data = _read_marker()
    if data is None:
        return {"state": "none"}
    return {
        "state": "done" if data.get("complete") is True else "partial",
        "sources": _count(data.get("sources")),
        "copied": _count(data.get("copied")),
        "skipped_existing": _count(data.get("skipped_existing")),
        "failed": _count(data.get("failed")),
        "attempts": _count(data.get("attempts")),
        "config_merged": data.get("config_merged") is True,
        "adopted_dirs": _count(data.get("adopted_dirs")),
        "adopt_failed": _count(data.get("adopt_failed")),
    }


# --------------------------------------------------------------- 文件复制
class _Tally:
    def __init__(self) -> None:
        self.copied = 0
        self.skipped = 0
        self.failed = 0
        self.deferred = 0  # 没做完、下次再试的项（超时 / 源库被占），不算失败


def _excluded(name: str) -> bool:
    return name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES)


def _copy_file(src: Path, dst: Path, tally: _Tally) -> None:
    """src → dst：dst 已有就跳过（绝不覆盖）；否则写临时名再改名。失败记数、不留半截。"""
    try:
        if os.path.lexists(dst):
            tally.skipped += 1
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        tally.failed += 1
        return
    tmp = dst.with_name(dst.name + f".{os.getpid()}{TMP_SUFFIX}")
    try:
        shutil.copy2(src, tmp)
        if os.path.lexists(dst):  # 复制期间别人写出来了：不盖
            tally.skipped += 1
            return
        os.replace(tmp, dst)
        tally.copied += 1
    except OSError:
        tally.failed += 1
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _backup_sqlite(src: Path, tmp: Path, deadline: float) -> None:
    """用备份接口把 src 取成一致快照写到 tmp；分步 + 进度回调检查截止时间，到点抛 `_Deadline`。

    源库被独占事务持有时，`backup()` 会在 BUSY 上睡眠重试——每次重试都会走进度回调，所以这里能按时中止。
    """

    def check(_status, _remaining, _total) -> None:
        if time.monotonic() > deadline:
            raise _Deadline

    if time.monotonic() > deadline:
        raise _Deadline
    with contextlib.closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as s:
        with contextlib.closing(sqlite3.connect(str(tmp))) as d:
            try:
                s.backup(d, pages=64, progress=check, sleep=0.05)
            except sqlite3.Error:
                if time.monotonic() > deadline:  # 超时附近的 BUSY / LOCKED 一并算「没做完」
                    raise _Deadline from None
                raise


def _copy_sqlite(src: Path, dst: Path, tally: _Tally, deadline: float) -> bool:
    """SQLite：只通过备份接口取一致快照（含 WAL 里未并入的部分），绝不单独复制主文件。

    受 `deadline` 约束：到点中止、清掉半截临时文件、记为 deferred（下次再试），返回 False。
    """
    try:
        if os.path.lexists(dst):
            tally.skipped += 1
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        tally.failed += 1
        return True
    tmp = dst.with_name(dst.name + f".{os.getpid()}{TMP_SUFFIX}")
    try:
        _backup_sqlite(src, tmp, deadline)
        if os.path.lexists(dst):
            tally.skipped += 1
            return True
        os.replace(tmp, dst)
        tally.copied += 1
    except _Deadline:
        tally.deferred += 1
        return False
    except sqlite3.Error as exc:
        if _is_lock_contention(exc):
            tally.deferred += 1
            return False
        tally.failed += 1
    except OSError:
        tally.failed += 1
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
    return True


def _copy_tree_missing(src_root: Path, dst_root: Path, tally: _Tally, deadline: float) -> bool:
    """递归补缺。返回 False = 时间预算用尽、中途停手。"""
    for base, dirs, files in os.walk(src_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        rel = Path(base).relative_to(src_root)
        for name in sorted(files):
            if _excluded(name):
                continue
            if time.monotonic() > deadline:
                return False
            s = Path(base) / name
            if s.is_symlink():
                continue
            _copy_file(s, dst_root / rel / name, tally)
    return True


def _copy_critical(local: Path, tally: _Tally) -> bool:
    """先于一切迁「启动早期会被新建」的库（`SQLITE_ITEMS`）。

    `app.main()` 随后的 `ai_history.mark_interrupted_running()` 会在目标位置新建库；目标一旦存在，
    补缺逻辑就永远跳过它——旧历史 / 草稿再也搬不回来。所以这些项排在最前，用独立的 `CRITICAL_BUDGET_S`，
    不吃主预算；没迁成（超时 / 源库被占）时由消费者初始化前的 `adopt_sqlite()` 兜底再试。
    返回 False = 有项没做完。
    """
    target = config.data_dir()
    deadline = time.monotonic() + CRITICAL_BUDGET_S
    done = True
    for item in SQLITE_ITEMS:
        src = local.joinpath(*item.split("/"))
        if src.is_file() and not src.is_symlink():
            if not _copy_sqlite(src, target.joinpath(*item.split("/")), tally, deadline):
                done = False
    return done


def _copy_eager(local: Path, tally: _Tally, deadline: float) -> bool:
    target = config.data_dir()
    for item in EAGER_ITEMS:
        src = local.joinpath(*item.split("/"))
        dst = target.joinpath(*item.split("/"))
        if src.is_dir():
            if not _copy_tree_missing(src, dst, tally, deadline):
                return False
        elif src.is_file() and not src.is_symlink():
            if time.monotonic() > deadline:
                return False
            _copy_file(src, dst, tally)
    return True


def adopt_sqlite(*parts: str, budget_s: float = ADOPT_SQLITE_BUDGET_S) -> bool:
    """消费者（如 `ai_history._connect`）要**新建**库之前调用：真实处还没有就先从迁移源补一份。永不抛。

    与同步阶段同一把跨进程锁（拿不到就算了，别阻塞）；每个进程每个目标只试一次。返回「这次搬成了」。
    """
    try:
        with _SQLITE_ADOPT_LOCK:
            done = _adopt_sqlite(parts, budget_s)
            if not done:
                _note_pending_if_left_behind(parts)
            return done
    except Exception:  # noqa: BLE001
        return False


def _note_pending_if_left_behind(parts: tuple[str, ...]) -> None:
    """补迁没搬成、目标仍不存在、而迁移源里确实有这个库：调用方马上要新建空库——记成「待补」，后台再合并回来。"""
    if not _eligible() or os.path.lexists(config.data_path(*parts)):
        return
    if not any(local is not None and local.joinpath(*parts).is_file() for _r, local in sources()):
        return
    _add_pending(parts)


def _add_pending(parts: tuple[str, ...]) -> None:
    rel = "/".join(parts)
    data = _read_pending() or {"schema": SCHEMA, "items": {}}
    if rel not in data["items"]:
        data["items"][rel] = {"failures": 0}
        _write_pending(data)


def _adopt_sqlite(parts: tuple[str, ...], budget_s: float) -> bool:
    target = config.data_path(*parts)
    key = os.path.normcase(str(target))
    if key in _SQLITE_CHECKED or os.path.lexists(target) or not _eligible():
        return False
    _SQLITE_CHECKED.add(key)
    fd = layoutsession.acquire_file_lock(config.data_path(*LOCK_REL), time.monotonic() + budget_s)
    if fd is None:
        return False
    try:
        if os.path.lexists(target):
            return False
        tally = _Tally()
        deadline = time.monotonic() + budget_s
        left_behind = False
        for _roaming, local in sources():
            src = local.joinpath(*parts) if local is not None else None
            if src is not None and src.is_file() and not src.is_symlink():
                before = tally.copied
                _copy_sqlite(src, target, tally, deadline)
                if tally.copied == before:
                    left_behind = (
                        True  # 这份（更新的）源没搬进来：失败 / 被占 / 超时 / 被消费者抢先建库
                    )
                if os.path.lexists(target):
                    break
        if left_behind and os.path.lexists(target):
            # 更新的源没搬成，却由更旧的包（或消费者新建的库）把目标建出来了：目标一存在，普通补缺就永远跳过它。
            # 必须记成「待补」，后台再把没搬成的新历史合并回来（Codex #2285 P1，L-121 同一失败模式）。
            _add_pending(parts)
        return tally.copied > 0
    finally:
        layoutsession.release_file_lock(fd)


# --------------------------------------------------------------- config 合并
def _entries_union(real: list, extra: list, cap: int) -> list:
    seen = {e.get("path") for e in real if isinstance(e, dict)}
    out = list(real)
    for e in extra:
        if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"] not in seen:
            seen.add(e["path"])
            out.append(e)
    return out[:cap]


def _merge_ai(real: dict, extra: dict) -> dict:
    out = dict(real)
    for key, val in extra.items():
        if key == "providers" and isinstance(val, list):
            have = out.get("providers")
            have = list(have) if isinstance(have, list) else []
            ids = {p.get("id") for p in have if isinstance(p, dict)}
            for rec in val:
                if isinstance(rec, dict) and rec.get("id") and rec["id"] not in ids:
                    ids.add(rec["id"])
                    have.append(rec)
            out["providers"] = have
        elif key == "agents" and isinstance(val, dict):
            have = out.get("agents")
            have = dict(have) if isinstance(have, dict) else {}
            for agent_id, rec in val.items():
                have.setdefault(agent_id, rec)
            out["agents"] = have
        elif key not in out:
            out[key] = val
    return out


def merge_config_into(cfg: dict, extra: dict) -> bool:
    """把 `extra`（迁移源的配置）里 `cfg` 缺的东西补进 `cfg`；已有的值不改。回「有没有补」。"""
    before = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    cfg["recent_projects"] = _entries_union(
        cfg.get("recent_projects") or [], extra.get("recent_projects") or [], config.RECENT_KEEP
    )
    cfg["pinned_projects"] = _entries_union(
        cfg.get("pinned_projects") or [], extra.get("pinned_projects") or [], config.PINNED_KEEP
    )
    projects = dict(cfg.get("projects") or {})
    for key, val in (extra.get("projects") or {}).items():
        if isinstance(key, str) and isinstance(val, dict):
            projects.setdefault(key, val)
    cfg["projects"] = projects
    if isinstance(extra.get("ai"), dict):
        cfg["ai"] = _merge_ai(cfg.get("ai") or {}, extra["ai"])
    return json.dumps(cfg, sort_keys=True, ensure_ascii=False) != before


def _merge_config(roaming: Path | None) -> tuple[bool, bool]:
    """返回 (合并成功并改了东西, 出错)。真实处文件读不出来就不碰（transaction 的 save 会覆盖它）。"""
    if roaming is None:
        return False, False
    src = roaming / "config.json"
    if not src.is_file():
        return False, False
    try:
        extra = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(extra, dict):
            return False, True
        real_path = config.config_path()
        real_exists = real_path.is_file()
        if real_exists:
            real_raw = json.loads(real_path.read_text(encoding="utf-8"))
            if not isinstance(real_raw, dict):
                return False, True
        with config.transaction() as cfg:
            changed = merge_config_into(cfg, extra)
            if not real_exists:
                # 真实处还没有配置：整份带过去（load() 认得的其余段也收下）
                for key in ("updates", "worker", "telemetry", "experiments"):
                    if isinstance(extra.get(key), dict) and not cfg.get(key):
                        cfg[key] = extra[key]
                        changed = True
        return changed, False
    except (OSError, ValueError):
        return False, True


# --------------------------------------------------------------- 入口
def run_once(*, budget_s: float = SYNC_BUDGET_S) -> dict | None:
    """启动早期同步迁一次；返回本次的计数字典（没动手 / 不适用回 None）。永不抛。"""
    try:
        return _run_once(budget_s)
    except Exception:  # noqa: BLE001 —— 迁移绝不阻塞启动
        LOG.debug("MSIX 数据迁移意外失败（不影响使用）", exc_info=True)
        return None


def _run_once(budget_s: float) -> dict | None:
    if not _eligible():
        return None
    _kick_pending_merge()  # 在完成标记的提前返回之前：已标 complete 的安装也要能把待补的旧历史合并回来
    marker = _read_marker()
    if marker is not None and marker.get("complete") is True:
        return None
    lock_path = config.data_path(*LOCK_REL)
    try:
        fd = layoutsession.acquire_file_lock(lock_path, time.monotonic() + 0.05)
    except Exception:  # noqa: BLE001 —— 拿不到锁（别的进程在迁 / 文件系统不支持）：这次不迁
        return None
    if fd is None:
        return None
    try:
        marker = _read_marker()  # 拿到锁再看一遍：等锁期间别人可能迁完了
        if marker is not None and marker.get("complete") is True:
            return None
        deadline = time.monotonic() + budget_s
        tally = _Tally()
        srcs = sources()
        merged_any = False
        config_failed = 0
        # 两类「没做完」分开算：
        # - walked=False：同步阶段预算到点、目录还没走完——每次都有进展（只补缺），不计次数，下次接着走；
        # - 有失败 / 关键项被推迟（拷贝失败、config 合并失败、源库被占导致备份超时）——计次数，到 MAX_ATTEMPTS
        #   就停止自动重试（保留失败计数进诊断），永久性问题不拖慢每次启动（Codex #1603 复核）
        walked = True
        for roaming, local in srcs:
            changed, errored = _merge_config(roaming)
            merged_any = merged_any or changed
            config_failed += 1 if errored else 0
            if local is not None:
                _copy_critical(local, tally)  # 失败 / 推迟都记在 tally 里
                if not _copy_eager(local, tally, deadline):
                    walked = False
                    break
        prev = marker or {}
        failed_now = tally.failed + config_failed
        attempts = _count(prev.get("attempts"))
        problems = bool(failed_now or tally.deferred)
        if problems:
            attempts += 1
        complete = walked and (not problems or attempts >= MAX_ATTEMPTS)
        result = {
            "schema": SCHEMA,
            "complete": complete,
            "sources": len(srcs),
            "copied": _count(prev.get("copied")) + tally.copied,
            "skipped_existing": tally.skipped,
            "failed": failed_now,
            "attempts": attempts,
            "config_merged": merged_any or prev.get("config_merged") is True,
            "adopted_dirs": _count(prev.get("adopted_dirs")),
            "adopt_failed": _count(prev.get("adopt_failed")),
        }
        try:
            _write_marker(result)
        except OSError:
            pass
        return result
    finally:
        layoutsession.release_file_lock(fd)


# --------------------------------------------------------------- 按需搬（大目录）
def adopt_dir(*parts: str, validate=None) -> bool:
    """真实 `data_dir()/<parts>` 不存在时，从迁移源里找同名目录搬过来。返回「这次搬成了」。

    复制到同级临时目录 → `validate(临时目录)` 通过 → `os.replace` 成正式名。任一步不过就清掉临时目录、
    当没有（调用方走原来的创建 / 下载）。不覆盖：目标已存在直接返回 False。永不抛。
    """
    try:
        return _adopt_dir(parts, validate)
    except Exception:  # noqa: BLE001
        return False


def _adopt_dir(parts: tuple[str, ...], validate) -> bool:
    target = config.data_path(*parts)
    key = os.path.normcase(str(target))
    if key in _ADOPT_CHECKED or os.path.lexists(target) or not _eligible():
        return False
    with _ADOPT_LOCK:
        if key in _ADOPT_CHECKED or os.path.lexists(target):
            return False
        _ADOPT_CHECKED.add(key)
        marker = _read_marker()
        if marker is not None and marker.get("complete") is True and marker.get("sources") == 0:
            return False
        for _roaming, local in sources():
            if local is None:
                continue
            src = local.joinpath(*parts)
            if not src.is_dir() or src.is_symlink():
                continue
            if _adopt_from(src, target, validate):
                _bump_marker(adopted_dirs=1)
                return True
            _bump_marker(adopt_failed=1)
        return False


def _adopt_from(src: Path, target: Path, validate) -> bool:
    tmp = target.with_name(f".{target.name}.{os.getpid()}{TMP_SUFFIX}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        sizes: dict[str, int] = {}
        for base, dirs, files in os.walk(src, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            rel = Path(base).relative_to(src)
            (tmp / rel).mkdir(parents=True, exist_ok=True)
            for name in files:
                s = Path(base) / name
                if s.is_symlink():
                    continue
                shutil.copy2(s, tmp / rel / name)
                sizes[(rel / name).as_posix()] = s.stat().st_size
        for rel_name, size in sizes.items():  # 逐文件大小核对：拷贝中途出错不会静默带进来
            if (tmp / rel_name).stat().st_size != size:
                raise OSError("size mismatch")
        if validate is not None and not validate(tmp):
            raise OSError("validation failed")
        if os.path.lexists(target):
            return False
        os.replace(tmp, target)
        return True
    except OSError:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def base_runtime_available(home: str) -> bool:
    """受管 venv 的基础解释器目录（`pyvenv.cfg` 的 `home`）可用吗。

    在就是可用；不在、且位于本机 `data_dir()/private-python/runtimes/<id>` 之下（Tavotto 自己的私有 Python，
    当时也被重定向进了包私有区）时，按需把那一个 `<id>` 目录搬回来再核；其它情况（用户自己的 Python 已卸载等）不可用。
    """
    try:
        if Path(home).is_dir():
            return True
        rel = Path(os.path.normpath(home)).relative_to(config.data_dir())
    except (OSError, ValueError):
        return False
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "private-python" or parts[1] != "runtimes":
        return False
    adopt_dir(*parts[:3])
    return Path(home).is_dir()


# --------------------------------------------------------------- 待补旧历史（#2282）
#: SQLite 项「补回旧行」的实现由拥有该库 schema 的模块在 import 时登记（`ai_history` 登记 `merge_legacy`）。
#: 本模块**不** import 它们：`ai_history` 已经 import 本模块，反向再连一条边就成环（tests/test_import_architecture.py）。
_SQLITE_MERGERS: dict = {}
_MERGERS_CHANGED = threading.Condition()


def register_sqlite_merger(rel: str, merge) -> None:
    """`merge(snapshot, target, deadline) -> int`：把旧库快照里新库没有的行补进 target，到点抛 `TimeoutError`。"""
    with _MERGERS_CHANGED:
        _SQLITE_MERGERS[rel] = merge
        _MERGERS_CHANGED.notify_all()


def _merger_for(rel: str, deadline: float):
    """后台线程可能比 `ai_history` 的 import 先跑：在截止时间内等它登记，等不到回 None（调用方当 deferred）。"""
    with _MERGERS_CHANGED:
        _MERGERS_CHANGED.wait_for(
            lambda: rel in _SQLITE_MERGERS, timeout=max(0.0, deadline - time.monotonic())
        )
        return _SQLITE_MERGERS.get(rel)


# 诊断包：旧包私有区的库比真实目录那份多出多少行（#2282 已中招用户的补救 A）。同样由拥有 schema 的模块登记。
#: `count(legacy, target_or_None, deadline) -> dict[str, int]`，只读两边、只回非负整数，到点抛 `TimeoutError`。
_SQLITE_GAP_COUNTERS: dict = {}
LEGACY_GAP_BUDGET_S = 3.0  # 诊断包里所有库、所有旧包加起来的上限：只数不搬，报告不能被它拖住
LEGACY_GAP_MAX_SOURCES = 4


def register_sqlite_gap_counter(rel: str, count) -> None:
    _SQLITE_GAP_COUNTERS[rel] = count


def legacy_gap_facts(budget_s: float = LEGACY_GAP_BUDGET_S) -> dict:
    """每个登记过的库、每个还留着它的旧包（mtime 新→旧）各一项：`state` 闭集 ok / timeout / error，ok 时带计数。

    不带路径与包族名；不写任何东西（两边都只读打开）。数出来的「缺」也可能是用户在新库里自己删掉的——
    这是给人看的事实，不据此自动补。非 Windows 回 `{"state": "not_windows"}`。
    """
    if not _is_windows():
        return {"state": "not_windows"}
    deadline = time.monotonic() + budget_s
    dbs: dict[str, list[dict]] = {}
    for rel, count in sorted(_SQLITE_GAP_COUNTERS.items()):
        parts = tuple(rel.split("/"))
        target = config.data_path(*parts)
        rows: list[dict] = []
        # 先筛「真有这个库」的旧包再截前几个：只有设置、没有历史库的新包不能把留着历史的老包挤出去（Codex #2311）
        srcs = [local.joinpath(*parts) for _roaming, local in sources() if local is not None]
        srcs = [p for p in srcs if p.is_file() and not p.is_symlink()][:LEGACY_GAP_MAX_SOURCES]
        for src in srcs:
            if time.monotonic() > deadline:  # 库很小时 SQLite 根本不会调进度回调：到点先在这里截住
                rows.append({"state": "timeout"})
                continue
            try:
                got = count(src, target if target.is_file() else None, deadline)
                rows.append({"state": "ok", **{k: _count(v) for k, v in got.items()}})
            except TimeoutError:
                rows.append({"state": "timeout"})
            except (OSError, sqlite3.Error, ValueError):
                rows.append({"state": "error"})
        dbs[rel] = rows
    return {"state": "ok", "dbs": dbs}


def legacy_sqlite_paths(rel: str) -> list[Path]:
    """`rel` 在各旧包私有区里的那份（mtime 新→旧）；不合资格（非 Windows / 带包身份）回空。只给只读计数与手动找回用。"""
    if not _eligible():
        return []
    parts = tuple(rel.split("/"))
    out = [local.joinpath(*parts) for _r, local in sources() if local is not None]
    return [p for p in out if p.is_file() and not p.is_symlink()]


def restore_legacy(rel: str, project: str, budget_s: float = PENDING_MERGE_BUDGET_S) -> str:
    """用户手动「找回之前的任务」（#2282 补救 B）：不看「待补」标记，把旧库里真实库没有的行按主键补回来。

    与后台待补合并同一条路（快照 → 登记的合并器，一个事务、到点整体回滚），所以补回的会话取新 rev、
    未结束的收尾。和自动路径的区别只有一条：用户在新库里删掉的、旧库还有的**也会回来**——这是用户点头的。
    **只补 `project` 这一个项目的行**（确认句里的条数只算这个项目；别的项目里用户删掉的不能顺手带回来，Codex #2317）。
    即使真实库还不存在，手动路径也用登记的合并器建库并只导入确认的项目。
    回闭集 done / deferred（超时、锁被占、合并器未登记，可再试）/ failed / not_eligible。
    """
    if not _eligible():
        return "not_eligible"
    deadline = time.monotonic() + budget_s
    if rel not in SQLITE_ITEMS or _merger_for(rel, deadline) is None:
        return "deferred"
    try:
        fd = layoutsession.acquire_file_lock(config.data_path(*LOCK_REL), deadline)
    except layoutsession.SessionStateUnavailable:
        return "deferred"  # 别的进程正在迁移 / 合并：等不到就让用户稍后再点，不报失败
    if fd is None:
        return "deferred"
    try:
        # 只补了一个项目：别的项目的「待补」照旧留给后台合并，不清标记
        return _merge_pending_item(tuple(rel.split("/")), deadline, project=project)
    finally:
        layoutsession.release_file_lock(fd)


_PENDING_THREAD: threading.Thread | None = None
_PENDING_THREAD_LOCK = threading.Lock()


def _kick_pending_merge() -> None:
    """有「待补」就起一个后台线程去合并（每个进程同时最多一个）。不等它：启动照常往下走。"""
    global _PENDING_THREAD
    pending = _read_pending()
    if pending is None:
        return
    # 拥有这个库的模块（ai_history）没在本进程加载就不起线程：CLI（`handoff.cli`）也调 run_once，但不 import ai_history；
    # 起了线程就会拿着迁移锁干等登记，正好挡住随后启动的桌面版同步迁移（d2 #2283 复核）。由加载了它的进程来合并。
    with _MERGERS_CHANGED:
        if any(rel in SQLITE_ITEMS and rel not in _SQLITE_MERGERS for rel in pending["items"]):
            return
    with _PENDING_THREAD_LOCK:
        if _PENDING_THREAD is not None and _PENDING_THREAD.is_alive():
            return
        _PENDING_THREAD = threading.Thread(
            target=merge_pending, name="msix-history-merge", daemon=True
        )
        _PENDING_THREAD.start()


def merge_pending(budget_s: float = PENDING_MERGE_BUDGET_S) -> dict | None:
    """把「待补」的旧历史合并回真实库；回每项结论（`done` / `deferred` / `failed` / `gave_up`），没有待补回 None。永不抛。

    与同步迁移同一把跨进程锁（等它，最多到截止时间）。结论：done → 从待补里去掉；deferred（到点 / 源被占）→ 留着、不计次数；
    failed → 计次数，到 `PENDING_MAX_FAILURES` 放弃（gave_up）。进程被杀在合并中途：合并是一个事务，整体回滚，待补还在。
    """
    try:
        return _merge_pending(budget_s)
    except Exception:  # noqa: BLE001
        LOG.debug("补回 MSIX 旧历史意外失败（不影响使用）", exc_info=True)
        return None


def _merge_pending(budget_s: float) -> dict | None:
    if not _eligible() or _read_pending() is None:
        return None
    deadline = time.monotonic() + budget_s
    # 先确认合并实现都已登记、再拿锁：不拿着迁移锁干等（会挡住别的进程的同步迁移）
    first = _read_pending() or {"items": {}}
    for rel in first["items"]:
        if rel in SQLITE_ITEMS and _merger_for(rel, deadline) is None:
            return {rel: "deferred" for rel in first["items"]}
    fd = layoutsession.acquire_file_lock(config.data_path(*LOCK_REL), deadline)
    if fd is None:
        return None
    try:
        data = _read_pending()
        if data is None:
            return None
        outcomes: dict[str, str] = {}
        for rel, state in list(data["items"].items()):
            parts = tuple(rel.split("/"))
            outcome = _merge_pending_item(parts, deadline) if rel in SQLITE_ITEMS else "done"
            if outcome == "failed":
                failures = _count(state.get("failures") if isinstance(state, dict) else 0) + 1
                if failures >= PENDING_MAX_FAILURES:
                    outcome = "gave_up"
                else:
                    data["items"][rel] = {"failures": failures}
            if outcome in ("done", "gave_up"):
                data["items"].pop(rel, None)
            outcomes[rel] = outcome
        _write_pending(data)
        return outcomes
    finally:
        layoutsession.release_file_lock(fd)


def _merge_pending_item(parts: tuple[str, ...], deadline: float, project: str | None = None) -> str:
    target = config.data_path(*parts)
    srcs = [local.joinpath(*parts) for _r, local in sources() if local is not None]
    srcs = [p for p in srcs if p.is_file() and not p.is_symlink()]
    if not srcs:
        return "done"  # 源没了（用户卸了旧包）：没东西可补
    if project is None and not os.path.lexists(target):
        # 消费者终究没建库：走普通的只补缺复制
        tally = _Tally()
        # 最新源的快照失败时，不能拿更旧的库当作这次待补已经完成。
        _copy_sqlite(srcs[0], target, tally, deadline)
        if tally.copied:
            return "done"
        if tally.deferred:
            return "deferred"
        if tally.failed or not os.path.lexists(target):
            return "failed"
        # 消费者在备份期间建了目标库：只补缺复制没有写入，继续走合并。
    merge = _merger_for("/".join(parts), deadline)
    if merge is None:
        return "deferred"  # 拥有这个库的模块还没加载：不算失败，下次启动再来
    for src in srcs:  # 新→旧；只补缺，后面的源只补前面没有的
        snap = target.with_name(target.name + f".{os.getpid()}.legacy{TMP_SUFFIX}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _backup_sqlite(src, snap, deadline)
            if project is None:
                merge(snap, target, deadline)
            else:
                merge(snap, target, deadline, project=project)
        except (_Deadline, TimeoutError):
            return "deferred"
        except sqlite3.OperationalError as e:
            # 别的 Tavotto 进程占着新库写锁超过 busy_timeout（`database is locked` / `busy`）是暂时的：下次再来、不计失败，
            # 否则连着几次撞上就被当成坏库放弃，旧历史再也回不来（Codex #2283）
            return "deferred" if _is_lock_contention(e) else "failed"
        except (OSError, sqlite3.Error):
            return "failed"
        finally:
            with contextlib.suppress(OSError):
                snap.unlink(missing_ok=True)
    return "done"


#: SQLite 的主错误码（固定值）。`sqlite3.SQLITE_BUSY` 等常量与 `sqlite_errorcode` 属性 3.11 才有——3.10 上直接引用会
#: AttributeError，把真失败也甩出处理器、永远计不进次数（Codex #2283）。扩展码（如 517 BUSY_SNAPSHOT）取低 8 位比。
_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


def _is_lock_contention(e: sqlite3.Error) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in (_SQLITE_BUSY, _SQLITE_LOCKED):
        return True
    msg = str(e).lower()
    return "database is locked" in msg or "database table is locked" in msg or "busy" in msg
