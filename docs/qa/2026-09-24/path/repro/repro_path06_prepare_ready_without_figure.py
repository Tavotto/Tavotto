"""复现（PATH-06 发现）：沙盒默认下「先 exists 再读」的脚本一张图都没捕获，准备接口却给 `ready`
（`error=None`、回执 `descriptors=[]`），只有渲染入口报 `no_figures_captured`。准备接口的 `ready` 于是不能当
「这张面板可编辑」的证据（FO-054：ready_editable 需实际图）。

期望：准备接口对请求的面板没捕获到图时不给 `ready`（给 `error` + `no_figures_captured`，与渲染同一个 code）。
期望不成立 exit 1（= 缺陷仍在），成立 exit 0。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path06_prepare_ready_without_figure.py <worker-python>
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
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p06ready-"))
paper, _ = t._d1(tmp)
a = paper / "analysis"
a.mkdir()
(a / "points.csv").write_bytes(t._csv(t.TRUE_Y))
(a / "probe_read.py").write_text(t.PROBE_THEN_READ, encoding="utf-8")
t._native(paper, "probe_read.py", tmp, cwd=a)
with fa.running_app(paper, tmp / "work") as app:
    pid = t._panel(app, "analysis/probe_read.pdf")["id"]
    st = app.prepare(pid)
    r = st["result"]
    prep = {
        "status": r["status"],
        "error": r["error"],
        "descriptors": (r.get("receipt") or {}).get("descriptors"),
        "completeness": (r.get("receipt") or {}).get("completeness"),
    }
    try:
        app.render(pid)
        rend = {"code": None}
    except fa.HttpError as exc:
        rend = {"http": exc.status, "code": exc.body.get("code")}
print(json.dumps({"prepare": prep, "render": rend}, ensure_ascii=False, indent=1))
ok = prep["status"] != "ready"
print("EXPECTED prepare.status != ready when no figure captured:", "OK" if ok else "VIOLATED")
sys.exit(0 if ok else 1)
