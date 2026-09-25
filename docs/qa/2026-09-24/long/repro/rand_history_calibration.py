#!/usr/bin/env python3
"""RAND-01 新用例 `tests/test_override_history_sequences.py` 的校准与强度统计。

用**测试模块自己的** `Generator` / `History`（同一种子串 `hist:{stem}:{seed}`）在一条热 worker 上
走序列，记：
  * 动作分布（style / drag / move_axes / resize / undo / redo）；
  * 列表真的变化的步、undo/redo 回访已有状态的次数；
  * 拖动锚点残差：每一步、每一条生效的 pos_frac / loc_frac，|manifest.anchor − 声明值| 的最大值，
    分「之后发生过 move_axes / resize」与「没有」两档——ANCHOR_ATOL 的依据；
  * 被拒（warnings）步数。
种子数 `TAVOTTO_HIST_SEEDS`（默认 8）。输出 JSON。

用法（worktree 根目录）：
    PYTHONPATH=$PWD/src:$PWD/tests TAVOTTO_HIST_SEEDS=8 \\
      TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
      /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/long/repro/rand_history_calibration.py
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from collections import Counter
from pathlib import Path

import test_override_history_sequences as H
from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from tavotto.engine import pool


def main() -> int:
    n = int(os.environ.get("TAVOTTO_HIST_SEEDS", "8"))
    rep = {"seeds": n, "steps": H.STEPS, "per_stem": {}}
    with tempfile.TemporaryDirectory() as d:
        figs = Path(d)
        (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
        w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
        w.ensure_built()
        try:
            for stem in H.STEMS:
                kinds, changed, total, revisits, rejected = Counter(), 0, 0, 0, 0
                res_plain = res_geom = 0.0
                n_checks = 0
                for seed in range(n):
                    base = w.override(stem, [])["manifest"]
                    gen, h = H.Generator(random.Random(f"hist:{stem}:{seed}"), base), H.History()
                    seen, man_now = {H._k([])}, base
                    for _ in range(H.STEPS):
                        prev = h.current
                        kind = gen.step(h, man_now)
                        kinds[kind.split(":")[0]] += 1
                        total += 1
                        changed += h.current != prev
                        revisits += kind in ("undo", "redo") and H._k(h.current) in seen
                        seen.add(H._k(h.current))
                        r = w.override(stem, h.current)
                        rejected += bool(r.get("warnings"))
                        man_now = r["manifest"]
                        els = {e["gid"]: e for e in man_now["elements"]}
                        geo = any(p["prop"] in ("position", "size_mm") for p in h.current)
                        for p in h.current:
                            if p["prop"] in H._FRAC and (els.get(p["gid"]) or {}).get("anchor"):
                                a = els[p["gid"]]["anchor"]
                                dev = max(abs(a[0] - p["value"][0]), abs(a[1] - p["value"][1]))
                                n_checks += 1
                                if geo:
                                    res_geom = max(res_geom, dev)
                                else:
                                    res_plain = max(res_plain, dev)
                rep["per_stem"][stem] = {
                    "actions": dict(kinds),
                    "steps_total": total,
                    "list_changed_steps": changed,
                    "history_revisits": revisits,
                    "rejected_steps": rejected,
                    "anchor_checks": n_checks,
                    "max_anchor_residual_no_geometry": res_plain,
                    "max_anchor_residual_with_move_or_resize_active": res_geom,
                }
        finally:
            pool.discard(w)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
