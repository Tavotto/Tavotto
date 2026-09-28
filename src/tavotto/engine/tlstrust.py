"""出站 HTTPS 的信任来源：**平台原生**的证书校验（truststore），不是 OpenSSL 读到的那份根证书快照。

为什么（2026-09-28，阿里云 Windows Server 2025 build 26100 实测，冻结 NSIS 安装包）：私有 Python 的归档地址
（github.com releases）302 到 `release-assets.githubusercontent.com`，证书链的根是 ISRG Root X1。干净的
Windows 证书库**起初没有**这张根——Windows 只在 CryptoAPI / SChannel 建链时按需补装（Automatic Root
Certificates Update）；而 `ssl.create_default_context()` 在 Windows 上的 `load_default_certs` 只**枚举已安装**
的根，不触发补装。于是下载两次都 `CERTIFICATE_VERIFY_FAILED`（约 0.7 s 一次），被归成「检查网络」。同机的
pip 不受影响：新版 pip 经 truststore 走平台校验。

各平台上 `truststore.SSLContext` 是什么（与 pip 一致，全平台都用它）：

* Windows：`CertGetCertificateChain`（CryptoAPI）——缺根时按需拉取，与浏览器 / pip 同一条路；
* macOS：Security.framework（SecTrust）——认钥匙串里的根，包括学校 / 企业装的代理根；冻结产物里 OpenSSL
  的默认 CA 路径指向构建机，本来就不可靠；
* Linux：OpenSSL + 系统 CA 目录，与 `ssl.create_default_context()` 等效。

校验只会更严不会更松：上下文是 `PROTOCOL_TLS_CLIENT`（`CERT_REQUIRED` + 校验主机名）；本模块没有
`CERT_NONE`、没有 `check_hostname = False`、没有 `_create_unverified_context`（`tests/test_private_python_tls.py`
按 AST 钉）。truststore 不可用（import 失败：包坏了、冻结产物漏收）→ 退回 `ssl.create_default_context()`，
并记一条 WARNING（进程内一次）——**不静默**：那时 Windows 上缺根的机器会以 `private_python_tls` 失败，
日志里写着为什么。

**产品里的出站 HTTPS 只有三处，全经这里**（`tests/test_outbound_https_trust.py` 按 AST 钉）：私有 Python
下载（`privatepython._fetch`）、匿名遥测投递（`telemetry._post`）、检查更新（`updater._fetch_latest_release`）。
三处都是 `urllib.request.build_opener(tlstrust.https_handler(tlstrust.client_context()))`；别的 `urlopen`
全是 `http://127.0.0.1` 上的本机回环（会话凭据 / 交接 / 已在运行的实例），不经 TLS。

纯标准库 + 延后 import 的 truststore（`pyproject.toml` 的运行时依赖；冻结产物由 `packaging/tavotto.spec`
的 hiddenimports 收进——延后 import 静态分析看不见）。
"""

from __future__ import annotations

import logging
import ssl
import threading
import urllib.request

LOG = logging.getLogger("tavotto.tlstrust")

#: 信任来源（日志里明文出门的闭集，`logsafe.known`）。
SOURCE_PLATFORM = "platform"
SOURCE_OPENSSL = "openssl"
SOURCES = frozenset({SOURCE_PLATFORM, SOURCE_OPENSSL})

#: 传输层失败的根异常类型名（`root_cause` 的类型名；日志里明文出门的闭集，`logsafe.known`——不在表里的
#: 照样哈希）。私有 Python 下载、遥测投递、检查更新三处记的都是这一张表。
TRANSPORT_ERROR_NAMES = frozenset(
    {
        "SSLCertVerificationError",
        "SSLError",
        "SSLEOFError",
        "SSLZeroReturnError",
        "SSLSyscallError",
        "URLError",
        "HTTPError",
        "HTTPException",
        "RemoteDisconnected",
        "IncompleteRead",
        "BadStatusLine",
        "TimeoutError",
        "timeout",
        "gaierror",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "OSError",
    }
)

_warn_lock = threading.Lock()
_fallback_warned = False


def _platform_context() -> ssl.SSLContext | None:
    """truststore 的上下文；import 不了回 None（调用方退回并记 WARNING）。"""
    try:
        import truststore
    except ImportError as exc:
        _warn_fallback(exc)
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _warn_fallback(exc: BaseException) -> None:
    global _fallback_warned
    with _warn_lock:
        if _fallback_warned:
            return
        _fallback_warned = True
    LOG.warning(
        "truststore 不可用，HTTPS 退回 OpenSSL 默认信任库（Windows 上不会按需补装缺失的根证书）: %s",
        exc,
    )


def client_context() -> ssl.SSLContext:
    """出站 HTTPS 用的上下文：平台原生校验；truststore 不可用时退回标准库默认（记 WARNING）。

    每次现建：代价是微秒级，而上下文里没有要跨请求保留的状态。用例经 monkeypatch 本函数注入
    带测试根的上下文（不依赖真实的系统证书库）。"""
    ctx = _platform_context()
    if ctx is None:
        ctx = ssl.create_default_context()
    return ctx


def source_of(ctx: ssl.SSLContext) -> str:
    """这个上下文的信任来源（`SOURCES` 之一）：truststore 的是平台，其余都是 OpenSSL 的信任库。"""
    module = type(ctx).__module__ or ""
    return SOURCE_PLATFORM if module.split(".")[0] == "truststore" else SOURCE_OPENSSL


def https_handler(ctx: ssl.SSLContext) -> urllib.request.HTTPSHandler:
    """给 `urllib.request.build_opener` 的 HTTPS 处理器：用 `ctx`，别的一概默认（代理仍由
    `build_opener` 按下载那一刻的环境变量现建）。"""
    return urllib.request.HTTPSHandler(context=ctx)


def root_cause(exc: BaseException) -> BaseException:
    """传输层异常的根：`URLError.reason`（是异常时）→ `__cause__` → `__context__`，取最深的那个。"""
    seen: set[int] = set()
    cur: BaseException = exc
    while id(cur) not in seen:
        seen.add(id(cur))
        reason = getattr(cur, "reason", None)
        if isinstance(reason, BaseException):
            cur = reason
            continue
        nxt = cur.__cause__ or cur.__context__
        if nxt is None:
            break
        cur = nxt
    return cur


def cert_verification_error(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """`exc` 的因果链上有没有**证书校验失败**（缺根 / 过期 / 主机名不符 / 自签）；有就回它。

    只认 `ssl.SSLCertVerificationError`：握手协议层的别的 `SSLError`（被中间设备截断、协议不匹配）
    不是「证书不被信任」，归不到这里。truststore 在三个平台上报的也是这个类。"""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return cur
        reason = getattr(cur, "reason", None)
        for nxt in (reason, cur.__cause__, cur.__context__):
            if isinstance(nxt, BaseException):
                stack.append(nxt)
    return None


def reset_for_tests() -> None:
    global _fallback_warned
    with _warn_lock:
        _fallback_warned = False
