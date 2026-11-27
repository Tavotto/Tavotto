"""高级版闭源组件的下载 / 验签 / 安装 / 切换 / 回退（ADR 0198 §五–§七、§十二）。

用开源示例扩展 `tests/fixtures/pro_echo` 打 zip；**真的本地 `http.server`** 当清单服务与下载主机（计数每一次请求）；
PyNaCl 现生成测试发布密钥与测试授权密钥。CI 不需要闭源代码。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tavotto.engine import diagsend, license as lic, prohost, proinstall

nacl_signing = pytest.importorskip("nacl.signing")

ECHO = Path(__file__).parent / "fixtures" / "pro_echo"
PLAT = "macos-arm64"
LID = "lic_abcdefghijklmn"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# 假服务端（真 HTTP）
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):  # 静音
        pass

    def _json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        srv = self.server.state
        with srv.lock:
            srv.posts.append((self.path, body))
        if self.path == "/v1/activate":
            return self._json(200, {"token": srv.mint(body["device_pub"])})
        if self.path == "/v1/pro/manifest":
            nacl_signing.VerifyKey(base64.b64decode(body["device_pub"])).verify(
                lic.signed_bytes(body), base64.b64decode(body["sig"])
            )
            assert body["op"] == "pro_manifest"
            return self._json(200, srv.manifest(body))
        return self._json(404, {"error": {"code": "nope"}})

    def do_GET(self):  # noqa: N802
        srv = self.server.state
        with srv.lock:
            srv.gets.append(self.path)
        if self.path.startswith("/pro/redirect"):
            self.send_response(302)
            self.send_header("Location", "/pro/zip")
            self.end_headers()
            return
        if self.path.startswith(
            "/pro/endless"
        ):  # 不带 Content-Length 的超大流：测「一超过 size 立刻中止」
            self.send_response(200)
            self.end_headers()
            chunk = b"x" * 65536
            try:
                for _ in range(1024):  # 64 MiB
                    self.wfile.write(chunk)
                    with srv.lock:
                        srv.streamed += len(chunk)
            except OSError:
                pass
            return
        if self.path.startswith("/pro/slowdrip"):  # 慢速滴灌：每 0.05 秒 1 字节，持续很久
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(600):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
            return
        data = srv.files.get(self.path.split("?")[0])
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class State:
    def __init__(self, lic_key):
        self.lock = threading.Lock()
        self.posts: list = []
        self.gets: list = []
        self.files: dict[str, bytes] = {}
        self.streamed = 0
        self.lic_key = lic_key
        self.manifest_reply: dict = {"state": "none"}

    def mint(self, device_pub_b64: str) -> dict:
        return mint_token(self.lic_key, device_pub_b64)

    def manifest(self, body) -> dict:
        return self.manifest_reply

    def manifest_calls(self) -> int:
        return sum(1 for p, _ in self.posts if p == "/v1/pro/manifest")


def mint_token(key, device_pub_b64: str, **over) -> dict:
    iat = int(time.time())
    payload = {
        "v": 1,
        "lid": LID,
        "features": ["quick-layout"],
        "exp": iat + 30 * 86400,
        "iat": iat,
        "device": hashlib.sha256(base64.b64decode(device_pub_b64)).hexdigest(),
        "refresh_after": iat + 86400,
        "offline_until": iat + 14 * 86400,
        "email_hint": "m***@gmail.com",
        **over,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return {"payload": _b64(raw), "sig": _b64(key.sign(raw).signature), "kid": "test-1"}


@pytest.fixture
def srv():
    state = State(nacl_signing.SigningKey.generate())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.state = state
    state.port = httpd.server_address[1]
    state.base = f"http://127.0.0.1:{state.port}"
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield state
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def rel_key():
    return nacl_signing.SigningKey.generate()


@pytest.fixture
def env(monkeypatch, tmp_path, srv, rel_key):
    """开发 / 验收模式：本地端点 + 测试授权公钥 + 测试发布公钥 + 数据目录；平台钉成 macos-arm64。"""
    monkeypatch.delenv(lic.ENV_HARD_OFF, raising=False)  # conftest 全局关着授权
    monkeypatch.delenv(prohost.ENV_PRO_DIR, raising=False)
    monkeypatch.delenv("PRO_ECHO_API", raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_DIAG_DEV_LOOPBACK", "1")
    monkeypatch.setenv(lic.ENV_ENDPOINT, srv.base)
    monkeypatch.setenv(lic.ENV_TEST_PUBKEY, _b64(bytes(srv.lic_key.verify_key)))
    monkeypatch.setenv(proinstall.ENV_TEST_RELEASE_PUBKEY, _b64(bytes(rel_key.verify_key)))
    monkeypatch.setattr(proinstall, "platform_key", lambda: PLAT)
    monkeypatch.setattr(prohost, "_crashes", 0)
    monkeypatch.setattr(lic, "_AFTER_LICENSE", [])
    lic.reset_for_tests()
    prohost.shutdown()
    yield
    prohost.shutdown()
    lic.reset_for_tests()


def license_token(srv, **over):
    """给本机设备签一枚有效令牌并落盘（`license.has` 为真）。"""
    key = lic._signing_key(create=True)
    lic._save(mint_token(srv.lic_key, _b64(bytes(key.verify_key)), **over))


# ---------------------------------------------------------------------------
# 发布物构造
# ---------------------------------------------------------------------------
CRASH_MAIN = "import sys\nsys.exit(3)\n"


def release_payload(version="1.0.0", *, zip_bytes=b"", features=("quick-layout",), **over) -> dict:
    return {
        "v": 1,
        "pro_version": version,
        "platform": PLAT,
        "pro_api": 1,
        "features": list(features),
        "sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "size": len(zip_bytes),
        "released_at": 1_700_000_000,
        **over,
    }


def sign_release(key, payload: dict, kid="pro-test") -> dict:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return {"payload": _b64(raw), "sig": _b64(key.sign(raw).signature), "kid": kid}


def make_zip(
    version="1.0.0",
    *,
    main_py: str | None = None,
    features=("quick-layout",),
    inner_override: dict | None = None,
    skip_release=False,
    skip_pkg=False,
    extra: dict | None = None,
    symlink: str | None = None,
) -> bytes:
    """zip = `tavotto-pro/tavotto_pro/*`（开源示例扩展，或自定义 `__main__`）+ `release.json`。"""
    main = (
        main_py
        if main_py is not None
        else (ECHO / "tavotto_pro" / "__main__.py").read_text(encoding="utf-8")
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if not skip_pkg:
            z.writestr("tavotto-pro/tavotto_pro/__init__.py", "")
            z.writestr("tavotto-pro/tavotto_pro/__main__.py", main)
        if not skip_release:
            inner = release_payload(version, features=features)
            inner.pop("sha256"), inner.pop("size")  # zip 自己的摘要与大小写不进 zip
            inner.update(inner_override or {})
            z.writestr("release.json", json.dumps(inner))
        for name, data in (extra or {}).items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = (
                name  # 绕过 Windows 上 ZipInfo 把 `\\` 换成 `/` 的规范化，条目名原样写进 zip
            )
            z.writestr(info, data)
        if symlink:
            info = zipfile.ZipInfo("tavotto-pro/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, symlink)
    return buf.getvalue()


def release_for(rel_key, zip_bytes: bytes, version="1.0.0", **over):
    return sign_release(rel_key, release_payload(version, zip_bytes=zip_bytes, **over))


def stage(srv, rel_key, version="1.0.0", *, main_py=None, features=("quick-layout",), **zkw):
    """做一个版本：zip 放进下载主机，返回 `(zip 字节, 信封, 下载地址)`。"""
    z = make_zip(version, main_py=main_py, features=features, **zkw)
    env_ = release_for(rel_key, z, version, features=list(features))
    path = f"/pro/{version}.zip"
    srv.files[path] = z
    return z, env_, srv.base + path


def install_version(srv, rel_key, version="1.0.0", **kw):
    z, envl, url = stage(srv, rel_key, version, **kw)
    tmp = proinstall.download(url, len(z), hashlib.sha256(z).hexdigest())
    try:
        return proinstall.install(tmp, envl)
    finally:
        tmp.unlink(missing_ok=True)


def names_in_pro_dir():
    return (
        sorted(p.name for p in proinstall.pro_dir().iterdir())
        if proinstall.pro_dir().exists()
        else []
    )


def state_snapshot():
    p = proinstall.pro_dir() / "current.json"
    return p.read_bytes() if p.exists() else None


# ---------------------------------------------------------------------------
# 平台键
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("system", "machine", "want"),
    [
        ("Darwin", "arm64", "macos-arm64"),
        ("Darwin", "x86_64", "macos-x86_64"),
        ("Windows", "AMD64", "windows-x64"),
        ("Windows", "ARM64", None),
        ("Linux", "x86_64", None),
        ("Darwin", "ppc", None),
    ],
)
def test_platform_key(monkeypatch, system, machine, want):
    monkeypatch.setattr(proinstall, "_system", lambda: system)
    monkeypatch.setattr(proinstall, "_machine", lambda: machine)
    assert proinstall.platform_key() == want


def test_production_release_keys_are_well_formed_and_never_the_test_kid():
    # 正式发布公钥（ADR 0198 §五）：kid 都在 `pro-` 命名空间、每把都是 32 字节；测试 kid 永不进正式表
    assert proinstall.RELEASE_KEYS, "正式版要内置发布公钥"
    assert proinstall.TEST_KID not in proinstall.RELEASE_KEYS
    for kid, pub in proinstall.RELEASE_KEYS.items():
        assert kid.startswith(proinstall.KID_PREFIX)
        assert len(base64.b64decode(pub, validate=True)) == 32


# ---------------------------------------------------------------------------
# 清单验签（ADR §十二：篡改 payload / 换 kid / 字段）
# ---------------------------------------------------------------------------
def test_verify_release_ok(env, rel_key):
    payload = proinstall.verify_release(sign_release(rel_key, release_payload(zip_bytes=b"abc")))
    assert payload["pro_version"] == "1.0.0"


def test_tampered_payload_rejected(env, rel_key):
    good = sign_release(rel_key, release_payload(zip_bytes=b"abc"))
    forged = json.loads(base64.b64decode(good["payload"]))
    forged["sha256"] = "0" * 64  # 换成别的摘要，但沿用原签名
    bad = {**good, "payload": _b64(json.dumps(forged, separators=(",", ":")).encode())}
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(bad)
    assert exc.value.code == "bad_signature"


def test_signature_by_other_key_rejected(env):
    other = nacl_signing.SigningKey.generate()
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(sign_release(other, release_payload(zip_bytes=b"abc")))
    assert exc.value.code == "bad_signature"


@pytest.mark.parametrize("kid", ["pro-other", "test-1", "license-1", "", None, 7])
def test_wrong_kid_rejected(env, rel_key, kid):
    envl = sign_release(rel_key, release_payload(zip_bytes=b"abc"), kid="pro-test")
    envl["kid"] = kid  # 签名是对的，kid 换了：非 pro- 前缀 / 未知 kid 一律拒收
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(envl)
    assert exc.value.code == "unknown_kid"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"v": 2}, "bad_payload"),
        ({"platform": "windows-x64"}, "platform_mismatch"),
        ({"platform": "linux"}, "platform_mismatch"),
        ({"pro_api": 99}, "unsupported_api"),
        ({"pro_api": "1"}, "unsupported_api"),
        ({"pro_api": True}, "unsupported_api"),
        ({"features": ["quick-layout", "free-stuff"]}, "bad_features"),
        ({"features": "quick-layout"}, "bad_features"),
        ({"sha256": "A" * 64}, "bad_hash"),
        ({"sha256": "a" * 63}, "bad_hash"),
        ({"size": 0}, "bad_size"),
        ({"size": proinstall.MAX_PRO_BYTES + 1}, "bad_size"),
        ({"size": "10"}, "bad_size"),
        ({"size": True}, "bad_size"),
        ({"released_at": "now"}, "bad_payload"),
        ({"pro_version": "../evil"}, "bad_version"),
        ({"pro_version": "1.0/2"}, "bad_version"),
        ({"pro_version": ".hidden"}, "bad_version"),
        ({"pro_version": ""}, "bad_version"),
    ],
)
def test_release_field_checks(env, rel_key, over, code):
    payload = release_payload(zip_bytes=b"abc")
    payload.update(over)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(sign_release(rel_key, payload))
    assert exc.value.code == code


def test_malformed_envelopes(env, rel_key):
    good = sign_release(rel_key, release_payload(zip_bytes=b"abc"))
    for bad in (None, "x", [], {}, {**good, "payload": "!!!"}, {**good, "sig": 5}):
        with pytest.raises(proinstall.ProInstallError):
            proinstall.verify_release(bad)


def test_test_release_key_ignored_in_production(monkeypatch, env, rel_key):
    monkeypatch.delenv(lic.ENV_ENDPOINT)  # 正式模式：没有开发 / 验收端点
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(sign_release(rel_key, release_payload(zip_bytes=b"abc")))
    assert exc.value.code == "unknown_kid"


def test_pro_dir_ignored_in_production(monkeypatch, env):
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(ECHO))
    assert prohost.locate() == ECHO  # 开发 / 验收模式认
    monkeypatch.delenv(lic.ENV_ENDPOINT)
    assert prohost.locate() is None  # 正式模式设了也无效


# ---------------------------------------------------------------------------
# 下载地址白名单
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/pro/x.zip",  # 主机不在白名单
        "https://dl.tavotto.cn.evil.example/pro/x.zip",
        "https://dl.tavotto.cn@evil.example/pro/x.zip",  # 用户信息
        "https://dl.tavotto.cn:8443/pro/x.zip",  # 端口
        "https://dl.tavotto.cn/updater/latest.json",  # 路径不以 /pro/ 起
        "https://dl.tavotto.cn/pro",  # 没有尾斜杠
        "https://dl.tavotto.cn/pro/../updater/x.zip",  # 穿越
        "https://dl.tavotto.cn/pro/%2e%2e/x.zip",
        "http://dl.tavotto.cn/pro/x.zip",  # 非 https
        "ftp://dl.tavotto.cn/pro/x.zip",
        "https://127.0.0.1/pro/x.zip",  # 回环只有开发模式认
        "http://127.0.0.1:9/pro/x.zip",
        "https://dl.tavotto.cn/pro/x.zip#frag",
        "",
        None,
    ],
)
def test_download_url_rejected_in_production(monkeypatch, tmp_path, url):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv(lic.ENV_ENDPOINT, raising=False)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.validate_download_url(url)
    assert exc.value.code == "host_not_allowed"


def test_download_url_allowed(env, monkeypatch):
    assert proinstall.validate_download_url("https://dl.tavotto.cn/pro/a/b.zip?sig=1")
    assert proinstall.validate_download_url("http://127.0.0.1:5000/pro/x.zip")  # 开发模式
    assert proinstall.validate_download_url("http://localhost:5000/pro/x.zip")
    for bad in ("http://127.0.0.1:5000/other/x.zip", "http://evil.example/pro/x.zip"):
        with pytest.raises(proinstall.ProInstallError):
            proinstall.validate_download_url(bad)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def test_download_ok(env, srv, rel_key):
    z, _, url = stage(srv, rel_key)
    tmp = proinstall.download(url, len(z), hashlib.sha256(z).hexdigest())
    assert tmp.read_bytes() == z
    assert tmp.parent == proinstall.pro_dir() and tmp.name.startswith(".dl-")


def test_download_hash_mismatch_removes_file(env, srv, rel_key):
    z, _, url = stage(srv, rel_key)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download(url, len(z), "0" * 64)
    assert exc.value.code == "hash_mismatch"
    assert names_in_pro_dir() == []


def test_download_short_body_is_size_mismatch(env, srv, rel_key):
    z, _, url = stage(srv, rel_key)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download(url, len(z) + 10, hashlib.sha256(z).hexdigest())
    assert exc.value.code == "size_mismatch"
    assert names_in_pro_dir() == []


def test_download_declared_length_over_size_aborts(env, srv, rel_key):
    z, _, url = stage(srv, rel_key)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download(url, len(z) - 1, hashlib.sha256(z).hexdigest())
    assert exc.value.code == "size_exceeded"
    assert names_in_pro_dir() == []


def test_download_stream_aborts_as_soon_as_size_exceeded(env, srv):
    """没有 Content-Length 的无限流：一超过 `size` 就断开，服务端只发出了一小部分（不是读完 64 MiB 再判断）。"""
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download(srv.base + "/pro/endless", 100_000, "0" * 64)
    assert exc.value.code == "size_exceeded"
    time.sleep(0.3)
    assert srv.streamed < 16 * 1024 * 1024
    assert names_in_pro_dir() == []


def test_download_does_not_follow_redirect(env, srv, rel_key):
    z, _, _ = stage(srv, rel_key)
    srv.files["/pro/zip"] = z
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download(srv.base + "/pro/redirect", len(z), hashlib.sha256(z).hexdigest())
    assert exc.value.code == "download_failed"
    assert not [g for g in srv.gets if g == "/pro/zip"]


def test_download_refuses_unlisted_host_without_network(env, srv):
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.download("http://127.0.0.1:1/other/x.zip", 10, "0" * 64)
    assert exc.value.code == "host_not_allowed"
    assert srv.gets == []


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
def test_install_ok(env, srv, rel_key):
    out = install_version(srv, rel_key, "1.0.0")
    assert out["current"] == f"1.0.0-{PLAT}" and out["previous"] is None
    st = proinstall.read_state()
    assert st == {"current": f"1.0.0-{PLAT}", "previous": None}
    assert (
        proinstall.pro_dir() / f"1.0.0-{PLAT}" / "tavotto-pro" / "tavotto_pro" / "__main__.py"
    ).is_file()
    assert not [n for n in names_in_pro_dir() if n.startswith((".tmp-", ".dl-"))]


def test_install_switch_keeps_current_and_previous_only(env, srv, rel_key):
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        install_version(srv, rel_key, v)
    st = proinstall.read_state()
    assert st == {"current": f"1.2.0-{PLAT}", "previous": f"1.1.0-{PLAT}"}
    assert (proinstall.pro_dir() / f"1.2.0-{PLAT}").is_dir()
    assert (proinstall.pro_dir() / f"1.1.0-{PLAT}").is_dir()
    assert not (proinstall.pro_dir() / f"1.0.0-{PLAT}").exists()  # 更早的删了


def test_install_keeps_running_sidecar_dir(env, srv, rel_key, monkeypatch):
    monkeypatch.setattr(prohost, "_codesign_ok", lambda root: True)
    install_version(srv, rel_key, "1.0.0")
    assert prohost.call("echo", {"a": 1}, feature="quick-layout") == {"payload": {"a": 1}}
    install_version(srv, rel_key, "1.1.0")
    install_version(srv, rel_key, "1.2.0")  # 1.0.0 该被删了，但它正在运行
    assert (proinstall.pro_dir() / f"1.0.0-{PLAT}").is_dir()
    assert prohost.call("echo", {"b": 2}, feature="quick-layout") == {
        "payload": {"b": 2}
    }  # 没被切断
    prohost.shutdown()
    install_version(srv, rel_key, "1.3.0")  # 不再运行：这一轮一并清掉
    assert not (proinstall.pro_dir() / f"1.0.0-{PLAT}").exists()


def test_install_prune_leaves_foreign_entries(env, srv, rel_key):
    d = proinstall.pro_dir()
    d.mkdir(parents=True)
    (d / "notes").mkdir()
    (d / "readme.txt").write_text("x", encoding="utf-8")
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        install_version(srv, rel_key, v)
    assert (d / "notes").is_dir() and (d / "readme.txt").is_file()


def test_release_json_mismatch_rejected(env, srv, rel_key):
    for override in (
        {"pro_version": "9.9.9"},
        {"features": ["hosted-ai"]},
        {"released_at": 5},
        {"extra": 1},
    ):
        z = make_zip("1.0.0", inner_override=override)
        envl = release_for(rel_key, z, "1.0.0")
        p = proinstall.pro_dir() / "z.zip"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(z)
        before = state_snapshot()
        with pytest.raises(proinstall.ProInstallError) as exc:
            proinstall.install(p, envl)
        assert exc.value.code == "release_mismatch", override
        assert state_snapshot() == before
        assert not [
            n for n in names_in_pro_dir() if n.startswith(".tmp-") or n.endswith("-" + PLAT)
        ]


def test_release_json_compared_in_canonical_form(env, srv, rel_key):
    """键序 / 空白不同不算不一致（比较规范化后的 JSON 字节）；sha256 / size 不参与（它们描述 zip 自己）。"""
    z = make_zip("1.0.0")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        inner = json.loads(zf.read("release.json"))
    reordered = json.dumps(dict(reversed(list(inner.items()))), indent=3)
    z2 = make_zip("1.0.0", skip_release=True, extra={"release.json": reordered})
    p = proinstall.pro_dir() / "z.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(z2)
    assert proinstall.install(p, release_for(rel_key, z2, "1.0.0"))["current"] == f"1.0.0-{PLAT}"


@pytest.mark.parametrize(
    "kw",
    [
        {"skip_release": True},
        {"skip_pkg": True},
        {"extra": {"../escape.txt": "x"}},
        {"extra": {"/abs.txt": "x"}},
        {"extra": {"tavotto-pro/../../escape.txt": "x"}},
        {"extra": {"C:/win.txt": "x"}},
        {"extra": {"tavotto-pro\\back.txt": "x"}},
        {"extra": {"other-top/file": "x"}},
        {"symlink": "/etc/passwd"},
    ],
)
def test_hostile_zip_rejected(env, srv, rel_key, kw):
    install_version(srv, rel_key, "1.0.0")
    before = state_snapshot()
    z = make_zip("1.1.0", **kw)
    p = proinstall.pro_dir() / "z.zip"
    p.write_bytes(z)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.install(p, release_for(rel_key, z, "1.1.0"))
    assert exc.value.code in {"bad_package", "release_mismatch"}
    assert state_snapshot() == before
    assert not [n for n in names_in_pro_dir() if n.startswith(".tmp-")]
    assert not (proinstall.pro_dir() / f"1.1.0-{PLAT}").exists()
    assert not (proinstall.pro_dir().parent / "escape.txt").exists()
    assert not (proinstall.pro_dir().parent.parent / "escape.txt").exists()


def test_unpacked_size_cap(env, srv, rel_key, monkeypatch):
    monkeypatch.setattr(proinstall, "MAX_UNPACKED_BYTES", 100)
    z = make_zip("1.0.0", extra={"tavotto-pro/blob": "x" * 5000})
    p = proinstall.pro_dir() / "z.zip"
    p.parent.mkdir(parents=True)
    p.write_bytes(z)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.install(p, release_for(rel_key, z, "1.0.0"))
    assert exc.value.code == "bad_package"
    assert state_snapshot() is None


def test_install_reverifies_envelope(env, srv, rel_key):
    """install 不信调用方：信封被换 / 篡改一样拒收，current.json 不动。"""
    z = make_zip("1.0.0")
    p = proinstall.pro_dir() / "z.zip"
    p.parent.mkdir(parents=True)
    p.write_bytes(z)
    envl = release_for(rel_key, z, "1.0.0")
    envl["kid"] = "evil-1"
    with pytest.raises(proinstall.ProInstallError):
        proinstall.install(p, envl)
    assert state_snapshot() is None


def test_install_failure_between_rename_and_state_cleans_up(env, srv, rel_key, monkeypatch):
    install_version(srv, rel_key, "1.0.0")
    before = state_snapshot()

    def boom(*a, **k):
        raise proinstall.ProInstallError("io_error")

    z = make_zip("1.1.0")
    p = proinstall.pro_dir() / "z.zip"
    p.write_bytes(z)
    with monkeypatch.context() as m:
        m.setattr(proinstall, "_write_state", boom)
        with pytest.raises(proinstall.ProInstallError):
            proinstall.install(p, release_for(rel_key, z, "1.1.0"))
    assert state_snapshot() == before
    assert not (proinstall.pro_dir() / f"1.1.0-{PLAT}").exists()


def test_install_lock_is_exclusive(env):
    got = threading.Event()
    release = threading.Event()

    def holder():
        with proinstall._locked():
            got.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert got.wait(5)
    with pytest.raises(proinstall.ProInstallError) as exc:
        with proinstall._locked(0.2):
            pass
    assert exc.value.code == "busy"
    release.set()
    t.join(5)
    with proinstall._locked(1):
        pass


# ---------------------------------------------------------------------------
# bad.json
# ---------------------------------------------------------------------------
def test_bad_version_not_installed(env, srv, rel_key):
    proinstall.mark_bad(f"1.0.0-{PLAT}")
    z = make_zip("1.0.0")
    p = proinstall.pro_dir() / "z.zip"
    p.write_bytes(z)
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.install(p, release_for(rel_key, z, "1.0.0"))
    assert exc.value.code == "bad_version"
    assert state_snapshot() is None


def test_mark_bad_ignores_bad_names(env):
    proinstall.mark_bad("../x")
    proinstall.mark_bad(f"1.0.0-{PLAT}")
    proinstall.mark_bad(f"1.0.0-{PLAT}")
    assert proinstall.read_bad() == [f"1.0.0-{PLAT}"]


# ---------------------------------------------------------------------------
# 切换与回退（prohost）
# ---------------------------------------------------------------------------
@pytest.fixture
def host(env, monkeypatch):
    monkeypatch.setattr(prohost, "_codesign_ok", lambda root: True)  # 这些用例不涉及 macOS 签名
    yield


def test_new_version_handshake_failure_rolls_back_to_previous(host, srv, rel_key):
    install_version(srv, rel_key, "1.0.0")
    install_version(srv, rel_key, "2.0.0", main_py=CRASH_MAIN)  # 新版本起不来
    assert proinstall.current_name() == f"2.0.0-{PLAT}"
    out = prohost.call("echo", {"ok": 1}, feature="quick-layout")  # 静默切回、照常工作
    assert out == {"payload": {"ok": 1}}
    assert proinstall.read_state() == {"current": f"1.0.0-{PLAT}", "previous": None}
    assert proinstall.read_bad() == [f"2.0.0-{PLAT}"]
    assert prohost._crashes == 0  # 回退成功不算崩溃


def test_no_previous_means_no_component(host, srv, rel_key):
    install_version(srv, rel_key, "1.0.0", main_py=CRASH_MAIN)
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="quick-layout")
    assert exc.value.code == "missing"
    assert proinstall.current_name() is None
    assert proinstall.read_bad() == [f"1.0.0-{PLAT}"]


def test_rollback_chain_stops_when_previous_also_bad(host, srv, rel_key):
    install_version(srv, rel_key, "1.0.0", main_py=CRASH_MAIN)
    install_version(srv, rel_key, "2.0.0", main_py=CRASH_MAIN)
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="quick-layout")
    assert exc.value.code == "missing"
    assert set(proinstall.read_bad()) == {f"1.0.0-{PLAT}", f"2.0.0-{PLAT}"}


def test_features_mismatch_with_release_rolls_back(host, srv, rel_key):
    install_version(srv, rel_key, "1.0.0")
    # release 说只有 hosted-ai，示例扩展握手回 quick-layout：不一致 = 坏包
    install_version(srv, rel_key, "2.0.0", features=("hosted-ai",))
    assert prohost.call("echo", {}, feature="quick-layout") == {"payload": {}}
    assert proinstall.read_bad() == [f"2.0.0-{PLAT}"]


def test_unsupported_pro_api_from_sidecar_rolls_back(host, srv, rel_key, monkeypatch):
    install_version(srv, rel_key, "1.0.0")
    monkeypatch.setenv("PRO_ECHO_API", "2")  # 握手回 pro_api=2：宿主不认
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="quick-layout")
    assert exc.value.code == "missing"  # 唯一的版本也不兼容 → 当作没有组件
    assert proinstall.read_bad() == [f"1.0.0-{PLAT}"]


def test_codesign_failure_rolls_back(env, srv, rel_key, monkeypatch):
    install_version(srv, rel_key, "1.0.0")
    install_version(srv, rel_key, "2.0.0")
    seen = []

    def fake(root):
        seen.append(root.parent.name)
        return not root.parent.name.startswith("2.0.0")

    monkeypatch.setattr(prohost, "_codesign_ok", fake)
    assert prohost.call("echo", {}, feature="quick-layout") == {"payload": {}}
    assert seen == [f"2.0.0-{PLAT}", f"1.0.0-{PLAT}"]
    assert proinstall.read_bad() == [f"2.0.0-{PLAT}"]


def test_codesign_command_shape(monkeypatch, tmp_path):
    exe = tmp_path / "tavotto-pro"
    exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr(prohost, "_is_macos", lambda: True)
    calls = []

    class Done:
        returncode = 0

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return Done()

    monkeypatch.setattr(prohost.subprocess, "run", fake_run)
    assert prohost._codesign_ok(tmp_path) is True
    argv, kw = calls[0]
    assert argv == ["/usr/bin/codesign", "--verify", "--strict", str(exe)]
    assert kw["timeout"] == prohost.CODESIGN_TIMEOUT_S and "creationflags" in kw
    Done.returncode = 1
    assert prohost._codesign_ok(tmp_path) is False

    def timeout(argv, **kw):
        raise prohost.subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(prohost.subprocess, "run", timeout)
    assert prohost._codesign_ok(tmp_path) is False
    monkeypatch.setattr(prohost, "_is_macos", lambda: False)
    assert prohost._codesign_ok(tmp_path) is True  # 非 macOS 不跑


def test_dev_dir_source_skips_install_checks(host, env, monkeypatch, srv, rel_key):
    """开发目录来源不走回退：`TAVOTTO_PRO_DIR` 起不来就是起不来，不去动已安装的版本。"""
    install_version(srv, rel_key, "1.0.0")
    bad = Path(os.environ["TAVOTTO_DATA_DIR"]) / "devpro"
    (bad / "tavotto_pro").mkdir(parents=True)
    (bad / "tavotto_pro" / "__main__.py").write_text(CRASH_MAIN, encoding="utf-8")
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(bad))
    with pytest.raises(prohost.ProUnavailable):
        prohost.call("echo", {}, feature="quick-layout")
    assert proinstall.read_bad() == [] and proinstall.current_name() == f"1.0.0-{PLAT}"


# ---------------------------------------------------------------------------
# check_for_update：零网络门槛与完整流程
# ---------------------------------------------------------------------------
def test_no_token_means_zero_requests(env, srv):
    assert proinstall.check_for_update()["status"] == "unlicensed"
    assert srv.posts == [] and srv.gets == []


def test_expired_token_means_zero_requests(env, srv):
    license_token(srv, exp=int(time.time()) - 10, offline_until=int(time.time()) - 10)
    assert proinstall.check_for_update()["status"] == "unlicensed"
    assert srv.posts == [] and srv.gets == []


def test_token_without_any_feature_means_zero_requests(env, srv):
    license_token(srv, features=[])
    assert proinstall.check_for_update()["status"] == "unlicensed"
    assert srv.posts == []


def test_unsupported_platform_means_zero_requests(env, srv, monkeypatch):
    license_token(srv)
    monkeypatch.setattr(proinstall, "platform_key", lambda: None)
    assert proinstall.check_for_update()["status"] == "no_platform"
    assert srv.posts == [] and srv.gets == []


def test_license_off_means_zero_requests(env, srv, monkeypatch):
    license_token(srv)
    monkeypatch.setenv(lic.ENV_HARD_OFF, "1")
    assert proinstall.check_for_update()["status"] == "disabled"
    assert srv.posts == [] and srv.gets == []


def test_positive_control_one_manifest_request(env, srv):
    """反面对照：有令牌且平台受支持时确实发了请求（证明上面的「零请求」断言不是计数器坏了）。"""
    license_token(srv)
    assert proinstall.check_for_update()["status"] == "no_release"
    assert srv.manifest_calls() == 1
    body = srv.posts[0][1]
    assert body["platform"] == PLAT and body["pro_api"] == [1] and body["license_id"] == LID
    assert body["op"] == "pro_manifest" and set(body) >= {"device_pub", "ts", "sig"}


def test_pro_manifest_without_token_raises_without_network(env, srv):
    with pytest.raises(lic.LicenseError) as exc:
        lic.pro_manifest(PLAT, [1])
    assert exc.value.code == "client_error"
    assert srv.posts == []
    assert "pro_manifest" in lic.OPS


def test_check_for_update_full_flow(env, srv, rel_key):
    license_token(srv)
    z, envl, url = stage(srv, rel_key, "1.0.0")
    srv.manifest_reply = {"release": envl, "url": url, "app_update_needed": True}
    out = proinstall.check_for_update()
    assert out == {"status": "installed", "code": "", "app_update_needed": True}
    assert proinstall.current_name() == f"1.0.0-{PLAT}"
    n_gets = len(srv.gets)
    again = proinstall.check_for_update()  # 已是 current：不再下载
    assert again["status"] == "current" and len(srv.gets) == n_gets


def test_check_for_update_upgrade_and_rollback_by_server(env, srv, rel_key):
    license_token(srv)
    for v in ("1.0.0", "1.1.0"):
        z, envl, url = stage(srv, rel_key, v)
        srv.manifest_reply = {"release": envl, "url": url, "app_update_needed": False}
        assert proinstall.check_for_update()["status"] == "installed"
    # 服务端把推荐版本改回 1.0.0：允许降级（旧版本同样有有效签名）
    z, envl, url = stage(srv, rel_key, "1.0.0")
    srv.manifest_reply = {"release": envl, "url": url, "app_update_needed": False}
    assert proinstall.check_for_update()["status"] == "installed"
    assert proinstall.read_state() == {"current": f"1.0.0-{PLAT}", "previous": f"1.1.0-{PLAT}"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda srv, envl, url: (envl, "https://evil.example/pro/1.0.0.zip"),  # 下载地址不在白名单
        lambda srv, envl, url: (envl, url.replace("/pro/", "/other/")),  # 路径不以 /pro/ 起
        lambda srv, envl, url: ({**envl, "kid": "pro-other"}, url),  # 换 kid
        lambda srv, envl, url: (
            {**envl, "payload": envl["payload"][:-4] + "AAAA"},
            url,
        ),  # 篡改负载
        lambda srv, envl, url: (None, url),  # 没有 release
    ],
)
def test_check_for_update_rejects_bad_manifest(env, srv, rel_key, mutate):
    license_token(srv)
    z, envl, url = stage(srv, rel_key, "1.0.0")
    envl2, url2 = mutate(srv, envl, url)
    srv.manifest_reply = {"release": envl2, "url": url2, "app_update_needed": False}
    before = state_snapshot()
    out = proinstall.check_for_update()
    assert out["status"] in {"rejected", "manifest_failed"}
    assert state_snapshot() == before
    assert srv.gets == []  # 验不过就一个下载请求都不发
    assert not [n for n in names_in_pro_dir() if n.startswith((".dl-", ".tmp-"))]


def test_check_for_update_rejects_wrong_hash_and_size(env, srv, rel_key):
    license_token(srv)
    z = make_zip("1.0.0")
    srv.files["/pro/1.0.0.zip"] = z
    for over, code in (
        ({"sha256": "0" * 64}, "hash_mismatch"),
        ({"size": len(z) - 1}, "size_exceeded"),
    ):
        envl = sign_release(rel_key, release_payload("1.0.0", zip_bytes=z, **over))
        srv.manifest_reply = {
            "release": envl,
            "url": srv.base + "/pro/1.0.0.zip",
            "app_update_needed": False,
        }
        out = proinstall.check_for_update()
        assert (out["status"], out["code"]) == ("rejected", code)
        assert state_snapshot() is None
        assert not [n for n in names_in_pro_dir() if n.startswith((".dl-", ".tmp-"))]


def test_check_for_update_rejects_release_json_mismatch(env, srv, rel_key):
    license_token(srv)
    z = make_zip("1.0.0", inner_override={"features": ["hosted-ai"]})
    srv.files["/pro/1.0.0.zip"] = z
    srv.manifest_reply = {
        "release": release_for(rel_key, z, "1.0.0"),
        "url": srv.base + "/pro/1.0.0.zip",
        "app_update_needed": False,
    }
    out = proinstall.check_for_update()
    assert (out["status"], out["code"]) == ("rejected", "release_mismatch")
    assert state_snapshot() is None


def test_bad_version_not_redownloaded_until_recommendation_changes(host, env, srv, rel_key):
    license_token(srv)
    z1, e1, u1 = stage(srv, rel_key, "1.0.0")
    srv.manifest_reply = {"release": e1, "url": u1, "app_update_needed": False}
    assert proinstall.check_for_update()["status"] == "installed"
    z2, e2, u2 = stage(srv, rel_key, "2.0.0", main_py=CRASH_MAIN)
    srv.manifest_reply = {"release": e2, "url": u2, "app_update_needed": False}
    assert proinstall.check_for_update()["status"] == "installed"
    assert prohost.call("echo", {}, feature="quick-layout") == {
        "payload": {}
    }  # 回退到 1.0.0、2.0.0 记 bad
    assert proinstall.read_bad() == [f"2.0.0-{PLAT}"]
    gets = len(srv.gets)
    assert proinstall.check_for_update()["status"] == "bad_version"  # 服务端仍推荐 2.0.0：不再下载
    assert len(srv.gets) == gets
    z3, e3, u3 = stage(srv, rel_key, "3.0.0")  # 推荐版本变了：bad 清空、装新版本
    srv.manifest_reply = {"release": e3, "url": u3, "app_update_needed": False}
    assert proinstall.check_for_update()["status"] == "installed"
    assert proinstall.read_bad() == []


def test_check_for_update_never_raises(env, srv, monkeypatch):
    license_token(srv)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(lic, "pro_manifest", boom)
    assert proinstall.check_for_update()["status"] == "failed"


def test_check_for_update_manifest_network_failure_is_status(env, srv):
    license_token(srv)
    # 端点指向没人监听的端口：网络失败折成状态，不抛
    os.environ[lic.ENV_ENDPOINT] = "http://127.0.0.1:1"
    out = proinstall.check_for_update()
    assert out["status"] == "manifest_failed" and out["code"] == "network"


def test_concurrent_check_is_busy(env, srv, monkeypatch):
    license_token(srv)
    entered, release = threading.Event(), threading.Event()
    real = lic.pro_manifest

    def slow(*a, **k):
        entered.set()
        release.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(lic, "pro_manifest", slow)
    t = threading.Thread(target=proinstall.check_for_update)
    t.start()
    assert entered.wait(5)
    assert proinstall.check_for_update()["status"] == "busy"
    release.set()
    t.join(10)


# ---------------------------------------------------------------------------
# 授权后触发（激活 / 刷新成功后，后台线程、不阻塞响应）
# ---------------------------------------------------------------------------
def test_activate_triggers_check_in_background_without_blocking(env, srv):
    started, finish = threading.Event(), threading.Event()

    def slow_hook():
        started.set()
        finish.wait(10)

    lic.register_after_license(slow_hook)
    t0 = time.monotonic()
    out = lic.activate("TVK-ABCD-EFGH-JKMN")
    returned_before_hook_finished = not finish.is_set()
    assert out["ok"] is True and out["licensed"] is True
    assert returned_before_hook_finished  # 激活的响应没有等回调
    assert started.wait(5)
    assert time.monotonic() - t0 < 5
    finish.set()


def test_activate_then_installs_component(env, srv, rel_key):
    z, envl, url = stage(srv, rel_key, "1.0.0")
    srv.manifest_reply = {"release": envl, "url": url, "app_update_needed": False}
    proinstall.register()
    lic.activate("TVK-ABCD-EFGH-JKMN")
    deadline = time.monotonic() + 15
    while proinstall.current_name() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proinstall.current_name() == f"1.0.0-{PLAT}"


def test_after_license_hook_exceptions_are_swallowed(env, srv):
    done = threading.Event()

    def bad():
        raise RuntimeError("boom")

    lic.register_after_license(bad)
    lic.register_after_license(done.set)
    assert lic.activate("TVK-ABCD-EFGH-JKMN")["ok"] is True
    assert done.wait(5)  # 前一个回调炸了不影响后一个


def test_no_hooks_registered_is_noop(env, srv):
    assert lic._AFTER_LICENSE == []
    assert lic.activate("TVK-ABCD-EFGH-JKMN")["ok"] is True


# ---------------------------------------------------------------------------
# 诊断包与结构
# ---------------------------------------------------------------------------
def test_diagnostics_modules_never_read_pro_dir():
    """诊断包是白名单收集（不遍历 data_dir）；`pro/` 整个不在其中。这里钉死：收集代码不 import 安装链、不拼 `pro` 数据路径。"""
    root = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
    for name in ("diagnostics.py", "diagdetail.py", "diagsummary.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "proinstall" not in src and "prohost" not in src, name
        assert 'data_path("pro"' not in src and 'data_dir() / "pro"' not in src, name


def test_proinstall_has_no_direct_network_imports():
    """出站只经 `diagsend.stream_get`（笼子）与 `license`：本模块自己不 import 任何网络库（AST 判 import，不判子串）。"""
    import ast

    assert callable(diagsend.stream_get)
    tree = ast.parse(Path(proinstall.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mods.add(node.module or "")
    assert not mods & {"socket", "ssl", "http.client", "urllib.request", "requests", "httpx"}, mods


def test_proinstall_not_imported_by_document_paths():
    """结果是普通数据（ADR 0198 §十二）：文档读写 / 渲染 / 导出模块不 import 授权与组件宿主。"""
    root = Path(__file__).resolve().parents[1] / "src" / "tavotto"
    for name in (
        "documents.py",
        "engine/atomicio.py",
        "engine/exportjob.py",
        "engine/exportreq.py",
    ):
        p = root / name
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        assert "proinstall" not in src and "prohost" not in src, name


def test_release_keys_table_only_serves_pro_kids(env, monkeypatch):
    """`RELEASE_KEYS` 里即使被塞了非 `pro-` 前缀的 kid 也不认（授权令牌的 kid 与发布 kid 命名空间隔离）。"""
    key = nacl_signing.SigningKey.generate()
    pub = _b64(bytes(key.verify_key))
    monkeypatch.setattr(proinstall, "RELEASE_KEYS", {"license-1": pub, "pro-1": pub})
    payload = release_payload(zip_bytes=b"abc")
    assert (
        proinstall.verify_release(sign_release(key, payload, kid="pro-1"))["pro_version"] == "1.0.0"
    )
    with pytest.raises(proinstall.ProInstallError) as exc:
        proinstall.verify_release(sign_release(key, payload, kid="license-1"))
    assert exc.value.code == "unknown_kid"


# ---------------------------------------------------------------------------
# 跨进程「正在使用」租约（另一个宿主进程在跑旧版本时，本进程的清理不删它）
# ---------------------------------------------------------------------------
_HOLDER = (
    "import sys, time\n"
    "from tavotto.engine import proinstall\n"
    "h = proinstall.acquire_in_use(sys.argv[1])\n"
    "print('held' if h is not None else 'none', flush=True)\n"
    "sys.stdin.readline()\n"
)


def _spawn_holder(root):
    import subprocess
    import sys

    p = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _HOLDER, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(proinstall.__file__).parents[2])},
    )
    assert p.stdout.readline().strip() == "held"
    return p


def _stop_holder(p):
    p.stdin.write("\n")
    p.stdin.flush()
    p.wait(timeout=10)


def test_install_prune_keeps_version_leased_by_other_process(env, srv, rel_key):
    install_version(srv, rel_key, "1.0.0")
    a = proinstall.pro_dir() / f"1.0.0-{PLAT}"
    holder = _spawn_holder(a / "tavotto-pro")
    try:
        install_version(srv, rel_key, "1.1.0")
        install_version(srv, rel_key, "1.2.0")
        assert a.is_dir()  # 另一个进程的 sidecar 还在用它
    finally:
        _stop_holder(holder)
    install_version(srv, rel_key, "1.3.0")  # 租约随进程释放，下一轮清掉
    assert not a.exists()
    # 持有者是直接退出的（没调 release）：租约文件可能还在，但已没人持锁，过了宽限期由清理删掉
    assert not any(proinstall._lease_held(p) for p in proinstall._leases(f"1.0.0-{PLAT}"))


def test_every_host_running_a_version_keeps_its_own_lease(env, srv, rel_key):
    """两个宿主跑同一个版本：先启动的那个退出后，后一个还在跑，这个版本仍不能被清掉。"""
    install_version(srv, rel_key, "1.0.0")
    a = proinstall.pro_dir() / f"1.0.0-{PLAT}"
    first = _spawn_holder(a / "tavotto-pro")
    second = _spawn_holder(a / "tavotto-pro")  # 第二个也拿到租约（_spawn_holder 断言 held）
    try:
        _stop_holder(first)
        install_version(srv, rel_key, "1.1.0")
        install_version(srv, rel_key, "1.2.0")
        assert a.is_dir()
    finally:
        _stop_holder(second)
    install_version(srv, rel_key, "1.3.0")
    assert not a.exists()


def test_orphan_lease_file_is_swept_but_fresh_one_is_kept(env, srv, rel_key):
    """进程被强杀留下的租约文件：没人持锁、超过宽限期就清；刚建的（还没来得及上锁）不动。"""
    install_version(srv, rel_key, "1.0.0")
    name = f"1.0.0-{PLAT}"
    old = proinstall.pro_dir() / f".inuse-{name}@dead.lock"
    fresh = proinstall.pro_dir() / f".inuse-{name}@fresh.lock"
    old.write_bytes(b"")
    fresh.write_bytes(b"")
    past = time.time() - 3600
    os.utime(old, (past, past))
    assert not proinstall._in_use_elsewhere(name)
    assert not old.exists()
    assert fresh.exists()


def test_install_refuses_to_replace_dir_leased_elsewhere(env, srv, rel_key):
    install_version(srv, rel_key, "1.0.0")
    install_version(srv, rel_key, "1.1.0")
    a = proinstall.pro_dir() / f"1.0.0-{PLAT}"
    holder = _spawn_holder(a / "tavotto-pro")
    try:
        with pytest.raises(proinstall.ProInstallError) as exc:
            install_version(srv, rel_key, "1.0.0")
        assert exc.value.code == "busy"
    finally:
        _stop_holder(holder)


def test_prohost_holds_lease_while_sidecar_runs(env, srv, rel_key, monkeypatch):
    monkeypatch.setattr(prohost, "_codesign_ok", lambda root: True)
    install_version(srv, rel_key, "1.0.0")
    name = f"1.0.0-{PLAT}"
    prohost.call("echo", {}, feature="quick-layout")
    assert proinstall._in_use_elsewhere(name)  # 运行中：租约被持有
    prohost.shutdown()
    assert not proinstall._in_use_elsewhere(name)  # 退出：释放


def test_stream_get_total_deadline_holds_against_slow_drip(env, srv):
    """服务端每 0.05 秒滴 1 字节：每次 socket 操作都不超时，但总时限 0.3 秒必须按时到（不能等攒够一整块）。"""
    t0 = time.monotonic()
    with pytest.raises(diagsend._Failure) as exc:
        for _ in diagsend.stream_get(
            srv.base + "/pro/slowdrip", max_bytes=10**6, timeout=5.0, deadline_s=0.3
        ):
            pass
    assert exc.value.code == "timeout"
    assert time.monotonic() - t0 < 1.0
