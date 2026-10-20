"""#1047：native 成功记录的 helper / 数据依据（`launchctx.build_native_record(inputs=…)` / `observe_native_basis`）。

依据来自 bridge 在屏障上报的输入观察（与 safe 同一份 `InputObserver.report()` 形状）。这里钉住纯逻辑的边：
跑完后的完整观察 → `basis_observed: true`、与 safe 同形；停在 `show()` 屏障上的 → 依据照填、落盘 `false` + `basis_scope`；没有 /
截断 / 有文件没算出摘要 → `false`、不沿用；脚本跑完后的补齐只动**这一次运行**（同一 `launch_id`）的 native 记录。
真进程端到端在 `test_run_routing_e2e.py`。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from tavotto.engine import launchctx, runconfig, scriptargs


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text("print('x')\n", encoding="utf-8")
    return root


def _plan(root, plan_id="plan-1"):
    return SimpleNamespace(
        execution="native",
        interpreter=sys.executable,
        script_revision=scriptargs.source_revision(root / "fig.py"),
        environment={},
        workdir_decision={"mode": "script_dir"},
        binding={},
        plan_id=plan_id,
    )


def _inputs(**over):
    out = {
        "observation": "partial",
        "truncated": False,
        "files": [{"path": "data.csv", "size": 2, "sha256": "d" * 64}],
        "local_modules": [{"name": "helper", "path": "helper.py", "sha256": "h" * 64}],
    }
    out.update(over)
    return out


def test_a_complete_observation_becomes_the_same_basis_a_safe_record_carries(proj):
    record = launchctx.build_native_record(proj, "fig.py", _plan(proj), inputs=_inputs())
    assert record["source"]["basis_observed"] is True
    assert record["source"]["basis"] == {"data.csv": "d" * 64, "helper.py": "h" * 64}
    assert record["launch_id"] == "plan-1"


@pytest.mark.parametrize(
    "inputs",
    [
        None,  # 放行过 / 终端拥有的旧 runner
        _inputs(truncated=True),  # 观察器截断：存不全
        _inputs(
            files=[{"path": "big.bin", "size": 10**9, "sha256": None}]
        ),  # 大文件没算摘要：内容改了发现不了
    ],
)
def test_an_absent_or_incomplete_observation_is_never_reusable(proj, inputs):
    record = launchctx.build_native_record(proj, "fig.py", _plan(proj), inputs=inputs)
    assert record["source"] == {
        "script_sha1": record["source"]["script_sha1"],
        "basis": {},
        "basis_observed": False,
    }


def _store_unobserved(proj, plan_id="plan-1"):
    record = launchctx.build_native_record(proj, "fig.py", _plan(proj, plan_id), inputs=None)
    runconfig.set_launch_record(proj, "fig.py", record)
    return record


def test_the_late_observation_fills_in_the_record_of_the_same_run(proj):
    before = _store_unobserved(proj)
    assert launchctx.observe_native_basis(proj, "plan-1", _inputs()) is True
    after = runconfig.launch_record(proj, "fig.py")
    assert after["source"]["basis_observed"] is True
    assert after["source"]["basis"] == {"data.csv": "d" * 64, "helper.py": "h" * 64}
    # 其余事实原样（解释器 / 环境 / 运行目录档 / 脚本摘要都是收口那一刻的）
    assert {k: v for k, v in after.items() if k != "source"} == {
        k: v for k, v in before.items() if k != "source"
    }
    assert after["source"]["script_sha1"] == before["source"]["script_sha1"]


def test_the_late_observation_never_lands_on_another_runs_record(proj):
    _store_unobserved(proj, plan_id="plan-2")  # 之后又有一次运行成功，记录已换成它的
    assert launchctx.observe_native_basis(proj, "plan-1", _inputs()) is False
    assert runconfig.launch_record(proj, "fig.py")["source"]["basis_observed"] is False


def test_the_late_observation_leaves_safe_and_already_observed_records_alone(proj):
    safe = {**_store_unobserved(proj), "profile": "safe"}
    runconfig.set_launch_record(proj, "fig.py", safe)
    assert launchctx.observe_native_basis(proj, "plan-1", _inputs()) is False
    assert runconfig.launch_record(proj, "fig.py") == safe

    observed = launchctx.build_native_record(proj, "fig.py", _plan(proj), inputs=_inputs())
    runconfig.set_launch_record(proj, "fig.py", observed)
    other = _inputs(files=[{"path": "data.csv", "size": 2, "sha256": "e" * 64}])
    assert launchctx.observe_native_basis(proj, "plan-1", other) is False
    assert runconfig.launch_record(proj, "fig.py") == observed


def test_an_incomplete_late_observation_does_not_upgrade(proj):
    _store_unobserved(proj)
    assert launchctx.observe_native_basis(proj, "plan-1", _inputs(truncated=True)) is False
    assert launchctx.observe_native_basis(proj, "", _inputs()) is False
    assert runconfig.launch_record(proj, "fig.py")["source"]["basis_observed"] is False


def test_an_observation_that_stops_at_a_show_barrier_is_stored_but_never_vouched_for_on_disk(proj):
    """停在 `show()` 屏障上收口：依据照填、带 `basis_scope`，但落盘不是 `true`——之后可能被放行（Codex #1202 P1）。"""
    record = launchctx.build_native_record(
        proj, "fig.py", _plan(proj), inputs=_inputs(), complete=False
    )
    assert record["source"]["basis_observed"] is False
    assert record["source"]["basis_scope"] == launchctx.BASIS_SCOPE_BARRIER
    assert record["source"]["basis"] == {"data.csv": "d" * 64, "helper.py": "h" * 64}
    assert launchctx.barrier_basis_launch(record) == "plan-1"
    runconfig.set_launch_record(proj, "fig.py", record)

    # 脚本跑完后的全部观察补成 true，范围标记去掉
    later = _inputs(files=[{"path": "late.csv", "size": 2, "sha256": "f" * 64}])
    assert launchctx.observe_native_basis(proj, "plan-1", later) is True
    after = runconfig.launch_record(proj, "fig.py")
    assert after["source"]["basis_observed"] is True
    assert "basis_scope" not in after["source"]
    assert after["source"]["basis"] == {"late.csv": "f" * 64, "helper.py": "h" * 64}
    assert launchctx.barrier_basis_launch(after) == ""


@pytest.mark.parametrize(
    "record",
    [
        None,
        {
            "profile": "safe",
            "launch_id": "plan-1",
            "source": {"basis_observed": False, "basis_scope": "barrier"},
        },
        {
            "profile": "native",
            "launch_id": "plan-1",
            "source": {"basis_observed": True, "basis_scope": "barrier"},
        },
        {"profile": "native", "launch_id": "plan-1", "source": {"basis_observed": False}},
    ],
)
def test_only_a_native_barrier_scoped_record_names_a_run_to_ask_about(record):
    assert launchctx.barrier_basis_launch(record) == ""
