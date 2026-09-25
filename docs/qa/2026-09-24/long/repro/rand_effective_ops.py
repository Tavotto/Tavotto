#!/usr/bin/env python3
"""RAND-02：`tests/test_override_sequences.py` 的随机序列**真的在做有效操作吗**。

对每个 (stem, seed) 用**测试模块自己的** `_sequence` 生成序列（同一 rng 字符串 `f"{stem}:{seed}"`），
另用一份带记账的逐字复刻重跑一遍，先断言两者产出**逐字相同**（记账版与被测生成器不同源就拒绝
报数），再在一条热 worker 上逐步应用，统计：

* 动作分布：add / remove / change / change→remove（采样不出新值被改成删）/ add 了已生效的同一组；
* 列表级 no-op：P_k == P_{k-1}（全量列表逐字相同，这一步什么都没发）；
* 有效步：与上一步相比 manifest 或预览像素（380 px PNG 的 sha1）至少一个变了；
* 被拒：`override` 响应带 warnings；
* 覆盖面：动过的 (role, prop) 数、是否碰过任何几何 / 拖动类 prop（pos_frac / loc_frac / position /
  size_mm / xlim / ylim / xscale / yscale）。

输出 JSON 到 stdout。种子数由 `TAVOTTO_SEQ_SEEDS` 决定（默认 8，与测试一致）。

用法（worktree 根目录）：
    PYTHONPATH=$PWD/src:$PWD/tests TAVOTTO_SEQ_SEEDS=32 \\
      TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
      /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/long/repro/rand_effective_ops.py
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

import test_override_sequences as T  # noqa: E402  —— 被测生成器本体
from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from support.overridesample import _sample_value
from tavotto.engine import pool

GEOMETRY = {
    "pos_frac",
    "loc_frac",
    "endpoints_frac",
    "position",
    "size_mm",
    "xlim",
    "ylim",
    "xscale",
    "yscale",
    "loc_anchor",
}


def traced_sequence(rng, candidates, fields, steps):
    """与 `T._sequence` 逐字相同的逻辑，只多记一份动作账。"""
    current: dict = {}
    out, acts = [], []
    for _ in range(steps):
        action = rng.choices(("add", "remove", "change"), weights=(5, 3, 2))[0]
        if action == "add" or not current:
            group = rng.choice(candidates)
            already = all(T._key(p) in current and current[T._key(p)] == p for p in group)
            for p in group:
                current[T._key(p)] = dict(p)
            acts.append("add_same" if already else ("add" if action == "add" else "add(forced)"))
        elif action == "remove":
            current.pop(rng.choice(list(current)), None)
            acts.append("remove")
        else:
            k = rng.choice(list(current))
            p = current[k]
            nv = _sample_value({**fields[k], "value": p["value"]})
            if nv is None or nv == p["value"]:
                current.pop(k, None)
                acts.append("change→remove")
            else:
                p["value"] = nv
                acts.append("change")
        out.append([dict(p) for p in current.values()])
    return out, acts


def main() -> int:
    n = int(os.environ.get("TAVOTTO_SEQ_SEEDS", "8"))
    with tempfile.TemporaryDirectory() as d:
        figs = Path(d)
        (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
        w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
        w.ensure_built()
        report = {"seeds": n, "steps_per_seq": T.STEPS, "per_stem": {}}
        try:
            for stem in T.STEMS:
                base = w.override(stem, [])["manifest"]
                cands, fields = T._candidates(base)
                role_of = {e["gid"]: e["role"] for e in base["elements"]}
                acts_c, touched = Counter(), set()
                noop = effective = rejected = total = 0
                geometry_hits = 0
                for seed in range(n):
                    ref = T._sequence(random.Random(f"{stem}:{seed}"), cands, fields, T.STEPS)
                    seq, acts = traced_sequence(
                        random.Random(f"{stem}:{seed}"), cands, fields, T.STEPS
                    )
                    if seq != ref:
                        print(f"记账版与被测生成器不一致：{stem} seed {seed}", file=sys.stderr)
                        return 2
                    acts_c.update(acts)
                    w.override(stem, [])
                    prev_list, prev_man = [], json.dumps(base, sort_keys=True)
                    prev_png = hashlib.sha1(
                        w.preview_png(stem, [], 380, f"q-{stem}-{seed}-b").read_bytes()
                    ).hexdigest()
                    for k, step in enumerate(seq):
                        total += 1
                        for p in step:
                            touched.add((role_of.get(p["gid"], "?"), p["prop"]))
                            geometry_hits += p["prop"] in GEOMETRY
                        if step == prev_list:
                            noop += 1
                        r = w.override(stem, step)
                        if r.get("warnings"):
                            rejected += 1
                        man = json.dumps(r["manifest"], sort_keys=True)
                        png = hashlib.sha1(
                            w.preview_png(stem, step, 380, f"q-{stem}-{seed}-{k}").read_bytes()
                        ).hexdigest()
                        if man != prev_man or png != prev_png:
                            effective += 1
                        prev_list, prev_man, prev_png = step, man, png
                report["per_stem"][stem] = {
                    "candidate_groups": len(cands),
                    "steps_total": total,
                    "actions": dict(acts_c),
                    "list_noop_steps": noop,
                    "effective_steps": effective,
                    "effective_ratio": round(effective / total, 3),
                    "rejected_steps": rejected,
                    "distinct_role_prop_touched": len(touched),
                    "roles_touched": sorted({r for r, _p in touched}),
                    "geometry_prop_occurrences": geometry_hits,
                }
        finally:
            pool.discard(w)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
