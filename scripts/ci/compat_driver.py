#!/usr/bin/env python3
"""CompatBench 的**旁路**驱动：跑在科学栈解释器里，做三件生产路径做不到的事。

它不是第二条渲染路径——兼容判定（execute/capture/open/edit/replay/export）
一律走真实 worker 协议。这里只做那三件真实 worker **按设计**不该做的事：

* `native` —— **原生对照**。用普通 matplotlib 跑一遍用户脚本（缺省 cwd = 脚本
  目录；登记 case 可声明启动 cwd/argv），`savefig` 只**记录**、随后**照常
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
import re
import runpy
import sys
import traceback
from pathlib import Path, PureWindowsPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 同 compat_matrix：中文输出在 Windows 的 cp1252 stdout 上会打死进程。
# 这里尤其要紧——**stdout 是协议通道**，末行那份 JSON 带 ensure_ascii=False。
from _common import use_utf8_streams  # noqa: E402

use_utf8_streams()

_REQ: dict = {}


def _fail(code: str, message: str, **extra) -> int:
    sys.stdout.write(
        "\n"
        + json.dumps(
            {"ok": False, "code": code, "message": message, **extra},
            ensure_ascii=False,
            default=str,
        )
    )
    return 0  # 失败也要 0 退出：父进程读 JSON，不读退出码


def _ok(**payload) -> int:
    sys.stdout.write("\n" + json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str))
    return 0


def _tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "[truncated]\n" + text[-limit:]


class ReferenceInvocationError(ValueError):
    """The registered native-control invocation is not project-local."""


def _reference_invocation(req: dict, default_cwd: str) -> tuple[str, list[str]]:
    """Resolve only cwd/argv; the registered script and entry remain authoritative."""
    if "reference_invocation" not in req:
        return default_cwd, [req["script"]]
    invocation = req["reference_invocation"]
    if not isinstance(invocation, dict) or set(invocation) != {"cwd", "argv"}:
        raise ReferenceInvocationError("reference_invocation requires only cwd and argv")
    relative, argv = invocation["cwd"], invocation["argv"]
    if not isinstance(relative, str) or not relative:
        raise ReferenceInvocationError("reference_invocation.cwd must be a relative directory")
    if (
        Path(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or PureWindowsPath(relative).root
    ):
        raise ReferenceInvocationError("reference_invocation.cwd must be project-relative")
    if not isinstance(argv, list) or any(not isinstance(arg, str) for arg in argv):
        raise ReferenceInvocationError("reference_invocation.argv must be a list of strings")
    project = Path(req["project"]).resolve()
    try:
        cwd = (project / relative).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ReferenceInvocationError("reference_invocation.cwd must exist") from exc
    if not cwd.is_relative_to(project) or not cwd.is_dir():
        raise ReferenceInvocationError("reference_invocation.cwd must be a directory in project")
    return str(cwd), [req["script"], *argv]


# ---------------------------------------------------------------- 原生对照
@contextlib.contextmanager
def _observe_native_crop(figure):
    """Observe Matplotlib's resolved crop during the original save, without another draw.

    The native renderer has already resolved tight padding, extra artists and layout.
    Do not use Tavotto's frame implementation to manufacture its own reference.
    """
    from matplotlib import _tight_bbox

    original = _tight_bbox.adjust_bbox
    crop = [None]

    def observe(fig, bbox, *args, **kwargs):
        if fig is figure:
            crop[0] = bbox.frozen()
        return original(fig, bbox, *args, **kwargs)

    _tight_bbox.adjust_bbox = observe
    try:
        yield crop
    finally:
        _tight_bbox.adjust_bbox = original


def run_native(req: dict) -> int:
    """普通 matplotlib 执行 + 真实 savefig + 按 stem 出 PNG。

    与 Tavotto 拦截的区别只有一处、但它是全部意义所在：这里 `record(...)`
    之后**照常调用原始 savefig**。像 Tavotto 那样阻止真实输出的话，对照组
    就不再是「没有 Tavotto 时会发生什么」，而是「Tavotto 的另一个实现」。
    """
    cwd, argv = _reference_invocation(req, os.path.dirname(req["script"]))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.figure as mfigure
    import matplotlib.pyplot as plt

    sys.path.insert(0, os.path.dirname(req["script"]))
    sys.path.insert(0, req["project"])
    sys.path.insert(0, req["engine_dir"])
    import figcapture  # noqa: PLC0415

    real_savefig = mfigure.Figure.savefig
    captured: dict[str, object] = {}
    # 同一路径多张图时谁算这个 stem 的图：产品三条入口共用的 `note_savefig_writer`（G2，#1141），
    # 不在这里另写一套——磁盘上留下的是最后写入者，对照组必须与它同义。
    writers: dict = {}
    sources: dict = {}
    calls: dict = {}
    extras: dict = {}
    crops: dict = {}

    def recording_savefig(self, fname, *args, **kwargs):
        stem = figcapture.savefig_stem(fname)
        if stem and stem not in captured:
            captured[stem] = self
            sources[stem] = figcapture.SOURCE_SAVEFIG
        call = figcapture.savefig_call(fname, kwargs)
        with _observe_native_crop(self) as observed:
            result = real_savefig(self, fname, *args, **kwargs)  # ← 照常写出去
        if stem:  # 成功写盘之后才记账（失败的保存不顶替任何人），与 native bridge 同序
            figcapture.note_savefig_writer(
                writers,
                captured,
                sources,
                calls,
                extras,
                stem,
                self,
                figcapture.savefig_target_key(fname, kwargs, getattr(self, "canvas", None)),
                call,
                kwargs.get("bbox_extra_artists"),
            )
            # Keep the measured crop on this save event. frame_call selects the
            # actual recorded call, including writer changes and format aliases.
            crops[id(call)] = observed[0]
        return result

    mfigure.Figure.savefig = recording_savefig
    plt.show = lambda *a, **k: None
    # Default remains script-local; registered research cases may specify their launch cwd.
    os.chdir(cwd)
    sys.argv = argv

    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            if req.get("entry", "__main__") == "__main__":
                runpy.run_path(req["script"], run_name="__main__")
            else:
                import importlib  # noqa: PLC0415

                mod = importlib.import_module(os.path.splitext(os.path.basename(req["script"]))[0])
                getattr(mod, req["entry"])()
    except BaseException:  # noqa: BLE001 - 用户代码
        return _fail(
            "native_script_error",
            "原生执行失败",
            traceback=_tail(traceback.format_exc()),
            log=_tail(log.getvalue()),
        )
    finally:
        mfigure.Figure.savefig = real_savefig

    # 写了文件却因 stem 被别的图持有而没认领的图（`panel.png` 甲 / `panel.pdf` 乙）：先于 pyplot 兜底补进来，
    # 与产品三条入口同一个函数、同一个顺序——对照组也要有 `panel-2` 这张图，否则像素层只剩甲
    file_stems: dict = {}
    figcapture.adopt_displaced_writers(writers, captured, sources, calls, extras, file_stems)
    fallback, _dropped = figcapture.collect_pyplot_figures(
        captured, os.path.splitext(os.path.basename(req["script"]))[0], plt
    )

    out_dir = req["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    shots, sizes = {}, {}
    width = int(req.get("width", 800))
    for stem, fig in captured.items():
        call = figcapture.frame_call(
            calls.get(stem),
            figcapture.find_original_artifact(
                req["project"],
                file_stems.get(stem, stem),
                formats=figcapture.saved_formats(calls.get(stem)),
            ),
        )
        crop = crops.get(id(call)) if call else None
        if call and call.get("bbox_inches") is not None and crop is None:
            return _fail("native_render_failed", f"{stem}: native crop was not observed")
        inches = [crop.width, crop.height] if crop is not None else fig.get_size_inches()
        w_in = float(inches[0]) or 1.0
        path = os.path.join(out_dir, f"{stem}.png")
        try:
            if call:
                # The save-time crop is authoritative even if rcParams changed later.
                with matplotlib.rc_context({"savefig.bbox": None}):
                    real_savefig(
                        fig, path, format="png", dpi=max(50, width / w_in), bbox_inches=crop
                    )
            else:
                # Pyplot-only captures have no original save to replay.
                real_savefig(fig, path, format="png", dpi=max(50, width / w_in))
        except Exception as exc:  # noqa: BLE001
            return _fail("native_render_failed", f"{stem}: {exc}")
        shots[stem] = path
        sizes[stem] = [round(float(v) * 25.4, 2) for v in inches]
    return _ok(
        stems=sorted(captured),
        shots=shots,
        size_mm=sizes,
        fallback_stems=sorted(fallback),
        log=_tail(log.getvalue()),
    )


# ---------------------------------------------------------------- 原生参照（Guardian M1）
#: 目录快照里不算副作用的噪音。
_REF_SKIP_DIRS = {"__pycache__", ".ipynb_checkpoints"}
_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def _ref_snapshot(root: str) -> dict[str, tuple[int, str]]:
    """相对路径 → (字节数, sha256)。符号链接不跟随，也不读内容。"""
    import hashlib  # noqa: PLC0415

    snap: dict[str, tuple[int, str]] = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _REF_SKIP_DIRS)
        for name in sorted(files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.islink(path):
                snap[rel] = (-1, "symlink")
                continue
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                snap[rel] = (-1, "unreadable")
                continue
            snap[rel] = (len(data), hashlib.sha256(data).hexdigest())
    return snap


def _ref_pdf_pages(path: str) -> dict:
    """`{"pdf_pages": N}` for a PDF artifact (page objects in the file), `{}` for anything else.

    Matplotlib's PDF backend writes page dictionaries uncompressed (`/Type /Page`, as opposed to
    the `/Type /Pages` tree), so counting them is exact for the files these scripts produce.
    """
    if not path.lower().endswith(".pdf"):
        return {}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return {}
    return {"pdf_pages": len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", data))}


def _ref_readable(path: str) -> bool:
    """产物能不能被读回来——只回答「是不是一个合法文件」，不回答「画得对不对」。"""
    suffix = os.path.splitext(path)[1].lower()
    try:
        if os.path.getsize(path) == 0:
            return False
        if suffix in _RASTER_SUFFIXES:
            from PIL import Image  # noqa: PLC0415

            with Image.open(path) as img:
                img.load()
            return True
        with open(path, "rb") as fh:
            head = fh.read(2048)
        if suffix == ".pdf":
            return head.startswith(b"%PDF")
        if suffix == ".svg":
            import xml.etree.ElementTree as ET  # noqa: PLC0415

            return ET.parse(path).getroot().tag.endswith("svg")
        return True
    except Exception:  # noqa: BLE001 - 任何读不回来都是 False
        return False


def _axes_titles(axes) -> list[str]:
    """每个轴的中/左/右三个标题（`get_title()` 只给中间那个）；Tavotto 侧清单把三者都列为 title。"""
    return [ax.get_title(loc=loc) for ax in axes for loc in ("center", "left", "right")]


def _line_xy_digests(figure) -> list[str] | None:
    """Sorted per-Line2D `get_xydata()` digests (shape + little-endian float64, as `manifest.canonical_digest`).

    Read-only and independent of the product: the control must not borrow the product's code path.
    """
    import hashlib  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    digests = []
    try:
        for ax in figure.axes:
            for line in ax.lines:
                arr = np.ma.filled(np.ma.asarray(line.get_xydata(), dtype=float), np.nan)
                arr = arr.astype("<f8", copy=True)
                arr[np.isnan(arr)] = np.nan
                head = ("x".join(str(n) for n in arr.shape) + ":").encode()
                digests.append(hashlib.sha256(head + arr.tobytes(order="C")).hexdigest())
    except Exception:  # noqa: BLE001 - a diagnostic must never fail the control run
        return None
    return sorted(digests)


def _line_xy_incomparable(figure) -> int:
    """Curves the plain-Line2D comparison cannot speak for: errorbar / stem containers, 3D axes and
    any line that is not exactly a `Line2D` (the product stores those under other fingerprint parts)."""
    from matplotlib.container import ErrorbarContainer, StemContainer  # noqa: PLC0415
    from matplotlib.lines import Line2D  # noqa: PLC0415

    count = 0
    for ax in figure.axes:
        if getattr(ax, "name", "") == "3d":
            count += 1
        count += sum(isinstance(c, (ErrorbarContainer, StemContainer)) for c in ax.containers)
        count += sum(type(line) is not Line2D for line in ax.lines)
    return count


def run_native_ref(req: dict) -> int:
    """Guardian 的原生参照：普通 `python script.py`，只观测、不干预。

    与 `run_native` 的区别是**独立实现**：这里不 import `figcapture`，图的身份只用
    Figure 对象自己，文件命名一律按原样记路径。共用产品的命名/收集代码，对照组就会
    继承产品的同一个盲点。输出不含渲染（不出 PNG），只出事实：

    * `exit_status`：脚本自然结束 = 0；`SystemExit(code)` 记 code；异常 = 1；
    * `figure_count`：脚本期间被 `savefig` 的、被 `show()` 展示过的、以及退出时 pyplot 里还开着的不同 Figure
      （`show()` 之后 `close()` 的图用户看见过，必须算）；`savefig_last_writer` 记每个路径最后由哪张图写出；
    * `artifacts`：目录快照差里新增/改动的文件 + 是否读得回来；
    * `side_effects`：新增 / 修改 / 删除 的相对路径（项目目录整体快照对比）；
    * `script_entries`：驱动自己数到的脚本入口次数（只作交叉核对，权威计数在驱动外）。

    `entry` 与 `run_native` 同义：`__main__` 走 runpy，其余 import 模块后调用该函数。
    """
    cwd, argv = _reference_invocation(req, os.path.dirname(req["script"]))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.figure as mfigure
    import matplotlib.pyplot as plt

    project = req["project"]
    script = req["script"]
    root_real = os.path.realpath(project)
    saved: list[dict] = []
    figures: list[object] = []
    real_savefig = mfigure.Figure.savefig

    def recording_savefig(self, fname, *args, **kwargs):
        if not any(self is f for f in figures):
            figures.append(self)
        target = os.fspath(fname) if isinstance(fname, (str, os.PathLike)) else None
        saved.append(
            {
                "figure": next(i for i, f in enumerate(figures) if f is self),
                "path": os.path.realpath(target) if target else None,
                "titles": _axes_titles(self.axes),
            }
        )
        return real_savefig(self, fname, *args, **kwargs)

    sys.path.insert(0, os.path.dirname(script))
    sys.path.insert(0, project)
    before = _ref_snapshot(project)
    shown: list[object] = []

    def recording_show(*args, **kwargs):
        """`show()` is where a user would see every open figure; remember them."""
        for number in plt.get_fignums():
            fig = plt.figure(number)
            if not any(fig is f for f in figures):
                figures.append(fig)
            if not any(fig is f for f in shown):
                shown.append(fig)

    mfigure.Figure.savefig = recording_savefig
    plt.show = recording_show
    os.chdir(cwd)
    sys.argv = argv

    log = io.StringIO()
    entries = 0
    exit_status: int = 0
    error: dict | None = None
    try:
        with contextlib.redirect_stdout(log):
            entries += 1
            if req.get("entry", "__main__") == "__main__":
                runpy.run_path(script, run_name="__main__")
            else:
                import importlib  # noqa: PLC0415

                mod = importlib.import_module(os.path.splitext(os.path.basename(script))[0])
                getattr(mod, req["entry"])()
    except SystemExit as exc:
        code = exc.code
        exit_status = code if isinstance(code, int) else (0 if code is None else 1)
    except BaseException as exc:  # noqa: BLE001 - 用户代码
        exit_status = 1
        error = {"type": type(exc).__name__, "traceback": _tail(traceback.format_exc())}
    finally:
        mfigure.Figure.savefig = real_savefig

    open_figures = [plt.figure(n) for n in plt.get_fignums()]
    for fig in open_figures:
        if not any(fig is f for f in figures):
            figures.append(fig)
    after = _ref_snapshot(project)
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    artifacts = [
        {
            "path": rel,
            "bytes": after[rel][0],
            "sha256": after[rel][1],
            "readable": _ref_readable(os.path.join(project, rel)),
            **_ref_pdf_pages(os.path.join(project, rel)),
        }
        for rel in created + modified
    ]
    by_path: dict[str, set[int]] = {}
    for entry in saved:
        if entry["path"]:
            by_path.setdefault(entry["path"], set()).add(entry["figure"])
    # relpath -> the LAST figure written there (what is on disk after `python script.py`).
    last_writer: dict[str, dict] = {}
    for entry in saved:
        if entry["path"] and entry["path"].startswith(root_real + os.sep):
            rel = os.path.relpath(entry["path"], root_real).replace(os.sep, "/")
            last_writer[rel] = {"figure": entry["figure"], "titles": entry["titles"]}
    return _ok(
        exit_status=exit_status,
        error=error,
        script_entries=entries,
        figure_count=len(figures),
        figures_shown=len(shown),
        savefig_last_writer=last_writer,
        figures_saved=len({e["figure"] for e in saved}),
        figures_open_at_exit=len(open_figures),
        savefig_calls=len(saved),
        same_path_distinct_figures=sorted(
            os.path.relpath(p, root_real).replace(os.sep, "/")
            for p, ids in by_path.items()
            if len(ids) > 1 and p.startswith(root_real + os.sep)
        ),
        artifacts=artifacts,
        side_effects={"created": created, "modified": modified, "deleted": deleted},
        semantics={
            f"figure{i}": {"titles": _axes_titles(fig.axes)} for i, fig in enumerate(figures)
        },
        line_xy=[_line_xy_digests(fig) for fig in figures],
        line_xy_incomparable=[_line_xy_incomparable(fig) for fig in figures],
        log=_tail(log.getvalue()),
    )


# ---------------------------------------------------------------- artist 普查
#: 普查**剪枝**这些容器：不计数、也不下探。
#:
#: 第一版用的是 `fig.findobj()`（全树展平），结果 Top-N 是
#: `Line2D 7964 / Text 6553`——那是每条刻度线与每个刻度标签，把真正的兼容
#: 缺口（LineCollection、QuadMesh、Wedge…）整个挤出了视野。坐标轴零件由
#: Tavotto 的刻度模型统一处理（`TickSet`/`TickLabel` 伪元素），它们**本来就
#: 不该**逐个拿 gid，算进去只会制造一个恒定的大数字。
_CENSUS_PRUNE = {
    "XAxis",
    "YAxis",
    "ZAxis",
    "ThetaAxis",
    "RadialAxis",
    "Spine",
    "_ColorbarSpine",
    "XTick",
    "YTick",
    "ZTick",
    "Tick",
    "ThetaTick",
    "RadialTick",
    "Legend",
    # offsetbox 一族是图例/标注的内部排版盒，不是用户的 artist
    "TextArea",
    "HPacker",
    "VPacker",
    "DrawingArea",
    "AnchoredOffsetbox",
    "OffsetBox",
    "AuxTransformBox",
    "PaddedBox",
}

#: 只递归、不计数的容器（它们是宿主，不是「一个 artist 缺口」）。
_CENSUS_CONTAINERS = {"Figure", "SubFigure", "Axes", "Axes3D", "PolarAxes", "AxesSubplot"}

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
    cwd, argv = _reference_invocation(req, req["sandbox"])
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.figure as mfigure
    import matplotlib.pyplot as plt

    sys.path.insert(0, req["engine_dir"])
    sys.path.insert(0, os.path.dirname(req["script"]))
    sys.path.insert(0, req["project"])
    import figcapture  # noqa: PLC0415
    import manifest as manifest_mod  # noqa: PLC0415
    import overrides as overrides_mod  # noqa: PLC0415

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
    os.chdir(cwd)
    if "reference_invocation" not in req:
        figcapture.install_relative_read_fallback(os.path.dirname(req["script"]), req["project"])
    sys.argv = argv

    try:
        with contextlib.redirect_stdout(sys.stderr):
            if req.get("entry", "__main__") == "__main__":
                runpy.run_path(req["script"], run_name="__main__")
            else:
                import importlib  # noqa: PLC0415

                mod = importlib.import_module(os.path.splitext(os.path.basename(req["script"]))[0])
                getattr(mod, req["entry"])()
    except BaseException:  # noqa: BLE001
        return _fail("census_script_error", "普查执行失败", traceback=_tail(traceback.format_exc()))
    finally:
        mfigure.Figure.savefig = real_savefig

    figcapture.collect_pyplot_figures(
        captured, os.path.splitext(os.path.basename(req["script"]))[0], plt
    )

    per_stem = {}
    for stem, fig in captured.items():
        try:
            state = overrides_mod.FigState(fig)
            manifest_mod.instrument(state)
        except Exception:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(items, dict):
                    items = [
                        v for vs in items.values() for v in (vs if isinstance(vs, list) else [vs])
                    ]
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

        backgrounds = {id(fig.patch)} | {id(getattr(ax, "patch", None)) for ax in _every_axes(fig)}
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
            empty_text = (
                cls == "Text" and not str(getattr(artist, "get_text", lambda: "")()).strip()
            )
            if not container and not empty_text and id(artist) not in backgrounds:
                total[cls] += 1
                if id(artist) in known:
                    recognized[cls] += 1
            if id(artist) in cbar_axes:
                return
            if cls in _CENSUS_LEAF:
                return  # 计一次，不下探
            children = getattr(artist, "get_children", None)
            if children is None:
                return
            try:
                kids = list(children())
            except Exception:  # noqa: BLE001
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
    import browser  # noqa: PLC0415

    def call(payload: dict) -> dict:
        return json.loads(browser.handle(json.dumps(payload)))

    with open(req["script"], encoding="utf-8") as fh:
        source = fh.read()
    loaded = call(
        {
            "cmd": "load",
            "filename": os.path.basename(req["script"]),
            "source": source,
            "workspace": req["workspace"],
        }
    )
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
                f"{el['gid']}.{f['prop']}" for el in man["elements"] for f in el.get("editable", [])
            ),
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
    return _ok(
        load={k: v for k, v in loaded.items() if k != "figures"},
        figures=figures,
        semantics=semantics,
        truncated=loaded.get("truncated_figures", 0),
    )


MODES = {
    "native": run_native,
    "native_ref": run_native_ref,
    "census": run_census,
    "browser": run_browser,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CompatBench 旁路驱动（跑在科学栈解释器里）")
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    ap.add_argument("--request", required=True, help="请求 JSON 文件路径")
    args = ap.parse_args(argv)
    with open(args.request, encoding="utf-8") as fh:
        req = json.load(fh)
    try:
        return MODES[args.mode](req)
    except ReferenceInvocationError as exc:
        return _fail("invalid_reference_invocation", str(exc))
    except BaseException:  # noqa: BLE001
        return _fail(
            "driver_crashed", f"{args.mode} 驱动自身崩溃", traceback=_tail(traceback.format_exc())
        )


if __name__ == "__main__":
    raise SystemExit(main())
