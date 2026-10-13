"""P05b：一次运行意图（ADR 0124）——用户明确的「打开并运行」之后，后端把**用户代码首次执行之前**的准备连续推进，然后执行恰一次。

三组：

* **假 pool**（目录回答、取消、外部变化、预算、argv 转移、竞态）——只数 `build_owned` / 注入的 `launch` 被调几次；
* **假换代事务**（`held`：授权范围内的受管安装、超出授权、影响扩大、没有进展、失败 / 取消后的迟到 done）——数安装事务起了几次；
* **真走一遍**（真 venv、真 pip、真 worker）——安装 → 重核 → 恰一次执行并出图；**真实外部计数夹具**：脚本先往项目外的计数文件
  写一行、之后才缺包，准备好修复也不会把计数悄悄变成 2（EX13 / AC-14）。

没有意图的会话仍然是「只准备不自动执行」（ADR 0116 的合同没变）：每组都有不武装的对照。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from support.dependency_repair import needs_worker
from tavotto.engine import deprepair, pool as engine_pool, prepsession
from test_preparation_api import _open, _project, client, fake_pool  # noqa: F401
from test_preparation_session import _act, _action, _create, _get, _settle, sessions  # noqa: F401
from test_preparation_session_dependencies import (  # noqa: F401
    ALPHA,
    BETA,
    _act as _dep_act,  # noqa: F401
    _clean,
    _create as _dep_create,  # noqa: F401
    _get as _dep_get,  # noqa: F401
    _kinds,
    _wait,
    _wait_until,
    _write,
    held,
    house,
    opened,
)

pytest_plugins = ("support.dependency_repair",)

SESSIONS = "/api/engine/preparation-sessions"
WORKDIR_SCRIPT = (
    "import matplotlib.pyplot as plt\nimport csv\nrows = list(csv.reader(open('data/points.csv')))\n"
    "plt.plot([1, 2])\nplt.savefig('fig.pdf')\n"
)


def _workdir_project(tmp_path: Path, name: str = "p") -> Path:
    """`scripts/fig2.py` 读相对路径的数据：首开要先问运行目录（检查项 `workdir` = needs_action）。"""
    root = _project(tmp_path, name)
    (root / "data").mkdir()
    (root / "data" / "points.csv").write_text("1,2\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "fig2.py").write_text(WORKDIR_SCRIPT, encoding="utf-8")
    return root


def _arm(client, report: dict, *, echo: bool = True, **extra):
    action = _action(report, "run_when_ready")
    body = {"action_id": action["id"], "expected_config_revision": report["config_revision"]}
    if echo:
        body["impact_digest"] = action["impact"]["impact_digest"]
    body.update(extra)
    return client.post(f"{SESSIONS}/{report['session_id']}/actions", json=body)


def _answer_workdir(client) -> None:
    resp = client.patch("/api/engine/workdir", json={"mode": "project"})
    assert resp.status_code == 200, resp.get_json()


def _recheck(client, sid: str) -> dict:
    """像界面那样：答完之后让空闲会话重新检查（`recheck` 动作）。回新报告。"""
    report = _get(client, sid).get_json()
    resp = _act(client, sid, _action(report, "recheck")["id"], report["config_revision"])
    assert resp.status_code == 200, resp.get_json()
    return _get(client, sid).get_json()


def _quiet(client, sid: str, *, rounds: int = 5) -> dict:
    """让后台线程有机会（错误地）再动一动，然后读终态。"""
    for _ in range(rounds):
        time.sleep(0.05)
    return _get(client, sid).get_json()


# ===========================================================================
# 被动浏览不创建意图、不执行（对照组：ADR 0116 的合同没变）
# ===========================================================================
def test_passive_browsing_never_creates_an_intent_or_runs(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    sid = report["session_id"]
    assert "run_when_ready" in [a["kind"] for a in report["actions"]]  # 摆出来，但没人认领
    for _ in range(3):  # 刷新 / 组件挂载 / SSE 重放后的补拉 / 同目标再建
        assert _get(client, sid).get_json()["intent"] is None
        assert _create(client, {"script": "fig.py"}).get_json()["session_id"] == sid
    _recheck(client, sid)
    assert fake_pool["build_calls"] == 0
    assert _get(client, sid).get_json()["intent"] is None


def test_without_an_intent_a_workdir_answer_still_only_rechecks(
    client, tmp_path, fake_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    assert report["phase"] == "awaiting_configuration"
    _answer_workdir(client)
    after = _recheck(client, report["session_id"])
    assert after["phase"] == "ready_to_run" and after["intent"] is None
    assert _quiet(client, report["session_id"])["phase"] == "ready_to_run"
    assert fake_pool["build_calls"] == 0


# ===========================================================================
# 创建：只有用户明确的动作，且必须回显授权合同的摘要
# ===========================================================================
def test_arming_requires_the_echoed_grant_digest_and_changes_nothing_without_it(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    bare = _arm(client, report, echo=False)
    assert bare.status_code == 400 and bare.get_json()["code"] == "preparation_impact_unconfirmed"
    wrong = _arm(client, report, echo=False, impact_digest="0" * 32)
    assert wrong.status_code == 409 and wrong.get_json()["code"] == "preparation_impact_changed"
    assert fake_pool["build_calls"] == 0
    assert _get(client, report["session_id"]).get_json()["intent"] is None
    grant = _action(report, "run_when_ready")["impact"]["run_intent"]  # 合同原样摆在动作上
    assert grant["modifies_user_environment"] is False and grant["max_installs"] == 1
    assert grant["scope"] == "managed_generation" and grant["user_specified_packages"] is False


def test_arming_a_ready_session_executes_exactly_once(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    sid = report["session_id"]
    first = _arm(client, report)
    assert first.status_code == 202 and first.get_json()["claimed"] is True
    again = _arm(client, report)  # 重复点击 / 另一个标签页：同一个动作 → 同一个意图
    assert again.status_code == 200 and again.get_json()["claimed"] is False
    final = _settle(client, sid)
    assert final["phase"] == "completed"
    assert final["intent"]["status"] == "launched" and final["intent"]["attempt_id"]
    assert final["intent"]["attempt_id"] == final["provider"]["attempt_id"]
    _recheck(client, sid)
    _create(client, {"script": "fig.py"})
    assert _quiet(client, sid)["provider"]["attempts"] == 1
    assert fake_pool["build_calls"] == 1
    assert "run_when_ready" not in [a["kind"] for a in _get(client, sid).get_json()["actions"]]


def test_concurrent_claims_of_one_arm_action_launch_once(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    action = _action(report, "run_when_ready")
    launches: list[str] = []
    results: list = []

    def claim() -> None:
        try:
            results.append(
                prepsession.SESSIONS.act(
                    report["session_id"],
                    report["project_id"],
                    action["id"],
                    report["config_revision"],
                    launch=lambda plan, on_done: launches.append(plan.plan_id),
                    impact_digest=action["impact"]["impact_digest"],
                )
            )
        except prepsession.SessionError as exc:  # 输家只许是幂等 / 已在进行，不许是第二次执行
            results.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(launches) == 1
    assert sum(1 for r in results if isinstance(r, tuple) and r[1] is True) == 1


# ===========================================================================
# AC-12：目录回答是预期变化，重核后继续一次；重复触发仍 1 次
# ===========================================================================
def test_a_workdir_answer_changes_the_revision_and_the_intent_follows_to_one_execution(
    client, tmp_path, fake_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    sid, rev0 = report["session_id"], report["config_revision"]
    assert _arm(client, report).status_code == 202
    waiting = _get(client, sid).get_json()
    assert waiting["intent"]["status"] == "active" and waiting["intent"]["waiting_on"] == "workdir"
    assert fake_pool["build_calls"] == 0 and waiting["provider"]["attempts"] == 0
    assert "cancel" in [a["kind"] for a in waiting["actions"]]  # 没有尝试在跑，也能取消这条意图
    _answer_workdir(client)
    assert (
        _get(client, sid).get_json()["intent"]["status"] == "active"
    )  # 答了不会自己动：等重新检查
    _recheck(client, sid)
    final = _settle(client, sid)
    assert final["config_revision"] > rev0  # 预期的修订变化被批准
    assert final["intent"]["status"] == "launched"
    assert fake_pool["build_calls"] == 1
    for _ in range(3):  # 重复 recheck / 重复读 / 另一个标签页再建：仍是同一次执行
        _recheck(client, sid)
        _create(client, {"script": "scripts/fig2.py"})
    assert _quiet(client, sid)["provider"]["attempts"] == 1 and fake_pool["build_calls"] == 1


# ===========================================================================
# AC-13：取消终局稳定；外部变化不沿用旧意图
# ===========================================================================
def test_cancelling_the_intent_is_terminal_and_a_late_answer_cannot_revive_it(
    client, tmp_path, fake_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    sid = report["session_id"]
    assert _arm(client, report).status_code == 202
    armed = _get(client, sid).get_json()
    cancelled = _act(client, sid, _action(armed, "cancel")["id"], armed["config_revision"])
    assert cancelled.status_code == 200
    after = _get(client, sid).get_json()
    assert (
        after["intent"]["status"] == "cancelled" and after["intent"]["reason"] == "user_cancelled"
    )
    _answer_workdir(client)  # 后到的回答 / 重新检查 / 重复读：都复活不了
    ready = _recheck(client, sid)
    assert ready["phase"] == "ready_to_run" and ready["intent"]["status"] == "cancelled"
    assert _quiet(client, sid)["provider"]["attempts"] == 0
    assert fake_pool["build_calls"] == 0


def test_an_unrelated_source_edit_after_arming_does_not_ride_the_old_intent(
    client, tmp_path, fake_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    sid = report["session_id"]
    assert _arm(client, report).status_code == 202
    script = root / "scripts" / "fig2.py"
    script.write_text("# 别的页改了源码\n" + script.read_text("utf-8"), encoding="utf-8")
    _answer_workdir(client)
    after = _recheck(client, sid)
    assert after["intent"]["status"] == "revoked"
    assert after["intent"]["reason"] in ("source_changed", "external_change")
    assert _quiet(client, sid)["provider"]["attempts"] == 0 and fake_pool["build_calls"] == 0
    assert after["phase"] == "ready_to_run"  # 用户仍可手动 Run / 重新武装，但不会被旧意图偷偷启动


def test_a_replaced_environment_after_arming_does_not_ride_the_old_intent(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    sid = report["session_id"]
    assert _arm(client, report).status_code == 202
    monkeypatch.setattr(
        engine_pool,
        "resolve_worker_python",
        lambda root=None, **kw: ("/envs/other/bin/python", "system"),
    )
    _answer_workdir(client)
    after = _recheck(client, sid)
    assert after["intent"]["status"] == "revoked" and after["intent"]["reason"] == "external_change"
    assert _quiet(client, sid)["provider"]["attempts"] == 0 and fake_pool["build_calls"] == 0


def test_the_wall_clock_budget_is_bounded(client, tmp_path, fake_pool, sessions, monkeypatch):
    root = _workdir_project(tmp_path)
    _open(client, root)
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(prepsession.SESSIONS, "_clock", lambda: now["t"])
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    sid = report["session_id"]
    assert _arm(client, report).status_code == 202
    _answer_workdir(client)
    now["t"] += prepsession.RUN_INTENT_TTL_S + 1  # 等用户回答等过了头
    after = _recheck(client, sid)
    assert (
        after["intent"]["status"] == "revoked" and after["intent"]["reason"] == "budget_exhausted"
    )
    assert _quiet(client, sid)["provider"]["attempts"] == 0 and fake_pool["build_calls"] == 0


def test_a_restart_forgets_the_intent_and_never_guesses_or_reruns(
    client, tmp_path, fake_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    report = _create(client, {"script": "scripts/fig2.py"}).get_json()
    assert _arm(client, report).status_code == 202
    prepsession.SESSIONS.reset_for_tests()  # 应用重启：会话与意图都在内存里
    gone = _get(client, report["session_id"])
    assert gone.status_code == 404 and gone.get_json()["params"]["reason"] == "unknown_or_restarted"
    _answer_workdir(client)
    fresh = _create(client, {"script": "scripts/fig2.py"}).get_json()
    assert fresh["intent"] is None and fresh["phase"] == "ready_to_run"
    assert _recheck(client, fresh["session_id"])["intent"] is None
    assert fake_pool["build_calls"] == 0


# ===========================================================================
# argv 换了：未执行的意图受控转移，新旧合计不多跑
# ===========================================================================
@pytest.fixture
def argv_pool(fake_pool, monkeypatch):
    """带 argv 的执行线程以 `run=` 调 `peek`：替身 peek 接住它（其余照旧）。"""
    monkeypatch.setattr(engine_pool, "peek", lambda script, root, **kw: fake_pool["peek"])
    return fake_pool


def _two_sessions(client, root_script: str):
    one = _create(client, {"script": root_script, "argv": ["--panel", "a"]}).get_json()
    two = _create(client, {"script": root_script, "argv": ["--panel", "b"]}).get_json()
    assert one["session_id"] != two["session_id"]
    return one, two


def test_changing_argv_transfers_an_unclaimed_intent_and_retires_the_old_target(
    client, tmp_path, fake_pool, argv_pool, sessions
):
    root = _workdir_project(tmp_path)
    _open(client, root)
    one, two = _two_sessions(client, "scripts/fig2.py")
    old_run_arm = _action(one, "run_when_ready")["id"]
    assert _arm(client, one).status_code == 202
    waiting = _get(client, one["session_id"]).get_json()
    old_cancel = _action(waiting, "cancel")  # 旧页面手里捏着的、还没认领的动作
    old_recheck = _action(waiting, "recheck")
    # 不转移就再武装另一份运行配置：并存会让合计多跑一次 → 拒绝，零副作用
    conflict = _arm(client, two)
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "preparation_run_intent_conflict"
    assert _get(client, two["session_id"]).get_json()["intent"] is None
    moved = _arm(client, two, transfer_from=one["session_id"])
    assert moved.status_code == 202, moved.get_json()
    old = _get(client, one["session_id"]).get_json()
    assert old["intent"]["status"] == "transferred"
    assert not [a for a in old["actions"] if a["kind"] in ("run", "prepare_dependencies")]
    assert old["provider"]["attempts"] == 0 and old["config_revision"] == one["config_revision"]
    for gone in (old_cancel, old_recheck):  # 转移当场退役：不用等谁再读一次报告
        refused = client.post(
            f"{SESSIONS}/{one['session_id']}/actions",
            json={"action_id": gone["id"], "expected_config_revision": one["config_revision"]},
        )
        assert refused.status_code == 404
        assert refused.get_json()["code"] == "preparation_action_unknown"
    replay = client.post(  # 旧页面重放当初的认领：幂等回当初那个（已转移的）意图，没有新副作用
        f"{SESSIONS}/{one['session_id']}/actions",
        json={"action_id": old_run_arm, "expected_config_revision": one["config_revision"]},
    )
    assert replay.status_code == 200 and replay.get_json()["claimed"] is False
    assert replay.get_json()["report"]["intent"]["status"] == "transferred"
    new = _get(client, two["session_id"]).get_json()
    assert new["intent"]["status"] == "active" and new["intent"]["transferred"] is True
    _answer_workdir(client)
    _recheck(client, one["session_id"])  # 旧目标收到重新检查：不会被旧意图启动
    assert fake_pool["build_calls"] == 0
    final_new = _recheck(client, two["session_id"])
    final_new = _settle(client, two["session_id"])
    assert final_new["intent"]["status"] == "launched"
    assert _get(client, one["session_id"]).get_json()["provider"]["attempts"] == 0
    assert _quiet(client, two["session_id"])["provider"]["attempts"] == 1
    assert fake_pool["build_calls"] == 1  # 新旧合计恰一次


def test_an_intent_that_already_claimed_execution_is_not_transferable(
    client, tmp_path, fake_pool, argv_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    one, two = _two_sessions(client, "fig.py")
    assert _arm(client, one).status_code == 202
    _settle(client, one["session_id"])
    assert fake_pool["build_calls"] == 1
    refused = _arm(client, two, transfer_from=one["session_id"])
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "preparation_run_intent_not_transferable"
    assert refused.get_json()["params"]["executed"] is False
    assert _get(client, two["session_id"]).get_json()["intent"] is None
    assert fake_pool["build_calls"] == 1  # 没有为"换了参数"悄悄再跑一次


def test_transfer_racing_the_old_target_never_executes_more_than_once(
    client, tmp_path, fake_pool, argv_pool, sessions
):
    """旧目标的推进（目录答完后的 recheck）与 argv 提交并发：两边都在旧会话的锁上串行，合计恰一次，不论谁先。"""
    outcomes = {"old_first": 0, "transfer_first": 0}
    for i in range(8):
        prepsession.SESSIONS.reset_for_tests()
        root = _workdir_project(tmp_path, f"r{i}")
        _open(client, root)
        one, two = _two_sessions(client, "scripts/fig2.py")
        launches: list[str] = []

        def launcher(plan, on_done, _into=launches):
            _into.append(plan.plan_id)

        def serve(report: dict, kind: str, **kw):
            action = _action(report, kind)
            digest = action["impact"].get("impact_digest")
            return prepsession.SESSIONS.act(
                report["session_id"],
                report["project_id"],
                action["id"],
                report["config_revision"],
                launch=launcher,
                impact_digest=digest if kind == "run_when_ready" else None,
                **kw,
            )

        serve(one, "run_when_ready")
        _answer_workdir(client)
        two = _recheck(client, two["session_id"])  # 界面答完目录后会让每个空闲会话重新检查
        one_now = _get(client, one["session_id"]).get_json()
        barrier = threading.Barrier(2)
        errors: list = []

        def old_side() -> None:
            barrier.wait()
            serve(one_now, "recheck")

        def transfer_side() -> None:
            barrier.wait()
            try:
                serve(two, "run_when_ready", transfer_from=one["session_id"])
            except prepsession.SessionError as exc:
                errors.append(exc.code)

        threads = [threading.Thread(target=old_side), threading.Thread(target=transfer_side)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert len(launches) == 1, (i, launches, errors)
        outcomes["old_first" if errors else "transfer_first"] += 1
        if errors:
            assert errors == ["preparation_run_intent_not_transferable"]
    assert sum(outcomes.values()) == 8


def _three_sessions(client, script: str):
    return [
        _create(client, {"script": script, "argv": ["--panel", tag]}).get_json()
        for tag in ("a", "b", "c")
    ]


def _service_claim(report: dict, kind: str, launches: list, **kw):
    action = _action(report, kind)
    return prepsession.SESSIONS.act(
        report["session_id"],
        report["project_id"],
        action["id"],
        report["config_revision"],
        launch=lambda plan, on_done: launches.append(plan.plan_id),
        impact_digest=action["impact"].get("impact_digest") if kind == "run_when_ready" else None,
        **kw,
    )


def test_concurrent_arming_of_two_run_configurations_leaves_exactly_one_intent(
    client, tmp_path, fake_pool, argv_pool, sessions, monkeypatch
):
    """两个标签页对同一脚本、不同运行配置并发武装：冲突检查与意图赋值在同一临界区，只有一边成功（Codex r4254020393）。"""
    real_check = prepsession.SESSIONS._reject_conflict
    meet = threading.Barrier(2)

    def check_then_meet(sess):
        # 把「检查通过 → 建意图」之间的窗口撑开：两个线程都过了检查再往下走。临界区原子时，另一个线程被锁挡在检查之外，
        # 栅栏超时放行，它随后的检查会撞上已建好的意图
        real_check(sess)
        try:
            meet.wait(0.3)
        except threading.BrokenBarrierError:
            meet.reset()

    monkeypatch.setattr(prepsession.SESSIONS, "_reject_conflict", check_then_meet)
    for i in range(6):
        prepsession.SESSIONS.reset_for_tests()
        _open(client, _workdir_project(tmp_path, f"c{i}"))
        one, two, _three = _three_sessions(client, "scripts/fig2.py")
        launches: list[str] = []
        barrier = threading.Barrier(2)
        outcomes: list = []

        def arm(report, _l=launches, _o=outcomes, _b=barrier):
            _b.wait()
            try:
                _service_claim(report, "run_when_ready", _l)
                _o.append("ok")
            except prepsession.SessionError as exc:
                _o.append(exc.code)

        threads = [threading.Thread(target=arm, args=(r,)) for r in (one, two)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(outcomes) == ["ok", "preparation_run_intent_conflict"], (i, outcomes)
        live = [
            r
            for r in (one, two)
            if (prepsession.SESSIONS.get(r["session_id"], r["project_id"]).intent or None)
            is not None
        ]
        assert len(live) == 1
        # 目录答完、两边都重新检查：合计恰一次执行
        _answer_workdir(client)
        for r in (one, two):
            _recheck(client, r["session_id"])
        assert len(launches) == 1, (i, launches)


def test_a_transfer_racing_a_plain_arm_never_leaves_two_active_intents(
    client, tmp_path, fake_pool, argv_pool, sessions
):
    for i in range(30):
        prepsession.SESSIONS.reset_for_tests()
        _open(client, _workdir_project(tmp_path, f"t{i}"))
        one, two, three = _three_sessions(client, "scripts/fig2.py")
        launches: list[str] = []
        _service_claim(one, "run_when_ready", launches)
        barrier = threading.Barrier(2)
        outcomes: list = []

        def go(report, kw, _l=launches, _o=outcomes, _b=barrier):
            _b.wait()
            try:
                _service_claim(report, "run_when_ready", _l, **kw)
                _o.append("ok")
            except prepsession.SessionError as exc:
                _o.append(exc.code)

        threads = [
            threading.Thread(target=go, args=(two, {"transfer_from": one["session_id"]})),
            threading.Thread(target=go, args=(three, {})),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        active = [
            r["session_id"]
            for r in (one, two, three)
            if (lambda x: x is not None and x.status == "active")(
                prepsession.SESSIONS.get(r["session_id"], r["project_id"]).intent
            )
        ]
        assert len(active) == 1, (i, outcomes, active)
        assert launches == []


# ===========================================================================
# 授权范围内的受管安装（假换代事务 `held`）
# ===========================================================================
def _arm_dep(client, report: dict, *, echo: bool = True):
    action = next(a for a in report["actions"] if a["kind"] == "run_when_ready")
    body = {"action_id": action["id"], "expected_config_revision": report["config_revision"]}
    if echo:
        body["impact_digest"] = action["impact"]["impact_digest"]
    return client.post(f"{SESSIONS}/{report['session_id']}/actions", json=body)


class TestInstallScope:
    def test_arming_installs_once_within_the_grant_without_a_second_click(
        self, client, house, offline_managed_env, opened, held
    ):
        gate, calls = held
        opened([ALPHA, BETA])
        report = _dep_create(client, {"script": "figure.py"})
        assert "run_when_ready" in _kinds(report) and "prepare_dependencies" in _kinds(report)
        assert _arm_dep(client, report).status_code == 202
        _wait_until(lambda: calls)  # 没有再点「准备依赖」，安装事务自己起了
        busy = _dep_get(client, report["session_id"])
        assert busy["phase"] == "preparing_environment" and busy["intent"]["status"] == "active"
        assert busy["intent"]["steps"] == 1
        assert _arm_dep(client, report).status_code == 200  # 重复点击：同一个意图
        gate.set()
        # 假事务只"完成"而没真装：同一份影响还在 → 没有进展，不循环重装
        done = _wait(client, report["session_id"], lambda r: r["intent"]["status"] != "active")
        assert done["intent"]["status"] == "revoked" and done["intent"]["reason"] == "no_progress"
        assert len(calls) == 1 and done["provider"]["attempts"] == 0

    def test_a_user_environment_install_is_beyond_the_grant_and_nothing_starts(
        self, client, house, opened, held
    ):
        from support.dependency_repair import real_venv
        from tavotto.engine import projectenv

        gate, calls = held
        proj = opened([ALPHA])
        projectenv.remember(
            proj,
            projectenv.interpreter_of(real_venv(proj)),
            automatic=False,
            trigger=projectenv.TRIGGER_RECOMMENDED,
        )
        report = _dep_create(client, {"script": "figure.py"})
        assert _arm_dep(client, report).status_code == 202
        after = _dep_get(client, report["session_id"])
        assert after["intent"]["status"] == "revoked"
        assert after["intent"]["reason"].startswith("grant_exceeded:")
        assert calls == [] and not deprepair.installing(proj)
        # 手动「准备依赖」仍是用户自己的确认路径，没被意图关掉
        assert any(a["kind"] == "prepare_dependencies" for a in after["actions"])
        gate.set()

    def test_an_impact_widened_while_installing_needs_the_user_not_a_second_auto_install(
        self, client, house, offline_managed_env, opened, held
    ):
        gate, calls = held
        proj = opened([ALPHA])
        report = _dep_create(client, {"script": "figure.py"})
        assert _arm_dep(client, report).status_code == 202
        _wait_until(lambda: calls)
        # 别处在安装期间改了 requirements 里要装的东西（脚本字节没动）：这份影响不是本意图批准的那一份
        (proj / "requirements.txt").write_text(f"{ALPHA[0]}==1.0\n", encoding="utf-8")
        gate.set()
        done = _wait(client, report["session_id"], lambda r: r["intent"]["status"] != "active")
        assert (
            done["intent"]["status"] == "revoked" and done["intent"]["reason"] == "impact_expanded"
        )
        assert len(calls) == 1  # 没有为新增的包再自动装一次
        assert any(
            a["kind"] == "prepare_dependencies" for a in done["actions"]
        )  # 用户看到真实新增影响再决定

    def test_cancel_then_a_late_done_cannot_start_anything(
        self, client, house, offline_managed_env, opened, held
    ):
        gate, calls = held
        opened([ALPHA])
        report = _dep_create(client, {"script": "figure.py"})
        assert _arm_dep(client, report).status_code == 202
        _wait_until(lambda: calls)
        busy = _wait(client, report["session_id"], lambda r: "cancel" in _kinds(r))
        assert _dep_act(client, busy, "cancel").status_code == 200
        assert _dep_get(client, report["session_id"])["intent"]["status"] == "cancelled"
        gate.set()  # 安装线程迟到地"完成"
        time.sleep(0.5)
        later = _dep_get(client, report["session_id"])
        assert later["intent"]["status"] == "cancelled" and later["provider"]["attempts"] == 0
        assert len(calls) == 1


# ===========================================================================
# 真走一遍：真 venv / 真 pip / 真 worker
# ===========================================================================
@needs_worker
class TestRealRoundTrip:
    def test_install_recheck_and_exactly_one_first_run_without_another_click(
        self, client, house, offline_managed_env, opened
    ):
        proj = opened(
            [ALPHA, BETA],
            script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n",
        )
        (proj / "figure.py").write_text(
            (proj / "figure.py")
            .read_text("utf-8")
            .replace("ax.plot([0, 1], [0, 1])", "ax.plot([0, 1], [0, Y])"),
            encoding="utf-8",
        )
        report = _dep_create(client, {"script": "figure.py"})
        sid = report["session_id"]
        assert _arm_dep(client, report).status_code == 202
        final = _wait(
            client,
            sid,
            lambda r: (
                r["phase"] in ("completed", "partial", "action_required")
                or r["intent"]["status"] == "revoked"
            ),
        )
        assert final["phase"] == "completed", final["intent"]
        assert final["intent"]["status"] == "launched" and final["facts"]["figure_captured"]
        assert final["provider"]["attempts"] == 1
        assert final["config_revision"] > report["config_revision"]  # 安装带来的预期修订变化
        for _ in range(3):
            _dep_get(client, sid)
            _dep_create(client, {"script": "figure.py"})
        time.sleep(0.3)
        assert _dep_get(client, sid)["provider"]["attempts"] == 1

    def test_a_script_that_wrote_an_external_counter_is_not_rerun_by_the_repair(
        self, client, house, offline_managed_env, opened, tmp_path
    ):
        """EX13 / AC-14：脚本先往项目外的计数文件写一行、之后才缺包。准备好修复不能把计数悄悄变成 2；
        只有用户明确再点一次运行，计数才是 2（对照：夹具确实数得出真实执行）。"""
        counter = tmp_path / "external_counter.txt"
        proj = opened(
            [],
            script_body=(
                f"open({str(counter)!r}, 'a').write('ran\\n')\n"
                "def _load():\n    import tavotto_test_alpha as a\n    return a.VALUE\n"
                "Y = _load()\n"
            ),
            extra_requirements=f"{ALPHA[0]}\n",
        )
        (proj / "figure.py").write_text(
            (proj / "figure.py")
            .read_text("utf-8")
            .replace("ax.plot([0, 1], [0, 1])", "ax.plot([0, 1], [0, Y])"),
            encoding="utf-8",
        )
        report = _dep_create(client, {"script": "figure.py"})
        sid = report["session_id"]
        assert report["phase"] == "ready_to_run"  # 静态看不出缺包
        assert _arm_dep(client, report).status_code == 202
        missing = _wait(client, sid, lambda r: r["outcome"].get("kind") == "needs_dependencies")
        assert counter.read_text("utf-8").count("ran") == 1  # 那一次执行已经发生
        assert missing["intent"]["status"] == "launched"
        assert _dep_act(client, missing, "prepare_dependencies").status_code == 202
        ready = _wait(client, sid, lambda r: r["phase"] == "ready_to_run")
        assert ready["dependency_delta"]["rerun_required"] is True
        time.sleep(1.5)  # 给任何"悄悄重跑"留出时间
        again = _dep_get(client, sid)
        assert counter.read_text("utf-8").count("ran") == 1
        assert again["provider"]["attempts"] == 1 and again["intent"]["status"] == "launched"
        assert "run" in _kinds(again)  # 要再跑，是用户明确的下一次尝试
        assert _dep_act(client, again, "run").status_code == 202
        done = _wait(
            client, sid, lambda r: r["phase"] in ("completed", "partial", "action_required")
        )
        assert done["provider"]["attempts"] == 2
        assert counter.read_text("utf-8").count("ran") == 2

    def test_transferring_during_an_install_joins_the_job_without_spending_another_step(
        self, client, house, offline_managed_env, opened, monkeypatch
    ):
        """安装进行中 argv 变了：新会话加入同一个作业，不再扣一步；装完执行恰一次（Codex r4254020397）。"""
        real = deprepair._run_generation
        started, gate = threading.Event(), threading.Event()

        def gated(job, cancel_ev):
            started.set()
            assert gate.wait(120)
            return real(job, cancel_ev)

        monkeypatch.setattr(deprepair, "_run_generation", gated)
        opened([ALPHA])
        one = _dep_create(client, {"script": "figure.py", "argv": ["--panel", "a"]})
        two = _dep_create(client, {"script": "figure.py", "argv": ["--panel", "b"]})
        assert _arm_dep(client, one).status_code == 202
        assert started.wait(60)
        action = next(a for a in two["actions"] if a["kind"] == "run_when_ready")
        moved = client.post(
            f"{SESSIONS}/{two['session_id']}/actions",
            json={
                "action_id": action["id"],
                "expected_config_revision": two["config_revision"],
                "impact_digest": action["impact"]["impact_digest"],
                "transfer_from": one["session_id"],
            },
        )
        assert moved.status_code == 202, moved.get_json()
        joined = _dep_get(client, two["session_id"])
        assert joined["intent"]["status"] == "active" and joined["intent"]["steps"] == 1
        assert joined["provider"]["dependency"]["joined"] is True
        gate.set()
        final = _wait(
            client,
            two["session_id"],
            lambda r: (
                r["phase"] in ("completed", "partial", "action_required")
                or r["intent"]["status"] == "revoked"
            ),
        )
        assert final["phase"] == "completed", final["intent"]
        assert final["intent"]["status"] == "launched" and final["provider"]["attempts"] == 1
        old = _dep_get(client, one["session_id"])
        assert old["intent"]["status"] == "transferred" and old["provider"]["attempts"] == 0
