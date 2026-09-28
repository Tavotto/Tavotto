"""私有 Python 下载的证书校验：平台原生信任（truststore）、证书失败单列 `private_python_tls`、失败进日志。

2026-09-28 实测（阿里云 Windows Server 2025 build 26100，冻结 NSIS 安装包）：归档地址 302 到
`release-assets.githubusercontent.com`，链根 ISRG Root X1；干净 Windows 的证书库起初没有它，
`ssl.create_default_context()` 只枚举已装的根、不触发 Windows 的按需补装 → 两次 `CERTIFICATE_VERIFY_FAILED`
（约 0.7 s 一次）→ 报 `private_python_offline`「检查网络后重试」，app.log 里一个字都没有。

判据的主语：

* 「缺根」= 本地 HTTPS 回环服务用**每会话现造的私有 CA**（`support.tls_certs`）签的证书——这台机器（任何一台
  CI runner）的信任库里都没有它，与干净 Windows 缺 ISRG Root X1 同一个形状；走的是**产品默认**的上下文工厂
  （`tlstrust.client_context`，不注入），所以量到的是产品真实的那条路；
* 「正常根」= 经 `tlstrust.client_context` 这一个注入点换成**带测试根**的上下文（truststore 的与标准库的各一份）
  ——不依赖、也不改动任何真实的系统证书库；
* 「客户端确实来过」= 服务端在握手之前数的 TCP 连接数（TLS 失败时 HTTP 请求日志为空，不能拿它当前提）。
"""

from __future__ import annotations

import ast
import logging
import os
import re
import socket
import ssl
import sys
from pathlib import Path

import pytest

from support import private_python as pp_support
from support.private_python import LoopbackServer, fake_archive, source_from
from support.tls_certs import make_pki
from tavotto.engine import diagnostics, privatepython, tlstrust

ROOT = Path(__file__).resolve().parent.parent
POSIX = os.name != "nt"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """与 `test_private_python.py` 同一套隔离：数据目录、逃生门、死代理（回环经 NO_PROXY 放行）。"""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    # 用户 / runner 设的 OpenSSL 证书路径会改变「标准库默认信任库」的内容：摘掉，判据只看代码
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    privatepython.reset_for_tests()
    tlstrust.reset_for_tests()
    yield
    privatepython.reset_for_tests()
    tlstrust.reset_for_tests()


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory) -> dict[str, Path]:
    return make_pki().write(tmp_path_factory.mktemp("pki"))


@pytest.fixture
def server_tls(pki_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    return ctx


@pytest.fixture
def launches(tmp_path) -> Path:
    return tmp_path / "launches.log"


def _launch_count(log: Path) -> int | None:
    if not POSIX:
        return None
    try:
        return len(log.read_text("utf-8").splitlines())
    except OSError:
        return 0


def _runtime_dirs() -> set[str]:
    try:
        return {p.name for p in privatepython.runtimes_dir().iterdir()}
    except OSError:
        return set()


def _parts() -> list[str]:
    try:
        return [p.name for p in privatepython.downloads_dir().glob("*.part")]
    except OSError:
        return []


def _source(url: str, archive: Path, sha: str, rel: str):
    return source_from(archive, sha, rel, url=url, version=pp_support.host_python_version())


def _archive(tmp_path, launches):
    return fake_archive(tmp_path / "serve", host_python=sys.executable, launches_log=launches)


def _platform_ctx_with(ca: Path) -> ssl.SSLContext:
    """truststore 的平台上下文 + 测试根（`load_verify_locations`：truststore 在三个平台上都支持追加锚点）。"""
    import truststore

    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=str(ca))
    return ctx


def _openssl_ctx_with(ca: Path) -> ssl.SSLContext:
    """退回路径的形状：标准库默认上下文，信任库只有测试根。"""
    return ssl.create_default_context(cafile=str(ca))


def _assert_nothing_published(src, launches) -> None:
    assert _launch_count(launches) in (0, None), "证书失败的路上解释器一次都不许起"
    assert _runtime_dirs() == set() and _parts() == []
    assert privatepython.python_of(src) is None
    assert privatepython.read_ledger()["runtimes"] == {}


def _failure_records(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "tavotto.privatepython"
        and r.levelno == logging.WARNING
        and "下载第" in str(r.msg)
    ]


# ================================================================ 产品默认的信任：缺根 → tls，不是 offline
class TestMissingRoot:
    def test_untrusted_root_is_reported_as_tls_not_offline(
        self, tmp_path, launches, server_tls, caplog
    ):
        """干净 Windows 的形状：服务端证书链的根不在这台机器的信任库里 → `private_python_tls`。
        走产品默认的上下文工厂（不注入）；有界重试照旧（两次都连到了服务），什么都不发布。"""
        caplog.set_level(logging.WARNING, logger="tavotto")
        archive, sha, rel = _archive(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve", tls=server_tls) as server:
            src = _source(server.url(archive.name), archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert server.connections >= privatepython.DOWNLOAD_ATTEMPTS, (
                "判据的前提：客户端确实来过"
            )
            assert server.requests == [], "握手就被拒：一个 HTTP 请求都不该发出去"
        assert err.value.code == privatepython.ERROR_TLS, err.value
        assert err.value.code != privatepython.ERROR_OFFLINE
        _assert_nothing_published(src, launches)

    def test_each_failed_attempt_logs_the_exception_type_and_message(
        self, tmp_path, launches, server_tls, caplog
    ):
        """2026-09-28 那台机器的 app.log 里什么都没有。现在每次失败一条 WARNING：根异常类型（闭集明文）、
        信任来源（闭集明文）、消息（本机日志原样）；诊断包那份（出门版）只留类型，消息哈希（REL-05）。"""
        caplog.set_level(logging.WARNING, logger="tavotto")
        archive, sha, rel = _archive(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve", tls=server_tls) as server:
            src = _source(server.url(archive.name), archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError):
                privatepython.provision(src)
        records = _failure_records(caplog)
        assert len(records) == privatepython.DOWNLOAD_ATTEMPTS, [r.getMessage() for r in records]
        for rec in records:
            local = rec.getMessage()
            assert "SSLCertVerificationError" in local, local
            assert f"信任来源 {tlstrust.SOURCE_PLATFORM}" in local, local
            message = str(rec.args[-1])
            assert message and message in local, "本机日志要带底层消息原文"
            exported = diagnostics.ExportLogFormatter().format(rec)
            assert "SSLCertVerificationError" in exported, exported
            assert tlstrust.SOURCE_PLATFORM in exported, exported
            assert message not in exported, "出门版不许带自由文本的消息"

    def test_hostname_mismatch_is_tls_even_with_a_trusted_root(
        self, tmp_path, launches, server_tls, pki_files, monkeypatch
    ):
        """根可信、主机名不符（证书只签了 IP 127.0.0.1，URL 写 localhost）→ 照样 `private_python_tls`：
        主机名校验没被关掉。"""
        monkeypatch.setattr(tlstrust, "client_context", lambda: _platform_ctx_with(pki_files["ca"]))
        archive, sha, rel = _archive(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve", tls=server_tls, host="localhost") as server:
            src = _source(server.url(archive.name), archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert server.connections >= 1
        assert err.value.code == privatepython.ERROR_TLS, err.value
        _assert_nothing_published(src, launches)


# ================================================================ 正常根：两种上下文都能下
class TestTrustedRoot:
    @pytest.mark.parametrize(
        ("factory", "source"),
        [
            (_platform_ctx_with, tlstrust.SOURCE_PLATFORM),
            (_openssl_ctx_with, tlstrust.SOURCE_OPENSSL),
        ],
        ids=["platform", "openssl-fallback"],
    )
    def test_download_succeeds_when_the_root_is_trusted(
        self, tmp_path, launches, server_tls, pki_files, monkeypatch, factory, source
    ):
        """经注入点给上下文加上测试根 → 走完整条链（下载 → 校验 → 解包 → 真起 → 改名 → 记账）。
        与上面「缺根」那条只差注入的上下文：证明 opener 用的就是 `tlstrust.client_context()` 给的那一个。"""
        seen: list[str] = []

        def _factory():
            ctx = factory(pki_files["ca"])
            seen.append(tlstrust.source_of(ctx))
            return ctx

        monkeypatch.setattr(tlstrust, "client_context", _factory)
        archive, sha, rel = _archive(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve", tls=server_tls) as server:
            src = _source(server.url(archive.name), archive, sha, rel)
            python = privatepython.provision(src)
            assert server.requests == [f"/{archive.name}"]
        assert seen == [source]
        assert Path(python).is_file() and privatepython.python_of(src) == python
        assert _launch_count(launches) in (1, None)
        assert src.id in privatepython.read_ledger()["runtimes"]


# ================================================================ 别的传输失败仍是 offline（tls 只收证书失败）
class TestNotEverythingIsTls:
    def test_https_to_a_closed_port_is_offline(self, tmp_path, launches):
        archive, sha, rel = _archive(tmp_path, launches)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        src = _source(f"https://127.0.0.1:{port}/{archive.name}", archive, sha, rel)
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src)
        assert err.value.code == privatepython.ERROR_OFFLINE, err.value
        _assert_nothing_published(src, launches)

    def test_a_non_certificate_ssl_error_is_offline(self, tmp_path, launches):
        """服务端说明文 HTTP、客户端按 HTTPS 握手：`SSLError`（协议层）但不是证书校验失败 → offline。
        `private_python_tls` 的文案说「证书」，不许把别的 TLS 故障也揽进来。"""
        archive, sha, rel = _archive(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:  # 不带 tls
            url = server.url(archive.name).replace("http://", "https://", 1)
            src = _source(url, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert server.connections >= 1
        assert err.value.code == privatepython.ERROR_OFFLINE, err.value


# ================================================================ 信任来源本身
class TestTrustSource:
    def test_default_context_is_the_platform_trust_store_and_verifies(self):
        import truststore

        ctx = tlstrust.client_context()
        assert isinstance(ctx, truststore.SSLContext)
        assert tlstrust.source_of(ctx) == tlstrust.SOURCE_PLATFORM
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True

    def test_missing_truststore_falls_back_to_openssl_loudly(self, monkeypatch, caplog):
        """truststore import 不了（包坏了 / 冻结产物漏收）→ 标准库默认上下文（校验照样开）+ 一条 WARNING；
        进程内只记一次，不刷屏。"""
        caplog.set_level(logging.WARNING, logger="tavotto")
        monkeypatch.setitem(sys.modules, "truststore", None)  # `import truststore` → ImportError
        first = tlstrust.client_context()
        second = tlstrust.client_context()
        for ctx in (first, second):
            assert type(ctx) is ssl.SSLContext
            assert tlstrust.source_of(ctx) == tlstrust.SOURCE_OPENSSL
            assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
        warnings = [
            r
            for r in caplog.records
            if r.name == "tavotto.tlstrust" and r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "truststore" in warnings[0].getMessage()

    def test_cert_verification_error_is_found_through_urllib_wrapping(self):
        import urllib.error

        inner = ssl.SSLCertVerificationError("unable to get local issuer certificate")
        wrapped = urllib.error.URLError(inner)
        assert tlstrust.cert_verification_error(wrapped) is inner
        assert tlstrust.root_cause(wrapped) is inner
        assert (
            tlstrust.cert_verification_error(urllib.error.URLError(ConnectionRefusedError()))
            is None
        )
        assert tlstrust.cert_verification_error(ssl.SSLError("wrong version number")) is None


# ================================================================ 结构：不降级、只有一处 import、打包收得进
def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestStructure:
    def test_tlstrust_never_weakens_verification(self):
        """AST：没有 CERT_NONE / CERT_OPTIONAL、没有 `_create_unverified_context`、没有给 `check_hostname` /
        `verify_mode` 赋值——上下文只按 `PROTOCOL_TLS_CLIENT` 的默认（CERT_REQUIRED + 主机名）用。"""
        tree = ast.parse(_src("src/tavotto/engine/tlstrust.py"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        }
        assert not names & {"CERT_NONE", "CERT_OPTIONAL", "_create_unverified_context"}, names
        assigned = {
            t.attr
            for n in ast.walk(tree)
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            if isinstance(t, ast.Attribute)
        }
        assert not assigned & {"check_hostname", "verify_mode"}, assigned
        # 对照：判据认得出降级的写法
        bad = ast.parse("ctx.check_hostname = False\nctx.verify_mode = ssl.CERT_NONE\n")
        bad_assigned = {
            t.attr for n in ast.walk(bad) if isinstance(n, ast.Assign) for t in n.targets
        }
        assert bad_assigned == {"check_hostname", "verify_mode"}

    def test_truststore_is_imported_only_in_tlstrust(self):
        offenders = []
        for path in sorted((ROOT / "src" / "tavotto").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                mods = []
                if isinstance(n, ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and not n.level:
                    mods = [n.module or ""]
                if any(m.split(".")[0] == "truststore" for m in mods):
                    offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == ["src/tavotto/engine/tlstrust.py"], offenders

    def test_privatepython_builds_its_opener_with_the_tlstrust_handler(self):
        """opener 只有一处，参数是 `tlstrust.https_handler(...)`——换回无参 `build_opener()` 就回到 OpenSSL
        的根证书快照（2026-09-28 的缺陷形状）。"""
        tree = ast.parse(_src("src/tavotto/engine/privatepython.py"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "build_opener"
        ]
        assert len(calls) == 1, "opener 只许在 _fetch 里建一次"
        args = calls[0].args
        assert len(args) == 1 and isinstance(args[0], ast.Call), ast.dump(calls[0])
        func = args[0].func
        assert isinstance(func, ast.Attribute) and func.attr == "https_handler"
        assert isinstance(func.value, ast.Name) and func.value.id == "tlstrust"

    def test_truststore_is_a_runtime_dependency_and_the_spec_freezes_it(self):
        """wheel：pyproject 的运行时依赖里有它（requirements.txt 的镜像由 test_rendercore_fonts 看护）；
        冻结产物：延后 import 静态分析看不见，spec 必须把整包子模块点名进 hiddenimports，打包机上没装就拒绝打包。"""
        # 不用 tomllib（3.10 上没有，而 3.10 是支持档）：按块切出 `[project]` 的 dependencies 数组
        block = re.search(r"^dependencies = \[(.*?)^\]", _src("pyproject.toml"), re.M | re.S)
        assert block, "pyproject 里读不出 dependencies"
        deps = re.findall(r'^\s*"([^"]+)"', block.group(1), re.M)
        assert any(re.match(r"^truststore\b", d) for d in deps), deps
        spec = _src("packaging/tavotto.spec")
        assert 'TRUSTSTORE_MODULES = collect_submodules("truststore")' in spec
        assert "*TRUSTSTORE_MODULES," in spec
        assert re.search(
            r'if "truststore" not in TRUSTSTORE_MODULES:\s*\n\s*raise SystemExit', spec
        )
