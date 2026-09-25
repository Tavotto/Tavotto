"""复现（PATH-07 附带发现）：一次失败的 build 留下的会话在证据变化后仍被渲染入口复用——准备接口说
`needs_input`（要先选运行目录），渲染入口却在旧会话（沙盒）里把脚本又跑一遍、报 `script_error`。
「四类入口同一道门」只在 `pool._new_worker()`；已经存在的会话不会因为静态证据变化而回到门前。

期望（按 ADR 0057 §三「四类入口同一个 code」）：证据变成 project_root 后，渲染也回 `workdir_confirmation_required`。
本脚本在期望不成立时 exit 1（= 缺陷仍在），成立时 exit 0。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path07_gate_vs_live_worker.py <worker-python>
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "tests"))

import test_first_open_paths_d1 as t  # noqa: E402
from support import foundation_app as fa  # noqa: E402

t.WORKER_PY = sys.argv[1] if len(sys.argv) > 1 else t.WORKER_PY
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p07gate-"))
paper, _ = t._d1(tmp)
ext = tmp / "outside"
ext.mkdir()
(ext / "abs_points.csv").write_bytes(t._csv(t.EXTERNAL_Y))
a = paper / "analysis"
a.mkdir()
os.symlink(ext / "abs_points.csv", a / "link_out.csv")
(a / "link_out.py").write_text(
    t._plot_script("_read_csv('link_out.csv')", "link_out.pdf"), encoding="utf-8"
)
t._native(paper, "link_out.py", tmp, cwd=a)
steps: list[dict] = []


def render_code(app, pid):
    try:
        app.render(pid)
        return "ok"
    except fa.HttpError as exc:
        return exc.body.get("code")


with fa.running_app(paper, tmp / "work") as app:
    pid = t._panel(app, "analysis/link_out.pdf")["id"]
    steps.append({"step": "prepare#1", "status": app.prepare(pid)["result"]["status"]})
    steps.append({"step": "render#1", "code": render_code(app, pid)})
    (paper / "link_out.csv").write_bytes(t._csv(t.DECOY_Y))
    steps.append({"step": "root same-name file appears", "status": None})
    st = app.prepare(pid)
    steps.append({"step": "prepare#2", "status": st["result"]["status"]})
    steps.append({"step": "render#2 (no invalidate)", "code": render_code(app, pid)})
    logs = sorted((tmp / "work" / "data").rglob("worker*.log"))
    runs = sum(p.read_text("utf-8", "replace").count("No such file") for p in logs)
    steps.append({"step": "worker log 'No such file' count", "count": runs})
    app.call("/api/engine/invalidate", {"id": pid})
    steps.append({"step": "render#3 (after invalidate)", "code": render_code(app, pid)})
print(json.dumps(steps, ensure_ascii=False, indent=1))
render2 = next(s for s in steps if s["step"].startswith("render#2"))["code"]
ok = render2 == "workdir_confirmation_required"
print("EXPECTED render#2 == workdir_confirmation_required:", "OK" if ok else "VIOLATED")
sys.exit(0 if ok else 1)
