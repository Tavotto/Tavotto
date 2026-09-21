"""Trace（统一实施包 U09，ADR 0071）：有界的阶段轨迹，只答「卡在哪一步、哪一步坏的」，不记内容。"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import exportjob, exportreq, preparation, trace

_ORIGINAL = {
    "scope": "original",
    "filename": "fig",
    "formats": ["pdf"],
    "original": {"figure_id": "p1.pdf", "w_mm": 70.6, "h_mm": 52.9, "source_kind": "vector"},
}


def test_phases_are_a_closed_set_and_unknown_names_are_rejected():
    t = trace.Trace()
    t.mark("plan")
    with pytest.raises(ValueError):
        t.mark("render")  # 不在闭集里：想加名字先加到 PHASES
    with pytest.raises(ValueError):
        t.mark("plan", "maybe")
    assert set(trace.PHASES) >= {"plan", "check", "spawn", "execute", "receipt"}
    assert set(trace.PHASES) >= {
        "prepare",
        "source",
        "compile",
        "compose",
        "raster",
        "inspect",
        "publish",
    }


def test_the_trace_is_bounded_and_keeps_head_and_tail():
    """RC-080：节点超限不无限记——丢中间的，头（从哪开始）与尾（现在在哪）留着，`truncated` 如实。"""
    t = trace.Trace(limit=4)
    t.mark("plan")
    for _ in range(10):
        t.mark("execute")
    t.fail("receipt", "boom")
    payload = t.to_payload()
    assert len(payload["events"]) == 4
    assert payload["events"][0]["phase"] == "plan"
    assert payload["events"][-1]["phase"] == "receipt" and payload["events"][-1]["code"] == "boom"
    assert payload["truncated"] is True and payload["dropped"] == 8
    assert payload["failed_phase"] == "receipt" and payload["failed_code"] == "boom"
    assert payload["current_phase"] == "receipt"


def test_facts_only_carry_scalars_never_content():
    """RC-081：轨迹是公开投影的一部分——路径 / 脚本正文 / 图内文字进不来：非标量换成类型名，长串截断。"""
    t = trace.Trace()
    secret = "/Users/某人/实验 数据/" + "x" * 300
    t.mark(
        "compile",
        n=3,
        ratio=0.12345678,
        ok=True,
        path=secret,
        blob={"script": "import os"},
        items=[1],
    )
    facts = t.to_payload()["events"][0]["facts"]
    assert facts["n"] == 3 and facts["ratio"] == 0.123 and facts["ok"] is True
    assert facts["blob"] == "dict" and facts["items"] == "list"
    assert len(facts["path"]) <= 120 and "x" * 200 not in facts["path"]
    assert "import os" not in json.dumps(t.to_payload(), ensure_ascii=False)


def test_a_failure_pins_the_phase_it_happened_in():
    t = trace.Trace()
    t.mark("prepare")
    t.mark("source")
    t.mark("compile")
    t.fail("compose", "font_missing")
    assert t.failed_phase == "compose"
    assert t.to_payload()["events"][-1]["outcome"] == "failed"
    t2 = trace.Trace()
    t2.mark("spawn")
    t2.cancel("execute")
    assert t2.failed_phase == "execute" and t2.failed_code == "cancelled"


def test_preparation_and_export_results_both_carry_a_trace(tmp_path):
    """两条链同一个形状：准备结果与导出作业的载荷里都有 `trace`（有界、带 failed_phase）。"""
    result = preparation.PreparationResult()
    payload = result.to_payload()["trace"]
    assert payload["trace_version"] == trace.TRACE_VERSION and payload["events"] == []
    job = exportjob.prepare(_ORIGINAL, tmp_path / "out")
    assert job.to_payload()["trace"]["failed_phase"] is None


def test_an_export_that_fails_while_producing_names_the_phase(tmp_path):
    """FO-066：坏在哪一步就写哪一步——生产者在拿源那一步炸了，轨迹的 failed_phase 是 `source`，
    错误码原样带着；提交点没过、目录里没有半个文件。"""
    job = exportjob.prepare(_ORIGINAL, tmp_path / "out")

    def produce(job, tmp_dir):
        raise exportreq.ExportRequestError("export_render_failed", "源打不开", {"id": "fig"})

    payload = exportjob.run(job, produce)
    assert payload["status"] == "failed" and payload["error"]["code"] == "export_render_failed"
    tr = payload["trace"]
    assert tr["failed_phase"] == "source" and tr["failed_code"] == "export_render_failed"
    # 失败是轨迹上单独的一条（同一阶段先 ok 后 failed），读的人一眼看到坏在哪
    assert [(e["phase"], e["outcome"]) for e in tr["events"]] == [
        ("prepare", "ok"),
        ("source", "ok"),
        ("source", "failed"),
    ]
    assert not any((tmp_path / "out").glob("*.pdf"))
