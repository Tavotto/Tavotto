"""环境选择进入准备会话（T05，ADR 0114；验收 E03 / E04 / C03 / C04 / C19）——**假 pool**。

会话把「项目里有自己的环境线索、用户还没决定」表达成一个 `needs_action` 的 `environment` 检查项：phase 是
`awaiting_confirmation`，`requirements` 里是只读的环境建议，没有 `run` 动作。回答走既有的采用端点（或选回内置），
答完 `recheck`。判据的主语是**哪一刻的哪一份决定**：决定变了（采用 / 选回内置 / 环境被重建）→ 配置修订加一，
旧动作作废，旧修订上的 `run` 一行用户代码都不跑。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool）从 test_preparation_api 导入复用，参数名与导入名相同
from __future__ import annotations

import pytest

from support import envworld
from tavotto.engine import deprepair, pool as engine_pool, prepsession, projectenv
from test_preparation_api import (  # noqa: F401 — 夹具与替身复用，不重写第二套
    _open,
    _project,
    _wait,
    client,
    fake_pool,
)
from test_preparation_session import (  # noqa: F401
    SESSIONS,
    _act,
    _action,
    _create,
    _get,
    sessions,
)


@pytest.fixture(autouse=True)
def _confirm_mode(monkeypatch):
    """本文件钉的是 T05 的**确认模式**（`TAVOTTO_ENV_ADOPTION=confirm`，ADR 0114 §一～§五）。默认的检测模式（§六）
    不出现 `environment_choice`，见 `test_environment_autodetect.py`。"""
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "confirm")


def _project_with_venv(tmp_path):
    root = _project(tmp_path, "p")
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    return root, py


def test_an_undecided_project_environment_is_a_requirement_not_a_machine_decision(
    client, tmp_path, fake_pool, sessions
):
    root, _py = _project_with_venv(tmp_path)
    _open(client, root)

    report = _create(client, {"script": "fig.py"}).get_json()

    assert report["phase"] == "awaiting_confirmation"
    assert report["outcome"] == {"kind": "pending", "code": "environment_choice_required"}
    env_check = next(c for c in report["checks"] if c["id"] == "environment")
    assert (
        env_check["status"] == "needs_action" and env_check["code"] == "environment_choice_required"
    )
    requirement = next(r for r in report["requirements"] if r["id"] == "environment")
    assert requirement["kind"] == "environment_choice"
    payload = requirement["payload"]
    assert payload["decision"]["needs_decision"] is True
    assert payload["recommended_id"] and payload["candidates"][-1]["id"] == "builtin"
    assert [a["kind"] for a in report["actions"]] == ["recheck"]  # 没有 run
    assert fake_pool["build_calls"] == 0
    assert projectenv.remembered_record(root) is None  # 检查不替用户决定


def test_choosing_the_builtin_environment_settles_the_question_and_moves_the_revision(
    client, tmp_path, fake_pool, sessions
):
    root, _py = _project_with_venv(tmp_path)
    _open(client, root)
    first = _create(client, {"script": "fig.py"}).get_json()

    projectenv.remember_default(root)  # 用户选回内置（PATCH 空路径走的就是它）
    redo = _act(
        client, first["session_id"], _action(first, "recheck")["id"], first["config_revision"]
    )
    assert redo.status_code == 200, redo.get_json()
    after = _get(client, first["session_id"]).get_json()

    assert after["config_revision"] == first["config_revision"] + 1
    assert after["phase"] == "ready_to_run" and {c["status"] for c in after["checks"]} == {"ok"}
    assert after["requirements"] == []
    assert _action(after, "run")["config_revision"] == after["config_revision"]


def test_adopting_an_environment_answers_the_same_question_and_old_actions_stop_being_valid(
    client, tmp_path, fake_pool, sessions
):
    """C04：用户在建议上采用了环境之后，建议那一刻的修订上的动作作废——旧修订的 `run` / `recheck` 一行都不跑。"""
    root, py = _project_with_venv(tmp_path)
    _open(client, root)
    first = _create(client, {"script": "fig.py"}).get_json()
    old_recheck = _action(first, "recheck")

    assert projectenv.remember(root, str(py), automatic=False, trigger="recommended")
    redo = _act(client, first["session_id"], old_recheck["id"], first["config_revision"])
    assert redo.status_code == 200
    after = _get(client, first["session_id"]).get_json()
    assert after["phase"] == "ready_to_run" and after["config_revision"] == 2

    # 旧修订上的动作：拒绝，且没有任何执行
    stale = _act(client, first["session_id"], old_recheck["id"], first["config_revision"])
    assert stale.status_code in (404, 409)
    assert fake_pool["build_calls"] == 0


def test_an_environment_rebuilt_in_place_after_the_check_is_a_new_revision(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """C19：路径没变、环境代变了（删了重建并重新采用）——配置修订随之加一，不会让旧检查冒充新环境。"""
    root, py = _project_with_venv(tmp_path)
    assert projectenv.remember(root, str(py), automatic=False, trigger="recommended")
    # 解析到的就是这条真实路径（替身 pool 默认回一个不存在的假路径，那样没有「环境代」可言）
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda root=None, **kw: (str(py), "project_venv")
    )
    _open(client, root)
    first = _create(client, {"script": "fig.py"}).get_json()
    assert first["phase"] == "ready_to_run"
    run = _action(first, "run")

    import shutil

    shutil.rmtree(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n# rebuilt\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /y\n", "utf-8")
    assert projectenv.remember(root, str(py), automatic=False, trigger="recommended")

    second = _create(client, {"script": "fig.py"}).get_json()

    assert second["session_id"] == first["session_id"]
    assert second["config_revision"] == first["config_revision"] + 1
    gone = _act(client, first["session_id"], run["id"], second["config_revision"])
    assert gone.status_code == 404 and gone.get_json()["code"] == "preparation_action_unknown"
    assert fake_pool["build_calls"] == 0


def test_the_session_report_never_carries_a_machine_path_for_a_candidate(
    client, tmp_path, fake_pool, sessions
):
    root, py = _project_with_venv(tmp_path)
    _open(client, root)

    report = _create(client, {"script": "fig.py"}).get_json()

    text = repr(report)
    assert str(tmp_path) not in text and str(py) not in text
    assert "_python" not in text
    assert prepsession.CHECK_NEEDS_ACTION == "needs_action"
    assert deprepair  # 依赖层仍是假的（sessions 夹具）：这里只看环境投影


def test_a_plan_whose_environment_was_rebuilt_between_confirm_and_spawn_never_runs(
    client, tmp_path, fake_pool, monkeypatch
):
    """确认之后、spawn 之前环境身份被换掉（同一路径删了重建）：旧计划安全作废，没有任何 worker 被起、
    更没有在另一个环境里把脚本跑一遍（`build_calls == 0`）。"""
    import shutil
    import threading

    from tavotto.engine import preparation

    root, py = _project_with_venv(tmp_path)
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda root=None, **kw: (str(py), "project_venv")
    )
    _open(client, root)
    hold = threading.Event()
    monkeypatch.setattr(engine_pool, "peek", lambda script, root: hold.wait(30) and None)
    plan = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()["plan"]
    assert plan["environment"]["generation"]

    shutil.rmtree(root / ".venv")  # 计划之后、执行线程起 worker 之前：路径没变，环境换了一代
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n# rebuilt\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /y\n", "utf-8")
    hold.set()
    result = _wait(client, plan["plan_id"])["result"]

    assert result["status"] == preparation.STATUS_ERROR
    assert result["error"]["code"] == preparation.ERROR_PLAN_STALE
    assert result["error"]["reason"] == preparation.STALE_ENVIRONMENT
    assert result["receipt"] is None and fake_pool["build_calls"] == 0
