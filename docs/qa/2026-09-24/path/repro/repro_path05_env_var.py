"""PATH-05 附加：脚本按**环境变量**找数据文件（`os.environ['QA_D1_DATA']`），变量设在启动 Tavotto 的那个进程上
（用户在终端里 `export` 后启动）。记录 safe worker 是否看得到它、值是否原样、重放是否一致。只观测、不判缺陷：
safe 档的 `spec.env` 只存注入增量，是否继承启动进程的环境 ADR 0014 没有承诺——结果进台账当「行为记录」。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path05_env_var.py <worker-python>
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "tests"))

import test_first_open_paths_d1 as t  # noqa: E402
from support import foundation_app as fa  # noqa: E402

t.WORKER_PY = sys.argv[1] if len(sys.argv) > 1 else t.WORKER_PY
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p05env-"))
paper, truth = t._d1(tmp)
data = tmp / "环境 变量 数据" / "points env.csv"
data.parent.mkdir()
data.write_bytes(t._csv(t.EXTERNAL_Y))
(paper / "scripts" / "envread.py").write_text(
    t._plot_script(
        "_read_csv(os.environ.get('QA_D1_DATA', 'missing-env.csv'))",
        "envread.pdf",
        title="'env=' + str('QA_D1_DATA' in os.environ)",
    ),
    encoding="utf-8",
)
import subprocess  # noqa: E402

subprocess.run(
    [t.WORKER_PY, "scripts/envread.py"],
    cwd=paper,
    check=True,
    capture_output=True,
    env={
        "PATH": "/usr/bin:/bin",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(tmp / "mpl"),
        "QA_D1_DATA": str(data),
    },
)
out: dict = {}
with fa.running_app(paper, tmp / "work", env_overrides={"QA_D1_DATA": str(data)}) as app:
    pid = t._panel(app, "envread.pdf")["id"]
    for label in ("first", "replay"):
        if label == "replay":
            app.call("/api/engine/invalidate", {"id": pid})
        st = app.prepare(pid)
        rec = {"status": st["result"]["status"], "error": st["result"]["error"]}
        try:
            r = app.render(pid)
            rec["title"] = t._title_text(r)
            rec["plotted_y"] = [round(v, 3) for v in t._plotted_y(r)]
        except fa.HttpError as exc:
            rec["render"] = exc.body.get("code")
        out[label] = rec
print(json.dumps(out, ensure_ascii=False, indent=1))
