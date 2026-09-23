"""性能探针（ADR 0075）的后端一半与分析器。

两件事各自的主语：

* ``/api/perf/system``：回的是**这台机器的硬件与电源事实**，而且**只有**这些
  ——报告是用户要发给别人的东西，主机名 / 用户名 / 家目录一个字都不许出现。
  解析 pmset 输出的判据用真实见过的几种形状钉住（新老两种电源模式写法、
  「没有记录」≠「不知道」）。
* ``scripts/perf_report.py``：给它一份卡点已知的报告（人工构造的帧行），
  它必须把**那一个**卡点排在第一，而且流畅的报告不许报出任何卡点。
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import perfprobe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_report as PR  # noqa: E402


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()


# ---------------------------------------------------------------- 机器事实


def test_endpoint_returns_facts_without_identity(client):
    res = client.get("/api/perf/system")
    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "no-store"
    body = res.get_json()
    assert body["tavotto_version"]
    assert body["os"] == sys.platform
    text = json.dumps(body)
    # 身份信息在结构上就不在这里：逐个真实值查一遍（拿假值查会恒真）
    for ident in {socket.gethostname(), getpass.getuser(), os.path.expanduser("~")}:
        if ident and len(ident) >= 3:
            assert ident not in text, ident


def test_power_source_shapes():
    assert perfprobe._power_source("Now drawing from 'AC Power'\n -InternalBattery-0") == "ac"
    assert (
        perfprobe._power_source("Now drawing from 'Battery Power'\n -InternalBattery-0")
        == "battery"
    )
    assert perfprobe._power_source("Now drawing from 'UPS Power'") == "other"
    assert perfprobe._power_source(None) is None


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (" standby 1\n lowpowermode 1\n", True),
        (" standby 1\n lowpowermode 0\n", False),
        (" standby 0\n powermode 1\n", True),  # 新写法：1 = 低电量
        (" standby 0\n powermode 0\n", False),  # 0 = 自动
        (" standby 0\n powermode 2\n", False),  # 2 = 高性能
        (" standby 0\n", None),  # 两种写法都没有 = 不知道，不是「没开」
        (None, None),
    ],
)
def test_low_power_mode_shapes(settings, expected):
    assert perfprobe._low_power_mode(settings) is expected


def test_thermal_distinguishes_not_recorded_from_unknown():
    quiet = (
        "Note: No thermal warning level has been recorded\n"
        "Note: No performance warning level has been recorded\n"
        "Note: No CPU power status has been recorded\n"
    )
    assert perfprobe._thermal(quiet) == {"cpu_speed_limit": 100, "thermal_warning_level": 0}
    hot = "CPU_Scheduler_Limit \t= 100\nCPU_Available_CPUs \t= 8\nCPU_Speed_Limit \t= 62\n"
    assert perfprobe._thermal(hot)["cpu_speed_limit"] == 62
    assert perfprobe._thermal("something else") == {
        "cpu_speed_limit": None,
        "thermal_warning_level": None,
    }


# ---------------------------------------------------------------- 分析器


def _row(dt, render=4.0, handler=0.5, flush=0.3, raf=0.2, moves=1, latency=20.0):
    return [dt, render, handler, flush, raf, moves, latency]


def _seg(rows, kind="move", source="user", label=None, spans=None, counts=None, context=None):
    return {
        "kind": kind,
        "source": source,
        "label": label,
        "start": 0,
        "end": 1000,
        "tailUntil": 1600,
        "frames": rows,
        "tail": [],
        "moves": sum(r[5] for r in rows),
        "spans": spans or {},
        "counts": counts or {},
        "context": context or {},
    }


def _report(segments, idle=None, system=None):
    return {
        "schema": PR.SCHEMA,
        "created_at": "2026-09-23T00:00:00Z",
        "started_at": "2026-09-23T00:00:00Z",
        "duration_ms": 10000,
        "client": {"dpr": 2},
        "system": system or {"model": "MacBookAir10,1", "cpu": "Apple M1", "memory_gb": 8.0},
        "idle_frame_ms": idle if idle is not None else [16.7] * 60,
        "segments": segments,
        "authority": [],
    }


def test_smooth_report_has_no_findings():
    rows = [_row(16.7) for _ in range(120)]
    a = PR.analyze_report(_report([_seg(rows)]))
    assert len(a["stats"]) == 1
    assert a["findings"] == []


def test_input_bound_drag_is_ranked_first_with_code_pointer():
    # 每帧 2 个 pointermove、每个 12ms 的业务处理 → 帧间隔被输入处理撑到 ~33ms
    rows = [_row(33.4, render=4.0, handler=24.0, flush=3.0, moves=2) for _ in range(120)]
    spans = {
        "doc.txn_update": {"count": 240, "total": 240 * 10.0, "max": 14, "samples": []},
        "input.handler": {"count": 240, "total": 240 * 12.0, "max": 15, "samples": []},
        "input.react_flush": {"count": 240, "total": 240 * 1.5, "max": 3, "samples": []},
    }
    counts = {"render.Inspector": 240, "render.PanelView": 240, "render.Rulers": 10}
    a = PR.analyze_report(_report([_seg(rows, spans=spans, counts=counts)]))
    top = a["findings"][0]
    assert top.title == "拖动时输入处理太重"
    joined = " ".join(top.where)
    assert "documentStore.ts txnUpdate" in joined
    # 渲染次数只列每个 pointermove 至少 0.3 次的组件：Rulers 不该出现
    ev = " ".join(top.evidence)
    assert "Inspector" in ev and "Rulers" not in ev
    assert any("rAF 里调一次 onMove" in f for f in top.fix)


def test_render_bound_drag_names_svg_size():
    rows = [_row(40.0, render=34.0, handler=0.5, flush=0.2) for _ in range(120)]
    ctx = {
        "svg_nodes": 60000,
        "largest_svg_nodes": 52000,
        "dom_nodes": 70000,
        "preview_modes": {"vector": 1},
    }
    a = PR.analyze_report(_report([_seg(rows, kind="element", context=ctx)]))
    top = a["findings"][0]
    assert top.title.startswith("拖动时浏览器渲染")
    assert any("52000" in f for f in top.fix)


def test_growing_per_move_cost_is_reported():
    rows = [_row(16.7 + i * 0.4, handler=0.2 + i * 0.12, flush=0.1) for i in range(150)]
    ctx = {"txn_patches_at_end": 4200}
    a = PR.analyze_report(_report([_seg(rows, context=ctx)]))
    titles = [f.title for f in a["findings"]]
    assert "越拖越慢：每次移动的成本随拖动时长增长" in titles
    f = next(f for f in a["findings"] if f.title.startswith("越拖越慢"))
    assert any("4200" in e for e in f.evidence)


def test_synthetic_m1_vs_m3_gap_points_at_coalescing():
    m1 = [_row(16.7, handler=6.0, moves=1) for _ in range(120)]
    m3 = [_row(36.0, handler=18.0, flush=2.0, moves=3) for _ in range(120)]
    a = PR.analyze_report(
        _report(
            [
                _seg(m1, source="synthetic", label="m1"),
                _seg(m3, source="synthetic", label="m3"),
            ]
        )
    )
    assert any(f.title == "输入频率越高越卡：每帧多个 pointermove 没有合并" for f in a["findings"])


def test_clicks_are_not_drags():
    # 一次点击：几帧、没有 pointermove。它不是拖动，不进统计
    a = PR.analyze_report(_report([_seg([_row(16.7, moves=0) for _ in range(4)])]))
    assert a["stats"] == []


def test_machine_state_is_called_out():
    rows = [_row(16.7) for _ in range(60)]
    sysf = {
        "model": "MacBookAir10,1",
        "low_power_mode": True,
        "power_source": "battery",
        "rosetta": True,
    }
    a = PR.analyze_report(_report([_seg(rows)], system=sysf))
    f = next(f for f in a["findings"] if f.title.startswith("机器状态"))
    ev = " ".join(f.evidence)
    assert "低电量" in ev and "电池" in ev and "Rosetta" in ev


def test_cli_writes_html_and_json(tmp_path):
    rows = [_row(33.4, handler=24.0, flush=3.0, moves=2) for _ in range(60)]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_report([_seg(rows)])), encoding="utf-8")
    out_html = tmp_path / "r.html"
    out_json = tmp_path / "f.json"
    assert PR.main([str(p), "--html", str(out_html), "--json", str(out_json)]) == 0
    assert "<svg" in out_html.read_text(encoding="utf-8")
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload[0]["findings"][0]["title"] == "拖动时输入处理太重"


def test_rejects_foreign_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        PR.main([str(p)])


# ---------------------------------------------------------------- 保存报告


def _minimal_report() -> dict:
    return {"schema": perfprobe.REPORT_SCHEMA, "segments": [], "idle_frame_ms": []}


def test_save_report_writes_into_data_dir_with_server_side_name(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    body = _minimal_report()
    # 请求体里塞一个「文件名」：它绝不参与拼路径
    body["name"] = "../../evil.json"
    res = client.post("/api/perf/report", data=json.dumps(body), content_type="application/json")
    assert res.status_code == 200, res.get_json()
    out = res.get_json()
    saved = Path(out["dir"]) / out["name"]
    assert saved.parent == tmp_path / "perf-reports"
    assert out["name"].startswith("tavotto-perf-") and out["name"].endswith(".json")
    assert json.loads(saved.read_text(encoding="utf-8"))["schema"] == perfprobe.REPORT_SCHEMA
    assert not (tmp_path.parent / "evil.json").exists()
    # 同一秒再存一份：不覆盖前一份
    res2 = client.post("/api/perf/report", data=json.dumps(body), content_type="application/json")
    assert res2.get_json()["name"] != out["name"]
    assert len(list((tmp_path / "perf-reports").iterdir())) == 2


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps({"schema": "something-else/1", "segments": []}).encode(),
        json.dumps({"schema": perfprobe.REPORT_SCHEMA}).encode(),  # 没有片段列表
        json.dumps([1, 2, 3]).encode(),
    ],
)
def test_save_report_rejects_foreign_payloads(client, tmp_path, monkeypatch, payload):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    res = client.post("/api/perf/report", data=payload, content_type="application/json")
    assert res.status_code == 400
    assert res.get_json()["error"] == "perf_report_rejected"
    assert not (tmp_path / "perf-reports").exists()


def test_save_report_rejects_oversized_body_without_writing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(perfprobe, "MAX_REPORT_BYTES", 64)
    body = _minimal_report()
    body["pad"] = "x" * 200
    res = client.post("/api/perf/report", data=json.dumps(body), content_type="application/json")
    assert res.status_code == 400
    assert res.get_json()["reason"] == "too_large"
    assert not (tmp_path / "perf-reports").exists()
