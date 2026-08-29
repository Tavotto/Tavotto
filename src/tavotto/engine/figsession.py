"""LiveFigureSession —— 一组常驻内存 Figure 的**唯一一套**编辑语义。

## 为什么有这个模块

`worker.py` 里长期把两件事焊在一起：**怎么把用户脚本跑起来**（safe 沙盒：
cwd 切走、argv 换掉、savefig 吞掉、写/删守卫、相对路径只读回退）和
**Figure 到手之后怎么用**（instrument → manifest → 应用 override → 出预览
SVG / PNG / 导出 → 快照还原）。只有一条入口时这没问题。

native bridge（ADR 0020）把第一件事整个换掉了——脚本由**用户自己的 Python**
在**用户自己的进程**里按原样跑，Tavotto 只挂钩子——但第二件事必须一个字节
都不变（总纲原则 1：只有一套 Figure 编辑语义，不得出现第二份 manifest
builder / 第二份 override setter）。抄一份进 bridge 的代价不是"多一点重复
代码"，而是**同一张图在两条入口里 manifest 不一样**——前端按 gid 索引一切，
那是数据级的错位，且只会在用户那边、在某一族 artist 上、在几周之后暴露。

所以第二件事收在这里，safe worker 与 native bridge 各调一次。

## 线程归属（native bridge 的硬约束）

Matplotlib 的 Figure 不是线程安全的，而 native bridge 的控制通道天生想开一个
后台线程去读 socket。`LiveFigureSession` 因此**记住创建它的那个线程**，每个
会改变 Figure 或从 Figure 取几何的方法都先核对一次线程身份：拿 Figure 的进程
里，只有拥有它的那个线程可以动它。这条不是约定而是断言——约定会在某次"顺手
把渲染挪到回调里"之后静默失效，而失效的表现是随机的段错误或画错的图。
看护：`tests/bridge/test_bridge_thread_model.py`。

safe worker 本来就是单线程串行读 stdin，这条断言对它恒真（等于免费）。

## 不在这里的东西

* **怎么跑脚本**（沙盒 / 守卫 / argv / 钩子安装）——safe 在 `worker.py`，
  native 在 `bridge_runner.py`；
* **协议信封**——`wireproto.py`；
* **捕获策略**（stem 怎么取、pyplot 兜底怎么收、描述符怎么造）——`figcapture`，
  本模块是它的消费者。

纯 matplotlib + 兄弟模块；worker / bridge_runner 都平铺 import 它。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import figcapture
import manifest as manifest_mod
import overrides as overrides_mod
import preview_hybrid
import previewbudget

__all__ = ["LiveFigureSession", "WrongThread", "ms_since"]


class WrongThread(RuntimeError):
    """在不拥有 Figure 的线程上动了 Figure。**永远是缺陷，不是运行时状况。**"""


def ms_since(t0: float) -> float:
    """自 `t0`（perf_counter）以来的毫秒数，保留三位小数。

    用 `perf_counter` 而不是 `time.time()`：后者会被系统改时间 / NTP 校正
    带偏，而这些数字是要拿去做「哪一段最慢」的判断的。
    """
    return round((time.perf_counter() - t0) * 1000.0, 3)


def _json_default(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


class LiveFigureSession:
    """捕获到的一组 Figure + 它们的可编辑状态。

    `out_dir` 是本会话的产物目录（预览 SVG / manifest JSON / PNG）。
    `preview_dpi` 只影响预览 SVG 里**嵌入位图**的分辨率。
    """

    def __init__(self, out_dir: str | Path, preview_dpi: int = 200):
        self.out_dir = Path(out_dir)
        self.preview_dpi = int(preview_dpi)
        #: stem -> Figure（捕获顺序；dict 保序）
        self.capture: dict[str, object] = {}
        #: stem -> figcapture.SOURCE_*。`pyplot` 的那些从没存过盘，**没有原始
        #: 产物可写回**——调用方如实带出去，别让上层以为「捕获到了」就等于
        #: 「磁盘上有一份原件」。
        self.capture_source: dict[str, str] = {}
        #: stem -> FigState（`instrument()` 之后才有）
        self.states: dict[str, overrides_mod.FigState] = {}
        self._manifest_cache: dict[str, dict] = {}
        #: 拥有这些 Figure 的线程。见模块头「线程归属」。
        self.owner_thread = threading.get_ident()

    # ---------------- 线程归属 ----------------
    def _own(self) -> None:
        cur = threading.get_ident()
        if cur != self.owner_thread:
            raise WrongThread(
                f"Figure 属于线程 {self.owner_thread}，本次调用在线程 {cur}——"
                f"matplotlib 的 Figure 不是线程安全的，改动必须回到拥有它的线程"
                f"（native bridge 的控制通道请把请求投递给主线程执行）"
            )

    # ---------------- 捕获表 ----------------
    def add_figure(self, stem: str, fig, source: str) -> bool:
        """把一张 Figure 记进捕获表。已有同名 stem 时**不覆盖**，返回 False。

        `setdefault` 语义是刻意的：显式 `savefig("Fig1.pdf")` 先认领了 stem，
        脚本跑完 pyplot 兜底再遇到同一张图时不该把它顶掉（来源也就跟着变了）。
        """
        if source not in (figcapture.SOURCE_SAVEFIG, figcapture.SOURCE_PYPLOT):
            raise ValueError(f"capture_source 非法: {source!r}")
        if stem in self.capture:
            return False
        self.capture[stem] = fig
        self.capture_source[stem] = source
        return True

    def instrument_all(self) -> None:
        """给捕获表里还没有 FigState 的图建状态并出一次预览。

        可重入：native bridge 在每次 `plt.show()` 屏障处都会再调一次，
        只有新出现的图会被 instrument（已有的 FigState 带着用户的 override，
        重建等于把编辑丢掉）。
        """
        self._own()
        for stem, fig in self.capture.items():
            if stem in self.states:
                continue
            state = overrides_mod.FigState(fig)
            manifest_mod.instrument(state)
            self.states[stem] = state
            self.render(stem)

    def stems_summary(self, dropped_figures: int = 0) -> dict:
        """build 响应的 stems 表。

        `source` 回答的是「这张图有没有原始产物」——`pyplot` 的那些从没存过
        盘，渲染 / 编辑 / 导出都成立，写回无从谈起。
        """
        out = {
            "stems": {
                s: {
                    "size_mm": self._manifest_cache[s]["size_mm"],
                    "source": self.capture_source.get(s, figcapture.SOURCE_SAVEFIG),
                }
                for s in self.states
            }
        }
        if dropped_figures:
            out["dropped_figures"] = dropped_figures
        return out

    def descriptors(
        self,
        *,
        script: str,
        entry: str,
        execution_profile: str,
        source_fingerprint: str,
        project_root: str | None,
    ) -> list[dict]:
        """每张捕获 Figure 的统一描述——**语义全在 figcapture，这里只是装配**。

        原始产物只对 savefig 来源的 stem 查（pyplot 捕获的图从没存过盘，磁盘上
        碰巧同名的文件不是它的原件，工厂对「pyplot + 产物」直接抛）。
        `project_root=None` 表示这条入口不谈原始产物（native bridge 的
        passthrough savefig 写到哪由用户脚本决定，不是我们的图库）。
        """
        out = []
        for stem in self.states:  # 捕获顺序（dict 保序）
            source = self.capture_source.get(stem, figcapture.SOURCE_SAVEFIG)
            artifact = None
            if source == figcapture.SOURCE_SAVEFIG and project_root is not None:
                artifact = figcapture.find_original_artifact(project_root, stem)
            out.append(
                figcapture.build_descriptor(
                    script=script,
                    entry=entry,
                    stem=stem,
                    capture_source=source,
                    execution_profile=execution_profile,
                    size_mm=figcapture.size_mm_of(self.states[stem].fig),
                    source_fingerprint=source_fingerprint,
                    original_artifact=artifact,
                ).to_payload()
            )
        return out

    # ---------------- 渲染 ----------------
    def render(
        self,
        stem: str,
        timings: dict | None = None,
        preview_dpi: int | None = None,
        preview: dict | None = None,
    ) -> dict:
        """导出预览 SVG + 重建 manifest，写入 out_dir。

        **表示法在这里定，两条入口共用**（ADR 0022）：冷 build（`instrument_all`
        走这里）与热 render（`do_render` 走这里）必须落到同一条 hybrid 策略上
        ——只在 render request 上 rasterize 的话，用户**第一次打开** #181 那张
        图仍然要先等十几秒把 66 万个 `<path>` 画出来，那不叫修好。

        `preview_dpi` 影响 SVG 里**嵌入位图**的分辨率——hybrid 之后这句话才
        真正管用：mesh 层变成 `<image>`，dpi 直接决定它多大（实测同一张图
        dpi 72 → 310 KB、dpi 200 → 600 KB）。纯矢量图上它仍然一分钱都不值
        （dpi 72→300 耗时与体积一模一样），含 imshow 的图上 200→100 能让
        savefig 从 ~29ms 降到 ~17ms、SVG 从 827KB 降到 196KB。

        计时口径（`timings` 非空时填）：`manifest_ms` 是 `build_manifest`
        （其中包含一次 `fig.canvas.draw()`——量每个元素的包围盒必须有
        renderer）；`preview_plan_ms` 是复杂度分析；`canvas_draw_ms` 是
        `savefig(svg)`（升档时是**两遍之和**——用户等的就是两遍）。**SVG
        序列化与 draw 在 matplotlib 里分不开**（`print_svg` 是「边画边写」的
        一趟），所以不单出 `svg_ms`，见 ADR 0003 §9。

        `preview` 是**出参**（与 `timings` 同一条纪律，ADR 0003 §1）：给一个
        dict 就往里填这一版的表示法元数据。**manifest 在 rasterize 之前就建完
        了**——语义保真（不变量 1）不是靠谁记得，是靠这个顺序。
        """
        self._own()
        state = self.states[stem]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        man = manifest_mod.build_manifest(state, stem)
        t1 = time.perf_counter()
        # **传文件对象、且 `newline=""`，不许传路径。** matplotlib 拿到路径时走
        # `cbook.to_filehandle` → `open(fname, "w", encoding=…)`——**没有
        # `newline` 参数**，于是 Windows 上每个 `\n` 被翻成 `\r\n`。
        #
        # 后果不是「文件大一点」：`svg_bytes` 是**判定量**（`resolve_mode`
        # 拿它决定 vector 还是 raster），而它取自 `stat().st_size`。同一张图在
        # Windows 上因此显得大约 **+3.8%**（实测 22511 vs 21688，差值正好是
        # 换行数），**更早掉进 raster**——而没有任何地方会报错，用户看到的是
        # 「同一份项目，在 Windows 上预览掉档了」。
        #
        # 这也让 `do_render` 那句「读回磁盘那一份，与 out_dir/<stem>.svg 逐字节
        # 相同」重新成立——在此之前它在 Windows 上是假的（读回来时
        # universal-newlines 又把 `\r\n` 翻回 `\n`，两侧字节数对不上）。
        #
        # 抓到它的是 `test_v1_render_reports_the_preview_verdict` 在
        # `backend-platforms (windows-latest)` 上——PR 上那一格是 skipping，
        # 所以本机与 PR 全绿都是真的，它只在 merge_group 里发作。
        svg_path = self.out_dir / f"{stem}.svg"
        dpi = preview_dpi or self.preview_dpi

        def _save(_plan) -> int:
            with self.real_output(), open(svg_path, "w", encoding="utf-8", newline="") as fh:
                state.fig.savefig(fh, format="svg", dpi=dpi)
            try:
                return svg_path.stat().st_size
            except OSError:
                # 刚写完它，这里读不到 size 说明磁盘 / 权限出了事。按 0 记会把
                # 这一版说成「很小的矢量图」，接着 read_text 也一定会抛——不如
                # 当场按最坏处理：不读，降到 raster。
                return previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES

        # `preview_plan_ms` / `canvas_draw_ms` 由 `save_preview_svg` 自己填——
        # 只有它知道那两段各自从哪到哪（升档时 savefig 跑两遍）。
        plan, svg_bytes = preview_hybrid.save_preview_svg(state, _save, timings)
        if timings is not None:
            timings["manifest_ms"] = round((t1 - t0) * 1000.0, 3)
        if preview is not None:
            mode, reason = previewbudget.resolve_mode(
                svg_bytes=svg_bytes, rasterized_artist_count=plan.rasterized_artist_count
            )
            preview.update(
                previewbudget.metadata(
                    svg_bytes=svg_bytes,
                    mode=mode,
                    reason=reason,
                    rasterized_artist_count=plan.rasterized_artist_count,
                    estimated_primitives=plan.estimated_primitives,
                    estimated_vertices=plan.estimated_vertices,
                )
            )
        (self.out_dir / f"{stem}.json").write_text(
            json.dumps(man, ensure_ascii=False, default=_json_default), encoding="utf-8"
        )
        self._manifest_cache[stem] = man
        return man

    def manifest(self, stem: str) -> dict:
        return self._manifest_cache[stem]

    def real_output(self):
        """引擎自己写盘的那一段（预览 / 导出）——**由入口决定要不要绕开钩子**。

        safe worker 在这里摘掉 savefig 拦截（build 期间脚本一个图文件都不写，
        但引擎自己的预览必须写得出去）；native bridge 的 savefig 本来就是透传，
        它给的是一个什么都不做的上下文。默认无操作。
        """
        return _NULL_CTX

    # ---------------- 命令原语（两套信封 / 两条入口共用同一份实现） ----------------
    def snapshot(self, stem: str) -> list[dict]:
        """当前会话已应用的 override，作为「全量列表」形状的快照。"""
        state = self.states[stem]
        return [{"gid": g, "prop": p, "value": v} for (g, p), v in state.applied.items()]

    def do_render(
        self,
        stem: str,
        patches: list,
        timings: dict | None = None,
        preview_dpi: int | None = None,
        inline_svg: bool = False,
        preview: dict | None = None,
    ) -> dict:
        """应用全量 override 列表 + 重出预览 SVG/manifest（v1 的 render）。

        `inline_svg=True` 时响应里**多带一份 SVG 文本**。为什么要它：SVG 与
        manifest 必须成对——另一个标签页（或同一文件的另一个变体）的渲染插进来
        之后，第二跳 GET 拿到的磁盘 SVG 已经是别人的了，而 manifest 是这次的，
        画布上就出现「框选命中的元素和看到的图对不上」。会话串行执行，在这里
        把刚写完的那份读回来天然原子。

        **超过硬闸时不读**（ADR 0022 不变量 3）。判据吃的是 `stat().st_size`，
        因为「先 read 126 MB 再说太大」根本不算保护：实测那一读加上两次
        JSON 编解码就能让 Flask 进程峰值 RSS 到 1.2 GB，而 SVG 一个字节都还
        没到浏览器。这时响应里 **`svg` 整个不出现**——**它仍然是一次成功的
        渲染**（manifest / warnings / timings 齐全），不是一次失败。

        `preview` 是**出参**，与 `timings` 同一条纪律（ADR 0003 §1）：给一个
        dict 就往里填这一版的表示法元数据，不给就一个字段都不多。这道弯是
        为了 **legacy 扁平信封的形状一字不改**——它的响应契约就是
        `{ok, manifest, warnings}`，手工 `echo '{"cmd":"override"}'` 调试与
        任何还没切过来的调用方都靠它（看护
        `test_legacy_envelope_keeps_the_old_response_shape`）。
        **判定本身与信封无关**：两条信封上「超限就不读」都照常发生，v1 多的
        只是把理由说出来。
        """
        self._own()
        t0 = time.perf_counter()
        warnings = overrides_mod.apply(self.states[stem], patches)
        if timings is not None:
            timings["patch_apply_ms"] = ms_since(t0)
        # **裁决永远发生，信封只决定说不说**：`verdict` 是本地的，legacy 扁平
        # 信封不给 `preview` 时它照样按同一条闸决定读不读 SVG。
        verdict: dict = {}
        result = {
            "manifest": self.render(stem, timings, preview_dpi, preview=verdict),
            "warnings": warnings,
        }
        if preview is not None:
            preview.update(verdict)
        if inline_svg and verdict["mode"] != previewbudget.MODE_RASTER:
            # 读回磁盘那一份而不是另存一个内存缓冲：调用方拿到的与
            # out_dir/<stem>.svg 逐字节相同，排障时不必怀疑「是不是两份」
            result["svg"] = (self.out_dir / f"{stem}.svg").read_text(encoding="utf-8")
        return result

    def do_render_png(self, stem: str, width: int) -> dict:
        """从 live figure 按目标像素宽出高清位图（imshow 类面板显示用）。"""
        self._own()
        state = self.states[stem]
        w_in = float(state.fig.get_size_inches()[0])
        path = self.out_dir / f"{stem}_w{int(width)}.png"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.real_output():
            state.fig.savefig(path, format="png", dpi=max(50, int(width) / w_in))
        return {"path": str(path)}

    def do_preview_png(self, stem: str, patches: list, width: int, tag: str) -> dict:
        """历史版本预览：临时应用指定 patches 出图，随后还原当前会话状态。"""
        self._own()
        state = self.states[stem]
        prev = self.snapshot(stem)
        # `try` 必须从 apply 之前起：apply 自己会抛（属性不认、值越界），
        # 起晚了的话异常路径上还原就不执行，这次预览专用的 patches 留在常驻
        # figure 上，此后前端手里的 lastPatches 与会话真实状态错位。
        try:
            overrides_mod.apply(state, patches)
            w_in = float(state.fig.get_size_inches()[0])
            path = self.out_dir / f"{stem}__{tag}.png"
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with self.real_output():
                state.fig.savefig(path, format="png", dpi=max(50, int(width) / w_in))
        finally:
            overrides_mod.apply(state, prev)
        return {"path": str(path)}

    def do_export(
        self,
        stem: str,
        patches: list,
        path: str,
        fmt: str = "pdf",
        dpi: int = 600,
        timings: dict | None = None,
    ) -> dict:
        """全质量导出（供 PyMuPDF 合成）。

        与 preview_png 同一纪律：export 是**状态中立**的一次性动作。
        不还原的话导出用的 patches 会留在常驻 figure 上——历史版本恢复、
        画布导出（各面板自带一套 overrides）之后，热会话的真实状态就与
        前端手里的 lastPatches 错位，下一次 render 的「全量列表」语义
        会拿着错的 applied 表去做还原。
        """
        self._own()
        state = self.states[stem]
        prev = self.snapshot(stem)
        out = Path(path)
        # `try` 从 apply 之前起（见 do_preview_png 的同款说明）：apply 与
        # mkdir 都会抛——目标目录不可写、路径过长、Windows 上被占用——而它们
        # 恰恰是最需要还原的那两步。画布合成导出用的是**热会话**，一次没还原
        # 就把这一个面板的 overrides 留在了下一个面板的渲染上。
        try:
            t0 = time.perf_counter()
            warnings = overrides_mod.apply(state, patches)
            t1 = time.perf_counter()
            out.parent.mkdir(parents=True, exist_ok=True)
            with self.real_output():
                state.fig.savefig(out, format=fmt, dpi=int(dpi))
            if timings is not None:
                timings["patch_apply_ms"] = round((t1 - t0) * 1000.0, 3)
                # 还原那一次的耗时**不算进 export_ms**：它是状态中立这条纪律的
                # 代价，不是用户等的那张图的成本（但它确实要花时间，见 total_ms）
                timings["export_ms"] = ms_since(t1)
        finally:
            # 还原那次的 warnings 丢弃：报给调用方的必须是「这组 patches
            # 有没有写不进去的」，混进还原噪音会让写回自检误判。
            overrides_mod.apply(state, prev)
        return {"path": str(out), "warnings": warnings}


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


_NULL_CTX = _NullCtx()
