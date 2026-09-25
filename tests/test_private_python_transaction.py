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

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from support import private_python as pp_support
from support.dependency_repair import WORKER_PY, build_wheel, needs_worker, wait_for
from support.private_python import (
    LoopbackServer,
    closed_port_url,
    fake_archive,
    needs_real_base,
    source_from,
)
from tavotto.engine import (
    bootstrap,
    depplan,
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
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
    @needs_real_base
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
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
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

    @needs_real_base
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
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        assert deprepair.STATE_DOWNLOADING_PYTHON in [e["state"] for e in events]
        assert server.requests == [f"/{src.archive_name}"]
        assert managedenv.referenced_base_runtimes() == {src.id}

    def test_single_package_repair_on_an_existing_environment_still_offers_the_private_base(
        self, tmp_path, house, offline_managed_env, fake, monkeypatch
    ):
        """受管环境已有一代（当年在系统 Python 上建的），系统 Python 后来没了：单包修复照样要建新的一代，
        计划必须带 `private_python`，而不是执行时才撞 `managed_env_unavailable`（Codex #464 第二轮 P2）。"""
        server, src, _ = fake
        project = _project(tmp_path)
        rec, _ = _prepare(deprepair.create_joint_plan(project, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
        deprepair.reset_state()
        assert deprepair.base_python() is None
        # offer 那一侧同样说出口：环境在（creates_environment=False）也要 base，载荷挂在受管目标上。
        # 先把 beta 声明进项目：offer 只对解析得出的包给目标，解析不出时 targets 为空（那是另一条路）
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        offer = deprepair.offer(project, "figure.py", BETA[1])
        managed = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["creates_environment"] is False and managed["available"] is True
        assert managed["private_python"]["download_bytes"] == src.size
        plan = deprepair.create_plan(
            project,
            "figure.py",
            BETA[1],
            target_kind=deprepair.TARGET_MANAGED,
            user_distribution=BETA[0],
        )
        assert plan.creates_environment is False  # 环境在，但新的一代仍要 base
        assert plan.private_python is not None and plan.private_python["required"] is True
        assert server.requests == []

    def test_rebuild_and_package_first_install_never_download(self, tmp_path, house, no_base, fake):
        """没有明示过下载的两条路：重建 / 包管理首装——没有基础解释器就照旧 unavailable，零请求。"""
        server, src, _ = fake
        project = _project(tmp_path)
        managedenv.write_manifest(project, managedenv.new_manifest(project, "x"))
        out = deprepair._rebuild_guarded(project, None, deprepair.new_rebuild_progress_id())
        assert out["state"] == deprepair.STATE_FAILED
        assert out["code"] == deprepair.ERROR_MANAGED_UNAVAILABLE
        assert server.requests == []
        assert privatepython.python_of(src) is None
        assert managedenv.referenced_base_runtimes() == set()

    @needs_real_base
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
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
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

    @needs_real_base
    def test_cancel_right_after_the_acknowledgement_never_starts_the_download(
        self, tmp_path, house, no_base, fake, monkeypatch
    ):
        """取消登记时刻的合同（U04 C，Codex #470 P1）在 `downloading_python` 这段同样成立：`prepare_async` 一回来
        就取消——句柄在起线程之前就登记（accepted）、`prepare()` 拿锁之前看一次事件、供应进来之前已取消就直接拒绝
        （不起下载线程、不发请求、不起子进程）→ 终态 cancelled、零请求、没有 `.part` / staging / runtime、没登记任何一代。"""
        server, src, launches = fake
        project = _project(tmp_path)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python is not None and plan.private_python["required"] is True
        gate = threading.Event()
        real_guarded = deprepair._prepare_guarded

        def _held_at_entry(plan_id, on_event, *, claimed=False):
            gate.wait(timeout=30)  # 线程按在入口：登记若在线程里做，这一刻只能回 not_found
            return real_guarded(plan_id, on_event, claimed=claimed)

        monkeypatch.setattr(deprepair, "_prepare_guarded", _held_at_entry)
        events: list[dict] = []
        deprepair.prepare_async(plan.plan_id, on_event=events.append)
        answer = deprepair.cancel_status(plan.plan_id)  # ack 之后立刻取消
        gate.set()
        assert answer == {"accepted": True, "reason": ""}, answer
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_CANCELLED, json.dumps(rec, ensure_ascii=False)
        assert rec["result"] == {"activated": False}
        assert deprepair.STATE_DOWNLOADING_PYTHON not in [e["state"] for e in events]
        assert server.requests == []  # 一个字节没下
        assert privatepython.python_of(src) is None
        assert not privatepython.downloads_dir().exists() or not any(
            privatepython.downloads_dir().iterdir()
        )
        assert not privatepython.runtimes_dir().exists() or not any(
            privatepython.runtimes_dir().iterdir()
        )
        assert managedenv.generations(project) == {}
        if os.name != "nt":
            assert not launches.exists()  # 子进程一次没起
        assert deprepair.cancel_status(plan.plan_id)["reason"] == "not_found"  # 句柄随计划一起清掉
        # 取消之后再来：同一份形状照样成
        plan2 = deprepair.create_joint_plan(project, "figure.py")
        rec2, _ = _prepare(plan2.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2

    @needs_real_base
    def test_a_duplicate_prepare_does_not_start_a_second_provisioning(
        self, tmp_path, house, no_base, fake
    ):
        """U04 C 的认领合同（Codex #470 P1）在供应路径上：同一份计划第二次 `prepare_async` 回 False、不起第二个
        线程——供应只发一次请求、下载线程只有一个；放开后终态 done、一代、一份 runtime。"""
        server, src, _ = fake
        server.mode = "hold"
        server.gate.clear()
        project = _project(tmp_path)
        plan = deprepair.create_joint_plan(project, "figure.py")
        events: list[dict] = []
        assert deprepair.prepare_async(plan.plan_id, on_event=events.append) is True
        deadline = time.time() + 30
        while not server.requests and time.time() < deadline:
            time.sleep(0.05)
        assert server.requests == [f"/{src.archive_name}"]
        assert deprepair.prepare_async(plan.plan_id, on_event=events.append) is False  # 已在跑
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id)  # 同步入口同样认领
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED
        time.sleep(0.3)
        assert server.requests == [f"/{src.archive_name}"]  # 还是那一次
        with privatepython._lock:
            assert privatepython._inflight[src.id].consumers == 1
        server.gate.set()
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        assert server.requests == [f"/{src.archive_name}"]
        assert len(managedenv.generations(project)) == 1
        assert sorted(p.name for p in privatepython.runtimes_dir().iterdir()) == [src.id]

    @pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="需要 POSIX 权限位且非 root")
    def test_manifest_write_failure_after_provisioning_is_write_failed_and_keeps_the_runtime(
        self, tmp_path, house, no_base, fake
    ):
        """U04 C 的清单合同（Codex #470 P1）在供应路径上：私有 Python 供应完、登记这一代时清单写不进只读目录 →
        `managed_env_write_failed`（不是走到建 venv 才报）；供应好的 runtime 留着（它是缓存，下一次不用再下），
        没有半个 active、磁盘上没有清单。"""
        server, src, _ = fake
        project = _project(tmp_path)
        envs = managedenv.env_dir(project).parent
        envs.mkdir(parents=True, exist_ok=True)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python is not None
        envs.chmod(0o500)
        try:
            rec, events = _prepare(plan.plan_id)
        finally:
            envs.chmod(0o700)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_MANAGED_WRITE_FAILED
        assert deprepair.STATE_DOWNLOADING_PYTHON in [e["state"] for e in events]
        assert server.requests == [f"/{src.archive_name}"]
        assert privatepython.python_of(src)  # runtime 留着
        assert managedenv.python_of(project) is None
        assert managedenv.read_manifest(project) is None
        # 目录恢复后同一份形状再来：不再下载，直接建代
        plan2 = deprepair.create_joint_plan(project, "figure.py")
        assert plan2.private_python is None  # base 已在
        rec2, _ = _prepare(plan2.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2
        assert server.requests == [f"/{src.archive_name}"]

    @needs_real_base
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
        assert rec["state"] == deprepair.STATE_CANCELLED, json.dumps(rec, ensure_ascii=False)
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

    @needs_real_base
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

    @needs_real_base
    def test_a_new_private_runtime_makes_a_new_generation_and_never_touches_the_active_one(
        self, tmp_path, house, no_base, fake, monkeypatch
    ):
        """Codex #464 P1：项目意图没变、锁换了版本（新 runtime id）→ 重建走的是**另一代**（代号折进 base），
        active 那一代的记录与目录一个字节不动；新代失败时旧的照常可用，成功后才切过去。"""
        server, src_a, _ = fake
        project = _project(tmp_path)
        rec, _ = _prepare(deprepair.create_joint_plan(project, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        # 重建一次：重建的代号只由账上的需求 + 约束算（与联合计划的身份公式不同），于是**两次重建**
        # 之间意图不变 → 同一个代号——这正是「锁换了版本、意图没变」会撞上的那条路
        out0 = deprepair._rebuild_guarded(project, None, deprepair.new_rebuild_progress_id())
        assert out0.get("ok") is True, out0
        gen_a = managedenv.active_generation(project)
        assert gen_a == out0["generation"]
        python_a = managedenv.python_of(project)
        assert managedenv.generations(project)[gen_a]["base_runtime"] == src_a.id
        # 锁换版本：另一份假归档（字节不同 → 新 id），已被别的项目供应到磁盘上
        launches_b = tmp_path / "launches-b.log"
        archive_b, sha_b, rel_b = fake_archive(
            tmp_path / "serve-b", host_python=WORKER_PY, launches_log=launches_b
        )
        with LoopbackServer(tmp_path / "serve-b") as server_b:
            src_b = source_from(
                archive_b, sha_b, rel_b, url=server_b.url(archive_b.name), version=src_a.version
            )
            monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src_b)
            assert src_b.id != src_a.id
            privatepython.provision(src_b)
        deprepair.reset_state()
        assert deprepair.base_python() == privatepython.python_of(src_b)
        # 重建（delta 为空 → 与 gen_a 同一份意图），但 wheelhouse 被清空 → pip 必失败
        monkeypatch.setenv("PIP_FIND_LINKS", str(tmp_path / "empty-house"))
        (tmp_path / "empty-house").mkdir()
        out = deprepair._rebuild_guarded(project, None, deprepair.new_rebuild_progress_id())
        assert out["state"] == deprepair.STATE_FAILED, out
        assert managedenv.active_generation(project) == gen_a
        assert managedenv.python_of(project) == python_a
        assert Path(python_a).is_file() and _in(python_a, "print('alive')") == "alive"
        gens = managedenv.generations(project)
        assert gens[gen_a]["state"] == "ready" and gens[gen_a]["base_runtime"] == src_a.id
        failed = [g for g in gens if g != gen_a]
        assert len(failed) == 1 and gens[failed[0]]["state"] == "incomplete"
        assert gens[failed[0]]["base_runtime"] == src_b.id
        # wheelhouse 回来：重建成功 → 新代（≠ gen_a）active、base 是 B；旧代退役
        monkeypatch.setenv("PIP_FIND_LINKS", str(house))
        out2 = deprepair._rebuild_guarded(project, None, deprepair.new_rebuild_progress_id())
        assert out2.get("ok") is True, out2  # 成功时回的是事务结果（ok / generation），不是进度记录
        gen_b = managedenv.active_generation(project)
        assert gen_b != gen_a and gen_b == failed[0]
        assert managedenv.generations(project)[gen_b]["base_runtime"] == src_b.id
        assert gen_a not in managedenv.generations(project)

    @needs_real_base
    def test_an_old_private_runtime_survives_while_a_generation_records_it(
        self, tmp_path, house, no_base, fake
    ):
        """FO-028：锁文件换了版本（新 id）之后，旧 runtime 只要还有一代记着它就不删；没人记的才删。"""
        server, src, _ = fake
        project = _project(tmp_path)
        rec, _ = _prepare(deprepair.create_joint_plan(project, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
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


# ================================================================ 干净机器：一个渲染解释器都没有（PR B）
@pytest.fixture
def no_interpreter(monkeypatch, no_base):
    """`resolve_worker_python` 什么都找不到（`no_worker_python`）：干净机器的形状——`no_base` 之外连渲染解释器
    也没有。发现链那一层的输入，其余全是产品代码。"""
    real = engine_pool.resolve_worker_python

    def _none(figures_dir=None, *, script=None, discover=True):
        raise engine_pool._no_python_error()

    monkeypatch.setattr(engine_pool, "resolve_worker_python", _none)
    return real


class TestCleanMachine:
    @needs_real_base
    def test_first_plan_uses_standin_facts_then_replans_on_the_real_private_python(
        self, tmp_path, house, no_interpreter, fake, monkeypatch
    ):
        """FO23 的机制面（干净机器）：没有任何解释器 → 计划以私有 Python 为目标（替身事实：锁的版本 + 本机平台、
        已装为空）→ 明示下载 → 一次 prepare：供应 → 按真解释器重算 delta → 建代 → 装 → 验 → 切 active → 项目
        从此用它（`resolve_worker_python` 真的那一份回受管环境）。"""
        server, src, _ = fake
        project = _project(tmp_path)
        joint, kind, python = deprepair.joint_plan_for(project, "figure.py")
        assert kind == deprepair.TARGET_MANAGED and python == ""
        # 替身：新的一代装之前只有 adapter（matplotlib / numpy，与 `fresh_venv_facts(provided=)` 同一条纪律）
        # ——披露的是「空环境还要装什么」：只有 fixture 包
        assert joint.status == "ready" and set(joint.requirements) == {ALPHA[0]}
        assert joint.facts["python_version"] == src.version and joint.facts["installed_count"] == 2
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.replan is True and plan.private_python["required"] is True
        assert plan.to_payload()["replan"] is True
        rec, events = _prepare(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        assert [e["state"] for e in events].index(deprepair.STATE_DOWNLOADING_PYTHON) < [
            e["state"] for e in events
        ].index(deprepair.STATE_CREATING_ENV)
        gen = rec["result"]["generation"]
        record = managedenv.generations(project)[gen]
        assert record["base_runtime"] == src.id
        # 真量出来的 delta 装进去了；账上一笔
        managed = managedenv.python_of(project)
        assert managed and _in(managed, f"import {ALPHA[1]}; print('ok')") == "ok"
        assert [e["distribution"] for e in managedenv.state(project)["installed"]] == [ALPHA[0]]
        # 从此这个项目的解释器就是它（真实的 resolve 链：记住的受管环境）
        monkeypatch.setattr(engine_pool, "resolve_worker_python", no_interpreter)
        engine_pool.reset_worker_python()
        resolved, source = engine_pool.resolve_worker_python(str(project), script="figure.py")
        assert engine_pool.same_python(resolved, managed)
        assert source == engine_pool.SOURCE_MANAGED_PROJECT

    @needs_real_base
    def test_a_second_project_on_the_same_machine_still_gets_the_gate_and_its_own_generation(
        self, tmp_path, house, no_interpreter, fake, monkeypatch
    ):
        """Codex #475 P1：别的项目已经把私有 Python 供应好了 → 本项目照样一个解释器都没有：门照样问（哪怕脚本
        只用 adapter 里的包、`nothing_needed`）、载荷说「已就位、不联网」而不是 None、授权后建**本项目自己**的一代
        （零请求、`base_runtime` 记着共享的那份）；之前的形状是门放行 → 起 worker `no_worker_python`。"""
        server, src, _ = fake
        first = _project(tmp_path, "first")
        rec, _ = _prepare(deprepair.create_joint_plan(first, "figure.py").plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        requests_after_first = list(server.requests)
        assert requests_after_first == [f"/{src.archive_name}"]
        deprepair.reset_state()  # 第二个项目通常是另一次打开：缓存不算
        second = _project(tmp_path, "second")
        (second / "requirements.txt").write_text("", encoding="utf-8")
        (second / "figure.py").write_text(
            'import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig("Fig1.pdf")\n',
            encoding="utf-8",
        )
        assert managedenv.python_of(second) is None
        # 门：nothing_needed 也问；载荷是「已就位」（required=False、零字节、不联网），受管目标可用且带同一载荷
        offer = deprepair.gate(second, "figure.py")
        assert offer is not None, "私有 Python 已在 → 门放行 → 起 worker 只能 no_worker_python"
        assert offer["clean_machine"] is True
        assert offer["plan"]["status"] == "nothing_needed"
        assert offer["private_python"]["required"] is False
        assert offer["private_python"]["download_bytes"] == 0
        assert offer["private_python"]["network_required"] is False
        assert offer["private_python"]["version"] == src.version
        managed = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["available"] is True and managed["private_python"]["required"] is False
        # 授权 → 建本项目自己的一代：不下载、base 是共享的那份
        plan = deprepair.create_joint_plan(second, "figure.py")
        assert plan.private_python is None and plan.replan is False and plan.requirements == ()
        rec2, events = _prepare(plan.plan_id)
        assert rec2["state"] == deprepair.STATE_DONE, rec2
        assert deprepair.STATE_DOWNLOADING_PYTHON not in [e["state"] for e in events]
        assert server.requests == requests_after_first  # 零请求
        gen = rec2["result"]["generation"]
        assert managedenv.generations(second)[gen]["base_runtime"] == src.id
        assert managedenv.python_of(second) and managedenv.python_of(
            second
        ) != managedenv.python_of(first)
        assert managedenv.referenced_base_runtimes() == {src.id}
        # 从此第二个项目的解释器就是它自己的那一代
        monkeypatch.setattr(engine_pool, "resolve_worker_python", no_interpreter)
        engine_pool.reset_worker_python()
        resolved, source = engine_pool.resolve_worker_python(str(second), script="figure.py")
        assert engine_pool.same_python(resolved, managedenv.python_of(second))
        assert source == engine_pool.SOURCE_MANAGED_PROJECT

    def test_the_gate_judges_by_clean_machine_not_by_the_download_payload(
        self, tmp_path, monkeypatch
    ):
        """门的判据是 `clean_machine`：载荷是给界面说出口的，没有载荷（或将来载荷形状变了）门也得问。"""
        project = _project(tmp_path)
        base = {
            "code": deprepair.ERROR_PREPARATION_REQUIRED,
            "script": "figure.py",
            "plan": {"status": "nothing_needed", "missing": []},
            "target_kind": deprepair.TARGET_MANAGED,
            "targets": [],
            "rounds_remaining": 3,
            "skipped": False,
        }
        monkeypatch.setattr(
            deprepair,
            "_preparation_offer",
            lambda root, script: ({**base, "clean_machine": True, "private_python": None}, []),
        )
        assert deprepair.gate(project, "figure.py") is not None
        monkeypatch.setattr(
            deprepair,
            "_preparation_offer",
            lambda root, script: ({**base, "clean_machine": False, "private_python": None}, []),
        )
        assert deprepair.gate(project, "figure.py") is None  # 有解释器且什么都不缺：放行

    @needs_real_base
    def test_nothing_needed_still_builds_the_environment(
        self, tmp_path, house, no_interpreter, fake
    ):
        """干净机器上「脚本只用标准库」也得有环境（没有任何解释器可用）：nothing_needed 照样成计划
        （delta 空 = 只装 adapter）。"""
        server, src, _ = fake
        project = _project(tmp_path)
        (project / "requirements.txt").write_text("", encoding="utf-8")
        (project / "figure.py").write_text("import math\nprint(math.pi)\n", encoding="utf-8")
        joint, _, _ = deprepair.joint_plan_for(project, "figure.py")
        assert joint.status == "nothing_needed"
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python is not None and plan.requirements == ()
        rec, _ = _prepare(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        assert managedenv.python_of(project)
        assert managedenv.state(project)["installed"] == []  # 账上一笔都没有：只有 adapter

    def test_inputs_changed_during_the_download_are_stale_and_install_nothing(
        self, tmp_path, house, no_interpreter, fake
    ):
        """用户确认的是按**当时**的脚本与声明算的计划；下载私有 Python 期间 requirements 多了一行、脚本多了一个
        import → 重算前先比输入指纹 → `repair_plan_stale`：没登记任何一代、账上一笔没有、pip 一次没跑
        （Codex #475 P1）。私有 Python 本身留着（那是缓存，不是安装）；重新规划会把新输入说出口。"""
        server, src, _ = fake
        project = _project(tmp_path)
        build_wheel(house, name=BETA[0], import_name=BETA[1], version="1.0")
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.replan is True and set(plan.requirements) == {ALPHA[0]}
        assert plan.inputs_digest
        server.mode = "hold"
        server.gate.clear()
        events: list[dict] = []
        deprepair.prepare_async(plan.plan_id, on_event=events.append)
        deadline = time.time() + 30
        while not server.requests and time.time() < deadline:
            time.sleep(0.05)
        assert server.requests, "下载没开始"
        # 下载被扣住的这段时间里，用户改了输入
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        (project / "figure.py").write_text(
            (project / "figure.py").read_text(encoding="utf-8") + f"import {BETA[1]}\n",
            encoding="utf-8",
        )
        server.gate.set()
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED, json.dumps(rec, ensure_ascii=False)
        assert rec["code"] == deprepair.ERROR_PLAN_STALE
        states = [e["state"] for e in events]
        assert deprepair.STATE_DOWNLOADING_PYTHON in states
        assert (
            deprepair.STATE_CREATING_ENV not in states and deprepair.STATE_INSTALLING not in states
        )
        assert managedenv.generations(project) == {}
        assert managedenv.python_of(project) is None
        assert privatepython.python_of(src)  # 供应好的 runtime 留着：下一次不用再下
        # 重新规划：新输入进了计划
        again = deprepair.create_joint_plan(project, "figure.py")
        assert set(again.requirements) == {ALPHA[0], BETA[0]}
        assert again.inputs_digest != plan.inputs_digest

    def test_without_the_offer_a_clean_machine_still_reports_no_worker_python(
        self, tmp_path, house, no_interpreter, fake, monkeypatch
    ):
        """资格未取得：干净机器上照旧 `no_worker_python`（原路径的错误，不替它说话），一个字节不下。"""
        server, src, _ = fake
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "0")
        project = _project(tmp_path)
        assert deprepair.private_python_target(project, "figure.py") is None
        with pytest.raises(engine_pool.WorkerError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.NO_WORKER_PYTHON
        assert server.requests == []

    def test_offline_clean_machine_is_a_safe_stop_without_a_generation(
        self, tmp_path, house, no_interpreter, fake, monkeypatch
    ):
        """FO25 在干净机器上：计划说要下载、执行时断网 → `private_python_offline`；没登记任何一代、没有目录。"""
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
        assert plan.replan is True
        rec, _ = _prepare(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED and rec["code"] == privatepython.ERROR_OFFLINE
        assert managedenv.generations(project) == {}
        assert not privatepython.runtimes_dir().exists() or not any(
            privatepython.runtimes_dir().iterdir()
        )


# ================================================================ 真归档的完整链（工程验证；目标腿）
REAL = os.environ.get("TAVOTTO_PRIVATE_PYTHON_REAL") == "1"


@pytest.mark.skipif(
    not REAL,
    reason="真 pbs 归档要联网 / 要 wheelhouse：TAVOTTO_PRIVATE_PYTHON_REAL=1 才跑（private-python-targets.yml 三条腿）",
)
class TestRealChain:
    """宿主目标的**真** pbs 归档经产品代码走完整链：（公网 / 缓存）取回 → 整份 sha256 → 逐成员校验 → 真起 →
    U04 代事务（`python -m venv` on 私有 Python → pip 从 `TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE` 离线装真 matplotlib /
    numpy + fixture 包 → pip check → 关键 import → worker 自检 → 切 active）→ 独立用该 venv 出图。

    「没有基础解释器」= 发现链末端置空（`bootstrap.find_base_python → None`）——这是**工程验证**，不是无系统
    Python 的资格（ADR 0064 三档里的第二档）。`TAVOTTO_PRIVATE_PYTHON_DATA_DIR` 指定时数据目录用它（目标腿
    随后把 runtime 挂进空镜像 / 做注册表快照要找得到它）；`TAVOTTO_PRIVATE_PYTHON_REPORT` 指定时写一份 JSON 证据。
    """

    def test_real_pbs_python_through_the_transaction_to_a_figure(self, tmp_path, monkeypatch):
        wheelhouse = os.environ.get("TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE")
        if not wheelhouse or not Path(wheelhouse).is_dir():
            pytest.skip(
                "TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE 没指到一个目录（要真 matplotlib / numpy 的 wheel）"
            )
        src = privatepython.source_for()
        assert src is not None, "宿主目标不在锁文件里"
        data_dir = os.environ.get("TAVOTTO_PRIVATE_PYTHON_DATA_DIR") or str(tmp_path / "data")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("TAVOTTO_DATA_DIR", data_dir)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        cache = os.environ.get("TAVOTTO_PRIVATE_PYTHON_CACHE")
        if cache and (Path(cache) / src.archive_name).is_file():
            privatepython.downloads_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(cache) / src.archive_name, privatepython.archive_path(src))
        # wheelhouse：拷一份再加 fixture 包（不往调用方给的目录里写）
        house = tmp_path / "house"
        shutil.copytree(wheelhouse, house)
        build_wheel(house, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
        monkeypatch.setenv("PIP_FIND_LINKS", str(house))
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        # 没有基础解释器（工程表达）
        monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
        deprepair.reset_state()
        assert deprepair.base_python() is None

        project = tmp_path / "paper"
        project.mkdir()
        (project / "requirements.txt").write_text(f"{ALPHA[0]}==1.0\n", encoding="utf-8")
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport numpy as np\nimport matplotlib.pyplot as plt\n"
            "x = np.array([1.0, 2.0, 3.0])\nfig, ax = plt.subplots()\nax.plot(x, 3 * x + 1)\n"
            "ax.set_title('u05-private-python')\nfig.savefig('Fig1.pdf')\n",
            encoding="utf-8",
        )
        t0 = time.perf_counter()
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.private_python is not None and plan.private_python["required"] is True
        assert plan.private_python["id"] == src.id
        assert plan.private_python["download_bytes"] in (0, src.size)
        events: list[dict] = []
        deprepair.prepare_async(plan.plan_id, on_event=events.append)
        rec = wait_for(plan.plan_id, timeout=1500)
        elapsed = round(time.perf_counter() - t0, 1)
        assert rec["state"] == deprepair.STATE_DONE, json.dumps(rec, ensure_ascii=False)
        states = [e["state"] for e in events]
        assert deprepair.STATE_DOWNLOADING_PYTHON in states
        private = privatepython.python_of(src)
        assert private and Path(private).is_file()
        gen = rec["result"]["generation"]
        record = managedenv.generations(project)[gen]
        assert record["base_runtime"] == src.id and record["base_source"] == "private_python"
        venv_py = managedenv.python_of(project)
        assert venv_py
        info = json.loads(
            _in(
                venv_py,
                "import sys, json, platform, matplotlib, numpy; print(json.dumps({'prefix': sys.prefix, "
                "'base_prefix': sys.base_prefix, 'mpl': matplotlib.__version__, 'np': numpy.__version__, "
                "'v': platform.python_version()}))",
            )
        )
        runtime_root = Path(os.path.realpath(privatepython.runtime_dir(src)))
        assert Path(os.path.realpath(info["base_prefix"])) == runtime_root
        assert Path(os.path.realpath(info["prefix"])) == Path(
            os.path.realpath(managedenv.generation_dir(project, gen))
        )
        assert info["v"] == src.version
        # 独立再起一次：直接用这个 venv 跑脚本出图（不经 worker）
        out_pdf = project / "Fig1.pdf"
        run = subprocess.run(
            [venv_py, "figure.py"],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env={**os.environ, "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp_path / "mpl")},
        )
        assert run.returncode == 0, run.stderr[-800:]
        assert out_pdf.is_file() and out_pdf.stat().st_size > 1000
        # 账与退役：当前那份、且有代记着 → 不删
        assert src.id in privatepython.read_ledger()["runtimes"]
        assert privatepython.retire_unused(in_use=deprepair._private_runtime_in_use) == []
        report = os.environ.get("TAVOTTO_PRIVATE_PYTHON_REPORT")
        if report:
            Path(report).parent.mkdir(parents=True, exist_ok=True)
            Path(report).write_text(
                json.dumps(
                    {
                        "target": src.target,
                        "id": src.id,
                        "version": src.version,
                        "sha256": src.sha256,
                        "url": src.url,
                        "download_bytes_disclosed": plan.private_python["download_bytes"],
                        "runtime_dir": str(privatepython.runtime_dir(src)),
                        "python_rel": src.python_rel,
                        "generation": gen,
                        "states": sorted(set(states)),
                        "venv": info,
                        "pdf_bytes": out_pdf.stat().st_size,
                        "seconds": elapsed,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )
