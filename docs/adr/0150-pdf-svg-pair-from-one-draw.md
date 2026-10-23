# ADR 0150：PDF / SVG 配对出自**同一次 draw**（`teedraw`），只算值的回调不再阻断

日期：2026-10-23 · 状态：**Accepted**（用户 2026-10-23 裁决方案 B「一次绘制同时出 PDF 和 SVG」；beta 0.19.0b3 反馈「脚本图导 SVG 报内部错」的根治）
相关：[0144 SVG 默认保留可编辑文字](0144-svg-export-keeps-text-by-default.md)（配对导出的 text 变体）、[0098 savefig frame 即图](0098-savefig-frame-is-the-figure.md)；
规则全文 `docs/rules/backend/export-pipeline.md`「SVG（X03）」；PR #1055（引入配对与 `_svg_pair_blocker`）、#1348（用户话错误码）。

## 动机

Figure 的 PDF / SVG 配对原来是「同一命令里 PDF 画一遍、SVG 再画一遍」（还要把布局引擎钉成 `none`，免得第二遍重排）。
**两次 draw 之间用户回调可能不同**：带计数器的 `FuncFormatter`、`draw_event` 里改文字、自定义 Artist / Locator / scale / transform / path effect。
为了不把「第二遍画出来的另一个场景」标成「同一张 Figure」，`figsession._svg_pair_blocker` 对这些一律不授予 SVG 资格。
结果：研究者最常写的 `FuncFormatter(lambda …)`（XPS 谱图的刻度写法）就让整张图导不出 SVG，用户看到的还是英文内部串（#1348 先把它说成人话）。

## 决策

1. **单次绘制**：matplotlib 自己的 `print_pdf` 照常走，只把它里面的 `backend_pdf.MixedModeRenderer` 换成 `engine/teedraw.TeeRenderer`——
   一次 `Figure.draw`，**同一串绘制调用**同时进 PDF 与各份 SVG（path 基准、保留文字的成品）。场景相同是结构性的，不再靠「证明回调是纯的」。
2. **PDF 是主**：版面（文字度量）、`flipy`、图像合成选项由 PDF 的 renderer 回答（与原先「SVG 复用 PDF 布局」同一口径）；PDF 字节与常规 `savefig` 逐字节相同（`tests/test_teedraw.py`）。
   SVG 目标：每次调用单独 `new_gc()` + `copy_properties`（方向：目标 ← tee 的 gc）；`draw_text` / `draw_tex` 的 y 在 `flipy` 不同的目标上换成 `H - y`；
   `svg.fonttype` / `svg.image_inline` 是 draw 时才读的 rcParam，每个 SVG 目标带自己的 rc、只在转发给它时生效，所以 path 与 text 变体同一次 draw 并存。
3. **SVG 目标失败不拖累 PDF**（该目标停掉并记原因）；`bbox_inches="tight"` 的探测 renderer 建完即弃，SVG 缓冲在内存、只有走到 `finalize()` 的那一组才交给 sink。
4. **阻断收窄（只放行「不接触 renderer」的回调）**：单次绘制路径（`_svg_pair_blocker(fig, single_draw=True)`）放行只算值的回调——
   `FuncFormatter` / Locator / scale / transform 函数 / 图像滤镜（用户反馈的场景）；**仍阻断能看到 renderer 的回调**：`draw_event` 处理器、自定义
   `Artist.draw` / `get_tightbbox`（含自定义 Artist 类）、axes locator、注解坐标回调、path effect。理由（Codex #1355）：这些回调可以按 renderer 的类分派
   （`isinstance(renderer, MixedModeRenderer / RendererPdf)`），而 tee 与 PDF 本来拿到的 renderer 不是同一个类，**连 PDF 自己都可能变**；自检只用内置 artist，测不到。
   另保留自定义 `Figure.savefig` / canvas / 打印方法 / 注册后端（`_svg_pair_env_blocker`）。二次绘制退路与 #1185 证人仍用完整阻断。
   评估过「让 `TeeRenderer` 对 `isinstance` 透明」（子类化 `MixedModeRenderer` 代理）：能骗过 `isinstance(…, MixedModeRenderer)`，但用户代码读 `renderer._renderer`、
   直接判 `RendererPdf`、按 `renderer.__class__.__name__` 分派都无法完整模拟，且无法在三档 matplotlib 上穷尽证明——**不做**，保守放行范围，将来有证据再放宽。
5. **自检先于导出**：`teedraw.status()`（进程内一次）用一张小图核对三件事——tee 的 PDF 与常规 PDF 字节相同；计数器回调的文字调用在 PDF 与 SVG 两路是同一串；两份 SVG 是合法 XML 且 text 变体保留 `<text>`。
   不过（换了 matplotlib、内部名字变了）= 退回二次绘制 + 完整回调阻断，原因带上自检结论（`… (single-draw pairing unavailable: <why>)`），经 `svg_error.message` 进 `job.warnings` / 诊断，并由 `svg_figure_unavailable` 说成人话。
   自检不经 `Figure.savefig`（worker / bridge 的 savefig 捕获钩子不能把它记成一次用户保存）。
6. **响应加字段** `svg_pairing: "single_draw" | "two_pass"`（加字段协议，旧调用方忽略）。worker 协议、`svg` / `svg_text` 字段形状、回执 / generation 绑定都不变；
   「同一命令、同一次 overrides」的保证只会更强（现在是同一次 draw）。
7. **自动捕获（`snapshot_only`）同样走单次绘制**；`_svg_pair_env_blocker` 不过仍在 draw 之前拒绝（`svg_source_unavailable`）。

## 为什么 #1185 的 SVG 素材字节绑定**不**走 tee

`_bind_svg_asset` 要证明「磁盘上的 `<stem>.svg` 就是活 Figure 此刻的序列化」：拿活 Figure 导出的 SVG 与磁盘文件比 `svgcheck.canonical_digest`。
磁盘文件是脚本**独立** `savefig("x.svg")` 存的，文字度量来自 SVG 自己的 renderer；tee 的 SVG 版面度量取自 PDF，文字坐标与独立保存有亚点级差（实测 ≤ 0.005 pt），
摘要会无故对不上、EPS 的字节绑定回归。所以这个**等价证人**保持独立 savefig（`app._capture_svg_snapshot_best_effort` 另跑一次 `worker.export(fmt="svg", require_built=True)`，且要求前后回执相同），
原有阻断条件不变。证人不是交付物：交付的 SVG 仍出自单次绘制。

**有用户回调就不绑**（Codex #1355 P1）：状态型回调下磁盘态、配对态、证人态可以是三个不同的态，证人与磁盘一致不说明被 freeze 的配对那份也一致。
所以 `do_export` 另算一份完整（二次绘制口径）阻断，随响应带出 `svg_strict_blocker`；`_capture_svg_snapshot_best_effort` 见到它就不做证人、不绑定（保持旧行为，EPS 如实 `eps_source_unverified`）。

## 后果

* 取消：脚本里有自定义刻度函数 / 回调就导不出 SVG 的限制（#1348 的 `svg_figure_unavailable` 退为被保留的阻断与自检失败的用户话）。
* 依赖 matplotlib 的两处内部：`backend_pdf` 模块里 `MixedModeRenderer` 这个名字，与 renderer 协议（`draw_*` 集合、`flipy`、`start/stop_rasterizing`……）。
  已在 3.8.4（minimum）/ 3.10.8（playground）/ 3.11.1（bundled）验证；升级 matplotlib 的检查表（`docs/ci/matplotlib-upgrade-checklist.md` §9）要求先跑 `tests/test_teedraw.py` 与自检。
* 少画一遍：配对导出少一次 `savefig`（SVG path + text 各省一遍）。
* 新增 `engine/teedraw.py`，进 `worker._ENGINE_MODULES` / `bridge_runner._PHASE2` / `packaging/tavotto.spec` 的清单与 import 架构基线。
* 风险档 `risk:high`（导出核心路径）。

## 看护

`tests/test_teedraw.py`（三档 matplotlib：PDF 字节同、SVG 结构同、文字落位同、计数器回调同一串、`draw_event` 只一次、SVG 失败不拖累 PDF、tight 探测无残片、自检）；
`tests/test_worker_svg_pair.py` / `test_worker_svg_canvas.py`（只算值回调的用例翻成「必须配对成功且文字一致」，能看到 renderer 的回调保留阻断，自定义 canvas / savefig 类保留阻断）；
`tests/test_export_svg.py`（带计数器 FuncFormatter 的脚本图画布导 SVG 与 PDF 同一串刻度；被保留的阻断说成用户话）。
