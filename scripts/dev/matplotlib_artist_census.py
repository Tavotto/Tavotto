#!/usr/bin/env python3
"""Artist 普查——**开发/审计工具，不在产品路径上**。

回答两个问题：

1. 一段画图代码最终生成了什么 artist 图（class × 数量 × 归属）；
2. Tavotto 的 `manifest.instrument()` 把其中哪些登记成了语义元素、
   哪些由容器代表、哪些是 matplotlib 自己的结构件、哪些**真的漏了**。

为什么要有它：Tavotto 的兼容性问题几乎从不表现为报错，而是「图上有那块
东西、点不中」。没有普查就只能靠用户描述去猜是哪个类漏了。

**它不是 `instrument()` 的替代品**：产品路径靠语义化遍历（有界、可预测），
这里的 `get_children()` 全量走查只在开发时跑。

用法（必须用装了科学栈的解释器）：

    python scripts/dev/matplotlib_artist_census.py --api
    python scripts/dev/matplotlib_artist_census.py --api --with-seaborn
    python scripts/dev/matplotlib_artist_census.py path/to/figure.py --entry main
    python scripts/dev/matplotlib_artist_census.py --api --json
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import runpy
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
#: 默认用本仓库的引擎；`TAVOTTO_CENSUS_ENGINE` 可指到另一份（把改动前后的
#: manifest/overrides 各普查一遍，缺口清单的前后对照就是这么来的）。
ENGINE = os.environ.get("TAVOTTO_CENSUS_ENGINE") or os.path.normpath(
    os.path.join(HERE, "..", "..", "src", "tavotto", "engine"))
sys.path.insert(0, ENGINE)

import matplotlib                                        # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
from matplotlib.axes import Axes                         # noqa: E402
from matplotlib.axis import Axis                         # noqa: E402
from matplotlib.figure import Figure                     # noqa: E402
from matplotlib.text import Text                         # noqa: E402

import manifest as manifest_mod                          # noqa: E402
import overrides as overrides_mod                        # noqa: E402
from overrides import SeriesGroup, _cls_key              # noqa: E402


# ---------------------------------------------------------------------------
# 分类：漏掉 ≠ 「不是用户的东西」
# ---------------------------------------------------------------------------
#: 每张图上属于 matplotlib 自己的结构件。把它们算成「Tavotto 漏掉了」，
#: 普查结果就会被几十条噪音淹没，真正的缺口反而看不见（§29）。
SEMANTIC = "semantic"          # 已登记成语义元素
COMPOSITE = "composite"        # 被容器消费（柱 / 误差棒横杠 / 茎）
INTERNAL = "internal"          # 轴、边框、背景矩形——由刻度/边框/facecolor 模型代表
GENERIC = "generic"            # 登记了，但只开 visible/zorder
MISSING = "missing"            # 真的漏了


def _internal_ids(fig, colorbar_axes=()) -> set[int]:
    ids = {id(fig.patch)}
    for ax in fig.axes:
        ids.add(id(ax))
        ids.add(id(ax.patch))
        ids.update(id(sp) for sp in getattr(ax, "spines", {}).values())
        for name in ("xaxis", "yaxis", "zaxis"):
            axis = getattr(ax, name, None)
            if axis is not None:
                ids.add(id(axis))
        if ax in colorbar_axes:
            # 色条轴的内部件（色带 / 分隔线 / 延伸三角）有意不登记，见
            # manifest._internal_ids 的注释
            ids.update(id(a) for a in getattr(ax, "patches", []))
            ids.update(id(a) for a in getattr(ax, "collections", []))
    return ids


def _qual(obj) -> str:
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def census(fig) -> dict:
    """一张 Figure → 分类计数 + 漏掉的类名清单。"""
    state = overrides_mod.FigState(fig)
    manifest_mod.instrument(state)

    semantic_ids: dict[int, str] = {}
    generic_ids: set[int] = set()
    composite_ids: set[int] = set()
    for el in state.elements:
        art = el["artist"]
        if isinstance(art, SeriesGroup):
            for m in art.members():
                composite_ids.add(id(m))
            if isinstance(art.artists, list):
                composite_ids.update(id(m) for m in art.artists)
            continue
        (generic_ids if el["role"] == "artist" else semantic_ids.setdefault(
            id(art), el["role"]) or semantic_ids)
        if el["role"] == "artist":
            generic_ids.add(id(art))
        else:
            semantic_ids[id(art)] = el["role"]

    internal = _internal_ids(fig, state.colorbar_axes)
    buckets: dict[str, Counter] = {k: Counter() for k in
                                   (SEMANTIC, COMPOSITE, INTERNAL, GENERIC, MISSING)}
    for owner in [fig] + list(fig.axes):
        for child in owner.get_children():
            if isinstance(child, (Axes, Axis)):
                continue
            if isinstance(child, Text) and not child.get_text():
                continue
            key = _qual(child)
            cid = id(child)
            if cid in generic_ids:
                buckets[GENERIC][key] += 1
            elif cid in semantic_ids:
                buckets[SEMANTIC][key] += 1
            elif cid in composite_ids:
                buckets[COMPOSITE][key] += 1
            elif cid in internal:
                buckets[INTERNAL][key] += 1
            else:
                buckets[MISSING][key] += 1

    elements = [{"gid": el["gid"], "role": el["role"],
                 "cls": _qual(el["artist"]),
                 "family": _cls_key(el["artist"]),
                 "fields": len(manifest_mod._fields_for(el))}   # noqa: SLF001
                for el in state.elements]
    return {"buckets": {k: dict(v) for k, v in buckets.items() if v},
            "elements": elements,
            "n_axes": len(fig.axes)}


# ---------------------------------------------------------------------------
# 采集 figure
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _capture():
    """拦 `Figure.savefig`，按脚本自己写出来的名字收 figure（与 worker 同一手法），
    build 期间一个图文件都不落盘。"""
    grabbed: list[tuple[str, Figure]] = []
    original = Figure.savefig

    def savefig(self, fname=None, *a, **kw):
        stem = os.path.splitext(os.path.basename(str(fname)))[0] if fname else f"fig{len(grabbed)}"
        grabbed.append((stem, self))

    Figure.savefig = savefig
    try:
        yield grabbed
    finally:
        Figure.savefig = original


def from_script(path: str, entry: str | None) -> list[tuple[str, Figure]]:
    d = os.path.dirname(os.path.abspath(path))
    sys.path.insert(0, d)
    argv, cwd = sys.argv[:], os.getcwd()
    sys.argv = [os.path.abspath(path)]
    os.chdir(d)
    try:
        with _capture() as grabbed:
            if entry:
                mod = runpy.run_path(os.path.abspath(path), run_name="__tavotto_census__")
                fn = mod.get(entry)
                if fn is None:
                    raise SystemExit(f"脚本里没有入口函数 {entry}()")
                fn()
            else:
                runpy.run_path(os.path.abspath(path), run_name="__main__")
            if not grabbed:
                grabbed.extend((f"fig{i}", plt.figure(n))
                               for i, n in enumerate(plt.get_fignums()))
            return list(grabbed)
    finally:
        sys.argv, _ = argv, os.chdir(cwd)


# ---------------------------------------------------------------------------
# 代表性 API 表（§41–43）。上层库**不该**需要专有 handler：它们最终仍然产出
# matplotlib 的 artist，普查就是这条判断的证据。
# ---------------------------------------------------------------------------
def api_specimens(with_seaborn: bool) -> list[tuple[str, object]]:
    import numpy as np
    rng = np.random.RandomState(0)
    x = np.linspace(0.5, 6.0, 30)
    Z = rng.rand(8, 8)
    D, D2 = rng.randn(200), rng.randn(200)
    cases = [
        ("plot", lambda ax: ax.plot(x, np.sin(x))),
        ("scatter", lambda ax: ax.scatter(x, np.sin(x), c=x)),
        ("bar", lambda ax: ax.bar([1, 2, 3], [3, 2, 1])),
        ("hist", lambda ax: ax.hist(D, bins=12)),
        ("hist2d", lambda ax: ax.hist2d(D, D2, bins=8)),
        ("step", lambda ax: ax.step(x, np.sin(x))),
        ("stairs", lambda ax: ax.stairs(np.arange(1, 6), np.arange(6))),
        ("stem", lambda ax: ax.stem(x[:8], np.sin(x[:8]))),
        ("fill_between", lambda ax: ax.fill_between(x, 0, np.sin(x))),
        ("stackplot", lambda ax: ax.stackplot(x, np.sin(x) + 2, np.cos(x) + 3)),
        ("pie", lambda ax: ax.pie([3, 4, 5])),
        ("boxplot", lambda ax: ax.boxplot([D, D2])),
        ("violinplot", lambda ax: ax.violinplot([D, D2])),
        ("eventplot", lambda ax: ax.eventplot([D[:20], D2[:20]])),
        ("errorbar", lambda ax: ax.errorbar(x[:8], np.sin(x[:8]), yerr=0.1, capsize=3)),
        ("contour", lambda ax: ax.contour(Z)),
        ("contourf", lambda ax: ax.contourf(Z)),
        ("pcolormesh", lambda ax: ax.pcolormesh(Z)),
        ("pcolor", lambda ax: ax.pcolor(Z)),
        ("hexbin", lambda ax: ax.hexbin(D, D2, gridsize=6)),
        ("imshow", lambda ax: ax.imshow(Z)),
        ("quiver", lambda ax: ax.quiver(Z[:4, :4], Z[:4, :4])),
        ("barbs", lambda ax: ax.barbs(Z[:4, :4], Z[:4, :4])),
        ("streamplot", lambda ax: ax.streamplot(np.arange(5), np.arange(5),
                                                Z[:5, :5], Z[:5, :5])),
        ("table", lambda ax: ax.table([[1, 2], [3, 4]])),
        ("axhspan", lambda ax: ax.axhspan(0.2, 0.4)),
        ("annotate", lambda ax: ax.annotate("hi", (1, 0), (2, 0.5),
                                            arrowprops=dict(arrowstyle="->"))),
    ]
    if with_seaborn:
        import pandas as pd
        import seaborn as sns
        df = pd.DataFrame({"x": np.tile(np.arange(10), 3), "y": rng.randn(30),
                           "g": np.repeat(list("abc"), 10), "z": rng.rand(30)})
        mat = pd.DataFrame(rng.rand(5, 5))
        cases += [
            ("sns.lineplot", lambda ax: sns.lineplot(df, x="x", y="y", hue="g", ax=ax)),
            ("sns.scatterplot", lambda ax: sns.scatterplot(df, x="x", y="y", hue="g", ax=ax)),
            ("sns.barplot", lambda ax: sns.barplot(df, x="g", y="y", ax=ax)),
            ("sns.boxplot", lambda ax: sns.boxplot(df, x="g", y="y", ax=ax)),
            ("sns.violinplot", lambda ax: sns.violinplot(df, x="g", y="y", ax=ax)),
            ("sns.heatmap", lambda ax: sns.heatmap(mat, ax=ax)),
            ("sns.kdeplot", lambda ax: sns.kdeplot(df, x="y", fill=True, ax=ax)),
            ("sns.regplot", lambda ax: sns.regplot(df, x="x", y="y", ax=ax)),
            ("pd.plot.line", lambda ax: df.plot.line(x="x", y="y", ax=ax)),
            ("pd.plot.bar", lambda ax: df.head(5).plot.bar(x="x", y="y", ax=ax)),
            ("pd.plot.scatter", lambda ax: df.plot.scatter(x="x", y="y", c="z", ax=ax)),
            ("pd.plot.area", lambda ax: df.head(5).plot.area(x="x", y="z", ax=ax)),
            ("pd.plot.box", lambda ax: df[["y", "z"]].plot.box(ax=ax)),
        ]
    return cases


def run_api_table(with_seaborn: bool) -> dict:
    rows = {}
    for name, fn in api_specimens(with_seaborn):
        fig, ax = plt.subplots(figsize=(4, 3))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn(ax)
            fig.canvas.draw()
            rows[name] = census(fig)
        except Exception as exc:                     # noqa: BLE001 — 逐条报告，不中断普查
            rows[name] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            plt.close(fig)
    return rows


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _fmt_counter(d: dict) -> str:
    return ", ".join(f"{k.rsplit('.', 1)[-1]}×{v}" for k, v in sorted(d.items()))


def print_report(rows: dict) -> int:
    missing_total: Counter = Counter()
    print(f"matplotlib {matplotlib.__version__}\n")
    print(f"{'API / figure':22s} {'元素':>4s} {'语义':>4s} {'容器成员':>8s} "
          f"{'仅识别':>7s} {'漏掉':>5s}  漏掉的类")
    print("-" * 100)
    for name, r in rows.items():
        if "error" in r:
            print(f"{name:22s} {'':>6s} {'':>8s} {'':>7s} {'':>5s}  !! {r['error']}")
            continue
        b = r["buckets"]
        miss = b.get(MISSING, {})
        missing_total.update(miss)
        # 「元素」是元素表里的条目数（含容器、刻度组这些伪元素）；「语义」只数
        # 真 artist——误差棒那种全部由容器代表的，语义列是 0 而元素列不是
        n_user = len([e for e in r["elements"]
                      if e["role"] not in ("axes", "axes3d", "figure",
                                           "ticks", "ticklabel")])
        print(f"{name:22s} {n_user:4d} {sum(b.get(SEMANTIC, {}).values()):4d} "
              f"{sum(b.get(COMPOSITE, {}).values()):8d} "
              f"{sum(b.get(GENERIC, {}).values()):7d} "
              f"{sum(miss.values()):5d}  {_fmt_counter(miss)}")
    print("-" * 100)
    if missing_total:
        print("\n还没有语义模型的类（按出现次数）：")
        for cls, n in missing_total.most_common():
            print(f"  {n:4d}  {cls}")
    else:
        print("\n没有漏掉的 artist。")
    return len(missing_total)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", nargs="?", help="要普查的画图脚本")
    ap.add_argument("--entry", default=None, help="脚本入口函数名（默认整脚本跑一遍）")
    ap.add_argument("--api", action="store_true", help="普查代表性 matplotlib API 表")
    ap.add_argument("--with-seaborn", action="store_true",
                    help="同时普查 seaborn / pandas（验证上层库不需要专有 handler）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给 CI / CompatBench 用）")
    args = ap.parse_args()

    if not args.api and not args.script:
        ap.error("给一个脚本路径，或者用 --api")

    rows = run_api_table(args.with_seaborn) if args.api else {}
    if args.script:
        for stem, fig in from_script(args.script, args.entry):
            fig.canvas.draw()
            rows[stem] = census(fig)

    if args.json:
        json.dump({"matplotlib": matplotlib.__version__, "figures": rows},
                  sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        print()
        return 0
    print_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
