"""MCP 会话：改脚本之后同一个会话自动按新脚本重跑（ADR 0192 修订 2026-11-18）的真链路。

真 MCP 桥、真 worker、真 matplotlib。独立可观测事实取自 manifest 里的真实尺寸 / 属性值与会话账本，
不只看回包里的 `script_rerun`。所有 fixture 都在 pytest 的临时目录里，改的是测试自己的脚本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import execpolicy, pool as engine_pool  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


pytestmark = pytest.mark.skipif(
    _worker_python() is None, reason="没有带科学栈的解释器，跳过真链路用例"
)

TEMPLATE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, (ax, ax2) = plt.subplots(1, 2, figsize={size})
ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("left")
ax2.plot([0, 1, 2], [4, 1, 0])
ax2.set_title("right")
fig.savefig("fig.png")
"""
TWO_AXES = TEMPLATE.format(size="(4, 2)")
WIDER = TEMPLATE.format(size="(6, 2)")
WIDEST = TEMPLATE.format(size="(8, 2)")
# 只剩一个子图：axes_1.title 没了
ONE_AXES = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(4, 2))
ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("left")
fig.savefig("fig.png")
"""
BROKEN = TWO_AXES.replace(
    'fig.savefig("fig.png")', 'raise RuntimeError("boom")\nfig.savefig("fig.png")'
)

LEFT = "axes_0.title"
RIGHT = "axes_1.title"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.delenv(execpolicy.FALLBACK_ENV, raising=False)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.shutdown_all()
    bridge.reset_root_authority()


def _project(tmp_path: Path, script: str = TWO_AXES) -> Path:
    figures = tmp_path / "fig"
    figures.mkdir()
    (figures / "fig.py").write_text(script, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"fig.py": {"entry": "__main__", "cost": "light", "stems": ["fig"]}}}
        ),
        encoding="utf-8",
    )
    return figures


def _prop(manifest: dict, gid: str, prop: str):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(p["value"] for p in el["editable"] if p["prop"] == prop)


def _fontsize(value: float, gid: str = LEFT) -> dict:
    return {"gid": gid, "prop": "fontsize", "value": value}


def _width(manifest: dict) -> float:
    return float(manifest["size_mm"][0])


def _rewrite(figures: Path, script: str) -> None:
    (figures / "fig.py").write_text(script, encoding="utf-8")


# --------------------------- open 的复用分支 --------------------------------
def test_reopen_after_script_edit_reruns_the_same_session(tmp_path):
    figures = _project(tmp_path)
    first = bridge.open_figure(str(figures), stem="fig")
    sid, old_width = first["session_id"], _width(first["manifest"])
    assert "script_rerun" not in first

    _rewrite(figures, WIDER)
    again = bridge.open_figure(str(figures), stem="fig")

    assert again["reused"] is True and again["session_id"] == sid
    assert again["script_rerun"] == {
        "reason": "script_changed",
        "patches": 0,
        "dropped": [],
        "dropped_count": 0,
    }
    # 独立事实：manifest 的真实宽度变成新脚本的（4in -> 6in），不是旧代码的重渲染
    assert _width(again["manifest"]) == pytest.approx(old_width * 6 / 4, rel=0.02)
    assert bridge.get_session(sid).manifest["size_mm"] == again["manifest"]["size_mm"]


def test_open_text_first_line_says_the_session_was_rerun(tmp_path):
    figures = _project(tmp_path)
    bridge.open_figure(str(figures), stem="fig")
    _rewrite(figures, WIDER)
    res = server.call_tool("tavotto_open_figure", {"project_path": str(figures), "stem": "fig"})
    sid = res["structuredContent"]["session_id"]
    first_line = res["content"][0]["text"].splitlines()[0]
    assert first_line.startswith(f"脚本改过，已按新脚本重跑同一个会话 {sid}（保留 0 条修改")
    assert "沿用同一个会话" not in res["content"][0]["text"]


def test_unchanged_script_does_not_rerun(tmp_path):
    figures = _project(tmp_path)
    first = bridge.open_figure(str(figures), stem="fig")
    again = bridge.open_figure(str(figures), stem="fig")
    assert again["reused"] is True and "script_rerun" not in again
    assert "script_rerun" not in bridge.run_preflight(first["session_id"])
    applied = bridge.apply_overrides(first["session_id"], [_fontsize(15.0)])
    assert "script_rerun" not in applied
    # worker 没被换：同一代
    assert applied["worker_generation"] == first["worker_generation"]


# ----------------------------- 其它入口 -------------------------------------
def test_apply_after_script_edit_keeps_patches_and_reruns(tmp_path):
    figures = _project(tmp_path)
    first = bridge.open_figure(str(figures), stem="fig")
    sid = first["session_id"]
    bridge.apply_overrides(sid, [_fontsize(19.0)])

    _rewrite(figures, WIDER)
    out = bridge.apply_overrides(sid, [_fontsize(19.0)])

    assert out["script_rerun"] == {
        "reason": "script_changed",
        "patches": 1,
        "dropped": [],
        "dropped_count": 0,
    }
    assert out["session_id"] == sid
    assert _width(out["manifest"]) > _width(first["manifest"]) * 1.4
    assert _prop(out["manifest"], LEFT, "fontsize") == 19.0
    assert bridge.get_session(sid).patches == [_fontsize(19.0)]


def test_preflight_after_script_edit_judges_the_new_script(tmp_path):
    figures = _project(tmp_path)
    first = bridge.open_figure(str(figures), stem="fig")
    sid = first["session_id"]
    _rewrite(figures, WIDER)

    out = bridge.run_preflight(sid)

    assert out["script_rerun"]["reason"] == "script_changed"
    session = bridge.get_session(sid)
    assert _width(session.manifest) > _width(first["manifest"]) * 1.4
    # 预检是 manifest 的函数：缓存不能还是旧脚本那一份
    assert session.preflight_cache is None or session.preflight_cache["patch_hash"] == (
        session.patch_hash()
    )
    # 同一条会话的第二次调用：脚本没再变，不再重跑
    assert "script_rerun" not in bridge.run_preflight(sid)


def test_export_and_session_state_also_sync(tmp_path):
    figures = _project(tmp_path)
    first = bridge.open_figure(str(figures), stem="fig")
    sid = first["session_id"]
    _rewrite(figures, WIDER)
    state = bridge.session_state(sid)
    assert state["script_rerun"]["reason"] == "script_changed"
    assert _width(state["manifest"]) > _width(first["manifest"]) * 1.4

    _rewrite(figures, WIDEST)
    exported = bridge.export(sid, formats=["png"], dpi=100, explicit_confirm=True, proof=False)
    assert exported["script_rerun"]["reason"] == "script_changed"
    assert _width(bridge.get_session(sid).manifest) > _width(state["manifest"]) * 1.2


def test_tool_text_gets_a_rerun_line_on_other_tools(tmp_path):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    _rewrite(figures, WIDER)
    res = server.call_tool("tavotto_preflight", {"session_id": sid})
    assert (
        res["content"][0]["text"]
        .splitlines()[0]
        .startswith(f"脚本改过，已按新脚本重跑同一个会话 {sid}（保留 0 条修改")
    )
    assert res["structuredContent"]["script_rerun"]["reason"] == "script_changed"


# ------------------------------ dropped -------------------------------------
def test_patch_whose_gid_vanished_is_dropped_and_recorded(tmp_path):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    bridge.apply_overrides(sid, [_fontsize(19.0, LEFT), _fontsize(17.0, RIGHT)])
    session = bridge.get_session(sid)
    session.contract = {"contract_id": "c1", "allowed": [], "allowed_adjust": []}
    session.normalized = {"patch_hash": session.patch_hash()}

    _rewrite(figures, ONE_AXES)
    out = bridge.run_preflight(sid)

    rerun = out["script_rerun"]
    assert rerun["patches"] == 1
    assert [(d["gid"], d["prop"]) for d in rerun["dropped"]] == [(RIGHT, "fontsize")]
    assert rerun["dropped"][0]["reason"] in {"element_missing", "identity_mismatch"}
    # 会话账本里只剩落得上的那条；规范化合同与验收一并作废（丢过就不再对得上）
    assert bridge.get_session(sid).patches == [_fontsize(19.0, LEFT)]
    assert session.contract is None and session.normalized is None


def test_summary_text_mentions_skipped_patches(tmp_path):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    bridge.apply_overrides(sid, [_fontsize(19.0, LEFT), _fontsize(17.0, RIGHT)])
    _rewrite(figures, ONE_AXES)
    res = server.call_tool("tavotto_preflight", {"session_id": sid})
    assert "（保留 1 条修改，另有 1 条用不了已跳过）" in res["content"][0]["text"]


# ------------------------------ 新脚本报错 ----------------------------------
def test_broken_new_script_raises_with_code_and_keeps_session_and_patches(tmp_path):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    bridge.apply_overrides(sid, [_fontsize(19.0)])

    _rewrite(figures, BROKEN)
    with pytest.raises(bridge.BridgeError) as err:
        bridge.run_preflight(sid)
    assert err.value.code
    # 会话还在、patches 还在（账本没被清）
    session = bridge.get_session(sid)
    assert session.patches == [_fontsize(19.0)]
    # 仍然报错、仍然不丢（脚本没改好之前每次调用都再试一遍）
    with pytest.raises(bridge.BridgeError):
        bridge.apply_overrides(sid, [_fontsize(19.0)])
    assert bridge.get_session(sid).patches == [_fontsize(19.0)]

    # 改好之后恢复：按新脚本重跑，patches 还在
    _rewrite(figures, WIDER)
    out = bridge.apply_overrides(sid, [_fontsize(19.0)])
    assert out["script_rerun"]["patches"] == 1
    assert _prop(out["manifest"], LEFT, "fontsize") == 19.0


# ------------------------------ refresh / close -----------------------------
def test_refresh_lists_stale_sessions_without_running_the_script(tmp_path, monkeypatch):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    other = bridge.open_figure(str(figures), stem="fig", argv=None)["session_id"]
    assert other == sid

    spawned: list[str] = []
    for name in ("get", "one_shot"):
        real = getattr(engine_pool, name)

        def spy(*a, _real=real, _name=name, **k):
            spawned.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(engine_pool, name, spy)

    clean = bridge.refresh_project(
        project_path=str(figures), http_status=lambda *a, **k: (None, None)
    )
    assert clean["stale_sessions"] == []

    _rewrite(figures, WIDER)
    stale = bridge.refresh_project(
        project_path=str(figures), http_status=lambda *a, **k: (None, None)
    )
    assert stale["stale_sessions"] == [sid]
    assert spawned == [], "refresh 不许运行用户脚本 / 起 worker"
    # 会话本身没被重跑：manifest 仍是旧的，直到下一次调用
    assert bridge.script_changed(bridge.get_session(sid)) is True

    res = server.call_tool("tavotto_refresh_project", {"session_id": sid})
    assert f"会话 {sid} 下次调用会按新脚本自动重跑，不用关掉重开" in res["content"][0]["text"]


def test_close_session_does_not_rerun(tmp_path, monkeypatch):
    figures = _project(tmp_path)
    sid = bridge.open_figure(str(figures), stem="fig")["session_id"]
    _rewrite(figures, WIDER)

    calls: list[str] = []
    real_render = bridge._render
    monkeypatch.setattr(
        bridge, "_render", lambda *a, **k: calls.append("render") or real_render(*a, **k)
    )
    closed = bridge.close_session(sid)
    assert closed["ok"] is True
    assert calls == []
    assert sid not in bridge.sessions()


# ------------------------------ 把手定长 ----------------------------------
def test_rerun_dropped_list_is_bounded_so_the_handle_fits_the_apply_budget():
    """`script_rerun` 在把手里、预算收缩时不会被剔：300 条带长原因的 dropped 也必须装进 16 KiB。"""
    miss = [
        {
            "gid": f"axes_0.title_{i}" + "x" * 300,
            "prop": "fontsize",
            "reason": "element_missing" * 40,
        }
        for i in range(300)
    ]
    rerun = {"reason": bridge.SCRIPT_RERUN_REASON, "patches": 0, **bridge._bounded_dropped(miss)}
    assert rerun["dropped_count"] == 300 and rerun["dropped_truncated"] is True
    assert len(rerun["dropped"]) == bridge.SCRIPT_RERUN_DROPPED_LIMIT
    for entry in rerun["dropped"]:
        assert all(
            len(v.encode("utf-8")) <= bridge.SCRIPT_RERUN_FIELD_BYTES for v in entry.values()
        )
    result = {
        "content": [{"type": "text", "text": "x" * 40000}],
        "structuredContent": {
            "ok": True,
            "session_id": "s-1",
            "script_rerun": rerun,
            "svg": "<svg/>" * 5000,
        },
    }
    server.fit_inline_budget(result, server.APPLY_SUMMARY_BUDGET_BYTES)
    assert server._serialized_bytes(result) <= server.APPLY_SUMMARY_BUDGET_BYTES
    assert result["structuredContent"]["script_rerun"]["dropped_count"] == 300


def test_rerun_note_counts_all_dropped_not_just_the_listed_ones():
    note = server._rerun_note("s-1", {"patches": 2, "dropped": [{}] * 8, "dropped_count": 30})
    assert "另有 30 条用不了已跳过" in note
