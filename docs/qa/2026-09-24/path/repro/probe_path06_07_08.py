"""探针：PATH-06（沙盒里 exists 先判再读）/ PATH-07（逃出项目的软链接 + 根上同名）/ PATH-08（删数据后导出）
在真实入口下的实际响应。打印 JSON，供台账引用。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/probe_path06_07_08.py <worker-python>
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


def brief(state: dict) -> dict:
    r = state["result"]
    return {
        "status": r["status"],
        "error": r.get("error"),
        "required_input": r.get("required_input"),
        "receipt_inputs": (r.get("receipt") or {}).get("inputs"),
        "note": r.get("note"),
    }


out: dict = {}
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p678-"))

# ---- PATH-06
paper, truth = t._d1(tmp / "p06")
a = paper / "analysis"
a.mkdir()
(a / "points.csv").write_bytes(t._csv(t.TRUE_Y))
(a / "probe_read.py").write_text(t.PROBE_THEN_READ, encoding="utf-8")
t._native(paper, "probe_read.py", tmp, cwd=a)
with fa.running_app(paper, tmp / "w06") as app:
    panel = t._panel(app, "analysis/probe_read.pdf")
    st = app.prepare(panel["id"])
    out["path06_sandbox_prepare"] = brief(st)
    try:
        r = app.render(panel["id"])
        out["path06_sandbox_render"] = {
            "title": t._title_text(r),
            "plotted_y": t._plotted_y(r),
        }
    except fa.HttpError as exc:
        out["path06_sandbox_render"] = {"http": exc.status, "body": exc.body}
    wlog = sorted((tmp / "w06" / "data").rglob("worker*.log"))
    out["path06_worker_logs"] = [
        p.read_text("utf-8", "replace")[-1500:]
        for p in wlog
        if "probe" in p.read_text("utf-8", "replace")
    ][:2]

# ---- PATH-07
paper, truth = t._d1(tmp / "p07")
ext = tmp / "outside07"
ext.mkdir()
(ext / "abs_points.csv").write_bytes(t._csv(t.EXTERNAL_Y))
a = paper / "analysis"
a.mkdir()
os.symlink(ext / "abs_points.csv", a / "link_out.csv")
(a / "link_out.py").write_text(
    t._plot_script("_read_csv('link_out.csv')", "link_out.pdf"), encoding="utf-8"
)
t._native(paper, "link_out.py", tmp, cwd=a)
with fa.running_app(paper, tmp / "w07a") as app:
    panel = t._panel(app, "analysis/link_out.pdf")
    st = app.prepare(panel["id"])
    out["path07_no_root_copy_prepare"] = brief(st)
    out["path07_no_root_copy_evidence"] = st["plan"]["workdir_decision"]
(paper / "link_out.csv").write_bytes(t._csv(t.DECOY_Y))
with fa.running_app(paper, tmp / "w07b") as app:
    panel = t._panel(app, "analysis/link_out.pdf")
    st = app.prepare(panel["id"])
    out["path07_with_root_same_name_prepare"] = brief(st)
    out["path07_with_root_same_name_evidence"] = st["plan"]["workdir_decision"]

# ---- PATH-08
paper, truth = t._d1(tmp / "p08", decoy=True)
t._native(paper, "scripts/entry.py", tmp)
with fa.running_app(paper, tmp / "w08") as app:
    panel = t._panel(app, "entry.pdf")
    app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
    st = app.prepare(panel["id"])
    render = app.render(panel["id"])
    title = next(e for e in render["manifest"]["elements"] if e["role"] == "title")
    (paper / "data" / "points.csv").unlink()
    app.call("/api/engine/invalidate", {"id": panel["id"]})
    st = app.prepare(panel["id"])
    out["path08_after_delete_prepare"] = brief(st)
    for label, overrides in (
        ("no_overrides", []),
        ("with_edit", [{"gid": title["gid"], "prop": "text", "value": "edit"}]),
    ):
        try:
            _, body = app.call(
                "/api/export",
                {
                    "scope": "original",
                    "filename": f"p08-{label}",
                    "formats": ["pdf"],
                    "overwrite": "replace",
                    "original": {
                        "figure_id": panel["id"],
                        "overrides": overrides,
                        "source_kind": "figure",
                        "w_mm": 81.28,
                        "h_mm": 60.96,
                    },
                },
                timeout=300,
            )
            rec = {"status": body.get("status"), "error": body.get("error")}
            if body.get("status") == "done":
                o = body["outputs"][0]
                pdf = (Path(body["export_dir"]) / o["name"]).read_bytes()
                rec["series"] = t._exported_series(pdf)
                rec["manifest_keys"] = sorted(o.get("manifest") or {})
                mani = o.get("manifest") or {}
                rec["manifest_source"] = {
                    k: mani.get(k) for k in ("source", "verdict", "checks", "execution", "facts")
                }
            out[f"path08_export_{label}"] = rec
        except fa.HttpError as exc:
            out[f"path08_export_{label}"] = {"http": exc.status, "body": exc.body}

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
