# 独立交叉：不看 manifest anchor，改看 worker 返回 SVG 里标题 <g id> 的 transform/文本坐标 + manifest bbox
import json, sys, re, shutil
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tavotto.engine import pool
src = Path(sys.argv[2]).parent / "docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py"
ns = {}
exec(compile(src.read_text().split("def by_gid")[0], "x", "exec"), ns)
work = Path(sys.argv[1]) / "xcheck"; shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
(work / "fig_repro.py").write_text(ns["SCRIPT"])
w = pool.one_shot("fig_repro.py", str(work), "main"); w.ensure_built()
try:
    for stem in ("TNone", "TConstrained"):
        r0 = w.override(stem, [])
        e0 = {e["gid"]: e for e in r0["manifest"]["elements"]}["axes_0.title"]
        print(stem, "keys", sorted(r0.keys()), sorted(e0.keys()))
        t = [round(e0["anchor"][0] - 0.05, 4), round(e0["anchor"][1] + 0.04, 4)]
        r1 = w.override(stem, [{"gid": "axes_0.title", "prop": "pos_frac", "value": t}])
        e1 = {e["gid"]: e for e in r1["manifest"]["elements"]}["axes_0.title"]
        print(stem, "bbox0", e0.get("bbox"), "bbox1", e1.get("bbox"))
        for k in ("svg",):
            if k in r1:
                s0, s1 = r0[k], r1[k]
                def grab(s):
                    i = s.find('id="axes_0.title'); return s[i:i+400] if i >= 0 else None
                print(" svg0", (grab(s0) or "")[:300]); print(" svg1", (grab(s1) or "")[:300])
finally:
    pool.discard(w)
