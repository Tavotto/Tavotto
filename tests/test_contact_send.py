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
    # 第二期：本机留主题 / 正文 / 附件文件名；邮箱、complete_token、附件内容仍然一样都没落盘
    assert (rec["subject"], rec["body"], rec["attachment_names"]) == (
        CANARY_SUBJECT,
        CANARY_BODY,
        ["a.png"],
    )
    for secret in (CANARY_MAIL, msg.complete_token, PNG.hex(), "0123456789abcdef"):
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


# --------------------------------------------------------------------------- 第二期：本机原文、回信 HTML 与附图
from pathlib import Path  # noqa: E402

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "contact" / "reply_sanitized.html").read_text(
    encoding="utf-8"
)
JPG = b"\xff\xd8\xff\xe0" + b"jpegbody" * 4
GIF = b"GIF89a" + b"gifbody!" * 4
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"webpbody" * 2
FOUR = [("image/png", PNG), ("image/jpeg", JPG), ("image/gif", GIF), ("image/webp", WEBP)]
MID = "TVM-ABCD-EFGH-JKMN-PQRS"
TOKEN = "a" * 64


def _asset_dir(mid: str) -> Path:
    return contactinbox.path().parent / contactinbox.ASSET_DIR / mid


def _html_reply(on, mid, assets=FOUR, html=FIXTURE_HTML):
    return on.reply(mid, "纯文字版", html=html, assets=list(assets))


def test_contact_inbox_limits_match_the_contract():
    assert contactinbox.MAX_SUBJECT_CHARS == contactsend.SUBJECT_MAX
    assert contactinbox.MAX_BODY_CHARS == contactsend.BODY_MAX
    assert contactinbox.MAX_ATTACHMENT_NAMES == contactsend.MAX_ATTACHMENTS


def test_add_keeps_the_original_text_capped_and_public_messages_returns_it():
    names = [f"{i}-" + "n" * 200 + ".png" for i in range(7)]
    assert contactinbox.add(
        MID,
        TOKEN,
        "cn",
        delivered=True,
        subject="s" * 500,
        body="b" * 9000,
        attachment_names=names,
    )
    (m,) = contactinbox.public_messages()
    assert (len(m["subject"]), len(m["body"])) == (120, 5000)
    assert len(m["attachment_names"]) == 5
    assert all(len(n) == 120 for n in m["attachment_names"])


def test_add_is_idempotent_and_fills_in_text_the_first_call_lacked():
    contactinbox.add(MID, TOKEN, "cn", delivered=False)
    contactinbox.add(MID, TOKEN, "cn", delivered=True, subject="S", body="B")
    contactinbox.add(MID, TOKEN, "cn", delivered=True, subject="other", body="other")
    (m,) = contactinbox.public_messages()
    assert (m["subject"], m["body"]) == ("S", "B")


def test_old_records_without_the_new_fields_still_load_as_empty():
    p = contactinbox.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = {
        "message_id": MID,
        "reply_token": TOKEN,
        "route": "cn",
        "sent_at": time.time(),
        "delivered": True,
        "state": "",
        "replies": [{"id": "r_AAAA", "created_at": "2026-10-22T08:00:00Z", "body": "hi"}],
        "read": [],
    }
    p.write_text(json.dumps({"version": 1, "messages": [old]}), encoding="utf-8")
    (m,) = contactinbox.public_messages()
    assert (m["subject"], m["body"], m["attachment_names"]) == ("", "", [])
    assert m["replies"][0]["has_html"] is False


def test_a_sent_letter_stores_its_text_and_attachment_names_but_not_the_files(on):
    contactsend.start(meta(), [("shot.png", PNG)])
    wait_for({"done"})
    (m,) = contactinbox.public_messages()
    assert (m["subject"], m["body"], m["attachment_names"]) == (
        CANARY_SUBJECT,
        CANARY_BODY,
        ["shot.png"],
    )


def test_apply_replies_stores_html_and_assets_and_drops_html_over_256kib():
    contactinbox.add(MID, TOKEN, "cn", delivered=True)
    meta_ = [{"n": 0, "content_type": "image/png", "size": 10}]
    big = "<p>" + "x" * contactinbox.MAX_REPLY_HTML_BYTES + "</p>"
    contactinbox.apply_replies(
        MID,
        "replied",
        [
            {"id": "r_OK", "created_at": "t", "body": "b", "html": "<p>hi</p>", "assets": meta_},
            {"id": "r_BIG", "created_at": "t", "body": "b", "html": big, "assets": meta_},
            {"id": "r_TXT", "created_at": "t", "body": "plain"},
        ],
    )
    (m,) = contactinbox.pollable()
    by = {r["id"]: r for r in m["replies"]}
    assert by["r_OK"]["html"] == "<p>hi</p>" and by["r_OK"]["assets"] == meta_
    assert "html" not in by["r_BIG"] and "assets" not in by["r_BIG"]  # 只留纯文字
    assert "html" not in by["r_TXT"]
    assert [r["has_html"] for r in contactinbox.public_messages()[0]["replies"]] == [
        True,
        False,
        False,
    ]
    assert "html" not in json.dumps(contactinbox.public_messages()).replace("has_html", "")


def test_asset_metadata_is_validated_when_stored():
    contactinbox.add(MID, TOKEN, "cn", delivered=True)
    bad = [
        {"n": 0, "content_type": "image/svg+xml", "size": 5},
        {"n": 1, "content_type": "image/png", "size": contactinbox.MAX_ASSET_BYTES + 1},
        {"n": 2, "content_type": "image/png", "size": 5},
        {"n": 2, "content_type": "image/png", "size": 5},
        {"n": 99, "content_type": "image/png", "size": 5},
        {"n": "x", "content_type": "image/png", "size": 5},
    ]
    contactinbox.apply_replies(
        MID,
        "replied",
        [{"id": "r_A", "created_at": "t", "body": "b", "html": "<p/>", "assets": bad}],
    )
    (m,) = contactinbox.pollable()
    assert m["replies"][0]["assets"] == [{"n": 2, "content_type": "image/png", "size": 5}]


def test_html_reply_assets_are_downloaded_0600_into_a_0700_tree(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid)
    contactsend.poll_once(force=True)
    d = _asset_dir(mid) / rid
    assert [(d / str(i)).read_bytes() for i in range(4)] == [b for _, b in FOUR]
    reqs = [c for c in on.calls if "/assets/" in c["path"]]
    assert [c["path"].rsplit("/", 1)[1] for c in reqs] == ["0", "1", "2", "3"]
    assert reqs[0]["headers"]["authorization"] == f"Bearer {on.only_message().reply_token}"
    assert reqs[0]["headers"]["user-agent"] == contactsend._UA
    assert reqs[0]["headers"]["tavotto-diagnostics-api"] == "1"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(d / "0").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(_asset_dir(mid)).st_mode) == 0o700
    # 已下完的不再下
    n = len(on.calls)
    contactsend.poll_once(force=True)
    assert [c["path"] for c in on.calls[n:] if "/assets/" in c["path"]] == []


@pytest.mark.parametrize(
    "ctype, data",
    [
        ("image/jpeg", PNG),  # 头说 jpeg、元数据说 png
        ("image/png", b"X" * len(PNG)),  # 类型对、大小对，只有魔数不符
        ("image/svg+xml", PNG),  # 类型不在四种之内
        ("text/html", b"<html></html>"),
        ("image/png", PNG + b"extra"),  # 大小与元数据不符
        ("image/png", PNG[:-3]),
    ],
)
def test_bad_asset_responses_are_rejected_and_not_saved(on, ctype, data):
    mid = _send_ok(on)
    rid = _html_reply(on, mid, assets=[("image/png", PNG)])
    on.serve_asset_as(rid, 0, ctype, data)
    contactsend.poll_once(force=True)
    assert not (_asset_dir(mid) / rid / "0").exists()
    assert [a["n"] for a in contactinbox.pending_assets(mid)] == [0]


def test_asset_over_10mib_is_refused_even_if_the_metadata_lies(on):
    mid = _send_ok(on)
    big = PNG + b"\0" * (contactinbox.MAX_ASSET_BYTES + 5 - len(PNG))
    rid = on.reply(mid, "b", html="<p/>", assets=[("image/png", PNG)])
    # 元数据声明合法（<=10MiB），实际体超限
    on.serve_asset_as(rid, 0, "image/png", big)
    contactsend.poll_once(force=True)
    assert not (_asset_dir(mid) / rid / "0").exists()
    # 元数据本身超限：存回信时就丢掉这张，更不会去下
    n = len(on.calls)
    rid2 = on.reply(mid, "b", html="<p/>", assets=[("image/png", PNG)])
    on.only_message().replies[-1]["assets"][0]["size"] = contactinbox.MAX_ASSET_BYTES + 1
    contactsend.poll_once(force=True)
    assert not [c for c in on.calls[n:] if rid2 in c["path"] and "/assets/" in c["path"]]


def test_failed_asset_downloads_retry_next_round_without_blocking_other_replies(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid, assets=FOUR[:2])
    on.fail("asset", 500, "internal", times=2)  # 两条线路各一次？只有一条线路：前两张请求各失败一次
    contactsend.poll_once(force=True)
    d = _asset_dir(mid) / rid
    assert not (d / "0").exists() and not (d / "1").exists()
    on.reply(mid, "第二封纯文字")
    contactsend.poll_once(force=True)  # 下一轮：补齐，并且新回信也收到了
    assert (d / "0").read_bytes() == PNG and (d / "1").read_bytes() == JPG
    assert len(contactinbox.public_messages()[0]["replies"]) == 2


def test_asset_404_skips_that_image_only(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid, assets=FOUR[:3])
    on.fail("asset", 404, "not_found", times=1)
    contactsend.poll_once(force=True)
    d = _asset_dir(mid) / rid
    assert not (d / "0").exists() and (d / "1").exists() and (d / "2").exists()


@pytest.mark.parametrize("status", [301, 307])
def test_asset_redirects_are_never_followed(two, status):
    a, b = two
    contactsend.start(meta(), [])
    wait_for({"done"})
    mid = a.only_message().mid
    rid = _html_reply(a, mid, assets=FOUR[:1])
    a.redirect("asset", f"{b.base}/v1/messages/{mid}/replies/{rid}/assets/0", status=status)
    contactsend.poll_once(force=True)
    assert not (_asset_dir(mid) / rid / "0").exists()
    assert b.calls == []  # 重定向目标一次都没被访问


def test_assets_are_only_fetched_from_the_whitelisted_hosts(on, monkeypatch):
    mid = _send_ok(on)
    _html_reply(on, mid, assets=FOUR[:1])
    monkeypatch.setenv(contactsend.ENV_ENDPOINT, "https://evil.example.com")
    n = len(on.calls)
    assert contactsend.poll_once(force=True) == {"polled": 0}
    assert len(on.calls) == n and not _asset_dir(mid).exists()


def test_a_closed_letter_still_gets_its_missing_assets(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid, assets=FOUR[:1])
    on.fail("asset", 500, "internal", times=1)
    contactsend.poll_once(force=True)
    on.only_message().state = "closed"
    contactsend.poll_once(force=True)  # 状态变 closed 的这一轮：问到了 closed，附图补下
    contactsend.poll_once(force=True)
    assert (_asset_dir(mid) / rid / "0").read_bytes() == PNG


def test_a_closed_letter_with_all_assets_is_not_polled_again(on):
    mid = _send_ok(on)
    _html_reply(on, mid, assets=FOUR[:1])
    on.only_message().state = "closed"
    contactsend.poll_once(force=True)
    n = len(on.calls)
    contactsend.poll_once(force=True)
    assert len(on.calls) == n


def test_per_round_asset_budget_caps_the_work_and_the_next_round_continues(on, monkeypatch):
    mid = _send_ok(on)
    rid = _html_reply(on, mid)
    monkeypatch.setattr(contactsend, "ASSET_ROUND_REQUESTS", 2)
    contactsend.poll_once(force=True)
    d = _asset_dir(mid) / rid
    assert sorted(p.name for p in d.iterdir()) == ["0", "1"]
    contactsend.poll_once(force=True)
    assert sorted(p.name for p in d.iterdir()) == ["0", "1", "2", "3"]


def test_forget_and_retention_delete_the_asset_tree(on, monkeypatch):
    mid = _send_ok(on)
    _html_reply(on, mid, assets=FOUR[:2])
    contactsend.poll_once(force=True)
    assert _asset_dir(mid).is_dir()
    assert contactinbox.forget(mid) is True
    assert not _asset_dir(mid).exists()
    # 365 天保留期到了：下一次读（prune）连带删
    mid2 = _send_ok_again(on)
    _html_reply(on, mid2, assets=FOUR[:1])
    contactsend.poll_once(force=True)
    assert _asset_dir(mid2).is_dir()
    real = time.time()
    monkeypatch.setattr(contactinbox, "_now", lambda: real + 366 * 86400)
    assert contactinbox.public_messages() == []
    assert not _asset_dir(mid2).exists()


def _send_ok_again(on) -> str:
    contactsend.start(meta(body=CANARY_BODY + " 2"), [])
    wait_for({"done"})
    return [m for m in on.messages.values() if m.mid != next(iter(on.messages))][-1].mid


@pytest.mark.parametrize("bad", ["../x", "TVM-ABCD", "a/b"])
def test_asset_paths_reject_traversal(bad):
    assert contactinbox.asset_file(bad, "r_A", 0) is None
    assert contactinbox.asset_file(MID, bad, 0) is None
    assert contactinbox.asset_file(MID, "r_A", -1) is None
    assert contactinbox.save_asset(bad, "r_A", 0, PNG) is False


def test_reply_doc_has_csp_inlines_every_asset_and_uses_placeholders_for_missing(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid)
    on.serve_asset_as(rid, 3, "image/png", b"")  # 第 4 张永远下不来 -> 占位
    contactsend.poll_once(force=True)
    doc = contactinbox.reply_doc(mid, rid)
    assert doc is not None and doc.startswith("<!doctype html>")
    assert (
        f'<meta http-equiv="Content-Security-Policy" content="{contactinbox.REPLY_DOC_CSP}">' in doc
    )
    assert (
        contactinbox.REPLY_DOC_CSP == "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )
    assert doc.index("Content-Security-Policy") < doc.index("<style>") < doc.index("<table")
    assert "asset:" not in doc
    assert '<base target="_blank">' in doc
    import base64

    for ctype, data in FOUR[:3]:
        assert f"data:{ctype};base64,{base64.b64encode(data).decode()}" in doc
    assert contactinbox._PLACEHOLDER in doc
    assert 'alt="Tavotto"' in doc  # alt 仍在
    for rule in ("#fff", "max-width:100%!important", "pre-wrap", "overflow-wrap:anywhere"):
        assert rule in doc


def test_reply_doc_is_none_without_html_or_for_unknown_ids(on):
    mid = _send_ok(on)
    on.reply(mid, "plain")
    contactsend.poll_once(force=True)
    rid = contactinbox.public_messages()[0]["replies"][0]["id"]
    assert contactinbox.reply_doc(mid, rid) is None
    assert contactinbox.reply_doc(mid, "r_NOPE") is None
    assert contactinbox.reply_doc("TVM-0000-0000-0000-0000", rid) is None


def test_replies_response_bigger_than_the_default_cap_is_not_truncated(on):
    mid = _send_ok(on)
    html = "<p>" + "字" * 60000 + "</p>"  # ~180 KB，远超默认 64 KiB 响应上限
    on.reply(mid, "b", html=html, assets=[])
    contactsend.poll_once(force=True)
    assert contactinbox.pollable()[0]["replies"][0]["html"] == html


# --------------------------------------------------------------------------- Codex 评审 #1344：上限与可重试清理
def _mid(i: int) -> str:
    """第 i 个合规信件编号（Crockford 字母表）。"""
    alpha = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    t = "".join(alpha[(i >> (5 * k)) & 31] for k in range(4))
    return f"TVM-{t}-ABCD-EFGH-JKMN"


def _boom_rmtree(monkeypatch, calls=None):
    def boom(path, *a, **k):
        if calls is not None:
            calls.append(Path(path).name)
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(contactinbox.shutil, "rmtree", boom)


def test_rmtree_failure_is_rediscovered_by_the_sweep_on_every_load_until_it_works(
    on, monkeypatch, caplog
):
    mid = _send_ok(on)
    _html_reply(on, mid, assets=FOUR[:2])
    contactsend.poll_once(force=True)
    d = _asset_dir(mid)
    assert d.is_dir()
    real = contactinbox.shutil.rmtree
    _boom_rmtree(monkeypatch)
    with caplog.at_level(logging.WARNING):
        assert contactinbox.forget(mid) is True  # 记录照删
    assert contactinbox.public_messages() == []
    assert d.is_dir()  # 图还在盘上，没有任何“凭据”文件……
    assert "pending_asset_deletes" not in contactinbox.path().read_text(encoding="utf-8")
    assert "PermissionError" in caplog.text and "antivirus" not in caplog.text
    assert str(d) not in caplog.text
    for _ in range(3):  # ……连续失败也不会丢：每次加载都再试
        contactinbox.public_messages()
        assert d.is_dir()
    monkeypatch.setattr(contactinbox.shutil, "rmtree", real)
    contactinbox.public_messages()  # 下一轮读：重试成功
    assert not d.exists()


def test_partial_rmtree_failure_is_retried_and_legacy_pending_key_is_ignored(on, monkeypatch):
    mid = _send_ok(on)
    _html_reply(on, mid, assets=FOUR[:1])
    contactsend.poll_once(force=True)
    real = contactinbox.shutil.rmtree

    def half(path, *a, **k):  # 删了一半就抛
        for f in Path(path).rglob("*"):
            if f.is_file():
                f.unlink()
        raise PermissionError("x")

    monkeypatch.setattr(contactinbox.shutil, "rmtree", half)
    contactinbox.forget(mid)
    assert _asset_dir(mid).exists()
    # 旧版本写过的 pending_asset_deletes 键：容忍并忽略（脏值也不会被当目录删）
    raw = json.loads(contactinbox.path().read_text(encoding="utf-8"))
    raw["pending_asset_deletes"] = ["../x", mid, 5]
    contactinbox.path().write_text(json.dumps(raw), encoding="utf-8")
    assert "pending_asset_deletes" not in contactinbox._load_locked()
    monkeypatch.setattr(contactinbox.shutil, "rmtree", real)
    contactinbox.public_messages()
    assert not _asset_dir(mid).exists()


def test_sweep_only_touches_well_named_orphans_and_orphan_reply_dirs(on):
    mid = _send_ok(on)
    rid = _html_reply(on, mid, assets=FOUR[:1])
    contactsend.poll_once(force=True)
    root = contactinbox.path().parent / contactinbox.ASSET_DIR
    orphan = root / _mid(1)
    (orphan / "r_X").mkdir(parents=True)
    (orphan / "r_X" / "0").write_bytes(b"x")
    stray_reply = root / mid / "r_GONE"  # 现存记录里已不存在的回信目录
    stray_reply.mkdir()
    (stray_reply / "0").write_bytes(b"x")
    foreign = [root / "notes", root / "TVM-lower-case-nope", root / mid / "weird-name"]
    for f in foreign:
        f.mkdir()
        (f / "keep.txt").write_text("keep", encoding="utf-8")
    contactinbox.public_messages()
    assert not orphan.exists() and not stray_reply.exists()  # 合规孤儿被扫掉
    assert (root / mid / rid / "0").read_bytes() == PNG  # 现存回信的图不动
    assert all((f / "keep.txt").exists() for f in foreign)  # 不合规名字不碰


def test_sweep_is_bounded_per_load_and_rotates_so_nothing_is_starved(on, monkeypatch):
    root = contactinbox.path().parent / contactinbox.ASSET_DIR
    mids = [_mid(i) for i in range(25)]
    for m in mids:
        (root / m).mkdir(parents=True)
    calls: list[str] = []
    real = contactinbox.shutil.rmtree
    _boom_rmtree(monkeypatch, calls)
    contactinbox.public_messages()
    assert len(calls) == contactinbox.MAX_ASSET_SWEEP == 20  # 每次加载有界
    contactinbox.public_messages()
    assert set(calls) == set(mids)  # 起点轮转：持续失败的前 20 个不会饿死后 5 个
    monkeypatch.setattr(contactinbox.shutil, "rmtree", real)
    for _ in range(2):
        contactinbox.public_messages()
    assert not any((root / m).exists() for m in mids)


def _tiny_quota(monkeypatch, per_message, total):
    monkeypatch.setattr(contactinbox, "MAX_ASSET_BYTES_PER_MESSAGE", per_message)
    monkeypatch.setattr(contactinbox, "MAX_ASSET_BYTES_TOTAL", total)


def test_constants_of_the_asset_quota_and_render_cap():
    assert contactinbox.MAX_ASSET_BYTES_PER_MESSAGE == 100 * 1024 * 1024
    assert contactinbox.MAX_ASSET_BYTES_TOTAL == 500 * 1024 * 1024
    assert contactinbox.MAX_REPLY_DOC_IMAGE_BYTES == 24 * 1024 * 1024


def test_per_message_quota_skips_further_downloads_for_good_and_marks_the_asset(on, monkeypatch):
    mid = _send_ok(on)
    size = len(PNG)
    _tiny_quota(monkeypatch, size * 2, 10**9)  # 每封只放得下两张
    rid = _html_reply(on, mid, assets=[("image/png", PNG)] * 4)
    contactsend.poll_once(force=True)
    d = _asset_dir(mid) / rid
    assert sorted(p.name for p in d.iterdir()) == ["0", "1"]
    (m,) = contactinbox.pollable()
    assert [a.get("skipped") for a in m["replies"][0]["assets"]] == [None, None, "quota", "quota"]
    n = len(on.calls)
    contactsend.poll_once(force=True)  # 标过 skipped 的不再重试
    assert [c for c in on.calls[n:] if "/assets/" in c["path"]] == []
    doc = contactinbox.reply_doc(mid, rid)
    assert doc is not None and "asset:" not in doc and contactinbox.ALT_QUOTA in doc
    assert doc.count(contactinbox._PLACEHOLDER) == 2


def test_global_quota_spans_letters_and_stops_requests(on, monkeypatch):
    mid = _send_ok(on)
    mid2 = _send_ok_again(on)
    size = len(PNG)
    _tiny_quota(monkeypatch, 10**9, size * 3)
    r1 = _html_reply(on, mid, assets=[("image/png", PNG)] * 2)
    contactsend.poll_once(force=True)
    r2 = _html_reply(on, mid2, assets=[("image/png", PNG)] * 3)
    n = len(on.calls)
    contactsend.poll_once(force=True)
    got = [c["path"] for c in on.calls[n:] if "/assets/" in c["path"]]
    assert len(got) == 1  # 全局只剩一张的空间；其余没发请求
    assert contactinbox.asset_bytes() == size * 3
    assert (_asset_dir(mid) / r1 / "0").exists()
    (m2,) = [m for m in contactinbox.pollable() if m["message_id"] == mid2]
    assert [a.get("skipped") for a in m2["replies"][0]["assets"]] == [None, "quota", "quota"]
    assert r2


def test_reply_doc_caps_inlined_image_bytes_and_uses_placeholders_after_the_cap(on, monkeypatch):
    mid = _send_ok(on)
    big = PNG + b"\0" * 4000
    rid = on.reply(
        mid,
        "b",
        html="<p>" + "".join(f'<img src="asset:{i}" alt="p{i}">' for i in range(4)) + "</p>",
        assets=[("image/png", big)] * 4,
    )
    contactsend.poll_once(force=True)
    monkeypatch.setattr(contactinbox, "MAX_REPLY_DOC_IMAGE_BYTES", len(big) * 2 + 10)
    doc = contactinbox.reply_doc(mid, rid)
    assert doc is not None
    assert doc.count("data:image/png;base64,") == 2  # 前面的正常内联
    assert doc.count(contactinbox.ALT_TOO_BIG) == 2  # 超出的是占位 + 说明
    assert 'alt="p0"' in doc and 'alt="p2"' not in doc
    assert len(doc) < (len(big) * 2 + 10) * 1.4 + 20_000


def test_too_many_or_garbled_replies_are_not_silent_success_and_recover(on, monkeypatch, caplog):
    mid = _send_ok(on)
    on.reply(mid, "第一封")
    contactsend.poll_once(force=True)
    # 1. 超上限：不当成功，标 too_large，旧回信保留
    orig = contactsend.REPLIES_MAX_BYTES
    monkeypatch.setattr(contactsend, "REPLIES_MAX_BYTES", 300)
    on.reply(mid, "x" * 1000)
    with caplog.at_level(logging.WARNING):
        contactsend.poll_once(force=True)
    (m,) = contactinbox.public_messages()
    assert m["poll_error"] == "too_large" and len(m["replies"]) == 1
    assert "xxxx" not in caplog.text and mid in caplog.text and "too_large" in caplog.text
    # 2. 恢复：同一封下一轮照常再试，标记清除，新回信进来
    monkeypatch.setattr(contactsend, "REPLIES_MAX_BYTES", orig)
    contactsend.poll_once(force=True)
    (m,) = contactinbox.public_messages()
    assert m["poll_error"] == "" and len(m["replies"]) == 2
    # 3. 坏 JSON（响应被截断）
    on.raw_200("replies", b'{"message_id": "' + mid.encode() + b'", "replies": [{"id"')
    contactsend.poll_once(force=True)
    assert contactinbox.public_messages()[0]["poll_error"] == "bad_response"
    # 4. 合法 JSON 但不是回信（缺 replies）也不算成功
    on.raw_200("replies", b'{"message_id": "' + mid.encode() + b'"}')
    contactsend.poll_once(force=True)
    assert contactinbox.public_messages()[0]["poll_error"] == "bad_response"
    contactsend.poll_once(force=True)
    assert contactinbox.public_messages()[0]["poll_error"] == ""


def test_replies_limit_is_derived_from_the_worst_case_wire_encoding():
    kib = 1024
    expected = 50 * (2 * (256 * kib + 20000 * 4) + kib + 20 * 256) + 1024 * kib
    assert contactinbox.MAX_REPLIES_PER_MESSAGE == 50
    assert contactsend.REPLIES_MAX_BYTES == expected


def _wire_replies(mid: str, n: int, html: str) -> bytes:
    replies = [
        {"id": f"r_{i:04d}", "created_at": "t", "body": "b", "html": html, "assets": []}
        for i in range(n)
    ]
    obj = {"message_id": mid, "state": "replied", "replies": replies}
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def test_worst_case_quote_heavy_replies_fit_the_limit_and_are_stored(on):
    mid = _send_ok(on)
    html = '"' * contactinbox.MAX_REPLY_HTML_BYTES  # 每个字符线缆上都是 \"，膨胀 2 倍
    raw = _wire_replies(mid, 50, html)
    assert len(raw) > 50 * contactinbox.MAX_REPLY_HTML_BYTES * 1.9  # 线缆确实膨胀了
    assert len(raw) <= contactsend.REPLIES_MAX_BYTES
    on.raw_200("replies", raw)
    contactsend.poll_once(force=True)
    (m,) = contactinbox.public_messages()
    assert m["poll_error"] == "" and len(m["replies"]) == 50  # 不是 too_large


def test_more_than_50_replies_keep_only_the_first_50(on):
    mid = _send_ok(on)
    on.raw_200("replies", _wire_replies(mid, 51, "<p>x</p>"))
    contactsend.poll_once(force=True)
    (m,) = contactinbox.public_messages()
    assert m["poll_error"] == "" and len(m["replies"]) == 50
    assert m["replies"][0]["id"] == "r_0000" and m["replies"][-1]["id"] == "r_0049"


def test_round_budget_is_charged_by_bytes_actually_read_not_by_validated_bytes(on, monkeypatch):
    """服务端对每张小图回超大垃圾：每次读取都扣预算，本轮实际读到的字节 ≤ 预算（读取被剩余预算封顶，所以比「预算 + 一张」更紧）。"""
    mid = _send_ok(on)
    one = (
        1024 * 1024
    )  # 缩小比例跑：单张上限 1 MiB、本轮预算 3 MiB（生产值 10 MiB / 64 MiB，逻辑同）
    monkeypatch.setattr(contactinbox, "MAX_ASSET_BYTES", one)
    monkeypatch.setattr(contactsend, "ASSET_ROUND_BYTES", 3 * one)
    rids = [
        on.reply(mid, "b", html="<p/>", assets=[("image/png", PNG)] * 20) for _ in range(4)
    ]  # 80 张，元数据都只有几十字节
    junk = b"J" * (one + 1)
    for rid in rids:
        for n in range(20):
            on.serve_asset_as(rid, n, "image/png", junk)
    read: list[int] = []
    real_get = contactsend._get

    def spy(*a, **k):
        resp = real_get(*a, **k)
        if "/assets/" in a[1]:
            read.append(len(resp.body))
        return resp

    monkeypatch.setattr(contactsend, "_get", spy)
    contactsend.poll_once(force=True)
    assert read  # 确实在下
    assert (
        sum(read) <= 3 * one + 1
    )  # 单次读取被剩余预算封顶：只允许 1 字节的超限哨兵，不是「再多读一张」
    assert len(read) < contactsend.ASSET_ROUND_REQUESTS  # 预算耗尽就停，没有把 80 次发满


def test_failover_error_bodies_of_the_preferred_route_are_charged_to_the_round_budget(
    two, monkeypatch
):
    a, b = two
    mid = _send_ok(a)
    monkeypatch.setattr(contactsend, "ASSET_ROUND_BYTES", 10_000_000)
    seen: list = []
    real = contactsend._fetch_assets

    def spy(rec, targets, budget):
        seen.append(budget)
        return real(rec, targets, budget)

    monkeypatch.setattr(contactsend, "_fetch_assets", spy)
    _html_reply(a, mid, assets=FOUR[:3])
    a.fail("asset", 503, "e" * 60000, times=3)  # 首选线路每张都回 ~60 KB 的 5xx 错误体
    contactsend.poll_once(force=True)
    assert b.count("/v1/messages/%s/replies/%s/assets/0" % (mid, a.only_message().replies[0]["id"]))
    spent = 10_000_000 - seen[0].bytes_left
    ok_bytes = len(PNG) + len(JPG) + len(GIF)
    assert spent >= 3 * 60000 + ok_bytes  # 错误体也计入，不只是最终响应
