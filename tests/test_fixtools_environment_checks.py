"""ADR 0221 第 1 期：`read_environment_checks` 与「改」组失败结果里的自检形状。

判据的主语：工具**返回的 JSON**（形状、脱敏、条数）、改组失败结果的文本、诊断包 report.json 的**序列化文本**里没有原文。
一切在 tmp_path；密钥串运行时拼接。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import json
import time

from tavotto.engine import deprepair, diagnostics, envdiag
from tavotto.engine.fixtools import REGISTRY, STAGE_OF_TOOL, observe as fx_observe
from tests.test_fixtools import call, make_ctx
from tests.test_fixtools_change import box, change_ctx  # noqa: F401 - box 是夹具

CANARY = "sk-ant-" + "api03-CANARYsecretValue1234567890"
SHAPE = {
    "stage": "error_reply",
    "exit_code": 1,
    "worker_code": "safe_file_operation_blocked",
    "structure": [],
    "engine_frame": None,
}


def _job(**kw) -> dict:
    now = time.time()
    row = {
        "requested": ["phreeqpython"],
        "outcome": "failed",
        "source": "pypi",
        "started_at": now - 5,
        "finished_at": now - 1,
        "committed": False,
        "generation": "0123456789abcdef",
        "error_code": "dependency_worker_selftest_failed",
        "selftest": dict(SHAPE),
        "selftest_detail": "Traceback...\nPermissionError at " + CANARY,
    }
    row.update(kw)
    return row


def _tool_json(ctx) -> dict:
    r = call(ctx, "read_environment_checks", {})
    assert not r.is_error
    return json.loads(r.content)


def test_registered_as_observe_and_stage_diagnose():
    tool = REGISTRY["read_environment_checks"]
    assert tool.tier == "observe"
    assert STAGE_OF_TOOL["read_environment_checks"] == "diagnose"


def test_no_records_returns_empty_list(box):
    assert _tool_json(make_ctx(box)) == {"generations": []}


def test_failed_selftest_shape_and_scrubbed_detail_tail(box):
    envdiag.record_job(box.proj, _job())
    out = _tool_json(make_ctx(box))
    (gen,) = out["generations"]
    assert gen["requested"] == ["phreeqpython"]
    assert gen["outcome"] == "failed"
    assert gen["error_code"] == "dependency_worker_selftest_failed"
    assert gen["selftest"] == SHAPE
    assert "PermissionError" in gen["detail_tail"]
    assert CANARY not in gen["detail_tail"] and CANARY not in json.dumps(out)


def test_detail_tail_is_bounded_and_keeps_the_end(box):
    envdiag.record_job(box.proj, _job(selftest_detail="A" * 20000 + "THE-END"))
    (gen,) = _tool_json(make_ctx(box))["generations"]
    assert len(gen["detail_tail"]) <= 4096 and gen["detail_tail"].endswith("THE-END")


def test_at_most_five_generations_newest_first(box):
    for n in range(8):
        envdiag.record_job(box.proj, _job(requested=[f"pkg{n}"], selftest_detail=f"d{n}"))
    gens = _tool_json(make_ctx(box))["generations"]
    assert [g["requested"] for g in gens] == [[f"pkg{n}"] for n in (7, 6, 5, 4, 3)]


def test_row_without_selftest_has_no_detail_tail(box):
    envdiag.record_job(
        box.proj, _job(outcome="done", error_code=None, selftest=None, selftest_detail="x")
    )
    (gen,) = _tool_json(make_ctx(box))["generations"]
    assert gen["selftest"] is None and "detail_tail" not in gen


def test_closed_value_passes_unknown_keys_but_drops_open_text():
    shape = {
        "stage": "error_reply",
        "operations": {"blocked": 2, "kinds": ["unlink"]},
        "fidelity": 0.5,
    }
    assert fx_observe.closed_value(shape) == shape  # 将来的键（#2370）不被白名单丢掉
    assert fx_observe.closed_value("x" * 500) is None  # 长文本不算闭集值
    assert fx_observe.closed_value(object()) is None


def test_install_packages_failure_carries_selftest_shape(box):
    envdiag.record_job(box.proj, _job())
    ctx = change_ctx(box)
    ctx.installer = lambda python, reqs: {
        "ok": False,
        "code": "dependency_worker_selftest_failed",
        "done": [],
    }
    r = call(ctx, "install_packages", {"env": "e", "packages": ["numpy"]})
    assert r.is_error and r.code == "dependency_worker_selftest_failed"
    assert "Self-test record: " + json.dumps(SHAPE) in r.content
    assert "Use read_environment_checks to see the full self-test record" in r.content
    assert "PermissionError" not in r.content  # 原文只在 read_environment_checks 里


def test_install_packages_other_failure_has_no_selftest_note(box):
    envdiag.record_job(box.proj, _job())
    ctx = change_ctx(box)
    ctx.installer = lambda python, reqs: {"ok": False, "code": "dependency_not_found", "done": []}
    r = call(ctx, "install_packages", {"env": "e", "packages": ["numpy"]})
    assert "read_environment_checks" not in r.content


def test_change_python_failure_carries_selftest_shape(box):
    envdiag.record_job(box.proj, _job())
    ctx = change_ctx(
        box,
        python_changer=lambda req: {"ok": False, "code": "dependency_worker_selftest_failed"},
    )
    r = call(ctx, "change_python", {"version": "3.12"})
    assert r.code == "dependency_worker_selftest_failed"
    assert "Self-test record: " + json.dumps(SHAPE) in r.content
    assert "Use read_environment_checks" in r.content


def test_selftest_failure_without_any_record_still_points_at_the_tool(box):
    ctx = change_ctx(
        box,
        python_changer=lambda req: {"ok": False, "code": "dependency_worker_selftest_failed"},
    )
    r = call(ctx, "change_python", {"version": "3.12"})
    assert "Use read_environment_checks" in r.content and "Self-test record" not in r.content


def test_diagnostics_bundle_never_contains_the_detail(box):
    envdiag.record_job(box.proj, _job(selftest_detail="UNIQUE-RAW-DETAIL-9f3a"))
    rows = envdiag.recent(box.proj)
    assert "selftest_detail" not in rows[0]
    report = diagnostics._project_section({"open": True, "managed_environment_jobs": rows}, [])
    assert "UNIQUE-RAW-DETAIL-9f3a" not in json.dumps(report)
    assert "selftest_detail" not in json.dumps(report)
    # 即使上游误把带原文的行塞进来，导出投影也丢掉它
    leaky = envdiag.recent_checks(box.proj)
    assert "selftest_detail" in leaky[0]
    again = diagnostics._project_section({"open": True, "managed_environment_jobs": leaky}, [])
    assert "UNIQUE-RAW-DETAIL-9f3a" not in json.dumps(again)


def test_deprepair_stores_sanitized_tail_from_worker_self_test(monkeypatch):
    """`worker_self_test` 失败的返回带 `tail`；`_sanitize` 对它起作用（路径 / 密钥不留）。"""
    text = deprepair._sanitize("boom " + CANARY)
    assert CANARY not in text


def test_detail_tail_stays_out_of_the_repair_transcript(box):
    """`detail_tail` 只给本机模型：转录（→ 诊断包 detail-runs.json 的 fix_sessions）里有形状、没有自检原文。"""
    sentinel = "LOCAL-ONLY-SELFTEST-LINE-7731"
    envdiag.record_job(box.proj, _job(selftest_detail="Traceback...\n" + sentinel))
    ctx = make_ctx(box)
    gen = _tool_json(ctx)["generations"][0]
    assert sentinel in gen["detail_tail"]  # 模型这一侧仍拿得到
    dumped = json.dumps(ctx.transcript, ensure_ascii=False)
    assert "read_environment_checks" in dumped and "detail_tail" not in dumped
    assert sentinel not in dumped
    (entry,) = ctx.transcript
    kept = json.loads(entry["text"])["generations"][0]
    assert kept["selftest"] == SHAPE and kept["error_code"] == "dependency_worker_selftest_failed"
