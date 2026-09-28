"""只读探测不往用户的解释器里写东西；Tavotto 自己的环境不往数据目录之外写缓存。

2026-09-28 Windows Server 2025 实测（冻结安装包）：

* 体检系统候选（用户装的 Python 3.7.6）之后，它的 `Lib\\__pycache__\\dataclasses.cpython-37.pyc`、
  `uuid.cpython-37.pyc` 被新写入——时间戳正好是体检那一刻。产品承诺不改动用户环境。
* 私有 Python + 受管环境装 adjustText 之后，多出 `%LOCALAPPDATA%\\pip`（受管环境里跑的 pip 的缓存）与
  `%LOCALAPPDATA%\\matplotlib`（受管环境里的 matplotlib 3.11 在 Windows 上的默认配置 / 缓存目录）。

**判据的主语**：

* 字节码——被探测的那个解释器进程，在它 import 的源码旁边有没有写 `.pyc`。传感器是 PYTHONPATH
  上的一个 `sitecustomize.py`：每个不带 `-I` / `-S` 的解释器启动时都会 import 它，不带 `-B` 就在
  传感器目录里留下 `__pycache__/sitecustomize.*.pyc`。对照组先证明传感器是活的（同样的环境、
  同一个解释器、不带 `-B` 跑一句 `pass` 会留下它），再逐个入口量「没留下」。
* 缓存——在 **Tavotto 自己数据目录里的**解释器（受管环境的形态：`<data_dir>/envs/...` 下的 venv）上
  跑的 pip / matplotlib，缓存写到了哪。HOME / LOCALAPPDATA / XDG 全部指到空的假家目录，量的是
  「假家目录里有没有多出东西、数据目录里有没有」。用户自己的环境（不在数据目录下）一律原样继承。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import (
    bootstrap,
    codexinstall,
    depplan,
    deprepair,
    managedenv,
    pool,
    projectenv,
    runspec,
    runtime,
)

try:
    USER_PYTHON = pool.find_worker_python()
except pool.WorkerError:  # pragma: no cover - 取决于开发机
    USER_PYTHON = None

pytestmark = pytest.mark.skipif(
    USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 会从外面决定「写不写字节码 / 缓存落在哪」的变量：用例量的是产品自己的行为，一律摘掉。
_OUTSIDE_ENV = (
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
    "PYTHONPATH",
    "MPLCONFIGDIR",
    "PIP_CACHE_DIR",
    "PIP_NO_CACHE_DIR",
    "PIP_CONFIG_FILE",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
)


def _pycs(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.pyc"))


# --------------------------------------------------------------------- 字节码
@pytest.fixture
def sensor(tmp_path, monkeypatch) -> Path:
    """PYTHONPATH 上的传感器目录（只有一个 `sitecustomize.py`），并证明它是活的。"""
    for key in _OUTSIDE_ENV:
        monkeypatch.delenv(key, raising=False)
    d = tmp_path / "sensor"
    d.mkdir()
    (d / "sitecustomize.py").write_text("SENSOR = True\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(d))
    # 对照：同一个解释器、同样的环境、不带 -B → 传感器留下 .pyc（不然下面的「没留下」什么都没证明）
    subprocess.run([USER_PYTHON, "-c", "pass"], check=True, timeout=120)
    assert _pycs(d), "前提：传感器是活的（不带 -B 的解释器启动会缓存 sitecustomize）"
    for p in d.rglob("*.pyc"):
        p.unlink()
    assert _pycs(d) == []
    return d


_PLUGIN_SERVER = Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp" / "server.py"

#: 每一个「起一个不属于 Tavotto 的解释器做只读探测」的入口（src/tavotto 里全部）。新增一个就加一行。
_PROBES = {
    "projectenv.probe_environment": lambda py: projectenv.probe_environment(py, "json"),
    "depplan.target_facts": lambda py: depplan.target_facts(py, use_cache=False),
    "bootstrap.find_base_python/_probe": lambda py: bootstrap._probe(py, "import venv"),
    "bootstrap.matplotlib_version": lambda py: bootstrap.matplotlib_version(py),
    "pool._has_matplotlib": lambda py: pool._has_matplotlib(py),
    "runtime.probe_packages": lambda py: runtime.probe_packages(py, ["json"]),
    "deprepair._run (pip --version / installed_version / pip check …)": (
        lambda py: deprepair.installed_version(py, "pip")
    ),
    "deprepair.probe_imports": lambda py: deprepair.probe_imports(py, ["json"]),
    "deprepair._run_lookup": lambda py: deprepair._run_lookup(deprepair.pip_index_argv(py, "zzz")),
    "runspec.probe_interpreter": lambda py: runspec.probe_interpreter(py),
    "codexinstall._runs_python": lambda py: codexinstall._runs_python(py),
    # 插件 `server.py --health` 连同它起的孙进程（resolver 探候选 / 问引擎版本）与之后
    # `launcher_starts` 的复核（Codex #717 P2）
    "codexinstall._verified_interpreter (server.py --health + launcher_starts)": (
        lambda py: codexinstall._verified_interpreter(_PLUGIN_SERVER, py)
    ),
}


@pytest.mark.parametrize("name", sorted(_PROBES))
def test_read_only_probes_write_no_bytecode_into_the_probed_interpreter(name, sensor, monkeypatch):
    monkeypatch.setenv("PIP_NO_INDEX", "1")  # 查找那一条：不联网，秒回
    _PROBES[name](USER_PYTHON)
    assert _pycs(sensor) == [], f"{name} 起的解释器写了字节码"


def test_the_plugins_cli_version_probe_writes_no_bytecode(sensor, tmp_path, monkeypatch):
    """插件降级路径上问 `tavotto doctor --json` 的那一跳（`server._tavotto_cli_version`，Codex #717 P2）：
    `cmd` 多半是 pip / pipx 的控制台脚本，塞不进 `-B`——它起的解释器也不许写字节码。"""
    import importlib

    monkeypatch.syspath_prepend(str(_PLUGIN_SERVER.parent))
    launcher = importlib.import_module("server")
    fake = tmp_path / "tavotto_cli.py"
    fake.write_text('print(\'{"version": "0.10.0"}\')\n', encoding="utf-8")
    assert launcher._tavotto_cli_version([USER_PYTHON, str(fake)]) == "0.10.0"
    assert _pycs(sensor) == []


def test_the_diagnostics_endpoint_probe_writes_no_bytecode(sensor, monkeypatch):
    """`/api/diagnostics` 的 matplotlib 探测（路由里内联的那一条）。"""
    from tavotto import app as m
    from tavotto.engine import ai_bridge

    m.app.config["TESTING"] = True
    monkeypatch.setattr(m.engine_pool, "find_worker_python", lambda: USER_PYTHON)
    monkeypatch.setattr(
        m.engine_ai,
        "capabilities",
        lambda refresh=False: {"agents": [], "endpoints": [], "presets": [], "checked_at_ms": 0},
    )
    try:
        checks = {
            x["id"]: x for x in m.app.test_client().get("/api/diagnostics").get_json()["checks"]
        }
    finally:
        ai_bridge.invalidate_capabilities()
    assert checks["matplotlib"]["ok"] is True, checks["matplotlib"]
    assert _pycs(sensor) == []


# --------------------------------------------------------------------- 缓存
@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    """空的假家目录（HOME / USERPROFILE / LOCALAPPDATA 都指这里）+ 独立的数据目录。"""
    for key in _OUTSIDE_ENV:
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    (home / "AppData" / "Local").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    return home


def _owned_python(tmp_path: Path) -> str:
    """数据目录里的一个 venv（受管环境的形态）；科学栈与 pip 经 .pth 借宿主的，不联网。"""
    root = tmp_path / "data" / "envs" / "p" / "g1"
    subprocess.run(
        [USER_PYTHON, "-m", "venv", "--without-pip", str(root)],
        check=True,
        timeout=120,
    )
    py = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.run(
        [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    # 不 import matplotlib 就问出它在哪（import 会在假家目录里建它的配置目录，污染被测对象）
    host = subprocess.run(
        [
            USER_PYTHON,
            "-c",
            "import importlib.util as u, os;"
            " print(os.path.dirname(os.path.dirname(u.find_spec('matplotlib').origin)))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    Path(site, "host.pth").write_text(host + "\n", encoding="utf-8")
    assert runtime.is_owned_python(str(py)), "前提：它在数据目录里"
    return str(py)


def _home_entries(home: Path) -> list[str]:
    """假家目录里多出来的东西（夹具自己建的 `AppData/Local` 不算）。"""
    fixture = {Path("AppData"), Path("AppData") / "Local"}
    return sorted(
        str(p.relative_to(home)) for p in home.rglob("*") if p.relative_to(home) not in fixture
    )


def test_pip_in_a_tavotto_environment_caches_inside_the_data_dir(fake_home, tmp_path, monkeypatch):
    """受管环境里跑的 pip（装包 `_run_pip` / 其余只读 `_run`）：缓存目录是 `<data_dir>/cache/pip`；
    用户 pip 配置里的其它项（index-url）照样生效，用户关掉缓存时照样不缓存。"""
    py = _owned_python(tmp_path)
    conf = tmp_path / "pip.conf"
    conf.write_text("[global]\nindex-url = https://pypi.example.invalid/simple\n", encoding="utf-8")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(conf))
    expected = str(tmp_path / "data" / "cache" / "pip")

    code, out = deprepair._run_pip(
        [py, "-m", "pip", "cache", "dir"], deprepair.threading.Event(), None
    )
    assert code == "" and out.strip().splitlines()[-1] == expected, out
    rc, out = deprepair._run([py, "-m", "pip", "config", "list"], 60)
    assert rc == 0 and "pypi.example.invalid" in out, "用户的 pip 配置被一起屏蔽了"

    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    code, out = deprepair._run_pip(
        [py, "-m", "pip", "cache", "dir"], deprepair.threading.Event(), None
    )
    assert code != "" and "cache is disabled" in out, "用户关掉的缓存被我们重新打开了"
    assert _home_entries(fake_home) == []


def test_pip_on_a_user_environment_is_left_alone(fake_home, tmp_path):
    """用户自己的环境（不在数据目录里）：原样继承，缓存位置由他的 pip 决定。"""
    assert runtime.owned_env(USER_PYTHON) is None
    code, out = deprepair._run_pip(
        [USER_PYTHON, "-m", "pip", "cache", "dir"], deprepair.threading.Event(), None
    )
    assert code == "" and str(tmp_path / "data") not in out, out


def test_matplotlib_in_a_tavotto_environment_caches_inside_the_data_dir(fake_home, tmp_path):
    """受管环境上的体检（真执行 worker.py → matplotlib 建字体缓存）：用户一个 matplotlib 目录都没有时，
    字体缓存落在 `<data_dir>/cache/mpl`，假家目录里什么都不多。"""
    py = _owned_python(tmp_path)
    info = projectenv.probe_environment(py)
    assert info.get("tavotto_worker_ok") is True, info
    assert list((tmp_path / "data" / "cache" / "mpl").glob("fontlist-*.json"))
    assert _home_entries(fake_home) == []


def test_an_existing_user_matplotlib_dir_is_kept(fake_home, tmp_path):
    """用户已经有自己的 matplotlib 目录（里面可能有 matplotlibrc / stylelib）：不改道，受管环境
    渲染他的脚本时认得他的配置；我们不在数据目录外**新建**任何东西。"""
    mine = fake_home / ".matplotlib"
    mine.mkdir()
    (mine / "matplotlibrc").write_text("lines.linewidth: 7\n", encoding="utf-8")
    py = _owned_python(tmp_path)
    env = runtime.owned_env(py)
    assert env is not None and "MPLCONFIGDIR" not in env
    out = subprocess.run(
        [py, "-B", "-c", "import matplotlib; print(matplotlib.rcParams['lines.linewidth'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    ).stdout.strip()
    assert out == "7.0"


def test_every_worker_spawn_path_gets_the_owned_env(fake_home, tmp_path, monkeypatch):
    """三条 worker spawn 路径（Python 池的 Popen、workerd 规格、自检）同一份判据：Tavotto 自己的
    环境带上缓存目录，用户的环境原样继承。"""
    py = _owned_python(tmp_path)
    delta = pool.worker_env(py, pool.SOURCE_MANAGED_PROJECT, base={})
    assert delta == {
        "PIP_CACHE_DIR": str(tmp_path / "data" / "cache" / "pip"),
        "MPLCONFIGDIR": str(tmp_path / "data" / "cache" / "mpl"),
    }
    assert pool.worker_env(USER_PYTHON, pool.SOURCE_SYSTEM) is None

    seen: dict = {}

    class _Proc:
        def poll(self):
            return None

    def fake_popen(argv, **kw):
        seen["env"] = kw.get("env")
        return _Proc()

    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *a, **k: (py, pool.SOURCE_MANAGED_PROJECT)
    )
    monkeypatch.setattr(pool.subprocess, "Popen", fake_popen)
    figs = tmp_path / "figs"
    figs.mkdir()
    pool.EngineWorker("f.py", str(figs), "main")
    assert seen["env"]["MPLCONFIGDIR"] == delta["MPLCONFIGDIR"]
    spec = pool._spawn_spec(
        "f.py",
        figs,
        "main",
        tmp_path / "o",
        tmp_path / "s",
        tmp_path / "l",
        py,
        pool.SOURCE_MANAGED_PROJECT,
    )
    assert spec["env"] == delta


@pytest.mark.parametrize("entry", ["bootstrap.status", "diagnostics.build_report"])
def test_matplotlib_probe_of_the_bundled_runtime_uses_the_workers_env(
    entry, fake_home, tmp_path, monkeypatch
):
    """状态页 / 诊断包问**内置 runtime** 的 matplotlib 版本（Codex #717 P2 第四轮）：与 worker 同一套启动
    条件——`child_env()`（摘掉外来的 PYTHONPATH、matplotlib 缓存进数据目录），不是原样继承。"""
    from tavotto.engine import diagnostics

    bundled_py = str(tmp_path / "app" / "runtime" / "bin" / "python3")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile"))
    monkeypatch.setattr(pool, "find_worker_python", lambda: bundled_py)
    monkeypatch.setattr(pool, "source_of", lambda py: pool.SOURCE_BUNDLED)
    seen: list = []
    real_run = subprocess.run

    def fake(argv, *a, **kw):
        if argv and argv[0] == bundled_py:
            seen.append((list(argv), kw.get("env")))
            raise OSError("fake")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    if entry == "bootstrap.status":
        bootstrap.status()
    else:
        diagnostics.build_report()
    probes = [(a, env) for a, env in seen if any("import matplotlib" in x for x in a)]
    assert probes, "没问到内置 runtime 的 matplotlib"
    for argv, env in probes:
        assert env is not None and "PYTHONPATH" not in env, entry
        assert env["MPLCONFIGDIR"] == str(tmp_path / "data" / "cache" / "mpl"), entry


def test_the_install_dir_test_python_is_not_owned():
    """前提：宿主测试解释器不在数据目录里——不然上面「用户的环境」那几条量的是另一个对象。"""
    assert not runtime.is_owned_python(USER_PYTHON)
    assert not runtime.is_owned_python(sys.executable)


#: 在 Tavotto 自己的环境上起子进程的每一个入口（除了上面真跑过的 worker 池 / workerd 规格 / `_run_pip` /
#: `_run`）。新增一个就加一行。
_OWNED_SPAWNS = {
    "deprepair.worker_self_test": lambda py: deprepair.worker_self_test(py),
    "deprepair._run_lookup": lambda py: deprepair._run_lookup(deprepair.pip_index_argv(py, "x")),
    "deprepair.probe_imports": lambda py: deprepair.probe_imports(py, ["json"]),
    "projectenv.probe_environment": lambda py: projectenv.probe_environment(py),
    "runtime.probe_packages": lambda py: runtime.probe_packages(py, ["json"]),
    "bootstrap._probe": lambda py: bootstrap._probe(py, "pass"),
    "bootstrap._run (worker-env 的 pip install)": lambda py: bootstrap._run([py, "-m", "pip"]),
    "managedenv._run": lambda py: managedenv._run([py, "-c", "pass"], 5),
    "depplan.target_facts": lambda py: depplan.target_facts(py, use_cache=False),
}


@pytest.mark.parametrize("name", sorted(_OWNED_SPAWNS))
def test_every_spawn_on_a_tavotto_environment_carries_the_cache_dirs(
    name, fake_home, tmp_path, monkeypatch
):
    """判据的主语是真正交给子进程的 env：解释器是假的，起到那一步就回 OSError。"""
    py = str(tmp_path / "data" / "envs" / "p" / "g1" / "bin" / "python")
    seen: list = []

    def fake(argv, *a, **kw):
        seen.append(kw.get("env"))
        raise OSError("fake")

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(subprocess, "Popen", fake)
    _OWNED_SPAWNS[name](py)
    assert seen, "没起子进程"
    cache = tmp_path / "data" / "cache"
    for env in seen:
        assert env is not None and env["PIP_CACHE_DIR"] == str(cache / "pip"), name
        assert env["MPLCONFIGDIR"] == str(cache / "mpl"), name
