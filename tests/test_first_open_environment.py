"""首开的环境选择前移与失效的显式选择（U03，ADR 0057）——**真实解释器，不 mock**。

判据的主语：`pool.resolve_worker_python(project, script=…)` 在**第一个 worker 起来之前**给出的
决策，以及它拒绝给出决策时说的原因。venv 用 `python -m venv` 现建（`support.venvfixture` 让
宿主的 matplotlib 可见——模拟「用户本来就装了」，一个字节都不下载）。

| 场景 | 预期 |
|---|---|
| 项目自带健康 venv、没有显式选择 | 首次执行前就选它并记住（trigger=first_open），脚本一次都不在默认环境里跑 |
| 同一 base 的两个 venv（两个项目） | 各选各的，prefix 不同，回执私有键不同（FO17） |
| 显式指定（环境变量 / 设置）一条没有 matplotlib 的解释器 | `explicit_python_unusable`，不换（FO15） |
| 设置里指定的那条已不存在 | 同上，reason=missing |
| 用户为项目挑的那条失效 | `project_python_unusable`（FO16 的停止合同） |
| 自动记住的那条失效 | 作废、重新发现，`invalidated_decision()` 有记录 |
| 用户明确选回默认链条 | 首开发现不再盖回去（FO-013） |
| 不同 minor 的项目 venv（FO11） | 有 `TAVOTTO_FOUNDATION_ALT_PYTHON` 才跑；没有就 skip 并说明 |
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from support import venvfixture
from tavotto.engine import (
    config as engine_config,
    pool as engine_pool,
    preparation,
    projectenv,
    receipt,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = (
    "import json, sys\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "print(json.dumps({'prefix': sys.prefix, 'executable': sys.executable}), file=sys.stderr)\n"
    "fig, ax = plt.subplots()\nax.plot([1, 2, 3], [4, 5, 6])\nfig.savefig('Fig1.pdf')\n"
)


@pytest.fixture(autouse=True)
def _clean():
    projectenv.reset_cache()
    engine_pool.reset_worker_python()
    yield
    engine_pool.shutdown_all(wait=True)
    projectenv.reset_cache()
    engine_pool.reset_worker_python()


def _project(tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "figure.py").write_text(SCRIPT, encoding="utf-8")
    return root


def _identity(python: str) -> dict:
    out = subprocess.run(
        [
            python,
            "-c",
            "import sys, json; print(json.dumps({'prefix': sys.prefix, 'base': sys.base_prefix}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    import json

    return json.loads(out.stdout.strip().splitlines()[-1])


def _bare_venv(tmp_path: Path, name: str = "bare") -> str:
    venv = tmp_path / name
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert (
        subprocess.run([str(python), "-c", "import matplotlib"], capture_output=True).returncode
        != 0
    )
    return str(python)


# ---------------------------------------------------------------- 前移：首次执行前就选对
@needs_worker
def test_a_healthy_project_venv_is_chosen_before_the_first_run_and_remembered(tmp_path):
    root = _project(tmp_path)
    venv = venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    venv_python = projectenv.interpreter_of(venv)
    assert projectenv.remembered(root) is None  # 干净：没记住过任何东西
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert engine_pool.same_python(python, venv_python)
    assert source == engine_pool.SOURCE_PROJECT_VENV
    state = projectenv.state(root)
    assert state["automatic"] is True and state["trigger"] == projectenv.TRIGGER_FIRST_OPEN
    assert state["python_version"] and state["matplotlib_version"]  # 体检的证据存下来了
    # 起第一条会话：用的就是它，generation 1——脚本一次都没在默认环境里跑（FO11 的形状）
    worker, resp = engine_pool.build("figure.py", str(root), "__main__")
    assert worker.generation == 1 and worker.python_source == engine_pool.SOURCE_PROJECT_VENV
    assert (
        Path(resp["runtime"]["prefix"]).resolve()
        == Path(_identity(venv_python)["prefix"]).resolve()
    )
    assert len(engine_pool._workers) == 1
    outcome = engine_pool.first_open_outcome(root)
    assert outcome["ok"] is True and outcome["rejected"] == []


@needs_worker
def test_first_open_discovery_runs_once_per_project(tmp_path, monkeypatch):
    root = _project(tmp_path)
    venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    calls = []
    real = projectenv.probe_environment
    monkeypatch.setattr(
        projectenv, "probe_environment", lambda p, m=None: calls.append(p) or real(p, m)
    )
    engine_pool.resolve_worker_python(str(root), script="figure.py")
    engine_pool.resolve_worker_python(str(root), script="figure.py")
    engine_pool.resolve_worker_python(str(root))
    assert len(calls) == 1


@needs_worker
def test_an_unhealthy_project_venv_is_rejected_with_a_reason_and_the_chain_continues(tmp_path):
    root = _project(tmp_path)
    _bare_venv(root, ".venv")  # 项目里有 .venv，但没有 matplotlib
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert source != engine_pool.SOURCE_PROJECT_VENV
    outcome = engine_pool.first_open_outcome(root)
    assert outcome["ok"] is False
    assert outcome["rejected"][0]["code"] == projectenv.ERROR_NO_MATPLOTLIB
    assert projectenv.remembered(root) is None  # 不健康的不记


# ---------------------------------------------------------------- FO17：同一 base 的两个 venv
@needs_worker
def test_fo17_two_venvs_on_the_same_base_are_kept_apart(tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    va = venvfixture.make_project_venv(a, ".venv", python=WORKER_PY)
    vb = venvfixture.make_project_venv(b, ".venv", python=WORKER_PY)
    pa, pb = projectenv.interpreter_of(va), projectenv.interpreter_of(vb)
    ia, ib = _identity(pa), _identity(pb)
    assert ia["base"] == ib["base"] and ia["prefix"] != ib["prefix"]  # 前提：同 base、不同 prefix
    assert engine_pool.same_python(
        engine_pool.resolve_worker_python(str(a), script="figure.py")[0], pa
    )
    assert engine_pool.same_python(
        engine_pool.resolve_worker_python(str(b), script="figure.py")[0], pb
    )
    wa, ra = engine_pool.build("figure.py", str(a), "__main__")
    wb, rb = engine_pool.build("figure.py", str(b), "__main__")
    assert ra["runtime"]["prefix"] != rb["runtime"]["prefix"]
    rcpt_a = receipt.from_worker(wa, ra, control_plane="python_pool", grant=None)
    rcpt_b = receipt.from_worker(wb, rb, control_plane="python_pool", grant=None)
    assert rcpt_a.private_invalidation_key() != rcpt_b.private_invalidation_key()
    assert rcpt_a.receipt_id != rcpt_b.receipt_id


# ---------------------------------------------------------------- 失效的显式选择：不静默替换
@needs_worker
def test_fo15_an_explicit_env_var_without_matplotlib_stops_instead_of_falling_through(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)  # 旁边有个能用的也不换
    bare = _bare_venv(tmp_path)
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, bare)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.code == engine_pool.EXPLICIT_UNUSABLE_CODE
    assert err.value.explicit == {
        "source": engine_pool.SOURCE_ENV,
        "python": bare,
        "reason": "no_matplotlib",
    }
    assert projectenv.remembered(root) is None  # 没有偷偷发现并采用项目 venv
    with pytest.raises(engine_pool.WorkerError):
        engine_pool.get("figure.py", str(root), "__main__")
    assert not engine_pool._workers


@needs_worker
def test_a_configured_interpreter_that_is_gone_or_broken_stops_with_the_reason(tmp_path):
    root = _project(tmp_path)
    bare = _bare_venv(tmp_path)
    engine_config.set_worker_python(bare)
    engine_pool.reset_worker_python()
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.code == engine_pool.EXPLICIT_UNUSABLE_CODE
    assert err.value.explicit["source"] == engine_pool.SOURCE_CONFIGURED
    assert err.value.explicit["reason"] == "no_matplotlib"
    gone = str(tmp_path / "gone" / "python")
    engine_config.set_worker_python(gone)
    engine_pool.reset_worker_python()
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.explicit == {
        "source": engine_pool.SOURCE_CONFIGURED,
        "python": gone,
        "reason": "missing",
    }


def test_a_stale_env_var_is_still_ignored_but_a_stale_setting_is_not(tmp_path, monkeypatch):
    """环境变量指向不存在的路径按「没设」（shell rc 里的过期值，既有语义）；设置里指定的那条
    不在了要说出来——那是用户在这个应用里做的选择。"""
    root = _project(tmp_path)
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, str(tmp_path / "gone" / "python"))
    if WORKER_PY is None:
        pytest.skip("没有科学栈，老链条选不出解释器")
    python, _ = engine_pool.resolve_worker_python(str(root), script="figure.py", discover=False)
    assert python  # 走到老链条了
    engine_config.set_worker_python(str(tmp_path / "gone2" / "python"))
    engine_pool.reset_worker_python()
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py", discover=False)
    assert err.value.explicit["reason"] == "missing"


def test_a_stale_env_var_and_a_stale_setting_on_the_same_path_still_stop(tmp_path, monkeypatch):
    """环境变量与设置里指着**同一条**已不存在的路径：设置那条不能因为去重被跳过而静默滑到
    系统解释器——仍然是 `explicit_python_unusable`（reason=missing，source=configured）
    （Codex #454 P2）。"""
    gone = str(tmp_path / "gone" / "python")
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, gone)
    engine_config.set_worker_python(gone)
    engine_pool.reset_worker_python()
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.select_worker_python()
    assert err.value.code == engine_pool.EXPLICIT_UNUSABLE_CODE
    assert err.value.explicit == {
        "source": engine_pool.SOURCE_CONFIGURED,
        "python": gone,
        "reason": "missing",
    }


@needs_worker
def test_fo16_a_user_chosen_project_interpreter_that_broke_stops_with_the_reason(tmp_path):
    root = _project(tmp_path)
    bare = _bare_venv(tmp_path)
    projectenv.remember(root, bare, automatic=False, trigger="user_selected")
    engine_pool.reset_worker_python()
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.code == engine_pool.PROJECT_PYTHON_UNUSABLE_CODE
    assert (
        err.value.explicit["source"] == "project"
        and err.value.explicit["reason"] == "no_matplotlib"
    )
    # 记录还在（没有被自动作废）：用户的选择由用户改
    assert projectenv.remembered_record(root)["path"] == bare
    engine_pool.reset_worker_python()
    os.remove(bare)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert err.value.explicit["reason"] == "missing"


@needs_worker
def test_an_automatically_remembered_interpreter_that_broke_is_invalidated_and_rediscovered(
    tmp_path,
):
    """#435 的负例形状：记住的 venv 被删 / 重建成别的 Python——自动的决定作废、重新发现，
    事实留在 `invalidated_decision()`（准备计划里能看到），不是静默换。"""
    root = _project(tmp_path)
    old = _bare_venv(tmp_path, "old")
    projectenv.remember(root, old, automatic=True, trigger="missing_dependency")
    engine_pool.reset_worker_python()
    venv = venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert engine_pool.same_python(python, projectenv.interpreter_of(venv))
    inv = engine_pool.invalidated_decision(root)
    assert inv == {"python": old, "reason": "no_matplotlib", "trigger": "missing_dependency"}
    plan = preparation.plan_for(
        project_id="p",
        project_root=str(root),
        asset_id="Fig1.pdf",
        stem="Fig1",
        script="figure.py",
        entry="__main__",
        original_artifact=None,
    )
    assert plan.environment["invalidated"]["reason"] == "no_matplotlib"
    assert plan.environment["trigger"] == projectenv.TRIGGER_FIRST_OPEN
    assert plan.environment["python_version"] and plan.environment["support"]


@needs_worker
def test_a_user_who_chose_the_default_chain_is_not_overridden_by_discovery(tmp_path):
    root = _project(tmp_path)
    venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    projectenv.remember_default(root)
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert source != engine_pool.SOURCE_PROJECT_VENV
    assert engine_pool.first_open_outcome(root) is None  # 连发现都没做


# ---------------------------------------------------------------- FO16：支持矩阵外的 Python（observing）
UNSUPPORTED_PYTHON_ENV = "TAVOTTO_FOUNDATION_UNSUPPORTED_PYTHON"


def _unsupported_python() -> str | None:
    """一个真实的、`projectenv.PYTHON_MIN` 之下的解释器（不需要 matplotlib：体检先看版本）。
    nightly 的 `foundation-observing` job 用 setup-python 装一个 3.9 点名进来；本机没有就 skip。"""
    cand = os.environ.get(UNSUPPORTED_PYTHON_ENV)
    if not cand or not Path(cand).is_file():
        return None
    try:
        out = subprocess.run(
            [cand, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    version = (int(out[0]), int(out[1]))
    return cand if version < projectenv.PYTHON_MIN else None


@needs_worker
def test_fo16_a_project_venv_on_an_unsupported_python_is_rejected_not_adopted(tmp_path):
    """observing：项目自带的 venv 建在支持矩阵外的 Python 上——首开发现体检到它、**不采用**、
    说得出原因（`project_env_unsupported_python` + 版本），链条继续到默认环境；静态原件与
    图内编辑照常（默认环境能跑）；没有偷偷升级、没有装包。"""
    unsupported = _unsupported_python()
    if unsupported is None:
        pytest.skip(f"not_run：{UNSUPPORTED_PYTHON_ENV} 没有指向「支持矩阵外」的真实解释器")
    root = _project(tmp_path)
    subprocess.run([unsupported, "-m", "venv", str(root / ".venv")], check=True, timeout=300)
    venv_python = projectenv.interpreter_of(root / ".venv")
    assert venv_python, "前提：venv 建成了"
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert not engine_pool.same_python(python, venv_python)
    assert source != engine_pool.SOURCE_PROJECT_VENV
    outcome = engine_pool.first_open_outcome(root)
    assert outcome["ok"] is False
    rejected = outcome["rejected"][0]
    assert rejected["code"] == projectenv.ERROR_UNSUPPORTED_PYTHON
    assert rejected["support"] == projectenv.SUPPORT_UNSUPPORTED
    assert rejected["python_version"].startswith("3.")
    assert projectenv.remembered(root) is None  # 不采用就不记
    plan = preparation.plan_for(
        project_id="p",
        project_root=str(root),
        asset_id="Fig1.pdf",
        stem="Fig1",
        script="figure.py",
        entry="__main__",
        original_artifact=None,
    )
    assert (
        plan.environment["discovery"]["rejected"][0]["code"] == projectenv.ERROR_UNSUPPORTED_PYTHON
    )
    assert plan.environment["discovery"]["rejected"][0]["venv"] == ".venv"
    assert plan.environment["source"] == source
    # venv 里一个字节都没被装（首开只体检）
    packages = subprocess.run(
        [
            venv_python,
            "-c",
            "import importlib.metadata as m; print(sorted(d.metadata['Name'] for d in m.distributions()))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    ).stdout
    assert "matplotlib" not in packages.lower()


# ---------------------------------------------------------------- FO11：真实不同 minor（observing）
ALT_PYTHON_ENV = "TAVOTTO_FOUNDATION_ALT_PYTHON"


def _alt_python() -> str | None:
    """另一个 minor 且装了 matplotlib 的解释器——由环境指定，harness 不装（05 §4）。"""
    alt = os.environ.get(ALT_PYTHON_ENV)
    if not alt or not Path(alt).is_file() or WORKER_PY is None:
        return None
    try:
        v_alt = subprocess.run(
            [alt, "-c", "import sys, matplotlib; print(sys.version_info[1])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=True,
        ).stdout.strip()
        v_app = subprocess.run(
            [sys.executable, "-c", "import sys; print(sys.version_info[1])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return alt if v_alt != v_app else None


def test_fo11_different_minor_project_venv_is_chosen_before_first_run(tmp_path):
    """observing：需要 `TAVOTTO_FOUNDATION_ALT_PYTHON` 指向另一个 minor 且装了 matplotlib 的
    解释器；本机 / invariants job 都没有 → skip 并说明（skip 不是绿；台账 observing）。"""
    alt = _alt_python()
    if alt is None:
        pytest.skip(f"not_run：{ALT_PYTHON_ENV} 没有指向「另一个 minor 且装了 matplotlib」的解释器")
    root = _project(tmp_path)
    make_venv = Path(__file__).parent / "fixtures" / "foundation" / "project_venv" / "make_venv.py"
    subprocess.run(
        [
            sys.executable,
            str(make_venv),
            "--python",
            alt,
            "--app-python",
            sys.executable,
            "--link-host-site",
            "--dest",
            str(root),
        ],
        check=True,
        timeout=600,
    )
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert source == engine_pool.SOURCE_PROJECT_VENV
    worker, resp = engine_pool.build("figure.py", str(root), "__main__")
    assert worker.generation == 1
    ident = _identity(python)
    assert Path(resp["runtime"]["prefix"]).resolve() == Path(ident["prefix"]).resolve()
    assert resp["runtime"]["python_version"].rsplit(".", 1)[0] != ".".join(
        map(str, sys.version_info[:2])
    )
