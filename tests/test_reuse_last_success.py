"""#982 / V2 PR4：`reuse_last_success`——「再次打开脚本」沿用上次成功的运行配置（只由明确的打开动作带）。

真服务子进程 + 真 worker：上次成功的 argv 来自一次真实运行（记录由成功收口写），再次打开时请求明确带
`reuse_last_success: true` 才沿用；执行了什么 argv 以脚本自己写在项目外的日志为准（回显不算证据）。
不沿用的情形：没有带标志（省略 argv = 无参数，与 P09 之前逐字节一致）、给了 argv、敏感参数、上次是没观察全输入的
native 成功（`basis_observed: false`，核验覆盖不全）、脚本字节变了。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import (
    databinding,
    launchctx,
    launcher,
    pool as _pool,
    projectenv,
    runconfig,
    workdir,
)

ROOT = Path(__file__).resolve().parent.parent

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import argparse, json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--scale", default="1")
ap.add_argument("--tag", default="")
ap.parse_args()
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [2.0, 4.0, 8.0])
fig.savefig("fig_out.pdf")
"""
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}
SESSIONS = "/api/engine/preparation-sessions"
SETTLED = ("completed", "partial", "action_required", "cancelled")


def _argvs(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _run(app, body: dict, log: Path) -> dict:
    """创建会话 → 认领 run → 等落定；回创建时的报告（看 target）与落定后的报告。"""
    status, created = app.call(SESSIONS, body, timeout=120)
    assert status in (200, 201), created
    (run,) = [a for a in created["actions"] if a["kind"] == "run"]
    status, claimed = app.call(
        f"{SESSIONS}/{created['session_id']}/actions",
        {"action_id": run["id"], "expected_config_revision": created["config_revision"]},
        timeout=60,
    )
    assert status == 202, claimed
    deadline = time.time() + 300
    while True:
        _, report = app.call(f"{SESSIONS}/{created['session_id']}", timeout=30)
        if report["phase"] in SETTLED:
            return {"created": created, "final": report}
        assert time.time() < deadline, report
        time.sleep(0.2)


@pytest.fixture
def world(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    log = tmp_path / "argv.log"
    (proj / "fig.py").write_text(SCRIPT.format(log=str(log)), encoding="utf-8")
    return proj, log, tmp_path / "work"


@needs_worker
def test_reuse_is_explicit_and_runs_the_recorded_argv(world):
    proj, log, work = world
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        first = _run(app, {"script": "fig.py", "argv": ["--scale", "7.125"]}, log)
        assert first["final"]["phase"] == "completed", first["final"]
        assert _argvs(log) == [["--scale", "7.125"]]

        # 省略 argv（没有标志）= 无参数运行：与没有这个功能时逐字节一致
        plain = _run(app, {"script": "fig.py"}, log)
        assert plain["created"]["target"].get("argv_count", 0) == 0
        assert _argvs(log)[-1] == []

        # 再成功一次带参数的，让「上次成功」是 --scale 3 而不是上面的无参数
        again = _run(app, {"script": "fig.py", "argv": ["--scale", "7.125"]}, log)
        assert again["final"]["phase"] == "completed"

        # 明确带标志：沿用，会话目标里只有个数（argv 值不进公开投影），执行的 argv 以脚本日志为准
        reused = _run(app, {"script": "fig.py", "reuse_last_success": True}, log)
        assert reused["created"]["target"]["argv_count"] == 2
        assert reused["final"]["phase"] == "completed"
        assert _argvs(log)[-1] == ["--scale", "7.125"]
        assert "7.125" not in json.dumps(reused["created"])  # 参数值不进公开投影

        # 显式给了空 argv（明确要「不带参数运行」）：它赢，不用上次的参数覆盖（Codex #1044 r4271063076）；
        # 只有省略 argv 才考虑沿用
        bare = _run(app, {"script": "fig.py", "argv": [], "reuse_last_success": True}, log)
        assert bare["created"]["target"].get("argv_count", 0) == 0
        assert _argvs(log)[-1] == []
        # 无参数那次成功把「上次成功」改写成无参数，重新造一次带参数的成功，让后面的断言仍有东西可沿用
        again2 = _run(app, {"script": "fig.py", "argv": ["--scale", "7.125"]}, log)
        assert again2["final"]["phase"] == "completed"

        # 给了 argv：用户给的赢，标志不覆盖
        explicit = _run(
            app, {"script": "fig.py", "argv": ["--tag", "x"], "reuse_last_success": True}, log
        )
        assert _argvs(log)[-1] == ["--tag", "x"]
        assert explicit["created"]["target"]["argv_count"] == 2


@needs_worker
def test_script_edited_since_the_success_is_not_reused(world):
    proj, log, work = world
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        assert _run(app, {"script": "fig.py", "argv": ["--scale", "7.125"]}, log)["final"][
            "phase"
        ] == ("completed")
        (proj / "fig.py").write_text(
            (proj / "fig.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
        )
        again = _run(app, {"script": "fig.py", "reuse_last_success": True}, log)
        assert again["created"]["target"].get("argv_count", 0) == 0
        assert _argvs(log)[-1] == []


@needs_worker
def test_sensitive_argv_is_never_reused(world):
    proj, log, work = world
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        run = _run(
            app, {"script": "fig.py", "argv": ["--tag", "s3cret"], "argv_sensitive": True}, log
        )
        assert run["final"]["phase"] == "completed"
        again = _run(app, {"script": "fig.py", "reuse_last_success": True}, log)
        assert again["created"]["target"].get("argv_count", 0) == 0
        assert _argvs(log)[-1] == []


@needs_worker
def test_non_boolean_flag_is_rejected_with_nothing_executed(world):
    proj, log, work = world
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        try:
            status, body = app.call(
                SESSIONS, {"script": "fig.py", "reuse_last_success": "yes"}, timeout=60
            )
        except fa.HttpError as exc:
            status, body = exc.status, exc.body
        assert status == 400 and body["code"] == "bad_request", body
        assert _argvs(log) == []


# ---------------------------------------------------------------- 纯逻辑：native 记录不当作完整依据
def _record(proj: Path, script: str, cid: str, *, observed: bool | None) -> dict:
    """一条本机上全部依据都成立的记录（解释器 = 当前 Python），`basis_observed` 可控。"""
    py = sys.executable
    prefix = launcher.conda_prefix_of(py)
    src = {"script_sha1": launchctx._sha1(proj / script), "basis": {}}
    if observed is not None:
        src["basis_observed"] = observed
    binding = databinding.binding_for(proj / script, proj, workdir.mode_for(proj))
    return {
        "version": launchctx.RECORD_VERSION,
        "profile": launchctx.PROFILE_SAFE,
        "recorded_at": time.time(),
        "interpreter": {
            "path": py,
            "source": "bundled",
            "generation": projectenv.environment_generation(py),
            "project_decided": False,
        },
        "launcher": {"kind": launcher.KIND_DIRECT},
        "env_state": launcher.prefix_state(prefix) if prefix else "",
        "cwd_mode": workdir.mode_for(proj),
        "run_config": cid,
        "run_sensitive": False,
        "source": src,
        "binding_revision": binding.get("revision") or "",
    }


@pytest.fixture
def pure(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    sel = runconfig.selection_for(proj, "fig.py", ["--scale", "7.125"])
    return proj, sel.config_id


def test_a_complete_record_is_reusable(pure):
    proj, cid = pure
    runconfig.set_launch_record(proj, "fig.py", _record(proj, "fig.py", cid, observed=None))
    got = launchctx.reusable_run_config(
        proj, "fig.py", python=sys.executable, python_source="bundled"
    )
    assert got == cid


def test_a_record_without_observed_basis_is_not_reused(pure):
    """native 成功（`basis_observed: false`）：核验只覆盖脚本本身，凑不齐五样依据——不沿用。"""
    proj, cid = pure
    runconfig.set_launch_record(proj, "fig.py", _record(proj, "fig.py", cid, observed=False))
    assert (
        launchctx.reusable_run_config(
            proj, "fig.py", python=sys.executable, python_source="bundled"
        )
        is None
    )


def test_a_different_interpreter_is_not_reused(pure):
    proj, cid = pure
    runconfig.set_launch_record(proj, "fig.py", _record(proj, "fig.py", cid, observed=None))
    assert (
        launchctx.reusable_run_config(
            proj, "fig.py", python=sys.executable + ".other", python_source="bundled"
        )
        is None
    )


def test_a_no_argument_success_has_nothing_to_reuse(pure):
    proj, _ = pure
    runconfig.set_launch_record(proj, "fig.py", _record(proj, "fig.py", None, observed=None))
    assert (
        launchctx.reusable_run_config(
            proj, "fig.py", python=sys.executable, python_source="bundled"
        )
        is None
    )


def test_no_record_is_nothing_to_reuse(pure):
    proj, _ = pure
    assert (
        launchctx.reusable_run_config(
            proj, "fig.py", python=sys.executable, python_source="bundled"
        )
        is None
    )


def test_a_sensitive_record_is_not_reused(pure):
    """敏感配置：秘密值不落盘，记录只记「有过」——即便此刻进程里还握着它，也不自动沿用。"""
    proj, cid = pure
    rec = _record(proj, "fig.py", cid, observed=None)
    rec["run_sensitive"] = True
    runconfig.set_launch_record(proj, "fig.py", rec)
    assert (
        launchctx.reusable_run_config(
            proj, "fig.py", python=sys.executable, python_source="bundled"
        )
        is None
    )
