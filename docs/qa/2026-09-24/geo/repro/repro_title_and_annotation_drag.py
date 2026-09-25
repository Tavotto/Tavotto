"""最小复现：两类「写了 pos_frac、manifest 说可拖，但落点不对」的文字。

A. 标题在 ``layout="constrained"`` / ``"tight"`` 的图里：写 pos_frac 之后 **y 纹丝不动**（x 也差 ~6e-4），
   对照组（无布局引擎 / tight）同样的写法落点精确。热会话与全新 worker 重放结果一致——不是
   时序问题，是 setter 在这种布局下不生效，且不报 warning、manifest 仍宣称 draggable。
C. 拖过的文字之后再改图幅（size_mm）：布局引擎下 figure 分数锚点漂走（无布局引擎时守住）。
B. ``ax.annotate(..., textcoords=<非 data>)`` 的注释文字：写 pos_frac 之后跑到别处
   （误差随 textcoords 而变），对照组 ``ax.text`` / textcoords='data' 精确。

判据主语：同一 worker、一次 override 之后 manifest 里目标元素的 ``anchor``（figure 分数、y 向下），
与写下的 pos_frac 之差。预算 1e-4（远宽于实测的正常误差 1e-16）。

用法（从 worktree 根目录）::

    TAVOTTO_DATA_DIR=<scratch>/data TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py <scratch_dir>

退出码：0 = 全部落点正确（缺陷已修）；1 = 复现到缺陷。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from tavotto.engine import pool

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _title(stem, **kw):
    fig, ax = plt.subplots(figsize=(5.0, 3.2), **kw)
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_title("title " + stem)
    ax.set_xlabel("x")
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _title("TNone")
    _title("TConstrained", layout="constrained")
    _title("TTight", layout="tight")
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.text(0.3, 0.8, "plain text")
    ax.annotate("ann data", xy=(1, 1), xytext=(1.5, 0.6), arrowprops={"arrowstyle": "->"})
    ax.annotate("ann axfrac", xy=(0.5, 0.5), xycoords="axes fraction", xytext=(0.2, 0.3),
                textcoords="axes fraction", arrowprops={"arrowstyle": "->"})
    ax.annotate("ann offset", xy=(0.5, 0.2), xytext=(20, 10), textcoords="offset points")
    ax.annotate("ann figfrac", xy=(0.5, 0.5), xytext=(0.7, 0.9), textcoords="figure fraction")
    fig.savefig("Ann.pdf")
    plt.close(fig)
"""


def by_gid(resp):
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def probe(w, stem, gid, d=(-0.05, 0.04), extra=()):
    base = by_gid(w.override(stem, []))[gid]
    t = [round(base["anchor"][0] + d[0], 4), round(base["anchor"][1] + d[1], 4)]
    patch = {"gid": gid, "prop": base["drag_prop"], "value": t}
    resp = w.override(stem, [patch, *extra])
    got = by_gid(resp)[gid]["anchor"]
    w.override(stem, [])
    return {
        "stem": stem,
        "gid": gid,
        "extra": [p["prop"] for p in extra],
        "label": base.get("label"),
        "draggable": base.get("draggable"),
        "drag_prop": base.get("drag_prop"),
        "unsupported_props": base.get("unsupported_props"),
        "warnings": resp.get("warnings"),
        "target": t,
        "anchor_after": got,
        "err": [got[0] - t[0], got[1] - t[1]],
    }


def main() -> int:
    work = Path(sys.argv[1]).resolve() / "repro-title-ann"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "fig_repro.py").write_text(SCRIPT, "utf-8")
    w = pool.one_shot("fig_repro.py", str(work), "main")
    rows = []
    try:
        w.ensure_built()
        size = {"gid": "figure", "prop": "size_mm", "value": [150.0, 70.0]}
        for stem in ("TNone", "TConstrained", "TTight"):
            for gid in ("axes_0.title", "axes_0.xlabel"):
                rows.append(probe(w, stem, gid))
                # C. 拖过之后再改图幅：figure 分数锚点应当不变（pos_frac 是 figure 锚定）
                rows.append(probe(w, stem, gid, extra=(size,)))
        ann = by_gid(w.override("Ann", []))
        for g, e in sorted(ann.items()):
            if e["role"] == "text" and e.get("drag_prop"):
                rows.append(probe(w, "Ann", g))
    finally:
        pool.discard(w)
    bad = 0
    for r in rows:
        ok = max(abs(r["err"][0]), abs(r["err"][1])) <= 1e-4
        bad += not ok
        print(("OK  " if ok else "BAD ") + json.dumps(r, ensure_ascii=False))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
