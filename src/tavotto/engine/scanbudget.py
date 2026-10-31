"""有界扫描的预算与「看不全」账本（T02）。

导入即扫描（`engine/projscan.py`）走的是**已有**的两个遍历——脚本 `discover._iter_py`、素材
`project_refresh.iter_assets`——而不是另起一个 walker；这些遍历原先没有任何上限（脚本只有
`MAX_DEPTH`，素材连深度都没有）。本模块只给它们一个共同的**预算 + 账本**：

* 预算：目录项数、脚本数、素材数、累计源码字节、墙钟时间、取消回调；每一项超了都**停下来并留下
  痕迹**，而不是悄悄少给几行；
* 账本（`issues`）：权限读不动、预算用完、占位文件（云盘未下载）、符号链接没跟进、层级太深……
  每条是 `{code, severity, scope, path?, count}`。**路径只写项目相对 POSIX 路径**，不出绝对路径。
  `severity == "partial"` 表示「这里确实有东西没看见」；`"note"` 表示有界设计下的静默剪枝（只在
  「一个脚本都没找到」时才升级成 partial，见 `projscan`）。

「没测量」不是「测量结果是零」（`readiness.py` 同一条纪律）：任何 `partial` 都不许被当成「这个目录
里没有脚本」。

纯标准库、叶子模块（不 import 兄弟模块）：被 `discover` / `project_refresh` / `projscan` / `importscan`（本地模块跟进的读文件与
清单，Import Origin Resolver PR1）共同依赖，
自己不能再依赖它们。
"""

from __future__ import annotations

import io
import ntpath
import os
import re
import stat
import threading
import time
import tokenize
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---- 预算上限（新常量；与既有常量的关系写在旁边） -------------------------------------------------
#: 一次扫描最多检视多少个目录项（文件 + 目录；脚本遍历与素材遍历共用一本账）。
MAX_ENTRIES = 100_000
#: 最多列出多少个 .py。
MAX_SCRIPTS = 400
#: 最多数多少个素材文件。
MAX_ASSETS = 20_000
#: 单个源文件最多读多少字节做 AST。**与 `importscan.MAX_SOURCE_BYTES` 同值**（1 MiB，既有常量）；
#: 本模块是叶子不能 import 它，同值由 `tests/test_project_scan.py` 钉着。
MAX_FILE_BYTES = 1024 * 1024
#: 一次扫描累计最多读多少源码字节。
MAX_SOURCE_BYTES = 32 * 1024 * 1024
#: 素材遍历的最大目录深度（脚本遍历沿用 `discover.MAX_DEPTH`）。
MAX_ASSET_DEPTH = 12
#: 墙钟预算（秒）。
MAX_SECONDS = 20.0
#: 账本最多留多少条（超出的只计数）。
MAX_ISSUES = 40

# ---- 账本 code（闭集；前端按 code 查自己的文案） ------------------------------------------------
ISSUE_UNREADABLE_DIR = "unreadable_dir"  # 目录读不动（权限 / IO）
ISSUE_UNREADABLE_FILE = "unreadable_file"  # 文件读不动
ISSUE_SYMLINK_DIR = "symlinked_dir"  # 保留协议名；链接 / 路径替身的目标未检查
ISSUE_PLACEHOLDER = "placeholder_file"  # 云盘占位文件：不读它（读就是强制下载）
ISSUE_TOO_LARGE = "file_too_large"  # 单个源文件超过 MAX_FILE_BYTES
ISSUE_PARSE_FAILED = "parse_budget"  # AST 解析递归 / 内存失败（病态嵌套）
ISSUE_DEPTH = "depth_limit"  # 目录层级超过上限，没有下探
ISSUE_ENTRIES = "entry_budget"  # 目录项预算用完
ISSUE_SCRIPTS = "script_limit"  # 脚本数超过上限
ISSUE_ASSETS = "asset_limit"  # 素材数超过上限
ISSUE_SOURCE_BYTES = "source_byte_budget"  # 累计源码字节预算用完
ISSUE_TIME = "time_budget"  # 墙钟预算用完
ISSUE_CANCELLED = "cancelled"  # 用户取消了扫描
ISSUE_CODES = (
    ISSUE_UNREADABLE_DIR,
    ISSUE_UNREADABLE_FILE,
    ISSUE_SYMLINK_DIR,
    ISSUE_PLACEHOLDER,
    ISSUE_TOO_LARGE,
    ISSUE_PARSE_FAILED,
    ISSUE_DEPTH,
    ISSUE_ENTRIES,
    ISSUE_SCRIPTS,
    ISSUE_ASSETS,
    ISSUE_SOURCE_BYTES,
    ISSUE_TIME,
    ISSUE_CANCELLED,
)

SEVERITY_PARTIAL = "partial"
SEVERITY_NOTE = "note"

#: 机器级「系统解释器」候选枚举（PATH 逐项、逐级 lstat、`C:\\Python*` scandir……）的**独立子预算**。
#: 它是补充来源，不能拿项目扫描的账：真 Windows runner 上 PATH 很长、hostedtoolcache 多版本、盘根条目多，
#: 曾把整次扫描的预算吃光、降成 partial（#957 windows-exe-smoke）。
#: 条目数 2000：典型机器 PATH ~40 项 × 候选 2~3 个 × 祖先层级（walker 按目录缓存）合计几百次 lstat，
#: 盘根通配枚举几十到一两百项，2000 留约 10 倍余量，又只有总预算（100k）的 2%。
SYSTEM_MAX_ENTRIES = 2_000
#: 时间 3 秒：冷盘 + 杀毒软件下单次 lstat 可到毫秒级，2000 次内的正常量远低于此；超了宁可少列系统候选。
SYSTEM_MAX_SECONDS = 3.0
#: 子预算用尽的账本行用这个 scope：无路径、note 级，且**不参与**「没找到脚本就升级 partial」（见 `projscan.scan`）。
SCOPE_SYSTEM = "system"
#: 机器级路径（PATH / Conda / pyenv / IDE 线索）逐级检查的剪枝：与项目扫描完整性无关
SCOPE_MACHINE = "machine"

#: macOS `SF_DATALESS`（`st_flags`）：文件内容不在本机，读它会触发下载。
_DARWIN_DATALESS = 0x40000000
#: Windows `st_file_attributes`：OFFLINE / RECALL_ON_OPEN / RECALL_ON_DATA_ACCESS。
_WIN_PLACEHOLDER = 0x1000 | 0x40000 | 0x400000


#: Windows 重解析 tag：真正的符号链接。junction（MOUNT_POINT 0xA0000003）、云盘占位、AppExecLink 等都不是它。
_WIN_TAG_SYMLINK = 0xA000000C


def is_symlink(st) -> bool:
    """`st`（lstat 结果）是**符号链接**——POSIX 符号链接，或 Windows 的 `IO_REPARSE_TAG_SYMLINK`。

    junction / mount point / 其它 reparse（云盘占位、AppExecLink）不是：它们要一律拒，不能当符号链接去
    `readlink`（Windows 上 `os.readlink` 对 junction 也读得出来，所以必须先按 tag 区分）。
    CPython 在 Windows 上只给 tag 为 SYMLINK 的 reparse point 置 `S_IFLNK`（3.8 起；`os.path.islink`
    对 junction 为 False，3.12 加的 `isjunction` 才是 junction 的判据）；`st_reparse_tag` 自 3.8 起存在，
    这里再按 tag 复核一次，不单靠 `st_mode`。非 Windows 的 stat 没有 reparse 位，按 `S_ISLNK` 即可。
    """
    if not stat.S_ISLNK(st.st_mode):
        return False
    if (getattr(st, "st_file_attributes", 0) or 0) & 0x400:
        tag = getattr(st, "st_reparse_tag", 0) or 0
        return tag == _WIN_TAG_SYMLINK
    return True


def is_redirect(st) -> bool:
    """只读不跟随的 stat 结果：符号链接、Windows 路径替身（含 junction）不得探目标。

    Windows name-surrogate 位表示重解析点可替换路径；云盘非路径替身的 tag
    保留既有占位文件判据。属性说是 reparse 却没有 tag 时保守跳过，不猜目标类型。
    调用方必须传 lstat / stat(follow_symlinks=False)，不能先 is_dir 再来问。
    """
    if stat.S_ISLNK(st.st_mode):
        return True
    if not (getattr(st, "st_file_attributes", 0) or 0) & 0x400:
        return False
    tag = getattr(st, "st_reparse_tag", 0) or 0
    return tag == 0 or bool(tag & 0x20000000)


def redirected_component(root, path, *, allow_final_link: bool = False) -> str | None:
    """项目派生路径 `path` 在 `root` 之下的每一级里，第一个符号链接 / 路径替身（junction 等）的**项目相对
    POSIX 路径**；干净、不在 `root` 之下（机器级候选）或这一级不存在回 None。

    导入即扫描对项目派生的候选解释器路径，在调用任何会跟随链接的谓词（`is_file` / `exists` / `realpath` /
    `stat` / 指纹 / 起进程）**之前**先问这一句：只做字符串运算与逐级 `lstat`，不碰目标（目标可能是
    攻击者的 UNC / WebDAV 共享，哪怕 `exists` 也会发 NTLM）。`..` 一律按「名字」原样当作被拒（normpath
    会悄悄折叠掉中间那一级的 lstat）。`root` 自己（用户打开的那一层）不查。

    `allow_final_link=True`：最后一级允许是 POSIX 符号链接（venv 的 `bin/python` 本来就是指向基础解释器的
    软链接，不是目录重定向）；Windows 上最后一级的任何重定向仍然拒。不可读（非「不存在」的 OSError）当拒。
    """
    root_n = os.path.normpath(os.fspath(root))
    norm = [os.path.normcase(x) for x in Path(root_n).parts]
    rest: tuple[str, ...] | None = None
    for candidate in (Path(os.fspath(path)).parts, Path(os.path.normpath(os.fspath(path))).parts):
        head = [os.path.normcase(x) for x in candidate[: len(norm)]]
        if len(candidate) > len(norm) and head == norm:
            rest = candidate[len(norm) :]
            break
    if not rest:
        return None
    cur = root_n
    seen: list[str] = []
    for i, part in enumerate(rest):
        seen.append(part)
        rel = "/".join(seen)
        if part in ("..", ".") or any(sep in part for sep in {os.sep, os.altsep or os.sep}):
            return rel
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except (OSError, ValueError):
            return rel
        if is_redirect(st):
            final = i == len(rest) - 1
            if final and allow_final_link and os.name != "nt" and is_symlink(st):
                return None
            return rel
    return None


_DRIVE_UNKNOWN = 0
_DRIVE_NO_ROOT_DIR = 1
_DRIVE_REMOTE = 4


def _windows_drive_type(root: str) -> int:
    """`GetDriveTypeW(root)`（kernel32，只读、不联系网络共享）。非 Windows 不调用 ctypes.windll，回固定盘。
    Windows 真机行为未验证（CI 不覆盖）；测试用注入的假函数。"""
    if os.name != "nt":
        return 3
    import ctypes

    return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]


class PathWalker:
    """机器级绝对路径的逐级不跟随检查（`redirected_component` 的姊妹：那个只管项目根之下）。

    Windows 语义（`nt=True`）下，扫描构造出的「本地样子」候选（PATH 目录、IDE 的解释器线索、Conda / pyenv 前缀、
    glob 的字面目录前缀）可能在**任何一级祖先**上是指向断线 UNC / WebDAV 共享的 junction——只验叶子不够：
    对后代路径做 `lstat` 时系统会先解析那个祖先、联系共享。所以从盘符根起每一级只 `lstat` 该级本身
    （祖先已验证非重定向），UNC / 设备命名空间根、重定向（`is_redirect`，含最后一级）、不可读都拒；第一个
    被拒的那一级之下不再有任何 `lstat`。非路径替身的 AppExecLink（商店版 python）不被 `is_redirect` 命中。

    POSIX 语义（`nt=False`）是空操作：`/tmp`、`/var`、Homebrew、pyenv、venv 的 `bin/python` 软链接是常态，
    不是网络重定向（项目之下的链接另由 `redirected_component` 管）。每一级 `lstat` 计入 `budget`
    （预算耗尽按拒处理，调用方自会收手），结果按路径缓存，PATH 很长也不重复。命中的记账本（severity=note）。
    """

    def __init__(
        self,
        *,
        nt: bool | None = None,
        budget: Budget | None = None,
        drive_type: Callable[[str], int] | None = None,
    ):
        self.nt = os.name == "nt" if nt is None else nt
        self.budget = budget
        self._drive_type = drive_type or _windows_drive_type
        self._drives: dict[str, bool] = {}  # 盘符 -> 是否可碰（GetDriveTypeW 结果按盘符缓存）
        self._seen: dict[str, str | None] = {}  # 前缀 -> 被拒的那一级（None = 干净）
        self._missing: set[str] = set()

    def _note(self, code: str) -> None:
        if self.budget is not None:
            # 机器上某条 PATH / 前缀按策略不碰，是设计内的剪枝，不是项目扫描没做完：记 note（不把整次扫描降成 partial）。
            # 报告是公开的（`/api/project/scan` 原样回），机器级路径（用户目录、UNC 主机名）不进去：
            # 只记无路径的一行（同 code 合并计数）。scope 用机器级 `SCOPE_MACHINE`，**不是**项目遍历的 `walk`：
            # 后者在「一个脚本都没找到」时会把扫描升级成 partial，而机器上的剪枝与项目扫没扫全无关（Codex #957）。
            self.budget.note(code, severity=SEVERITY_NOTE, scope=SCOPE_MACHINE)

    def _drive_ok(self, drive: str) -> bool:
        """盘符根可不可碰：在**任何**文件系统操作（含 lstat 盘符根）之前先问盘类型。远程盘（含断开的映射盘）、
        根不存在、类型未知一律拒；本地固定盘 / 可移动盘 / 光驱 / RAM 盘照常。按盘符缓存。"""
        key = drive.upper()
        ok = self._drives.get(key)
        if ok is None:
            try:
                kind = self._drive_type(key + "\\")
                ok = kind not in (_DRIVE_UNKNOWN, _DRIVE_NO_ROOT_DIR, _DRIVE_REMOTE)
            except Exception:  # noqa: BLE001 - 判不出就当不可碰
                ok = False
            self._drives[key] = ok
        return ok

    def redirected(self, path) -> str | None:
        """`path` 经过重定向 / UNC 根 / 读不动的一级：回第一个被拒的前缀（已记账本）；干净或不存在回 None。"""
        if not self.nt:
            return None
        text = os.fspath(path)
        drive, rest = ntpath.splitdrive(text)
        if drive.startswith(("\\\\", "//")):
            self._note(ISSUE_SYMLINK_DIR)
            return drive
        if len(drive) == 2 and drive[1] == ":" and not self._drive_ok(drive):
            # 远程 / 断开的映射盘：盘符根的 lstat 也会碰网络，一次都不发
            self._note(ISSUE_SYMLINK_DIR)
            return drive
        cur = drive + (rest[:1] if rest[:1] in ("\\", "/") else "")
        for part in (x for x in re.split(r"[\\/]+", rest) if x and x != "."):
            if part == "..":  # 名字而非路径：normpath 会悄悄折叠掉中间一级的 lstat
                self._note(ISSUE_SYMLINK_DIR)
                return os.path.join(cur, part)
            cur = os.path.join(cur, part)
            key = os.path.normcase(cur)
            if key in self._missing:
                return None
            if key in self._seen:
                if self._seen[key] is not None:
                    return self._seen[key]
                continue
            if self.budget is not None and not self.budget.charge_entry():
                return cur
            try:
                st = os.lstat(cur)
            except (FileNotFoundError, NotADirectoryError):
                self._missing.add(key)
                return None
            except (OSError, ValueError):
                self._seen[key] = cur
                self._note(ISSUE_UNREADABLE_FILE)
                return cur
            bad = cur if is_redirect(st) else None
            self._seen[key] = bad
            if bad is not None:
                self._note(ISSUE_SYMLINK_DIR)
                return bad
        return None


def _contained_lexical(base, parts: tuple[str, ...]) -> str:
    """把 `base` + `parts` 钉在 `base` 之内，回**守卫过的那一条**字符串路径；逃出去抛 `OSError`。

    只做字符串运算（`normpath` + 前缀判，不 realpath、不 stat：no_follow 下 UNC / 链接目标不能被碰）。
    守卫写成静态分析认得的 barrier 形状（`== base` 与 `not startswith` 分开两段，相等那支回 `base_n`
    本身；规则见 docs/rules/backend/session-auth.md「用户可控的路径」），下游 lstat / open 只用回值。
    `parts` 里的 `..` / `.` / 分隔符一律拒（它们是名字不是路径，normpath 会悄悄折叠掉中间一级的 lstat）。
    """
    seps = {os.sep, os.altsep or os.sep}
    for part in parts:
        if part in ("", ".", "..") or any(sep in part for sep in seps):
            raise OSError(f"bad path component: {part!r}")
    base_n = os.path.normpath(os.fspath(base))
    cur = os.path.normpath(os.path.join(base_n, *parts))
    if cur == base_n:
        return base_n
    if not cur.startswith(base_n.rstrip(os.sep) + os.sep):
        raise OSError("path escapes base")
    return cur


def read_regular_text(
    base,
    *parts: str,
    no_follow: bool = False,
    max_bytes: int | None = MAX_FILE_BYTES,
    python_source: bool = False,
) -> str:
    """读一个文件的文本：只认**普通文件**、有字节上限、绝不阻塞在 FIFO 上；读不了一律 `OSError`。

    导入即扫描里「读用户项目里的文件」的唯一读法（环境线索文件与依赖声明共用，不另造第二套）。
    `no_follow=True`：`base`（已 realpath、钉在项目内的目录）之下的每一级都先 `lstat`，符号链接 /
    Windows 路径替身（junction 等）不探目标；打开用 `O_NOFOLLOW`（有的平台）并在打开后 `fstat` 复核
    仍是普通文件。这是元数据先行的防御，不声称对并发替换有原子保证。默认形态保持跟随用户自己的符号
    链接（准备 / 依赖门），但同样只读普通文件、有上限。`max_bytes=None` = 不设字节上限（仍只认普通文件）。
    """
    safe = _contained_lexical(base, parts)
    if no_follow:
        # 逐级 lstat：层级全从守卫过的 `safe` 往上取 dirname（不拿 base / parts 原串重拼）
        levels: list[str] = []
        level = safe
        base_n = os.path.normpath(os.fspath(base))
        while level != base_n:
            levels.append(level)
            parent = os.path.dirname(level)
            if parent == level:
                break
            level = parent
        for level in reversed(levels):
            if is_redirect(Path(level).lstat()):
                raise OSError(f"redirect refused: {os.path.basename(level)}")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    if no_follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(safe, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (max_bytes is not None and st.st_size > max_bytes):
            raise OSError("not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:  # 打开之后又长大了：同样不读
                raise OSError("file grew past the limit")
            chunks.append(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if python_source:
        # Python 源码按 PEP 263 声明 / BOM 解码（带 BOM、`# coding: gbk` 的脚本不能被当 UTF-8 读坏）；解不出读不了
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            return data.decode(encoding)
        except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
            raise OSError(f"source decode failed: {type(exc).__name__}") from exc
    return data.decode("utf-8", errors="replace")


def is_placeholder(st) -> bool:
    """这个 `os.stat_result` 是不是云盘占位文件（内容不在本机，读它会强制下载）。

    只看 stat 返回的位，**不打开、不读**。判不出（平台没有这些字段）就当不是——这是已知盲点：Linux
    上的 FUSE 云盘没有统一标志，靠 `MAX_FILE_BYTES` 与时间预算兜底。
    """
    flags = getattr(st, "st_flags", 0) or 0
    if flags & _DARWIN_DATALESS:
        return True
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & _WIN_PLACEHOLDER)


@dataclass
class Limits:
    max_entries: int = MAX_ENTRIES
    max_scripts: int = MAX_SCRIPTS
    max_assets: int = MAX_ASSETS
    max_file_bytes: int = MAX_FILE_BYTES
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_asset_depth: int = MAX_ASSET_DEPTH
    max_seconds: float = MAX_SECONDS


@dataclass
class Budget:
    """一次扫描的预算 + 账本。线程安全（扫描线程写、HTTP 线程读快照）。"""

    limits: Limits = field(default_factory=Limits)
    cancel: Callable[[], bool] | None = None
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = self.clock()
        self.entries = 0
        self.scripts = 0
        self.assets = 0
        self.source_bytes = 0
        self._stopped: str | None = None
        self._issues: dict[tuple[str, str, str], dict] = {}
        self._overflow = 0

    # ------------------------------------------------------------------ 账本
    def note(
        self,
        code: str,
        *,
        severity: str = SEVERITY_PARTIAL,
        scope: str = "dir",
        path: str = "",
    ) -> None:
        """记一条「看不全」。同 (code, scope, path) 合并；总条数封顶，多的只计数。"""
        key = (code, scope, path)
        with self._lock:
            hit = self._issues.get(key)
            if hit is not None:
                hit["count"] += 1
                return
            if len(self._issues) >= MAX_ISSUES:
                self._overflow += 1
                return
            row = {"code": code, "severity": severity, "scope": scope, "count": 1}
            if path:
                row["path"] = path
            self._issues[key] = row

    def issues(self) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self._issues.values()]
            if self._overflow:
                rows.append(
                    {
                        "code": "more_issues",
                        "severity": SEVERITY_PARTIAL,
                        "scope": "walk",
                        "count": self._overflow,
                    }
                )
        return rows

    # ------------------------------------------------------------------ 是否该停
    def stop_reason(self) -> str | None:
        """扫描该不该停：返回停的原因（也就是账本 code），否则 None。一旦停了就一直停。"""
        with self._lock:
            if self._stopped is not None:
                return self._stopped
        reason = None
        if self.cancel is not None and self.cancel():
            reason = ISSUE_CANCELLED
        elif self.clock() - self._t0 > self.limits.max_seconds:
            reason = ISSUE_TIME
        elif self.entries > self.limits.max_entries:
            reason = ISSUE_ENTRIES
        if reason is not None:
            with self._lock:
                if self._stopped is None:
                    self._stopped = reason
            self.note(reason, scope="walk")
        return reason

    @property
    def stopped(self) -> str | None:
        return self._stopped

    def charge_entry(self) -> bool:
        """检视一个目录项。预算用完 / 超时 / 取消返回 False——调用方应当立刻收手。"""
        if self._stopped is not None:
            return False  # 已经停了：不再记数，用量停在越线的那一项
        self.entries += 1
        # 时钟与取消不必每一项都查：每 64 项一次，加上预算刚好越线的那一次
        if self.entries % 64 == 0 or self.entries > self.limits.max_entries:
            return self.stop_reason() is None
        return self._stopped is None

    def charge_source(self, size: int) -> str | None:
        """要读一个 `size` 字节的源文件前问一声：不能读返回原因 code，可以读返回 None 并记账。"""
        if size > self.limits.max_file_bytes:
            return ISSUE_TOO_LARGE
        if self.source_bytes + size > self.limits.max_source_bytes:
            return ISSUE_SOURCE_BYTES
        self.source_bytes += size
        return None

    def elapsed(self) -> float:
        return self.clock() - self._t0

    def snapshot(self) -> dict:
        """预算用量的公开投影（不含路径）。"""
        return {
            "entries": self.entries,
            "scripts": self.scripts,
            "assets": self.assets,
            "source_bytes": self.source_bytes,
            "elapsed_s": round(self.elapsed(), 3),
            "limits": {
                "entries": self.limits.max_entries,
                "scripts": self.limits.max_scripts,
                "assets": self.limits.max_assets,
                "file_bytes": self.limits.max_file_bytes,
                "source_bytes": self.limits.max_source_bytes,
                "seconds": self.limits.max_seconds,
            },
        }


def system_sub_budget(parent: Budget) -> Budget:
    """系统候选枚举用的子预算：自己的条目数与时间上限，取消回调与时钟沿用父预算。
    枚举里所有 `charge_entry` / walker 的 lstat 都记在它名下，不动父预算的 `entries`。"""
    # 时间上限不得越过父预算的截止：父预算剩多少就最多给多少（扫描末尾才开始枚举时不能再白送 3 秒，Codex #957）
    remaining = parent.limits.max_seconds - (parent.clock() - parent._t0)
    return Budget(
        limits=Limits(
            max_entries=SYSTEM_MAX_ENTRIES,
            max_seconds=max(0.0, min(SYSTEM_MAX_SECONDS, remaining)),
        ),
        cancel=parent.cancel,
        clock=parent.clock,
    )


def fold_system_issues(parent: Budget, sub: Budget) -> None:
    """把子预算的账本折回父预算：一律降成 note 级、scope=system、不带路径（报告是公开的）。
    取消不折（父预算自己会观察到取消并记账）。子预算用完 = 只少列了系统候选，不是项目没扫完。"""
    for row in sub.issues():
        if row["code"] == ISSUE_CANCELLED:
            continue
        code = ISSUE_ENTRIES if row["code"] == "more_issues" else row["code"]
        parent.note(code, severity=SEVERITY_NOTE, scope=SCOPE_SYSTEM)


def no_follow_isfile(root, charge=None):
    """「项目内的普通文件吗」谓词：只 lstat、不跟随；拼接后的路径必须落在 `root` 之下（词法包含，CodeQL
    py/path-injection 的 barrier 形状），经过的任一级是链接 / 路径替身同样不探。`charge`（可选）每问一次记一笔
    预算，返回 False 即答 False。**这是项目派生路径探文件的默认形状**（#820 r4232425162）：目标可能是攻击者的
    UNC / WebDAV，跟随式 `os.path.isfile` 在 Windows 上会发出 NTLM。"""
    real_root = os.path.normpath(os.fspath(root))

    def isfile(path) -> bool:
        if charge is not None and not charge():
            return False
        norm = os.path.normpath(os.fspath(path))
        if not norm.startswith(real_root.rstrip(os.sep) + os.sep):
            return False
        if redirected_component(real_root, norm) is not None:
            return False
        try:
            st = os.lstat(norm)
        except OSError:
            return False
        return not is_redirect(st) and stat.S_ISREG(st.st_mode)

    return isfile
