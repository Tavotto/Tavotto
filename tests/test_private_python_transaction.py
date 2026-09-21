"""统一实施包 U05（ADR 0063）：私有 Python 接进 U04 代事务——**真**建 venv、真跑 pip（离线 wheelhouse）、
真起 worker 自检，只是 base 换成经本地供应服务取回的私有 Python。

「这台机器没有合格基础解释器」用 `bootstrap.find_base_python` 回 None 表达：那是发现链末端的**输入**
（一台只有区间外 Python 的机器就是这个形状），产品自己的判断（`managedenv.base_python` → 私有 Python）
一步不改。这是工程验证，**不是** NO_SYSTEM_PYTHON 的资格（06 §2）——资格要在真实目标上取。

每条对应的场景：FO23 的机制面（一次授权：计划里明示下载 → 供应 → 建代 → 装 → 验 → 切 active）、
FO25 / FO26 在事务层（离线 / 坏 hash → safe_stop、这一代**没登记**、旧 active 原样）、FO27（下载期间
取消 → cancelled、无残留）、FO-028（换代退役旧代但记着 base 的私有 runtime 不删）；重建 / 包管理首装
**没有明示过下载**，没有基础解释器就照旧 `managed_env_unavailable`（一个字节不下）。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from support import private_python as pp_support
from support.dependency_repair import WORKER_PY, build_wheel, needs_worker, wait_for
from support.private_python import LoopbackServer, closed_port_url, fake_archive, source_from
from tavotto.engine import (
    bootstrap,
    depplan,
    deprepair,
    envlease,
    managedenv,
    privatepython,
)

pytest_plugins = ("support.dependency_repair",)
pytestmark = needs_worker

ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")


@pytest.fixture(autouse=True)
def _clean(clean_state, tmp_path, monkeypatch):
    envlease.reset_for_tests()
    depplan.reset_cache()
    privatepython.reset_for_tests()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()
    privatepython.reset_for_tests()


@pytest.fixture
def no_base(monkeypatch, offline_managed_env):
    """发现链末端没有合格的基础解释器（`offline_managed_env` 把 base 钉成宿主，这里再拿掉）。"""
    monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
    monkeypatch.setattr(deprepair, "_base_python", None)
    monkeypatch.setattr(deprepair, "_base_python_known", False)


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """假 pbs 归档（替身 exec 的是 WORKER_PY——venv 要真建、worker 要真起）+ 本地供应服务 + 注入锁来源。

    回 (server, source, launches)。`privatepython.source_for` 换成回这一份：锁文件那一层的输入
    （URL / hash / 版本）由用例给，之后的每一步都是产品代码。"""
    launches = tmp_path / "launches.log"
    archive, sha, rel = fake_archive(
        tmp_path / "serve", host_python=WORKER_PY, launches_log=launches
    )
    server = LoopbackServer(tmp_path / "serve").__enter__()
    src = source_from(
        archive,
        sha,
        rel,
        url=server.url(archive.name),
        version=pp_support.host_python_version(WORKER_PY),
    )
    monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src)
    try:
        yield server, src, launches
    finally:
        server.__exit__(None, None, None)


def _project(tmp_path, name="paper") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / "requirements.txt").write_text(f"{ALPHA[0]}\n", encoding="utf-8")
    (proj / "figure.py").write_text(
        f"import {ALPHA[1]}\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
        'ax.plot([0, 1], [0, 1])\nfig.savefig("Fig1.pdf")\n',
        encoding="utf-8",
    )
    return proj


def _prepare(plan_id: str) -> tuple[dict, list[dict]]:
    events: list[dict] = []
    deprepair.prepare_async(plan_id, on_event=events.append)
    return wait_for(plan_id), events


def _in(python: str, code: str) -> str:
    out = subprocess.run(
        [python, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip()


class TestPrivateBase:
    def test_one_authorization_provisions_python_then_builds_the_generation(
        self, tmp_path, house, no_base, fake
    ):
        """FO23 的机制面：计划里明示「将下载 N 字节」→ 一次 prepare：供应（进度以 downloading_python 外露）
        → 建代 → 装 → 验 → 切 active；这一代记着 base_runtime；账上 last_used 被摸过；同进程里下一次计划
        不再要求下载。"""
        server, src, launches = fake
        project = _project(tmp_path)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.creates_environment and plan.private_python is not None
        assert plan.private_python["required"] is True
        assert plan.private_python["download_bytes"] == src.size
        assert plan.private_python["network_required"] is True
        assert plan.to_payload()["private_python"]["version"] == src.version
        assert server.requests == []  # 计划阶段一个字节不下

        rec, events = _prepare(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert server.requests == [f"/{src.archive_name}"]
        states = [e["state"] for e in events]
        assert deprepair.STATE_DOWNLOADING_PYTHON in states
        assert states.index(deprepair.STATE_DOWNLOADING_PYTHON) < states.index(
            deprepair.STATE_CREATING_ENV
        )
        dl = next(e for e in events if e["state"] == deprepair.STATE_DOWNLOADING_PYTHON)
        assert dl["result"]["download"]["total_bytes"] == src.size
        assert dl["result"]["private_python"]["id"] == src.id
        # 私有 Python 就位、这一代以它为 base、账上记着
        private = privatepython.python_of(src)
        assert private and Path(private).is_file()
        gen = rec["result"]["generation"]
        record = managedenv.generations(project)[gen]
        assert record["base_runtime"] == src.id and record["base_source"] == "private_python"
        assert record["base_interpreter_fingerprint"] == managedenv.base_interpreter_fingerprint(
            private
        )
        assert managedenv.referenced_base_runtimes() == {src.id}
        ledger = privatepython.read_ledger()["runtimes"][src.id]
        assert ledger["last_used"] >= ledger["provisioned_at"]
        # 环境真能用：包在、worker 自检过了（rec 是 done）、prefix 是这一代
        python = managedenv.python_of(project)
        assert python and rec["result"]["python"] == python
        assert _in(python, f"import {ALPHA[1]}; print('ok')") == "ok"
        assert Path(_in(python, "import sys; print(sys.prefix)")).resolve() == (
            managedenv.generation_dir(project, gen).resolve()
        )
        if os.name != "nt":
            # 替身被起过：供应时的一次 + 建 venv 的一次（`-m venv`）
            assert len(launches.read_text("utf-8").splitlines()) >= 2
        # 同一进程里的下一次计划：基础解释器缓存已刷新，不再要求下载
        assert deprepair.base_python() == private
        # 应用重开（缓存清空）：探测链末级在磁盘上看见已供应的那份，不再要求下载
        deprepair.reset_state()
        assert deprepair.base_python() == private
        assert deprepair.managed_available() is True
        other = _project(tmp_path, "second")
        plan2 = deprepair.create_joint_plan(other, "figure.py")
        assert plan2.private_python is None
        rec2, _ = _prepare(plan2.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2
        assert server.requests == [f"/{src.archive_name}"]  # 没有第二次下载

    def test_without_the_offer_no_base_is_still_managed_env_unavailable(
        self, tmp_path, house, no_base, fake, monkeypatch
    ):
        """资格未取得（锁 enabled=false 且没开逃生门）：行为与 U04 逐字相同，一个字节不下。"""
        server, src, _ = fake
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "0")
        project = _project(tmp_path)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_MANAGED_UNAVAILABLE
        offer = deprepair.offer(project, "figure.py", ALPHA[1])
        managed = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["available"] is False
        assert managed["reason"] == deprepair.ERROR_MANAGED_UNAVAILABLE
        assert managed["private_python"] is None
        assert server.requests == [] and privatepython.python_of(src) is None

    def test_offer_and_single_package_plan_carry_the_download(self, tmp_path, house, no_base, fake):
        """运行后缺包那条路（`offer` → `create_plan`）同样把下载说出口，再经同一个事务执行。"""
        server, src, _ = fake
        project = _project(tmp_path)
        # offer 不起子进程：第一次问时基础解释器还在后台探（None），探完再问才有结论
        deadline = time.time() + 30
        while deprepair.managed_available() is None and time.time() < deadline:
            time.sleep(0.05)
        assert deprepair.managed_available() is False
        offer = deprepair.offer(project, "figure.py", ALPHA[1])
        managed = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["available"] is True and managed["creates_environment"] is True
        assert managed["private_python"]["download_bytes"] == src.size
        plan = deprepair.create_plan(
            project, "figure.py", ALPHA[1], target_kind=deprepair.TARGET_MANAGED
        )
        assert plan.private_python is not None and plan.to_payload()["private_python"]["required"]
        assert server.requests == []
        events: list[dict] = []
        deprepair.install_async(plan.plan_id, on_event=events.append)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert deprepair.STATE_DOWNLOADING_PYTHON in [e["state"] for e in events]
        assert server.requests == [f"/{src.archive_name}"]
        assert managedenv.referenced_base_runtimes() == {src.id}

    def test_rebuild_and_package_first_install_never_download(self, tmp_path, house, no_base, fake):
        """没有明示过下载的两条路：重建 / 包管理首装——没有基础解释器就照旧 unavailable，零请求。"""
        server, src, _ = fake
        project = _project(tmp_path)
        managedenv.write_manifest(project, managedenv.new_manifest(project, "x"))
        out = deprepair._rebuild_guarded(project, None)
        assert out["state"] == deprepair.STATE_FAILED
        assert out["code"] == deprepair.ERROR_MANAGED_UNAVAILABLE
        assert server.requests == []
        assert privatepython.python_of(src) is None
        assert managedenv.referenced_base_runtimes() == set()

    def test_offline_prepare_is_a_safe_stop_and_registers_no_generation(
        self, tmp_path, house, no_base, fake, monkeypatch
    ):
        """FO25 在事务层：计划说要下载、执行时断网 → `private_python_offline`；这一代**没登记**、没有
        目录、没有 .part；之后网络回来同一份计划形状再跑就成功。"""
        server, src, _ = fake
        offline = source_from(
            Path(tmp_path / "serve" / src.archive_name),
            src.sha256,
            src.python_rel,
            url=closed_port_url(src.archive_name),
            version=src.version,
        )
        monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: offline)
        project = _project(tmp_path)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python["required"] is True
        rec, _ = _prepare(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == privatepython.ERROR_OFFLINE
        assert managedenv.generations(project) == {}
        assert managedenv.python_of(project) is None
        assert not privatepython.runtimes_dir().exists() or not any(
            privatepython.runtimes_dir().iterdir()
        )
        assert not list(privatepython.downloads_dir().glob("*.part"))
        assert not envlease.is_mutating_key(
            deprepair._env_key(deprepair.TARGET_MANAGED, "", project)
        )
        # 网络回来
        monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src)
        plan2 = deprepair.create_joint_plan(project, "figure.py")
        rec2, _ = _prepare(plan2.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2

    def test_corrupt_download_keeps_the_previous_generation_active(
        self, tmp_path, house, offline_managed_env, fake, monkeypatch
    ):
        """FO26 在事务层：先在宿主 base 上有一代 active；base 消失、来源被篡改 → `private_python_hash_mismatch`，
        旧 active 原样可用、没有新代登记、`runtimes/` 没有目录。"""
        server, src, launches = fake
        project = _project(tmp_path)
        rec, _ = _prepare(deprepair.create_joint_plan(project, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        gen_before = managedenv.active_generation(project)
        python_before = managedenv.python_of(project)
        assert managedenv.generations(project)[gen_before]["base_runtime"] == ""
        # 现在换一个需要新代的意图（多一个包），且机器上再没有 base
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\nimport matplotlib\n", encoding="utf-8"
        )
        build_wheel(house, name=BETA[0], import_name=BETA[1], version="1.0")
        monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
        monkeypatch.setattr(deprepair, "_base_python", None)
        monkeypatch.setattr(deprepair, "_base_python_known", False)
        server.mode = "corrupt"
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python["required"] is True
        rec2, _ = _prepare(plan.plan_id)
        assert rec2["state"] == deprepair.STATE_FAILED
        assert rec2["code"] == privatepython.ERROR_HASH_MISMATCH
        assert managedenv.active_generation(project) == gen_before
        assert managedenv.python_of(project) == python_before
        assert set(managedenv.generations(project)) == {gen_before}
        assert not any(privatepython.runtimes_dir().iterdir())
        if os.name != "nt":
            assert not launches.exists()  # 坏归档一次都没起

    def test_cancel_during_the_download_leaves_no_generation_and_no_runtime(
        self, tmp_path, house, no_base, fake
    ):
        """FO27：供应阶段取消 → 终态 cancelled、没有登记任何一代、没有 runtime 目录、锁已放；再来能成。"""
        server, src, launches = fake
        server.mode = "hold"
        server.gate.clear()
        project = _project(tmp_path)
        plan = deprepair.create_joint_plan(project, "figure.py")
        events: list[dict] = []
        deprepair.prepare_async(plan.plan_id, on_event=events.append)
        deadline = time.time() + 30
        while not server.requests and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        assert deprepair.cancel_status(plan.plan_id) == {"accepted": True, "reason": ""}
        # 唯一的消费者走了 → 下载线程被要求中止；放开服务端之后它在下一段就停
        deadline = time.time() + 30
        while time.time() < deadline:
            with privatepython._lock:
                job = privatepython._inflight.get(src.id)
            if job is None or job.abort.is_set():
                break
            time.sleep(0.05)
        server.gate.set()
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_CANCELLED, rec
        assert rec["code"] == deprepair.ERROR_CANCELLED
        assert rec["result"]["activated"] is False
        assert managedenv.generations(project) == {}
        deadline = time.time() + 30
        while privatepython._inflight and time.time() < deadline:
            time.sleep(0.05)
        assert not privatepython.runtimes_dir().exists() or not any(
            privatepython.runtimes_dir().iterdir()
        )
        assert not list(privatepython.downloads_dir().glob("*.part"))
        assert not envlease.is_mutating_key(
            deprepair._env_key(deprepair.TARGET_MANAGED, "", project)
        )
        if os.name != "nt":
            assert not launches.exists()
        server.mode = "ok"
        plan2 = deprepair.create_joint_plan(project, "figure.py")
        rec2, _ = _prepare(plan2.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2

    def test_two_projects_share_one_private_python(self, tmp_path, house, no_base, fake):
        """FO-027 在事务层：两个项目并发准备 → 一次下载、一份 runtime、两代各自记着它。"""
        server, src, _ = fake
        server.mode = "hold"
        server.gate.clear()
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        pa = deprepair.create_joint_plan(a, "figure.py")
        pb = deprepair.create_joint_plan(b, "figure.py")
        assert pa.private_python and pb.private_python
        deprepair.prepare_async(pa.plan_id)
        deadline = time.time() + 30
        while not server.requests and time.time() < deadline:
            time.sleep(0.05)
        deprepair.prepare_async(pb.plan_id)
        time.sleep(0.5)
        server.gate.set()
        ra, rb = wait_for(pa.plan_id), wait_for(pb.plan_id)
        assert ra["state"] == deprepair.STATE_DONE and rb["state"] == deprepair.STATE_DONE, (ra, rb)
        assert server.requests == [f"/{src.archive_name}"]
        assert [p.name for p in privatepython.runtimes_dir().iterdir()] == [src.id]
        assert managedenv.referenced_base_runtimes() == {src.id}
        for proj in (a, b):
            gen = managedenv.active_generation(proj)
            assert managedenv.generations(proj)[gen]["base_runtime"] == src.id

    def test_an_old_private_runtime_survives_while_a_generation_records_it(
        self, tmp_path, house, no_base, fake
    ):
        """FO-028：锁文件换了版本（新 id）之后，旧 runtime 只要还有一代记着它就不删；没人记的才删。"""
        server, src, _ = fake
        project = _project(tmp_path)
        rec, _ = _prepare(deprepair.create_joint_plan(project, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        old_id = src.id
        # 「换了版本」：当前来源指向另一个 id（目录名不同）；旧目录仍被这一代记着
        newer = privatepython.PythonSource(**{**src.__dict__, "sha256": "f" * 64})
        removed = privatepython.retire_unused(
            in_use=deprepair._private_runtime_in_use, source=newer
        )
        assert removed == [] and (privatepython.runtimes_dir() / old_id).is_dir()
        # 那一代退役（模拟：从 manifest 里划掉）之后才可删
        data = managedenv.read_manifest(project)
        data["generations"] = {}
        data["active"] = ""
        managedenv.write_manifest(project, data)
        assert managedenv.referenced_base_runtimes() == set()
        removed = privatepython.retire_unused(
            in_use=deprepair._private_runtime_in_use, source=newer
        )
        assert removed == [old_id] and not (privatepython.runtimes_dir() / old_id).exists()
