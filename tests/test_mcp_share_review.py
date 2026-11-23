"""`tavotto_share_review`（ADR 0203 一期）：Agent 把会话里的图发成外部审阅链接，先问用户再发。

对端是 `tests/support/review_fakes.py` 的本地模拟（HTTPS 回环 + 私有 CA，验设备签名），假 worker 出图，不真联网。
判据的主语：

* 「没联网 / 没上传」= 模拟服务端 `calls` 的长度（服务端那一侧收到了什么），不是工具自己说没发；
* 「出图回调随这次分享走」= 桌面注册的模块级 `_RENDERER` 没被调用、也没被改；
* 「更新只认同项目同图」= 收件箱按（项目、图）找记录，别的项目拿不到、`start_update` 回 404 not_found。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
from __future__ import annotations

import base64
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from support import artifactbytes  # noqa: E402
from tavotto.engine import diagsend, reviewinbox, reviewshare  # noqa: E402
from tavotto_mcp import bridge, nextstep, server  # noqa: E402
from test_mcp_server import (  # noqa: E402, F401  — 夹具与假 worker 复用（autouse 的会话清理也要带进来）
    FakeWorker,
    _body,
    _call,
    _clean_sessions,
    _open,
    fake_pool,
    project,
)
from test_review_share import (  # noqa: E402, F401  — 模拟服务端 + 私有 CA 的夹具（含摘掉硬关的 autouse）
    _isolated,
    on,
    pki_files,
    srv,
    trust,
)

TOOL = "tavotto_share_review"
PATCH_A = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 11}]
PATCH_B = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 12}]


class ShareWorker(FakeWorker):
    """在假 worker 上补「同一次绘制的 PNG + manifest」。"""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[tuple] = []

    def preview_png_snapshot(self, stem, patches, width_px, **_kw):
        self.snapshots.append((stem, list(patches), width_px))
        png = artifactbytes.solid_png(1200, 900)
        return {"png": base64.b64encode(png).decode("ascii"), "manifest": self._manifest(patches)}

    def preview_png(self, stem, patches, width, tag):  # 旧引擎路径 / 不带确认的小预览
        self.snapshots.append((stem, list(patches), width))
        path = self.exported_dir / f"{tag}.png"
        path.write_bytes(artifactbytes.solid_png(800, 600))
        return str(path)

    exported_dir = None


@pytest.fixture
def worker(project, monkeypatch, tmp_path):
    w = ShareWorker()
    w.exported_dir = tmp_path
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: w)
    return w


@pytest.fixture
def desktop_renderer():
    """桌面注册的模块级出图回调：MCP 分享绝不能调用它，也不能把它换掉。"""
    calls: list[tuple] = []

    def desktop(target, render, edge):
        calls.append((target, edge))
        raise AssertionError("桌面的出图回调不该被 MCP 的分享用到")

    reviewshare.set_renderer(desktop)
    return desktop, calls


def _text(res: dict) -> str:
    return " ".join(c["text"] for c in res["content"] if c["type"] == "text")


def _share(sid: str, **over) -> dict:
    return _call(TOOL, {"session_id": sid, **over})


# --------------------------------------------------------------------------- 开关
def test_tool_is_absent_and_refuses_while_review_share_is_off(project, worker, monkeypatch):
    """硬关（TAVOTTO_NO_REVIEW_SHARE=1，conftest 整个测试进程的默认）= 工具不出现；调用回 `review_share_disabled`。"""
    monkeypatch.setenv(reviewshare.ENV_HARD_OFF, "1")
    assert TOOL not in [t["name"] for t in server._tools()]
    res = _share("nope", title="t", explicit_confirm=True)
    assert res["isError"] is True and _body(res)["code"] == "review_share_disabled"


def test_tool_appears_when_review_share_is_on(on):
    tools = {t["name"]: t for t in server._tools()}
    schema = tools[TOOL]["inputSchema"]
    assert schema["required"] == ["session_id"] and schema["additionalProperties"] is False
    assert schema["properties"]["update"]["enum"] == ["auto", "new"]
    assert "用户明确" in schema["properties"]["explicit_confirm"]["description"]
    assert TOOL in server.HANDLERS and TOOL not in __import__("tavotto_mcp.usage").usage.TOOLS


def test_old_engine_without_the_renderer_parameter_counts_as_off(on, monkeypatch):
    monkeypatch.setattr(reviewshare, "start_share", lambda body, *, scope=None, project="": None)
    assert not bridge.review_share_available()
    assert TOOL not in [t["name"] for t in server._tools()]


# --------------------------------------------------------------------------- 不带确认：零网络
def test_without_confirm_nothing_leaves_the_machine(on, project, worker, desktop_renderer):
    sid = _open(project)
    res = _share(sid, title="给导师看看")
    body = _body(res)
    assert not res.get("isError")
    assert on.calls == []  # 服务端一次请求都没收到（含额度）
    assert reviewshare.status()["stage"] == "idle"
    assert body["status"] == "needs_confirmation" and body["mode"] == "new"
    assert body["code"] == "review_confirmation_required"
    assert body["next"]["action"] == "ask_user" and body["next"]["options"][1] == "先不发"
    assert (
        "explicit_confirm=true" in body["next"]["options"][0] and sid in body["next"]["options"][0]
    )
    assert any(c["type"] == "image" and c["mimeType"] == "image/png" for c in res["content"])
    assert "_previews" not in body  # 图走 image 内容块，不进 structuredContent
    text = _text(res)
    assert "还没发送" in text and "不含脚本、数据" in text and "给导师看看" in text
    assert "owner_token" not in json.dumps(res, ensure_ascii=False)
    assert worker.snapshots[-1][2] == bridge.REVIEW_PREVIEW_WIDTH_PX  # 小预览，不是上传那张


def test_the_server_never_fills_in_the_confirmation_itself(on, project, worker):
    sid = _open(project)
    for args in ({"title": "x"}, {"title": "x", "explicit_confirm": False}):
        assert _body(_share(sid, **args))["status"] == "needs_confirmation"
    assert on.calls == []


def test_title_is_required_for_a_new_link_and_bounded(on, project, worker):
    sid = _open(project)
    for bad in (None, "  ", "x" * (reviewshare.TITLE_MAX + 1)):
        res = _share(sid, title=bad, explicit_confirm=True)
        assert res["isError"] is True and _body(res)["code"] == "bad_arguments"
    assert on.calls == []


def test_unknown_session_is_the_usual_error_when_enabled(on):
    res = _share("missing", title="t")
    assert res["isError"] is True and _body(res)["code"] == "unknown_session"


# --------------------------------------------------------------------------- 带确认：新建 / 更新
def test_confirmed_share_creates_a_link_with_its_own_renderer(
    on, project, worker, desktop_renderer
):
    desktop, desktop_calls = desktop_renderer
    sid = _open(project)
    res = _share(sid, title="Fig1 初稿", note="请看图例", explicit_confirm=True)
    body = _body(res)
    assert not res.get("isError"), body
    assert body["status"] == "shared" and body["mode"] == "new" and body["version"] == 1
    assert body["review_url"].startswith("http") and "/r/" in body["review_url"]
    assert "owner_token" not in json.dumps(body)
    assert len(on.shares) == 1
    # 出图回调随这一次分享走：桌面注册的没被调用、也没被换掉
    assert desktop_calls == [] and reviewshare._RENDERER is desktop
    # 同一次绘制的 PNG + manifest；长边 ≈ 上传规格（80x60 mm 横图 → 宽 = 长边）
    assert worker.snapshots[-1][2] == reviewshare.LONG_EDGE_PX
    # 本机收件箱里能看到：与桌面同一份记录，target 与桌面同口径（kind=figure, id=stem）
    rec = reviewinbox.for_target("figure", "Fig1", bridge.project_id(str(project)))
    assert rec is not None and rec["title"] == "Fig1 初稿" and rec["latest_version"] == 1
    assert reviewshare.status()["stage"] == "idle"  # 结束后已 dismiss
    # 没有后台轮询：服务端只看到建链接那几步
    assert not [p for p in on.paths() if "feed" in p]


def test_second_call_updates_the_same_link_and_the_confirmation_names_it(on, project, worker):
    sid = _open(project)
    first = _body(_share(sid, title="Fig1 初稿", explicit_confirm=True))
    bridge.apply_overrides(sid, PATCH_A)
    before = len(on.calls)
    res = _share(sid)  # 不带 title、不带确认：预览里点名要更新哪一条
    pre = _body(res)
    assert pre["mode"] == "update" and pre["existing"]["title"] == "Fig1 初稿"
    assert pre["existing"]["version"] == 1 and pre["existing"]["review_url"] == first["review_url"]
    assert len(on.calls) == before and "第 2 版" in _text(res)
    assert "更新" in pre["next"]["question"]
    out = _body(_share(sid, explicit_confirm=True))
    assert out["mode"] == "update" and out["version"] == 2
    assert out["review_url"] == first["review_url"] and len(on.shares) == 1
    assert reviewshare.status()["stage"] == "idle"


def test_update_new_always_creates_and_a_quota_failure_asks_the_user(on, project, worker):
    sid = _open(project)
    _share(sid, title="一", explicit_confirm=True)
    res = _share(sid, title="二", update="new", explicit_confirm=True)  # 模拟服务端额度 1
    body = _body(res)
    assert res["isError"] is True and body["code"] == "review_quota_new_exhausted"
    assert body["failure"] == "quota_new_exhausted" and body["retryable"] is False
    assert body["next"]["action"] == "ask_user"
    assert len(on.shares) == 1


def test_an_edit_between_preview_and_send_is_reported(on, project, worker):
    sid = _open(project)
    _share(sid, title="t")  # 预览
    bridge.apply_overrides(sid, PATCH_B)
    out = _body(_share(sid, title="t", explicit_confirm=True))
    assert out["sent_latest"] is True
    # 预览后没改的不带这个字段
    out2 = _body(_share(sid, explicit_confirm=True))
    assert "sent_latest" not in out2


def test_update_only_sees_this_project_and_this_figure(on, project, worker):
    sid = _open(project)
    out = _body(_share(sid, title="t", explicit_confirm=True))
    pid = bridge.project_id(str(project))
    assert reviewinbox.for_target("figure", "Fig1", pid) is not None
    assert reviewinbox.for_target("figure", "Fig1", "otherproject00") is None
    assert reviewinbox.for_target("figure", "Other", pid) is None
    share_id = reviewinbox.for_target("figure", "Fig1", pid)["share_id"]
    # 别的项目拿同一个 share_id 更新：引擎同样 404 not_found
    with pytest.raises(reviewshare.ReviewError) as exc:
        reviewshare.start_update(
            {"share_id": share_id, "render": {"x": 1}}, project="otherproject00"
        )
    assert (exc.value.code, exc.value.status) == ("not_found", 404)
    assert out["mode"] == "new"


# --------------------------------------------------------------------------- 渲染与等待
def test_old_engine_falls_back_to_preview_png_plus_session_manifest(
    on, project, monkeypatch, tmp_path
):
    class Old(ShareWorker):
        preview_png_snapshot = None

    w = Old()
    w.exported_dir = tmp_path
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: w)
    sid = _open(project)
    out = _body(_share(sid, title="t", explicit_confirm=True))
    assert out["status"] == "shared" and w.snapshots[-1][2] == reviewshare.LONG_EDGE_PX


def test_timeout_returns_in_progress_and_a_retry_does_not_send_twice(
    on, project, worker, monkeypatch
):
    release = threading.Event()
    real_init = reviewshare._init_share

    def slow_init(*a, **k):
        release.wait(10)
        return real_init(*a, **k)

    monkeypatch.setattr(reviewshare, "_init_share", slow_init)
    monkeypatch.setattr(bridge, "REVIEW_WAIT_S", 0.3)
    sid = _open(project)
    res = _share(sid, title="t", explicit_confirm=True)
    body = _body(res)
    assert not res.get("isError") and body["status"] == "in_progress"
    assert body["next"]["tool"] == TOOL and body["next"]["args"]["session_id"] == sid
    # 另一张图 / 别的参数在这期间调用：不并发发
    other = _share(sid, title="换个标题", explicit_confirm=True)
    assert other["isError"] is True and _body(other)["code"] == "review_in_progress"
    release.set()
    # 后台先走完：收件箱里这时已有这条链接，再调时 `auto` 会解析成「更新」——键不能含这个派生值，否则会多发第 2 版
    deadline = time.monotonic() + 20
    while reviewshare.status()["stage"] not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reviewshare.status()["stage"] == "done"
    assert reviewinbox.for_target("figure", "Fig1", bridge.project_id(str(project))) is not None
    version_calls = len([c for c in on.calls if c["path"].endswith("/versions")])
    monkeypatch.setattr(bridge, "REVIEW_WAIT_S", 30.0)
    done = _body(_share(sid, title="t", explicit_confirm=True))
    assert done["status"] == "shared" and done["version"] == 1 and done["mode"] == "new"
    assert len(on.shares) == 1  # 同一指纹的重试接上了上一次，没有重复建链接
    assert (
        len([c for c in on.calls if c["path"].endswith("/versions")]) == version_calls
    )  # 也没有多发一版


def test_a_render_failure_is_reported_with_the_adr_failure_code(on, project, worker, monkeypatch):
    sid = _open(project)

    def boom(*a, **k):
        raise reviewshare._Failure("network")

    monkeypatch.setattr(reviewshare, "_init_share", boom)
    res = _share(sid, title="t", explicit_confirm=True)
    body = _body(res)
    assert res["isError"] is True and body["code"] == "review_network"
    assert body["failure"] == "network" and body["retryable"] is True
    assert body["next"]["tool"] == TOOL and body["next"]["args"]["explicit_confirm"] is True


def test_the_hard_off_switch_stops_a_confirmed_call_cold(project, worker, monkeypatch, srv, trust):
    monkeypatch.setenv(reviewshare.ENV_ENDPOINT, srv.base)
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.delenv(reviewshare.ENV_HARD_OFF, raising=False)
    sid = _open(project)
    assert _body(_share(sid, title="t"))["status"] == "needs_confirmation"
    monkeypatch.setenv(reviewshare.ENV_HARD_OFF, "1")
    res = _share(sid, title="t", explicit_confirm=True)
    assert res["isError"] is True and _body(res)["code"] == "review_share_disabled"
    assert srv.calls == []


def test_confirm_question_never_confirms_on_the_users_behalf():
    step = nextstep.share_review_confirm({"mode": "new", "title": "T"}, {"session_id": "s1"})
    assert step["action"] == "ask_user" and step["options"][-1] == "先不发"
    assert "explicit_confirm=true" in step["options"][0]


def test_width_follows_the_long_edge_for_landscape_and_portrait():
    assert bridge._review_width_px({"size_mm": [80, 60]}, 3000) == 3000
    assert bridge._review_width_px({"size_mm": [60, 80]}, 3000) == 2250
    assert bridge._review_width_px({}, 3000) == 3000
    assert bridge._review_width_px({"size_mm": [0, 80]}, 3000) == 3000
