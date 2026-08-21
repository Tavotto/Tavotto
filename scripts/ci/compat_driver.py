#!/usr/bin/env python3
"""CompatBench 的**旁路**驱动：跑在科学栈解释器里，做三件生产路径做不到的事。

它不是第二条渲染路径——兼容判定（execute/capture/open/edit/replay/export）
一律走真实 worker 协议。这里只做那三件真实 worker **按设计**不该做的事：

* `native` —— **原生对照**。用普通 matplotlib 跑一遍用户脚本（cwd = 脚本
  目录，就是 `python figure.py` 的语义），`savefig` 只**记录**、随后**照常
  调用真实实现**（这一点与 Tavotto 的拦截正相反：拦截是为了不写用户的文件，
  对照是为了拿到「没有 Tavotto 时这张图长什么样」）。然后按目标像素宽出 PNG。
* `census` —— **artist 普查**。instrument 之后走一遍 artist 树，统计每个类
  出现了几次、其中几个拿到了 gid。**纯诊断，不参与 pass/fail**——它的产出是
  产品路线图（「哪个 artist 缺口最大」），不是门禁。
* `browser` —— 用 `engine/browser.py` 的**同一份**入口跑一遍，产出捕获、
  角色、可编辑属性与 patch 哈希，供桌面/浏览器语义对拍。

出入口都是 JSON：argv 给一个请求文件，stdout **末行**是响应。父进程
（`compat_matrix.py`）跑在 `.venv` 里、没有 matplotlib，所以这里不能被
import，只能被 spawn。

用户脚本的 print 一律改道 stderr——与 worker 同一条纪律，协议通道必须干净。
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import runpy
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 同 compat_matrix：中文输出在 Windows 的 cp1252 stdout 上会打死进程。
# 这里尤其要紧——**stdout 是协议通道**，末行那份 JSON 带 ensure_ascii=False。
from _common import use_utf8_streams                              # noqa: E402

use_utf8_streams()

_REQ: dict = {}


def _fail(code: str, message: str, **extra) -> int:
    sys.stdout.write("\n" + json.dumps(
        {"ok": False, "code": code, "message": message, **extra},
        ensure_ascii=False, default=str))
    return 0                       # 失败也要 0 退出：父进程读 JSON，不读退出码


def _ok(**payload) -> int:
    sys.stdout.write("\n" + json.dumps({"ok": True, **payload},
                                       ensure_ascii=False, default=str))
    return 0


def _tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "[truncated]\n" + text[-limit:]


# ---------------------------------------------------------------- 原生对照
def run_native(req: dict) -> int:
    """普通 matplotlib 执行 + 真实 savefig + 按 stem 出 PNG。

    与 Tavotto 拦截的区别只有一处、但它是全部意义所在：这里 `record(...)`
    之后**照常调用原始 savefig**。像 Tavotto 那样阻止真实输出的话，对照组
    就不再是「没有 Tavotto 时会发生什么」，而是「Tavotto 的另一个实现」。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.figure as mfigure
    import matplotlib.pyplot as plt

    sys.path.insert(0, os.path.dirname(req["script"]))
    sys.path.insert(0, req["project"])
    sys.path.insert(0, req["engine_dir"])
    import figcapture                                   # noqa: PLC0415

    real_savefig = mfigure.Figure.savefig
    captured: dict[str, object] = {}

    def recording_savefig(self, fname, *args, **kwargs):
        stem = figcapture.savefig_stem(fname)
        if stem:
            captured.setdefault(stem, self)
        return real_savefig(self, fname, *args, **kwargs)   # ← 照常写出去

    mfigure.Figure.savefig = recording_savefig
    plt.show = lambda *a, **k: None
    # cwd = 脚本目录：`python figure.py` 就是这个语义，对照组必须是它。
    os.chdir(os.path.dirname(req["script"]))
    sys.argv = [req["script"]]

    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            if req.get("entry", "__main__") == "__main__":
                runpy.run_path(req["script"], run_name="__main__")
            else:
                import importlib                        # noqa: PLC0415
                mod = importlib.import_module(
                    os.path.splitext(os.path.basename(req["script"]))[0])
                getattr(mod, req["entry"])()
    except BaseException:                               # noqa: BLE001 - 用户代码
        return _fail("native_script_error", "原生执行失败",
                     traceback=_tail(traceback.format_exc()),
                     log=_tail(log.getvalue()))
    finally:
        mfigure.Figure.savefig = real_savefig

    fallback, _dropped = figcapture.collect_pyplot_figures(
        captured, os.path.splitext(os.path.basename(req["script"]))[0], plt)

    out_dir = req["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    shots, sizes = {}, {}
    width = int(req.get("width", 800))
    for stem, fig in captured.items():
        w_in = float(fig.get_size_inches()[0]) or 1.0
        path = os.path.join(out_dir, f"{stem}.png")
        try:
            real_savefig(fig, path, format="png", dpi=max(50, width / w_in))
        except Exception as exc:                        # noqa: BLE001
            return _fail("native_render_failed", f"{stem}: {exc}")
        shots[stem] = path
        sizes[stem] = [round(float(v) * 25.4, 2) for v in fig.get_size_inches()]
    return _ok(stems=sorted(captured), shots=shots, size_mm=sizes,
               fallback_stems=sorted(fallback), log=_tail(log.getvalue()))


# ---------------------------------------------------------------- artist 普查
#: 普查**剪枝**这些容器：不计数、也不下探。
#:
#: 第一版用的是 `fig.findobj()`（全树展平），结果 Top-N 是
#: `Line2D 7964 / Text 6553`——那是每条刻度线与每个刻度标签，把真正的兼容
#: 缺口（LineCollection、QuadMesh、Wedge…）整个挤出了视野。坐标轴零件由
#: Tavotto 的刻度模型统一处理（`TickSet`/`TickLabel` 伪元素），它们**本来就
#: 不该**逐个拿 gid，算进去只会制造一个恒定的大数字。
_CENSUS_PRUNE = {"XAxis", "YAxis", "ZAxis", "ThetaAxis", "RadialAxis",
                 "Spine", "_ColorbarSpine",
                 "XTick", "YTick", "ZTick", "Tick", "ThetaTick", "RadialTick",
                 "Legend",
                 # offsetbox 一族是图例/标注的内部排版盒，不是用户的 artist
                 "TextArea", "HPacker", "VPacker", "DrawingArea",
                 "AnchoredOffsetbox", "OffsetBox", "AuxTransformBox",
                 "PaddedBox"}

#: 只递归、不计数的容器（它们是宿主，不是「一个 artist 缺口」）。
_CENSUS_CONTAINERS = {"Figure", "SubFigure", "Axes", "Axes3D", "PolarAxes",
                      "AxesSubplot"}

#: 计一次、**不下探**的类。`SecondaryAxis` 是 `secondary_[xy]axis` 建出来的
#: 子 Axes：它整个不被 Tavotto 认识（`instrument` 只遍历 `fig.axes`，
#: 而它挂在 `ax.child_axes` 上），所以它本身就是一个缺口条目；下探进去只会
#: 把它内部的刻度零件也算成缺口，把那一个数字放大成几十个。
_CENSUS_LEAF = {"SecondaryAxis"}


def run_census(req: dict) -> int:
    """instrument 之后统计 artist 类的出现数与「拿到 gid 的数」。

    **纯诊断**：它回答「哪个 matplotlib artist 是最大的兼容缺口」，用来排
    产品路线图。真正的 pass/fail 一律走生产路径的 worker——诊断探针替代不了
    门禁，那会变成「我们自己写的尺子量自己」。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.figure as mfigure
    import matplotlib.pyplot as plt

    sys.path.insert(0, req["engine_dir"])
    sys.path.insert(0, os.path.dirname(req["script"]))
    sys.path.insert(0, req["project"])
    import figcapture                                   # noqa: PLC0415
    import manifest as manifest_mod                     # noqa: PLC0415
    import overrides as overrides_mod                   # noqa: PLC0415

    real_savefig = mfigure.Figure.savefig
    captured: dict[str, object] = {}

    def quiet_savefig(self, fname, *args, **kwargs):
        stem = figcapture.savefig_stem(fname)
        if stem:
            captured.setdefault(stem, self)
        return None

    mfigure.Figure.savefig = quiet_savefig
    plt.show = lambda *a, **k: None
    os.makedirs(req["sandbox"], exist_ok=True)
    os.chdir(req["sandbox"])
    figcapture.install_relative_read_fallback(
        os.path.dirname(req["script"]), req["project"])
    sys.argv = [req["script"]]

    try:
        with contextlib.redirect_stdout(sys.stderr):
            if req.get("entry", "__main__") == "__main__":
                runpy.run_path(req["script"], run_name="__main__")
            else:
                import importlib                        # noqa: PLC0415
                mod = importlib.import_module(
                    os.path.splitext(os.path.basename(req["script"]))[0])
                getattr(mod, req["entry"])()
    except BaseException:                               # noqa: BLE001
        return _fail("census_script_error", "普查执行失败",
                     traceback=_tail(traceback.format_exc()))
    finally:
        mfigure.Figure.savefig = real_savefig

    figcapture.collect_pyplot_figures(
        captured, os.path.splitext(os.path.basename(req["script"]))[0], plt)

    per_stem = {}
    for stem, fig in captured.items():
        try:
            state = overrides_mod.FigState(fig)
            manifest_mod.instrument(state)
        except Exception:                               # noqa: BLE001
            per_stem[stem] = {"error": _tail(traceback.format_exc(), 1200)}
            continue
        known = {id(a) for a in state.index.values()}
        # 复合元素（误差棒组 / 柱形系列）**整体**是一个可编辑元素，它的零件
        # 不该被算成「未识别」。不展开的话 errorbar 的 5 条 Line2D 会顶到
        # Top-N 上，而它们其实是可编辑的。
        for a in list(state.index.values()):
            for attr in ("members", "artists"):
                got = getattr(a, attr, None)
                try:
                    items = got() if callable(got) else got
                except Exception:                       # noqa: BLE001
                    continue
                if isinstance(items, dict):
                    items = [v for vs in items.values()
                             for v in (vs if isinstance(vs, list) else [vs])]
                for m in items or []:
                    known.add(id(m))

        total: collections.Counter = collections.Counter()
        recognized: collections.Counter = collections.Counter()
        # `fig.patch` / `ax.patch` 是画布底色，不是用户画的形状。
        # **子 axes 的底色也要收**：`inset_axes` / `secondary_[xy]axis` 建出来的
        # 挂在 `ax.child_axes` 上、不在 `fig.axes` 里，漏掉的话插图的背景会被
        # 算成一个「未识别的 Rectangle」——普查是排路线图用的，这种假缺口会把
        # 真正的缺口挤下去。
        def _every_axes(root):
            seen, out, layer = set(), [], list(root.axes)
            while layer:
                nxt = []
                for a in layer:
                    if id(a) in seen:
                        continue
                    seen.add(id(a))
                    out.append(a)
                    nxt += list(getattr(a, "child_axes", None) or [])
                layer = nxt
            return out

        backgrounds = {id(fig.patch)} | {id(getattr(ax, "patch", None))
                                         for ax in _every_axes(fig)}
        # **色条轴的内部不下探**：色带是 QuadMesh、outline 是 _ColorbarSpine、
        # extend 三角是 PathPatch，全部由 matplotlib 每次 `_draw_all()` 删掉
        # 重建，Tavotto 刻意不登记它们（见 CLAUDE.md 色条一节）。算进去的话
        # Top-N 上的 QuadMesh 十有八九是色带而不是用户的网格图，而那会把
        # 路线图指向完全错误的地方。
        cbar_axes = {id(ax) for ax in getattr(state, "colorbar_axes", set())}

        def visit(artist, depth=0):
            cls = type(artist).__name__
            if cls in _CENSUS_PRUNE or depth > 12:
                return
            container = cls in _CENSUS_CONTAINERS
            # 空文字不算缺口：每个 Axes 天生带三个 title Text（居中/左/右），
            # 脚本没写字的那些永远拿不到 gid，把它们算进「未识别」会在
            # Top-N 顶上挂一个恒定的大数字，把真正的缺口挤下去。
            empty_text = cls == "Text" and not str(
                getattr(artist, "get_text", lambda: "")()).strip()
            if not container and not empty_text and id(artist) not in backgrounds:
                total[cls] += 1
                if id(artist) in known:
                    recognized[cls] += 1
            if id(artist) in cbar_axes:
                return
            if cls in _CENSUS_LEAF:
                return                                  # 计一次，不下探
            children = getattr(artist, "get_children", None)
            if children is None:
                return
            try:
                kids = list(children())
            except Exception:                           # noqa: BLE001
                return
            # 已经被认出来的复合 artist（散点系列、误差棒组、色条）不再下探：
            # 它整体就是一个可编辑元素，把它的零件算成「未识别」是误报。
            if id(artist) in known and not container:
                return
            for kid in kids:
                visit(kid, depth + 1)

        visit(fig)
        per_stem[stem] = {
            "total": dict(total),
            "recognized": dict(recognized),
            "roles": sorted({el["role"] for el in state.elements}),
        }
    return _ok(stems=sorted(captured), census=per_stem)


# ---------------------------------------------------------------- 浏览器语义
def run_browser(req: dict) -> int:
    """用 `engine/browser.py` 的**同一扇门**（`handle`）跑一遍。

    只取语义：捕获了哪些 stem、有哪些角色、可编辑属性键、以及一组 patch 的
    规范化哈希。像素不比——Pyodide 与 CPython 的字体栈本来就不同，那是合理
    差异；语义随入口改变才是事故。
    """
    sys.path.insert(0, req["engine_dir"])
    import browser                                      # noqa: PLC0415

    def call(payload: dict) -> dict:
        return json.loads(browser.handle(json.dumps(payload)))

    with open(req["script"], encoding="utf-8") as fh:
        source = fh.read()
    loaded = call({"cmd": "load", "filename": os.path.basename(req["script"]),
                   "source": source, "workspace": req["workspace"]})
    if not loaded.get("ok"):
        return _ok(load=loaded, figures=[], semantics={})

    figures = [f["stem"] for f in loaded.get("figures", [])]
    probes = req.get("patch_probe") or {}
    semantics = {}
    for stem in figures:
        opened = call({"cmd": "open", "stem": stem})
        if not opened.get("ok"):
            semantics[stem] = {"error": opened}
            continue
        man = opened["manifest"]
        entry = {
            "roles": sorted({el["role"] for el in man["elements"]}),
            "editable": sorted(
                f"{el['gid']}.{f['prop']}"
                for el in man["elements"] for f in el.get("editable", [])),
            "gids": [el["gid"] for el in man["elements"]],
            "size_mm": man.get("size_mm"),
            "patch_hash": opened.get("patch_hash", ""),
        }
        # 同一组 patch 在两个入口算出的规范化哈希必须逐字相同——patchspec
        # 是同一份文件，这条断言看住的是「有没有人在某一侧另写了一份」。
        patches = probes.get(stem)
        if patches:
            rendered = call({"cmd": "render", "stem": stem, "patches": patches})
            entry["applied_patch_hash"] = rendered.get("patch_hash", "")
            entry["apply_warnings"] = rendered.get("warnings", [])
            entry["apply_ok"] = bool(rendered.get("ok"))
            # 还原回零 patch，别把状态留给下一个 stem（全量列表语义）
            call({"cmd": "render", "stem": stem, "patches": []})
        semantics[stem] = entry
    return _ok(load={k: v for k, v in loaded.items() if k != "figures"},
               figures=figures, semantics=semantics,
               truncated=loaded.get("truncated_figures", 0))


MODES = {"native": run_native, "census": run_census, "browser": run_browser}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="CompatBench 旁路驱动（跑在科学栈解释器里）")
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    ap.add_argument("--request", required=True, help="请求 JSON 文件路径")
    args = ap.parse_args(argv)
    with open(args.request, encoding="utf-8") as fh:
        req = json.load(fh)
    try:
        return MODES[args.mode](req)
    except BaseException:                               # noqa: BLE001
        return _fail("driver_crashed", f"{args.mode} 驱动自身崩溃",
                     traceback=_tail(traceback.format_exc()))


if __name__ == "__main__":
    raise SystemExit(main())
