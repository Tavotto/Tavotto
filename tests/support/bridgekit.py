"""native bridge 用例的公共助手（Session 8 technical spike，ADR 0020）。

放在 `tests/support/` 而不是 `tests/bridge/conftest.py`：仓库里已有
`from support import frontend_schema` 这条惯例（`tests/` 在 pytest 的
sys.path 上），而两个同名 `conftest` 模块之间的相对 import 在无 `__init__.py`
的布局下不成立。夹具留在 `tests/bridge/conftest.py`，这里只放纯函数。

**这些用例大量真起子进程**：native bridge 的全部主张——用户自己的解释器、
用户的 cwd/argv/env、不污染 import 命名空间、不提前 import pyplot、
stdout 归用户——只有在真进程里才成立得了或塌得掉。mock 出来的绿是假绿。
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    USER_PYTHON = pool.find_worker_python()
except pool.WorkerError:  # pragma: no cover - 取决于开发机
    USER_PYTHON = None

needs_user_python = pytest.mark.skipif(
    USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def child_env(extra: dict | None = None) -> dict:
    """子进程环境：继承当前环境，但**洗掉 `PYTHONPATH`**。

    开发机上 pytest 自己带着 `PYTHONPATH=src`，子进程继承之后
    `import tavotto` 就成立了——那正是 ADR 0020 §3「用户环境不装 Tavotto」
    要证明的反面。native 继承环境是**设计**（不是疏漏），所以洗环境这件事
    由用例做，不由 bridge 做。
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if extra:
        env.update(extra)
    return env


def run_runner(
    python: str,
    runner_py,
    *,
    target,
    target_kind="script",
    argv=(),
    cwd=None,
    report=None,
    out_dir=None,
    env=None,
    timeout=300,
):
    """直接 spawn bridge_runner（**不建控制通道**），返回 CompletedProcess。

    `--report` 形态：跑一遍、写小结、退出。对拍与捕获用例走这条——不需要
    控制通道的时候就不建，屏障会立刻返回。
    """
    cmd = [python, str(runner_py), "--target-kind", target_kind, "--target", str(target)]
    if out_dir:
        cmd += ["--out-dir", str(out_dir)]
    if report:
        cmd += ["--report", str(report)]
    cmd.append("--")
    cmd += [str(a) for a in argv]
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=child_env() if env is None else env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
