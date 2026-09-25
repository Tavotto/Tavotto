"""Tavotto 私有完整 Python：没有合格基础解释器的机器上，给受管环境当 base 的那份 CPython
（统一实施包 U05，ADR 0063）。

它只回答一个问题：**受管环境的基础解释器从哪来**。装包仍是 U04 的那一个事务（`deprepair`
的代事务 + pip）：python-build-standalone 的 install_only 自带 venv / ensurepip / pip，所以
`create_generation_venv` / `pip_install_joint_argv` 一个字节不用改，本模块交出去的只是一条
解释器路径。不出现第二个安装器、第二份安装锁（D05）。

    <data_dir>/private-python/
        runtimes/<id>/            id = cpython-<版本>-<sha256 前 12 位>：**按内容命名、不可变**
        runtimes/.staging-<id>-<pid>/   解包 + 真起一次的临时目录；从不叫最终名字
        downloads/<归档名>        校验过的归档缓存（离线可复用，FO24）；`.part` 是还没校验的
        ledger.json               账：供应过哪些 runtime、何时、来源——**不是指针**

「现在该用哪一个」不设第二个指针文件：锁文件（`resources/private_python_lock.json`）钉着的
那一份就是当前的；换版本 = 改锁 → 新 id → 新目录，旧目录留到没有受管环境再以它为 base
（`retire_unused`）才删。同一份字节永远落在同一个目录：已在就复用、不重解、不重下。

四条纪律（判据的主语写在各自函数上）：

* **校验先于一切执行**：`_download()` 只在整份归档 sha256 与锁一致时才把 `.part` 改成正式名；
  解包只读正式名；解释器只在解包完、成员校验完之后才起一次。坏 hash / 截断的路上解释器
  执行计数为 0、`runtimes/` 里没有新目录、`.part` 当场删掉（FO26）。
* **只往 `data_dir` 之下写**：归档成员逐条校验（绝对路径 / `..` / 越界软链接 / 非常规成员
  一律拒绝，不靠 `tarfile` 的默认行为），落点再过一次 `_assert_under`。不改 PATH、shell、
  注册表、默认 Python、用户的 `.python-version`——本模块没有任何一行写到那些地方，用例用
  HOME / PATH 前后快照钉住。
* **联网只有一条路**：`urllib`（与 `updater` 同一张脸：TLS 校验默认开、代理只从
  `HTTP(S)_PROXY` / `NO_PROXY` 环境变量来），不读 pip.conf / uv 配置 / 项目设置，
  不带任何身份。离线 = 传输层失败，`private_python_offline`，不重试到天亮（FO25）。
* **取消按消费者管理（D11）**：同一个 id 的并发请求只下一次（`_inflight`），每个消费者
  各自等；一个消费者取消只是它自己退出，下载在最后一个消费者放弃时才中止；提交点
  （`os.replace` 到最终目录）之后取消无效——目录不可变，留下的永远是完整的一份。

纯标准库（Flask 父进程 import 链上，被 `managedenv` / `deprepair` import）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import logging
import os
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

from . import brand, config, logsafe, runtime

LOG = logging.getLogger("tavotto.privatepython")

LOCK_SCHEMA = 1
LOCK_PARTS = ("resources", "private_python_lock.json")

DIRNAME = "private-python"
RUNTIMES_DIRNAME = "runtimes"
DOWNLOADS_DIRNAME = "downloads"
LEDGER_NAME = "ledger.json"
LEDGER_SCHEMA = 1
STAGING_PREFIX = ".staging-"

#: 工程 / CI 逃生门：`1` 在锁文件 `enabled=false` 的目标上也提供这条路，`0` 一律不提供。
#: 与 `TAVOTTO_RUNTIME_HOST_ARCH` 同一档——给目标验证腿用的，不是产品开关。
ENABLE_ENV = "TAVOTTO_PRIVATE_PYTHON"

#: 稳定错误码（协议契约；`ERROR_CODES` 给 `tests/test_error_codes.py` 门禁读）。
ERROR_NOT_OFFERED = "private_python_not_offered"
ERROR_OFFLINE = "private_python_offline"
ERROR_SOURCE_UNAVAILABLE = "private_python_source_unavailable"
ERROR_HASH_MISMATCH = "private_python_hash_mismatch"
ERROR_DISK_LOW = "private_python_disk_low"
ERROR_CANCELLED = "private_python_cancelled"
ERROR_INVALID_ARCHIVE = "private_python_invalid_archive"
ERROR_LAUNCH_FAILED = "private_python_launch_failed"
ERROR_WRITE_FAILED = "private_python_write_failed"
ERROR_CODES = (
    ERROR_NOT_OFFERED,
    ERROR_OFFLINE,
    ERROR_SOURCE_UNAVAILABLE,
    ERROR_HASH_MISMATCH,
    ERROR_DISK_LOW,
    ERROR_CANCELLED,
    ERROR_INVALID_ARCHIVE,
    ERROR_LAUNCH_FAILED,
    ERROR_WRITE_FAILED,
)

#: 下载阶段（`on_progress(stage, done, total)` 的第一个参数；闭集）。
STAGE_DOWNLOADING = "downloading"
STAGE_VERIFYING = "verifying"
STAGE_EXTRACTING = "extracting"
STAGE_LAUNCHING = "launching"
STAGE_COMMITTED = "committed"

NETWORK_TIMEOUT_S = 30
#: 传输层失败的有界重试（hash 不符**绝不**重试：对不上的文件是拒绝的对象，不是再下一次的对象）。
DOWNLOAD_ATTEMPTS = 2
CHUNK = 1 << 20
PROBE_TIMEOUT_S = 60
#: 等别的消费者把同一份下载完的上限；超过按离线处置（不是永远等）。
WAIT_TIMEOUT_S = 1800
#: 磁盘配额：归档 + 解开（实测 macOS 25 MB → 106 MB；Linux 归档更大）+ 余量。量不出来不拦。
EXTRACTED_FACTOR = 5
DISK_MARGIN_BYTES = 64 * 1024 * 1024
#: 别的进程留下的 staging 目录多久算孤儿（Windows 上量不了 pid 存活时的兜底）。
STALE_STAGING_S = 3600

_lock = threading.RLock()


class ProvisionError(RuntimeError):
    """带稳定 code 的供应失败；`deprepair` 原样转成 `RepairError`。"""

    def __init__(self, code: str, message: str = "", **detail):
        super().__init__(message or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- 锁文件
@dataclasses.dataclass(frozen=True)
class PythonSource:
    """锁文件里一个目标的来源——**唯一**决定「下什么、多大、字节是什么、解释器在哪」。"""

    target: str
    version: str
    release: str
    triple: str
    url: str
    sha256: str
    size: int
    archive_root: str
    python_rel: str
    enabled: bool

    @property
    def id(self) -> str:
        return f"cpython-{self.version}-{self.sha256[:12]}"

    @property
    def archive_name(self) -> str:
        return urllib.parse.unquote(posixpath.basename(urllib.parse.urlsplit(self.url).path))

    def to_payload(self) -> dict:
        """交给界面的形态：版本 / 目标 / 体积 / 来源域名，没有机器路径。"""
        return {
            "id": self.id,
            "version": self.version,
            "target": self.target,
            "download_bytes": int(self.size),
            "source_host": urllib.parse.urlsplit(self.url).hostname or "",
        }


def lock_path() -> Path:
    """`resources/private_python_lock.json`：`importlib.resources` → 源码树兜底（与 tutorial 同一条纪律）。"""
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath(*LOCK_PARTS)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return Path(__file__).resolve().parent.parent.joinpath(*LOCK_PARTS)


_HEX64 = frozenset("0123456789abcdef")


def validate_lock(lock: dict) -> None:
    """锁文件必须真的「锁住」（与 `build_worker_runtime.validate_lock` 同一种严格度）。"""
    if not isinstance(lock, dict) or lock.get("schema") != LOCK_SCHEMA:
        raise ValueError(
            f"私有 Python 锁文件 schema={lock.get('schema') if isinstance(lock, dict) else '?'}，只认 {LOCK_SCHEMA}"
        )
    py = lock.get("python")
    if not isinstance(py, dict):
        raise ValueError("锁文件缺 python 块")
    for key in ("version", "implementation", "release", "flavor", "archive_root"):
        if not py.get(key):
            raise ValueError(f"锁文件 python.{key} 缺失")
    version = str(py["version"])
    if not all(part.isdigit() for part in version.split(".")) or version.count(".") != 2:
        raise ValueError(f"python.version 必须是精确补丁版本，拿到 {version!r}")
    if py.get("implementation") != "cpython" or py.get("flavor") != "install_only":
        raise ValueError("私有 Python 只认 cpython 的 install_only 发行版")
    targets = lock.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise ValueError("锁文件 targets 缺失或为空")
    for name, t in targets.items():
        if not isinstance(t, dict):
            raise ValueError(f"目标 {name} 不是对象")
        for key in ("os", "arch", "triple", "url", "sha256", "python_rel"):
            if not t.get(key):
                raise ValueError(f"目标 {name} 的 {key} 缺失")
        if name != f"{t['os']}-{t['arch']}":
            raise ValueError(f"目标 {name} 的名字与 os-arch 不一致")
        sha = str(t["sha256"])
        if len(sha) != 64 or any(c not in _HEX64 for c in sha):
            raise ValueError(f"{name}: sha256 必须是 64 位小写十六进制")
        if not isinstance(t.get("size"), int) or t["size"] <= 0:
            raise ValueError(f"{name}: size 必须是正整数")
        if not str(t["url"]).startswith("https://"):
            raise ValueError(f"{name}: 来源必须是 https")
        if not isinstance(t.get("enabled"), bool):
            raise ValueError(f"{name}: enabled 必须是布尔值")
        rel = str(t["python_rel"])
        if rel.startswith(("/", "\\")) or ".." in rel.split("/"):
            raise ValueError(f"{name}: python_rel 必须是相对路径")


def load_lock(path: Path | None = None) -> dict:
    lock = json.loads((path or lock_path()).read_text(encoding="utf-8"))
    validate_lock(lock)
    return lock


def host_target() -> str | None:
    """当前进程的目标名（`<os>-<arch>`，与锁文件的键同形）；认不出回 None。"""
    host_os = runtime.host_os()
    arch = runtime.host_arch()
    if host_os == "other" or not arch:
        return None
    return f"{host_os}-{arch}"


def source_for(target: str | None = None, lock: dict | None = None) -> PythonSource | None:
    """这个目标的来源；锁里没有回 None。`target` 缺省为宿主。"""
    name = target or host_target()
    if not name:
        return None
    try:
        lock = lock or load_lock()
    except (OSError, ValueError) as exc:
        LOG.warning("私有 Python 锁文件不可用: %s", exc)
        return None
    t = lock["targets"].get(name)
    if not t:
        return None
    py = lock["python"]
    return PythonSource(
        target=name,
        version=str(py["version"]),
        release=str(py["release"]),
        triple=str(t["triple"]),
        url=str(t["url"]),
        sha256=str(t["sha256"]).lower(),
        size=int(t["size"]),
        archive_root=str(py["archive_root"]),
        python_rel=str(t["python_rel"]),
        enabled=bool(t["enabled"]),
    )


def offered(source: PythonSource | None) -> bool:
    """产品在这个目标上提供不提供这条路：环境变量逃生门 > 锁文件 `enabled`。"""
    if source is None:
        return False
    flag = os.environ.get(ENABLE_ENV, "").strip()
    if flag == "1":
        return True
    if flag == "0":
        return False
    return bool(source.enabled)


# --------------------------------------------------------------- 位置
def root_dir() -> Path:
    return config.data_path(DIRNAME)


def runtimes_dir() -> Path:
    return root_dir() / RUNTIMES_DIRNAME


def downloads_dir() -> Path:
    return root_dir() / DOWNLOADS_DIRNAME


def runtime_dir(source: PythonSource) -> Path:
    return runtimes_dir() / source.id


def runtime_python(source: PythonSource) -> Path:
    return runtime_dir(source) / source.python_rel


def archive_path(source: PythonSource) -> Path:
    return downloads_dir() / source.archive_name


def _assert_under(path: Path, root: Path) -> Path:
    """`path`（realpath）必须在 `root`（realpath）之下——软链接指出去也算越界。"""
    p = Path(os.path.realpath(path))
    r = Path(os.path.realpath(root))
    try:
        p.relative_to(r)
    except ValueError:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "落点不在私有目录之下") from None
    return p


# --------------------------------------------------------------- 账
def read_ledger() -> dict:
    path = root_dir() / LEDGER_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": LEDGER_SCHEMA, "runtimes": {}}
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
        return {"schema": LEDGER_SCHEMA, "runtimes": {}}
    if not isinstance(data.get("runtimes"), dict):
        data["runtimes"] = {}
    return data


def _write_ledger(data: dict) -> None:
    path = root_dir() / LEDGER_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 临时名带 pid：两个进程同时记账时各写各的，别在同一个 .tmp 上互相撞（Windows 上撞了是 PermissionError）
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        LOG.warning("私有 Python 账写入失败: %s", exc)


def _record(source: PythonSource, probe: dict) -> None:
    with _lock:
        data = read_ledger()
        data["runtimes"][source.id] = {
            "version": source.version,
            "release": source.release,
            "triple": source.triple,
            "target": source.target,
            "url": source.url,
            "sha256": source.sha256,
            "size": source.size,
            "python_rel": source.python_rel,
            "provisioned_at": int(time.time()),
            "last_used": int(time.time()),
            "reported_version": str(probe.get("version", "")),
        }
        _write_ledger(data)


def touch(source: PythonSource) -> None:
    with _lock:
        data = read_ledger()
        rec = data["runtimes"].get(source.id)
        if rec:
            rec["last_used"] = int(time.time())
            _write_ledger(data)


# --------------------------------------------------------------- 状态（不起子进程）
def python_of(source: PythonSource | None = None) -> str | None:
    """这个目标的私有 Python **现在在不在**：最终目录里的解释器文件在且可执行才回路径。

    只看最终目录：staging 从不叫这个名字，所以「在」就是「校验过、真起过一次、原子改名过」。
    不起子进程（问它的地方在渲染出错的响应路径上）。
    """
    source = source or source_for()
    if source is None:
        return None
    python = runtime_python(source)
    try:
        if not python.is_file():
            return None
        if os.name != "nt" and not os.access(python, os.X_OK):
            return None
    except OSError:
        return None
    return str(python)


def status(source: PythonSource | None = None) -> dict:
    """对外视图：提不提供、在不在、要不要下、下多少。没有机器路径。"""
    source = source or source_for()
    if source is None:
        return {
            "offered": False,
            "target": host_target() or "",
            "provisioned": False,
            "cached": False,
            "reason": ERROR_NOT_OFFERED,
        }
    present = python_of(source) is not None
    cached = False
    try:
        cached = archive_path(source).is_file()
    except OSError:
        cached = False
    return {
        **source.to_payload(),
        "offered": offered(source),
        "enabled_by_default": source.enabled,
        "provisioned": present,
        "cached": cached and not present,
        "reason": "" if offered(source) else ERROR_NOT_OFFERED,
    }


def offer_payload(source: PythonSource | None = None) -> dict | None:
    """挂在计划上的「要不要先准备私有 Python」段；不提供 / 已在时回 None。

    有值就意味着这次授权**包含下载**：`download_bytes` 是界面必须说出口的数字
    （已有校验过的归档缓存时 `cached=True`、`download_bytes=0`——那时不联网，FO24）。
    """
    source = source or source_for()
    if source is None or not offered(source) or python_of(source) is not None:
        return None
    cached = False
    try:
        cached = _cached_archive_ok(source)
    except OSError:
        cached = False
    return {
        **source.to_payload(),
        "required": True,
        "cached": cached,
        "download_bytes": 0 if cached else int(source.size),
        "network_required": not cached,
    }


def present_payload(source: PythonSource | None = None) -> dict | None:
    """已供应就位的那份的载荷（`required=False`、零字节、不联网）：这台机器没有别的 Python、受管环境要
    从它建时，界面照样要把「用的是 Tavotto 自己的 Python x」说出口——它不是下载授权，是环境来源的披露。
    不提供 / 还没就位回 None。"""
    source = source or source_for()
    if source is None or not offered(source) or python_of(source) is None:
        return None
    return {
        **source.to_payload(),
        "required": False,
        "cached": True,
        "download_bytes": 0,
        "network_required": False,
    }


def _cached_archive_ok(source: PythonSource) -> bool:
    path = archive_path(source)
    return path.is_file() and _sha256_file(path) == source.sha256


#: 锁里目标名的 os 段 → PEP 508 marker 的三个平台字段（与目标解释器自报的取法一致：`sys.platform` /
#: `os.name` / `platform.system()`）。
_MARKER_PLATFORM = {
    "macos": ("darwin", "posix", "Darwin"),
    "linux": ("linux", "posix", "Linux"),
    "windows": ("win32", "nt", "Windows"),
}


def standin_marker_env(source: PythonSource) -> dict:
    """私有 Python **还没落盘**时替它答 PEP 508 的 marker 环境（PR B：干净机器上算第一份计划要用）。

    判据的主语：Python 相关字段来自**锁**（版本 / 实现），平台字段来自**这台机器**（目标就是这台机器：
    `platform_machine` / `platform_release` / `platform_version` 与真起后自报的相同，`sys_platform` /
    `os_name` / `platform_system` 由锁的 os 段定）。它只服务「要装什么」的第一次披露；供应之后事务按真解释器
    重新量（`depplan.target_facts`），不拿替身当真值。
    """
    import platform as _platform

    sys_platform, os_name, system = _MARKER_PLATFORM.get(
        source.target.split("-", 1)[0], ("", "", "")
    )
    major_minor = ".".join(source.version.split(".")[:2])
    return {
        "implementation_name": "cpython",
        "implementation_version": source.version,
        "os_name": os_name,
        "platform_machine": _platform.machine(),
        "platform_release": _platform.release(),
        "platform_system": system,
        "platform_version": _platform.version(),
        "python_full_version": source.version,
        "platform_python_implementation": "CPython",
        "python_version": major_minor,
        "sys_platform": sys_platform,
    }


def require_free_disk(source: PythonSource) -> None:
    """下载 + 解开要落得下（`size × EXTRACTED_FACTOR + 余量`）；量不出来不拦。"""
    need = int(source.size) * EXTRACTED_FACTOR + DISK_MARGIN_BYTES
    try:
        root = root_dir()
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(root)).free
    except OSError:
        return
    if free < need:
        raise ProvisionError(
            ERROR_DISK_LOW, "磁盘剩余空间不足以准备私有 Python", need_bytes=need, free_bytes=free
        )


# --------------------------------------------------------------- 供应：去重与消费者
class _Inflight:
    """同一个 id 正在进行的一次供应：一个下载线程，若干消费者各自等。"""

    def __init__(self, source: PythonSource):
        self.source = source
        self.done = threading.Event()
        self.abort = threading.Event()
        self.consumers = 0
        self.committed = threading.Event()  # `os.replace` 之后置上：此后的取消对这份供应无效
        self.result: str | None = None
        self.error: ProvisionError | None = None
        self.progress: tuple[str, int, int] = (STAGE_DOWNLOADING, 0, int(source.size))
        self.listeners: list = []


_inflight: dict[str, _Inflight] = {}


def provision(
    source: PythonSource | None = None,
    *,
    cancel_ev: threading.Event | None = None,
    on_progress=None,
    wait_timeout: float = WAIT_TIMEOUT_S,
) -> str:
    """把这个目标的私有 Python 准备好，回解释器路径。已在就直接回（不联网、不起子进程）。

    同一个 id 的并发调用只下载一次：第一个消费者起下载线程，其余的等它；每个消费者只管
    自己的 `cancel_ev`——它取消只是自己以 `private_python_cancelled` 退出，下载在最后一个
    消费者也放弃时才中止（`_Inflight.abort`）。提交之后目录不可变，取消不再有任何效果。
    """
    source = source or source_for()
    if source is None or not offered(source):
        raise ProvisionError(ERROR_NOT_OFFERED, "这个目标上不提供私有 Python")
    existing = python_of(source)
    if existing:
        return existing
    if cancel_ev is not None and cancel_ev.is_set():
        # 进来之前就取消了：不起下载线程、不挂到别人的下载上（快的本地 / 缓存供应会在第一次轮询
        # 之前就提交，那时「已取消」的消费者拿到的是成功——Codex #464 第二轮 P2）
        raise ProvisionError(ERROR_CANCELLED, "已取消")
    with _lock:
        job = _inflight.get(source.id)
        leader = job is None
        if job is None:
            job = _Inflight(source)
            _inflight[source.id] = job
        job.consumers += 1
        if on_progress is not None:
            job.listeners.append(on_progress)
    if leader:
        threading.Thread(
            target=_run_inflight,
            args=(job,),
            daemon=True,
            name=f"tavotto-private-python-{source.id}",
        ).start()
    deadline = time.time() + wait_timeout
    try:
        while not job.done.wait(0.2):
            if cancel_ev is not None and cancel_ev.is_set():
                with _lock:
                    committed = job.committed.is_set()
                if committed:
                    continue  # 提交点之后取消无效：等它记完账，拿到的是完整的一份
                raise ProvisionError(ERROR_CANCELLED, "已取消")
            if time.time() > deadline:
                raise ProvisionError(ERROR_OFFLINE, "等待下载超时")
    finally:
        with _lock:
            job.consumers -= 1
            if on_progress is not None and on_progress in job.listeners:
                job.listeners.remove(on_progress)
            if job.consumers <= 0 and not job.committed.is_set():
                job.abort.set()  # 最后一个消费者也走了：中止下载、清掉半成品
    if job.error is not None:
        raise ProvisionError(job.error.code, str(job.error), **job.error.detail)
    assert job.result
    return job.result


def _run_inflight(job: _Inflight) -> None:
    try:
        job.result = _provision_once(job.source, job)
    except ProvisionError as exc:
        job.error = exc
    except Exception as exc:  # noqa: BLE001 — 线程里不许漏异常：消费者要拿到一个 code
        LOG.exception("私有 Python 供应线程异常")
        job.error = ProvisionError(ERROR_WRITE_FAILED, str(exc))
    finally:
        with _lock:
            if _inflight.get(job.source.id) is job:
                _inflight.pop(job.source.id, None)
        job.done.set()


def _emit(job: _Inflight, stage: str, done: int, total: int) -> None:
    job.progress = (stage, done, total)
    with _lock:
        listeners = list(job.listeners)
    for fn in listeners:
        try:
            fn(stage, done, total)
        except Exception:  # noqa: BLE001 — 进度回调不许让下载失败
            LOG.debug("进度回调异常", exc_info=True)


def _check_abort(job: _Inflight) -> None:
    if job.abort.is_set():
        raise ProvisionError(ERROR_CANCELLED, "所有消费者都已取消")


# --------------------------------------------------------------- 供应：一次完整的状态机
def _provision_once(source: PythonSource, job: _Inflight) -> str:
    """下载 / 校验 → 解到 staging → 成员校验 → 真起一次 → `os.replace` 到最终目录 → 记账。

    任一步失败：`.part` 与 staging 删掉、最终目录不存在、账不动。最终目录已在（别的进程刚好
    先完成）则复用它。执行计数只在解包并校验完之后才增（用例钉着：坏 hash 那条路上是 0）。
    """
    runtimes = runtimes_dir()
    final = runtime_dir(source)
    try:
        runtimes.mkdir(parents=True, exist_ok=True)
        downloads_dir().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"私有目录不可写: {exc}") from exc
    _reap_orphans(runtimes)
    existing = python_of(source)
    if existing:
        return existing
    require_free_disk(source)
    archive = _download(source, job)
    _check_abort(job)
    staging = runtimes / f"{STAGING_PREFIX}{source.id}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True)
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"staging 目录不可建: {exc}") from exc
    try:
        _emit(job, STAGE_EXTRACTING, 0, source.size)
        extracted = _extract(archive, staging, source)
        _check_abort(job)
        python = extracted / source.python_rel
        _assert_under(python, root_dir())
        _verify_executable(python)
        _emit(job, STAGE_LAUNCHING, 0, source.size)
        probe = _launch(python, source)
        # 提交点与「最后一个消费者放弃」在同一把锁下判：被接受的取消（abort 已置）绝不会发布，
        # 发布之后到来的取消对这份供应无效（`committed` 已置）——两者不会交错（Codex #464 P2）
        with _lock:
            _check_abort(job)
            try:
                os.replace(extracted, final)  # 提交点：同一文件系统内的 rename，要么在要么不在
            except OSError as exc:
                if final.exists():
                    LOG.info("私有 Python %s 已由别的进程就位，复用", source.id)
                else:
                    raise ProvisionError(ERROR_WRITE_FAILED, f"最终目录改名失败: {exc}") from exc
            job.committed.set()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = python_of(source)
    if not result:
        raise ProvisionError(ERROR_LAUNCH_FAILED, "改名后最终目录里没有可执行的解释器")
    _record(source, probe)
    _emit(job, STAGE_COMMITTED, source.size, source.size)
    LOG.info("私有 Python 就位: %s（%s）", source.id, logsafe.version(source.version))
    return result


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(source: PythonSource, job: _Inflight) -> Path:
    """校验过的归档路径。缓存命中且 sha256 一致就不联网；否则下到 `.part`、整份校验通过才改名。

    传输层失败（连不上 / TLS / 超时 / 读到一半断）有界重试后报 `private_python_offline`；
    HTTP 4xx / 5xx 报 `private_python_source_unavailable`（钉死的地址上没有这个文件——那是
    要升级 Tavotto 的事，不是重试的事）；hash 不符报 `private_python_hash_mismatch`，**不重试**。
    """
    dest = archive_path(source)
    try:
        if dest.is_file():
            _emit(job, STAGE_VERIFYING, 0, source.size)
            if _sha256_file(dest) == source.sha256:
                return dest
            dest.unlink()  # 缓存里躺着一份对不上的：不是复用对象
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存不可读: {exc}") from exc
    # `.part` 带 pid + 随机后缀：两个进程同时供应同一份时各写各的，先完成的把正式名 `os.replace`
    # 上去，后完成的再 replace 一次同一份字节（校验过才会走到这里）——不会有谁在 rename 时发现
    # 自己的 `.part` 已被别人搬走（Codex #464 P2）。孤儿 `.part` 由 `_reap_orphans` 按时限清。
    part = dest.with_name(f"{dest.name}.{os.getpid()}-{secrets.token_hex(4)}.part")
    last: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        _check_abort(job)
        try:
            got = _fetch(source, part, job)
        except urllib.error.HTTPError as exc:
            part.unlink(missing_ok=True)
            raise ProvisionError(
                ERROR_SOURCE_UNAVAILABLE, f"来源回 HTTP {exc.code}", status=int(exc.code)
            ) from exc
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as exc:
            part.unlink(missing_ok=True)
            last = exc
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(1.0)
            continue
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        _emit(job, STAGE_VERIFYING, source.size, source.size)
        if got != source.sha256:
            part.unlink(missing_ok=True)
            raise ProvisionError(
                ERROR_HASH_MISMATCH,
                "下载的归档 SHA-256 与锁文件不符",
                expected=source.sha256,
                got=got,
            )
        try:
            os.replace(part, dest)
        except OSError as exc:
            part.unlink(missing_ok=True)
            # Windows 上正式名被别的进程打开着（它刚把自己那份搬上去、正在解包）时 replace 会拒
            # （共享冲突）：那一份的字节校验过才叫这个名字，hash 对得上就直接用它——两个进程同时供应
            # 同一份，后到的复用而不是报 write_failed（#467 Windows 腿确定性红）
            if _is_verified_archive(dest, source):
                LOG.info("归档 %s 已由别的进程落盘，复用", source.archive_name)
                return dest
            raise ProvisionError(ERROR_WRITE_FAILED, f"归档落盘失败: {exc}") from exc
        return dest
    raise ProvisionError(ERROR_OFFLINE, f"下载失败: {last}")


def _is_verified_archive(dest: Path, source: PythonSource) -> bool:
    try:
        return dest.is_file() and _sha256_file(dest) == source.sha256
    except OSError:
        return False


def _user_agent() -> str:
    try:
        from .. import __version__
    except Exception:  # noqa: BLE001
        __version__ = ""
    return f"{brand.PRODUCT_NAME}/{__version__}"


def _fetch(source: PythonSource, part: Path, job: _Inflight) -> str:
    """一次传输：流式写 `.part` 并顺手算 sha256；回实得 hash。取消 / 中止时抛。"""
    req = urllib.request.Request(source.url, headers={"User-Agent": _user_agent()})
    h = hashlib.sha256()
    done = 0
    _emit(job, STAGE_DOWNLOADING, 0, source.size)
    # `.part` 打不开 / 写不进（只读目录、配额、预检之后磁盘满了）是**本机**的事，报 write_failed；
    # 传输层的 OSError 才是离线——两种恢复动作不同，不能混成一个 code（Codex #464 第二轮 P2）
    try:
        fh = part.open("wb")
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存不可写: {exc}") from exc
    # 每次现建 opener 而不是模块级 `urlopen`：后者第一次调用时把 `ProxyHandler` 连同**当时**的
    # `HTTP(S)_PROXY` / `NO_PROXY` 缓存进全局 opener，之后环境变量再变它也不看——代理配置要在
    # 下载那一刻读（用例的死代理对照就是这样量的）。TLS 校验仍是 `HTTPSHandler` 的默认。
    opener = urllib.request.build_opener()
    with fh, opener.open(req, timeout=NETWORK_TIMEOUT_S) as resp:
        while True:
            _check_abort(job)
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            try:
                fh.write(chunk)
            except OSError as exc:
                raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存写入失败: {exc}") from exc
            h.update(chunk)
            done += len(chunk)
            _emit(job, STAGE_DOWNLOADING, done, source.size)
    return h.hexdigest()


def _validate_member(member: tarfile.TarInfo, root: str) -> None:
    """归档成员的落点必须在 `<root>/` 之下，软链接目标也不许指出去；只认常规文件 / 目录 / 软链接。"""
    name = member.name
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or ":" in parts[0]:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档成员是绝对路径", member=name)
    if ".." in parts:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档成员越界", member=name)
    if parts[0] != root:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档成员不在预期的顶层目录下", member=name)
    if member.isdir() or member.isfile():
        return
    if member.issym():
        link = member.linkname
        # Windows 语义也要拒：`..\\..\\x`、`C:\\x`、`C:/x`——POSIX 归一化把反斜杠当普通字符、把盘符当目录名，
        # 没有 `data` 过滤器的解释器上会真的写到 data_dir 之外（Codex #464 P2）
        if "\\" in link or re.match(r"^[A-Za-z]:", link):
            raise ProvisionError(
                ERROR_INVALID_ARCHIVE, "归档里的软链接目标不是 POSIX 相对路径", member=name
            )
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
        if link.startswith("/") or not (target == root or target.startswith(root + "/")):
            raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档里的软链接指向目录之外", member=name)
        return
    raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档里有非常规成员", member=name)


def _extract(archive: Path, staging: Path, source: PythonSource) -> Path:
    """解到 staging；**每个成员先过 `_validate_member`**，再交给 `tarfile`（有 `data` 过滤器就用）。"""
    root = source.archive_root
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for m in members:
                _validate_member(m, root)
            kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            try:
                tar.extractall(staging, members=members, **kwargs)
            except tarfile.TarError:
                raise
            except OSError as exc:
                # 目标写不进（磁盘满 / 配额 / 权限变了）：归档本身没坏，是本机的事——报 write_failed，
                # 用户的下一步是腾空间，不是重下（Codex #464 P2）
                raise ProvisionError(ERROR_WRITE_FAILED, f"解包写入失败: {exc}") from exc
    except (tarfile.TarError, EOFError, ValueError) as exc:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, f"归档解不开: {exc}") from exc
    except OSError as exc:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, f"归档读不了: {exc}") from exc
    extracted = staging / root
    if not extracted.is_dir():
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "归档里没有预期的顶层目录", root=root)
    _assert_under(extracted, root_dir())
    return extracted


def _verify_executable(python: Path) -> None:
    try:
        st = python.stat()
    except OSError as exc:
        raise ProvisionError(ERROR_INVALID_ARCHIVE, f"归档里没有解释器: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "解释器不是常规文件")
    if os.name != "nt" and not os.access(python, os.X_OK):
        raise ProvisionError(ERROR_INVALID_ARCHIVE, "解释器没有可执行位")


_PROBE = (
    "import sys, json, platform; print(json.dumps({'version': platform.python_version(), "
    "'prefix': sys.prefix, 'executable': sys.executable}))"
)


def _probe_env() -> dict:
    """真起私有解释器用的环境：不继承 `PYTHON*`（宿主 shell 里的 PYTHONHOME 会让它起不来）。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PYTHON") and k not in ("VIRTUAL_ENV", "CONDA_PREFIX")
    }
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _launch(python: Path, source: PythonSource) -> dict:
    """真起一次：`-I -c` 打印 version / prefix / executable；版本必须等于锁的版本。"""
    try:
        proc = subprocess.run(
            [str(python), "-I", "-c", _PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            env=_probe_env(),
            cwd=str(python.parent),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvisionError(ERROR_LAUNCH_FAILED, f"私有解释器起不来: {exc}") from exc
    if proc.returncode != 0:
        raise ProvisionError(
            ERROR_LAUNCH_FAILED, "私有解释器退出非零", stderr=(proc.stderr or "")[-400:]
        )
    try:
        info = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ProvisionError(ERROR_LAUNCH_FAILED, "私有解释器没有按约定自报身份") from None
    if str(info.get("version", "")) != source.version:
        raise ProvisionError(
            ERROR_LAUNCH_FAILED,
            "私有解释器自报的版本与锁文件不符",
            expected=source.version,
            got=str(info.get("version", "")),
        )
    return info


# --------------------------------------------------------------- 孤儿与退役
def _pid_alive(pid: int) -> bool | None:
    """`True` / `False`；量不了回 None（Windows）。"""
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _reap_orphans(runtimes: Path) -> None:
    """别的进程（上一次应用）留下的 staging 与 `.part`：pid 已死或过了 `STALE_STAGING_S` 就删。

    半个 staging 从不叫最终名字，所以它们本来就不会被当成可用 runtime；清掉只是为了不占盘。
    本进程正在进行的 staging（pid == 自己）不碰。
    """
    now = time.time()
    try:
        entries = list(runtimes.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(STAGING_PREFIX):
            continue
        pid_text = entry.name.rsplit("-", 1)[-1]
        try:
            pid = int(pid_text)
        except ValueError:
            pid = -1
        if pid == os.getpid():
            continue
        alive = _pid_alive(pid) if pid > 0 else False
        try:
            stale = now - entry.stat().st_mtime > STALE_STAGING_S
        except OSError:
            stale = True
        if alive is False or (alive is None and stale):
            shutil.rmtree(entry, ignore_errors=True)
    try:
        for part in downloads_dir().glob("*.part"):
            try:
                if now - part.stat().st_mtime > STALE_STAGING_S:
                    part.unlink()
            except OSError:
                pass
    except OSError:
        pass


def list_runtimes() -> list[dict]:
    """磁盘上最终目录里的各份（id、在不在账上、是不是锁文件当前那份）；不出路径。"""
    current = source_for()
    ledger = read_ledger()["runtimes"]
    out: list[dict] = []
    try:
        entries = sorted(p for p in runtimes_dir().iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith(STAGING_PREFIX):
            continue
        rec = ledger.get(entry.name) or {}
        out.append(
            {
                "id": entry.name,
                "version": str(rec.get("version", "")),
                "current": bool(current and current.id == entry.name),
                "recorded": bool(rec),
                "last_used": int(rec.get("last_used") or 0),
            }
        )
    return out


def retire_unused(*, in_use, source: PythonSource | None = None) -> list[str]:
    """删掉不是当前锁文件那份、且 `in_use(id, 解释器路径)` 为假的旧 runtime；回删掉的 id。

    `in_use` 由调用方（deprepair）给：任一项目的受管环境哪一代以它为 base、池里 / envlease 上
    有谁用着它，都算在用——旧运行版本有租约就不 GC（FO-028 / FO-027）。当前那份永远不删。
    """
    current = source or source_for()
    removed: list[str] = []
    with _lock:
        try:
            entries = sorted(p for p in runtimes_dir().iterdir() if p.is_dir())
        except OSError:
            return removed
        ledger = read_ledger()
        for entry in entries:
            if entry.name.startswith(STAGING_PREFIX):
                continue
            if current is not None and entry.name == current.id:
                continue
            rec = ledger["runtimes"].get(entry.name) or {}
            rel = str(rec.get("python_rel") or (current.python_rel if current else "bin/python3"))
            python = str(entry / rel)
            try:
                if in_use(entry.name, python):
                    continue
            except Exception:  # noqa: BLE001 — 判「在用」失败按在用处理，宁可留着
                continue
            try:
                shutil.rmtree(entry)
            except OSError as exc:
                LOG.warning("旧私有 Python 删除失败（下次再试）: %s: %s", entry.name, exc)
                continue
            ledger["runtimes"].pop(entry.name, None)
            removed.append(entry.name)
        if removed:
            _write_ledger(ledger)
    return removed


def reset_for_tests() -> None:
    with _lock:
        _inflight.clear()
