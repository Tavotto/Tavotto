#!/usr/bin/env python3
"""复现：非内置解释器起 worker 时不带 -B，.pyc 被写进安装目录（macOS 上即 .app 内）。

背景（根 AGENTS.md 不变量）：运行时可写数据一律走 data_dir()，不往包/安装目录写任何东西——
macOS 上写 .app 会当场破坏代码签名。`runtime.child_args()` 的 `-B` 是这条纪律的「真正保证」，
但 `pool._spawn_spec` / `EngineWorker.__init__` 只在 `source == bundled` 时加它：
项目 .venv / 用户配置 / 系统 Python 起的 worker 直接 import 安装目录里的 engine/*.py，
并把 __pycache__ 写在那旁边。

本脚本不碰 /Applications：把工作树的 src/tavotto 拷进 scratch 下一个模拟的
`Fake.app/Contents/Resources/sidecar/Tavotto/_internal/` 布局，用**产品自己的**
`pool._spawn_spec` 产出 argv（source=system，即非内置），然后按 argv 真起 worker（stdin 关着，
它 import 完引擎模块读到 EOF 就退出），最后数模拟安装目录里新出现的 .pyc。
对照组：同一 argv 但 source=bundled（产品会加 -B）。

用法（worktree 根目录）：
    PYTHONPATH=<worktree>/src /Volumes/Projects/Tavotto/.venv/bin/python \
        docs/qa/2026-09-24/rel/repro/repro_nonbundled_worker_writes_pyc_into_install_dir.py \
        --worker-python /opt/homebrew/opt/python@3.13/libexec/bin/python3 --scratch <dir>
退出码：0 = 缺陷已复现（非内置那组写了 .pyc、bundled 组没写）；1 = 未复现；2 = 夹具错误。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[5]


def count_pyc(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.pyc"))


def run_arm(scratch: Path, worker_python: str, source: str) -> dict:
    app_internal = scratch / f"Fake-{source}.app/Contents/Resources/sidecar/Tavotto/_internal"
    if app_internal.exists():
        shutil.rmtree(app_internal.parents[3])
    app_internal.mkdir(parents=True)
    shutil.copytree(
        REPO / "src/tavotto",
        app_internal / "tavotto",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "web"),
    )
    assert not count_pyc(app_internal), "拷贝后不应有 .pyc"
    figures = scratch / f"figs-{source}"
    figures.mkdir(exist_ok=True)
    (figures / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nplt.plot([0,1],[0,1])\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TAVOTTO_DATA_DIR"] = str(scratch / "data")
    env["TAVOTTO_CONFIG_DIR"] = str(scratch / "config")
    # 用模拟安装目录里那份 tavotto 算 argv——WORKER_PY 因此指向「安装目录」里的 worker.py
    probe = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from tavotto.engine import pool\n"
        f"spec = pool._spawn_spec('fig.py', {str(figures)!r}, 'main', Path({str(scratch / 'out')!r}),"
        f" Path({str(scratch / 'sb')!r}), Path({str(scratch / 'w.log')!r}), {worker_python!r}, {source!r})\n"
        "print(json.dumps(spec))\n"
    )
    env_probe = dict(env)
    env_probe["PYTHONPATH"] = str(app_internal)
    env_probe["PYTHONDONTWRITEBYTECODE"] = "1"  # 算 argv 这一步本身不许留 .pyc
    r = subprocess.run(
        [sys.executable, "-c", probe], env=env_probe, capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit(2)
    spec = json.loads(r.stdout.strip().splitlines()[-1])
    argv = spec["argv"]
    (scratch / "out").mkdir(exist_ok=True)
    (scratch / "sb").mkdir(exist_ok=True)
    # 按 workerd/Popen 的方式起：继承环境 + 增量 env；不设 PYTHONDONTWRITEBYTECODE
    run_env = {k: v for k, v in env.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH")}
    run_env.update(spec["env"])
    w = subprocess.run(
        argv,
        env=run_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    pycs = count_pyc(app_internal)
    return {
        "source": source,
        "argv_has_-B": "-B" in argv,
        "argv_head": argv[:3],
        "worker_exit": w.returncode,
        "pyc_written_into_install_dir": len(pycs),
        "pyc_sample": pycs[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker-python", required=True)
    ap.add_argument("--scratch", required=True)
    a = ap.parse_args()
    scratch = Path(a.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    res = [run_arm(scratch, a.worker_python, s) for s in ("system", "bundled")]
    print(json.dumps(res, ensure_ascii=False, indent=2))
    nonb, bund = res
    reproduced = (
        nonb["pyc_written_into_install_dir"] > 0 and bund["pyc_written_into_install_dir"] == 0
    )
    print("REPRODUCED" if reproduced else "NOT_REPRODUCED")
    return 0 if reproduced else 1


if __name__ == "__main__":
    sys.exit(main())
