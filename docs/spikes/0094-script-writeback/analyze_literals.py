"""ADR 0094 spike：方案 B（就地改字面量）对同一批 override 能表达多少。

在本进程里真跑每个用例脚本（Agg，savefig 只记图不落盘），给创建 artist 的几个 API 挂上
记账：每个 artist 由哪一行调用建出来、那一行一共执行了几次。再把 spike 的每条 override
按 gid 找到 artist，分四类：

  A 唯一字面量   调用只执行一次，对应关键字参数是常量 → 方案 B 可以就地改
  B 共享字面量   常量，但这一行执行了多次（循环 / 被多次调用的函数）→ 改了会连带别的对象
  C 表达式       变量 / 下标 / f-string / 调用 → 方案 B 得改表达式或它的定义，同样会连带
  D 不在调用里   值来自 rcParams / 默认值 → 方案 B 只能**新增**参数（这已经是插入，
                 且插在 tight_layout 之前时布局随之改变，与热态不一定相等）

用法：python analyze_literals.py   （读 results*.json 里的 patch，写 literals.json）
"""

from __future__ import annotations

import ast
import collections
import json
import os
import runpy
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.axes  # noqa: E402
import matplotlib.figure  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_spike import CASES  # noqa: E402

KW = {
    "color": ("color", "c"),
    "linewidth": ("linewidth", "lw"),
    "fontsize": ("fontsize", "size"),
    "loc": ("loc",),
}

SITES: dict[int, tuple[str, int]] = {}  # id(artist) → (file, line)
COUNTS: collections.Counter = collections.Counter()
CAPTURE: dict[str, object] = {}


def _caller(script: Path):
    f = sys._getframe(2)
    while f is not None:
        if Path(f.f_code.co_filename).resolve() == script:
            return (str(script), f.f_lineno)
        f = f.f_back
    return None


def _wrap(cls, name, script, pick):
    real = getattr(cls, name)

    def wrapper(self, *a, **k):
        site = _caller(script)
        out = real(self, *a, **k)
        if site is not None:
            COUNTS[site] += 1
            for art in pick(self, out):
                SITES.setdefault(id(art), site)
        return out

    setattr(cls, name, wrapper)


def _instrument(script: Path):
    A = matplotlib.axes.Axes
    _wrap(A, "plot", script, lambda ax, out: out)
    _wrap(A, "text", script, lambda ax, out: [out])
    _wrap(A, "annotate", script, lambda ax, out: [out])
    _wrap(A, "set_xlabel", script, lambda ax, out: [ax.xaxis.label])
    _wrap(A, "set_ylabel", script, lambda ax, out: [ax.yaxis.label])
    _wrap(A, "set_title", script, lambda ax, out: [ax.title])
    _wrap(A, "legend", script, lambda ax, out: [out])
    _wrap(A, "axhline", script, lambda ax, out: [out])

    def savefig(self, fname, *a, **k):
        stem = (
            os.path.splitext(os.path.basename(os.fspath(fname)))[0]
            if isinstance(fname, (str, os.PathLike))
            else ""
        )
        CAPTURE.setdefault(stem, self)

    matplotlib.figure.Figure.savefig = savefig


def _axes(fig):
    out = list(fig.axes)
    return out


def _artist(fig, gid):
    head, _, rest = gid.partition(".")
    ax = _axes(fig)[int(head.split("_")[1])]
    if rest in ("xlabel", "ylabel"):
        return getattr(ax, rest[0] + "axis").label
    if rest == "title":
        return ax.title
    if rest == "legend":
        return ax.get_legend()
    if rest.startswith("lines_"):
        return ax.lines[int(rest.split("_")[1])]
    if rest.startswith("texts_"):
        return ax.texts[int(rest.split("_")[1])]
    return None


def _call_at(tree, line):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno <= line <= node.end_lineno:
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in (
                "plot",
                "text",
                "annotate",
                "set_xlabel",
                "set_ylabel",
                "set_title",
                "legend",
                "axhline",
            ):
                return node
    return None


def classify(tree, site, prop):
    if site is None:
        return "D", "没有可追溯的创建调用（刻度 / 由库函数建出）"
    node = _call_at(tree, site[1])
    if node is None:
        return "D", "创建点不是可识别的调用"
    names = KW.get(prop, ())
    kw = next((k for k in node.keywords if k.arg in names), None)
    if prop == "pos_frac":
        return "C", "位置是 figure 分数，要换算回脚本的坐标系（数据 / 轴分数）再改实参"
    if kw is None:
        return "D", f"{site[1]} 行没有 {prop} 参数（取 rcParams / 默认值）"
    shared = COUNTS[site] > 1
    literal = isinstance(kw.value, ast.Constant)
    if literal and not shared:
        return "A", f"{site[1]} 行 {kw.arg}={ast.unparse(kw.value)}"
    if literal:
        return "B", f"{site[1]} 行 {kw.arg}={ast.unparse(kw.value)} 执行了 {COUNTS[site]} 次"
    return "C", f"{site[1]} 行 {kw.arg}={ast.unparse(kw.value)}"


def run(case):
    d, script, entry, stem = CASES[case]
    path = (HERE / d / script).resolve()
    SITES.clear(), COUNTS.clear(), CAPTURE.clear()
    old = os.getcwd()
    os.chdir(path.parent)
    sys.path.insert(0, str(path.parent))
    sys.argv = [str(path)]
    try:
        _instrument(path)
        ns = runpy.run_path(str(path), run_name="__main__" if entry == "__main__" else "spike")
        if entry != "__main__":
            ns[entry]()
    finally:
        os.chdir(old)
        sys.path.remove(str(path.parent))
    tree = ast.parse(path.read_bytes())
    return tree, CAPTURE.get(stem)


def main():
    rows = []
    res = json.loads((HERE / "results_round1.json").read_text("utf-8"))
    for case_res in res:
        case = case_res["case"]
        try:
            tree, fig = run(case)
        except Exception:  # noqa: BLE001
            rows.append({"case": case, "error": traceback.format_exc()[-400:]})
            continue
        for row in case_res["results"]:
            if row["category"] == "ALL":
                continue
            spec = row["patches"][0]
            gid_prop = spec.split("=", 1)[0]
            gid, prop = gid_prop.rsplit(".", 1)
            art = _artist(fig, gid) if fig is not None else None
            if gid.endswith("ticks"):
                cls, why = "D", "刻度文字不是一次调用建出来的（tick_params / rcParams）"
            elif prop == "loc_frac":
                cls, why = (
                    "C",
                    "拖出来的位置要换算成 loc 元组并清掉 bbox_to_anchor——不是改一个字面量",
                )
            elif gid.endswith(".legend") and prop == "fontsize":
                cls, why = classify(tree, SITES.get(id(art)), "fontsize")
            elif ".legend.texts_" in gid:
                cls, why = "D", "单条图例文字没有自己的调用（legend() 统一建）"
            else:
                cls, why = classify(tree, SITES.get(id(art)), prop)
            rows.append(
                {"case": case, "patch": gid_prop, "class": cls, "why": why, "hook": row["verdict"]}
            )
        matplotlib.pyplot.close("all")
    (HERE / "literals.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    c = collections.Counter(r.get("class") for r in rows if "class" in r)
    print(dict(c), "of", sum(c.values()))
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
