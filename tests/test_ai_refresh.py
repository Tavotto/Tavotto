"""AI 改完代码之后的统一刷新（ADR 0041 §2）。

守的是一条**确定性**的后端路径：文件真的变了 → 作废 worker → 统一刷新
（reason=ai）→ `panel.file_changed`（reason=ai）→ `ai.done` 带 `refresh` 结局。
watcher 仍是兜底，但同一次写入它**不再**重复那三件事（`absorb`）。

刷新失败不把 AI 修改伪装成全部成功，也不把 AI 会话记成失败——两件事分开记。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    ai_bridge,
    ai_history,
    project_refresh as engine_refresh,
    project_watch as engine_watch,
)

SCRIPT = """import matplotlib.pyplot as plt
from pathlib import Path


def main():
    Path("RAN.txt").write_text("executed", encoding="utf-8")
    fig = plt.figure()
    fig.savefig("{stem}.pdf")
"""


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture
def sse_spy(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda ev, data: events.append((ev, data)))
    return events


@pytest.fixture
def invalidated(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(m.engine_pool, "invalidate", lambda script, d: calls.append((script, d)))
    return calls


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(path)
    doc.close()


def _project(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT.format(stem="Fig1"), encoding="utf-8")
    _pdf(figs / "Fig1.pdf")
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    return figs


def _open(client, figs, default=True):
    body = client.post(
        "/api/projects/open", json={"path": str(figs), "default": default}
    ).get_json()
    return m.PROJECTS[body["id"]]


def _manual_watcher(ctx, *, debounce=0.0, clock=None):
    """把项目的 watcher 换成用例自己驱动的那一个，**并登记进注册表**——
    `_after_ai_change` 是经 `engine_watch.absorb(path)` 找它的。"""
    engine_watch.stop()
    calls: dict[str, list] = {"refresh": [], "script_changed": []}
    sink = engine_watch.WatchSink(
        refresh=lambda paths: (
            calls["refresh"].append(list(paths)),
            m.refresh_project(ctx, reason="watcher", changed_paths=paths),
        ),
        script_changed=lambda scripts: calls["script_changed"].append(list(scripts)),
    )
    w = engine_watch.ProjectWatcher(
        ctx,
        sink=sink,
        interval=0.01,
        debounce=debounce,
        max_batch=1e9,
        clock=clock or FakeClock(),
    )
    w.prime()
    with engine_watch._lock:
        engine_watch._watchers[engine_watch._key(ctx.path)] = w
    return w, calls


def _ai_edit(figs, name="fig1.py"):
    """模拟 AI 改了脚本：追加一行（size 变、mtime 变）。"""
    p = figs / name
    p.write_text(p.read_text(encoding="utf-8") + "\n# edited by ai\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# refresh_outcome：结局摘要
# ---------------------------------------------------------------------------
class TestRefreshOutcome:
    def test_unchanged_file_is_skipped_and_never_calls_refresh(self):
        called = []
        out = ai_bridge.refresh_outcome(lambda s: called.append(s), "fig1.py", False)
        assert out == {"status": "skipped"}
        assert called == []

    def test_no_hook_is_said_out_loud(self):
        assert ai_bridge.refresh_outcome(None, "fig1.py", True) == {"status": "not_wired"}

    def test_ok_carries_only_enums_and_booleans(self):
        out = ai_bridge.refresh_outcome(
            lambda s: {
                "registry": {
                    "added_scripts": ["x.py"],
                    "removed_scripts": [],
                    "changed_scripts": [],
                },
                "assets": {"added": [], "removed": [], "changed": []},
                "published": ["registry.changed"],
            },
            "fig1.py",
            True,
        )
        assert out == {
            "status": "ok",
            "registry_changed": True,
            "assets_changed": False,
            "published": ["registry.changed"],
        }
        # 摘要里不许带脚本名 / 路径 / diff
        assert "x.py" not in json.dumps({k: v for k, v in out.items() if k != "published"})

    def test_failure_keeps_the_stable_code_and_does_not_raise(self):
        def boom(script):
            raise engine_refresh.RefreshError("scan_failed", "扫描失败", {"reason": "x"})

        assert ai_bridge.refresh_outcome(boom, "fig1.py", True) == {
            "status": "failed",
            "code": "scan_failed",
        }

    def test_unexpected_exception_is_a_generic_failure(self):
        def boom(script):
            raise ValueError("nope")

        assert ai_bridge.refresh_outcome(boom, "fig1.py", True) == {
            "status": "failed",
            "code": "refresh_failed",
        }


# ---------------------------------------------------------------------------
# app._after_ai_change：确定性路径 + watcher 去重
# ---------------------------------------------------------------------------
class TestAfterAiChange:
    def test_backend_path_does_the_three_things_once_and_watcher_stays_quiet(
        self, client, tmp_path, sse_spy, invalidated
    ):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        w, calls = _manual_watcher(ctx)
        sse_spy.clear()

        _ai_edit(figs)
        result = m._after_ai_change(ctx, "fig1.py")

        assert result["reason"] == "ai" and result["panel_event"] is True
        assert invalidated == [("fig1.py", str(ctx.path))]
        kinds = [ev for ev, _ in sse_spy]
        assert kinds.count("panel.file_changed") == 1
        payload = next(d for ev, d in sse_spy if ev == "panel.file_changed")
        assert payload == {"scripts": ["fig1.py"], "stems": ["Fig1"], "pj": ctx.id, "reason": "ai"}
        # 内容改了但注册表关系没变：统一刷新一条事件都不发
        assert "registry.changed" not in kinds and "assets.changed" not in kinds

        # watcher 随后看到同一次写入：不刷新、不再发 panel.file_changed
        w.poll()
        w.poll()
        assert calls == {"refresh": [], "script_changed": []}
        assert [ev for ev, _ in sse_spy].count("panel.file_changed") == 1

        # 用户紧接着再改一次：watcher 照常触发——absorb 认的是签名，不是时间窗
        _ai_edit(figs)
        w.poll()
        assert calls["refresh"] and calls["script_changed"] == [["fig1.py"]]

    def test_a_write_already_pending_in_the_watcher_is_still_fresh(
        self, client, tmp_path, sse_spy, invalidated
    ):
        """watcher 已经把这次写入拍进 pending、但防抖还没到期（真实场景里最常见的
        一档：CLI 刚退出、watcher 上一轮刚拍过）。这时快照里的签名已经是新的——
        只比快照会误判成「watcher 处理过了」，AI 路径就一件事都不做，而 pending 里
        那一批随后照样结算：用户看到的是提示迟了两秒、还是两份。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        clock = FakeClock()
        w, calls = _manual_watcher(ctx, debounce=0.5, clock=clock)
        sse_spy.clear()

        _ai_edit(figs)
        w.poll()  # 拍进 pending，防抖没到期 → 不结算
        assert calls == {"refresh": [], "script_changed": []}

        result = m._after_ai_change(ctx, "fig1.py")
        assert result["panel_event"] is True, "躺在 pending 里的写入也算没消化"
        assert invalidated == [("fig1.py", str(ctx.path))]
        assert [ev for ev, _ in sse_spy].count("panel.file_changed") == 1

        clock.t += 5.0
        w.poll()  # 防抖到期：那一批已经被 absorb 摘掉，不再结算
        w.poll()
        assert calls == {"refresh": [], "script_changed": []}
        assert [ev for ev, _ in sse_spy].count("panel.file_changed") == 1

    def test_no_user_script_is_executed_and_no_probe(self, client, tmp_path, sse_spy, monkeypatch):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _manual_watcher(ctx)
        monkeypatch.setattr(
            m.engine_probe, "probe", lambda *a, **k: pytest.fail("AI 路径不许 probe")
        )
        monkeypatch.setattr(
            m.engine_probe, "probe_and_register", lambda *a, **k: pytest.fail("AI 路径不许 probe")
        )
        _ai_edit(figs)
        m._after_ai_change(ctx, "fig1.py")
        assert not (figs / "RAN.txt").exists(), "统一刷新只读 AST，不许跑用户脚本"

    def test_watcher_first_race_does_not_double_publish(
        self, client, tmp_path, sse_spy, invalidated
    ):
        """CLI 写完文件之后又跑了几秒：watcher 先结算了这一批。AI 路径此时
        只补一次刷新（无差异零事件），不再作废、不再发第二份 panel.file_changed。"""
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        w, calls = _manual_watcher(ctx)
        sse_spy.clear()

        _ai_edit(figs)
        w.poll()  # watcher 先看到：作废 + 刷新 + panel.file_changed 都是它做的
        assert calls["script_changed"] == [["fig1.py"]]
        assert invalidated == [("fig1.py", str(ctx.path))]
        invalidated.clear()
        n_before = len([ev for ev, _ in sse_spy if ev == "panel.file_changed"])

        result = m._after_ai_change(ctx, "fig1.py")
        assert result["panel_event"] is False
        assert invalidated == [], "watcher 已作废过，AI 路径不再作废一次"
        assert len([ev for ev, _ in sse_spy if ev == "panel.file_changed"]) == n_before

    def test_without_a_watcher_the_backend_path_does_everything(
        self, client, tmp_path, sse_spy, invalidated
    ):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        engine_watch.stop()  # 没有 watcher（absorb 回 None）
        sse_spy.clear()
        _ai_edit(figs)
        result = m._after_ai_change(ctx, "fig1.py")
        assert result["panel_event"] is True
        assert invalidated == [("fig1.py", str(ctx.path))]
        assert [ev for ev, _ in sse_spy].count("panel.file_changed") == 1

    def test_new_script_from_ai_shows_up_in_the_registry_diff(self, client, tmp_path, sse_spy):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _manual_watcher(ctx)
        sse_spy.clear()
        (figs / "fig2.py").write_text(SCRIPT.format(stem="Fig2"), encoding="utf-8")
        result = m._after_ai_change(ctx, "fig2.py")
        assert result["registry"]["added_scripts"] == ["fig2.py"]
        assert "registry.changed" in [ev for ev, _ in sse_spy]
        assert ai_bridge.refresh_outcome(lambda s: result, "fig2.py", True)["registry_changed"]

    def test_closed_project_is_a_stable_failure_code(self, client, tmp_path):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        m.close_project(ctx.id)
        with pytest.raises(engine_refresh.RefreshError) as exc:
            m._after_ai_change(ctx, "fig1.py")
        assert exc.value.code == "project_closed"
        out = ai_bridge.refresh_outcome(lambda s: m._after_ai_change(ctx, s), "fig1.py", True)
        assert out == {"status": "failed", "code": "project_closed"}

    def test_refresh_failure_is_reported_not_hidden(self, client, tmp_path, monkeypatch):
        figs = _project(tmp_path)
        ctx = _open(client, figs)
        _manual_watcher(ctx)
        (figs / "tavotto_registry.json").write_text("{not json", encoding="utf-8")
        _ai_edit(figs)
        out = ai_bridge.refresh_outcome(lambda s: m._after_ai_change(ctx, s), "fig1.py", True)
        assert out["status"] == "failed" and out["code"] in (
            "scan_failed",
            "registry_reload_failed",
        )

    def test_two_projects_are_isolated(self, client, tmp_path, sse_spy, invalidated):
        figs_a = _project(tmp_path, "a")
        figs_b = _project(tmp_path, "b")
        ctx_a = _open(client, figs_a)
        ctx_b = _open(client, figs_b, default=False)
        engine_watch.stop()
        w_b, calls_b = _manual_watcher(ctx_b)
        sse_spy.clear()

        _ai_edit(figs_a)
        m._after_ai_change(ctx_a, "fig1.py")
        assert all(d.get("pj") == ctx_a.id for _, d in sse_spy)
        assert invalidated == [("fig1.py", str(ctx_a.path))]
        w_b.poll()
        assert calls_b == {"refresh": [], "script_changed": []}


# ---------------------------------------------------------------------------
# ai_bridge.run：changed true / false / refresh failure，ai.done 与历史库的边界
# ---------------------------------------------------------------------------
EDIT_CLI = (
    "import sys, pathlib; p = pathlib.Path(sys.argv[1]); "
    "p.write_text(p.read_text(encoding='utf-8') + '\\n# ai\\n', encoding='utf-8')"
)
NOOP_CLI = "pass"


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """把 Agent CLI 换成一段 python：改 / 不改脚本，不联网、不装东西。"""
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    state = {"code": EDIT_CLI}

    def _cmd(agent_id, prompt, figures_dir, model=None, effort=None, endpoint=None):
        # 脚本名从 prompt 里拿不到；用例只有 fig1.py 一个脚本
        return [sys.executable, "-c", state["code"], str(Path(figures_dir) / "fig1.py")], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    return state


def _wait_done(sid, timeout=10.0):
    """等 AI 会话**真的收尾**——等 pump 线程退出，不是等够几毫秒（issue #277）。

    以前这里轮询到 `SESSIONS[sid]["status"] != "running"` 就返回，再
    `time.sleep(0.05)`「让 pump 线程把 record_end / emit 做完」——**拿固定 sleep
    当跨线程屏障**。可 `status` 是在 `_pump` 里先翻的，`ai.done` 排在它后面，中间
    还隔着一次 `ai_history.record_end` 的 SQLite 写。runner 一忙 50ms 就不够，调用方
    紧接着的 `next(d for n, d in events if n == "ai.done")` 便在空序列上炸成
    `StopIteration`——CI 的 3.13 腿上见过两次，分别来自两个毫无交集的 PR（一个纯
    前端、一个纯引擎），自变量只剩机器负载。

    换成等 pump 线程收尾。这是**充要**的：`emit` 就是 `on_event` 的同步调用
    （`ai_bridge.py`「emit = on_event or …」），而 `emit("ai.done", …)` 是 `_pump`
    的最后一步——线程退出 ⇔ `ai.done` 已经发完。**加大 sleep 不是修**，那只是把
    窗口挪远一点，路径本身还是在赌时序。
    """
    deadline = time.monotonic() + timeout
    # 线程在 `run()` 返回 sid 之前就起了；找不到只有一种可能——它已经收尾了。
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    while time.monotonic() < deadline:
        if ai_bridge.SESSIONS[sid]["status"] != "running":
            if pump is not None:
                pump.join(max(0.0, deadline - time.monotonic()))
                assert not pump.is_alive(), "pump 线程没有在超时内收尾"
            return ai_bridge.SESSIONS[sid]
        time.sleep(0.01)
    raise AssertionError("AI 会话没有结束")


class TestRunWiresRefresh:
    def test_changed_true_refreshes_before_ai_done(self, tmp_path, fake_cli):
        figs = _project(tmp_path)
        order: list[str] = []
        events: list[tuple[str, dict]] = []

        def on_changed(script):
            order.append(f"refresh:{script}")
            return {
                "registry": {"added_scripts": []},
                "assets": {"changed": ["Fig1.pdf"]},
                "published": ["assets.changed"],
            }

        def on_event(name, data):
            order.append(name)
            events.append((name, data))

        sid = ai_bridge.run(
            "codex", "fig1.py", "改标题", str(figs), on_event=on_event, on_changed=on_changed
        )
        sess = _wait_done(sid)
        assert sess["changed"] is True and sess["status"] == "done"
        done = next(d for n, d in events if n == "ai.done")
        assert done["refresh"] == {
            "status": "ok",
            "registry_changed": False,
            "assets_changed": True,
            "published": ["assets.changed"],
        }
        assert order.index("refresh:fig1.py") < order.index("ai.done"), "刷新要在 ai.done 之前做完"
        row = ai_history.get(sid)
        assert row["changed"] is True and row["refresh"] == done["refresh"]

    def test_changed_false_skips_refresh(self, tmp_path, fake_cli):
        fake_cli["code"] = NOOP_CLI
        figs = _project(tmp_path)
        called = []
        events: list[tuple[str, dict]] = []
        sid = ai_bridge.run(
            "codex",
            "fig1.py",
            "p",
            str(figs),
            on_event=lambda n, d: events.append((n, d)),
            on_changed=lambda s: called.append(s),
        )
        sess = _wait_done(sid)
        assert sess["changed"] is False and called == []
        done = next(d for n, d in events if n == "ai.done")
        assert done["refresh"] == {"status": "skipped"}
        assert ai_history.get(sid)["refresh"] == {"status": "skipped"}

    def test_wait_done_barrier_survives_a_slow_record_end(self, tmp_path, fake_cli, monkeypatch):
        """`_wait_done` 的屏障不许是「等够几毫秒」（issue #277）。

        往 `ai_history.record_end` 里注入一个**远大于**旧实现那 50ms 的延迟——它正好
        落在 `_pump` 里 `status` 翻转与 `emit("ai.done")` 之间，也就是旧实现盲等的那
        一段。屏障只要还是固定 sleep，这条用例就**必然**红（不是偶发、与机器快慢无关）；
        等 pump 线程收尾则与这个延迟完全无关。

        **不用「跑 N 次都绿」当验证**——那是样本，证明不了「不再依赖时序」。
        """
        real_record_end = ai_history.record_end

        def slow_record_end(*a, **kw):
            time.sleep(0.4)  # 旧屏障是 0.05s：这里稳稳地越过它
            return real_record_end(*a, **kw)

        monkeypatch.setattr(ai_history, "record_end", slow_record_end)
        fake_cli["code"] = NOOP_CLI
        figs = _project(tmp_path)
        events: list[tuple[str, dict]] = []
        sid = ai_bridge.run(
            "codex",
            "fig1.py",
            "p",
            str(figs),
            on_event=lambda n, d: events.append((n, d)),
            on_changed=lambda s: None,
        )
        _wait_done(sid)
        assert [n for n, _ in events if n == "ai.done"], (
            "`_wait_done` 返回时 ai.done 还没发出来——屏障还是在等时间，不是等那件事"
        )

    def test_refresh_failure_does_not_fail_the_ai_session(self, tmp_path, fake_cli):
        figs = _project(tmp_path)
        events: list[tuple[str, dict]] = []

        def on_changed(script):
            raise engine_refresh.RefreshError("scan_failed", "扫描失败", {"reason": "x"})

        sid = ai_bridge.run(
            "codex",
            "fig1.py",
            "p",
            str(figs),
            on_event=lambda n, d: events.append((n, d)),
            on_changed=on_changed,
        )
        sess = _wait_done(sid)
        assert sess["status"] == "done" and sess["changed"] is True
        done = next(d for n, d in events if n == "ai.done")
        assert done["changed"] is True
        assert done["refresh"] == {"status": "failed", "code": "scan_failed"}
        assert ai_history.get(sid)["refresh"] == {"status": "failed", "code": "scan_failed"}

    def test_get_after_restart_carries_refresh_from_history(self, tmp_path, fake_cli):
        figs = _project(tmp_path)
        sid = ai_bridge.run(
            "codex",
            "fig1.py",
            "p",
            str(figs),
            on_changed=lambda s: {"registry": {}, "assets": {}, "published": []},
        )
        _wait_done(sid)
        ai_bridge.SESSIONS.pop(sid)  # 模拟后端重启：只剩 sidecar + 历史库
        got = ai_bridge.get(sid)
        assert got["refresh"]["status"] == "ok"


def test_api_ai_run_wires_the_backend_refresh(client, tmp_path, monkeypatch, fake_cli):
    """端点把 `on_changed` 接到 `_after_ai_change`——判据看源码里的接线，
    不看 agent 是否真的装着（那是另一批用例的事）。"""
    import inspect

    src = inspect.getsource(m.api_ai_run)
    assert "on_changed=" in src and "_after_ai_change(ctx, script)" in src
    # 老的 `str(require_project())` 换成了 ctx.path：同一个 ctx 既给 run 也给刷新
    assert "str(ctx.path)" in src
