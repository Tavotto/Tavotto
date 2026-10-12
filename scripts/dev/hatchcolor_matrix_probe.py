"""#874 调研：Patch 花纹色在 rc_context 创建 × 模式 × set_alpha / 撤销 下的实测矩阵（不进产品）。

用法：<python> scripts/dev/hatchcolor_matrix_probe.py [--md]
每行：创建时 rc hatch.color / 脚本 hatchcolor 模式 / 边色 / 脚本 alpha →
  base（创建后）、alpha=0.5 之后、撤销（set_alpha(脚本 alpha)）之后的花纹 RGBA，以及 undo==base。
创建结束后环境 rc 一律回到默认（'black'），所以「重解析」会读到与创建时不同的值。
花纹色读法：3.11 用 get_hatchcolor() 的解析值与 _hatch_color 原样（'edge' 哨兵）；≤3.10 读 _hatch_color。
"""

from __future__ import annotations

import itertools
import sys

import matplotlib as mpl
from matplotlib.patches import Rectangle

# Windows 上 stdout/stderr 被重定向成管道时会退回系统区域编码（cp1252/cp936），第一句中文就 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HAS_API = hasattr(Rectangle, "set_hatchcolor")


def fmt(v):
    if isinstance(v, str):
        return v
    return "(" + ",".join(f"{x:g}" for x in v) + ")"


def read(p):
    raw = p._hatch_color  # noqa: SLF001
    if HAS_API:
        return fmt(raw) if isinstance(raw, str) else fmt(raw)
    return fmt(raw)


def make(rc, mode, edge, alpha0):
    ctx = {} if rc is None else {"hatch.color": rc}
    try:
        with mpl.rc_context(ctx):
            kw = {"facecolor": "#cccccc", "hatch": "//", "alpha": alpha0}
            if edge is not None:
                kw["edgecolor"] = edge
            if mode is not None:
                if not HAS_API:
                    return None
                kw["hatchcolor"] = mode
            return Rectangle((0, 0), 1, 1, **kw)
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}"


def pin(p):
    """原型（调研用，非产品）：首次触碰前把「未设」模式钉成创建时解析出的显式值。"""
    if getattr(p, "_original_hatchcolor", 0) is None:
        p.set_hatchcolor(p._hatch_color)  # noqa: SLF001


def main():
    use_pin = "--pin" in sys.argv
    rcs = [None, "red", "edge", (1.0, 0.0, 0.0, 0.25)]
    modes = [None, "blue", "edge", (0.0, 0.0, 1.0, 0.5)]
    edges = [None, "green"]
    alphas = [None, 0.8]
    rows = []
    for rc, mode, edge, a0 in itertools.product(rcs, modes, edges, alphas):
        p = make(rc, mode, edge, a0)
        label = f"rc={fmt(rc) if rc else 'default'} hc={fmt(mode) if mode else 'unset'} edge={edge or 'unset'} a0={a0}"
        if p is None:
            continue
        if isinstance(p, str):
            rows.append((label, p, "", "", ""))
            continue
        base = read(p)
        if use_pin and HAS_API:
            pin(p)
            assert read(p) == base, "钉住本身不能改变解析值"
        p.set_alpha(0.5)
        hot = read(p)
        p.set_alpha(a0)
        undo = read(p)
        rows.append((label, base, hot, undo, "OK" if undo == base else "DIVERGE"))
    print(f"matplotlib {mpl.__version__} set_hatchcolor={HAS_API} pin={use_pin}")
    print("| case | base | alpha=0.5 | undo | undo==base |")
    print("|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(r) + " |")


if __name__ == "__main__":
    sys.exit(main())
