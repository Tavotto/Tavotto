"""异步准备接口（统一实施包 U01，ADR 0053）的状态机与项目绑定——**假 pool**。

这里不起任何 worker：`pool.build` / `pool.peek` / `pool.force_cancel` 全部换成可控的
替身，验的是 `engine/preparation.py` 与三个端点自己的行为——四种可报告的状态
（现有 runtime 正在运行 / 错误 / 取消 / 静态源可用）、取消的接受时刻与边界
（D11 / FO-009）、两个项目的同名脚本互不可见（FO-008）、会话认证不被绕过
（`test_browser_auth.py` 从 url_map 枚举，自动覆盖这三条路由）。
真 worker 穿过这条接口的用例在 `test_foundation_harness.py`。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import execspec, pool as engine_pool, preparation

SCRIPT = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\nfig.savefig('fig.pdf')\n"
REGISTRY = '{"version": 1, "scripts": {"fig.py": {"entry": "__main__", "cost": "light", "notes": "", "stems": ["fig"]}}}'


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "fig.py").write_text(SCRIPT, encoding="utf-8")
    (root / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    doc.save(root / "fig.pdf")
    doc.close()
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    doc.save(root / "static_only.pdf")  # 注册表里没有它的脚本：纯静态素材
    doc.close()
    return root


class _FakeWorker:
    def __init__(self, root: Path, generation: int = 1, built: bool = True):
        self.spec = execspec.safe_spec(
            "fig.py", str(root), "__main__", interpreter="/envs/fake/bin/python", sandbox="/box"
        )
        self.generation = generation
        self.built = built
        self.script_sha1 = "deadbeef"
        self.python_source = "system"
        self.python = "/envs/fake/bin/python"


def _build_resp(prefix="/envs/fake"):
    return {
        "stems": {"fig": {"size_mm": [50, 25], "source": "savefig"}},
        "descriptors": [{"stem": "fig", "script": "fig.py"}],
        "runtime": {
            "runtime_report_version": 1,
            "python_version": "3.13.0",
            "python_implementation": "CPython",
            "executable": f"{prefix}/bin/python",
            "prefix": prefix,
            "base_prefix": "/opt/py",
            "platform": "darwin",
            "machine": "arm64",
            "cwd": "/box",
            "argv0": "fig.py",
            "packages": {"matplotlib": "3.10.8"},
        },
    }


@pytest.fixture
def fake_pool(monkeypatch):
    """可控的 pool 替身：`build` 阻塞在一个事件上，`peek` / `force_cancel` 记账。"""
    box = {
        "peek": None,
        "build_calls": 0,
        "force_cancel": [],
        "gate": threading.Event(),
        "error": None,
        "worker_factory": None,
    }
    box["gate"].set()  # 默认不阻塞

    def build_owned(script, root, entry):
        """替身按 pool 的合同回 `(worker, resp, created)`：所有权由这里**原子**给出，
        默认「第一次调用创建、之后复用」——与真 pool 的 `get()` 同形。"""
        box["build_calls"] += 1
        ordinal = box["build_calls"]  # 进门那一刻的序号：所有权在「建会话那一下」就定了
        box["gate"].wait(timeout=30)
        if box["error"] is not None:
            raise box["error"]
        factory = box["worker_factory"] or (lambda: _FakeWorker(Path(root)))
        created = ordinal == 1 if box["created"] is None else box["created"]
        return factory(), _build_resp(), created

    box["created"] = None
    monkeypatch.setattr(engine_pool, "build_owned", build_owned)
    monkeypatch.setattr(engine_pool, "same_python", lambda a, b: True)
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda root=None: ("/envs/fake/bin/python", "system")
    )
    monkeypatch.setattr(engine_pool, "peek", lambda script, root: box["peek"])
    monkeypatch.setattr(
        engine_pool, "force_cancel", lambda script, root: box["force_cancel"].append((script, root))
    )
    monkeypatch.setattr(engine_pool, "control_plane_of", lambda w: "python_pool")
    return box


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    preparation.SERVICE.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    preparation.SERVICE.reset_for_tests()


def _wait(client, plan_id: str, pj: str | None = None, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while True:
        args = {"pj": pj} if pj else {}
        body = client.get(f"/api/engine/preparation/{plan_id}", query_string=args).get_json()
        if body["result"]["status"] in preparation.TERMINAL:
            return body
        assert time.time() < deadline, body
        time.sleep(0.02)


def _open(client, root: Path) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


# ---------------------------------------------------------------- 四种状态


def test_static_only_asset_reports_static_source_and_runs_nothing(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    resp = client.post("/api/engine/preparation", json={"id": "static_only.pdf"})
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body["result"]["status"] == preparation.STATUS_STATIC
    assert body["plan"]["script"] is None and body["plan"]["launch_context"] is None
    static = body["plan"]["static_source"]
    assert static["origin"] == "static" and static["kind"] == "pdf"
    assert static["size_bytes"] == (root / "static_only.pdf").stat().st_size
    assert fake_pool["build_calls"] == 0  # 一个子进程都不起（FO-010）


def test_a_script_asset_runs_to_ready_with_a_complete_receipt(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert resp.status_code == 202
    plan = resp.get_json()["plan"]
    assert plan["launch_context"]["cwd_origin"] == "sandbox"
    assert plan["static_source"]["source_id"] == "fig.pdf"
    assert plan["python_requirement"] == {"declared": False}
    body = _wait(client, plan["plan_id"])
    result = body["result"]
    assert result["status"] == preparation.STATUS_READY
    assert result["existing_runtime"] is None and result["created_runtime"] is True
    receipt = result["receipt"]
    assert receipt["completeness"] == "complete"
    assert receipt["generation"] == 1
    assert receipt["source_revision"] == "deadbeef"
    assert receipt["runtime"]["packages"] == {"matplotlib": "3.10.8"}
    # 默认投影不带机器路径
    assert "prefix" not in receipt["runtime"] and "executable" not in receipt["runtime"]
    assert fake_pool["build_calls"] == 1


def test_an_existing_built_runtime_is_reported_and_not_rebuilt(client, tmp_path, fake_pool):
    """已经 build 过的会话：回执从它记下的 build 响应装配，**runner 一次都不调**
    （Codex #451 P1：二开不许再碰用户脚本，哪怕 worker 侧本来就是幂等的）。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    existing = _FakeWorker(root, generation=3, built=True)
    existing.last_build_descriptors = [{"stem": "fig", "script": "fig.py"}]
    existing.last_build_runtime = _build_resp()["runtime"]
    fake_pool["peek"] = existing
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    body = _wait(client, resp.get_json()["plan"]["plan_id"])
    assert body["result"]["status"] == preparation.STATUS_READY
    assert body["result"]["existing_runtime"] == {
        "generation": 3,
        "built": True,
        "control_plane": "python_pool",
    }
    assert body["result"]["created_runtime"] is False
    assert body["result"]["receipt"]["generation"] == 3
    assert body["result"]["receipt"]["completeness"] == "complete"
    assert fake_pool["build_calls"] == 0


def test_an_existing_built_session_on_another_interpreter_is_not_reused(
    client, tmp_path, fake_pool, monkeypatch
):
    """池里那条会话是旧解释器起的（用户刚换了环境）：它不是「现有 runtime」，
    要走 runner（`pool.get()` 会按「渲染解释器已变」重建）。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    existing = _FakeWorker(root, generation=3, built=True)
    fake_pool["peek"] = existing
    monkeypatch.setattr(engine_pool, "same_python", lambda a, b: False)
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    body = _wait(client, resp.get_json()["plan"]["plan_id"])
    assert body["result"]["status"] == preparation.STATUS_READY
    assert fake_pool["build_calls"] == 1
    assert body["result"]["created_runtime"] is True


def test_a_worker_error_is_reported_with_its_code_and_module(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    err = engine_pool.WorkerError("缺包", code="missing_dependency", module="lmfit")
    err.project_env = {"ok": False, "reason": "no_venv", "python": "/abs/elsewhere/python"}
    fake_pool["error"] = err
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    body = _wait(client, resp.get_json()["plan"]["plan_id"])
    assert body["result"]["status"] == preparation.STATUS_ERROR
    assert body["result"]["error"] == {
        "code": "missing_dependency",
        "message": "缺包",
        "module": "lmfit",
        "project_env": {"ok": False, "reason": "no_venv"},  # 带路径的键不进公开投影
    }
    assert body["result"]["receipt"] is None


# ---------------------------------------------------------------- 取消


def test_cancel_during_build_kills_only_a_session_this_plan_created(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    fake_pool["gate"].clear()  # build 卡住
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    plan_id = resp.get_json()["plan"]["plan_id"]
    deadline = time.time() + 5
    while (
        client.get(f"/api/engine/preparation/{plan_id}").get_json()["result"]["status"] != "running"
    ):
        assert time.time() < deadline
        time.sleep(0.02)
    cancelled = client.post(f"/api/engine/preparation/{plan_id}/cancel").get_json()
    assert cancelled["cancelling"] is True
    assert cancelled["result"]["cancel_requested_at"] is not None
    assert cancelled["result"]["status"] == "running"  # 接受 ≠ 已取消
    fake_pool["gate"].set()
    body = _wait(client, plan_id)
    assert body["result"]["status"] == preparation.STATUS_CANCELLED
    assert body["result"]["created_runtime"] is True
    assert fake_pool["force_cancel"] == [("fig.py", str(root))]
    assert "外部副作用不撤销" in body["result"]["note"]
    # 已经终局：再取消不接受
    assert (
        client.post(f"/api/engine/preparation/{plan_id}/cancel").get_json()["cancelling"] is False
    )


def test_cancel_never_touches_a_session_that_belongs_to_someone_else(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    existing = _FakeWorker(root, generation=7, built=False)  # 别人正在冷启动
    fake_pool["peek"] = existing
    fake_pool["worker_factory"] = lambda: existing
    fake_pool["created"] = False  # pool 说：这条会话不是你建的
    fake_pool["gate"].clear()
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    plan_id = resp.get_json()["plan"]["plan_id"]
    time.sleep(0.05)
    assert client.post(f"/api/engine/preparation/{plan_id}/cancel").get_json()["cancelling"] is True
    fake_pool["gate"].set()
    body = _wait(client, plan_id)
    assert body["result"]["status"] == preparation.STATUS_CANCELLED
    assert body["result"]["created_runtime"] is False
    assert fake_pool["force_cancel"] == []  # FO-009：别的消费者的会话一根手指不碰
    assert "别的消费者" in body["result"]["note"]


def test_ownership_comes_from_the_pool_not_from_a_pre_build_snapshot(client, tmp_path, fake_pool):
    """Codex #451 P1：两份准备同时起步、都在 peek 时看到「没有会话」，只有 pool 里真正
    建了会话的那一份是主人；另一份取消时**不许**杀掉共用的会话。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    fake_pool["gate"].clear()  # 两份都卡在 build 里，peek 对两者都回 None
    a = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()["plan"]["plan_id"]
    b = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()["plan"]["plan_id"]
    deadline = time.time() + 5
    while fake_pool["build_calls"] < 2:
        assert time.time() < deadline
        time.sleep(0.02)
    assert client.post(f"/api/engine/preparation/{b}/cancel").get_json()["cancelling"] is True
    fake_pool["gate"].set()
    ra, rb = _wait(client, a)["result"], _wait(client, b)["result"]
    owners = [r["created_runtime"] for r in (ra, rb)]
    assert owners.count(True) == 1, owners  # 主人恰好一个，由 pool 的 created 决定
    assert rb["status"] == preparation.STATUS_CANCELLED
    # 第二份不是主人（pool 只把 created 给第一次调用）→ 共用会话一根手指不碰
    assert rb["created_runtime"] is False
    assert fake_pool["force_cancel"] == []


def test_cancel_before_the_thread_touches_the_pool_runs_no_user_code(tmp_path, fake_pool):
    """直接在 service 层验：登记后立刻取消，runner 一次都不被调。"""
    svc = preparation.PreparationService()
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
    svc.register(plan)
    assert svc.cancel(plan.plan_id, "pj") == {"accepted": True, "reason": ""}
    _, result = svc.get(plan.plan_id, "pj")
    assert result.status == preparation.STATUS_CANCELLED
    calls = []
    svc.start(plan.plan_id, runner=lambda pl: calls.append(pl))  # 已终局：不起线程
    assert svc.wait(plan.plan_id, 1.0) and calls == []


# ---------------------------------------------------------------- 项目绑定（FO-008）


def test_plans_are_invisible_from_another_project_even_for_the_same_script(
    client, tmp_path, fake_pool
):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    pj_a = _open(client, a)
    pj_b = _open(client, b)
    ra = client.post("/api/engine/preparation", json={"id": "fig.pdf"}, query_string={"pj": pj_a})
    rb = client.post("/api/engine/preparation", json={"id": "fig.pdf"}, query_string={"pj": pj_b})
    pa, pb = ra.get_json()["plan"], rb.get_json()["plan"]
    assert pa["project_id"] == pj_a and pb["project_id"] == pj_b and pa["plan_id"] != pb["plan_id"]
    # 拿 A 的 plan 到 B 项目下查：404 + 稳定 code，绝不返回 A 的状态
    wrong = client.get(f"/api/engine/preparation/{pa['plan_id']}", query_string={"pj": pj_b})
    assert wrong.status_code == 404
    assert wrong.get_json()["code"] == "preparation_not_found"
    assert wrong.get_json()["params"] == {"id": pa["plan_id"]}
    cancel = client.post(
        f"/api/engine/preparation/{pa['plan_id']}/cancel", query_string={"pj": pj_b}
    )
    assert cancel.status_code == 404
    # 各自的回执 id 不同（项目根进私有键）
    fa = _wait(client, pa["plan_id"], pj_a)["result"]["receipt"]
    fb = _wait(client, pb["plan_id"], pj_b)["result"]["receipt"]
    assert fa["receipt_id"] != fb["receipt_id"]
    assert fa["public_identity"] == fb["public_identity"]  # 意图相同


def test_unknown_plan_and_missing_id_are_structured_errors(client, tmp_path, fake_pool):
    _open(client, _project(tmp_path, "p"))
    assert client.get("/api/engine/preparation/prep-nope").status_code == 404
    bad = client.post("/api/engine/preparation", json={})
    assert bad.status_code == 400 and bad.get_json()["code"] == "bad_request"
    assert client.post("/api/engine/preparation", json={"id": "missing.pdf"}).status_code == 404


_PATH_NEEDLES = ("data_dir", "home", "sys.prefix", "sys.base_prefix", "interpreter")


def _machine_paths(root: Path) -> dict[str, str]:
    """本机会泄露的那几个绝对路径（都要在公开投影里找不到）。"""
    from tavotto.engine import config as engine_config

    return {
        "data_dir": str(engine_config.data_dir()),
        "home": str(Path.home()),
        "sys.prefix": sys.prefix,
        "sys.base_prefix": sys.base_prefix,
        "interpreter": engine_pool.resolve_worker_python(str(root))[0],
    }


def test_the_public_projection_carries_no_machine_paths(client, tmp_path, fake_pool, monkeypatch):
    """Codex（#455 转办，P2）：`plan.environment.python` 对 bundled / system 解释器是绝对路径，
    经 HTTP / MCP 投影就把安装目录 / 用户目录暴露出去，与 ADR 0053「公开身份不含机器路径」
    矛盾。公开投影里只留公开身份（来源标签 + 项目相对路径 + 版本），绝对路径只在私有键。
    错误分支的 `project_env` 也一样（那里会带体检到的解释器路径）。"""
    root = _project(tmp_path, "p")
    _open(client, root)
    # 解释器决策指向项目外的一个绝对路径（system 档的形状）
    outside = tmp_path / "elsewhere" / "envs" / "sci" / "bin" / "python"
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda r=None: (str(outside), "system")
    )
    needles = {**_machine_paths(root), "interpreter": str(outside), "tmp": str(tmp_path)}
    # ready 分支
    body = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()
    body = _wait(client, body["plan"]["plan_id"])
    text = json.dumps(body, ensure_ascii=False)
    for label, needle in needles.items():
        assert needle not in text, f"公开投影里带了机器路径 {label}: {needle}"
    assert body["plan"]["environment"]["source"] == "system"
    assert body["plan"]["environment"]["python"] is None  # 项目外：不给路径
    # 错误分支：project_env 里带体检到的绝对路径
    err = engine_pool.WorkerError("缺包", code="missing_dependency", module="lmfit")
    err.project_env = {
        "ok": False,
        "code": "no_venv",
        "module": "lmfit",
        "python": str(outside),
        "candidates": [str(outside)],
    }
    fake_pool["error"] = err
    body = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()
    body = _wait(client, body["plan"]["plan_id"])
    text = json.dumps(body, ensure_ascii=False)
    assert body["result"]["status"] == preparation.STATUS_ERROR
    assert str(outside) not in text and str(tmp_path) not in text
    assert body["result"]["error"]["project_env"] == {
        "ok": False,
        "code": "no_venv",
        "module": "lmfit",
    }


def test_a_project_venv_interpreter_is_shown_project_relative(
    client, tmp_path, fake_pool, monkeypatch
):
    root = _project(tmp_path, "p")
    _open(client, root)
    inside = root / ".venv" / "bin" / "python"
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda r=None: (str(inside), "project_venv")
    )
    plan = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()["plan"]
    assert plan["environment"]["python"] == ".venv/bin/python"
    assert str(root) not in json.dumps(plan, ensure_ascii=False)


def test_the_plan_reads_the_grant_the_product_recorded(client, tmp_path, fake_pool):
    root = _project(tmp_path, "p")
    _open(client, root)
    assert client.patch("/api/engine/workdir", json={"mode": "project"}).status_code == 200
    plan = client.post("/api/engine/preparation", json={"id": "fig.pdf"}).get_json()["plan"]
    assert plan["grant"]["cwd_write"]["granted"] is True
    assert plan["launch_context"]["cwd_origin"] == "script.parent"
    assert plan["launch_context"]["write_mode"] == "project_dir"
