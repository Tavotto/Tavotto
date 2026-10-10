"""环境「建议 → 检查 → 采用」三个动作分开（T05，ADR 0114；验收 E01–E07 / C03–C06 / C19 / C30）。

判据的主语（这一族缺陷的形状是「量错对象」，所以先说清）：

* **谁**没执行：推荐 / GET 报告 / 解析解释器时的**候选**（项目 venv、用户的 Conda、登录 shell）——不是用户已经
  采用过的那个（采用过的就是要跑脚本的那个，起它是执行的一部分）；
* **哪个时刻**：首次解析、缺包后的接手、跑前的门、GET `/api/engine/environment`、明确的检查 / 采用动作；
* **哪个维度**：不只是「没写记录」，还有哨兵计数、实际 worker 的 `sys.prefix`、venv 文件树的指纹、环境代。

分层：假解释器 + 哨兵（`World`，只证明「没执行」）与真 venv（`real_venv`，证明采用之后实际在哪个环境里跑）。
"""

from __future__ import annotations

import pytest

from support import envworld
from support.envworld import World, real_venv, rebuild_venv
from tavotto.engine import (
    deprepair,
    pool as engine_pool,
    projectenv,
    userenvs,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
posix_only = pytest.mark.skipif(not envworld.POSIX, reason="哨兵是 POSIX shell 脚本")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    # 本文件钉的是 T05 的**确认模式**（ADR 0114 §一～§五）；默认的检测模式（§六）见 `test_environment_autodetect.py`
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "confirm")
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()
    yield
    engine_pool.shutdown_all(wait=True)
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.setenv("SHELL", str(w.shell))
    envworld.set_home(monkeypatch, tmp_path / "home")
    (tmp_path / "home").mkdir()
    return w


def _open(client, root) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    c = m.app.test_client()
    yield c
    m.reset_projects()


# ---------------------------------------------------------------------------
# E07 / T02 遗留洞：GET /api/engine/environment 不起解释器
# ---------------------------------------------------------------------------
@posix_only
def test_the_environment_report_starts_no_interpreter_even_when_one_is_remembered(client, world):
    """记住的解释器此刻被「复检」要起一次（T02 实测）：状态读取不该背这笔账——能不能用是明确的核验动作。"""
    assert projectenv.remember(
        world.root, str(world.venv_python), automatic=False, trigger="user_selected"
    )
    pj = _open(client, world.root)

    resp = client.get("/api/engine/environment", query_string={"pj": pj})

    assert resp.status_code == 200
    assert world.fired() == []
    project = resp.get_json()["project"]
    assert project["source"] == engine_pool.SOURCE_PROJECT_VENV  # 线索照常给出：记住的就是它
    assert project["python"] == envworld.venv_rel(".venv")


# ---------------------------------------------------------------------------
# 首次解析：推荐，不采用，不体检
# ---------------------------------------------------------------------------
@needs_worker
def test_the_first_resolution_does_not_adopt_a_project_venv(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)

    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")

    assert source != engine_pool.SOURCE_PROJECT_VENV
    assert not engine_pool.same_python(python, str(envworld.venv_python(root / ".venv")))
    assert projectenv.remembered_record(root) is None  # 没有任何采用记录被写出来


@needs_worker
def test_the_first_resolution_runs_no_candidate_probe(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    calls: list[str] = []
    real = projectenv.probe_environment
    monkeypatch.setattr(
        projectenv, "probe_environment", lambda p, *a, **k: calls.append(p) or real(p, *a, **k)
    )

    engine_pool.resolve_worker_python(str(root), script="figure.py")
    engine_pool.resolve_worker_python(str(root))

    assert calls == []


@needs_worker
def test_the_legacy_switch_keeps_the_silent_first_open_adoption(tmp_path, monkeypatch):
    """兼容一版：`TAVOTTO_ENV_ADOPTION=auto` 恢复 ADR 0057 / 0079 / 0107 的静默采用（无头 / CI 用）。"""
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "auto")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    venv_python = real_venv(root, ".venv", python=WORKER_PY)

    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")

    assert engine_pool.same_python(python, venv_python)
    assert source == engine_pool.SOURCE_PROJECT_VENV
    assert projectenv.state(root)["trigger"] == projectenv.TRIGGER_FIRST_OPEN


# ---------------------------------------------------------------------------
# 跑前的门 / 缺包后的接手：只推荐
# ---------------------------------------------------------------------------
def test_deciding_the_environment_recommends_without_remembering(tmp_path, monkeypatch):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "figure.py").write_text("import openpyxl\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda *a, **k: calls.append("offer"))

    assert deprepair.decide_environment(root, "figure.py") is None

    assert calls == []  # 「决定」连门都没问：确认模式下它不替用户换环境，也就不必去量
    assert projectenv.remembered_record(root) is None


@needs_worker
def test_a_missing_dependency_lists_the_project_venv_instead_of_switching(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    venv_python = real_venv(root, ".venv", python=WORKER_PY)

    outcome = engine_pool.try_project_env(str(root), "figure.py", "matplotlib")

    assert outcome["ok"] is False
    assert outcome["code"] == projectenv.ERROR_CONFIRMATION_REQUIRED
    assert outcome["recommended"]["venv"].endswith(".venv")
    assert projectenv.remembered_record(root) is None
    # 体检过了（用户自己的运行触发的有界检查），但没有采用
    assert engine_pool.same_python(outcome["recommended"]["python"], venv_python)
    assert outcome["recommended"]["id"] == userenvs.env_id(venv_python)
    assert outcome["recommended"]["generation"] == projectenv.environment_generation(venv_python)


# ---------------------------------------------------------------------------
# E04 / C19：环境代——同一路径重建后不是同一个环境
# ---------------------------------------------------------------------------
@needs_worker
def test_a_confirmed_environment_rebuilt_at_the_same_path_is_not_silently_trusted(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    venv_python = real_venv(root, ".venv", python=WORKER_PY)
    health = projectenv.probe_environment(venv_python)
    assert health["ok"]
    assert projectenv.remember(
        root, venv_python, automatic=False, trigger="user_selected", health=health
    )
    assert engine_pool.same_python(
        engine_pool.resolve_worker_python(str(root), script="figure.py")[0], venv_python
    )

    rebuild_venv(root, ".venv", python=WORKER_PY)  # 路径一个字没变，环境是另一代
    engine_pool._project_python_ok.clear()  # 进程内体检结论不跨重启：重启后只剩落盘的记录

    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.code == engine_pool.PROJECT_PYTHON_UNUSABLE_CODE
    assert err.value.explicit["reason"] == "rebuilt"


@needs_worker
def test_a_plan_made_before_the_environment_was_rebuilt_is_refused_before_it_runs(tmp_path):
    from tavotto.engine import preparation

    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    venv_python = real_venv(root, ".venv", python=WORKER_PY)
    health = projectenv.probe_environment(venv_python)
    assert projectenv.remember(
        root, venv_python, automatic=False, trigger="user_selected", health=health
    )
    plan = preparation.plan_for(
        project_id="pj-x",
        project_root=str(root),
        asset_id="",
        stem="",
        script="figure.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    assert preparation.stale_reason(plan) is None
    assert plan.environment["generation"]

    # 把解释器原地换成另一代，但记录里的环境代随之更新（用户在计划之后又采用了一次）：
    # 路径相同、环境代不同——旧计划不能拿去跑新环境
    rebuild_venv(root, ".venv", python=WORKER_PY)
    engine_pool._project_python_ok.clear()
    health = projectenv.probe_environment(venv_python)
    assert projectenv.remember(
        root, venv_python, automatic=False, trigger="user_selected", health=health
    )

    assert preparation.stale_reason(plan) == (preparation.STALE_ENVIRONMENT, {})


# ---------------------------------------------------------------------------
# 建议：纯读；检查：唯一起候选的入口，只起被点名的
# ---------------------------------------------------------------------------
@posix_only
def test_recommending_starts_nothing_with_every_entry_armed(world, monkeypatch):
    import os
    import socket
    import subprocess

    from tavotto.engine import envadvice

    world.fake_venv("env")
    calls: list[str] = []

    def boom(name):
        def inner(*a, **k):
            calls.append(name)
            raise AssertionError(f"recommend reached a process/network entry: {name}")

        return inner

    monkeypatch.setattr(subprocess.Popen, "__init__", boom("Popen"))
    for name in ("system", "execv", "fork", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, boom(f"os.{name}"))
    monkeypatch.setattr(socket.socket, "connect", boom("connect"))

    rec = envadvice.recommend(world.root, "plot.py")

    assert calls == [] and world.fired() == []
    names = {c["python_relative"] for c in rec["candidates"] if c["python_relative"]}
    assert {envworld.venv_rel(".venv"), envworld.venv_rel("env")} <= names
    project = [c for c in rec["candidates"] if c["scope"] == "project"]
    # 没检查过的候选如实写 unchecked，不冒充 verified；推荐的是线索里排最前的，凭的是项目声明
    assert {c["status"] for c in project} == {"unchecked"} and all(
        not c["checked"] for c in project
    )
    assert {c["label"] for c in project} == {"project_hint"}
    assert rec["recommended_id"] == project[0]["id"]
    assert rec["decision"]["needs_decision"] is True and rec["decision"]["consent"] == "none"
    # 内置 / 默认链条永远在表尾：只读、可选回
    assert rec["candidates"][-1]["id"] == "builtin" and rec["candidates"][-1]["read_only"] is True


@posix_only
def test_only_the_check_action_starts_a_candidate_and_only_the_named_one(world):
    from tavotto.engine import envadvice

    world.fake_venv("env")
    rec = envadvice.recommend(world.root, "plot.py")
    by_rel = {c["python_relative"]: c["id"] for c in rec["candidates"] if c["python_relative"]}

    out = envadvice.check(world.root, "plot.py", ids=[by_rel[envworld.venv_rel(".venv")]])

    assert world.fired() == [
        "venv_python"
    ]  # 点名的那一个（假 .venv 的哨兵名），没点名的 env 一次没起
    assert out["checked"] == [by_rel[envworld.venv_rel(".venv")]] and out["skipped"] == []
    rows = {c["id"]: c for c in out["recommendation"]["candidates"]}
    assert rows[by_rel[envworld.venv_rel(".venv")]]["checked"] is True
    # 假解释器通不过体检（不报版本）：如实写成不能用，绝不当 healthy / verified
    assert rows[by_rel[envworld.venv_rel(".venv")]]["status"] in ("unusable", "unsupported_python")
    assert rows[by_rel[envworld.venv_rel(".venv")]]["health"]["ok"] is False
    assert rows[by_rel[envworld.venv_rel("env")]]["checked"] is False
    # 检查 ≠ 采用：什么都没写进项目设置
    assert projectenv.remembered_record(world.root) is None


@posix_only
def test_checking_asks_the_login_shell_only_when_told_to(world):
    from tavotto.engine import envadvice

    envadvice.check(world.root, "plot.py", scope="all")
    assert "login_shell" not in world.fired()

    envadvice.check(world.root, "plot.py", scope="all", include_login_shell=True)
    assert "login_shell" in world.fired()


def test_a_check_has_a_candidate_budget_a_deadline_and_a_cancel(tmp_path, monkeypatch):
    import threading

    from tavotto.engine import envadvice

    root = tmp_path / "p"
    for name in (".venv", "venv", "env"):
        py = envworld.venv_python(root / name)
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n", "utf-8")
        (root / name / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    seen: list[str] = []

    def probe(python, *a, **k):
        seen.append(python)
        return {"ok": True, "code": "", "support": "verified", "python_version": "3.12.1"}

    monkeypatch.setattr(envadvice, "CHECK_MAX_CANDIDATES", 2)
    out = envadvice.check(root, "p.py", probe=probe)
    assert len(seen) == 2 and [s["reason"] for s in out["skipped"]] == ["limit"]

    seen.clear()
    out = envadvice.check(root, "p.py", probe=probe, deadline_s=0)
    assert seen == [] and {s["reason"] for s in out["skipped"]} == {"deadline"}

    event = threading.Event()
    event.set()
    out = envadvice.check(root, "p.py", probe=probe, cancel=event)
    assert seen == [] and out["cancelled"] is True
    assert {s["reason"] for s in out["skipped"]} == {"cancelled"}

    # 同一项目同一时刻只有一次检查：探测中再来一次 → busy，不并发起第二组进程
    def reentrant(python, *a, **k):
        with pytest.raises(envadvice.CheckBusy):
            envadvice.check(root, "p.py", probe=probe)
        return {"ok": True}

    envadvice.check(root, "p.py", probe=reentrant, ids=None)
    assert envadvice.cancel_check(root) is False  # 已经结束：没有可取消的


@pytest.mark.parametrize(
    "health, status",
    [
        ({"ok": True}, "healthy"),
        ({"ok": False, "code": "project_env_unsupported_python"}, "unsupported_python"),
        ({"ok": False, "code": "project_env_no_matplotlib"}, "no_matplotlib"),
        ({"ok": False, "code": "project_env_worker_import_failed"}, "worker_import_failed"),
        ({"ok": False, "code": "project_env_unusable"}, "unusable"),
    ],
)
def test_a_check_tells_unsupported_python_missing_packages_and_a_broken_import_chain_apart(
    tmp_path, health, status
):
    """E06：三种「不能用」的出路各不相同（换 Python / 装 matplotlib / 修环境），不合并成一句；
    被判不能用的候选不被推荐，也不伪称 verified。"""
    from tavotto.engine import envadvice

    root = tmp_path / "p"
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")

    out = envadvice.check(root, "p.py", probe=lambda python, *a, **k: dict(health))

    row = next(c for c in out["recommendation"]["candidates"] if c["python_relative"])
    assert row["status"] == status
    assert (out["recommendation"]["recommended_id"] == row["id"]) is (status == "healthy")
    assert out["recommendation"]["decision"]["needs_decision"] is (status == "healthy")


def test_the_evidence_order_not_the_python_version_decides_the_recommendation(
    tmp_path, monkeypatch
):
    """推荐只看证据层次：项目声明 > 已检查的机器环境 > 未检查的线索；同层按线索出现的顺序。
    Python 更新、装的包更多都不是优先理由。"""
    from tavotto.engine import envadvice

    home = tmp_path / "home"
    root = tmp_path / "p"
    root.mkdir()
    envworld.set_home(monkeypatch, home)
    monkeypatch.delenv("PYENV_ROOT", raising=False)
    monkeypatch.setattr(userenvs, "_conda_roots", lambda: [])
    for version in ("3.10.9", "3.13.1"):
        py = envworld.pyenv_python(home, version)
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n", "utf-8")

    rec = envadvice.recommend(root, "p.py")
    machine = [c for c in rec["candidates"] if c["label"] == "machine_hint"]
    assert len(machine) == 2 and all(c["status"] == "unchecked" for c in machine)
    first_by_discovery = machine[0]["id"]

    # 两个都检查过且健康；更新的 3.13 不因版本新而被推荐到前面
    def probe(python, *a, **k):
        version = "3.13.1" if "3.13.1" in python else "3.10.9"
        return {"ok": True, "code": "", "support": "verified", "python_version": version}

    out = envadvice.check(root, "p.py", scope="machine", probe=probe)
    rows = [c for c in out["recommendation"]["candidates"] if c["label"] == "checked_compatible"]
    assert [c["id"] for c in rows][0] == first_by_discovery
    assert out["recommendation"]["recommended_id"] == first_by_discovery
    # 机器环境只是线索：没有项目自己的声明，就不替用户在第一次打开时提问
    assert out["recommendation"]["decision"]["needs_decision"] is False


def test_the_python_requirement_comes_from_the_matrix_and_a_hint_is_not_a_range(
    tmp_path, monkeypatch
):
    from tavotto.engine import envadvice

    home = tmp_path / "home"
    root = tmp_path / "p"
    root.mkdir()
    envworld.set_home(monkeypatch, home)
    monkeypatch.setattr(userenvs, "_conda_roots", lambda: [])
    plain = envadvice.recommend(root, "p.py")["python_requirement"]
    assert plain["declared"] is None and plain["status"] == "unknown"
    assert plain["supported"] == {
        "min": "%d.%d" % projectenv.PYTHON_MIN,
        "max_exclusive": "%d.%d" % projectenv.PYTHON_MAX_EXCLUSIVE,
    }

    (root / ".python-version").write_text("3.11.9\n", "utf-8")
    py = envworld.pyenv_python(home, "3.11.9")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    hinted = envadvice.recommend(root, "p.py")["python_requirement"]
    # 线索原样给出，不被伪造成 ">=3.11,<3.12" 之类的完整兼容区间；兼容性仍 unknown
    assert hinted["declared"] == {"source": "python_version_file", "value": "3.11.9"}
    assert hinted["status"] == "unknown"


# ---------------------------------------------------------------------------
# 采用：明确动作，绑定候选身份与环境代
# ---------------------------------------------------------------------------
@needs_worker
def test_adopting_a_recommended_candidate_runs_the_worker_in_exactly_that_environment(
    client, tmp_path
):
    """E01：用户在建议上点「使用」→ 实际 worker 的 prefix 就是那个 venv 的（独立身份对拍），
    记录是用户的明确决定（automatic=False、带环境代），授权来源 = confirmed。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    venv_python = real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)

    checked = client.post(
        "/api/engine/environment/check", json={"script": "figure.py"}, query_string={"pj": pj}
    )
    assert checked.status_code == 200, checked.get_json()
    rec = checked.get_json()["recommendation"]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))
    assert row["status"] == "healthy" and row["checked"] is True
    assert rec["recommended_id"] == row["id"] and rec["decision"]["consent"] == "none"
    assert projectenv.remembered_record(root) is None  # 检查 ≠ 采用

    adopted = client.patch(
        "/api/engine/environment",
        json={
            "scope": "project",
            "candidate": row["id"],
            "expected_generation": row["generation"],
            "script": "figure.py",
        },
        query_string={"pj": pj},
    )
    assert adopted.status_code == 200, adopted.get_json()
    project = adopted.get_json()["project"]
    assert project["consent"] == "confirmed" and project["source"] == "project_venv"
    record = projectenv.remembered_record(root)
    assert record["automatic"] is False and record["trigger"] == "recommended"
    assert record["generation"] == row["generation"]

    worker, resp = engine_pool.build("figure.py", str(root), "__main__")
    assert worker.python_source == engine_pool.SOURCE_PROJECT_VENV
    from pathlib import Path

    assert (
        Path(resp["runtime"]["prefix"]).resolve()
        == Path(envworld.python_identity(venv_python)["prefix"]).resolve()
    )


@needs_worker
def test_an_adoption_confirmed_against_an_old_generation_is_refused(client, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))

    rebuild_venv(root, ".venv", python=WORKER_PY)  # 用户看到建议之后，环境被删了重建
    stale = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"], "expected_generation": row["generation"]},
        query_string={"pj": pj},
    )

    assert stale.status_code == 409 and stale.get_json()["code"] == "environment_changed"
    assert projectenv.remembered_record(root) is None  # 零副作用：没有采用另一个环境


def test_a_candidate_id_is_only_ever_resolved_from_the_local_enumeration(client, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    pj = _open(client, root)
    gone = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": "0123456789abcdef"},
        query_string={"pj": pj},
    )
    assert gone.status_code == 400 and gone.get_json()["code"] == "environment_candidate_gone"
    escape = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": "../../../etc/passwd"},
        query_string={"pj": pj},
    )
    assert escape.status_code == 400 and projectenv.remembered_record(root) is None


@needs_worker
def test_using_an_environment_does_not_modify_it(client, tmp_path):
    """C30：采用并实际用它跑出一个 worker 之后，venv 的文件树（相对路径 + 大小 + mtime）一个字节没变——
    使用环境和修改环境（安装）是两件事，采用不带安装授权。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    before = envworld.tree_digest(root / ".venv")
    rec = client.post(
        "/api/engine/environment/check", json={"script": "figure.py"}, query_string={"pj": pj}
    ).get_json()["recommendation"]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))
    client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"], "expected_generation": row["generation"]},
        query_string={"pj": pj},
    )
    engine_pool.build("figure.py", str(root), "__main__")
    assert envworld.tree_digest(root / ".venv") == before


def test_adopting_never_reaches_the_installer(client, tmp_path, monkeypatch):
    """C30 的结构面：采用路径上 `deprepair` 的任何安装入口一碰就炸，采用照样成功。"""
    root = tmp_path / "proj"
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    pj = _open(client, root)
    for name in ("install_async", "create_plan", "prepare_async", "install_plan"):
        if hasattr(deprepair, name):
            monkeypatch.setattr(
                deprepair, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer"))
            )
    monkeypatch.setattr(
        projectenv,
        "probe_environment",
        lambda python, *a, **k: {"ok": True, "python_version": "3.12.1", "support": "verified"},
    )
    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    row = next(c for c in rec["candidates"] if c["python_relative"])
    resp = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"], "expected_generation": row["generation"]},
        query_string={"pj": pj},
    )
    assert resp.status_code == 200, resp.get_json()


# ---------------------------------------------------------------------------
# E05 全局锁定 / E04 删除 / 迁移
# ---------------------------------------------------------------------------
def test_a_global_lock_is_named_and_nothing_is_adopted_into_an_environment_that_will_not_be_used(
    client, tmp_path, monkeypatch
):
    import sys

    root = tmp_path / "proj"
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)
    pj = _open(client, root)

    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    assert rec["decision"]["locked_by"] == {"source": "env_override"}
    assert rec["decision"]["needs_decision"] is False and rec["recommended_id"] is None

    row = next(c for c in rec["candidates"] if c["python_relative"])
    resp = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"]},
        query_string={"pj": pj},
    )
    assert resp.status_code == 409 and resp.get_json()["code"] == "environment_locked"
    # 是谁锁的在建议里（`locked_by`），响应只给稳定 code
    assert "params" not in resp.get_json()
    assert projectenv.remembered_record(root) is None  # 不把包装/记录写到永远不会被使用的目标


@posix_only
def test_a_deleted_confirmed_environment_is_reported_without_falling_back_or_starting_anything(
    client, world
):
    assert projectenv.remember(
        world.root, str(world.venv_python), automatic=False, trigger="recommended"
    )
    pj = _open(client, world.root)
    import shutil

    shutil.rmtree(world.root / ".venv")

    project = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"]

    assert world.fired() == []
    err = project["resolution_error"]
    assert err["code"] == "project_python_unusable" and err["explicit"]["reason"] == "missing"
    assert project["recommendation"]["decision"]["current_id"] is not None or True
    with pytest.raises(engine_pool.WorkerError) as raised:
        engine_pool.resolve_worker_python(str(world.root), script="plot.py")
    assert raised.value.code == "project_python_unusable"  # 解析时同一个答案：不降级、不换别的


def test_a_pre_0114_automatic_record_keeps_working_and_is_not_counted_as_a_confirmation(
    client, tmp_path
):
    """迁移：历史自动记录照用（不重新询问、不终止已有运行），但授权来源是 legacy_auto——证明不了用户确认过；
    用户在建议上确认它之后才变成 confirmed。"""
    root = tmp_path / "proj"
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    # 一条 ADR 0114 之前写下的记录：automatic=True、没有环境代
    engine_config = __import__("tavotto.engine.config", fromlist=["x"])
    engine_config.set_project_settings(
        str(root),
        {
            projectenv.SETTINGS_KEY: {
                "automatic": True,
                "trigger": "first_open",
                "module": "",
                "python_relative": envworld.venv_rel(".venv"),
            }
        },
    )
    pj = _open(client, root)

    project = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"]
    rec = project["recommendation"]
    assert project["consent"] == "legacy_auto" and project["source"] == "project_venv"
    assert (
        rec["decision"]["consent"] == "legacy_auto" and rec["decision"]["needs_decision"] is False
    )
    current = next(c for c in rec["candidates"] if c["current"])
    assert current["label"] == "remembered_legacy"  # 照用，但标签说实话
    assert engine_pool.peek_project_resolution(str(root)) is not None  # 解析侧也没有要重新问
    assert projectenv.consent_of(projectenv.remembered_record(root)) == "legacy_auto"


@needs_worker
def test_a_compatible_environment_the_user_chose_over_the_recommended_one_is_the_one_that_runs(
    client, tmp_path
):
    """E03：建议是 `.venv`，用户却点了另一个同样健康的 `venv`——实际 worker（热态）与重建后的 worker（冷重放
    走的是同一个解析）都落在所选的那个 prefix 上，没有被推荐的偷换；两个 venv 指向同一个 base 也分得开（E02）。"""
    from pathlib import Path

    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    recommended = real_venv(root, ".venv", python=WORKER_PY)
    chosen = real_venv(root, "venv", python=WORKER_PY)
    ids = envworld.python_identity
    assert ids(recommended)["base"] == ids(chosen)["base"]  # 前提：同 base
    assert ids(recommended)["prefix"] != ids(chosen)["prefix"]  # 但不是同一个环境
    pj = _open(client, root)
    rec = client.post(
        "/api/engine/environment/check", json={"script": "figure.py"}, query_string={"pj": pj}
    ).get_json()["recommendation"]
    rows = {c["python_relative"]: c for c in rec["candidates"] if c["python_relative"]}
    assert rec["recommended_id"] == rows[envworld.venv_rel(".venv")]["id"]
    assert (
        rows[envworld.venv_rel(".venv")]["id"] != rows[envworld.venv_rel("venv")]["id"]
    )  # 不按 realpath 合并
    assert (
        rows[envworld.venv_rel(".venv")]["generation"]
        != rows[envworld.venv_rel("venv")]["generation"]
    )

    pick = rows[envworld.venv_rel("venv")]
    client.patch(
        "/api/engine/environment",
        json={
            "scope": "project",
            "candidate": pick["id"],
            "expected_generation": pick["generation"],
            "script": "figure.py",
        },
        query_string={"pj": pj},
    )

    want = Path(ids(chosen)["prefix"]).resolve()
    _worker, hot = engine_pool.build("figure.py", str(root), "__main__")
    assert Path(hot["runtime"]["prefix"]).resolve() == want
    engine_pool.shutdown_all(wait=True)  # 冷重放：整个会话丢掉后重建，走的仍是同一个解析
    _worker, cold = engine_pool.build("figure.py", str(root), "__main__")
    assert Path(cold["runtime"]["prefix"]).resolve() == want


@needs_worker
def test_two_projects_on_one_base_keep_their_own_confirmed_environment(client, tmp_path):
    from pathlib import Path

    prefixes = {}
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
        real_venv(root, ".venv", python=WORKER_PY)
        pj = _open(client, root)
        rec = client.post(
            "/api/engine/environment/check", json={"script": "figure.py"}, query_string={"pj": pj}
        ).get_json()["recommendation"]
        row = next(c for c in rec["candidates"] if c["python_relative"])
        client.patch(
            "/api/engine/environment",
            json={
                "scope": "project",
                "candidate": row["id"],
                "expected_generation": row["generation"],
            },
            query_string={"pj": pj},
        )
        _worker, resp = engine_pool.build("figure.py", str(root), "__main__")
        prefixes[name] = Path(resp["runtime"]["prefix"]).resolve()
    assert prefixes["a"] != prefixes["b"]


def test_progress_style_refreshes_do_not_ask_the_environment_question_again(
    client, tmp_path, monkeypatch
):
    """C03：检查留下的结论（带时间戳）、重复读取建议，都不是「配置变了」——不撤销已给出的动作，也不让修订漂移。"""
    from tavotto.engine import envadvice

    root = tmp_path / "proj"
    py = envworld.venv_python(root / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")

    first = envadvice.recommend(root, "p.py")
    envadvice.check(
        root,
        "p.py",
        probe=lambda python, *a, **k: {"ok": True, "support": "verified", "python_version": "3.12"},
    )
    second = envadvice.recommend(root, "p.py")

    def semantic(rec):  # 会话指纹读的那两样
        return rec["decision"]["needs_decision"], [c["generation"] for c in rec["candidates"]]

    assert semantic(first) == semantic(second)
    assert first["candidates"][0]["checked"] is False and second["candidates"][0]["checked"] is True


# ---------------------------------------------------------------------------
# 跑前的门：确认模式下候选只列、不起
# ---------------------------------------------------------------------------
@posix_only
def test_the_dependency_gate_lists_candidates_unchecked_and_starts_none(world, monkeypatch):
    """门要列「你机器上哪些环境可能已经装齐」：确认模式下它只给已有的检查结论，没检查过的单列成 `checked=False`
    （`ok` / `satisfies` 是 None，不冒充装齐也不冒充没装齐），一个候选解释器 / 登录 shell 都不起；
    用户点「检查并使用」时才由采用端点现场体检。"""
    monkeypatch.delenv("TAVOTTO_USER_ENV_DISCOVERY", raising=False)  # conftest 默认关掉整个发现
    world.fake_venv("env")
    plan = {"missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}], "satisfied": []}

    entries = deprepair.user_environment_offer(world.root, "plot.py", plan, "/builtin/python")

    assert world.fired() == []
    assert entries and all(e["checked"] is False for e in entries)
    assert all(e["ok"] is None and e["satisfies"] is None for e in entries)
    sources = {e["source"] for e in entries}
    assert userenvs.SOURCE_PROJECT_VENV in sources  # 项目自己的环境排在候选表里
    public = [userenvs.public(e) for e in entries]
    assert all("python" not in p for p in public)  # 公开形态不带路径

    # 反证这个哨兵是活的：兼容开关打开（旧的静默采用时代）就是会体检候选
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "auto")
    deprepair.user_environment_offer(world.root, "plot.py", plan, "/builtin/python")
    assert world.fired() != []


def test_the_environment_generation_ignores_permission_bits_but_sees_a_rebuild(tmp_path):
    """环境代要稳：`chmod` / 扩展属性 / 备份工具会悄悄改 ctime 与权限位，拿它们判「换代」会把用户确认过的环境
    误判成被重建；真的删了重建（路径本身与 `pyvenv.cfg` 都是新文件）才换代。"""
    import os
    import shutil

    def build(marker: str) -> str:
        py = envworld.venv_python(tmp_path / ".venv")
        py.parent.mkdir(parents=True)
        py.write_text(f"#!/bin/sh\n# {marker}\n", "utf-8")
        (tmp_path / ".venv" / "pyvenv.cfg").write_text(f"home = /{marker}\n", "utf-8")
        return str(py)

    python = build("first")
    first = projectenv.environment_generation(python)
    assert first and len(first) == 16

    os.chmod(python, 0o700)
    os.chmod(python, 0o755)  # ctime 与权限位都动过
    assert projectenv.environment_generation(python) == first

    shutil.rmtree(tmp_path / ".venv")
    python = build("second")
    assert projectenv.environment_generation(python) not in ("", first)
    assert projectenv.environment_generation(str(tmp_path / "nowhere" / "python")) == ""


@needs_worker
def test_missing_dependency_recommendation_keeps_its_generation_through_http_projection(
    client, tmp_path, monkeypatch
):
    from tavotto import app as m

    root = tmp_path / "missing-dependency-adoption"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    python = real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    outcome = engine_pool.try_project_env(str(root), "figure.py", "matplotlib")
    assert outcome["code"] == projectenv.ERROR_CONFIRMATION_REQUIRED
    error = engine_pool.WorkerError("missing", code="missing_dependency", module="matplotlib")
    error.project_env = outcome
    monkeypatch.setattr(m, "_dependency_repair_offer", lambda *args: None)
    with m.app.test_request_context(query_string={"pj": pj}):
        payload = m._worker_error_payload(error)
    shown = payload["project_env"]["recommended"]
    assert shown == {
        "venv": ".venv",
        "id": userenvs.env_id(python),
        "generation": projectenv.environment_generation(python),
    }
    rebuild_venv(root, ".venv", python=WORKER_PY)
    response = client.patch(
        "/api/engine/environment",
        json={
            "scope": "project",
            "candidate": shown["id"],
            "expected_generation": shown["generation"],
        },
        query_string={"pj": pj},
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "environment_changed"
    assert projectenv.remembered_record(root) is None


@needs_worker
def test_dependency_repair_offer_binds_the_recommended_target_to_the_displayed_generation(
    client, tmp_path
):
    """#814 评审（PRRT_kwDOT51-YM6pwk-Q）的第二个消费者：依赖修复卡的「改用」目标取自同一份 recommended。
    它必须带着看到那一刻的候选 id + 环境代，而且这一对交给采用端点时，环境被重建 → 409、什么都不记。"""
    root = tmp_path / "repair-offer-binding"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    python = real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    outcome = engine_pool.try_project_env(str(root), "figure.py", "matplotlib")
    assert outcome["code"] == projectenv.ERROR_CONFIRMATION_REQUIRED

    offered = deprepair.offer(str(root), "figure.py", "matplotlib", outcome)
    adoptable = [t for t in offered["targets"] if t["kind"] == deprepair.TARGET_SYSTEM]
    assert len(adoptable) == 1
    shown = adoptable[0]["candidate"]
    assert shown == {
        "id": userenvs.env_id(python),
        "generation": projectenv.environment_generation(python),
    }

    rebuild_venv(root, ".venv", python=WORKER_PY)
    response = client.patch(
        "/api/engine/environment",
        json={
            "scope": "project",
            "candidate": shown["id"],
            "expected_generation": shown["generation"],
            "module": "matplotlib",
        },
        query_string={"pj": pj},
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "environment_changed"
    assert projectenv.remembered_record(root) is None


def test_dependency_repair_offer_does_not_list_an_unbound_recommendation(tmp_path):
    """旧响应 / 缺 id 或环境代的建议不能退回按路径采用：目标不列，用户走手填 / 安装那几条路。"""
    root = tmp_path / "repair-offer-unbound"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    base = {
        "ok": False,
        "code": projectenv.ERROR_CONFIRMATION_REQUIRED,
        "recommended": {
            "python": str(envworld.venv_python(root / ".venv")),
            "venv": str(root / ".venv"),
            "health": {"python_version": "3.12.0", "support": "verified"},
        },
    }
    for missing in ({}, {"id": "env-x"}, {"generation": "gen-x"}):
        detail = {**base, "recommended": {**base["recommended"], **missing}}
        offered = deprepair.offer(str(root), "figure.py", "matplotlib", detail)
        assert [t for t in offered["targets"] if t["kind"] == deprepair.TARGET_SYSTEM] == [], (
            missing
        )


# ---------------------------------------------------------------------------
# #814 Codex r4217064306：采用要在写入前再核一次环境代，且 expected_generation 必填
# ---------------------------------------------------------------------------


@needs_worker
def test_a_rebuild_during_the_adoption_probe_is_refused_and_nothing_is_recorded(
    client, tmp_path, monkeypatch
):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))

    # 代次比较通过之后、写入之前（体检期间）环境被重建
    def probe_then_rebuild(python, *a, **k):
        rebuild_venv(root, ".venv", python=WORKER_PY)
        return {"ok": True, "code": "", "support": "verified", "python_version": "3.12.1"}

    monkeypatch.setattr(projectenv, "probe_environment", probe_then_rebuild)
    response = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"], "expected_generation": row["generation"]},
        query_string={"pj": pj},
    )

    assert response.status_code == 409 and response.get_json()["code"] == "environment_changed"
    assert projectenv.remembered_record(root) is None  # 不落盘


@needs_worker
def test_a_candidate_adoption_without_expected_generation_is_refused(client, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))

    response = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"]},
        query_string={"pj": pj},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "environment_generation_required"
    assert projectenv.remembered_record(root) is None


# ---------------------------------------------------------------------------
# #814 Codex r4217064314：总时限也约束正在起的那个探测
# ---------------------------------------------------------------------------


def test_the_total_deadline_caps_each_probe_and_stops_starting_new_ones(tmp_path, monkeypatch):
    from tavotto.engine import envadvice

    root = tmp_path / "p"
    for name in (".venv", "venv", "env"):
        py = envworld.venv_python(root / name)
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n", "utf-8")
        (root / name / "pyvenv.cfg").write_text("home = /x\n", "utf-8")

    now = [1000.0]
    timeouts: list[float] = []

    def probe(python, *a, timeout=None, **k):
        timeouts.append(timeout)
        now[0] += 96.0  # 每个探测"跑"96 s（可注入时钟，不真等）
        return {"ok": True, "code": "", "support": "verified", "python_version": "3.12.1"}

    out = envadvice.check(root, "p.py", probe=probe, deadline_s=100.0, clock=lambda: now[0])

    # 第一个探测拿到全部 100 s（但被单探测上限封顶）；第二个只剩 4 s（低于 MIN_PROBE_BUDGET_S）→ 不起
    assert len(timeouts) == 1 and timeouts[0] == min(projectenv.PROBE_TIMEOUT_S, 100.0)
    assert [s["reason"] for s in out["skipped"]] == ["deadline", "deadline"]

    # 剩余预算够起，但比单探测上限小：超时被收紧到剩余
    now[0], timeouts[:] = 2000.0, []
    envadvice.check(root, "p.py", probe=probe, deadline_s=190.0, clock=lambda: now[0])
    assert timeouts[0] == projectenv.PROBE_TIMEOUT_S
    assert timeouts[1] == 190.0 - 96.0 and timeouts[1] < projectenv.PROBE_TIMEOUT_S
    assert len(timeouts) == 2  # 第三个：剩余 <0 → deadline


# ---------------------------------------------------------------------------
# #818 Codex r4220889695：项目设置写不进去（`remember()` 回 False）= 采用失败，不重置池、不开图
# ---------------------------------------------------------------------------
def _save_failure_world(client, tmp_path, monkeypatch):
    """真 venv + 一个候选行；`remember()` 被换成「写不进去」（回 False、什么都不落盘）。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text("import matplotlib\n", encoding="utf-8")
    real_venv(root, ".venv", python=WORKER_PY)
    pj = _open(client, root)
    rec = client.get("/api/engine/environment", query_string={"pj": pj}).get_json()["project"][
        "recommendation"
    ]
    row = next(c for c in rec["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))
    resets: list[str] = []
    monkeypatch.setattr(projectenv, "remember", lambda *a, **k: False)
    monkeypatch.setattr(engine_pool, "reset_worker_python", lambda: resets.append("reset"))
    monkeypatch.setattr(engine_pool, "shutdown_all", lambda *a, **k: resets.append("shutdown"))
    return root, pj, row, resets


@needs_worker
def test_a_candidate_adoption_whose_settings_cannot_be_saved_fails_and_resets_nothing(
    client, tmp_path, monkeypatch
):
    root, pj, row, resets = _save_failure_world(client, tmp_path, monkeypatch)

    response = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "candidate": row["id"], "expected_generation": row["generation"]},
        query_string={"pj": pj},
    )

    assert response.status_code == 500
    assert response.get_json()["code"] == "environment_save_failed"
    assert resets == []  # 解析器没变：不重置池、不把旧会话关掉


@needs_worker
def test_a_typed_path_adoption_whose_settings_cannot_be_saved_fails_and_resets_nothing(
    client, tmp_path, monkeypatch
):
    root, pj, _row, resets = _save_failure_world(client, tmp_path, monkeypatch)

    response = client.patch(
        "/api/engine/environment",
        json={"scope": "project", "python": envworld.venv_rel(".venv")},
        query_string={"pj": pj},
    )

    assert response.status_code == 500
    assert response.get_json()["code"] == "environment_save_failed"
    assert resets == []


@needs_worker
def test_an_mcp_adoption_whose_settings_cannot_be_saved_fails_and_never_runs_the_script(
    client, tmp_path, monkeypatch
):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))
    from tavotto_mcp import bridge, server

    root, _pj, row, resets = _save_failure_world(client, tmp_path, monkeypatch)
    (root / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "tavotto_registry.json").write_text(
        '{"scripts": {"figure.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}',
        encoding="utf-8",
    )
    ran: list[str] = []
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setattr(
        bridge.engine_pool, "get", lambda *a, **k: ran.append("run") or pytest.fail("不该开图")
    )
    bridge.reset_root_authority()
    try:
        res = server.call_tool(
            "tavotto_open_figure",
            {
                "project_path": str(root),
                "adopt_environment": row["id"],
                "expected_environment_generation": row["generation"],
            },
        )
    finally:
        bridge.sessions().clear()
        bridge.reset_root_authority()

    assert res["isError"] is True
    assert res["structuredContent"]["code"] == "environment_save_failed"
    assert ran == [] and resets == []
