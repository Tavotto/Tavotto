import sys, shutil
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tavotto.engine import pool
src = Path(sys.argv[2]).parent / "docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py"
ns = {}; exec(compile(src.read_text().split("def by_gid")[0], "x", "exec"), ns)
work = Path(sys.argv[1]) / "xcheck2"; shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
(work / "fig_repro.py").write_text(ns["SCRIPT"])
w = pool.one_shot("fig_repro.py", str(work), "main"); w.ensure_built()
d = (-0.05, 0.04)
try:
    for g in ("axes_0.texts_0", "axes_0.texts_1", "axes_0.texts_2", "axes_0.texts_3", "axes_0.texts_4"):
        e0 = {e["gid"]: e for e in w.override("Ann", [])["manifest"]["elements"]}[g]
        t = [round(e0["anchor"][0] + d[0], 4), round(e0["anchor"][1] + d[1], 4)]
        r = w.override("Ann", [{"gid": g, "prop": "pos_frac", "value": t}])
        e1 = {e["gid"]: e for e in r["manifest"]["elements"]}[g]
        sh = [e1["bbox"][0] - e0["bbox"][0], e1["bbox"][1] - e0["bbox"][1]]
        print(g, "b0", [round(x,4) for x in e0["bbox"]], "b1", [round(x,4) for x in e1["bbox"]], "a0", e0["anchor"], "a1", e1["anchor"]); print(g, e0["label"], "bbox shift", [round(x, 4) for x in sh], "requested", d, "warnings", r["warnings"])
finally:
    pool.discard(w)
