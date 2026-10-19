"""`export_result`：每个被受理的导出尝试，终局时恰好一条（2026-10-15 引入）。

判据的主语是**尝试**，不是路由：同步 / 后台、成功 / 部分 / 失败 / 取消、`run()` 早退、
SSE 补拉与 `/state` 轮询，都得落在"一个作业 = 最多一条"上；被拒的请求与 conflict 一条都没有。
全部走真实作业流 + 真实 `capture()`（白名单校验也在路上），只把传输层换成收集盒。
"""

from __future__ import annotations

import json
import threading
import time

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exporttelemetry, telemetry


@pytest.fixture
def env(tmp_path, monkeypatch, telemetry_sent):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=None, fill=(0.1, 0.2, 0.8))
    doc.save(figs / "p1.pdf")
    doc.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), telemetry_sent


def _canvas(**over):
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf"],
        "canvas": {
            "page_w_mm": 180,
            "page_h_mm": 120,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 10, "y_mm": 10, "w_mm": 40, "h_mm": 30}
            ],
        },
    }
    spec.update(over)
    return spec


def _settle(box):
    """等所有后台导出线程收尾，再排空遥测队列，返回收到的事件。"""
    for t in list(threading.enumerate()):
        if t.name.startswith("export-"):
            t.join(10)
    assert telemetry.flush(5.0)
    return box


def _results(box):
    return [p for p in _settle(box) if p["event"] == "export_result"]


def _completed(box):
    return [p for p in _settle(box) if p["event"] == "export_completed"]


def _wait_terminal(client, job_id):
    for _ in range(300):
        body = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled", "conflict"):
            return body
        time.sleep(0.02)
    raise AssertionError("作业没有走到终局")


# ------------------------------ 四种终态，同步路 ------------------------------
def test_sync_done_emits_one_result_and_keeps_export_completed(env):
    client, box = env
    resp = client.post("/api/export", json=_canvas(formats=["pdf", "png"], include_report=False))
    assert resp.get_json()["status"] == "done"
    (ev,) = _results(box)
    props = ev["properties"]
    assert props["outcome"] == "done" and props["scope"] == "canvas"
    assert props["pdf"] is True and props["png"] is True and props["eps"] is False
    assert props["delivered"] == 2
    assert "stage" not in props and "error_class" not in props
    assert len(_completed(box)) == 1, "旧事件 export_completed 的发出逻辑不变"


def test_pure_svg_export_reports_only_the_svg_bit(env):
    """格式位记的是**请求**：静态 PDF 面板直出 SVG 按设计失败，svg 位照样是 True。"""
    client, box = env
    resp = client.post("/api/export", json=_canvas(formats=["svg"], include_report=False))
    assert resp.get_json()["status"] == "failed"
    (ev,) = _results(box)
    props = ev["properties"]
    assert props["svg"] is True
    assert [props[k] for k in ("pdf", "png", "eps", "tiff")] == [False] * 4


def test_mixed_formats_report_each_requested_bit(env):
    client, box = env
    resp = client.post("/api/export", json=_canvas(formats=["pdf", "svg"], include_report=False))
    assert resp.get_json()["status"] in ("failed", "partial")
    (ev,) = _results(box)
    props = ev["properties"]
    assert (props["pdf"], props["svg"]) == (True, True)
    assert [props[k] for k in ("png", "eps", "tiff")] == [False] * 3


def test_sync_partial_is_partial_and_old_event_stays(env, monkeypatch):
    client, box = env
    from tavotto.rendercore import job as rc_job

    def fail(*a, **kw):
        raise OSError("PNG 写不出来")

    monkeypatch.setattr(rc_job.raster, "encode_png", fail)
    resp = client.post("/api/export", json=_canvas(formats=["pdf", "png"], ppi=300))
    assert resp.get_json()["status"] == "partial"
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "partial"
    assert ev["properties"]["pdf"] is True and ev["properties"]["png"] is True
    assert ev["properties"]["delivered"] == 1
    assert ev["properties"]["error_class"] == "other"
    assert ev["properties"]["stage"] in exporttelemetry.STAGES
    (old,) = _completed(box)
    assert old["properties"]["pdf"] is True and old["properties"]["png"] is False


def test_sync_failure_after_acceptance_is_reported(env, monkeypatch):
    client, box = env

    def refuse(tmp, dest):
        raise OSError("磁盘满了")

    monkeypatch.setattr(exportjob.atomicio, "publish_file", refuse)
    resp = client.post("/api/export", json=_canvas())
    assert resp.get_json()["status"] == "failed"
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "failed"
    assert ev["properties"]["error_class"] == "environment"
    assert ev["properties"]["delivered"] == 0
    assert _completed(box) == [], "失败的导出不记 export_completed"


def test_image_publish_failure_is_not_blamed_on_the_report_that_follows(env, monkeypatch):
    """图在落盘时坏了、后面的报告照常写成：坏在 publish，不是 report（Codex #1103 P2）。"""
    client, box = env
    real = exportjob.atomicio.publish_file

    def refuse_png(tmp, dest):
        if str(dest).endswith(".png"):
            raise OSError("磁盘满了")
        return real(tmp, dest)

    monkeypatch.setattr(exportjob.atomicio, "publish_file", refuse_png)
    body = client.post(
        "/api/export",
        json=_canvas(formats=["pdf", "png"], ppi=300, include_style_check_report=True),
    ).get_json()
    assert body["status"] == "partial"
    (ev,) = _results(box)
    assert ev["properties"]["with_report"] is True
    assert ev["properties"]["stage"] == "publish"
    assert ev["properties"]["error_class"] == "environment"


def test_early_exit_of_run_still_counts(env, tmp_path, monkeypatch):
    """`run()` 在渲染前就早退（导出目录建不出来）：已受理，所以有终态。"""
    client, box = env
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(m, "project_export_dir", lambda ctx=None: blocker / "sub")
    resp = client.post("/api/export", json=_canvas())
    assert resp.get_json()["status"] == "failed"
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "failed"
    assert ev["properties"]["error_class"] == "environment"
    assert ev["properties"]["stage"] == "prepare"


def test_produce_exception_is_failed(env, monkeypatch):
    client, box = env

    def boom(job, tmp_dir, **kw):
        raise RuntimeError("内部错误 /Users/secret/path")

    monkeypatch.setattr(m, "_export_produce", boom)
    assert client.post("/api/export", json=_canvas()).get_json()["status"] == "failed"
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "failed"
    assert ev["properties"]["error_class"] == "other"
    assert "secret" not in json.dumps(ev)


# ------------------------------ 后台路 / 取消 / 回放 -------------------------
def test_async_done_once_even_if_state_is_polled_many_times(env):
    client, box = env
    job_id = client.post("/api/export/start", json=_canvas()).get_json()["job_id"]
    assert _wait_terminal(client, job_id)["status"] == "done"
    for _ in range(5):  # SSE 断线补拉 = 反复读 /state，不许再触发
        client.get(f"/api/export/state?job_id={job_id}")
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "done"
    assert len(_completed(box)) == 1


def test_async_cancel_emits_exactly_one_cancelled(env, monkeypatch):
    client, box = env
    gate = threading.Event()
    real = m._export_produce

    def slow(job, tmp_dir, **kw):
        gate.wait(5)
        job.check_cancelled()
        return real(job, tmp_dir, **kw)

    monkeypatch.setattr(m, "_export_produce", slow)
    job_id = client.post("/api/export/start", json=_canvas()).get_json()["job_id"]
    assert client.post("/api/export/cancel", json={"job_id": job_id}).get_json()["cancelling"]
    client.post("/api/export/cancel", json={"job_id": job_id})  # 重复取消
    gate.set()
    assert _wait_terminal(client, job_id)["status"] == "cancelled"
    client.post("/api/export/cancel", json={"job_id": job_id})  # 终局之后再取消
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "cancelled"
    assert "error_class" not in ev["properties"]
    assert ev["properties"]["delivered"] == 0
    assert _completed(box) == []


def test_async_failure_is_reported_once(env, monkeypatch):
    client, box = env

    def boom(job, tmp_dir, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(m, "_export_produce", boom)
    job_id = client.post("/api/export/start", json=_canvas()).get_json()["job_id"]
    assert _wait_terminal(client, job_id)["status"] == "failed"
    (ev,) = _results(box)
    assert ev["properties"]["outcome"] == "failed"


# ------------------------------ 不该发的 / 新尝试 ----------------------------
def test_rejected_request_emits_nothing(env):
    client, box = env
    assert client.post("/api/export", json=_canvas(formats=[])).status_code == 400
    assert client.post("/api/export/start", json=_canvas(formats=[])).status_code == 400
    assert _results(box) == []


def test_conflict_emits_nothing_and_the_resubmit_is_the_attempt(env, monkeypatch):
    client, box = env
    # 计数 stub 挡在白名单之前：不然"发了一条会被白名单拒掉的"也会假绿
    attempts = []
    real = telemetry.capture
    monkeypatch.setattr(
        telemetry,
        "capture",
        lambda event, props=None: (attempts.append(event), real(event, props))[1],
    )
    assert client.post("/api/export", json=_canvas()).get_json()["status"] == "done"
    second = client.post("/api/export", json=_canvas()).get_json()
    assert second["status"] == "conflict"
    assert len(_results(box)) == 1, "conflict 是「还没动手」，不是一次尝试的结果"
    assert attempts.count("export_result") == 1, "conflict 连 capture 都不该调"
    third = client.post("/api/export", json=_canvas(overwrite="rename")).get_json()
    assert third["status"] == "done"
    assert [e["properties"]["outcome"] for e in _results(box)] == ["done", "done"]


def test_user_retry_is_a_new_attempt(env, monkeypatch):
    client, box = env
    real = exportjob.atomicio.publish_file
    monkeypatch.setattr(
        exportjob.atomicio, "publish_file", lambda *a: (_ for _ in ()).throw(OSError("full"))
    )
    assert client.post("/api/export", json=_canvas()).get_json()["status"] == "failed"
    monkeypatch.setattr(exportjob.atomicio, "publish_file", real)
    assert client.post("/api/export", json=_canvas()).get_json()["status"] == "done"
    assert [e["properties"]["outcome"] for e in _results(box)] == ["failed", "done"]


def test_no_user_content_in_the_event(env):
    client, box = env
    resp = client.post("/api/export", json=_canvas(filename="保密文件名_Fig1_kinetics"))
    out_dir = resp.get_json()["export_dir"]
    blob = json.dumps(_results(box), ensure_ascii=False)
    assert "保密" not in blob and "kinetics" not in blob and out_dir not in blob


# ------------------------------ 作业层：恰好一次 -----------------------------
def test_emit_notifies_a_terminal_job_at_most_once(tmp_path):
    from tavotto.engine import exportreq

    req = exportreq.normalize(_canvas())
    job = exportjob.ExportJob(id="j1", request=req, export_dir=tmp_path)
    calls = []
    job.on_terminal = calls.append
    job.status = exportjob.STATUS_RUNNING
    exportjob._emit(job, None)
    assert calls == []
    job.status = exportjob.STATUS_DONE
    for _ in range(3):
        exportjob._emit(job, None)
    assert calls == [job]


def test_a_failing_callback_never_breaks_the_job(tmp_path):
    from tavotto.engine import exportreq

    job = exportjob.ExportJob(id="j2", request=exportreq.normalize(_canvas()), export_dir=tmp_path)

    def boom(j):
        raise RuntimeError("telemetry down")

    job.on_terminal = boom
    job.status = exportjob.STATUS_DONE
    exportjob._emit(job, None)  # 不抛


# ------------------------------ 契约 -----------------------------------------
@pytest.mark.parametrize(
    "code,cls",
    [
        ("bad_formats", "request"),
        ("no_format", "request"),
        ("export_dir_unwritable", "environment"),
        ("backend_unavailable", "backend"),
        ("artifact_rejected", "artifact_rejected"),
        ("input_remap_changed", "source_changed"),
        ("report_failed", "report"),
        ("format_failed", "other"),
        ("never_seen_before", "other"),
    ],
)
def test_error_class_is_a_closed_fold(code, cls):
    assert exporttelemetry.error_class(code) == cls
    assert cls in exporttelemetry.ERROR_CLASSES


def test_the_event_table_matches_the_helper_vocabularies():
    spec = telemetry.EVENTS["export_result"]
    assert tuple(spec["stage"]["values"]) == exporttelemetry.STAGES
    assert tuple(spec["error_class"]["values"]) == exporttelemetry.ERROR_CLASSES
    assert tuple(spec["outcome"]["values"]) == exporttelemetry.OUTCOMES
