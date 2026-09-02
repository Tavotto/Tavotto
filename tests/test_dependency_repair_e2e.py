"""受控依赖修复的**真安装**端到端（ADR 0019）。

这一组不 mock 安装：真建 venv、真跑 pip、真起 worker、真出 Figure。

    打开项目 → 跑脚本 → missing_dependency → 界面给出修复选项
        → 用户确认 → pip 装进那个环境 → 三层验证 → worker 重建
        → Figure 出来 → 改标题 → 导出

**不联网**：临时目录里手工造一个纯 Python wheel，用 pip 自己的
`PIP_FIND_LINKS` + `PIP_NO_INDEX` 指过去。这两个环境变量正是「index 用那个
环境自己的配置，Tavotto 不覆盖也不绕过」的可测形态——安装命令一个字节都
不用为测试改动。

断言必须证明**包真的进了那个环境**（site-packages 里有那个文件、
`sys.executable` 是那个解释器），而不是「字符串选中了某条路径」。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support.dependency_repair import (
    FIXTURE_DIST,
    FIXTURE_IMPORT,
    needs_worker,
    real_venv,
    site_packages,
    wait_for,
)
from tavotto.engine import (
    deprepair,
    managedenv,
    pool as engine_pool,
    projectenv,
)

#: fixture 走插件（见 support/dependency_repair.py 的模块说明）
pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    """本文件每条用例前后都清一遍模块级状态。"""


pytestmark = needs_worker


def _probe(client, script: str = "figure.py") -> dict:
    """走**真实产品路由**跑一次脚本（素材库打开旧项目走的就是这条）。"""
    resp = client.post("/api/registry/probe", json={"script": script})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _plan(
    client, module: str, target: str, script: str = "figure.py", distribution: str = ""
) -> dict:
    body = {"module": module, "script": script, "target": target}
    if distribution:
        body["distribution"] = distribution
    resp = client.post("/api/engine/dependency/plan", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["plan"]


def _install(client, plan_id: str) -> dict:
    resp = client.post("/api/engine/dependency/install", json={"plan_id": plan_id})
    assert resp.status_code == 200, resp.get_json()
    return wait_for(plan_id)


def _in_venv(python: str, expr: str) -> str:
    out = subprocess.run(
        [python, "-c", expr],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# ===========================================================================
# 黄金路径：装进项目自己的 .venv
# ===========================================================================
def test_golden_path_install_into_the_project_venv(client, project, wheelhouse):
    """用户看到「缺少 X」→ 点一次 → 图出来 → 还能编辑和导出。

    这条链的每一步都走真实入口：`/api/registry/probe`（素材库打开脚本）、
    `/api/engine/dependency/plan`、`/api/engine/dependency/install`、
    `/api/engine/render`（编辑）、`/api/export`（导出）。
    """
    from tavotto import app as m

    venv = real_venv(project)
    # 项目自己声明过这个依赖 —— 最可信的那一档解析
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))

    # ---- 1. 跑脚本：缺依赖，且**带着可执行的修复建议** -------------------
    first = _probe(client)
    err = first["error"]
    assert err["code"] == "missing_dependency"
    assert err["params"]["module"] == FIXTURE_IMPORT
    # Session 7 已经体检过项目 venv：它健康，只是没有这个包
    assert err["params"]["project_env"] == projectenv.ERROR_MODULE_MISSING
    repair = err["dependency_repair"]
    assert repair["requirement"]["installable"] is True
    assert repair["requirement"]["resolution_source"] == "project_declared"
    targets = {t["kind"]: t for t in repair["targets"]}
    assert targets["project_venv"]["modifies_user_environment"] is True
    assert targets["project_venv"]["venv"] == ".venv"

    # ---- 2. 形成计划：说清楚装什么、装到哪、会不会改用户的环境 -----------
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_PROJECT_VENV)
    assert plan["modifies_user_environment"] is True
    assert plan["network_required"] is True
    assert plan["python"].startswith(".venv"), "界面上只出项目相对路径"
    assert plan["distribution"].replace("_", "-") == FIXTURE_DIST

    # ---- 3. 安装 ----------------------------------------------------------
    final = _install(client, plan["plan_id"])
    assert final["state"] == deprepair.STATE_DONE, final
    assert final["result"]["version"] == "1.0"

    # ---- 4. 包**真的**进了那个 venv（不是「字符串对了」）------------------
    assert (site_packages(venv) / f"{FIXTURE_IMPORT}.py").is_file()
    python = projectenv.interpreter_of(venv)
    where = _in_venv(python, f"import {FIXTURE_IMPORT},sys;print(sys.executable)")
    assert Path(where).is_relative_to(project)

    # ---- 5. worker 用新解释器重建，Figure 出来 ---------------------------
    second = _probe(client)
    assert second["error"] is None, second["error"]
    assert second["stems"] == ["Fig1"]
    worker = engine_pool.get("figure.py", str(project), second["entry"])
    assert engine_pool.same_python(worker.python, python)
    assert worker.python_source == engine_pool.SOURCE_PROJECT_VENV

    # ---- 6. 编辑（改标题）+ 导出：修好之后是完整可用的，不是「能打开」----
    asset_id = second["descriptors"][0]["asset_id"]
    rendered = client.post("/api/engine/render", json={"id": asset_id, "patches": []})
    assert rendered.status_code == 200, rendered.get_json()
    manifest = rendered.get_json()["manifest"]
    gid = next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title"
    )
    patch = [{"gid": gid, "prop": "text", "value": "Repaired Title"}]
    edited = client.post("/api/engine/render", json={"id": asset_id, "patches": patch})
    assert edited.status_code == 200, edited.get_json()
    assert any(
        f["value"] == "Repaired Title"
        for el in edited.get_json()["manifest"]["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text"
    )
    exported = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 80,
            "formats": ["pdf"],
            "stem": "repaired",
            "objects": [
                {
                    "type": "panel",
                    "id": asset_id,
                    "x_mm": 0,
                    "y_mm": 0,
                    "w_mm": 100,
                    "h_mm": 80,
                    "overrides": patch,
                }
            ],
        },
    )
    assert exported.status_code == 200, exported.get_json()
    out = Path(exported.get_json()["export_dir"]) / exported.get_json()["files"][0]["name"]
    assert out.is_file() and out.stat().st_size > 0


def test_the_sandbox_is_unchanged_after_a_repair(client, project, wheelhouse):
    """修好依赖**不等于**放宽执行语义：仍然是 safe 档（ADR 0019 §十四）。

    「用项目 .venv + pip install」如果顺手变成了 native 执行，写入守卫、
    删除守卫、相对路径只读回退全部失效——那是数据损坏级的退化。
    """
    from tavotto import app as m

    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    _probe(client)
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_PROJECT_VENV)
    assert _install(client, plan["plan_id"])["state"] == deprepair.STATE_DONE

    result = _probe(client)
    assert result["error"] is None
    worker = engine_pool.get("figure.py", str(project), result["entry"])
    spec = worker.spec
    assert spec.profile == "safe"
    assert spec.passthrough_savefig is False, "savefig 仍然被捕获，不落用户磁盘"
    assert spec.argv == (), "safe 档里脚本看不到 argv"
    assert Path(spec.cwd) == worker.sandbox, "cwd 仍然是会话沙盒（写入边界）"
    # 脚本 savefig 出来的名字被捕获成 stem，磁盘上的图库目录零改动
    assert not (project / "Fig1.pdf").exists()


def test_pip_success_alone_is_not_success(client, project, wheelhouse, monkeypatch):
    """**负向反证 #5**：pip 退出码 0 之后不做 import 探测，这条必须红。

    「装进了另一个环境 / 装的是同名的另一个包 / 扩展模块 ABI 对不上」三种
    都是 exit 0 + import 失败。这里把 pip 换成一个「什么都不装但成功返回」
    的桩，验证第二层验证真的拦得住。
    """
    from tavotto import app as m

    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    _probe(client)
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_PROJECT_VENV)
    monkeypatch.setattr(
        deprepair, "_pip_install", lambda py, req, ev, log: ("", "Successfully installed\n")
    )
    final = _install(client, plan["plan_id"])
    assert final["state"] == deprepair.STATE_FAILED
    assert final["code"] == deprepair.ERROR_IMPORT_STILL_FAILED


def test_a_package_that_does_not_exist_is_reported_as_such(client, project, wheelhouse):
    """本地 index 里没有这个包 → `dependency_not_found`，不是笼统的失败。"""
    from tavotto import app as m

    real_venv(project)
    (project / "requirements.txt").write_text(
        "tavotto-test-nonexistent-package\n", encoding="utf-8"
    )
    (project / "figure.py").write_text(
        "import tavotto_test_nonexistent_package\n", encoding="utf-8"
    )
    m.open_project(str(project))
    _probe(client)
    plan = _plan(client, "tavotto_test_nonexistent_package", deprepair.TARGET_PROJECT_VENV)
    final = _install(client, plan["plan_id"])
    assert final["state"] == deprepair.STATE_FAILED
    assert final["code"] == deprepair.ERROR_NOT_FOUND, final


# ===========================================================================
# Tavotto 受管环境
# ===========================================================================
# `offline_managed_env` 夹具搬到了 `support/dependency_repair.py`：包管理那组
# 用例（`test_package_management.py`）要用同一份，抄两份会漂。


def test_managed_environment_end_to_end(client, project, wheelhouse, offline_managed_env):
    """项目**没有** venv 时：建一个 Tavotto 自己的隔离环境并装进去。

    验证跑的真是那个环境（`sys.prefix` / `sys.executable` 都在受管目录里），
    并且它在项目之外——用户的源码与已有环境一个字节都没动。
    """
    from tavotto import app as m
    from tavotto.engine import config as engine_config

    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    first = _probe(client)
    repair = first["error"]["dependency_repair"]
    kinds = {t["kind"] for t in repair["targets"]}
    assert deprepair.TARGET_PROJECT_VENV not in kinds, "项目里根本没有 venv"
    assert deprepair.TARGET_MANAGED in kinds

    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_MANAGED)
    assert plan["modifies_user_environment"] is False
    assert plan["creates_environment"] is True
    final = _install(client, plan["plan_id"])
    assert final["state"] == deprepair.STATE_DONE, final

    python = managedenv.python_of(project)
    assert python, "受管环境应该建出来并标成 ready"
    assert Path(python).is_relative_to(engine_config.data_dir())
    assert not Path(python).is_relative_to(project), "绝不建在用户项目里"
    prefix, executable = _in_venv(
        python, f"import {FIXTURE_IMPORT},sys;print(sys.prefix);print(sys.executable)"
    ).splitlines()
    # macOS 上 `/var` 是指向 `/private/var` 的软链接：比路径必须先 resolve，
    # 否则「同一个目录」会被判成两个。
    assert Path(prefix).resolve() == managedenv.venv_dir(project).resolve()
    # 比路径**两边都要归一**，而归一的对象是**父目录**不是解释器本身：
    #   * Windows runner 的 TEMP 是 8.3 短名（`C:\Users\RUNNER~1\...`），
    #     子进程报回来的 `sys.executable` 带短名，而 `.resolve()` 会把它展开成
    #     长名（`runneradmin`）——一边展开一边不展开，`is_relative_to` 按路径段
    #     比就永远不等（Windows 平台档上真红过一次）；
    #   * 但**不能 resolve 解释器本身**：`venv/bin/python` 在 POSIX 上是指向
    #     基础解释器的软链接，跟着它走每个 venv 都会被判成「在别处」
    #     （projectenv 里同一个坑）。
    # 父目录（`Scripts/` / `bin/`）两个平台上都不是软链接，resolve 它只把
    # 短名与 `/var`→`/private/var` 这类展开掉，正好是要的那一半。
    assert Path(executable).parent.resolve().is_relative_to(managedenv.env_dir(project).resolve())

    # manifest 如实记账：重建要照着它装回去
    data = managedenv.read_manifest(project)
    assert data["created_by_tavotto"] is True
    assert data["state"] == managedenv.STATE_READY
    assert data["python_version"]
    entry = next(
        e for e in data["installed_by_tavotto"] if e["distribution"] == plan["distribution"]
    )
    assert entry["resolved_version"] == "1.0"
    assert entry["import_name"] == FIXTURE_IMPORT

    # 图真的出得来，且解释器来源标得对（受管 ≠ 项目自带）
    second = _probe(client)
    assert second["error"] is None, second["error"]
    assert second["stems"] == ["Fig1"]
    assert engine_pool.resolve_worker_python(str(project))[1] == engine_pool.SOURCE_MANAGED_PROJECT


def test_managed_environments_do_not_leak_across_projects(
    client, tmp_path, wheelhouse, offline_managed_env
):
    """**负向反证 #9 的真环境版**：两个项目各建各的，装的东西互不可见。"""
    from tavotto import app as m

    a, b = tmp_path / "paper-a", tmp_path / "paper-b"
    for root in (a, b):
        root.mkdir()
        (root / "figure.py").write_text(
            "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('Fig1.pdf')\n",
            encoding="utf-8",
        )
        (root / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    try:
        m.open_project(str(a))
        plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_MANAGED)
        assert _install(client, plan["plan_id"])["state"] == deprepair.STATE_DONE
        assert managedenv.python_of(a)
        # B 项目的受管环境根本还不存在——A 装的包不会凭空出现在它那里
        assert managedenv.python_of(b) is None
        assert managedenv.env_dir(a) != managedenv.env_dir(b)
    finally:
        for root in (a, b):
            for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(root)]:
                m.close_project(pid, wait=True)
            engine_pool.shutdown_all(str(root), wait=True)


def test_managed_environment_can_be_rebuilt(client, project, wheelhouse, offline_managed_env):
    """受管环境坏了可以整个扔掉重建——这是它相对「改用户环境」的唯一优势。"""
    from tavotto import app as m

    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_MANAGED)
    assert _install(client, plan["plan_id"])["state"] == deprepair.STATE_DONE
    before = managedenv.python_of(project)
    assert before

    resp = client.post("/api/engine/environment/managed/rebuild", json={})
    assert resp.status_code == 200, resp.get_json()
    # 端点只负责「开始」——拆旧（读账 + 删除）已经搬进重建线程的那把环境锁里
    # （Codex 评审 P1：删除在锁外时，一个已形成的 plan 能往正被删的 venv 里装）
    assert resp.get_json() == {"started": True}
    final = wait_for(deprepair.REBUILD_PROGRESS_ID)
    assert final["state"] == deprepair.STATE_DONE, final

    after = managedenv.python_of(project)
    assert after, "重建之后必须重新可用"
    # 我们装过的那个包被装回去了（不是「建了个空环境就说完成」）
    assert _in_venv(after, f"import {FIXTURE_IMPORT};print('ok')") == "ok"


def test_cancelling_leaves_the_managed_environment_marked_incomplete(
    client, project, wheelhouse, offline_managed_env, monkeypatch
):
    """取消之后**不假装干净**：受管环境标成未完成，下次重建。"""
    from tavotto import app as m

    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_MANAGED)

    real_pip = deprepair._pip_install

    def _slow(python, requirement, cancel_ev, on_log):
        # 第一个包（基础栈）正常装，之后的那次故意等到取消到达
        if requirement in managedenv.BASE_PACKAGES:
            return real_pip(python, requirement, cancel_ev, on_log)
        deadline = time.time() + 30
        while not cancel_ev.is_set() and time.time() < deadline:
            time.sleep(0.05)
        return deprepair.ERROR_CANCELLED, "已取消"

    monkeypatch.setattr(deprepair, "_pip_install", _slow)
    client.post("/api/engine/dependency/install", json={"plan_id": plan["plan_id"]})
    deadline = time.time() + 60
    while (
        deprepair.progress(plan["plan_id"]).get("state") != deprepair.STATE_INSTALLING
        and time.time() < deadline
    ):
        time.sleep(0.05)
    assert client.post(
        "/api/engine/dependency/cancel", json={"plan_id": plan["plan_id"]}
    ).get_json()["cancelling"]
    final = wait_for(plan["plan_id"])
    assert final["state"] == deprepair.STATE_CANCELLED
    assert managedenv.read_manifest(project)["state"] == managedenv.STATE_INCOMPLETE
    assert managedenv.python_of(project) is None, "未完成的环境不许被复用"


# ===========================================================================
# worker 生命周期（真进程）
# ===========================================================================
def test_the_old_worker_is_gone_and_the_new_one_uses_the_new_interpreter(
    client, project, wheelhouse
):
    """**负向反证 #6**：装完不作废 worker，这条必须红。

    磁盘上多一个包不会让一个已经起来的解释器看见它——`sys.modules` 是缓存
    的、已加载的动态库不会重载。断言看的是**进程身份**（pid / generation）
    与解释器身份，不是「有没有调用某个函数」。
    """
    from tavotto import app as m

    venv = real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    _probe(client)

    # 修复之前先起一个用**默认**解释器的会话，握在手里
    old = engine_pool.get("figure.py", str(project), "__main__")
    old_pid = old.proc.pid
    assert not engine_pool.same_python(old.python, projectenv.interpreter_of(venv))

    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_PROJECT_VENV)
    assert _install(client, plan["plan_id"])["state"] == deprepair.STATE_DONE

    # 旧进程被收掉了（安装开始前就该停——它的 site-packages 正在被写）
    deadline = time.time() + 30
    while old.alive() and time.time() < deadline:
        time.sleep(0.1)
    assert not old.alive(), "安装期间那个环境上的旧会话必须已经停掉"

    new = engine_pool.get("figure.py", str(project), "__main__")
    assert new is not old
    assert new.proc.pid != old_pid
    assert new.generation > old.generation
    assert engine_pool.same_python(new.python, projectenv.interpreter_of(venv))
    assert new.ensure_built()["stems"], "新会话直接就能跑通脚本"


def test_diagnostics_explain_the_repair_without_leaking_paths(
    client, project, wheelhouse, offline_managed_env
):
    """诊断包要能回答「修过什么」，且不带路径 / index / 凭据。"""
    import io
    import zipfile

    from tavotto import app as m

    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    m.open_project(str(project))
    plan = _plan(client, FIXTURE_IMPORT, deprepair.TARGET_MANAGED)
    assert _install(client, plan["plan_id"])["state"] == deprepair.STATE_DONE

    blob = client.get("/api/diagnostics/bundle").data
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith("report.json"))
        report = json.loads(z.read(name).decode("utf-8"))
    section = report["project"]["dependency_repair"]
    assert section["rounds"]["figure.py"] == 1
    installed = section["managed_environment"]["installed"]
    assert any(e["resolved_version"] == "1.0" for e in installed)
    blob_text = json.dumps(section, ensure_ascii=False)
    assert str(project) not in blob_text
    assert sys.prefix not in blob_text
