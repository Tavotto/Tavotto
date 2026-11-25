# ruff: noqa: F811 - `env` 是从端点用例导入的 fixture，用例参数同名
"""批注重改（ADR 0206 §五，`POST /api/change-plans/<id>/revise`）：同一套假 worker / 假模型走完整条后台任务链。

判据的主语：授权在 `engine/changeplan.py`（未授权回 `not_licensed`、什么都不渲染）；第 2 版是**新方案**（`version` + 1、`parent` 指向上一版、base 仍是原 B0）；
批注只进 `<comments>` 数据块、模型看到的是**上一版之后**的元素表；卡上的改动**相对 B0** 现算，批注出的组标 `origin: "note"`。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import changeplan as cp
from tests.test_changeplan import ch
from tests.test_changeplan_endpoints import B0, env, post, run, wait  # noqa: F401  (env 是 fixture)

NOTES = [
    {
        "id": "n1",
        "anchor": {"kind": "element", "gid": "ax0.title", "box": [0.1, 0.0, 0.5, 0.1]},
        "text": "标题再大一点",
    }
]


@pytest.fixture
def licensed(monkeypatch):
    monkeypatch.setattr(cp.engine_license, "has", lambda f: f == cp.FEATURE)


def first_plan(client):
    out = run(client)
    assert out["state"] == "ready"
    return out["plan"]


def revise(client, plan_id, **over):
    body = {"notes": NOTES, **over}
    return client.post(f"/api/change-plans/{plan_id}/revise", json=body)


def run_revise(client, plan_id, **over):
    resp = revise(client, plan_id, **over)
    assert resp.status_code == 202, resp.get_json()
    return wait(client, resp.get_json()["plan_id"])


def test_unlicensed_gets_not_licensed_and_nothing_runs(env, monkeypatch):
    _m, client, _figs, worker, model, _retired = env
    plan = first_plan(client)
    monkeypatch.setattr(cp.engine_license, "has", lambda f: False)
    n_calls, n_model = len(worker.calls), model.calls
    resp = revise(client, plan["id"])
    assert resp.status_code == 200 and resp.get_json() == {"ok": False, "code": "not_licensed"}
    assert len(worker.calls) == n_calls and model.calls == n_model


def test_licensed_revise_makes_version_two_relative_to_b0(env, licensed):
    _m, client, _figs, worker, model, retired = env
    v1 = first_plan(client)
    model.result = {
        "groups": [{"comment_id": "n1", "changes": [ch("ax0.title", "fontsize", 14)]}],
        "unhandled": [],
    }
    seen = {}
    real = model.__call__

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    cp._CALL = spy
    worker.calls.clear()
    out = run_revise(client, v1["id"])
    assert out["state"] == "ready"
    v2 = out["plan"]
    assert v2["id"] != v1["id"] and v2["version"] == 2 and v2["parent"] == v1["id"]
    assert v2["base"] == v1["base"] and v2["base_patches"] == v1["base_patches"] == B0
    # 上一版的线宽改动带着走（相对 B0 现算），批注的标题字号改动标 note
    by = {g["comment_id"]: g for g in v2["groups"]}
    assert set(by) == {"c1", "n1"}
    assert by["c1"]["origin"] == "review" and by["n1"]["origin"] == "note"
    assert by["n1"]["label"] == "标题再大一点"
    (chg,) = by["n1"]["changes"]
    assert (chg["target_gid"], chg["prop"], chg["before"], chg["after"]) == (
        "ax0.title",
        "fontsize",
        8,
        14,
    )
    patches = {(p["gid"], p["prop"]): p["value"] for p in v2["candidate_patches"]}
    assert patches[("ax0.line0", "linewidth")] == 3 and patches[("ax0.title", "fontsize")] == 14
    # 起点 = 上一版的候选：模型看到上一版之后的元素表与改动摘要；批注只在数据块里
    assert "标题再大一点" in seen["user"] and "<previous_changes>" in seen["user"]
    assert "标题再大一点" not in seen["system"]
    user = seen["user"]
    assert '"value":3' in user.split("<elements>")[1]  # 线宽已是上一版的 3
    # 第 1 版仍在、可切回
    assert client.get(f"/api/change-plans/{v1['id']}").get_json()["state"] == "ready"
    assert retired  # 渲染过候选：热 worker 作废、前端要重放
    assert out["replay_required"] is True


def test_note_overriding_a_previous_change_replaces_it(env, licensed):
    _m, client, _figs, _worker, model, _retired = env
    v1 = first_plan(client)
    model.result = {
        "groups": [{"comment_id": "n1", "changes": [ch("ax0.line0", "linewidth", 5)]}],
        "unhandled": [],
    }
    out = run_revise(client, v1["id"])
    v2 = out["plan"]
    assert [g["comment_id"] for g in v2["groups"]] == ["n1"]  # c1 的同一属性被批注改掉了
    assert v2["groups"][0]["changes"][0]["after"] == 5
    assert {(p["gid"], p["prop"]): p["value"] for p in v2["candidate_patches"]}[
        ("ax0.line0", "linewidth")
    ] == 5


def test_note_with_no_effect_ends_in_unhandled(env, licensed, env_worker=None):
    _m, client, _figs, worker, model, _retired = env
    v1 = first_plan(client)
    worker.ignored = {"fontsize"}
    model.result = {
        "groups": [{"comment_id": "n1", "changes": [ch("ax0.title", "fontsize", 14)]}],
        "unhandled": [],
    }
    v2 = run_revise(client, v1["id"])["plan"]
    assert {u["comment_id"]: u["reason"] for u in v2["unhandled"]}["n1"] == "no_effect"
    assert [g["comment_id"] for g in v2["groups"]] == ["c1"]  # 上一版的组仍在


def test_unknown_plan_bad_notes_and_busy(env, licensed):
    _m, client, _figs, worker, model, _retired = env
    v1 = first_plan(client)
    n_model = model.calls
    assert revise(client, "0" * 24).get_json()["code"] == "change_plan_unknown"
    for notes in (
        [],
        "x",
        [{"id": "n1", "anchor": {"kind": "overall"}, "text": "  "}],
        [{"id": "n1"}],
    ):
        resp = revise(client, v1["id"], notes=notes)
        assert resp.status_code == 400 and resp.get_json()["code"] == "invalid_change_plan"
    resp = revise(client, v1["id"], client_request_id="short")
    assert resp.status_code == 400
    assert model.calls == n_model
    # 互斥：同一张图已有任务在跑
    cp._BY_FIGURE[(next(iter(cp._TASKS.values())).project, "Kin.pdf")] = "run"
    cp._TASKS["run"] = cp.Task("run", next(iter(cp._TASKS.values())).project, "Kin.pdf", None)
    resp = revise(client, v1["id"])
    assert resp.status_code == 409 and resp.get_json()["code"] == "change_plan_busy"


def test_same_request_id_returns_same_task(env, licensed):
    _m, client, _figs, _worker, model, _retired = env
    v1 = first_plan(client)
    a = revise(client, v1["id"], client_request_id="rid-12345678").get_json()
    b = revise(client, v1["id"], client_request_id="rid-12345678").get_json()
    assert a["plan_id"] == b["plan_id"]
    wait(client, a["plan_id"])


def test_figure_changed_since_version_one_fails_without_override(env, licensed):
    _m, client, figs, worker, model, _retired = env
    v1 = first_plan(client)
    (figs / "fig.py").write_text("print('v2')\n", encoding="utf-8")
    n = len(worker.calls)
    out = run_revise(client, v1["id"])
    assert out["state"] == "failed" and out["code"] == "change_plan_figure_changed"
    assert all(k != "override" for k, _ in worker.calls[n:])


def test_clean_notes_shapes():
    assert cp.clean_notes([{"id": "a", "anchor": {"kind": "overall"}, "text": " x "}]) == [
        {"id": "a", "anchor": {"kind": "overall"}, "text": "x", "resolved": False}
    ]
    assert (
        cp.clean_notes([{"id": "a", "anchor": {"kind": "element", "gid": "g"}, "text": "x"}])
        is None
    )
    dup = {"id": "a", "anchor": {"kind": "overall"}, "text": "x"}
    assert cp.clean_notes([dup, dup]) is None
    assert (
        json.dumps(cp.clean_notes([dict(dup, text="y" * 900)])[0]["text"]).count("y")
        == cp.MAX_COMMENT_CHARS
    )
