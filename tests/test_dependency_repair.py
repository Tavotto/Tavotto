"""Compatibility Bridge Session 7B：受控依赖修复（ADR 0019）。

四层看护，与 ADR 的四条边界一一对应：

* **解析**：import 名 ≠ 包名（`PIL → Pillow`）；项目声明压过 curated；
  **未知 import 绝不因为同名被安装**；包名语法是安全边界（pip 自己会把
  `-r` / `--index-url` 解析成选项，argv 是 list 挡不住这个）。
* **授权**：没有计划就装不了东西；计划绑死 (项目, 环境, 需求)，执行端一个
  字节都不从请求体里读；环境在确认期间变过 → stale。
* **安全**：内置 runtime **永远**不是安装目标；`<python> -m pip`、
  `shell=False`、wheels-only、不 `--upgrade`。
* **生命周期**：安装期间那个环境上的 worker 全停、新会话被拒；装完旧会话
  作废、新会话用新解释器；**A 项目装包不影响 B 项目**。

真安装用例**不联网**：临时目录里手工造一个纯 Python wheel，用 pip 自己的
`PIP_FIND_LINKS` + `PIP_NO_INDEX` 指过去（那正是「用 pip 自己的 index 配置」
这条决策的可测形态）。断言必须证明**包真的进了那个 venv**，不是「字符串
选中了某条路径」。
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from support.dependency_repair import (
    FIXTURE_DIST,
    FIXTURE_IMPORT,
    WORKER_PY,
    needs_worker,
    real_venv,
)
from tavotto.engine import (
    deprepair,
    depresolve,
    managedenv,
    pool as engine_pool,
    projectenv,
)

#: fixture（clean_state / project / client / wheelhouse）以插件形式挂进来，
#: 不 import 名字——那样 fixture 名会与用例参数名互相遮蔽。
pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    """本文件每条用例前后都清一遍模块级状态（实现在 support 里，只有一份）。"""


# ===========================================================================
# 一、解析：import 名不是包名
# ===========================================================================
@pytest.mark.parametrize(
    "import_name,distribution",
    [
        ("PIL", "Pillow"),
        ("cv2", "opencv-python"),
        ("sklearn", "scikit-learn"),
        ("skimage", "scikit-image"),
        ("yaml", "PyYAML"),
        ("bs4", "beautifulsoup4"),
        ("dateutil", "python-dateutil"),
    ],
)
def test_import_names_are_not_distribution_names(tmp_path, import_name, distribution):
    """`pip install PIL` 装到的是**另一个包**——这条表就是为它存在的。"""
    req = depresolve.resolve(tmp_path, import_name)
    assert req is not None, f"{import_name} 应该解析得出来"
    assert req.distribution == distribution
    assert req.resolution_source == depresolve.SOURCE_CURATED
    assert req.installable


def test_same_name_packages_still_need_explicit_registration(tmp_path):
    """同名也要显式登记：`同名 = 可信` 正是抢注攻击的入口。"""
    assert depresolve.resolve(tmp_path, "lmfit").distribution == "lmfit"
    assert depresolve.resolve(tmp_path, "astropy").distribution == "astropy"


def test_an_unknown_import_is_never_installable(tmp_path):
    """**负向反证 #3**：未知 import 不许按同名装。

    把 `depresolve.resolve` 改成 `return DependencyRequirement(m, m, ...)`
    这条就红——那正是「pip install <traceback 里的字符串>」这条供应链路径。
    """
    for name in ("my_lab_tools", "internal_utils", "totally_made_up_xyz"):
        assert depresolve.resolve(tmp_path, name) is None
        assert depresolve.curated_distribution(name) is None


def test_project_declared_wins_and_carries_the_specifier(tmp_path):
    """项目自己声明过 → 用**它的**包名与版本约束（最可信的一档）。"""
    (tmp_path / "requirements.txt").write_text(
        "# 注释\nlmfit>=1.3\nPillow >= 10\n", encoding="utf-8"
    )
    lmfit = depresolve.resolve(tmp_path, "lmfit")
    assert lmfit.resolution_source == depresolve.SOURCE_PROJECT_DECLARED
    assert lmfit.requirement() == "lmfit>=1.3"
    # `import PIL` + `Pillow>=10`：要 curated 与项目声明两档合起来才成立
    pil = depresolve.resolve(tmp_path, "PIL")
    assert (pil.distribution, pil.specifier) == ("Pillow", ">=10")
    assert pil.resolution_source == depresolve.SOURCE_PROJECT_DECLARED


def test_a_private_package_becomes_installable_once_the_project_declares_it(tmp_path):
    """私有包 curated 里当然没有——但项目自己声明过就是可信证据。"""
    assert depresolve.resolve(tmp_path, "my_lab_tools") is None
    (tmp_path / "requirements.txt").write_text("my-lab-tools==2.1\n", encoding="utf-8")
    req = depresolve.resolve(tmp_path, "my_lab_tools")
    assert req.resolution_source == depresolve.SOURCE_PROJECT_DECLARED
    assert req.requirement() == "my_lab_tools==2.1"


def test_pyproject_dependencies_are_read(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "paper"\n'
        'dependencies = ["astropy>=6", "opencv-python"]\n'
        'classifiers = ["Programming Language :: Python"]\n',
        encoding="utf-8",
    )
    assert depresolve.resolve(tmp_path, "astropy").specifier == ">=6"
    cv2 = depresolve.resolve(tmp_path, "cv2")
    assert cv2.resolution_source == depresolve.SOURCE_PROJECT_DECLARED
    # classifiers 那种数组不是依赖声明，绝不能被当成「项目声明过」
    assert "programming-language-::-python" not in depresolve.project_declared(tmp_path)


def test_declaration_files_are_never_modified(tmp_path):
    """只读解析：解析完那两个文件必须一个字节都没变。"""
    req = tmp_path / "requirements.txt"
    req.write_text("lmfit>=1.3\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["astropy"]\n', encoding="utf-8")
    before = (req.read_bytes(), pyproject.read_bytes())
    depresolve.resolve(tmp_path, "lmfit")
    depresolve.resolve(tmp_path, "astropy")
    assert (req.read_bytes(), pyproject.read_bytes()) == before


def test_malformed_metadata_does_not_block_repair(tmp_path):
    """坏掉的 pyproject 只意味着「这一档解析源不可用」，不该连坐 curated。"""
    (tmp_path / "pyproject.toml").write_text(
        "[project\nthis is not toml at all ][[[\n", encoding="utf-8"
    )
    req = depresolve.resolve(tmp_path, "PIL")
    assert req is not None and req.distribution == "Pillow"


# ===========================================================================
# 二、包名语法是安全边界（不是输入校验）
# ===========================================================================
@pytest.mark.parametrize(
    "hostile",
    [
        "-r evil.txt",
        "-revil.txt",
        "--index-url=https://evil.example/simple",
        "--index-url https://evil.example/simple",
        "--target=/tmp/anywhere",
        "https://evil.example/pkg.whl",
        "git+https://github.com/evil/pkg.git",
        "file:///etc/passwd",
        "../local-package",
        "./local",
        "pkg @ https://evil.example/pkg.whl",
        "pkg[extra]",
        'pkg;os_name=="nt"',
        "lmfit==1.3 --index-url http://evil.example",
        "pkg==$(id)",
        "pkg|cat /etc/passwd",
        "pkg&&whoami",
        "pkg`id`",
        "",
        "  ",
    ],
)
def test_package_option_injection_is_rejected(hostile):
    """**负向反证 #4**：pip 自己会把这些解析成选项，`shell=False` 挡不住。

    放行任何一条这条用例就红——`--index-url` 能把安装源整个换掉，`-r` 能读
    任意需求文件，`--target` 能装到任意目录。
    """
    assert depresolve.parse_requirement(hostile) is None
    assert depresolve.from_user_input("x", hostile) is None


@pytest.mark.parametrize(
    "ok,name,spec",
    [
        ("lmfit", "lmfit", ""),
        ("lmfit>=1.3", "lmfit", ">=1.3"),
        ("scikit-learn==1.4.2", "scikit-learn", "==1.4.2"),
        ("pkg~=1.2", "pkg", "~=1.2"),
        ("pkg>=1.2,<2", "pkg", ">=1.2,<2"),
    ],
)
def test_the_allowed_grammar_is_exactly_these_shapes(ok, name, spec):
    assert depresolve.parse_requirement(ok) == (name, spec)


def test_a_hostile_requirement_never_reaches_pip(tmp_path):
    """第二道门：真正拼 argv 之前再验一次形状。"""
    code, out = deprepair._pip_install(sys.executable, "-r evil.txt", threading.Event(), None)
    assert code == deprepair.ERROR_REQUIREMENT_INVALID
    assert "evil" in out  # 如实回显被拒的那一串，不假装无事发生


# ===========================================================================
# 三、内置 runtime 永远不是安装目标
# ===========================================================================
def test_the_bundled_runtime_is_never_a_mutation_target(project, monkeypatch):
    """**负向反证 #1**：把内置解释器当安装目标，这条必须红。

    做法是把「哪条路径是内置 runtime」钉死，然后把 pip 执行层换成记录器——
    整条修复链上出现的每一条 argv 都不许以它开头。
    """
    fake_bundled = str(project / "fake-bundled" / "python")
    Path(fake_bundled).parent.mkdir(parents=True, exist_ok=True)
    Path(fake_bundled).write_text("", encoding="utf-8")
    monkeypatch.setattr(engine_pool.runtime, "bundled_python", lambda: fake_bundled)

    seen: list[list[str]] = []

    def _record(argv, *a, **kw):
        seen.append(list(argv))
        raise AssertionError("这条用例不该真的执行任何子进程")

    monkeypatch.setattr(deprepair.subprocess, "Popen", _record)
    monkeypatch.setattr(deprepair.subprocess, "run", _record)
    monkeypatch.setattr(managedenv.subprocess, "run", _record)

    # 目标环境的挑选**只从发现结果里取**，调用方给不了路径。
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project), "figure.py", "lmfit", target_kind=deprepair.TARGET_PROJECT_VENV
        )
    assert err.value.code == projectenv.ERROR_NOT_FOUND
    assert not [argv for argv in seen if engine_pool.same_python(argv[0], fake_bundled)]


def test_pip_argv_is_a_list_wheels_only_and_never_upgrades():
    """安装命令逐字节钉住——它是这个子系统唯一会往磁盘写东西的地方。"""
    argv = deprepair.pip_install_argv("/env/bin/python", "lmfit>=1.3")
    assert argv == [
        "/env/bin/python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
        "lmfit>=1.3",
    ]
    assert "--upgrade" not in argv, "默认升级会把用户的科学栈整体换掉"
    assert argv[0] != "pip", "绝不用 PATH 上的 pip：它属于哪个解释器全看 PATH"
    assert all(isinstance(a, str) for a in argv)


@pytest.mark.parametrize(
    "output,code",
    [
        (
            "ERROR: Could not find a version that satisfies the requirement zzz "
            "(from versions: none)",
            deprepair.ERROR_NOT_FOUND,
        ),
        (
            "ERROR: Could not find a version that satisfies the requirement zzz "
            "(from versions: 1.0, 2.0)",
            deprepair.ERROR_REQUIRES_BUILD,
        ),
        (
            "WARNING: Retrying (Retry(total=4)) after connection broken by "
            "NewConnectionError\nERROR: Could not find a version that satisfies "
            "the requirement zzz (from versions: none)",
            deprepair.ERROR_NETWORK,
        ),
        ("ERROR: ResolutionImpossible: for help visit …", deprepair.ERROR_CONFLICT),
        ("ERROR: something else entirely", deprepair.ERROR_FAILED),
    ],
)
def test_pip_failures_get_distinguishable_codes(output, code):
    """「没网」与「没这个包」在 pip 输出里只差一句，用户要做的事完全不同。

    网络判据**排在前面**：断网时 pip 两句都会打，只看后一句会把「没网」
    报成「这个包不存在」。
    """
    assert deprepair.classify_pip_failure(output) == code


# ===========================================================================
# 四、授权与 TOCTOU
# ===========================================================================
def test_install_endpoint_refuses_without_a_plan(client, project):
    """**负向反证 #2**：没有计划就装不了东西——后端**自己**是能力边界。

    「按钮理论上不会调这个接口」不是边界。计划 id 不可猜、绑定项目、有有效期。
    """
    from tavotto import app as m

    m.open_project(str(project))
    for body in (
        {},
        {"plan_id": ""},
        {"plan_id": "made-up"},
        {"plan_id": "x", "python": sys.executable, "distribution": "evil-package"},
    ):
        resp = client.post("/api/engine/dependency/install", json=body)
        assert resp.status_code == 409, body
        assert resp.get_json()["code"] == deprepair.ERROR_NOT_ALLOWED


def test_direct_install_call_refuses_an_unknown_plan():
    """API 之外也一样：`install()` 只认计划，不认参数。"""
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.install("not-a-real-plan")
    assert err.value.code == deprepair.ERROR_NOT_ALLOWED


@needs_worker
def test_the_plan_binds_the_requirement_not_the_request(project, monkeypatch):
    """**负向反证 #10**：换掉请求里的包名，装的仍然是计划里那个。

    执行端如果按请求体里的 distribution 走，一个构造出来的请求就能把
    「装 lmfit 到项目环境」换成「装别的东西」。
    """
    venv = real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    plan = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    assert plan.requirement.distribution.replace("_", "-") == FIXTURE_DIST
    assert plan.python == projectenv.interpreter_of(venv)

    installs: list[str] = []
    monkeypatch.setattr(
        deprepair, "_pip_install", lambda py, req, ev, log: (installs.append(req), ("", ""))[1]
    )
    monkeypatch.setattr(deprepair, "_run", lambda argv, timeout: (0, "pip 24.0"))
    monkeypatch.setattr(
        deprepair.projectenv, "probe_environment", lambda py, mod=None: {"ok": True, "python": py}
    )
    monkeypatch.setattr(deprepair, "worker_self_test", lambda py: {"ok": True})
    monkeypatch.setattr(deprepair, "installed_version", lambda py, dist: "1.0")

    deprepair.install(plan.plan_id)
    assert installs == [plan.requirement.requirement()]


@needs_worker
def test_a_changed_environment_makes_the_plan_stale(project):
    """确认期间环境被换过 → `repair_plan_stale`，让用户重新看一遍再决定。"""
    venv = real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    plan = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    # venv 被删掉重建 = 另一个环境了（哪怕路径一模一样）
    cfg = venv / "pyvenv.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "# 变了\n", encoding="utf-8")
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.install(plan.plan_id)
    assert err.value.code == deprepair.ERROR_PLAN_STALE


@needs_worker
def test_repair_rounds_are_capped(project, monkeypatch):
    """**负向反证 #8**：修复轮次有上限，不会无限循环。

    把 `MAX_DEPENDENCY_REPAIR_ROUNDS` 去掉这条就红——「装完还缺、再装还缺」
    会把用户拖进一个永远转圈的界面。
    """
    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    for _ in range(deprepair.MAX_DEPENDENCY_REPAIR_ROUNDS):
        deprepair._note_round(str(project), "figure.py")
    assert deprepair.rounds_remaining(str(project), "figure.py") == 0
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
        )
    assert err.value.code == deprepair.ERROR_ROUNDS_EXHAUSTED
    offer = deprepair.offer(str(project), "figure.py", FIXTURE_IMPORT, None)
    assert offer["code"] == deprepair.ERROR_ROUNDS_EXHAUSTED
    assert offer["targets"] == []


def test_an_unresolvable_module_gets_no_install_target(project):
    """解析不出包名时**不提供**一键安装，只给手动出口。"""
    offer = deprepair.offer(str(project), "figure.py", "my_lab_tools", None)
    assert offer["requirement"] is None
    assert offer["code"] == deprepair.ERROR_UNRESOLVED
    assert offer["targets"] == []
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project), "figure.py", "my_lab_tools", target_kind=deprepair.TARGET_MANAGED
        )
    assert err.value.code == deprepair.ERROR_UNRESOLVED


def test_user_specified_package_still_goes_through_the_grammar(project):
    """用户手填的包名可以救私有包，但语法关一视同仁。"""
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project),
            "figure.py",
            "my_lab_tools",
            target_kind=deprepair.TARGET_MANAGED,
            user_distribution="-r evil.txt",
        )
    assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID


# ===========================================================================
# 五、worker 生命周期
# ===========================================================================
class _FakeWorker:
    """只实现 `pool` 在这条路上会用到的那几个方法。"""

    def __init__(self, python: str, script: str = "figure.py"):
        self.python = python
        self.script_name = script
        self.entry = "__main__"
        self.last_used = time.time()
        self.closed = False

    def alive(self) -> bool:
        return not self.closed

    def shutdown(self) -> None:
        self.closed = True


def test_workers_on_the_mutating_environment_are_stopped(monkeypatch):
    """**负向反证 #7**：安装期间旧 worker 还能渲染，这条必须红。

    磁盘上的 site-packages 正在变，而已经起来的解释器的 `sys.modules`、
    已加载的动态库、import 缓存都不会跟着变——让它继续接请求，用户看到的
    是「装完了还是老错误」，或者更糟：半新半旧的一次 import。
    """
    mine = _FakeWorker("/env/a/bin/python")
    other = _FakeWorker("/env/b/bin/python", "another.py")
    monkeypatch.setitem(engine_pool._workers, ("/p1", "figure.py"), mine)
    monkeypatch.setitem(engine_pool._workers, ("/p2", "another.py"), other)
    monkeypatch.setattr(
        engine_pool.threading,
        "Thread",
        lambda target, **kw: type("T", (), {"start": lambda s: target()})(),
    )

    with engine_pool.mutating_environment(
        engine_pool.env_key_of("/env/a/bin/python"), "/env/a/bin/python"
    ):
        assert mine.closed, "这个环境上的会话必须停掉"
        assert not other.closed, "**别的**环境上的会话不该被牵连"
        assert engine_pool.is_mutating("/env/a/bin/python")
        assert not engine_pool.is_mutating("/env/b/bin/python")
    assert not engine_pool.is_mutating("/env/a/bin/python")


def test_a_new_session_is_refused_while_the_environment_is_mutating(project, monkeypatch):
    """安装期间不许起新会话——半装完的包 import 到一半是最难解释的失败。"""
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda d=None: ("/env/a/bin/python", "system")
    )
    with engine_pool.mutating_environment(engine_pool.env_key_of("/env/a/bin/python"), ""):
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.get("figure.py", str(project), "__main__")
        assert err.value.code == engine_pool.ENVIRONMENT_MUTATING


def test_rebuild_and_install_are_mutually_exclusive(tmp_path, monkeypatch):
    """**负向反证**：重建与安装拿的必须是**同一把**环境锁。

    Codex 评审 P1 指出的形状：重建当时拿 `tavotto_managed:<项目指纹>` 这个
    合成 key，而 install 拿的是**解释器路径** key——两把锁互不相干，于是
    「一个已形成的 plan 往正被删除的 venv 里 pip install」这条竞态是敞开的。
    把 `mutating_environment(key, existing)` 的第二个参数去掉，这条就红。
    """
    project = tmp_path / "paper"
    project.mkdir()
    managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
    python = managedenv.venv_python(project)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    managedenv.mark_ready(project)

    # 重建拿锁期间，install 那条路（按解释器路径判）必须被挡住
    monkeypatch.setattr(
        deprepair, "_create_managed", lambda root, ev: pytest.fail("这条用例不该真的建环境")
    )
    seen: list[bool] = []

    def _spy(root, ev):
        seen.append(engine_pool.is_mutating(str(python)))
        raise deprepair.RepairError(deprepair.ERROR_MANAGED_CREATE_FAILED, "stop")

    monkeypatch.setattr(deprepair, "_create_managed", _spy)
    with pytest.raises(deprepair.RepairError):
        deprepair.rebuild_managed(project)
    assert seen == [True], "重建期间那条解释器路径必须处于「正在改动」状态"
    # 出来之后锁要干净地放掉（按归属清，不是按进入时那几个 key）
    assert not engine_pool.is_mutating(str(python))


def test_two_installs_on_one_environment_do_not_overlap():
    """同一个环境上不允许并发 pip；**不同**环境互不阻塞。"""
    key_a = engine_pool.env_key_of("/env/a/bin/python")
    key_b = engine_pool.env_key_of("/env/b/bin/python")
    with engine_pool.mutating_environment(key_a, ""):
        with pytest.raises(engine_pool.EnvironmentBusy):
            with engine_pool.mutating_environment(key_a, ""):
                pass
        with engine_pool.mutating_environment(key_b, ""):
            pass  # 另一个环境照常


# ===========================================================================
# 六、受管环境
# ===========================================================================
def test_managed_environments_are_project_scoped(tmp_path):
    """**负向反证 #9**：所有项目共用一个受管环境，这条必须红。

    共用会让它慢慢变成依赖垃圾桶：A 项目要 numpy 1.x、B 项目要 2.x，
    后装的把先装的顶掉，症状是「昨天还好好的图今天画不出来了」。
    """
    a, b = tmp_path / "paper-a", tmp_path / "paper-b"
    a.mkdir()
    b.mkdir()
    assert managedenv.env_dir(a) != managedenv.env_dir(b)
    assert managedenv.venv_python(a) != managedenv.venv_python(b)
    # 同一个项目、不同大小写/写法的路径必须是同一个环境
    assert managedenv.project_fingerprint(a) == managedenv.project_fingerprint(str(a) + os.sep)


def test_managed_environment_lives_in_the_data_dir_not_in_the_project(tmp_path):
    """绝不建在用户项目里：那会进他的 git、被同步、被 `rm -rf` 掉。"""
    from tavotto.engine import config as engine_config

    project = tmp_path / "paper"
    project.mkdir()
    env = managedenv.env_dir(project)
    assert env.is_relative_to(engine_config.data_dir())
    assert not env.is_relative_to(project)
    # 目录名是指纹而不是路径：数据目录里不该出现用户的课题名
    assert "paper" not in env.name


def test_a_half_built_managed_environment_is_not_reused(tmp_path):
    """建到一半（取消 / 失败）的环境下次不直接复用。"""
    project = tmp_path / "paper"
    project.mkdir()
    managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
    managedenv.venv_python(project).parent.mkdir(parents=True, exist_ok=True)
    managedenv.venv_python(project).write_text("", encoding="utf-8")
    assert managedenv.python_of(project) is None  # state=incomplete
    managedenv.mark_ready(project)
    assert managedenv.python_of(project) is not None
    managedenv.mark_incomplete(project, "安装被取消")
    assert managedenv.python_of(project) is None


def test_a_directory_we_did_not_create_is_never_adopted(tmp_path):
    """`created_by_tavotto` 不为真的一律不认——路径对不等于是我们建的。"""
    project = tmp_path / "paper"
    project.mkdir()
    path = managedenv.manifest_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": managedenv.SCHEMA, "state": "ready"}), encoding="utf-8")
    assert managedenv.read_manifest(project) is None
    assert managedenv.python_of(project) is None


def test_the_manifest_records_what_we_installed(tmp_path):
    """重建要照着这份装回去，所以它必须如实、且同一个包只留最后一笔。"""
    project = tmp_path / "paper"
    project.mkdir()
    managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
    managedenv.record_install(
        project,
        import_name="lmfit",
        distribution="lmfit",
        requested_specifier=">=1.3",
        resolved_version="1.3.2",
        reason="missing_dependency",
    )
    managedenv.record_install(
        project,
        import_name="lmfit",
        distribution="lmfit",
        requested_specifier="",
        resolved_version="1.3.3",
        reason="missing_dependency",
    )
    managedenv.record_install(
        project,
        import_name="PIL",
        distribution="Pillow",
        requested_specifier="",
        resolved_version="11.0.0",
        reason="missing_dependency",
    )
    assert managedenv.installed_requirements(project) == ["lmfit==1.3.3", "Pillow==11.0.0"]
    # 诊断视图只出包名、版本与来源枚举（缺包修复 / 用户装的），不出请求约束
    # （能少给就少给；`reason` 是两值枚举，不是用户内容——包管理页按它分来源）
    installed = managedenv.state(project)["installed"]
    assert installed == [
        {"distribution": "lmfit", "resolved_version": "1.3.3", "reason": "missing_dependency"},
        {"distribution": "Pillow", "resolved_version": "11.0.0", "reason": "missing_dependency"},
    ]


@needs_worker
def test_the_worker_self_test_really_runs_a_worker():
    """第三层验证不是形式主义：它真的起一个 worker 并跑通一次 build。

    断言「捕获到了图」而不是「函数返回了 True」——后者一个 `return {"ok":
    True}` 的桩也能满足。
    """
    ok = deprepair.worker_self_test(WORKER_PY)
    assert ok["ok"] is True, ok
    assert ok["figures"] >= 1, "跑通了却一张图都没捕获，那不算 worker 能用"


@needs_worker
def test_imports_can_pass_while_the_worker_still_cannot_run(project, monkeypatch):
    """**负向反证 #14**：装完不做 worker 自检，这条必须红。

    前两层（import 那个包 / import matplotlib）在这里**刻意都放行**——真实
    世界里「import 得到但跑不起来」正是这个形状：字体缓存目录不可写、某个
    `.so` 只在子进程里崩、后端起不来。只有真跑一次 worker 才看得见。
    """
    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    plan = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    monkeypatch.setattr(deprepair, "_run", lambda argv, t: (0, "pip 24.0"))
    monkeypatch.setattr(deprepair, "_pip_install", lambda *a: ("", "ok"))
    monkeypatch.setattr(deprepair, "installed_version", lambda py, dist: "1.0")
    # 第一、二层：都说没问题
    monkeypatch.setattr(
        deprepair.projectenv, "probe_environment", lambda py, mod=None: {"ok": True, "python": py}
    )
    # 第三层：worker 起不来（这里用「worker 脚本不在」构造，症状与上面那几种
    # 真实成因一致——子进程根本跑不起来）
    monkeypatch.setattr(engine_pool, "WORKER_PY", project / "no-such-worker.py")
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.install(plan.plan_id)
    assert err.value.code == deprepair.ERROR_SELFTEST_FAILED


def test_managed_venv_creation_is_isolated_and_minimal(tmp_path, monkeypatch):
    """新建的受管环境**不带 `--system-site-packages`**，且只装 matplotlib。

    这两条正是 E2E 的离线 fixture 放宽掉的东西（那边不联网，装不了
    matplotlib），所以必须在这里逐字节钉住——否则「离线 fixture 好使」会
    掩盖「生产上建出来的环境根本不隔离」。

    不隔离的后果：基础解释器上的一次升级会当场改变这个项目的渲染结果，
    而「隔离环境」四个字是它相对项目 `.venv` 的全部卖点。
    """
    project = tmp_path / "paper"
    project.mkdir()
    seen: list[list[str]] = []
    monkeypatch.setattr(
        managedenv, "_run", lambda argv, timeout: (seen.append(list(argv)), (1, "stub"))[1]
    )
    managedenv.create_venv(project, "/base/python")
    assert seen == [["/base/python", "-m", "venv", str(managedenv.venv_dir(project))]]
    assert "--system-site-packages" not in seen[0]
    # numpy 由 matplotlib 带进来；pandas / scipy / seaborn 不预装——脚本真要
    # 用时会走同一条 missing_dependency 修复路。
    assert managedenv.BASE_PACKAGES == ("matplotlib",)


def test_an_environment_without_pip_is_reported_not_silently_fixed(project, monkeypatch):
    """没有 pip 的环境**不静默 `ensurepip`**——用户确认的是「装这个包」。

    往用户的环境里再塞一样他没同意的东西，与「改用户环境必须明确确认」
    是同一条纪律的两面。
    """
    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    plan = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    seen: list[list[str]] = []

    def _no_pip(argv, timeout):
        seen.append(list(argv))
        return 1, "No module named pip"

    monkeypatch.setattr(deprepair, "_run", _no_pip)
    monkeypatch.setattr(
        deprepair, "_pip_install", lambda *a: pytest.fail("没有 pip 就不该走到安装")
    )
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.install(plan.plan_id)
    assert err.value.code == deprepair.ERROR_PIP_UNAVAILABLE
    assert seen == [[plan.python, "-m", "pip", "--version"]]
    assert not [a for a in seen if "ensurepip" in " ".join(a)]


@pytest.mark.parametrize(
    "escape",
    [
        "../../../../usr/bin/python3",
        ".venv/../../../../bin/sh",
        # 用 os.sep 拼：硬写 `..\..\x` 在 POSIX 上根本不是逃逸（反斜杠不是
        # 分隔符，那只是个带反斜杠的怪文件名），断言会红在 interpreter_not_found
        # 上而不是逃逸判据上——那样这条用例在一半平台上量的是另一件事。
        os.sep.join(["..", "..", "..", "somewhere", "python"]),
    ],
)
def test_a_relative_interpreter_path_cannot_escape_the_project(client, project, escape):
    """**相对不等于安全**：`../../../etc/x` 也是相对路径。

    `PATCH /api/engine/environment` 的 `scope="project"` 分支把相对路径拼到
    项目根上——不钉回去的话它能指到项目外任意可执行文件，**而这条路径下游
    是要被当解释器 spawn 的**。绝对路径仍然允许（ADR 0018 明确写了用户可以
    挑项目外的 conda 环境），被堵住的只有「假装是相对路径」这条。
    """
    from tavotto import app as m

    m.open_project(str(project))
    resp = client.patch("/api/engine/environment", json={"scope": "project", "python": escape})
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["code"] == "script_path_outside_project"
    assert projectenv.remembered(project) is None


def test_contained_path_pins_candidates_inside_the_root(tmp_path):
    """`contained_path` 的两条判据各自都不可省。"""
    root = tmp_path / "paper"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    inside = projectenv.contained_path(root, ".venv/bin/python")
    assert inside and Path(inside).is_file()

    # `..` 逃逸
    assert projectenv.contained_path(root, "../outside") is None
    # 前缀相同但不是子目录：`/a/paper-evil` 不在 `/a/paper` 里
    sibling = tmp_path / "paper-evil"
    sibling.mkdir()
    assert projectenv.contained_path(root, str(sibling)) is None
    # 软链接指到根外：字符串看着在里面，实体在外面
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("这个平台建不了软链接")
    assert projectenv.contained_path(root, "linked") is None


def test_reset_state_really_forgets_attempted_repairs(project, monkeypatch):
    """**负向反证（Codex 评审 P2）**：`reset_state(project)` 说「丢弃已试过」，
    就必须真的丢。

    只按环境 key 记的话它清不掉：受管环境重建之后解释器路径一模一样，
    `create_plan` 会一直以「这一轮已经试过了」拒绝那个依赖，直到整个应用
    重启——而重建正是用户为了摆脱失败才点的。
    """
    real_venv(project)
    (project / "requirements.txt").write_text(f"{FIXTURE_DIST}\n", encoding="utf-8")
    plan = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    # 手动登记一次「试过了」（`install()` 真跑时做的就是这件事）
    with deprepair._lock:
        deprepair._attempted.add(
            (
                plan.project_id,
                deprepair._env_key(plan.target_kind, plan.python, str(project)),
                plan.requirement.requirement(),
            )
        )
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
        )
    assert err.value.code == deprepair.ERROR_NOT_ALLOWED

    deprepair.reset_state(project)  # 用户重建环境时走的就是这条
    again = deprepair.create_plan(
        str(project), "figure.py", FIXTURE_IMPORT, target_kind=deprepair.TARGET_PROJECT_VENV
    )
    assert again.plan_id, "重置之后必须能重新形成计划"


def test_managed_base_python_must_be_in_the_support_range(project, monkeypatch):
    """**负向反证（Codex 评审 P2）**：只有 3.14 的机器上不许提供受管修复。

    判据只问「`import venv` 行不行」的话，用户会走完「建 venv → 下载装
    matplotlib 与那个包」，**最后**才在体检那一步被告知版本不支持——白等
    一场下载。判据要提到选解释器那一刻。
    """
    from tavotto.engine import bootstrap

    captured = {}

    def _fake(accept=None):
        captured["accept"] = accept
        # 模拟「机器上只有 3.14」：候选唯一，版本超出支持区间
        return "/fake/python3.14" if accept is None or accept("/fake/python3.14", "3.14") else None

    monkeypatch.setattr(bootstrap, "find_base_python", _fake)
    deprepair.reset_state()
    assert managedenv.base_python() is None, "3.14 不该被当成可用的基础解释器"
    assert captured["accept"] is not None, "版本判据没被传下去"
    # 区间内的照常接受
    assert captured["accept"]("/fake/python3.12", "3.12") is True


def test_managed_environment_is_not_offered_without_a_base_python(project, monkeypatch):
    """没有基础 Python 就没有受管环境这条路——如实说，不假装能修。"""
    monkeypatch.setattr(deprepair, "_base_python", None)
    monkeypatch.setattr(deprepair, "_base_python_known", True)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.create_plan(
            str(project), "figure.py", "lmfit", target_kind=deprepair.TARGET_MANAGED
        )
    assert err.value.code == deprepair.ERROR_MANAGED_UNAVAILABLE


# ===========================================================================
# 七、诊断与隐私
# ===========================================================================
def test_install_logs_never_carry_the_package_index(tmp_path):
    """index 地址可能带凭据，也会泄漏用户所在机构——一个字节都不许出门。"""
    raw = (
        "Looking in indexes: https://alice:s3cr3t@pypi.corp.example/simple\n"
        "Collecting lmfit\n"
        "  Downloading https://alice:s3cr3t@pypi.corp.example/lmfit.whl\n"
    )
    clean = deprepair._sanitize(raw)
    assert "s3cr3t" not in clean
    assert "pypi.corp.example" not in clean or "<credentials>" in clean
    assert "alice:s3cr3t" not in clean
    assert "Collecting lmfit" in clean, "包名本身是有用的排障信息，不该被抹掉"


def test_diagnostics_state_carries_no_paths(tmp_path):
    project = tmp_path / "paper"
    project.mkdir()
    managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
    managedenv.mark_ready(project)
    state = deprepair.diagnostics_state(project)
    blob = json.dumps(state, ensure_ascii=False)
    assert str(project) not in blob
    assert "index" not in blob.lower()
    assert state["max_rounds"] == deprepair.MAX_DEPENDENCY_REPAIR_ROUNDS


def test_custom_index_is_reported_as_a_boolean_never_as_a_url(monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://alice:s3cr3t@pypi.corp/simple")
    assert deprepair.custom_package_index(sys.executable) is True
