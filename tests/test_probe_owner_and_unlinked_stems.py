"""T09b：旧试运行路径的两条收尾——**假 pool**（不起 worker）。

* **`/api/registry/probe/cancel` 按 owner 只退役本任务**（ADR 0116 §三，与准备会话的取消同一条规则）：杀的是这次试运行
  自己取到、自己建的那条会话（`expected_worker`）；池键上此刻是别人的会话（渲染路在试运行换入口之间起的同键会话）时
  一根手指不碰；取到的是别人正在用的同键会话（`owned is False`）时只停自己的等待意图，不杀；取消比「取到会话」早到时只立
  标志，取到那一刻按同一规则处理。开关关闭时素材库 / 接入中心仍走这条路，所以修在这里而不是随旧路径退役。
* **无参数运行整条替换注册表 stems 的可恢复提示**（T03 已知缺口）：登记时把这次替换掉的旧图名（`unlinked_stems`）如实
  带回——试运行响应与准备会话报告同一口径；带运行配置的执行是并入，不替换任何东西。不改注册表文件格式。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import json
import random
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import pool as engine_pool, probe as engine_probe
from test_preparation_api import _FakeWorker, _open, _project, client, fake_pool  # noqa: F401
from test_preparation_session import _act, _action, _create, _get, sessions  # noqa: F401


def _registry(root: Path) -> dict:
    return json.loads((root / "tavotto_registry.json").read_text(encoding="utf-8"))


def _write_registry(root: Path, stems: list[str]) -> None:
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {"fig.py": {"entry": "__main__", "cost": "light", "stems": stems}},
            }
        ),
        encoding="utf-8",
    )


def _captured_before(root: Path, stem: str, tmp_path: Path, run_config: str = "") -> None:
    """让 `stem` 有「此前真被某次执行捕获过」的证据：与 `app._materialize_runtime` 同一个函数物化进 runtime cache。"""
    from tavotto.engine import figcapture, runtimeasset

    svg = tmp_path / f"{stem}.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    asset_id = figcapture.runtime_asset_id("fig.py", stem, run_config)
    assert runtimeasset.materialize(
        root, {"asset_id": asset_id, "script": "fig.py", "stem": stem}, svg
    )


# ===================================================================== 一、替换掉的旧图名
def test_a_bare_run_reports_the_stems_it_replaced(tmp_path):
    root = _project(tmp_path, "p")
    # `fig_scaled` 只在带 `--scale` 时才产出（T03 那条缺口的形状），而且此前真的被捕获过
    _write_registry(root, ["fig", "fig_scaled"])
    _captured_before(root, "fig_scaled", tmp_path)
    got = engine_probe.register_probed(
        root,
        "fig.py",
        {"script": "fig.py", "entry": "__main__", "stems": ["fig"], "descriptors": []},
    )
    assert got["registered"] is True
    assert got["unlinked_stems"] == ["fig_scaled"]
    assert _registry(root)["scripts"]["fig.py"]["stems"] == ["fig"]  # 行为不变：仍是整条替换


def test_a_registered_name_that_was_never_captured_is_not_reported(tmp_path):
    """T11：打开项目时的静态扫描会把字面量 savefig 的图名先登记上；条件分支里那张从没产出过。第一次无参数运行替换掉它时
    不能说「此前带其他参数生成的……已不再关联」——它从来没有生成过（真 worker 复现见
    `test_foundation_script_first_run.py::test_a_bare_run_on_a_hot_session_still_says_which_figures_it_unlinked`）。"""
    root = _project(tmp_path, "p")
    _write_registry(root, ["fig", "fig_scaled"])
    got = engine_probe.register_probed(
        root,
        "fig.py",
        {"script": "fig.py", "entry": "__main__", "stems": ["fig"], "descriptors": []},
    )
    assert got["registered"] is True and "unlinked_stems" not in got
    assert _registry(root)["scripts"]["fig.py"]["stems"] == ["fig"]  # 替换语义不变


def test_a_run_with_a_configuration_merges_and_replaces_nothing(tmp_path):
    root = _project(tmp_path, "p")
    _write_registry(root, ["fig", "fig_scaled"])
    got = engine_probe.register_probed(
        root,
        "fig.py",
        {
            "script": "fig.py",
            "entry": "__main__",
            "stems": ["fig_other"],
            "descriptors": [],
            "run_config": "rc-1",
        },
    )
    assert got["registered"] is True
    assert "unlinked_stems" not in got
    assert _registry(root)["scripts"]["fig.py"]["stems"] == ["fig", "fig_other", "fig_scaled"]


def test_a_bare_run_that_reproduces_every_stem_reports_nothing(tmp_path):
    root = _project(tmp_path, "p")
    _write_registry(root, ["fig"])
    got = engine_probe.register_probed(
        root,
        "fig.py",
        {"script": "fig.py", "entry": "__main__", "stems": ["fig"], "descriptors": []},
    )
    assert got["registered"] is True and "unlinked_stems" not in got


def test_the_session_report_names_the_stems_a_bare_run_replaced(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    _write_registry(root, ["fig", "fig_scaled"])
    _captured_before(root, "fig_scaled", tmp_path)
    report = _create(client, {"script": "fig.py"}).get_json()
    assert report["unlinked_stems"] == []
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    deadline = time.time() + 10
    while (final := _get(client, sid).get_json())["phase"] == "running":
        assert time.time() < deadline, final
        time.sleep(0.02)
    assert final["phase"] == "completed", final
    assert final["unlinked_stems"] == ["fig_scaled"]
    assert _registry(root)["scripts"]["fig.py"]["stems"] == ["fig"]


# ===================================================================== 二、试运行的取消按 owner
class _Worker:
    def __init__(self, name: str):
        self.name = name
        self.killed = threading.Event()

    def force_kill(self) -> None:
        self.killed.set()


@pytest.fixture
def probe_pool(monkeypatch):
    """可控的试运行池：`build` 取到会话之后先过 `before_build`（与真 pool 同一时刻），再卡在闸门上。

    `owned` = `pool.acquired_here` 的回答；`pool_worker` = 取消那一刻池键上的会话（`force_cancel(expected_worker=…)`
    只有与它相同才杀）。"""
    box = {
        "owned": True,
        "mine": _Worker("probe"),
        "pool_worker": None,
        "force_cancel": [],
        "taking": threading.Event(),  # 清掉 = 卡在「取会话之前」
        "gate": threading.Event(),  # 清掉 = 卡在 build 里
        "entered": threading.Event(),  # 取到会话、过了 before_build、正卡在 build 里
    }
    box["pool_worker"] = box["mine"]
    box["taking"].set()

    def build(script, root, entry, *, before_build=None, **kw):
        box["taking"].wait(timeout=30)
        worker = box["mine"]
        if before_build is not None:
            before_build(worker)
        box["entered"].set()
        box["gate"].wait(timeout=30)
        if worker.killed.is_set():
            raise engine_pool.WorkerError("worker 被关掉", code="worker_crashed")
        return worker, {
            "stems": {"fig": {"size_mm": [50, 25], "source": "savefig"}},
            "descriptors": [{"stem": "fig", "script": "fig.py"}],
        }

    def force_cancel(script, root, *, expected_worker=None, **kw):
        box["force_cancel"].append(expected_worker)
        if expected_worker is not None and expected_worker is not box["pool_worker"]:
            return False
        target = box["pool_worker"]
        if target is not None:
            target.killed.set()
        box["gate"].set()
        return True

    monkeypatch.setattr(engine_pool, "build", build)
    # raising=False：修前的代码上没有这个函数——用例照样跑到行为断言上（修前红是行为红，不是缺属性）
    monkeypatch.setattr(engine_pool, "acquired_here", lambda worker: box["owned"], raising=False)
    monkeypatch.setattr(engine_pool, "force_cancel", force_cancel)
    monkeypatch.setattr(engine_pool, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **kw: None)
    return box


def _start_probe(client) -> tuple[threading.Thread, dict]:
    done: dict = {}

    def go():
        resp = client.post("/api/registry/probe", json={"script": "fig.py"})
        done["status"] = resp.status_code
        done["json"] = resp.get_json()

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th, done


def _wait_entered(box: dict, timeout: float = 5.0) -> None:
    """等到试运行已经取到会话、正卡在 build 里（「运行中」那条路，不是「取到会话之前」）。"""
    assert box["entered"].wait(timeout), "试运行一直没有取到会话"


def _cancel(client) -> dict:
    resp = client.post("/api/registry/probe/cancel", json={"script": "fig.py"})
    assert resp.status_code == 200
    return resp.get_json()


def test_cancelling_a_probe_kills_only_the_session_it_created(client, tmp_path, probe_pool):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    probe_pool["gate"].clear()
    th, done = _start_probe(client)
    _wait_entered(probe_pool)
    assert m._PROBE_OWNERS[(pj, "fig.py")] == (probe_pool["mine"], True)
    assert _cancel(client)["cancelling"] is True
    th.join(timeout=5)  # 闸门要 30 s 才自己放开：5 s 内返回只能是取消当场杀的
    assert not th.is_alive()
    assert done["json"]["error"]["code"] == "execution_cancelled"
    assert probe_pool["force_cancel"] == [
        probe_pool["mine"]
    ]  # 按 expected_worker，不是池键上的随便哪条


def test_cancelling_never_kills_a_replacement_someone_else_put_under_the_same_key(
    client, tmp_path, probe_pool
):
    """试运行换入口之间渲染路在同一个池键上起了自己的会话：取消只杀试运行自己那条（已经离开池），替换者不碰。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    probe_pool["gate"].clear()
    th, done = _start_probe(client)
    _wait_entered(probe_pool)
    render = _Worker("render")
    probe_pool["pool_worker"] = render  # 池键上此刻是渲染路的会话
    assert _cancel(client)["cancelling"] is True
    assert probe_pool["mine"].killed.is_set()  # 自己那条直接杀
    probe_pool["gate"].set()  # 被杀的 build 返回
    th.join(timeout=5)
    assert not th.is_alive()
    assert done["json"]["error"]["code"] == "execution_cancelled"
    assert not render.killed.is_set(), "取消杀掉了渲染路的会话"


def test_a_probe_waiting_on_someone_elses_session_does_not_kill_it(client, tmp_path, probe_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    probe_pool["owned"] = False  # 取到的是渲染路正在用的同键会话
    probe_pool["gate"].clear()
    th, done = _start_probe(client)
    _wait_entered(probe_pool)
    assert _cancel(client)["cancelling"] is True
    assert probe_pool["force_cancel"] == []
    assert not probe_pool["mine"].killed.is_set(), "取消杀掉了别人的会话"
    probe_pool["gate"].set()  # 别人的 build 自己跑完
    th.join(timeout=5)
    assert not th.is_alive() and done["status"] == 200


def test_a_cancel_before_the_session_is_taken_spares_a_shared_session(client, tmp_path, probe_pool):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    probe_pool["owned"] = False
    probe_pool["taking"].clear()  # 还在取会话（解析解释器 / 等池锁）
    th, done = _start_probe(client)
    deadline = time.time() + 5
    while (pj, "fig.py") not in m._PROBES:
        assert time.time() < deadline
        time.sleep(0.01)
    assert _cancel(client)["cancelling"] is True
    assert probe_pool["force_cancel"] == []  # 还没取到会话：只立标志
    probe_pool["taking"].set()
    th.join(timeout=5)
    assert not th.is_alive()
    assert done["json"]["error"]["code"] == "execution_cancelled"
    assert probe_pool["force_cancel"] == [] and not probe_pool["mine"].killed.is_set()


def test_a_cancel_before_the_session_is_taken_closes_a_session_it_creates(
    client, tmp_path, probe_pool
):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    probe_pool["taking"].clear()
    th, done = _start_probe(client)
    deadline = time.time() + 5
    while (pj, "fig.py") not in m._PROBES:
        assert time.time() < deadline
        time.sleep(0.01)
    assert _cancel(client)["cancelling"] is True
    probe_pool["taking"].set()
    th.join(timeout=5)
    assert not th.is_alive()
    assert done["json"]["error"]["code"] == "execution_cancelled"
    assert probe_pool["force_cancel"] == [probe_pool["mine"]]


def test_acquired_here_answers_only_for_this_threads_last_acquisition(monkeypatch, tmp_path):
    """`pool.acquired_here` 只回答「本线程最近一次 acquire 取到的就是它」：别的会话 / 别的线程一律说不清（None）。"""
    a, b = object(), object()
    engine_pool._last_acquired.result = (a, True)
    assert engine_pool.acquired_here(a) is True
    assert engine_pool.acquired_here(b) is None
    seen: list = []
    th = threading.Thread(target=lambda: seen.append(engine_pool.acquired_here(a)))
    th.start()
    th.join()
    assert seen == [None]
    engine_pool._last_acquired.result = (a, False)
    assert engine_pool.acquired_here(a) is False
    del engine_pool._last_acquired.result


def test_acquire_records_whether_this_call_created_the_session(monkeypatch, tmp_path):
    """真 `acquire()` 把 `(worker, created)` 记进本线程：第一次新建 → True，第二次复用同一条 → False。"""

    class _W:
        entry = "main"
        python = "/envs/p/bin/python"
        last_used = 0.0
        build_failed = False

        def alive(self):
            return True

    monkeypatch.setattr(engine_pool, "_workers", {})
    monkeypatch.setattr(engine_pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda *a, **k: ("/envs/p/bin/python", "system")
    )
    monkeypatch.setattr(engine_pool, "_new_worker", lambda *a, **k: _W())
    monkeypatch.setattr(engine_pool, "_remap_current", lambda *a, **k: True)
    monkeypatch.setattr(engine_pool, "_schedule_prune", lambda: None)
    first, created = engine_pool.acquire("fig.py", str(tmp_path), "main")
    assert created is True and engine_pool.acquired_here(first) is True
    again, created = engine_pool.acquire("fig.py", str(tmp_path), "main")
    assert again is first and created is False
    assert engine_pool.acquired_here(first) is False


# ===================================================================== 三、取消与取会话的交错（T11 压测）
@pytest.fixture
def jittery_pool(monkeypatch):
    """每次试运行一条新会话；取会话前后各插一段随机小停顿，让「取消」落在任意位置。取到之后最多等 3 s 被杀，
    没被杀就当脚本跑完（成功）。所以「取消已登记却没杀到自己的会话」会表现为 3 s 后成功，而不是当场取消。"""
    box: dict = {"owned": True, "workers": [], "force_cancel": []}

    def build(script, root, entry, *, before_build=None, **kw):
        time.sleep(random.uniform(0, 0.002))
        worker = _Worker(f"probe-{len(box['workers'])}")
        box["workers"].append(worker)
        box["current"] = worker
        if before_build is not None:
            before_build(worker)
        time.sleep(random.uniform(0, 0.001))
        if worker.killed.wait(3.0 if box["owned"] else 0.05):
            raise engine_pool.WorkerError("worker 被关掉", code="worker_crashed")
        return worker, {
            "stems": {"fig": {"size_mm": [50, 25], "source": "savefig"}},
            "descriptors": [{"stem": "fig", "script": "fig.py"}],
        }

    def force_cancel(script, root, *, expected_worker=None, **kw):
        box["force_cancel"].append(expected_worker)
        if expected_worker is None or expected_worker is not box.get("current"):
            return False
        expected_worker.killed.set()
        return True

    monkeypatch.setattr(engine_pool, "build", build)
    monkeypatch.setattr(engine_pool, "acquired_here", lambda worker: box["owned"])
    monkeypatch.setattr(engine_pool, "force_cancel", force_cancel)
    monkeypatch.setattr(engine_pool, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **kw: None)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # 线程切换尽量频繁：交错点更分散
    yield box
    sys.setswitchinterval(old)


def _race_once(client, pj: str) -> tuple[dict, float]:
    th, done = _start_probe(client)
    deadline = time.time() + 5
    while (pj, "fig.py") not in m._PROBES and th.is_alive():
        assert time.time() < deadline
        time.sleep(0.0005)
    time.sleep(random.uniform(0, 0.003))
    t0 = time.monotonic()
    landed = _cancel(client)["cancelling"]
    th.join(timeout=10)
    assert not th.is_alive()
    return {"landed": landed, **done}, time.monotonic() - t0


@pytest.mark.parametrize("seed", [11, 23])
def test_cancel_and_taking_the_session_interleaved_never_lose_the_cancel_or_kill_a_stranger(
    client, tmp_path, jittery_pool, seed
):
    """T09b 留下的复核：取消端点「先置标志、再读所有权」与试运行「先记所有权、再看标志」在任意交错下，
    ① 自己建的会话：登记了的取消一定当场生效（`execution_cancelled`，远早于 3 s 的自然结束），杀的只有自己那条；
    ② 别人的会话（`owned is False`）：一次都不杀。随机交错 × 2 个种子；不是证明，是把窄窗口反复撞几百次。"""
    random.seed(seed)
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    for i in range(150):
        jittery_pool["owned"] = True
        before = len(jittery_pool["workers"])
        out, took = _race_once(client, pj)
        assert out["landed"] is True, i
        assert out["json"]["error"]["code"] == "execution_cancelled", (i, out["json"])
        assert took < 2.5, (i, took)  # 不是等 3 s 自己结束
        new = jittery_pool["workers"][before:]  # 取消早于进入入口循环时一条会话都不取（空）
        assert all(w.killed.is_set() for w in new), i
        assert all(w in new for w in jittery_pool["force_cancel"]), i
        jittery_pool["force_cancel"].clear()
        assert (pj, "fig.py") not in m._PROBE_OWNERS
    for i in range(100):
        jittery_pool["owned"] = False
        before = len(jittery_pool["workers"])
        out, _took = _race_once(client, pj)
        assert jittery_pool["force_cancel"] == [], i
        assert not any(w.killed.is_set() for w in jittery_pool["workers"][before:]), i
        assert out["status"] == 200, i


class _SteppedLock:
    """`app._PROBES_LOCK` 的替身：在**指定的那一次** `with` 之后 / 之前停住，把两条线程摆成某一种确定的交错。

    按调用它的函数名认是哪一处（`api_registry_probe_cancel` 第 2 次 = 读所有权那一处；`note_owner` = 试运行记所有权那一处），
    不按次数猜别人的线程。真锁照常互斥；停的那一下在锁外。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancel_entries = 0
        self.after_cancel_read: threading.Event | None = None  # 取消读完所有权、放锁之后等它
        self.before_note: threading.Event | None = None  # 试运行记所有权、拿锁之前等它
        self.cancel_read_done = threading.Event()

    def __enter__(self):
        caller = sys._getframe(1).f_code.co_name
        if caller == "note_owner" and self.before_note is not None:
            assert self.before_note.wait(5), "取消一直没走完"
        self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()
        if sys._getframe(1).f_code.co_name == "api_registry_probe_cancel":
            self.cancel_entries += 1
            if self.cancel_entries == 2:
                self.cancel_read_done.set()
                if self.after_cancel_read is not None:
                    assert self.after_cancel_read.wait(5), "试运行一直没过取会话那一步"
        return False


def _stepped(monkeypatch, jittery_pool) -> _SteppedLock:
    lock = _SteppedLock()
    monkeypatch.setattr(m, "_PROBES_LOCK", lock)
    return lock


def test_a_cancel_that_reads_the_owner_before_the_probe_records_it_is_still_seen(
    client, tmp_path, jittery_pool, monkeypatch
):
    """确定性的交错 ①：取消端点读所有权（此刻还没有）之后、试运行才记所有权并看标志。取消端点「先置标志」，所以试运行
    看标志时一定看得见，自己收掉会话——反过来（先读所有权、后置标志）这一种交错里两边都看不见对方，会话一直跑。"""
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    lock = _stepped(monkeypatch, jittery_pool)
    taking = threading.Event()
    checked = threading.Event()
    real_build = engine_pool.build

    def build(script, root_, entry, *, before_build=None, **kw):
        assert taking.wait(5)

        def gated(worker):
            try:
                before_build(worker)
            finally:
                checked.set()

        return real_build(script, root_, entry, before_build=gated, **kw)

    monkeypatch.setattr(engine_pool, "build", build)
    lock.after_cancel_read = checked
    th, done = _start_probe(client)
    deadline = time.time() + 5
    while (pj, "fig.py") not in m._PROBES:
        assert time.time() < deadline
        time.sleep(0.005)
    canceller = threading.Thread(target=lambda: done.update(cancel=_cancel(client)))
    canceller.start()
    assert lock.cancel_read_done.wait(5)
    taking.set()  # 取消已读完所有权（还没有）：现在才让试运行去取会话、记所有权、看标志
    canceller.join(10)
    th.join(10)
    assert not th.is_alive()
    assert done["cancel"]["cancelling"] is True
    # 取消丢了的样子：试运行 3 s 后「成功」登记（error 为空）、会话没被杀
    assert (done["json"].get("error") or {}).get("code") == "execution_cancelled", done["json"]
    assert jittery_pool["workers"][-1].killed.is_set()


def test_a_probe_that_looks_at_the_flag_before_recording_its_owner_is_still_cancelled(
    client, tmp_path, jittery_pool, monkeypatch
):
    """确定性的交错 ②：试运行取到会话、记所有权之前，取消整条走完（置标志、读所有权=没有、不杀）。试运行「先记所有权、
    再看标志」，所以记完之后看标志一定看得见，自己收掉会话——反过来（先看标志、再记所有权）这一种交错里会话一直跑。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    lock = _stepped(monkeypatch, jittery_pool)
    lock.before_note = lock.cancel_read_done
    th, done = _start_probe(client)
    deadline = time.time() + 5
    while not jittery_pool["workers"]:
        assert time.time() < deadline  # 试运行已取到会话、停在记所有权之前
        time.sleep(0.005)
    done["cancel"] = _cancel(client)
    th.join(10)
    assert not th.is_alive()
    assert done["cancel"]["cancelling"] is True
    # 取消丢了的样子：试运行 3 s 后「成功」登记（error 为空）、会话没被杀
    assert (done["json"].get("error") or {}).get("code") == "execution_cancelled", done["json"]
    assert jittery_pool["workers"][-1].killed.is_set()
