"""首开环境选择的**执行身份**：同名包、两个真实 venv、PATH 上更靠前的那个不许赢（QA 2026-09-24 §3 ENV-02 / ENV-03）。

`test_first_open_environment.py` 钉的是 `resolve_worker_python` 给出的**决策**与 worker 自报的 prefix；
这里补两件它没量的事，主语都是「真正跑脚本的那个进程 import 到了**哪一份**同名包、画出了**哪一串**数」：

| 场景 | 预期 |
|---|---|
| 项目 `.venv`（A）与 PATH 更靠前的 venv（B）各装一份 `qa_probe_pkg`，返回不同数值 | 选 A；worker 里 `qa_probe_pkg.__file__` 在 A、图里完整点序列 = A 的 `[0, 9, 2, 10]`（不是 B 的 `[1, 1, 1, 1]`；极值 / 长度都不同，import 成功不算数） |
| 环境变量点名一条没有 matplotlib 的解释器 → 停下；用户清掉它 | 先 `explicit_python_unusable`、一行脚本不跑；清掉后（同一进程）首开选 A，点序列 = A |

两个 venv 都用 `support.venvfixture.make_project_venv`（接宿主的 matplotlib，一个字节不下载）；同名包直接写进
各自的 site-packages（含 dist-info），harness 不设 `TAVOTTO_WORKER_PYTHON`、不 remember（spec §3）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from support import venvfixture
from tavotto.engine import pool as engine_pool, projectenv

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

VALUES = {"A": [0, 9, 2, 10], "B": [1, 1, 1, 1]}

SCRIPT = (
    "import json, sys\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "import qa_probe_pkg\n"
    "values = qa_probe_pkg.value()\n"
    "fig, ax = plt.subplots()\n"
    "ax.plot(range(len(values)), values)\n"
    "ax.set_title(json.dumps({'values': values, 'pkg': qa_probe_pkg.__file__, 'prefix': sys.prefix}))\n"
    "fig.savefig('figure.pdf')\n"
)


@pytest.fixture(autouse=True)
def _clean():
    projectenv.reset_cache()
    engine_pool.reset_worker_python()
    yield
    engine_pool.shutdown_all(wait=True)
    projectenv.reset_cache()
    engine_pool.reset_worker_python()


def _site_packages(venv: Path) -> Path:
    python = projectenv.interpreter_of(venv)
    out = subprocess.run(
        [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return Path(out.stdout.strip().splitlines()[-1])


def _install_probe_package(venv: Path, variant: str) -> Path:
    """把 `qa_probe_pkg`（带 dist-info）写进这个 venv 自己的 site-packages；回包的 `__init__.py`。"""
    site = _site_packages(venv)
    pkg = site / "qa_probe_pkg"
    pkg.mkdir(parents=True)
    init = pkg / "__init__.py"
    init.write_text(f"def value():\n    return {VALUES[variant]!r}\n", encoding="utf-8")
    dist = site / "qa_probe_pkg-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: qa-probe-pkg\nVersion: 1.0\n", encoding="utf-8"
    )
    return init


def _import_from(python: str, env: dict | None = None) -> dict:
    """独立探针：这个解释器 import 到的那份包与它的值（不经产品代码）。"""
    out = subprocess.run(
        [
            python,
            "-c",
            "import json, qa_probe_pkg; print(json.dumps({'file': qa_probe_pkg.__file__, 'value': qa_probe_pkg.value()}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
        env=env,
        cwd=os.path.sep,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _two_envs(tmp_path: Path, monkeypatch) -> tuple[Path, dict, dict]:
    """项目 A（`.venv`）+ 项目外的 B（PATH 最前）。回 (项目根, A 的真值, B 的真值)。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figure.py").write_text(SCRIPT, encoding="utf-8")
    venv_a = venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    venv_b = venvfixture.make_project_venv(tmp_path / "other", ".venv", python=WORKER_PY)
    init_a = _install_probe_package(venv_a, "A")
    init_b = _install_probe_package(venv_b, "B")
    py_b = projectenv.interpreter_of(venv_b)
    monkeypatch.setenv("PATH", str(Path(py_b).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv(engine_pool.WORKER_PYTHON_ENV, raising=False)
    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    # 前提（否则「选对」恒真）：两份都 import 得到，且 PATH 上第一个 python 拿到的是 B
    truth_a = _import_from(projectenv.interpreter_of(venv_a))
    truth_b = _import_from(py_b)
    assert truth_a == {"file": str(init_a), "value": VALUES["A"]}
    assert truth_b == {"file": str(init_b), "value": VALUES["B"]}
    on_path = _import_from("python", env=dict(os.environ))
    assert Path(on_path["file"]).resolve() == init_b.resolve(), on_path
    return root, truth_a, truth_b


def _plotted(worker) -> dict:
    """图里写下的东西（标题 JSON）：完整点序列 + worker 里 import 到的包文件 + sys.prefix。"""
    resp = worker.override("figure", [])
    title = next(e for e in resp["manifest"]["elements"] if e["role"] == "title")
    text = next(f["value"] for f in title["editable"] if f["prop"] == "text")
    return json.loads(text)


@needs_worker
def test_the_project_venv_wins_over_a_same_name_package_earlier_on_path(tmp_path, monkeypatch):
    root, truth_a, truth_b = _two_envs(tmp_path, monkeypatch)
    python, source = engine_pool.resolve_worker_python(str(root), script="figure.py")
    assert source == engine_pool.SOURCE_PROJECT_VENV
    worker, resp = engine_pool.build("figure.py", str(root), "__main__")
    plotted = _plotted(worker)
    # 全序列比较：B 的 [1, 1, 1, 1] 与 A 的极值 / 均值都不同，但判据不靠它们
    assert plotted["values"] == truth_a["value"] != truth_b["value"]
    assert Path(plotted["pkg"]).resolve() == Path(truth_a["file"]).resolve()
    assert Path(plotted["prefix"]).resolve() == (root / ".venv").resolve()
    assert Path(resp["runtime"]["prefix"]).resolve() == (root / ".venv").resolve()


@needs_worker
def test_an_unusable_explicit_interpreter_stops_and_clearing_it_recovers_the_project_venv(
    tmp_path, monkeypatch
):
    root, truth_a, _ = _two_envs(tmp_path, monkeypatch)
    bare = tmp_path / "bare"
    subprocess.run([WORKER_PY, "-m", "venv", str(bare)], check=True, timeout=300)
    bare_py = projectenv.interpreter_of(bare)
    assert subprocess.run([bare_py, "-c", "import matplotlib"], capture_output=True).returncode != 0
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, bare_py)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("figure.py", str(root), "__main__")
    assert err.value.code == engine_pool.EXPLICIT_UNUSABLE_CODE
    assert err.value.explicit == {
        "source": engine_pool.SOURCE_ENV,
        "python": bare_py,
        "reason": "no_matplotlib",
    }
    assert not engine_pool._workers  # 一行脚本都没跑
    assert projectenv.remembered(root) is None  # 没有趁机采用项目 venv
    # 用户纠正：清掉环境变量（同一进程）——首开选 A，画出来的是 A 的完整点序列
    monkeypatch.delenv(engine_pool.WORKER_PYTHON_ENV)
    engine_pool.reset_worker_python()
    worker, _ = engine_pool.build("figure.py", str(root), "__main__")
    assert worker.python_source == engine_pool.SOURCE_PROJECT_VENV
    plotted = _plotted(worker)
    assert plotted["values"] == truth_a["value"]
    assert Path(plotted["pkg"]).resolve() == Path(truth_a["file"]).resolve()
