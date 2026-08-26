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
from tavotto.engine import pool as engine_pool
from tavotto.engine import probe as engine_probe
from tavotto.engine import registry as engine_registry
from tavotto.engine import runtimeasset

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

SHOW_ONLY = '''\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.show()
'''

# 顶层先画图再睡死：cancel 到达时 build 一定还没结束
SLOW = '''\
import time
import matplotlib.pyplot as plt

plt.plot([1, 2, 3])
time.sleep(120)
'''


def write(figs: Path, name: str, source: str) -> Path:
    p = figs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


def write_registry(figs: Path, scripts: dict) -> None:
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": scripts}, ensure_ascii=False), encoding="utf-8")


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
    engine_pool.stop_watcher()


# ===========================================================================
# 一、取消：真正终止，不是藏 UI
# ===========================================================================
class TestCancelSemantics:
    def test_cancel_before_any_execution_never_spawns_a_worker(
            self, tmp_path, monkeypatch):
        """取消先于执行：一个 worker 都不许起（协作取消的最早检查点）。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)

        def bomb(*a, **k):
            raise AssertionError("已取消的 probe 不得 spawn worker")

        monkeypatch.setattr(engine_pool, "get", bomb)
        result = engine_probe.probe(figs, "show_only.py",
                                    should_cancel=lambda: True)
        assert result["error"]["code"] == engine_probe.ERROR_CANCELLED
        assert result["stems"] == []

    def test_cancelled_worker_failure_is_not_misreported(
            self, tmp_path, monkeypatch):
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
                cancelled["flag"] = True         # 模拟：执行期间 cancel 到达
                raise engine_pool.WorkerError("worker 进程崩溃（无响应）")

        monkeypatch.setattr(engine_pool, "get", lambda *a, **k: FakeWorker())
        result = engine_probe.probe(figs, "show_only.py",
                                    entries=["__main__", "main"],
                                    should_cancel=lambda: cancelled["flag"])
        assert result["error"]["code"] == engine_probe.ERROR_CANCELLED
        assert calls == ["build"]                # 第二个 entry 没有被试

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
            resp = m.app.test_client().post("/api/registry/probe",
                                            json={"script": "slow.py"})
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

        time.sleep(1.0)            # 让 worker 进入 build（睡在用户脚本里）
        resp = client.post("/api/registry/probe/cancel",
                           json={"script": "slow.py"})
        assert resp.get_json()["cancelling"] is True

        th.join(timeout=30)        # SLOW 睡 120s：30s 内返回只能是被杀
        assert not th.is_alive(), "cancel 之后 probe 请求仍未返回（没杀掉）"
        assert done["status"] == 200
        assert done["json"]["error"]["code"] == "execution_cancelled"
        assert done["json"]["registered"] is False
        key = (engine_pool._norm_dir(str(figs)), "slow.py")
        assert key not in engine_pool._workers, "被取消的会话不得留在池里"
        # 项目打开时会静态起草一份注册表；取消的 probe 不许把 slow.py 写进去
        cfg = json.loads((figs / "tavotto_registry.json")
                         .read_text(encoding="utf-8"))
        assert "slow.py" not in cfg.get("scripts", {}), "取消不许登记脚本"
        # 幂等：没有在跑的 probe 时取消不是错误
        resp = client.post("/api/registry/probe/cancel",
                           json={"script": "slow.py"})
        assert resp.status_code == 200
        assert resp.get_json()["cancelling"] is False


# ===========================================================================
# 二、runtime 素材清单：只读、不双列
# ===========================================================================
class TestRuntimeAssetListing:
    def test_listing_never_executes_scripts(self, client, tmp_path, monkeypatch):
        """结构性看护：GET /api/runtime/assets 绝不 spawn worker。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        write_registry(figs, {"show_only.py": {"entry": "__main__",
                                               "stems": ["show_only"]}})
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

    def test_stems_with_disk_artifacts_are_file_assets_not_runtime(
            self, tmp_path):
        """同一张图绝不双列：磁盘有原件的 stem 归 FileAsset（scan_panels），
        清单只列没有原件的。负向反证 #2 的邻接看护：runtime 条目不带
        磁盘路径字段，消费方拿不到「假路径」。"""
        figs = _make_project(tmp_path)
        write(figs, "mixed.py", SHOW_ONLY)
        write_registry(figs, {"mixed.py": {"entry": "__main__",
                                           "stems": ["on_disk", "live_only"]}})
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

    def test_pyplot_capture_is_not_shadowed_by_a_stale_same_stem_file(
            self, tmp_path):
        """Codex 评审 P1（PR #127）：pyplot 捕获**从来没有原件**（figcapture
        工厂钉死的语义），磁盘上同名文件只是旧样本——按文件名巧合把
        runtime 素材让位给它，用户编辑的就是陈旧文件。归属按**捕获来源**
        判（`is_pyplot_capture`），savefig 来源的照旧归 FileAsset 不双列。"""
        from tavotto.engine import figcapture
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        write_registry(figs, {"show_only.py": {"entry": "__main__",
                                               "stems": ["show_only"]}})
        desc = figcapture.build_descriptor(
            script="show_only.py", entry="__main__", stem="show_only",
            capture_source=figcapture.SOURCE_PYPLOT,
            execution_profile=figcapture.PROFILE_SAFE,
            size_mm=(120.0, 90.0),
            source_fingerprint="sha256:deadbeef").to_payload()
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
        write_registry(figs, {
            "show_only.py": {"entry": "__main__", "stems": ["show_only"]},
            "saved.py": {"entry": "main", "stems": ["saved"]}})
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(figs / "saved.pdf")
        doc.close()
        desc2 = figcapture.build_descriptor(
            script="saved.py", entry="main", stem="saved",
            capture_source=figcapture.SOURCE_SAVEFIG,
            execution_profile=figcapture.PROFILE_SAFE,
            size_mm=(80.0, 60.0), source_fingerprint="sha256:beef",
            original_artifact="saved.pdf").to_payload()
        assert runtimeasset.materialize(figs, desc2, svg) is not None
        reg.load(figs)
        stems = [x["stem"] for x in runtimeasset.list_assets(
            figs, reg, worker_python="python")]
        assert stems == ["show_only"]

    def test_bad_registry_pairs_do_not_break_the_listing(self, tmp_path):
        reg = engine_registry.Registry()
        reg.load_data({"scripts": {}})
        assert runtimeasset.list_assets(tmp_path, reg,
                                        worker_python="python") == []

    @needs_worker
    def test_probe_then_listing_carries_the_descriptor(self, client, tmp_path):
        """普通入口的数据链：probe 成功 → 清单条目带物化描述符（前端
        「添加到画布」的数据源）与 fresh 状态。"""
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        client.post("/api/projects/open", json={"path": str(figs)})
        try:
            resp = client.post("/api/registry/probe",
                               json={"script": "show_only.py"})
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
