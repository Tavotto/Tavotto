"""Compatibility Bridge Session 5：素材库普通入口的后端面。

三件事的看护：

* **取消真正终止工作**（负向反证 #3 的看护对象）：
  `POST /api/registry/probe/cancel` 置取消标志并 `pool.force_cancel` 硬杀
  在跑的 worker——阻塞中的 probe 请求必须在秒级内以 `execution_cancelled`
  返回、会话从池里消失、注册表零改动。只藏 UI 不杀进程的话，这里的
  sentinel 用例当场红。
* **同一脚本不能并行两个 probe**：第二个请求 409 `probe_in_progress`
  （前端状态机是礼貌，后端这道闸才是兜底）。
* **`GET /api/runtime/assets` 只读**：清单 = 注册表里磁盘无原件的
  (script, stem)；有原件的是 FileAsset 绝不双列；调用绝不执行脚本。

真执行脚本的用例与 test_script_probe 同一条纪律：本进程不 import
matplotlib，桌面侧经 pool 起真 worker。
"""

import json
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    pool as engine_pool,
    probe as engine_probe,
    project_watch as engine_watch,
    registry as engine_registry,
    runtimeasset,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SHOW_ONLY = """\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.show()
"""

# 顶层先画图再睡死：cancel 到达时 build 一定还没结束
SLOW = """\
import time
import matplotlib.pyplot as plt

plt.plot([1, 2, 3])
print("TAVOTTO_TEST_PROBE_STARTED", flush=True)
time.sleep(120)
"""


def write(figs: Path, name: str, source: str) -> Path:
    p = figs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


def write_registry(figs: Path, scripts: dict) -> None:
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": scripts}, ensure_ascii=False), encoding="utf-8"
    )


def _make_project(tmp_path, name="figs") -> Path:
    figs = tmp_path / name
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    return figs


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


# ===========================================================================
# 一、取消：真正终止，不是藏 UI
# ===========================================================================
class TestCancelSemantics:
    def test_cancel_before_any_execution_never_spawns_a_worker(self, tmp_path, monkeypatch):
        """取消先于执行：一个 worker 都不许起（协作取消的最早检查点）。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)

        def bomb(*a, **k):
            raise AssertionError("已取消的 probe 不得 spawn worker")

        monkeypatch.setattr(engine_pool, "get", bomb)
        result = engine_probe.probe(figs, "show_only.py", should_cancel=lambda: True)
        assert result["error"]["code"] == engine_probe.ERROR_CANCELLED
        assert result["stems"] == []

    def test_cancelled_worker_failure_is_not_misreported(self, tmp_path, monkeypatch):
        """cancel 硬杀导致的 WorkerError 必须归类为 execution_cancelled，
        且**不再尝试下一个 entry**——把用户的取消报成「脚本坏了」是撒谎，
        被杀后还接着盲试 entry 等于取消无效。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        cancelled = {"flag": False}
        calls = []

        class FakeWorker:
            def ensure_built(self):
                calls.append("build")
                cancelled["flag"] = True  # 模拟：执行期间 cancel 到达
                raise engine_pool.WorkerError("worker 进程崩溃（无响应）")

        monkeypatch.setattr(engine_pool, "get", lambda *a, **k: FakeWorker())
        result = engine_probe.probe(
            figs,
            "show_only.py",
            entries=["__main__", "main"],
            should_cancel=lambda: cancelled["flag"],
        )
        assert result["error"]["code"] == engine_probe.ERROR_CANCELLED
        assert calls == ["build"]  # 第二个 entry 没有被试

    @pytest.mark.parametrize("fallback", [False, True], ids=["initial", "fallback"])
    @pytest.mark.parametrize("replaced", [False, True], ids=["current", "replacement"])
    def test_cancel_during_acquisition_never_builds_or_retires_a_replacement(
        self, tmp_path, monkeypatch, fallback, replaced
    ):
        """Cancel can find an empty pool while acquisition is still discovering Python.

        The returned worker must be checked before either initial or fallback execution;
        a replacement installed meanwhile is another owner's session and must survive.
        """
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        key = (engine_pool._norm_dir(str(figs)), "show_only.py")
        monkeypatch.setattr(engine_pool, "_workers", {})
        monkeypatch.setattr(engine_probe, "entry_candidates", lambda *a: ["__main__"])
        cancelled = threading.Event()
        events = []

        class Worker:
            def __init__(self, name):
                self.name = name

            def ensure_built(self):
                events.append(f"build:{self.name}")
                if self.name == "initial":
                    raise engine_pool.WorkerError(
                        "missing fixture", code="missing_dependency", module="fixture"
                    )
                pytest.fail("A worker acquired after cancellation must never execute")

            def force_kill(self):
                events.append(f"kill:{self.name}")

            def shutdown(self):
                events.append(f"shutdown:{self.name}")

        initial, acquired, replacement = (Worker(name) for name in ("initial", "acquired", "other"))
        takes = []

        def take(*a, **k):
            takes.append(True)
            if fallback and len(takes) == 1:
                engine_pool._workers[key] = initial
                return initial
            # The cancel endpoint has set its event and attempted a kill before registration.
            cancelled.set()
            assert engine_pool.force_cancel("show_only.py", str(figs)) is False
            engine_pool._workers[key] = replacement if replaced else acquired
            return acquired

        def adopt(*a):
            assert engine_pool._workers.pop(key) is initial
            events.append("adopt")
            return {"ok": True}

        monkeypatch.setattr(engine_pool, "get", take)
        monkeypatch.setattr(engine_pool, "try_project_env", adopt)
        result = engine_probe.probe_and_register(
            figs, "show_only.py", should_cancel=cancelled.is_set
        )
        assert result["error"]["code"] == engine_probe.ERROR_CANCELLED
        assert result["registered"] is False
        assert not (figs / "tavotto_registry.json").exists()
        assert len(takes) == (2 if fallback else 1)
        assert events == (["build:initial", "adopt"] if fallback else []) + ["kill:acquired"]
        assert engine_pool._workers == ({key: replacement} if replaced else {})

    @needs_worker
    def test_cancel_kills_the_running_probe(self, client, tmp_path):
        """负向反证 #3 的 sentinel：cancel 之后，阻塞中的 probe 请求必须
        在远小于 BUILD_TIMEOUT 的时间内返回 execution_cancelled，worker
        会话从池里消失，注册表零改动。"""
        figs = _make_project(tmp_path)
        write(figs, "slow.py", SLOW)
        client.post("/api/projects/open", json={"path": str(figs)})
        done = {}

        def run():
            resp = m.app.test_client().post("/api/registry/probe", json={"script": "slow.py"})
            done["status"] = resp.status_code
            done["json"] = resp.get_json()

        th = threading.Thread(target=run, daemon=True)
        th.start()
        deadline = time.time() + 30
        while time.time() < deadline and not m._PROBES:
            time.sleep(0.05)
        assert m._PROBES, "probe 没有登记到在跑表里"

        # 并发闸：同一脚本的第二个 probe 请求被 409 挡住
        second = client.post("/api/registry/probe", json={"script": "slow.py"})
        assert second.status_code == 409
        assert second.get_json()["code"] == "probe_in_progress"

        # _PROBES only proves admission; cold interpreter discovery may take over a second.
        # Wait for evidence from inside this script, separately from the early-cancel tests.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            worker = engine_pool.peek("slow.py", figs)
            if worker and "TAVOTTO_TEST_PROBE_STARTED" in worker.log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("The slow script never reached its execution sentinel")
        resp = client.post("/api/registry/probe/cancel", json={"script": "slow.py"})
        assert resp.get_json()["cancelling"] is True

        th.join(timeout=30)  # SLOW 睡 120s：30s 内返回只能是被杀
        assert not th.is_alive(), "cancel 之后 probe 请求仍未返回（没杀掉）"
        assert done["status"] == 200
        assert done["json"]["error"]["code"] == "execution_cancelled"
        assert done["json"]["registered"] is False
        key = (engine_pool._norm_dir(str(figs)), "slow.py")
        assert key not in engine_pool._workers, "被取消的会话不得留在池里"
        # 项目打开时会静态起草一份注册表；取消的 probe 不许把 slow.py 写进去
        cfg = json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))
        assert "slow.py" not in cfg.get("scripts", {}), "取消不许登记脚本"
        # 幂等：没有在跑的 probe 时取消不是错误
        resp = client.post("/api/registry/probe/cancel", json={"script": "slow.py"})
        assert resp.status_code == 200
        assert resp.get_json()["cancelling"] is False

    def test_cancel_reads_event_and_run_under_one_lock(self, client, tmp_path, monkeypatch):
        """Codex #812 P2：取消端点必须在同一把锁里一起取 event 与 run。

        桩模拟「端点拿到 event 之后、读 runs 之前，运行配置那张表被收」：包一层锁，第一次释放时
        把 runs 清掉。分两次加锁的旧实现会把 run 读成 None，force_cancel 打到同脚本无参数的 worker；
        单锁实现拿到的是登记时的那份 run。（T09b 起 owner 另在 event 置位之后再读，且要求条目仍是同一个
        event——整条收尾的赛跑由下面「输了赛跑」那条钉住，这里只收 runs 表以保 owner 判定仍成立。）
        """
        figs = _make_project(tmp_path)
        client.post("/api/projects/open", json={"path": str(figs)})
        calls = []
        monkeypatch.setattr(
            engine_pool,
            "force_cancel",
            lambda script, root, **kw: calls.append((script, kw)) or True,
        )
        sentinel_run = object()
        real_lock = m._PROBES_LOCK

        class _FinishAfterFirstRelease:
            def __init__(self):
                self.released = 0

            def __enter__(self):
                real_lock.acquire()

            def __exit__(self, *exc):
                real_lock.release()
                self.released += 1
                if self.released == 1:
                    with real_lock:  # 带参数的试运行在两次加锁之间把运行配置那张表收了尾
                        m._PROBE_RUNS.clear()

        with m.app.test_request_context("/", json={}):
            key = (m.current_ctx().id, "slow.py")
        ev = threading.Event()
        owner_worker = object()
        m._PROBES[key] = ev
        m._PROBE_RUNS[key] = sentinel_run
        m._PROBE_OWNERS[key] = (owner_worker, True)
        monkeypatch.setattr(m, "_PROBES_LOCK", _FinishAfterFirstRelease())
        try:
            resp = client.post("/api/registry/probe/cancel", json={"script": "slow.py"})
        finally:
            m._PROBES.pop(key, None)
            m._PROBE_RUNS.pop(key, None)
            m._PROBE_OWNERS.pop(key, None)
        assert resp.get_json()["cancelling"] is True
        assert ev.is_set()
        # 取消只能打到登记时那份配置的会话（run 在第一把锁里与 event 一起取），且只杀这次试运行自己取到的那条
        assert calls == [("slow.py", {"expected_worker": owner_worker, "run": sentinel_run})]

    def test_cancel_after_probe_finished_hits_no_worker(self, client, tmp_path, monkeypatch):
        """输了赛跑（条目已经不在）：不取消、不打到任何 worker，形状与「没有在跑的」一致。"""
        figs = _make_project(tmp_path)
        client.post("/api/projects/open", json={"path": str(figs)})
        calls = []
        monkeypatch.setattr(
            engine_pool, "force_cancel", lambda script, root, **kw: calls.append((script, kw))
        )
        resp = client.post("/api/registry/probe/cancel", json={"script": "slow.py"})
        assert resp.status_code == 200
        assert resp.get_json() == {"cancelling": False}
        assert calls == []


# ===========================================================================
# 二、runtime 素材清单：只读、不双列
# ===========================================================================
class TestRuntimeAssetListing:
    def test_listing_never_executes_scripts(self, client, tmp_path, monkeypatch):
        """结构性看护：GET /api/runtime/assets 绝不 spawn worker。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        write_registry(figs, {"show_only.py": {"entry": "__main__", "stems": ["show_only"]}})
        client.post("/api/projects/open", json={"path": str(figs)})

        def bomb(*a, **k):
            raise AssertionError("清单端点不得执行脚本")

        monkeypatch.setattr(engine_pool, "get", bomb)
        resp = client.get("/api/runtime/assets")
        assert resp.status_code == 200
        (a,) = resp.get_json()["assets"]
        assert a["id"] == "runtime:show_only.py#show_only"
        assert a["script"] == "show_only.py"
        assert a["stem"] == "show_only"
        assert a["cached"] is False
        assert a["descriptor"] is None
        # 没跑过：needs_rerun；机器上连解释器都没有时如实报 missing_environment
        assert a["status"] in {"needs_rerun", "missing_environment"}

    def test_an_unreadable_run_config_store_is_an_explicit_error_not_an_empty_variant_list(
        self, client, tmp_path
    ):
        """Codex 评审 P2（#812 r4214383757）：登记坏 / 新版本写的，清单不得把它吞成「没有变体」。"""
        from tavotto.engine import runconfig

        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        write_registry(figs, {"show_only.py": {"entry": "__main__", "stems": ["show_only"]}})
        client.post("/api/projects/open", json={"path": str(figs)})
        runconfig.put(figs, "show_only.py", ["--k", "1"])
        store = runconfig.store_path(figs)
        store.write_text("not json", encoding="utf-8")
        resp = client.get("/api/runtime/assets")
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "run_config_unreadable"
        store.write_text(json.dumps({"version": runconfig.FORMAT_VERSION + 1}), encoding="utf-8")
        resp = client.get("/api/runtime/assets")
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "run_config_unsupported"

    def test_stems_with_disk_artifacts_are_file_assets_not_runtime(self, tmp_path):
        """同一张图绝不双列：磁盘有原件的 stem 归 FileAsset（scan_panels），
        清单只列没有原件的。负向反证 #2 的邻接看护：runtime 条目不带
        磁盘路径字段，消费方拿不到「假路径」。"""
        figs = _make_project(tmp_path)
        write(figs, "mixed.py", SHOW_ONLY)
        write_registry(figs, {"mixed.py": {"entry": "__main__", "stems": ["on_disk", "live_only"]}})
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(figs / "on_disk.pdf")
        doc.close()
        reg = engine_registry.Registry()
        reg.load(figs)
        (a,) = runtimeasset.list_assets(figs, reg, worker_python="python")
        assert a["stem"] == "live_only"
        assert a["status"] == runtimeasset.STALE_NEEDS_RERUN
        assert "path" not in a and "file" not in a

    def test_pyplot_capture_is_not_shadowed_by_a_stale_same_stem_file(self, tmp_path):
        """Codex 评审 P1（PR #127）：pyplot 捕获**从来没有原件**（figcapture
        工厂钉死的语义），磁盘上同名文件只是旧样本——按文件名巧合把
        runtime 素材让位给它，用户编辑的就是陈旧文件。归属按**捕获来源**
        判（`is_pyplot_capture`），savefig 来源的照旧归 FileAsset 不双列。"""
        from tavotto.engine import figcapture

        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        write_registry(figs, {"show_only.py": {"entry": "__main__", "stems": ["show_only"]}})
        desc = figcapture.build_descriptor(
            script="show_only.py",
            entry="__main__",
            stem="show_only",
            capture_source=figcapture.SOURCE_PYPLOT,
            execution_profile=figcapture.PROFILE_SAFE,
            size_mm=(120.0, 90.0),
            source_fingerprint="sha256:deadbeef",
        ).to_payload()
        svg = tmp_path / "preview.svg"
        svg.write_text("<svg>preview</svg>", encoding="utf-8")
        assert runtimeasset.materialize(figs, desc, svg) is not None
        # 旧样本：同名 PDF 躺在磁盘上（不是这张图写的）
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(figs / "show_only.pdf")
        doc.close()

        reg = engine_registry.Registry()
        reg.load(figs)
        (a,) = runtimeasset.list_assets(figs, reg, worker_python="python")
        assert a["id"] == "runtime:show_only.py#show_only"
        assert a["capture_source"] == "pyplot"
        assert a["descriptor"] is not None

        # 对照：savefig 来源 + 磁盘原件 → 归 FileAsset（不双列）
        write(figs, "saved.py", "def main():\n    pass\n")
        write_registry(
            figs,
            {
                "show_only.py": {"entry": "__main__", "stems": ["show_only"]},
                "saved.py": {"entry": "main", "stems": ["saved"]},
            },
        )
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(figs / "saved.pdf")
        doc.close()
        desc2 = figcapture.build_descriptor(
            script="saved.py",
            entry="main",
            stem="saved",
            capture_source=figcapture.SOURCE_SAVEFIG,
            execution_profile=figcapture.PROFILE_SAFE,
            size_mm=(80.0, 60.0),
            source_fingerprint="sha256:beef",
            original_artifact="saved.pdf",
        ).to_payload()
        assert runtimeasset.materialize(figs, desc2, svg) is not None
        reg.load(figs)
        stems = [x["stem"] for x in runtimeasset.list_assets(figs, reg, worker_python="python")]
        assert stems == ["show_only"]

    def test_bad_registry_pairs_do_not_break_the_listing(self, tmp_path):
        reg = engine_registry.Registry()
        reg.load_data({"scripts": {}})
        assert runtimeasset.list_assets(tmp_path, reg, worker_python="python") == []

    @needs_worker
    def test_probe_then_listing_carries_the_descriptor(self, client, tmp_path):
        """普通入口的数据链：probe 成功 → 清单条目带物化描述符（前端
        「添加到画布」的数据源）与 fresh 状态。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        client.post("/api/projects/open", json={"path": str(figs)})
        try:
            resp = client.post("/api/registry/probe", json={"script": "show_only.py"})
            assert resp.get_json()["error"] is None
            listing = client.get("/api/runtime/assets").get_json()["assets"]
            (a,) = [x for x in listing if x["stem"] == "show_only"]
            assert a["cached"] is True
            assert a["status"] == "fresh"
            assert a["capture_source"] == "pyplot"
            d = a["descriptor"]
            assert d["asset_id"] == "runtime:show_only.py#show_only"
            assert d["can_writeback_artifact"] is False
            assert len(d["size_mm"]) == 2 and d["size_mm"][0] > 0
        finally:
            engine_pool.shutdown_all(str(figs), wait=True)
