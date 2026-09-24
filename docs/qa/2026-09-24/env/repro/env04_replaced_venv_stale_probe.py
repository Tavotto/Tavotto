"""最小复现（ENV-04）：项目 `.venv` 在**同一路径**被重建成没有 matplotlib 的环境后，同一后端进程里
旧的「健康」体检结论被沿用——重建会话时直接在坏环境里起 worker，以 `session_dead` 收场；准备计划仍写
`discovery.ok = true`、`invalidated = null`。重启进程后才重新体检、作废自动决定（`invalidated.reason =
no_matplotlib`）。

    PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1 /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/env/repro/env04_replaced_venv_stale_probe.py --out <scratch dir>

进程内复现（不起 HTTP）：`pool.build` 两次，中间把 `.venv` 换掉并 `pool.invalidate()`（= 界面「重新构建」）。
合同：spec §3 ENV-04「同一路径替换 Python / 依赖后重跑：旧探测 / worker 失效并重新验证」；ADR 0057 §一
「自动记住的失效 → 作废并记 invalidated_decision()，继续第 4 条」。
退出码：0 = 符合合同；1 = 复现到偏离。
"""

from __future__ import annotations

import os
import tempfile

# 绝不碰真实用户数据 / 配置目录：import tavotto 之前就钉到临时目录（没显式给的话）
for _var in ("TAVOTTO_DATA_DIR", "TAVOTTO_CONFIG_DIR"):
    os.environ.setdefault(_var, tempfile.mkdtemp(prefix=f"qa-env-{_var.lower()}-"))
os.environ["TAVOTTO_NO_TELEMETRY"] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

WT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(WT / "tests"))

from support import venvfixture  # noqa: E402
from tavotto.engine import pool, projectenv  # noqa: E402

SCRIPT = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\nax.plot([0, 9, 2, 10])\nfig.savefig('figure.pdf')\n"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).absolute() / "env04_stale_probe"
    shutil.rmtree(out, ignore_errors=True)
    root = out / "proj"
    root.mkdir(parents=True)
    (root / "figure.py").write_text(SCRIPT, encoding="utf-8")
    host = pool.find_worker_python()
    venvfixture.make_project_venv(root, ".venv", python=host)
    steps: dict = {}
    w, _ = pool.build("figure.py", str(root), "__main__")
    steps["first_build"] = {"python_source": w.python_source}
    # 用户在终端里把 .venv 删了重建（同一路径），新环境里没有 matplotlib
    pool.shutdown_all(wait=True)
    shutil.rmtree(root / ".venv")
    subprocess.run([host, "-m", "venv", str(root / ".venv")], check=True, timeout=300)
    pool.invalidate("figure.py", str(root))  # 界面上的「重新构建」
    try:
        w2, _ = pool.build("figure.py", str(root), "__main__")
        steps["second_build"] = {"ok": True, "python_source": w2.python_source}
    except pool.WorkerError as exc:
        steps["second_build"] = {"ok": False, "code": exc.code, "message": str(exc)[:200]}
    steps["invalidated_decision"] = pool.invalidated_decision(root)
    steps["first_open_outcome_ok"] = (pool.first_open_outcome(root) or {}).get("ok")
    pool.shutdown_all(wait=True)
    # 对照：进程内缓存清掉（等价于重启后端）
    projectenv.reset_cache()
    pool.reset_worker_python()
    try:
        pool.build("figure.py", str(root), "__main__")
        steps["after_reset"] = {"ok": True}
    except pool.WorkerError as exc:
        steps["after_reset"] = {"ok": False, "code": exc.code}
    steps["after_reset_invalidated_decision"] = pool.invalidated_decision(root)
    pool.shutdown_all(wait=True)
    print(json.dumps(steps, indent=2, ensure_ascii=False, default=str))
    ok = steps["second_build"].get("code") != "session_dead" and bool(steps["invalidated_decision"])
    print(
        "CONTRACT_OK"
        if ok
        else "DEVIATION: same-process rebuild reused the stale health verdict "
        f"(second_build={steps['second_build'].get('code')!r}, invalidated_decision={steps['invalidated_decision']!r})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
