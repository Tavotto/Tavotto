"""MCP 来源 safe 运行的写入根绊线（#898，ADR 0141）。

**绊线不是沙盒。** 这批用例两头都钉：

* 挡得住的（chdir 之后的相对写、`contextlib.chdir`、跨文件 helper 里切目录再写、根外的删除 / 改名……）在 MCP
  来源下得到稳定码 `write_outside_roots`，且文件真的没落盘；
* **挡不住的（`subprocess`、`ctypes`）断言「不挡」**——防止后人把它当成沙盒宣传（盲区清单见 `writetripwire` 模块头）；
* GUI / CLI 来源（不带 `write_roots`）同一份脚本不装钩子；
* 合法的写（沙盒、授权根内、临时目录、matplotlib 缓存、seaborn 数据目录、字节码缓存、引擎自己导出到任意临时目录）不误拦；
* 绊线是 worker 的**身份**：池键 / 缓存目录 / workerd 规格里的 argv 都因它而不同。

真 worker 走 `bridge._spawn_worker`（桥里唯一的取 worker 入口）：这里量的是 MCP 的整条链，不是模块自己。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from tavotto.engine import capabilities, execspec, pool as engine_pool, writetripwire

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge  # noqa: E402

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="需要 POSIX 的 libc / 进程语义")

CODE = "write_outside_roots"

PLOT = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nplt.figure()\n"


class World:
    """工作区（授权根）+ 根外目录 + 一个 MCP 来源的 worker 入口。"""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.ws = tmp_path / "ws"
        self.figs = self.ws / "proj"
        self.outside = tmp_path / "outside"
        self.figs.mkdir(parents=True)
        self.outside.mkdir()
        self.monkeypatch = monkeypatch
        # 授权根就是工作区。桥里 `_tripwire_roots` 读的是 `allowed_roots()`
        monkeypatch.setattr(bridge, "allowed_roots", lambda: [str(self.ws)])

    def script(self, name: str, source: str) -> None:
        (self.figs / name).write_text(source, encoding="utf-8")

    def session(self, name: str):
        return types.SimpleNamespace(
            run=None, project=str(self.figs), script=name, entry="__main__"
        )

    def mcp_worker(self, name: str):
        return bridge._spawn_worker(self.session(name), one_shot=False)

    def gui_worker(self, name: str):
        """GUI / CLI 来源：不带 `write_roots`，与桥无关。"""
        return engine_pool.get(name, str(self.figs), "__main__")


@pytest.fixture
def world(tmp_path, monkeypatch):
    # 测试进程的 pytest tmp_path 在系统临时目录之下：把 worker 的临时目录挪走，否则「根外」目录
    # 本身就落在临时目录白名单里，什么都证明不了
    tmpdir = tmp_path / "worker-tmp"
    tmpdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmpdir))
    monkeypatch.setenv("TEMP", str(tmpdir))
    monkeypatch.setenv("TMP", str(tmpdir))
    # Windows 上 pytest 的 tmp_path 在 %LOCALAPPDATA%\\Temp 之下，而白名单放行了那里的 Temp（临时文件的家）：
    # 不挪走它，「根外」目录就落在白名单里，用例的主语错了（CI 上 DID NOT RAISE）。macOS / Linux 的白名单
    # 是 ~/Library/Caches 与 ~/.cache，pytest 的 tmp_path 不在其中，所以同一个主语错误在那里咬不到
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    # 默认的 seaborn / sklearn 数据目录也别落进真实 HOME
    monkeypatch.setenv("SEABORN_DATA", str(tmp_path / "seaborn-data"))
    monkeypatch.setenv("SCIKIT_LEARN_DATA", str(tmp_path / "scikit_learn_data"))
    w = World(tmp_path, monkeypatch)
    yield w
    engine_pool.shutdown_all(str(w.figs), wait=True)


def _refused(world: World, name: str) -> engine_pool.WorkerError:
    worker = world.mcp_worker(name)
    with pytest.raises(engine_pool.WorkerError) as err:
        worker.ensure_built()
    return err.value


# ============================================================ 修前红：#898 的三种静态证明不了的写法
CHDIR = (
    """\
import os
os.chdir({out!r})
with open("leak.txt", "w") as f:
    f.write("x")
"""
    + PLOT
)

CONTEXT_CHDIR = (
    """\
import contextlib, os
chdir = getattr(contextlib, "chdir", None)
if chdir is None:  # 3.10：同一语义的手写版
    @contextlib.contextmanager
    def chdir(path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)
with chdir({out!r}):
    with open("leak.txt", "w") as f:
        f.write("x")
"""
    + PLOT
)

HELPER = """\
import os


def go(target):
    os.chdir(target)
    with open("leak.txt", "w") as f:
        f.write("x")
"""

CROSS_FILE = "import helper\nhelper.go({out!r})\n" + PLOT


@needs_worker
@pytest.mark.parametrize(
    "name,source",
    [("chdir.py", CHDIR), ("ctxchdir.py", CONTEXT_CHDIR), ("cross.py", CROSS_FILE)],
    ids=["os.chdir", "contextlib.chdir", "cross-file-helper"],
)
def test_a_write_after_chdir_outside_the_roots_is_refused_with_the_stable_code(world, name, source):
    world.script("helper.py", HELPER)
    world.script(name, source.format(out=str(world.outside)))
    err = _refused(world, name)
    assert err.code == CODE
    assert not (world.outside / "leak.txt").exists(), "越界的写不许落盘"
    # 相对化展示：消息里没有测试机上的任何绝对路径
    assert (
        str(world.tmp) not in str(err) and str(world.tmp) not in err.traceback_text.split("File")[0]
    )
    assert err.extra["path"].endswith("outside/leak.txt")


@needs_worker
def test_swallowing_the_permission_error_does_not_wash_the_attempt(world):
    world.script(
        "swallow.py",
        "import os\nos.chdir({out!r})\ntry:\n    open('leak.txt', 'w').write('x')\n"
        "except PermissionError:\n    pass\n".format(out=str(world.outside))
        + PLOT,
    )
    assert _refused(world, "swallow.py").code == CODE


@needs_worker
def test_removing_or_renaming_a_file_outside_the_roots_is_refused_and_the_file_survives(world):
    victim = world.outside / "keep.txt"
    victim.write_text("data", encoding="utf-8")
    world.script("rm.py", f"import os\nos.remove({str(victim)!r})\n" + PLOT)
    assert _refused(world, "rm.py").code == CODE
    assert victim.read_text(encoding="utf-8") == "data"
    world.script(
        "mv.py", f"import os\nos.rename({str(victim)!r}, {str(world.ws / 'moved.txt')!r})\n" + PLOT
    )
    assert _refused(world, "mv.py").code == CODE
    assert victim.exists() and not (world.ws / "moved.txt").exists()


@needs_worker
def test_the_error_reaches_the_bridge_with_the_recovery_and_no_absolute_paths(world):
    world.script("chdir.py", CHDIR.format(out=str(world.outside)))
    err = _refused(world, "chdir.py")
    shaped = bridge._bridge_error_from_worker(err)
    body = shaped.payload()
    assert body["code"] == CODE
    assert "Tavotto 窗口" in body["recovery"]
    assert str(world.tmp) not in str(body.get("path", ""))


# ============================================================ 正向：合法的写不误拦
@needs_worker
def test_legitimate_writes_are_not_refused(world, tmp_path):
    (world.ws / "other").mkdir()
    world.script(
        "ok.py",
        "\n".join(
            [
                "import os, tempfile, tarfile",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                # 沙盒（默认 cwd）里的相对写
                "open('scratch.txt', 'w').write('x')",
                # 授权根内、但不在项目里的目录
                f"open({str(world.ws / 'other' / 'inroot.txt')!r}, 'w').write('x')",
                # 临时目录
                "with tempfile.NamedTemporaryFile('w') as t:\n    t.write('x')",
                # matplotlib 的缓存目录
                "open(os.path.join(matplotlib.get_cachedir(), 'probe.tmp'), 'w').write('x')",
                # seaborn / sklearn 的数据目录（环境变量指到测试目录）
                f"os.makedirs({str(tmp_path / 'seaborn-data')!r}, exist_ok=True)",
                f"open({str(tmp_path / 'seaborn-data' / 'tips.csv')!r}, 'w').write('x')",
                f"os.makedirs({str(tmp_path / 'scikit_learn_data')!r}, exist_ok=True)",
                f"open({str(tmp_path / 'scikit_learn_data' / 'x.pkz')!r}, 'w').write('x')",
                "plt.figure()",
            ]
        )
        + "\n",
    )
    worker = world.mcp_worker("ok.py")
    assert worker.ensure_built()["stems"]


@needs_worker
def test_bytecode_caches_are_allowed_but_only_that_shape(world):
    libdir = world.outside / "lib"
    libdir.mkdir()
    (libdir / "outsidemod.py").write_text("VALUE = 3\n", encoding="utf-8")
    world.script(
        "imp.py",
        f"import sys\nsys.dont_write_bytecode = False\nsys.path.insert(0, {str(libdir)!r})\n"
        "import outsidemod\nassert outsidemod.VALUE == 3\n" + PLOT,
    )
    world.mcp_worker(
        "imp.py"
    ).ensure_built()  # 不抛：字节码写要么成功要么被 importlib 吞掉，都不是越界
    (libdir / "sub" / "__pycache__").mkdir(parents=True)
    world.script(
        "pycfake.py",
        f"open({str(libdir / 'sub' / '__pycache__' / 'data.txt')!r}, 'w').write('x')\n" + PLOT,
    )
    assert _refused(world, "pycfake.py").code == CODE  # `__pycache__` 里不是 .pyc 形状的不放行


@needs_worker
def test_the_engine_itself_may_export_to_any_directory_after_the_build(world):
    """绊线窗口只是脚本 build：引擎自己的导出要写进 Agent 指定 / 临时的任意目录，不能被拦。"""
    world.script(
        "fig.py",
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([0, 1])\nfig.savefig('Fig.pdf')\n",
    )
    worker = world.mcp_worker("fig.py")
    resp = worker.ensure_built()
    stem = next(iter(resp["stems"]))
    target = world.outside / "exported.pdf"
    worker.export(stem, [], str(target), "pdf", 150)
    assert target.is_file() and target.stat().st_size > 0


# ============================================================ 盲区：断言「不挡」，防止误宣称成沙盒
@needs_worker
def test_a_subprocess_write_is_not_caught_because_this_is_a_tripwire_not_a_sandbox(world):
    leak = world.outside / "child.txt"
    code = f"open({str(leak)!r}, 'w').write('x')"
    world.script(
        "child.py",
        f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {code!r}], check=True)\n"
        + PLOT,
    )
    world.mcp_worker("child.py").ensure_built()
    assert leak.exists(), (
        "盲区：子进程写不经本进程的审计钩子——若这里挡住了，文档与 ADR 0141 的盲区清单就要改"
    )


@needs_worker
@posix_only
def test_a_ctypes_write_is_not_caught_either(world):
    leak = world.outside / "ctypes.txt"
    world.script(
        "ct.py",
        "import ctypes, os\n"
        "libc = ctypes.CDLL(None)\n"
        "libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]\n"
        f"fd = libc.open({str(leak).encode()!r}, os.O_WRONLY | os.O_CREAT, 0o644)\n"
        "assert fd >= 0\nos.close(fd)\n" + PLOT,
    )
    world.mcp_worker("ct.py").ensure_built()
    assert leak.exists(), "盲区：ctypes 直调 libc 不经审计事件"


# ============================================================ GUI / CLI 来源：同脚本不装钩子
@needs_worker
def test_the_same_script_from_the_gui_source_has_no_tripwire(world):
    world.script("chdir.py", CHDIR.format(out=str(world.outside)))
    world.gui_worker("chdir.py").ensure_built()
    assert (world.outside / "leak.txt").exists()


@needs_worker
def test_mcp_and_gui_get_different_sessions_for_the_same_script(world):
    world.script("same.py", PLOT)
    mcp = world.mcp_worker("same.py")
    gui = world.gui_worker("same.py")
    assert mcp is not gui
    assert mcp.base != gui.base
    assert mcp.write_roots == (str(world.ws),) and gui.write_roots is None
    assert world.mcp_worker("same.py") is mcp, "同一来源、同一组根复用热会话"


# ============================================================ 身份：池键 / 缓存目录 / spawn 规格
def test_the_tripwire_is_part_of_the_pool_key_and_the_cache_dir(tmp_path):
    figs = str(tmp_path)
    plain = engine_pool._worker_key(figs, "a.py")
    guarded = engine_pool._worker_key(figs, "a.py", None, None, (str(tmp_path / "ws"),))
    other = engine_pool._worker_key(figs, "a.py", None, None, (str(tmp_path / "ws2"),))
    assert plain != guarded != other
    assert plain == engine_pool._worker_key(figs, "a.py", None, None, None), (
        "没装绊线时键逐字节不变"
    )
    assert guarded[:2] == plain[:2], "按 (项目, 脚本) 前缀作废时，绊线变体一起作废"
    assert (
        len({engine_pool._worker_base(figs, "a.py", None, None, r) for r in (None, ("a",), ("b",))})
        == 3
    )
    assert execspec.write_roots_key(("a", "b")) == execspec.write_roots_key(("b", "a")), (
        "根的顺序不是身份"
    )


def test_the_workerd_spawn_spec_carries_the_roots_so_its_hash_differs(tmp_path):
    """workerd 的会话键是 spawn 规格里 argv + env + cwd 的哈希（`SpawnSpec::hash`，Rust 侧
    `spec_hash_changes_with_argv_env_and_cwd` 钉着）。根进 argv，哈希就天然不同。"""
    common = (
        "a.py",
        str(tmp_path),
        "__main__",
        tmp_path / "out",
        tmp_path / "sb",
        tmp_path / "log",
        sys.executable,
        engine_pool.SOURCE_SYSTEM,
    )
    plain = engine_pool._spawn_spec(*common)
    guarded = engine_pool._spawn_spec(*common, write_roots=(str(tmp_path / "ws"),))
    assert "--write-roots" not in plain["argv"]
    assert guarded["argv"][-2] == "--write-roots"
    assert guarded["argv"][:-2] == plain["argv"], "只多这两个 token，其余逐字节不变"
    assert guarded["argv"] != plain["argv"]


def test_both_control_planes_get_the_same_argv_from_one_source(tmp_path):
    spec = execspec.safe_spec(
        "a.py",
        str(tmp_path),
        "__main__",
        interpreter=sys.executable,
        sandbox=str(tmp_path / "sb"),
        write_roots=("/r1", "/r2"),
    )
    argv = execspec.worker_argv(spec, worker_py="w.py", out_dir=tmp_path / "out")
    assert argv[-2:] == ["--write-roots", '["/r1","/r2"]']
    none_spec = execspec.safe_spec(
        "a.py", str(tmp_path), "__main__", interpreter=sys.executable, sandbox=str(tmp_path / "sb")
    )
    assert "--write-roots" not in execspec.worker_argv(
        none_spec, worker_py="w.py", out_dir=tmp_path / "out"
    )
    assert none_spec.stable_payload() == spec.stable_payload(), "机器相关的根不进跨机器稳定的身份"
    assert execspec.spec_from_payload(spec.to_payload()).write_roots == ("/r1", "/r2")


def test_a_native_spec_cannot_carry_the_tripwire():
    with pytest.raises(ValueError):
        execspec.ExecutionSpec(
            profile=execspec.PROFILE_NATIVE,
            interpreter=sys.executable,
            target_kind=execspec.TARGET_SCRIPT,
            target="a.py",
            entry=None,
            argv=(),
            cwd="/",
            env=None,
            project_root="/",
            passthrough_savefig=True,
            raw_target="a.py",
            write_roots=("/r",),
        )


# ============================================================ 桥：能力协商 / 回滚开关
def test_without_the_engine_feature_the_bridge_sends_nothing(monkeypatch):
    monkeypatch.setattr(bridge, "allowed_roots", lambda: ["/ws"])
    assert bridge._tripwire_roots() == ("/ws",)
    monkeypatch.setattr(bridge, "engine_features", lambda: [])
    assert bridge._tripwire_roots() is None, "引擎不宣告 = 回滚：不传参，旧引擎同理"
    assert bridge.WRITE_ROOT_TRIPWIRE_FEATURE == capabilities.WRITE_ROOT_TRIPWIRE
    assert bridge.WRITE_OUTSIDE_ROOTS == writetripwire.CODE == engine_pool.WRITE_OUTSIDE_ROOTS_CODE


def test_the_rollback_does_not_reach_the_pool(world, monkeypatch):
    seen = {}

    def fake_get(script, project, entry, **context):
        seen.update(context)
        return object()

    monkeypatch.setattr(engine_pool, "get", fake_get)
    world.script("a.py", PLOT)
    bridge._spawn_worker(world.session("a.py"), one_shot=False)
    assert seen["write_roots"] == (str(world.ws),)
    seen.clear()
    monkeypatch.setattr(bridge, "engine_features", lambda: [])
    bridge._spawn_worker(world.session("a.py"), one_shot=False)
    assert "write_roots" not in seen


def test_the_tripwire_error_never_triggers_the_native_fallback():
    from tavotto.engine import execpolicy

    assert engine_pool.WRITE_OUTSIDE_ROOTS_CODE not in execpolicy.BLOCKED_CODES


# ============================================================ 白名单的纯函数部分
def test_infrastructure_roots_are_derived_per_platform_with_reasons(tmp_path):
    home = str(tmp_path / "home")
    linux = writetripwire.infrastructure_roots({}, home=home, platform="linux", extra=("/s",))
    assert "/s" in linux
    assert str(tmp_path / "home" / ".cache") in linux
    assert str(tmp_path / "home" / "seaborn-data") in linux
    assert str(tmp_path / "home" / "scikit_learn_data") in linux
    mac = writetripwire.infrastructure_roots({}, home=home, platform="darwin")
    assert str(tmp_path / "home" / "Library" / "Caches") in mac
    win = writetripwire.infrastructure_roots(
        {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, home=home, platform="win32"
    )
    assert r"C:\Users\u\AppData\Local" not in win, (
        "不放行整个 %LOCALAPPDATA%（Tavotto 数据目录在它下面）"
    )
    assert any(r.endswith("pip") for r in win)
    env = writetripwire.infrastructure_roots(
        {
            "SEABORN_DATA": "/sb",
            "SCIKIT_LEARN_DATA": "/sk",
            "XDG_CACHE_HOME": "/xdg",
            "MPLCONFIGDIR": "/mpl",
        },
        home=home,
        platform="linux",
    )
    assert {"/sb", "/sk", "/xdg", "/mpl"} <= set(env)
    assert home not in env, "不放行整个 HOME"
    assert sys.prefix not in env, "不放行 sys.prefix 整体（只放行字节码形状）"


def test_allowed_is_the_roots_plus_bytecode_plus_devices_and_nothing_else(tmp_path):
    root = str(tmp_path / "ws")
    assert writetripwire.allowed(os.path.join(root, "a", "b.csv"), (root,))
    assert not writetripwire.allowed(str(tmp_path / "ws2" / "a"), (root,)), "前缀相同的兄弟目录不算"
    assert not writetripwire.allowed(str(tmp_path / "x.txt"), (root,))
    pyc = os.path.join(str(tmp_path), "site", "__pycache__", "m.cpython-313.pyc")
    assert writetripwire.allowed(pyc, (root,)) and writetripwire.allowed(pyc + ".123456", (root,))
    assert not writetripwire.allowed(str(tmp_path / "site" / "__pycache__" / "m.txt"), (root,))
    assert not writetripwire.allowed(str(tmp_path / "site" / "m.pyc"), (root,)), (
        "不在 __pycache__ 里的 .pyc 不放行"
    )
    cache_dir = str(tmp_path / "site" / "__pycache__")
    assert writetripwire.allowed(cache_dir, (root,), event="os.mkdir"), "建缓存目录本身放行"
    for event in ("open", "os.rmdir", "os.remove", "os.rename", "shutil.rmtree"):
        assert not writetripwire.allowed(cache_dir, (root,), event=event), event
    assert writetripwire.allowed(pyc, (root,), event="os.rename")
    assert writetripwire.allowed(pyc, (root,), event="os.remove")
    assert not writetripwire.allowed(pyc, (root,), event="os.rmdir")
    assert not writetripwire.allowed(pyc, (root,), event="shutil.rmtree")


# ============================================================ Codex #1224 的三条
@needs_worker
def test_hardlinking_an_outside_file_into_the_roots_is_refused_and_the_file_is_untouched(world):
    victim = world.outside / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    alias = world.ws / "alias.txt"
    world.script(
        "link.py",
        f"import os\nos.link({str(victim)!r}, {str(alias)!r})\n"
        f"open({str(alias)!r}, 'w').write('overwritten')\n" + PLOT,
    )
    assert _refused(world, "link.py").code == CODE
    assert victim.read_text(encoding="utf-8") == "original"
    assert not alias.exists()


@needs_worker
@pytest.mark.parametrize(
    "op",
    [
        "import shutil; shutil.rmtree({d!r})",
        "import os; os.rmdir({d!r})",
        "open({f!r}, 'w').write('x')",
        "import os; os.remove({f!r})",
        "import os; os.rename({f!r}, {g!r})",
    ],
    ids=["rmtree", "rmdir", "overwrite-non-pyc", "remove-non-pyc", "rename-non-pyc"],
)
def test_a_directory_named_pycache_outside_the_roots_is_not_a_free_pass(world, op):
    cache = world.outside / "lib" / "__pycache__"
    cache.mkdir(parents=True)
    keep = cache / "keep.txt"
    keep.write_text("data", encoding="utf-8")
    empty = world.outside / "lib2" / "__pycache__"
    empty.mkdir(parents=True)
    target = empty if "rmdir" in op else cache
    world.script(
        "pyc.py",
        op.format(d=str(target), f=str(keep), g=str(world.outside / "moved.txt")) + "\n" + PLOT,
    )
    assert _refused(world, "pyc.py").code == CODE
    assert target.exists()
    assert keep.read_text(encoding="utf-8") == "data"


def _folds_case(directory: Path) -> bool:
    flipped = directory.parent / directory.name.swapcase()
    return flipped.exists() and os.path.samefile(flipped, directory)


@needs_worker
def test_a_root_spelled_in_another_case_on_a_case_insensitive_volume_is_not_a_false_refusal(world):
    if not _folds_case(world.ws):
        pytest.skip("这个卷区分大小写（Linux 等）：没有这条误拦")
    other = world.ws / "other"
    other.mkdir()
    spelled = world.ws.parent / world.ws.name.swapcase()
    world.monkeypatch.setattr(bridge, "allowed_roots", lambda: [str(spelled)])
    world.script("case.py", f"open({str(other / 'ok.txt')!r}, 'w').write('x')\n" + PLOT)
    world.mcp_worker("case.py").ensure_built()
    assert (other / "ok.txt").exists()


def test_allowed_folds_case_per_volume_not_by_platform_guess(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    spelled = str(tmp_path / "WS" / "a.csv")
    assert writetripwire.allowed(spelled, (str(root),)) == _folds_case(root)


# ---------------------------------------------------------------- Codex #1224 复核：不存在的根不许落到平台默认值
def _probe_world(monkeypatch, *, folds: bool | None):
    """替换探测函数来模拟卷的大小写行为（macOS 默认卷上做不出真实区分大小写的卷：这部分只能纯函数验）。"""
    seen: list[str] = []

    def probe(directory):
        seen.append(directory)
        return folds

    writetripwire._FOLD_CACHE.clear()
    monkeypatch.setattr(writetripwire, "_probe_existing", probe)
    return seen


def test_a_missing_root_on_a_case_sensitive_volume_does_not_fold(tmp_path, monkeypatch):
    seen = _probe_world(monkeypatch, folds=False)  # 最近存在的祖先所在的卷：区分大小写
    root = str(tmp_path / "seaborn-data")  # 不存在，和 ~/seaborn-data 一样
    assert not os.path.exists(root)
    assert not writetripwire.allowed(str(tmp_path / "SEABORN-DATA" / "x.csv"), (root,))
    assert writetripwire.allowed(os.path.join(root, "x.csv"), (root,)), "同拼写照常放行"
    assert seen == [str(tmp_path)], "探测的是最近存在的祖先，不是不存在的根"
    writetripwire._FOLD_CACHE.clear()


def test_a_missing_root_under_a_case_insensitive_ancestor_folds(tmp_path, monkeypatch):
    _probe_world(monkeypatch, folds=True)
    root = str(tmp_path / "deep" / "er" / "seaborn-data")  # 连父目录都不存在
    assert writetripwire.allowed(str(tmp_path / "DEEP" / "er" / "SEABORN-DATA" / "x"), (root,))
    writetripwire._FOLD_CACHE.clear()


def test_when_the_probe_cannot_tell_the_answer_is_fail_closed(tmp_path, monkeypatch):
    _probe_world(monkeypatch, folds=None)  # 探测不出
    root = str(tmp_path / "ws")
    assert not writetripwire.allowed(str(tmp_path / "WS" / "x"), (root,))
    writetripwire._FOLD_CACHE.clear()
    assert writetripwire.folds_case("") is False, "没有任何存在的祖先 → 不折叠"
    writetripwire._FOLD_CACHE.clear()
