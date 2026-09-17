# 编辑预览的表示法与复杂度预算（ADR 0022，issue #181）

> 原文出自 `src/tavotto/AGENTS.md`「编辑预览的表示法与复杂度预算（ADR 0022，issue #181）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

**预览怎么画** 与 **能编辑什么** 是两件事。动这一带之前先读
`docs/adr/0022-complexity-aware-editor-preview.md` 与
`docs/perf-baseline.md` 的「大图预览基线」。

- **常量与判据唯一出处 `engine/previewbudget.py`**（前端镜像
  `web/src/lib/previewBudget.ts` 是**二道闸**，不是第二份权威；两侧的数字由
  `tests/test_preview_budget.py` 逐个比对）。`vector` / `hybrid` / `raster`
  三档写进协议的 `preview` 字段——**加字段不升协议版本**（ADR 0003 §1），
  老 worker 不返回它时前端按 `vector` 解读，行为一字不改。
- **判定必须在 `read_text()` 之前**（`figsession.do_render`，吃的是
  `stat().st_size`）。「先读 126 MB 再说太大」不算保护：实测那一读加上两次
  JSON 编解码就让 Flask 进程峰值 RSS 到 **1.2 GB**，而 SVG 一个字节都还没到
  浏览器。**这条是本轮唯一的硬验收**，看护是
  `tests/support/preview_guard_probe.py`——它把 `Path.read_text` 换成记账实现，
  在阈值两侧各跑一次；只跑一侧的绿是样本，不是对照。
- **超限是一次成功的渲染**：`manifest` / `warnings` / `timings` / `rev` 一样
  不少，只是 `svg` 整个不出现。别让错误路径接住它——把我们主动做出的一个显示
  决定说成「渲染失败」，用户会去修一个不存在的问题。
- **降级 ≠ 只读**：raster 档下命中层与 exact manifest 一个字都不放松
  （不变量 4 = ADR 0017）。#181 的用户要的恰恰是编辑这张图。
- **MCP 那条路上 raster 的位图与 manifest 在同一次响应里**
  （`bridge._render` 的 `preview_png_base64`，宽度钉死
  `previewbudget.RASTER_PREVIEW_WIDTH_PX`）。内嵌画布里没有可连的 HTTP 服务，
  不带上它就是一张全白的画布；另开一跳去取则会拿到另一组 patches 的像素——
  与 SVG/manifest 的原子配对是同一条纪律。**绝不把 giant SVG 转成 base64
  塞回去**，那只是把同一个 payload 换个编码再放大三分之一。
- **不按 artist 类型特判**。#181 的表面成因是 `pcolormesh`，成本的真实来源是
  primitive 数量——`scatter` 十万点、`contourf` 上千条等值线是同一个问题。
  判据问「有多少 primitive」，不问「你是谁」。
- **复杂度分析器 `engine/preview_complexity.py`（Session 02）只算账**：
  Figure → `PreviewPlan`（mode / 估算 / 该 rasterize 谁）。**不 savefig、
  不改 artist、不建大数组、不读 SVG**——它进 render 热路径（实测 0.0165 ms，
  对面是 `savefig` 的 10 540 ms）。改了 artist 就等于把预览的表示法写进常驻
  Figure，而常驻 Figure 是导出读的那一份（不变量 2）。
- **设 / 还原 `rasterized` 收在 `engine/preview_hybrid.py`（Session 03）一处**，
  且只发生在 `savefig` 那一瞬。三条纪律：**先读后写**（旧值在设新值之前入账）、
  **还原不许半途而废**（逐个 try，一个失败不连累其余）、**还原失败要吵**
  （`RestoreFailed`，静默吞掉 = 下一次导出把预览的位图化写进论文）。
  「还原」是**还回原值**，不是 `set_rasterized(False)`——用户自己设了
  `rasterized=True` 的那块 mesh 预览之后仍然得是 True。
- **接线点是 `figsession.render()` 一处，冷 build 与热 render 共用。**
  `instrument_all()`（冷 build）与 `do_render()`（热 render）都走它——只在
  render request 上 rasterize 的话，用户**第一次打开** #181 那张图仍然要先等
  十几秒，那不叫修好。playground 的 `browser._render()` 与它共用
  `preview_hybrid.save_preview_svg`（策略只有一份，两条入口不许分叉）。
- **软闸是第二把尺子，不是第二个档位**：`savefig` 出来的字节数越过
  `EDITOR_SVG_SOFT_LIMIT_BYTES`、而可 rasterize 的层还没收满 ⇒ 全收、重画
  一遍（最多第二遍）。复杂度预算量原料、字节闸量产物，模型估低了的时候只有
  后者看得见。正常科研图（几十到几百 KB）一次都不会付那第二遍。
- **gid 丢失是允许的**：rasterize 掉的 artist 在 SVG DOM 里没有节点了。
  不造隐藏占位节点、不重新矢量化 mesh、不动 manifest 的 gid 语义——前端在
  `findGidNode` 返回 null 时安静退出、覆盖层接管，几何权威照旧是 exact
  manifest。**假实时覆盖的是矢量层，不是数据层。**
- **成本模型抄的是 matplotlib 自己的绘制路径，不是「数据点数 == SVG path 数」**：
  `Collection.draw` 的单形状快路（→ `draw_markers`，几何进 `<defs>`）与
  `RendererSVG.draw_path_collection` 的成本取舍式。同一个 `PathCollection`，
  `s=标量` 共享几何、`s=<数组>` **每个 marker 各自内联**（顶点数差 500 倍）。
  **改这个模型必须重跑对拍**（`test_model_matches_what_the_svg_backend_actually_emits`）：
  同一张图带 / 不带那个 artist 各 `savefig` 一次，差分出后端真的写出来的节点
  与顶点。它抓出过五处「我以为」，**两个方向的偏都出现过**——三处模型偏低
  （网格每 cell 5 个坐标对、空 contour 层也占节点、`s=<数组>` 的散点逐个内联），
  一处偏低一处偏高是评审抓的：**顶点抽样只取前 4096 条**（异构 collection 上
  实测只报出真值的 63.7%，改成跨全序列等距抽样）与 **`visible=False` 的 artist
  按全价记账**（后端一个节点都不写，记了就会凭空逼出一次 hybrid）。
  两条判据的共同形状是「量错了对象」：一个量的是开头那一段而不是整条，一个
  量的是 artist 图而不是后端会写出什么。
- **`rasterized=True` 的 artist 只值一个 `<image>`**，不按 family 摊成 N 个
  `<path>`——色条色带就是 matplotlib 自己设成 rasterized 的 `QuadMesh`。
- 合成复现在 `tests/fixtures/large_figures/`，摊成可用图库用
  `tests/support/large_figures.py`。**跑出来的 SVG/PDF 绝不提交**
  （默认规模下 SVG 一百多 MB）。
