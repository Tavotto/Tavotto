"""带凭据的明文 http 的放行判据（`engine/netpolicy.py`，用户裁决 2026-10-25：实验室自建模型服务器，放行局域网）。

判据的主语：**连接实际去了哪个 IP**。放行 = 回环 + 私网（10/8、172.16/12、192.168/16、100.64/10、fc00::/7）；
链路本地（含云元数据）、0.0.0.0/8、公网、组播一律拒。主机名在判定时解析、**全部**结果都得在放行段；连接时钉住判定那次的 IP
（`Host` 头保留原主机名）——所以用例里的「假 DNS」第二次问就换成公网，看连接有没有被带走。全部在 127.0.0.1 的本机服务上，从不联网。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tavotto.engine import ai_catalog, ai_providers, netpolicy
from tavotto.engine.agent_runner import wires

ALLOWED = [
    "127.0.0.1",
    "[::1]",
    "localhost",
    "10.0.0.1",
    "10.255.255.254",
    "172.16.0.1",
    "172.31.255.1",
    "192.168.0.1",
    "192.168.255.9",
    "100.64.0.1",
    "100.127.255.1",
    "[fc00::1]",
    "[fd12:3456::7]",
    "[::ffff:10.0.0.5]",
]
DENIED = [
    "169.254.169.254",
    "169.254.0.1",
    "[fe80::1]",
    "0.0.0.0",
    "0.1.2.3",
    "8.8.8.8",
    "1.1.1.1",
    "172.32.0.1",
    "172.15.255.255",
    "100.128.0.1",
    "100.63.255.255",
    "192.169.0.1",
    "11.0.0.1",
    "224.0.0.1",
    "255.255.255.255",
    "240.0.0.1",
    "127.0.0.2",
    "[::2]",
    "[2001:db8::1]",
    "[::ffff:8.8.8.8]",
]


@pytest.mark.parametrize("host", ALLOWED)
def test_literal_private_and_loopback_addresses_are_allowed(host):
    url = f"http://{host}:8080/v1"
    assert wires.check_base_url(url) is None
    assert ai_catalog.url_refusal(url) is None


@pytest.mark.parametrize("host", DENIED)
def test_other_literal_addresses_are_refused(host):
    url = f"http://{host}:8080/v1"
    assert wires.check_base_url(url) == "base_url_scheme"
    assert ai_catalog.url_refusal(url) == "insecure_url"


def test_hostnames_need_every_resolved_address_to_be_private(monkeypatch):
    table = {
        "lab.local": ["192.168.1.20"],
        "lab-dual.local": ["10.0.0.5", "fd00::5"],
        "mixed.example": ["10.0.0.5", "8.8.8.8"],
        "meta.example": ["169.254.169.254"],
        "ll.example": ["fe80::1"],
        "gone.example": [],
        "v4mapped.example": ["::ffff:10.1.1.1"],
    }
    monkeypatch.setattr(netpolicy, "resolve_host", lambda host: list(table.get(host, [])))
    for name, ok in (
        ("lab.local", True),
        ("lab-dual.local", True),
        ("v4mapped.example", True),
        ("mixed.example", False),
        ("meta.example", False),
        ("ll.example", False),
        ("gone.example", False),
        ("unknown.example", False),
    ):
        netpolicy.clear_cache()
        url = f"http://{name}:9000"
        assert (wires.check_base_url(url) is None) is ok, name
        assert (ai_catalog.url_refusal(url) is None) is ok, name


def test_localhost_never_goes_through_dns(monkeypatch):
    def boom(host):
        raise AssertionError("localhost 不该查 DNS")

    monkeypatch.setattr(netpolicy, "resolve_host", boom)
    assert wires.check_base_url("http://localhost:1") is None
    assert ai_catalog.url_refusal("http://LOCALHOST") is None
    monkeypatch.setattr(netpolicy, "resolve_host", lambda host: [])
    assert wires.check_base_url("http://localhost.evil.example") == "base_url_scheme"


def test_https_is_never_subject_to_the_lan_policy():
    assert wires.check_base_url("https://api.example.com") is None
    assert ai_catalog.url_refusal("https://8.8.8.8") is None


def test_insecure_lan_flag_marks_plain_http_off_the_loopback(monkeypatch):
    monkeypatch.setattr(
        netpolicy, "resolve_host", lambda host: ["10.0.0.9"] if host == "lab.local" else []
    )
    assert netpolicy.is_insecure_lan("http://10.0.0.5:8000")
    assert netpolicy.is_insecure_lan("http://lab.local")
    assert not netpolicy.is_insecure_lan("http://127.0.0.1:8000")
    assert not netpolicy.is_insecure_lan("http://localhost")
    assert not netpolicy.is_insecure_lan("https://10.0.0.5")
    assert not netpolicy.is_insecure_lan("http://8.8.8.8")  # 被拒的不算「放行的局域网」


class Recorder:
    """127.0.0.1 上的假模型服务：记录收到的 Host 头与路径。"""

    def __init__(self):
        self.hits: list[tuple[str, str]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                outer.hits.append((self.path, self.headers.get("Host", "")))
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                data = json.dumps(
                    {"data": [{"id": "m1"}], "content": [], "stop_reason": "end_turn"}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _answer

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(n, "http://127.0.0.1:9")  # 死代理：回环 / 私网 http 能通就证明没走代理
    s = Recorder()
    yield s
    s.close()


def test_post_connects_to_the_pinned_ip_and_keeps_the_original_host_header(server, monkeypatch):
    monkeypatch.setattr(
        netpolicy, "resolve_host", lambda host: ["127.0.0.1"] if host == "lab.local" else []
    )
    status, _h, _b = wires.post_json(
        f"http://lab.local:{server.port}/v1/messages", {"x-api-key": "K"}, {"a": 1}, timeout=5
    )
    assert status == 200
    assert server.hits == [("/v1/messages", f"lab.local:{server.port}")]


def test_dns_rebinding_between_check_and_connect_cannot_redirect_the_request(server, monkeypatch):
    answers = {"lab.local": ["127.0.0.1"]}
    monkeypatch.setattr(netpolicy, "resolve_host", lambda host: list(answers.get(host, [])))
    url = f"http://lab.local:{server.port}/v1/messages"
    assert wires.check_base_url(url) is None  # 判定时是私网（结果进缓存）
    answers["lab.local"] = ["8.8.8.8"]  # 连接时 DNS 已被改成公网
    with pytest.raises(wires.RunnerFailure):
        wires.post_json(url, {"x-api-key": "K"}, {"a": 1}, timeout=5)
    assert server.hits == [], "密钥没有发往任何地方"


def test_pinned_connection_dials_the_validated_ip_not_the_name(monkeypatch):
    seen = {}
    real = netpolicy.http.client.HTTPConnection

    class Spy(real):
        def __init__(self, host, *a, **kw):
            seen["host"] = host
            super().__init__(host, *a, **kw)

    monkeypatch.setattr(netpolicy.http.client, "HTTPConnection", Spy)
    monkeypatch.setattr(netpolicy, "resolve_host", lambda host: ["192.168.7.7"])
    conn, host_header = netpolicy.pinned_connection("http://lab.local:8123/x", 3)
    assert seen["host"] == "192.168.7.7" and host_header == "lab.local:8123"
    conn.close()
    with pytest.raises(ValueError):
        netpolicy.pinned_connection("http://169.254.169.254/latest", 3)
    with pytest.raises(ValueError):
        netpolicy.pinned_connection("https://lab.local", 3)


def test_model_listing_also_pins_the_address(server, monkeypatch):
    monkeypatch.setattr(
        netpolicy, "resolve_host", lambda host: ["127.0.0.1"] if host == "lab.local" else []
    )
    models, err = ai_catalog.fetch_models(f"http://lab.local:{server.port}", "K")
    assert (models, err) == (["m1"], None)
    assert server.hits == [("/v1/models", f"lab.local:{server.port}")]
    models, err = ai_catalog.fetch_models("http://169.254.169.254", "K")
    assert (models, err) == (None, "insecure_url")


def test_builtin_conn_and_connections_follow_the_same_judge(monkeypatch):
    rec = {
        "id": "lab",
        "agent": "claude",
        "base_url": "http://10.2.3.4:8000/anthropic",
        "api_key": "k-0123456789",
        "default_model": "m",
    }
    assert ai_providers.builtin_conn(rec) is not None
    assert ai_providers.builtin_conn({**rec, "base_url": "http://169.254.169.254/x"}) is None
    assert ai_providers.builtin_conn({**rec, "base_url": "http://8.8.8.8/x"}) is None


#: 云厂商元数据地址（出处见 `netpolicy.METADATA_IPS` 的注释）。其中 AWS 的 IPv6 `fd00:ec2::254` 落在 `fc00::/7`、
#: 阿里云 `100.100.100.200` 落在 `100.64.0.0/10`——不先排除就会被放行段放过去。
METADATA_ADDRESSES = [
    "169.254.169.254",  # AWS / GCP / Azure / Oracle / DigitalOcean / Huawei IMDS
    "169.254.170.2",  # AWS ECS 任务元数据
    "169.254.169.123",  # Amazon Time Sync
    "169.254.0.23",  # 腾讯云
    "[fd00:ec2::254]",  # AWS IMDS IPv6
    "[fd20:ce::254]",  # GCP IPv6-only 实例的元数据端点
    "[fd00:a9fe:a9fe::1]",  # Akamai / Linode IPv6
    "100.100.100.200",  # 阿里云
    "168.63.129.16",  # Azure WireServer
    "192.0.0.192",  # Oracle 旧版
    "[::ffff:100.100.100.200]",  # IPv4 映射写法也一样
]
METADATA_NAMES = [
    "metadata.google.internal",
    "metadata.tencentyun.com",
    "instance-data",
    "METADATA.google.internal.",
]


@pytest.mark.parametrize("host", METADATA_ADDRESSES)
def test_cloud_metadata_addresses_are_refused_even_inside_allowed_ranges(host):
    url = f"http://{host}/latest/meta-data"
    assert wires.check_base_url(url) == "base_url_scheme"
    assert ai_catalog.url_refusal(url) == "insecure_url"
    with pytest.raises(ValueError):
        netpolicy.pin_url(url)
    with pytest.raises(ValueError):
        netpolicy.pinned_connection(url, 1)


@pytest.mark.parametrize("name", METADATA_NAMES)
def test_cloud_metadata_hostnames_are_refused_without_asking_dns(monkeypatch, name):
    def boom(host):
        raise AssertionError("元数据主机名不该走到 DNS")

    monkeypatch.setattr(netpolicy, "resolve_host", boom)
    url = f"http://{name}/computeMetadata/v1/"
    assert wires.check_base_url(url) == "base_url_scheme"
    assert ai_catalog.url_refusal(url) == "insecure_url"


def test_a_hostname_resolving_to_a_metadata_address_is_refused(monkeypatch):
    monkeypatch.setattr(
        netpolicy,
        "resolve_host",
        lambda host: ["fd00:ec2::254"] if host == "lab.local" else ["10.0.0.5", "100.100.100.200"],
    )
    for name in ("lab.local", "sneaky.lab"):
        netpolicy.clear_cache()
        assert wires.check_base_url(f"http://{name}") == "base_url_scheme", name
        with pytest.raises(ValueError):
            netpolicy.pin_url(f"http://{name}/x")
    # 紧邻的合法地址不受影响
    assert wires.check_base_url("http://100.100.100.201") is None
    assert wires.check_base_url("http://[fd00:ec2::255]") is None
    assert wires.check_base_url("http://[fd20:ce::255]") is None
    assert wires.check_base_url("http://[fd00:a9fe:a9fe::2]") is None
    assert wires.check_base_url("http://100.64.0.1") is None


@pytest.mark.parametrize("name", METADATA_NAMES)
def test_metadata_hostnames_stay_refused_even_if_dns_says_private(monkeypatch, name):
    monkeypatch.setattr(netpolicy, "resolve_host", lambda host: ["10.0.0.5"])
    url = f"http://{name}/x"
    assert wires.check_base_url(url) == "base_url_scheme"
    with pytest.raises(ValueError):
        netpolicy.pin_url(url)
    with pytest.raises(ValueError):
        netpolicy.pinned_connection(url, 1)
