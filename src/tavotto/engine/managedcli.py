"""受控 CLI 供应：把官方 Codex / Claude Code CLI 装进 `data_dir()/cli/<名>/`——不要 npm、不要 Homebrew、不要终端。

## 两个 CLI 的来源（锁里 `clis.<名>.sources` 逐项声明，顺序即尝试顺序）

* `codex`：GitHub release（`.tar.gz`）→ npmmirror 平台子包（`.tgz`，树根前缀 `package/vendor/<triple>/`）。
* `claude`：npmmirror 平台子包 `@anthropic-ai/claude-code-<平台>`（`.tgz`，只取里面的原生二进制 `only`）→
  官方 `downloads.claude.ai` 的**裸二进制**（`raw`：下载物本身就是入口文件，不是归档）。
  两个来源落盘必须是同一份字节：npm 包里的 `claude` 与 GCS 上的 `claude` 逐字节相同（`--update` 时核过）。

**条款红线**：只原样下载 + 校验 + 解包，不修改、不重打包二进制；登录由 claude 自己的 `auth login` 完成，Tavotto 不收集、
不存储、不转发任何 token；托管的 claude 启动时设 `DISABLE_AUTOUPDATER=1`（版本只由锁控制，见 `child_env`）。

## 为什么是锁文件 + 两个来源

`resources/managed_cli_lock.json` 钉死「装什么」：每个平台一份官方发行包（`codex-package-<triple>`，
与 npm 平台子包 `@openai/codex@<ver>-<平台>` 里的 `vendor/<triple>/` 目录**逐文件字节相同**，2026-10-29
对 0.165.0 的四个平台逐文件核过 sha256），以及三层校验：

1. 归档整份 sha256：GitHub 的 `.tar.gz` 与 npmmirror 的 `.tgz` 是两份不同的归档，**各锁各的**；
2. 解开之后整棵树的摘要 `tree_sha256`（两个来源共用）——来源不同、落盘必须是同一棵树；
3. 真起一次 `--version`，且报出来的版本与锁一致。

任一层不符：拒绝、不重试、不换源（字节对不上不管来自哪里都是拒绝的对象）。

## 来源顺序

GitHub 主 → registry.npmmirror.com 镜像。「慢了换镜像」与私有 Python 同一套判据（ADR 0112）：开始收
字节后至少量 `SLOW_GRACE_S`，按全程平均速度估的剩余时间超过 `SLOW_ETA_S` 就换下一个来源；被放弃的
来源排到队尾、不再测速地再试一次。

## 为什么没有抽成与 privatepython 共用的传输层

`privatepython` 的下载循环与它的 `_Inflight`（消费者计数、取消语义、`PythonSource`、`ORIGIN_*`、日志文案、
模块级常量被一批用例 monkeypatch）长在一起；抽出去要么改它的行为面，要么留一层转发垫片。本模块是新增能力，
用相同的判据（常量名、错误分类、`.part` 原子改名、hash 不符不重试）在这里**最小复刻**，TLS 与日志脱敏仍走
同一份 `tlstrust` / `logsafe`。两处判据的同源看护在 `tests/test_managed_cli.py`。

## 落盘布局（`config.data_dir()`，永不写包目录）

    cli/<名>/<版本>-<树摘要前12>/      不可变；内容寻址（锁换了树就是另一个目录）
    cli/<名>/current.json              原子切换的指针 {"id": "<目录名>", "version": "…"}
    cli/<名>/.staging-<id>-<pid>/      解包 + 探测在这里做，通过后 `os.replace` 成最终目录

macOS 不需要去隔离属性：本进程写下的文件没有 `com.apple.quarantine`；这些二进制出厂已由 OpenAI 签名。

## 不做的事

不登录（登录由前端在安装完成后调用既有的 `/api/ai/agents/<id>/login`）；不改用户的 `config.toml`；
不替用户升级（锁里的版本变了，下一次点安装才会换）。

纯标准库（Flask 父进程 import 链允许）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import logging
import os
import platform as _platform
import posixpath
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import atomicio, brand, config, logsafe, msixmigrate, tlstrust
from .runtime import CREATE_NO_WINDOW

LOG = logging.getLogger("tavotto.managedcli")

LOCK_SCHEMA = 1
LOCK_PARTS = ("resources", "managed_cli_lock.json")
DIRNAME = "cli"
POINTER_NAME = "current.json"
STAGING_PREFIX = ".staging-"
DOWNLOADS_DIRNAME = ".downloads"

# --------------------------------------------------------------- 错误码（安装状态里的 `code`，文案在前端 errors.json）
ERROR_OFFLINE = "managed_cli_offline"
ERROR_TLS = "managed_cli_tls"
ERROR_SOURCE_UNAVAILABLE = "managed_cli_source_unavailable"
ERROR_HASH_MISMATCH = "managed_cli_hash_mismatch"
ERROR_UNSUPPORTED_PLATFORM = "managed_cli_unsupported_platform"
ERROR_DISK_FULL = "managed_cli_disk_full"
ERROR_INVALID_ARCHIVE = "managed_cli_invalid_archive"
ERROR_WRITE_FAILED = "managed_cli_write_failed"
ERROR_LAUNCH_FAILED = "managed_cli_launch_failed"
ERROR_CANCELLED = "managed_cli_cancelled"
ERROR_CODES = (
    ERROR_OFFLINE,
    ERROR_TLS,
    ERROR_SOURCE_UNAVAILABLE,
    ERROR_HASH_MISMATCH,
    ERROR_UNSUPPORTED_PLATFORM,
    ERROR_DISK_FULL,
    ERROR_INVALID_ARCHIVE,
    ERROR_WRITE_FAILED,
    ERROR_LAUNCH_FAILED,
    ERROR_CANCELLED,
)

# --------------------------------------------------------------- 阶段（安装状态里的 `phase`）
PHASE_DOWNLOAD = "download"
PHASE_VERIFY = "verify"
PHASE_EXTRACT = "extract"
PHASE_PROBE = "probe"
PHASES = (PHASE_DOWNLOAD, PHASE_VERIFY, PHASE_EXTRACT, PHASE_PROBE)

# --------------------------------------------------------------- 传输常量（判据与 privatepython 同源，见模块头）
NETWORK_TIMEOUT_S = 30
DOWNLOAD_ATTEMPTS = 2
CHUNK = 1 << 20
READ_BYTES = 64 * 1024
SLOW_GRACE_S = 20.0
SLOW_ETA_S = 180.0
PROBE_TIMEOUT_S = 60
DISK_MARGIN_BYTES = 64 * 1024 * 1024
STALE_STAGING_S = 3600

#: 已发行的来源主机（固定常量，不从调用方传进来的 URL 现推——现推的判据恒真）：只有它们进日志时明文。
SOURCE_HOSTS = frozenset({"github.com", "registry.npmmirror.com", "downloads.claude.ai"})
#: 锁里 URL 模板允许的前缀；`validate_lock` 钉着。
GITHUB_PREFIX = "https://github.com/openai/codex/releases/download/"
MIRROR_PREFIX = "https://registry.npmmirror.com/@openai/codex/-/"
CLAUDE_MIRROR_PREFIX = "https://registry.npmmirror.com/@anthropic-ai/claude-code-"
CLAUDE_GCS_PREFIX = "https://downloads.claude.ai/claude-code-releases/"
#: 每个受控 CLI 的每个来源键允许的 URL 前缀（`validate_lock` 钉着；锁里出现表外的 CLI / 来源键 = 拒绝）
SOURCE_PREFIXES: dict[str, dict[str, str]] = {
    "codex": {"github": GITHUB_PREFIX, "mirror": MIRROR_PREFIX},
    "claude": {"mirror": CLAUDE_MIRROR_PREFIX, "gcs": CLAUDE_GCS_PREFIX},
}
#: 托管 CLI 子进程要带的环境变量（版本只由我们的锁控制：不许 CLI 自己升级）
MANAGED_CHILD_ENV: dict[str, dict[str, str]] = {"claude": {"DISABLE_AUTOUPDATER": "1"}}

_HEX64 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_TRIPLE = re.compile(r"[a-z0-9_]+(-[a-z0-9_]+)+")
_SRI512 = re.compile(r"sha512-[A-Za-z0-9+/]{86}==")

_lock = threading.RLock()


class _TooSlow(Exception):
    def __init__(self, rate_bps: float, eta_s: float):
        super().__init__(f"{rate_bps:.0f} B/s, ETA {eta_s:.0f} s")
        self.rate_bps = rate_bps
        self.eta_s = eta_s


class ManagedCliError(RuntimeError):
    """带稳定 code 的供应失败。"""

    def __init__(self, code: str, message: str = "", **detail):
        super().__init__(message or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- 锁文件
@dataclasses.dataclass(frozen=True)
class ArchiveSource:
    """同一棵树的一个下载来源：URL + 这份归档自己的 size / sha256 + 归档里树的根前缀。"""

    url: str
    size: int
    sha256: str
    #: 归档内树根的前缀（GitHub 包为空串；npm 平台包为 `package/vendor/<triple>/`）；前缀之外的成员忽略
    strip: str = ""
    #: 非空 = 归档里只取这一个成员（相对 `strip` 之后的路径），其余忽略（npm 包里的 package.json / 许可证不进树）
    only: str = ""
    #: True = 下载物本身就是入口文件（官方发行的裸二进制），不是归档
    raw: bool = False

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""


@dataclasses.dataclass(frozen=True)
class ManagedCli:
    """锁文件里一个（CLI, 平台）——**唯一**决定「下什么、多大、解开是什么树、入口在哪」。"""

    name: str
    version: str
    platform: str
    triple: str
    entrypoint: str
    tree_sha256: str
    unpacked_size: int
    file_count: int
    sources: tuple[ArchiveSource, ...]

    @property
    def id(self) -> str:
        return f"{self.version}-{self.tree_sha256[:12]}"

    @property
    def download_bytes(self) -> int:
        return int(self.sources[0].size) if self.sources else 0

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "platform": self.platform,
            "download_bytes": self.download_bytes,
            "source_host": self.sources[0].host if self.sources else "",
        }


def lock_path() -> Path:
    """`resources/managed_cli_lock.json`：`importlib.resources` → 源码树兜底（与 privatepython 同一条纪律）。"""
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath(*LOCK_PARTS)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return Path(__file__).resolve().parent.parent.joinpath(*LOCK_PARTS)


def _fmt(template: str, **kw) -> str:
    return template.format(**kw)


def _source_defs(where: str, name: str, cli: dict) -> list[dict]:
    """`clis.<名>.sources`：来源声明（顺序即尝试顺序）。每项 `key` 必须在 `SOURCE_PREFIXES[名]` 里，URL 必须以登记过的前缀开头。"""
    prefixes = SOURCE_PREFIXES.get(name)
    if prefixes is None:
        raise ValueError(f"{where}: {name!r} 不是登记过的受控 CLI")
    defs = cli.get("sources")
    if not isinstance(defs, list) or not defs:
        raise ValueError(f"{where}.sources 缺失")
    seen: set[str] = set()
    for d in defs:
        if not isinstance(d, dict):
            raise ValueError(f"{where}.sources 里有非对象项")
        key = str(d.get("key", ""))
        if key not in prefixes or key in seen:
            raise ValueError(f"{where}.sources 的来源键 {key!r} 不在登记表里或重复")
        seen.add(key)
        url = str(d.get("url", ""))
        if not url.startswith(prefixes[key]) or "{version}" not in url:
            raise ValueError(f"{where}.sources.{key}.url 必须是 {prefixes[key]}… 且含 {{version}}")
        strip = str(d.get("strip", ""))
        if strip and (not strip.endswith("/") or ".." in strip or strip.startswith("/")):
            raise ValueError(f"{where}.sources.{key}.strip 形状不对")
        only = str(d.get("only", ""))
        if ".." in only.split("/") or only.startswith("/") or "\\" in only:
            raise ValueError(f"{where}.sources.{key}.only 形状不对")
        if d.get("raw") is not None and not isinstance(d["raw"], bool):
            raise ValueError(f"{where}.sources.{key}.raw 必须是布尔值")
        if d.get("raw") and (strip or only):
            raise ValueError(f"{where}.sources.{key}: raw 来源没有 strip / only")
    return defs


def validate_lock(lock: dict) -> None:
    """锁文件必须真的「锁住」：精确版本、64 位小写 hex、来源只能是登记过的主机、URL 与模板推导一致。"""
    if not isinstance(lock, dict) or lock.get("schema") != LOCK_SCHEMA:
        raise ValueError(f"受控 CLI 锁文件 schema 不是 {LOCK_SCHEMA}")
    clis = lock.get("clis")
    if not isinstance(clis, dict) or not clis:
        raise ValueError("锁文件缺 clis 块")
    for name, cli in clis.items():
        where = f"clis.{name}"
        if not isinstance(cli, dict):
            raise ValueError(f"{where} 不是对象")
        version = str(cli.get("version", ""))
        if not _VERSION.fullmatch(version):
            raise ValueError(f"{where}.version 必须是精确的 x.y.z，拿到 {version!r}")
        defs = _source_defs(where, name, cli)
        plats = cli.get("platforms")
        if not isinstance(plats, dict) or not plats:
            raise ValueError(f"{where}.platforms 缺失")
        for plat, p in plats.items():
            pw = f"{where}.platforms.{plat}"
            if not isinstance(p, dict):
                raise ValueError(f"{pw} 不是对象")
            if not _TRIPLE.fullmatch(str(p.get("triple", ""))):
                raise ValueError(f"{pw}.triple 形状不对")
            if not re.fullmatch(r"[a-z0-9]+-[a-z0-9]+", str(p.get("npm", ""))):
                raise ValueError(f"{pw}.npm 形状不对")
            entry = str(p.get("entrypoint", ""))
            if not entry or entry.startswith("/") or ".." in entry.split("/") or "\\" in entry:
                raise ValueError(f"{pw}.entrypoint 必须是树内的相对 POSIX 路径")
            if not _HEX64.fullmatch(str(p.get("tree_sha256", ""))):
                raise ValueError(f"{pw}.tree_sha256 必须是 64 位小写 hex")
            for key in ("unpacked_size", "file_count"):
                if not isinstance(p.get(key), int) or p[key] <= 0:
                    raise ValueError(f"{pw}.{key} 必须是正整数")
            for d in defs:
                key = d["key"]
                s = p.get(key)
                if not isinstance(s, dict):
                    raise ValueError(f"{pw}.{key} 缺失")
                if not _HEX64.fullmatch(str(s.get("sha256", ""))):
                    raise ValueError(f"{pw}.{key}.sha256 必须是 64 位小写 hex")
                if not isinstance(s.get("size"), int) or s["size"] <= 0:
                    raise ValueError(f"{pw}.{key}.size 必须是正整数")
                if key == "mirror" and not _SRI512.fullmatch(str(s.get("integrity", ""))):
                    raise ValueError(
                        f"{pw}.mirror.integrity 必须是 npm 的 sha512 SRI（在线核对用）"
                    )
            try:
                srcs = _sources(name, cli, plat, p)
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(f"{pw}: URL 模板推不出来: {exc}") from exc
            for src in srcs:
                host = urllib.parse.urlsplit(src.url).hostname
                if host not in SOURCE_HOSTS:
                    raise ValueError(f"{pw}: 来源主机 {host!r} 不在登记表里")


def _sources(name: str, cli: dict, plat: str, p: dict) -> tuple[ArchiveSource, ...]:
    kw = {
        "version": cli["version"],
        "triple": p["triple"],
        "npm": p["npm"],
        "entry": p["entrypoint"],
    }
    out: list[ArchiveSource] = []
    for d in cli["sources"]:
        meta = p[d["key"]]
        out.append(
            ArchiveSource(
                _fmt(d["url"], **kw),
                meta["size"],
                meta["sha256"],
                _fmt(d.get("strip", ""), **kw),
                _fmt(d.get("only", ""), **kw),
                bool(d.get("raw", False)),
            )
        )
    return tuple(out)


def load_lock(path: Path | None = None) -> dict:
    lock = json.loads((path or lock_path()).read_text(encoding="utf-8"))
    validate_lock(lock)
    return lock


def host_platform() -> str | None:
    """本机的平台键（`darwin-arm64` / `darwin-x64` / `win32-x64` / `linux-x64`）；不在锁里的架构回 None。"""
    import sys

    machine = _platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine)
    if arch is None:
        return None
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    if os.name == "nt":
        return f"win32-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    return None


def cli_for(
    name: str, platform_key: str | None = None, lock: dict | None = None
) -> ManagedCli | None:
    """这个 CLI 在这个平台的锁条目；锁里没有这个 CLI 或这个平台（如 Windows ARM64）回 None。"""
    key = platform_key or host_platform()
    if key is None:
        return None
    lock = lock or load_lock()
    cli = (lock.get("clis") or {}).get(name)
    if not cli:
        return None
    p = (cli.get("platforms") or {}).get(key)
    if not p:
        return None
    return ManagedCli(
        name=name,
        version=cli["version"],
        platform=key,
        triple=p["triple"],
        entrypoint=p["entrypoint"],
        tree_sha256=p["tree_sha256"],
        unpacked_size=p["unpacked_size"],
        file_count=p["file_count"],
        sources=_sources(name, cli, key, p),
    )


def offered(name: str) -> bool:
    """这台机器上能不能走托管安装（不联网）。锁文件读不了也当不提供，由调用方回落到 npm。"""
    try:
        return cli_for(name) is not None
    except (OSError, ValueError, KeyError):
        return False


# --------------------------------------------------------------- 路径
def root_dir(name: str) -> Path:
    # `cli/<名>/` 还不存在：MSIX 宿主私有区里若有同名的（#1574 之前装进去的）先搬回来，入口文件核过才算数；
    # 搬不成 = 当没装，照旧走下载。进程内每个名字只问一次。
    msixmigrate.adopt_dir(DIRNAME, name, validate=lambda cand: _adopted_cli_ok(name, cand))
    return config.data_dir() / DIRNAME / name


def _adopted_cli_ok(name: str, candidate: Path) -> bool:
    """搬来的 `cli/<名>/`：指针认得出、指针指的版本目录里入口文件在（版本目录是内容寻址的，名字里带树摘要前缀）。"""
    try:
        ptr = json.loads((candidate / POINTER_NAME).read_text(encoding="utf-8"))
        cli = cli_for(name)
    except (OSError, ValueError, KeyError):
        return False
    ident = ptr.get("id") if isinstance(ptr, dict) else None
    if cli is None or not isinstance(ident, str):
        return False
    if not re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{12}", ident):
        return False
    return (candidate / ident / Path(*cli.entrypoint.split("/"))).is_file()


def _under(path: Path, root: Path) -> Path:
    resolved, base = path.resolve(), root.resolve()
    if resolved != base and base not in resolved.parents:
        raise ManagedCliError(ERROR_WRITE_FAILED, "托管目录越界")
    return path


def _pointer(name: str) -> dict | None:
    try:
        data = json.loads((root_dir(name) / POINTER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ident = data.get("id")
    # 指针里的 id 要当目录名用：只认我们自己写出的形状，绝不让一份被改过的 current.json 指到目录之外
    if not isinstance(ident, str) or not re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{12}", ident):
        return None
    return data


def installed_version(name: str) -> str | None:
    ptr = _pointer(name)
    return str(ptr.get("version")) if ptr and ptr.get("version") else None


def entrypoint(name: str, cli: ManagedCli | None = None) -> Path | None:
    """当前托管的入口文件；没装 / 指针坏 / 入口不在返回 None。不联网、不起子进程。"""
    ptr = _pointer(name)
    if ptr is None:
        return None
    if cli is None:
        try:
            cli = cli_for(name)
        except (OSError, ValueError, KeyError):
            return None
    if cli is None:
        return None
    path = root_dir(name) / ptr["id"] / Path(*cli.entrypoint.split("/"))
    return path if path.is_file() else None


def bin_dir(name: str) -> Path | None:
    """托管入口所在的目录——给 `ai_agents.search_locations` 当第一个落点（`shutil.which` 按目录找）。"""
    try:
        entry = entrypoint(name)
    except Exception:  # noqa: BLE001 — 发现路径上的探查绝不抛（数据目录不可解析 = 没装）
        return None
    return entry.parent if entry is not None else None


def child_env(path: str | os.PathLike | None) -> dict[str, str]:
    """起托管 CLI 子进程要追加的环境变量：路径在 `cli/<名>/` 之下才带（用户自己装的那份不动）。
    claude 的 `DISABLE_AUTOUPDATER=1`：版本只由我们的锁控制。"""
    if not path:
        return {}
    for name, env in MANAGED_CHILD_ENV.items():
        try:
            _under(Path(path), root_dir(name))
        except Exception:  # noqa: BLE001 — 子进程环境的探查绝不抛（数据目录不可解析 = 不是托管那份）
            continue
        return dict(env)
    return {}


def status(name: str) -> dict:
    cli = None
    try:
        cli = cli_for(name)
    except (OSError, ValueError, KeyError):
        pass
    entry = entrypoint(name, cli)
    return {
        "offered": cli is not None,
        "installed": entry is not None,
        "version": installed_version(name) if entry is not None else None,
        "latest": cli.version if cli else None,
    }


# --------------------------------------------------------------- 磁盘
def require_free_disk(cli: ManagedCli) -> None:
    """下载 + 解开要落得下（最大归档 + 解包后总量 + 余量）；量不出来不拦。"""
    need = max(s.size for s in cli.sources) + int(cli.unpacked_size) + DISK_MARGIN_BYTES
    try:
        root = root_dir(cli.name)
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(root)).free
    except OSError:
        return
    if free < need:
        raise ManagedCliError(
            ERROR_DISK_FULL, "磁盘剩余空间不足以安装", need_bytes=need, free_bytes=free
        )


def _is_disk_full(exc: OSError) -> bool:
    import errno

    return exc.errno in (errno.ENOSPC, errno.EDQUOT) or getattr(exc, "winerror", None) in (
        112,
        39,
    )


# --------------------------------------------------------------- 进度
class _Job:
    def __init__(self, cancel_ev, on_progress):
        self.cancel_ev = cancel_ev
        self.on_progress = on_progress
        self.trust_source = ""
        self.source_host = ""

    def emit(self, phase: str, done: int, total: int) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(phase, done, total)
        except Exception:  # noqa: BLE001 — 进度回调不许让安装失败
            LOG.debug("进度回调异常", exc_info=True)

    def check(self) -> None:
        if self.cancel_ev is not None and self.cancel_ev.is_set():
            raise ManagedCliError(ERROR_CANCELLED, "已取消")


def _plain_host(host: str):
    return logsafe.known(host, SOURCE_HOSTS) if host else "-"


def _user_agent() -> str:
    try:
        from .. import __version__
    except Exception:  # noqa: BLE001
        __version__ = ""
    return f"{brand.PRODUCT_NAME}/{__version__}"


# --------------------------------------------------------------- 供应
def provision(
    name: str = "codex",
    *,
    cli: ManagedCli | None = None,
    cancel_ev: threading.Event | None = None,
    on_progress=None,
) -> str:
    """把这个 CLI 的托管版本准备好，回入口文件路径。已装（同一棵树）就直接回，不联网。

    同一进程里并发调用串行化（后到的拿到先到的结果）；跨进程靠 staging 目录带 pid + `os.replace` 容忍。
    `on_progress(phase, done, total)`：`phase` 取 `PHASES`。
    """
    cli = cli or cli_for(name)
    if cli is None:
        raise ManagedCliError(ERROR_UNSUPPORTED_PLATFORM, "这个平台上没有可托管安装的版本")
    with _lock:
        job = _Job(cancel_ev, on_progress)
        job.check()
        final = root_dir(name) / cli.id
        existing = _existing_entry(cli, final)
        if existing is not None:
            _switch_pointer(cli)
            return str(existing)
        return _provision_once(cli, job)


def _existing_entry(cli: ManagedCli, final: Path) -> Path | None:
    entry = final / Path(*cli.entrypoint.split("/"))
    return entry if entry.is_file() else None


def _provision_once(cli: ManagedCli, job: _Job) -> str:
    root = root_dir(cli.name)
    final = root / cli.id
    try:
        (root / DOWNLOADS_DIRNAME).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ManagedCliError(ERROR_WRITE_FAILED, f"托管目录不可写: {exc}") from exc
    _reap_orphans(root)
    require_free_disk(cli)
    archive, src = _download(cli, job)
    staging = root / f"{STAGING_PREFIX}{cli.id}-{os.getpid()}"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        try:
            staging.mkdir(parents=True)
        except OSError as exc:
            raise ManagedCliError(ERROR_WRITE_FAILED, f"staging 目录不可建: {exc}") from exc
        job.emit(PHASE_EXTRACT, 0, cli.unpacked_size)
        _extract(archive, staging, src, cli, job)
        job.check()
        job.emit(PHASE_VERIFY, 0, cli.unpacked_size)
        digest, _count, _total = tree_digest(
            staging, progress=lambda d: job.emit(PHASE_VERIFY, d, cli.unpacked_size)
        )
        if digest != cli.tree_sha256:
            LOG.warning("托管 CLI 解包后的树摘要与锁不符（来源 %s）：拒绝", _plain_host(src.host))
            raise ManagedCliError(
                ERROR_HASH_MISMATCH,
                "解包后的内容与锁文件不符",
                expected=cli.tree_sha256,
                got=digest,
            )
        entry = staging / Path(*cli.entrypoint.split("/"))
        _make_executable(entry)
        job.emit(PHASE_PROBE, 0, 1)
        _probe(entry, cli)
        job.check()
        with _lock:
            try:
                os.replace(staging, final)  # 提交点
            except OSError as exc:
                if _existing_entry(cli, final) is None:
                    raise ManagedCliError(ERROR_WRITE_FAILED, f"最终目录改名失败: {exc}") from exc
                LOG.info("托管 CLI %s 已由别的进程就位，复用", cli.id)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
    _switch_pointer(cli)
    job.emit(PHASE_PROBE, 1, 1)
    LOG.info("托管 CLI %s %s 就位（来源 %s）", cli.name, cli.version, _plain_host(src.host))
    result = _existing_entry(cli, final)
    if result is None:
        raise ManagedCliError(ERROR_LAUNCH_FAILED, "改名后最终目录里没有入口文件")
    _retire_others(cli)
    return str(result)


def _switch_pointer(cli: ManagedCli) -> None:
    body = json.dumps({"id": cli.id, "version": cli.version}, ensure_ascii=False).encode("utf-8")
    try:
        atomicio.write_bytes(root_dir(cli.name) / POINTER_NAME, body)
    except atomicio.AtomicWriteError as exc:
        raise ManagedCliError(ERROR_WRITE_FAILED, f"指针写入失败: {exc}") from exc


def _retire_others(cli: ManagedCli) -> None:
    """切换之后删旧版本目录。删不掉（Windows 上旧进程还占着）就留着，下一次再清；绝不影响这次结果。"""
    root = root_dir(cli.name)
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for p in entries:
        if p.is_dir() and not p.name.startswith(".") and p.name != cli.id:
            try:
                shutil.rmtree(p)
            except OSError as exc:
                LOG.info("旧版托管 CLI 暂时删不掉（下次再清）: %s", type(exc).__name__)


def _reap_orphans(root: Path) -> None:
    """别的进程留下的 staging / `.part` 超时就清（无法量 pid 存活时的兜底）。"""
    now = time.time()
    for base in (root, root / DOWNLOADS_DIRNAME):
        try:
            items = list(base.iterdir())
        except OSError:
            continue
        for p in items:
            if not (p.name.startswith(STAGING_PREFIX) or p.name.endswith(".part")):
                continue
            try:
                if now - p.stat().st_mtime < STALE_STAGING_S:
                    continue
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                continue


# --------------------------------------------------------------- 下载
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(cli: ManagedCli, job: _Job) -> tuple[Path, ArchiveSource]:
    """校验过的归档路径与它对应的来源。按顺序试 GitHub → 镜像；换源的理由与私有 Python 同：传输层失败用完
    `DOWNLOAD_ATTEMPTS`、HTTP 4xx/5xx、太慢。**hash 不符不换源、不重试。**每个来源从零开始下（不续传）。"""
    dl = root_dir(cli.name) / DOWNLOADS_DIRNAME
    queue: list[tuple[ArchiveSource, bool]] = [(s, False) for s in cli.sources]
    abandoned_slow = False
    last: Exception | None = None
    cert: BaseException | None = None
    http_failures = 0
    tried = 0
    while queue:
        src, retry = queue.pop(0)
        tried += 1
        part = dl / f"{cli.id}-{src.sha256[:12]}.{os.getpid()}-{secrets.token_hex(4)}.part"
        with _lock:
            job.source_host = src.host
        judge = bool(queue) and not abandoned_slow and not retry
        reason = ""
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            job.check()
            try:
                got = _fetch(src, part, job, judge_slow=judge)
            except _TooSlow as exc:
                part.unlink(missing_ok=True)
                abandoned_slow = True
                queue.append((src, True))
                reason = f"too_slow（{exc.rate_bps / 1024:.0f} KB/s，预计还要 {exc.eta_s:.0f} s）"
                break
            except urllib.error.HTTPError as exc:
                part.unlink(missing_ok=True)
                http_failures += 1
                last = exc
                reason = f"HTTP {exc.code}"
                break
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                socket.timeout,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                part.unlink(missing_ok=True)
                if isinstance(exc, OSError) and not isinstance(exc, urllib.error.URLError):
                    if _is_disk_full(exc):
                        raise ManagedCliError(ERROR_DISK_FULL, "磁盘已满") from exc
                last = exc
                cert = cert or tlstrust.cert_verification_error(exc)
                _log_transport_failure(attempt, exc, job.trust_source, _plain_host(src.host))
                reason = "transport（{}）".format(
                    logsafe.known(
                        type(tlstrust.root_cause(exc)).__name__, tlstrust.TRANSPORT_ERROR_NAMES
                    )
                )
                if attempt < DOWNLOAD_ATTEMPTS:
                    time.sleep(1.0)
                continue
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            job.emit(PHASE_VERIFY, 0, src.size)
            if got != src.sha256:
                part.unlink(missing_ok=True)
                LOG.warning(
                    "托管 CLI 归档 SHA-256 与锁不符（来源 %s）：拒绝，不换源", _plain_host(src.host)
                )
                raise ManagedCliError(
                    ERROR_HASH_MISMATCH,
                    "下载的归档 SHA-256 与锁文件不符",
                    expected=src.sha256,
                    got=got,
                )
            dest = dl / f"{cli.id}-{src.sha256[:12]}.archive"
            try:
                os.replace(part, dest)
            except OSError as exc:
                part.unlink(missing_ok=True)
                raise ManagedCliError(ERROR_WRITE_FAILED, f"归档落盘失败: {exc}") from exc
            return dest, src
        if queue:
            LOG.warning(
                "托管 CLI 下载源 %s 放弃：%s；改用 %s",
                _plain_host(src.host),
                reason,
                _plain_host(queue[0][0].host),
            )
    if cert is not None:
        raise ManagedCliError(ERROR_TLS, f"证书校验失败: {cert}")
    if http_failures and http_failures == tried:
        status_code = int(getattr(last, "code", 0) or 0)
        raise ManagedCliError(
            ERROR_SOURCE_UNAVAILABLE, f"来源回 HTTP {status_code}", status=status_code
        )
    raise ManagedCliError(ERROR_OFFLINE, f"下载失败: {last}")


def _log_transport_failure(attempt: int, exc: BaseException, trust_source: str, host) -> None:
    root = tlstrust.root_cause(exc)
    cert = tlstrust.cert_verification_error(exc)
    LOG.warning(
        "托管 CLI 下载第 %d/%d 次失败（来源 %s）：%s（信任来源 %s，verify_code=%s）: %s",
        attempt,
        DOWNLOAD_ATTEMPTS,
        host,
        logsafe.known(type(root).__name__, tlstrust.TRANSPORT_ERROR_NAMES),
        logsafe.known(trust_source, tlstrust.SOURCES),
        getattr(cert, "verify_code", None) if cert is not None else None,
        str(root),
    )


def _fetch(src: ArchiveSource, part: Path, job: _Job, *, judge_slow: bool = False) -> str:
    """一次传输：流式写 `.part` 并顺手算 sha256；回实得 hash。取消时抛。"""
    req = urllib.request.Request(src.url, headers={"User-Agent": _user_agent()})
    h = hashlib.sha256()
    done = 0
    job.emit(PHASE_DOWNLOAD, 0, src.size)
    ctx = tlstrust.client_context()
    try:
        fh = part.open("wb")
    except OSError as exc:
        raise ManagedCliError(ERROR_WRITE_FAILED, f"归档缓存不可写: {exc}") from exc
    # 每次现建 opener：代理环境变量要在下载那一刻读（与私有 Python 同）；HTTPS 上下文来自 tlstrust
    job.trust_source = tlstrust.source_of(ctx)
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    with fh, opener.open(req, timeout=NETWORK_TIMEOUT_S) as resp:
        started = time.monotonic()
        read = getattr(resp, "read1", None) or resp.read
        while True:
            job.check()
            chunk = read(READ_BYTES)
            if not chunk:
                break
            try:
                fh.write(chunk)
            except OSError as exc:
                code = ERROR_DISK_FULL if _is_disk_full(exc) else ERROR_WRITE_FAILED
                raise ManagedCliError(code, f"归档缓存写入失败: {exc}") from exc
            h.update(chunk)
            done += len(chunk)
            job.emit(PHASE_DOWNLOAD, done, src.size)
            if judge_slow:
                elapsed = time.monotonic() - started
                if elapsed >= SLOW_GRACE_S:
                    rate = done / elapsed if elapsed > 0 else 0.0
                    eta = (max(0, src.size - done) / rate) if rate > 0 else float("inf")
                    if eta > SLOW_ETA_S:
                        raise _TooSlow(rate, eta)
    return h.hexdigest()


# --------------------------------------------------------------- 解包
def _member_rel(member: tarfile.TarInfo, strip: str) -> str | None:
    """成员相对树根的路径；前缀之外的成员回 None（忽略）。路径越界 / 非常规成员直接拒绝。"""
    name = member.name
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or ":" in parts[0]:
        raise ManagedCliError(
            ERROR_INVALID_ARCHIVE, "归档成员是绝对路径或含非 POSIX 分隔", member=name
        )
    if ".." in parts:
        raise ManagedCliError(ERROR_INVALID_ARCHIVE, "归档成员越界", member=name)
    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        raise ManagedCliError(
            ERROR_INVALID_ARCHIVE, "归档里有非常规成员（链接 / 设备）", member=name
        )
    norm = posixpath.normpath(name)
    if strip:
        prefix = strip.rstrip("/")
        if norm == prefix:
            return ""
        if not norm.startswith(prefix + "/"):
            return None
        norm = norm[len(prefix) + 1 :]
    return "" if norm == "." else norm


def _extract(archive: Path, staging: Path, src: ArchiveSource, cli: ManagedCli, job: _Job) -> None:
    """逐成员校验 + 手写落盘（不用 `extractall`：解释器版本不同的过滤器语义不同，这里不依赖它）。
    总量超过锁里的 `unpacked_size` 立刻拒绝（防解压炸弹）。"""
    if src.raw:
        _install_raw(archive, staging, cli, job)
        return
    written = 0
    count = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                rel = _member_rel(member, src.strip)
                if rel is None:
                    continue
                if rel == "":
                    continue
                if src.only and rel != src.only:
                    continue  # 只取登记的那一个成员（npm 包里的 package.json / 许可证等不进树）
                target = staging.joinpath(*rel.split("/"))
                _under(target, staging)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                written += int(member.size)
                if written > cli.unpacked_size:
                    raise ManagedCliError(ERROR_INVALID_ARCHIVE, "归档解开后比锁里登记的大")
                count += 1
                job.check()
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f is None:
                    raise ManagedCliError(
                        ERROR_INVALID_ARCHIVE, "归档成员读不出", member=member.name
                    )
                try:
                    with target.open("wb") as out:
                        shutil.copyfileobj(f, out, CHUNK)
                    os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
                except OSError as exc:
                    code = ERROR_DISK_FULL if _is_disk_full(exc) else ERROR_WRITE_FAILED
                    raise ManagedCliError(code, f"解包写入失败: {exc}") from exc
                job.emit(PHASE_EXTRACT, written, cli.unpacked_size)
    except (tarfile.TarError, EOFError, ValueError) as exc:
        raise ManagedCliError(ERROR_INVALID_ARCHIVE, f"归档解不开: {exc}") from exc
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise ManagedCliError(ERROR_INVALID_ARCHIVE, f"归档读不了: {exc}") from exc
        raise
    if count != cli.file_count:
        raise ManagedCliError(
            ERROR_INVALID_ARCHIVE,
            "归档里的文件数与锁里登记的不同",
            expected=cli.file_count,
            got=count,
        )


def _install_raw(archive: Path, staging: Path, cli: ManagedCli, job: _Job) -> None:
    """裸二进制来源：下载物（已核过 sha256）原样放到入口路径，不改一个字节。"""
    size = archive.stat().st_size
    if size > cli.unpacked_size:
        raise ManagedCliError(ERROR_INVALID_ARCHIVE, "下载物比锁里登记的大")
    target = staging.joinpath(*cli.entrypoint.split("/"))
    _under(target, staging)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
        os.chmod(target, 0o755)
    except OSError as exc:
        code = ERROR_DISK_FULL if _is_disk_full(exc) else ERROR_WRITE_FAILED
        raise ManagedCliError(code, f"裸二进制落盘失败: {exc}") from exc
    job.emit(PHASE_EXTRACT, size, cli.unpacked_size)
    if cli.file_count != 1:
        raise ManagedCliError(
            ERROR_INVALID_ARCHIVE,
            "裸二进制来源的文件数只能是 1",
            expected=cli.file_count,
            got=1,
        )


def tree_digest(root: Path, progress=None) -> tuple[str, int, int]:
    """一棵目录树的摘要：对按路径排序的 `<相对路径>\\t<大小>\\t<sha256>\\n` 逐行再做 sha256。
    只认常规文件与目录（遇到软链接 / 其它直接抛 invalid_archive）。回 (hex, 文件数, 总字节)。"""
    root = Path(root)
    rows: list[tuple[str, int, str]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                raise ManagedCliError(ERROR_INVALID_ARCHIVE, "树里有非常规文件", member=fn)
            rel = p.relative_to(root).as_posix()
            size = p.stat().st_size
            rows.append((rel, size, _sha256_file(p)))
            total += size
            if progress is not None:
                progress(total)
    rows.sort()
    h = hashlib.sha256()
    for rel, size, sha in rows:
        h.update(f"{rel}\t{size}\t{sha}\n".encode())
    return h.hexdigest(), len(rows), total


def _make_executable(path: Path) -> None:
    if not path.is_file():
        raise ManagedCliError(ERROR_INVALID_ARCHIVE, "解开的树里没有入口文件")
    if os.name != "nt":
        try:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError as exc:
            raise ManagedCliError(ERROR_WRITE_FAILED, f"入口文件权限设不上: {exc}") from exc


def _probe(entry: Path, cli: ManagedCli) -> str:
    """真起一次 `--version`：退出码 0 且输出里有锁里的版本号才算数。"""
    try:
        out = subprocess.run(
            [str(entry), "--version"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env={**os.environ, **MANAGED_CHILD_ENV.get(cli.name, {})},
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagedCliError(ERROR_LAUNCH_FAILED, "托管 CLI 的 --version 超时") from exc
    except OSError as exc:
        raise ManagedCliError(ERROR_LAUNCH_FAILED, f"托管 CLI 起不来: {exc}") from exc
    text = ((out.stdout or "") + (out.stderr or "")).strip()
    if out.returncode != 0 or cli.version not in text:
        raise ManagedCliError(
            ERROR_LAUNCH_FAILED, "托管 CLI 的 --version 与锁里的版本不符", returncode=out.returncode
        )
    return text.splitlines()[0][:80]
