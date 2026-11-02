"""项目打开后的后台预热（ADR 0160）与前台优先。

预热只**取会话**：不 build、不出图、不写盘、不发事件；同一 (项目, 面板) 只一次；前台渲染取会话
期间让路；native 不预热；失败静默。另外钉两个争用点的修法：并发的 `import matplotlib` 探测合并成一个
（`pool._has_matplotlib_once`），以及预热与前台对同一张图共用同一条会话（池锁上合并）。
"""

import threading
import time

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import pool, prewarm, project_watch as engine_watch

# ------------------------------ Prewarmer 本体 ------------------------------


@pytest.fixture
def pw():
    return prewarm.Prewarmer()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_runs_once_per_key_even_after_success(pw):
    calls = []
    assert pw.request(("p", "a"), lambda: calls.append(1), alive=0, cap=2) == prewarm.STARTED
    pw.join()
    assert pw.request(("p", "a"), lambda: calls.append(2), alive=0, cap=2) == prewarm.ALREADY
    assert calls == [1]


def test_failure_is_silent_and_not_retried(pw):
    def boom():
        raise RuntimeError("spawn failed")

    assert pw.request(("p", "a"), boom, alive=0, cap=2) == prewarm.STARTED
    pw.join()  # 不抛
    assert pw.request(("p", "a"), boom, alive=0, cap=2) == prewarm.ALREADY


def test_only_one_in_flight(pw):
    gate = threading.Event()
    assert pw.request(("p", "a"), gate.wait, alive=0, cap=2) == prewarm.STARTED
    assert pw.request(("p", "b"), lambda: None, alive=0, cap=2) == prewarm.BUSY
    gate.set()
    pw.join()


def test_yields_to_a_foreground_render(pw):
    ran = []
    with pw.foreground():
        assert pw.request(("p", "a"), lambda: ran.append(1), alive=0, cap=2) == prewarm.FOREGROUND
    assert ran == []
    # 前台走了之后同一张图还有机会（让路不算「已预热」）
    assert pw.request(("p", "a"), lambda: ran.append(1), alive=0, cap=2) == prewarm.STARTED
    pw.join()
    assert ran == [1]


def test_foreground_arriving_after_registration_aborts_the_start(pw):
    """登记成功、线程还没跑 work 时前台抢先：work 不跑，且不记「已预热」。"""
    ran = []
    entered = threading.Event()
    release = threading.Event()
    real_run = pw._run

    def slow_run(key, work):
        entered.set()
        release.wait(5)
        real_run(key, work)

    pw._run = slow_run
    assert pw.request(("p", "a"), lambda: ran.append(1), alive=0, cap=2) == prewarm.STARTED
    assert entered.wait(5)
    with pw.foreground():
        release.set()
        pw.join()
    assert ran == []
    assert pw.request(("p", "a"), lambda: ran.append(1), alive=0, cap=2) == prewarm.STARTED
    pw.join()


def test_leaves_room_in_the_pool(pw):
    assert pw.request(("p", "a"), lambda: None, alive=2, cap=2) == prewarm.POOL_FULL
    assert pw.request(("p", "a"), lambda: None, alive=1, cap=2) == prewarm.STARTED
    pw.join()


# ------------------------------ 端点 ------------------------------


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    prewarm.PREWARM.reset()
    yield m.app.test_client()
    prewarm.PREWARM.join()
    prewarm.PREWARM.reset()
    m.reset_projects()
    engine_watch.stop()


class _FakeWorker:
    built = False
    rev = 1
    renders = 0

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.renders += 1
        return {"manifest": {"elements": []}, "warnings": []}


def _open(client, tmp_path):
    figs = tmp_path / "pw"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    client.post("/api/projects/open", json={"path": str(figs)})
    return figs


def _stub(monkeypatch, worker, gets):
    monkeypatch.setattr(m.engine_pool, "peek_project_resolution", lambda root: ("/x/python", "x"))
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )

    def fake_get(*a, **kw):
        gets.append(a)
        return worker

    monkeypatch.setattr(m.engine_pool, "get", fake_get)


def _post(client, rel_id="p1.pdf"):
    return client.post("/api/engine/prewarm", json={"id": rel_id}).get_json()


def test_prewarm_takes_a_session_once_and_has_no_side_effects(client, tmp_path, monkeypatch):
    figs = _open(client, tmp_path)
    worker, gets, events = _FakeWorker(), [], []
    _stub(monkeypatch, worker, gets)
    monkeypatch.setattr(m, "sse_publish", lambda *a, **kw: events.append(a))
    before = sorted(p.name for p in figs.rglob("*"))

    assert _post(client)["status"] == "started"
    prewarm.PREWARM.join()
    assert len(gets) == 1
    assert worker.renders == 0  # 没有 build / override：不跑用户脚本、不出图
    assert events == []  # 不发 render.* 事件
    assert sorted(p.name for p in figs.rglob("*")) == before  # 项目目录零写入

    assert _post(client)["status"] == "already"
    assert len(gets) == 1


def test_foreground_render_reuses_the_prewarmed_session(client, tmp_path, monkeypatch):
    _open(client, tmp_path)
    worker, gets = _FakeWorker(), []
    _stub(monkeypatch, worker, gets)
    assert _post(client)["status"] == "started"
    prewarm.PREWARM.join()
    res = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert res.status_code == 200
    assert worker.renders == 1
    # 两次都经同一个取会话入口（真池里按键合并成同一条，见下面的池用例）
    assert len(gets) == 2


def test_prewarm_failure_is_silent(client, tmp_path, monkeypatch):
    _open(client, tmp_path)
    monkeypatch.setattr(m.engine_pool, "peek_project_resolution", lambda root: ("/x/python", "x"))
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )

    def boom(*a, **kw):
        raise pool.WorkerError("no python", code="no_worker_python")

    monkeypatch.setattr(m.engine_pool, "get", boom)
    events = []
    monkeypatch.setattr(m, "sse_publish", lambda *a, **kw: events.append(a))
    res = client.post("/api/engine/prewarm", json={"id": "p1.pdf"})
    assert res.status_code == 200
    prewarm.PREWARM.join()
    assert events == []


@pytest.mark.parametrize(
    "code",
    [
        "workdir_confirmation_required",
        "dependency_preparation_required",
        "missing_input",
        "conda_launcher_unavailable",
    ],
)
def test_gates_that_would_ask_the_user_stay_silent_in_prewarm(client, tmp_path, monkeypatch, code):
    """依赖门 / 工作目录门 / 缺输入只是**抛**（载荷挂在异常上、由渲染响应带给前端）：预热吞掉它，
    不发 SSE、不登记任何待弹窗——用户什么都没点就不会看到弹窗。"""
    _open(client, tmp_path)
    monkeypatch.setattr(m.engine_pool, "peek_project_resolution", lambda root: ("/x/python", "x"))
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )

    def asks(*a, **kw):
        err = pool.WorkerError("needs an answer", code=code)
        err.confirmation = {"kind": "ask"}
        err.dependency_preparation = {"plan": {}}
        raise err

    monkeypatch.setattr(m.engine_pool, "get", asks)
    events = []
    monkeypatch.setattr(m, "sse_publish", lambda *a, **kw: events.append(a))
    assert _post(client)["status"] == "started"
    prewarm.PREWARM.join()
    assert events == []


def test_undecided_environment_is_not_prewarmed(client, tmp_path, monkeypatch):
    """还没定解释器的项目：取会话会先做环境决定（写用户配置），预热不做。"""
    _open(client, tmp_path)
    gets = []
    _stub(monkeypatch, _FakeWorker(), gets)
    monkeypatch.setattr(m.engine_pool, "peek_project_resolution", lambda root: None)
    assert _post(client) == {"status": "skipped", "reason": "environment_undecided"}
    prewarm.PREWARM.join()
    assert gets == []


def test_native_runtime_figure_is_not_prewarmed(client, tmp_path, monkeypatch):
    _open(client, tmp_path)
    gets = []
    _stub(monkeypatch, _FakeWorker(), gets)
    monkeypatch.setattr(m, "_resolve_runtime_asset", lambda rid: {"script": "x.py"})
    monkeypatch.setattr(m.engine_enginesession, "profile_of", lambda root, rid: "native")
    assert _post(client, "runtime:abc") == {"status": "skipped", "reason": "native"}
    prewarm.PREWARM.join()
    assert gets == []


def test_safe_runtime_figure_only_takes_the_session(client, tmp_path, monkeypatch):
    """runtime 图预热也只取会话：端点只读，build（真正执行脚本）仍由第一次渲染惰性触发。"""
    _open(client, tmp_path)
    worker, gets = _FakeWorker(), []
    _stub(monkeypatch, worker, gets)
    info = {"script": "x.py", "entry": "main", "stem": "s"}
    monkeypatch.setattr(m, "_resolve_runtime_asset", lambda rid: info)
    monkeypatch.setattr(m.engine_runtimeasset, "run_selection", lambda root, i: None)
    assert _post(client, "runtime:abc")["status"] == "started"
    prewarm.PREWARM.join()
    assert len(gets) == 1 and worker.renders == 0


def test_prewarm_yields_while_a_foreground_render_is_getting_its_session(
    client, tmp_path, monkeypatch
):
    """前台优先（确定性）：渲染卡在取会话时，预热请求直接让路，一个会话都不起。"""
    _open(client, tmp_path)
    monkeypatch.setattr(m.engine_pool, "peek_project_resolution", lambda root: ("/x/python", "x"))
    in_get, release, gets = threading.Event(), threading.Event(), []
    worker = _FakeWorker()
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )

    def slow_get(*a, **kw):
        gets.append(a)
        in_get.set()
        release.wait(10)
        return worker

    monkeypatch.setattr(m.engine_pool, "get", slow_get)
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            r=m.app.test_client().post(
                "/api/engine/render",
                json={"id": "p1.pdf", "patches": []},
                headers={"X-Tavotto-Project": m.current_ctx().id},
            )
        )
    )
    # test_client 在另一个线程里没有「默认项目」之外的上下文；默认项目即刚打开的这个
    t.start()
    assert in_get.wait(10)
    try:
        assert _post(client)["status"] == "foreground_busy"
        assert len(gets) == 1
    finally:
        release.set()
        t.join(10)


# ------------------------------ 池：争用点的修法 ------------------------------


def test_concurrent_probes_of_one_interpreter_share_one_subprocess(monkeypatch):
    calls, gate = [], threading.Event()

    def slow_probe(python, **kw):
        calls.append(python)
        gate.wait(10)
        return True

    monkeypatch.setattr(pool, "_has_matplotlib", slow_probe)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(pool._has_matplotlib_once("/x/python")))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    assert _wait(lambda: len(calls) == 1)
    time.sleep(0.1)  # 其余三路都已排到领头者后面
    gate.set()
    for t in threads:
        t.join(10)
    assert calls == ["/x/python"]
    assert results == [True] * 4
    assert pool._probe_flights == {}


def test_a_leader_failure_is_not_inherited_by_followers(monkeypatch):
    """领头者的异常取决于它自己的线程状态（如 CaptureDeferred）：后来者各自重来，不继承。"""
    gate, calls = threading.Event(), []

    def probe(python, **kw):
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            gate.wait(10)
            raise RuntimeError("leader only")
        return True

    monkeypatch.setattr(pool, "_has_matplotlib", probe)
    outcome = {}

    def leader():
        try:
            pool._has_matplotlib_once("/x/python")
        except RuntimeError as exc:
            outcome["leader"] = exc

    lt = threading.Thread(target=leader, name="leader")
    lt.start()
    assert _wait(lambda: len(calls) == 1)
    ft = threading.Thread(
        target=lambda: outcome.update(follower=pool._has_matplotlib_once("/x/python")),
        name="follower",
    )
    ft.start()
    time.sleep(0.1)
    gate.set()
    lt.join(10)
    ft.join(10)
    assert isinstance(outcome["leader"], RuntimeError)
    assert outcome["follower"] is True
    assert calls == ["leader", "follower"]


try:
    _WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    _WORKER_PY = None


@pytest.mark.skipif(_WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")
def test_prewarm_and_foreground_share_one_real_session(tmp_path):
    """预热与前台对同一张图并发取会话：池里只建一条，两边拿到同一个对象。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "f0.py").write_text(
        "import matplotlib.pyplot as plt\n\ndef main():\n    plt.subplots()\n", encoding="utf-8"
    )
    try:
        got = []
        threads = [
            threading.Thread(target=lambda: got.append(pool.acquire("f0.py", str(proj), "main")))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert len(got) == 2
        assert got[0][0] is got[1][0]
        assert sorted(created for _, created in got) == [False, True]
    finally:
        pool.shutdown_all(str(proj), wait=True)
