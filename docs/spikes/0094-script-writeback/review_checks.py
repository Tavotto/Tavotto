"""ADR 0094 spike：Codex #667 第一轮两条 P2 的看护（纯 matplotlib，不 import Tavotto）。

1. registry  两份写回过的脚本在同一个 Python 进程里跑（notebook、一个脚本 import 另一个）：
             各自的调整都要生效；两份都登记了同一个 stem 时，各自 savefig 只用自己那一份；
             同一个脚本跑第二遍不叠加、不失效。
2. legend_once  路线 2(b) 的源码层精修：唯一那处 legend() 在一个被调用两次的函数里 → 必须拒改；
                同样的函数只调一次 → 可以改（反方向那条边，防止判据恒拒）。

用法：python review_checks.py [--impl 目录]   （--impl 指向另一份 emit.py / legend_ast.py 做反证）
全过退出码 0，否则 1。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import sys
import tempfile
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402

META = {"version": "check", "date": "2026-09-28", "backup": "<check>"}

SCRIPT = """import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], color="#000000", label="curve")
    fig.savefig("{stem}.pdf")
    fig2, ax2 = plt.subplots()
    ax2.plot([0, 1], [1, 0], color="#000000", label="curve")
    fig2.savefig("{own}.pdf")
    return fig, fig2
"""

LEGEND_HELPER = """import matplotlib.pyplot as plt


def panel(i):
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label="a")
    ax.legend(loc="upper left")
    fig.savefig(f"p{{i}}.pdf")


def main():
    for i in range({calls}):
        panel(i)
"""


def _block(emit, stem_colors: dict[str, str]):
    els = [
        {"gid": "axes_0.lines_0", "role": "line", "editable": [{"prop": "label", "value": "curve"}]}
    ]
    stems = {
        s: [{"gid": "axes_0.lines_0", "prop": "color", "value": c}] for s, c in stem_colors.items()
    }
    b, _ = emit.build_block(stems, {s: {"elements": els} for s in stems}, META)
    return b


def check_registry(emit, tmp: Path) -> list[str]:
    errs = []
    # 恢复干净的 Figure.savefig（别的检查可能装过钩子）
    real = matplotlib.figure.Figure.__dict__["savefig"]
    for attr in ("_tavotto_adjust_registry",):
        if attr in matplotlib.figure.Figure.__dict__:
            delattr(matplotlib.figure.Figure, attr)
    captured = {}

    def capture(self, fname, *a, **k):
        captured[os.path.splitext(os.path.basename(os.fspath(fname)))[0]] = self

    matplotlib.figure.Figure.savefig = capture
    try:
        paths = {}
        for name, shared_color, own_color in (
            ("a", "#aa0000", "#00aa00"),
            ("b", "#0000aa", "#aaaa00"),
        ):
            src = SCRIPT.format(stem="shared", own=f"own_{name}").encode()
            blk = _block(emit, {"shared": shared_color, f"own_{name}": own_color})
            p = tmp / f"script_{name}.py"
            p.write_bytes(emit.insert_block(src, blk))
            paths[name] = (p, shared_color, own_color)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for rnd in (1, 2):  # 第二遍：同一个进程里重跑两份脚本
                for name, (p, shared_color, own_color) in paths.items():
                    captured.clear()
                    ns = runpy.run_path(str(p), run_name=f"script_{name}")
                    fig, fig2 = ns["main"]()
                    got_shared = to_hex(fig.axes[0].lines[0].get_color())
                    got_own = to_hex(fig2.axes[0].lines[0].get_color())
                    if got_shared != shared_color:
                        errs.append(
                            f"第 {rnd} 遍 script_{name} 的共用 stem 得到 {got_shared}，应为 {shared_color}"
                        )
                    if got_own != own_color:
                        errs.append(
                            f"第 {rnd} 遍 script_{name} 自己的 stem 得到 {got_own}，应为 {own_color}"
                        )
    finally:
        matplotlib.figure.Figure.savefig = real
        if "_tavotto_adjust_registry" in matplotlib.figure.Figure.__dict__:
            delattr(matplotlib.figure.Figure, "_tavotto_adjust_registry")
    return errs


def check_legend_once(legend_ast, tmp: Path) -> list[str]:
    errs = []
    for calls, should_edit in ((2, False), (1, True)):
        p = tmp / f"helper_{calls}.py"
        p.write_text(LEGEND_HELPER.format(calls=calls), encoding="utf-8")
        kwargs = {}
        if hasattr(legend_ast, "legend_call_count"):
            kwargs["executions"] = lambda lo, hi, p=p: legend_ast.legend_call_count(
                p, "main", lo, hi
            )
        try:
            legend_ast.edit_legend_fontsize(p.read_bytes(), 11.0, **kwargs)
            edited = True
        except legend_ast.NotEditable:
            edited = False
        if edited != should_edit:
            errs.append(
                f"legend() 执行 {calls} 次时 {'改了' if edited else '没改'}（应{'改' if should_edit else '拒'}）"
            )
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    sys.path.insert(0, a.impl)
    for m in ("emit", "legend_ast", "run_spike"):
        sys.modules.pop(m, None)
    emit = importlib.import_module("emit")
    # legend_ast import 了 run_spike → tavotto；这里只用它的纯函数，给一个不连 Tavotto 的替身
    sys.modules.setdefault("run_spike", type(sys)("run_spike"))
    tav = type(sys)("tavotto")
    tav_engine = type(sys)("tavotto.engine")
    tav_engine.pool = None
    sys.modules.setdefault("tavotto", tav)
    sys.modules.setdefault("tavotto.engine", tav_engine)
    legend_ast = importlib.import_module("legend_ast")
    res = {}
    with tempfile.TemporaryDirectory() as t:
        res["registry"] = check_registry(emit, Path(t))
    with tempfile.TemporaryDirectory() as t:
        res["legend_once"] = check_legend_once(legend_ast, Path(t))
    bad = {k: v for k, v in res.items() if v}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
