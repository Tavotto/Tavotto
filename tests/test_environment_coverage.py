"""Import Origin Resolver PR3：环境覆盖度（脚本要的 import 在候选环境里装齐了没有）。

判据的主语（这一族缺陷的形状是「量错对象」，所以先说清）：

* **谁**被量：被用户明确点名检查的候选解释器（用户的 `.venv` / Conda / pyenv …）；内置 runtime 只读缓存；
* **哪个时刻**：覆盖度只在**明确的环境检查**（`envadvice.check(modules=)`）里被量；默认扫描、`recommend`、依赖门的
  `cache_only` 读的都只是缓存——候选环境子进程数必须为 0；
* **哪个维度**：路径 + **环境代**（同一路径被重建就是另一个环境）+ import 集合；`not_found` 与 `import_error` 分开；
  环境本身跑不了（不支持的 Python / 没 matplotlib / worker 起不来）与「缺包」分开；
* **授权检查不是无副作用的**：它真 import 那些包 = 执行它们的 `__init__`。恶意包写 SIDE_EFFECT 文件：默认扫描之后
  不得出现，授权检查之后必须出现，而且结果必须如实标注「执行过」。

夹具 A–J（BASELINE §PR3）：A 无第三方 / B `.venv` 装齐 / C 缺一个 / D 内置缺而用户环境有 / E 版本冲突 / F 同 base 两个
venv / G 被重建 / H 不支持的 Python / I matplotlib 在但 worker import 失败 / J 用户全局解释器设置。
真 venv（`real_venv`）证明「真 import 量出来的」，假解释器 + 假探测函数证明「谁没执行 / 怎么分类」。
"""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from support import envworld, venvfixture
from support.envworld import real_venv, rebuild_venv
from tavotto.engine import (
    deprepair,
    distmeta,
    envadvice,
    importscan,
    pool as engine_pool,
    projectenv,
    projscan,
    runtime,
    userenvs,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
posix_only = pytest.mark.skipif(not envworld.POSIX, reason="哨兵是 POSIX shell 脚本")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "confirm")
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()
    yield
    engine_pool.shutdown_all(wait=True)
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
GOOD = "VALUE = 1\n"
#: 在、但导入时抛错：它自己的依赖缺（ModuleNotFoundError 的 name 是**另一个**名字）
BROKEN_DEP = "import covmissing_dependency_xyz\n"
#: 在、但导入时抛 ImportError（ABI / DLL 一类）
BROKEN_RAISE = "raise ImportError('boom: binary mismatch')\n"


def _site(python: str) -> Path:
    return venvfixture.site_packages(Path(python).parent.parent)


def _package(python: str, name: str, body: str = GOOD, *, version: str = "") -> Path:
    """往一个真 venv 的 site-packages 里放一个包（带不带 dist-info 由 `version` 决定）。"""
    pkg = _site(python) / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(body, "utf-8")
    if version:
        info = _site(python) / f"{name}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", "utf-8"
        )
        (info / "top_level.txt").write_text(name + "\n", "utf-8")
    return pkg


def _project(tmp_path: Path, script: str = "plot.py") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / script).write_text(
        "import covok_a\nimport covok_b\nimport matplotlib.pyplot as plt\nplt.plot([1])\n",
        "utf-8",
    )
    return root


def _row(out: dict, rel: str) -> dict:
    return next(c for c in out["recommendation"]["candidates"] if c.get("python_relative") == rel)


def _fake_venv(root: Path, name: str) -> Path:
    py = envworld.venv_python(root / name)
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", "utf-8")
    (root / name / "pyvenv.cfg").write_text("home = /x\n", "utf-8")
    return py


def _health(**over) -> dict:
    base = {
        "ok": True,
        "code": "",
        "support": "verified",
        "python_version": "3.12.1",
        "matplotlib_version": "3.9.0",
        "modules_ok": {},
        "modules_detail": {},
        "site_dirs": [],
    }
    base.update(over)
    return base


def _fake_probe(by_rel: dict[str, dict], seen: list | None = None):
    """按解释器路径的末尾（`.venv/bin/python`）给每个候选一份预设结论；记下每次调用的 kwargs。"""

    def probe(python, *a, **k):
        if seen is not None:
            seen.append((python, dict(k)))
        for rel, health in by_rel.items():
            if python.replace("\\", "/").endswith(rel):
                mods = tuple(k.get("modules") or ())
                h = dict(health)
                # 真探测的账：请求了几个名字就 import 了几个（预设里没给 execution 才补）
                h.setdefault(
                    "execution",
                    {
                        "ran_environment_code": True,
                        "imported_modules": list(mods),
                        "may_run_package_init": bool(mods),
                        "incomplete": False,
                    },
                )
                if mods and not h.get("modules_detail"):
                    detail = {m: "found" for m in mods}
                    h["modules_detail"] = detail
                    h["modules_ok"] = {m: True for m in mods}
                return h
        raise AssertionError(f"没有为 {python} 预设结论")

    return probe


@contextmanager
def _armed():
    """默认扫描 / 建议 / 缓存读取期间：起进程 / 连网络的入口一次都不许被调用（调用即失败并记账）。
    退出 with 就还原（授权检查要在同一个测试里真起进程）。"""
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from _armed_in(monkeypatch)


def _armed_in(monkeypatch):
    calls: list[str] = []

    def boom(name):
        def inner(*a, **k):
            calls.append(name)
            raise AssertionError(f"reached a process/network entry: {name}")

        return inner

    monkeypatch.setattr(subprocess.Popen, "__init__", boom("subprocess.Popen"))
    for name in ("system", "execv", "execve", "execvp", "execvpe", "fork", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, boom(f"os.{name}"))
    for name in ("posix_spawn", "posix_spawnp", "spawnv", "spawnve", "spawnvp", "spawnvpe"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, boom(f"os.{name}"))
    monkeypatch.setattr(socket.socket, "connect", boom("socket.connect"))
    yield calls


# ---------------------------------------------------------------------------
# 探测脚本：not_found 与 import_error 分开（真 venv、真 import）
# ---------------------------------------------------------------------------
@needs_worker
def test_the_probe_tells_a_missing_module_from_one_that_is_there_but_fails_to_import(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covbroken_dep", BROKEN_DEP)
    _package(py, "covbroken_raise", BROKEN_RAISE)

    health = projectenv.probe_environment(
        py, modules=("covok_a", "covnever_installed", "covbroken_dep", "covbroken_raise")
    )

    assert health["ok"] is True
    assert health["modules_detail"] == {
        "covok_a": "found",
        "covnever_installed": "not_found",
        # 它自己的依赖缺：ModuleNotFoundError 的 name 是 covmissing_dependency_xyz，不是被要求的名字
        "covbroken_dep": "import_error",
        "covbroken_raise": "import_error",
    }
    # 既有的布尔口径一个字没变：只有 found 是 True
    assert health["modules_ok"] == {
        "covok_a": True,
        "covnever_installed": False,
        "covbroken_dep": False,
        "covbroken_raise": False,
    }


@needs_worker
def test_a_probe_that_imports_declares_it_and_one_without_modules_still_declares_environment_code(
    tmp_path,
):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")

    with_modules = projectenv.probe_environment(py, modules=("covok_a",))
    without = projectenv.probe_environment(py)

    assert with_modules["execution"] == {
        "ran_environment_code": True,
        "imported_modules": ["covok_a"],
        "may_run_package_init": True,
        "incomplete": False,
    }
    # 不带 modules 也不是只读的：解释器按正常方式启动（.pth / sitecustomize）、import matplotlib 与 worker 启动链
    assert without["execution"]["ran_environment_code"] is True
    assert without["execution"]["imported_modules"] == []
    assert without["execution"]["may_run_package_init"] is False
    assert without["modules_detail"] == {}


def test_the_execution_note_of_a_before_run_probe_imports_nothing():
    note = projectenv.execution_note(
        isolated=True, spec_mode=True, module=None, modules=("numpy", "scipy"), info={}
    )
    assert note == {
        "ran_environment_code": False,
        "imported_modules": [],
        "may_run_package_init": False,
        "incomplete": False,
    }


NOT_RUN = {
    "ran_environment_code": False,
    "imported_modules": [],
    "may_run_package_init": False,
    "incomplete": False,
}


def test_a_probe_that_cannot_even_start_reports_that_nothing_ran(tmp_path):
    """#906 r4236524006：解释器不存在 / spawn 失败——什么都没跑，不能报成「跑过、import 过全部」。"""
    missing = tmp_path / "nope" / "python"
    health = projectenv.probe_environment(str(missing), modules=("covok_a",))
    assert health["ok"] is False and health["code"] == projectenv.ERROR_UNUSABLE
    assert health["execution"] == NOT_RUN


def test_a_failure_before_the_child_exists_reports_that_nothing_ran(monkeypatch, tmp_path):
    def no_scratch():
        raise OSError("disk full")

    monkeypatch.setattr(projectenv, "_probe_scratch_dir", no_scratch)
    health = projectenv.probe_environment(sys.executable, modules=("covok_a",))
    assert health["code"] == projectenv.ERROR_UNUSABLE
    assert health["execution"] == NOT_RUN


def _fake_run(**kw):
    import subprocess

    def run(argv, **_kw):
        if "raise" in kw:
            raise kw["raise"]
        return subprocess.CompletedProcess(
            argv, kw.get("rc", 0), kw.get("out", ""), kw.get("err", "")
        )

    return run


@pytest.mark.parametrize(
    "fake",
    [
        _fake_run(**{"raise": subprocess.TimeoutExpired("python", 1)}),
        _fake_run(rc=139, err="Segmentation fault"),
        _fake_run(rc=0, out="not json"),
    ],
    ids=["timeout", "crash", "unparsable_output"],
)
def test_a_child_that_started_but_gave_no_complete_result_is_not_reported_as_not_run(
    monkeypatch, fake
):
    """子进程起来了却没拿到完整结果：保守地当作跑过，但不知道哪些名字走完了——`imported_modules` 空 + `incomplete`。"""
    monkeypatch.setattr(projectenv.subprocess, "run", fake)
    health = projectenv.probe_environment(sys.executable, modules=("covok_a",))
    assert health["ok"] is False and health["code"] == projectenv.ERROR_UNUSABLE
    assert health["execution"] == {
        "ran_environment_code": True,
        "imported_modules": [],
        "may_run_package_init": True,
        "incomplete": True,
    }
    # 下游：覆盖度的 executed_user_code 跟着为真，不把「可能跑过」报成「没跑」
    assert envadvice.coverage_of(health, ("covok_a",))["executed_user_code"] is True


def test_the_imported_modules_are_the_intersection_of_requested_and_reported():
    info = {
        "modules_detail": {"covok_a": "found", "evil_extra": "found"},
        "requested_module": None,
    }
    note = projectenv.execution_note(
        isolated=False,
        spec_mode=False,
        module=None,
        modules=("covok_a", "covok_b"),
        info=info,
    )
    # covok_b 子进程没报 = 不算；evil_extra 是子进程多报的 = 不信
    assert note["imported_modules"] == ["covok_a"]
    assert note["may_run_package_init"] is True and note["incomplete"] is False


def test_the_check_summary_counts_only_candidates_that_really_started(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _fake_venv(root, ".venv")
    dead = _health(ok=False, code="env_unusable", execution=dict(NOT_RUN))
    out = envadvice.check(
        root, "p.py", modules=("covok_a",), probe=_fake_probe({".venv/bin/python": dead})
    )
    assert out["checked"] != []
    assert out["executed"] == {
        "ran_user_code": False,
        "candidate_interpreters": 0,
        "imported_modules": [],
        "may_run_package_init": False,
        "incomplete": False,
        "side_effect_free": False,
    }
    unsure = _health(
        ok=False,
        code="env_unusable",
        execution={
            "ran_environment_code": True,
            "imported_modules": [],
            "may_run_package_init": True,
            "incomplete": True,
        },
    )
    out = envadvice.check(
        root, "p.py", modules=("covok_a",), probe=_fake_probe({".venv/bin/python": unsure})
    )
    assert out["executed"]["ran_user_code"] is True
    assert out["executed"]["may_run_package_init"] is True
    assert out["executed"]["incomplete"] is True
    assert out["executed"]["candidate_interpreters"] == 1


# ---------------------------------------------------------------------------
# coverage_of：纯函数，状态与结构化 detail 的闭集
# ---------------------------------------------------------------------------
class TestCoverageOf:
    MODS = ("covok_a", "covok_b")

    def test_everything_found_is_covered(self):
        h = _health(
            modules_ok={"covok_a": True, "covok_b": True},
            modules_detail={"covok_a": "found", "covok_b": "found"},
            execution={
                "ran_environment_code": True,
                "imported_modules": ["covok_a", "covok_b"],
                "may_run_package_init": True,
                "incomplete": False,
            },
        )
        cov = envadvice.coverage_of(h, self.MODS)
        assert cov["state"] == "covered" and cov["detail"] == []
        assert cov["modules"] == {"covok_a": "found", "covok_b": "found"}
        # 覆盖度只回答「导入得到吗」：已装版本满不满足声明归 distmeta，这里不假装查过
        assert cov["versions_checked"] is False
        assert cov["executed_user_code"] is True

    @pytest.mark.parametrize(
        "role, not_found, import_failed",
        [
            (
                "user",
                "module_not_found_in_user_environment",
                "module_import_failed_in_user_environment",
            ),
            (
                "target",
                "module_missing_in_target_environment",
                "module_import_failed_in_target_environment",
            ),
        ],
    )
    def test_not_found_and_import_error_are_different_details_per_role(
        self, role, not_found, import_failed
    ):
        h = _health(
            modules_ok={"covok_a": True, "covok_b": False, "c": False},
            modules_detail={"covok_a": "found", "covok_b": "not_found", "c": "import_error"},
        )
        cov = envadvice.coverage_of(h, ("covok_a", "covok_b", "c"), role=role)
        assert cov["state"] == "partial" and cov["role"] == role
        assert cov["detail"] == [
            {"code": not_found, "module": "covok_b", "reason": "not_found"},
            {"code": import_failed, "module": "c", "reason": "import_error"},
        ]
        assert {d["code"] for d in cov["detail"]} <= set(envadvice.DETAIL_CODES)

    def test_nothing_found_is_missing_not_partial(self):
        h = _health(
            modules_ok={"covok_a": False, "covok_b": False},
            modules_detail={"covok_a": "not_found", "covok_b": "import_error"},
        )
        assert envadvice.coverage_of(h, self.MODS)["state"] == "missing"

    @pytest.mark.parametrize(
        "code, reason",
        [
            (projectenv.ERROR_UNSUPPORTED_PYTHON, "unsupported_python"),
            (projectenv.ERROR_NO_MATPLOTLIB, "no_matplotlib"),
            (projectenv.ERROR_WORKER_IMPORT, "worker_import_failed"),
            (projectenv.ERROR_UNUSABLE, "unusable"),
        ],
    )
    def test_an_environment_that_cannot_run_tavotto_is_unusable_never_missing_packages(
        self, code, reason
    ):
        # 即使探测脚本顺手量出了「模块都没有」：跑不了 Tavotto 的环境谈不上缺包
        h = _health(
            ok=False,
            code=code,
            modules_ok={"covok_a": False, "covok_b": False},
            modules_detail={"covok_a": "not_found", "covok_b": "not_found"},
        )
        cov = envadvice.coverage_of(h, self.MODS)
        assert cov["state"] == "unusable"
        assert cov["detail"] == [{"code": "environment_unusable", "reason": reason}]
        assert cov["modules"] == {}  # 不拿一个跑不了的环境的 import 结果说话

    def test_no_verdict_and_deferred_are_not_checked_not_covered_and_not_missing(self):
        none = envadvice.coverage_of(None, self.MODS)
        assert none["state"] == "not_checked"
        assert none["detail"] == [{"code": "environment_not_checked", "reason": "no_verdict"}]
        for flag in ("deferred_env", "health_deferred"):
            cov = envadvice.coverage_of(_health(**{flag: True}), self.MODS)
            assert cov["state"] == "not_checked"
            assert cov["detail"] == [{"code": "environment_not_checked", "reason": "deferred"}]

    def test_a_module_the_probe_did_not_report_is_unknown_not_found(self):
        h = _health(modules_ok={"covok_a": True}, modules_detail={"covok_a": "found"})
        cov = envadvice.coverage_of(h, self.MODS)
        assert cov["modules"]["covok_b"] == "unknown"
        assert cov["state"] == "not_checked"

    def test_an_old_shaped_verdict_without_detail_never_invents_a_reason(self):
        h = _health(modules_ok={"covok_a": True, "covok_b": False}, modules_detail={})
        cov = envadvice.coverage_of(h, self.MODS)
        assert cov["modules"] == {"covok_a": "found", "covok_b": "unknown"}
        assert cov["state"] == "not_checked"  # 说不出是没装还是导入失败：不猜
        assert not [d for d in cov["detail"] if d["code"].startswith("module_")]


# ---------------------------------------------------------------------------
# 要量的 import：合形状、有上限、数出被丢掉的
# ---------------------------------------------------------------------------
def test_modules_are_only_well_shaped_top_level_names_and_the_dropped_are_counted():
    mods, dropped = envadvice.normalize_modules(
        ["numpy", "numpy", "a.b", "", "x y", "os; rm -rf", 3, "scipy"]
    )
    assert mods == ("numpy", "scipy") and dropped == 5
    many = [f"m{i}" for i in range(envadvice.MAX_COVERAGE_MODULES + 6)]
    kept, dropped = envadvice.normalize_modules(many)
    assert len(kept) == envadvice.MAX_COVERAGE_MODULES and dropped == 6


def test_the_script_modules_come_from_the_static_plan_the_gate_uses(tmp_path):
    root = _project(tmp_path)
    names = envadvice.script_modules(root, "plot.py")
    assert names is not None
    assert {"covok_a", "covok_b", "matplotlib"} <= set(names)
    # 同一份：与依赖门 / 检测的 `_plan_imports` 对上（不另写第二个判据）
    needed, unknown = deprepair._plan_imports(deprepair.static_plan_payload(root, "plot.py"))
    assert set(names) == {n["import_name"] for n in needed if n["import_name"]} | set(unknown)


# ---------------------------------------------------------------------------
# A 无第三方：不带 modules 时，调用形状与既有行为逐字相同
# ---------------------------------------------------------------------------
@posix_only
def test_A_a_script_without_third_party_imports_probes_exactly_as_before(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "s.py").write_text("import os, json\nprint(json.dumps({}))\n", "utf-8")
    _fake_venv(root, ".venv")
    seen: list = []

    mods = envadvice.script_modules(root, "s.py")
    assert mods == ()  # 纯标准库：没有要量的 import
    out = envadvice.check(
        root, "s.py", modules=mods, probe=_fake_probe({".venv/bin/python": _health()}, seen)
    )

    assert len(seen) == 1 and set(seen[0][1]) == {
        "timeout"
    }  # 没有 modules= 参数：调用形状与以前逐字相同
    assert out["executed"]["imported_modules"] == []
    assert out["executed"]["may_run_package_init"] is False
    assert all("coverage" not in c for c in out["recommendation"]["candidates"])
    assert out["coverage"] == {"modules": [], "dropped": 0}
    assert userenvs._probe_cache == {}  # 没有 import 要记，缓存里什么都不进


# ---------------------------------------------------------------------------
# B / C 真 venv：装齐 / 缺一个 / 装了但坏
# ---------------------------------------------------------------------------
@needs_worker
def test_B_a_venv_with_everything_installed_is_covered_and_says_user_code_ran(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covok_b")
    mods = envadvice.script_modules(root, "plot.py")

    out = envadvice.check(root, "plot.py", modules=mods)

    row = _row(out, envworld.venv_rel(".venv"))
    cov = row["coverage"]
    assert cov["state"] == "covered" and cov["detail"] == []
    assert cov["modules"]["covok_a"] == cov["modules"]["covok_b"] == "found"
    assert cov["role"] == "user" and cov["executed_user_code"] is True
    assert row["status"] == "healthy"
    assert out["executed"]["side_effect_free"] is False
    assert (
        out["executed"]["ran_user_code"] is True and out["executed"]["may_run_package_init"] is True
    )
    assert set(out["executed"]["imported_modules"]) == set(mods)


@needs_worker
def test_C_one_missing_and_one_broken_package_are_told_apart(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "plot.py").write_text(
        "import covok_a\nimport covmissing_here\nimport covbroken_dep\nimport matplotlib\n", "utf-8"
    )
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covbroken_dep", BROKEN_DEP)
    mods = envadvice.script_modules(root, "plot.py")

    out = envadvice.check(root, "plot.py", modules=mods)

    cov = _row(out, envworld.venv_rel(".venv"))["coverage"]
    assert cov["state"] == "partial"
    assert cov["detail"] == [
        {
            "code": "module_not_found_in_user_environment",
            "module": "covmissing_here",
            "reason": "not_found",
        },
        {
            "code": "module_import_failed_in_user_environment",
            "module": "covbroken_dep",
            "reason": "import_error",
        },
    ]
    # 检测读的是同一份：缺的算缺，装了但坏的另列（装它救不了）
    cand = {"python": py, "source": "project_venv", "label": ".venv"}
    needed = [{"import_name": m, "distribution": m} for m in mods]
    entry = userenvs.evaluate([cand], needed, [], cache_only=True)[0]
    assert entry["checked"] is True and entry["satisfies"] is False
    assert entry["import_failed"] == ["covbroken_dep"]
    assert "covmissing_here" in entry["missing"]


# ---------------------------------------------------------------------------
# 缓存：只有 userenvs._probe_cache 一份，键带环境代，检查与检测共用
# ---------------------------------------------------------------------------
@needs_worker
def test_the_check_writes_the_one_cache_the_gate_and_detection_read_and_only_that_one(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covok_b")
    mods = envadvice.script_modules(root, "plot.py")
    before = dict(userenvs._probe_cache)

    envadvice.check(root, "plot.py", modules=mods)

    added = {k: v for k, v in userenvs._probe_cache.items() if k not in before}
    assert len(added) == 1  # 一个候选、一份覆盖度
    (key,) = added
    assert key == userenvs._cache_key(py, tuple(mods), False)
    assert key[1] == projectenv.environment_generation(py)  # 键里带环境代
    # 顺序无关：依赖门拼出来的 import 顺序不同也读得到同一份
    assert userenvs.cached_probe(py, tuple(reversed(mods))) is added[key]
    # 健康体检结论表里只有摘要，没有覆盖度（不是第二个覆盖度缓存）
    verdict = envadvice._verdict(py, projectenv.environment_generation(py))
    assert "modules_ok" not in verdict and "modules_detail" not in verdict
    # 依赖门的 cache_only 读到的就是用户刚点的检查：一个进程都不起
    needed = [{"import_name": m, "distribution": m} for m in mods]
    cand = {"python": py, "source": "project_venv", "label": ".venv"}
    entry = userenvs.evaluate([cand], needed, [], cache_only=True)[0]
    assert entry["checked"] is True and entry["satisfies"] is True


@posix_only
def test_recommend_with_modules_only_reads_the_cache_and_starts_nothing(tmp_path, monkeypatch):
    root = tmp_path / "p"
    root.mkdir()
    (root / "s.py").write_text("import covok_a\n", "utf-8")
    _fake_venv(root, ".venv")
    envadvice.check(
        root, "s.py", modules=("covok_a",), probe=_fake_probe({".venv/bin/python": _health()})
    )

    with _armed() as calls:
        rec = envadvice.recommend(root, "s.py", modules=["covok_a"])
        other = envadvice.recommend(root, "s.py", modules=["covok_other"])  # 另一组 import：没量过
    assert calls == []
    row = next(c for c in rec["candidates"] if c["python_relative"])
    assert row["coverage"]["state"] == "covered"
    row2 = next(c for c in other["candidates"] if c["python_relative"])
    assert row2["coverage"]["state"] == "not_checked"


@posix_only
def test_recommend_without_modules_adds_no_coverage_key(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _fake_venv(root, ".venv")
    rec = envadvice.recommend(root, "p.py")
    assert all("coverage" not in c for c in rec["candidates"])
    assert rec["check"]["executes_user_code"] is True
    assert rec["check"]["side_effect_free"] is False


# ---------------------------------------------------------------------------
# D 内置缺而用户环境有
# ---------------------------------------------------------------------------
@needs_worker
def test_D_the_bundled_runtime_lacks_it_while_the_user_environment_has_it(tmp_path, monkeypatch):
    root = _project(tmp_path)
    user_py = real_venv(root, ".venv", python=WORKER_PY)
    _package(user_py, "covok_a")
    _package(user_py, "covok_b")
    mods = envadvice.script_modules(root, "plot.py")
    # 内置 runtime：这里借宿主解释器当「内置」（它没有 covok_*）；项目明确选回默认链条 = 目标环境是内置
    monkeypatch.setattr(runtime, "bundled_python", lambda: WORKER_PY)
    projectenv.remember_default(root)
    # 依赖门量过内置（`bundled=True` 与 worker 同一套启动条件），结论进同一张缓存
    missing = userenvs.imports_missing(WORKER_PY, list(mods), bundled=True)
    assert sorted(missing) == ["covok_a", "covok_b"]
    # 量的是另一组 import 就不是同一份结论：不会拿它冒充（覆盖度的键含 import 集合）
    assert (
        envadvice.coverage_of(
            userenvs.cached_probe(WORKER_PY, ("covok_a",), bundled=True), ("covok_a",)
        )["state"]
        == "not_checked"
    )

    out = envadvice.check(root, "plot.py", modules=mods)

    cands = {c["id"]: c for c in out["recommendation"]["candidates"]}
    builtin = cands["builtin"]["coverage"]
    assert builtin["role"] == "target" and builtin["state"] == "partial"
    assert {(d["code"], d["module"]) for d in builtin["detail"]} == {
        ("module_missing_in_target_environment", "covok_a"),
        ("module_missing_in_target_environment", "covok_b"),
    }
    user = _row(out, envworld.venv_rel(".venv"))["coverage"]
    assert user["state"] == "covered" and user["role"] == "user"
    # 覆盖度不替用户换环境：项目仍明确选着默认链条
    assert out["recommendation"]["decision"]["consent"] == "builtin"


# ---------------------------------------------------------------------------
# E 版本冲突：覆盖度说「导入得到」，不假装版本也满足；冲突由 distmeta 报告
# ---------------------------------------------------------------------------
@needs_worker
def test_E_a_declared_version_conflict_is_not_hidden_by_coverage(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a", version="1.0")
    _package(py, "covok_b")
    out = envadvice.check(root, "plot.py", modules=("covok_a", "covok_b"))
    cov = _row(out, envworld.venv_rel(".venv"))["coverage"]
    assert cov["state"] == "covered" and cov["versions_checked"] is False

    idx = distmeta.index_environment(Path(py).parent.parent)
    (root / "s.py").write_text("import covok_a\n", "utf-8")
    scanned = importscan.scan(root, "s.py", declared={"covok-a": ">=2"}, dists=idx)
    got = {c.module: c for c in scanned.classes}["covok_a"]
    assert "declared_version_conflict" in got.compatibility
    assert (got.observed_distribution, got.observed_version) == ("covok_a", "1.0")


# ---------------------------------------------------------------------------
# F 同 base 两个 venv：不因 realpath 合并
# ---------------------------------------------------------------------------
@needs_worker
def test_F_two_venvs_on_one_base_keep_their_own_coverage(tmp_path):
    root = _project(tmp_path)
    with_pkgs = real_venv(root, ".venv", python=WORKER_PY)
    without = real_venv(root, "venv", python=WORKER_PY)
    for name in ("covok_a", "covok_b"):
        _package(with_pkgs, name)
    mods = ("covok_a", "covok_b")

    out = envadvice.check(root, "plot.py", modules=mods, scope="project")

    a = _row(out, envworld.venv_rel(".venv"))
    b = _row(out, envworld.venv_rel("venv"))
    assert a["id"] != b["id"]
    assert a["coverage"]["state"] == "covered" and b["coverage"]["state"] == "missing"
    assert userenvs._cache_key(with_pkgs, mods, False) != userenvs._cache_key(without, mods, False)
    assert userenvs.cached_probe(with_pkgs, mods) is not userenvs.cached_probe(without, mods)
    assert userenvs.cached_probe(with_pkgs, mods)["modules_ok"] == {
        "covok_a": True,
        "covok_b": True,
    }
    assert userenvs.cached_probe(without, mods)["modules_ok"] == {
        "covok_a": False,
        "covok_b": False,
    }


# ---------------------------------------------------------------------------
# G 被重建：同一路径换了环境，旧覆盖度对不上
# ---------------------------------------------------------------------------
@needs_worker
def test_G_a_rebuilt_environment_never_serves_the_old_coverage(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covok_b")
    mods = ("covok_a", "covok_b")
    first = envadvice.check(root, "plot.py", modules=mods)
    assert _row(first, envworld.venv_rel(".venv"))["coverage"]["state"] == "covered"
    old_generation = projectenv.environment_generation(py)
    old_key = userenvs._cache_key(py, mods, False)

    rebuilt = rebuild_venv(root, ".venv", python=WORKER_PY)  # 同一路径，没有那两个包
    assert rebuilt == py and projectenv.environment_generation(py) != old_generation
    # 环境代是键的一部分：同一路径换了环境，旧结论的键根本不会再被命中（不只靠站点目录指纹兜底）
    assert userenvs._cache_key(py, mods, False) != old_key

    stale = envadvice.recommend(root, "plot.py", modules=mods)  # 纯读：不能还说 covered
    row = next(c for c in stale["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))
    assert row["coverage"]["state"] == "not_checked"
    assert row["coverage"]["detail"][0]["code"] == "environment_not_checked"
    assert userenvs.cached_probe(py, mods) is None

    fresh = envadvice.check(root, "plot.py", modules=mods)
    assert _row(fresh, envworld.venv_rel(".venv"))["coverage"]["state"] == "missing"


@needs_worker
def test_G_a_venv_rebuilt_while_its_check_runs_does_not_get_the_old_coverage(tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    _package(py, "covok_b")
    mods = ("covok_a", "covok_b")
    real_probe = projectenv.probe_environment

    def probe_then_rebuild(python, **kw):
        health = real_probe(python, **kw)  # 量的是旧环境（两个包都在）
        rebuild_venv(
            root, ".venv", python=WORKER_PY
        )  # 结果回来之前，同一路径被换成没有那两个包的新环境
        return health

    envadvice.check(root, "plot.py", modules=mods, probe=probe_then_rebuild)
    # 旧环境的覆盖度不许记在新代名下：读侧不能说新环境 covered
    assert userenvs.cached_probe(py, mods) is None
    stale = envadvice.recommend(root, "plot.py", modules=mods)
    row = next(c for c in stale["candidates"] if c["python_relative"] == envworld.venv_rel(".venv"))
    assert row["coverage"]["state"] == "not_checked"


# ---------------------------------------------------------------------------
# H / I 环境本身跑不了：与缺包分开
# ---------------------------------------------------------------------------
@posix_only
@pytest.mark.parametrize(
    "code, reason",
    [
        (projectenv.ERROR_UNSUPPORTED_PYTHON, "unsupported_python"),  # H
        (
            projectenv.ERROR_WORKER_IMPORT,
            "worker_import_failed",
        ),  # I matplotlib 在、worker import 失败
        (projectenv.ERROR_NO_MATPLOTLIB, "no_matplotlib"),
    ],
)
def test_H_I_an_environment_that_cannot_run_tavotto_is_not_reported_as_missing_packages(
    tmp_path, code, reason
):
    root = tmp_path / "p"
    root.mkdir()
    _fake_venv(root, ".venv")
    _fake_venv(root, "venv")
    bad = _health(
        ok=False,
        code=code,
        modules_ok={"covok_a": False},
        modules_detail={"covok_a": "not_found"},
    )
    lacking = _health(modules_ok={"covok_a": False}, modules_detail={"covok_a": "not_found"})
    out = envadvice.check(
        root,
        "p.py",
        modules=("covok_a",),
        probe=_fake_probe({".venv/bin/python": bad, "venv/bin/python": lacking}),
    )

    unusable = _row(out, envworld.venv_rel(".venv"))
    missing = _row(out, envworld.venv_rel("venv"))
    assert unusable["coverage"]["state"] == "unusable"
    assert unusable["coverage"]["detail"] == [{"code": "environment_unusable", "reason": reason}]
    assert missing["coverage"]["state"] == "missing"
    assert missing["coverage"]["detail"][0]["code"] == "module_not_found_in_user_environment"
    # 状态（健康体检）与覆盖度各说各的：不能用的环境不被推荐，缺包的健康环境仍是候选
    assert unusable["status"] != "healthy" and missing["status"] == "healthy"
    # 检测侧读同一份：不健康的候选 `missing` 恒为空（不是「缺一堆包」），也不 satisfies
    entry = userenvs.evaluate(
        [
            {
                "python": str(envworld.venv_python(root / ".venv")),
                "source": "project_venv",
                "label": "",
            }
        ],
        [{"import_name": "covok_a", "distribution": "covok-a"}],
        [],
        cache_only=True,
    )[0]
    assert entry["checked"] is True and entry["missing"] == [] and entry["satisfies"] is False


# ---------------------------------------------------------------------------
# J 用户全局解释器设置
# ---------------------------------------------------------------------------
@posix_only
def test_J_a_global_interpreter_setting_still_allows_coverage_but_recommends_nothing(
    tmp_path, monkeypatch
):
    root = tmp_path / "p"
    root.mkdir()
    _fake_venv(root, ".venv")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)

    out = envadvice.check(
        root,
        "p.py",
        modules=("covok_a",),
        probe=_fake_probe({".venv/bin/python": _health()}),
    )

    rec = out["recommendation"]
    assert rec["decision"]["locked_by"] is not None  # 说清楚是谁锁的
    assert rec["recommended_id"] is None  # 全局指定压过项目级决定：不推荐采用
    row = _row(out, envworld.venv_rel(".venv"))
    assert row["coverage"]["state"] == "covered"  # 覆盖度照常（只读事实），不因锁定消失


# ---------------------------------------------------------------------------
# 显式选择不被覆盖度改写
# ---------------------------------------------------------------------------
@posix_only
def test_an_explicit_selection_is_not_overridden_by_a_better_covered_candidate(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    chosen = _fake_venv(root, ".venv")
    _fake_venv(root, "venv")
    assert projectenv.remember(
        root,
        str(chosen),
        automatic=False,
        trigger=projectenv.TRIGGER_RECOMMENDED,
        health=_health(),
    )
    before = projectenv.remembered_record(root)
    lacking = _health(modules_ok={"covok_a": False}, modules_detail={"covok_a": "not_found"})

    out = envadvice.check(
        root,
        "p.py",
        modules=("covok_a",),
        probe=_fake_probe({".venv/bin/python": lacking, "venv/bin/python": _health()}),
    )

    rec = out["recommendation"]
    selected = _row(out, envworld.venv_rel(".venv"))
    other = _row(out, envworld.venv_rel("venv"))
    assert selected["label"] == "selected" and selected["current"] is True
    assert selected["coverage"]["state"] == "missing" and selected["coverage"]["role"] == "target"
    assert selected["coverage"]["detail"][0]["code"] == "module_missing_in_target_environment"
    assert other["coverage"]["state"] == "covered" and other["label"] != "selected"
    assert rec["recommended_id"] == selected["id"]  # 排序只看证据层次，覆盖度不改它
    assert projectenv.remembered_record(root) == before  # 检查不写项目设置


# ---------------------------------------------------------------------------
# 默认扫描零子进程；授权检查有副作用且如实标注
# ---------------------------------------------------------------------------
def _structure_names(path: Path, functions: set[str]) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    used.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    used.add(sub.attr)
    return used


def test_the_read_side_of_envadvice_references_no_process_or_probe_entry():
    """结构性：覆盖度的读法（`recommend` / `coverage_of` / `_coverage_for` / 内置覆盖度 / 要量的 import）只能读缓存。"""
    read_side = {
        "recommend",
        "coverage_of",
        "_coverage_for",
        "_builtin_coverage",
        "normalize_modules",
        "script_modules",
        "_rows",
    }
    names = _structure_names(Path(envadvice.__file__), read_side)
    forbidden = {
        "subprocess",
        "Popen",
        "probe_environment",
        "probe_fn",
        "probe",
        "login_shell_pythons",
        "_probe",  # userenvs._probe 会起进程；读侧只能 cached_probe
        "evaluate",
        "run",
        "system",
    }
    assert names & forbidden == set()
    assert "cached_probe" in names  # 读的确实是那一张缓存


def test_the_probe_entry_is_referenced_only_by_the_explicit_actions():
    tree = ast.parse(Path(envadvice.__file__).read_text("utf-8"))
    holders = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            (isinstance(s, ast.Attribute) and s.attr == "probe_environment")
            or (isinstance(s, ast.Name) and s.id == "probe_fn")
            for s in ast.walk(node)
        )
    }
    assert holders == {"adopt_candidate", "_check"}


@needs_worker
def test_the_default_scan_never_runs_user_code_and_the_authorized_check_does_and_says_so(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    mark = tmp_path / "SIDE_EFFECT"
    # 恶意包：被 import 就写一个文件；还有一个 .pth 的 import 行（解释器启动期的 site 就会跑它）
    _package(py, "covok_a", f"open({str(mark)!r}, 'a').write('init\\n')\n")
    _package(py, "covok_b")
    (_site(py) / "covstartup.pth").write_text(
        f"import os; open({str(tmp_path / 'SIDE_EFFECT_STARTUP')!r}, 'a').write('startup')\n",
        "utf-8",
    )
    mods = ("covok_a", "covok_b", "matplotlib")

    # —— 默认路径：项目扫描 / 建议（含覆盖度）/ 静态来源解析（读 dist-info）/ 依赖门的 cache_only：零子进程、零执行 ——
    with _armed() as calls:
        report = projscan.scan(root)
        envadvice.recommend(root, "plot.py", modules=mods)
        envadvice.script_modules(root, "plot.py")
        idx = distmeta.index_environment(Path(py).parent.parent)
        importscan.scan(root, "plot.py", dists=idx)
        userenvs.evaluate(
            [{"python": py, "source": "project_venv", "label": ""}],
            [{"import_name": m, "distribution": m} for m in mods],
            [],
            cache_only=True,
        )
    assert calls == []
    assert report["environment"]["verified"] is False
    assert not mark.exists() and not (tmp_path / "SIDE_EFFECT_STARTUP").exists()

    # —— 授权检查：真起候选、真 import = 真执行，而且结果如实标注 ——
    out = envadvice.check(root, "plot.py", modules=mods)

    assert mark.exists(), "授权检查确实会执行用户包的 __init__——结果不能装作它没发生"
    assert (tmp_path / "SIDE_EFFECT_STARTUP").exists()  # 解释器启动期的 .pth 也执行了
    ex = out["executed"]
    assert ex["ran_user_code"] is True and ex["candidate_interpreters"] == 1
    assert ex["may_run_package_init"] is True and ex["side_effect_free"] is False
    assert set(ex["imported_modules"]) == set(mods)
    row = _row(out, envworld.venv_rel(".venv"))
    assert row["coverage"]["executed_user_code"] is True
    assert out["recommendation"]["check"]["side_effect_free"] is False
    assert out["recommendation"]["check"]["executes_user_code"] is True


@posix_only
def test_a_check_that_started_no_candidate_claims_no_execution(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _fake_venv(root, ".venv")
    out = envadvice.check(
        root, "p.py", modules=("covok_a",), ids=["not-a-candidate"], probe=_fake_probe({})
    )
    assert out["checked"] == []
    assert out["executed"]["ran_user_code"] is False
    assert out["executed"]["candidate_interpreters"] == 0
    assert out["executed"]["imported_modules"] == []
    assert out["executed"]["side_effect_free"] is False  # 我们从不宣称「无副作用」


# ---------------------------------------------------------------------------
# HTTP：coverage / modules 的校验与往返
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    c = m.app.test_client()
    yield c
    m.reset_projects()


def _open(client, root) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


@needs_worker
def test_http_check_with_coverage_derives_the_modules_from_the_script(client, tmp_path):
    root = _project(tmp_path)
    py = real_venv(root, ".venv", python=WORKER_PY)
    _package(py, "covok_a")
    pj = _open(client, root)

    resp = client.post(
        "/api/engine/environment/check",
        json={"script": "plot.py", "coverage": True},
        query_string={"pj": pj},
    )

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert {"covok_a", "covok_b", "matplotlib"} <= set(body["coverage"]["modules"])
    cov = next(c for c in body["recommendation"]["candidates"] if c["python_relative"])["coverage"]
    assert cov["state"] == "partial"
    assert [d["module"] for d in cov["detail"]] == ["covok_b"]
    assert body["executed"]["side_effect_free"] is False
    assert str(root) not in resp.get_data(as_text=True)  # 不出绝对路径


@pytest.mark.parametrize(
    "body",
    [
        {"modules": "numpy"},
        {"modules": [1, 2]},
        {"modules": [f"m{i}" for i in range(envadvice.MAX_COVERAGE_MODULES + 1)]},
        {"coverage": True},  # 没有 script
    ],
)
def test_http_check_rejects_malformed_coverage_requests_before_starting_anything(
    client, tmp_path, monkeypatch, body
):
    root = tmp_path / "p"
    root.mkdir()
    pj = _open(client, root)
    with _armed() as calls:
        resp = client.post("/api/engine/environment/check", json=body, query_string={"pj": pj})
    assert resp.status_code == 400 and resp.get_json()["code"] == "bad_request"
    assert calls == []


# ---------------------------------------------------------------------------
# 同源对：后端闭集 ↔ web/src/lib/api.ts 镜像（顺序也比）
# ---------------------------------------------------------------------------
def test_the_coverage_closed_sets_are_the_same_on_both_sides():
    from support.tsconst import exported_string_array

    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    assert exported_string_array(src, "ENV_MODULE_STATES") == list(envadvice.MODULE_STATES)
    assert exported_string_array(src, "ENV_COVERAGE_STATES") == list(envadvice.COVERAGE_STATES)
    assert exported_string_array(src, "ENV_COVERAGE_DETAIL_CODES") == list(envadvice.DETAIL_CODES)
    # 结构化 detail 不是发布的错误码：不进 ERROR_CODES，也不撞已有的稳定码
    assert not set(envadvice.DETAIL_CODES) & set(envadvice.ERROR_CODES)
