"""执行追踪故障注入矩阵 T05（ADR 0151 PR4 §4.6）：Pillow 像素图。

两半证据：
* 真 worker 的一次准备（脚本只用 Pillow 画、没有 matplotlib 图）——产品如实报「没有产生任何图」，Trace 必须同样记失败，
  阶段 / 错误码与准备结果一致，不因为「脚本跑完了、有张 png」就升格成可编辑的成功；
* 转写交接（假模型走真实的 `fixsession` + `run_tool`）——`finish(outcome=convert)` 冻结为 ok + outcome，且 `convert_script` 工具 Span 在册。
"""

# ruff: noqa: F811 — 夹具（box）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

import json
import subprocess
import time

import pytest

from tavotto.engine import exectrace_diag as diag, fixsession
from tests.test_fixasks import Scripted, box, wait_for  # noqa: F401

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None


def _has_pillow(py) -> bool:
    if not py:
        return False
    try:
        return (
            subprocess.run(
                [py, "-c", "import PIL"], capture_output=True, timeout=60, check=False
            ).returncode
            == 0
        )
    except OSError:
        return False


HAS_PILLOW = _has_pillow(WORKER_PY)

DRAW = (
    "from PIL import Image, ImageDraw\n"
    "im = Image.new('RGB', (200, 120), 'white')\n"
    "ImageDraw.Draw(im).line((0, 0, 200, 120), fill='red', width=3)\n"
    "im.save('pillow_canary_chart.png')\n"
)


@pytest.mark.skipif(not HAS_PILLOW, reason="worker 解释器里没有 Pillow（not_run）")
def test_t05_a_pillow_only_script_is_recorded_as_the_failure_the_product_reports(tmp_path):
    from tests.support import foundation_app as fa

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pillow_canary_draw.py").write_text(DRAW, encoding="utf-8")
    # 先跑一遍留下那张 png（面板由磁盘上的图 + 脚本关联而来）
    subprocess.run([WORKER_PY, "pillow_canary_draw.py"], cwd=proj, check=True, timeout=120)
    work = tmp_path / "work"
    with fa.running_app(proj, work) as app:
        _, panels = app.call("/api/panels", timeout=30)
        panel = next(p for p in panels["panels"] if p["id"] == "pillow_canary_chart.png")
        state = app.prepare(panel["id"])
        result = state["result"]
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
            time.sleep(0.1)
    # 产品的结论：没有可编辑图
    assert result["status"] == "error", result
    code = result["error"]["code"]
    # 产品按脚本有没有打印分成两个码（`_silent` = 一个字都没打印）；本用例的主语是「Trace 与产品结论一致」，两者都是没出可编辑图
    assert code in ("no_figures_captured", "no_figures_captured_silent")
    # Trace 与之一致：失败、同一错误码、落在真实执行区间
    assert snap is not None
    assert snap["status"] == "failed" and snap["error_code"] == code
    runs = [s for s in snap["spans"] if s["name"] == "script_run"]
    assert len(runs) == 1 and runs[0]["status"] == "failed" and runs[0]["error_code"] == code
    assert runs[0]["timing"] == "measured"
    a = diag.attribution(snap)
    assert a["origin"]["stage"] == "script_run" and a["origin"]["error_code"] == code
    assert a["final_status"] == "failed" and a["verified_by_execution"] == "failed"
    text = json.dumps(snap) + json.dumps(a)
    for leak in ("pillow_canary", str(proj), str(tmp_path)):
        assert leak not in text


def _convert_session(box, model):
    (box.proj / "fig.py").write_text("print(1)", encoding="utf-8")
    hint = {"kind": "raster_script", "library": "pillow", "in_project": False, "path": None}
    from tavotto.engine.fixtools import AuditLog

    return fixsession.SESSIONS.start(
        scope="pj",
        prep_session_id="prep1",
        script="fig.py",
        project_root=box.proj,
        script_path=box.proj / "fig.py",
        model=model,
        report=lambda: {"phase": "failed", "no_figure_hint": hint},
        audit=AuditLog(box.data / "ai" / "fix_audit.jsonl"),
    )


def test_t05_the_convert_handoff_freezes_ok_with_outcome_convert_and_the_tool_span(box):
    model = Scripted(
        [("convert_script", {})],
        [("finish", {"outcome": "convert", "summary": "我会生成一份可编辑版本"})],
    )
    s = _convert_session(box, model)
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    assert fixsession.SESSIONS.get(s.id, "pj")["outcome"] == "convert"
    snap = s.trace.snapshot()
    assert snap is not None
    root = next(x for x in snap["spans"] if x["span_id"] == snap["root_span_id"])
    assert snap["status"] == "ok" and root["attributes"]["outcome"] == "convert"
    tools = [x for x in snap["spans"] if x["name"] == "tool_call"]
    assert [(t["attributes"]["tool"], t["status"]) for t in tools] == [
        ("convert_script", "ok"),
        ("finish", "ok"),
    ]
    a = diag.attribution(snap)
    assert a["final_status"] == "ok" and a["origin"] is None
    assert "我会生成" not in json.dumps(snap) and "fig.py" not in json.dumps(snap)
