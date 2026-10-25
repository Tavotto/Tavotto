"""AI 修复的三个诊断缺口（真实语料覆盖率测量）：项目内同名包遮蔽 · 安装失败分类 · worker 日志来源。

判据的主语：

* 遮蔽：**对着真实 tmp 文件系统**造出项目（带半截 `celerite/` / 无 `__init__` 的 `pysb/`），看 `importshadow.detect` 的事实，
  以及模型**读到的文字**（`read_run_report` / `try_run` / `try_import` / 提示词）。
* 安装：注入的假 `_pip_install` / 假索引探针看到的调用，以及 `install_packages` 回给模型的 code 与文字——不联网、不装包。
* 日志：`read_logs(worker)` 读到的**文件内容**，文件按 `pool._cache_slug` 的真实命名放进假的缓存目录。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tavotto.engine import deprepair, fixsession, importshadow, pool
from tavotto.engine.fixtools import REGISTRY, FixContext, executors, run_tool
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


def test_cannot_import_name_from_a_project_copy_is_recognised(box):
    celerite_project(box)
    init = box.proj / "celerite" / "__init__.py"
    text = f"ImportError: cannot import name 'terms' from 'celerite' ({init})"
    fact = importshadow.detect(text, [box.proj])
    assert fact and fact["relation"] == "missing_name" and fact["missing"] == "celerite.terms"
    elsewhere = f"ImportError: cannot import name 'terms' from 'celerite' ({box.tmp}/site/celerite/__init__.py)"
    assert importshadow.detect(elsewhere, [box.proj]) is None


def test_read_run_report_states_the_shadow_first(box):
    script = celerite_project(box)
    tb = CELERITE_TB.format(root=box.proj)
    report = {
        "phase": "action_required",
        "error": {
            "code": "script_error",
            "message": "No module named 'celerite.celerite_version'",
            "traceback": tb,
        },
    }
    ctx = make_ctx(box, script_path=script, report=lambda: report)
    r = run_tool(REGISTRY["read_run_report"], ctx, {})
    assert not r.is_error
    assert r.content.lstrip("{\n ").startswith('"project_package_shadow"')
    assert "celerite.celerite_version" in r.content and "celerite/" in r.content


def test_read_run_report_without_shadow_has_no_extra_key(box):
    script = celerite_project(box)
    report = {"error": {"message": "No module named 'celerite'"}}
    ctx = make_ctx(box, script_path=script, report=lambda: report)
    assert "project_package_shadow" not in run_tool(REGISTRY["read_run_report"], ctx, {}).content


def test_try_run_and_try_import_failures_carry_the_shadow_note(box):
    script = celerite_project(box)
    stderr = CELERITE_TB.format(root=box.proj)

    def fake_runner(python, work, name, mode, timeout, **kw):
        return {"status": "ok", "returncode": 1, "figures": 0, "stderr": stderr}

    ctx = make_ctx(box, environments={"e": "py"}, script_path=script, runner=fake_runner)
    r = run_tool(REGISTRY["try_run"], ctx, {"env": "e"})
    assert "project_package_shadow:" in r.content and "Do not install it" in r.content

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


def test_system_prompt_has_the_shadow_rule_for_both_wires():
    for text in (
        fixsession.SYSTEM_PROMPT,
        fixsession.cli_prompt(FixContext.for_fix_session("s", "."), "f.py"),
    ):
        rule = text.split("3f. PROJECT FOLDER SHADOWS", 1)[1].split("\n4. ")[0]
        assert "do NOT install a package named `a`" in rule
        assert "never edit the script" in rule and "not_fixed" in rule


# =========================================================================== 2. 安装失败分类
PIP_NONE = (
    "ERROR: Could not find a version that satisfies the requirement celerite (from versions: none)\n"
    "ERROR: No matching distribution found for celerite\n"
)


def _run_default_installer(monkeypatch, pip_out, probe):
    calls = {}

    def fake_pip_install(python, req, cancel, on_log, **kw):
        calls["req"] = req
        return deprepair.classify_pip_failure(pip_out), pip_out

    monkeypatch.setattr(deprepair, "_pip_install", fake_pip_install)
    monkeypatch.setattr(executors, "index_has_project", probe)

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pool, "mutating_environment", lambda key, python: _Lock())
    return executors._default_installer("py", ["celerite"]), calls


def test_wheelless_package_is_requires_build_not_not_found(monkeypatch):
    res, _ = _run_default_installer(monkeypatch, PIP_NONE, lambda py, name, ev: True)
    assert res["code"] == deprepair.ERROR_REQUIRES_BUILD == "dependency_requires_build"
    assert "No matching distribution found for celerite" in res["detail"]


def test_package_absent_from_the_index_stays_not_found(monkeypatch):
    res, _ = _run_default_installer(monkeypatch, PIP_NONE, lambda py, name, ev: False)
    assert res["code"] == "dependency_not_found"


def test_unknown_index_state_keeps_the_original_classification(monkeypatch):
    res, _ = _run_default_installer(monkeypatch, PIP_NONE, lambda py, name, ev: None)
    assert res["code"] == "dependency_not_found"


def test_network_failure_is_not_probed_or_renamed(monkeypatch):
    def boom(*a):
        raise AssertionError("must not probe")

    res, _ = _run_default_installer(monkeypatch, "ERROR: Connection reset by peer\nretrying", boom)
    assert res["code"] == "dependency_network_unavailable"


def test_index_probe_reads_pip_index_versions_output(monkeypatch):
    seen = {}

    def fake_run_pip(argv, cancel, on_log, **kw):
        seen["argv"] = argv
        return "", "celerite (0.4.3)\nAvailable versions: 0.4.3, 0.4.2\n"

    monkeypatch.setattr(deprepair, "_run_pip", fake_run_pip)
    assert executors.index_has_project("py", "celerite", threading.Event()) is True
    assert (
        seen["argv"][1:5] == ["-m", "pip", "index", "versions"]
        and "--only-binary=:all:" not in seen["argv"]
    )
    monkeypatch.setattr(
        deprepair,
        "_run_pip",
        lambda *a, **k: ("x", "ERROR: No matching distribution found for zzz"),
    )
    assert executors.index_has_project("py", "zzz", threading.Event()) is False
    monkeypatch.setattr(
        deprepair, "_run_pip", lambda *a, **k: ("x", "ERROR: unknown command index")
    )
    assert executors.index_has_project("py", "zzz", threading.Event()) is None


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
