"""#874 问 2 最小复现：main 上只改 alpha / fill 再撤销（与纹理颜色编辑无关），热态是否 != 全新重放。

用法：
  TAVOTTO_WORKER_PYTHON=<含 matplotlib 的解释器> PYTHONPATH=$PWD/src python scripts/dev/hatchcolor_alpha_undo_repro.py
走 Tavotto 真 worker（pool.one_shot → override → preview_png，与 tests/test_hatch_editing.py 同一套入口）。

三个量（每行）：
  hot(undone)  热 worker：应用 on 列表后再提交 []（撤销）之后的像素 sha1
  fresh([])    只见最终列表 [] 的全新 worker 的像素 sha1（= 脚本原样）
  hot==fresh   不变量 HOT(P) == REPLAY(P) 在撤销点是否成立
on 列表本身两边同样重放，hot(on)==fresh(on) 恒成立（不在表里，见 --verbose）。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from tavotto.engine import pool

# Windows 上 stdout/stderr 被重定向成管道时会退回系统区域编码（cp1252/cp936），第一句中文就 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CTX = {
    "rc_red": 'with plt.rc_context({"hatch.color": "red"}):\n        {make}',
    "rc_edge": 'plt.rcParams["hatch.color"] = "red"\n    with plt.rc_context({"hatch.color": "edge"}):\n        {make}',  # 仅 3.11 合法
    "rc_rgba": 'with plt.rc_context({"hatch.color": (1, 0, 0, 0.25)}):\n        {make}',
    "control": "{make}",
}
MAKERS = {
    # (脚本里建 artist 的语句, 目标 gid)
    "bar_edge": (
        'ax.bar([0, 1], [3, 5], 0.6, facecolor="#ccc", edgecolor="#2255aa", linewidth=2, hatch="//")',
        "axes_0.barseries_0",
    ),
    "bar_noedge": (
        'ax.bar([0, 1], [3, 5], 0.6, facecolor="#ccc", hatch="//")',
        "axes_0.barseries_0",
    ),
    "bar0_noedge": (
        'ax.bar([0, 1], [3, 5], 0.6, facecolor="#ccc", hatch="//")',
        "axes_0.barseries_0.bar_0",
    ),  # 单柱 gid（有 fill）
    "rect_noedge": (
        'ax.add_patch(Rectangle((0, 0), 1, 3, facecolor="#ccc", hatch="//"))',
        "axes_0.patches_0",
    ),
}
PROPS = {"alpha": 0.5, "fill": False}
TEMPLATE = """\
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    {body}
    ax.set_xlim(-1, 3)
    ax.set_ylim(0, 7)
    fig.savefig("Fig.png")
"""


def png(w, patches, tag):
    return hashlib.sha1(w.preview_png("Fig", list(patches), 360, tag).read_bytes()).hexdigest()[:10]


def run(ctx, maker, prop, value):
    make, gid = MAKERS[maker]
    d = Path(tempfile.mkdtemp(prefix="hatch874-"))
    body = CTX[ctx].replace("{make}", make)
    (d / "fig.py").write_text(TEMPLATE.replace("{body}", body), encoding="utf-8")
    on = [{"gid": gid, "prop": prop, "value": value}]

    def worker():
        w = pool.one_shot("fig.py", str(d), "main")
        w.ensure_built()
        return w

    hot = worker()
    hot.override("Fig", on)
    hot_on = png(hot, on, "on")
    resp = hot.override("Fig", [])
    warn = bool(resp.get("warnings"))
    hot_undone = png(hot, [], "undone")
    pool.discard(hot)
    fresh = worker()
    fresh_on = png(fresh, on, "fon")
    pool.discard(fresh)
    fresh0 = worker()  # 只见 [] 的全新 worker（没经历过 on）才是「全新重放」
    fresh_off = png(fresh0, [], "foff")
    pool.discard(fresh0)
    return hot_on == fresh_on, hot_undone, fresh_off, warn


def main():
    print("| ctx | maker | prop | hot(on)==fresh(on) | hot(undone) | fresh([]) | hot==fresh |")
    print("|---|---|---|---|---|---|---|")
    for ctx in CTX:
        for maker in MAKERS:
            for prop, value in PROPS.items():
                try:
                    same_on, hu, fu, warn = run(ctx, maker, prop, value)
                except Exception as e:  # noqa: BLE001 — 例如 ≤3.10 的 rc 'edge' 不合法
                    print(f"| {ctx} | {maker} | {prop} | ERR {str(e)[:50]} ||||")
                    continue
                print(
                    f"| {ctx} | {maker} | {prop}{' (warn)' if warn else ''} | {same_on} | {hu} | {fu} | {'OK' if hu == fu else 'DIVERGE'} |"
                )


if __name__ == "__main__":
    sys.exit(main())
