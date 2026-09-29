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
* **字节的来源按序三处，信任只有一条**（ADR 0111）：安装包附带的归档（`runtime.private_python_bundle_dirs`，
  桌面版随包带本目标那份 pbs 归档）→ `downloads/` 里校验过的缓存 → 锁里的 URL。前两处不联网；每一处都是
  **整份 sha256 与锁一致才用**，包内那份对不上就当它不在（记 WARNING）、往下一处走。包内归档**不复制**进
  `downloads/`：它本身就是一份校验过的本地归档，后面解包 / 真起 / 原子改名是同一条链。
* **联网只有一条路**：`urllib`（代理只从 `HTTP(S)_PROXY` / `NO_PROXY` 环境变量来），证书按
  **平台原生**校验（`tlstrust`：truststore——干净 Windows 缺 ISRG Root X1 时由 CryptoAPI 按需补装，
  2026-09-28 实测），不读 pip.conf / uv 配置 / 项目设置，不带任何身份。离线 = 传输层失败，
  `private_python_offline`，不重试到天亮（FO25）；证书校验失败单列 `private_python_tls`
  （那不是网络问题），底层异常类型与消息都进日志。
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

from . import brand, config, logsafe, runtime, tlstrust

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
#: 证书校验失败（缺根 / 过期 / 主机名不符 / 被中间设备换了证书）：不是离线，「检查网络」帮不上忙。
ERROR_TLS = "private_python_tls"
#: 计划时告诉用户的归档来源（`required_origin`）此刻已不成立（包内那份不见了 / 缓存被删 / 多出一份本不该用的
#: 本地归档 / 已就位的那份没了）：**不换成另一个没说过的来源**，尤其不变成联网下载——让调用方按「计划过期」
#: 请用户重新确认（ADR 0111 §一）。
ERROR_SOURCE_CHANGED = "private_python_source_changed"
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
    ERROR_TLS,
    ERROR_SOURCE_CHANGED,
)

#: 传输层失败的根异常类型名（闭集唯一出处在 `tlstrust`：遥测 / 检查更新记同一张表）。
TRANSPORT_ERROR_NAMES = tlstrust.TRANSPORT_ERROR_NAMES

#: 归档从哪来（`offer_payload()["origin"]` 与账里的 `origin`；闭集，ADR 0111）：安装包附带 / 数据目录里
#: 校验过的缓存 / 按锁 URL 下载。前两种不联网（`download_bytes=0`）。
ORIGIN_BUNDLED = "bundled"
ORIGIN_CACHED = "cached"
ORIGIN_DOWNLOAD = "download"
ORIGINS = (ORIGIN_BUNDLED, ORIGIN_CACHED, ORIGIN_DOWNLOAD)
#: `provision(required_origin=)` 的第四个取值：计划时私有 Python 已就位（`present_payload()`），执行时它必须仍在
#: ——不解任何归档、不联网。不是 `ORIGINS` 的一员（载荷里没有这个来源）。
REQUIRE_PRESENT = "present"

#: 下载阶段（`on_progress(stage, done, total)` 的第一个参数；闭集）。
STAGE_DOWNLOADING = "downloading"
STAGE_VERIFYING = "verifying"
STAGE_EXTRACTING = "extracting"
STAGE_LAUNCHING = "launching"
STAGE_COMMITTED = "committed"

NETWORK_TIMEOUT_S = 30
#: 传输层失败的有界重试（hash 不符**绝不**重试：对不上的文件是拒绝的对象，不是再下一次的对象）。
#: 按**来源**计：主地址用完这几次才换镜像。
DOWNLOAD_ATTEMPTS = 2
CHUNK = 1 << 20
#: 下载时一次读多少（`read1`：有多少回多少，最多这么多）。小于 CHUNK：20 KB/s 的线路上 1 MiB 一读要等
#: 50 s，测速判据就要等那么久才轮得到一次。
READ_BYTES = 64 * 1024

#: 「太慢，换镜像」（2026-09-29 用户裁决「自动测速选源」；ADR 0063 修订）：还有下一个来源、且此前没有
#: 哪个来源因为慢被放弃过时，开始收字节后至少量 `SLOW_GRACE_S` 秒，此后任一时刻按**全程平均速度**估的
#: 剩余时间超过 `SLOW_ETA_S` 就放弃这个来源、换下一个。取值的理由（与实测对着看）：
#:   * 阿里云华东 Windows Server 2025 实测 GitHub 20–40 KB/s——47 MB 估剩余约 20 分钟，20 s 内判出；
#:     npmmirror 同一个文件 11 MB/s，几秒下完。旧行为是硬等 21 分钟。
#:   * 3 分钟 = Windows 归档（47 MB）要 ≥ 约 260 KB/s、Linux x86_64（118 MB）要 ≥ 约 660 KB/s 才不换——
#:     正常宽带远高于此，不会被误换；误换的代价只是丢掉 ≤ 20 s 的字节、从镜像重下同一份（hash 照验）。
#:   * 平均而不是瞬时速度：TCP 慢启动与偶发抖动不该触发切换；20 s 的量程让平均值站得住。
#: 放弃的来源不删：后面的来源都失败时它会被**不再测速**地再试一次（慢总比没有好）。
SLOW_GRACE_S = 20.0
SLOW_ETA_S = 180.0
PROBE_TIMEOUT_S = 60
#: 等别的消费者把同一份下载完的上限；超过按离线处置（不是永远等）。
WAIT_TIMEOUT_S = 1800
#: 磁盘配额：归档 + 解开（实测 macOS 25 MB → 106 MB；Linux 归档更大）+ 余量。量不出来不拦。
EXTRACTED_FACTOR = 5
DISK_MARGIN_BYTES = 64 * 1024 * 1024
#: 别的进程留下的 staging 目录多久算孤儿（Windows 上量不了 pid 存活时的兜底）。
STALE_STAGING_S = 3600

_lock = threading.RLock()


class _TooSlow(Exception):
    """这个来源太慢（`SLOW_ETA_S`）：换下一个。不出本模块。"""

    def __init__(self, rate_bps: float, eta_s: float):
        super().__init__(f"{rate_bps:.0f} B/s, ETA {eta_s:.0f} s")
        self.rate_bps = rate_bps
        self.eta_s = eta_s


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
    #: 同一份文件的备用地址（锁里 `python.mirrors` 按规则推导，`mirror_urls`）；不是信任来源——
    #: 字节照样按 `sha256` 校验。
    mirrors: tuple[str, ...] = ()

    @property
    def urls(self) -> tuple[str, ...]:
        """按顺序试的来源：锁里的 url 在前，镜像在后。"""
        return (self.url, *self.mirrors)

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
    mirrors = py.get("mirrors", [])
    if not isinstance(mirrors, list):
        raise ValueError("python.mirrors 必须是列表")
    for m in mirrors:
        if not isinstance(m, dict) or not m.get("name") or not m.get("base"):
            raise ValueError("python.mirrors 的每一项要有 name 与 base")
        base = str(m["base"])
        if not base.startswith("https://") or not base.endswith("/"):
            raise ValueError(f"镜像 {m['name']} 的 base 必须是 https 且以 / 结尾")
    if mirrors:
        # 推导规则成立的前提：主地址就是 pbs 这个 release 的发行地址，文件名就是镜像上的文件名
        prefix = f"{PBS_RELEASE_PREFIX}{py['release']}/"
        for name, t in targets.items():
            url = str(t["url"])
            if not url.startswith(prefix) or "/" in url[len(prefix) :]:
                raise ValueError(f"{name}: 配了镜像时 url 必须是 {prefix}<文件名>")


#: 镜像推导规则的前提：锁里的主地址是 pbs 某个 release 的发行地址（`validate_lock` 钉着）。
PBS_RELEASE_PREFIX = "https://github.com/astral-sh/python-build-standalone/releases/download/"


def mirror_urls(lock: dict, target: dict) -> tuple[str, ...]:
    """一个目标的镜像地址——**唯一**推导规则：`<base><release>/<主地址的文件名>`（文件名按主地址原样，
    `%2B` 不解码不重编码）。镜像上的文件与 GitHub release 同名同字节（2026-09-29 逐目标核过 size，
    Windows 那份核过 sha256）；字节对不上照样拒绝，推导错了的代价是一次 404，不是信任。"""
    release = str(lock["python"]["release"])
    filename = str(target["url"]).rsplit("/", 1)[-1]
    return tuple(
        f"{m['base']}{release}/{filename}" for m in lock["python"].get("mirrors", []) or []
    )


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
        mirrors=mirror_urls(lock, t),
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


def bundled_archive(source: PythonSource) -> Path | None:
    """安装包附带的这个目标的归档：文件在、**且整份 sha256 等于锁**才回路径；否则 None。

    判据的主语：包内那个文件**此刻**的字节（不信文件名、不信大小）。对不上的包内归档不是可用来源——
    记 WARNING、当它不在，调用方往缓存 / 下载那一处走（安装目录是只读的，删不掉也不该删）。"""
    for folder in runtime.private_python_bundle_dirs():
        cand = Path(folder) / source.archive_name
        try:
            if not cand.is_file():
                continue
            got = _sha256_file(cand)
        except OSError as exc:
            LOG.warning("包内私有 Python 归档读不了，不用它: %s", exc)
            continue
        if got == source.sha256:
            return cand
        LOG.warning(
            "包内私有 Python 归档 SHA-256 与锁不符，不用它（期望 %s，实得 %s）", source.sha256, got
        )
    return None


def archive_origin(source: PythonSource) -> str:
    """供应**此刻**会从哪拿到归档（`ORIGINS`）：包内 → 缓存 → 下载。每一处都按锁的 sha256 判。"""
    if bundled_archive(source) is not None:
        return ORIGIN_BUNDLED
    try:
        if _cached_archive_ok(source):
            return ORIGIN_CACHED
    except OSError:
        pass
    return ORIGIN_DOWNLOAD


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


def _record(
    source: PythonSource, probe: dict, origin: str = "", downloaded_from: str = ""
) -> None:
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
            "origin": origin,
            # 这份字节实际从哪个主机来（主地址 / 镜像；包内 / 缓存命中时为空）——只是账，信任看的是 sha256
            "downloaded_from": downloaded_from,
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

    有值就意味着这次授权**包含准备私有 Python**；`origin`（`ORIGINS`，ADR 0111）说字节从哪来：
    `bundled` 安装包附带、`cached` 数据目录里校验过的缓存——这两种不联网，`download_bytes=0`、
    `cached=True`（`cached` 保留原义「有校验过的本地归档、不联网」，FO24）；`download` 才按锁 URL 下载，
    `download_bytes` 是界面必须说出口的数字。
    """
    source = source or source_for()
    if source is None or not offered(source) or python_of(source) is not None:
        return None
    origin = archive_origin(source)
    local = origin != ORIGIN_DOWNLOAD
    return {
        **source.to_payload(),
        "required": True,
        "origin": origin,
        "cached": local,
        "download_bytes": 0 if local else int(source.size),
        "network_required": not local,
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

    def __init__(self, source: PythonSource, required_origin: str | None = None):
        self.source = source
        #: 领头消费者的计划说过的来源（`ORIGINS`）；None = 不绑定（工程 / 目标腿直接调用），按序三处。
        self.required_origin = required_origin
        self.done = threading.Event()
        self.abort = threading.Event()
        self.consumers = 0
        self.committed = threading.Event()  # `os.replace` 之后置上：此后的取消对这份供应无效
        self.result: str | None = None
        self.error: ProvisionError | None = None
        self.progress: tuple[str, int, int] = (STAGE_DOWNLOADING, 0, int(source.size))
        self.listeners: list = []
        self.trust_source = ""  # 最近一次传输用的信任来源（`tlstrust.SOURCES`；只进日志）
        self.source_host = ""  # 正在 / 最后从哪个主机下（主地址或镜像）；进度与账用，缓存命中为空


_inflight: dict[str, _Inflight] = {}


def _plain_host(source: PythonSource, host: str):
    """来源主机名进日志：是锁里推得出的主机（主地址 / 镜像）就明文（`logsafe.known`，诊断包里也看得见
    换没换源），否则照常按自由文本处置。空串写成 `-`。"""
    hosts = {urllib.parse.urlsplit(u).hostname or "" for u in source.urls} - {""}
    return logsafe.known(host, hosts) if host else "-"


def downloading_from(source: PythonSource) -> str:
    """这份供应此刻从哪个主机下（换了镜像就是镜像的主机名）；没在下回空串。进度载荷用。"""
    with _lock:
        job = _inflight.get(source.id)
        return job.source_host if job is not None else ""


def provision(
    source: PythonSource | None = None,
    *,
    cancel_ev: threading.Event | None = None,
    on_progress=None,
    wait_timeout: float = WAIT_TIMEOUT_S,
    required_origin: str | None = None,
) -> str:
    """把这个目标的私有 Python 准备好，回解释器路径。已在就直接回（不联网、不起子进程）。

    `required_origin`（ADR 0111）：计划时告诉用户的来源（`offer_payload()["origin"]`，或计划时已就位的
    `REQUIRE_PRESENT`）。给了就**只从这一处取**：此刻不成立即 `private_python_source_changed`，绝不静默换成
    另一处（尤其不变成联网下载）。runtime 已在（`python_of`）永远可用——那不取任何归档、不联网。

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
    if required_origin == REQUIRE_PRESENT:
        raise ProvisionError(ERROR_SOURCE_CHANGED, "计划时已就位的私有 Python 不见了")
    if cancel_ev is not None and cancel_ev.is_set():
        # 进来之前就取消了：不起下载线程、不挂到别人的下载上（快的本地 / 缓存供应会在第一次轮询
        # 之前就提交，那时「已取消」的消费者拿到的是成功——Codex #464 第二轮 P2）
        raise ProvisionError(ERROR_CANCELLED, "已取消")
    for _attempt in range(2):
        job = _consume(source, cancel_ev, on_progress, wait_timeout, required_origin)
        err = job.error
        if (
            err is not None
            and err.code == ERROR_SOURCE_CHANGED
            and job.required_origin != required_origin
        ):
            # 挂上的是别的计划领起的那一份（它说的来源不成立）：这不是本消费者的计划过期——按自己的
            # 来源再领一次（至多一次）
            continue
        break
    if job.error is not None:
        raise ProvisionError(job.error.code, str(job.error), **job.error.detail)
    assert job.result
    return job.result


def _consume(source, cancel_ev, on_progress, wait_timeout, required_origin) -> _Inflight:
    """挂到同一个 id 正在进行的那份供应上（没有就领一份），等它结束；回那份 `_Inflight`（结果或错误在上面）。"""
    with _lock:
        job = _inflight.get(source.id)
        leader = job is None
        if job is None:
            job = _Inflight(source, required_origin)
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
    return job


def _run_inflight(job: _Inflight) -> None:
    try:
        job.result = _provision_once(job.source, job)
    except ProvisionError as exc:
        job.error = exc
        # 失败进 app.log（稳定 code + 最后在用的来源主机）：2026-09-29 那台机器上失败只落在
        # environment.json，日志里一个字都没有
        LOG.warning(
            "私有 Python 供应失败：%s（来源 %s）",
            logsafe.known(exc.code, ERROR_CODES),
            _plain_host(job.source, job.source_host),
        )
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
    archive, origin = _download(source, job)
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
    _record(source, probe, origin, job.source_host if origin == ORIGIN_DOWNLOAD else "")
    _emit(job, STAGE_COMMITTED, source.size, source.size)
    LOG.info("私有 Python 就位: %s（%s，归档来自 %s）", source.id, source.version, origin)
    return result


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(source: PythonSource, job: _Inflight) -> tuple[Path, str]:
    """校验过的归档路径与它的来源（`ORIGINS`）。安装包附带的那份 sha256 与锁一致就直接用它（不复制、
    不联网，ADR 0111）；否则缓存命中且 sha256 一致也不联网；再否则下到 `.part`、整份校验通过才改名。

    传输层失败（连不上 / 超时 / 读到一半断）有界重试后报 `private_python_offline`；最后一次失败的
    根是**证书校验失败**时报 `private_python_tls`（2026-09-28 干净 Windows 实测：缺 ISRG Root X1 被报成
    「检查网络」）；每次失败的根异常类型与消息都记 WARNING（诊断包里消息按 REL-05 哈希）；
    HTTP 4xx / 5xx 报 `private_python_source_unavailable`（钉死的地址上没有这个文件——那是
    要升级 Tavotto 的事，不是重试的事）；hash 不符报 `private_python_hash_mismatch`，**不重试**。
    """
    required = job.required_origin
    bundled = (
        bundled_archive(source) if required in (None, ORIGIN_BUNDLED, ORIGIN_DOWNLOAD) else None
    )
    if bundled is not None and required in (None, ORIGIN_BUNDLED):
        _emit(job, STAGE_VERIFYING, source.size, source.size)
        return bundled, ORIGIN_BUNDLED
    if required == ORIGIN_BUNDLED:
        raise ProvisionError(ERROR_SOURCE_CHANGED, "计划时的包内归档此刻不可用（不见了或校验不符）")
    if bundled is not None:  # 计划说要下载，此刻却多出一份包内归档：不是计划里的来源
        raise ProvisionError(ERROR_SOURCE_CHANGED, "计划说要下载，此刻却有了包内归档")
    dest = archive_path(source)
    try:
        if dest.is_file():
            _emit(job, STAGE_VERIFYING, 0, source.size)
            if _sha256_file(dest) == source.sha256:
                if required == ORIGIN_DOWNLOAD:
                    raise ProvisionError(ERROR_SOURCE_CHANGED, "计划说要下载，此刻却多出一份缓存")
                return dest, ORIGIN_CACHED
            dest.unlink()  # 缓存里躺着一份对不上的：不是复用对象
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存不可读: {exc}") from exc
    if required == ORIGIN_CACHED:
        # 计划说「有缓存、不联网」：缓存没了（或坏了）就是计划过期，不联网去补
        raise ProvisionError(ERROR_SOURCE_CHANGED, "计划时的归档缓存此刻不可用")
    # `.part` 带 pid + 随机后缀：两个进程同时供应同一份时各写各的，先完成的把正式名 `os.replace`
    # 上去，后完成的再 replace 一次同一份字节（校验过才会走到这里）——不会有谁在 rename 时发现
    # 自己的 `.part` 已被别人搬走（Codex #464 P2）。孤儿 `.part` 由 `_reap_orphans` 按时限清。
    part = dest.with_name(f"{dest.name}.{os.getpid()}-{secrets.token_hex(4)}.part")
    # 来源按顺序试（ADR 0063 修订 2026-09-29「自动测速选源」）：主地址 → 镜像。换来源的三种理由：
    # 传输层失败用完 `DOWNLOAD_ATTEMPTS` 次、HTTP 4xx / 5xx、太慢（`_TooSlow`，只在还有下一个来源且
    # 此前没有来源因慢被放弃时才测）。因慢被放弃的来源排到队尾、不再测速地再试一次。每次换源一条 WARNING。
    # 每个来源都从零开始下（不续传）：判据的主语是**最终落盘的整份字节**，与来源无关。
    # **hash 不符不换源、不重试**——对不上的字节不管来自哪里都是拒绝的对象（镜像篡改就停在这里）。
    queue: list[tuple[str, bool]] = [(url, False) for url in source.urls]
    abandoned_slow = False
    last: Exception | None = None
    cert: BaseException | None = None
    http_failures = 0
    tried = 0
    while queue:
        url, retry = queue.pop(0)
        tried += 1
        host = urllib.parse.urlsplit(url).hostname or ""
        with _lock:
            job.source_host = host
        judge = bool(queue) and not abandoned_slow and not retry
        reason = ""
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            _check_abort(job)
            try:
                got = _fetch(source, url, part, job, judge_slow=judge)
            except _TooSlow as exc:
                part.unlink(missing_ok=True)
                abandoned_slow = True
                queue.append((url, True))
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
                last = exc
                cert = cert or tlstrust.cert_verification_error(exc)
                _log_transport_failure(attempt, exc, job.trust_source, _plain_host(source, host))
                reason = "transport（{}）".format(
                    logsafe.known(type(tlstrust.root_cause(exc)).__name__, TRANSPORT_ERROR_NAMES)
                )
                if attempt < DOWNLOAD_ATTEMPTS:
                    time.sleep(1.0)
                continue
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            _emit(job, STAGE_VERIFYING, source.size, source.size)
            if got != source.sha256:
                part.unlink(missing_ok=True)
                LOG.warning(
                    "私有 Python 归档 SHA-256 与锁不符（来源 %s）：拒绝，不换源",
                    _plain_host(source, host),
                )
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
                    return dest, ORIGIN_DOWNLOAD
                raise ProvisionError(ERROR_WRITE_FAILED, f"归档落盘失败: {exc}") from exc
            LOG.info(
                "私有 Python 归档下载完成：来源 %s（%s）",
                _plain_host(source, host),
                source.archive_name,
            )
            return dest, ORIGIN_DOWNLOAD
        if queue:
            nxt = urllib.parse.urlsplit(queue[0][0]).hostname or ""
            LOG.warning(
                "私有 Python 下载源 %s 放弃：%s；改用 %s",
                _plain_host(source, host),
                reason,
                _plain_host(source, nxt),
            )
    if cert is not None:
        raise ProvisionError(ERROR_TLS, f"证书校验失败: {cert}")
    if http_failures and http_failures == tried:
        status = int(getattr(last, "code", 0) or 0)
        raise ProvisionError(ERROR_SOURCE_UNAVAILABLE, f"来源回 HTTP {status}", status=status)
    raise ProvisionError(ERROR_OFFLINE, f"下载失败: {last}")


def _log_transport_failure(
    attempt: int, exc: BaseException, trust_source: str, host: str = ""
) -> None:
    """一次传输失败进日志：根异常类型（闭集明文）、信任来源（闭集明文）、OpenSSL 的 verify_code（数）、
    消息（自由文本：app.log 原样，诊断包里哈希）。2026-09-28 那台机器上 app.log 里什么都没有——
    「无法下载」到底是超时还是证书，只能靠事后另起进程复现。"""
    root = tlstrust.root_cause(exc)
    cert = tlstrust.cert_verification_error(exc)
    LOG.warning(
        "私有 Python 下载第 %d/%d 次失败（来源 %s）：%s（信任来源 %s，verify_code=%s）: %s",
        attempt,
        DOWNLOAD_ATTEMPTS,
        host,
        logsafe.known(type(root).__name__, TRANSPORT_ERROR_NAMES),
        logsafe.known(trust_source, tlstrust.SOURCES),
        getattr(cert, "verify_code", None) if cert is not None else None,
        str(root),
    )


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


def _fetch(
    source: PythonSource, url: str, part: Path, job: _Inflight, *, judge_slow: bool = False
) -> str:
    """一次传输：流式写 `.part` 并顺手算 sha256；回实得 hash。取消 / 中止时抛。

    `judge_slow`：收字节满 `SLOW_GRACE_S` 秒后，按全程平均速度估的剩余时间超过 `SLOW_ETA_S` 就抛
    `_TooSlow`（调用方换下一个来源）。总量按锁里的 `size` 算——那是唯一可信的总数。"""
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    h = hashlib.sha256()
    done = 0
    _emit(job, STAGE_DOWNLOADING, 0, source.size)
    ctx = tlstrust.client_context()
    # `.part` 打不开 / 写不进（只读目录、配额、预检之后磁盘满了）是**本机**的事，报 write_failed；
    # 传输层的 OSError 才是离线——两种恢复动作不同，不能混成一个 code（Codex #464 第二轮 P2）
    try:
        fh = part.open("wb")
    except OSError as exc:
        raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存不可写: {exc}") from exc
    # 每次现建 opener 而不是模块级 `urlopen`：后者第一次调用时把 `ProxyHandler` 连同**当时**的
    # `HTTP(S)_PROXY` / `NO_PROXY` 缓存进全局 opener，之后环境变量再变它也不看——代理配置要在
    # 下载那一刻读（用例的死代理对照就是这样量的）。HTTPS 的上下文来自 `tlstrust`：平台原生校验
    # （CERT_REQUIRED + 主机名），不是 OpenSSL 读到的根证书快照（干净 Windows 缺根，见模块头）。
    job.trust_source = tlstrust.source_of(ctx)
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    with fh, opener.open(req, timeout=NETWORK_TIMEOUT_S) as resp:
        started = time.monotonic()
        read = getattr(resp, "read1", None) or resp.read
        while True:
            _check_abort(job)
            chunk = read(READ_BYTES)
            if not chunk:
                break
            try:
                fh.write(chunk)
            except OSError as exc:
                raise ProvisionError(ERROR_WRITE_FAILED, f"归档缓存写入失败: {exc}") from exc
            h.update(chunk)
            done += len(chunk)
            _emit(job, STAGE_DOWNLOADING, done, source.size)
            if judge_slow:
                elapsed = time.monotonic() - started
                if elapsed >= SLOW_GRACE_S:
                    rate = done / elapsed if elapsed > 0 else 0.0
                    eta = (max(0, source.size - done) / rate) if rate > 0 else float("inf")
                    if eta > SLOW_ETA_S:
                        raise _TooSlow(rate, eta)
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
