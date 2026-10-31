"""脚本插进 `sys.path` 的私有目录里的编译扩展是为别的 Python 小版本编的（ADR 0154，TVD-ZCR4）。

现场：脚本把项目里的 `.kegg_python`（`pip install --target` 出来的 cp312 依赖）插到 `sys.path` 最前，Tavotto 选中的是
3.14 的环境，环境里的 matplotlib 本来完好；导入被那个目录盖住，以 ImportError 收场。换 Python 版本救不了。
本文件钉：

* 识别（`bridgeboot.foreign_abi_facts`，以文件系统事实为主）：该报的报，`abi3` / 无标签 / 当前 ABI / 解释器包目录里的 /
  没有扩展的 / 项目根或脚本目录 / 非 ImportError 一律不误判；
* 过滤（`bridgeboot.IgnoredImportPaths`）：只对名单里的目录生效，用户同名模块照旧赢，`sys.path` 不动；
* 名单存储与令牌（`pathignore`）、spec / argv 的同源（`execspec`）、失败码转换（`pool.foreign_abi_error`）；
* 端到端：真 safe worker 与真 native bridge——先得到新失败码，记住忽略后重跑成功。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from support import bridgekit
from tavotto.engine import bridgeboot, execspec, pathignore, pool as engine_pool, workdir

HERE = tuple(sys.version_info[:2])
OTHER = (HERE[0], HERE[1] + 1 if HERE[1] < 90 else HERE[1] - 1)


def _foreign(name: str = "_native", minor: int | None = None) -> str:
    """一个为别的小版本编的扩展文件名（POSIX 与 Windows 两种后缀都用得上，判据只看名字）。"""
    tag = f"{OTHER[0]}{OTHER[1] if minor is None else minor}"
    return f"{name}.cpython-{tag}-x86_64-linux-gnu.so"


def _name() -> str:
    return "abimod_" + uuid.uuid4().hex[:10]


def _vendor(root: Path, pkg: str, files: list[str]) -> Path:
    d = root / "vendor"
    (d / pkg).mkdir(parents=True)
    (d / pkg / "__init__.py").write_text("from . import _native\n")
    for f in files:
        (d / pkg / f).write_bytes(b"")
    return d


def _import_error(path: Path) -> ImportError:
    return ImportError("cannot import name '_native'", path=str(path))


@pytest.fixture
def clean_sys_path():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    importlib.invalidate_caches()


# --------------------------------------------------------------------------- 识别
def test_foreign_extension_in_a_sys_path_folder_is_named(tmp_path, clean_sys_path):
    pkg = _name()
    d = _vendor(tmp_path / "proj", pkg, [_foreign()])
    sys.path.insert(0, str(d))
    fact, private = bridgeboot.foreign_abi_facts(
        _import_error(d / pkg / "__init__.py"), [], [], [str(tmp_path / "proj")]
    )
    assert fact == {
        "folder": "vendor",
        "abi": f"{OTHER[0]}.{OTHER[1]}",
        "current": f"{HERE[0]}.{HERE[1]}",
        "module": pkg,
    }
    assert private == {"path": str(d)}


def test_evidence_can_be_a_traceback_frame_instead_of_the_import_error_path(
    tmp_path, clean_sys_path
):
    """`ModuleNotFoundError` 常常不带 path：证据取 traceback 里的文件（内层优先），不解析报错文字。"""
    pkg = _name()
    d = _vendor(tmp_path / "proj", pkg, [_foreign()])
    sys.path.insert(0, str(d))
    code = compile(
        "raise ModuleNotFoundError('No module named x')", str(d / pkg / "__init__.py"), "exec"
    )
    try:
        exec(code, {})  # noqa: S102
    except ModuleNotFoundError as exc:
        fact, _ = bridgeboot.foreign_abi_facts(exc, [], [], [str(tmp_path / "proj")])
    assert fact["folder"] == "vendor" and fact["abi"] == f"{OTHER[0]}.{OTHER[1]}"


def test_folder_outside_the_project_is_shown_by_name_only(tmp_path, clean_sys_path):
    pkg = _name()
    d = _vendor(tmp_path / "elsewhere", pkg, [_foreign()])
    sys.path.insert(0, str(d))
    fact, private = bridgeboot.foreign_abi_facts(
        _import_error(d / pkg / "__init__.py"), [], [], [str(tmp_path / "proj")]
    )
    assert fact["folder"] == "vendor" and str(tmp_path) not in json.dumps(fact)
    assert private["path"] == str(d)


@pytest.mark.parametrize(
    "files",
    [
        ["_native.abi3.so"],  # abi3 兼容
        ["_native.so"],  # 无标签
        ["_native.pyd"],
        [f"_native.cpython-{HERE[0]}{HERE[1]}-x86_64-linux-gnu.so"],  # 当前 ABI
        [f"_native.cp{HERE[0]}{HERE[1]}-win_amd64.pyd"],
        ["data.txt", "helper.py"],  # 没有扩展
        [],
    ],
)
def test_compatible_or_absent_extensions_are_not_a_foreign_abi(tmp_path, clean_sys_path, files):
    pkg = _name()
    d = _vendor(tmp_path / "proj", pkg, files)
    sys.path.insert(0, str(d))
    assert bridgeboot.foreign_abi_facts(
        _import_error(d / pkg / "__init__.py"), [], [], [str(tmp_path / "proj")]
    ) == ({}, {})


def test_folder_inside_the_interpreters_own_packages_is_not_blamed(tmp_path, clean_sys_path):
    pkg = _name()
    site = tmp_path / "env" / "lib" / "site-packages"
    (site / pkg).mkdir(parents=True)
    (site / pkg / "__init__.py").write_text("")
    (site / pkg / _foreign()).write_bytes(b"")
    sys.path.insert(0, str(site))
    assert bridgeboot.foreign_abi_facts(
        _import_error(site / pkg / "__init__.py"), [str(tmp_path / "env")], [], []
    ) == ({}, {})


def test_script_dir_and_project_root_are_never_blamed(tmp_path, clean_sys_path):
    """用户自己的模块所在的地方（脚本目录、项目根）忽略不得——即使里面躺着别的版本的扩展。"""
    proj = tmp_path / "proj"
    pkg = _name()
    (proj / pkg).mkdir(parents=True)
    (proj / pkg / "__init__.py").write_text("")
    (proj / pkg / _foreign()).write_bytes(b"")
    sys.path.insert(0, str(proj))
    err = _import_error(proj / pkg / "__init__.py")
    assert bridgeboot.foreign_abi_facts(err, [], [str(proj)], [str(proj)]) == ({}, {})
    # 脚本在子目录：项目根包着脚本目录，同样不算
    (proj / "scripts").mkdir()
    assert bridgeboot.foreign_abi_facts(err, [], [str(proj / "scripts")], []) == ({}, {})


def test_non_import_errors_and_missing_files_are_not_blamed(tmp_path, clean_sys_path):
    pkg = _name()
    d = _vendor(tmp_path / "proj", pkg, [_foreign()])
    sys.path.insert(0, str(d))
    assert bridgeboot.foreign_abi_facts(ValueError("x"), [], [], [str(tmp_path / "proj")]) == (
        {},
        {},
    )
    gone = ImportError("x", path=str(tmp_path / "nowhere" / "__init__.py"))
    assert bridgeboot.foreign_abi_facts(gone, [], [], []) == ({}, {})
    # 文件在 sys.path 之外
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.py").write_text("")
    assert bridgeboot.foreign_abi_facts(_import_error(outside / "a.py"), [], [], []) == ({}, {})


# --------------------------------------------------------------------------- 过滤
@pytest.fixture
def two_pkgs(tmp_path, clean_sys_path):
    """同名包两份：`bad/`（被忽略的）排在 `good/` 前面。"""
    name = _name()
    bad, good = tmp_path / "bad", tmp_path / "good"
    for root, value in ((bad, 0), (good, 1)):
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text(f"Y = {value}\n")
        (root / name / "sub.py").write_text(f"Z = {value}\n")
    sys.path[:0] = [str(bad), str(good)]
    importlib.invalidate_caches()
    return name, bad, good


@pytest.fixture
def finder():
    made = []

    def _make(paths, on_ignore=None):
        f = bridgeboot.IgnoredImportPaths(paths, on_ignore)
        assert f.install()
        made.append(f)
        return f

    yield _make
    for f in made:
        if f in sys.meta_path:
            sys.meta_path.remove(f)


def _fresh(name: str):
    for k in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[k]
    importlib.invalidate_caches()
    try:
        return importlib.import_module(name)
    finally:
        for k in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[k]


def test_without_the_finder_the_first_directory_wins(two_pkgs):
    name, _bad, _good = two_pkgs
    assert _fresh(name).Y == 0  # 对照：没有名单时行为就是 CPython 的


def test_ignored_directory_is_skipped_for_top_level_imports(two_pkgs, finder):
    name, bad, _good = two_pkgs
    events = []
    f = finder([str(bad)], events.append)
    before = list(sys.path)
    assert _fresh(name).Y == 1
    assert sys.path == before  # 用户的 sys.path 不动
    assert [(e["module"], os.path.normcase(e["entry"])) for e in f.events] == [
        (name, os.path.normcase(str(bad)))
    ]
    assert events == f.events


def test_submodules_follow_the_package_that_won(two_pkgs, finder):
    name, bad, _good = two_pkgs
    finder([str(bad)])
    importlib.invalidate_caches()
    for k in [k for k in sys.modules if k.startswith(name)]:
        del sys.modules[k]
    try:
        sub = importlib.import_module(name + ".sub")
        assert sub.Z == 1
    finally:
        for k in [k for k in sys.modules if k.startswith(name)]:
            del sys.modules[k]


def test_only_listed_directories_are_affected(two_pkgs, finder, tmp_path):
    name, bad, good = two_pkgs
    finder([str(tmp_path / "unrelated")])  # 名单里是别的目录
    assert _fresh(name).Y == 0
    finder([str(good)])  # 忽略的是后面那个：前面的仍赢
    assert _fresh(name).Y == 0


def test_a_users_own_module_in_an_unlisted_directory_still_wins(two_pkgs, finder, tmp_path):
    name, bad, _good = two_pkgs
    mine = tmp_path / "mine"
    (mine / name).mkdir(parents=True)
    (mine / name / "__init__.py").write_text("Y = 7\n")
    sys.path.insert(0, str(mine))
    finder([str(bad)])
    assert _fresh(name).Y == 7


def test_a_name_only_the_ignored_directory_has_is_not_found(tmp_path, clean_sys_path, finder):
    name = _name()
    only = tmp_path / "only"
    (only / name).mkdir(parents=True)
    (only / name / "__init__.py").write_text("")
    sys.path.insert(0, str(only))
    finder([str(only)])
    importlib.invalidate_caches()
    with pytest.raises(ModuleNotFoundError) as err:
        importlib.import_module(name)
    assert err.value.name == name
    assert name not in sys.modules


def test_names_the_ignored_directory_does_not_have_take_the_normal_path(two_pkgs, finder):
    _name_, bad, _good = two_pkgs
    f = finder([str(bad)])
    assert f.find_spec("json") is None  # 被忽略的目录里没有 json：不接手，常规路径一字不变
    assert f.events == []


def test_installs_before_pathfinder_and_after_builtin_and_frozen(finder):
    f = finder([])
    chain = sys.meta_path
    assert chain.index(f) < chain.index(importlib.machinery.PathFinder)
    assert chain.index(f) > chain.index(importlib.machinery.BuiltinImporter)


def test_install_helper_is_idempotent_and_empty_list_installs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(bridgeboot, "_IGNORED", None)
    assert bridgeboot.install_ignored_import_paths([]) is None
    made = bridgeboot.install_ignored_import_paths([str(tmp_path)])
    try:
        assert (
            made is not None
            and bridgeboot.install_ignored_import_paths([str(tmp_path / "x")]) is made
        )
        assert sum(1 for f in sys.meta_path if f is made) == 1
    finally:
        sys.meta_path.remove(made)


# --------------------------------------------------------------------------- 名单与令牌
@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    yield root
    pathignore.forget(root)
    pathignore.reset()


def test_decision_is_remembered_per_project_and_stored_relative(project, tmp_path):
    inner = project / "vendor"
    outer = tmp_path / "outside_dir"
    inner.mkdir()
    outer.mkdir()
    assert pathignore.paths_for(project) == ()
    pathignore.ignore_folder(project, str(inner))
    state = pathignore.ignore_folder(project, str(outer))
    assert state == {"paths": ["vendor", "outside_dir"]}
    assert pathignore.paths_for(project) == (str(inner), str(outer))
    from tavotto.engine import config

    stored = config.project_settings(str(project))[pathignore.SETTINGS_KEY]
    assert stored == ["vendor", str(outer)]  # 项目里的存相对路径，项目外的存绝对路径
    other = tmp_path / "other"
    other.mkdir()
    assert pathignore.paths_for(other) == ()  # 别的项目没有


def test_duplicates_collapse_and_forget_undoes(project):
    d = project / "vendor"
    d.mkdir()
    pathignore.ignore_folder(project, str(d))
    pathignore.ignore_folder(project, str(d))
    assert pathignore.paths_for(project) == (str(d),)
    pathignore.forget(project, str(d))
    assert pathignore.paths_for(project) == ()


def test_project_root_and_its_ancestors_can_never_be_ignored(project, tmp_path):
    for bad in (str(project), str(tmp_path), "relative/dir", "", None):
        with pytest.raises(pathignore.IgnoreError) as err:
            pathignore.ignore_folder(project, bad)
        assert err.value.code == pathignore.ERROR_REJECTED
    # 设置文件被手改成项目根：读出来也被丢掉
    from tavotto.engine import config

    config.set_project_settings(str(project), {pathignore.SETTINGS_KEY: [".", "..", "vendor"]})
    assert pathignore.paths_for(project) == (str(project / "vendor"),)


def test_token_is_bound_to_the_project_expires_and_is_one_shot(project, tmp_path):
    d = project / "vendor"
    d.mkdir()
    token = pathignore.issue(project, str(d), now=100.0)
    assert token and str(d) not in token
    assert pathignore.path_of_token(token, project, now=101.0) == str(d)
    with pytest.raises(pathignore.IgnoreError) as err:
        pathignore.path_of_token(token, tmp_path / "other", now=101.0)
    assert err.value.code == pathignore.ERROR_TOKEN_INVALID
    with pytest.raises(pathignore.IgnoreError):
        pathignore.path_of_token(token, project, now=100.0 + pathignore.TOKEN_TTL + 1)
    pathignore.consume_token(token)
    with pytest.raises(pathignore.IgnoreError):
        pathignore.path_of_token(token, project, now=101.0)
    assert pathignore.issue(project, "relative") is None and pathignore.issue("", str(d)) is None


# --------------------------------------------------------------------------- spec / argv 同源
def _safe(ignored=()):
    return execspec.safe_spec(
        "fig.py", "/p", "__main__", interpreter="/py", sandbox="/sb", ignored_paths=ignored
    )


def test_both_entries_take_the_same_flag_and_golden_argv_is_unchanged_without_it():
    plain = execspec.worker_argv(_safe(), worker_py="/w.py", out_dir="/o")
    assert "--ignore-paths" not in plain
    with_ = execspec.worker_argv(_safe(("/p/vendor",)), worker_py="/w.py", out_dir="/o")
    assert with_[: len(plain)] == plain and with_[-2:] == ["--ignore-paths", '["/p/vendor"]']

    native = execspec.native_spec("fig.py", interpreter="/py", cwd="/p", project_root="/p")
    assert "--ignore-paths" not in execspec.bridge_argv(native, runner_py="/r.py", out_dir="/o")
    native = execspec.native_spec(
        "fig.py", interpreter="/py", cwd="/p", project_root="/p", ignored_paths=("/p/vendor",)
    )
    argv = execspec.bridge_argv(native, runner_py="/r.py", out_dir="/o")
    i = argv.index("--ignore-paths")
    assert argv[i + 1] == '["/p/vendor"]' and i < argv.index("--")


def test_ignored_paths_are_machine_specific_and_round_trip():
    spec = _safe(("/p/vendor",))
    assert "ignored_paths" not in spec.stable_payload()
    assert execspec.spec_from_payload(spec.to_payload()).ignored_paths == ("/p/vendor",)
    with pytest.raises(ValueError):
        execspec.ExecutionSpec(**{**spec.__dict__, "ignored_paths": ("",)})


# --------------------------------------------------------------------------- 失败码转换
def _extra(**over):
    fact = {"folder": "vendor", "abi": "3.12", "current": "3.14", "module": "fakepkg"}
    fact.update(over)
    return {
        "foreign_abi_folder": fact,
        "foreign_abi_folder_private": {"path": "/abs/secret/vendor"},
    }


def test_failure_becomes_the_closed_code_with_a_token_and_no_path(project):
    d = project / "vendor"
    d.mkdir()
    extra = _extra()
    extra["foreign_abi_folder_private"] = {"path": str(d)}
    err = engine_pool.foreign_abi_error("boom", "tb", extra, project=str(project))
    assert err.code == engine_pool.IMPORT_FOLDER_ABI_CODE == "import_folder_abi_mismatch"
    assert err.traceback_text == "tb" and str(err) == "boom"
    params = engine_pool.recovery_params(err)
    assert set(params) == {"folder", "abi", "current", "ignore_token"}
    assert (params["folder"], params["abi"], params["current"]) == ("vendor", "3.12", "3.14")
    assert str(d) not in json.dumps(err.extra) and str(d) not in json.dumps(params)
    assert pathignore.path_of_token(params["ignore_token"], project) == str(d)


def test_without_a_project_there_is_no_token_and_malformed_facts_are_distrusted():
    err = engine_pool.foreign_abi_error("m", "tb", _extra(), project="")
    assert err.code == engine_pool.IMPORT_FOLDER_ABI_CODE
    assert "ignore_token" not in engine_pool.recovery_params(err)
    for bad in (
        {"folder": ""},
        {"folder": "a\\b"},
        {"folder": "x" * 200},
        {"abi": None},
        {"current": "x\n"},
    ):
        assert engine_pool.foreign_abi_error("m", "tb", _extra(**bad), project="") is None
    assert engine_pool.foreign_abi_error("m", "tb", None) is None
    assert engine_pool.foreign_abi_error("m", "tb", {"foreign_abi_folder": "x"}) is None


def test_classification_comes_before_missing_dependency_and_unreadable(project):
    err = engine_pool._worker_error(
        "No module named 'fakepkg._native'", "script_error", "tb", _extra(), project=str(project)
    )
    assert err.code == engine_pool.IMPORT_FOLDER_ABI_CODE


def test_the_code_is_in_the_registry():
    assert engine_pool.IMPORT_FOLDER_ABI_CODE in engine_pool.ERROR_CODES


# --------------------------------------------------------------------------- 端到端：真 worker / 真 bridge
SCRIPT = """\
import sys
sys.path.insert(0, {vendor!r})
sys.path.append({good!r})
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
import {pkg}
ax.plot([0, 1], [0, {pkg}.Y])
{show}
"""


def _version_of(python: str) -> tuple[int, int]:
    import subprocess

    out = subprocess.run(
        [python, "-c", "import sys;print(sys.version_info[0], sys.version_info[1])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return int(out[0]), int(out[1])


def _world(tmp_path: Path, pkg: str) -> tuple[Path, Path, Path]:
    """项目里一个 vendor/（里面的 `pkg` 带一个别的小版本的扩展，`__init__` 要 import 它）+ 另一个目录里同名的正常包。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    # 扩展的标签对着**起脚本的那个解释器**取别的小版本（它不一定是跑 pytest 的这个）
    major, minor = _version_of(bridgekit.USER_PYTHON)
    vendor = _vendor(proj, pkg, [_foreign(minor=minor + 1 if minor < 90 else minor - 1)])
    good = tmp_path / "good"
    (good / pkg).mkdir(parents=True)
    (good / pkg / "__init__.py").write_text("Y = 1\n")
    return proj, vendor, good


needs_worker = pytest.mark.skipif(
    bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"
)


@pytest.fixture
def safe_project(tmp_path):
    root = tmp_path / "proj"
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)
    pathignore.forget(root)
    pathignore.reset()


@needs_worker
def test_safe_worker_end_to_end_names_the_folder_then_succeeds_after_it_is_ignored(
    tmp_path, safe_project
):
    pkg = _name()
    proj, vendor, good = _world(tmp_path, pkg)
    (proj / "fig.py").write_text(
        SCRIPT.format(vendor=str(vendor), good=str(good), pkg=pkg, show="")
    )
    workdir.set_mode(proj, workdir.MODE_SANDBOX)
    # 1) 没有决定：被私有目录盖住，得到新失败码（带显示名、两个 ABI 版本和令牌）
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(proj), "__main__")
    assert err.value.code == engine_pool.IMPORT_FOLDER_ABI_CODE, err.value
    params = engine_pool.recovery_params(err.value)
    major, minor = _version_of(bridgekit.USER_PYTHON)
    assert (params["folder"], params["abi"], params["current"]) == (
        "vendor",
        f"{major}.{minor + 1 if minor < 90 else minor - 1}",
        f"{major}.{minor}",
    )
    assert str(vendor) not in json.dumps(params)
    # 2) 记住忽略（令牌 → 目录，与端点同一条路径）→ 重跑成功，留痕
    path = pathignore.path_of_token(params["ignore_token"], proj)
    pathignore.ignore_folder(proj, path)
    engine_pool.shutdown_all(str(proj), wait=True)
    worker, resp = engine_pool.build("fig.py", str(proj), "__main__")
    assert resp["runtime"]["python_version"].startswith(f"{major}.{minor}")
    log = Path(worker.log_path).read_text(encoding="utf-8", errors="replace")
    assert f'[import-ignore] {{"module": "{pkg}"' in log
    # 3) 撤销后回到原样（决定是用户的、可逆的）
    pathignore.forget(proj)
    engine_pool.shutdown_all(str(proj), wait=True)
    with pytest.raises(engine_pool.WorkerError) as again:
        engine_pool.build("fig.py", str(proj), "__main__")
    assert again.value.code == engine_pool.IMPORT_FOLDER_ABI_CODE


@needs_worker
def test_native_bridge_end_to_end_names_the_folder_then_succeeds_with_the_flag(tmp_path):
    from tavotto.engine import bridge

    pkg = _name()
    proj, vendor, good = _world(tmp_path, pkg)
    script = bridgekit.write(
        proj / "fig.py",
        SCRIPT.format(vendor=str(vendor), good=str(good), pkg=pkg, show="plt.show()"),
    )
    user_python = bridgekit.USER_PYTHON

    def run(ignored):
        spec = bridge.spec_for(
            str(script),
            interpreter=user_python,
            cwd=str(proj),
            project_root=str(proj),
            ignored_paths=ignored,
        )
        os.environ.pop("PYTHONPATH", None)
        sess = bridge.BridgeSession(spec, out_dir=tmp_path / f"out-{len(ignored)}")
        sess.start()
        try:
            return sess.wait_event("barrier")["script_error"]
        finally:
            sess.close()

    saved = os.environ.get("PYTHONPATH")
    try:
        failed = run(())
        assert failed is not None
        fact = failed["foreign_abi_folder"]
        assert fact["folder"] == "vendor" and fact["module"] == pkg
        assert failed["foreign_abi_folder_private"] == {"path": str(vendor)}
        assert run((str(vendor),)) is None
    finally:
        if saved is not None:
            os.environ["PYTHONPATH"] = saved
