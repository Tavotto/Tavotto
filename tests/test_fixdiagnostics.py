"""AI 修复的三个诊断缺口（真实语料覆盖率测量）：项目内同名包遮蔽 · 安装失败分类 · worker 日志来源。

判据的主语：

* 遮蔽：**对着真实 tmp 文件系统**造出项目（带半截 `celerite/` / 无 `__init__` 的 `pysb/`），看 `importshadow.detect` 的事实，
  以及模型**读到的文字**（`read_run_report` / `try_run` / `try_import` / 提示词）。
* 安装：注入的假 `_pip_install` / 假索引探针看到的调用，以及 `install_packages` 回给模型的 code 与文字——不联网、不装包。
* 日志：`read_logs(worker)` 读到的**文件内容**，文件按 `pool._cache_slug` 的真实命名放进假的缓存目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import deprepair, fixsession, importshadow, pool
from tavotto.engine.fixtools import REGISTRY, FixContext, base, executors, run_tool
from tests.test_fixtools import put

CELERITE_TB = (
    "Traceback (most recent call last):\n"
    '  File "{root}/paper/figures/sho.py", line 8, in <module>\n'
    "    from celerite.plot_setup import setup, get_figsize\n"
    '  File "{root}/celerite/__init__.py", line 3, in <module>\n'
    "    from .celerite_version import __version__\n"
    "ModuleNotFoundError: No module named 'celerite.celerite_version'"
)


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    return type("Box", (), {"home": home, "proj": proj, "tmp": tmp_path, "data": data})


def make_ctx(box, **kw) -> FixContext:
    kw.setdefault("case_insensitive", False)
    return FixContext.for_fix_session("s1", box.proj, home=box.home, **kw)


def celerite_project(box) -> Path:
    put(
        box.proj / "celerite" / "__init__.py",
        "# -*- coding: utf-8 -*-\nfrom .celerite_version import __version__\n",
    )
    put(box.proj / "celerite" / "plot_setup.py", "def setup(): pass\n")
    put(box.proj / "setup.py", "from setuptools import setup\n")
    return put(box.proj / "paper" / "figures" / "sho.py", "from celerite.plot_setup import setup\n")


# =========================================================================== 1. 遮蔽
def test_regular_project_package_missing_a_generated_submodule_is_named_exactly(box):
    celerite_project(box)
    fact = importshadow.detect(CELERITE_TB.format(root=box.proj), [box.proj])
    assert fact is not None
    assert (fact["kind"], fact["package"], fact["missing"]) == (
        "regular",
        "celerite",
        "celerite.celerite_version",
    )
    assert fact["project_path"] == "celerite/" and fact["init_imports_it"] is True
    assert fact["project_has_build_files"] is True
    note = fact["note"]
    assert "not `celerite`" in note and "celerite/" in note and "Do not install it" in note


def test_namespace_folder_shape_says_installing_the_real_package_can_fix_it(box):
    # pysb 语料：项目里有 pysb/examples/…，但 pysb/ 没有 __init__.py，pysb.macros 不在项目里
    put(box.proj / "pysb" / "examples" / "run.py", "from pysb.macros import *\n")
    text = "ModuleNotFoundError: No module named 'pysb.macros'"
    fact = importshadow.detect(text, [box.proj, box.proj / "pysb" / "examples"])
    assert fact is not None and fact["kind"] == "namespace" and fact["package"] == "pysb"
    assert "installing the distribution that provides `pysb` can fix this" in fact["note"]
    assert "Do not install it" not in fact["note"]


@pytest.mark.parametrize(
    "text",
    [
        "ModuleNotFoundError: No module named 'celerite'",  # 顶层缺：没有父包，不是遮蔽
        "ModuleNotFoundError: No module named 'numpy.core._x'",  # 项目里没有 numpy/
        "ValueError: boom",
        "",
    ],
)
def test_other_failures_are_not_called_shadowing(box, text):
    celerite_project(box)
    assert importshadow.detect(text, [box.proj]) is None


def test_submodule_that_exists_in_the_project_is_not_the_missing_piece(box):
    celerite_project(box)
    put(box.proj / "celerite" / "celerite_version.py", "__version__ = '1'\n")
    tb = "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    assert importshadow.detect(tb, [box.proj]) is None


@pytest.mark.parametrize(
    "init_text",
    [
        "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
        "__import__('pkg_resources').declare_namespace(__name__)\n",
    ],
)
def test_init_that_extends_the_package_path_is_treated_like_a_namespace(box, init_text):
    put(box.proj / "celerite" / "__init__.py", init_text)
    fact = importshadow.detect("ModuleNotFoundError: No module named 'celerite.extra'", [box.proj])
    assert fact and fact["kind"] == "namespace"
    assert "Do not install it" not in fact["note"] and "can fix this" in fact["note"]


@pytest.mark.parametrize(
    "init_text",
    [
        "# We deliberately do not call extend_path(__path__, __name__).\n",
        '"""Unlike pkg_resources.declare_namespace(__name__), this package stays regular."""\n',
        "NOTE = 'extend_path(__path__, __name__)'\n",
    ],
)
def test_mentions_of_path_extension_in_comments_or_strings_keep_the_package_regular(box, init_text):
    put(box.proj / "celerite" / "__init__.py", init_text)
    fact = importshadow.detect("ModuleNotFoundError: No module named 'celerite.extra'", [box.proj])
    assert fact and fact["kind"] == "regular"
    assert "Do not install it" in fact["note"]


def test_script_dir_package_wins_over_project_root_package(box):
    # project/foo（regular）与 project/scripts/foo（extend_path）同时在：脚本目录在 sys.path 前面，按它那份判
    put(box.proj / "foo" / "__init__.py", "x = 1\n")
    put(
        box.proj / "scripts" / "foo" / "__init__.py",
        "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
    )
    script = put(box.proj / "scripts" / "fig.py", "import foo.bar\n")
    ctx = make_ctx(box, script_path=script)
    note = base.shadow_note(ctx, "ModuleNotFoundError: No module named 'foo.bar'")
    assert "project_package_shadow:" in note
    assert "Do not install it" not in note and "can fix this" in note


_INIT_EXTEND = "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n"
_MISSING = "ModuleNotFoundError: No module named 'foo.bar'"


@pytest.mark.parametrize(
    ("layout", "expect"),
    [
        # PathFinder：第一个有 __init__.py 的位置胜出，哪怕更前面有无 __init__ 的目录（Codex 形状）
        ({"scripts/foo": None, "foo": "x = 1\n"}, ("regular", "foo/")),
        ({"scripts/foo": "x = 1\n", "foo": None}, ("regular", "foo/")),
        ({"scripts/foo": "x = 1\n", "foo": "y = 1\n"}, ("regular", "foo/")),
        # 胜出的 regular 包用 extend_path：按 namespace 说（装上能修）
        ({"scripts/foo": _INIT_EXTEND, "foo": "x = 1\n"}, ("namespace", None)),
        ({"foo": "__import__('pkg_resources').declare_namespace(__name__)\n"}, ("namespace", None)),
        # 都没有 __init__：namespace，装真包能修
        ({"scripts/foo": None, "foo": None}, ("namespace", None)),
        ({"foo": None}, ("namespace", None)),
        # 项目里没有这个包：不下结论
        ({"other": "x = 1\n"}, None),
    ],
)
def test_package_selection_follows_pathfinder_and_pep420(box, layout, expect):
    for rel, init in layout.items():
        if init is None:
            (box.proj / rel).mkdir(parents=True, exist_ok=True)
        else:
            put(box.proj / rel / "__init__.py", init)
    script = put(box.proj / "scripts" / "fig.py", "import foo.bar\n")
    ctx = make_ctx(box, script_path=script)
    fact = importshadow.detect(_MISSING, [script.parent, box.proj])
    if expect is None:
        assert fact is None
        assert base.shadow_note(ctx, _MISSING) == ""
        return
    kind, path = expect
    assert fact is not None and fact["kind"] == kind
    note = base.shadow_note(ctx, _MISSING)
    assert ("Do not install it" in note) == (kind == "regular")
    if path:
        assert fact["project_path"] == path


@pytest.mark.parametrize(
    ("layout", "kind"),
    [
        # 顶层 foo 是项目里的 regular 包：foo.__path__ 只有它自己，没有 __init__ 的 foo/sub/ 也是项目自有 → 别装
        ({"foo": "x = 1\n", "foo/sub": None}, "regular"),
        ({"foo": "x = 1\n", "foo/sub": "y = 1\n"}, "regular"),
        # 顶层是 namespace / extend_path：逐级往下，装上能修
        ({"foo": None, "foo/sub": None}, "namespace"),
        ({"foo": _INIT_EXTEND, "foo/sub": None}, "namespace"),
        # 项目里的 foo 根本没有 sub：不是项目遮蔽
        ({"foo": "x = 1\n"}, None),
    ],
)
def test_resolution_starts_from_the_top_level_package(box, layout, kind):
    for rel, init in layout.items():
        if init is None:
            (box.proj / rel).mkdir(parents=True, exist_ok=True)
        else:
            put(box.proj / rel / "__init__.py", init)
    fact = importshadow.detect("ModuleNotFoundError: No module named 'foo.sub.extra'", [box.proj])
    assert (fact["kind"] if fact else None) == kind


def test_cannot_import_name_from_a_project_copy_is_recognised(box):
    celerite_project(box)
    init = box.proj / "celerite" / "__init__.py"
    text = f"ImportError: cannot import name 'terms' from 'celerite' ({init})"
    fact = importshadow.detect(text, [box.proj])
    assert fact and fact["relation"] == "missing_name" and fact["missing"] == "celerite.terms"
    elsewhere = f"ImportError: cannot import name 'terms' from 'celerite' ({box.tmp}/site/celerite/__init__.py)"
    assert importshadow.detect(elsewhere, [box.proj]) is None


def test_try_run_and_try_import_failures_carry_the_shadow_note(box):
    script = celerite_project(box)
    stderr = CELERITE_TB.format(root=box.proj)

    def fake_runner(python, work, name, mode, timeout, **kw):
        return {"status": "ok", "returncode": 1, "figures": 0, "stderr": stderr}

    root_script = put(
        box.proj / "sho_root.py", "import celerite\n"
    )  # 脚本与 celerite/ 同在项目根：副本里有它
    ctx = make_ctx(box, environments={"e": "py"}, script_path=root_script, runner=fake_runner)
    r = run_tool(REGISTRY["try_run"], ctx, {"env": "e"})
    assert "project_package_shadow:" in r.content and "Do not install it" in r.content
    # Codex 形状：脚本在子目录，试运行只复制那个目录，根下的包不在副本的导入路径上 → 不下遮蔽结论
    ctx_sub = make_ctx(box, environments={"e": "py"}, script_path=script, runner=fake_runner)
    assert (
        "project_package_shadow" not in run_tool(REGISTRY["try_run"], ctx_sub, {"env": "e"}).content
    )

    from tavotto.engine.fixtools import trial

    ctx2 = make_ctx(box, environments={"e": "py"}, script_path=script)
    orig = trial.run_python
    trial.run_python = lambda *a, **k: {
        "status": "ok",
        "returncode": 1,
        "stdout": "",
        "stderr": stderr,
    }
    try:
        r2 = run_tool(REGISTRY["try_import"], ctx2, {"env": "e", "module": "celerite.plot_setup"})
    finally:
        trial.run_python = orig
    assert "project_package_shadow:" in r2.content


def test_try_import_without_project_on_path_needs_provenance_evidence(box):
    celerite_project(box)
    script = box.proj / "paper" / "figures" / "sho.py"
    site_tb = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        '  File "/venv/lib/python3.13/site-packages/celerite/__init__.py", line 3, in <module>\n'
        "    from .celerite_version import __version__\n"
        "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    )
    from tavotto.engine.fixtools import trial

    def imp(stderr):
        ctx = make_ctx(box, environments={"e": "py"}, script_path=script)
        orig = trial.run_python
        trial.run_python = lambda *a, **k: {
            "status": "ok",
            "returncode": 1,
            "stdout": "",
            "stderr": stderr,
        }
        try:
            return run_tool(REGISTRY["try_import"], ctx, {"env": "e", "module": "celerite"}).content
        finally:
            trial.run_python = orig

    assert "project_package_shadow" not in imp(
        site_tb
    )  # 失败来自 site-packages，项目里同名目录不算数
    assert "project_package_shadow" in imp(
        CELERITE_TB.format(root=box.proj)
    )  # 帧落在项目的 celerite/ 里


def test_system_prompt_has_the_shadow_rule_for_both_wires():
    for text in (
        fixsession.SYSTEM_PROMPT,
        fixsession.cli_prompt(FixContext.for_fix_session("s", "."), "f.py"),
    ):
        rule = text.split("3f. PROJECT FOLDER SHADOWS", 1)[1].split("\n4. ")[0]
        assert "do NOT install a package named `a`" in rule
        assert "never edit the script" in rule and "not_fixed" in rule


@pytest.mark.parametrize("current_shadow", [True, False])
def test_application_and_observation_share_the_current_package_diagnosis(box, current_shadow):
    script = celerite_project(box)
    shadow_error = {
        "code": "script_error",
        "message": "No module named 'celerite.celerite_version'",
        "traceback": CELERITE_TB.format(root=box.proj),
    }
    error = (
        shadow_error
        if current_shadow
        else {"code": "script_error", "message": "ValueError: current data"}
    )
    report = {
        "phase": "completed",
        "outcome": {"kind": "failed"},
        "result": {"error": error},
        "plan": {"launch_context": {"cwd": "paper/figures"}},
        "attempt_history": [shadow_error] * 12,
    }
    ctx = make_ctx(
        box,
        environments={"e": "py"},
        script_path=script,
        report=lambda: report,
        application_runner=lambda *_args: {"report": report},
    )
    observation = run_tool(REGISTRY["read_run_report"], ctx, {})
    verification = run_tool(REGISTRY["try_run"], ctx, {"env": "e"})
    assert not observation.is_error and not verification.is_error
    assert observation.content in verification.content
    for result in (observation, verification):
        assert ('"project_package_shadow"' in result.content) is current_shadow
        assert error["message"] in result.content
        assert '"launch_context"' in result.content and "paper/figures" in result.content


# =========================================================================== 2. 安装失败分类
PIP_NONE = (
    "ERROR: Could not find a version that satisfies the requirement celerite (from versions: none)\n"
    "ERROR: No matching distribution found for celerite\n"
)


def _run_default_installer(monkeypatch, pip_out, req="celerite"):
    """缺省安装器 + 假 `_pip_install`；任何额外的 pip 子进程（`_run_pip`）/ `subprocess` 调用都记进 `extra`。"""
    import subprocess

    extra = []

    def fake_pip_install(python, r, cancel, on_log, **kw):
        return deprepair.classify_pip_failure(pip_out), pip_out

    monkeypatch.setattr(deprepair, "_pip_install", fake_pip_install)
    monkeypatch.setattr(
        deprepair, "_run_pip", lambda *a, **k: extra.append(("run_pip", a)) or ("", "")
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: extra.append(("Popen", a)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: extra.append(("run", a)))

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pool, "mutating_environment", lambda key, python: _Lock())
    return executors._default_installer("py", [req]), extra


@pytest.mark.parametrize(
    ("pip_out", "code"),
    [
        (PIP_NONE, "dependency_not_found"),  # 只有源码包 / 异平台 wheel / 没这个包：输出一样，不猜
        (
            "ERROR: Could not find a version that satisfies the requirement celerite (from versions: 0.1.0)\n"
            "ERROR: No matching distribution found for celerite\n",
            "dependency_requires_build",  # 安装器自己列出了版本却装不上
        ),
        ("ERROR: Connection reset by peer\nretrying", "dependency_network_unavailable"),
    ],
)
@pytest.mark.parametrize("req", ["celerite", "celerite==2.0", "celerite>=1,<2"])
def test_installer_classifies_from_its_own_output_and_starts_no_second_process(
    monkeypatch, pip_out, code, req
):
    # 配置了 no-index / find-links / 私有源的用户：任何额外的 pip / 网络调用都会绕开他的配置，所以一个都不许有
    res, extra = _run_default_installer(monkeypatch, pip_out, req)
    assert res["code"] == code
    assert extra == []
    assert "ERROR:" in res["detail"]


def test_other_platform_wheels_only_stays_not_found_and_the_hint_asserts_no_nonexistence(box):
    from tests.test_fixtools_change import change_ctx

    out = (
        "ERROR: Could not find a version that satisfies the requirement exotic (from versions: none)\n"
        "ERROR: No matching distribution found for exotic\n"
    )
    ctx = change_ctx(box, ask="yes")
    ctx.installer = lambda python, reqs: {
        "ok": False,
        "code": "dependency_not_found",
        "done": [],
        "detail": out.strip(),
    }
    r = run_tool(REGISTRY["install_packages"], ctx, {"env": "e", "packages": ["seaborn"]})
    assert r.code == "dependency_not_found"
    assert "only be published for other Python versions or platforms" in r.content
    low = r.content.lower()
    assert "does not exist" not in low and "no package with that name" not in low


def test_install_packages_tells_the_model_the_reason_and_the_installer_line(box):
    from tests.test_fixtools_change import change_ctx

    ctx = change_ctx(box, ask="yes")
    secret_path = str(box.home / "x")
    ctx.installer = lambda python, reqs: {
        "ok": False,
        "code": "dependency_requires_build",
        "done": [],
        "detail": f"ERROR: No matching distribution found for seaborn ({secret_path})",
    }
    r = run_tool(REGISTRY["install_packages"], ctx, {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == "dependency_requires_build"
    assert "no ready-made wheel" in r.content and "Do not try another name" in r.content
    assert "Installer said: ERROR: No matching distribution found for seaborn" in r.content


# =========================================================================== 3. worker 日志
def test_read_logs_worker_reads_the_current_projects_newest_worker_log(box, monkeypatch):
    cache = box.tmp / "enginecache"
    monkeypatch.setattr(pool, "ENGINE_CACHE", cache)
    mine = cache / pool._cache_slug(pool._norm_dir(str(box.proj)), "paper/figures/sho.py")
    other = cache / pool._cache_slug(pool._norm_dir(str(box.tmp / "other")), "x.py")
    put(mine / "worker.log", "boot\nTraceback...\nModuleNotFoundError: celerite\n")
    put(other / "worker.log", "SOMEONE ELSES LOG\n")
    ctx = make_ctx(box)
    r = run_tool(REGISTRY["read_logs"], ctx, {"source": "worker", "lines": 5})
    assert not r.is_error and "ModuleNotFoundError: celerite" in r.content
    assert "SOMEONE ELSES" not in r.content


def test_read_logs_worker_without_any_log_is_a_clear_no_log(box, monkeypatch):
    monkeypatch.setattr(pool, "ENGINE_CACHE", box.tmp / "empty-cache")
    r = run_tool(REGISTRY["read_logs"], make_ctx(box), {"source": "worker"})
    assert r.is_error and r.code == "no_log" and "read_run_report" in r.content


def test_an_import_failure_the_script_caught_is_not_the_diagnosis(box):
    # 脚本接住可选子模块的缺失、再抛别的错：最终异常不是 import 类，遮蔽说明不能出现
    celerite_project(box)
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{box.proj / "fig.py"}", line 3, in <module>\n'
        "    import celerite.celerite_version\n"
        "ModuleNotFoundError: No module named 'celerite.celerite_version'\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "\n"
        "Traceback (most recent call last):\n"
        f'  File "{box.proj / "fig.py"}", line 5, in <module>\n'
        '    raise ValueError("input data has invalid dimensions")\n'
        "ValueError: input data has invalid dimensions\n"
    )
    assert importshadow.detect(tb, [box.proj]) is None


def test_a_chained_import_failure_that_is_terminal_is_still_diagnosed(box):
    celerite_project(box)
    tb = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "KeyError: 'a'\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "\n"
        "ModuleNotFoundError: No module named 'celerite.celerite_version'\n"
    )
    fact = importshadow.detect(tb, [box.proj])
    assert fact and fact["missing"] == "celerite.celerite_version"
