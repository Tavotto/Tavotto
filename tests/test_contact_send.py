"""「联系作者」客户端（`engine/contactsend.py` + `engine/contactinbox.py`，ADR 0145）对契约 v1 的验收。

对端是 `tests/support/contact_fakes.py` 的本地模拟（HTTPS 回环 + 每会话现造的私有 CA）；真服务器、真 COS、真机
WebView 联调不在这里（NOT_RUN）。

判据的主语：
* 「没联网」= 模拟服务端 `calls` 的长度——服务端那一侧收到了什么，不是客户端自己说没发；
* 「线路顺序 / 换线路」= 两台模拟服务端各自收到的请求；
* 「没泄漏」= 状态对象、日志（caplog 全级别）里搜金丝雀，以及落盘文件的内容；
* 「令牌落盘」= 数据目录里的文件内容与权限位，不是模块变量。
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import stat
import sys
import threading
import time

import pytest

from support.contact_fakes import FakeContactServer
from support.tls_certs import make_pki
from tavotto.engine import brand, contactinbox, contactsend, diagsend, tlstrust

CANARY_BODY = "CANARY_BODY_Xk29_user_text"
CANARY_MAIL = "canary.Zr41@example.org"
CANARY_SUBJECT = "CANARY_SUBJECT_Lm03"
PNG = b"\x89PNG\r\n\x1a\n" + b"0123456789abcdef" * 8


# --------------------------------------------------------------------------- 装置
@pytest.fixture(scope="module")
def pki_files(tmp_path_factory):
    return make_pki().write(tmp_path_factory.mktemp("contact-pki"))


def _server_tls(pki_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    return ctx


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    # conftest 给整个测试进程设了 TAVOTTO_NO_CONTACT=1；这里摘掉它让“没设任何开关”的默认态可测。
    # 真连生产主机由 conftest 的会话级探针拦下并判红。
    for name in (
        contactsend.ENV_HARD_OFF,
        contactsend.ENV_ENDPOINT,
        contactsend.ENV_REGION,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    sleeps: list[float] = []
    monkeypatch.setattr(contactsend, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.sleeps = sleeps  # type: ignore[attr-defined]
    contactsend.reset_for_tests()
    monkeypatch.setattr(contactsend, "_sleep", lambda s: sleeps.append(s))
    yield
    contactsend.reset_for_tests()


@pytest.fixture
def srv(pki_files):
    with FakeContactServer(_server_tls(pki_files)) as s:
        yield s


@pytest.fixture
def srv2(pki_files, srv):
    # 第二条线路背后是同一个后端（Worker 只转发），共用信件状态
    with FakeContactServer(_server_tls(pki_files), share_with=srv) as s:
        yield s


@pytest.fixture
def trust(pki_files, monkeypatch):
    monkeypatch.setattr(
        tlstrust,
        "client_context",
        lambda: ssl.create_default_context(cafile=str(pki_files["ca"])),
    )


@pytest.fixture
def on(srv, trust, monkeypatch):
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(contactsend.ENV_ENDPOINT, srv.base)
    return srv


def meta(**over) -> dict:
    base = {
        "locale": "zh-CN",
        "subject": CANARY_SUBJECT,
        "body": CANARY_BODY,
        "reply_email": "",
        "report_id": "",
    }
    return {**base, **over}


def wait_for(states: set[str], timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    st = contactsend.status()
    while time.monotonic() < deadline:
        st = contactsend.status()
        if st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(f"没等到 {states}: {st}")


# --------------------------------------------------------------------------- 开关与线路
def test_default_process_is_off_without_any_switch_touched(monkeypatch):
    """conftest 硬关：没设开关的测试进程里入口不出现，也不可能连生产。"""
    monkeypatch.setenv(contactsend.ENV_HARD_OFF, "1")
    assert contactsend.resolve_targets() == []
    assert contactsend.capability() == {"enabled": False}
    with pytest.raises(contactsend.ContactError) as e:
        contactsend.start(meta(), [])
    assert (e.value.code, e.value.status) == ("disabled", 404)


def test_builtin_routes_are_exactly_the_two_whitelisted_hosts():
    hosts = {t.host for t in contactsend.resolve_targets()}
    assert hosts == {"tavotto.cn", "api.tavotto.com"} == set(contactsend.CONTACT_HOSTS)
    assert all(t.port == 443 and not t.loopback for t in contactsend.resolve_targets())


def test_hard_switch_removes_the_entry(monkeypatch):
    monkeypatch.setenv(contactsend.ENV_HARD_OFF, "1")
    assert not contactsend.enabled()


@pytest.mark.parametrize(
    ("region", "locale", "first"),
    [
        ("cn", None, "tavotto.cn"),  # 壳说大陆
        ("", "zh-CN", "tavotto.cn"),  # 无壳（pip）+ 简体中文界面
        ("global", "zh-CN", "tavotto.cn"),  # 壳没算界面语言：global 不否决 zh-CN
        ("global", "en-US", "api.tavotto.com"),
        ("", None, "api.tavotto.com"),
        ("", "en-US", "api.tavotto.com"),
    ],
)
def test_route_order_follows_the_region_signal(monkeypatch, region, locale, first):
    if region:
        monkeypatch.setenv(contactsend.ENV_REGION, region)
    order = [t.host for t in contactsend.resolve_targets(locale)]
    assert order[0] == first
    assert sorted(order) == sorted(contactsend.CONTACT_HOSTS)  # 两条线路永远都在


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil.example",
        "https://tavotto.cn,https://evil.example",  # 混进一个非白名单：整个功能关闭，不回退内置线路
        "http://tavotto.cn",
        "https://tavotto.cn:8443",
        "https://user@tavotto.cn",
        "https://tavotto.cn/v1",
        "https://127.0.0.1:9",  # 回环没有开发开关
    ],
)
def test_endpoint_override_outside_the_whitelist_disables_the_feature(monkeypatch, raw):
    monkeypatch.setenv(contactsend.ENV_ENDPOINT, raw)
    assert contactsend.resolve_targets() == []


def test_region_judgement_is_not_reimplemented_in_python():
    """“大陆环境”的判据唯一出处是壳的 app_update.rs；Python 侧不许出现第二份时区 / 语言表。"""
    src = (
        contactsend.__file__ and open(contactsend.__file__, encoding="utf-8").read()  # noqa: SIM115
    )
    assert "Asia/Shanghai" not in src and "Asia/Chongqing" not in src
    assert contactsend.ENV_REGION == "TAVOTTO_NETWORK_REGION"


# --------------------------------------------------------------------------- 本机输入校验
@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"body": ""}, "body_required"),
        ({"body": "   \n"}, "body_required"),
        ({"body": "x" * 5001}, "body_too_long"),
        ({"subject": "s" * 121}, "subject_too_long"),
        ({"reply_email": "a@b@c"}, "email_invalid"),
        ({"reply_email": "a b@c.com"}, "email_invalid"),
        ({"reply_email": "a@b.com,c@d.com"}, "email_invalid"),
        ({"reply_email": "<a@b.com>"}, "email_invalid"),
        ({"reply_email": "@b.com"}, "email_invalid"),
        ({"reply_email": "a@b"}, "email_invalid"),  # 域名必须含「.」
        ({"reply_email": 'a"b@c.com'}, "email_invalid"),
        ({"reply_email": "a;b@c.com"}, "email_invalid"),
        ({"reply_email": "a(b)@c.com"}, "email_invalid"),
        ({"reply_email": "用户@c.com"}, "email_invalid"),  # 只收可打印 ASCII
        ({"report_id": "TVD-nope"}, "report_invalid"),
    ],
)
def test_local_validation_rejects_before_any_network(on, over, code):
    with pytest.raises(contactsend.ContactError) as e:
        contactsend.start(meta(**over), [])
    assert (e.value.code, e.value.status) == (code, 422)
    assert on.calls == []


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([("a.exe", PNG)], "attachment_type"),
        ([("a.png", b"not a png at all")], "attachment_mismatch"),
        ([("a.pdf", PNG)], "attachment_mismatch"),  # 扩展名说 pdf，魔数是 png
        ([("a.png", b"")], "attachment_empty"),
        ([(f"{i}.png", PNG) for i in range(6)], "attachments_limit"),
        ([("a.png", PNG + b"\0" * (21 * 1024 * 1024))], "attachments_too_large"),
        (
            [(f"{i}.png", PNG + b"\0" * (5 * 1024 * 1024)) for i in range(5)],
            "attachments_too_large",
        ),
    ],
)
def test_attachment_rules_are_enforced_locally(on, files, code):
    with pytest.raises(contactsend.ContactError) as e:
        contactsend.start(meta(), files)
    assert e.value.code == code
    assert on.calls == []


def test_every_whitelisted_extension_with_its_magic_is_accepted():
    cases = {
        "a.png": PNG,
        "a.jpg": b"\xff\xd8\xff\xe0" + b"0" * 8,
        "a.jpeg": b"\xff\xd8\xff\xe0" + b"0" * 8,
        "a.gif": b"GIF89a" + b"0" * 8,
        "a.webp": b"RIFF\0\0\0\0WEBPVP8 ",
        "a.pdf": b"%PDF-1.7\n",
        "a.docx": b"PK\x03\x04" + b"0" * 8,
        "a.xlsx": b"PK\x03\x04" + b"0" * 8,
        "a.pptx": b"PK\x03\x04" + b"0" * 8,
    }
    assert set(cases) == {f"a{e}" for e in contactsend.TYPE_BY_EXT}
    for name, data in cases.items():
        att = contactsend.clean_attachment(name, data)
        assert att.content_type == contactsend.TYPE_BY_EXT["." + name.rsplit(".", 1)[1]]
    assert len(set(contactsend.TYPE_BY_EXT.values())) == 8  # 契约白名单恰好八种


def test_attachment_name_loses_path_and_dangerous_characters():
    att = contactsend.clean_attachment('../../etc/pa"ss<>wd/my shot.png', PNG)
    assert att.name == "my shot.png"
    assert att.wire_name == "attachment.png"  # multipart 头里永远是 ASCII 的固定名


# --------------------------------------------------------------------------- 发送
def test_send_roundtrip_with_attachments_matches_the_contract(on, caplog):
    caplog.set_level(logging.DEBUG)
    start = contactsend.start(
        meta(reply_email=CANARY_MAIL, report_id="TVD-AAAA-BBBB-CCCC-DDDD"),
        [("shot.png", PNG), ("note.pdf", b"%PDF-1.4 hello")],
    )
    assert start["state"] == "sending"
    st = wait_for({"done", "failed"})
    assert st["state"] == "done", st

    assert on.paths() == [
        "/v1/messages/init",
        "/v1/local-upload",
        "/v1/local-upload",
        "/v1/messages/complete",
    ]
    init = on.calls[0]
    assert init["headers"]["tavotto-diagnostics-api"] == "1"
    req = init["json"]
    assert req["locale"] == "zh-CN" and req["body"] == CANARY_BODY
    assert req["subject"] == CANARY_SUBJECT and req["reply_email"] == CANARY_MAIL
    assert req["report_id"] == "TVD-AAAA-BBBB-CCCC-DDDD"
    assert req["route"] in ("cn", "global") and req["os"] and req["arch"] and req["distribution"]
    assert [(a["name"], a["size"]) for a in req["attachments"]] == [
        ("shot.png", len(PNG)),
        ("note.pdf", len(b"%PDF-1.4 hello")),
    ]
    assert req["attachments"][0]["content_type"] == "image/png"
    for up in (on.calls[1], on.calls[2]):
        assert up["field_order"] == ["key", "policy", "file"]  # 授权给的顺序，文件字段在最后
        assert 'filename="attachment.' in up["file_head"]  # 用户的文件名不进 multipart 头
    msg = on.only_message()
    assert msg.uploads[0] == PNG and msg.state == "new"
    assert st["message_id"] == msg.mid and st["route"] in ("cn", "global")

    # 状态对象里没有令牌、邮箱、正文
    blob = json.dumps(st)
    for secret in (msg.reply_token, msg.complete_token, CANARY_MAIL, CANARY_BODY, CANARY_SUBJECT):
        assert secret not in blob
    # 日志里也没有（含 DEBUG）
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (msg.reply_token, msg.complete_token, CANARY_MAIL, CANARY_BODY, CANARY_SUBJECT):
        assert secret not in logs
    assert logs.count("联系作者") >= 1


def test_send_without_attachments_still_calls_complete(on):
    contactsend.start(meta(), [])
    assert wait_for({"done", "failed"})["state"] == "done"
    assert on.paths() == ["/v1/messages/init", "/v1/messages/complete"]


def test_optional_fields_are_omitted_not_blanked(on):
    contactsend.start(meta(subject="", reply_email="", report_id=""), [])
    wait_for({"done"})
    req = on.calls[0]["json"]
    assert "reply_email" not in req and "report_id" not in req
    assert req["subject"] == ""


def test_double_click_is_refused_while_sending(on):
    ev = threading.Event()
    on.hold_early("init", ev)
    contactsend.start(meta(), [])
    with pytest.raises(contactsend.ContactError) as e:
        contactsend.start(meta(), [])
    assert (e.value.code, e.value.status) == ("send_in_progress", 409)
    ev.set()
    assert wait_for({"done", "failed"})["state"] == "done"


# --------------------------------------------------------------------------- 回信令牌落盘
def test_reply_token_is_stored_0600_and_never_in_status_or_listing(on):
    contactsend.start(meta(reply_email=CANARY_MAIL), [("a.png", PNG)])
    st = wait_for({"done"})
    msg = on.only_message()
    path = contactinbox.path()
    raw = path.read_text(encoding="utf-8")
    rec = json.loads(raw)["messages"][0]
    assert rec["message_id"] == msg.mid and rec["reply_token"] == msg.reply_token
    assert rec["route"] in ("cn", "global") and rec["sent_at"] > 0 and rec["delivered"] is True
    # 用户写的东西一样都没落盘
    for secret in (CANARY_BODY, CANARY_MAIL, CANARY_SUBJECT, msg.complete_token):
        assert secret not in raw
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    listing = json.dumps(contactinbox.public_messages())
    assert msg.reply_token not in listing and msg.mid in listing
    assert st["message_id"] == msg.mid


def test_failed_send_with_unconfirmed_complete_keeps_the_token_but_hides_the_letter(on):
    """complete 的响应丢光了：服务端可能已收下。令牌先存下（等轮询证实），但在“我的来信”里不显示未证实的。"""
    on.drop("complete", times=4)
    contactsend.start(meta(), [])
    st = wait_for({"failed"})
    assert st["code"] == "network" and st["retryable"] is True
    assert contactinbox.public_messages() == []
    (rec,) = contactinbox.pollable()
    assert rec["delivered"] is False
    # 轮询证实了服务端有这封信 → 转正
    on.only_message().state = "new"
    contactsend.poll_once(force=True)
    assert [m["message_id"] for m in contactinbox.public_messages()] == [rec["message_id"]]


def test_undelivered_records_expire_and_delivered_ones_last_a_year(monkeypatch, tmp_path):
    mid, tok = "TVM-AAAA-BBBB-CCCC-DDDD", "a" * 64
    t = [1_000_000.0]
    monkeypatch.setattr(contactinbox, "_now", lambda: t[0])
    contactinbox.add(mid, tok, "cn", delivered=False)
    contactinbox.add("TVM-EEEE-FFFF-GGGG-HHHH", "b" * 64, "global", delivered=True)
    assert len(contactinbox.pollable()) == 2
    t[0] += contactinbox.UNDELIVERED_KEEP_S + 1
    assert [r["message_id"] for r in contactinbox.pollable()] == ["TVM-EEEE-FFFF-GGGG-HHHH"]
    t[0] += contactinbox.RETENTION_DAYS * 86400
    assert contactinbox.pollable() == []
    assert json.loads(contactinbox.path().read_text())["messages"] == []  # 文件里也删了


# --------------------------------------------------------------------------- 选路与换线路
@pytest.fixture
def two(srv, srv2, trust, monkeypatch):
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(contactsend.ENV_ENDPOINT, f"{srv.base},{srv2.base}")
    return srv, srv2


def test_first_route_is_tried_first_and_the_second_is_left_alone(two):
    a, b = two
    contactsend.start(meta(), [])
    assert wait_for({"done"})["state"] == "done"
    assert a.count("/v1/messages/init") == 1 and b.calls == []


@pytest.mark.parametrize("how", ["drop", "5xx"])
def test_unreachable_first_route_falls_over_to_the_second(two, how):
    a, b = two
    if how == "drop":
        a.drop("init")
    else:
        a.fail("init", 503, "storage_unavailable")
    contactsend.start(meta(), [("a.png", PNG)])
    assert wait_for({"done", "failed"})["state"] == "done"
    assert a.count("/v1/messages/init") == 1
    assert b.paths() == ["/v1/messages/init", "/v1/local-upload", "/v1/messages/complete"]
    # 接下来的步骤跟着作答的那条线路走，不回头找失败的那条
    assert a.count("/v1/messages/complete") == 0


def test_complete_failover_replays_with_the_same_token(two):
    a, b = two
    a.drop_after("complete")  # 动作已执行、响应丢了
    contactsend.start(meta(), [])
    assert wait_for({"done", "failed"})["state"] == "done"
    assert a.count("/v1/messages/complete") == 1 and b.count("/v1/messages/complete") == 1
    assert a.only_message().complete_runs == 2  # 同一个 complete_token 在另一条线路上幂等重放


def test_a_definite_server_answer_does_not_switch_routes(two):
    a, b = two
    a.fail("init", 429, "rate_limited")
    contactsend.start(meta(), [])
    st = wait_for({"failed"})
    assert (st["code"], st["retryable"]) == ("rate_limited", True)
    assert b.calls == []  # 429 是服务端明确的答复，不去另一条线路绕过限流


def test_both_routes_down_reports_network(two):
    a, b = two
    a.drop("init")
    b.drop("init")
    contactsend.start(meta(), [])
    assert wait_for({"failed"})["code"] == "network"


@pytest.mark.parametrize(
    ("status", "server_code", "expected", "retryable"),
    [
        (422, "invalid_email", "invalid_email", False),
        (422, "attachment_invalid", "attachment_invalid", True),
        (422, "report_not_found", "report_not_found", True),
        (422, "bad_request", "client_error", False),
        (413, "payload_too_large", "too_large", False),
        (410, "expired", "expired", True),
        (404, "not_found", "not_found", True),
        (429, "rate_limited", "rate_limited", True),
        (415, "x", "client_error", False),
    ],
)
def test_server_errors_are_folded_into_the_closed_failure_set(
    on, status, server_code, expected, retryable
):
    on.fail("init", status, server_code)
    contactsend.start(meta(), [])
    st = wait_for({"failed"})
    assert (st["code"], st["retryable"]) == (expected, retryable)
    assert st["code"] in contactsend.FAILURES
    assert contactsend.status().get("message_id") is None


def test_only_the_top_level_error_shape_is_understood():
    class R:
        body = b'{"ok": false, "code": "invalid_email", "error": "x"}'

    class N:  # 嵌套形状不是服务端的实际行为：不认，免得两种都认掩盖契约漂移
        body = b'{"error": {"code": "invalid_email", "message": "x"}}'

    assert contactsend._server_code(R) == "invalid_email"
    assert contactsend._server_code(N) == ""


# --------------------------------------------------------------------------- 目的地不由响应决定
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v1/local-upload",
        "http://127.0.0.1/v1/local-upload",
        "https://tavotto-1250000000.cos.ap-guangzhou.myqcloud.com/",  # 回环开发模式只许本端点的 local-upload
        "https://cos.accelerate.myqcloud.com/",
    ],
)
def test_upload_url_outside_the_pinned_destination_is_refused(on, url):
    on.upload_url = url
    contactsend.start(meta(), [("a.png", PNG)])
    st = wait_for({"failed"})
    assert (st["code"], st["retryable"]) == ("upload_url_rejected", False)
    assert on.count("/v1/local-upload") == 0
    assert on.count("/v1/messages/complete") == 0


def test_production_cos_host_shape_is_the_diagsend_one():
    t = contactsend.resolve_targets()[0]
    ok = "https://tavotto-msg-1250000000.cos.ap-guangzhou.myqcloud.com/"
    assert diagsend.validate_upload_url(ok, t) == ok
    for bad in (
        "https://cos.accelerate.myqcloud.com/",
        "https://tavotto-msg-1250000000.cos.ap-guangzhou.myqcloud.com:8443/",
        "https://tavotto-msg-1250000000.cos.ap-guangzhou.myqcloud.com/?x=1",
    ):
        with pytest.raises(diagsend._Failure):
            diagsend.validate_upload_url(bad, t)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_never_followed(two, srv, pki_files, status):
    """urllib 默认会把 301/302/303 的 POST 改成 GET 跟过去（307/308 的 POST 它自己不跟）：每一种都要挡。"""
    a, b = two
    with FakeContactServer(_server_tls(pki_files)) as elsewhere:
        a.redirect("init", elsewhere.base + "/v1/messages/init", status=status)
        contactsend.start(meta(), [])
        st = wait_for({"failed"})
        assert (st["code"], st["retryable"]) == ("redirect_blocked", False)
        assert elsewhere.calls == [] and b.calls == []


def test_api_major_mismatch_is_refused(on, monkeypatch):
    orig = on._send

    def wrong(h, status, obj):
        body = json.dumps(obj).encode()
        h.send_response(status)
        h.send_header("Tavotto-Diagnostics-Api", "2.0.0")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    monkeypatch.setattr(on, "_send", wrong)
    contactsend.start(meta(), [])
    assert wait_for({"failed"})["code"] == "api_incompatible"
    monkeypatch.setattr(on, "_send", orig)


# --------------------------------------------------------------------------- 幂等重试
def test_retry_with_same_content_reuses_the_client_message_id(on):
    on.fail("init", 503, "storage_unavailable")
    on.fail("complete", 429, "rate_limited")
    on.fail("init", 503, "storage_unavailable", times=0)
    files = [("a.png", PNG)]
    contactsend.start(meta(), files)
    # 第一次 init 503 → 只有一台线路 → 失败
    assert wait_for({"failed"})["code"] == "storage_unavailable"
    contactsend.start(meta(), files)
    assert wait_for({"failed"})["code"] == "rate_limited"
    contactsend.start(meta(), files)
    assert wait_for({"done"})["state"] == "done"
    keys = {c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"}
    assert len(keys) == 1  # 三次点击，同一个幂等键
    assert len(on.messages) == 1


def test_changed_content_gets_a_new_client_message_id(on):
    on.fail("init", 503, "storage_unavailable")
    contactsend.start(meta(), [])
    wait_for({"failed"})
    contactsend.start(meta(body=CANARY_BODY + " edited"), [])
    wait_for({"done"})
    keys = [c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"]
    assert len(keys) == 2 and keys[0] != keys[1]


def test_server_closed_session_at_complete_forces_a_new_id_on_retry(on):
    on.fail("complete", 409, "session_closed")
    contactsend.start(meta(), [])
    assert wait_for({"failed"})["code"] == "session_closed"
    contactsend.start(meta(), [])
    wait_for({"done"})
    keys = [c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"]
    assert keys[0] != keys[1]


def test_lost_complete_response_is_replayed_inside_one_send(on):
    on.drop_after("complete")
    contactsend.start(meta(), [])
    assert wait_for({"done"})["state"] == "done"
    assert on.count("/v1/messages/complete") == 2
    assert on.only_message().complete_runs == 2 and on.only_message().state == "new"


def test_attachment_rejected_by_the_server_is_reported_and_reopens(on):
    on.fail("complete", 422, "attachment_invalid")
    contactsend.start(meta(), [("a.png", PNG)])
    st = wait_for({"failed"})
    assert st["code"] == "attachment_invalid"
    contactsend.start(meta(), [("a.png", PNG)])
    wait_for({"done"})
    keys = [c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"]
    assert keys[0] != keys[1]


# --------------------------------------------------------------------------- 取消
def test_cancel_during_upload_stops_it_and_tells_the_server(on):
    big = PNG + b"\0" * (6 * 1024 * 1024)
    ev = threading.Event()
    on.hold_early("upload", ev)
    contactsend.start(meta(), [("big.png", big)])
    deadline = time.monotonic() + 10
    while contactsend.status()["stage"] != "upload" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert contactsend.status()["stage"] == "upload"
    assert contactsend.cancel()["state"] == "cancelling"
    ev.set()
    st = wait_for({"cancelled", "done", "failed"})
    assert st["state"] == "cancelled"
    assert on.count("/v1/messages/cancel") == 1
    assert on.count("/v1/messages/complete") == 0
    assert on.only_message().state == "cancelled"
    assert contactinbox.pollable() == []  # 没发出去的信不留回信令牌


def test_progress_is_monotonic_and_ends_at_total(on):
    big = PNG + b"\0" * (2 * 1024 * 1024)
    seen: list[int] = []
    contactsend.start(meta(), [("big.png", big)])
    while contactsend.status()["state"] == "sending":
        seen.append(contactsend.status()["sent"])
        time.sleep(0.002)
    st = contactsend.status()
    assert st["state"] == "done" and st["total"] == len(big)
    assert seen == sorted(seen) and st["sent"] <= st["total"]


# --------------------------------------------------------------------------- 回信
def _send_ok(on) -> str:
    contactsend.start(meta(), [])
    wait_for({"done"})
    return on.only_message().mid


def test_polling_fetches_replies_and_marks_unread_then_read(on):
    mid = _send_ok(on)
    assert contactinbox.unread_count() == 0
    on.reply(mid, "收到，下周修复。")
    out = contactsend.poll_once(force=True)
    assert out == {"polled": 1}
    reply_call = [c for c in on.calls if c["path"].endswith("/replies")][-1]
    assert reply_call["headers"]["authorization"] == f"Bearer {on.only_message().reply_token}"
    assert reply_call["headers"]["tavotto-diagnostics-api"] == "1"
    (m,) = contactinbox.public_messages()
    assert m["state"] == "replied" and m["unread"] == 1
    assert m["replies"][0]["body"] == "收到，下周修复。" and m["replies"][0]["read"] is False
    assert contactinbox.unread_count() == 1
    assert contactsend.capability()["unread"] == 1
    # 再问一次不重复
    contactsend.poll_once(force=True)
    assert len(contactinbox.public_messages()[0]["replies"]) == 1
    # 看过即已读；再来一条只算新的
    assert contactinbox.mark_read(mid) is True
    assert contactinbox.unread_count() == 0
    on.reply(mid, "补充一句")
    contactsend.poll_once(force=True)
    assert contactinbox.unread_count() == 1


def test_read_state_survives_a_restart(on):
    mid = _send_ok(on)
    on.reply(mid, "hi")
    contactsend.poll_once(force=True)
    contactinbox.mark_read(mid)
    # “重启”= 重新从文件读：没有任何进程内缓存
    assert contactinbox.unread_count() == 0
    assert contactinbox.public_messages()[0]["replies"][0]["read"] is True


def test_no_records_means_zero_network(on):
    assert contactsend.poll_once(force=True) == {"polled": 0}
    assert on.calls == []


def test_polling_is_throttled_unless_forced(on):
    _send_ok(on)
    n = len(on.calls)
    contactsend.poll_once()  # 第一次
    contactsend.poll_once()  # 30 秒内第二次：不联网
    assert len(on.calls) == n + 1
    assert contactsend.poll_once() == {"polled": 0, "throttled": True}


def test_polling_a_closed_letter_is_skipped(on):
    mid = _send_ok(on)
    on.only_message().state = "closed"
    contactsend.poll_once(force=True)
    n = len(on.calls)
    contactsend.poll_once(force=True)
    assert len(on.calls) == n  # closed 之后不再问
    assert mid in json.dumps(contactinbox.public_messages())


def test_polling_tries_the_recorded_route_first_then_the_other(two):
    a, b = two
    contactsend.start(meta(), [])
    wait_for({"done"})
    mid = a.only_message().mid
    a.drop("replies")
    # 记录里的线路（a，第一条成功的）先问，断线就换 b，同一个令牌
    assert contactsend.poll_once(force=True) == {"polled": 1}
    assert a.count(f"/v1/messages/{mid}/replies") == 1
    assert b.count(f"/v1/messages/{mid}/replies") == 1


def test_poll_failure_never_raises_and_keeps_old_replies(on):
    mid = _send_ok(on)
    on.reply(mid, "keep me")
    contactsend.poll_once(force=True)
    on.drop("replies")
    assert contactsend.poll_once(force=True) == {"polled": 0}
    assert contactinbox.public_messages()[0]["replies"][0]["body"] == "keep me"


def test_forget_removes_only_the_local_record(on):
    mid = _send_ok(on)
    assert contactinbox.forget(mid) is True
    assert contactinbox.public_messages() == []
    assert contactinbox.forget(mid) is False


def test_corrupt_or_foreign_records_are_ignored(tmp_path):
    p = contactinbox.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert contactinbox.public_messages() == []
    p.write_text(
        json.dumps(
            {
                "messages": [
                    {"message_id": "../../x", "reply_token": "nope", "route": "cn", "sent_at": 1}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert contactinbox.pollable() == []


def test_recipient_constant_is_the_brand_one(on):
    assert contactsend.capability()["recipient"] == brand.FEEDBACK_EMAIL == "feedback@tavotto.com"


def test_the_shell_hands_its_region_verdict_to_the_engine():
    """壳（`app_update::detect_order`，ADR 0121 的唯一判据）把结论写进引擎的环境变量；两侧的变量名必须是同一个。"""
    from pathlib import Path

    sidecar = (Path(__file__).resolve().parent.parent / "src-tauri/src/sidecar.rs").read_text(
        encoding="utf-8"
    )
    assert f'.env("{contactsend.ENV_REGION}", region)' in sidecar
    assert "crate::app_update::detect_order(None)" in sidecar
    assert '=> "cn"' in sidecar and '_ => "global"' in sidecar


def test_contact_email_is_one_constant_on_both_sides():
    """`engine/brand.py` 与 `web/src/lib/brand.ts` 的 `FEEDBACK_EMAIL` 是同一个地址（严格同源对，ADR 0145）。"""
    import re
    from pathlib import Path

    brand_ts = (Path(__file__).resolve().parent.parent / "web/src/lib/brand.ts").read_text(
        encoding="utf-8"
    )
    (ts,) = re.findall(r"^export const FEEDBACK_EMAIL = '([^']+)'$", brand_ts, re.M)
    assert ts == brand.FEEDBACK_EMAIL == "feedback@tavotto.com"


def test_a_failed_upload_voids_the_letter_and_the_retry_uses_a_new_id(on):
    """服务端 complete 时附件缺失会把整封信置 rejected：任何上传失败就 cancel，重试换新的 client_message_id。"""
    on.fail("upload", 403, "forbidden")
    contactsend.start(meta(), [("a.png", PNG)])
    assert wait_for({"failed"})["code"] == "upload_denied"
    assert on.count("/v1/messages/cancel") == 1 and on.count("/v1/messages/complete") == 0
    contactsend.start(meta(), [("a.png", PNG)])
    assert wait_for({"done"})["state"] == "done"
    keys = [c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"]
    assert len(keys) == 2 and keys[0] != keys[1]


def test_init_replay_of_a_completed_letter_with_empty_tickets_goes_straight_to_complete(on):
    """complete 的响应丢光 → 用户再试：init 重放 200 + 空票据，附件不重传，complete 回 200 + state，算已发送。"""
    on.drop_after("complete", times=4)
    contactsend.start(meta(), [("a.png", PNG)])
    assert wait_for({"failed"})["code"] == "network"
    uploads = on.count("/v1/local-upload")
    contactsend.start(meta(), [("a.png", PNG)])
    assert wait_for({"done"})["state"] == "done"
    assert on.count("/v1/local-upload") == uploads  # 没有重传
    assert len(on.messages) == 1
    # state 已经是 replied / closed 也算已发送
    on.only_message().state = "replied"
    contactsend.start(meta(body=CANARY_BODY + "2"), [])  # 新稿：新信
    wait_for({"done"})


def test_complete_accepting_replied_and_closed_states(on):
    contactsend.start(meta(), [])
    wait_for({"done"})
    msg = on.only_message()
    for st in ("replied", "closed"):
        msg.state = st
        r = on._do_complete({"message_id": msg.mid, "complete_token": msg.complete_token})
        assert r[0] == 200 and r[1]["state"] == st


def test_init_409_session_closed_restarts_with_a_new_id_once(on):
    on.fail("init", 409, "idempotency_conflict")
    contactsend.start(meta(), [])
    assert wait_for({"done", "failed"})["state"] == "done"
    keys = [c["json"]["client_message_id"] for c in on.calls if c["path"] == "/v1/messages/init"]
    assert len(keys) == 2 and keys[0] != keys[1]
