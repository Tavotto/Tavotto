# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""数据出图 v2 大表的真实进度与取消（ADR 0178 修订·大表·进度；`engine/ai_data_progress.py`、`/api/ai/data-progress/<rid>`）。

判据的主语：

* 「进度单调、到 100%」量的是**轮询线程在读表进行中实际读到的 `fraction` 序列**（不是内部变量）：不减、最后一次是 `done` + 1.0；
* 「取消后什么都没留下」量的是文件系统与进程内登记：候选根目录没有会话目录、`_SESSIONS` 为空、缓存里没有半截的扫描结果；
* 「取消幂等 / 与完成赛跑」量的是取消端点的响应与原请求的响应（以原请求为准）；
* 「小表不变」量的是小表请求之后进度表里没有任何条目、响应与不带请求号时同形。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_progress as progress, ai_data_propose as P
from tests.test_ai_data_propose import (  # noqa: F401 - `project` 是 fixture
    commit,
    needs_worker,
    project,
    propose,
    put,
)

N_ROWS = 300_000


def _big_rows(n=N_ROWS):
    return [["time_s", "signal_mV"]] + [[i * 0.001, round((i % 977) * 0.01, 3)] for i in range(n)]


@pytest.fixture
def big(project, monkeypatch):
    monkeypatch.setattr(adf, "SMALL_ROWS", 1000)  # 30 万行的文件走大表读法（几 MB，读一遍零点几秒）
    progress.reset_for_tests()
    yield project
    progress.reset_for_tests()


def _poll(client, rid):
    return client.get(f"/api/ai/data-progress/{rid}").get_json()


class Watcher(threading.Thread):
    """进行中不停轮询 `GET /api/ai/data-progress/<rid>`，把读到的状态按序存下来。"""

    def __init__(self, m, rid):
        super().__init__(daemon=True)
        self.client, self.rid, self.seen, self.stop = m.app.test_client(), rid, [], False

    def run(self):
        while not self.stop:
            self.seen.append(_poll(self.client, self.rid))
            time.sleep(0.003)


# =========================================================================== 进度登记表本身
def test_the_registry_is_bounded_in_count_and_age_and_hides_small_requests():
    progress.reset_for_tests()
    for i in range(progress.MAX_ENTRIES + 10):
        with progress.track("p", f"rid-{i:06d}", "overview"):
            progress.tick("scan", 1, 2)
    assert len(progress._ENTRIES) <= progress.MAX_ENTRIES
    # 小表请求（从没进过大表阶段）：结束后不留条目，查询也是 none
    with progress.track("p", "small-req-1", "propose"):
        assert progress.snapshot("p", "small-req-1") == {"state": "none"}
    assert ("p", "small-req-1") not in progress._ENTRIES
    # 终态条目过了 TTL 被扫掉
    key = next(iter(progress._ENTRIES))
    progress._ENTRIES[key].updated -= progress.TTL_S + 1
    with progress.track("p", "fresh-req-1", "overview"):
        pass
    assert key not in progress._ENTRIES
    with pytest.raises(ValueError):
        with progress.track("p", "bad rid!", "overview"):
            pass


def test_fraction_is_monotone_per_kind_and_a_second_pass_does_not_move_it_back():
    progress.reset_for_tests()
    seen = []
    with progress.track("p", "rid-mono-01", "propose"):
        for phase, total in (("validate", 200), ("scan", 100), ("stage", 100)):
            for d in range(0, total + 1, 10):
                progress.tick(phase, d, total)
                seen.append(progress.snapshot("p", "rid-mono-01")["fraction"])
        progress.set_passes("reduce", 2)
        for _pass in range(2):  # 缩减读两遍：每遍从 0 起，总进度仍不减
            for d in range(0, 101, 10):
                progress.tick("reduce", d, 100)
                seen.append(progress.snapshot("p", "rid-mono-01")["fraction"])
    assert seen == sorted(seen) and 0 <= seen[0] and seen[-1] < 1.0
    snap = progress.snapshot("p", "rid-mono-01")
    assert snap["state"] == "done" and snap["fraction"] == 1.0 and snap["phase"] == "reduce"


def test_cancel_is_idempotent_scoped_to_the_project_and_checked_at_ticks():
    progress.reset_for_tests()
    assert progress.cancel("p", "nobody-001")["cancelled"] is False  # 不认识的请求
    with pytest.raises(progress.Cancelled):
        with progress.track("p", "rid-cancel-1", "overview"):
            progress.tick("scan", 1, 10)
            assert (
                progress.cancel("other", "rid-cancel-1")["cancelled"] is False
            )  # 别的项目取消不了
            assert progress.cancel("p", "rid-cancel-1")["cancelled"] is True
            assert progress.cancel("p", "rid-cancel-1")["cancelled"] is False  # 重复取消
            progress.tick("scan", 2, 10)  # 下一个检查点：停
    assert progress.snapshot("p", "rid-cancel-1")["state"] == "cancelled"
    assert progress.cancel("p", "rid-cancel-1")["cancelled"] is False  # 已结束的请求
    # 缩略图 / 提交不可取消
    with progress.track("p", "rid-thumb-01", "thumbnail"):
        progress.enter("render")
        out = progress.cancel("p", "rid-thumb-01")
        assert (
            out["cancelled"] is False
            and out["cancellable"] is False
            and out["indeterminate"] is True
        )
        assert out["fraction"] is None
        progress.tick("render", 0, 0)  # 不会因取消抛


def test_cancelled_is_not_swallowed_by_except_exception():
    assert not issubclass(progress.Cancelled, Exception)  # 各处 `except Exception` 吞不掉它


# =========================================================================== 读表本身：进度、取消、缓存
def test_reading_a_large_table_reports_monotone_progress_to_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(adf, "SMALL_ROWS", 1000)
    progress.reset_for_tests()
    p = tmp_path / "a.csv"
    p.write_text("\n".join(",".join(map(str, r)) for r in _big_rows()) + "\n", encoding="utf-8")
    calls: list[tuple[str, int, int]] = []
    real = progress.tick

    def spy(phase, done, total):
        calls.append((phase, done, total))
        real(phase, done, total)

    monkeypatch.setattr(progress, "tick", spy)
    fr = []
    with progress.track("p", "rid-read-001", "overview"):
        info = adf.read_text_table(p, use_cache=True)
        fr.append(progress.snapshot("p", "rid-read-001")["fraction"])
    assert info.status == "ok" and info.summary.large is not None
    phases = [c[0] for c in calls]
    assert (
        phases[0] == "validate"
        and "scan" in phases
        and phases.index("scan") > phases.index("validate")
    )
    for ph in ("validate", "scan"):
        ds = [d for (q, d, _t) in calls if q == ph]
        assert ds == sorted(ds)  # 每个阶段已读字节不减
    size = p.stat().st_size
    assert max(d for (q, d, _t) in calls if q == "validate") == 2 * size
    assert max(d for (q, d, _t) in calls if q == "scan") > 0.8 * size  # 每 65536 行一个检查点
    snap = progress.snapshot("p", "rid-read-001")
    assert snap["state"] == "done" and snap["fraction"] == 1.0
    assert fr[0] is not None and fr[0] <= 0.999


def test_a_cancelled_read_leaves_no_cache_entry_and_the_next_read_is_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(adf, "SMALL_ROWS", 1000)
    progress.reset_for_tests()
    adf._LARGE_CACHE.clear()
    p = tmp_path / "a.csv"
    p.write_text("\n".join(",".join(map(str, r)) for r in _big_rows()) + "\n", encoding="utf-8")
    real = progress.tick

    def cancelling(phase, done, total):
        if phase == "scan":
            progress.cancel("p", "rid-read-002")
        real(phase, done, total)

    monkeypatch.setattr(progress, "tick", cancelling)
    with pytest.raises(progress.Cancelled):
        with progress.track("p", "rid-read-002", "overview"):
            adf.read_text_table(p, use_cache=True)
    assert not adf._LARGE_CACHE  # 半截的扫描结果不进缓存
    assert progress.snapshot("p", "rid-read-002")["state"] == "cancelled"
    monkeypatch.setattr(progress, "tick", real)
    again = adf.read_text_table(p, use_cache=True)
    assert again.status == "ok" and again.summary.row_count == N_ROWS


def test_profile_folder_does_not_swallow_a_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(adf, "SMALL_ROWS", 1000)
    (tmp_path / "a.csv").write_text(
        "\n".join(",".join(map(str, r)) for r in _big_rows()) + "\n", encoding="utf-8"
    )
    real = progress.tick

    def cancelling(phase, done, total):
        progress.cancel("p", "rid-prof-001")
        real(phase, done, total)

    monkeypatch.setattr(progress, "tick", cancelling)
    with pytest.raises(progress.Cancelled):
        with progress.track("p", "rid-prof-001", "propose"):
            adf.profile_folder(tmp_path)  # 内部 `except Exception` 不许把取消当成「这个文件读不了」


# =========================================================================== HTTP：进度、取消、清理、赛跑
def _start_propose(m, rid, **body):
    out = {}

    def run():
        c = m.app.test_client()
        r = propose(c, client_request_id=rid, render="lazy", answers={"y": "signal_mV"}, **body)
        out["status"], out["body"] = r.status_code, r.get_json()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def test_progress_endpoint_is_guarded_and_validates_the_request_id(big):
    m, client, figs, state = big
    assert client.get("/api/ai/data-progress/short").status_code == 400
    assert client.post("/api/ai/data-progress/short/cancel").status_code == 400
    assert _poll(client, "rid-none-001") == {"state": "none"}
    state["taf"] = False
    assert client.get("/api/ai/data-progress/rid-none-001").status_code == 404
    assert client.post("/api/ai/data-progress/rid-none-001/cancel").status_code == 404


def test_propose_progress_is_visible_and_reaches_done(big):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    w = Watcher(m, "rid-prop-0001")
    w.start()
    t, out = _start_propose(m, "rid-prop-0001")
    t.join(120)
    w.stop = True
    w.join(5)
    assert out["status"] == 200 and out["body"]["kind"] == "candidates", out
    running = [s for s in w.seen if s["state"] == "running"]
    assert running, "轮询期间没读到进行中的进度"
    fr = [s["fraction"] for s in running if s["fraction"] is not None]
    assert fr == sorted(fr)
    assert {s["phase"] for s in running} <= {"validate", "scan", "stage", "reduce"}
    assert all(s["cancellable"] for s in running)
    final = _poll(client, "rid-prop-0001")
    assert final["state"] == "done" and final["fraction"] == 1.0 and final["cancellable"] is False
    assert w.seen and [s["state"] for s in w.seen if s["state"] != "none"][-1] in (
        "running",
        "done",
    )


def test_cancelling_propose_stops_it_and_leaves_nothing_behind(big, monkeypatch):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    real = progress.tick
    hit = {"n": 0}

    def cancel_at_stage(phase, done, total):
        if phase == "stage":  # 复制候选副本的第一块之后取消：目录与半份副本都已经在盘上
            hit["n"] += 1
            progress.cancel(str(figs.resolve()), "rid-canc-0001")
        real(phase, done, total)

    monkeypatch.setattr(progress, "tick", cancel_at_stage)
    adf._LARGE_CACHE.clear()
    r = propose(
        client, client_request_id="rid-canc-0001", render="lazy", answers={"y": "signal_mV"}
    )
    assert hit["n"] >= 1
    assert r.status_code == 409 and r.get_json()["code"] == "propose_cancelled"
    root = P.CANDIDATE_ROOT
    assert not root.exists() or list(root.iterdir()) == []  # 候选目录（含半份大表副本）整个删了
    assert P._SESSIONS == {} and P._REQUESTS == {}  # 没有会话，也没有被记住的幂等响应
    snap = _poll(client, "rid-canc-0001")
    assert snap["state"] == "cancelled" and snap["cancellable"] is False
    out = client.post("/api/ai/data-progress/rid-canc-0001/cancel").get_json()
    assert out["cancelled"] is False and out["state"] == "cancelled"  # 重复取消幂等
    monkeypatch.setattr(progress, "tick", real)
    again = propose(
        client, client_request_id="rid-canc-0002", render="lazy", answers={"y": "signal_mV"}
    )
    assert again.get_json()["kind"] == "candidates"  # 取消之后同一份数据照常能出备选


def test_cancel_during_the_scan_of_overview_returns_409_and_caches_nothing(big, monkeypatch):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    adf._LARGE_CACHE.clear()
    real = progress.tick

    def cancel_at_scan(phase, done, total):
        if phase == "scan":
            progress.cancel(str(figs.resolve()), "rid-ov-00001")
        real(phase, done, total)

    monkeypatch.setattr(progress, "tick", cancel_at_scan)
    r = client.get(
        "/api/ai/data-overview", query_string={"path": "data/run.csv", "request_id": "rid-ov-00001"}
    )
    assert r.status_code == 409 and r.get_json()["code"] == "propose_cancelled"
    assert not adf._LARGE_CACHE
    monkeypatch.setattr(progress, "tick", real)
    ok = client.get(
        "/api/ai/data-overview", query_string={"path": "data/run.csv", "request_id": "rid-ov-00002"}
    )
    body = ok.get_json()
    assert (
        ok.status_code == 200
        and body["status"] == "ok"
        and body["large"] is True
        and body["rows"] == N_ROWS
    )


def test_a_cancel_that_arrives_after_completion_changes_nothing(big):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    r = propose(client, client_request_id="rid-race-001", render="lazy", answers={"y": "signal_mV"})
    body = r.get_json()
    assert r.status_code == 200 and body["kind"] == "candidates"
    out = client.post("/api/ai/data-progress/rid-race-001/cancel").get_json()
    assert out["cancelled"] is False and out["state"] == "done"
    assert P.get_session(str(figs.resolve()), body["session"])  # 会话与候选没被动


def test_a_cancel_racing_with_the_last_checkpoint_never_corrupts_state(big):
    # 取消线程与读表线程赛跑：不管谁先，结局只能是「409 + 什么都没留下」或「200 + 完整结果」，不会是半截
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    for k in range(4):
        rid = f"rid-race-1{k:03d}"
        adf._LARGE_CACHE.clear()
        t, out = _start_propose(m, rid)
        time.sleep(0.02 * k)
        c = m.app.test_client()
        c.post(f"/api/ai/data-progress/{rid}/cancel")
        t.join(120)
        if out["status"] == 409:
            assert out["body"]["code"] == "propose_cancelled"
            assert _poll(client, rid)["state"] in ("cancelled", "none")
        else:
            assert out["status"] == 200 and out["body"]["kind"] == "candidates"
        P.reset_for_tests()


def test_small_tables_leave_no_progress_entries_and_the_same_response_shape(project):
    m, client, figs, _ = project
    progress.reset_for_tests()
    rows = [["time_s", "signal_mV"]] + [[i, i * 0.5] for i in range(40)]
    put(figs, "data/s.csv", rows)
    a = propose(
        client, client_request_id="rid-small-01", render="lazy", answers={"y": "signal_mV"}
    ).get_json()
    b = propose(client, render="lazy", answers={"y": "signal_mV"}).get_json()
    assert a["kind"] == b["kind"] == "candidates"
    assert set(a) == set(b) and [set(i) for i in a["items"]] == [set(i) for i in b["items"]]
    assert "reduced" not in a["items"][0] and "large" not in a
    assert progress._ENTRIES == {}
    assert _poll(client, "rid-small-01") == {"state": "none"}
    ov = client.get(
        "/api/ai/data-overview", query_string={"path": "data/s.csv", "request_id": "rid-small-02"}
    ).get_json()
    assert "large" not in ov and progress._ENTRIES == {}
    listing = client.get("/api/ai/data-files?formats=all").get_json()["files"][0]
    assert "large" not in listing["table"]


def test_the_data_files_listing_marks_large_tables_it_has_read(big):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows())
    row = client.get("/api/ai/data-files?formats=all").get_json()["files"][0]
    assert row["table"]["status"] == "ok" and row["table"]["large"] is True


@needs_worker
def test_commit_reports_an_indeterminate_render_phase_and_cannot_be_cancelled(big):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _big_rows(60_000))
    body = propose(client, render="lazy", answers={"y": "signal_mV"}).get_json()
    assert body["kind"] == "candidates"
    item = next(i for i in body["items"] if i["title_code"] == "line_points")
    assert client.get(item["thumbnail_url"] + "?request_id=rid-thum-0001").status_code == 200
    w = Watcher(m, "rid-comm-0001")
    w.start()
    r = client.post(
        "/api/ai/data-figure/commit",
        json={"session": body["session"], "id": item["id"], "client_request_id": "rid-comm-0001"},
    )
    w.stop = True
    w.join(5)
    assert r.status_code == 200 and r.get_json()["data_check"] == "verified"
    running = [s for s in w.seen if s["state"] == "running"]
    assert all(s["kind"] == "commit" and s["cancellable"] is False for s in running)
    render = [s for s in running if s["phase"] == "render"]
    assert render and all(s["fraction"] is None and s["indeterminate"] for s in render)
    assert (
        client.post("/api/ai/data-progress/rid-comm-0001/cancel").get_json()["cancelled"] is False
    )
    assert _poll(client, "rid-comm-0001")["state"] == "done"
    assert Path(figs).exists()
