#!/usr/bin/env python3
"""渲染 worker 子进程（系统 python3，需 matplotlib 科学栈）。

跑一个 fig 脚本一次，把产出的 Figure 常驻内存（live-figure 会话），
之后通过 stdin/stdout 的 JSON 行协议接受指令。**两套信封并存**：

* **协议 v1**（带 `protocol_version` 字段，`pool.py` 走这条）：

      {"protocol_version":1,"request_id":"r-…","worker_generation":3,
       "render_revision":17,"cmd":"render","stem":"Fig1",
       "canonical_patch_hash":"sha256:…","payload":{"patches":[…]}}

  命令：ping / build / render / render_png / preview_png / export /
  cancel / shutdown。generation、revision、hash **worker 只回显不理解**
  （校验归 supervisor）。build / render / export 的成功响应带 `timings`
  （毫秒，见 `_TIMED_COMMANDS`）。完整契约见
  `docs/adr/0003-worker-protocol-v1.md`。

* **legacy**（无 `protocol_version`）：老的扁平信封，行为一字不改——
  手工 `echo '{"cmd":"build"}' | python worker.py …` 调试时用的就是它。

      {"cmd":"build"}                                → 导入脚本、跑入口、捕获全部 Figure
      {"cmd":"override","stem":s,"patches":[...]}    → 应用全量 override，重导出预览 SVG
      {"cmd":"export","stem":s,"patches":[...],
       "path":p,"format":"pdf","dpi":600}            → 全质量导出（供 PyMuPDF 合成）
      {"cmd":"ping"} / {"cmd":"shutdown"}

安全措施：
  * cwd 切到沙盒目录——fig6/fig7 等脚本的相对路径写出/glob 删除不会碰真实 figures 目录
  * 拦截 Figure.savefig 与 paper_style.save（import 脚本前安装）——build 期间不写任何图文件
  * 脚本的 stdout 重定向到 stderr，保证协议通道干净
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure as mfigure  # noqa: E402

import manifest as manifest_mod  # noqa: E402
import overrides as overrides_mod  # noqa: E402
# 规范化与哈希**只有这一份实现**（父进程走 magplot.engine.patchspec，
# 这里因为 engine 目录已在 sys.path 里而平铺 import 同一个文件）。
# 在这里复制一份「一样的算法」= 两边迟早分叉，而分叉的表现是哈希对不上、
# 缓存永远不命中，没人会立刻联想到序列化细节。
import patchspec  # noqa: E402

CAPTURE: dict[str, object] = {}   # stem -> Figure（脚本产出顺序）
STATES: dict[str, overrides_mod.FigState] = {}
_intercept = True
_REAL_SAVEFIG = mfigure.Figure.savefig

#: 本 worker 实现的协议版本。升版规则见 ADR 0003：加字段不升版（两侧都必须
#: 容忍未知字段），改语义/删字段才升。
PROTOCOL_VERSION = 1

#: v1 命令集——不在表里的一律 unknown_cmd，绝不「猜一个最像的」。
V1_COMMANDS = frozenset({
    "ping", "build", "render", "render_png", "preview_png",
    "export", "cancel", "shutdown",
})

#: 带 patches 的命令（要算 canonical hash 做序列化自检）
_PATCH_COMMANDS = frozenset({"render", "preview_png", "export"})

#: 需要 stem 的命令
_STEM_COMMANDS = frozenset({"render", "render_png", "preview_png", "export"})

#: 会在响应里带 `timings` 的命令（v1 **only**——legacy 信封的形状一字不改）。
#: 加字段不升协议版本（ADR 0003 §1：两侧都必须容忍未知字段）。
_TIMED_COMMANDS = frozenset({"build", "render", "export"})


def _ms(t0: float) -> float:
    """自 `t0`（perf_counter）以来的毫秒数，保留三位小数。

    用 `perf_counter` 而不是 `time.time()`：后者会被系统改时间/NTP 校正带偏，
    而这些数字是要拿去做「哪一段最慢」的判断的。
    """
    return round((time.perf_counter() - t0) * 1000.0, 3)


class ProtocolError(Exception):
    """v1 的结构化错误。`retryable` 是给 supervisor 看的：只有 internal
    （我们也不知道为什么）才值得重启后重试一次；bad_request / unknown_cmd /
    unknown_stem / script_error 重试多少次都是同一个结果。"""

    def __init__(self, code: str, message: str, retryable: bool = False,
                 traceback_text: str = "", extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.traceback_text = traceback_text
        self.extra = extra or {}


def _int_arg(payload: dict, key: str, default: int) -> int:
    """payload 里的整数参数（width / dpi）；写错类型报 bad_request。"""
    raw = payload.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ProtocolError("bad_request", f"payload.{key} 必须是整数")
    try:
        return int(raw)
    except ValueError as exc:
        raise ProtocolError("bad_request",
                            f"payload.{key} 必须是整数: {raw!r}") from exc


def _patched_savefig(self, fname, *args, **kwargs):
    """通用兜底：raw fig.savefig 的脚本也被捕获；同 stem 的 pdf/png 只记一次。"""
    if not _intercept:
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)
    if isinstance(fname, (str, os.PathLike)):
        stem = Path(os.fspath(fname)).stem
        if stem:
            CAPTURE.setdefault(stem, self)
    return None


@contextlib.contextmanager
def _real_output():
    global _intercept
    _intercept = False
    try:
        yield
    finally:
        _intercept = True


def _json_default(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


class Worker:
    def __init__(self, args):
        self.script = Path(args.script).resolve()
        self.figures_dir = Path(args.figures_dir).resolve()
        self.out_dir = Path(args.out_dir).resolve()
        self.sandbox = Path(args.sandbox).resolve()
        self.entry = args.entry
        self.preview_dpi = args.preview_dpi
        self.built = False
        self._manifest_cache: dict[str, dict] = {}
        # 见过的 request_id（v1 的 cancel 用来分辨「那条已经跑完了」和
        # 「根本没见过这个 id」）。worker 串行读 stdin，能读到 cancel 就说明
        # 目标请求早已结束——只留最近一小段，不做无上限的账本。
        self._seen: collections.deque = collections.deque(maxlen=64)

    # ---------------- build ----------------
    def build(self, timings: dict | None = None) -> dict:
        """跑一次用户脚本，把产出的 Figure 全部收进内存。

        `timings` 非空时填两个数：`script_build_ms` 是整个 build（脚本 +
        instrument + 每个 stem 的首次预览），`script_exec_ms` 只是用户脚本
        自己那一段。两者分开是因为它们的改法完全不同——前者大头在用户的
        计算里（我们无能为力），差额才是引擎自己的开销。
        """
        t_build = time.perf_counter()
        if self.built:
            return self._stems_summary()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        os.chdir(self.sandbox)
        # 图库根先进 sys.path（脚本 import 同目录的 paper_style / 数据模块），
        # 脚本自己所在目录再插到最前——面板脚本放 panels/ 子目录时，
        # 只加图库根会让 import_module(stem) 直接 ModuleNotFoundError。
        sys.path.insert(0, str(self.figures_dir))
        if self.script.parent != self.figures_dir:
            sys.path.insert(0, str(self.script.parent))

        # 删除守卫：fig6 用绝对路径删“过期输出”（ROOT/figures/...），沙盒 cwd
        # 挡不住，这里直接拒绝任何指向真实图库目录的删除（渲染用不到删除）
        real_figs = self.figures_dir
        real_unlink = Path.unlink

        def _guarded_unlink(p, missing_ok=False):
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过删除真实图库文件: {p}", file=sys.stderr)
                    return None
            except OSError:
                pass
            return real_unlink(p, missing_ok=missing_ok)

        Path.unlink = _guarded_unlink

        # 写入守卫：脚本的 write_caption 等用绝对路径 write_text 写真实图库
        # （fig9 的 *_caption.txt），沙盒 cwd 拦不住；导出走 savefig 不受影响
        real_write_text = Path.write_text

        def _guarded_write_text(p, *a, **kw):
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过写入真实图库文件: {p}", file=sys.stderr)
                    return 0
            except OSError:
                pass
            return real_write_text(p, *a, **kw)

        Path.write_text = _guarded_write_text

        # 拦截必须发生在 import 脚本之前（多数脚本 from paper_style import save）
        mfigure.Figure.savefig = _patched_savefig
        # paper_style 是某些图库的私有方言，不是引擎的依赖：没有就跳过，
        # 靠 _patched_savefig 这条通用兜底捕获。曾经这里是无保护的 import，
        # 任何不带 paper_style.py 的图库都会以 ModuleNotFoundError 开局。
        try:
            import paper_style  # noqa: PLC0415
        except ImportError:
            pass
        else:
            paper_style.save = (
                lambda fig, stem, outdir="figures": CAPTURE.setdefault(stem, fig))

        # 脚本看到的 argv 必须是它自己的，不是 worker 的。不换的话
        # `sys.argv[1:]` 拿到的是 --script/--out-dir/--entry 这串内部参数，
        # 按参数命名输出的脚本会存出一堆叫 "--entry" 的图（试运行探测时
        # 当场撞见过）。真跑 `python fig.py` 时 argv 就只有脚本自己。
        sys.argv = [str(self.script)]

        t_script = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            if self.entry == "__main__":
                import runpy  # noqa: PLC0415 — 内联脚本（fig4c / fig_models）
                runpy.run_path(str(self.script), run_name="__main__")
            else:
                module = importlib.import_module(self.script.stem)
                getattr(module, self.entry)()
        script_ms = _ms(t_script)

        for stem, fig in CAPTURE.items():
            state = overrides_mod.FigState(fig)
            manifest_mod.instrument(state)
            STATES[stem] = state
            self._render(stem)
        self.built = True
        if timings is not None:
            timings["script_exec_ms"] = script_ms
            timings["script_build_ms"] = _ms(t_build)
        return self._stems_summary()

    def _stems_summary(self) -> dict:
        return {"stems": {s: {"size_mm": self._manifest_cache[s]["size_mm"]}
                          for s in STATES}}

    def _render(self, stem: str, timings: dict | None = None,
                preview_dpi: int | None = None) -> dict:
        """导出预览 SVG + 重建 manifest，写入 out_dir。

        `preview_dpi` 只影响 SVG 里**嵌入位图**的分辨率（imshow / 光栅化的
        面板）：纯矢量图上它一分钱都不值（实测 dpi 72→300 耗时与体积一模一样），
        含 imshow 的图上 200→100 能让 savefig 从 ~29ms 降到 ~17ms、SVG 从
        827KB 降到 196KB。给不给由调用方决定，缺省仍是 `--preview-dpi`。

        计时口径（`timings` 非空时填）：

        * `manifest_ms` —— `build_manifest`，其中包含一次 `fig.canvas.draw()`
          （量每个元素的包围盒必须有 renderer）；
        * `canvas_draw_ms` —— `savefig(svg)`。**SVG 序列化与 draw 在 matplotlib
          里分不开**（`print_svg` 是「边画边写」的一趟），所以不单出 `svg_ms`，
          两者合在这一个数里，见 ADR 0003 §9。
        """
        state = STATES[stem]
        t0 = time.perf_counter()
        man = manifest_mod.build_manifest(state, stem)
        t1 = time.perf_counter()
        with _real_output():
            state.fig.savefig(self.out_dir / f"{stem}.svg", format="svg",
                              dpi=preview_dpi or self.preview_dpi)
        if timings is not None:
            timings["manifest_ms"] = round((t1 - t0) * 1000.0, 3)
            timings["canvas_draw_ms"] = _ms(t1)
        (self.out_dir / f"{stem}.json").write_text(
            json.dumps(man, ensure_ascii=False, default=_json_default), encoding="utf-8")
        self._manifest_cache[stem] = man
        return man

    # ---------------- 命令原语（两套信封共用同一份实现） ----------------
    def _snapshot(self, state) -> list[dict]:
        """当前会话已应用的 override，作为「全量列表」形状的快照。"""
        return [{"gid": g, "prop": p, "value": v}
                for (g, p), v in state.applied.items()]

    def _do_render(self, stem: str, patches: list,
                   timings: dict | None = None,
                   preview_dpi: int | None = None,
                   inline_svg: bool = False) -> dict:
        """应用全量 override 列表 + 重出预览 SVG/manifest（v1 的 render）。

        `inline_svg=True` 时响应里**多带一份 SVG 文本**。为什么要它：SVG 与
        manifest 必须成对——另一个标签页（或同一文件的另一个变体）的渲染插进来
        之后，第二跳 GET 拿到的磁盘 SVG 已经是别人的了，而 manifest 是这次的，
        画布上就出现「框选命中的元素和看到的图对不上」。worker 串行执行，在这里
        把刚写完的那份读回来天然原子。
        """
        t0 = time.perf_counter()
        warnings = overrides_mod.apply(STATES[stem], patches)
        if timings is not None:
            timings["patch_apply_ms"] = _ms(t0)
        result = {"manifest": self._render(stem, timings, preview_dpi),
                  "warnings": warnings}
        if inline_svg:
            # 读回磁盘那一份而不是另存一个内存缓冲：调用方拿到的与
            # out_dir/<stem>.svg 逐字节相同，排障时不必怀疑「是不是两份」
            result["svg"] = (self.out_dir / f"{stem}.svg").read_text(encoding="utf-8")
        return result

    def _do_render_png(self, stem: str, width: int) -> dict:
        """从 live figure 按目标像素宽出高清位图（imshow 类面板显示用）。"""
        state = STATES[stem]
        w_in = float(state.fig.get_size_inches()[0])
        path = self.out_dir / f"{stem}_w{int(width)}.png"
        with _real_output():
            state.fig.savefig(path, format="png", dpi=max(50, int(width) / w_in))
        return {"path": str(path)}

    def _do_preview_png(self, stem: str, patches: list, width: int, tag: str) -> dict:
        """历史版本预览：临时应用指定 patches 出图，随后还原当前会话状态。"""
        state = STATES[stem]
        prev = self._snapshot(state)
        # `try` 必须从 apply 之前起：apply 自己会抛（属性不认、值越界），
        # 起晚了的话异常路径上还原就不执行，这次预览专用的 patches 留在常驻
        # figure 上，此后前端手里的 lastPatches 与 worker 真实状态错位。
        try:
            overrides_mod.apply(state, patches)
            w_in = float(state.fig.get_size_inches()[0])
            path = self.out_dir / f"{stem}__{tag}.png"
            with _real_output():
                state.fig.savefig(path, format="png", dpi=max(50, int(width) / w_in))
        finally:
            overrides_mod.apply(state, prev)
        return {"path": str(path)}

    def _do_export(self, stem: str, patches: list, path: str,
                   fmt: str = "pdf", dpi: int = 600,
                   timings: dict | None = None) -> dict:
        """全质量导出（供 PyMuPDF 合成）。

        与 preview_png 同一纪律：export 是**状态中立**的一次性动作。
        不还原的话导出用的 patches 会留在常驻 figure 上——历史版本恢复、
        画布导出（各面板自带一套 overrides）之后，热会话的真实状态就与
        前端手里的 lastPatches 错位，下一次 render 的「全量列表」语义
        会拿着错的 applied 表去做还原。
        """
        state = STATES[stem]
        prev = self._snapshot(state)
        out = Path(path)
        # `try` 从 apply 之前起（见 _do_preview_png 的同款说明）：apply 与
        # mkdir 都会抛——目标目录不可写、路径过长、Windows 上被占用——而它们
        # 恰恰是最需要还原的那两步。画布合成导出用的是**热会话**，一次没还原
        # 就把这一个面板的 overrides 留在了下一个面板的渲染上。
        try:
            t0 = time.perf_counter()
            warnings = overrides_mod.apply(state, patches)
            t1 = time.perf_counter()
            out.parent.mkdir(parents=True, exist_ok=True)
            with _real_output():
                state.fig.savefig(out, format=fmt, dpi=int(dpi))
            if timings is not None:
                timings["patch_apply_ms"] = round((t1 - t0) * 1000.0, 3)
                # 还原那一次的耗时**不算进 export_ms**：它是状态中立这条纪律的
                # 代价，不是用户等的那张图的成本（但它确实要花时间，见 total_ms）
                timings["export_ms"] = _ms(t1)
        finally:
            # 还原那次的 warnings 丢弃：报给调用方的必须是「这组 patches
            # 有没有写不进去的」，混进还原噪音会让写回自检误判。
            overrides_mod.apply(state, prev)
        return {"path": str(out), "warnings": warnings}

    # ---------------- legacy 信封（无 protocol_version） ----------------
    def handle(self, req: dict) -> dict:
        """老的扁平协议。**响应形状一字不改**——手工调试与旧调用方靠它。"""
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True}
        if cmd == "shutdown":
            raise SystemExit(0)
        if cmd == "build":
            return {"ok": True, **self.build()}
        if not self.built:
            self.build()

        stem = req.get("stem", "")
        if stem not in STATES:
            return {"ok": False, "error": f"stem 不存在: {stem}",
                    "known": sorted(STATES)}

        if cmd == "override":
            return {"ok": True, **self._do_render(stem, req.get("patches", []))}
        if cmd == "preview_png":
            return {"ok": True, **self._do_preview_png(
                stem, req.get("patches", []), int(req.get("width", 400)),
                str(req.get("tag", "p")))}
        if cmd == "render_png":
            return {"ok": True, **self._do_render_png(
                stem, int(req.get("width", 800)))}
        if cmd == "export":
            return {"ok": True, **self._do_export(
                stem, req.get("patches", []), req["path"],
                req.get("format", "pdf"), int(req.get("dpi", 600)))}

        return {"ok": False, "error": f"未知指令: {cmd}"}

    # ---------------- 协议 v1 ----------------
    def _ensure_built(self, timings: dict | None = None) -> None:
        """按需 build；用户脚本炸了报 script_error（重试没有意义）。

        `timings` 一路传下去：冷启动的 render 里 `script_build_ms` 才是那几十秒
        的去向，不带上的话响应里只剩几毫秒的 apply/draw，读数与用户的体感完全
        对不上。

        build 里除了跑脚本还有 mkdir / 预览 SVG 落盘，理论上也会因磁盘问题
        失败——这里**一律归到 script_error**：绝大多数是脚本自己的问题，
        而把两者分开需要猜 traceback 的来源，猜错比归错更难排查。
        真正的原因永远在 `error.traceback` 里原样带着。
        （`missing_dependency` 由父进程按 traceback 正则单独识别，先于 code。）
        """
        if self.built:
            return
        try:
            self.build(timings)
        except Exception as exc:  # noqa: BLE001 — 转成结构化错误，进程不退出
            raise ProtocolError("script_error", f"脚本执行失败: {exc}",
                                retryable=False,
                                traceback_text=traceback.format_exc()) from exc

    def handle_v1(self, req: dict) -> dict:
        """v1 信封 → v1 响应。抛 ProtocolError 由 `_v1_error()` 转成错误信封。"""
        rid = req.get("request_id")
        if req.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(
                "bad_request",
                f"不支持的 protocol_version: {req.get('protocol_version')!r}"
                f"（本 worker 说 v{PROTOCOL_VERSION}）")
        if not isinstance(rid, str) or not rid:
            raise ProtocolError("bad_request", "request_id 必须是非空字符串")
        cmd = req.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            raise ProtocolError("bad_request", "cmd 必须是非空字符串")
        payload = req.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ProtocolError("bad_request", "payload 必须是对象")
        for key in ("worker_generation", "render_revision"):
            val = req.get(key)
            if val is not None and not isinstance(val, int):
                raise ProtocolError("bad_request", f"{key} 必须是整数")
        if cmd not in V1_COMMANDS:
            raise ProtocolError("unknown_cmd", f"未知指令: {cmd}",
                                extra={"known": sorted(V1_COMMANDS)})

        self._seen.append(rid)

        if cmd == "shutdown":
            raise SystemExit(0)
        if cmd == "ping":
            return {}
        if cmd == "cancel":
            return self._cancel(payload)

        patches = payload.get("patches", [])
        if cmd in _PATCH_COMMANDS and not isinstance(patches, list):
            raise ProtocolError("bad_request", "payload.patches 必须是数组")

        # 阶段计时（毫秒）。**只在 v1 出现**，legacy 信封的形状一字不改。
        timings: dict[str, float] = {}
        self._ensure_built(timings)
        if cmd == "build":
            return {**self._stems_summary(), "timings": timings}

        result: dict = {}
        stem = req.get("stem")
        if cmd in _STEM_COMMANDS:
            if not isinstance(stem, str) or not stem:
                raise ProtocolError("bad_request", "stem 必须是非空字符串")
            if stem not in STATES:
                raise ProtocolError("unknown_stem", f"stem 不存在: {stem}",
                                    extra={"known": sorted(STATES)})

        # 参数校验全部先做完再进渲染：混在下面那个 try 里的话，matplotlib 自己
        # 抛的 ValueError（画的时候什么都可能抛）会被当成「调用方参数写错了」，
        # 报出 retryable=false 的 bad_request——排障时指向完全错误的方向。
        if cmd == "export":
            path = payload.get("path")
            if not isinstance(path, str) or not path:
                raise ProtocolError("bad_request", "payload.path 必须是非空字符串")
        width = _int_arg(payload, "width", 800 if cmd == "render_png" else 400)
        dpi = _int_arg(payload, "dpi", 600)
        # 可选：这一次预览 SVG 用的 dpi（缺省 = 启动参数 --preview-dpi）。
        # 非正数一律 bad_request——0 会让 matplotlib 抛在渲染里，报出来的
        # 是 internal + 一段 traceback，指向完全错误的方向。
        preview_dpi = None
        if "preview_dpi" in payload and payload["preview_dpi"] is not None:
            preview_dpi = _int_arg(payload, "preview_dpi", self.preview_dpi)
            if preview_dpi <= 0:
                raise ProtocolError("bad_request",
                                    f"payload.preview_dpi 必须为正: {preview_dpi}")
        # 可选：把这次的预览 SVG 一并放进响应（与 manifest 原子配对，见 _do_render）。
        # 只认真正的布尔值——"false"/0 这类写法在真值判断下会静默地做反，
        # 而这个字段决定的是「调用方能不能拿到配对的 SVG」，静默出错代价太大。
        inline_svg = payload.get("inline_svg", False)
        if not isinstance(inline_svg, bool):
            raise ProtocolError("bad_request",
                                f"payload.inline_svg 必须是布尔值: {inline_svg!r}")

        try:
            if cmd == "render":
                result = self._do_render(stem, patches, timings, preview_dpi,
                                         inline_svg)
            elif cmd == "render_png":
                result = self._do_render_png(stem, width)
            elif cmd == "preview_png":
                result = self._do_preview_png(
                    stem, patches, width, str(payload.get("tag", "p")))
            elif cmd == "export":
                result = self._do_export(stem, patches, payload["path"],
                                         str(payload.get("format", "pdf")), dpi,
                                         timings)
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 我们也不知道为什么——supervisor 重启后重试一次是合理的
            raise ProtocolError("internal", str(exc) or exc.__class__.__name__,
                                retryable=True,
                                traceback_text=traceback.format_exc()) from exc
        if cmd in _TIMED_COMMANDS:
            result["timings"] = timings
        return result

    def _cancel(self, payload: dict) -> dict:
        """**尽力而为的幂等 no-op**——这是协议里最容易被误解的一条。

        worker 单线程串行读 stdin：正在跑 build/export 的时候根本读不到
        cancel，等读到了那条请求早就结束了。所以这里不假装能中断 matplotlib，
        只回 ok + 一句诚实的 note。真正的硬取消 = supervisor kill 掉进程重启
        （`pool` 的超时路径就是这个语义）。
        """
        target = payload.get("request_id")
        if not isinstance(target, str) or not target:
            raise ProtocolError("bad_request", "cancel 需要 payload.request_id")
        seen = target in self._seen
        note = ("该请求已执行完毕，取消无事可做（worker 串行执行，"
                "读到 cancel 时它必然已结束）" if seen else
                "没见过这个 request_id（可能已被淘汰出最近记录，或从未到达）")
        return {"note": note, "cancelled": False, "seen": seen}


def _echo(req: dict) -> dict:
    """v1 响应里原样回显的信封字段。

    generation / revision / hash **worker 一律不解释**：它们是 supervisor 的
    账本（哪一代 worker、渲染到第几版、这组 patch 的身份），worker 插手只会
    多出一个可能与账本不一致的地方。回显让上层能把响应对回请求。
    """
    out = {"protocol_version": PROTOCOL_VERSION,
           "request_id": req.get("request_id")}
    for key in ("worker_generation", "render_revision", "canonical_patch_hash"):
        if key in req:
            out[key] = req[key]
    return out


def _hash_check(req: dict) -> dict:
    """自己也算一遍 canonical hash，与请求里带的对不上就标记出来。

    **不拒绝执行**：分歧只可能来自「另一种语言的序列化实现」（未来的 Rust
    supervisor），当场拒绝会把一个可观测的对齐问题变成一次用户可见的渲染
    失败。标记 + stderr 警告，让上层发现并去修序列化。
    """
    claimed = req.get("canonical_patch_hash")
    if not isinstance(claimed, str) or not claimed:
        return {}
    payload = req.get("payload")
    patches = payload.get("patches", []) if isinstance(payload, dict) else []
    mine = patchspec.patch_hash(patches)
    if mine == claimed:
        return {}
    print(f"[protocol] canonical_patch_hash 不一致: 请求 {claimed} / "
          f"本地 {mine}（照常执行，请检查两侧的规范化实现）", file=sys.stderr)
    return {"hash_mismatch": True, "worker_patch_hash": mine}


def _v1_error(req: dict, exc: ProtocolError) -> dict:
    err = {"code": exc.code, "retryable": exc.retryable,
           "message": exc.message, "traceback": exc.traceback_text}
    err.update(exc.extra)
    return {"ok": False, **_echo(req), "error": err}


def _respond(worker: "Worker", req: object) -> dict:
    """按信封分派。带 `protocol_version` 走 v1，否则走 legacy（形状不变）。"""
    if not isinstance(req, dict):
        return _v1_error({}, ProtocolError("bad_request", "请求必须是 JSON 对象"))
    if "protocol_version" not in req:
        return worker.handle(req)
    try:
        result = worker.handle_v1(req)
    except ProtocolError as exc:
        return _v1_error(req, exc)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 兜底，绝不让 worker 静默退出
        return _v1_error(req, ProtocolError(
            "internal", str(exc) or exc.__class__.__name__, retryable=True,
            traceback_text=traceback.format_exc()))
    return {"ok": True, **_echo(req), **_hash_check(req), **result}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--figures-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--entry", default="main")
    ap.add_argument("--preview-dpi", type=int, default=200)

    # 协议管道钉死 UTF-8。Windows 的默认 stdio 编码跟着系统区域走（常是
    # cp1252/cp936），而回应里 ensure_ascii=False——中文标签、µ、⁻¹ 这类字符
    # 一出现就 UnicodeEncodeError 把 worker 整个打死，表现为「worker 无响应」。
    # errors="replace" 是最后一道保险：宁可某个字符显示成 ? 也不能让会话崩掉。
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    worker = Worker(ap.parse_args())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            # 连信封都解析不出来，无从判断对方说的是哪套协议：按 v1 的错误
            # 形状回（request_id 只能是 null），至少 code 是可读的。
            resp = _v1_error({}, ProtocolError("bad_request", f"JSON 解析失败: {exc}"))
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = _respond(worker, req)
        except SystemExit:
            break
        except Exception as exc:  # noqa: BLE001 — 结构化返回，进程不退出
            resp = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=_json_default) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
