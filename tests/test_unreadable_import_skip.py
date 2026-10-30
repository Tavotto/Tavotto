"""import 时跳过「找到了但读不了」的位置（ADR 0153，risk:high：每个脚本的 import 路径）。

CPython 在 `sys.path` 某处找到了模块、却读不了它（`PermissionError`）时就地失败，不往后找。反馈包 TVD-HZ0A：
脚本把一个当前账户没权限读的项目外目录塞到 `sys.path[0]`，`import pandas` 挂在 `tzdata\\__init__.py` 上。
`bridgeboot.UnreadableImportSkipper` 只在这一种情况换下一个位置；本文件钉：

* 生效面：包 / 单文件模块 / 命名空间包下的子模块、相对路径条目、大小写不敏感；
* 不生效面：能读的路径、找不到的模块、读得了但语法错的模块——行为一字不变；全部读不了 → 原样抛最初的 `PermissionError`；
* 不改 `sys.path`；留痕；失败事实 `unreadable_folder_fact`；回执投影；
* 两条真入口（safe worker、native bridge）各一条端到端，外加变异：不装查找器，端到端必须红。

不可读文件用 `chmod 000` 构造；Windows 与 root 下打不开的前提不成立，整个文件在前提不成立时 skip（Windows 的 ACL 腿
由 Windows Lab 手工验证，见 ADR 0153）。
"""

from __future__ import annotations

import importlib
import importlib.machinery
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

from support import bridgekit
from tavotto.engine import bridgeboot, pool as engine_pool, receipt, workdir


def _unreadable_works(tmp_path: Path) -> bool:
    probe = tmp_path / "probe.txt"
    probe.write_text("x")
    try:
        probe.chmod(0)
        with open(probe, "rb"):
            return False
    except PermissionError:
        return True
    finally:
        probe.chmod(0o600)


@pytest.fixture(autouse=True)
def _need_chmod(tmp_path_factory):
    if os.name == "nt":
        pytest.skip("chmod 000 在 Windows 上不构成不可读；ACL 腿见 ADR 0153")
    if not _unreadable_works(tmp_path_factory.mktemp("probe")):
        pytest.skip("以特权身份运行，chmod 000 的文件仍可读")


def _name() -> str:
    return "skipmod_" + uuid.uuid4().hex[:10]


def _lock(path: Path) -> Path:
    path.chmod(0)
    return path


@pytest.fixture
def skipper():
    """装一个独立的查找器实例（不碰进程级单例），用完摘掉并清缓存。

    同一个 pytest 进程里别的用例可能 import 过 `worker.py`（模块层就会装进程级单例，装进的是它自己按文件装的那份
    `bridgeboot`），所以先把已有的同类查找器按位置摘下来、结束再原位放回——否则事件记在别人账上。"""
    saved_path = list(sys.path)
    others = [
        (i, f) for i, f in enumerate(sys.meta_path) if type(f).__name__ == "UnreadableImportSkipper"
    ]
    for _, f in reversed(others):
        sys.meta_path.remove(f)
    made = bridgeboot.UnreadableImportSkipper()
    assert made.install()
    yield made
    made.uninstall()
    for i, f in others:
        sys.meta_path.insert(i, f)
    sys.path[:] = saved_path
    importlib.invalidate_caches()


@pytest.fixture
def two_dirs(tmp_path):
    bad, good = tmp_path / "bad", tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    yield bad, good
    for root in (bad, good):  # 让 tmp_path 清理删得掉
        for p in root.rglob("*"):
            p.chmod(0o700)


def _import(name: str):
    importlib.invalidate_caches()
    try:
        return importlib.import_module(name)
    finally:
        sys.modules.pop(name, None)


# --------------------------------------------------------------------------- 生效面
def test_package_unreadable_first_readable_second(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    (bad / name).mkdir()
    (bad / name / "__init__.py").write_text("WHERE='bad'")
    _lock(bad / name / "__init__.py")
    (good / name).mkdir()
    (good / name / "__init__.py").write_text("WHERE='good'")
    sys.path[:0] = [str(bad), str(good)]
    before = list(sys.path)
    assert _import(name).WHERE == "good"
    assert sys.path == before  # 用户的 sys.path 本身不动
    assert [(e["module"], e["entry"], e["error"]) for e in skipper.events] == [
        (name, os.path.normcase(os.path.abspath(bad)), "PermissionError")
    ]


def test_single_file_module(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    (bad / f"{name}.py").write_text("WHERE='bad'")
    _lock(bad / f"{name}.py")
    (good / f"{name}.py").write_text("WHERE='good'")
    sys.path[:0] = [str(bad), str(good)]
    assert _import(name).WHERE == "good"
    assert len(skipper.events) == 1


def test_submodule_of_a_namespace_package(skipper, two_dirs):
    bad, good = two_dirs
    ns, leaf = _name(), "leaf"
    for root, body in ((bad, "WHERE='bad'"), (good, "WHERE='good'")):
        (root / ns).mkdir()
        (root / ns / f"{leaf}.py").write_text(body)
    _lock(bad / ns / f"{leaf}.py")
    sys.path[:0] = [str(bad), str(good)]
    importlib.invalidate_caches()
    try:
        assert importlib.import_module(f"{ns}.{leaf}").WHERE == "good"
    finally:
        for key in (ns, f"{ns}.{leaf}"):
            sys.modules.pop(key, None)
    assert [e["module"] for e in skipper.events] == [f"{ns}.{leaf}"]


def test_subpackage_inside_a_regular_package_path(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    for root in (bad, good):
        (root / name).mkdir()
        (root / name / "__init__.py").write_text(
            "import pkgutil\n__path__ = pkgutil.extend_path(__path__, __name__)\n"
        )
        (root / name / "sub").mkdir()
    (bad / name / "sub" / "__init__.py").write_text("WHERE='bad'")
    (good / name / "sub" / "__init__.py").write_text("WHERE='good'")
    _lock(bad / name / "sub" / "__init__.py")
    sys.path[:0] = [str(bad), str(good)]
    importlib.invalidate_caches()
    try:
        assert importlib.import_module(f"{name}.sub").WHERE == "good"
    finally:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key)
    assert [e["module"] for e in skipper.events] == [f"{name}.sub"]


def test_relative_sys_path_entry(skipper, two_dirs, monkeypatch):
    bad, good = two_dirs
    name = _name()
    (bad / f"{name}.py").write_text("WHERE='bad'")
    _lock(bad / f"{name}.py")
    (good / f"{name}.py").write_text("WHERE='good'")
    monkeypatch.chdir(bad.parent)
    sys.path[:0] = ["bad", str(good)]  # 条目是相对路径，origin 是绝对路径
    assert _import(name).WHERE == "good"
    assert len(skipper.events) == 1


def test_entry_matching_is_case_insensitive_where_normcase_is(monkeypatch):
    class Spec:
        origin = "C:\\Deps\\Pkg\\__init__.py"
        submodule_search_locations = ["C:\\Deps\\Pkg"]

    import ntpath

    monkeypatch.setattr(bridgeboot.os, "path", ntpath)
    assert bridgeboot._hit_entry(Spec, ["c:\\other", "C:\\DEPS"]) == ntpath.normcase("C:\\Deps")


def test_second_unreadable_is_skipped_too(skipper, tmp_path):
    name = _name()
    dirs = [tmp_path / n for n in ("a", "b", "c")]
    for d in dirs:
        d.mkdir()
        (d / f"{name}.py").write_text(f"WHERE={d.name!r}")
    _lock(dirs[0] / f"{name}.py")
    _lock(dirs[1] / f"{name}.py")
    sys.path[:0] = [str(d) for d in dirs]
    try:
        assert _import(name).WHERE == "c"
    finally:
        for d in dirs:
            (d / f"{name}.py").chmod(0o600)
    assert len(skipper.events) == 2


# --------------------------------------------------------------------------- 不生效面
def test_all_locations_unreadable_raises_the_first_permission_error(skipper, two_dirs):
    bad, other = two_dirs
    name = _name()
    for root in (bad, other):
        (root / f"{name}.py").write_text("x=1")
        _lock(root / f"{name}.py")
    sys.path[:0] = [str(bad), str(other)]
    with pytest.raises(PermissionError) as err:
        _import(name)
    assert os.path.dirname(err.value.filename) == str(bad)  # 最初命中的那个，不是后面的
    assert skipper.events == []


def test_readable_path_is_untouched(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    (good / f"{name}.py").write_text("WHERE='good'")
    sys.path[:0] = [str(good)]
    importlib.invalidate_caches()
    want = importlib.machinery.PathFinder.find_spec(name)
    got = skipper.find_spec(name)
    assert (got.origin, got.name, type(got.loader)) == (want.origin, want.name, type(want.loader))
    assert _import(name).WHERE == "good"
    assert skipper.events == []


def test_missing_module_is_still_module_not_found(skipper):
    name = _name()
    with pytest.raises(ModuleNotFoundError) as err:
        _import(name)
    assert err.value.name == name


def test_readable_but_broken_module_is_not_skipped(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    (bad / f"{name}.py").write_text("def broken(:\n")  # 读得了，语法错：不是我们的事
    (good / f"{name}.py").write_text("WHERE='good'")
    sys.path[:0] = [str(bad), str(good)]
    with pytest.raises(SyntaxError):
        _import(name)
    assert skipper.events == []


def _bytecode_case(skipper, two_dirs, mutate):
    """源码读不了 + 一份（按 `mutate` 改过的）缓存：回 find_spec 命中的 origin 所在目录，和事件数。"""
    bad, good = two_dirs
    name = _name()
    src = bad / f"{name}.py"
    src.write_text("WHERE='bad'")
    importlib.invalidate_caches()
    sys.path[:0] = [str(bad)]
    _import(name)  # 先写出 .pyc
    pyc = Path(importlib.util.cache_from_source(str(src)))
    if not pyc.exists():
        pytest.skip("本环境不写字节码")
    mutate(src, pyc)
    (good / f"{name}.py").write_text("WHERE='good'")
    sys.path.append(str(good))
    importlib.invalidate_caches()
    _lock(src)
    try:
        spec = skipper.find_spec(name)
        return Path(spec.origin).parent, bad, good, len(skipper.events)
    finally:
        src.chmod(0o600)


def test_valid_cached_bytecode_means_the_module_is_loadable_not_skipped(skipper, two_dirs):
    where, bad, _, events = _bytecode_case(skipper, two_dirs, lambda src, pyc: None)
    assert where == bad and events == 0


def test_stale_cached_bytecode_does_not_stop_the_skip(skipper, two_dirs):
    def age(src, pyc):  # 源码的 mtime 变了：时间戳型缓存按 CPython 的规则失效
        st = src.stat()
        os.utime(src, (st.st_atime, st.st_mtime + 1000))

    where, _, good, events = _bytecode_case(skipper, two_dirs, age)
    assert where == good and events == 1


def test_corrupt_cached_bytecode_does_not_stop_the_skip(skipper, two_dirs):
    def corrupt(src, pyc):
        pyc.write_bytes(pyc.read_bytes()[:20])  # 头还在，body 被截断

    where, _, good, events = _bytecode_case(skipper, two_dirs, corrupt)
    assert where == good and events == 1

    def bad_magic(src, pyc):
        pyc.write_bytes(b"\x00\x00\x00\x00" + pyc.read_bytes()[4:])

    where, _, good, events = _bytecode_case(skipper, two_dirs, bad_magic)
    assert where == good


def test_reentrant_calls_fall_through(skipper):
    skipper._local.busy = True
    try:
        assert skipper.find_spec(_name()) is None
    finally:
        skipper._local.busy = False


def test_installs_right_before_pathfinder_and_after_builtin_and_frozen(skipper):
    mp = sys.meta_path
    i = mp.index(skipper)
    assert mp[i + 1] is importlib.machinery.PathFinder
    assert mp.index(importlib.machinery.BuiltinImporter) < i
    assert mp.index(importlib.machinery.FrozenImporter) < i


def test_uninstall_removes_it(two_dirs):
    s = bridgeboot.UnreadableImportSkipper()
    assert s.install() and s in sys.meta_path
    s.uninstall()
    assert s not in sys.meta_path


def test_overhead_is_small(skipper, tmp_path):
    """热路径多一次 open：对已存在模块的 find_spec 与裸 PathFinder 比，每次多出的绝对时间在 0.5 ms 以内。"""
    mod = _name()
    (tmp_path / f"{mod}.py").write_text("x=1")
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    n = 400
    pf = importlib.machinery.PathFinder.find_spec
    t0 = time.perf_counter()
    for _ in range(n):
        pf(mod)
    base = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n):
        skipper.find_spec(mod)
    ours = time.perf_counter() - t0
    assert (ours - base) / n < 0.0005, (base / n, ours / n)


# --------------------------------------------------------------------------- 失败事实 B
def test_unreadable_folder_fact_names_the_script_added_outside_folder(tmp_path, monkeypatch):
    dep = tmp_path / ".work_deps"
    dep.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    err = PermissionError(13, "Permission denied", str(dep / "tzdata" / "__init__.py"))
    baseline = list(sys.path)
    monkeypatch.syspath_prepend(str(dep))
    wrapped = ImportError("boom")
    wrapped.__cause__ = err
    fact = bridgeboot.unreadable_folder_fact(
        wrapped, baseline, [str(proj)], [sys.prefix], _seen(err)
    )
    assert fact == {"folder": ".work_deps", "module": "tzdata"}


def _seen(err) -> set:
    """查找器当场登记过的「命中但读不了」的文件集合（证据）。"""
    return {bridgeboot._norm(err.filename)}


def test_data_file_permission_error_is_not_an_import_failure(tmp_path, monkeypatch):
    """脚本把外部库目录加进 sys.path 后读写其中的数据文件（external/pandas/data.csv）撞权限错误：不出事实。"""
    dep = tmp_path / "external"
    dep.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    baseline = list(sys.path)
    monkeypatch.syspath_prepend(str(dep))
    csv = PermissionError(13, "Permission denied", str(dep / "pandas" / "data.csv"))
    module = PermissionError(13, "Permission denied", str(dep / "pandas" / "__init__.py"))
    registered = _seen(module)  # 查找器只登记过模块文件
    assert bridgeboot.unreadable_folder_fact(csv, baseline, [str(proj)], (), registered) == {}
    assert bridgeboot.unreadable_folder_fact(module, baseline, [str(proj)], (), registered) == {
        "folder": "external",
        "module": "pandas",
    }
    assert bridgeboot.unreadable_folder_fact(module, baseline, [str(proj)], (), set()) == {}


def test_skipper_registers_the_blocked_module_file_as_evidence(skipper, two_dirs):
    bad, good = two_dirs
    name = _name()
    (bad / f"{name}.py").write_text("x=1")
    _lock(bad / f"{name}.py")
    sys.path[:0] = [str(bad)]
    with pytest.raises(PermissionError) as err:
        _import(name)
    assert bridgeboot._norm(err.value.filename) in skipper.blocked_files


def test_unreadable_folder_fact_rejects_what_it_should(tmp_path, monkeypatch):
    dep = tmp_path / "dep"
    dep.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    err = PermissionError(13, "x", str(dep / "m.py"))
    monkeypatch.syspath_prepend(str(dep))
    base_with = list(sys.path)
    seen = _seen(err)
    # 启动时就在 sys.path 里的目录（PYTHONPATH 之类）：不是「脚本加载的」
    assert bridgeboot.unreadable_folder_fact(err, base_with, [str(proj)], (), seen) == {}
    base_without = [p for p in base_with if p != str(dep)]
    # 在项目里：不是项目外
    assert bridgeboot.unreadable_folder_fact(err, base_without, [str(tmp_path)], (), seen) == {}
    # 解释器自己的目录：换环境才可能有用
    assert bridgeboot.unreadable_folder_fact(err, base_without, [str(proj)], [str(dep)], seen) == {}
    # 不是 Errno 13 / 不是 PermissionError / 没有文件名
    assert (
        bridgeboot.unreadable_folder_fact(
            FileNotFoundError(2, "x", str(dep / "m.py")), base_without, [str(proj)], (), seen
        )
        == {}
    )
    assert (
        bridgeboot.unreadable_folder_fact(
            PermissionError(13, "x"), base_without, [str(proj)], (), seen
        )
        == {}
    )
    assert bridgeboot.unreadable_folder_fact(err, base_without, [str(proj)], (), seen) == {
        "folder": "dep",
        "module": "m",
    }


def test_unmappable_module_falls_back_to_the_one_sentence_code():
    fact = {"unreadable_folder": {"folder": ".deps", "module": "ROOT"}}  # ROOT：装不了（不在 PyPI）
    exc = engine_pool.unreadable_folder_error("m", "ModuleNotFoundError: No module named 'x'", fact)
    assert exc.code == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    assert engine_pool.recovery_params(exc) == {"folder": ".deps"}
    no_module = {"unreadable_folder": {"folder": ".deps"}}
    assert (
        engine_pool.unreadable_folder_error("m", "t", no_module).code
        == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    )
    worker = engine_pool.EngineWorker.__new__(engine_pool.EngineWorker)
    worker.script_name = "fig.py"
    resp = {"error": {"code": "script_error", "message": "m", "traceback": "t", **no_module}}
    assert worker._error_of(resp).code == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    assert engine_pool._worker_error("m", "script_error", "t", no_module).code == (
        engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    )


def test_classification_is_not_bookkeeping_and_install_is(tmp_path):
    mod, proj, other = _name(), str(tmp_path / "p1"), str(tmp_path / "p2")
    fact = {"unreadable_folder": {"folder": ".deps", "module": mod}}
    # 连续分类两次：都是 missing_dependency（分类从不记账，用户重试渲染仍有安装出路）
    for _ in range(2):
        exc = engine_pool.unreadable_folder_error("m", "t", fact, project=proj)
        assert exc.code == "missing_dependency" and exc.module == mod
    worker = engine_pool.EngineWorker.__new__(engine_pool.EngineWorker)
    worker.script_name, worker.figures_dir = "fig.py", proj
    resp = {"error": {"code": "script_error", "message": "m", "traceback": "t", **fact}}
    assert worker._error_of(resp).code == "missing_dependency"
    # 依赖安装真正成功提交到该项目之后，同一处仍失败才落到一句话的卡
    engine_pool.note_unreadable_install(proj, mod)
    assert (
        engine_pool.unreadable_folder_error("m", "t", fact, project=proj).code
        == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    )
    assert worker._error_of(resp).code == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    # 换一个项目不受影响
    assert engine_pool.unreadable_folder_error("m", "t", fact, project=other).code == (
        "missing_dependency"
    )


def test_both_success_endings_of_a_dependency_install_record_the_module():
    """记账只在安装成功提交之后：`deprepair._run_install` 的两个成功终态（受管环境 / 用户环境）各调一次。"""
    import ast
    import inspect
    import textwrap

    from tavotto.engine import deprepair

    tree = ast.parse(textwrap.dedent(inspect.getsource(deprepair._run_install)))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "note_unreadable_install"
    ]
    assert len(calls) == 2


def test_mutation_without_the_routing_the_routing_test_goes_red(monkeypatch):
    """变异：路由判据恒假时，上面那条的断言（缺依赖码）必须不成立——它不是恒真。"""
    monkeypatch.setattr(engine_pool, "_mappable_to_distribution", lambda module: False)
    fact = {"unreadable_folder": {"folder": ".deps", "module": _name()}}
    assert engine_pool.unreadable_folder_error("m", "t", fact).code != "missing_dependency"


@pytest.mark.parametrize("folder", ["", "a/b", "a\\b", "x\ny", "z" * 200, 3, None])
def test_pool_distrusts_malformed_folder_names(folder):
    assert (
        engine_pool.unreadable_folder_error("m", "t", {"unreadable_folder": {"folder": folder}})
        is None
    )
    assert engine_pool.unreadable_folder_error("m", "t", {"unreadable_folder": folder}) is None
    assert engine_pool.unreadable_folder_error("m", "t", None) is None


# --------------------------------------------------------------------------- 回执投影
def test_receipt_projection_is_whitelisted_and_paths_stay_private():
    raw = [
        {"module": "tzdata", "entry": "/secret/dep", "error": "PermissionError", "extra": 1},
        {"module": "../evil", "entry": "x"},
        "junk",
        {"module": "a.b", "entry": 5},
    ]
    assert receipt.project_import_skips(raw) == [
        {"module": "tzdata", "error": "PermissionError"},
        {"module": "a.b", "error": "PermissionError"},
    ]
    private = receipt.project_import_skips(raw, include_private=True)
    assert private[0]["entry"] == "/secret/dep" and "entry" not in private[1]
    assert receipt.project_import_skips("nope") == []


# --------------------------------------------------------------------------- 端到端：真 worker / 真 bridge
SCRIPT = """\
import sys
sys.path.insert(0, {bad!r})
sys.path.insert(1, {good!r})
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
import {mod}
ax.plot([0, 1], [0, {mod}.Y])
{show}
"""


def _dep_dirs(tmp_path: Path, mod: str, *, with_good: bool) -> tuple[Path, Path]:
    bad, good = tmp_path / ".work_deps_bad", tmp_path / "deps_good"
    (bad / mod).mkdir(parents=True)
    (bad / mod / "__init__.py").write_text("Y = 0")
    _lock(bad / mod / "__init__.py")
    good.mkdir()
    if with_good:
        (good / mod).mkdir()
        (good / mod / "__init__.py").write_text("Y = 1")
    return bad, good


def _unlock(*roots: Path) -> None:
    for root in roots:
        for p in root.rglob("*"):
            p.chmod(0o700)


@pytest.fixture
def safe_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


needs_worker = pytest.mark.skipif(
    bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"
)


@needs_worker
def test_safe_worker_end_to_end_skips_and_leaves_a_trace(tmp_path, safe_project):
    mod = _name()
    bad, good = _dep_dirs(tmp_path, mod, with_good=True)
    (safe_project / "fig.py").write_text(
        SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show="")
    )
    workdir.set_mode(safe_project, workdir.MODE_SANDBOX)
    try:
        worker, resp = engine_pool.build("fig.py", str(safe_project), "__main__")
        skips = resp["runtime"]["import_skips"]
        assert [(e["module"], e["error"]) for e in skips] == [(mod, "PermissionError")]
        assert os.path.normcase(str(bad)) in os.path.normcase(skips[0]["entry"])
        log = Path(worker.log_path).read_text(encoding="utf-8", errors="replace")
        assert f'[import-skip] {{"module": "{mod}"' in log
    finally:
        _unlock(bad, good)


@needs_worker
def test_safe_worker_end_to_end_fail_then_install_then_succeed(tmp_path, safe_project):
    """现场形状：依赖夹里的包读不了、没有别的位置 → 先是「缺依赖」卡（带模块名）；用户一键装进可读位置后重跑成功并留痕。"""
    mod = _name()
    bad, good = _dep_dirs(tmp_path, mod, with_good=False)
    (safe_project / "fig.py").write_text(
        SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show="")
    )
    workdir.set_mode(safe_project, workdir.MODE_SANDBOX)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build("fig.py", str(safe_project), "__main__")
        assert err.value.code == "missing_dependency" and err.value.module == mod
        assert "PermissionError" in err.value.traceback_text  # 最初的 PermissionError 原样在
        engine_pool.shutdown_all(str(safe_project), wait=True)
        # 模拟「装进受管环境」：后面出现一份可读的
        (good / mod).mkdir()
        (good / mod / "__init__.py").write_text("Y = 1")
        worker, resp = engine_pool.build("fig.py", str(safe_project), "__main__")
        assert [e["module"] for e in resp["runtime"]["import_skips"]] == [mod]
    finally:
        _unlock(bad, good)


@needs_worker
def test_safe_worker_end_to_end_unmappable_module_names_the_folder(tmp_path, safe_project):
    mod = "ROOT"  # 装不了的名字：落到一句话的卡
    bad, good = _dep_dirs(tmp_path, mod, with_good=False)
    (safe_project / "fig.py").write_text(
        SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show="")
    )
    workdir.set_mode(safe_project, workdir.MODE_SANDBOX)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build("fig.py", str(safe_project), "__main__")
        assert err.value.code == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
        assert engine_pool.recovery_params(err.value) == {"folder": ".work_deps_bad"}
    finally:
        _unlock(bad, good)


# --------------------------------------------------------------------------- 变异（空门禁检查）
def test_mutation_without_the_install_calls_the_end_to_end_goes_red(tmp_path):
    """变异：拷一份 `tavotto` 包，把 worker.py 里的装配调用换成 `pass`，用拷贝的 pool 真起 worker 跑同一个脚本——
    原版跳过成功，变异版必须撞上 PermissionError。这条自己就是门禁：端到端里「跳过成功」的断言不是恒真。"""
    if bridgekit.USER_PYTHON is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    import subprocess

    src = Path(bridgeboot.__file__).parent.parent  # .../tavotto
    mod = _name()
    bad, good = _dep_dirs(tmp_path, mod, with_good=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show=""))
    harness = (
        "import sys, json\n"
        "from tavotto.engine import pool, workdir\n"
        f"workdir.set_mode({str(proj)!r}, workdir.MODE_SANDBOX)\n"
        "try:\n"
        f"    w, resp = pool.build('fig.py', {str(proj)!r}, '__main__')\n"
        "    print('RESULT', json.dumps({'ok': True, 'skips': resp['runtime']['import_skips']}))\n"
        "except pool.WorkerError as exc:\n"
        "    print('RESULT', json.dumps({'ok': False, 'code': exc.code, 'tb': exc.traceback_text[-300:]}))\n"
        f"pool.shutdown_all({str(proj)!r}, wait=True)\n"
    )

    def build_with(root: Path) -> dict:
        env = bridgekit.child_env({"PYTHONPATH": str(root), "TAVOTTO_NO_TELEMETRY": "1"})
        env["XDG_CACHE_HOME"] = env["TAVOTTO_DATA_DIR"] = str(tmp_path / ("data-" + root.name))
        done = subprocess.run(
            [bridgekit.USER_PYTHON, "-c", harness],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=240,
            cwd=str(tmp_path),
        )
        line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), None)
        assert line, done.stdout[-500:] + done.stderr[-1500:]
        return __import__("json").loads(line[7:])

    real_root = tmp_path / "real"
    mutant_root = tmp_path / "mutant"
    for root in (real_root, mutant_root):
        shutil.copytree(src, root / "tavotto", ignore=shutil.ignore_patterns("__pycache__"))
    worker_py = mutant_root / "tavotto" / "engine" / "worker.py"
    text = worker_py.read_text(encoding="utf-8")
    call = "bridgeboot.install_unreadable_import_skipper(_log_import_skip)"
    assert call in text
    worker_py.write_text(text.replace(call, "pass"), encoding="utf-8")
    try:
        real = build_with(real_root)
        mutant = build_with(mutant_root)
    finally:
        _unlock(bad, good)
    assert real["ok"] is True and [e["module"] for e in real["skips"]] == [mod], real
    assert mutant["ok"] is False and "PermissionError" in mutant["tb"], mutant


# --------------------------------------------------------------------------- 试运行 / tavotto open 保留
def test_probe_keeps_the_folder_code_and_params():
    from tavotto.engine import probe

    exc = engine_pool.unreadable_folder_error(
        "m", "tb", {"unreadable_folder": {"folder": ".deps", "module": "ROOT"}}
    )
    out = probe._error_from_worker(exc, "main")
    assert out["code"] == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    assert out["params"] == {"folder": ".deps"}


def test_probe_missing_dependency_from_an_unreadable_folder_keeps_module_and_folder():
    from tavotto.engine import probe

    exc = engine_pool.unreadable_folder_error(
        "m", "tb", {"unreadable_folder": {"folder": ".deps", "module": _name()}}, project=""
    )
    assert exc.code == "missing_dependency"
    out = probe._error_from_worker(exc, "main")  # 没有 figures_dir：不生成 offer，但码与参数在
    assert out["code"] == "missing_dependency"
    assert out["params"]["module"] == exc.module and out["params"]["folder"] == ".deps"


def test_tavotto_open_names_the_folder_instead_of_the_own_environment_sentence():
    from tavotto.engine import handoff

    for code in ("missing_dependency", "import_folder_unreadable"):
        err = handoff._probe_error(
            {"code": code, "message": "x", "params": {"module": "tzdata", "folder": ".deps"}}
        )
        assert err.code == "import_folder_unreadable"
        assert err.extra["folder"] == ".deps" and err.extra["probe_code"] == code
        assert ".deps" in str(err) and "自己的 Python 环境" not in str(err)
    plain = handoff._probe_error({"code": "missing_dependency", "params": {"module": "scipy"}})
    assert plain.code == "native_run_required"


# --------------------------------------------------------------------------- 联合准备的成功终态也记账
def test_joint_install_endings_record_every_import_name_of_the_plan(tmp_path):
    """联合安装（原地）与受管环境换代事务的成功终态各调一次 `_note_unreadable_installs`，按账里每个 import 名记一笔；
    用户指定的包（没有 import 名）跳过。是源码形状 + 行为检查，没有真跑一次 joint 安装。"""
    import ast
    import inspect
    import textwrap

    from tavotto.engine import deprepair

    for fn in (deprepair._run_joint_in_place, deprepair._run_generation_locked):
        names = {
            n.func.id
            for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn))))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_note_unreadable_installs" in names, fn.__name__
    proj = str(tmp_path / "p")
    mods = [_name(), _name()]
    deprepair._note_unreadable_installs(
        proj,
        (
            {"import_name": mods[0], "distribution": "a"},
            {"import_name": mods[1], "distribution": "b"},
            {"import_name": "", "distribution": "user"},
        ),
    )
    for m in mods:
        fact = {"unreadable_folder": {"folder": ".deps", "module": m}}
        assert (
            engine_pool.unreadable_folder_error("m", "t", fact, project=proj).code
            == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
        )
