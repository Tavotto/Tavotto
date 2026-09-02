"""设置 → 包管理（ADR 0038）：用户包的安装 / 升级 / 卸载。

四层看护，与 ADR 0019 那组同构、再加一条：

* **目标**：只有这个项目的 Tavotto 受管环境。内置 runtime、用户的 `.venv`
  在这条面上一个都不出现（结构性断言：作业里的解释器只能是受管环境的那条）。
* **语法**：包名 / 需求串过同一道白名单（`depresolve.parse_requirement`）；
  敌意串在**形成作业**那一步就死，pip 一次都不会被调。
* **保护**：内置 = 基础栈 + 它的依赖闭包 + pip 自身，卸它一律拒绝；卸一个被
  别的用户包依赖的，作业里要把「谁依赖它」报出来。
* **生命周期**：同一环境上作业与修复共用一把锁；作业按 job_id 执行，请求体
  里别的字段一个都不读；磁盘不够先停在计划那一步。

真安装用例**不联网**（手工 wheel + `PIP_FIND_LINKS` / `PIP_NO_INDEX`），
断言必须证明包真的进了 / 出了那个 venv。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from support.dependency_repair import (
    FIXTURE_DIST,
    FIXTURE_IMPORT,
    build_wheel,
    needs_worker,
    wait_for,
)
from tavotto.engine import (
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
)

pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    """本文件每条用例前后都清一遍模块级状态。"""
    deprepair._jobs.clear()
    yield
    deprepair._jobs.clear()


# --------------------------------------------------------------- 夹具
def _fake_ready_env(project: Path) -> Path:
    """一份「形状对」的受管环境：manifest + 一个空的解释器文件。

    只给不真跑 pip 的用例用：它证明的是**判据**（拒绝 / 报什么），不是安装。
    """
    managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
    python = managedenv.venv_python(project)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    managedenv.mark_ready(project)
    return python


#: 一份假的盘点：matplotlib → numpy，lmfit 是用户装的、mylab 依赖 lmfit。
_INV = {
    "matplotlib": {"name": "matplotlib", "version": "3.10.0", "requires": ["numpy", "pillow"]},
    "numpy": {"name": "numpy", "version": "2.1.0", "requires": []},
    "pillow": {"name": "Pillow", "version": "11.0", "requires": []},
    "pip": {"name": "pip", "version": "25.0", "requires": []},
    "lmfit": {"name": "lmfit", "version": "1.3.2", "requires": ["numpy", "scipy"]},
    "scipy": {"name": "scipy", "version": "1.14", "requires": ["numpy"]},
    "mylab": {"name": "mylab", "version": "0.1", "requires": ["lmfit"]},
}


def _account(project: Path, *dists: tuple[str, str, str]) -> None:
    for dist, ver, reason in dists:
        managedenv.record_install(
            project,
            import_name="",
            distribution=dist,
            requested_specifier="",
            resolved_version=ver,
            reason=reason,
        )


@pytest.fixture
def no_pip(monkeypatch):
    """这条用例不许真的起任何子进程。"""

    def _boom(*a, **kw):
        raise AssertionError("这条用例不该执行任何子进程")

    monkeypatch.setattr(deprepair.subprocess, "Popen", _boom)
    monkeypatch.setattr(deprepair.subprocess, "run", _boom)
    monkeypatch.setattr(managedenv.subprocess, "run", _boom)


# ===========================================================================
# 一、清单
# ===========================================================================
def test_no_project_means_disabled_with_a_reason():
    out = deprepair.list_managed_packages(None)
    assert out["capability"] == {"available": False, "reason": "no_project"}
    assert out["user"] == [] and out["builtin"] == []


def test_listing_without_a_managed_env_starts_no_subprocess(tmp_path, no_pip, monkeypatch):
    """环境还没建时一个子进程都不起——这一页打开不该卡几百毫秒去问一个不存在的解释器。"""
    monkeypatch.setattr(deprepair, "managed_available", lambda: True)
    out = deprepair.list_managed_packages(tmp_path)
    assert out["environment"]["exists"] is False
    assert out["builtin_source"] in ("bundled_runtime", "planned")
    assert out["user"] == []
    assert out["capability"]["available"] is True


def test_builtin_is_the_dependency_closure_not_a_hardcoded_list(tmp_path, monkeypatch):
    """内置 = matplotlib + 它拉进来的一切 + pip；用户装的 lmfit / scipy 不在其中。"""
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV) if p == str(python) else None)
    monkeypatch.setattr(deprepair, "custom_package_index", lambda p: False)
    _account(tmp_path, ("lmfit", "1.3.2", managedenv.REASON_MISSING_DEPENDENCY))
    _account(tmp_path, ("mylab", "0.1", managedenv.REASON_USER_REQUESTED))
    out = deprepair.list_managed_packages(tmp_path)
    assert out["builtin_source"] == "managed_env"
    names = {b["name"] for b in out["builtin"]}
    assert {"matplotlib", "numpy", "Pillow", "pip"} <= names
    assert "lmfit" not in names and "scipy" not in names
    user = {u["distribution"]: u for u in out["user"]}
    assert user["lmfit"]["status"] == deprepair.PKG_INSTALLED
    assert user["lmfit"]["reason"] == managedenv.REASON_MISSING_DEPENDENCY
    assert user["lmfit"]["required_by"] == ["mylab"]
    assert user["mylab"]["reason"] == managedenv.REASON_USER_REQUESTED
    assert user["mylab"]["required_by"] == []
    assert user["lmfit"]["protected"] is False


def test_user_package_status_reflects_the_environment_not_the_ledger(tmp_path, monkeypatch):
    """账上有、环境里没 → missing；版本对不上 → changed。界面按状态换文案。"""
    python = _fake_ready_env(tmp_path)
    inv = dict(_INV)
    inv["lmfit"] = {"name": "lmfit", "version": "1.4.0", "requires": []}
    monkeypatch.setattr(deprepair, "inventory", lambda p: inv)
    monkeypatch.setattr(deprepair, "custom_package_index", lambda p: None)
    _account(tmp_path, ("lmfit", "1.3.2", "user_requested"), ("gone", "0.1", "user_requested"))
    user = {u["distribution"]: u for u in deprepair.list_managed_packages(tmp_path)["user"]}
    assert user["lmfit"]["status"] == deprepair.PKG_CHANGED
    assert user["lmfit"]["installed_version"] == "1.4.0"
    assert user["lmfit"]["recorded_version"] == "1.3.2"
    assert user["gone"]["status"] == deprepair.PKG_MISSING
    assert python  # 夹具真的在


def test_a_user_installed_numpy_is_marked_protected(tmp_path, monkeypatch):
    """用户自己装过 numpy（账上有），但它在基础栈的闭包里——只读，不给卸。"""
    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    monkeypatch.setattr(deprepair, "custom_package_index", lambda p: False)
    _account(tmp_path, ("numpy", "2.1.0", "user_requested"))
    user = deprepair.list_managed_packages(tmp_path)["user"]
    assert user and user[0]["protected"] is True


def test_listing_never_carries_paths_or_index_urls(tmp_path, monkeypatch):
    """清单是要显示在界面上、也会进诊断的：只有真假与版本，没有路径、没有地址。"""
    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    monkeypatch.setattr(deprepair, "custom_package_index", lambda p: True)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.example:3128")
    out = deprepair.list_managed_packages(tmp_path)
    assert out["network"] == {"proxy": True, "custom_index": True}
    blob = repr(out)
    assert str(tmp_path) not in blob and "proxy.example" not in blob and "secret" not in blob


# ===========================================================================
# 二、闭包
# ===========================================================================
def test_protected_closure_follows_requires_transitively():
    inv = {
        "matplotlib": {"name": "matplotlib", "version": "1", "requires": ["a"]},
        "a": {"name": "a", "version": "1", "requires": ["b"]},
        "b": {"name": "b", "version": "1", "requires": []},
        "c": {"name": "c", "version": "1", "requires": ["b"]},
    }
    got = deprepair.protected_distributions(inv)
    assert {"matplotlib", "a", "b", "pip"} <= got
    assert "c" not in got  # 依赖 b 不等于被 b 依赖


def test_protected_closure_without_an_inventory_is_the_base_set_only():
    assert deprepair.protected_distributions(None) >= {"matplotlib", "pip"}


# ===========================================================================
# 三、语法与安全
# ===========================================================================
def test_pip_argv_shapes_are_pinned():
    """install 默认不带 --upgrade；只有 update 带；uninstall 带 -y 且**不**带 --upgrade。"""
    assert deprepair.pip_install_argv("py", "lmfit>=1.3") == [
        "py",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
        "lmfit>=1.3",
    ]
    assert deprepair.pip_install_argv("py", "lmfit", upgrade=True)[-2:] == ["--upgrade", "lmfit"]
    assert deprepair.pip_uninstall_argv("py", "lmfit") == [
        "py",
        "-m",
        "pip",
        "uninstall",
        "--disable-pip-version-check",
        "--no-input",
        "-y",
        "lmfit",
    ]
    for argv in (deprepair.pip_install_argv("py", "x"), deprepair.pip_uninstall_argv("py", "x")):
        assert argv[:3] == ["py", "-m", "pip"], "绝不用 PATH 上的 pip"


@pytest.mark.parametrize(
    "hostile",
    [
        "-r evil.txt",
        "--index-url http://evil",
        "lmfit --index-url http://evil",
        "lmfit; rm -rf /",
        "lmfit && echo x",
        "https://evil/pkg.whl",
        "git+https://evil/repo",
        "../local-package",
        "pkg @ https://evil",
        "lmfit[extra]",
        "lmfit ==1.0",
        "$(whoami)",
        "",
    ],
)
def test_a_hostile_spec_dies_at_plan_time_and_pip_is_never_called(tmp_path, no_pip, hostile):
    _fake_ready_env(tmp_path)
    for op in deprepair.PACKAGE_OPS:
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_package_job(tmp_path, op, hostile)
        assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID
    assert not deprepair._jobs


def test_uninstall_takes_a_bare_name_only(tmp_path, no_pip):
    _fake_ready_env(tmp_path)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit>=1.3")
    assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID


def test_an_unknown_op_is_refused(tmp_path, no_pip):
    _fake_ready_env(tmp_path)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_package_job(tmp_path, "reinstall", "lmfit")
    assert err.value.code == deprepair.ERROR_PACKAGE_OP_INVALID


def test_the_bundled_runtime_and_the_project_venv_are_never_targets(tmp_path, no_pip, monkeypatch):
    """**结构性**：作业里的解释器只能是受管环境的那条（或空 = 还没建）。

    调用方给不了路径——`create_package_job(project, op, spec)` 的签名里根本
    没有解释器参数；这条用例钉住的是那个形状。
    """
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    for op, spec in (
        (deprepair.OP_INSTALL, "lmfit"),
        (deprepair.OP_UPDATE, "lmfit"),
        (deprepair.OP_UNINSTALL, "lmfit"),
    ):
        job = deprepair.create_package_job(tmp_path, op, spec)
        assert engine_pool.same_python(job.python, str(python))
        assert Path(job.python).resolve().is_relative_to(managedenv.env_dir(tmp_path).resolve())
    import inspect

    assert "python" not in inspect.signature(deprepair.create_package_job).parameters


# ===========================================================================
# 四、保护与依赖
# ===========================================================================
def test_uninstalling_a_builtin_is_refused(tmp_path, no_pip, monkeypatch):
    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    for name in ("matplotlib", "numpy", "Pillow", "pip"):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, name)
        assert err.value.code == deprepair.ERROR_PACKAGE_PROTECTED, name
    assert not deprepair._jobs


def test_uninstalling_something_not_installed_is_refused(tmp_path, no_pip, monkeypatch):
    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "nothere")
    assert err.value.code == deprepair.ERROR_PACKAGE_NOT_INSTALLED


def test_uninstall_job_reports_who_depends_on_it(tmp_path, no_pip, monkeypatch):
    """卸 lmfit：mylab 依赖它——作业要把这件事报出来，界面才有东西可以二次确认。"""
    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    _account(tmp_path, ("lmfit", "1.3.2", "user_requested"), ("mylab", "0.1", "user_requested"))
    job = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
    assert job.dependents == ("mylab",)
    assert job.to_payload()["network_required"] is False
    leaf = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "mylab")
    assert leaf.dependents == ()


def test_update_and_uninstall_need_an_existing_environment(tmp_path, no_pip):
    for op in (deprepair.OP_UPDATE, deprepair.OP_UNINSTALL):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_package_job(tmp_path, op, "lmfit")
        assert err.value.code == deprepair.ERROR_PACKAGE_ENV_MISSING


def test_install_without_an_environment_plans_to_create_one(tmp_path, no_pip, monkeypatch):
    monkeypatch.setattr(deprepair, "base_python", lambda: sys.executable)
    job = deprepair.create_package_job(tmp_path, deprepair.OP_INSTALL, "lmfit")
    assert job.creates_environment is True and job.python == ""


def test_install_without_a_base_python_is_refused(tmp_path, no_pip, monkeypatch):
    monkeypatch.setattr(deprepair, "base_python", lambda: None)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_package_job(tmp_path, deprepair.OP_INSTALL, "lmfit")
    assert err.value.code == deprepair.ERROR_MANAGED_UNAVAILABLE


def test_low_disk_stops_at_plan_time(tmp_path, no_pip, monkeypatch):
    _fake_ready_env(tmp_path)
    Usage = type("U", (), {"free": 10 * 1024 * 1024, "total": 1, "used": 1})
    monkeypatch.setattr(deprepair.shutil, "disk_usage", lambda p: Usage())
    for op in (deprepair.OP_INSTALL, deprepair.OP_UPDATE):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_package_job(tmp_path, op, "lmfit")
        assert err.value.code == deprepair.ERROR_PACKAGE_DISK_LOW
    # 卸载不下载东西，磁盘满也让它过（正是腾空间的动作）
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")


# ===========================================================================
# 五、作业绑定与并发
# ===========================================================================
def test_running_an_unknown_job_is_refused(no_pip):
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.run_package_job("nope")
    assert err.value.code == deprepair.ERROR_NOT_ALLOWED


def test_a_changed_environment_makes_the_job_stale(tmp_path, no_pip, monkeypatch):
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    job = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
    python.write_text("changed", encoding="utf-8")  # pyvenv.cfg / 解释器变了
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.run_package_job(job.job_id)
    assert err.value.code == deprepair.ERROR_PLAN_STALE
    assert deprepair.get_package_job(job.job_id) is None


def test_jobs_are_bound_to_their_project(client, tmp_path, no_pip, monkeypatch):
    """A 项目形成的作业不能在 B 项目的请求里执行。"""
    from tavotto import app as m

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _fake_ready_env(a)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    job = deprepair.create_package_job(a, deprepair.OP_UNINSTALL, "lmfit")
    pid_b = m.open_project(str(b))["id"]
    try:
        resp = client.post(f"/api/engine/packages/run?pj={pid_b}", json={"job_id": job.job_id})
        assert resp.status_code == 409
        assert resp.get_json()["code"] == deprepair.ERROR_NOT_ALLOWED
    finally:
        m.close_project(pid_b, wait=True)


def test_run_endpoint_reads_nothing_but_the_job_id(client, tmp_path, no_pip, monkeypatch):
    """请求体里塞 op / spec / python 都不算数：执行的是作业里那一件事。"""
    from tavotto import app as m

    _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    seen: list[str] = []
    monkeypatch.setattr(
        deprepair, "run_package_job_async", lambda job_id, on_event=None: seen.append(job_id)
    )
    pid = m.open_project(str(tmp_path))["id"]
    try:
        job = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
        resp = client.post(
            f"/api/engine/packages/run?pj={pid}",
            json={"job_id": job.job_id, "op": "install", "spec": "evil", "python": "/bin/sh"},
        )
        assert resp.status_code == 200 and resp.get_json()["started"] is True
        assert seen == [job.job_id]
    finally:
        m.close_project(pid, wait=True)


def test_plan_endpoint_gives_stable_codes(client, tmp_path, no_pip):
    from tavotto import app as m

    pid = m.open_project(str(tmp_path))["id"]
    try:
        resp = client.post(
            f"/api/engine/packages/plan?pj={pid}", json={"op": "uninstall", "spec": "x"}
        )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == deprepair.ERROR_PACKAGE_ENV_MISSING
        resp = client.post(
            f"/api/engine/packages/plan?pj={pid}", json={"op": "install", "spec": "-r x"}
        )
        assert resp.get_json()["code"] == deprepair.ERROR_REQUIREMENT_INVALID
    finally:
        m.close_project(pid, wait=True)


def test_list_endpoint_without_project_is_a_disabled_reason_not_an_error(client, monkeypatch):
    from tavotto import app as m

    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    resp = client.get("/api/engine/packages")
    assert resp.status_code == 200
    assert resp.get_json()["capability"]["reason"] == "no_project"


def test_a_job_and_a_repair_share_one_environment_lock(tmp_path, no_pip, monkeypatch):
    """修复正在装东西时形成作业 → busy；作业执行时另一个作业 → busy。同一把锁。"""
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    key = deprepair._env_key(deprepair.TARGET_MANAGED, str(python), str(tmp_path))
    with engine_pool.mutating_environment(key, str(python)):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
        assert err.value.code == deprepair.ERROR_BUSY
    job = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
    with engine_pool.mutating_environment(key, str(python)):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.run_package_job(job.job_id)
        assert err.value.code == deprepair.ERROR_BUSY
    assert not envlease.is_mutating(str(python))


def test_a_native_session_blocks_package_jobs_with_its_own_code(tmp_path, no_pip, monkeypatch):
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    job = deprepair.create_package_job(tmp_path, deprepair.OP_UNINSTALL, "lmfit")
    with envlease.native_lease(str(python), "sess-1"):
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.run_package_job(job.job_id)
    assert err.value.code == deprepair.ERROR_IN_USE_BY_NATIVE


def test_listing_reports_busy_while_the_environment_is_mutating(tmp_path, monkeypatch):
    python = _fake_ready_env(tmp_path)
    monkeypatch.setattr(deprepair, "inventory", lambda p: dict(_INV))
    monkeypatch.setattr(deprepair, "custom_package_index", lambda p: False)
    key = deprepair._env_key(deprepair.TARGET_MANAGED, str(python), str(tmp_path))
    with engine_pool.mutating_environment(key, str(python)):
        assert deprepair.list_managed_packages(tmp_path)["busy"] is True
    assert deprepair.list_managed_packages(tmp_path)["busy"] is False


# ===========================================================================
# 六、记账
# ===========================================================================
def test_forget_install_removes_exactly_that_distribution(tmp_path):
    managedenv.write_manifest(tmp_path, managedenv.new_manifest(tmp_path, "/x/py"))
    _account(
        tmp_path, ("Scikit_Learn", "1.5", "user_requested"), ("lmfit", "1.3", "user_requested")
    )
    assert managedenv.forget_install(tmp_path, "scikit-learn") is True  # PEP 503 同名
    assert managedenv.forget_install(tmp_path, "scikit-learn") is False
    assert [
        e["distribution"] for e in managedenv.read_manifest(tmp_path)["installed_by_tavotto"]
    ] == ["lmfit"]
    assert managedenv.installed_requirements(tmp_path) == ["lmfit==1.3"]


def test_snapshots_are_kept_bounded_and_named_without_paths(tmp_path):
    managedenv.write_manifest(tmp_path, managedenv.new_manifest(tmp_path, "/x/py"))
    for i in range(managedenv.SNAPSHOT_KEEP + 3):
        managedenv.record_snapshot(tmp_path, f"before-install-pkg{i}", f"pkg{i}==1\n")
    names = managedenv.list_snapshots(tmp_path)
    assert len(names) == managedenv.SNAPSHOT_KEEP
    assert all(n.endswith(".txt") and "/" not in n for n in names)
    assert deprepair.diagnostics_state(tmp_path)["snapshots"] == managedenv.SNAPSHOT_KEEP


# ===========================================================================
# 七、真安装（不联网：本地 wheel）
# ===========================================================================
def _in_venv(python: str, expr: str) -> str:
    out = subprocess.run(
        [python, "-c", expr], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@needs_worker
def test_install_update_uninstall_end_to_end(tmp_path, wheelhouse, offline_managed_env):
    """装进受管环境 → 账上有、环境里 import 得到 → 升级（同版本，幂等）→ 卸掉 →
    账上没了、import 不到、matplotlib 仍好、快照留下了前后各一份。

    **系统 Python 一个字节没动**：装完在宿主解释器里 import 那个包必须仍然失败。
    """
    project = tmp_path / "paper"
    project.mkdir()
    events: list[dict] = []

    def run(op: str, spec: str) -> dict:
        job = deprepair.create_package_job(project, op, spec)
        deprepair.run_package_job_async(job.job_id, events.append)
        rec = wait_for(job.job_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        return rec

    # ---- install（顺带建环境）----
    rec = run(deprepair.OP_INSTALL, FIXTURE_DIST)
    python = managedenv.python_of(project)
    assert python, "受管环境该建出来了"
    assert _in_venv(python, f"import {FIXTURE_IMPORT}; print({FIXTURE_IMPORT}.VALUE)") == "42"
    assert rec["result"]["version"] == "1.0"
    ledger = managedenv.installed_entry(project, FIXTURE_DIST)
    assert ledger and ledger["reason"] == managedenv.REASON_USER_REQUESTED
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            [sys.executable, "-c", f"import {FIXTURE_IMPORT}"], check=True, capture_output=True
        )
    listing = deprepair.list_managed_packages(project)
    assert [u["distribution"] for u in listing["user"]] == [FIXTURE_DIST]
    assert listing["user"][0]["status"] == deprepair.PKG_INSTALLED
    assert listing["environment"]["in_use"] is True, "装进去就该让这个项目用它"
    states = [e["state"] for e in events]
    assert states[0] == deprepair.STATE_PREPARING and deprepair.STATE_VERIFYING in states

    # ---- update（wheelhouse 里没有更新的版本 → pip 说 already satisfied，幂等）----
    rec = run(deprepair.OP_UPDATE, FIXTURE_DIST)
    assert rec["result"]["version"] == "1.0"

    # ---- uninstall ----
    rec = run(deprepair.OP_UNINSTALL, FIXTURE_DIST)
    assert rec["result"]["op"] == deprepair.OP_UNINSTALL
    out = subprocess.run([python, "-c", f"import {FIXTURE_IMPORT}"], capture_output=True, text=True)
    assert out.returncode != 0, "卸完还 import 得到"
    assert managedenv.installed_entry(project, FIXTURE_DIST) is None
    assert managedenv.read_manifest(project)["state"] == managedenv.STATE_READY
    assert _in_venv(python, "import matplotlib; print('ok')") == "ok"
    snaps = managedenv.list_snapshots(project)
    assert any("before-uninstall" in n for n in snaps) and any("after-install" in n for n in snaps)
    assert deprepair.list_managed_packages(project)["user"] == []


@needs_worker
def test_a_newer_wheel_really_upgrades(tmp_path, wheelhouse, offline_managed_env):
    project = tmp_path / "paper"
    project.mkdir()
    job = deprepair.create_package_job(project, deprepair.OP_INSTALL, f"{FIXTURE_DIST}==1.0")
    deprepair.run_package_job_async(job.job_id)
    assert wait_for(job.job_id)["state"] == deprepair.STATE_DONE
    build_wheel(wheelhouse, version="1.1")
    job = deprepair.create_package_job(project, deprepair.OP_UPDATE, FIXTURE_DIST)
    deprepair.run_package_job_async(job.job_id)
    rec = wait_for(job.job_id)
    assert rec["state"] == deprepair.STATE_DONE, rec
    assert rec["result"]["version"] == "1.1"
    assert managedenv.installed_entry(project, FIXTURE_DIST)["resolved_version"] == "1.1"


@needs_worker
def test_a_missing_package_is_reported_with_its_own_code(tmp_path, wheelhouse, offline_managed_env):
    project = tmp_path / "paper"
    project.mkdir()
    job = deprepair.create_package_job(project, deprepair.OP_INSTALL, "tavotto-does-not-exist")
    deprepair.run_package_job_async(job.job_id)
    rec = wait_for(job.job_id)
    assert rec["state"] == deprepair.STATE_FAILED
    assert rec["code"] == deprepair.ERROR_NOT_FOUND
    # 失败之后环境仍然可用（建好了、matplotlib 在）——失败保留环境可用性
    python = managedenv.python_of(project)
    assert python and _in_venv(python, "import matplotlib; print('ok')") == "ok"
