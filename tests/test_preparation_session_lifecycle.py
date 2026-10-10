"""T09：准备会话成为界面默认入口之后的生命周期合同——**假 pool**（替身与 `test_preparation_api` 同一套）。

* **明确取消按 owner 当场退役本任务**：本计划新建的会话还在 build（长计算 / 停在 input 上）时取消，立即只关这一条
  （`force_cancel(expected_worker=…)`），不等 build 自己返回；共享会话的等待者取消只停自己的等待，会话一根手指不碰，
  用户可以马上再检查 / 再跑一次。
* **进入编辑用这次尝试真正捕获的图**：成功的报告带 `captured`（与试运行响应同一份公开描述符），失败 / 没出图时是空表。

替身 `build_owned` 打开 `announce` 才像真 pool 那样在执行之前报所有权；不打开时走的是旧的「build 返回之后再收」，
那组用例（`test_preparation_api` / `test_preparation_session` 的取消）原样钉着旧路径。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import dataclasses
import threading
import time
from pathlib import Path

from tavotto import app as m
from tavotto.engine import pool as engine_pool, preparation
from test_preparation_api import _FakeWorker, _open, _project, client, fake_pool  # noqa: F401
from test_preparation_session import (  # noqa: F401
    _act,
    _action,
    _create,
    _get,
    sessions,
)


def _acquired(client, sid: str, timeout: float = 5.0) -> dict:
    """等到执行线程**已经取到会话、报过所有权**（替身在执行之前调 `on_acquired`）：取消走的是「运行中」那条路，
    不是「取到会话之前」那条（后者另有用例）。"""
    deadline = time.time() + timeout
    while True:
        report = _get(client, sid).get_json()
        attempt = report["provider"]["attempt_id"]
        entry = preparation.SERVICE._entry(attempt) if attempt else None
        if report["phase"] == "running" and entry is not None and entry.acquired:
            return report
        assert time.time() < deadline, report
        time.sleep(0.02)


def _terminal(client, sid: str, timeout: float) -> dict:
    """在 `timeout` 之内等到不是 running——替身的 build 闸门要 30 s 才自己放开，短于它就证明是取消当场收的。"""
    deadline = time.time() + timeout
    while True:
        report = _get(client, sid).get_json()
        if report["phase"] not in ("running", "awaiting_runtime_input"):
            return report
        assert time.time() < deadline, report
        time.sleep(0.02)


def test_cancelling_a_build_this_session_owns_closes_that_session_at_once(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    sid = report["session_id"]
    fake_pool["announce"] = True
    fake_pool["gate"].clear()  # build 卡住：像一段很长的计算 / 停在 input() 上
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    running = _acquired(client, sid)
    assert _act(client, sid, _action(running, "cancel")["id"], 1).status_code == 200
    final = _terminal(client, sid, timeout=5.0)  # 闸门从没被测试放开：是取消当场关掉的
    assert final["phase"] == "cancelled"
    assert fake_pool["force_cancel"] == [("fig.py", str(root))]
    assert fake_pool["build_calls"] == 1
    assert "当场关闭" in final["result"]["note"]
    assert final["captured"] == []


def test_a_waiter_on_someone_elses_session_only_stops_its_own_wait(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    sid = report["session_id"]
    fake_pool["announce"] = True
    fake_pool["created"] = False  # 池里这条会话是别人的（例如编辑那条渲染正在冷启动）
    fake_pool["gate"].clear()
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    running = _acquired(client, sid)
    first_attempt = running["provider"]["attempt_id"]
    assert _act(client, sid, _action(running, "cancel")["id"], 1).status_code == 200
    final = _terminal(client, sid, timeout=5.0)
    assert final["phase"] == "cancelled"
    assert fake_pool["force_cancel"] == []  # 别人的会话一根手指不碰
    assert "别的消费者" in final["result"]["note"]
    # 执行线程还卡在别人的会话上，但这份会话已经不在等它：可以马上再跑一次（新的一次尝试）
    rerun = _act(client, sid, _action(final, "run")["id"], final["config_revision"])
    assert rerun.status_code == 202, rerun.get_json()
    fake_pool["gate"].set()
    deadline = time.time() + 10
    while _get(client, sid).get_json()["phase"] == "running":
        assert time.time() < deadline
        time.sleep(0.02)
    # 第一次尝试迟到的结局不改写它的终局：仍是取消，回执不归它
    plan, result = preparation.SERVICE.get(first_attempt, final["project_id"])
    assert result.status == preparation.STATUS_CANCELLED
    assert result.receipt is None and result.captured is None


def test_a_cancel_that_arrives_before_the_session_is_taken_still_closes_only_its_own(
    tmp_path, fake_pool, monkeypatch
):
    """取消比「取到会话」还早到：标志先立着；取到的那一刻是自己新建的 → 当场关掉、不让它开跑；是别人的 →
    不碰，本计划直接以取消收场（不再等它）。"""
    root = _project(tmp_path, "p")
    svc = preparation.PreparationService()
    killed: list = []
    monkeypatch.setattr(
        engine_pool,
        "force_cancel",
        lambda script, root, **kw: killed.append(kw["expected_worker"]) or True,
    )

    def cancelled_before_taking() -> str:
        plan = preparation.plan_for(
            project_id="pj",
            project_root=str(root),
            asset_id="fig.pdf",
            stem="fig",
            script="fig.py",
            entry="__main__",
            original_artifact="fig.pdf",
        )
        svc.register(plan)
        entry = svc._entry(plan.plan_id)
        entry.result.status = preparation.STATUS_RUNNING  # 线程已过检查、正在解析解释器 / 取会话
        entry.thread = threading.current_thread()
        before = list(killed)
        assert svc.cancel(plan.plan_id, "pj")["accepted"] is True
        assert killed == before  # 还没取到会话：只立标志
        return plan.plan_id

    mine = _FakeWorker(root)
    owned_plan = cancelled_before_taking()
    svc.note_owner(owned_plan, mine, True)
    assert killed == [mine]  # 自己新建的：取到那一刻就关

    theirs = _FakeWorker(root)
    waiter_plan = cancelled_before_taking()
    svc.note_owner(waiter_plan, theirs, False)
    assert killed == [mine]  # 别人的会话：不碰
    _, result = svc.get(waiter_plan, "pj")
    assert result.status == preparation.STATUS_CANCELLED  # 本计划不再等它


def test_the_app_runner_reports_ownership_to_the_preparation_service(tmp_path, monkeypatch):
    """`_preparation_runner` 把 `on_acquired` 接到 `PreparationService.note_owner`：真 pool 的 `acquire` 一给出
    `created`，取消就知道该不该关。"""
    root = _project(tmp_path, "p")
    plan = preparation.plan_for(
        project_id="pj",
        project_root=str(root),
        asset_id="fig.pdf",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact="fig.pdf",
    )
    preparation.SERVICE.register(plan)
    worker = _FakeWorker(root)

    def build_owned(script, root_, entry, **kw):
        kw["on_acquired"](worker, True)
        return worker, {}, True

    monkeypatch.setattr(engine_pool, "build_owned", build_owned)
    try:
        m._preparation_runner(plan)
        entry = preparation.SERVICE._entry(plan.plan_id)
        assert entry.acquired is True and entry.owned is worker
    finally:
        preparation.SERVICE.reset_for_tests()


def test_a_successful_attempt_reports_the_figures_it_captured(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    assert report["captured"] == []  # 还没有任何执行
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    final = _terminal(client, sid, timeout=10.0)
    assert final["phase"] == "completed"
    # 替身 build 响应里的那份描述符
    assert final["captured"] == [{"stem": "fig", "script": "fig.py"}]


def test_an_attempt_without_figures_reports_no_captured_figures(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    fake_pool["build_resp"] = lambda: {
        "ok": True,
        "stems": {},
        "descriptors": [{"stem": "x", "script": "fig.py"}],
        "runtime": {"pid": 4242},
    }
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    final = _terminal(client, sid, timeout=10.0)
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["captured"] == []  # 执行完成 ≠ 首图成功：没有图就不给「进入编辑」的东西


def _no_figure_report(client, root, source: str) -> dict:
    (root / "fig.py").write_text(source, encoding="utf-8")
    report = _create(client, {"script": "fig.py"}).get_json()
    assert report["no_figure_hint"] is None  # 还没有任何执行：没有「没出图」可解释
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    return _terminal(client, sid, timeout=10.0)


_PILLOW_SRC = "from PIL import Image\nImage.new('RGB', (4, 4)).save('out.png')\n"


def _writes_out_png(root):
    """假池里没有真的跑脚本：build_resp 在「运行」时代它把输出文件写出来。"""

    def build():
        (root / "out.png").write_bytes(b"x")
        return {"ok": True, "stems": {}, "descriptors": [], "runtime": {"pid": 1}}

    return build


def _sandbox_factory(root, box_dir):
    def factory():
        w = _FakeWorker(Path(root))
        w.spec = dataclasses.replace(w.spec, sandbox=str(box_dir))
        return w

    return factory


def test_a_pillow_script_that_wrote_into_the_default_sandbox_gets_the_reason_but_no_button(
    client, tmp_path, fake_pool, sessions
):
    """用户的原场景：默认沙盒模式，相对路径写进这次尝试的会话沙盒 -> 出原因句，`in_project` 为 false。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    box = tmp_path / "box"
    box.mkdir()
    fake_pool["worker_factory"] = _sandbox_factory(root, box)

    def build():
        (box / "out.png").write_bytes(b"x")
        return {"ok": True, "stems": {}, "descriptors": [], "runtime": {"pid": 1}}

    fake_pool["build_resp"] = build
    final = _no_figure_report(client, root, _PILLOW_SRC)
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["no_figure_hint"] == {
        "kind": "raster_script",
        "library": "pillow",
        "in_project": False,
    }
    assert final["facts"] == {"execution_finished": True, "figure_captured": False}


def test_a_pillow_script_that_wrote_nothing_in_the_sandbox_gets_no_hint(
    client, tmp_path, fake_pool, sessions
):
    root = _project(tmp_path, "p")
    _open(client, root)
    box = tmp_path / "box"
    box.mkdir()
    fake_pool["worker_factory"] = _sandbox_factory(root, box)
    fake_pool["build_resp"] = _writes_out_png(root)  # 写到了项目里、没写进沙盒：不是沙盒 cwd 的证据
    final = _no_figure_report(client, root, _PILLOW_SRC)
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["no_figure_hint"] is None


def test_a_pillow_script_run_in_project_mode_that_wrote_the_file_is_in_project(
    client, tmp_path, fake_pool, sessions
):
    from tavotto.engine import workdir

    root = _project(tmp_path, "p")
    _open(client, root)
    workdir.set_mode(root, "project")
    fake_pool["build_resp"] = _writes_out_png(root)
    final = _no_figure_report(client, root, _PILLOW_SRC)
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["no_figure_hint"] == {
        "kind": "raster_script",
        "library": "pillow",
        "in_project": True,
    }


def test_a_pillow_helper_never_called_gets_no_hint_even_in_project_mode(
    client, tmp_path, fake_pool, sessions
):
    from tavotto.engine import workdir

    root = _project(tmp_path, "p")
    _open(client, root)
    workdir.set_mode(root, "project")
    fake_pool["build_resp"] = lambda: {
        "ok": True,
        "stems": {},
        "descriptors": [],
        "runtime": {"pid": 1},
    }  # 没写任何文件
    final = _no_figure_report(
        client,
        root,
        "from PIL import Image\ndef unused():\n    Image.new('RGB', (4, 4)).save('out.png')\n",
    )
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["no_figure_hint"] is None


def test_a_matplotlib_script_without_figures_gets_no_hint(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    _open(client, root)
    fake_pool["build_resp"] = lambda: {
        "ok": True,
        "stems": {},
        "descriptors": [],
        "runtime": {"pid": 1},
    }
    final = _no_figure_report(
        client, root, "import matplotlib.pyplot as plt\nplt.subplots()\nplt.close('all')\n"
    )
    assert final["outcome"]["kind"] == "execution_finished_no_figure"
    assert final["no_figure_hint"] is None


class _KillableWorker(_FakeWorker):
    force_killed = False

    def force_kill(self):
        self.force_killed = True


def test_a_cancel_force_kills_an_owned_worker_that_already_left_the_pool(
    tmp_path, fake_pool, monkeypatch
):
    """本计划新建的会话在 Stop 之前已被 watcher / invalidate 摘出池子：`force_cancel(expected_worker)` 回 False
    （池里没有它），池侧只会优雅关闭、排在 build 后面——取消必须自己 `force_kill()` 这一条，否则脚本继续跑。"""
    root = _project(tmp_path, "p")
    svc = preparation.PreparationService()
    monkeypatch.setattr(engine_pool, "force_cancel", lambda script, root, **kw: False)
    plan = preparation.plan_for(
        project_id="pj",
        project_root=str(root),
        asset_id="fig.pdf",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact="fig.pdf",
    )
    svc.register(plan)
    entry = svc._entry(plan.plan_id)
    entry.result.status = preparation.STATUS_RUNNING
    entry.thread = threading.current_thread()
    mine = _KillableWorker(root)
    svc.note_owner(plan.plan_id, mine, True)
    assert svc.cancel(plan.plan_id, "pj")["accepted"] is True
    assert mine.force_killed, "已离池的自有会话没有被硬杀"

    # 取消早于取到会话、取到那一刻会话已离池：同一条规则
    plan2 = preparation.plan_for(
        project_id="pj",
        project_root=str(root),
        asset_id="fig.pdf",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact="fig.pdf",
    )
    svc.register(plan2)
    e2 = svc._entry(plan2.plan_id)
    e2.result.status = preparation.STATUS_RUNNING
    e2.thread = threading.current_thread()
    assert svc.cancel(plan2.plan_id, "pj")["accepted"] is True
    late = _KillableWorker(root)
    svc.note_owner(plan2.plan_id, late, True)
    assert late.force_killed
