"""建议待办的插件工具（ADR 0214 第 3 期）：`tavotto_suggest_plan` / `_apply` / `_status`。

盯的是：

* **只算不写**：出方案不改会话的 override，试渲染动过的热 worker 放回会话那一份；
* **同一套守卫**：交进来的建议走与改图助手同一份 `validate_proposal`（认不出的元素、没效果的修改逐条回 `ok: false` 与原因）；
* **用户勾好才写**：apply 只写勾着的图内待办（B0 ⊕ 勾着的组），脚本待办原样交回宿主；
* **不在别的底子上套旧修改**：出方案之后图被改过 → `suggest_plan_stale`；别的会话的方案 → `suggest_plan_unknown`。

假 worker 跑在 .venv 里（不要 matplotlib），全链路渲染在 `tests/test_changeplan_endpoints.py` 的桌面端点那一侧。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from support import artifactbytes  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402
from test_mcp_server import (  # noqa: E402, F401
    FakeWorker,
    _body,
    _call,
    _clean_sessions,
    _open,
    project,
)

PNG = base64.b64encode(artifactbytes.solid_png(8, 6)).decode("ascii")


class SuggestWorker(FakeWorker):
    """在协议层假 worker 上补「出一张位图 + manifest」：`fontsize` 跟着 patch 变，`spine_*` 不变（= 改了没效果）。"""

    TYPES = {
        "fontsize": {"type": "number", "min": 1, "max": 200},
        "direction": {"type": "enum", "options": ["in", "out", "inout"]},
    }

    def _manifest(self, patches):
        man = super()._manifest(patches)
        for el in man["elements"]:
            for f in el["editable"]:
                f.update(self.TYPES.get(f["prop"], {"type": "bool"}))
        return man

    def preview_png_snapshot(self, stem, patches, width):
        self.calls.append(("snapshot", stem, list(patches)))
        return {"png": PNG, "manifest": self._manifest(patches)}


@pytest.fixture
def worker(monkeypatch):
    w = SuggestWorker()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: w)
    return w


FONT = {"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}
TODOS = [
    {"label": "横轴刻度字号改小", "why": "和正文一致", "kind": "figure", "changes": [FONT]},
    {
        "label": "去掉上边框",
        "kind": "figure",
        "changes": [{"gid": "axes_0", "prop": "spine_top", "value": False}],
    },
    {"label": "加误差线（SD）", "why": "三次重复", "kind": "script"},
    {
        "label": "改一个不存在的元素",
        "kind": "figure",
        "changes": [{"gid": "nope", "prop": "fontsize", "value": 8}],
    },
]


def _plan(sid: str, todos=TODOS) -> dict:
    res = _call("tavotto_suggest_plan", {"session_id": sid, "todos": todos})
    assert not res.get("isError"), _body(res)
    return res


def test_tools_are_listed_only_when_the_engine_has_the_flow(monkeypatch):
    names = {t["name"] for t in server._tools()}
    assert {"tavotto_suggest_plan", "tavotto_suggest_apply", "tavotto_suggest_status"} <= names
    monkeypatch.setattr(bridge, "suggest_available", lambda: False)
    names = {t["name"] for t in server._tools()}
    assert not names & {"tavotto_suggest_plan", "tavotto_suggest_apply", "tavotto_suggest_status"}


def test_plan_lists_todos_reports_each_suggestion_and_writes_nothing(project, worker):
    sid = _open(project)
    before = list(bridge.get_session(sid).patches)
    res = _plan(sid)
    out = _body(res)
    by_label = {t["label"]: t for t in out["todos"]}
    assert set(by_label) == {"横轴刻度字号改小", "加误差线（SD）"}
    assert by_label["横轴刻度字号改小"]["kind"] == "figure"
    assert by_label["加误差线（SD）"]["kind"] == "script"
    results = {r["index"]: r for r in out["results"]}
    assert results[1]["ok"] and results[3]["ok"]
    assert results[2] == {**results[2], "ok": False, "reason": "no_effect"}
    assert results[4] == {**results[4], "ok": False, "reason": "unknown_element"}
    # 只算不写：会话的 override 没动，热 worker 最后放回会话那一份
    assert bridge.get_session(sid).patches == before
    assert [c for c in worker.calls if c[0] == "override"][-1][2] == before
    # 给人看的那份：逐条待办 + 没列进去的原因 + 修改后的图
    text = res["content"][0]["text"]
    assert "横轴刻度字号改小" in text and "图里没有这个元素" in text
    assert res["content"][1]["type"] == "image"
    assert "png_base64" not in out["preview"]


def test_apply_writes_only_the_ticked_figure_todos_and_hands_back_scripts(project, worker):
    sid = _open(project)
    out = _body(_plan(sid))
    keys = {t["label"]: t["key"] for t in out["todos"]}
    done = _body(
        _call(
            "tavotto_suggest_apply",
            {
                "session_id": sid,
                "plan_id": out["plan_id"],
                "keys": [keys["横轴刻度字号改小"], keys["加误差线（SD）"]],
            },
        )
    )
    assert done["applied"] == [keys["横轴刻度字号改小"]]
    assert done["script_todos"] == [{"key": keys["加误差线（SD）"], "label": "加误差线（SD）"}]
    patches = bridge.get_session(sid).patches
    assert any(
        p["gid"] == FONT["gid"] and p["prop"] == "fontsize" and p["value"] == 7.0 for p in patches
    )
    status = _body(_call("tavotto_suggest_status", {"session_id": sid, "plan_id": out["plan_id"]}))
    assert status["state"] == "applied" and status["by"] == "agent"
    assert sorted(status["chosen"]) == sorted([keys["横轴刻度字号改小"], keys["加误差线（SD）"]])
    again = _call(
        "tavotto_suggest_apply",
        {"session_id": sid, "plan_id": out["plan_id"], "keys": [keys["横轴刻度字号改小"]]},
    )
    assert again["isError"] and _body(again)["code"] == "suggest_plan_applied"


def test_unticked_figure_todo_is_not_written(project, worker):
    sid = _open(project)
    out = _body(_plan(sid))
    script_key = next(t["key"] for t in out["todos"] if t["kind"] == "script")
    done = _body(
        _call(
            "tavotto_suggest_apply",
            {"session_id": sid, "plan_id": out["plan_id"], "keys": [script_key]},
        )
    )
    assert done["applied"] == []
    assert bridge.get_session(sid).patches == []


def test_figure_changed_after_the_plan_is_refused(project, worker):
    sid = _open(project)
    out = _body(_plan(sid))
    _call(
        "tavotto_apply_overrides",
        {
            "session_id": sid,
            "patches": [{"gid": "axes_0.xticks", "prop": "direction", "value": "out"}],
        },
    )
    status = _body(_call("tavotto_suggest_status", {"session_id": sid, "plan_id": out["plan_id"]}))
    assert status["state"] == "proposed" and status["stale"] is True
    res = _call(
        "tavotto_suggest_apply",
        {"session_id": sid, "plan_id": out["plan_id"], "keys": [out["todos"][0]["key"]]},
    )
    assert res["isError"] and _body(res)["code"] == "suggest_plan_stale"


def test_bad_keys_and_unknown_plans_are_refused(project, worker):
    sid = _open(project)
    out = _body(_plan(sid))
    res = _call(
        "tavotto_suggest_apply", {"session_id": sid, "plan_id": out["plan_id"], "keys": ["t99"]}
    )
    assert res["isError"] and _body(res)["code"] == "bad_todo_keys"
    for plan_id in ("0" * 24, "../etc", 3):
        res = _call("tavotto_suggest_status", {"session_id": sid, "plan_id": plan_id})
        assert res["isError"] and _body(res)["code"] == "suggest_plan_unknown"


def test_another_sessions_plan_is_unknown(project, worker):
    sid = _open(project)
    out = _body(_plan(sid))
    bridge.close_session(sid)
    other = _open(project)
    assert other != sid
    res = _call("tavotto_suggest_status", {"session_id": other, "plan_id": out["plan_id"]})
    assert res["isError"] and _body(res)["code"] == "suggest_plan_unknown"


@pytest.mark.parametrize("todos", [[], "x", [{"label": "a", "kind": "figure"}] * 13])
def test_malformed_todos_are_refused(project, worker, todos):
    sid = _open(project)
    res = _call("tavotto_suggest_plan", {"session_id": sid, "todos": todos})
    assert res["isError"] and _body(res)["code"] == "bad_todos"


def test_sessions_running_in_real_python_are_not_probed(project, worker):
    """与桌面 `safe_only` 同一处置：真实 Python 进程里的图不拿来试渲染候选。"""
    sid = _open(project)
    bridge.get_session(sid).native = object()
    try:
        res = _call("tavotto_suggest_plan", {"session_id": sid, "todos": TODOS[:1]})
    finally:
        bridge.get_session(sid).native = None
    assert res["isError"] and _body(res)["code"] == "suggest_native_unsupported"
    assert not [c for c in worker.calls if c[0] == "snapshot"]


def test_script_edited_outside_the_session_is_rerun_before_planning(project, worker):
    """宿主改了 .py 再出方案：先按新脚本重跑同一个会话（`@_synced`，与 apply / preflight 同一入口），方案描述的是新脚本的图。"""
    sid = _open(project)
    (project / "fig1.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    out = _body(_plan(sid, TODOS[:1]))
    assert out["script_rerun"]["reason"] == bridge.SCRIPT_RERUN_REASON
    # 重跑之后的方案是当下的：直接能写
    done = _body(
        _call(
            "tavotto_suggest_apply",
            {"session_id": sid, "plan_id": out["plan_id"], "keys": [out["todos"][0]["key"]]},
        )
    )
    assert done["applied"] == [out["todos"][0]["key"]]
