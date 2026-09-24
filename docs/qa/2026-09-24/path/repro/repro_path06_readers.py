"""PATH-06 三种原生读取器（pandas.read_csv / numpy.loadtxt / h5py.File）在默认沙盒与「脚本目录」下的实际结果。

项目自带 `.venv`（首开第 4 条：项目 venv 发现，harness 不设 TAVOTTO_WORKER_PYTHON）由
`uv venv` + `uv pip install --offline matplotlib numpy pandas h5py` 现建（只用本机 uv 缓存，一个字节不下载；
没有缓存就 exit 3 = not_run）。每个读取器一份脚本、直接读同目录的数据（不先 exists）；另一份先
exists/glob/listdir 再三种读取。输出 JSON：每个脚本在两种模式下的准备终局 / 渲染 code / 全点序列。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path06_readers.py <base-python>
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
if not UV:
    print("not_run: 没有 uv")
    sys.exit(3)

tmp = Path(tempfile.mkdtemp(prefix="qa-path-p06readers-"))
paper, truth = t._d1(tmp)
venv = paper / ".venv"
subprocess.run([UV, "venv", "--python", BASE, str(venv)], check=True, capture_output=True)
vpy = str(venv / "bin" / "python")
inst = subprocess.run(
    [UV, "pip", "install", "--offline", "--python", vpy, "matplotlib", "numpy", "pandas", "h5py"],
    capture_output=True,
    text=True,
)
if inst.returncode != 0:
    print("not_run: 离线缓存装不齐\n" + inst.stderr[-1500:])
    sys.exit(3)
versions = subprocess.run(
    [
        vpy,
        "-c",
        "import sys, matplotlib, numpy, pandas, h5py, json; print(json.dumps({'python': sys.version.split()[0], "
        "'matplotlib': matplotlib.__version__, 'numpy': numpy.__version__, 'pandas': pandas.__version__, "
        "'h5py': h5py.__version__, 'hdf5': h5py.version.hdf5_version}))",
    ],
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()

a = paper / "analysis"
a.mkdir()
(a / "points.csv").write_bytes(t._csv(t.TRUE_Y))
subprocess.run(
    [
        vpy,
        "-c",
        "import h5py, numpy as np; f = h5py.File('points.h5', 'w'); "
        f"f['x'] = np.array({t.X}, dtype='f8'); f['y'] = np.array({t.TRUE_Y}, dtype='f8'); f.close()",
    ],
    cwd=a,
    check=True,
)
READERS = {
    "rd_pandas": "(lambda d: (list(d['x']), list(d['y'])))(__import__('pandas').read_csv('points.csv'))",
    "rd_numpy": "np.loadtxt('points.csv', delimiter=',', skiprows=1).T",
    "rd_h5py": "(lambda f: (f['x'][:], f['y'][:]))(__import__('h5py').File('points.h5', 'r'))",
}
for name, expr in READERS.items():
    (a / f"{name}.py").write_text(t._plot_script(expr, f"{name}.pdf"), encoding="utf-8")
PROBE_ALL = (
    "import glob, os, sys\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "import numpy as np, pandas as pd, h5py\n"
    "e = os.path.exists('points.csv') and os.path.exists('points.h5')\n"
    "g = len(glob.glob('points.*')); l = sorted(x for x in os.listdir('.') if x.startswith('points.'))\n"
    "print(f'probe exists={e} glob={g} listdir={l}', flush=True)\n"
    "if not e:\n    sys.exit(0)\n"
    "d = pd.read_csv('points.csv'); n = np.loadtxt('points.csv', delimiter=',', skiprows=1)\n"
    "with h5py.File('points.h5', 'r') as f:\n    h = f['y'][:]\n"
    "same = list(d['y']) == list(n[:, 1]) == list(h)\n"
    "fig, ax = plt.subplots(figsize=(3.2, 2.4))\nax.plot(n[:, 0], h, 'o-')\n"
    "ax.set_title(f'same={same} glob={g}')\nfig.savefig('probe_all.pdf')\n"
)
(a / "probe_all.py").write_text(PROBE_ALL, encoding="utf-8")
for name in [*READERS, "probe_all"]:
    subprocess.run(
        [vpy, f"{name}.py"],
        cwd=a,
        check=True,
        capture_output=True,
        env={"MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp / "mpl"), "PATH": "/usr/bin:/bin"},
    )

results: dict = {"project_venv_versions": json.loads(versions)}
with fa.running_app(paper, tmp / "work") as app:
    _, envst = app.call("/api/engine/environment", timeout=60)
    results["environment_source"] = envst["project"].get("source")
    for mode in ("sandbox_default", "project"):
        if mode == "project":
            app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        for name in [*READERS, "probe_all"]:
            pid = t._panel(app, f"analysis/{name}.pdf")["id"]
            st = app.prepare(pid)
            r = st["result"]
            rec = {
                "prepare": r["status"],
                "error": r["error"],
                "verdict": (st["plan"]["workdir_decision"].get("evidence") or {}).get("verdict"),
                "inputs": [
                    f["path"]
                    for f in ((r.get("receipt") or {}).get("inputs") or {}).get("files", [])
                ],
            }
            try:
                rend = app.render(pid)
                rec["plotted_y"] = [round(v, 3) for v in t._plotted_y(rend)]
                rec["title"] = t._title_text(rend)
                rec["matches_truth"] = all(
                    abs(p - q) < 0.02 for p, q in zip(rec["plotted_y"], t.TRUE_Y)
                )
            except fa.HttpError as exc:
                rec["render_code"] = exc.body.get("code")
                rec["render_error"] = (exc.body.get("error") or "")[:200]
                rec["traceback_tail"] = (exc.body.get("traceback") or "")[-300:]
            results[f"{mode}:{name}"] = rec
print(json.dumps(results, ensure_ascii=False, indent=1))
