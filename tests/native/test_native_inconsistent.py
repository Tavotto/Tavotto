"""native 图「与文档不一致」这一档（Codex #549 第八轮）。

引擎撤掉一条改动时还原抛了，账留着、下一次渲染重试（`overrides.apply` 的
`FigState.unrestored`，`tests/test_restore_failure_retry.py`）。safe worker 可以作废重起，
native 会话是用户自己的 Python（ADR 0021），不杀不断开——于是在还原成功之前，这张 live
Figure 与文档不一致，这里把它挡在 offline 同一条 409 路上：

1. 判据只看 v1 render 结果里的结构化字段 `unrestored`，不解析 warning 文案；
2. 不一致期间只放行**同一份列表**的重渲染（它会重试还原），报 0 即自动解除；
3. 换列表的编辑、导出、历史预览一律 `native_figure_inconsistent`（409）；
4. 导出与 offline 走同一条路：拿 live 图的那一步抛 `RunError`，作业不拿它交差。

第九轮（Codex #549）补两条：

5. 「查标记 → 发请求 → 按响应更新标记」是一整段（会话一把锁），两个请求重叠时第二个换了
   列表的渲染 / 导出照样被拦，标记以最后一次响应为准；
6. 任何一张图不一致时 continue / detach 一律拒绝（terminate 放行）；runner 侧
   `release_barrier()` 还原不回去时同样不放行——下面两条真进程链用例量的就是 runner 那一层。
"""

from __future__ import annotations

import threading
import time

import pytest

from support import nativekit
from tavotto import app as m
from tavotto.engine import nativesession, pool, runcodes
from tavotto.engine.runcodes import RunError

A = [{"gid": "axes_0.title", "prop": "text", "value": "A"}]
B = [{"gid": "axes_0.title", "prop": "text", "value": "B"}]


class _Session(nativesession.NativeSession):
    """一条不连 socket 的真会话：`_request` 由用例喂响应，发出去的帧记下来。"""

    def __init__(self, out_dir):
        super().__init__(
            session_id="native-inconsistent",
            descriptor={"native_id": "i" * 32, "metadata": {}, "out_dir": str(out_dir)},
        )
        self.sent: list[dict] = []
        self.unrestored = 0

    def _request(self, obj: dict, timeout: float) -> dict:
        self.sent.append(obj)
        if obj["cmd"] == "override":
            return {
                "ok": True,
                "manifest": {"elements": []},
                "warnings": [],
                "unrestored": self.unrestored,
            }
        return {"ok": True}


def _code(fn) -> str:
    with pytest.raises(RunError) as err:
        fn()
    return err.value.code


def test_only_the_same_list_may_rerender_until_the_engine_reports_clean(tmp_path):
    s = _Session(tmp_path)
    s.unrestored = 2
    s.override("Fig1", A)
    assert "Fig1" in s.inconsistent
    # 换列表的编辑：不发出去
    n = len(s.sent)
    assert _code(lambda: s.override("Fig1", B)) == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert len(s.sent) == n
    # 同一份列表：放行（引擎会重试还原），仍不干净就仍挡着
    s.override("Fig1", [dict(p) for p in A])
    assert "Fig1" in s.inconsistent
    # 别的图不受牵连
    s.unrestored = 0
    s.override("Fig2", B)
    assert "Fig2" not in s.inconsistent and "Fig1" in s.inconsistent
    # 重渲染干净了：自动解除，换列表照常
    s.override("Fig1", A)
    assert "Fig1" not in s.inconsistent
    s.override("Fig1", B)


def test_export_and_history_preview_refuse_the_live_figure_while_inconsistent(tmp_path):
    s = _Session(tmp_path)
    s.unrestored = 1
    s.override("Fig1", A)
    n = len(s.sent)
    assert _code(lambda: s.export("Fig1", A, str(tmp_path / "x.pdf"))) == (
        runcodes.NATIVE_FIGURE_INCONSISTENT
    )
    assert _code(lambda: s.preview_png("Fig1", A, 400, "v1")) == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert len(s.sent) == n, "拦在发出去之前，不是发了再说"
    s.unrestored = 0
    s.override("Fig1", A)
    s.export("Fig1", A, str(tmp_path / "x.pdf"))


def test_an_old_bridge_without_the_field_is_read_as_clean(tmp_path):
    """老 bridge 不给 `unrestored`：按 0 读，不凭空挂标记（判据只看这个字段）。"""
    s = _Session(tmp_path)

    def old(obj, timeout):
        return {"ok": True, "manifest": {"elements": []}, "warnings": ["还原失败 x.y: boom"]}

    s._request = old  # type: ignore[method-assign]
    s.override("Fig1", A)
    assert s.inconsistent == {}


# ---- 端点：与 offline 同一条 409 路 ----


@pytest.fixture
def session(tmp_path, monkeypatch):
    s = _Session(tmp_path)
    monkeypatch.setattr(m, "_engine_worker", lambda rel_id: (s, "Fig1"))
    # 端点只要一个项目身份（SSE 的 pj）与一张查得到 stem 的注册表，不碰磁盘
    monkeypatch.setattr(m, "current_ctx", lambda: type("Ctx", (), {"id": "p-test"})())
    monkeypatch.setattr(
        m, "current_registry", lambda: type("Reg", (), {"for_stem": lambda self, stem: None})()
    )
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **k: None)
    m.app.config["TESTING"] = True
    return s


def _render(patches):
    return m.app.test_client().post(
        "/api/engine/render", json={"id": "runtime:fig.py#Fig1", "patches": patches}
    )


def test_render_endpoint_passes_the_field_and_answers_409_like_offline(session):
    session.unrestored = 3
    resp = _render(A)
    assert resp.status_code == 200 and resp.get_json()["unrestored"] == 3
    resp = _render(B)
    assert resp.status_code == 409
    assert resp.get_json()["code"] == runcodes.NATIVE_FIGURE_INCONSISTENT
    session.unrestored = 0
    resp = _render(A)
    assert resp.status_code == 200 and resp.get_json()["unrestored"] == 0
    assert _render(B).status_code == 200


@pytest.mark.parametrize("state", ["offline", "inconsistent"])
def test_export_cannot_take_the_live_figure_the_same_way_as_offline(tmp_path, monkeypatch, state):
    """导出拿 live 图的唯一一扇门 `_serialize_figure_with_worker`：offline 在取会话时抛，
    不一致在会话导出时抛——同一个 `RunError` 冒进导出作业，作业不拿 live 图交差。"""
    s = _Session(tmp_path)
    s.unrestored = 1
    s.override("Fig1", A)

    def engine_worker(rel_id):
        if state == "offline":
            raise RunError(runcodes.NATIVE_SESSION_OFFLINE)
        return s, "Fig1"

    monkeypatch.setattr(m, "_engine_worker", engine_worker)
    monkeypatch.setattr(
        m.engine_runtimeasset, "resolve", lambda rel_id, reg: {"script": "fig.py", "stem": "Fig1"}
    )
    monkeypatch.setattr(m, "current_registry", lambda: None)
    with pytest.raises(RunError) as err:
        m._serialize_figure_with_worker("runtime:fig.py#Fig1", A, "pdf", 300, [], tmp_path)
    want = {
        "offline": runcodes.NATIVE_SESSION_OFFLINE,
        "inconsistent": runcodes.NATIVE_FIGURE_INCONSISTENT,
    }[state]
    assert err.value.code == want
    assert all(f["cmd"] != "export" for f in s.sent)


# ---- 第九轮：并发串行化 ----


class _Blocking(_Session):
    """`_request` 按调用顺序各自等一个闸门：第一个请求在途时第二个请求会发生什么。"""

    def __init__(self, out_dir, replies):
        super().__init__(out_dir)
        self.replies = list(replies)
        self.gates: list[threading.Event] = []
        self.entered = threading.Event()

    def _request(self, obj, timeout):
        self.sent.append(obj)
        gate = threading.Event()
        self.gates.append(gate)
        reply = self.replies.pop(0)
        self.entered.set()
        assert gate.wait(10), "闸门没开"
        return reply


def _render_reply(n):
    return {"ok": True, "manifest": {"elements": []}, "warnings": [], "unrestored": n}


def _overlap(s, first, second):
    """`first` 在途（卡在闸门上）时起 `second`，放开之后回 (second 的结果 / 异常, 帧数)。"""
    out: dict = {}

    def run(fn, key):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001
            out[key] = exc

    t1 = threading.Thread(target=run, args=(first, "first"))
    t1.start()
    assert s.entered.wait(10)
    t2 = threading.Thread(target=run, args=(second, "second"))
    t2.start()
    t2.join(0.3)  # 给没有锁的实现足够的时间把第二帧发出去
    sent_while_first_in_flight = len(s.sent)
    # 一直开闸直到两个线程都结束：第二个请求（若被放行）拿到的闸门是之后才建的
    for _ in range(200):
        for g in list(s.gates):
            g.set()
        if not t1.is_alive() and not t2.is_alive():
            break
        t2.join(0.05)
    assert not t1.is_alive() and not t2.is_alive(), "线程没结束"
    return out, sent_while_first_in_flight


@pytest.mark.parametrize("second", ["render", "export", "continue"])
def test_an_overlapping_request_waits_and_is_then_refused(tmp_path, second):
    """第一个渲染在途、它的响应会报 `unrestored > 0`：第二个（换列表的渲染 / 导出 / continue）
    必须等它回来、看到标记后被拦——不许在检查通过之后排在它后面执行。"""
    s = _Blocking(tmp_path, [_render_reply(2), _render_reply(0), {"ok": True}])
    s.state = nativesession.BARRIER
    fn = {
        "render": lambda: s.override("Fig1", B),
        "export": lambda: s.export("Fig1", A, str(tmp_path / "x.pdf")),
        "continue": s.resume,
    }[second]
    out, in_flight = _overlap(s, lambda: s.override("Fig1", A), fn)
    assert in_flight == 1, "第一个请求在途时第二帧已经发出去了"
    assert isinstance(out["second"], RunError), out
    assert out["second"].code == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert [f["cmd"] for f in s.sent] == ["override"]
    assert "Fig1" in s.inconsistent


def test_the_flag_follows_the_last_response(tmp_path):
    """两个同一份列表的渲染重叠：先发的报 2、后发的报 0——标记以后一个为准（已解除）。"""
    s = _Blocking(tmp_path, [_render_reply(2), _render_reply(0)])
    out, in_flight = _overlap(s, lambda: s.override("Fig1", A), lambda: s.override("Fig1", A))
    assert in_flight == 1
    assert not isinstance(out["second"], Exception), out
    assert s.inconsistent == {}


# ---- 第九轮：continue / detach 不放行，terminate 放行 ----


class _Transport:
    def __init__(self):
        self.oneway: list[dict] = []

    def send_oneway(self, obj, *, generation, revision):
        self.oneway.append(obj)


def _at_barrier(tmp_path, unrestored=1):
    s = _Session(tmp_path)
    s.state = nativesession.BARRIER
    s.transport = _Transport()
    s.unrestored = unrestored
    s.override("Fig1", A)
    return s


@pytest.mark.parametrize("action", ["resume", "detach"])
def test_continue_and_detach_are_refused_while_any_figure_is_inconsistent(tmp_path, action):
    s = _at_barrier(tmp_path)
    n = len(s.sent)
    assert _code(getattr(s, action)) == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert len(s.sent) == n, "continue 发出去了"
    assert s.state == nativesession.BARRIER


def test_terminate_is_still_allowed_while_inconsistent(tmp_path):
    s = _at_barrier(tmp_path)
    assert s.terminate() == {"terminated": True}
    assert [f["cmd"] for f in s.transport.oneway] == ["terminate"]


def test_a_runner_refusal_comes_back_as_the_same_code(tmp_path):
    """sidecar 这一层没看到不一致（例如还原只在放行那一刻失败）：runner 拒绝，码原样回来。"""
    s = _at_barrier(tmp_path, unrestored=0)

    def refuse(obj, timeout):
        raise pool.WorkerError("还原不回去", code=runcodes.NATIVE_FIGURE_INCONSISTENT)

    s._request = refuse  # type: ignore[method-assign]
    assert _code(s.resume) == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert s.state == nativesession.BARRIER


def test_continue_endpoint_answers_409_with_the_code(tmp_path, monkeypatch):
    s = _at_barrier(tmp_path)
    monkeypatch.setattr(nativesession.REGISTRY, "get", lambda sid: s)
    m.app.config["TESTING"] = True
    for action in ("continue", "detach"):
        resp = m.app.test_client().post(f"/api/native/sessions/{s.session_id}/{action}")
        assert resp.status_code == 409, action
        assert resp.get_json()["code"] == runcodes.NATIVE_FIGURE_INCONSISTENT


def test_the_runner_and_the_sidecar_agree_on_the_code():
    """**严格同源对**：`bridge_runner.py` 在用户的解释器里按路径执行、import 不到 `tavotto.*`，
    这个码只能各写一份——由这条逐字节对拍（与 `TERMINATE_EXIT` 同一种做法）。"""
    from tavotto.engine import bridge_runner

    assert bridge_runner.INCONSISTENT_CODE == runcodes.NATIVE_FIGURE_INCONSISTENT


# ---- 第九轮：runner 这一层（真进程链：真 tavotto run → 真用户 Python → 真 Bridge Runner） ----

#: 一段还原必抛的文字：Tavotto 改字号可以，改回脚本原样（10）时抛——还原失败只在放行那一刻
#: 发生，sidecar 此前的每一次渲染都干净，挡住它的只能是 runner 自己
PICKY_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.text import Text


class Picky(Text):
    armed = False

    def set_fontsize(self, fontsize):
        if Picky.armed and float(fontsize) == 10.0:
            raise RuntimeError("还原坏了")
        super().set_fontsize(fontsize)


fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.add_artist(Picky(0.5, 0.5, "picky", fontsize=10))
Picky.armed = True
plt.show()
print("AFTER-SHOW")
"""


def _picky_gid(session, stem):
    import json

    man = json.loads((session.out_dir / f"{stem}.json").read_text(encoding="utf-8"))
    for el in man["elements"]:
        for f in el.get("editable") or []:
            if f.get("prop") == "text" and f.get("value") == "picky":
                return el["gid"]
    raise AssertionError([e["gid"] for e in man["elements"]])


def _edit_picky(session):
    nativekit.wait_state(session, [nativesession.BARRIER])
    stem = next(iter(session.ensure_built()["stems"]))
    gid = _picky_gid(session, stem)
    resp = session.override(stem, [{"gid": gid, "prop": "fontsize", "value": 20}])
    assert resp.get("unrestored") == 0 and "Fig" not in str(session.inconsistent)
    return stem


@nativekit.needs_user_python
def test_the_runner_refuses_to_release_when_restore_fails_and_terminate_still_works(tmp_path):
    nativekit.write(tmp_path / "figure.py", PICKY_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        _edit_picky(session)
        assert session.inconsistent == {}, "前提：sidecar 这一层什么都没看到"
        assert _code(session.resume) == runcodes.NATIVE_FIGURE_INCONSISTENT
        assert session.state == nativesession.BARRIER, "runner 放行了"
        assert proc.poll() is None
        session.terminate()
        out, err = proc.communicate(timeout=120)
    assert "AFTER-SHOW" not in out, f"脚本带着改过的 Figure 往下跑了\n{err}"
    assert proc.returncode == runcodes.EXIT_TERMINATED, f"{proc.returncode}\n{err}"


@nativekit.needs_user_python
def test_a_dropped_desktop_does_not_hand_an_unrestorable_figure_back_to_the_script(tmp_path):
    """故障路径（桌面断开 / relay EOF）：还原不回去就不回到用户代码，按终止退出——
    故障路径不许比正常路径更宽松（ADR 0021 §8.1）。"""
    nativekit.write(tmp_path / "figure.py", PICKY_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        _edit_picky(session)
        session.transport.close()
        out, err = proc.communicate(timeout=180)
    assert "AFTER-SHOW" not in out, f"断开之后脚本带着改过的 Figure 往下跑了\n{err}"
    assert proc.returncode == runcodes.EXIT_TERMINATED, f"{proc.returncode}\n{err}"


# ---- 叠栈 PR：Codex #549 第九轮的两条 P1 ----


@pytest.mark.parametrize("cmd", ["export", "preview_png"])
def test_an_export_or_preview_that_cannot_restore_marks_the_figure(tmp_path, cmd):
    """r4105547311：export / 历史预览**临时套用**一份列表再还原回会话列表；那次还原失败时，
    会话里的 live 图就停在半改状态。结果里的 `unrestored` 要在锁里记下，与 override 同一条路：
    之后换列表的编辑 / 导出被拦，会话列表本身的重渲染放行（它会重试还原）。"""
    s = _Session(tmp_path)
    s.override("Fig1", A)  # 会话列表 = A（干净）

    def reply(obj, timeout):
        s.sent.append(obj)
        if obj["cmd"] in ("export", "preview_png"):
            return {"ok": True, "path": str(tmp_path / "x"), "warnings": [], "unrestored": 1}
        return {"ok": True, "manifest": {"elements": []}, "warnings": [], "unrestored": 0}

    s._request = reply  # type: ignore[method-assign]
    if cmd == "export":
        s.export("Fig1", B, str(tmp_path / "x.pdf"))
    else:
        s.preview_png("Fig1", B, 400, "v1")
    assert "Fig1" in s.inconsistent
    assert _code(lambda: s.override("Fig1", B)) == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert _code(lambda: s.export("Fig1", A, str(tmp_path / "y.pdf"))) == (
        runcodes.NATIVE_FIGURE_INCONSISTENT
    )
    s.override("Fig1", A)  # 会话列表的重渲染：放行，报 0 即解除
    assert "Fig1" not in s.inconsistent


class _GateTransport:
    """真 `_request` → 这个传输：每个请求各自等一个闸门，帧按到达顺序记下。"""

    def __init__(self):
        self.frames: list[dict] = []
        self.gates: list[threading.Event] = []
        self.entered = threading.Event()

    def request(self, obj, *, generation, revision, timeout):
        self.frames.append(obj)
        gate = threading.Event()
        self.gates.append(gate)
        self.entered.set()
        assert gate.wait(10)
        if obj["cmd"] == "override":
            return {"ok": True, "manifest": {"elements": []}, "warnings": [], "unrestored": 0}
        return {"ok": True}

    def close(self):
        pass


@pytest.mark.parametrize("action", ["resume", "detach"])
def test_a_waiting_render_cannot_slip_in_between_continue_and_the_state_change(tmp_path, action):
    """r4105547322：continue 成功之后、状态切成 CONTINUING / DETACHED 之前，等锁的渲染不许
    拿到锁——那时 runner 已经离开控制循环，这一帧要么超时毒化会话，要么在下一个屏障上
    莫名执行。状态切换与 continue 在同一段锁里。"""
    s = nativesession.NativeSession(
        session_id="native-gate",
        descriptor={"native_id": "g" * 32, "metadata": {}, "out_dir": str(tmp_path)},
    )
    s.state = nativesession.BARRIER
    t = _GateTransport()
    s.transport = t
    real_set_state = s._set_state

    def slow_set_state(state, **fields):
        # 把「continue 回来 → 切状态」之间的缝拉大：没有锁护着时等锁的渲染必然插进来
        time.sleep(0.3)
        return real_set_state(state, **fields)

    s._set_state = slow_set_state  # type: ignore[method-assign]
    out: dict = {}

    def run(fn, key):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001
            out[key] = exc

    ta = threading.Thread(target=run, args=(getattr(s, action), "release"))
    ta.start()
    assert t.entered.wait(10)
    tb = threading.Thread(target=run, args=(lambda: s.override("Fig1", A), "render"))
    tb.start()
    time.sleep(0.1)
    for _ in range(200):
        for g in list(t.gates):
            g.set()
        if not ta.is_alive() and not tb.is_alive():
            break
        time.sleep(0.02)
    assert not ta.is_alive() and not tb.is_alive()
    assert [f["cmd"] for f in t.frames] == ["continue"], "离开屏障之后还发出了渲染帧"
    assert isinstance(out["render"], RunError), out
    assert out["render"].code in (
        runcodes.NATIVE_SESSION_NOT_AT_BARRIER,
        runcodes.NATIVE_SESSION_ENDED,
    )
