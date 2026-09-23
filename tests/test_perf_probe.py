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
#
# 每条用例给一份**卡点已知**的人工报告，断言分析器点名的是那一个卡点、级别对、
# 指得出代码位置；同时有「不该报」的反向用例（流畅 / 量化噪声 / 旧版计数），
# 否则一个什么都报的分析器也能全绿。


def _row(dt, render=4.0, handler=0.5, flush=0.3, raf=0.2, moves=1, latency=20.0):
    return [dt, render, handler, flush, raf, moves, latency]


def _seg(
    rows,
    kind="move",
    source="user",
    label=None,
    spans=None,
    counts=None,
    context=None,
    tail=None,
    tail_counts=None,
    legacy=False,
):
    seg = {
        "kind": kind,
        "source": source,
        "label": label,
        "start": 0,
        "end": 1000,
        "tailUntil": 1600,
        "frames": rows,
        "tail": tail or [],
        "moves": sum(r[5] for r in rows),
        "spans": spans or {},
        "counts": counts or {},
        "tailSpans": {},
        "tailCounts": tail_counts or {},
        "context": {"largest_svg_nodes": 5000, "probe_overhead_ms": 0, **(context or {})},
    }
    if legacy:  # 旧版探针：没有把松手后的计数分开
        del seg["tailCounts"], seg["tailSpans"]
    return seg


def _report(segments, idle=None, system=None, authority=None):
    return {
        "schema": PR.SCHEMA,
        "created_at": "2026-09-23T00:00:00Z",
        "started_at": "2026-09-23T00:00:00Z",
        "duration_ms": 10000,
        "client": {"dpr": 2, "viewport": {"w": 1280, "h": 800}},
        "system": system or {"model": "MacBookAir10,1", "cpu": "Apple M1", "memory_gb": 8.0},
        "idle_frame_ms": idle if idle is not None else [16.7] * 60,
        "segments": segments,
        "authority": authority or [],
    }


def _smooth(n=120, **kw):
    return [_row(16.7, **kw) for _ in range(n)]


def _titles(a, levels=PR.LEVELS):
    return [f.title for f in a["findings"] if f.level in levels]


def _find(a, prefix):
    hits = [f for f in a["findings"] if f.title.startswith(prefix)]
    assert hits, f"没有以「{prefix}」开头的结论：{_titles(a)}"
    return hits[0]


def _grade(a, dim):
    return next(g for d, g, _ in a["grades"] if d == dim)


def test_smooth_light_drag_has_no_problem_and_grades_a():
    rows = _smooth(render=2.0, handler=0.3, flush=0.2)
    a = PR.analyze_report(_report([_seg(rows, tail=_smooth(30))]))
    assert len(a["stats"]) == 1
    assert _titles(a, ("严重", "问题")) == []
    assert _grade(a, "流畅度") == "A"
    assert _grade(a, "余量") == "A"  # 每帧忙 2.5ms，刷新周期 16.7 → 6 倍以上
    assert _grade(a, "松手") == "A"
    assert _grade(a, "稳定性") == "A"


def test_input_bound_drag_points_at_txn_update_and_coalescing():
    # 每帧 2 个 pointermove、每个 12ms 的业务处理 → 帧间隔被输入处理撑到 ~33ms
    rows = [_row(33.4, render=4.0, handler=24.0, flush=3.0, moves=2) for _ in range(120)]
    spans = {
        "doc.txn_update": {"count": 240, "total": 240 * 10.0, "max": 14, "samples": []},
        "input.handler": {"count": 240, "total": 240 * 12.0, "max": 15, "samples": []},
        "input.react_flush": {"count": 240, "total": 240 * 1.5, "max": 3, "samples": []},
    }
    a = PR.analyze_report(_report([_seg(rows, spans=spans)]))
    f = _find(a, "拖动时输入处理太重")
    assert f.level == "严重"
    assert "documentStore.ts txnUpdate" in " ".join(f.where)
    assert any("rAF 里调一次 onMove" in x for x in f.fix)
    assert any("produceWithPatches" in x for x in f.fix)
    # 归因不能同时甩给渲染
    assert not [t for t in _titles(a) if t.startswith("拖动时浏览器渲染")]
    assert _grade(a, "流畅度") == "D"


def test_render_bound_drag_names_svg_size_and_not_input():
    rows = [_row(40.0, render=34.0, handler=0.5, flush=0.2) for _ in range(120)]
    ctx = {"svg_nodes": 60000, "largest_svg_nodes": 52000, "dom_nodes": 70000}
    a = PR.analyze_report(_report([_seg(rows, kind="element", context=ctx)]))
    f = _find(a, "拖动时浏览器渲染")
    assert f.level == "严重"
    assert any("52000" in x for x in f.fix)
    assert "拖动时输入处理太重" not in _titles(a)


def test_growing_per_move_cost_is_reported_with_patch_count():
    rows = [_row(16.7 + i * 0.4, handler=0.2 + i * 0.12, flush=0.1) for i in range(150)]
    a = PR.analyze_report(_report([_seg(rows, context={"txn_patches_at_end": 4200})]))
    f = _find(a, "越拖越慢")
    assert any("4200" in e for e in f.evidence)
    assert _grade(a, "稳定性") in ("C", "D")


def test_quantized_tiny_costs_are_not_a_trend():
    # WebKit 1ms 分辨率：前段每帧 0.06ms、后段 0.13ms——比值 2 倍，绝对值是噪声
    rows = [_row(16.7, handler=(0.0 if i % 16 else 1.0), flush=0.0) for i in range(100)]
    rows += [_row(16.7, handler=(0.0 if i % 8 else 1.0), flush=0.0) for i in range(50)]
    a = PR.analyze_report(_report([_seg(rows)]))
    assert not [t for t in _titles(a) if t.startswith("越拖越慢")]
    assert _grade(a, "稳定性") == "A"


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
    f = _find(a, "输入频率越高越卡")
    assert f.level == "问题"


def test_release_hitch_is_graded_by_length():
    short = [_row(55.0, render=1.0)] + [_row(16.7) for _ in range(10)]
    long = [_row(150.0, render=1.0)] + [_row(16.7) for _ in range(10)]
    a = PR.analyze_report(_report([_seg(_smooth(), tail=short)]))
    assert _find(a, "松手那一刻顿一下").level == "提示"
    b = PR.analyze_report(_report([_seg(_smooth(), tail=long)]))
    assert _find(b, "松手那一刻顿一下").level == "问题"


def test_release_moment_and_later_swap_are_two_findings():
    # M2 Pro 第四份报告的形状：松手那一帧 107ms，约 466ms 后换新图又一帧 90ms
    tail = [_row(107.0, render=0.0, handler=0.0, flush=0.0)] + [_row(16.7) for _ in range(21)]
    tail += [_row(90.0, render=2.0)] + [_row(16.7) for _ in range(5)]
    spans = {
        "input.release": {"count": 1, "total": 12.0, "max": 12.0, "samples": []},
        "input.release_flush": {"count": 1, "total": 70.0, "max": 70.0, "samples": []},
    }
    seg = _seg(_smooth(), kind="element", tail=tail)
    seg["tailSpans"] = spans
    a = PR.analyze_report(_report([seg]))
    first = _find(a, "松手那一刻顿一下")
    assert first.level == "问题"
    assert any("React 同步重渲染 70.0ms" in e for e in first.evidence)
    later = _find(a, "松手后又顿一下")
    assert any("松手后约 458ms 那一帧 90ms" in e for e in later.evidence)
    assert "PanelView.tsx" in " ".join(later.where)


def test_font_dropdown_rerender_on_release_is_named():
    seg = _seg(
        _smooth(),
        kind="element",
        tail=[_row(107.0)] + _smooth(10),
        tail_counts={"render.FontFamilyRow": 2, "render.Inspector": 2},
    )
    f = _find(PR.analyze_report(_report([seg])), "松手那一刻顿一下")
    assert any("字体下拉在松手后重画了 2 次" in e for e in f.evidence)
    # 没重画就不提
    quiet = _seg(_smooth(), kind="element", tail=[_row(107.0)] + _smooth(10))
    g = _find(PR.analyze_report(_report([quiet])), "松手那一刻顿一下")
    assert not any("字体下拉" in e for e in g.evidence)


def test_synthetic_runs_are_the_control_group_for_the_release_hitch():
    user = _seg(_smooth(), kind="element", tail=[_row(107.0)] + _smooth(10))
    syn = _seg(_smooth(), kind="element", source="synthetic", label="m1", tail=_smooth(10))
    a = PR.analyze_report(_report([user, syn]))
    f = _find(a, "松手那一刻顿一下")
    assert any(e.startswith("对照：自动测试") for e in f.evidence)
    # 没有自动测试时不编造对照
    b = PR.analyze_report(_report([user]))
    assert not any(e.startswith("对照") for e in _find(b, "松手那一刻顿一下").evidence)
    # 自动测试松手后自己也顿：对照不成立，不能说「顿挫出在提交上」
    syn_hitch = _seg(
        _smooth(), kind="element", source="synthetic", label="m1", tail=[_row(107.0)] + _smooth(10)
    )
    c = PR.analyze_report(_report([user, syn_hitch]))
    assert not any(e.startswith("对照") for e in _find(c, "松手那一刻顿一下").evidence)


def test_start_hitch_subtracts_probe_overhead():
    rows = [_row(70.0)] + _smooth()
    # 第一帧 70ms 里 60ms 是探针自己采上下文：扣掉后只剩 10ms，不该报
    a = PR.analyze_report(_report([_seg(rows, context={"probe_overhead_ms": 60})]))
    assert "起拖那一下顿住" not in _titles(a)
    b = PR.analyze_report(_report([_seg(rows, context={"probe_overhead_ms": 0})]))
    assert _find(b, "起拖那一下顿住").level == "提示"


def test_components_that_should_not_render_during_drag_are_named():
    rows = _smooth()
    counts = {"render.Inspector": 120, "render.CanvasHud": 120, "render.OverlaySvg": 120}
    a = PR.analyze_report(_report([_seg(rows, counts=counts)]))
    f = _find(a, "拖动中不该动的组件在跟着重渲染")
    assert f.level == "问题"
    assert "Inspector" in " ".join(f.evidence)
    # 跟手的读数 / 选框每帧一次是预期，不点名
    assert "CanvasHud" not in " ".join(f.evidence)
    assert "Inspector.tsx" in " ".join(f.where)


def test_legacy_report_does_not_judge_render_amplification():
    counts = {"render.Inspector": 120, "store.document": 120}
    a = PR.analyze_report(_report([_seg(_smooth(), kind="element", counts=counts, legacy=True)]))
    assert "拖动中不该动的组件在跟着重渲染" not in _titles(a)
    assert "图内元素拖动途中在写文档" not in _titles(a)
    assert _find(a, "旧版探针录的报告")
    assert _grade(a, "渲染放大") == "—"


def test_doc_write_is_judged_only_on_document_body_changes():
    # 自动保存改保存状态（document.save）不是写文档；只有文档本体（document.doc）才算
    save_only = {"store.document": 60, "store.document.save": 60}
    a = PR.analyze_report(
        _report([_seg(_smooth(), kind="element", counts=save_only, context={"doc_split": True})])
    )
    assert "图内元素拖动途中在写文档" not in _titles(a)
    body = {"store.document": 60, "store.document.doc": 60}
    b = PR.analyze_report(
        _report([_seg(_smooth(), kind="element", counts=body, context={"doc_split": True})])
    )
    assert "图内元素拖动途中在写文档" in _titles(b)


def test_unsplit_document_notifications_are_not_judged_but_disclosed():
    counts = {"store.document": 60}
    a = PR.analyze_report(_report([_seg(_smooth(), kind="element", counts=counts)]))
    assert "图内元素拖动途中在写文档" not in _titles(a)
    ev = " ".join(_find(a, "这份报告能下的结论有限").evidence)
    assert "分不清是改了文档还是自动保存" in ev


def test_autosave_during_drag_is_reported_with_its_cost():
    light = {"autosave.flush": {"count": 1, "total": 4.0, "max": 4.0, "samples": [4.0]}}
    a = PR.analyze_report(_report([_seg(_smooth(), spans=light)]))
    f = _find(a, "上一次松手的自动保存落在了这次拖动途中")
    assert f.level == "提示"
    assert "flushAutosave" in " ".join(f.where)
    heavy = {"autosave.flush": {"count": 1, "total": 40.0, "max": 40.0, "samples": [40.0]}}
    b = PR.analyze_report(_report([_seg(_smooth(), spans=heavy)]))
    assert _find(b, "上一次松手的自动保存落在了这次拖动途中").level == "问题"


def _capped(n=120, **kw):
    return [_row(33.3, **kw) for _ in range(n)]


def test_capped_30hz_ui_is_the_top_finding_even_though_no_frame_is_late():
    # 2026-09-23 一份 M2 Pro 报告：低电量 + 电池，空转与拖动都是 33ms 一帧。按实测周期判
    # 每一帧都「准时」，旧分析器给了流畅度 A——而用户看到的正是一卡一卡
    sysf = {"model": "Mac14,9", "low_power_mode": True, "power_source": "battery"}
    a = PR.analyze_report(_report([_seg(_capped())], idle=[33.3] * 60, system=sysf))
    top = a["findings"][0]
    assert top.title == "整个界面被限在约 30 帧/秒"
    assert top.level == "严重"
    ev = " ".join(top.evidence)
    assert "低电量模式" in ev and "用电池" in ev
    assert _grade(a, "流畅度") == "C"


def test_capped_ui_without_low_power_mode_points_elsewhere():
    a = PR.analyze_report(_report([_seg(_capped())], idle=[33.3] * 60))
    f = _find(a, "整个界面被限在约 30 帧/秒")
    assert "低电量模式没开" in " ".join(f.evidence)


def test_60hz_and_120hz_are_not_capped():
    for dt in (16.7, 8.3):
        a = PR.analyze_report(_report([_seg([_row(dt) for _ in range(120)])], idle=[dt] * 60))
        assert not [t for t in _titles(a) if t.startswith("整个界面被限在")], dt


def test_budget_and_latency_are_measured_against_60hz_when_capped():
    # 30Hz 下每帧忙 10ms：按 33ms 算有 3.3 倍余量，按 60Hz 算只有 1.7 倍
    rows = _capped(render=8.0, handler=1.0, flush=1.0, latency=50.0)
    a = PR.analyze_report(_report([_seg(rows)], idle=[33.3] * 60))
    assert 1.5 < a["headroom"] < 1.8
    # 50ms 延迟 = 3 个 60Hz 帧（按 33ms 算只有 1.5 个，会被放过）
    assert _find(a, "拖动跟手性差")


def _render(
    request,
    rt=520.0,
    server=None,
    patch=380.0,
    draw=40.0,
    manifest=30.0,
    dom=20.0,
    swap=(60.0, 45.0),
):
    t = {
        "worker_get_ms": 1.0,
        "queue_wait_ms": 0.0,
        "patch_apply_ms": patch,
        "canvas_draw_ms": draw,
        "manifest_ms": manifest,
        "total_ms": patch + draw + manifest + 5.0,
    }
    if server is not None:
        t["server_ms"] = server
    resp = request + rt
    return {
        "request": request,
        "response": resp,
        "applied": resp + 1.0,
        "painted": resp + 1.0 + dom,
        "ok": True,
        "patches": 39,
        "svg_kb": 60,
        "timings": t,
        "swap_frames": [[swap[0], swap[1]], [16.7, 1.0]],
    }


def _committed(end, **kw):
    seg = _seg(_smooth(30), kind="element", context={"overrides": 39}, **kw)
    seg["end"] = end
    return seg


def test_release_chain_names_the_slowest_stage():
    # 重放 39 条 override 占了 380ms：它必须被点名，并指到 overrides
    segs = [_committed(1000), _committed(3000)]
    rep = _report(segs)
    rep["renders"] = [_render(1002, server=460.0), _render(3003, server=462.0)]
    a = PR.analyze_report(rep)
    f = _find(a, "松手 → 图落定")
    assert "重放全部 override" in f.title
    assert f.level == "问题"  # 中位 ~540ms
    assert any("overrides" in w for w in f.where)
    ev = " ".join(f.evidence)
    assert "传输 + 解析" in ev and "换进 DOM" in ev
    # 拆开之后不再报那条笼统的
    assert "松手后要等后端重画一遍才落定" not in _titles(a)


def test_release_chain_blames_transfer_when_backend_is_fast():
    rep = _report([_committed(1000)])
    rep["renders"] = [_render(1002, rt=600.0, server=60.0, patch=5.0, draw=30.0, manifest=20.0)]
    f = _find(PR.analyze_report(rep), "松手 → 图落定")
    assert "传输 + 解析" in f.title


def test_release_chain_without_server_ms_says_so():
    rep = _report([_committed(1000)])
    rep["renders"] = [_render(1002)]
    f = _find(PR.analyze_report(rep), "松手 → 图落定")
    assert any("没报 server_ms" in e for e in f.evidence)


def test_clicks_do_not_claim_a_release_chain():
    # 单击（按下又松开）后一秒，别处发起了一次渲染：它不是这次「松手」的定稿渲染
    click = _seg([_row(16.7, moves=0)], kind="element", context={"doc_split": True})
    click["end"] = 1000
    drag = _committed(5000)
    drag["context"]["doc_split"] = True
    drag["tailCounts"] = {"store.document.doc": 1}
    rep = _report([click, drag])
    rep["renders"] = [_render(1800, server=460.0), _render(5005, server=460.0)]
    f = _find(PR.analyze_report(rep), "松手 → 图落定")
    assert f.evidence[0].startswith("1 次提交修改的松手")
    assert "调度" not in f.title


def test_synthetic_segments_do_not_claim_a_release_chain():
    # 自动测试以取消收尾，不提交：它后面的渲染与它无关
    seg = _committed(1000)
    seg["source"] = "synthetic"
    rep = _report([seg])
    rep["renders"] = [_render(1002, server=460.0)]
    assert not [t for t in _titles(PR.analyze_report(rep)) if t.startswith("松手 → 图落定")]


def test_old_reports_fall_back_to_the_undivided_total():
    rep = _report([_seg(_smooth())], authority=[{"commit_to_authority_ms": 514.0}] * 3)
    f = _find(PR.analyze_report(rep), "松手后要等后端重画一遍才落定")
    assert any("拆不开" in e for e in f.evidence)


def test_same_issue_in_many_segments_is_one_finding():
    tail = [_row(60.0, render=1.0)] + [_row(16.7) for _ in range(5)]
    a = PR.analyze_report(_report([_seg(_smooth(), tail=tail) for _ in range(3)]))
    hits = [f for f in a["findings"] if f.title == "松手那一刻顿一下（同步提交）"]
    assert len(hits) == 1
    assert hits[0].evidence[0].startswith("出现在 3 段")


def test_saturated_main_thread_is_called_out_before_it_drops_frames():
    # 帧间隔还在 1.5 倍以内（不算超时），但主线程每帧忙 17ms：已经没有余量
    rows = [_row(20.0, render=14.0, handler=2.0, flush=1.0) for _ in range(120)]
    a = PR.analyze_report(_report([_seg(rows)]))
    f = _find(a, "主线程已经满载")
    assert f.level == "问题"
    assert _grade(a, "余量") == "D"


def test_latency_without_jank_gets_the_right_advice():
    rows = [_row(16.7, latency=70.0) for _ in range(120)]
    a = PR.analyze_report(_report([_seg(rows)]))
    f = _find(a, "拖动跟手性差")
    assert any("帧率正常而延迟高" in x for x in f.fix)


def test_scene_complexity_correlates_with_render_cost():
    segs = [
        _seg([_row(16.7, render=r) for _ in range(60)], context={"largest_svg_nodes": n})
        for n, r in ((300, 1.0), (3000, 3.0), (9000, 7.0), (20000, 12.0))
    ]
    a = PR.analyze_report(_report(segs))
    f = _find(a, "图越复杂，拖动时渲染越慢")
    assert any("r=+" in e for e in f.evidence)


def test_clicks_are_not_drags():
    # 一次点击：几帧、没有 pointermove。它不是拖动，不进统计
    a = PR.analyze_report(_report([_seg([_row(16.7, moves=0) for _ in range(4)])]))
    assert a["stats"] == []
    assert "这份报告能下的结论有限" in _titles(a)


def test_coverage_asks_for_auto_test_and_a_complex_figure():
    a = PR.analyze_report(_report([_seg(_smooth(), context={"largest_svg_nodes": 300})]))
    ev = " ".join(_find(a, "这份报告能下的结论有限").evidence)
    assert "自动测试" in ev and "300" in ev


def test_machine_state_is_called_out():
    sysf = {
        "model": "MacBookAir10,1",
        "low_power_mode": True,
        "power_source": "battery",
        "rosetta": True,
    }
    a = PR.analyze_report(_report([_seg(_smooth(60))], system=sysf))
    f = _find(a, "机器状态")
    ev = " ".join(f.evidence)
    assert "低电量" in ev and "电池" in ev and "Rosetta" in ev
    # 改法只列与出现的状态对应的那几条
    assert not any("活动监视器" in x for x in f.fix)
    assert _grade(a, "机器") == "C"


def test_cli_writes_html_and_json(tmp_path):
    rows = [_row(33.4, handler=24.0, flush=3.0, moves=2) for _ in range(60)]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_report([_seg(rows)])), encoding="utf-8")
    out_html = tmp_path / "r.html"
    out_json = tmp_path / "f.json"
    assert PR.main([str(p), str(p), "--html", str(out_html), "--json", str(out_json)]) == 0
    page = out_html.read_text(encoding="utf-8")
    assert "<svg" in page and "机器对照" in page and "评分" in page
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    titles = [f["title"] for f in payload[0]["findings"]]
    assert "拖动时输入处理太重" in titles
    # 每一维都在（没有数据的写「—」）：多份报告对照时列才对得上
    dims = [g["dimension"] for g in payload[0]["grades"]]
    assert dims == ["流畅度", "响应", "余量", "松手", "稳定性", "渲染放大", "机器", "覆盖"]


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


@pytest.mark.skipif(sys.platform != "darwin", reason="只在 macOS 上问 sw_vers")
def test_os_version_falls_back_to_sw_vers_when_mac_ver_is_empty(monkeypatch):
    # 冻结构建里 mac_ver() 在个别机器上回空串（2026-09-23 一份 M2 Pro 报告）
    monkeypatch.setattr(perfprobe.platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    calls = []
    real = perfprobe._run

    def spy(argv):
        calls.append(argv[0])
        return "15.6.1" if argv[0] == "/usr/bin/sw_vers" else real(argv)

    monkeypatch.setattr(perfprobe, "_run", spy)
    assert perfprobe.system_facts()["os_version"] == "15.6.1"
    assert "/usr/bin/sw_vers" in calls
