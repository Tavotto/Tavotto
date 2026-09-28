"""安装目录里一个 `.pyc` 都不许出现：从安装目录执行引擎代码的每一个子进程入口（QA REL-01-B1）。

根 AGENTS.md 的不变量：运行时可写数据一律走 `config.data_dir()`，**不往包目录 / 安装目录写任何
东西**——macOS 上往签过名的 `.app` 里写一个 `__pycache__`，`codesign --verify` 当场失败，下次
启动 Gatekeeper 报「应用已损坏」。内置 runtime 那条路由 `runtime.child_args()` 的 `-B` 看着；
而**非内置解释器**（项目 .venv / 系统 / 用户配置 / `TAVOTTO_WORKER_PYTHON`）起的子进程照样执行
安装目录里的引擎源码，QA 在 0.16.0 的 `/Applications/Tavotto.app` 里数出 17 个多出来的
`engine/__pycache__/*.pyc`。

为什么不给非内置解释器加 `-B`：那是用户环境的地盘。`-B` 关的是**整个进程**的字节码写入——
uv 建的 venv 默认不预编译，加了它，numpy / matplotlib 每次冷启动都要从源码重编
（`test_bundled_runtime.test_only_the_bundled_runtime_gets_b_flag` 钉着这条）；native bridge
更是明令解释器不加任何标志（`execspec.bridge_argv`）。所以挡的位置在**装载引擎代码的那一段**：
`sys.dont_write_bytecode` 只在 Tavotto 自己的模块装载窗口里打开，装完还原——
项目外的用户模块照常缓存（下面每条都带这一侧的对照断言，两条边一起钉）。safe worker 另外
不往**用户项目目录**里写字节码（2026-09-28 Windows 实测，`worker._suppress_project_bytecode`），
项目里的 `.venv` 不算项目源码；native bridge 与 `python fig.py` 逐字相同，项目内模块照常缓存。

入口清单（每条一个用例；新增一个「用户解释器执行安装目录里的引擎代码」的入口就要在这里加一条）：

* safe worker（`worker.py`，Python 池 / workerd / `deprepair.worker_self_test` 共用 `worker_argv`）
* 环境体检（`projectenv.probe_environment`：按文件执行 `worker.py`——QA 复核里那个来历不明的
  `worker.cpython-313.pyc` 就是它写的）
* 目标解释器静态分析（`discover.analyze_in_interpreter`：`from tavotto.engine import discover`）
* native bridge runner（`bridge_runner.py`：两阶段装载引擎模块）

做法：把 `src/tavotto` 拷进一个模拟的安装目录（不带任何 `__pycache__`），用有 matplotlib 的
**非内置**解释器、以产品自己的 argv 形状真跑一次，然后数安装目录里的 `.pyc`。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from support import bridgekit
from tavotto.engine import discover, execspec, pool, projectenv

SRC_PKG = Path(projectenv.__file__).resolve().parents[1]  # …/src/tavotto

try:
    USER_PYTHON = pool.find_worker_python()
except pool.WorkerError:  # pragma: no cover - 取决于开发机
    USER_PYTHON = None

pytestmark = pytest.mark.skipif(
    USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 会把「写不写字节码」从外面决定掉的变量：用例要量的是产品自己的行为，一律摘掉。
_BYTECODE_ENV = ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONPATH")


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _BYTECODE_ENV}
    env["MPLBACKEND"] = "Agg"
    return env


def _pycs(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.pyc"))


@pytest.fixture
def install(tmp_path) -> Path:
    """模拟的安装目录：`<install>/tavotto/…`，拷完先证明里面一个 .pyc 都没有。"""
    root = tmp_path / "Fake.app" / "Contents" / "Resources" / "_internal"
    root.mkdir(parents=True)
    shutil.copytree(
        SRC_PKG,
        root / "tavotto",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "web"),
    )
    assert _pycs(root) == [], "前提：拷出来的安装目录是干净的"
    return root


@pytest.fixture
def project(tmp_path) -> Path:
    """用户项目：脚本 import 同目录的一个本地模块——对照侧看的就是它的缓存还在不在。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "helper.py").write_text("VALUE = 3\n", encoding="utf-8")
    (proj / "fig.py").write_text(
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import helper\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, helper.VALUE])\n"
        "fig.savefig('fig.pdf')\n",
        encoding="utf-8",
    )
    return proj


def _assert_user_side_still_cached(proj: Path) -> None:
    """另一条边：用户自己的模块照常写字节码——挡的只是 Tavotto 的装载窗口，不是整个进程。"""
    cached = [p.name for p in (proj / "__pycache__").glob("helper.*.pyc")]
    assert cached, "用户模块的字节码缓存被一起关掉了（等于给用户解释器偷偷加了 -B）"


def _run_safe_worker(python, install: Path, proj: Path, entry: str, tmp_path, env) -> None:
    """以产品自己的 argv 形状（`worker_argv`，非内置解释器不带 `-B`）真 build 一次 `fig_data.py`。"""
    out_dir, sandbox = tmp_path / f"out-{entry}", tmp_path / f"sandbox-{entry}"
    out_dir.mkdir()
    sandbox.mkdir()
    spec = execspec.safe_spec(
        "fig_data.py", str(proj), entry, interpreter=python, sandbox=str(sandbox)
    )
    argv = execspec.worker_argv(
        spec, worker_py=install / "tavotto" / "engine" / "worker.py", out_dir=out_dir
    )
    assert "-B" not in argv, "前提：非内置解释器的 argv 不带 -B（用户环境的地盘）"
    proc = subprocess.run(
        argv,
        input='{"cmd": "build"}\n{"cmd": "shutdown"}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
        check=False,
    )
    resps = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    assert any(r.get("ok") and r.get("stems") for r in resps), (proc.stdout, proc.stderr[-2000:])


def _data_project(root: Path, *, extra_import: str) -> Path:
    """`fig_data.py` 两种 entry 都能跑：`main()` 画图，按 `__main__` 跑时模块末尾自己调它。
    它 import 项目里的 `helper`，外加 `extra_import` 那一个项目外（或 venv 里）的模块。"""
    root.mkdir()
    (root / "helper.py").write_text("VALUE = 3\n", encoding="utf-8")
    (root / "fig_data.py").write_text(
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import helper\n"
        f"import {extra_import}\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        "    ax.plot([0, 1], [0, helper.VALUE])\n"
        "    fig.savefig('fig_data.pdf')\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("entry", ["main", "__main__"])
def test_safe_worker_writes_no_bytecode_into_the_install_dir_or_the_project(
    install, tmp_path, entry
):
    """非内置解释器起 safe worker（系统 / 受管 / 项目 venv 跑的都是这一份 `worker.py`；内置 runtime
    另有 `-B`）：安装目录一个 .pyc 都没有，**用户项目目录也没有**——2026-09-28 Windows 实测，
    entry=main 时脚本按模块 import，项目里多出 `__pycache__/fig_data.cpython-312.pyc`，而
    `python fig_data.py` 从不写它；它 import 的项目内模块同样不写。

    对照（另一条边）：项目**外**的模块照常写字节码——挡的是项目目录，不是整个进程（等于偷偷加
    `-B` 的话，没预编译的用户环境每次冷启动都要重编科学栈）。"""
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitehelper.py").write_text("OFFSET = 1\n", encoding="utf-8")
    proj = _data_project(tmp_path / "proj", extra_import="sitehelper")
    env = {**_env(), "PYTHONPATH": str(site)}
    _run_safe_worker(USER_PYTHON, install, proj, entry, tmp_path, env)
    assert _pycs(install) == []
    assert _pycs(proj) == [], "worker 往用户项目目录里写了字节码"
    assert list((site / "__pycache__").glob("sitehelper.*.pyc")), (
        "项目外的模块也不缓存了（整个进程的字节码写入被关掉）"
    )


def _capture(argv: list[str]) -> str:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=_env(),
        timeout=120,
    ).stdout.strip()


def test_a_venv_inside_the_project_still_caches_its_own_packages(install, tmp_path):
    """「项目目录」的判据不吞掉项目根里的 `.venv`（常态）：那是用户的环境，里面的包照常缓存；
    项目源码照样不写。"""
    proj = _data_project(tmp_path / "proj", extra_import="venvmod")
    venv = proj / ".venv"
    subprocess.run(
        [USER_PYTHON, "-m", "venv", "--without-pip", str(venv)],
        check=True,
        env=_env(),
        timeout=120,
    )
    py = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    site = Path(_capture([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]))
    host_site = _capture(
        [
            USER_PYTHON,
            "-c",
            "import matplotlib, os; print(os.path.dirname(os.path.dirname(matplotlib.__file__)))",
        ]
    )
    # 科学栈借宿主的（.pth），venv 自己的 site-packages 里另放一个模块
    (site / "host.pth").write_text(host_site + "\n", encoding="utf-8")
    (site / "venvmod.py").write_text("X = 1\n", encoding="utf-8")
    _run_safe_worker(py, install, proj, "main", tmp_path, _env())
    pycs = _pycs(proj)
    assert [p for p in pycs if "venvmod" in p], pycs
    assert [p for p in pycs if not p.startswith(".venv")] == [], pycs


def test_the_environment_probe_writes_no_bytecode_into_the_install_dir(install, monkeypatch):
    """体检按文件执行 `worker.py`（`spec_from_file_location`）——SourceFileLoader 会把
    `engine/__pycache__/worker.cpython-3xx.pyc` 写在源码旁边，这正是 QA 复核里那一个。"""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(projectenv, "ENGINE_DIR", install / "tavotto" / "engine")
    info = projectenv.probe_environment(USER_PYTHON)
    assert info["ok"] is True and info["tavotto_worker_ok"] is True, info
    assert _pycs(install) == []


def test_target_interpreter_analysis_writes_no_bytecode_into_the_install_dir(install, project):
    """`analyze_in_interpreter` 在目标解释器里 `from tavotto.engine import discover`。
    引擎目录由 `Path(__file__)` 定死，这里用同一份 `_TARGET_SRC`、同一组解释器参数跑拷贝。"""
    proc = subprocess.run(
        [
            USER_PYTHON,
            *discover.TARGET_PARSE_ARGS,
            "-c",
            discover._TARGET_SRC,
            str(install / "tavotto" / "engine"),
            str(project / "fig.py"),
            str(project),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "info" in json.loads(proc.stdout)
    assert _pycs(install) == []


def test_native_bridge_runner_writes_no_bytecode_into_the_install_dir(install, project, tmp_path):
    """native 档：解释器**不加任何标志**（`bridge_argv`），跑到屏障 = 两阶段引擎模块全部装完。"""
    report = tmp_path / "report.json"
    r = bridgekit.run_runner(
        USER_PYTHON,
        install / "tavotto" / "engine" / "bridge_runner.py",
        target=project / "fig.py",
        cwd=str(project),
        report=report,
        out_dir=tmp_path / "bridge-out",
        env=_env(),
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert [f["stem"] for f in json.loads(report.read_text(encoding="utf-8"))["figures"]] == ["fig"]
    assert _pycs(install) == []
    _assert_user_side_still_cached(project)
