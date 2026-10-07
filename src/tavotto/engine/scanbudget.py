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

纯标准库、叶子模块（不 import 兄弟模块）：被 `discover` / `project_refresh` / `projscan` 共同依赖，
自己不能再依赖它们。
"""

from __future__ import annotations

import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

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

#: macOS `SF_DATALESS`（`st_flags`）：文件内容不在本机，读它会触发下载。
_DARWIN_DATALESS = 0x40000000
#: Windows `st_file_attributes`：OFFLINE / RECALL_ON_OPEN / RECALL_ON_DATA_ACCESS。
_WIN_PLACEHOLDER = 0x1000 | 0x40000 | 0x400000


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
