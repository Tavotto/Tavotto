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
3a. **作用域**（Codex #1355）：替换 `backend_pdf.MixedModeRenderer` 的替身只对「**当前线程的会话 + 会话要导的那张 figure**」返回 `TeeRenderer`（`ContextVar` 标记会话），
   别的线程、同线程别的 figure 的 `savefig` 拿到的就是原类的构造；替换按引用计数、最后一个会话退出即还原；SVG sink 绑在会话对象上，不是模块级状态。
   评估过完全不 patch（自己构造 `PdfFile` + `MixedModeRenderer` 再 `figure.draw`，复刻 `print_pdf`）：要重复 `print_pdf` 的 dpi / PdfPages / `endStream` / metadata 分支，
   三档 matplotlib 之间有差异，比「按上下文分派的替身」更脆，不取。已知边界：SVG 目标的 `svg.fonttype` / `svg.image_inline` 是 draw 时读的进程级 rcParams，
   只在转发到 SVG 目标那一刻临时置位、立即还原，且只发生在会话线程里。
4. **阻断改成白名单（Codex #1355 三轮后的结构）**：不再逐个列「哪些回调危险」（draw → get_children → agg_filter → get_window_extent……每轮都漏一个）。
   单次绘制资格 = 图里**每一个 artist**（figure、axes、axis、text、集合……）在**类和实例两层**上，**所有可调用属性都是 matplotlib / mpl_toolkits 自己定义的**
   （`figsession._svg_pair_artist_blocker`）：整条 MRO 的类都来自 matplotlib，类字典里没有被换成用户函数的可调用（含对 matplotlib 类的全局 monkeypatch），
   实例字典里同样没有（实例级 monkeypatch、用户的 `agg_filter` / axes locator / 注解坐标 callable / 自定义 path effect 类）。判据是**代码出处**（`__code__` 的文件与模块 globals 对得上，
   numpy 自己的纯 Python 函数同理，Tavotto 自己的 colorbar 观察包装核闭包里的原方法），不是 `__module__`——`functools.wraps` 会把它抄成 matplotlib 的。
   **类对象不是「非可调用数据」**（Codex #1355）：`artist.get_children = SomeClass` / `set_agg_filter(SomeClass)` 一调用就跑 `__new__` / `__init__` / `__call__`。
   所以实例 / 类字典里的任何 callable——函数、bound method、`functools.partial`（解开看 `func`）、带 `__call__` 的实例、**类对象**——都要出处是 matplotlib / mpl_toolkits / numpy / 内建自己的；
   类对象按其整条 MRO 与类字典里的可调用判定（标准库基类如 `Sequence` / `dict` 不扫）。非 callable 的数据属性照旧忽略。
   **C 实现的可调用也不放过**（Codex #1355）：builtin 函数 / C 的 bound method / 方法描述符（`ax.get_children = deque.popleft`、`list.pop`、`dict.get`）只有 `__self__`（描述符看 `__objclass__`）
   是 matplotlib / numpy 的对象、且名字确是其类型自己的方法才放行；绑在用户或内建对象上的一律阻断。**封闭例外表**（三档 matplotlib 常规图实测，实例字典里 matplotlib 自己放的 builtin / C bound method
   只有这一种）：`artist._remove_method = <list>.remove`（`Artist.remove()` 时才调用，绘制不调用）。类字典里没有。
   **判定遍历不经实例属性调用任何方法**：往下走一律用 `type(artist).get_children(artist)`（类上 matplotlib 自己的函数，且只在该实例通过白名单之后），transform / scale / 刻度 getter 同理。
   只读（`vars` / `__mro__` / `__code__`），**判定不调用用户代码**；通过后才用 matplotlib 自己的 `get_children` 往下走。`draw_event` 处理器同样阻断。
   理由：能看到 renderer 的代码可能按 renderer 的类分派（tee 与 PDF 本来拿到的不是同一个类，连 PDF 都会变）、可能被每个目标各跑一次（如 `stop_filter`）、
   可能在两次 draw 之间不同；这些都不是「值」，白名单一次覆盖，不靠穷举。
   **transform / scale 两种口径都要核**（Codex #1355）：renderer 拿到的是 transform **对象**并自己求值（PDF 与每个 SVG 各求一遍），有状态的 `transform_path` 会让各份几何不同——
   它们不是「已算好的值」。所以用户的 Transform 子类、`FuncTransform` / `FuncScale` 里的用户函数在单次绘制下也阻断（`_svg_pair_value_blocker`）。
   **唯一的例外是封闭的「值槽」= Axis 的 formatter 与 locator**（含 `FuncFormatter` 包着的用户函数、用户的 Formatter / Locator 子类）：它们在 `Axis.draw` 阶段只算一次，
   产出 Text / 数值后才进 renderer（`tests/test_teedraw.py` 核了调用次数与常规 savefig 相同）；它们挂在 `Ticker` 对象里，本来就不在白名单扫描范围内，二次绘制口径才另外核它们。
   另保留自定义 `Figure.savefig` / canvas / 打印方法 / 注册后端（`_svg_pair_env_blocker`）。
   评估过「让 `TeeRenderer` 对 `isinstance` 透明」：不做（见上一版论证，白名单已使它不再必要）。
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
