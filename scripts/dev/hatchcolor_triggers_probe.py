"""#874 调研：哪些 setter 会让 3.11 的未设模式花纹色按「当前 rcParams」重解析（不进产品）。

对「rc_context({'hatch.color': 'red'}) 里建、环境 hatch.color 是默认」的 Patch / Collection，
逐个调 setter 后读花纹色，看是否还是 red。≤3.10 没有 hatch.color 重解析，读 _hatch_color。
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt

print("matplotlib", mpl.__version__)
fig, ax = plt.subplots()


def hat(a):
    if hasattr(a, "_hatchcolors"):
        h = a._hatchcolors
        return h if isinstance(h, str) else [tuple(round(float(x), 2) for x in r) for r in h]
    h = a._hatch_color
    return h if isinstance(h, str) else tuple(round(float(x), 2) for x in h)


def make(kind):
    with mpl.rc_context({"hatch.color": "red"}):
        if kind == "bar":
            return ax.bar([0], [1], hatch="//")[0]
        if kind == "fill_between":
            return ax.fill_between([0, 1], [0, 1], hatch="//")
        if kind == "scatter":
            return ax.scatter([0], [0], hatch="//")
        if kind == "hist_patch":
            return ax.hist([1, 2, 2, 3], hatch="//")[2][0]


STEPS = {
    "(创建后)": lambda a: None,
    "set_alpha(0.5)": lambda a: a.set_alpha(0.5),
    "set_alpha(None)": lambda a: a.set_alpha(None),
    "set_fill(False)": lambda a: a.set_fill(False),
    "set_fill(True)": lambda a: a.set_fill(True),
    "set_edgecolor('k')": lambda a: a.set_edgecolor("k"),
    "set_facecolor('b')": lambda a: a.set_facecolor("b"),
    "set_hatch('x')": lambda a: a.set_hatch("x"),
    "set_linewidth(2)": lambda a: a.set_linewidth(2),
    "set_color('g')": lambda a: a.set_color("g"),
}
for kind in ("bar", "hist_patch", "fill_between", "scatter"):
    out = []
    for name, fn in STEPS.items():
        a = make(kind)
        try:
            fn(a)
            out.append(f"{name}: {hat(a)}")
        except AttributeError:
            out.append(f"{name}: n/a")
    print(kind, "|", " ; ".join(out))


# --- 原型验证（调研）：首次触碰前钉住，alpha / fill 往返后是否回到创建时的值 ---------------------------
def pin(a):
    if getattr(a, "_original_hatchcolor", 0) is None and hasattr(a, "set_hatchcolor"):
        a.set_hatchcolor(a._hatchcolors if hasattr(a, "_hatchcolors") else a._hatch_color)  # noqa: SLF001


if hasattr(mpl.patches.Patch, "set_hatchcolor"):
    for ctxval in ("red", "edge", (1.0, 0.0, 0.0, 0.25)):
        for kind in ("bar", "fill_between", "scatter"):
            with mpl.rc_context({"hatch.color": ctxval}):
                a = {
                    "bar": lambda: ax.bar([0], [1], hatch="//")[0],
                    "fill_between": lambda: ax.fill_between([0, 1], [0, 1], hatch="//"),
                    "scatter": lambda: ax.scatter([0], [0], hatch="//"),
                }[kind]()
            before = hat(a)
            pin(a)
            a.set_alpha(0.5)
            mid = hat(a)
            a.set_alpha(None)
            if hasattr(a, "set_fill"):
                a.set_fill(False)
                a.set_fill(True)
            after = hat(a)
            print(
                f"PIN ctx={ctxval} {kind}: before={before} mid={mid} after={after} roundtrip={'OK' if before == after else 'DIVERGE'}"
            )

    # r4221057303 的真形状：环境 hatch.color 非 'edge'，局部 rc_context 用 'edge' 建
    mpl.rcParams["hatch.color"] = "red"
    for use in (False, True):
        with mpl.rc_context({"hatch.color": "edge"}):
            a = ax.bar([0], [1], hatch="//")[0]
        if use:
            pin(a)
        a.set_alpha(0.5)
        a.set_alpha(None)
        print(
            f"EDGE-SENTINEL ambient=red pin={use}: after alpha round trip -> {hat(a)} (创建时 'edge')"
        )
