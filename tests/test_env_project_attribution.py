"""环境相关的后端事件分得清属于哪个项目（issue #606 第 2 / 3 条 + 同形清扫）。

判据的主语：
* `engine.environment_adopted` / `native.session` 这两条 SSE——**没有所属项目时不发**。以前以空 `pj`
  群发，前端按项目挡事件的闸对「没有 pj」一律放行，每个标签页都收到；
* 重建受管环境的进度 id——**每次重建一个**，由发起方在发请求之前给出（32 位小写十六进制）。以前固定
  `"managed-rebuild"`，两个项目同时重建时进度分不清是谁的；
* 已被占用 / 格式不对的 id 拒收，线程起来之前 `progress(id)` 就是 `preparing`（网络层失败后问实况用）。

每条都带「有所属项目时照发 / 合格的 id 照收」的对照：否则「一条都没发」会因为从来没人触发而恒绿。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import deprepair

pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


@pytest.fixture
def published(monkeypatch):
    out: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda ev, data: out.append((ev, dict(data))))
    return out


ENTRY = {"id": "abc123", "source": "conda", "label": "lab", "python_version": "3.12.1"}


# ------------------------------------------------------------------ 第 2 条


def test_an_adoption_for_a_project_that_is_not_open_is_not_broadcast(tmp_path, published):
    closed = tmp_path / "closed-project"
    closed.mkdir()
    m._publish_user_environment_adopted(str(closed), ENTRY)
    assert published == [], "路径不在 PROJECTS 里：不许以空 pj 群发"


def test_an_adoption_for_an_open_project_goes_to_that_project_only(project, published):
    """对照：项目开着 → 恰好一条，pj 是它的；不带路径。"""
    pid = m.open_project(str(project))["id"]
    m._publish_user_environment_adopted(str(project), ENTRY)
    assert [(ev, data["pj"]) for ev, data in published] == [("engine.environment_adopted", pid)]
    assert str(project) not in repr(published)


# ------------------------------------------------------------------ 同形清扫：native.session


class _Session:
    session_id = "s-1"
    project_root = "/nowhere"

    def public_state(self):
        return {"session_id": self.session_id}


def test_a_native_event_whose_project_cannot_be_named_is_not_broadcast(monkeypatch, published):
    def gone(path):
        raise OSError("目录不在了")

    monkeypatch.setattr(m, "_project_id", gone)
    m._native_session_event(_Session(), {"kind": "state"})
    assert published == []


def test_a_native_event_with_a_project_carries_its_pj(monkeypatch, published):
    """对照：算得出 pj 就照发，带上它。"""
    monkeypatch.setattr(m, "_project_id", lambda path: "p123")
    m._native_session_event(_Session(), {"kind": "state"})
    assert [(ev, data["pj"]) for ev, data in published] == [("native.session", "p123")]


# ------------------------------------------------------------------ 第 3 条


def test_two_projects_rebuilding_at_once_keep_their_progress_apart(tmp_path, monkeypatch):
    """两个项目同时重建：各自的 id 各自的进度，事件的 plan_id 不串。重建本身换成可控的替身——主语是
    进度 id 的归属，不是建环境。两个线程在同一道栅栏上会合（都进了 creating_env 才过得去），主线程
    按事件等，不轮询、不 sleep。"""
    running = threading.Event()
    both_running = threading.Barrier(2, action=running.set, timeout=10)
    release = threading.Event()
    done = {"a" * 32: threading.Event(), "b" * 32: threading.Event()}

    def fake_generation(job, cancel_ev):
        job.emit(deprepair.STATE_CREATING_ENV)
        both_running.wait()
        release.wait(10)
        job.emit(deprepair.STATE_DONE)
        return {"ok": True}

    monkeypatch.setattr(deprepair, "_run_generation", fake_generation)
    monkeypatch.setattr(deprepair.managedenv, "installed_requirements", lambda root: [])
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    events: list[dict] = []
    lock = threading.Lock()

    def on_event(p):
        with lock:
            events.append(dict(p))
        if p.get("state") == deprepair.STATE_DONE and p.get("plan_id") in done:
            done[p["plan_id"]].set()

    id_a, id_b = "a" * 32, "b" * 32
    assert deprepair.rebuild_managed_async(a, on_event, progress_id=id_a) == id_a
    assert deprepair.rebuild_managed_async(b, on_event, progress_id=id_b) == id_b
    assert running.wait(10), "两条重建没能同时在跑"
    # 同一时刻两条都在跑，各是各的
    assert deprepair.progress(id_a)["state"] == deprepair.STATE_CREATING_ENV
    assert deprepair.progress(id_b)["state"] == deprepair.STATE_CREATING_ENV
    release.set()
    assert done[id_a].wait(10) and done[id_b].wait(10)
    assert deprepair.progress(id_a)["state"] == deprepair.STATE_DONE
    assert deprepair.progress(id_b)["state"] == deprepair.STATE_DONE
    ids = {e["plan_id"] for e in events}
    assert ids == {id_a, id_b}, ids


def test_a_claimed_id_reads_preparing_before_the_thread_starts():
    """网络层失败后前端来问实况：已经到了后端的那次，`progress(id)` 回 preparing，不是 idle。"""
    pid = deprepair.claim_rebuild_progress_id("c" * 32)
    assert pid == "c" * 32
    assert deprepair.progress(pid)["state"] == deprepair.STATE_PREPARING
    # 从没到后端的 id：idle
    assert deprepair.progress("d" * 32)["state"] == "idle"


@pytest.mark.parametrize(
    "raw", ["managed-rebuild", "A" * 32, "a" * 31, "a" * 33, "../" + "a" * 29, "g" * 32]
)
def test_a_malformed_rebuild_id_is_refused(raw):
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.claim_rebuild_progress_id(raw)
    assert err.value.code == deprepair.ERROR_PROGRESS_ID_INVALID


def test_an_id_already_in_use_is_refused():
    pid = deprepair.claim_rebuild_progress_id("e" * 32)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.claim_rebuild_progress_id(pid)
    assert err.value.code == deprepair.ERROR_PROGRESS_ID_INVALID


def test_no_id_given_means_the_backend_makes_one_in_the_same_shape():
    pid = deprepair.claim_rebuild_progress_id("")
    assert deprepair.REBUILD_PROGRESS_ID_RE.match(pid)


def test_the_endpoint_echoes_the_id_and_refuses_a_bad_one(client, project, monkeypatch):
    m.open_project(str(project))
    started = []
    # 只数重建线程（打开项目会起 watcher 线程）：重建那一段换成记账，线程在 start() 里同步跑完，
    # 断言时它一定已经记上了（不赌线程调度）
    monkeypatch.setattr(
        deprepair,
        "_rebuild_guarded",
        lambda project, on_event, progress_id: started.append(progress_id),
    )

    class _SyncThread:
        def __init__(self, target, daemon, name):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(deprepair.threading, "Thread", _SyncThread)
    ok = client.post("/api/engine/environment/managed/rebuild", json={"progress_id": "f" * 32})
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json() == {"started": True, "progress_id": "f" * 32}
    bad = client.post("/api/engine/environment/managed/rebuild", json={"progress_id": "nope"})
    assert bad.status_code == 400
    assert bad.get_json()["code"] == deprepair.ERROR_PROGRESS_ID_INVALID
    assert started == ["f" * 32], "拒收的那次一个线程都不起"
    none = client.post("/api/engine/environment/managed/rebuild", json={})
    assert deprepair.REBUILD_PROGRESS_ID_RE.match(none.get_json()["progress_id"])


def test_the_sync_rebuild_reports_its_own_id(tmp_path, monkeypatch):
    monkeypatch.setattr(deprepair, "_run_generation", lambda job, ev: {"ok": False})
    out = deprepair.rebuild_managed(Path(tmp_path))
    assert deprepair.REBUILD_PROGRESS_ID_RE.match(out["progress_id"])


GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "rebuild_progress_id.json").read_text(encoding="utf-8")
)


def test_the_rebuild_id_format_is_the_golden_pair():
    """同源对的后端那一侧：格式与 `tests/golden/rebuild_progress_id.json` 一字不差，向量逐条过
    （前端那一侧在 `web/src/store/rebuildProgressId.golden.test.ts` 读同一份）。空串是「没给」，由后端生成。"""
    assert deprepair.REBUILD_PROGRESS_ID_RE.pattern == GOLDEN["pattern"]
    for raw in GOLDEN["accept"]:
        assert deprepair.claim_rebuild_progress_id(raw) == raw
    for raw in [r for r in GOLDEN["reject"] if r]:
        with pytest.raises(deprepair.RepairError):
            deprepair.claim_rebuild_progress_id(raw)
