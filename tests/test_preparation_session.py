"""T01：准备会话（`engine/prepsession.py` + `/api/engine/preparation-sessions*`）的合同——**假 pool**。

这里不起 worker：`pool.build_owned` / `peek` / `force_cancel` 是 `test_preparation_api` 里那套可控替身，验的是
会话自己的行为：phase 的纯派生（黄金向量）、检查不执行、动作的修订 / 失效 / 幂等 / 并发认领、取消只停
自己拥有的、跨项目与未知会话一律 404、回收有界且不误杀活跃的。真 worker 穿过同一组端点的用例在
`test_preparation_session_e2e.py`。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool）从 test_preparation_api 导入复用，参数名与导入名相同
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import deprepair, pool as engine_pool, preparation, prepsession
from test_preparation_api import (  # noqa: F401 — 夹具与替身复用，不重写第二套
    _build_resp,
    _FakeWorker,
    _open,
    _project,
    client,
    fake_pool,
)

SESSIONS = "/api/engine/preparation-sessions"
VECTORS = Path(__file__).parent / "golden" / "preparation_session_vectors.json"


@pytest.fixture
def sessions(client, monkeypatch):
    """干净的会话登记表 + 不真起 runtime cache 物化（假 pool 里没有真 worker 可复制预览）。"""
    prepsession.SESSIONS.reset_for_tests()
    calls: list[tuple] = []
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **kw: calls.append((a, kw)))
    # 替身解释器 `/envs/fake/bin/python` 不存在，依赖层探不了它（真依赖层由 e2e 走）：这里固定成「什么都不缺」
    monkeypatch.setattr(deprepair, "gate", lambda root, script: None)
    monkeypatch.setattr(
        deprepair, "preparation_offer", lambda root, script: {"plan": {"status": "nothing_needed"}}
    )
    prepsession.SESSIONS.notifier = None
    yield calls
    prepsession.SESSIONS.reset_for_tests()
    prepsession.SESSIONS.notifier = m._publish_preparation_session


def _create(client, body: dict, pj: str | None = None):
    return client.post(SESSIONS, json=body, query_string={"pj": pj} if pj else {})


def _get(client, sid: str, pj: str | None = None):
    return client.get(f"{SESSIONS}/{sid}", query_string={"pj": pj} if pj else {})


def _act(client, sid: str, action_id: str, revision, pj: str | None = None, **extra):
    body = {
        "action_id": action_id,
        **({"expected_config_revision": revision} if revision is not None else {}),
    }
    body.update(extra)
    return client.post(
        f"{SESSIONS}/{sid}/actions", json=body, query_string={"pj": pj} if pj else {}
    )


def _action(report: dict, kind: str) -> dict:
    return next(a for a in report["actions"] if a["kind"] == kind)


def _settle(client, sid: str, pj=None, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while True:
        report = _get(client, sid, pj).get_json()
        if report["phase"] not in ("running", "awaiting_runtime_input"):
            return report
        assert time.time() < deadline, report
        time.sleep(0.02)


# ---------------------------------------------------------------- phase 的纯派生


@pytest.mark.parametrize(
    "vector", json.loads(VECTORS.read_text("utf-8"))["vectors"], ids=lambda v: v["name"]
)
def test_phase_derivation_matches_the_golden_vectors(vector):
    assert prepsession.derive(vector["facts"]) == vector["expect"]


def test_the_vector_file_covers_every_phase_this_stage_can_produce():
    produced = {v["expect"]["phase"] for v in json.loads(VECTORS.read_text("utf-8"))["vectors"]}
    # scanning（T02 的有界扫描）与 preparing_environment（T06 的联合安装）本阶段不产生，词汇已收进闭集
    assert produced == set(prepsession.PHASES) - {"scanning", "preparing_environment"}
    assert set(prepsession.PHASES) >= produced


def test_derivation_never_reads_the_clock_or_a_provider(monkeypatch):
    """纯函数：把时钟和 provider 都换成会炸的，输出不变（主语：谁的、哪个时刻——只吃传进来的事实）。"""
    vector = json.loads(VECTORS.read_text("utf-8"))["vectors"][0]
    monkeypatch.setattr(time, "time", lambda: (_ for _ in ()).throw(AssertionError("clock")))
    monkeypatch.setattr(
        engine_pool,
        "resolve_worker_python",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("pool")),
    )
    assert prepsession.derive(vector["facts"]) == vector["expect"]


@pytest.mark.parametrize("code", ["missing_dependency", "script_error"])
def test_error_result_waits_until_the_session_callback_finalizes(
    client, tmp_path, fake_pool, sessions, monkeypatch, code
):
    """Provider terminal status must not expose retry actions before session finalization."""
    _open(client, _project(tmp_path, "p"))
    fake_pool["error"] = engine_pool.WorkerError("failed", code=code, module="alpha")
    entered, release = threading.Event(), threading.Event()
    original_start = preparation.SERVICE.start

    def held_start(*args, **kwargs):
        original_done = kwargs["on_done"]

        def held_done(plan, result):
            entered.set()
            assert release.wait(10), "test did not release the finalizer"
            original_done(plan, result)

        return original_start(*args, **{**kwargs, "on_done": held_done})

    monkeypatch.setattr(preparation.SERVICE, "start", held_start)
    report = _create(client, {"script": "fig.py"}).get_json()
    sid = report["session_id"]
    try:
        response = _act(client, sid, _action(report, "run")["id"], report["config_revision"])
        assert response.status_code == 202
        assert entered.wait(10)
        pending = _get(client, sid).get_json()
        assert pending["result"]["status"] == preparation.STATUS_ERROR
        assert pending["phase"] == "running", pending
        assert pending["outcome"] == {"kind": "running"}
        assert not {"run", "recheck"} & {action["kind"] for action in pending["actions"]}
    finally:
        release.set()
        live = prepsession.SESSIONS.get(sid, report["project_id"])
        assert preparation.SERVICE.wait(live.attempts[-1].attempt_id, 10)
    done = _get(client, sid).get_json()
    assert done["phase"] == "action_required", done
    assert done["outcome"]["code"] == code


def test_cancelled_provider_does_not_wait_for_a_shared_worker_finalizer():
    assert not prepsession.attempt_running(
        {"status": preparation.STATUS_CANCELLED, "finalized": False}
    )


def test_the_no_figure_codes_are_the_pool_ones():
    assert prepsession._NO_FIGURE_CODES == {
        engine_pool.NO_FIGURES_CODE,
        engine_pool.NO_FIGURES_SILENT_CODE,
    }


# ---------------------------------------------------------------- 检查：不执行


def test_a_script_target_is_checked_without_running_anything(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()  # 只有脚本：没有注册表、没有素材 id
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    before = sorted(p.name for p in root.iterdir())
    resp = _create(client, {"script": "fig.py"})
    assert resp.status_code == 201, resp.get_json()
    report = resp.get_json()
    assert report["phase"] == "ready_to_run" and report["outcome"] == {"kind": "pending"}
    assert report["project_id"] == pj
    assert report["target"] == {
        "kind": "script",
        "script": "fig.py",
        "entry": "__main__",
        "asset_id": None,
        "stem": None,
    }
    assert [c["id"] for c in report["checks"]] == [
        "target",
        "environment",
        "workdir",
        "dependencies",
        "data",
    ]
    assert {c["status"] for c in report["checks"]} == {"ok"}
    assert fake_pool["build_calls"] == 0
    assert sorted(p.name for p in root.iterdir()) == before  # 检查不写用户项目
    run = _action(report, "run")
    assert run["impact"] == {
        "executes_user_script": True,
        "installs_packages": False,
        "changes_environment": False,
        "writes_to_project": ["tavotto_registry.json"],
    }
    assert report["plan"]["target"] == "script" and report["plan"]["asset_id"] == ""
    assert report["result"] is None and report["provider"]["attempt_id"] is None


def test_checking_the_same_target_again_reuses_the_session_and_keeps_the_action(
    client, tmp_path, fake_pool, sessions
):
    """无关的重复检查 / 进度读取不撤销已给出的授权：同一会话、同一修订、同一个动作 id。"""
    _open(client, _project(tmp_path, "p"))
    first = _create(client, {"script": "fig.py"}).get_json()
    again = _create(client, {"script": "fig.py"})
    assert again.status_code == 200
    again = again.get_json()
    assert (
        again["session_id"] == first["session_id"]
        and again["config_revision"] == first["config_revision"] == 1
    )
    assert _action(again, "run")["id"] == _action(first, "run")["id"]
    reread = _get(client, first["session_id"]).get_json()
    assert _action(reread, "run")["id"] == _action(first, "run")["id"]


def test_a_changed_interpreter_bumps_the_revision_and_retires_the_old_action(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    _open(client, _project(tmp_path, "p"))
    first = _create(client, {"script": "fig.py"}).get_json()
    old = _action(first, "run")
    monkeypatch.setattr(
        engine_pool,
        "resolve_worker_python",
        lambda root=None, **kw: ("/envs/other/bin/python", "system"),
    )
    second = _create(client, {"script": "fig.py"}).get_json()
    assert second["session_id"] == first["session_id"] and second["config_revision"] == 2
    assert _action(second, "run")["id"] != old["id"]
    gone = _act(client, first["session_id"], old["id"], 2)
    assert gone.status_code == 404 and gone.get_json()["code"] == "preparation_action_unknown"
    assert fake_pool["build_calls"] == 0


def test_an_unanswered_workdir_question_is_awaiting_configuration_and_never_runnable(
    client, tmp_path, fake_pool, sessions
):
    """工作目录要先答：phase 是 awaiting_configuration，依赖检查是 unknown（没评估，不虚构已满足），没有 run 动作。"""
    root = _project(tmp_path, "p")
    (root / "fig.py").write_text(
        "import matplotlib.pyplot as plt\nimport csv\nrows = list(csv.reader(open('data/points.csv')))\n"
        "plt.plot([1, 2])\nplt.savefig('fig.pdf')\n",
        encoding="utf-8",
    )
    (root / "data").mkdir()
    (root / "data" / "points.csv").write_text("1,2\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "fig2.py").write_text(
        "import matplotlib.pyplot as plt\nimport csv\nrows = list(csv.reader(open('data/points.csv')))\nplt.plot([1, 2])\n",
        encoding="utf-8",
    )
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    by = {c["id"]: c for c in report["checks"]}
    assert by["workdir"]["status"] == "needs_action", report["checks"]
    assert by["dependencies"]["status"] == "unknown"
    assert report["phase"] == "awaiting_configuration"
    assert [r["kind"] for r in report["requirements"]] == ["workdir_choice"]
    assert report["requirements"][0]["payload"]["code"] == "workdir_confirmation_required"
    assert [a["kind"] for a in report["actions"]] == ["recheck"]  # 没有 run
    assert fake_pool["build_calls"] == 0


def test_a_missing_interpreter_blocks_the_session(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    _open(client, _project(tmp_path, "p"))

    def boom(root=None, **kw):
        raise engine_pool.WorkerError("没有解释器", code="explicit_python_unusable")

    monkeypatch.setattr(engine_pool, "resolve_worker_python", boom)
    report = _create(client, {"script": "fig.py"}).get_json()
    assert report["phase"] == "action_required"
    assert report["outcome"] == {"kind": "blocked", "code": "explicit_python_unusable"}
    assert [a["kind"] for a in report["actions"]] == ["recheck"]


# ---------------------------------------------------------------- 认领：幂等 / 并发 / 修订 / 失效


def _create_ready(client, tmp_path, name="p"):
    root = _project(tmp_path, name)
    pj = _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    return root, pj, report


def test_claiming_the_run_action_executes_once_and_a_repeat_returns_the_same_attempt(
    client, tmp_path, fake_pool, sessions
):
    root, _pj, report = _create_ready(client, tmp_path)
    sid, run = report["session_id"], _action(report, "run")
    first = _act(client, sid, run["id"], report["config_revision"])
    assert first.status_code == 202
    first = first.get_json()
    assert first["claimed"] is True
    again = _act(client, sid, run["id"], report["config_revision"])
    assert again.status_code == 200
    again = again.get_json()
    assert again["claimed"] is False
    assert again["report"]["provider"]["attempt_id"] == first["report"]["provider"]["attempt_id"]
    done = _settle(client, sid)
    assert done["phase"] == "completed" and done["facts"] == {
        "execution_finished": True,
        "figure_captured": True,
    }
    assert fake_pool["build_calls"] == 1
    assert done["observation_seq"] > report["observation_seq"]
    assert done["result"]["status"] == "ready" and done["provider"]["attempts"] == 1
    # 脚本目标成功之后登记进注册表、物化 runtime cache（与 /api/registry/probe 同一串）
    assert json.loads((root / "tavotto_registry.json").read_text("utf-8"))["scripts"]["fig.py"][
        "stems"
    ] == ["fig"]
    assert len(sessions) == 1


def test_two_tabs_claiming_the_same_action_start_one_build(client, tmp_path, fake_pool, sessions):
    _root, pj, report = _create_ready(client, tmp_path)
    sid, run = report["session_id"], _action(report, "run")
    fake_pool["gate"].clear()  # 让 build 卡住：并发窗口开着
    barrier = threading.Barrier(2)
    out: list[tuple[int, bool]] = []

    def tab():
        c = m.app.test_client()
        barrier.wait()
        resp = _act(c, sid, run["id"], report["config_revision"], pj=pj)
        out.append((resp.status_code, resp.get_json()["claimed"]))

    threads = [threading.Thread(target=tab) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    fake_pool["gate"].set()
    assert sorted(out) == [(200, False), (202, True)]
    _settle(client, sid)
    assert fake_pool["build_calls"] == 1


def test_a_stale_expected_revision_is_refused_with_zero_side_effects(
    client, tmp_path, fake_pool, sessions
):
    _root, _pj, report = _create_ready(client, tmp_path)
    run = _action(report, "run")
    resp = _act(client, report["session_id"], run["id"], report["config_revision"] + 1)
    assert (
        resp.status_code == 409 and resp.get_json()["code"] == "preparation_config_revision_changed"
    )
    assert resp.get_json()["params"] == {"config_revision": report["config_revision"]}
    assert _act(client, report["session_id"], run["id"], None).status_code == 400
    assert fake_pool["build_calls"] == 0
    assert _get(client, report["session_id"]).get_json()["result"] is None


def test_a_world_that_changed_after_the_check_is_refused_before_anything_runs(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """检查之后授权变了（撤销 / 改了工作目录）：认领时的失效检查在会话锁内、提交 provider 之前——一行不跑。"""
    _root, _pj, report = _create_ready(client, tmp_path)
    sid, run = report["session_id"], _action(report, "run")
    monkeypatch.setattr(m.engine_workdir, "grant_for", lambda root: {"granted": "something-else"})
    resp = _act(client, sid, run["id"], report["config_revision"])
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "preparation_plan_stale" and body["params"] == {
        "reason": "grant_changed",
        "executed": False,
    }
    assert fake_pool["build_calls"] == 0
    after = _get(client, sid).get_json()
    assert after["phase"] == "action_required" and after["outcome"]["kind"] == "stale"
    assert [a["kind"] for a in after["actions"]] == ["recheck"]  # run 撤了，只剩重新检查
    # 重新检查：世界变了 → 新修订、新的 run 动作，之后能跑
    redo = _act(client, sid, _action(after, "recheck")["id"], after["config_revision"])
    assert redo.status_code == 200
    fresh = redo.get_json()["report"]
    assert (
        fresh["config_revision"] == after["config_revision"] + 1
        and fresh["phase"] == "ready_to_run"
    )
    ok = _act(client, sid, _action(fresh, "run")["id"], fresh["config_revision"])
    assert ok.status_code == 202
    assert _settle(client, sid)["phase"] == "completed"
    assert fake_pool["build_calls"] == 1


def test_only_the_two_known_fields_are_accepted(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _create_ready(client, tmp_path)
    run = _action(report, "run")
    resp = _act(
        client,
        report["session_id"],
        run["id"],
        report["config_revision"],
        argv=["--evil"],
        shell="rm -rf /",
    )
    assert resp.status_code == 400
    assert resp.get_json()["params"] == {"unexpected": ["argv", "shell"]}
    unknown = _act(client, report["session_id"], "act-not-mine", report["config_revision"])
    assert unknown.status_code == 404 and unknown.get_json()["code"] == "preparation_action_unknown"
    assert fake_pool["build_calls"] == 0


def test_a_terminal_session_offers_a_fresh_run_and_an_explicit_rerun_is_a_new_attempt(
    client, tmp_path, fake_pool, sessions
):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid, first_run = report["session_id"], _action(report, "run")
    assert _act(client, sid, first_run["id"], report["config_revision"]).status_code == 202
    done = _settle(client, sid)
    first_attempt = done["provider"]["attempt_id"]
    rerun = _action(done, "run")
    assert rerun["id"] != first_run["id"] and done["config_revision"] == report["config_revision"]
    # 旧动作 id 再点 = 幂等回到当初那次尝试，不是重跑
    repeat = _act(client, sid, first_run["id"], done["config_revision"]).get_json()
    assert (
        repeat["claimed"] is False and repeat["report"]["provider"]["attempt_id"] == first_attempt
    )
    assert fake_pool["build_calls"] == 1
    # 用户明确重跑：新尝试
    again = _act(client, sid, rerun["id"], done["config_revision"])
    assert again.status_code == 202
    second = _settle(client, sid)
    assert second["provider"]["attempts"] == 2 and second["provider"]["attempt_id"] != first_attempt


def test_after_a_recheck_changes_the_configuration_the_old_completed_attempt_does_not_stand_in(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """上一修订的 `completed` 不能冒充新配置的结果：重新检查之后 phase 回到 ready_to_run，尝试历史还在。"""
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    done = _settle(client, sid)
    assert done["phase"] == "completed"
    monkeypatch.setattr(m.engine_workdir, "grant_for", lambda root: {"granted": "changed"})
    stale = _act(client, sid, _action(done, "run")["id"], done["config_revision"])
    assert stale.status_code == 409  # 重跑被失效检查拦下，一行不跑
    assert fake_pool["build_calls"] == 1
    after = _get(client, sid).get_json()
    assert (
        after["phase"] == "action_required" and after["outcome"]["kind"] == "stale"
    )  # 不是上一次的 completed
    redo = _act(client, sid, _action(after, "recheck")["id"], after["config_revision"]).get_json()[
        "report"
    ]
    assert redo["config_revision"] == 2 and redo["phase"] == "ready_to_run"
    assert redo["provider"]["attempts"] == 1 and redo["result"] is not None  # 历史仍可读


# ---------------------------------------------------------------- 取消的所有权


def test_cancel_closes_only_the_runtime_this_session_created(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    fake_pool["gate"].clear()
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    running = _get(client, sid).get_json()
    assert running["phase"] == "running"
    assert [a["kind"] for a in running["actions"]] == ["cancel"]  # 在跑时只有取消
    assert _act(client, sid, _action(running, "cancel")["id"], 1).status_code == 200
    fake_pool["gate"].set()
    final = _settle(client, sid)
    assert final["phase"] == "cancelled" and final["result"]["created_runtime"] is True
    assert fake_pool["force_cancel"] == [("fig.py", str(_root))]


def test_cancel_does_not_touch_a_runtime_someone_else_owns(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    fake_pool["created"] = False  # 池里本来就有这条会话（别的消费者的）
    fake_pool["gate"].clear()
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    running = _get(client, sid).get_json()
    assert _act(client, sid, _action(running, "cancel")["id"], 1).status_code == 200
    fake_pool["gate"].set()
    final = _settle(client, sid)
    assert final["phase"] == "cancelled"
    assert fake_pool["force_cancel"] == []  # 不是本会话起的，一根手指都不碰


# ---------------------------------------------------------------- 终局：没有图 / 旧接口兼容 / 已知素材


def test_a_script_that_finishes_without_a_figure_is_partial_not_a_first_figure(
    client, tmp_path, fake_pool, sessions
):
    root, _pj, report = _create_ready(client, tmp_path)

    def factory():
        w = _FakeWorker(root)
        w.script_name = "fig.py"
        w._log_tail = lambda n=30: ""
        return w

    def empty():
        resp = _build_resp()
        resp["stems"], resp["descriptors"] = {}, []
        return resp

    fake_pool["worker_factory"], fake_pool["build_resp"] = factory, empty
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    final = _settle(client, sid)
    assert final["phase"] == "partial"
    assert final["outcome"] == {
        "kind": "execution_finished_no_figure",
        "code": "no_figures_captured_silent",
    }
    assert final["facts"] == {"execution_finished": True, "figure_captured": False}
    assert sessions == []  # 没有捕获到图：不登记、不物化
    assert fake_pool["build_calls"] == 1


def test_a_known_asset_goes_through_the_same_session_and_the_old_endpoint_is_unchanged(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"id": "fig.pdf"}).get_json()
    assert report["target"] == {
        "kind": "asset",
        "script": "fig.py",
        "entry": "__main__",
        "asset_id": "fig.pdf",
        "stem": "fig",
    }
    assert report["phase"] == "ready_to_run" and report["plan"]["target"] == "asset"
    assert _action(report, "run")["impact"]["writes_to_project"] == []  # 已知素材不重新登记
    assert _act(client, report["session_id"], _action(report, "run")["id"], 1).status_code == 202
    done = _settle(client, report["session_id"])
    assert done["phase"] == "completed" and done["result"]["receipt"]["completeness"] == "complete"
    assert fake_pool["build_calls"] == 1
    # 旧接口：同一张图照旧 202 + {plan, result}，计划里 target 缺省为 asset
    legacy = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert legacy.status_code == 202 and set(legacy.get_json()) == {"plan", "result"}
    assert legacy.get_json()["plan"]["target"] == "asset"


def test_a_static_asset_is_completed_without_a_run_action(client, tmp_path, fake_pool, sessions):
    _open(client, _project(tmp_path, "p"))
    report = _create(client, {"id": "static_only.pdf"}).get_json()
    assert report["phase"] == "completed" and report["outcome"] == {"kind": "static_source"}
    assert [a["kind"] for a in report["actions"]] == ["recheck"]
    assert fake_pool["build_calls"] == 0


# ---------------------------------------------------------------- 身份与边界


def test_sessions_are_bound_to_their_project(client, tmp_path, fake_pool, sessions):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    pj_a, pj_b = _open(client, a), _open(client, b)
    ra = _create(client, {"script": "fig.py"}, pj_a).get_json()
    rb = _create(client, {"script": "fig.py"}, pj_b).get_json()
    assert (
        ra["session_id"] != rb["session_id"]
        and ra["project_id"] == pj_a
        and rb["project_id"] == pj_b
    )
    assert _get(client, ra["session_id"], pj_b).status_code == 404
    run = _action(ra, "run")
    wrong = _act(client, ra["session_id"], run["id"], 1, pj_b)
    assert wrong.status_code == 404 and wrong.get_json()["code"] == "preparation_session_not_found"
    assert fake_pool["build_calls"] == 0
    # 对的项目仍然认
    assert _act(client, ra["session_id"], run["id"], 1, pj_a).status_code == 202


def test_an_unknown_session_reports_that_it_may_have_been_lost_to_a_restart(
    client, tmp_path, fake_pool, sessions
):
    _open(client, _project(tmp_path, "p"))
    resp = _get(client, "psess-deadbeef")
    assert resp.status_code == 404
    body = resp.get_json()
    assert (
        body["code"] == "preparation_session_not_found"
        and body["params"]["reason"] == "unknown_or_restarted"
    )


@pytest.mark.parametrize(
    "body,code",
    [
        ({}, "bad_request"),
        ({"script": "fig.py", "id": "fig.pdf"}, "bad_request"),
        ({"script": "../escape.py"}, "script_path_outside_project"),
        ({"script": "fig.txt"}, "unsupported_script_type"),
        ({"script": "missing.py"}, "script_not_found"),
        ({"script": "fig.py", "entry": "not a valid entry!"}, "invalid_entry"),
    ],
)
def test_bad_targets_are_refused_with_stable_codes(
    client, tmp_path, fake_pool, sessions, body, code
):
    root = _project(tmp_path, "p")
    (root / "fig.txt").write_text("x", encoding="utf-8")
    _open(client, root)
    resp = _create(client, body)
    assert resp.status_code in (400, 404)
    assert resp.get_json()["code"] == code
    assert fake_pool["build_calls"] == 0


# ---------------------------------------------------------------- 回收有界；活跃的不被误杀


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _launcher(plan, on_done):
    """不经 app：把计划交给 `PreparationService.start`，runner 仍是（替身的）`pool.build_owned`。"""
    preparation.SERVICE.start(
        plan.plan_id,
        runner=lambda pl, before_retry=None: engine_pool.build_owned(
            pl.script, pl.project_root, pl.entry
        ),
        on_done=on_done,
    )


def _run(svc, sess, pj):
    rep = svc.report(sess)
    svc.act(
        sess.session_id, pj, _action(rep, "run")["id"], rep["config_revision"], launch=_launcher
    )


def test_idle_sessions_are_swept_but_an_active_one_survives_the_ttl(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    clock = _Clock()
    svc = prepsession.SessionService(preparation.SERVICE, idle_ttl_s=60, clock=clock)
    idle, _ = svc.check(project_id=pj, project_root=str(root), target=_script_target("fig.py"))
    asset = {
        "kind": "asset",
        "script": "fig.py",
        "entry": "__main__",
        "stem": "fig",
        "asset_id": "fig.pdf",
        "original_artifact": "fig.pdf",
        "original_path": str(root / "fig.pdf"),
    }
    live, _ = svc.check(project_id=pj, project_root=str(root), target=asset)
    fake_pool["gate"].clear()
    _run(svc, live, pj)
    clock.now += 3600  # 远超 TTL
    svc._sweep()
    assert svc.get(idle.session_id, pj) is None  # 闲置的被回收
    assert svc.get(live.session_id, pj) is live  # 在跑的不因普通 TTL 被回收
    fake_pool["gate"].set()
    assert preparation.SERVICE.wait(live.attempts[-1].attempt_id, 10)
    deadline = time.time() + 5
    while not live.attempts[-1].finalized and time.time() < deadline:
        time.sleep(0.02)
    clock.now += 3600
    svc._sweep()
    assert svc.get(live.session_id, pj) is None  # 终局并登记完之后才按 TTL 回收


def _script_target(script: str, entry: str = "__main__") -> dict:
    return {
        "kind": "script",
        "script": script,
        "entry": entry,
        "stem": "",
        "asset_id": "",
        "original_artifact": None,
        "original_path": None,
    }


def test_the_table_is_bounded_and_refuses_new_sessions_instead_of_evicting_active_ones(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    for name in ("a.py", "b.py", "c.py"):
        (root / name).write_text((root / "fig.py").read_text("utf-8"), encoding="utf-8")
    svc = prepsession.SessionService(preparation.SERVICE, max_sessions=2)
    first, _ = svc.check(project_id=pj, project_root=str(root), target=_script_target("a.py"))
    second, _ = svc.check(project_id=pj, project_root=str(root), target=_script_target("b.py"))
    # 两个都是闲置的终局前会话：第三个进来时回收最久没碰的那个，而不是拒绝
    svc.report(second)
    third, _ = svc.check(project_id=pj, project_root=str(root), target=_script_target("c.py"))
    assert svc.get(first.session_id, pj) is None and svc.get(third.session_id, pj) is third
    # 都在跑 → 拒绝（429），不驱逐
    fake_pool["gate"].clear()
    for sess in (second, third):
        _run(svc, sess, pj)
    with pytest.raises(prepsession.SessionError) as full:
        svc.check(project_id=pj, project_root=str(root), target=_script_target("fig.py"))
    assert full.value.status == 429 and full.value.code == "preparation_sessions_full"
    assert svc.get(second.session_id, pj) is second and svc.get(third.session_id, pj) is third
    fake_pool["gate"].set()


# ---------------------------------------------------------------- SSE 提示


def test_state_changes_ping_the_event_stream_with_project_target_and_revision(
    client, tmp_path, fake_pool, sessions
):
    pings: list[dict] = []
    prepsession.SESSIONS.notifier = pings.append
    _root, pj, report = _create_ready(client, tmp_path)
    assert pings and pings[0]["project_id"] == pj and pings[0]["session_id"] == report["session_id"]
    assert pings[0]["target"]["script"] == "fig.py" and pings[0]["config_revision"] == 1
    assert (
        "phase" not in pings[0] and "observation_seq" not in pings[0]
    )  # 提示不带状态，状态以 GET 为准
    before = len(pings)
    assert _act(client, report["session_id"], _action(report, "run")["id"], 1).status_code == 202
    _settle(client, report["session_id"])
    deadline = time.time() + 5
    while len(pings) < before + 2 and time.time() < deadline:  # 认领一次 + 登记完成一次
        time.sleep(0.02)
    assert len(pings) >= before + 2


def test_the_app_publishes_the_ping_on_the_existing_event_stream(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(m, "sse_publish", lambda event, data: events.append((event, data)))
    m._publish_preparation_session(
        {"session_id": "s", "project_id": "pj1", "target": {}, "config_revision": 3}
    )
    assert events == [
        (
            "preparation.session",
            {
                "pj": "pj1",
                "session_id": "s",
                "project_id": "pj1",
                "target": {},
                "config_revision": 3,
            },
        )
    ]


def test_changed_explicit_entry_survives_recheck(client, tmp_path, fake_pool, sessions):
    _open(client, _project(tmp_path, "p"))
    first = _create(client, {"script": "fig.py", "entry": "first"}).get_json()
    changed = _create(client, {"script": "fig.py", "entry": "second"}).get_json()
    assert changed["session_id"] == first["session_id"]
    assert changed["config_revision"] == first["config_revision"] + 1
    assert changed["target"]["entry"] == "second"
    rechecked = _act(
        client, changed["session_id"], _action(changed, "recheck")["id"], changed["config_revision"]
    ).get_json()["report"]
    assert rechecked["target"]["entry"] == rechecked["plan"]["entry"] == "second"
    assert rechecked["config_revision"] == changed["config_revision"]
    assert fake_pool["build_calls"] == 0


def test_explicit_rerun_retires_the_cached_build(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    done = _settle(client, sid)
    cached = _FakeWorker(root)
    cached.last_build_descriptors = _build_resp()["descriptors"]
    cached.last_build_runtime = _build_resp()["runtime"]
    fake_pool["peek"] = cached
    retired = []

    def invalidate(script, project_root, run=None, *, only_run=False, force=False):
        assert force and only_run and run is None
        retired.append((script, project_root))
        fake_pool["peek"] = None

    monkeypatch.setattr(engine_pool, "invalidate", invalidate)
    again = _act(client, sid, _action(done, "run")["id"], done["config_revision"])
    assert again.status_code == 202
    assert _settle(client, sid)["phase"] == "completed"
    assert fake_pool["build_calls"] == 2
    assert retired == [("fig.py", str(root))]


def test_script_resolution_uses_the_shared_containment_result(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = tmp_path / "project"
    root.mkdir()
    (root / "validated.py").write_text("pass\n", encoding="utf-8")
    calls = []

    def contained(project_root, raw):
        calls.append((project_root, raw))
        return str(root / "validated.py")

    monkeypatch.setattr(m.engine_projectenv, "contained_path", contained)
    with m.app.app_context():
        script, rejected = m._resolve_project_script(SimpleNamespace(path=root), "untrusted.py")
    assert rejected is None
    assert script == "validated.py"
    assert calls == [(root, "untrusted.py")]


def test_script_resolution_rejects_symlink_and_prefix_sibling_escapes(tmp_path):
    from types import SimpleNamespace

    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "project-evil"
    sibling.mkdir()
    outside = sibling / "escape.py"
    outside.write_text("pass\n", encoding="utf-8")
    link = root / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with m.app.app_context():
        for raw in (str(outside), "../project-evil/escape.py", "link.py"):
            script, rejected = m._resolve_project_script(SimpleNamespace(path=root), raw)
            assert script is None
            response, status = rejected
            assert status == 400
            assert response.get_json()["code"] == "script_path_outside_project"


def test_changed_entry_does_not_reuse_a_build_from_another_entry(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    _create(client, {"script": "fig.py", "entry": "first"})
    changed = _create(client, {"script": "fig.py", "entry": "second"}).get_json()
    cached = _FakeWorker(root)
    cached.entry = "first"
    cached.last_build_descriptors = _build_resp()["descriptors"]
    cached.last_build_runtime = _build_resp()["runtime"]
    fake_pool["peek"] = cached
    assert (
        _act(
            client, changed["session_id"], _action(changed, "run")["id"], changed["config_revision"]
        ).status_code
        == 202
    )
    assert _settle(client, changed["session_id"])["phase"] == "completed"
    assert fake_pool["build_calls"] == 1


def test_unconfirmed_rerun_retirement_reports_an_error_without_a_new_build(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    done = _settle(client, sid)

    def refuse_close(*args, **kwargs):
        raise engine_pool.WorkerError("close not confirmed", code="session_dead")

    monkeypatch.setattr(engine_pool, "invalidate", refuse_close)
    assert _act(client, sid, _action(done, "run")["id"], 1).status_code == 202
    refused = _settle(client, sid)
    assert refused["phase"] == "action_required"
    assert refused["result"]["error"]["code"] == "session_dead"
    assert fake_pool["build_calls"] == 1


@pytest.mark.parametrize("change", ["declarations", "identity", "inputs_digest"])
@pytest.mark.parametrize("gate_required", [False, True], ids=["runnable", "authorization-required"])
def test_recheck_revises_changed_dependency_authorization_with_the_same_status(
    client, tmp_path, fake_pool, sessions, monkeypatch, change, gate_required
):
    import copy

    root = _project(tmp_path, "p")
    declarations = root / "requirements.txt"
    declarations.write_text("numpy>=1.24\n", encoding="utf-8")
    _open(client, root)
    offer = {
        "code": deprepair.ERROR_PREPARATION_REQUIRED,
        "plan": {
            "status": "ready",
            "identity": "old-identity",
            "inputs_digest": "old-inputs",
            "requirements": ["numpy>=1.24"],
        },
    }
    monkeypatch.setattr(
        deprepair, "gate", lambda *_: copy.deepcopy(offer) if gate_required else None
    )
    monkeypatch.setattr(deprepair, "preparation_offer", lambda *_: copy.deepcopy(offer))
    first = _create(client, {"script": "fig.py"}).get_json()
    assert first["phase"] == ("awaiting_confirmation" if gate_required else "ready_to_run")
    old_run = None if gate_required else _action(first, "run")
    old = _action(first, "recheck")
    if change == "declarations":
        declarations.write_text("numpy>=2\n", encoding="utf-8")
    else:
        offer["plan"][change] = "new-value"
    offer["plan"]["requirements"] = ["numpy>=2"]
    changed = _act(client, first["session_id"], old["id"], first["config_revision"]).get_json()[
        "report"
    ]
    assert changed["config_revision"] == first["config_revision"] + 1
    if gate_required:
        assert changed["requirements"][0]["payload"]["plan"]["requirements"] == ["numpy>=2"]
    else:
        assert _action(changed, "run")["id"] != old_run["id"]
        assert (
            _act(client, first["session_id"], old_run["id"], changed["config_revision"]).status_code
            == 404
        )
    assert changed["plan"]["dependency_preparation"] == offer
    assert _action(changed, "recheck")["id"] != old["id"]
    assert (
        _act(client, first["session_id"], old["id"], changed["config_revision"]).status_code == 404
    )
    unchanged = _create(client, {"script": "fig.py"}).get_json()
    assert unchanged["config_revision"] == changed["config_revision"]
    assert _action(unchanged, "recheck")["id"] == _action(changed, "recheck")["id"]
    assert fake_pool["build_calls"] == 0
