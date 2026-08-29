"""浏览器 playground 的引擎适配层（Pyodide 里跑，也直接跑在 CPython 测试里）。

网站 `/try` 的浏览器 playground 把用户的 `.py` 在 **Pyodide（WebAssembly）**
里跑一遍，然后用 Tavotto 现有的前端画布做语义编辑。本模块是那条路径的
Python 侧全部：跑脚本、捕获 Figure、建 FigState/manifest、应用 override、
出 SVG。语义层**没有第二份实现**——manifest / overrides / pathgeom /
patchspec 都是 `engine/` 里的同一份文件，与桌面 worker（`worker.py`）
完全同源。ADR 0007 记录了整个决定。

与 `worker.py` 的关系：worker 面向的是「常驻子进程 + stdin/stdout 协议 +
真实文件系统」，这里面向的是「同进程调用 + JSON 字符串出入 + 虚拟文件系统」。
进程、超时、取消全部归 JS 侧（Web Worker 边界），所以这里没有协议信封、
没有超时、没有沙盒守卫——Pyodide 的 FS 本来就是虚拟的。**Figure 执行语义
照抄 worker**：拦截 `Figure.savefig` 按 stem 捕获、`sys.argv` 换成脚本
自己的、`run_name="__main__"`；此外多一条 pyplot 兜底（`plt.plot(...);
plt.show()` 这类从不 savefig 的脚本也要能用）。

平铺 import（`import manifest` 而不是 `from . import`）：与 worker.py 同一
纪律——engine 目录整个塞进 sys.path。Pyodide 侧由 JS 把 engine.zip 解到
/engine 并 `sys.path.insert`；pytest 侧由测试驱动脚本做同样的事。

出入口是 `handle(request_json) -> response_json`：JS 只拿到一个函数引用，
每次调用一进一出都是 JSON 字符串——不留 PyProxy、不传对象图，Worker 一死
什么都不泄漏。失败一律 `{ok: false, code, message, ...}`，code 是稳定的
机器可读值（错误分诊在前端做），traceback 原文附带但有界。

「源文件未被修改」是**可验证的结论**而不是口号，但**权威摘要不在这个模块里**：
界面用的那个由 Worker 侧的 JS 直接从 Emscripten FS 读字节、用 Web Crypto 算
（`pyodide.worker.ts` 的 `fsDigest`）。原因很实在——用户脚本跑在**同一个
解释器**里、而且跑在核对**之前**，它改完自己的文件再 monkeypatch
`builtins.open` 或换掉 `hashlib.sha256` 就能让下面这个函数继续回报原摘要。
**一个能被它所校验的代码改写的校验，不叫校验。**

这里的 `source_status` 仍然保留并被 CPython 测试盖着：它验的是引擎语义
（写进去的就是收到的、脚本跑完还是那份），跑在 Pyodide 之外、没有那个威胁
模型。两者要的东西不同，别把其中一个删掉当重复。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import runpy
import sys
import traceback

import matplotlib

from browser_imports import classify_imports  # noqa: F401 - 经 handle 的 classify 代理

matplotlib.use("Agg")
import matplotlib.figure as mfigure  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

import figcapture  # noqa: E402
import manifest as manifest_mod  # noqa: E402
import overrides as overrides_mod  # noqa: E402
import patchspec  # noqa: E402
import preview_hybrid  # noqa: E402
import previewbudget  # noqa: E402

#: 源文件上限。JS 侧在读文件时就拦，这里再守一道——两侧都必须拦：
#: 只有 JS 拦的话，绕过页面直接 postMessage 的调用就没人管。
MAX_SOURCE_BYTES = 256 * 1024
#: 脚本 stdout/stderr 的保留上限（只留**尾部**——报错几乎总在最后）。
MAX_LOG_BYTES = 64 * 1024
#: traceback 上限（同样留尾部）。
MAX_TRACEBACK_BYTES = 16 * 1024
#: 捕获 Figure 数上限。超出的不是静默丢——响应里带 truncated 标记。
#: 取的是桌面 pyplot 兜底那个数（`figcapture.MAX_PYPLOT_FALLBACK`），但**两侧
#: 作用的对象不同**，别照着旧注释以为「图数因此不会分叉」——那句话是错的：
#:
#:   桌面   `collect_pyplot_figures(limit=8)` 只截**待补的 pyplot 兜底**，
#:          savefig 认领的那些不受限 → 8 张 savefig + 1 张 show-only = 9 张
#:   这里   截的是**总数** → 同一个脚本只剩 8 张
#:
#: 差异是自觉的（浏览器里内存与下载都更紧，总量必须有上限），所以不改行为，
#: 改的是别再声称它不存在：CompatBench 的对拍现在逐条比张数与截断
#: （`_browser_verdict`），分叉会被报出来而不是被这句注释盖住。
MAX_FIGURES = figcapture.MAX_PYPLOT_FALLBACK
#: 预览 SVG 里嵌入位图的默认 dpi，与 worker 的 `--preview-dpi` 默认一致。
PREVIEW_DPI = 200
#: 图选择器缩略图的目标像素宽。
THUMB_PX = 420

_intercept = True
_REAL_SAVEFIG = mfigure.Figure.savefig


def _patched_savefig(self, fname, *args, **kwargs):
    """与 worker._patched_savefig 同语义：按 stem 捕获，不写用户的输出文件。"""
    if not _intercept:
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)
    stem = figcapture.savefig_stem(fname)
    if stem:
        _session_capture().setdefault(stem, self)
        # 来源记账与 worker.CAPTURE_SOURCE 同语义：savefig 认领的 stem
        # **可能**有原始产物（在桌面上；这里的虚拟 FS 里永远没有）。
        _session_sources().setdefault(stem, figcapture.SOURCE_SAVEFIG)
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
    """numpy 标量 → float，其余 → str。与 worker._json_default 同源同语义。"""
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


class _TailBuffer(io.TextIOBase):
    """有界的文本缓冲：只留最后 `limit` 字节（按 UTF-8 计）。

    死循环里的 print 会把内存写爆——日志是诊断材料不是主界面，留尾部就够
    （报错几乎总在最后）。截断过就在开头标一句，绝不假装是全文。
    """

    def __init__(self, limit: int = MAX_LOG_BYTES):
        super().__init__()
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self._truncated = False

    def writable(self) -> bool:  # pragma: no cover - io 协议
        return True

    def write(self, s: str) -> int:
        data = s.encode("utf-8", errors="replace")
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self._limit and len(self._chunks) > 1:
            dropped = self._chunks.pop(0)
            self._size -= len(dropped)
            self._truncated = True
        if self._size > self._limit:  # 单条就超限
            keep = self._chunks[0][-self._limit :]
            self._size = len(keep)
            self._chunks[0] = keep
            self._truncated = True
        return len(s)

    def text(self) -> str:
        body = b"".join(self._chunks).decode("utf-8", errors="replace")
        return ("[output truncated]\n" + body) if self._truncated else body


def _tail(text: str, limit: int) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text
    return "[truncated]\n" + data[-limit:].decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 会话


class BrowserSession:
    """一次 playground 会话：一个源文件、一批捕获的 Figure、若干编辑状态。

    「换一个文件 = 换一个 Worker = 换一个全新 Pyodide 会话」是 JS 侧的纪律
    （ADR 0007）：这里**不做**跨文件的状态清洗，Worker 一死一切归零，
    这比在活着的解释器里追着清 matplotlib 的全局状态可靠得多。
    """

    def __init__(self, workspace: str = "/workspace"):
        self.workspace = workspace
        self.capture: dict[str, object] = {}  # stem → Figure（脚本产出顺序）
        self.capture_source: dict[str, str] = {}  # stem → figcapture.SOURCE_*
        self.states: dict[str, overrides_mod.FigState] = {}
        self.revision = 0
        self.script_name = ""
        self.loaded = False

    # ---------------- 跑脚本 ----------------
    def load(self, filename: str, source: str) -> dict:
        if self.loaded:
            # 一个会话只跑一个文件（见类注释）。这里报错而不是悄悄重置。
            return _err("bad_request", "会话已加载过脚本；换文件要换一个新会话")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            return _err("source_too_large", f"源文件超过 {MAX_SOURCE_BYTES // 1024} KiB 上限")

        safe_name = _safe_script_name(filename)
        path = os.path.join(self.workspace, safe_name)
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            return _err(
                "syntax_error",
                f"{exc.msg} (line {exc.lineno})",
                line=exc.lineno,
                traceback=_tail("".join(traceback.format_exception_only(exc)), MAX_TRACEBACK_BYTES),
            )

        os.makedirs(self.workspace, exist_ok=True)
        # **二进制写**：文本模式在 Windows 上把 `\n` 翻成 `\r\n`，磁盘上的字节
        # 就不再是用户交出来的那份，完整性比对当场失效（CI 的 windows 腿实测
        # 逮到过）。Pyodide 的 Emscripten FS 恰好不翻译，所以生产环境看不出来
        # ——一个只在别的平台上成立的不变式不算不变式。
        with open(path, "wb") as f:
            f.write(source.encode("utf-8"))
        os.chdir(self.workspace)
        if self.workspace not in sys.path:
            sys.path.insert(0, self.workspace)

        # 拦截要在脚本 import matplotlib **之前**装好（模块级已 patch），
        # 这里只需要把捕获表指到本会话
        global _ACTIVE
        _ACTIVE = self
        mfigure.Figure.savefig = _patched_savefig
        # Agg 的 show() 只会发一条 UserWarning，patch 成 no-op 让日志干净
        plt.show = lambda *a, **k: None

        sys.argv = [path]
        self.script_name = safe_name
        log = _TailBuffer()
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                runpy.run_path(path, run_name="__main__")
        except SyntaxError as exc:  # exec 期的（比如脚本自己 exec 别的串）
            return _err(
                "syntax_error",
                f"{exc.msg} (line {exc.lineno})",
                log=log.text(),
                traceback=self._trim_tb(),
            )
        except ModuleNotFoundError as exc:
            # 动态 import 静态分类抓不到；名字给出去让前端分诊
            return _err(
                "unsupported_import",
                f"脚本 import 了浏览器环境里没有的模块: {exc.name}",
                modules=[exc.name or ""],
                log=log.text(),
                traceback=self._trim_tb(),
            )
        except FileNotFoundError as exc:
            return _err(
                "missing_file",
                f"脚本要读的文件不存在: {exc.filename or exc}",
                filename=str(exc.filename or ""),
                log=log.text(),
                traceback=self._trim_tb(),
            )
        except MemoryError:
            return _err("out_of_memory", "脚本耗尽了浏览器可用内存", log=log.text())
        except BaseException:  # noqa: BLE001 - 用户代码，什么都可能抛
            return _err("script_error", "脚本执行失败", log=log.text(), traceback=self._trim_tb())

        # pyplot 兜底：从不 savefig 的脚本（plt.plot + plt.show）也要能用。
        # 策略（去重、命名、上限）在 `figcapture` 里，与桌面 worker 共用同一份
        # 实现——两边各写一份的话，同一个脚本会在两个入口产出不同的 stem，
        # 而前端按 stem 索引一切。
        base = os.path.splitext(safe_name)[0]
        fallback, dropped = figcapture.collect_pyplot_figures(
            self.capture, base, plt, limit=MAX_FIGURES
        )
        for stem in fallback:
            self.capture_source[stem] = figcapture.SOURCE_PYPLOT

        truncated = dropped + max(0, len(self.capture) - MAX_FIGURES)
        if len(self.capture) > MAX_FIGURES:
            for stem in list(self.capture)[MAX_FIGURES:]:
                del self.capture[stem]
                self.capture_source.pop(stem, None)

        figures = []
        for stem, fig in self.capture.items():
            w_in, h_in = (float(v) for v in fig.get_size_inches())
            figures.append(
                {
                    "stem": stem,
                    "size_mm": [round(w_in * 25.4, 2), round(h_in * 25.4, 2)],
                    "preview": self._thumb(fig),
                }
            )
        self.loaded = True
        # 完整性哈希在**脚本跑完之后**采：要证明的是「实际被执行的那个文件
        # 此刻仍与你给的一模一样」，写进去就立刻算等于只验了一次 write。
        status = self.source_status()
        # `descriptors` 是加字段（旧前端原样忽略）：与桌面 worker 的 build
        # 响应共用 figcapture 那一份描述符语义（对拍用例钉住）。playground
        # 的**记录在案差异**如实体现在字段值上：entry 恒 "__main__"（按
        # `python figure.py` 语义跑）、original_artifact 恒 None（虚拟 FS
        # 里没有用户原件，写回本来就不存在于这个入口）。
        return {
            "ok": True,
            "figures": figures,
            "log": log.text(),
            "descriptors": self._descriptors(source.encode("utf-8")),
            "truncated_figures": truncated,
            "script": self.script_name,
            "source_sha256": status.get("sha256", ""),
            "source_bytes": status.get("bytes", 0),
        }

    def _descriptors(self, script_bytes: bytes) -> list[dict]:
        """捕获描述符（figcapture 唯一实现的装配，语义见 load 里的注释）。"""
        fingerprint = figcapture.source_fingerprint(
            script_bytes,
            script=self.script_name,
            entry="__main__",
            profile=figcapture.PROFILE_SAFE,
            target_kind="script",
            argv=(),
            passthrough_savefig=False,
            matplotlib_version=matplotlib.__version__,
        )
        return [
            figcapture.build_descriptor(
                script=self.script_name,
                entry="__main__",
                stem=stem,
                capture_source=self.capture_source.get(stem, figcapture.SOURCE_SAVEFIG),
                execution_profile=figcapture.PROFILE_SAFE,
                size_mm=figcapture.size_mm_of(fig),
                source_fingerprint=fingerprint,
                original_artifact=None,
            ).to_payload()
            for stem, fig in self.capture.items()
        ]

    # ---------------- 源文件完整性 ----------------
    def source_status(self) -> dict:
        """把 `/workspace/<脚本>` **从虚拟 FS 读回来**再算 sha256。

        读的是真正被 `runpy` 执行的那个文件，不是内存里那份传进来的字符串。

        **注意这不是界面上那句「未改动」的依据**（见模块 docstring）：它跑在
        用户脚本之后、同一个解释器里，`open` 与 `hashlib` 都在对方够得着的
        地方。界面用的权威摘要由 Worker 的 JS 在 Python 之外算。这个函数的
        价值在 Pyodide 之外——CPython 测试拿它验引擎语义，那里没有这个威胁。
        """
        if not self.script_name:
            return _err("bad_request", "还没有加载脚本")
        path = os.path.join(self.workspace, self.script_name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            return _err("source_unreadable", f"读不到工作区里的源文件: {exc}")
        return {
            "ok": True,
            "script": self.script_name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _trim_tb(self) -> str:
        """用户脚本的 traceback：去掉 runpy/browser 这几层内部帧再截尾。"""
        lines = traceback.format_exc().splitlines(keepends=True)
        # 找到第一条指向 workspace 的帧，从那里开始才是用户自己的调用栈
        start = 0
        for i, ln in enumerate(lines):
            if self.workspace in ln:
                start = i
                break
        return _tail(
            "Traceback (most recent call last):\n" + "".join(lines[start:]), MAX_TRACEBACK_BYTES
        )

    def _thumb(self, fig) -> str:
        """图选择器用的小 PNG（base64）。失败给空串，选择器退回文字条目。"""
        try:
            w_in = float(fig.get_size_inches()[0]) or 1.0
            buf = io.BytesIO()
            with _real_output():
                _REAL_SAVEFIG(fig, buf, format="png", dpi=max(50, THUMB_PX / w_in))
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001 - 缩略图失败不该挡住主流程
            return ""

    # ---------------- 编辑 ----------------
    def _state(self, stem: str) -> overrides_mod.FigState:
        if stem not in self.capture:
            raise KeyError(stem)
        if stem not in self.states:
            state = overrides_mod.FigState(self.capture[stem])
            manifest_mod.instrument(state)
            self.states[stem] = state
        return self.states[stem]

    def open_figure(self, stem: str) -> dict:
        try:
            state = self._state(stem)
            man, svg, preview = self._render(state, stem)
        except KeyError:
            return _err("bad_request", f"没有这个 figure: {stem}")
        except Exception:  # noqa: BLE001 - instrument/manifest/savefig 都可能栽
            return _err("render_error", "渲染失败", traceback=self._trim_tb())
        self.revision += 1
        return {
            "ok": True,
            "stem": stem,
            "script": self.script_name,
            "manifest": man,
            "svg": svg,
            "preview": preview,
            "patch_hash": patchspec.patch_hash([]),
            "render_revision": self.revision,
            "warnings": [],
        }

    def render(self, stem: str, patches: list, preview_dpi: int | None = None) -> dict:
        try:
            state = self._state(stem)
        except KeyError:
            return _err("bad_request", f"没有这个 figure: {stem}")
        except Exception:  # noqa: BLE001 - 首次 instrument 也可能栽
            return _err("render_error", "渲染失败", traceback=self._trim_tb())
        try:
            patch_hash = patchspec.patch_hash(patches)
        except (TypeError, ValueError) as exc:
            return _err("bad_request", f"patches 不合规: {exc}")
        try:
            warnings = overrides_mod.apply(state, patches)
            man, svg, preview = self._render(state, stem, preview_dpi)
        except Exception:  # noqa: BLE001
            return _err("render_error", "应用修改后渲染失败", traceback=self._trim_tb())
        self.revision += 1
        return {
            "ok": True,
            "manifest": man,
            "svg": svg,
            "preview": preview,
            "warnings": warnings,
            "patch_hash": patch_hash,
            "render_revision": self.revision,
        }

    def preview_png(self, stem: str, patches: list, width: int) -> dict:
        """按 patches 出高清位图——**状态中立**，与 worker._do_preview_png 同纪律。"""
        try:
            state = self._state(stem)
        except KeyError:
            return _err("bad_request", f"没有这个 figure: {stem}")
        except Exception:  # noqa: BLE001 - 首次 instrument 也可能栽
            return _err("render_error", "渲染失败", traceback=self._trim_tb())
        prev = [{"gid": g, "prop": p, "value": v} for (g, p), v in state.applied.items()]
        try:
            overrides_mod.apply(state, patches)
            w_in = float(state.fig.get_size_inches()[0]) or 1.0
            buf = io.BytesIO()
            with _real_output():
                _REAL_SAVEFIG(state.fig, buf, format="png", dpi=max(50, int(width) / w_in))
        except Exception:  # noqa: BLE001
            return _err("render_error", "位图预览失败", traceback=self._trim_tb())
        finally:
            with contextlib.suppress(Exception):
                overrides_mod.apply(state, prev)
        return {"ok": True, "png": base64.b64encode(buf.getvalue()).decode("ascii")}

    def _render(self, state, stem: str, preview_dpi: int | None = None):
        """→ `(manifest, svg_or_None, preview)`。

        **超过硬闸就不把 SVG 交出去**（ADR 0022 不变量 3）。桌面那侧判的是
        `stat().st_size`（判定必须在 `read_text` 之前）；这里 SVG 生在内存
        缓冲里，没有那一读——但**放大发生在它之后**：`decode()` 一份 str、
        `json.dumps` 一份、postMessage 过 Worker 边界再一份，然后展开成几十万
        个 DOM 节点。所以判据挪到「交给 JS 之前」，量的是同一个东西
        （字节数），用的是同一份常量。

        hybrid 也在这条路上：名单、设/还原、升档策略与桌面共用
        `preview_hybrid.save_preview_svg`——抄一份过来的代价是同一个脚本在两个
        入口里预览表示法不一样。
        """
        man = manifest_mod.build_manifest(state, stem)
        # **manifest 先建完再 rasterize**——语义保真（不变量 1）靠的是这个顺序，
        # 与桌面那条入口逐字相同（`figsession.render`）。
        buf = io.BytesIO()

        def _save(_plan) -> int:
            nonlocal buf
            buf = io.BytesIO()
            with _real_output():
                _REAL_SAVEFIG(state.fig, buf, format="svg", dpi=preview_dpi or PREVIEW_DPI)
            return buf.tell()

        plan, svg_bytes = preview_hybrid.save_preview_svg(state, _save)
        # manifest 走一遍 JSON 序列化再解回来：worker 是「落盘再读」，这里
        # 等价地把 numpy 标量在**这一层**就规约成纯 JSON 值——交给 JS 的
        # 结构里绝不能混着 numpy 类型
        man = json.loads(json.dumps(man, ensure_ascii=False, default=_json_default))
        mode, reason = previewbudget.resolve_mode(
            svg_bytes=svg_bytes, rasterized_artist_count=plan.rasterized_artist_count
        )
        preview = previewbudget.metadata(
            svg_bytes=svg_bytes,
            mode=mode,
            reason=reason,
            rasterized_artist_count=plan.rasterized_artist_count,
            estimated_primitives=plan.estimated_primitives,
            estimated_vertices=plan.estimated_vertices,
        )
        if mode == previewbudget.MODE_RASTER:
            return man, None, preview
        return man, buf.getvalue().decode("utf-8"), preview


_ACTIVE: BrowserSession | None = None


def _session_capture() -> dict:
    return _ACTIVE.capture if _ACTIVE is not None else {}


def _session_sources() -> dict:
    return _ACTIVE.capture_source if _ACTIVE is not None else {}


def _err(code: str, message: str, **extra) -> dict:
    return {"ok": False, "code": code, "message": message, **extra}


# ---------------------------------------------------------------- JSON 出入口


def _safe_script_name(filename: str) -> str:
    """用户文件名 → 虚拟 FS 里的脚本名。

    尽量保留原名（`Path(__file__).stem` 自命名的脚本靠它），但只认
    「字母数字._-」的 .py 基本名——文件名是 UI 本地数据，进 FS 前收紧一次。
    """
    base = os.path.basename(filename or "")
    stem, ext = os.path.splitext(base)
    ok = (
        ext == ".py"
        and stem
        and all(c.isalnum() or c in "._- " for c in stem)
        and not stem.startswith(".")
    )
    return base if ok else "figure.py"


def handle(request_json: str) -> str:
    """唯一出入口：JSON 字符串进、JSON 字符串出。

    JS 侧只保留这一个函数引用。任何异常都折成 `{ok: false, ...}`——
    这个函数把「Python 会话还活着」作为不变式，抛出去的异常只会让
    Worker 边界把整个会话判死。
    """
    global _ACTIVE
    try:
        req = json.loads(request_json)
        cmd = req.get("cmd")
        if cmd == "classify":
            out = {"ok": True, **classify_imports(req["source"], req["supported_roots"])}
        elif cmd == "safe_name":
            # **无状态、且必须在跑用户脚本之前调**：Worker 拿它把工作区里的
            # 脚本路径钉死在 JS 那一侧。路径要是等 `load` 跑完再从回应里取，
            # 用户脚本就能先留一个内容是原样的诱饵、再把 `_ACTIVE.script_name`
            # 改到诱饵头上——摘要算得再独立也只是在给诱饵作证。
            # 收紧规则只有 `_safe_script_name` 这一份实现，别在 JS 侧抄第二份。
            out = {"ok": True, "script": _safe_script_name(req["filename"])}
        elif cmd == "load":
            if _ACTIVE is None:
                _ACTIVE = BrowserSession(req.get("workspace", "/workspace"))
            out = _ACTIVE.load(req["filename"], req["source"])
        elif cmd in ("open", "render", "preview_png", "source_status"):
            if _ACTIVE is None or not _ACTIVE.loaded:
                out = _err("bad_request", "还没有加载脚本（先发 load）")
            elif cmd == "source_status":
                out = _ACTIVE.source_status()
            elif cmd == "open":
                out = _ACTIVE.open_figure(req["stem"])
            elif cmd == "render":
                out = _ACTIVE.render(req["stem"], req.get("patches", []), req.get("preview_dpi"))
            else:
                out = _ACTIVE.preview_png(
                    req["stem"], req.get("patches", []), int(req.get("width", 800))
                )
        elif cmd == "reset":
            _ACTIVE = None
            plt.close("all")
            out = {"ok": True}
        else:
            out = _err("unknown_cmd", f"未知命令: {cmd!r}")
    except SyntaxError as exc:  # classify 的 ast.parse
        out = _err("syntax_error", f"{exc.msg} (line {exc.lineno})", line=exc.lineno)
    except Exception:  # noqa: BLE001 - 边界函数，绝不向 JS 抛
        out = _err(
            "internal_error",
            "playground 引擎内部错误",
            traceback=_tail(traceback.format_exc(), MAX_TRACEBACK_BYTES),
        )
    return json.dumps(out, ensure_ascii=False, default=_json_default)
