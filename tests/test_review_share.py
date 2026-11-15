"""「外部审阅」引擎（`engine/reviewshare.py` + `engine/reviewinbox.py`，ADR 0187）对线上格式 v1 的验收。

对端是 `tests/support/review_fakes.py` 的本地模拟（HTTPS 回环 + 每会话现造的私有 CA，验设备签名）；真 CloudBase、真存储直传、
真机 WebView 联调不在这里（NOT_RUN）。出图用注入的假渲染回调（真导出管线属于导出用例）。

判据的主语：
* 「没联网」= 模拟服务端 `calls` 的长度——服务端那一侧收到了什么，不是客户端自己说没发；
* 「没泄漏」= 状态对象、日志（caplog 全级别）里搜管理令牌 / 标题 / 意见文字，以及落盘文件的权限位；
* 「重试复用」= 两次请求体里的 client id 相同、渲染回调只被调用一次。
"""

from __future__ import annotations

import json
import logging
import ssl
import stat
import struct
import sys
import time

import pytest

from support.review_fakes import FakeReviewServer, Forced
from support.tls_certs import make_pki
from tavotto.engine import diagsend, reviewinbox, reviewshare, tlstrust

CANARY_TITLE = "CANARY_TITLE_Xk29"
CANARY_NOTE = "CANARY_NOTE_Lm03"
CANARY_COMMENT = "CANARY_COMMENT_Qp88"
TARGET = {"kind": "figure", "id": "fig_a"}
RENDER = {"original": {"figure_id": "fig_a", "overrides": []}}


def make_png(w: int = 1200, h: int = 800, pad: int = 0) -> bytes:
    """只有魔数 + IHDR 合规的假 PNG（客户端与模拟服务端都只核这两处）；`pad` 字节的辅助块用来撑大小。"""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0"
    if pad:
        out += struct.pack(">I", pad) + b"tEXt" + b"x" * pad + b"\0\0\0\0"
    return out + struct.pack(">I", 0) + b"IEND\0\0\0\0"


ELEMENTS = [
    {"gid": "ax0.title", "label": "标题", "bbox": [0.1, 0.0, 0.5, 0.1]},
    {"gid": "ax0.xlabel", "label": "x 轴", "bbox": [0.3, 0.9, 0.2, 0.05]},
]


# --------------------------------------------------------------------------- 装置
@pytest.fixture(scope="module")
def pki_files(tmp_path_factory):
    return make_pki().write(tmp_path_factory.mktemp("review-pki"))


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
    # conftest 给整个测试进程设了 TAVOTTO_NO_REVIEW_SHARE=1；这里摘掉它让“没设任何开关”的默认态可测。
    # 真连生产主机由 conftest 的会话级探针拦下并判红。
    for name in (reviewshare.ENV_HARD_OFF, reviewshare.ENV_ENDPOINT, diagsend.ENV_DEV_LOOPBACK):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    reviewshare.reset_for_tests()
    sleeps: list[float] = []
    monkeypatch.setattr(reviewshare, "_sleep", lambda ev, s: sleeps.append(s))
    monkeypatch.sleeps = sleeps  # type: ignore[attr-defined]
    yield
    reviewshare.reset_for_tests()


@pytest.fixture
def srv(pki_files):
    with FakeReviewServer(_server_tls(pki_files)) as s:
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
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, srv.base)
    return srv


@pytest.fixture
def renders(monkeypatch):
    """假渲染回调：记录调用；`script` 里排的 (w, h, pad) 依次用完，之后用默认。"""
    state = {"calls": [], "script": []}

    def render(target, render_spec, long_edge):
        state["calls"].append((dict(target), long_edge))
        w, h, pad = state["script"].pop(0) if state["script"] else (1200, 800, 0)
        return reviewshare.Rendered(make_png(w, h, pad), list(ELEMENTS))

    reviewshare.set_renderer(render)
    return state


def body(**over) -> dict:
    return {"target": TARGET, "title": CANARY_TITLE, "note": CANARY_NOTE, "render": RENDER, **over}


def wait_for(stages: set[str], timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    st = reviewshare.status()
    while time.monotonic() < deadline:
        st = reviewshare.status()
        if st["stage"] in stages:
            return st
        time.sleep(0.01)
    raise AssertionError(f"没等到 {stages}: {st}")


def share_ok(**over) -> dict:
    assert reviewshare.start_share(body(**over)) == {"ok": True}
    return wait_for({"done", "failed"})


def share_ok_in(project: str, **over) -> dict:
    assert reviewshare.start_share(body(**over), project=project) == {"ok": True}
    return wait_for({"done", "failed"})


def update_ok_in(share_id: str, project: str, **over) -> dict:
    spec = {"share_id": share_id, "render": RENDER, **over}
    assert reviewshare.start_update(spec, project=project) == {"ok": True}
    return wait_for({"done", "failed"})


def update_ok(share_id: str, **over) -> dict:
    assert reviewshare.start_update({"share_id": share_id, "render": RENDER, **over}) == {
        "ok": True
    }
    return wait_for({"done", "failed"})


# --------------------------------------------------------------------------- 开关与端点
def test_default_process_is_off_and_never_touches_the_network(monkeypatch, srv):
    monkeypatch.setenv(reviewshare.ENV_HARD_OFF, "1")
    assert reviewshare.capability() == {"enabled": False}
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.start_share(body())
    assert (e.value.code, e.value.status) == ("disabled", 404)
    assert reviewshare.quota() == {"new_left": None, "update_left": None}
    assert reviewshare.poll_once(force=True)["polled"] == 0
    assert srv.calls == []


def test_production_entry_stays_hidden_until_the_service_is_live(monkeypatch, srv):
    # 线上服务未部署前：不设端点 = 入口不出现、零网络（发版依赖，见 PRODUCTION_LIVE）
    monkeypatch.delenv(reviewshare.ENV_ENDPOINT, raising=False)
    monkeypatch.setattr(reviewshare, "PRODUCTION_LIVE", False)
    assert reviewshare.resolve_target() is None
    assert reviewshare.capability() == {"enabled": False}
    assert reviewshare.poll_once(force=True)["polled"] == 0
    assert srv.calls == []


def test_builtin_endpoint_is_exactly_the_whitelisted_host(monkeypatch):
    monkeypatch.delenv(reviewshare.ENV_ENDPOINT, raising=False)
    monkeypatch.setattr(reviewshare, "PRODUCTION_LIVE", True)
    t = reviewshare.resolve_target()
    assert t is not None and (t.host, t.port, t.loopback) == ("review.tavotto.cn", 443, False)
    assert reviewshare.REVIEW_HOSTS == {"review.tavotto.cn"}
    assert reviewshare.capability() == {"enabled": True}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.example",
        "https://review.tavotto.cn.evil.example",
        "http://review.tavotto.cn",
        "https://review.tavotto.cn:8443",
        "https://user@review.tavotto.cn",
        "https://review.tavotto.cn/x",
        "https://127.0.0.1:9",  # 回环没开开发模式
    ],
)
def test_a_non_compliant_endpoint_turns_the_feature_off_without_falling_back(monkeypatch, endpoint):
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, endpoint)
    assert reviewshare.resolve_target() is None
    assert reviewshare.capability() == {"enabled": False}


def test_loopback_endpoint_needs_the_dev_switch(monkeypatch, srv):
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, srv.base)
    assert reviewshare.resolve_target() is None
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    t = reviewshare.resolve_target()
    assert t is not None and t.loopback


# --------------------------------------------------------------------------- 完整流程
def test_full_share_flow_persists_a_local_record(on, renders):
    st = share_ok()
    assert st["stage"] == "done" and st["failure"] is None
    share = st["share"]
    assert share["kind"] == "figure" and share["target_id"] == "fig_a"
    assert share["title"] == CANARY_TITLE and share["note"] == CANARY_NOTE
    assert share["latest_version"] == 1 and not share["revoked"] and not share["expired"]
    assert share["review_url"].startswith(on.base + "/r/")
    # 线上顺序：init → 直传 → complete；全部签名（模拟端验过，缺 / 错签名会 401）
    assert on.paths() == [
        "POST /v1/shares",
        f"PUT /upload/{share['share_id']}/1",
        f"POST /v1/shares/{share['share_id']}/versions/1/complete",
    ]
    sh = on.shares[share["share_id"]]
    assert sh.versions[1]["complete"] and sh.uploads[f"{share['share_id']}/1"].startswith(
        b"\x89PNG"
    )
    # init 体：只有契约里的字段
    init = json.loads(on.calls[0]["body"])
    assert set(init) == {"client_share_id", "kind", "title", "note", "png_bytes", "elements"}
    assert init["png_bytes"] == len(sh.uploads[f"{share['share_id']}/1"])
    # 本机记录：令牌 / 元素表 / 快照都在盘上
    rec = reviewinbox.get(share["share_id"])
    assert rec and rec["owner_token"] == sh.owner_token and rec["delivered"]
    assert rec["versions"][0]["elements"] == ELEMENTS
    assert reviewinbox.snapshot_file(share["share_id"], 1).read_bytes().startswith(b"\x89PNG")
    assert reviewinbox.for_target("figure", "fig_a")["share_id"] == share["share_id"]
    assert reviewinbox.for_target("layout", "fig_a") is None
    # 渲染 → 长边 3000 的请求
    assert renders["calls"] == [(TARGET, 3000)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限位")
def test_secret_files_are_0600(on, renders):
    share_ok()
    base = reviewinbox.dir_path()
    for name in ("shares.json", "device.key"):
        assert stat.S_IMODE((base / name).stat().st_mode) == 0o600
    assert len((base / "device.key").read_bytes()) == 32


def test_device_key_is_stable_across_requests(on, renders):
    st = share_ok()
    reviewshare.dismiss()
    update_ok(st["share"]["share_id"])
    devices = {
        c["headers"]["X-Tavotto-Device"] for c in on.calls if not c["path"].startswith("/upload/")
    }
    assert len(devices) == 1
    # 直传存储的请求不带任何 Tavotto 签名 / 令牌头
    for c in on.calls:
        if c["path"].startswith("/upload/"):
            assert not [
                k
                for k in c["headers"]
                if k.lower().startswith("x-tavotto") or k.lower() == "authorization"
            ]


def test_tokens_and_texts_never_reach_status_or_logs(on, renders, caplog):
    caplog.set_level(logging.DEBUG)
    st = share_ok()
    sid = st["share"]["share_id"]
    token = on.shares[sid].owner_token
    on.reviewer_comment(sid, "c1", CANARY_COMMENT)
    reviewshare.poll_once(force=True)
    reviewshare.revoke(sid)
    blobs = [
        json.dumps(reviewshare.status()),
        json.dumps(reviewinbox.summary(reviewinbox.get(sid))),
        json.dumps(reviewinbox.detail(reviewinbox.get(sid))),
        caplog.text,
    ]
    for blob in blobs:
        assert token not in blob and "owner_token" not in blob
    assert CANARY_TITLE not in caplog.text and CANARY_NOTE not in caplog.text
    assert CANARY_COMMENT not in caplog.text and "/r/" not in caplog.text


def test_update_appends_a_version_to_the_same_link(on, renders):
    st = share_ok()
    sid, url = st["share"]["share_id"], st["share"]["review_url"]
    reviewshare.dismiss()
    renders["script"] = [(900, 600, 0)]
    st2 = update_ok(sid, note="改好了")
    assert st2["stage"] == "done"
    assert st2["share"]["share_id"] == sid and st2["share"]["review_url"] == url
    assert st2["share"]["latest_version"] == 2 and st2["share"]["note"] == "改好了"
    assert on.shares[sid].versions[2]["complete"]
    rec = reviewinbox.get(sid)
    assert [v["v"] for v in rec["versions"]] == [1, 2]
    assert reviewinbox.snapshot_file(sid, 1) and reviewinbox.snapshot_file(sid, 2)
    # 更新请求带管理令牌 + 设备签名；体里是契约字段
    upd = [c for c in on.calls if c["path"].endswith("/versions") and c["method"] == "POST"][0]
    assert upd["headers"]["Authorization"] == f"Bearer {on.shares[sid].owner_token}"
    assert set(json.loads(upd["body"])) == {"client_version_id", "png_bytes", "elements", "note"}


# --------------------------------------------------------------------------- 额度与失败码
def test_new_quota_exhausted_passes_through_as_a_failure_code(on, renders):
    share_ok()
    reviewshare.dismiss()
    st = share_ok(target={"kind": "figure", "id": "fig_b"})
    assert (st["stage"], st["failure"], st["retryable"]) == ("failed", "quota_new_exhausted", False)
    assert reviewinbox.for_target("figure", "fig_b") is None


def test_updates_to_the_same_link_are_unlimited_and_update_quota_is_always_null(on, renders):
    sid = share_ok()["share"]["share_id"]
    for _ in range(7):
        reviewshare.dismiss()
        assert update_ok(sid)["stage"] == "done"
    assert reviewinbox.get(sid)["latest_version"] == 8
    assert "quota_update_exhausted" not in reviewshare.FAILURES
    assert reviewshare.quota(sid)["update_left"] is None  # 服务端就算回了数字也不显示


def test_update_flow_rate_limited_is_retryable(on, renders):
    sid = share_ok()["share"]["share_id"]
    reviewshare.dismiss()
    on.update_limit = 0  # 兜底限流（每小时 30 次）用 rate_limited 回
    st = update_ok(sid)
    assert (st["stage"], st["failure"], st["retryable"]) == ("failed", "rate_limited", True)
    reviewshare.dismiss()
    on.update_limit = None
    assert update_ok(sid)["stage"] == "done"


def test_quota_endpoint(on):
    assert reviewshare.quota() == {"new_left": 1, "update_left": None}
    assert reviewshare.quota("rs_" + "a" * 16)["new_left"] == 1
    assert reviewshare.quota("../x") == {"new_left": None, "update_left": None}  # 坏 id 不发请求
    on.force("/v1/quota", Forced("status", 500, "server"))
    assert reviewshare.quota() == {
        "new_left": None,
        "update_left": None,
    }  # 联网失败 → 前端不显示剩余次数


@pytest.mark.parametrize(
    ("status", "code", "want", "retry"),
    [
        (429, "rate_limited", "rate_limited", True),
        (413, "too_large", "too_large", False),
        (401, "bad_signature", "client_error", False),
        (401, "clock_skew", "client_error", False),
        (422, "invalid", "client_error", False),
        (500, "server", "server", True),
        (503, "whatever", "server", True),
        (404, "share_gone", "share_gone", False),
    ],
)
def test_server_errors_map_into_the_closed_failure_set(on, renders, status, code, want, retry):
    on.force("/v1/shares", Forced("status", status, code))
    st = share_ok()
    assert (st["stage"], st["failure"], st["retryable"]) == ("failed", want, retry)
    assert want in reviewshare.FAILURES


def test_clock_skew_is_a_client_error(on, renders, monkeypatch):
    monkeypatch.setattr(reviewshare, "_time", lambda: time.time() + 3600)
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("client_error", False)


def test_network_failure_is_retryable_and_reuses_the_client_id_and_bytes(on, renders):
    on.force("/v1/shares", Forced("drop"))
    st = share_ok()
    assert (st["stage"], st["failure"], st["retryable"]) == ("failed", "network", True)
    reviewshare.dismiss()
    st = share_ok()
    assert st["stage"] == "done"
    inits = [json.loads(c["body"]) for c in on.calls if c["path"] == "/v1/shares"]
    assert len(inits) == 2 and inits[0]["client_share_id"] == inits[1]["client_share_id"]
    assert inits[0] == inits[1]
    assert len(renders["calls"]) == 1  # 重试没有重新出图：同一份字节
    assert on.new_used and list(on.new_used.values()) == [1]  # 没有重复扣额度


def test_changed_content_gets_a_new_client_id(on, renders):
    on.force("/v1/shares", Forced("drop"))
    share_ok()
    reviewshare.dismiss()
    share_ok(title="改了标题")
    inits = [json.loads(c["body"]) for c in on.calls if c["path"] == "/v1/shares"]
    assert inits[0]["client_share_id"] != inits[1]["client_share_id"]
    assert len(renders["calls"]) == 2


def test_a_lost_complete_response_is_retried_idempotently(on, renders):
    on.force(
        "/v1/shares/", Forced("drop")
    )  # 第一次碰到任何 /v1/shares/... （这里就是 complete）断线
    st = share_ok()
    assert st["stage"] == "done"
    assert len(on.calls_to("/v1/shares/rs_")) == 2
    assert reviewinbox.get(st["share"]["share_id"])["delivered"]


def test_token_is_saved_before_complete_so_a_lost_response_does_not_orphan_it(on, renders):
    on.force("/v1/shares/", Forced("status", 400, "invalid"))  # complete 被拒
    st = share_ok()
    assert st["stage"] == "failed"
    (rec,) = reviewinbox._load_locked()["shares"]
    assert rec["owner_token"] and not rec["delivered"]
    assert reviewinbox.listed() == [] and reviewinbox.for_target("figure", "fig_a") is None
    assert reviewinbox.pollable() == []


def test_upload_url_off_the_whitelist_is_rejected_and_nothing_is_uploaded(on, renders):
    on.upload_url_override = "https://evil.example/upload/x"
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("client_error", False)
    assert [p for p in on.paths() if p.startswith("PUT")] == []


@pytest.mark.parametrize(
    "spec",
    [
        {"url": "http://127.0.0.1:1/u", "method": "PUT"},
        {"url": "https://u:p@bucket-1234567.cos.ap-shanghai.myqcloud.com/k", "method": "PUT"},
        {"url": "https://evil.example/k", "method": "PUT"},
        {"url": "https://bucket-1234567.cos.ap-shanghai.myqcloud.com/k", "method": "DELETE"},
        {
            "url": "https://bucket-1234567.cos.ap-shanghai.myqcloud.com/k",
            "method": "PUT",
            "headers": {"Host": "x"},
        },
        {
            "url": "https://bucket-1234567.cos.ap-shanghai.myqcloud.com/k",
            "method": "PUT",
            "headers": {"a": "b\r\nc: d"},
        },
        {"url": "https://bucket-1234567.cos.ap-shanghai.myqcloud.com:8443/k", "method": "PUT"},
    ],
)
def test_upload_spec_validation_rejects_unsafe_shapes(spec):
    target = reviewshare.diagsend.Target("review.tavotto.cn", 443, False)
    with pytest.raises(diagsend._Failure) as e:
        reviewshare._upload_spec(spec, target)
    assert e.value.code == "client_error"


def test_upload_spec_accepts_cloudbase_cos_post_with_signed_fields():
    target = reviewshare.diagsend.Target("review.tavotto.cn", 443, False)
    spec = reviewshare._upload_spec(
        {
            "url": "https://bkt-1234567.cos.ap-shanghai.myqcloud.com/",
            "method": "POST",
            "headers": {},
            "fields": {"key": "k", "Signature": "s", "x-cos-security-token": "t"},
        },
        target,
    )
    assert spec["method"] == "POST" and len(spec["fields"]) == 3
    with pytest.raises(diagsend._Failure):
        reviewshare._upload_spec(
            {"url": "https://x.myqcloud.com.evil.example/", "method": "POST", "fields": {}}, target
        )


def test_upload_spec_accepts_a_presigned_cos_put():
    target = reviewshare.diagsend.Target("review.tavotto.cn", 443, False)
    spec = reviewshare._upload_spec(
        {
            "url": "https://tavotto-review-1234567.cos.ap-shanghai.myqcloud.com/k/1.png?sign=abc&x=1",
            "method": "PUT",
            "headers": {"x-cos-meta": "v"},
        },
        target,
    )
    assert spec["method"] == "PUT" and spec["headers"] == {"x-cos-meta": "v"}


def test_bad_signature_is_detected_by_the_fake_so_the_check_is_real(on, renders, monkeypatch):
    """模拟端验签是真的：把签名函数换成错的，init 就 401 → client_error。"""
    real = reviewshare.sign_headers
    monkeypatch.setattr(reviewshare, "sign_headers", lambda m, p, b, ts: real(m, p + "x", b, ts))
    st = share_ok()
    assert st["failure"] == "client_error"
    assert on.calls and on.calls[0]["path"] == "/v1/shares"


def test_oversized_png_is_re_rendered_at_a_lower_resolution(on, renders):
    renders["script"] = [(3000, 2000, reviewshare.MAX_PNG_BYTES + 10), (2100, 1400, 0)]
    st = share_ok()
    assert st["stage"] == "done"
    assert [c[1] for c in renders["calls"]] == [3000, 2100]
    sid = st["share"]["share_id"]
    assert len(on.shares[sid].uploads[f"{sid}/1"]) <= reviewshare.MAX_PNG_BYTES


def test_a_renderer_that_overshoots_the_long_edge_is_asked_again(on, renders):
    renders["script"] = [(4000, 3000, 0)]
    st = share_ok()
    assert st["stage"] == "done"
    assert renders["calls"][1][1] < 3000


def test_a_bitmap_source_that_ignores_ppi_is_shrunk_locally(on, renders, monkeypatch):
    """位图素材保源像素网格、不认 ppi：本机把 PNG 等比缩到长边上限，用户不被尺寸卡死。"""
    from tavotto.rendercore import raster

    monkeypatch.setattr(reviewshare, "LONG_EDGE_PX", 300)
    px = bytes((x * 7 + y) % 256 for y in range(400) for x in range(500) for _ in range(3))
    big = raster.encode_png(raster.RasterBuffer(500, 400, 3, px, 1500))

    def render(target, spec, edge):
        renders["calls"].append((target, edge))
        return reviewshare.Rendered(big, list(ELEMENTS))

    reviewshare.set_renderer(render)
    st = share_ok()
    assert st["stage"] == "done"
    sid = st["share"]["share_id"]
    got = on.shares[sid].uploads[f"{sid}/1"]
    w, h = reviewshare.png_size(got)
    assert max(w, h) <= 300 and abs(w / h - 500 / 400) < 0.02
    assert json.loads(on.calls[0]["body"])["elements"] == ELEMENTS  # 比例坐标不变
    assert len(renders["calls"]) == 2


def test_shrink_gives_up_on_unreadable_png_as_too_large(on, renders):
    renders["script"] = [(5000, 4000, 0)] * 10  # 假 PNG：解不了 → too_large
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("too_large", False)
    assert on.calls == []


def test_hopelessly_large_png_fails_as_too_large(on, renders):
    renders["script"] = [(3000, 2000, reviewshare.MAX_PNG_BYTES + 10)] * 10
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("too_large", False)
    assert on.calls == []


def test_render_failure_is_a_failure_code_without_network(on, renders):
    def boom(*a):
        raise RuntimeError("/Users/secret/path.py exploded")

    reviewshare.set_renderer(boom)
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("render_failed", True)
    assert on.calls == []


def test_no_renderer_registered_is_render_failed(on):
    st = share_ok()
    assert st["failure"] == "render_failed"


def test_input_validation(on, renders):
    for bad in (
        body(title=""),
        body(title="x" * 81),
        body(note="x" * 201),
        body(target={"kind": "nope", "id": "a"}),
        body(target={"kind": "figure", "id": ""}),
        {k: v for k, v in body().items() if k != "render"},
    ):
        with pytest.raises(reviewshare.ReviewError) as e:
            reviewshare.start_share(bad)
        assert e.value.status == 422
    assert on.calls == [] and renders["calls"] == []


def test_a_second_share_while_one_is_running_is_409(on, renders):
    import threading

    gate = threading.Event()

    def slow(target, spec, edge):
        gate.wait(5)
        return reviewshare.Rendered(make_png(), [])

    reviewshare.set_renderer(slow)
    assert reviewshare.start_share(body()) == {"ok": True}
    assert wait_for({"rendering"})["stage"] == "rendering"
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.start_share(body())
    assert (e.value.code, e.value.status) == ("in_progress", 409)
    gate.set()
    wait_for({"done", "failed"})


# --------------------------------------------------------------------------- 停止分享与轮询
def test_a_bare_404_is_not_share_gone(on, renders):
    """裸 404（没有服务端错误码，多半是网关）不能当「链接没了」：不标 revoked、停止分享不假装成功。"""
    sid = share_ok()["share"]["share_id"]
    on.force("/v1/shares/", Forced("status", 404, ""), times=3)
    assert reviewshare.poll_once(force=True)["ok"] is False
    assert reviewshare.revoke(sid) == {"ok": False, "failure": "server"}
    reviewshare.dismiss()
    assert reviewinbox.get(sid)["revoked"] is False
    st = update_ok(sid)
    assert st["failure"] == "server" and reviewinbox.get(sid)["revoked"] is False


def test_records_are_scoped_to_the_project_they_were_shared_from(on, renders):
    st = share_ok_in("proj_a")
    sid = st["share"]["share_id"]
    assert reviewinbox.for_target("figure", "fig_a", "proj_a")["share_id"] == sid
    assert reviewinbox.for_target("figure", "fig_a", "proj_b") is None  # 别的项目的同名图看不到它
    assert reviewinbox.for_target("figure", "fig_a") is None  # 没开项目也看不到
    reviewshare.dismiss()
    with pytest.raises(reviewshare.ReviewError) as e:  # 也推不了画面上去
        reviewshare.start_update({"share_id": sid, "render": RENDER}, project="proj_b")
    assert e.value.status == 404
    assert update_ok_in(sid, "proj_a")["stage"] == "done"


def test_polling_with_no_records_makes_zero_requests(on, renders):
    assert reviewshare.poll_once(force=True) == {"polled": 0, "ok": True}
    assert on.calls == []


def test_revoke_then_feed_is_never_requested_again(on, renders):
    sid = share_ok()["share"]["share_id"]
    assert reviewshare.poll_once(force=True)["polled"] == 1
    assert len(on.calls_to(f"/v1/shares/{sid}/feed")) == 1
    assert reviewshare.revoke(sid) == {"ok": True}
    assert on.shares[sid].revoked
    n = len(on.calls)
    assert reviewshare.poll_once(force=True)["polled"] == 0
    assert len(on.calls) == n
    rec = reviewinbox.get(sid)
    assert rec["revoked"] and reviewinbox.summary(rec)["revoked"]
    # 已停止：更新被拒；意见副本留着；可以删
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.start_update({"share_id": sid, "render": RENDER})
    assert e.value.status == 409
    assert reviewinbox.forget(sid) == "ok" and reviewinbox.get(sid) is None
    assert reviewinbox.snapshot_file(sid, 1) is None


def test_forgetting_an_active_share_is_refused(on, renders):
    sid = share_ok()["share"]["share_id"]
    assert reviewinbox.forget(sid) == "active"
    assert reviewinbox.forget("rs_" + "z" * 16) == "missing"


def test_revoke_failure_is_reported_not_raised_and_keeps_the_record_active(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.force(f"/v1/shares/{sid}", Forced("status", 500, "server"))
    assert reviewshare.revoke(sid) == {"ok": False, "failure": "server"}
    assert not reviewinbox.get(sid)["revoked"]


def test_server_says_gone_stops_polling_but_keeps_the_copy(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.reviewer_comment(sid, "c1", "留言")
    reviewshare.poll_once(force=True)
    on.shares[sid].revoked = True  # 服务端那边被维护者停用
    assert reviewshare.poll_once(force=True)["ok"] is False
    assert reviewinbox.get(sid)["revoked"] and len(reviewinbox.get(sid)["comments"]) == 1
    assert reviewshare.poll_once(force=True)["polled"] == 0


def test_expired_records_are_not_polled_and_are_cleaned_after_thirty_days(on, renders, monkeypatch):
    sid = share_ok()["share"]["share_id"]
    rec_exp = reviewinbox.get(sid)["expires_ts"]
    monkeypatch.setattr(reviewinbox, "_now", lambda: rec_exp + 5)
    assert reviewinbox.pollable() == []
    assert reviewinbox.summary(reviewinbox.get(sid))["expired"] is True
    n = len(on.calls)
    assert reviewshare.poll_once(force=True)["polled"] == 0 and len(on.calls) == n
    monkeypatch.setattr(reviewinbox, "_now", lambda: rec_exp + 31 * 86400)
    assert reviewinbox.get(sid) is None
    assert not reviewinbox.snapshot_path(sid, 1).exists()


def test_poll_is_throttled_unless_forced(on, renders):
    share_ok()
    assert reviewshare.poll_once(force=True)["polled"] == 1
    assert reviewshare.poll_once().get("throttled") is True
    assert reviewshare.poll_once(force=True)["polled"] == 1


# --------------------------------------------------------------------------- feed 合并
def test_feed_merges_comments_replies_deletions_verdicts_resolved_and_counts_unread(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.reviewer_comment(
        sid,
        "c1",
        "第一条",
        anchor={"kind": "element", "gid": "ax0.title", "box": [0.1, 0, 0.5, 0.1]},
    )
    on.reviewer_comment(
        sid, "c2", "第二条", author="王同学", anchor={"kind": "region", "box": [0.2, 0.2, 0.3, 0.3]}
    )
    on.push(
        sid,
        {
            "type": "reply",
            "comment_id": "c1",
            "reply": {
                "id": "r1",
                "from": "reviewer",
                "author": "林老师",
                "text": "补充",
                "created_at": "2026-11-15T09:05:00Z",
            },
        },
    )
    on.push(
        sid,
        {
            "type": "verdict",
            "verdict": {
                "v": 1,
                "decision": "changes",
                "note": "再调",
                "author": "林老师",
                "at": "2026-11-15T09:06:00Z",
            },
        },
    )
    assert reviewshare.poll_once(force=True) == {"polled": 1, "ok": True}
    d = reviewinbox.detail(reviewinbox.get(sid))
    assert [c["id"] for c in d["comments"]] == ["c1", "c2"]
    assert d["comments"][0]["replies"][0]["text"] == "补充"
    assert d["verdicts"] == [
        {
            "v": 1,
            "decision": "changes",
            "note": "再调",
            "author": "林老师",
            "at": "2026-11-15T09:06:00Z",
        }
    ]
    assert (d["comment_count"], d["open_count"], d["unread"]) == (2, 2, 3)
    unread = reviewinbox.unread_summary()
    assert unread["total"] == 3 and unread["latest"]["share_id"] == sid
    assert unread["latest"]["count"] == 3 and unread["latest"]["author"] == "林老师"
    # 增量：游标已推进，再拉没有新东西、未读不变
    assert reviewshare.poll_once(force=True)["polled"] == 1
    assert reviewinbox.summary(reviewinbox.get(sid))["unread"] == 3
    # 结论被后提交覆盖、意见被删、已处理
    on.push(
        sid,
        {
            "type": "verdict",
            "verdict": {
                "v": 1,
                "decision": "approved",
                "note": "",
                "author": "林老师",
                "at": "2026-11-15T09:30:00Z",
            },
        },
    )
    on.push(sid, {"type": "comment_deleted", "comment_id": "c2"})
    on.push(sid, {"type": "resolved", "comment_id": "c1", "resolved": True})
    reviewshare.poll_once(force=True)
    d = reviewinbox.detail(reviewinbox.get(sid))
    assert [c["id"] for c in d["comments"]] == ["c1"] and d["comments"][0]["resolved"] is True
    assert [v["decision"] for v in d["verdicts"]] == ["approved"]
    assert (d["comment_count"], d["open_count"]) == (1, 0)
    assert d["unread"] <= 2  # 未读被总条数封顶，不会比存在的意见 / 回复还多
    assert reviewinbox.mark_read(sid) and reviewinbox.unread_summary() == {
        "total": 0,
        "latest": None,
    }


def test_malformed_feed_events_are_dropped_not_trusted(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.push(sid, {"type": "comment", "comment": {"id": "../x", "v": 1}})
    on.push(
        sid,
        {
            "type": "comment",
            "comment": {
                "id": "c9",
                "v": 1,
                "anchor": {"kind": "element"},
                "author": "a",
                "text": "t",
                "created_at": "x",
            },
        },
    )
    on.push(sid, {"type": "mystery"})
    on.reviewer_comment(sid, "c1", "<b>html</b>" + "x" * 900, author="a" * 50)
    reviewshare.poll_once(force=True)
    (c,) = reviewinbox.get(sid)["comments"]
    assert (
        c["id"] == "c1"
        and len(c["text"]) == 500
        and len(c["author"]) == 20
        and c["text"].startswith("<b>")
    )


def test_oversized_feed_is_not_taken_as_success(on, renders, monkeypatch):
    sid = share_ok()["share"]["share_id"]
    on.reviewer_comment(sid, "c1", "x")
    monkeypatch.setattr(reviewshare, "FEED_MAX_BYTES", 20)
    assert reviewshare.poll_once(force=True)["ok"] is False
    assert reviewinbox.get(sid)["comments"] == []


def test_reply_and_resolve_round_trip_and_replies_are_not_counted_unread(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.reviewer_comment(sid, "c1", "问题")
    reviewshare.poll_once(force=True)
    out = reviewshare.reply(sid, "c1", "已回复")
    assert out["ok"] and out["comment"]["replies"][-1]["from"] == "owner"
    out = reviewshare.resolve(sid, "c1", True)
    assert out["ok"] and out["comment"]["resolved"] is True
    assert reviewinbox.summary(reviewinbox.get(sid))["open_count"] == 0
    # 自己的回复稍后又出现在 feed 里：不重复、不计未读
    reviewinbox.mark_read(sid)
    reviewshare.poll_once(force=True)
    (c,) = reviewinbox.get(sid)["comments"]
    assert len(c["replies"]) == 1 and reviewinbox.summary(reviewinbox.get(sid))["unread"] == 0
    # 请求带管理令牌
    call = on.calls_to(f"/v1/shares/{sid}/comments/c1/replies")[0]
    assert call["headers"]["Authorization"] == f"Bearer {on.shares[sid].owner_token}"


def test_reply_validation_and_unknown_ids(on, renders):
    sid = share_ok()["share"]["share_id"]
    on.reviewer_comment(sid, "c1", "问题")
    reviewshare.poll_once(force=True)
    for text in ("", "   ", "x" * 501, 5):
        with pytest.raises(reviewshare.ReviewError) as e:
            reviewshare.reply(sid, "c1", text)
        assert e.value.status == 422
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.reply(sid, "nope", "hi")
    assert e.value.status == 404
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.resolve(sid, "c1", "yes")
    assert e.value.status == 422


# --------------------------------------------------------------------------- 元素表
def _man(*els):
    """`(gid, label, bbox[, role])`；role 缺省 = `text`（label 是画在图上的文字，原样上传）。"""
    return {
        "elements": [
            {"gid": e[0], "label": e[1], "bbox": e[2], "role": e[3] if len(e) > 3 else "text"}
            for e in els
        ]
    }


def test_manifest_elements_clamp_drop_figure_and_empty_areas():
    out = reviewshare.manifest_elements(
        _man(
            ("figure", "整张图", [0, 0, 1, 1]),
            ("ax0.line", "曲线", [-5, -5, 50, 50]),  # 未裁剪的数据包围盒：夹进画面
            ("ax0.off", "屏外", [3, 3, 1, 1]),
            ("ax0.flat", "零宽", [0.2, 0.2, 0, 0.1]),
            ("ax0.nan", "坏", [float("nan"), 0, 1, 1]),
            ("ax0.t", "", [0.1, 0.1, 0.2, 0.05]),
        )
    )
    assert out == [
        {"gid": "ax0.line", "label": "曲线", "bbox": [0.0, 0.0, 1.0, 1.0]},
        {"gid": "ax0.t", "label": "元素 1", "bbox": [0.1, 0.1, 0.2, 0.05]},
    ]
    assert (
        reviewshare.manifest_elements(None) == []
        and reviewshare.manifest_elements({"elements": 3}) == []
    )


def test_trim_elements_drops_smallest_area_first_by_count(caplog):
    many = [
        {"gid": f"g{i}", "label": "l", "bbox": [0.0, 0.0, 0.001 * (i + 1), 0.001 * (i + 1)]}
        for i in range(4100)
    ]
    keep, dropped = reviewshare.trim_elements(many)
    assert len(keep) == 4000 and dropped == 100
    assert {e["gid"] for e in keep} == {f"g{i}" for i in range(100, 4100)}
    assert [e["gid"] for e in keep] == sorted(
        (e["gid"] for e in keep), key=lambda g: int(g[1:])
    )  # 保持原顺序


def test_trim_elements_drops_smallest_area_first_by_bytes():
    fat = "L" * 120
    els = [
        {"gid": f"g{i}" + "x" * 200, "label": fat, "bbox": [0.0, 0.0, 0.0001 * (i + 1), 0.5]}
        for i in range(1500)
    ]
    size = len(json.dumps(els, ensure_ascii=False, separators=(",", ":")).encode())
    assert size > reviewshare.MAX_ELEMENTS_BYTES
    keep, dropped = reviewshare.trim_elements(els)
    assert dropped > 0 and len(keep) + dropped == 1500
    assert (
        len(json.dumps(keep, ensure_ascii=False, separators=(",", ":")).encode())
        <= reviewshare.MAX_ELEMENTS_BYTES
    )
    assert (
        min(float(e["bbox"][2]) for e in keep)
        >= max(float(e["bbox"][2]) for e in els[:dropped]) - 1e-9
    )


def test_trim_logs_only_the_count(on, renders, caplog):
    caplog.set_level(logging.INFO)
    many = [
        {"gid": f"S{i}", "label": "SECRET", "bbox": [0.0, 0.0, 0.001 * (i + 1), 0.001 * (i + 1)]}
        for i in range(4100)
    ]
    reviewshare.set_renderer(lambda *a: reviewshare.Rendered(make_png(), many))
    share_ok()
    assert "丢弃 100 项" in caplog.text and "SECRET" not in caplog.text


def test_two_panel_layout_conversion():
    # 页面 200 × 100 mm：左面板 (0,0,100,100) 原样；右面板 (100,0,100,100) 裁左半 + 水平翻转
    objects = [
        {
            "id": "figs/a.pdf",
            "object_id": "pA",
            "type": "panel",
            "name": "A 图",
            "x_mm": 0,
            "y_mm": 0,
            "w_mm": 100,
            "h_mm": 100,
        },
        {
            "id": "figs/a.pdf",  # 同一张图的第二个面板：`id` 是项目内路径，既不能出机也会撞
            "object_id": "pB",
            "type": "panel",
            "x_mm": 100,
            "y_mm": 0,
            "w_mm": 100,
            "h_mm": 100,
            "crop": {"x": 0, "y": 0, "w": 0.5, "h": 1},
            "flip_h": True,
        },
        {"id": "pH", "type": "panel", "hidden": True, "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10},
        {"id": "t1", "type": "text", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10},
    ]
    manifests = {
        "pA": _man(("ax0.title", "标题", [0.25, 0.1, 0.5, 0.1])),
        # pB 源图里：左半的一个元素 (0.1, 0.5, 0.2, 0.2)，右半的一个 (0.7, 0.5, 0.2, 0.2)（被裁掉）
        "pB": _man(
            ("ax0.left", "左", [0.1, 0.5, 0.2, 0.2]), ("ax0.right", "右", [0.7, 0.5, 0.2, 0.2])
        ),
        "pH": _man(("ax0.hid", "隐", [0, 0, 1, 1])),
    }
    out = reviewshare.layout_elements(200, 100, objects, manifests)
    by = {e["gid"]: e for e in out}
    assert list(by) == [
        "pA",
        "pB",
        "pA::ax0.title",
        "pB::ax0.left",
    ]  # 外框在前、隐藏 / 文字 / 被裁掉的元素都不在
    assert by["pA"] == {"gid": "pA", "label": "A 图", "bbox": [0.0, 0.0, 0.5, 1.0]}
    assert "figs" not in json.dumps(out) and ".pdf" not in json.dumps(out)  # 路径不出机
    assert by["pB"]["label"] == "#2" and by["pB"]["bbox"] == [0.5, 0.0, 0.5, 1.0]
    # pA：左面板占页面左半 → x = 0.25 * 0.5, w = 0.5 * 0.5；y 不变
    assert by["pA::ax0.title"]["bbox"] == pytest.approx([0.125, 0.1, 0.25, 0.1], abs=1e-5)
    # pB：裁 [0,0.5] → u' = (0.1..0.3)/0.5 = 0.2..0.6；水平翻转 → 0.4..0.8；映到右半页 x = 0.5 + 0.4*0.5 = 0.7, w = 0.4*0.5 = 0.2
    assert by["pB::ax0.left"]["bbox"] == pytest.approx([0.7, 0.5, 0.2, 0.2], abs=1e-5)


def test_layout_conversion_rotation_and_garbage():
    objects = [
        {
            "id": "a.pdf",
            "type": "panel",
            "x_mm": 0,
            "y_mm": 0,
            "w_mm": 100,
            "h_mm": 100,
            "rotation": 90,
        }
    ]
    out = reviewshare.layout_elements(
        100, 100, objects, {"p1": _man(("e", "e", [0.0, 0.0, 0.2, 0.1]))}
    )
    # 顺时针 90°：源图左上角元素 → 右上角；宽高互换
    assert out[1]["bbox"] == pytest.approx([0.9, 0.0, 0.1, 0.2], abs=1e-5)
    assert reviewshare.layout_elements("x", 100, objects, {}) == []
    assert reviewshare.layout_elements(100, 0, objects, {}) == []
    assert reviewshare.layout_elements(100, 100, "nope", {}) == []
    assert (
        reviewshare.layout_elements(100, 100, [{"id": "p", "type": "panel", "x_mm": "a"}], {}) == []
    )


def test_panel_keys_are_opaque_unique_and_never_the_object_id():
    objs = [
        {"type": "text"},
        {"type": "panel", "id": "a/b.pdf", "object_id": "obj_1"},
        {"type": "panel", "id": "a/b.pdf"},  # 没带 object_id：按第几个面板
        {"type": "panel", "id": "c.pdf", "object_id": "obj_1"},  # 重复
        {"type": "panel", "id": "d.pdf", "object_id": "../../etc"},  # 不合规
    ]
    keys = reviewshare.panel_keys(objs)
    assert keys == {1: "obj_1", 2: "p2", 3: "p3", 4: "p4"}
    assert not any("/" in k or ".pdf" in k for k in keys.values())
    assert reviewshare.panel_keys("nope") == {}
    # 显示名像路径就不用
    named = {"type": "panel", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10, "name": "figs/a.pdf"}
    assert reviewshare.layout_elements(100, 100, [named], {})[0]["label"] == "#1"


def test_layout_flow_uses_layout_kind(on, renders):
    st = share_ok(
        target={"kind": "layout", "id": "doc_1"},
        render={"canvas": {"page_w_mm": 100, "page_h_mm": 100, "objects": []}},
    )
    assert st["share"]["kind"] == "layout" and st["share"]["target_id"] == "doc_1"
    assert json.loads(on.calls[0]["body"])["kind"] == "layout"
    assert renders["calls"][0][0] == {"kind": "layout", "id": "doc_1"}


def test_update_can_retarget_and_versions_carry_their_frame(on, renders):
    sid = share_ok()["share"]["share_id"]
    assert reviewinbox.detail(reviewinbox.get(sid))["versions"][0]["frame"] == "figure"
    reviewshare.dismiss()
    st = update_ok(sid, target={"kind": "figure", "id": "fig_b"})
    assert st["share"]["target_id"] == "fig_b"
    assert reviewinbox.for_target("figure", "fig_b")["share_id"] == sid
    assert reviewinbox.for_target("figure", "fig_a") is None
    reviewshare.dismiss()
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.start_update(
            {"share_id": sid, "render": RENDER, "target": {"kind": "layout", "id": "d"}}
        )
    assert e.value.status == 422


def test_layout_versions_are_page_framed(on, renders):
    st = share_ok(
        target={"kind": "layout", "id": "doc_1"},
        render={"canvas": {"page_w_mm": 100, "page_h_mm": 100, "objects": []}},
    )
    assert (
        reviewinbox.detail(reviewinbox.get(st["share"]["share_id"]))["versions"][0]["frame"]
        == "page"
    )


# --------------------------------------------------------------------------- 仅回环开发模式的放宽
def test_dev_loopback_mode_allows_plain_http_endpoints_but_only_on_loopback(monkeypatch):
    for good in ("http://127.0.0.1:8787", "http://localhost:8787", "https://127.0.0.1:9"):
        monkeypatch.setenv(reviewshare.ENV_ENDPOINT, good)
        monkeypatch.delenv(diagsend.ENV_DEV_LOOPBACK, raising=False)
        assert reviewshare.resolve_target() is None  # 没开开发开关：一律不认
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        t = reviewshare.resolve_target()
        assert t is not None and t.loopback
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, "http://127.0.0.1:8787")
    assert reviewshare.resolve_target().base == "http://127.0.0.1:8787"
    for bad in (
        "http://evil.example",
        "http://review.tavotto.cn",
        "http://127.0.0.1.evil.example:8787",
        "http://u@127.0.0.1:8787",
        "http://127.0.0.1:8787/x",
    ):
        monkeypatch.setenv(reviewshare.ENV_ENDPOINT, bad)
        assert reviewshare.resolve_target() is None, bad


def test_dev_target_accepts_any_loopback_review_url_and_upload_address(monkeypatch):
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, "http://127.0.0.1:8787")
    t = reviewshare.resolve_target()
    assert reviewshare._review_url_ok("http://127.0.0.1:5173/r/abc?x=1", t)
    assert reviewshare._review_url_ok("http://localhost:5173/anything", t)
    assert not reviewshare._review_url_ok("http://evil.example/r/" + "a" * 20, t)
    assert not reviewshare._review_url_ok("https://review.tavotto.cn/r/" + "a" * 20, t)
    spec = reviewshare._upload_spec(
        {"url": "http://127.0.0.1:8787/_blob/upload", "method": "POST", "fields": {"k": "v"}}, t
    )
    assert spec["url"].endswith("/_blob/upload")
    for bad in (
        "http://evil.example/_blob/upload",
        "http://127.0.0.1.evil.example/u",
        "http://u@127.0.0.1/u",
    ):
        with pytest.raises(diagsend._Failure):
            reviewshare._upload_spec({"url": bad, "method": "PUT"}, t)


def test_production_rules_are_untouched_by_the_dev_relaxation(monkeypatch):
    monkeypatch.setattr(reviewshare, "PRODUCTION_LIVE", True)
    t = reviewshare.resolve_target()
    assert (
        t.host == "review.tavotto.cn" and not t.loopback and t.base == "https://review.tavotto.cn"
    )
    assert reviewshare._review_url_ok("https://review.tavotto.cn/r/" + "a" * 20, t)
    for bad in (
        "http://review.tavotto.cn/r/" + "a" * 20,
        "https://127.0.0.1:5173/r/" + "a" * 20,
        "http://127.0.0.1:5173/r/" + "a" * 20,
        "https://evil.example/r/" + "a" * 20,
    ):
        assert not reviewshare._review_url_ok(bad, t), bad
    for bad in (
        "http://127.0.0.1:8787/_blob/upload",
        "https://127.0.0.1:8787/u",
        "http://x.myqcloud.com/",
    ):
        with pytest.raises(diagsend._Failure):
            reviewshare._upload_spec({"url": bad, "method": "POST", "fields": {}}, t)


def test_changed_compares_the_render_digest_with_the_latest_version(on, renders):
    sid = share_ok()["share"]["share_id"]
    n = len(on.calls)
    assert reviewshare.changed(sid, RENDER) == {"changed": False}
    reordered = {"original": {"overrides": [], "figure_id": "fig_a"}}  # 键序不同、内容相同
    assert reviewshare.changed(sid, reordered) == {"changed": False}
    other = {
        "original": {"figure_id": "fig_a", "overrides": [{"gid": "g", "prop": "p", "value": 1}]}
    }
    assert reviewshare.changed(sid, other) == {"changed": True}
    assert len(on.calls) == n  # 只读本机，不联网
    reviewshare.dismiss()
    assert update_ok(sid, render=other)["stage"] == "done"
    assert reviewshare.changed(sid, other) == {"changed": False}  # 以最新版本为准
    assert reviewshare.changed(sid, RENDER) == {"changed": True}
    assert reviewinbox.get(sid)["versions"][-1]["digest"] == reviewshare.render_digest(other)


def test_changed_is_true_for_old_records_without_a_digest_and_validates_input(on, renders):
    sid = share_ok()["share"]["share_id"]
    data = reviewinbox._load_locked()
    for v in data["shares"][0]["versions"]:
        v["digest"] = ""
    reviewinbox._save_locked(data)
    assert reviewshare.changed(sid, RENDER) == {"changed": True}
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.changed(sid, None)
    assert (e.value.code, e.value.status) == ("render_required", 422)
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.changed("rs_" + "z" * 16, RENDER)
    assert e.value.status == 404
    with pytest.raises(reviewshare.ReviewError) as e:
        reviewshare.changed(sid, RENDER, project="other")
    assert e.value.status == 404


def test_element_labels_never_carry_artist_labels_only_rendered_text_or_structural_names(
    on, renders
):
    man = {
        "elements": [
            # 脚本里 label='/private/study.csv' 但没画图例：路径不在画面上，不许出机
            {
                "gid": "axes_0.lines_0",
                "role": "line",
                "label": "/private/study.csv",
                "bbox": [0.1, 0.1, 0.5, 0.5],
            },
            {
                "gid": "axes_0.lines_1",
                "role": "line",
                "label": "C:\\Users\\me\\data.csv",
                "bbox": [0.1, 0.1, 0.4, 0.4],
            },
            {
                "gid": "axes_0.scatter_0",
                "role": "scatter",
                "label": "secret.xlsx",
                "bbox": [0.2, 0.2, 0.1, 0.1],
            },
            {
                "gid": "axes_0.legend",
                "role": "legend",
                "label": "图例",
                "bbox": [0.7, 0.1, 0.2, 0.2],
            },
            # 画在图上的文字：标题 / 轴标签 / 图例条目（图例里实际显示的文字）可以出现
            {
                "gid": "axes_0.title",
                "role": "title",
                "label": "标题 “Raw”",
                "bbox": [0.3, 0.0, 0.2, 0.05],
            },
            {
                "gid": "axes_0.legend.texts_0",
                "role": "legend_text",
                "label": "图例项 “signal”",
                "bbox": [0.72, 0.12, 0.1, 0.03],
            },
            {
                "gid": "axes_0.mystery",
                "role": "weird",
                "label": "/Users/x/y",
                "bbox": [0.0, 0.5, 0.1, 0.1],
            },
        ]
    }
    out = reviewshare.manifest_elements(man)
    labels = {e["gid"]: e["label"] for e in out}
    assert labels == {
        "axes_0.lines_0": "曲线 1",
        "axes_0.lines_1": "曲线 2",
        "axes_0.scatter_0": "散点 1",
        "axes_0.legend": "图例 1",
        "axes_0.title": "标题 “Raw”",
        "axes_0.legend.texts_0": "图例项 “signal”",
        "axes_0.mystery": "元素 1",
    }
    blob = json.dumps(out, ensure_ascii=False)
    for secret in ("private", "study.csv", "Users", "secret.xlsx", "data.csv"):
        assert secret not in blob
    # 端到端：上传的 init 体里同样没有
    reviewshare.set_renderer(lambda *a: reviewshare.Rendered(make_png(), out))
    share_ok()
    assert "study.csv" not in on.calls[0]["body"].decode() and "Raw" in on.calls[0]["body"].decode()


def test_cancel_during_complete_backoff_stops_all_further_sends(on, renders, monkeypatch):
    on.force("/v1/shares/", Forced("drop"), times=10)  # complete 一直断线
    seen = []

    def cancelling_sleep(ev, seconds):
        seen.append(seconds)
        reviewshare.cancel()  # 退避等待中用户取消

    monkeypatch.setattr(reviewshare, "_sleep", cancelling_sleep)
    assert reviewshare.start_share(body()) == {"ok": True}
    st = wait_for({"idle", "done", "failed"})
    assert st["stage"] == "idle" and seen == [1.0]
    completes = [p for p in on.paths() if p.endswith("/complete")]
    assert len(completes) == 1  # 取消之后零发送


def test_cancel_event_interrupts_the_backoff_wait_itself():
    import threading as _t

    ev = _t.Event()
    ev.set()
    t0 = time.monotonic()
    reviewshare._interruptible_sleep(ev, 30)
    assert time.monotonic() - t0 < 1


def test_a_failed_local_save_after_complete_is_a_retryable_failure_and_the_retry_delivers(
    on, renders, monkeypatch
):
    real = reviewinbox.atomicio.write_bytes
    state = {"shares_writes": 0}

    def flaky(path, data, **kw):
        if str(path).endswith("shares.json"):
            state["shares_writes"] += 1
            if (
                state["shares_writes"] == 2
            ):  # 第 1 次是 init 后登记令牌；第 2 次是 complete 之后登记版本
                raise OSError("disk full")
        return real(path, data, **kw)

    monkeypatch.setattr(reviewinbox.atomicio, "write_bytes", flaky)
    st = share_ok()
    assert (st["stage"], st["failure"], st["retryable"]) == ("failed", "server", True)
    assert st["share"] is None
    # 远端已发布，本机留着带管理令牌的待投递记录，没有被当成功
    (rec,) = reviewinbox._load_locked()["shares"]
    assert rec["owner_token"] and not rec["delivered"]
    assert reviewinbox.listed() == [] and reviewinbox.for_target("figure", "fig_a") is None
    reviewshare.dismiss()
    st = share_ok()  # 重试：同一个 id、同一份字节、不重复扣额度、不重新出图
    assert st["stage"] == "done"
    inits = [json.loads(c["body"]) for c in on.calls if c["path"] == "/v1/shares"]
    assert len(inits) == 2 and inits[0] == inits[1] and len(renders["calls"]) == 1
    assert list(on.new_used.values()) == [1]
    got = reviewinbox.get(st["share"]["share_id"])
    assert got["delivered"] and got["owner_token"] == rec["owner_token"]
    assert reviewinbox.for_target("figure", "fig_a")["share_id"] == got["share_id"]


def test_a_failed_local_save_of_the_token_is_a_retryable_failure_too(on, renders, monkeypatch):
    real = reviewinbox.atomicio.write_bytes
    state = {"n": 0}

    def flaky(path, data, **kw):
        if str(path).endswith("shares.json"):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("read-only")
        return real(path, data, **kw)

    monkeypatch.setattr(reviewinbox.atomicio, "write_bytes", flaky)
    st = share_ok()
    assert (st["failure"], st["retryable"]) == ("server", True)
    assert [p for p in on.paths() if p.startswith("PUT")] == []  # 令牌没落盘就不上传
    reviewshare.dismiss()
    assert share_ok()["stage"] == "done"
    assert list(on.new_used.values()) == [1]  # 同一 client id 重放，不重复扣额度


def test_undelivered_records_survive_the_old_two_hour_cleanup(on, renders, monkeypatch):
    on.force("/v1/shares/", Forced("status", 400, "invalid"))
    share_ok()
    ts = reviewinbox._load_locked()["shares"][0]["created_ts"]
    monkeypatch.setattr(reviewinbox, "_now", lambda: ts + 3 * 3600)
    assert len(reviewinbox._load_pruned_locked()["shares"]) == 1  # 3 小时后仍在
    monkeypatch.setattr(reviewinbox, "_now", lambda: ts + 25 * 3600)
    assert reviewinbox._load_pruned_locked()["shares"] == []
