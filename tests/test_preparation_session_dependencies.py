"""T06：依赖准备并入准备会话——缺包待办 → 一次授权（绑定实际影响）→ 安装 → 差异重计划 → 首图，全在同一个会话里。

不新造面板后端：动作 `prepare_dependencies` 引用 `deprepair.start_confirmed`（= 既有的 `create_joint_plan` /
`create_plan` + `prepare` / `install` + `managedenv` 代事务 + `envlease`），会话只记「认领了哪份作业、确认的是哪份
影响、终局是什么」。

两组：

* 认领 / 授权 / 差额的本地状态——用会停住的假「换代事务」（`_run_generation`），独立计数事务被起了几次；
* 真走一遍——真 venv、真 pip（离线 wheelhouse，只有本地测试包）、真 worker、真出图，再用独立真值核对图里的
  数（包里的 `VALUE = 42` 进了 Figure 的 y 轴范围）。冷机 / 联网供应属于 T11，这里不声称。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker
from tavotto.engine import (
    depplan,
    deprepair,
    depresolve,
    envlease,
    managedenv,
    prepsession,
    taskdiag,
)

pytest_plugins = ("support.dependency_repair",)

SESSIONS = "/api/engine/preparation-sessions"
ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
GAMMA = ("tavotto-test-gamma", "tavotto_test_gamma")
LATE = ("tavotto-test-late", "tavotto_test_late")


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    taskdiag.reset_for_tests()
    prepsession.SESSIONS.reset_for_tests()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()
    taskdiag.reset_for_tests()
    prepsession.SESSIONS.reset_for_tests()


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    for name, import_name in (ALPHA, BETA, GAMMA, LATE):
        build_wheel(dest, name=name, import_name=import_name, version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _write(proj: Path, pairs, *, script_body: str = "", extra_requirements: str = "") -> None:
    (proj / "requirements.txt").write_text(
        "".join(f"{dist}\n" for dist, _imp in pairs) + extra_requirements, encoding="utf-8"
    )
    imports = "".join(f"import {imp}\n" for _dist, imp in pairs)
    (proj / "figure.py").write_text(
        f"{imports}import matplotlib.pyplot as plt\n{script_body}"
        "fig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\nfig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )


@pytest.fixture
def opened(tmp_path):
    """打开的项目（收尾时关掉，别让 watcher 一直监视临时目录里的 venv）。"""
    from tavotto import app as m

    made: list[Path] = []

    def open_it(pairs, *, name: str = "paper", **kw) -> Path:
        proj = tmp_path / name
        proj.mkdir()
        _write(proj, pairs, **kw)
        m.open_project(str(proj))
        made.append(proj)
        return proj

    yield open_it
    for proj in made:
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(proj)]:
            m.close_project(pid, wait=True)


@pytest.fixture
def held(monkeypatch):
    """会停住的假换代事务（认领 / 指纹 / 选择复核是真的；建 venv + pip 换成"记一次 → 等放行 → done"）。"""
    gate = threading.Event()
    calls: list[str] = []

    def fake(job, cancel_ev):
        calls.append(job.progress_id)
        job.emit(deprepair.STATE_INSTALLING)
        assert gate.wait(60), "测试没有放行"
        return job.emit(deprepair.STATE_DONE, result={"ok": True, "activated": True})

    monkeypatch.setattr(deprepair, "_run_generation", fake)
    yield gate, calls
    gate.set()
    # 线程都收完再撤替身：晚到的作业线程会去调"此刻的" `_run_generation`，撞进下一个用例的计数
    _wait_idle()


def _wait_idle(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while deprepair._running and time.time() < deadline:
        time.sleep(0.02)


def _wait_until(predicate, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, "条件没有在限期内成立"
        time.sleep(0.02)


def _create(client, body: dict) -> dict:
    resp = client.post(SESSIONS, json=body)
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()


def _get(client, sid: str) -> dict:
    resp = client.get(f"{SESSIONS}/{sid}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _act(client, report: dict, kind: str, **extra):
    action = next(a for a in report["actions"] if a["kind"] == kind)
    return client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": action["id"],
            "expected_config_revision": report["config_revision"],
            **extra,
        },
    )


def _kinds(report: dict) -> list[str]:
    return sorted(a["kind"] for a in report["actions"])


def _wait(client, sid: str, predicate, *, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while True:
        report = _get(client, sid)
        if predicate(report):
            return report
        assert time.time() < deadline, json.dumps(report, ensure_ascii=False)[:2000]
        time.sleep(0.05)


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# ===========================================================================
# 授权：一份计划、一次确认、绑定实际影响
# ===========================================================================
class TestAuthorisation:
    def test_several_missing_packages_are_one_requirement_and_one_confirmation(
        self, client, house, offline_managed_env, opened
    ):
        """D01：多个已知缺包是一份完整计划、一次确认，不逐包报错；检查本身不装、不写用户项目。"""
        proj = opened([ALPHA, BETA, GAMMA])
        before = _tree(proj)
        report = _create(client, {"script": "figure.py"})
        assert report["phase"] == "awaiting_confirmation"
        (check,) = [c for c in report["checks"] if c["id"] == "dependencies"]
        assert (
            check["status"] == "needs_action" and check["code"] == "dependency_preparation_required"
        )
        (req,) = [r for r in report["requirements"] if r["kind"] == "dependency_authorization"]
        assert sorted(req["payload"]["plan"]["requirements"]) == sorted(
            d for d, _ in (ALPHA, BETA, GAMMA)
        )
        assert _kinds(report) == ["prepare_dependencies", "recheck"]  # 还不能运行
        (action,) = [a for a in report["actions"] if a["kind"] == "prepare_dependencies"]
        impact = action["impact"]
        assert impact["installs"] == sorted(d for d, _ in (ALPHA, BETA, GAMMA))
        assert impact["installs_packages"] is True and impact["executes_user_script"] is False
        assert impact["changes_environment"] is True and impact["writes_to_project"] == []
        assert (
            impact["scope"] == "managed_generation" and impact["modifies_user_environment"] is False
        )
        assert impact["rollback"] == "generation_atomic"
        assert impact["impact_digest"] == check["detail"]["impact_digest"]
        # 检查什么都没装、没建环境、没写用户项目
        assert managedenv.python_of(proj) is None and _tree(proj) == before

    def test_confirming_a_stale_digest_is_refused_and_the_action_stays_usable(
        self, client, house, offline_managed_env, opened, held
    ):
        _gate, calls = held
        opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        resp = _act(client, report, "prepare_dependencies", impact_digest="0" * 32)
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["code"] == "preparation_impact_changed" and body["params"]["executed"] is False
        assert body["params"]["impact_digest"]  # 现在的影响交回去
        assert calls == []
        # 动作本身没作废（读了旧报告不等于世界变了）：带对的摘要照常认领
        again = _get(client, report["session_id"])
        assert again["config_revision"] == report["config_revision"]
        good = _act(
            client,
            again,
            "prepare_dependencies",
            impact_digest=next(a for a in again["actions"] if a["kind"] == "prepare_dependencies")[
                "impact"
            ]["impact_digest"],
        )
        assert good.status_code == 202

    def test_a_world_that_changed_after_the_check_needs_a_fresh_confirmation(
        self, client, house, offline_managed_env, opened, held
    ):
        """C05：确认之后项目多声明了一个包——旧同意不覆盖新增的，零副作用，重新检查才有新动作。"""
        _gate, calls = held
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        old_digest = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")[
            "impact"
        ]["impact_digest"]
        _write(proj, [ALPHA, BETA])
        resp = _act(client, report, "prepare_dependencies")
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["code"] == "preparation_impact_changed"
        assert BETA[0] in body["params"]["impact"]["installs"]
        assert calls == [] and not deprepair.installing(proj)
        stale = _get(client, report["session_id"])
        assert stale["outcome"]["kind"] == "stale" and "prepare_dependencies" not in _kinds(stale)
        # 重新检查：新的修订、新的动作、新的摘要；旧动作 id 再点也是 409
        client.post(
            f"{SESSIONS}/{report['session_id']}/actions",
            json={
                "action_id": next(a for a in stale["actions"] if a["kind"] == "recheck")["id"],
                "expected_config_revision": stale["config_revision"],
            },
        )
        fresh = _get(client, report["session_id"])
        assert fresh["config_revision"] > report["config_revision"]
        new = next(a for a in fresh["actions"] if a["kind"] == "prepare_dependencies")
        assert new["impact"]["impact_digest"] != old_digest and len(new["impact"]["installs"]) == 2

    def test_a_wider_install_set_is_a_new_revision_and_the_old_action_is_refused(
        self, client, house, offline_managed_env, opened, held
    ):
        """C05：要装的集合变宽了（没点任何动作，只是再检查）→ 配置修订 +1、旧动作 409、新动作带新摘要。"""
        _gate, calls = held
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        old = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")
        _write(proj, [ALPHA, BETA])
        again = _create(client, {"script": "figure.py"})  # 同一目标：复用会话，语义指纹变了才换修订
        assert again["session_id"] == report["session_id"]
        assert again["config_revision"] == report["config_revision"] + 1
        new = next(a for a in again["actions"] if a["kind"] == "prepare_dependencies")
        assert new["id"] != old["id"]
        assert new["impact"]["impact_digest"] != old["impact"]["impact_digest"]
        assert len(new["impact"]["installs"]) == 2
        refused = client.post(
            f"{SESSIONS}/{report['session_id']}/actions",
            json={"action_id": old["id"], "expected_config_revision": report["config_revision"]},
        )
        # 没认领过的旧修订动作已作废（不再存在）；认领过的留着，对它的重放是 409 修订已变
        assert refused.status_code == 404
        assert refused.get_json()["code"] == "preparation_action_unknown"
        assert calls == []

    def test_progress_and_unrelated_edits_do_not_revoke_a_confirmation(
        self, client, house, offline_managed_env, opened, held
    ):
        """C03/C05：与依赖无关的脚本改动 / 反复读报告不推进配置修订、不换动作 id；认领照常成功。"""
        gate, calls = held
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        action_id = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")["id"]
        for _ in range(3):
            again = _get(client, report["session_id"])
            assert again["config_revision"] == report["config_revision"]
            assert (
                next(a for a in again["actions"] if a["kind"] == "prepare_dependencies")["id"]
                == action_id
            )
        (proj / "figure.py").write_text(
            "# 与依赖无关的改动\n" + (proj / "figure.py").read_text("utf-8"), encoding="utf-8"
        )
        assert _act(client, report, "prepare_dependencies").status_code == 202
        _wait(client, report["session_id"], lambda r: r["phase"] == "preparing_environment")
        _wait_until(lambda: calls)
        assert len(calls) == 1
        gate.set()

    def test_a_user_environment_install_requires_the_echoed_digest(
        self, client, house, opened, held, tmp_path
    ):
        """C30/D：往用户自己的 venv 里装是单独的确认——光"点了一下"不够，必须回显它看到的影响摘要。"""
        from support.dependency_repair import real_venv
        from tavotto.engine import projectenv

        gate, calls = held
        proj = opened([ALPHA])
        venv = real_venv(proj)
        assert projectenv.remember(
            proj,
            projectenv.interpreter_of(venv),
            automatic=False,
            trigger=projectenv.TRIGGER_RECOMMENDED,
        )
        report = _create(client, {"script": "figure.py"})
        action = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")
        assert action["impact"]["modifies_user_environment"] is True
        assert action["impact"]["scope"] == "project_venv_in_place"
        assert action["impact"]["rollback"] == "none_partial_changes_possible"
        # 采用（使用）不含修改权限：没回显摘要 → 400，什么都没动
        bare = _act(client, report, "prepare_dependencies")
        assert bare.status_code == 400
        assert bare.get_json()["code"] == "preparation_impact_unconfirmed"
        assert calls == [] and not deprepair.installing(proj)
        gate.set()


class TestPinned:
    @needs_worker
    def test_a_global_pin_is_named_and_no_install_action_is_offered(
        self, client, house, offline_managed_env, opened, monkeypatch
    ):
        """E05：全局显式解释器压着——会话说清是谁锁的（来源 / 变量名，不带路径），不给授权动作，不假装依赖没问题。"""
        from support.dependency_repair import WORKER_PY

        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", WORKER_PY)
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        (dep,) = [c for c in report["checks"] if c["id"] == "dependencies"]
        assert dep["status"] == "blocked" and dep["code"] == "dependency_interpreter_pinned"
        assert dep["detail"]["pinned"] == {
            "source": "env_override",
            "variable": "TAVOTTO_WORKER_PYTHON",
        }
        assert report["outcome"] == {"kind": "blocked", "code": "dependency_interpreter_pinned"}
        assert _kinds(report) == ["recheck"]  # 没有 run、没有 prepare_dependencies
        (req,) = [r for r in report["requirements"] if r["kind"] == "dependency_pinned"]
        assert req["payload"] == dep["detail"]["pinned"]
        text = json.dumps(report, ensure_ascii=False)
        assert WORKER_PY not in text and str(proj) not in text  # 报告里没有机器路径
        assert managedenv.python_of(proj) is None


# ===========================================================================
# 认领：幂等、原作业、取消的所有权
# ===========================================================================
class TestClaim:
    def test_two_tabs_claim_one_job_and_the_session_shows_the_real_progress(
        self, client, house, offline_managed_env, opened, held
    ):
        """D08：双击 / 两个标签页提交同一个已确认动作——一次事务；重连读到的是真实进度。"""
        gate, calls = held
        opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        first = _act(client, report, "prepare_dependencies")
        second = _act(client, report, "prepare_dependencies")
        assert first.status_code == 202 and first.get_json()["claimed"] is True
        assert second.status_code == 200 and second.get_json()["claimed"] is False
        busy = _wait(client, report["session_id"], lambda r: r["phase"] == "preparing_environment")
        _wait_until(lambda: calls)
        busy = _get(client, report["session_id"])
        assert len(calls) == 1
        dep = busy["provider"]["dependency"]
        assert dep["state"] in ("preparing", "installing") and not dep["joined"]
        assert dep["plan_id"] == calls[0] and dep["origin"] == "joint"
        # 重连（新读一份报告）读到的是同一份真实进度，不是被重置回待确认
        assert _get(client, report["session_id"])["provider"]["dependency"]["plan_id"] == calls[0]
        assert _kinds(busy) == ["cancel"]  # 在装的时候：不能运行、不能再授权、不能重新检查
        gate.set()
        done = _wait(client, report["session_id"], lambda r: r["phase"] != "preparing_environment")
        assert len(calls) == 1 and done["phase"] != "preparing_environment"

    def test_a_second_session_for_the_same_impact_joins_the_original_job(
        self, client, house, offline_managed_env, opened, held
    ):
        """D08/C06：同一脚本、另一份运行配置（另一个会话）确认同一份影响——认领原作业，不起第二个 pip，也不拥有
        它（没有取消）；两边都读到终局。用户可以在安装时并行换参数，不阻塞也不重复安装。"""
        gate, calls = held
        opened([ALPHA])
        one = _create(client, {"script": "figure.py"})
        two = _create(client, {"script": "figure.py", "argv": ["--panel", "b"]})
        assert one["session_id"] != two["session_id"]
        assert _act(client, one, "prepare_dependencies").status_code == 202
        _wait(client, one["session_id"], lambda r: r["phase"] == "preparing_environment")
        _wait_until(lambda: calls)
        # 安装进行中，用户在另一个会话里填好了参数、也确认了同一份影响
        joined = _act(client, two, "prepare_dependencies")
        assert joined.status_code == 200 and joined.get_json()["claimed"] is False
        report = _wait(client, two["session_id"], lambda r: r["phase"] == "preparing_environment")
        assert report["outcome"]["reason"] == "joined_existing"
        assert report["provider"]["dependency"]["joined"] is True
        assert report["provider"]["dependency"]["plan_id"] == calls[0]
        assert "cancel" not in _kinds(report)  # 只退役自己拥有的工作
        assert len(calls) == 1
        gate.set()
        for sid in (one["session_id"], two["session_id"]):
            _wait(client, sid, lambda r: r["phase"] != "preparing_environment")
        assert len(calls) == 1

    def test_the_owner_can_cancel_and_the_failure_is_recoverable_in_place(
        self, client, house, offline_managed_env, opened, monkeypatch
    ):
        """D05：取消 / 失败 → 旧 active 保留（这里根本没有）、新代不 active；原面板里再给授权动作（新 id）可重试。"""
        gate = threading.Event()
        started = threading.Event()

        def real_wait(job, cancel_ev):
            started.set()
            job.emit(deprepair.STATE_INSTALLING)
            while not cancel_ev.is_set() and not gate.is_set():
                time.sleep(0.02)
            return job.emit(
                deprepair.STATE_CANCELLED,
                code=deprepair.ERROR_CANCELLED,
                result={"activated": False},
            )

        monkeypatch.setattr(deprepair, "_run_generation", real_wait)
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        first_action = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")
        assert _act(client, report, "prepare_dependencies").status_code == 202
        assert started.wait(30)
        busy = _wait(client, report["session_id"], lambda r: "cancel" in _kinds(r))
        assert _act(client, busy, "cancel").status_code == 200
        after = _wait(client, report["session_id"], lambda r: r["phase"] != "preparing_environment")
        assert after["phase"] == "awaiting_confirmation" and after["outcome"]["kind"] == "cancelled"
        assert after["outcome"]["reason"] == "dependency_preparation"
        assert managedenv.python_of(proj) is None  # 没有任何环境被切走
        retry = next(a for a in after["actions"] if a["kind"] == "prepare_dependencies")
        assert retry["id"] != first_action["id"]  # 新的明确确认 → 新的尝试
        assert after["config_revision"] == report["config_revision"]


# ===========================================================================
# 真走一遍：缺包待办 → 授权 → 真安装 → 差异重计划 → 首图
# ===========================================================================
@needs_worker
class TestRealRoundTrip:
    def test_from_the_todo_to_a_correct_first_figure_in_one_session(
        self, client, house, offline_managed_env, opened
    ):
        """D01/D05/D06/C30/R01 的真实路径：真 venv、真 pip（本地 wheelhouse）、真 worker 出图。

        脚本用 `tavotto_test_alpha.VALUE`（= 42，独立于被测接口的真值）当数据，所以 Figure 的 y 轴范围能证明
        包真的装进了跑脚本的那个环境。"""
        proj = opened(
            [ALPHA],
            script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n",
        )
        (proj / "figure.py").write_text(
            (proj / "figure.py")
            .read_text("utf-8")
            .replace("ax.plot([0, 1], [0, 1])", "ax.plot([0, 1], [0, Y])"),
            encoding="utf-8",
        )
        report = _create(client, {"script": "figure.py"})
        assert report["phase"] == "awaiting_confirmation" and "run" not in _kinds(report)
        sid, rev0 = report["session_id"], report["config_revision"]
        confirmed = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")
        assert _act(client, report, "prepare_dependencies").status_code == 202

        # 装好 → 差异重计划：同一个会话，新的修订，不再缺包，可以运行
        ready = _wait(client, sid, lambda r: r["phase"] == "ready_to_run")
        assert ready["config_revision"] > rev0
        (dep_check,) = [c for c in ready["checks"] if c["id"] == "dependencies"]
        assert dep_check["status"] == "ok"
        delta = ready["dependency_delta"]
        assert delta["added"] == [] and delta["already_installed"] == [ALPHA[0]]
        assert delta["previous_impact_digest"] == confirmed["impact"]["impact_digest"]
        assert delta["current_impact_digest"] == "" and delta["rerun_required"] is False
        assert ready["provider"]["dependency"]["state"] == "done"
        # 旧动作 id 不再有效（环境换了代 → 修订变了）
        old = client.post(
            f"{SESSIONS}/{sid}/actions",
            json={"action_id": confirmed["id"], "expected_config_revision": rev0},
        )
        assert old.status_code == 409

        # 装进去的是 Tavotto 自己的隔离环境：一代、active、账上一笔，pip 装的是 wheelhouse 里的版本
        gen = managedenv.active_generation(proj)
        assert gen and [g for g in managedenv.generations(proj)] == [gen]
        assert {e["distribution"] for e in managedenv.state(proj)["installed"]} == {ALPHA[0]}

        # 运行：真 worker 在新环境里 import 到包并出图
        assert _act(client, ready, "run").status_code == 202
        final = _wait(
            client, sid, lambda r: r["phase"] in ("completed", "partial", "action_required")
        )
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        from tavotto.engine import figcapture

        asset = figcapture.runtime_asset_id("figure.py", "Fig1")
        rendered = client.post("/api/engine/render", json={"id": asset, "patches": []})
        assert rendered.status_code == 200, rendered.get_json()
        axes = next(e for e in rendered.get_json()["manifest"]["elements"] if e["role"] == "axes")
        ylim = next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")
        assert ylim[1] >= 42  # 包里的 VALUE 真的进了图

    def test_a_failed_install_keeps_the_old_generation_and_offers_a_new_confirmation(
        self, client, house, offline_managed_env, opened
    ):
        """D05：已有一代 active；下一次安装（缺一个 wheelhouse 里没有的包）失败 → 上一代原样 active、新代不 active、
        在同一个会话里给出失败 + 新的授权动作；失败现场（任务诊断）不被后来的成功改写。"""
        proj = opened([ALPHA])
        report = _create(client, {"script": "figure.py"})
        sid = report["session_id"]
        assert _act(client, report, "prepare_dependencies").status_code == 202
        ready = _wait(client, sid, lambda r: r["phase"] == "ready_to_run")
        first_gen = managedenv.active_generation(proj)
        assert first_gen
        # 项目多声明了一个 wheelhouse 里没有的包
        _write(proj, [ALPHA, ("tavotto-test-sentinel-absent", "tavotto_test_sentinel_absent")])
        rechecked = client.post(
            f"{SESSIONS}/{sid}/actions",
            json={
                "action_id": next(a for a in ready["actions"] if a["kind"] == "recheck")["id"],
                "expected_config_revision": ready["config_revision"],
            },
        )
        assert rechecked.status_code == 200
        todo = _get(client, sid)
        assert todo["phase"] == "awaiting_confirmation"
        assert _act(client, todo, "prepare_dependencies").status_code == 202
        failed = _wait(
            client, sid, lambda r: r["outcome"].get("reason") == "dependency_preparation"
        )
        assert failed["phase"] == "action_required" and failed["outcome"]["kind"] == "failed"
        assert failed["outcome"]["code"] == "dependency_not_found"
        assert managedenv.active_generation(proj) == first_gen  # 旧代原样 active
        gens = managedenv.generations(proj)
        assert [g for g, e in gens.items() if e["state"] == "ready"] == [first_gen]
        assert any(e["state"] == "incomplete" for e in gens.values())  # 新代不伪就绪
        retry = next(a for a in failed["actions"] if a["kind"] == "prepare_dependencies")
        assert (
            retry["id"]
            != next(a for a in todo["actions"] if a["kind"] == "prepare_dependencies")["id"]
        )
        # 失败现场冻结在任务诊断里（按本次尝试的 id），并且在会话报告里能找到它的引用
        ref = failed["provider"]["dependency"]["plan_id"]
        doc = client.get(f"/api/diagnostics/task?kind=dependency&ref={ref}").get_json()
        assert doc["snapshot"]["outcome"] == "failed"
        assert doc["snapshot"]["error"] == {"code": "dependency_not_found"}
        assert doc["snapshot"]["stage"]["last"] == "installing"

    def test_an_unlisted_import_found_while_running_is_offered_as_an_unverified_same_name_install(
        self, client, house, offline_managed_env, opened, monkeypatch
    ):
        """D02（2026-10-08 改写）：映射不到包名的 import 不再判 unknown——按同名当候选，但**不自动 pip install**：
        差异计划亮出「未经核对的同名包」、给 `prepare_dependencies` 等用户点；没点之前一个字节不装。"""
        installs: list[str] = []
        real = deprepair._run_generation
        monkeypatch.setattr(
            deprepair, "_run_generation", lambda job, ev: (installs.append("x"), real(job, ev))[1]
        )
        opened(
            [],
            script_body="def _load():\n    import tavotto_test_unmapped\n\n_load()\n",
        )
        report = _create(client, {"script": "figure.py"})
        sid = report["session_id"]
        assert _act(client, report, "run").status_code == 202
        pending = _wait(client, sid, lambda r: r["phase"] not in ("running", "ready_to_run"))
        assert pending["phase"] == "awaiting_confirmation"
        (req,) = [r for r in pending["requirements"] if r.get("origin") == "runtime_missing"]
        assert req["payload"]["module"] == "tavotto_test_unmapped"
        assert req["payload"]["installable"] is True
        assert req["payload"]["requirement"]["resolution_source"] == "same_name_unverified"
        assert req["payload"]["requirement"]["distribution"] == "tavotto_test_unmapped"
        assert req["payload"]["impact"]["unverified_same_name"] == ["tavotto_test_unmapped"]
        assert req["payload"]["impact"]["modifies_user_environment"] is False
        assert "prepare_dependencies" in _kinds(pending)
        assert installs == [], "用户没点之前不许装"

    def _runtime_missing_offer(self, client, opened, module: str):
        proj = opened([], script_body=f"def _load():\n    import {module}\n\n_load()\n")
        report = _create(client, {"script": "figure.py"})
        assert _act(client, report, "run").status_code == 202
        pending = _wait(
            client, report["session_id"], lambda r: r["phase"] not in ("running", "ready_to_run")
        )
        assert pending["phase"] == "awaiting_confirmation", pending
        return proj, pending

    def test_a_same_name_package_that_is_not_the_module_is_rolled_back_in_the_session(
        self, client, house, offline_managed_env, opened
    ):
        """真往返：用户点了「安装」→ pip 退出码 0（同名发行包在 wheelhouse 里）→ 装完核验发现它不提供脚本要的模块
        → 这一代不激活、会话报告带 `dependency_same_name_mismatch` 与卡片要说的两个名字，不再给安装动作。"""
        build_wheel(house, name="tavotto-test-squat", import_name="tavotto_test_something_else")
        proj, pending = self._runtime_missing_offer(client, opened, "tavotto_test_squat")
        assert _act(client, pending, "prepare_dependencies").status_code == 202
        failed = _wait(
            client,
            pending["session_id"],
            lambda r: r["outcome"].get("reason") == "dependency_preparation",
        )
        assert failed["outcome"]["kind"] == "failed"
        assert failed["outcome"]["code"] == "dependency_same_name_mismatch"
        dep = failed["provider"]["dependency"]
        assert (dep["code"], dep["module"], dep["unverified_same_name"]) == (
            "dependency_same_name_mismatch",
            "tavotto_test_squat",
            ["tavotto_test_squat"],
        )
        assert managedenv.active_generation(proj) is None, "核验不过的一代不许激活"
        assert managedenv.state(proj)["installed"] == []

    def test_a_same_name_package_that_is_the_module_installs_after_the_user_clicks(
        self, client, house, offline_managed_env, opened
    ):
        build_wheel(house, name="tavotto-test-unlisted-ok", import_name="tavotto_test_unlisted_ok")
        proj, pending = self._runtime_missing_offer(client, opened, "tavotto_test_unlisted_ok")
        assert managedenv.active_generation(proj) is None
        assert _act(client, pending, "prepare_dependencies").status_code == 202
        _wait_until(lambda: managedenv.active_generation(proj) is not None, timeout=300)
        _wait_idle()
        assert {e["distribution"] for e in managedenv.state(proj)["installed"]} == {
            "tavotto_test_unlisted_ok"
        }

    def test_a_forbidden_same_name_import_found_while_running_is_never_guessed_into_a_pip_install(
        self, client, house, offline_managed_env, opened, monkeypatch
    ):
        """同名禁区（`MySQLdb` 的发行包叫 mysqlclient）：差异计划说清不能在会话里授权，并给可执行的出路。"""
        installs: list[str] = []
        real = deprepair._run_generation
        monkeypatch.setattr(
            deprepair, "_run_generation", lambda job, ev: (installs.append("x"), real(job, ev))[1]
        )
        opened(
            [],
            script_body="def _load():\n    import MySQLdb\n\n_load()\n",
        )
        report = _create(client, {"script": "figure.py"})
        sid = report["session_id"]
        assert _act(client, report, "run").status_code == 202
        failed = _wait(client, sid, lambda r: r["phase"] not in ("running", "ready_to_run"))
        assert failed["phase"] == "action_required" and failed["outcome"]["kind"] == "failed"
        (req,) = [r for r in failed["requirements"] if r.get("origin") == "runtime_missing"]
        assert req["payload"]["module"] == "MySQLdb"
        assert req["payload"]["installable"] is False
        assert req["payload"]["code"] == "dependency_unresolved"
        assert req["payload"]["options"] == ["specify_package", "choose_environment"]
        assert "prepare_dependencies" not in _kinds(failed)
        assert installs == []

    def test_a_package_found_missing_while_running_is_a_new_attempt_not_a_resume(
        self, client, house, offline_managed_env, opened
    ):
        """动态缺包：脚本跑到函数里才 import 的包，静态扫描看不见。原执行已经发生（事实保留），差异计划在同一个
        会话里呈现；补齐之后要**新的一次尝试**（不叫"从异常点继续"），新增集合另行授权。"""
        proj = opened(
            [],
            script_body=(
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
        report = _create(client, {"script": "figure.py"})
        assert report["phase"] == "ready_to_run"  # 静态看不出缺包
        sid = report["session_id"]
        assert _act(client, report, "run").status_code == 202
        missing = _wait(client, sid, lambda r: r["phase"] not in ("running", "ready_to_run"))
        first_attempt = missing["provider"]["attempt_id"]
        # 这次执行已经发生：事实保留；等授权补齐，而不是笼统的失败
        assert missing["phase"] == "awaiting_confirmation"
        assert missing["outcome"] == {
            "kind": "needs_dependencies",
            "code": "missing_dependency",
            "reason": "rerun_required",
        }
        assert missing["facts"] == {"execution_finished": True, "figure_captured": False}
        reqs = [r for r in missing["requirements"] if r.get("origin") == "runtime_missing"]
        assert len(reqs) == 1
        req = reqs[0]
        assert req["payload"]["module"] == ALPHA[1] and req["payload"]["rerun_required"] is True
        assert [
            depresolve.normalize_distribution(n) for n in req["payload"]["impact"]["installs"]
        ] == [depresolve.normalize_distribution(ALPHA[0])]
        assert "run" not in _kinds(missing)  # 同一份配置再跑只会撞同一个缺包
        assert _act(client, missing, "prepare_dependencies").status_code == 202
        ready = _wait(client, sid, lambda r: r["phase"] == "ready_to_run")
        assert ready["dependency_delta"]["origin"] == "runtime_missing"
        assert ready["dependency_delta"]["rerun_required"] is True
        assert ready["provider"]["attempts"] == 1  # 第一次尝试的记录还在，没被改写
        assert _act(client, ready, "run").status_code == 202
        done = _wait(
            client, sid, lambda r: r["phase"] in ("completed", "partial", "action_required")
        )
        assert done["phase"] == "completed", done
        assert done["provider"]["attempts"] == 2
        assert done["provider"]["attempt_id"] != first_attempt  # 新的 attempt，不是从异常点继续
        assert done["dependency_delta"] is None  # 新的一次尝试消费了上次的差额
