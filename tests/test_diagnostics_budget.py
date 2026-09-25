"""`GET /api/diagnostics` 的耗时预算：服务端最坏耗时 ↔ 冒烟客户端超时（#512）。

主语：**服务端**这一个请求按设计允许的最坏墙钟（`app.DIAG_PROBE_WORST_CASE_S`），
对 **CI 冒烟客户端**（`scripts/smoke_app.py`）那一次 `_get` 的超时。
以前客户端等 30 s、服务端单 matplotlib 探测就 30 s 再串行加每个 CLI 10 s，
余量是零，Windows runner 一慢就随机红。

前提（写在判据旁边）：服务端的最坏耗时 = 两项探测**并行**后取大，只有在
① matplotlib 子进程的超时用的是那个常量、② AI 探测是后台线程 + `join(预算)`
这两条都成立时才对——所以这两条用 AST 钉住，行为用例再证一次并行与到点即返。
项目注册表扫描（`build_draft`）是本地文件系统操作，没有常量可对，只能落在余量里。
"""

from __future__ import annotations

import ast
import importlib.util
import threading
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "src" / "tavotto" / "app.py"
SMOKE_PY = REPO / "scripts" / "smoke_app.py"

#: 客户端在服务端最坏耗时之外至少要留的余量（注册表扫描 + runner 线程调度）
MIN_CLIENT_SLACK_S = 15


def _load_smoke():
    spec = importlib.util.spec_from_file_location("_smoke_app_diag_budget", SMOKE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_smoke_client_waits_longer_than_server_worst_case():
    from tavotto import app as m

    smoke = _load_smoke()
    assert m.DIAG_PROBE_WORST_CASE_S == max(m.DIAG_MATPLOTLIB_TIMEOUT_S, m.DIAG_AI_PROBE_BUDGET_S)
    assert smoke.DIAGNOSTICS_TIMEOUT_S >= m.DIAG_PROBE_WORST_CASE_S + MIN_CLIENT_SLACK_S, (
        f"冒烟客户端等 {smoke.DIAGNOSTICS_TIMEOUT_S}s，服务端最坏 {m.DIAG_PROBE_WORST_CASE_S}s"
        f"，至少要留 {MIN_CLIENT_SLACK_S}s 余量"
    )


def test_server_probes_use_the_named_budgets():
    """服务端两项探测真的受那两个常量约束（真实 Call 节点，不是子串）。"""
    fn = _func(ast.parse(APP_PY.read_text(encoding="utf-8")), "api_diagnostics")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    # matplotlib 探测：唯一一次 sp.run，timeout 是具名常量
    runs = [
        c
        for c in calls
        if isinstance(c.func, ast.Attribute)
        and c.func.attr == "run"
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "sp"
    ]
    assert len(runs) == 1
    t = _kw(runs[0], "timeout")
    assert isinstance(t, ast.Name) and t.id == "DIAG_MATPLOTLIB_TIMEOUT_S"

    # AI 探测：后台线程，请求线程只等到**共同截止时刻**（起跑时 + 预算）为止
    deadlines = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and [getattr(x, "id", None) for x in n.targets] == ["caps_deadline"]
    ]
    assert len(deadlines) == 1
    assert "DIAG_AI_PROBE_BUDGET_S" in {
        x.id for x in ast.walk(deadlines[0].value) if isinstance(x, ast.Name)
    }
    joins = [
        c
        for c in calls
        if isinstance(c.func, ast.Attribute)
        and c.func.attr == "join"
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "caps_job"
    ]
    assert len(joins) == 1
    assert "caps_deadline" in {x.id for x in ast.walk(joins[0].args[0]) if isinstance(x, ast.Name)}
    started = [
        c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "_diag_capabilities_start"
    ]
    assert len(started) == 1


def test_every_smoke_diagnostics_call_passes_the_derived_timeout():
    """主语是 **scripts/ 下所有** 调 `/api/diagnostics`（不含子路径）的客户端。"""
    hits: list[tuple[str, str | None]] = []
    for py in sorted((REPO / "scripts").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            if not call.args:
                continue
            parts = [
                v.value
                for v in ast.walk(call.args[0])
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            if not any(p.rstrip("/").endswith("/api/diagnostics") for p in parts):
                continue
            t = _kw(call, "timeout")
            hits.append((py.name, t.id if isinstance(t, ast.Name) else None))
    assert hits == [("smoke_app.py", "DIAGNOSTICS_TIMEOUT_S")]


@pytest.fixture
def client(monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    ai_bridge.invalidate_capabilities()
    yield m, m.app.test_client()
    ai_bridge.invalidate_capabilities()


def _fake_caps():
    return {"agents": [], "endpoints": [], "presets": [], "checked_at_ms": 0}


def test_matplotlib_and_ai_probes_run_concurrently(client, monkeypatch):
    """matplotlib 子进程还没返回时，AI 探测必须已经开跑（串行时这里等不到）。"""
    import subprocess

    m, c = client
    caps_started = threading.Event()
    seen: dict[str, bool] = {}

    def caps(refresh=False):
        caps_started.set()
        return _fake_caps()

    def run(argv, **kw):
        seen["concurrent"] = caps_started.wait(5)
        return subprocess.CompletedProcess(argv, 0, stdout="3.9.0\n", stderr="")

    monkeypatch.setattr(m.engine_pool, "find_worker_python", lambda: "/x/python")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m.engine_ai, "capabilities", caps)

    checks = {x["id"]: x for x in c.get("/api/diagnostics").get_json()["checks"]}
    assert seen == {"concurrent": True}
    assert checks["matplotlib"]["ok"] is True


def test_ai_probe_over_budget_returns_instead_of_hanging(client, monkeypatch):
    m, c = client
    release = threading.Event()

    def caps(refresh=False):
        release.wait(10)
        return _fake_caps()

    def no_worker():
        raise m.engine_pool.WorkerError("no worker in this test")

    monkeypatch.setattr(m.engine_pool, "find_worker_python", no_worker)
    monkeypatch.setattr(m.engine_ai, "capabilities", caps)
    monkeypatch.setattr(m, "DIAG_AI_PROBE_BUDGET_S", 0.2)
    try:
        first = {x["id"]: x for x in c.get("/api/diagnostics").get_json()["checks"]}
        assert first["cli_probe"]["ok"] is False
        job = m._DIAG_CAPS_INFLIGHT[0]
        # 还在跑的探测被下一次诊断复用，不叠第二份
        c.get("/api/diagnostics")
        assert m._DIAG_CAPS_INFLIGHT[0] is job
    finally:
        release.set()
        m._DIAG_CAPS_INFLIGHT[0].join(5)
    again = {x["id"]: x for x in c.get("/api/diagnostics").get_json()["checks"]}
    assert "cli_probe" not in again


def test_ai_wait_is_only_what_is_left_after_the_matplotlib_probe(client, monkeypatch):
    """matplotlib 探测用掉的时间从 AI 的等待里扣掉：两者共用一个截止时刻，
    接口最坏是两份预算取大、不是相加。用假时钟判，不比墙钟。"""
    import subprocess

    m, c = client
    clock = [1000.0]
    joined: list[float] = []

    class FakeJob:
        result = _fake_caps()
        error = None

        def join(self, timeout=None):
            joined.append(timeout)

        def is_alive(self):
            return False

    def run(argv, **kw):
        # matplotlib 探测一直拖到自己的超时才回来
        clock[0] += m.DIAG_MATPLOTLIB_TIMEOUT_S
        return subprocess.CompletedProcess(argv, 0, stdout="3.9.0\n", stderr="")

    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.engine_pool, "find_worker_python", lambda: "/x/python")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m, "_diag_capabilities_start", lambda: FakeJob())

    assert c.get("/api/diagnostics").status_code == 200
    assert len(joined) == 1
    assert joined[0] == max(0.0, m.DIAG_AI_PROBE_BUDGET_S - m.DIAG_MATPLOTLIB_TIMEOUT_S)
