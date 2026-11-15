"""`/api/review*`（本机 HTTP 面，ADR 0187）：关闭时不存在、打开后端到端、状态 / 详情对象里没有管理令牌。

引擎侧的契约对账在 `test_review_share.py`；会话认证对每条路由的默认拒绝由 `test_browser_auth.py` 从 url_map 枚举
（这里再钉“这些路由确实在 url_map 里”，免得枚举悄悄漏掉它们）。出图用注入的假回调（真导出管线属于导出用例）。
"""

from __future__ import annotations

import json
import ssl
import time

import pytest

from support.review_fakes import FakeReviewServer
from support.tls_certs import make_pki
from tavotto import app as m
from tavotto.engine import diagsend, reviewinbox, reviewshare, tlstrust
from test_review_share import ELEMENTS, RENDER, TARGET, make_png


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory):
    return make_pki().write(tmp_path_factory.mktemp("review-api-pki"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for name in (reviewshare.ENV_HARD_OFF, reviewshare.ENV_ENDPOINT, diagsend.ENV_DEV_LOOPBACK):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    m.app.config["TESTING"] = True
    m.reset_projects()
    reviewshare.reset_for_tests()
    monkeypatch.setattr(reviewshare, "_sleep", lambda s: None)
    yield m.app.test_client()
    reviewshare.reset_for_tests()
    reviewshare.set_renderer(m._review_render)  # reset_for_tests 清掉了注册；还给后面的用例
    m.reset_projects()


@pytest.fixture
def on(client, pki_files, monkeypatch):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    with FakeReviewServer(ctx) as srv:
        monkeypatch.setattr(
            tlstrust,
            "client_context",
            lambda: ssl.create_default_context(cafile=str(pki_files["ca"])),
        )
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        monkeypatch.setenv(reviewshare.ENV_ENDPOINT, srv.base)
        reviewshare.set_renderer(lambda t, r, e: reviewshare.Rendered(make_png(), list(ELEMENTS)))
        yield srv


def _wait(client, stages, timeout=15.0):
    end = time.monotonic() + timeout
    st = {}
    while time.monotonic() < end:
        st = client.get("/api/review/status").get_json()
        if st["stage"] in stages:
            return st
        time.sleep(0.02)
    raise AssertionError(st)


def _share(client, **over):
    payload = {"target": TARGET, "title": "标题", "note": "", "render": RENDER, **over}
    return client.post("/api/review/share", json=payload)


def test_the_routes_exist_in_the_url_map_so_the_default_deny_sweep_sees_them():
    rules = {
        (r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in m.app.url_map.iter_rules()
    }
    for want in (
        ("/api/review/capability", ("GET",)),
        ("/api/review/quota", ("GET",)),
        ("/api/review/for", ("GET",)),
        ("/api/review/share", ("POST",)),
        ("/api/review/update", ("POST",)),
        ("/api/review/status", ("GET",)),
        ("/api/review/dismiss", ("POST",)),
        ("/api/review/revoke", ("POST",)),
        ("/api/review/shares/<share_id>", ("DELETE",)),
        ("/api/review/shares/<share_id>", ("GET",)),
        ("/api/review/refresh", ("POST",)),
        ("/api/review/shares/<share_id>/comments/<cid>/reply", ("POST",)),
        ("/api/review/shares/<share_id>/comments/<cid>/resolve", ("POST",)),
        ("/api/review/shares/<share_id>/read", ("POST",)),
        ("/api/review/shares/<share_id>/versions/<int:v>/snapshot.png", ("GET",)),
        ("/api/review/unread", ("GET",)),
    ):
        assert want in rules, want


def test_disabled_feature_only_answers_the_capability_probe(client, monkeypatch):
    monkeypatch.setenv(reviewshare.ENV_HARD_OFF, "1")
    assert client.get("/api/review/capability").get_json() == {"enabled": False}
    assert client.get("/api/review/for?kind=figure&id=a").get_json() == {"share": None}
    assert client.get("/api/review/unread").get_json() == {"total": 0, "latest": None}
    assert client.get("/api/review/quota").get_json() == {"new_left": None, "update_left": None}
    assert client.get("/api/review/status").status_code == 404
    assert _share(client).status_code == 404
    assert client.post("/api/review/revoke", json={"share_id": "rs_" + "a" * 16}).status_code == 404
    assert client.post("/api/review/refresh", json={}).status_code == 404


def test_end_to_end_share_update_reply_resolve_revoke_delete(client, on):
    assert client.get("/api/review/capability").get_json() == {"enabled": True}
    assert client.get("/api/review/quota").get_json() == {"new_left": 1, "update_left": None}
    assert client.get("/api/review/status").get_json() == {
        "stage": "idle",
        "failure": None,
        "retryable": False,
        "share": None,
    }
    assert client.get("/api/review/for?kind=figure&id=fig_a").get_json() == {"share": None}
    # 缺 render / 坏输入：同步 422、不联网
    before = len(on.calls)
    assert _share(client, render=None).status_code == 422
    assert _share(client, title="").status_code == 422
    assert len(on.calls) == before

    r = _share(client)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    st = _wait(client, {"done", "failed"})
    assert st["stage"] == "done" and st["failure"] is None
    sid = st["share"]["share_id"]
    assert on.shares[sid].owner_token not in json.dumps(st)
    assert client.post("/api/review/dismiss").get_json()["stage"] == "idle"

    # 「审阅」页签是否出现只看这个
    got = client.get("/api/review/for?kind=figure&id=fig_a").get_json()["share"]
    assert got["share_id"] == sid and got["latest_version"] == 1
    detail = client.get(f"/api/review/shares/{sid}").get_json()
    assert detail["versions"][0]["elements"] == ELEMENTS and detail["comments"] == []
    assert "owner_token" not in json.dumps(detail)
    snap = client.get(f"/api/review/shares/{sid}/versions/1/snapshot.png")
    assert (
        snap.status_code == 200
        and snap.mimetype == "image/png"
        and snap.data.startswith(b"\x89PNG")
    )
    assert client.get(f"/api/review/shares/{sid}/versions/9/snapshot.png").status_code == 404
    assert client.get("/api/review/shares/rs_notexist/versions/1/snapshot.png").status_code == 404

    # 更新到同一链接
    assert (
        client.post("/api/review/update", json={"share_id": sid, "render": RENDER}).status_code
        == 200
    )
    assert _wait(client, {"done", "failed"})["share"]["latest_version"] == 2
    client.post("/api/review/dismiss")
    assert (
        client.post(
            "/api/review/update", json={"share_id": "rs_" + "a" * 16, "render": RENDER}
        ).status_code
        == 404
    )

    # 审阅者留言 → 刷新 → 未读 → 回复 / 标记 / 已读
    on.reviewer_comment(sid, "c1", "这条线为什么断了")
    assert client.post("/api/review/refresh", json={"share_id": sid}).get_json() == {
        "ok": True,
        "unread": 1,
    }
    unread = client.get("/api/review/unread").get_json()
    assert unread["total"] == 1 and unread["latest"] == {
        "share_id": sid,
        "author": "林老师",
        "count": 1,
    }
    rep = client.post(
        f"/api/review/shares/{sid}/comments/c1/reply", json={"text": "已修"}
    ).get_json()
    assert rep["ok"] and rep["comment"]["replies"][-1]["text"] == "已修"
    assert (
        client.post(f"/api/review/shares/{sid}/comments/c1/reply", json={"text": ""}).status_code
        == 422
    )
    assert (
        client.post(f"/api/review/shares/{sid}/comments/zz/reply", json={"text": "x"}).status_code
        == 404
    )
    res = client.post(
        f"/api/review/shares/{sid}/comments/c1/resolve", json={"resolved": True}
    ).get_json()
    assert res["ok"] and res["comment"]["resolved"] is True
    assert (
        client.post(
            f"/api/review/shares/{sid}/comments/c1/resolve", json={"resolved": "y"}
        ).status_code
        == 422
    )
    assert client.post(f"/api/review/shares/{sid}/read").get_json() == {"ok": True, "unread": 0}
    assert client.get("/api/review/unread").get_json() == {"total": 0, "latest": None}

    # 删除：没停止分享前 409；停止后成功，意见副本在删除前仍可读
    assert client.delete(f"/api/review/shares/{sid}").status_code == 409
    assert client.post("/api/review/revoke", json={"share_id": sid}).get_json() == {"ok": True}
    assert client.get(f"/api/review/shares/{sid}").get_json()["revoked"] is True
    assert client.get("/api/review/for?kind=figure&id=fig_a").get_json()["share"]["revoked"] is True
    assert client.delete(f"/api/review/shares/{sid}").get_json() == {"ok": True}
    assert client.get(f"/api/review/shares/{sid}").status_code == 404
    assert client.delete(f"/api/review/shares/{sid}").status_code == 404


def test_failure_is_reported_in_status_with_a_code_and_no_secrets(client, on):
    _share(client)
    _wait(client, {"done"})
    client.post("/api/review/dismiss")
    assert _share(client, target={"kind": "figure", "id": "fig_b"}).status_code == 200
    st = _wait(client, {"failed"})
    assert (st["failure"], st["retryable"], st["share"]) == ("quota_new_exhausted", False, None)
    assert _share(client).status_code == 200  # 失败后可以再来（不卡住）
    _wait(client, {"done", "failed"})


def test_unknown_comment_and_share_ids_never_touch_the_network(client, on):
    n = len(on.calls)
    assert client.post("/api/review/revoke", json={"share_id": "rs_" + "q" * 16}).status_code == 404
    assert client.post("/api/review/revoke", json={"share_id": "../../etc"}).status_code == 404
    assert (
        client.post("/api/review/shares/rs_nope/comments/c1/reply", json={"text": "x"}).status_code
        == 404
    )
    assert client.get("/api/review/shares/rs_nope").status_code == 404
    assert len(on.calls) == n


def test_the_app_registers_the_export_backed_renderer(client):
    """`app` 在 import 时把导出管线出图回调注册给引擎（真出图属于导出用例）。"""
    assert reviewshare._RENDERER is None  # reset_for_tests 清过
    reviewshare.set_renderer(m._review_render)
    assert reviewshare._RENDERER is m._review_render
    assert reviewinbox.dir_path().name == "review"


def test_review_render_asks_the_export_pipeline_for_a_private_png_and_builds_elements(
    client, monkeypatch
):
    """`app._review_render`：只请求 png、ppi 由图幅算出、中间文件在私有临时目录（用完即删）、不写项目导出目录。
    真栅格化属于导出用例（本机缺批准字体时跑不了），这里替掉 `_export_produce` 与 manifest 取得。"""
    from pathlib import Path

    from tavotto.engine import exportjob

    seen = {}

    def fake_produce(job, tmp_dir, **kw):
        seen["request"] = job.request
        seen["tmp"] = Path(tmp_dir)
        out = Path(tmp_dir) / "review.png"
        out.write_bytes(make_png(2400, 1600))
        return [exportjob.Produced(format="png", tmp_path=out)]

    monkeypatch.setattr(m, "_export_produce", fake_produce)
    monkeypatch.setattr(
        m,
        "_review_manifest",
        lambda fid, ov: {
            "elements": [{"gid": f"{fid}.t", "label": "标题", "bbox": [0.1, 0.1, 0.2, 0.1]}]
        },
    )
    fig = m._review_render(
        {"kind": "figure", "id": "fig_a"},
        {"original": {"figure_id": "fig_a", "overrides": [], "w_mm": 200, "h_mm": 100}},
        3000,
    )
    req = seen["request"]
    assert (req.scope, req.formats, req.ppi) == (
        "original",
        ("png",),
        381,
    )  # 3000 px / (200 mm / 25.4)
    assert fig.png.startswith(b"\x89PNG") and fig.elements == [
        {"gid": "fig_a.t", "label": "标题", "bbox": [0.1, 0.1, 0.2, 0.1]}
    ]
    assert not seen["tmp"].exists()  # 私有临时目录用完即删
    lay = m._review_render(
        {"kind": "layout", "id": "doc_1"},
        {
            "canvas": {
                "page_w_mm": 100,
                "page_h_mm": 50,
                "objects": [
                    {"type": "panel", "id": "p1", "x_mm": 0, "y_mm": 0, "w_mm": 50, "h_mm": 50}
                ],
            }
        },
        3000,
    )
    assert seen["request"].scope == "canvas" and seen["request"].ppi == 762
    assert [e["gid"] for e in lay.elements] == ["p1", "p1::p1.t"]
    # 描述缺段 / 导出没给 PNG：抛，由引擎收成 render_failed
    with pytest.raises(ValueError):
        m._review_render({"kind": "layout", "id": "d"}, {"original": {}}, 3000)
    monkeypatch.setattr(m, "_export_produce", lambda job, tmp, **kw: [])
    with pytest.raises(ValueError):
        m._review_render({"kind": "figure", "id": "a"}, {"original": {"figure_id": "a"}}, 3000)
