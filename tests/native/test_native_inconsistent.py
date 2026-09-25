"""native 图「与文档不一致」这一档（Codex #549 第八轮）。

引擎撤掉一条改动时还原抛了，账留着、下一次渲染重试（`overrides.apply` 的
`FigState.unrestored`，`tests/test_restore_failure_retry.py`）。safe worker 可以作废重起，
native 会话是用户自己的 Python（ADR 0021），不杀不断开——于是在还原成功之前，这张 live
Figure 与文档不一致，这里把它挡在 offline 同一条 409 路上：

1. 判据只看 v1 render 结果里的结构化字段 `unrestored`，不解析 warning 文案；
2. 不一致期间只放行**同一份列表**的重渲染（它会重试还原），报 0 即自动解除；
3. 换列表的编辑、导出、历史预览一律 `native_figure_inconsistent`（409）；
4. 导出与 offline 走同一条路：拿 live 图的那一步抛 `RunError`，作业不拿它交差。
"""

from __future__ import annotations

import pytest

from tavotto import app as m
from tavotto.engine import nativesession, runcodes
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
