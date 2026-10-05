"""T04：把「失败的那一次」的现场带进诊断（`engine/taskdiag.py` + `/api/diagnostics/task`）。

主语是**指定的那一次尝试**（导出作业 / 准备尝试 / 脚本试运行），不是此刻的整台机器：

* C13/C14：失败后改设置重试成功，失败那份快照仍描述第一次（scope / revision / stage / outputs 各是各的）；
* C15：取快照不执行任何工作——没有解释器探测、没有安装、没有联网、不重跑脚本；
* C16：记录按项目认领；别的项目的 id、过期的 id、从未有过的 id 读作「没有」；
* C17：秘密 sentinel 及其常见编码 / 摘要在快照、端点响应、诊断包里都不出现——靠白名单结构，不靠过滤；
* C18：条数 / 单条字节 / 总字节 / 保留期有界，截断明确标出；活跃任务不被清理影响。
"""

# ruff: noqa: F811 — 夹具从兄弟测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import time
import urllib.parse
import zipfile

import pytest

from tavotto import app as m
from tavotto.engine import (
    deprepair,
    exportjob,
    pool as engine_pool,
    preparation,
    prepsession,
    taskdiag,
)
from test_export_pipeline import _canvas, _original, env  # noqa: F401
from test_preparation_api import (  # noqa: F401
    _build_resp,
    _FakeWorker,
    _open,
    _project,
    client as prep_client,
    fake_pool,
)

TASK = "/api/diagnostics/task"
REV1, REV2 = "a1b2c3d4e5f60718", "0f1e2d3c4b5a6978"
SECRET = "Zq9-sentinel-Patient7731xK"


def _variants(secret: str) -> set[str]:
    raw = secret.encode()
    b64 = base64.b64encode(raw).decode()
    out = {
        secret,
        secret.lower(),
        raw.hex(),
        b64,
        b64.rstrip("="),
        base64.urlsafe_b64encode(raw).decode().rstrip("="),
        urllib.parse.quote(secret, safe=""),
    }
    for algo in ("md5", "sha1", "sha256"):
        out.add(hashlib.new(algo, raw).hexdigest())
    return out


def assert_no_secret(text: str, secret: str = SECRET) -> None:
    for v in _variants(secret):
        assert v not in text, f"{v!r} 出现在输出里"


def _get(client, kind=None, ref=None, pj=None):
    qs = {k: v for k, v in (("kind", kind), ("ref", ref), ("pj", pj)) if v}
    return client.get(TASK, query_string=qs)


def _bundle_files(client, pj=None) -> dict[str, bytes]:
    res = client.get("/api/diagnostics/bundle", query_string={"pj": pj} if pj else {})
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.data))
    return {n: z.read(n) for n in z.namelist()}


@pytest.fixture(autouse=True)
def _clean_store():
    taskdiag.reset_for_tests()
    yield
    taskdiag.reset_for_tests()


# ===================================================================== 登记表本身（C18）
def _proj(i: int = 0, **extra) -> dict:
    return {
        "snapshot_version": 1,
        "kind": "export",
        "attempt_id": f"j{i}",
        "outcome": "failed",
        **extra,
    }


def test_shape_guards_drop_instead_of_hash():
    assert taskdiag.code("export_failed") == "export_failed"
    assert taskdiag.code("Has Space") is None and taskdiag.code(f"x/{SECRET}") is None
    assert taskdiag.ident("prep-" + "a" * 32) and taskdiag.ident("/etc/passwd") is None
    assert taskdiag.digest("deadbeef12345678") and taskdiag.digest("not hex!") is None
    assert (
        taskdiag.closed("pdf", ("pdf", "png")) == "pdf"
        and taskdiag.closed(SECRET, ("pdf",)) is None
    )
    assert taskdiag.count(True) is None and taskdiag.count(-1) is None and taskdiag.count(3) == 3
    assert taskdiag.version("3.13.1") == "3.13.1" and taskdiag.version(f"3.{SECRET}") is None


def test_stages_projection_never_reads_trace_facts():
    from tavotto.engine import trace

    tr = trace.Trace()
    tr.mark("prepare", filename=SECRET, path=f"/home/{SECRET}")
    tr.fail("compose", "export_failed", detail=SECRET)
    out = taskdiag.stages(tr.to_payload(), trace.PHASES)
    assert_no_secret(json.dumps(out))
    assert out["failed_phase"] == "compose" and out["failed_code"] == "export_failed"
    assert [e["phase"] for e in out["events"]] == ["prepare", "compose"]


def test_oversized_projection_is_truncated_and_says_so():
    big = _proj(
        stages={"events": [{"phase": "prepare", "at_ms": i, "outcome": "ok"} for i in range(400)]}
    )
    text, cut = taskdiag.fit(big, 2048)
    assert len(text) <= 2048 and "stages" in cut
    doc = json.loads(text)
    assert doc["truncated_fields"] and doc["stages"]["omitted"] > 0
    # 头尾留着：哪里开始、哪里坏
    assert doc["stages"]["events"][0]["at_ms"] == 0 and doc["stages"]["events"][-1]["at_ms"] == 399


def test_a_huge_unfittable_projection_collapses_to_its_core_with_a_flag():
    huge = _proj(extra_blob="x" * 100_000, target={"category": "canvas"})
    text, cut = taskdiag.fit(huge, 1024)
    doc = json.loads(text)
    assert len(text) <= 1024 and doc["truncated"] is True and "extra_blob" in cut
    assert doc["attempt_id"] == "j0" and "extra_blob" not in doc


def test_snapshots_are_immutable_first_write_wins():
    s = taskdiag.Store()
    a = s.record("pj", "export", "j1", _proj(1), outcome="failed", failed=True)
    b = s.record("pj", "export", "j1", _proj(1, outcome="done"), outcome="done", failed=False)
    assert b is a and json.loads(s.get("pj", "export", "j1").entry.blob)["outcome"] == "failed"


def test_entry_count_total_bytes_and_retention_are_bounded():
    s = taskdiag.Store(max_entries=5, max_total_bytes=10_000, retention_s=100)
    t0 = time.time()
    for i in range(12):
        s.record("pj", "export", f"j{i}", _proj(i), outcome="failed", failed=True, now=t0 + i)
    assert s.stats()["entries"] == 5
    s2 = taskdiag.Store(max_entries=100, max_entry_bytes=600, max_total_bytes=1500)
    for i in range(20):
        s2.record("pj", "export", f"j{i}", _proj(i, pad="y" * 300), outcome="failed", failed=True)
    assert s2.stats()["bytes"] <= 1500
    s3 = taskdiag.Store(retention_s=50)
    s3.record("pj", "export", "old", _proj(), outcome="failed", failed=True, now=100)
    assert s3.get("pj", "export", "old", now=120).entry is not None
    lost = s3.get("pj", "export", "old", now=200)
    assert lost.entry is None and lost.reason == taskdiag.REASON_EXPIRED


def test_eviction_keeps_failures_over_successes():
    s = taskdiag.Store(max_entries=3)
    t0 = time.time()
    s.record("pj", "export", "bad", _proj(), outcome="failed", failed=True, now=t0)
    for i in range(5):
        s.record(
            "pj",
            "export",
            f"ok{i}",
            _proj(i, outcome="done"),
            outcome="done",
            failed=False,
            now=t0 + 1 + i,
        )
    assert s.get("pj", "export", "bad").entry is not None


def test_expired_vs_never_existed_is_only_visible_inside_the_owning_project():
    s = taskdiag.Store(max_entries=1)
    t0 = time.time()
    s.record("A", "export", "j1", _proj(1), outcome="failed", failed=True, now=t0)
    s.record("A", "export", "j2", _proj(2), outcome="failed", failed=True, now=t0 + 1)
    assert s.get("A", "export", "j1").reason == taskdiag.REASON_EXPIRED
    # 别的项目问同一个 id：读作「从来没有」，不暴露它在 A 里存在过
    assert s.get("B", "export", "j1").reason == taskdiag.REASON_NOT_FOUND
    assert s.get("B", "export", "j2").entry is None


def test_retry_link_is_written_forward_and_never_rewrites_the_old_failure():
    s = taskdiag.Store()
    t0 = time.time()
    subj = {("doc", "d1")}
    first = s.record(
        "pj", "export", "j1", _proj(1), outcome="partial", failed=True, subject=subj, now=t0
    )
    blob_before = first.blob
    second = s.record(
        "pj",
        "export",
        "j2",
        _proj(2, outcome="done"),
        outcome="done",
        failed=False,
        subject=subj,
        now=t0 + 1,
    )
    assert second.retry_of == "j1" and first.retry_of is None
    assert s.get("pj", "export", "j1").entry.blob == blob_before
    assert s.later_attempts(first) == [
        {"ref": "j2", "outcome": "done", "recorded_at": round(t0 + 1, 3)}
    ]
    # 别的文档不是重试
    other = s.record(
        "pj",
        "export",
        "j3",
        _proj(3),
        outcome="failed",
        failed=True,
        subject={("doc", "d2")},
        now=t0 + 2,
    )
    assert other.retry_of is None


# ===================================================================== 导出（C13 / C14）
def _open_two(client, env_figs, tmp_path):
    a = _open(client, env_figs)
    other = tmp_path / "other_project"
    other.mkdir()
    b = _open(client, other)
    return a, b


def _export(client, spec, pj):
    resp = client.post("/api/export", json=spec, query_string={"pj": pj})
    return resp.status_code, resp.get_json()


def test_a_failed_export_keeps_its_own_facts_after_a_successful_retry(env, tmp_path):
    """F4：画布范围要 EPS（注定 partial，坏在 publish）→ 改成原图范围 PDF 成功。两份快照各描述各的。"""
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)
    s1, first = _export(
        client,
        _canvas(
            filename="Secret-Paper-Title",
            formats=["pdf", "eps"],
            document_id="doc-1",
            document_revision=REV1,
        ),
        pj,
    )
    assert first["status"] == "partial", first
    s2, second = _export(
        client,
        _original(
            filename="Secret-Paper-Title",
            formats=["pdf"],
            document_id="doc-1",
            document_revision=REV2,
            overwrite="replace",
        ),
        pj,
    )
    assert second["status"] == "done", second

    one = _get(client, "export", first["job_id"], pj)
    assert one.status_code == 200, one.get_json()
    doc = one.get_json()
    snap = doc["snapshot"]
    assert snap["outcome"] == "partial" and snap["attempt_id"] == first["job_id"]
    assert snap["request"]["scope"] == "canvas" and snap["request"]["formats"] == ["pdf", "eps"]
    assert snap["target"] == {"category": "canvas"}
    assert snap["stages"]["failed_phase"] and snap["stages"]["failed_code"] == "eps_not_for_canvas"
    by_format = {o["format"]: o for o in snap["outputs"]}
    assert by_format["pdf"]["status"] == "done" and by_format["pdf"]["vector"] is True
    assert (
        by_format["eps"]["status"] == "failed"
        and by_format["eps"]["error_code"] == "eps_not_for_canvas"
    )
    assert snap["request"]["document_revision"] == REV1
    assert doc["retry_of"] is None
    assert doc["later_attempts"] == [
        {
            "ref": second["job_id"],
            "outcome": "done",
            "recorded_at": doc["later_attempts"][0]["recorded_at"],
        }
    ]
    # 第二次是另一份快照：范围、结果各是各的，且指回第一次
    two = _get(client, "export", second["job_id"], pj).get_json()
    assert (
        two["snapshot"]["request"]["scope"] == "original" and two["snapshot"]["outcome"] == "done"
    )
    assert two["snapshot"]["request"]["document_revision"] == REV2
    assert "error" not in two["snapshot"]
    assert two["retry_of"] == first["job_id"]
    # 现场声明：这是冻结的快照，不是此刻采集
    assert (
        doc["collection"]["current_state_included"] is False
        and doc["collection"]["executed_anything"] is False
    )
    # 结构性排除：文件名、导出目录、项目路径一个字都不在
    text = json.dumps(doc)
    for banned in ("Secret-Paper-Title", str(figs), str(tmp_path), "doc-1", "exports"):
        assert banned not in text


def test_without_a_ref_the_latest_failure_of_this_project_is_picked(env, tmp_path):
    client, figs = env
    pj, other = _open_two(client, figs, tmp_path)
    _, bad = _export(client, _canvas(formats=["pdf", "eps"]), pj)
    _, good = _export(client, _original(), pj)
    got = _get(client, "export", None, pj).get_json()
    assert got["snapshot"]["attempt_id"] == bad["job_id"]
    nothing = _get(client, "export", None, other)
    assert nothing.status_code == 404 and nothing.get_json()["available"] is False


def test_an_unclassified_exception_text_and_a_huge_error_never_reach_the_snapshot(
    env, tmp_path, monkeypatch
):
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)

    def boom(*a, **k):
        raise RuntimeError(f"{SECRET} {'z' * 2_000_000}")

    monkeypatch.setattr(m, "_export_produce", boom)
    status, body = _export(client, _canvas(filename=f"name-{SECRET[:8]}"), pj)
    assert status == 500 and body["status"] == "failed"
    res = _get(client, "export", body["job_id"], pj)
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert_no_secret(text)
    assert "zzzzzzzz" not in text
    assert len(text) < taskdiag.MAX_DOCUMENT_BYTES
    snap = res.get_json()["snapshot"]
    assert snap["error"]["code"] == "export_failed" and snap["outcome"] == "failed"
    assert snap["stages"]["failed_phase"]


def test_a_cancelled_export_is_recorded_as_cancelled_not_failed(env, tmp_path):
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)
    gate, started = threading.Event(), threading.Event()
    real = m._export_produce

    def slow(job, tmp, **kw):
        started.set()
        gate.wait(10)
        job.check_cancelled()
        return real(job, tmp, **kw)

    m._export_produce, saved = slow, m._export_produce
    try:
        resp = client.post("/api/export/start", json=_original(), query_string={"pj": pj})
        job_id = resp.get_json()["job_id"]
        assert started.wait(10)
        # 还在跑：没有终局快照——读作「没有」，也不会生成一份当前状态
        assert _get(client, "export", job_id, pj).status_code == 404
        assert client.post(
            "/api/export/cancel", json={"job_id": job_id}, query_string={"pj": pj}
        ).get_json()["cancelling"]
        gate.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            got = _get(client, "export", job_id, pj)
            if got.status_code == 200:
                break
            time.sleep(0.02)
        snap = got.get_json()["snapshot"]
        assert snap["outcome"] == "cancelled" and snap["cancel_requested"] is True
        assert got.get_json()["snapshot"]["stages"]["failed_phase"]  # 取消停在哪一步也留着
    finally:
        m._export_produce = saved


def test_active_jobs_are_not_touched_by_cleanup_pressure(env, tmp_path):
    """登记表被塞满、逐出老记录时，在跑的作业不受影响，终局时照常写入。"""
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)
    gate, started = threading.Event(), threading.Event()
    real = m._export_produce

    def slow(job, tmp, **kw):
        started.set()
        gate.wait(10)
        return real(job, tmp, **kw)

    m._export_produce, saved = slow, m._export_produce
    try:
        job_id = client.post(
            "/api/export/start", json=_original(), query_string={"pj": pj}
        ).get_json()["job_id"]
        assert started.wait(10)
        for i in range(taskdiag.MAX_ENTRIES + 20):
            taskdiag.STORE.record(
                pj, "export", f"filler{i}", _proj(i), outcome="failed", failed=True
            )
        assert taskdiag.STORE.stats()["entries"] <= taskdiag.MAX_ENTRIES
        assert exportjob.get(job_id).status == exportjob.STATUS_RUNNING
        gate.set()
        deadline = time.time() + 10
        while exportjob.get(job_id).status == exportjob.STATUS_RUNNING and time.time() < deadline:
            time.sleep(0.02)
        assert _get(client, "export", job_id, pj).status_code == 200
    finally:
        m._export_produce = saved


# ===================================================================== 认领（C16）
def test_other_projects_cannot_read_a_job_or_tell_it_exists(env, tmp_path):
    client, figs = env
    a, b = _open_two(client, figs, tmp_path)
    _, job = _export(client, _canvas(formats=["pdf", "eps"]), a)
    ref = job["job_id"]
    assert _get(client, "export", ref, a).status_code == 200
    for pj in (b, None):
        res = _get(client, "export", ref, pj)
        assert res.status_code == 404
        body = res.get_json()
        assert body["available"] is False and body["reason"] == "not_found"
        assert ref not in res.get_data(as_text=True)
    # 与一个真的不存在的 id 无法区分
    ghost = _get(client, "export", "ffffffffffffffff", b)
    assert ghost.get_json() == _get(client, "export", ref, b).get_json()
    # 旧的状态端点也不再跨项目泄露请求与导出目录
    mine = client.get("/api/export/state", query_string={"job_id": ref, "pj": a}).get_json()
    theirs = client.get("/api/export/state", query_string={"job_id": ref, "pj": b}).get_json()
    assert mine["status"] == "partial" and theirs == {"job_id": ref, "status": "unknown"}


def test_another_project_cannot_cancel_a_running_job(env, tmp_path):
    """取消要在**还在跑**的作业上验（已终局的作业无论如何都取消不了，那样的断言是空的）。"""
    client, figs = env
    a, b = _open_two(client, figs, tmp_path)
    gate, started = threading.Event(), threading.Event()
    real = m._export_produce

    def slow(job, tmp, **kw):
        started.set()
        gate.wait(10)
        return real(job, tmp, **kw)

    m._export_produce, saved = slow, m._export_produce
    try:
        job_id = client.post(
            "/api/export/start", json=_original(), query_string={"pj": a}
        ).get_json()["job_id"]
        assert started.wait(10)
        theirs = client.post("/api/export/cancel", json={"job_id": job_id}, query_string={"pj": b})
        assert theirs.get_json() == {"cancelling": False}
        assert exportjob.get(job_id).cancelled is False
        gate.set()
        deadline = time.time() + 10
        while exportjob.get(job_id).status == exportjob.STATUS_RUNNING and time.time() < deadline:
            time.sleep(0.02)
        assert exportjob.get(job_id).status == exportjob.STATUS_DONE
    finally:
        gate.set()
        m._export_produce = saved


def test_bad_kind_is_rejected_and_unknown_ref_is_a_clear_not_found(env, tmp_path):
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)
    assert _get(client, "nonsense", "x", pj).status_code == 400
    res = _get(client, "export", "0123456789abcdef", pj)
    assert res.status_code == 404 and res.get_json()["reason"] == "not_found"


def test_an_evicted_record_says_expired_in_its_own_project_only(env, tmp_path):
    client, figs = env
    a, b = _open_two(client, figs, tmp_path)
    _, job = _export(client, _canvas(formats=["pdf", "eps"]), a)
    for i in range(taskdiag.MAX_ENTRIES + 5):
        taskdiag.STORE.record(a, "export", f"f{i}", _proj(i), outcome="failed", failed=True)
    mine = _get(client, "export", job["job_id"], a)
    assert mine.status_code == 404 and mine.get_json()["reason"] == "expired"
    # 完整状态对象的人看不见「它存在过」
    assert _get(client, "export", job["job_id"], b).get_json()["reason"] == "not_found"


# ===================================================================== 不执行任何工作（C15）
def test_collecting_a_task_diagnostic_executes_nothing(env, tmp_path, monkeypatch):
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)
    _, job = _export(client, _canvas(formats=["pdf", "eps"]), pj)
    hits: list[str] = []

    def tripwire(name):
        def f(*a, **k):
            hits.append(name)
            raise AssertionError(f"诊断采集不许触发 {name}")

        return f

    import subprocess
    import urllib.request

    from tavotto.engine import diagnostics as d, projectenv

    for obj, attr in (
        (engine_pool, "resolve_worker_python"),
        (engine_pool, "build"),
        (engine_pool, "build_owned"),
        (engine_pool, "get"),
        (projectenv, "probe_environment"),
        (deprepair, "decide_environment"),
        (deprepair, "gate"),
        (d, "build_report"),
        (subprocess, "run"),
        (subprocess, "Popen"),
        (urllib.request, "urlopen"),
    ):
        if hasattr(obj, attr):
            monkeypatch.setattr(obj, attr, tripwire(f"{obj.__name__}.{attr}"))
    res = _get(client, "export", job["job_id"], pj)
    assert res.status_code == 200, res.get_json()
    assert _get(client, "export", None, pj).status_code == 200
    assert hits == []


# ===================================================================== 准备尝试（T01 会话）
@pytest.fixture
def sessions(prep_client, monkeypatch):
    prepsession.SESSIONS.reset_for_tests()
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair, "gate", lambda root, script: None)
    monkeypatch.setattr(
        deprepair, "preparation_offer", lambda root, script: {"plan": {"status": "nothing_needed"}}
    )
    prepsession.SESSIONS.notifier = None
    yield
    prepsession.SESSIONS.reset_for_tests()
    prepsession.SESSIONS.notifier = m._publish_preparation_session


SESSIONS = "/api/engine/preparation-sessions"


@pytest.fixture
def run_aware_pool(fake_pool, monkeypatch):
    """T03 之后 `pool.peek` / `force_cancel` 会带 `run=`；兄弟文件的替身还没认这个关键字。"""
    monkeypatch.setattr(engine_pool, "peek", lambda script, root, **kw: fake_pool["peek"])
    monkeypatch.setattr(
        engine_pool,
        "force_cancel",
        lambda script, root, **kw: fake_pool["force_cancel"].append((script, root)),
    )
    return fake_pool


def _run_session(client, pj, body, timeout=10.0):
    created = client.post(SESSIONS, json=body, query_string={"pj": pj})
    assert created.status_code in (200, 201), created.get_json()
    report = created.get_json()
    sid = report["session_id"]
    run = next(a for a in report["actions"] if a["kind"] == "run")
    act = client.post(
        f"{SESSIONS}/{sid}/actions",
        json={"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        query_string={"pj": pj},
    )
    assert act.status_code in (200, 202), act.get_json()
    deadline = time.time() + timeout
    while True:
        report = client.get(f"{SESSIONS}/{sid}", query_string={"pj": pj}).get_json()
        if report["phase"] not in ("running", "awaiting_runtime_input"):
            return report
        assert time.time() < deadline, report
        time.sleep(0.02)


def test_a_failed_script_attempt_is_frozen_and_a_later_success_does_not_rewrite_it(
    prep_client, tmp_path, run_aware_pool, sessions
):
    client = prep_client
    fake_pool = run_aware_pool
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    fake_pool["error"] = engine_pool.WorkerError(
        f"script blew up: {SECRET}",
        code="script_error",
        traceback_text=f"Traceback ...\nValueError: {SECRET}",
    )
    failed = _run_session(client, pj, {"script": "fig.py", "argv": ["--token", SECRET]})
    assert failed["outcome"]["kind"] != "completed", failed
    ref1 = failed["provider"]["attempt_id"]

    fake_pool["error"] = None
    ok = _run_session(client, pj, {"script": "fig.py"})
    ref2 = ok["provider"]["attempt_id"]
    assert ref1 != ref2

    res = _get(client, "preparation", ref1, pj)
    assert res.status_code == 200, res.get_json()
    text = res.get_data(as_text=True)
    assert_no_secret(text)
    snap = res.get_json()["snapshot"]
    assert snap["outcome"] == "error" and snap["attempt_id"] == ref1
    assert snap["error"]["code"] == "script_error"
    assert (
        snap["stages"]["failed_phase"] == "execute"
        and snap["stages"]["failed_code"] == "script_error"
    )
    assert snap["config"]["argv_count"] == 2 and snap["config"]["run_config"].startswith("rc_")
    assert snap["target"]["category"] == "script"
    # 计划那一刻的环境事实（不是此刻再去探测）
    assert snap["environment_at_plan"]["source"] == "system"
    assert "fig.py" not in text and str(root) not in text and "/envs/fake" not in text
    later = res.get_json()["later_attempts"]
    assert [x["ref"] for x in later] == [ref2] and later[0]["outcome"] == "ready"
    again = _get(client, "preparation", ref2, pj).get_json()
    assert again["retry_of"] == ref1 and again["snapshot"]["outcome"] == "ready"
    assert "error" not in again["snapshot"]
    # 旧失败没有被后来的成功改写
    assert _get(client, "preparation", ref1, pj).get_json()["snapshot"] == snap


def test_a_cancelled_preparation_is_recorded_with_the_cancel_fact(
    prep_client, tmp_path, run_aware_pool, sessions
):
    client = prep_client
    fake_pool = run_aware_pool
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    fake_pool["gate"].clear()
    created = client.post(SESSIONS, json={"script": "fig.py"}, query_string={"pj": pj}).get_json()
    sid = created["session_id"]
    run = next(a for a in created["actions"] if a["kind"] == "run")
    client.post(
        f"{SESSIONS}/{sid}/actions",
        json={"action_id": run["id"], "expected_config_revision": created["config_revision"]},
        query_string={"pj": pj},
    )
    deadline = time.time() + 10
    while fake_pool["build_calls"] == 0 and time.time() < deadline:
        time.sleep(0.01)
    report = client.get(f"{SESSIONS}/{sid}", query_string={"pj": pj}).get_json()
    cancel = next(a for a in report["actions"] if a["kind"] == "cancel")
    client.post(
        f"{SESSIONS}/{sid}/actions",
        json={"action_id": cancel["id"], "expected_config_revision": report["config_revision"]},
        query_string={"pj": pj},
    )
    fake_pool["gate"].set()
    ref = report["provider"]["attempt_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        got = _get(client, "preparation", ref, pj)
        if got.status_code == 200:
            break
        time.sleep(0.02)
    snap = got.get_json()["snapshot"]
    assert snap["outcome"] == "cancelled" and snap["cancel_requested"] is True


def test_a_stale_plan_failure_keeps_its_closed_reason(
    prep_client, tmp_path, run_aware_pool, sessions, monkeypatch
):
    client = prep_client
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    calls = {"n": 0}

    def stale(plan):
        # 第 1 次是会话认领动作前的复核（通过），第 2 次是执行线程起会话前的（作废）
        calls["n"] += 1
        return (preparation.STALE_GRANT, {}) if calls["n"] >= 2 else None

    monkeypatch.setattr(preparation.PreparationService, "_stale_reason", staticmethod(stale))
    report = _run_session(client, pj, {"script": "fig.py"})
    got = _get(client, "preparation", report["provider"]["attempt_id"], pj)
    assert got.status_code == 200, got.get_json()
    snap = got.get_json()["snapshot"]
    assert snap["outcome"] == "error"
    assert snap["error"] == {
        "code": "preparation_plan_stale",
        "reason": preparation.STALE_GRANT,
        "executed": False,
    }
    assert snap["stages"]["failed_phase"] == "check"
    assert run_aware_pool["build_calls"] == 0


# ===================================================================== 脚本试运行
def test_a_failed_probe_gets_a_reference_and_a_frozen_snapshot(prep_client, tmp_path, monkeypatch):
    client = prep_client
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    monkeypatch.setattr(
        m.engine_probe,
        "probe_and_register",
        lambda *a, **k: {
            "registered": False,
            "stems": [],
            "entry": "main",
            "error": {
                "code": "script_probe_failed",
                "message": f"boom {SECRET}",
                "params": {"detail": SECRET},
                "traceback": f'File "x.py"\nValueError: {SECRET}',
            },
        },
    )
    resp = client.post(
        "/api/registry/probe",
        json={"script": "fig.py", "argv": ["--key", SECRET], "argv_sensitive": True},
        query_string={"pj": pj},
    )
    ref = resp.get_json()["diagnostic"]
    assert ref["kind"] == "script_run"
    res = _get(client, "script_run", ref["ref"], pj)
    assert res.status_code == 200
    assert_no_secret(res.get_data(as_text=True))
    snap = res.get_json()["snapshot"]
    assert snap["outcome"] == "error" and snap["error"] == {"code": "script_probe_failed"}
    assert snap["config"]["argv_count"] == 2
    assert "fig.py" not in res.get_data(as_text=True)


# ===================================================================== 全局诊断包（C17 / schema 5）
def test_the_global_bundle_carries_this_projects_snapshots_and_nothing_else(env, tmp_path):
    client, figs = env
    a, b = _open_two(client, figs, tmp_path)
    _, job = _export(client, _canvas(filename=f"n-{SECRET[:6]}", formats=["pdf", "eps"]), a)
    files = _bundle_files(client, a)
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema_version"] == 5 and manifest["contains_task_snapshots"] is True
    section = json.loads(files["task-diagnostics.json"])
    assert [s["snapshot"]["attempt_id"] for s in section["snapshots"]] == [job["job_id"]]
    for name, data in files.items():
        if name == "task-diagnostics.json":
            assert_no_secret(data.decode("utf-8", "replace"))
    # 别的项目的包里没有这份快照
    other = json.loads(_bundle_files(client, b)["task-diagnostics.json"])
    assert other["snapshots"] == []
    assert job["job_id"] not in _bundle_files(client, b)["task-diagnostics.json"].decode()


def test_bundle_section_is_bounded(env, tmp_path):
    client, figs = env
    a, _ = _open_two(client, figs, tmp_path)
    for i in range(30):
        taskdiag.STORE.record(a, "export", f"j{i}", _proj(i), outcome="failed", failed=True)
    section = json.loads(_bundle_files(client, a)["task-diagnostics.json"])
    assert len(section["snapshots"]) <= taskdiag.BUNDLE_SNAPSHOTS


# ===================================================================== 投影本身没有后门
def test_export_projection_never_reads_the_full_payload(env, tmp_path, monkeypatch):
    """白名单的反面证据：把 `to_payload()` 换成一碰就炸的，快照照样生成。"""
    client, figs = env
    pj, _ = _open_two(client, figs, tmp_path)

    def poisoned(self):
        raise AssertionError("diagnostic_projection 不许碰 to_payload()")

    # 同步导出本身要用 to_payload 回响应，所以只在 start 之后毒化：直接对已有作业投影
    _, job = _export(client, _canvas(formats=["pdf", "eps"]), pj)
    j = exportjob.get(job["job_id"])
    monkeypatch.setattr(exportjob.ExportJob, "to_payload", poisoned)
    from tavotto.engine import exportreq

    monkeypatch.setattr(exportreq.ExportRequest, "to_payload", poisoned)
    proj = exportjob.diagnostic_projection(j)
    assert proj["attempt_id"] == j.id and proj["request"]["scope"] == "canvas"
