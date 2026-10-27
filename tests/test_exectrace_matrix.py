"""执行追踪故障注入矩阵（ADR 0151 PR4 §4.6）里此前没有直接证据的几格：T02 缺依赖（真 worker）、T03 依赖修复成功、
T04 安装失败。其余各格的看护用例列在 ADR 0151「PR4 修订」的矩阵表里。

判据的主语：真实工具入口 `fixtools.run_tool`（装包执行器是替身，绝不往真实环境装）与真服务 + 真 worker 落盘的 Trace。
"""

# ruff: noqa: F811 — 夹具（box）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import exectrace as xt, exectrace_diag as diag
from tests.test_fixtools_change import box, call, change_ctx  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _traced(ctx):
    t = xt.ExecTrace("fix_session", task_ref="prepM1")
    ctx.trace, ctx.trace_parent = (
        t,
        t.start_span("agent_run", "agent", attributes={"agent": "builtin"}),
    )
    return t


def test_t03_a_successful_dependency_repair_records_the_install_and_the_evidence(box):
    ctx = change_ctx(box)
    t = _traced(ctx)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert not r.is_error
    t.end_span(ctx.trace_parent)
    t.finish("ok")
    snap = t.snapshot()
    tools = [s for s in snap["spans"] if s["name"] == "tool_call"]
    assert [(s["attributes"]["tool"], s["status"]) for s in tools] == [("install_packages", "ok")]
    # 装包工具成功本身不是验证：没有结局 / 执行证据时归因如实说 unknown
    assert diag.attribution(snap)["verified_by_execution"] == "unknown"
    # 修复会话交 `fixed` 必须先过「最后一次改动之后的应用执行证据」那道门（ADR 0149 修订）：结局 fixed 才是 verified
    from tavotto.engine import (
        fixsession,  # noqa: F401 —— 修复会话的结局词表由它登记；不依赖别的用例先 import
    )

    t2 = xt.ExecTrace("fix_session")
    t2.finish("ok", attributes={"outcome": "fixed"})
    assert diag.attribution(t2.snapshot())["verified_by_execution"] == "verified"


def test_t04_a_failed_install_has_its_stable_code_and_is_never_a_success(box):
    ctx = change_ctx(box)
    ctx.installer = lambda python, reqs: {"ok": False, "code": "pip_failed", "done": []}
    t = _traced(ctx)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == "pip_failed"
    t.end_span(ctx.trace_parent)
    t.finish("failed", error_code="not_fixed")
    snap = t.snapshot()
    a = diag.attribution(snap)
    assert a["origin"] == {"stage": "tool_call", "error_code": "pip_failed", "attempt": 1}
    assert a["final_status"] == "failed"


try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（not_run）")
def test_t02_a_missing_dependency_is_located_at_the_real_execution_and_carries_its_code(tmp_path):
    """T02（真 worker）：脚本 import 一个不存在的包——失败落在 script_run、错误码是稳定的缺依赖码，包名不进 Trace。"""
    from tests.support import foundation_app as fa

    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "tests" / "fixtures" / "foundation" / "single_file_csv", proj)
    proc = subprocess.run(
        [WORKER_PY, "figure.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={
            "PATH": "/usr/bin:/bin",
            "MPLCONFIGDIR": str(tmp_path / "mpl"),
            "MPLBACKEND": "Agg",
            **{
                k: os.environ[k]
                for k in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP")
                if os.environ.get(k)
            },
        },
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    src = (proj / "figure.py").read_text(encoding="utf-8")
    (proj / "figure.py").write_text(
        "import tavotto_canary_missing_pkg_qz\n" + src, encoding="utf-8"
    )
    work = tmp_path / "work"
    with fa.running_app(proj, work) as app:
        _, panels = app.call("/api/panels", timeout=30)
        panel = next(p for p in panels["panels"] if p["id"].replace("\\", "/") == "figure.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "error", state["result"]
        code = state["result"]["error"]["code"]
        plan_id = state["plan"]["plan_id"]
        store = work / "data" / "cache" / "exec-trace"
        snap = None
        for _ in range(600):  # 盘上那份由后台写线程落盘：满载的机器上留足余量
            found = [
                json.loads(p.read_text(encoding="utf-8"))["trace"]
                for p in store.glob("*.json")
                if not p.name.endswith(".open.json")
            ]
            snap = next((s for s in found if s.get("task_ref") == plan_id), None)
            if snap:
                break
            import time

            time.sleep(0.1)
    assert snap is not None
    a = diag.attribution(snap)
    assert a["origin"]["stage"] == "script_run" and a["origin"]["error_code"] == code
    assert a["verified_by_execution"] == "failed"
    assert "tavotto_canary_missing_pkg_qz" not in json.dumps(snap)
