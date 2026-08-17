#!/usr/bin/env python3
"""渲染 worker 子进程（系统 python3，需 matplotlib 科学栈）。

跑一个 fig 脚本一次，把产出的 Figure 常驻内存（live-figure 会话），
之后通过 stdin/stdout 的 JSON 行协议接受指令：

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
import contextlib
import importlib
import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure as mfigure  # noqa: E402

import manifest as manifest_mod  # noqa: E402
import overrides as overrides_mod  # noqa: E402

CAPTURE: dict[str, object] = {}   # stem -> Figure（脚本产出顺序）
STATES: dict[str, overrides_mod.FigState] = {}
_intercept = True
_REAL_SAVEFIG = mfigure.Figure.savefig


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

    # ---------------- build ----------------
    def build(self) -> dict:
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

        with contextlib.redirect_stdout(sys.stderr):
            if self.entry == "__main__":
                import runpy  # noqa: PLC0415 — 内联脚本（fig4c / fig_models）
                runpy.run_path(str(self.script), run_name="__main__")
            else:
                module = importlib.import_module(self.script.stem)
                getattr(module, self.entry)()

        for stem, fig in CAPTURE.items():
            state = overrides_mod.FigState(fig)
            manifest_mod.instrument(state)
            STATES[stem] = state
            self._render(stem)
        self.built = True
        return self._stems_summary()

    def _stems_summary(self) -> dict:
        return {"stems": {s: {"size_mm": self._manifest_cache[s]["size_mm"]}
                          for s in STATES}}

    def _render(self, stem: str) -> dict:
        """导出预览 SVG + 重建 manifest，写入 out_dir。"""
        state = STATES[stem]
        man = manifest_mod.build_manifest(state, stem)
        with _real_output():
            state.fig.savefig(self.out_dir / f"{stem}.svg", format="svg",
                              dpi=self.preview_dpi)
        (self.out_dir / f"{stem}.json").write_text(
            json.dumps(man, ensure_ascii=False, default=_json_default), encoding="utf-8")
        self._manifest_cache[stem] = man
        return man

    # ---------------- commands ----------------
    def handle(self, req: dict) -> dict:
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
        state = STATES[stem]

        if cmd == "override":
            warnings = overrides_mod.apply(state, req.get("patches", []))
            man = self._render(stem)
            return {"ok": True, "manifest": man, "warnings": warnings}

        if cmd == "preview_png":
            # 历史版本预览：临时应用指定 patches 出图，随后还原当前会话状态
            w_px = int(req.get("width", 400))
            tag = str(req.get("tag", "p"))
            prev = [{"gid": g, "prop": p, "value": v}
                    for (g, p), v in state.applied.items()]
            overrides_mod.apply(state, req.get("patches", []))
            w_in = float(state.fig.get_size_inches()[0])
            path = self.out_dir / f"{stem}__{tag}.png"
            with _real_output():
                state.fig.savefig(path, format="png", dpi=max(50, w_px / w_in))
            overrides_mod.apply(state, prev)
            return {"ok": True, "path": str(path)}

        if cmd == "render_png":
            # 从 live figure 按目标像素宽出高清位图（imshow 类面板显示用）
            w_px = int(req.get("width", 800))
            w_in = float(state.fig.get_size_inches()[0])
            path = self.out_dir / f"{stem}_w{w_px}.png"
            with _real_output():
                state.fig.savefig(path, format="png", dpi=max(50, w_px / w_in))
            return {"ok": True, "path": str(path)}

        if cmd == "export":
            warnings = overrides_mod.apply(state, req.get("patches", []))
            out = Path(req["path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            with _real_output():
                state.fig.savefig(out, format=req.get("format", "pdf"),
                                  dpi=int(req.get("dpi", 600)))
            return {"ok": True, "path": str(out), "warnings": warnings}

        return {"ok": False, "error": f"未知指令: {cmd}"}


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
            resp = worker.handle(json.loads(line))
        except SystemExit:
            break
        except Exception as exc:  # noqa: BLE001 — 结构化返回，进程不退出
            resp = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=_json_default) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
