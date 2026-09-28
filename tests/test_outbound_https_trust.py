"""遥测投递与检查更新的出站 HTTPS：平台原生证书校验（`tlstrust`）、证书失败在日志里分得出来。

2026-09-28 实测（阿里云 Windows Server 2025，冻结安装包 0.17.0 自带的 Python 3.13.15）：
`telemetry.tavotto.com` 的链根是 ISRG Root X1，`api.github.com` 的链根是 USERTrust ECC；干净机器的证书库起初
都没有。进程内交替 A/B（从 `ssl.enum_certificates` 建上下文，剔除对应根 vs 不剔除，各 3 次交替）：剔除侧 6/6
`CERTIFICATE_VERIFY_FAILED`，不剔除侧 6/6 成功——即 `telemetry._post` 与 `updater._fetch_latest_release`
原来的模块级 `urlopen`（OpenSSL + 已装的根）在干净 Windows 上会失败：遥测按设计静默全丢（与 #439 同形状），
检查更新失败，而 app.log 里一个字都没有。

判据的主语（与 `tests/test_private_python_tls.py` 同一套装置）：

* 「缺根」= 本地 HTTPS 回环服务用**每会话现造的私有 CA** 签的证书——任何一台机器的信任库里都没有它；走的是
  **产品默认**的上下文工厂（`tlstrust.client_context`，不注入），量到的是产品真实的那条路；
* 「正常根」= 经 `tlstrust.client_context` 这一个注入点换成带测试根的上下文——与「缺根」只差注入，证明
  opener 用的就是 `tlstrust` 给的那一个（换回模块级 `urlopen` 这几条就红）；
* 「客户端确实来过」= 服务端在握手之前数的 TCP 连接数；
* 日志：本机那份（`getMessage()`）带消息原文；诊断包那份（`ExportLogFormatter`）只留闭集明文的异常类型与
  信任来源，消息哈希（REL-05）。
"""

from __future__ import annotations

import ast
import json
import logging
import socket
import ssl
from pathlib import Path

import pytest

from support.private_python import LoopbackServer
from support.tls_certs import make_pki
from tavotto.engine import brand, diagnostics, telemetry, tlstrust, updater

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """死代理（回环经 NO_PROXY 放行）、摘掉 OpenSSL 证书路径变量、遥测允许投递（只投到回环）。"""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    # `_post` 发出前会再判一次硬开关；这里只投回环 endpoint（下面每条都设 TAVOTTO_TELEMETRY_ENDPOINT）
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    tlstrust.reset_for_tests()
    yield
    telemetry.reset_for_tests()
    tlstrust.reset_for_tests()


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory) -> dict[str, Path]:
    return make_pki().write(tmp_path_factory.mktemp("pki"))


@pytest.fixture
def server_tls(pki_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    return ctx


def _platform_ctx_with(ca: Path) -> ssl.SSLContext:
    import truststore

    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=str(ca))
    return ctx


def _openssl_ctx_with(ca: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(ca))


def _inject(monkeypatch, factory, ca: Path) -> list[str]:
    """把 `tlstrust.client_context` 换成带测试根的工厂；回一个记录「用了哪种信任来源」的列表。"""
    seen: list[str] = []

    def _factory():
        ctx = factory(ca)
        seen.append(tlstrust.source_of(ctx))
        return ctx

    monkeypatch.setattr(tlstrust, "client_context", _factory)
    return seen


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _records(caplog, logger: str, level: int | None = None) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger and (level is None or r.levelno == level)]


def _assert_tls_record(rec: logging.LogRecord) -> None:
    """一条证书失败记录：本机版带类型 / 信任来源 / 消息原文；出门版带类型与来源、不带消息。"""
    local = rec.getMessage()
    assert "证书校验失败" in local, local
    assert "SSLCertVerificationError" in local, local
    assert f"信任来源 {tlstrust.SOURCE_PLATFORM}" in local, local
    message = str(rec.args[-1])
    assert message and message in local, "本机日志要带底层消息原文"
    exported = diagnostics.ExportLogFormatter().format(rec)
    assert "SSLCertVerificationError" in exported, exported
    assert tlstrust.SOURCE_PLATFORM in exported, exported
    assert message not in exported, "诊断包那份不许带自由文本的消息"


_PAYLOAD = {"event": "app_started", "properties": {"app_mode": "browser"}}


# ================================================================ 遥测
class TestTelemetry:
    def test_missing_root_is_logged_once_as_tls_and_never_raises(
        self, tmp_path, server_tls, monkeypatch, caplog
    ):
        """干净 Windows 的形状：链根不在信任库里。投递照旧不抛、不打扰用户，但 app.log 里有**一条** WARNING
        说是证书校验失败（每进程一次：一天几十条事件不许刷屏）。"""
        caplog.set_level(logging.INFO, logger="tavotto")
        with LoopbackServer(tmp_path, tls=server_tls) as server:
            monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", server.url("v1/events"))
            telemetry._post(_PAYLOAD)
            telemetry._post(_PAYLOAD)
            assert server.connections >= 2, "判据的前提：客户端确实来过（两次）"
            assert server.posts == [], "握手就被拒：一个字节的事件都不该送到"
        records = _records(caplog, "tavotto.telemetry")
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].levelno == logging.WARNING
        _assert_tls_record(records[0])

    def test_hostname_mismatch_is_tls_even_with_a_trusted_root(
        self, tmp_path, server_tls, pki_files, monkeypatch, caplog
    ):
        """根可信、主机名不符（证书只签了 IP 127.0.0.1，URL 写 localhost）→ 照样拒、照样记证书失败。"""
        caplog.set_level(logging.INFO, logger="tavotto")
        _inject(monkeypatch, _platform_ctx_with, pki_files["ca"])
        with LoopbackServer(tmp_path, tls=server_tls, host="localhost") as server:
            monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", server.url("v1/events"))
            telemetry._post(_PAYLOAD)
            assert server.connections >= 1 and server.posts == []
        records = _records(caplog, "tavotto.telemetry", logging.WARNING)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert "SSLCertVerificationError" in records[0].getMessage()

    @pytest.mark.parametrize(
        ("factory", "source"),
        [
            (_platform_ctx_with, tlstrust.SOURCE_PLATFORM),
            (_openssl_ctx_with, tlstrust.SOURCE_OPENSSL),
        ],
        ids=["platform", "openssl-fallback"],
    )
    def test_trusted_root_delivers_the_event(
        self, tmp_path, server_tls, pki_files, monkeypatch, caplog, factory, source
    ):
        """注入带测试根的上下文 → 事件真的送到（请求体就是那条 payload），一条日志都没有。"""
        caplog.set_level(logging.INFO, logger="tavotto")
        seen = _inject(monkeypatch, factory, pki_files["ca"])
        with LoopbackServer(tmp_path, tls=server_tls) as server:
            monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", server.url("v1/events"))
            telemetry._post(_PAYLOAD)
            assert [path for path, _ in server.posts] == ["/v1/events"]
            assert json.loads(server.posts[0][1]) == _PAYLOAD
        assert seen == [source]
        assert _records(caplog, "tavotto.telemetry") == []

    def test_offline_and_non_certificate_tls_errors_stay_silent(
        self, tmp_path, monkeypatch, caplog
    ):
        """断网 / 协议层 SSLError 仍是「常态」：一个字都不写（设计不变），日志里只有证书失败那一种。"""
        caplog.set_level(logging.INFO, logger="tavotto")
        monkeypatch.setenv(
            "TAVOTTO_TELEMETRY_ENDPOINT", f"https://127.0.0.1:{_closed_port()}/v1/events"
        )
        telemetry._post(_PAYLOAD)
        with LoopbackServer(tmp_path) as server:  # 说明文 HTTP，客户端按 HTTPS 握手
            url = server.url("v1/events").replace("http://", "https://", 1)
            monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", url)
            telemetry._post(_PAYLOAD)
            assert server.connections >= 1, "判据的前提：客户端确实来过"
        assert _records(caplog, "tavotto.telemetry") == []


# ================================================================ 检查更新
def _serve_release(directory: Path, tag: str = "v99.0.0") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    name = "releases-latest.json"
    (directory / name).write_text(
        json.dumps({"tag_name": tag, "body": "notes", "assets": []}), encoding="utf-8"
    )
    return name


class TestUpdater:
    def test_missing_root_is_a_tls_warning_and_the_check_reports_failure(
        self, tmp_path, server_tls, monkeypatch, caplog
    ):
        caplog.set_level(logging.INFO, logger="tavotto")
        name = _serve_release(tmp_path)
        with LoopbackServer(tmp_path, tls=server_tls) as server:
            monkeypatch.setattr(brand, "RELEASES_API", server.url(name))
            out = updater.check(force=True)
            assert server.connections >= 1, "判据的前提：客户端确实来过"
            assert server.requests == [], "握手就被拒：一个 HTTP 请求都不该发出去"
        assert out["code"] == "update_check_failed" and out["update_available"] is False, out
        assert out["params"]["error"], (
            out
        )  # 原文随平台而变（macOS SecTrust 与 OpenSSL 的措辞不同），只要求有
        records = _records(caplog, "tavotto.updater")
        assert [r.levelno for r in records] == [logging.WARNING], [r.getMessage() for r in records]
        _assert_tls_record(records[0])

    def test_offline_is_an_info_line_not_a_certificate_warning(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="tavotto")
        monkeypatch.setattr(brand, "RELEASES_API", f"https://127.0.0.1:{_closed_port()}/latest")
        out = updater.check(force=True)
        assert out["code"] == "update_check_failed", out
        records = _records(caplog, "tavotto.updater")
        assert [r.levelno for r in records] == [logging.INFO], [r.getMessage() for r in records]
        local = records[0].getMessage()
        assert "ConnectionRefusedError" in local and "证书" not in local, local

    @pytest.mark.parametrize(
        ("factory", "source"),
        [
            (_platform_ctx_with, tlstrust.SOURCE_PLATFORM),
            (_openssl_ctx_with, tlstrust.SOURCE_OPENSSL),
        ],
        ids=["platform", "openssl-fallback"],
    )
    def test_trusted_root_reads_the_release(
        self, tmp_path, server_tls, pki_files, monkeypatch, caplog, factory, source
    ):
        caplog.set_level(logging.INFO, logger="tavotto")
        seen = _inject(monkeypatch, factory, pki_files["ca"])
        name = _serve_release(tmp_path)
        with LoopbackServer(tmp_path, tls=server_tls) as server:
            monkeypatch.setattr(brand, "RELEASES_API", server.url(name))
            out = updater.check(force=True)
            assert server.requests == [f"/{name}"]
        assert seen == [source]
        assert "error" not in out and out["latest"] == "99.0.0", out
        assert out["update_available"] is True
        assert _records(caplog, "tavotto.updater") == []


# ================================================================ 结构：出站 HTTPS 只经 tlstrust
#: 产品里的出站 HTTPS（非回环）调用点：模块 → 建 opener 的那个函数。与 `tlstrust` 模块头的清单、
#: PR 里的枚举表是同一份事实——多一处少一处这里都红，逼着清单跟着改。
OUTBOUND = {
    "engine/privatepython.py": "_fetch",
    "engine/telemetry.py": "_post",
    "engine/updater.py": "_fetch_latest_release",
}
#: 只有 `tlstrust` 许建 TLS 上下文 / HTTPS 处理器。
_TLS_BUILDERS = {
    "create_default_context",
    "SSLContext",
    "_create_unverified_context",
    "HTTPSHandler",
}
#: 绕开 urllib opener 的传输：一个都不许有（有了 conftest 的遥测探针与本文件的判据都看不见它）。
#: `http.client` 的 import 本身不禁（privatepython 要它的异常类型），禁的是直接建连接。
_BANNED_MODULES = {"requests", "httpx", "urllib3", "aiohttp", "truststore.inject"}
_BANNED_CALLS = {"HTTPSConnection", "HTTPConnection", "urlretrieve", "inject_into_ssl"}


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _calls_with_owner(tree: ast.AST):
    """(调用节点, 所在的最内层函数名) —— 模块级调用的 owner 是 `<module>`。"""

    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from walk(child, child.name)
            else:
                if isinstance(child, ast.Call):
                    yield child, owner
                yield from walk(child, owner)

    yield from walk(tree, "<module>")


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _is_tlstrust_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "tlstrust"
    )


def _opener_shape_ok(call: ast.Call, fn: ast.AST) -> bool:
    """`build_opener(tlstrust.https_handler(ctx))`，且 `ctx` 在同一个函数里取自 `tlstrust.client_context()`
    （或直接内联那一次调用）。"""
    if len(call.args) != 1 or call.keywords or not _is_tlstrust_call(call.args[0], "https_handler"):
        return False
    handler = call.args[0]
    if len(handler.args) != 1:
        return False
    arg = handler.args[0]
    if _is_tlstrust_call(arg, "client_context"):
        return True
    if not isinstance(arg, ast.Name):
        return False
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)
        and _is_tlstrust_call(n.value, "client_context")
        for n in ast.walk(fn)
    )


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _outbound_violations(rel: str, tree: ast.AST) -> tuple[list[str], dict[str, str]]:
    """一个模块里违反「出站 HTTPS 只经 tlstrust」的地方，以及它建 opener 的函数（模块 → 函数）。"""
    bad: list[str] = []
    openers: dict[str, str] = {}
    fns = _functions(tree)
    for call, owner in _calls_with_owner(tree):
        name = _callee(call)
        where = f"{rel}:{call.lineno}"
        if name == "build_opener":
            if not _opener_shape_ok(call, fns.get(owner, tree)):
                bad.append(
                    f"{where} build_opener 没用 tlstrust.https_handler(tlstrust.client_context())"
                )
            openers[rel] = owner
        elif name == "urlopen":
            # 模块级 `urlopen` 只许打本机回环：第一个参数是 `http://127.0.0.1:` 开头的 f-string，或
            # 一个变量——变量的情形由下面「这个模块里所有带 scheme 的字面量都是回环」兜着
            first = call.args[0] if call.args else None
            if isinstance(first, ast.JoinedStr):
                head = first.values[0] if first.values else None
                if not (
                    isinstance(head, ast.Constant)
                    and str(head.value).startswith("http://127.0.0.1:")
                ):
                    bad.append(f"{where} urlopen 打的不是本机回环")
            elif not isinstance(first, ast.Name):
                bad.append(f"{where} urlopen 的地址既不是回环 f-string 也不是变量")
            else:
                for lit in _scheme_literals(tree):
                    if not lit.startswith("http://127.0.0.1:"):
                        bad.append(f"{where} urlopen 所在模块里有非回环地址字面量 {lit!r}")
        elif name in _BANNED_CALLS:
            bad.append(f"{where} {name}（绕开 urllib opener 的传输）")
        elif name in _TLS_BUILDERS and rel != "engine/tlstrust.py":
            bad.append(f"{where} 在 tlstrust 之外建 TLS 上下文 / 处理器（{name}）")
    for n in ast.walk(tree):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and not n.level:
            mods = [n.module or ""]
        for m in mods:
            if m in _BANNED_MODULES or m.split(".")[0] in _BANNED_MODULES:
                bad.append(f"{rel}:{n.lineno} import {m}（绕开 urllib opener 的传输）")
    return bad, openers


def _scheme_literals(tree: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "://" in n.value:
            if n.value.split("://", 1)[0] in ("http", "https"):
                out.append(n.value)
    return out


class TestStructure:
    def test_every_outbound_https_goes_through_tlstrust(self):
        """判据的主语：`src/tavotto` 里**每一个** `build_opener` / `urlopen` 调用点与 TLS 上下文构造。
        正面形式：opener 一律 `build_opener(tlstrust.https_handler(<tlstrust.client_context()>))`；模块级
        `urlopen` 只打 `http://127.0.0.1:` 回环；TLS 上下文只在 tlstrust 里建；没有绕开 urllib 的传输。
        盲点（写在明处）：`urlopen` 的地址是变量时，只查得到「这个模块里没有非回环的地址字面量」——
        地址从别的模块传进来的情形这里量不到（今天的三个回环模块都是自己拼地址）。"""
        offenders: list[str] = []
        openers: dict[str, str] = {}
        urlopen_modules: set[str] = set()
        for rel, tree in _modules():
            bad, found = _outbound_violations(rel, tree)
            offenders += bad
            openers.update(found)
            if any(_callee(c) == "urlopen" for c, _ in _calls_with_owner(tree)):
                urlopen_modules.add(rel)
        assert not offenders, "\n".join(offenders)
        assert openers == OUTBOUND, openers
        # 对照：三个回环模块真的被扫到了（扫描器不是空转）
        assert {"app.py", "engine/handoff.py", "engine/session_client.py"} <= urlopen_modules, (
            urlopen_modules
        )
        assert not urlopen_modules & set(OUTBOUND), "出站三处不许再留模块级 urlopen"

    @pytest.mark.parametrize(
        ("snippet", "expect"),
        [
            ("def f(req):\n    urllib.request.build_opener().open(req)\n", "build_opener 没用"),
            (
                "def f(req):\n    ctx = ssl.create_default_context()\n"
                "    urllib.request.build_opener(tlstrust.https_handler(ctx)).open(req)\n",
                "在 tlstrust 之外建",
            ),
            (
                "def f(req):\n    urllib.request.urlopen(req)\nU = 'https://api.github.com/x'\n",
                "非回环地址字面量",
            ),
            ("def f():\n    urllib.request.urlopen(f'https://{H}/x')\n", "不是本机回环"),
            ("def f():\n    http.client.HTTPSConnection('api.github.com')\n", "绕开 urllib opener"),
            ("import requests\n", "绕开 urllib opener"),
        ],
        ids=[
            "bare-opener",
            "own-context",
            "urlopen-external",
            "urlopen-fstring",
            "http-client",
            "requests",
        ],
    )
    def test_the_predicate_catches_each_bypass(self, snippet, expect):
        """反证落点：六种绕开 tlstrust 的写法各自被认出来。"""
        bad, _ = _outbound_violations("engine/x.py", ast.parse(snippet))
        assert any(expect in b for b in bad), bad

    def test_the_predicate_accepts_the_product_shape(self):
        ok = (
            "def f(req):\n    ctx = tlstrust.client_context()\n"
            "    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))\n"
            "def g(port):\n    urllib.request.urlopen(f'http://127.0.0.1:{port}/api/version')\n"
        )
        bad, openers = _outbound_violations("engine/x.py", ast.parse(ok))
        assert bad == [] and openers == {"engine/x.py": "f"}

    def test_the_tlstrust_inventory_names_the_same_three_call_sites(self):
        """`tlstrust` 模块头的清单与 `OUTBOUND` 同一份事实：三处函数名都写在那里。"""
        doc = (
            ast.get_docstring(ast.parse((SRC / "engine" / "tlstrust.py").read_text("utf-8"))) or ""
        )
        for rel, fn in OUTBOUND.items():
            mod = Path(rel).stem
            assert f"{mod}.{fn}" in doc, f"tlstrust 模块头没写 {mod}.{fn}"
