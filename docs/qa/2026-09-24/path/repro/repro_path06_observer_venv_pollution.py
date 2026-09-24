"""复现（PATH-06 附带发现）：项目自带 `.venv` 放在项目根里（ADR 0057 首开第 4 条的标准形态）时，默认沙盒
模式下回执 `inputs.files`（「已观察的数据身份」，ADR 0070）把 `.venv/lib/.../site-packages` 里的库文件
（.so / 字体 / mplstyle …）当成数据输入记下；上限 `INPUT_OBSERVER_MAX_FILES=256`，依赖一多，用户真正读的
数据文件可能被挤出（`truncated=True`）。同一脚本在「脚本目录」模式下只记数据文件——两种模式不一致。

期望：`.venv` 里的库文件不进 `inputs.files`；用户的数据文件总在里面。期望不成立 exit 1，成立 exit 0，
缓存装不齐 exit 3（not_run）。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path06_observer_venv_pollution.py <base-python>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "tests"))

import test_first_open_paths_d1 as t  # noqa: E402
from support import foundation_app as fa  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else sys.executable
UV = shutil.which("uv")
PKGS = ["matplotlib", "numpy", "pandas", "scipy", "seaborn"]
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p06obs-"))
paper, truth = t._d1(tmp)
venv = paper / ".venv"
if not UV:
    print("not_run: 没有 uv")
    sys.exit(3)
subprocess.run([UV, "venv", "--python", BASE, str(venv)], check=True, capture_output=True)
vpy = str(venv / "bin" / "python")
inst = subprocess.run(
    [UV, "pip", "install", "--offline", "--python", vpy, *PKGS], capture_output=True, text=True
)
if inst.returncode != 0:
    print("not_run: 离线缓存装不齐\n" + inst.stderr[-800:])
    sys.exit(3)
a = paper / "analysis"
a.mkdir()
(a / "points.csv").write_bytes(t._csv(t.TRUE_Y))
(a / "heavy.py").write_text(
    t._plot_script(
        "_read_csv('points.csv')",
        "heavy.pdf",
        pre="import pandas, scipy.stats, scipy.optimize, scipy.signal, seaborn\n",
    ),
    encoding="utf-8",
)
subprocess.run(
    [vpy, "heavy.py"],
    cwd=a,
    check=True,
    capture_output=True,
    env={"MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp / "mpl"), "PATH": "/usr/bin:/bin"},
)
out: dict = {}
with fa.running_app(paper, tmp / "work") as app:
    pid = t._panel(app, "analysis/heavy.pdf")["id"]
    for mode in ("sandbox_default", "project"):
        if mode == "project":
            app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        st = app.prepare(pid)
        inputs = (st["result"].get("receipt") or {}).get("inputs") or {}
        files = [f["path"] for f in inputs.get("files", [])]
        out[mode] = {
            "status": st["result"]["status"],
            "n_files": len(files),
            "n_venv_files": sum(1 for f in files if f.startswith(".venv/")),
            "has_data_file": "analysis/points.csv" in files,
            "truncated": inputs.get("truncated"),
            "first_5": files[:5],
        }
print(json.dumps(out, ensure_ascii=False, indent=1))
sb = out["sandbox_default"]
ok = sb["n_venv_files"] == 0 and sb["has_data_file"]
print("EXPECTED sandbox inputs has data file and no .venv entries:", "OK" if ok else "VIOLATED")
sys.exit(0 if ok else 1)
