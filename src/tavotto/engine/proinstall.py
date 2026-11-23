"""高级版闭源组件的**下载、验签、安装、切换、回退**（ADR 0198 §五–§七）。`prohost` 只管进程与协议，安装链在这里。

流程（后台线程，永不抛给调用方）：`check_for_update()` → `license.pro_manifest()` 取清单与限时下载地址 → `verify_release()` 验发布签名
与字段 → `download()` 流式下载并边下边算 sha256 → `install()` 解压到临时目录、核对包内容、原子改名成版本目录、原子改写 `current.json`。
任何一步不对都留在原状态（旧版或免费版），不报错卡住。

* **没有令牌 / `license.has` 对任何功能键都不成立 / 这个平台没有组件 = 一次网络都不发**（ADR 0198 §五）。
* **信任根是发布签名，不是服务端**：清单用独立的发布密钥（kid 前缀 `pro-`）签名，公钥内置在 `RELEASE_KEYS`；服务端被攻破也推不了恶意包。
  **正式版 `RELEASE_KEYS` 现在是空表**（发布密钥由维护者本人生成后再加）→ 正式安装里验不过任何清单。测试发布公钥只在开发 / 验收模式
  （设了合规的 `TAVOTTO_LICENSE_ENDPOINT`，与 `license._public_key` 同一判据）经 `TAVOTTO_PRO_TEST_RELEASE_PUBKEY` 认，且只认 kid `pro-test`。
* **下载地址钉死**：https、主机 ∈ `PRO_DOWNLOAD_HOSTS`、路径以 `/pro/` 起；开发模式（授权端点是回环开发端点）额外认 127.0.0.1 / localhost 的 http。
  出站经 `diagsend.stream_get`（平台证书校验、不跟随重定向、超时、字节上限 = 清单 `size`）。
* **`release.json` 对账口径**：包内 `release.json` 是发布清单的负载 JSON，但**不含 `sha256` 与 `size`**（这两项描述 zip 自己，不可能写进 zip 里）。
  比较时两边都去掉这两个键，再各自按 `license.canonicalize`（键排序、紧凑、无浮点）规范化成 UTF-8 字节，逐字节相等才算一致。
* **安装持锁**（进程内 `threading.Lock` + `pro/.lock` 文件锁，跨进程）；只保留 current 与 previous 两个版本目录，更早的删；**正在运行的
  sidecar 所在目录不删**（问 prohost 登记的 `register_running_root`）。任何一步失败：清理临时目录、`current.json` 不动。
* **`bad.json`**：健康检查失败过的版本目录名。`install` 不装 bad 里的版本；服务端推荐的版本不在 bad 里（= 推荐版本变了）就把 bad 清空
  （`_sync_bad`，ADR 0198 §六「服务端换了推荐版本后清空」的简化口径）。

纯标准库（`nacl` 只经 `ed25519util` 延后 import）；Flask 父进程 import 链上的模块。依赖只朝一个方向：`prohost` import 本模块，本模块
**不** import `prohost`（正在运行的版本由 prohost 经 `register_running_root` 登记查询函数）。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import stat
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

from . import atomicio, config, diagsend, ed25519util, license as engine_license, logsafe

LOG = logging.getLogger("tavotto.proinstall")

ENV_TEST_RELEASE_PUBKEY = "TAVOTTO_PRO_TEST_RELEASE_PUBKEY"
#: 测试发布密钥在 `pro-` 命名空间里的唯一 kid（只在开发 / 验收模式认）。
TEST_KID = "pro-test"
KID_PREFIX = "pro-"
#: 发布公钥表 `kid -> base64(32 字节公钥)`。**正式版留空**：发布密钥由维护者本人生成后再加（ADR 0198 §五）。
RELEASE_KEYS: dict[str, str] = {}

PLATFORMS = frozenset({"macos-arm64", "macos-x86_64", "windows-x64"})
#: 下载主机白名单（ADR 0198 §五）；路径必须以 `/pro/` 开头。
PRO_DOWNLOAD_HOSTS: tuple[str, ...] = ("dl.tavotto.cn",)
PRO_PATH_PREFIX = "/pro/"
_DEV_DL_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: zip 字节数上限 300 MiB：Nuitka 独立目录版（含 Python 运行时与 numpy 一类依赖）压缩后预期在 100 MiB 量级，300 MiB 给三倍余量；
#: 再大的清单当作异常（被篡改 / 配错）拒收，也限制了一次下载最多占用的磁盘与流量。
MAX_PRO_BYTES = 300 * 1024 * 1024
#: 解压后总字节上限 1 GiB（≈ 压缩上限的 3.4 倍，对应常见的 3× 压缩比再留余量）；按**实际写出的字节**计，不信 zip 目录里声明的大小。
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
#: 条目数上限：Nuitka 独立目录里有几千个文件（动态库 / 数据），5 万足够且防 zip 炸弹式的海量小文件。
MAX_ENTRIES = 50_000
DOWNLOAD_TIMEOUT_S = 30  # 每次 socket 操作
DOWNLOAD_DEADLINE_S = 30 * 60  # 整个下载的总时限（大陆慢网络下 300 MiB 也够）
LOCK_TIMEOUT_S = 60.0
STALE_TMP_S = 3600  # 孤儿 `.tmp-` / `.dl-` 超过一小时才清（别的进程可能正在用）
MAX_BAD = 32

DIR_NAME = "pro"
STATE_FILE = "current.json"
BAD_FILE = "bad.json"
LOCK_FILE = ".lock"
PACKAGE_DIR = "tavotto-pro"
RELEASE_FILE = "release.json"

_SAFE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}")
_VERSION_DIR = re.compile(
    r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}-(?:macos-arm64|macos-x86_64|windows-x64)"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
#: 这两个键描述 zip 自己，不进 `release.json` 对账（见模块文档）。
_SELF_DESCRIBING = ("sha256", "size")

#: 本模块抛 / 返回的稳定失败码闭集。
FAILURES = frozenset(
    {
        "bad_envelope",
        "unknown_kid",
        "bad_signature",
        "bad_payload",
        "platform_mismatch",
        "unsupported_api",
        "bad_features",
        "bad_hash",
        "bad_size",
        "bad_version",
        "host_not_allowed",
        "download_failed",
        "size_exceeded",
        "size_mismatch",
        "hash_mismatch",
        "bad_package",
        "release_mismatch",
        "busy",
        "io_error",
    }
)
#: `check_for_update()` 返回的 `status` 闭集。
STATUSES = frozenset(
    {
        "disabled",
        "no_platform",
        "unlicensed",
        "busy",
        "manifest_failed",
        "no_release",
        "rejected",
        "bad_version",
        "current",
        "installed",
        "failed",
    }
)


#: 日志「结果码」位明文放行的闭集（`logsafe.known` 要求模块常量）：本模块的码 + 授权客户端的失败码 + 空。
_LOG_CODES = FAILURES | frozenset(engine_license.FAILURES) | {""}


#: 宿主认得的 pro 协议版本（ADR 0198 §三）。唯一出处在这里，`SUPPORTED_PRO_API` 引用它
#: （依赖只朝一个方向：prohost → proinstall，本模块不 import prohost）。
SUPPORTED_PRO_API = frozenset({1})

#: 「此刻在跑的 sidecar 根目录」的查询函数，由 prohost 在导入时登记（`register_running_root`）；没登记 = 当作没在跑
_running_root = None


def register_running_root(fn) -> None:
    """prohost 登记一个无参函数：返回正在运行的 sidecar 的根目录（`Path`），没有在跑返回 None。"""
    global _running_root
    _running_root = fn


class ProInstallError(Exception):
    """下载 / 验签 / 安装失败。`code` ∈ `FAILURES`。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# 平台与路径
# ---------------------------------------------------------------------------
def _system() -> str:
    return platform.system()


def _machine() -> str:
    return platform.machine()


def platform_key() -> str | None:
    """本机的组件平台键；不在首版支持列表（wheel / Linux / 其他架构）= None = 没有组件，不联网。"""
    system, machine = _system(), _machine().lower()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        if machine in ("x86_64", "amd64"):
            return "macos-x86_64"
    elif system == "Windows" and machine in ("amd64", "x86_64"):
        return "windows-x64"
    return None


def pro_dir() -> Path:
    return config.data_path(DIR_NAME)


def version_name(payload: dict) -> str:
    return f"{payload['pro_version']}-{payload['platform']}"


def version_root(name: str) -> Path | None:
    """版本目录里 prohost 要拉起的那一层（`<name>/tavotto-pro/`）；目录不存在 = None。"""
    p = pro_dir() / name / PACKAGE_DIR
    return p if p.is_dir() else None


def _dev_mode() -> bool:
    """开发 / 验收模式：设了合规的 `TAVOTTO_LICENSE_ENDPOINT`（与 `license._public_key`、`prohost._dev_dir` 同一判据）。"""
    return (
        engine_license._endpoint_override() is not None
        and engine_license.resolve_target() is not None
    )


# ---------------------------------------------------------------------------
# 验签
# ---------------------------------------------------------------------------
def _b64decode(text: object) -> bytes:
    if not isinstance(text, str):
        raise ValueError("not str")
    return base64.b64decode(text.encode("ascii"), validate=True)


def _release_pubkey(kid: object) -> bytes | None:
    if not isinstance(kid, str) or not kid.startswith(KID_PREFIX):
        return None
    known = RELEASE_KEYS.get(kid)
    if known is not None:
        try:
            pub = _b64decode(known)
        except (ValueError, binascii.Error):
            return None
        return pub if len(pub) == 32 else None
    if kid == TEST_KID and _dev_mode():
        raw = os.environ.get(ENV_TEST_RELEASE_PUBKEY, "").strip()
        if raw:
            try:
                pub = _b64decode(raw)
            except (ValueError, binascii.Error):
                return None
            return pub if len(pub) == 32 else None
    return None


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def verify_release(envelope: object) -> dict:
    """发布清单信封 `{"payload": b64(JSON), "sig": b64, "kid"}` → 通过验证的负载字典；任一项不对抛 `ProInstallError`（闭集码）。
    核：kid（`pro-` 前缀且已知）、Ed25519 签名、`v == 1`、`platform == platform_key()`、`pro_api` 是支持的整数、`features` ⊆ 功能键闭集、
    `sha256` 64 位小写 hex、`size` ∈ [1, `MAX_PRO_BYTES`]、`released_at` 整数、`pro_version` 只含能当目录名的安全字符。"""
    if not isinstance(envelope, dict):
        raise ProInstallError("bad_envelope")
    pub = _release_pubkey(envelope.get("kid"))
    if pub is None:
        raise ProInstallError("unknown_kid")
    try:
        raw = _b64decode(envelope.get("payload"))
        sig = _b64decode(envelope.get("sig"))
    except (ValueError, binascii.Error):
        raise ProInstallError("bad_envelope") from None
    if not ed25519util.verify(pub, raw, sig):
        raise ProInstallError("bad_signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProInstallError("bad_payload") from None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ProInstallError("bad_payload")
    if payload.get("platform") != platform_key():
        raise ProInstallError("platform_mismatch")
    api = payload.get("pro_api")
    if not _is_int(api) or api not in SUPPORTED_PRO_API:
        raise ProInstallError("unsupported_api")
    feats = payload.get("features")
    if (
        not isinstance(feats, list)
        or not all(isinstance(f, str) for f in feats)
        or not set(feats) <= engine_license.FEATURES
    ):
        raise ProInstallError("bad_features")
    sha = payload.get("sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise ProInstallError("bad_hash")
    size = payload.get("size")
    if not _is_int(size) or not 1 <= size <= MAX_PRO_BYTES:
        raise ProInstallError("bad_size")
    released = payload.get("released_at")
    if not _is_int(released) or released < 0:
        raise ProInstallError("bad_payload")
    ver = payload.get("pro_version")
    if not isinstance(ver, str) or not _SAFE_VERSION.fullmatch(ver):
        raise ProInstallError("bad_version")
    return payload


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def validate_download_url(url: object) -> str:
    """下载地址：https、主机 ∈ `PRO_DOWNLOAD_HOSTS`（精确匹配）、无用户信息 / 片段、端口缺省或 443、路径以 `/pro/` 起且无 `..` 段。
    开发模式（授权端点是回环开发端点）额外认 127.0.0.1 / localhost 的 http(s)（任意端口）。其余一律 `host_not_allowed`。"""
    if not isinstance(url, str) or not url or len(url) > 2048 or not url.isascii():
        raise ProInstallError("host_not_allowed")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise ProInstallError("host_not_allowed") from None
    if (
        parts.username is not None
        or parts.password is not None
        or "@" in parts.netloc
        or parts.fragment
    ):
        raise ProInstallError("host_not_allowed")
    path = urllib.parse.unquote(parts.path)
    if not path.startswith(PRO_PATH_PREFIX) or ".." in path.split("/") or "\\" in path:
        raise ProInstallError("host_not_allowed")
    dev_ok = (
        isinstance(engine_license.resolve_target(), engine_license._DevTarget)
        and host in _DEV_DL_HOSTS
        and parts.scheme in ("http", "https")
    )
    prod_ok = parts.scheme == "https" and host in PRO_DOWNLOAD_HOSTS and port in (None, 443)
    if not (dev_ok or prod_ok):
        raise ProInstallError("host_not_allowed")
    return url


def _discard(p: Path) -> None:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        LOG.warning("高级版组件: 清理临时文件失败")


def download(url: str, size: int, sha256: str) -> Path:
    """流式下载到 `data_dir()/pro/.dl-<随机>`，边下边算 sha256；**字节数一超过 `size` 立刻中止**。结束核 size 与 sha256，
    不对就删临时文件并抛 `ProInstallError`（`size_exceeded` / `size_mismatch` / `hash_mismatch` / `download_failed`）。成功返回临时文件路径（调用方负责删）。"""
    validate_download_url(url)
    if not _is_int(size) or not 1 <= size <= MAX_PRO_BYTES or not _SHA256.fullmatch(str(sha256)):
        raise ProInstallError("bad_size")
    d = pro_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ProInstallError("io_error") from None
    tmp = d / f".dl-{secrets.token_hex(8)}"
    digest = hashlib.sha256()
    total = 0
    try:
        with open(tmp, "wb") as f:
            for block in diagsend.stream_get(
                url, max_bytes=size, timeout=DOWNLOAD_TIMEOUT_S, deadline_s=DOWNLOAD_DEADLINE_S
            ):
                total += len(block)
                digest.update(block)
                f.write(block)
            f.flush()
            os.fsync(f.fileno())
        if total != size:
            raise ProInstallError("size_mismatch")
        if digest.hexdigest() != sha256:
            raise ProInstallError("hash_mismatch")
    except diagsend._Failure as fail:
        _discard(tmp)
        raise ProInstallError(
            "size_exceeded" if fail.code == "too_large" else "download_failed"
        ) from None
    except ProInstallError:
        _discard(tmp)
        raise
    except OSError:
        _discard(tmp)
        raise ProInstallError("io_error") from None
    except BaseException:
        _discard(tmp)
        raise
    return tmp


# ---------------------------------------------------------------------------
# 状态文件与锁
# ---------------------------------------------------------------------------
_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked(timeout: float = LOCK_TIMEOUT_S):
    """进程内锁 + `pro/.lock` 文件锁（跨进程；写法同 `svgstore`）。拿不到抛 `busy`。"""
    if not _THREAD_LOCK.acquire(timeout=timeout):
        raise ProInstallError("busy")
    fd = None
    locked = False
    try:
        d = pro_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            fd = os.open(d / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            raise ProInstallError("io_error") from None
        deadline = time.monotonic() + timeout
        while True:
            if _try_flock(fd):
                locked = True
                break
            if time.monotonic() >= deadline:
                raise ProInstallError("busy") from None
            time.sleep(0.02)
        yield
    finally:
        if fd is not None:
            try:
                if locked:
                    _funlock(fd)
            finally:
                os.close(fd)
        _THREAD_LOCK.release()


def _try_flock(fd: int) -> bool:
    """非阻塞地给 `fd` 上排他文件锁（POSIX `flock` / Windows `msvcrt.locking`）；拿到 True，被别人占着 False。进程死亡时系统自动释放。"""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _funlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 「正在使用」租约（跨进程）：每个跑着 sidecar 的宿主各持一个 `pro/.inuse-<版本>@<随机号>.lock` 的排他文件锁
# （一人一个文件，几个宿主同跑一个版本也都各有租约；Windows 的 msvcrt 没有共享锁，所以不用「一个文件 + 共享锁」）。
# 进程死了系统自动释放，不记 PID。锁文件放在版本目录外面：不改包内容，删目录也不被它挡住。
# ---------------------------------------------------------------------------
_INUSE_PREFIX = ".inuse-"
_INUSE_SEP = "@"
_INUSE_SUFFIX = ".lock"
#: 刚建出、还没来得及上锁的租约文件不当孤儿删（建文件与上锁之间有一瞬间的空档）
_INUSE_FRESH_S = 60.0


def _version_of_root(root: object) -> str | None:
    """sidecar 根目录 -> 它所在的版本目录名（不在 `pro/` 下 = 开发目录来源 = None）。"""
    try:
        rel = Path(root).resolve().relative_to(pro_dir().resolve())  # type: ignore[arg-type]
    except (ValueError, OSError):
        return None
    return _valid_name(rel.parts[0]) if rel.parts else None


def _lease_version(file_name: str) -> str | None:
    """租约文件名 -> 版本目录名；不是租约文件 = None。"""
    if not (file_name.startswith(_INUSE_PREFIX) and file_name.endswith(_INUSE_SUFFIX)):
        return None
    core = file_name[len(_INUSE_PREFIX) : -len(_INUSE_SUFFIX)]
    name, sep, _token = core.rpartition(_INUSE_SEP)
    return name if sep and name else None


def acquire_in_use(root: object) -> tuple[int, Path] | None:
    """给 `root` 所在的已安装版本上「正在使用」租约，返回句柄；不是已安装版本 / 拿不到 = None（尽力而为，不抛）。
    拿不到只会让别的进程少一道保护，不阻止本进程启动 sidecar。"""
    name = _version_of_root(root)
    if name is None:
        return None
    path = pro_dir() / f"{_INUSE_PREFIX}{name}{_INUSE_SEP}{secrets.token_hex(8)}{_INUSE_SUFFIX}"
    fd = None
    try:
        pro_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        for _ in range(50):  # 别的进程正探这个文件时会短暂占着锁：稍等重试
            if _try_flock(fd):
                return fd, path
            time.sleep(0.01)
    except OSError:
        pass
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink()
    return None


def release_in_use(handle: tuple[int, Path] | None) -> None:
    """放掉 `acquire_in_use` 给的租约并删掉自己的租约文件；重复 / None 无害。"""
    if handle is None:
        return
    fd, path = handle
    with contextlib.suppress(OSError):
        path.unlink()  # POSIX 先删再放锁，探的人不会在放锁后、删除前误判；Windows 持着删不掉，放锁后再删一次
    with contextlib.suppress(OSError):
        _funlock(fd)
    with contextlib.suppress(OSError):
        os.close(fd)
    with contextlib.suppress(OSError):
        path.unlink()


def _lease_held(p: Path) -> bool:
    """这个租约文件此刻有人持着锁吗？没人持着、又不是刚建的 = 孤儿，顺手删掉。探不了（IO 错）按「持着」算，宁可不删。"""
    try:
        fd = os.open(p, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if not _try_flock(fd):
            return True
        try:
            if time.time() - os.fstat(fd).st_mtime > _INUSE_FRESH_S:
                with contextlib.suppress(OSError):
                    p.unlink()  # Windows 上持着锁删不掉：留到下一轮
        finally:
            with contextlib.suppress(OSError):
                _funlock(fd)
        return False
    finally:
        os.close(fd)


def _leases(name: str | None = None) -> list[Path]:
    try:
        children = list(pro_dir().iterdir())
    except OSError:
        return []
    out = []
    for p in children:
        v = _lease_version(p.name)
        if v is not None and (name is None or v == name):
            out.append(p)
    return out


def _in_use_elsewhere(name: str) -> bool:
    """有没有任何宿主（别的进程，或本进程另一处）持着 `name` 的租约；顺手清掉这个版本的孤儿租约文件。"""
    held = False
    for p in _leases(name):
        held = _lease_held(p) or held  # 不短路：每个文件都探一遍，孤儿一并清
    return held


def _read_json(p: Path) -> dict:
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _valid_name(v: object) -> str | None:
    return v if isinstance(v, str) and _VERSION_DIR.fullmatch(v) else None


def read_state() -> dict:
    """`{"current": 名字 | None, "previous": 名字 | None}`（不看目录在不在）。"""
    rec = _read_json(pro_dir() / STATE_FILE)
    return {
        "current": _valid_name(rec.get("current")),
        "previous": _valid_name(rec.get("previous")),
    }


def current_name() -> str | None:
    return read_state()["current"]


def _write_state(current: str | None, previous: str | None) -> None:
    """先写临时文件再 `os.replace`（`atomicio.write_bytes` 的序列）。"""
    data = json.dumps({"current": current, "previous": previous}, separators=(",", ":")).encode(
        "utf-8"
    )
    try:
        atomicio.write_bytes(pro_dir() / STATE_FILE, data)
    except OSError:
        raise ProInstallError("io_error") from None


def read_bad() -> list[str]:
    raw = _read_json(pro_dir() / BAD_FILE).get("bad")
    return [n for n in (raw if isinstance(raw, list) else []) if _valid_name(n)][:MAX_BAD]


def _write_bad(names: list[str]) -> None:
    data = json.dumps({"v": 1, "bad": names[-MAX_BAD:]}, separators=(",", ":")).encode("utf-8")
    try:
        atomicio.write_bytes(pro_dir() / BAD_FILE, data)
    except OSError:
        raise ProInstallError("io_error") from None


def mark_bad(name: str) -> None:
    """记一个健康检查失败的版本目录名（不再自动切到它）。名字不合规 / 写不下去只记日志，不抛。"""
    if _valid_name(name) is None:
        return
    try:
        with _locked(10.0):
            bad = read_bad()
            if name not in bad:
                _write_bad([*bad, name])
    except ProInstallError:
        LOG.warning("高级版组件: 记录坏版本失败")


def _sync_bad(recommended: str) -> None:
    """服务端推荐的版本不在 bad 里 = 推荐版本变了 → 清空 bad（ADR 0198 §六）。"""
    with _locked():
        bad = read_bad()
        if bad and recommended not in bad:
            _write_bad([])


def _running_version() -> str | None:
    """正在运行的 sidecar 所在的版本目录名（开发目录来源 / 没有在跑 = None）。"""
    probe = _running_root
    root = probe() if probe is not None else None
    return _version_of_root(root) if root is not None else None


def _prune(keep: set[str]) -> None:
    """删掉不在 `keep` 里、也不是正在运行的版本目录；顺带清过期的孤儿临时文件。只碰名字合规的版本目录与 `.dl-` / `.tmp-`。"""
    running = _running_version()
    now = time.time()
    try:
        children = list(pro_dir().iterdir())
    except OSError:
        return
    for p in children:
        n = p.name
        if _VERSION_DIR.fullmatch(n):
            # 其它进程的 sidecar 可能正从这个目录跑：租约探不通就不删
            if n not in keep and n != running and not _in_use_elsewhere(n):
                _discard(p)
        elif _lease_version(n) is not None:
            # 对应版本目录已不在的租约文件：没人持有、又不是刚建的才删
            if not (pro_dir() / str(_lease_version(n))).exists():
                _lease_held(p)
        elif n.startswith((".dl-", ".tmp-")):
            try:
                if now - p.stat().st_mtime > STALE_TMP_S:
                    _discard(p)
            except OSError:
                pass


def rollback(failed: str) -> bool:
    """`failed` 版本健康检查没过：切回 previous（previous 的目录得在、且不在 bad 里），True；没有可退的就把 current 置空（= 没有组件），False。
    当前 current 已不是 `failed`（别的进程已切过）= 什么都不做，返回 True。只改 `current.json`，永不抛。"""
    try:
        with _locked(10.0):
            st = read_state()
            if st["current"] != failed:
                return True
            prev = st["previous"]
            ok = prev is not None and prev not in read_bad() and version_root(prev) is not None
            _write_state(prev if ok else None, None)
            _prune({prev} if ok else set())
            return bool(ok)
    except ProInstallError:
        LOG.warning("高级版组件: 回退失败")
        return False


# ---------------------------------------------------------------------------
# 解压与安装
# ---------------------------------------------------------------------------
def _canonical(obj: dict) -> bytes:
    return engine_license.canonicalize(obj).encode("utf-8")


def _unsafe_name(name: str) -> bool:
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        return True
    parts = name.rstrip("/").split("/")
    return any(p in ("", ".", "..") for p in parts)


def _extract(zip_path: Path, dest: Path) -> None:
    """安全解压：拒路径穿越 / 绝对路径 / 反斜杠 / 盘符 / 符号链接与其他特殊文件 / 加密条目 / 顶层多余条目；按**实际写出字节**限总量。"""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ProInstallError("bad_package") from None
    with z:
        infos = z.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise ProInstallError("bad_package")
        written = 0
        root = dest.resolve()
        seen: set[str] = set()
        for info in infos:
            name = info.filename
            # Windows 上 zipfile 读入时把 `\\` 规范成 `/`，原始条目名在 orig_filename 里，两个都查
            if (
                _unsafe_name(name)
                or _unsafe_name(info.orig_filename)
                or name in seen
                or info.flag_bits & 0x1
            ):
                raise ProInstallError("bad_package")
            seen.add(name)
            if name.split("/", 1)[0] not in (PACKAGE_DIR, RELEASE_FILE):
                raise ProInstallError("bad_package")
            mode = info.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ProInstallError("bad_package")  # 符号链接 / 设备文件等
            target = (dest / name.rstrip("/")).resolve()
            if root not in target.parents and target != root:
                raise ProInstallError("bad_package")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with z.open(info) as src, open(target, "xb") as out:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        written += len(block)
                        if written > MAX_UNPACKED_BYTES:
                            raise ProInstallError("bad_package")
                        out.write(block)
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError):
                raise ProInstallError("bad_package") from None
            if mode & 0o111:
                with contextlib.suppress(OSError):
                    os.chmod(target, 0o755)  # 保留可执行位（zip 里 Unix 的权限位）


def _without_self(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _SELF_DESCRIBING}


def _check_package(tmp: Path, payload: dict) -> None:
    """包里要有 `tavotto-pro/` 与 `release.json`，且 `release.json` 与清单负载按模块文档的口径逐字节一致。"""
    pkg, rel = tmp / PACKAGE_DIR, tmp / RELEASE_FILE
    if not pkg.is_dir() or not rel.is_file():
        raise ProInstallError("bad_package")
    try:
        inner = json.loads(rel.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProInstallError("release_mismatch") from None
    if not isinstance(inner, dict):
        raise ProInstallError("release_mismatch")
    try:
        same = _canonical(_without_self(inner)) == _canonical(_without_self(payload))
    except ValueError:
        same = False
    if not same:
        raise ProInstallError("release_mismatch")


def install(zip_path: Path | str, envelope: object) -> dict:
    """把已下载的 zip 装成版本目录并切 `current.json`。`envelope` 是服务端给的清单信封（这里**再验一次**，不信调用方）。
    持锁；任何一步失败清理临时目录、`current.json` 不动。成功回 `{"current", "previous", "pro_version"}`。"""
    payload = verify_release(envelope)
    name = version_name(payload)
    zip_path = Path(zip_path)
    with _locked():
        if name in read_bad():
            raise ProInstallError("bad_version")
        d = pro_dir()
        tmp = d / f".tmp-{secrets.token_hex(8)}"
        final = d / name
        renamed = False
        try:
            tmp.mkdir(parents=True)
            _extract(zip_path, tmp)
            _check_package(tmp, payload)
            st = read_state()
            if st["current"] == name:
                _discard(tmp)
                return {
                    "current": name,
                    "previous": st["previous"],
                    "pro_version": payload["pro_version"],
                }
            if final.exists():
                if name == _running_version() or _in_use_elsewhere(name):
                    raise ProInstallError("busy")  # 目标目录正有 sidecar 在跑：不动它
                _discard(final)
            os.rename(tmp, final)
            renamed = True
            prev = st["current"]
            _write_state(name, prev)
            _prune({name} | ({prev} if prev else set()))
            return {"current": name, "previous": prev, "pro_version": payload["pro_version"]}
        except BaseException:
            _discard(tmp)
            if renamed and read_state()["current"] != name:
                _discard(final)  # 改名成功但 current.json 没切：不留一个没人引用的目录
            raise
        finally:
            if tmp.exists():
                _discard(tmp)


# ---------------------------------------------------------------------------
# 检查更新（后台）
# ---------------------------------------------------------------------------
_CHECK_LOCK = threading.Lock()


def _log(status: str, code: str = "") -> None:
    LOG.info(
        "高级版组件: %s %s",
        logsafe.known(status, STATUSES),
        logsafe.known(code, _LOG_CODES),
    )


def _result(status: str, code: str = "", app_update_needed: bool = False) -> dict:
    _log(status, code)
    return {"status": status, "code": code, "app_update_needed": app_update_needed}


def check_for_update() -> dict:
    """一次完整的「取清单 → 验签 → 下载 → 安装」。返回 `{"status", "code", "app_update_needed"}`，**永不抛**（异常折成日志 + 状态）。

    没有令牌 / `license.has` 对任何功能键都不成立 / 平台不受支持：**一次网络都不发**。同一时刻只跑一个（别的调用直接回 `busy`）。
    `app_update_needed` 原样透传给调用方（本版不做界面）。清单**先验签再比对 current**（验签只是本机计算，不能拿没验过的 `pro_version` 做判断）。"""
    if not _CHECK_LOCK.acquire(blocking=False):
        return _result("busy")
    try:
        return _check()
    except Exception:  # noqa: BLE001 — 后台线程不允许把主进程带下水
        LOG.warning("高级版组件: 检查更新异常", exc_info=True)
        return {"status": "failed", "code": "", "app_update_needed": False}
    finally:
        _CHECK_LOCK.release()


def _check() -> dict:
    if not engine_license.enabled():
        return _result("disabled")
    plat = platform_key()
    if plat is None:
        return _result("no_platform")
    if not any(engine_license.has(k) for k in sorted(engine_license.FEATURES)):
        return _result("unlicensed")
    try:
        got = engine_license.pro_manifest(plat, sorted(SUPPORTED_PRO_API))
    except engine_license.LicenseError as exc:
        return _result("manifest_failed", exc.code if exc.code in engine_license.FAILURES else "")
    if "state" in got:
        return _result("no_release")
    app_needed = bool(got["app_update_needed"])
    try:
        payload = verify_release(got["release"])
        name = version_name(payload)
        _sync_bad(name)
        if name == current_name():
            return _result("current", "", app_needed)
        if name in read_bad():
            return _result("bad_version", "", app_needed)
        validate_download_url(got["url"])
        tmp = download(got["url"], payload["size"], payload["sha256"])
        try:
            install(tmp, got["release"])
        finally:
            _discard(tmp)
    except ProInstallError as exc:
        return _result("rejected", exc.code, app_needed)
    return _result("installed", "", app_needed)


def register() -> None:
    """把 `check_for_update` 接到授权回调（激活 / 刷新成功后与每日后台刷新）；幂等，由 `app.py` 启动时调用一次。"""
    engine_license.register_after_license(check_for_update)
