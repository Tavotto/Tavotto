"""修改方案端点（ADR 0206 §一，`/api/change-plans*`）：用假 worker 与假模型走完整条后台任务链，**不真起 worker、不真调模型**。

判据的主语：

* 「只算不写」量的是**假 worker 收到的调用序列**（B0 出图 → 候选 override → 候选出图 → 还原 override(B0)）与项目目录的文件快照；
* 「热态不变」量的是 worker 最后一次收到的 override 列表 == B0，且没有被作废（`pool.invalidate` 没被调）；
* 「终态都有稳定 code」量的是轮询到的 `state` / `code`，不是 HTTP 500。
"""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from tavotto.engine import ai_structured, changeplan as cp, pool as engine_pool
from tests.test_changeplan import BASE, COMMENTS, ch

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
B0 = [{"gid": "ax0.title", "prop": "fontsize", "value": 8}]


class FakeWorker:
    script_name = "fig.py"
    figures_dir = "/changeplan-test-figures"
    execution_replaces_current = False

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.ignored: set[str] = set()  # 渲染时被静默跳过的 prop
        self.warn = lambda patches: []
        self.explode_on = None  # 候选 override 抛 WorkerError
        self.on_snapshot = None

    def _manifest(self, patches):
        out = json.loads(json.dumps(BASE))
        for p in patches:
            if p["prop"] in self.ignored:
                continue
            for e in out["elements"]:
                if e["gid"] == p["gid"]:
                    for f in e["editable"]:
                        if f["prop"] == p["prop"]:
                            f["value"] = p["value"]
        return out

    def preview_png_snapshot(self, stem, patches, width, **_kw):
        self.calls.append(("snap", list(patches)))
        if self.on_snapshot:
            self.on_snapshot(patches)
        return {"png": base64.b64encode(PNG).decode(), "manifest": self._manifest(patches)}

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.calls.append(("override", list(patches)))
        if self.explode_on and self.explode_on(patches):
            raise engine_pool.WorkerError("渲染炸了（模拟）")
        return {"manifest": self._manifest(patches), "warnings": self.warn(patches)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(cp.config, "data_dir", lambda: tmp_path / "data")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig.py").write_text("print('v1')\n", encoding="utf-8")
    m.open_project(str(figs))
    worker = FakeWorker()
    retired: list[tuple] = []
    monkeypatch.setattr(m, "_resolve_engine_worker", lambda rel_id, **kw: (worker, "Kin"))
    monkeypatch.setattr(
        m.engine_pool,
        "invalidate",
        lambda script, root=None, run=None, **kw: retired.append((script, root)),
    )
    monkeypatch.setattr(cp, "_PRECHECK", lambda: (True, ""))
    monkeypatch.setattr(cp, "_RESOLVE", lambda budget=None: ({"kind": "fake"}, "", ""))
    monkeypatch.setattr(cp, "_TASKS", {})
    monkeypatch.setattr(cp, "_BY_FIGURE", {})
    monkeypatch.setattr(cp, "_BY_RID", {})
    model = Model()
    monkeypatch.setattr(cp, "_CALL", model)
    try:
        yield m, m.app.test_client(), figs, worker, model, retired
    finally:
        m.reset_projects()


class Model:
    """假的 `call_structured`：`result` 是模型回的 JSON（过 `validate`）或一个现成的 `StructuredResult`。"""

    def __init__(self):
        self.result = {
            "groups": [{"comment_id": "c1", "changes": [ch("ax0.line0", "linewidth", 3)]}],
            "unhandled": [{"comment_id": "c2", "reason": "needs_script"}],
        }
        self.calls = 0
        self.before = None

    def __call__(self, **kw):
        self.calls += 1
        if self.before:
            self.before()
        if isinstance(self.result, ai_structured.StructuredResult):
            return self.result
        return ai_structured.StructuredResult(True, value=kw["validate"](self.result))


def _comments():
    return [{**c, "v": 1, "created_at": "2026-11-24T00:00:00Z", "replies": []} for c in COMMENTS]


def post(client, **over):
    body = {"id": "Kin.pdf", "patches": B0, "source": "review", "comments": _comments(), **over}
    return client.post("/api/change-plans", json=body)


def wait(client, plan_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        out = client.get(f"/api/change-plans/{plan_id}").get_json()
        if out.get("state") != "running":
            return out
        time.sleep(0.01)
    raise AssertionError("方案任务没有在时限内结束")


def run(client, **over):
    resp = post(client, **over)
    assert resp.status_code == 202, resp.get_json()
    return wait(client, resp.get_json()["plan_id"])


def tree(figs):
    return sorted(
        (p.relative_to(figs).as_posix(), p.stat().st_size) for p in figs.rglob("*") if p.is_file()
    )


# --------------------------------------------------------------------------- 成功路径
def test_success_gives_a_plan_computed_from_the_rendered_manifests(env):
    m, client, figs, worker, model, retired = env
    before_tree = tree(figs)
    out = run(client)
    assert out["state"] == "ready" and "code" not in out
    plan = out["plan"]
    assert plan["source"] == "review" and plan["version"] == 1
    assert plan["base"]["target"] == "Kin.pdf" and plan["base"]["fingerprint"].startswith("sha256:")
    (group,) = plan["groups"]
    assert group["comment_id"] == "c1" and group["label"] == "线太细了" and group["atomic"] is False
    patch = {"gid": "ax0.line0", "prop": "linewidth", "value": 3, "identity": "id-line0"}
    assert group["changes"] == [
        {"target_gid": "ax0.line0", "prop": "linewidth", "before": 1.5, "after": 3, "patch": patch}
    ]
    assert plan["base_patches"] == B0
    assert plan["candidate_patches"] == cp.patchspec.canonicalize(B0 + [patch])
    assert (
        group["boxes"]["before"]
        == group["boxes"]["after"]
        == [{"gid": "ax0.line0", "box": [0.1, 0.2, 0.3, 0.1]}]
    )
    assert {u["comment_id"]: u["reason"] for u in plan["unhandled"]} == {"c2": "needs_script"}
    assert plan["preview"] == {
        "before": f"/api/change-plans/{plan['id']}/before.png",
        "after": f"/api/change-plans/{plan['id']}/after.png",
    }
    for name in ("before", "after"):
        img = client.get(plan["preview"][name])
        assert img.status_code == 200 and img.data == PNG and img.mimetype == "image/png"
    # 调用序列：B0 出图 → 基线 override(B0) → 候选 override → 候选出图；不还原 B0（那可能已过期），而是作废 worker、要求前端重放
    kinds = [k for k, _ in worker.calls]
    assert kinds == ["snap", "override", "override", "snap"]
    assert worker.calls[0][1] == worker.calls[1][1] == B0
    assert (
        worker.calls[2][1]
        == worker.calls[3][1]
        == B0 + [{"gid": "ax0.line0", "prop": "linewidth", "value": 3, "identity": "id-line0"}]
    )
    assert len(retired) == 1 and out["replay_required"] is True and plan["replay_required"] is True
    assert tree(figs) == before_tree  # 项目目录一个字节没动
    rev = m.engine_preparation._script_revision(str(figs), "fig.py")
    assert plan["base"]["fingerprint"] == cp.fingerprint(rev, B0)
    assert model.calls == 1


def test_prompt_sent_to_the_model_carries_comments_as_data_and_the_real_elements(env):
    _m, client, _figs, _worker, model, _retired = env
    seen = {}
    real = model.__call__

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    cp._CALL = spy
    run(client)
    assert "线太细了" in seen["user"] and "已经处理过" not in seen["user"]
    assert "ax0.line0" in seen["user"] and "ax0.hidden" not in seen["user"]


# --------------------------------------------------------------------------- 同步终态
def test_native_figures_are_refused_before_any_render(env, monkeypatch):
    m, client, _figs, worker, model, _retired = env
    monkeypatch.setattr(m, "_specfix_profile", lambda rel: m.engine_enginesession.PROFILE_NATIVE)
    resp = post(client)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "change_plan_native_unsupported" and body["params"] == {
        "product": "Tavotto"
    }
    assert worker.calls == [] and model.calls == 0


def test_a_figure_without_a_script_is_refused(env, monkeypatch):
    m, client, _figs, worker, model, _retired = env
    from werkzeug.exceptions import NotFound

    def gone(rel_id, **kw):
        raise NotFound()

    monkeypatch.setattr(m, "_resolve_engine_worker", gone)
    resp = post(client)
    assert resp.status_code == 409 and resp.get_json()["code"] == "change_plan_not_editable"
    assert worker.calls == [] and model.calls == 0


@pytest.mark.parametrize(
    "reason,code",
    [
        ("no_connection", "change_plan_no_connection"),
        ("unsupported_connection", "change_plan_unsupported_connection"),
    ],
)
def test_no_usable_ai_connection_is_a_409_before_anything_runs(env, monkeypatch, reason, code):
    _m, client, _figs, worker, model, _retired = env
    monkeypatch.setattr(cp, "_PRECHECK", lambda: (False, reason))
    resp = post(client)
    assert resp.status_code == 409 and resp.get_json()["code"] == code
    assert worker.calls == [] and model.calls == 0


def test_a_connection_the_sync_precheck_cannot_judge_fails_in_the_thread_with_the_same_code(
    env, monkeypatch
):
    _m, client, _figs, worker, model, _retired = env
    monkeypatch.setattr(cp, "_RESOLVE", lambda budget=None: (None, "no_connection", "none"))
    out = run(client)  # 预检放行（判不了），线程里真解析才发现没有
    assert out["state"] == "failed" and out["code"] == "change_plan_no_connection"
    assert [k for k, _ in worker.calls] == ["snap"] and model.calls == 0


def test_the_default_precheck_never_probes_for_cli_connections(monkeypatch):
    from tavotto.engine import ai_bridge, ai_connections

    def probe(*a, **k):
        raise AssertionError("同步预检不许探测 CLI")

    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    monkeypatch.setattr(ai_bridge, "capabilities", probe)
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: None)
    assert cp._default_precheck() == (True, "")  # 还没探测过：判不了，放行
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: {"connections": []})
    assert cp._default_precheck() == (False, "no_connection")  # 缓存有、没有可用连接：判得定
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: False)
    assert cp._default_precheck() == (False, "no_connection")
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(
        ai_connections, "first_key_connection", lambda refresh=False: {"kind": "key"}
    )
    assert cp._default_precheck() == (True, "")


@pytest.mark.parametrize(
    "over,code",
    [
        ({"comments": []}, "change_plan_no_comments"),
        ({"comments": [dict(c, resolved=True) for c in _comments()]}, "change_plan_no_comments"),
        ({"comments": "x"}, "invalid_change_plan"),
        ({"comments": None, "share_id": None}, "invalid_change_plan"),
        ({"share_id": "rs_AAAAAAAAAAAAAAAA"}, "invalid_change_plan"),  # 与 comments 二选一
        ({"source": "assistant"}, "invalid_change_plan"),
        ({"id": ""}, "invalid_change_plan"),
        ({"patches": "x"}, "invalid_change_plan"),
        ({"client_request_id": "short"}, "invalid_change_plan"),
        ({"figure_origin": {}}, "invalid_change_plan"),
        ({"patches": [{"gid": "a"}]}, "invalid_patches"),
    ],
)
def test_bad_requests_get_a_stable_code_and_start_nothing(env, over, code):
    _m, client, _figs, worker, model, _retired = env
    resp = post(client, **over)
    assert resp.status_code == 400 and resp.get_json()["code"] == code
    assert worker.calls == [] and model.calls == 0 and cp._TASKS == {}


def test_a_non_object_body_is_a_stable_400(env):
    _m, client, *_ = env
    resp = client.post("/api/change-plans", data="[1,2]", content_type="application/json")
    assert resp.status_code == 400 and resp.get_json()["code"] == "invalid_change_plan"


# --------------------------------------------------------------------------- 后台终态
@pytest.mark.parametrize("reason,code", sorted(cp.MODEL_FAILURE_CODES.items()))
def test_every_model_failure_reason_ends_as_a_failed_task_with_its_code(env, reason, code):
    _m, client, _figs, worker, model, retired = env
    model.result = ai_structured.StructuredResult(False, reason=reason, detail="x")
    out = run(client)
    assert out["state"] == "failed" and out["code"] == code and out["error"]
    assert [k for k, _ in worker.calls] == ["snap"]  # 只渲染过 B0（状态中立）
    assert retired == []
    assert client.get(f"/api/change-plans/{out['plan_id']}/after.png").status_code == 404


def test_a_model_reply_with_nothing_usable_is_an_empty_plan_not_an_error(env):
    _m, client, _figs, worker, model, _retired = env
    model.result = {"groups": [], "unhandled": [{"comment_id": "c1", "reason": "unclear"}]}
    out = run(client)
    assert out["state"] == "ready" and out["code"] == "change_plan_empty"
    assert out["plan"]["groups"] == []
    assert {u["comment_id"]: u["reason"] for u in out["plan"]["unhandled"]} == {
        "c1": "unclear",
        "c2": "not_addressed",
    }
    assert [k for k, _ in worker.calls] == ["snap"]  # 没有候选，不做候选渲染
    assert out["replay_required"] is False and out["plan"]["replay_required"] is False
    assert client.get(out["plan"]["preview"]["after"]).status_code == 200  # 仍是一个可展示的结果


def test_a_change_the_renderer_ignores_never_counts_as_changed(env):
    _m, client, _figs, worker, _model, _retired = env
    worker.ignored = {"linewidth"}
    out = run(client)
    assert out["state"] == "ready" and out["code"] == "change_plan_empty"
    assert {"comment_id": "c1", "reason": "no_effect"} in out["plan"]["unhandled"]


def test_a_new_warning_in_the_candidate_means_no_plan_and_the_worker_is_retired(env):
    _m, client, _figs, worker, _model, retired = env
    worker.warn = lambda patches: (
        [{"code": "x", "message": "boom"}] if len(patches) > len(B0) else []
    )
    out = run(client)
    assert (
        out["state"] == "failed"
        and out["code"] == "change_plan_render_failed"
        and out["detail"] == "warning"
    )
    assert [k for k, _ in worker.calls] == ["snap", "override", "override", "snap"]
    assert len(retired) == 1 and out["replay_required"] is True  # 动过热态：作废、前端重放
    assert (
        client.get(f"/api/change-plans/{out['plan_id']}/before.png").status_code == 404
    )  # 没有方案、没有落盘


def test_a_warning_that_was_already_there_before_the_change_does_not_block(env):
    _m, client, _figs, worker, _model, _retired = env
    worker.warn = lambda patches: [{"code": "preexisting"}]
    assert run(client)["state"] == "ready"


def test_a_render_that_raises_fails_the_task_and_retires_the_worker(env):
    _m, client, _figs, worker, _model, retired = env
    worker.explode_on = lambda patches: len(patches) > len(B0)
    out = run(client)
    assert (
        out["state"] == "failed"
        and out["code"] == "change_plan_render_failed"
        and out["detail"] == "error"
    )
    assert (
        len(retired) == 1 and out["replay_required"] is True
    )  # 热态不可信：作废，下一次请求冷重放


def test_before_render_failure_is_a_stable_code_and_touches_nothing(env):
    m, client, _figs, worker, _model, retired = env

    def boom(stem, patches, width, **kw):
        raise engine_pool.WorkerError("出图炸了（模拟）")

    worker.preview_png_snapshot = boom
    out = run(client)
    assert out["state"] == "failed" and out["code"] == "change_plan_render_failed"
    assert retired == []


def test_a_script_edited_during_the_model_call_voids_the_plan(env):
    _m, client, figs, worker, model, _retired = env
    model.before = lambda: (figs / "fig.py").write_text("print('v2 edited')\n", encoding="utf-8")
    out = run(client)
    assert out["state"] == "failed" and out["code"] == "change_plan_figure_changed"
    assert [k for k, _ in worker.calls] == ["snap"]


def test_the_task_thread_is_bound_to_the_requesting_project(env, monkeypatch, tmp_path):
    """开两个项目、请求**不是默认的那个**：线程里没有 request context，不绑定就会落到默认项目（方案落进别的图库）。"""
    m, client, _figs, _worker, _model, _retired = env
    other = tmp_path / "figures2"
    other.mkdir()
    (other / "fig.py").write_text("print('other')\n", encoding="utf-8")
    m.open_project(str(other))
    ids = set(m.PROJECTS)
    assert len(ids) == 2
    target = next(i for i in ids if i != m.DEFAULT_PROJECT)
    seen: list[str] = []
    real = m._engine_worker

    def spy(rel_id, **kw):
        seen.append(m.current_ctx().id)
        return real(rel_id, **kw)

    monkeypatch.setattr(m, "_engine_worker", spy)
    resp = client.post(
        "/api/change-plans",
        json={"id": "Kin.pdf", "patches": B0, "source": "review", "comments": _comments()},
        headers={"X-Tavotto-Project": target},
    )
    assert resp.status_code == 202
    plan_id = resp.get_json()["plan_id"]
    deadline = time.time() + 10
    while (
        time.time() < deadline
        and cp.load(target, plan_id) is None
        and not cp.task_of(target, plan_id).code
    ):
        time.sleep(0.01)
    assert seen == [target, target]  # 同步一次 + 线程里一次，都是请求指名的那个项目
    assert cp.load(target, plan_id) is not None
    assert cp.load(m.DEFAULT_PROJECT, plan_id) is None
    out = client.get(
        f"/api/change-plans/{plan_id}", headers={"X-Tavotto-Project": target}
    ).get_json()
    assert out["state"] == "ready"
    assert client.get(f"/api/change-plans/{plan_id}").status_code == 404  # 默认项目看不到


# --------------------------------------------------------------------------- 并发、去重、放弃、隔离
def test_one_plan_task_per_figure_and_it_frees_up_afterwards(env):
    _m, client, _figs, _worker, model, _retired = env
    started, release = threading.Event(), threading.Event()

    def hold():
        started.set()
        assert release.wait(10)

    model.before = hold
    first = post(client, client_request_id="rid-aaaaaaaa")
    assert first.status_code == 202
    assert started.wait(10)
    busy = post(client, client_request_id="rid-bbbbbbbb")
    assert busy.status_code == 409 and busy.get_json()["code"] == "change_plan_busy"
    assert model.calls == 1
    release.set()
    assert wait(client, first.get_json()["plan_id"])["state"] == "ready"
    model.before = None
    again = post(client, client_request_id="rid-cccccccc")
    assert again.status_code == 202
    assert wait(client, again.get_json()["plan_id"])["state"] == "ready"


def test_the_same_client_request_id_is_the_same_task(env):
    _m, client, _figs, _worker, model, _retired = env
    a = post(client, client_request_id="rid-dupdupdup")
    b = post(client, client_request_id="rid-dupdupdup")
    assert a.get_json()["plan_id"] == b.get_json()["plan_id"]
    assert wait(client, a.get_json()["plan_id"])["state"] == "ready"
    c = post(client, client_request_id="rid-dupdupdup")  # 完成后重发也回同一份
    assert c.get_json()["plan_id"] == a.get_json()["plan_id"] and c.status_code == 200
    assert model.calls == 1


def test_discard_deletes_the_directory_and_is_idempotent(env):
    _m, client, _figs, _worker, _model, _retired = env
    plan = run(client)["plan"]
    resp = client.post(f"/api/change-plans/{plan['id']}/discard")
    assert resp.status_code == 200 and resp.get_json() == {"discarded": True}
    for url in (
        f"/api/change-plans/{plan['id']}",
        plan["preview"]["before"],
        plan["preview"]["after"],
    ):
        got = client.get(url)
        assert got.status_code == 404 and got.get_json()["code"] == "change_plan_unknown"
    assert client.post(f"/api/change-plans/{plan['id']}/discard").get_json() == {"discarded": False}


def test_discarding_a_running_task_saves_nothing(env):
    _m, client, _figs, _worker, model, _retired = env
    started, release = threading.Event(), threading.Event()
    model.before = lambda: (started.set(), release.wait(10))
    plan_id = post(client).get_json()["plan_id"]
    assert started.wait(10)
    assert client.post(f"/api/change-plans/{plan_id}/discard").get_json() == {"discarded": True}
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline and cp._BY_FIGURE:
        time.sleep(0.01)
    assert cp._BY_FIGURE == {}  # 互斥放掉了
    assert client.get(f"/api/change-plans/{plan_id}").status_code == 404


def test_plans_of_another_project_are_invisible(env):
    m, client, _figs, _worker, _model, _retired = env
    cp.save("otherproj1234", {"id": "b" * 24, "groups": [], "unhandled": []}, b"B", b"A")
    for url in ("/api/change-plans/" + "b" * 24, "/api/change-plans/" + "b" * 24 + "/before.png"):
        got = client.get(url)
        assert got.status_code == 404 and got.get_json()["code"] == "change_plan_unknown"
    assert client.post("/api/change-plans/" + "b" * 24 + "/discard").get_json() == {
        "discarded": False
    }
    assert cp.load("otherproj1234", "b" * 24) is not None  # 别人的方案没被动过


@pytest.mark.parametrize("bad", ["not-a-plan-id", "A" * 24, "z" * 24, "a" * 23])
def test_malformed_plan_ids_are_a_stable_404_everywhere(env, bad):
    _m, client, *_ = env
    for method, url in (
        ("get", f"/api/change-plans/{bad}"),
        ("get", f"/api/change-plans/{bad}/before.png"),
        ("post", f"/api/change-plans/{bad}/discard"),
    ):
        got = getattr(client, method)(url)
        assert got.status_code == 404 and got.get_json()["code"] == "change_plan_unknown"


def test_image_names_are_a_closed_set(env):
    _m, client, *_ = env
    plan = run(client)["plan"]
    for name in ("plan", "../plan", "secret"):
        assert client.get(f"/api/change-plans/{plan['id']}/{name}.png").status_code == 404


# --------------------------------------------------------------------------- share_id（后端按本机分享记录取意见）
def _record(m, **over):
    comments = [dict(c, v=1) for c in _comments()[:1]] + [
        {**c, "id": "n" + c["id"], "v": 2, "text": "第二版：" + c["text"]} for c in _comments()[:2]
    ]
    return {
        "share_id": "rs_AAAAAAAAAAAAAAAA",
        "delivered": True,
        "kind": "figure",
        "target_id": "Kin.pdf",
        "project": m._review_project(),
        "latest_version": 2,
        "comments": comments,
        **over,
    }


def _post_share(client, **over):
    body = {
        "id": "Kin.pdf",
        "patches": B0,
        "source": "review",
        "share_id": "rs_AAAAAAAAAAAAAAAA",
        **over,
    }
    return client.post("/api/change-plans", json=body)


def test_share_id_takes_the_open_comments_of_the_latest_version_only(env, monkeypatch):
    m, client, *_ = env
    with m.app.test_request_context(headers={"X-Tavotto-Project": next(iter(m.PROJECTS))}):
        rec = _record(m)
    monkeypatch.setattr(m.engine_reviewinbox, "get", lambda sid: rec)
    seen = {}
    real = cp._CALL

    def spy(**kw):
        seen.update(kw)
        return ai_structured.StructuredResult(True, value={"groups": [], "unhandled": []})

    cp._CALL = spy
    resp = _post_share(client)
    assert resp.status_code == 202
    wait(client, resp.get_json()["plan_id"])
    cp._CALL = real
    assert (
        seen["user"].count("线太细了") == 1 and "第二版：线太细了" in seen["user"]
    )  # 只有最新版本的那条


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "layout"},
        {"target_id": "Other.pdf"},
        {"project": "someone-elses-project"},
        {"delivered": False},
    ],
)
def test_share_id_of_another_figure_project_or_an_undelivered_record_is_refused(
    env, monkeypatch, over
):
    m, client, _figs, worker, model, _retired = env
    rec = _record(m, **over)
    monkeypatch.setattr(m.engine_reviewinbox, "get", lambda sid: rec)
    resp = _post_share(client)
    assert resp.status_code == 400 and resp.get_json()["code"] == "invalid_change_plan"
    assert worker.calls == [] and model.calls == 0


def test_a_figure_edited_mid_task_is_not_overwritten_by_a_stale_b0(env):
    """模型调用期间用户改了图（热态被另一组 patches 占了）：任务结束不把请求时的 B0 写回去，而是作废 worker、要求前端重放。"""
    _m, client, _figs, worker, model, retired = env
    user_edit = [{"gid": "ax0.title", "prop": "fontsize", "value": 20}]
    worker.hot = None
    real = worker.override

    def tracking(stem, patches, *a, **k):
        worker.hot = list(patches)
        return real(stem, patches, *a, **k)

    worker.override = tracking
    model.before = lambda: setattr(worker, "hot", user_edit)  # 用户在模型调用期间改过图
    out = run(client)
    assert out["state"] == "ready" and out["replay_required"] is True
    assert worker.hot != B0  # 没有 override(B0) 把旧列表写回
    assert worker.calls[-1][0] == "snap"
    assert len(retired) == 1  # 热 worker 已作废，下一次交互按前端的全量列表冷重放


def test_a_running_assistant_session_on_the_script_makes_the_figure_busy(env, monkeypatch):
    m, client, _figs, worker, model, _retired = env
    monkeypatch.setattr(m.engine_ai, "script_busy", lambda path: True)
    resp = post(client)
    assert resp.status_code == 409 and resp.get_json()["code"] == "change_plan_busy"
    assert worker.calls == [] and model.calls == 0 and cp._TASKS == {}
