# ADR 0022：Complexity-Aware Editor Preview（预览表示法与语义编辑解耦）

状态：**Accepted（架构契约已定稿；分阶段落地进行中）**
日期：2026-08-28
相关：[issue #181](https://github.com/Tavotto/Tavotto/issues/181)、
[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)（本
ADR 是它的**延伸**：那条管「哪一版说了算」，这条管「这一版长什么样」）、
[0003 worker 协议 v1](0003-worker-protocol-v1.md)（加字段不升版）、
[0013 Runtime Figure Assets](0013-runtime-figure-assets.md)、
[0016 Diagnostics V2](0016-diagnostics-v2-frontend-state-tracing.md)、
[修复前基线](../perf-baseline.md#大图预览基线issue-181修复前)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 编辑预览的表示法 | `vector` / `hybrid` / `raster` 三档，**写进协议** |
| 谁决定用哪一档 | 引擎侧按复杂度预算裁决，前端只消费 |
| 语义 manifest | **不因表示法改变**（不变量 1） |
| publication 导出 | **不继承任何 preview-only rasterization**（不变量 2） |
| 超限 SVG | **不读进内存**就降档（不变量 3） |
| 几何权威 | 三档下都只认 exact manifest（不变量 4，= ADR 0017） |
| 未知/极端 artist | 最坏降到低内存 raster preview，**不 OOM / 不 crash**（不变量 5） |
| 「超过 N MB 就拒绝打开」 | **不是解法**，只能作为临时止血 |

---

## 1. 背景：数据说的是什么

issue #181 的形状是「多面板大型 mesh 科研图打开即冻结」。合成复现
（`tests/fixtures/large_figures/issue_181_large_pcolormesh.py`，2×2 图、三格
`pcolormesh`、每格 22 万 quad）在本机量出来的是：

| 指标 | 值 |
|---|---|
| 预览 SVG | 126 MB / **662 773 个 `<path>`** / 663 533 个 DOM 节点 |
| Flask 交给浏览器的 JSON | **134 MB** |
| 一次 render 后 Flask 峰值 RSS | **1 245 MB** |
| 热 render `canvas_draw_ms` | 11 789（占热态 98%） |
| **用户脚本自己那一段** `script_exec_ms` | **74.6** |

最后两行是这份 ADR 存在的全部理由：**用户的 `pcolormesh` 75 毫秒就画完了，
那 11.9 秒是 Tavotto 把它序列化成矢量 SVG 的成本。** 慢的不是他的图，是我们
选的表示法。因此：

* 「让用户自己 `mesh.set_rasterized(True)`」把我们的表示法问题记在用户账上；
* 「大于 N MB 就拒绝打开」把它记成用户的文件太大；
* 「让 Chromium 更努力地托管 70 万个节点」把它记成浏览器的问题。

三条都不是解法。

## 2. 决定

**编辑预览的表示法与语义编辑解耦。** 预览可以是矢量、可以是混合、可以是位图；
**能编辑什么、编辑到什么精度，一个字节都不跟着变。**

正式模型（协议里的一等公民）：

```ts
type PreviewMode =
  | 'vector'   // 普通科研图：行为与今天完全一致
  | 'hybrid'   // 大型 mesh / collection 临时 rasterize；文字、坐标轴、图例、
               // 标注、普通曲线保持 vector
  | 'raster'   // 极端或未知复杂度的最后安全降级：显示 PNG
```

三档都**不是**「降级模式」，而是同一件事的三种画法。用户能做的事在三档下相同：
选中、拖动、对齐、改属性、导出。

### 响应元数据（additive，ADR 0003 §1：加字段不升协议版本）

```ts
interface PreviewMetadata {
  mode: 'vector' | 'hybrid' | 'raster'
  reason: 'normal' | 'complexity_budget' | 'svg_hard_limit' | 'fallback'
  svg_bytes: number
  estimated_primitives?: number
  estimated_vertices?: number
  rasterized_artist_count: number
}
```

**旧后端不返回 `preview` 时，新前端必须维持今天的行为**——这是加字段协议的
代价，也是它的全部好处。

---

## 3. 五条不可破坏的不变量

### 不变量 1 — Semantic fidelity（语义保真）

> **预览表示法可以改变，语义 manifest 不得因此改变。**

同一组 patches、同一个 stem，`vector` / `hybrid` / `raster` 三档下
`build_manifest` 的输出必须逐字节相同：同样的 gid、同样的 bbox、同样的
`editable`、同样的 `role`。rasterization 发生在**画的那一步**，不在
**读语义的那一步**。

违反它的表现：切到 hybrid 之后某个元素在属性页里消失了，或者 gid 变了
（前端按 gid 索引一切——那是数据级错位，且只在大图上、在用户那边发作）。

### 不变量 2 — Export fidelity（导出保真）

> **编辑预览里的临时 rasterization 绝不能改变最终 PDF/SVG 导出。**

`do_export` 与 `do_render` 是两条独立路径。预览为了显示而设的
`rasterized` / dpi / 降采样，一律**不得**留在常驻 Figure 上被导出看见——
这与 `do_preview_png` / `do_export` 既有的「状态中立」纪律是同一条：
临时改动必须在 `finally` 里还原。

用户投的是论文。**预览糊一点没人会因此撤稿，导出糊一点会。**

### 不变量 3 — Memory safety（内存安全）

> **超限 SVG payload 不得无保护地进入浏览器 DOM。**

判定必须发生在**读取之前**：

```text
savefig(svg)
  ↓
stat(svg).st_size          ← 判定点
  ↓
< HARD LIMIT ?  → read_text() → 内联进响应
否则            → 不读，preview.mode = raster
```

**「先 read 134 MB 再告诉前端太大」不算保护**：基线里那 1.2 GB 峰值 RSS
全部发生在读取与两次 JSON 编解码的中间副本上，此时 SVG 一个字节都还没到
浏览器。判定点在 `read_text()` 之后 = 这道闸只挡住了最后一跳。

### 不变量 4 — Geometry authority（几何权威）

> **hybrid / raster 下仍然只认 exact manifest 作为几何权威。**

这条就是 ADR 0017，一个字都不放松。补充的只有一句：**raster 显示 ≠ 关闭语义
编辑**。画面是 PNG，命中层照旧在，权威照旧是 `exactPanelRender`：

```text
PNG / raster 显示
──────────────────
ElementHitLayer        ← 照常命中、框选、拖动
──────────────────
exact manifest         ← 唯一几何权威
```

把 raster 做成「只读预览」是最容易滑进去的错误：那等于告诉用户「你的图太大，
所以不能编辑了」——而 #181 的用户要的恰恰是编辑这张图。

### 不变量 5 — Graceful degradation（优雅降级）

> **未知或极端复杂的 artist，最坏降到低内存 raster preview，不得 freeze /
> OOM / renderer crash。**

复杂度分析器（Session 02）永远会遇到它不认识的 artist——第三方库的、
matplotlib 新版本的、用户自己继承出来的。**不认识时的默认答案是「按贵的算」**，
不是「按普通的算」。降错档的代价是一次画质降级；不降档的代价是一次崩溃。

同理：超限时返回的是一次**成功的渲染**（manifest / warnings / timings / rev /
preview 元数据齐全，只是没有 `svg`），不是一次渲染失败。让现有的错误 UI 把它
显示成「渲染失败」，等于把一个我们主动做出的保护决定说成故障。

---

## 4. 复杂度预算

常量唯一出处 `src/tavotto/engine/previewbudget.py`（前端镜像
`web/src/lib/previewBudget.ts`，Python 侧有同源看护用例）。

| 常量 | 初值 | 语义 |
|---|---|---|
| `EDITOR_SVG_SOFT_LIMIT_BYTES` | 8 MiB | **hybrid 的第二把尺子**：产物越过它且名单没收满 ⇒ 全收、重画一遍 |
| `EDITOR_SVG_HARD_LIMIT_BYTES` | 16 MiB | **raster 的硬闸**：超过就不读 |

两个数字都是**可调的策略**，不是物理常数；调它们要带上新的测量。选这两个初值
的理由：Phase E 基线里普通科研图的预览 SVG 是几十到几百 KB，含 imshow 的
面板最坏 827 KB——8 MiB 比它们高一个数量级以上，正常图一张都不会被碰到；
而 #181 那张是 126 MB，比硬闸高 7.8 倍，怎么调都在闸外。

**两条闸量的是两个时刻的两样东西，这正是留着两条的理由**：复杂度那几个数量
的是**原料**（artist 图上有多少 primitive，`savefig` 之前就答得出来），字节
那两条量的是**产物**（`savefig` 之后才知道）。模型估低了的时候，只有产物那
一侧看得见——同源的两把尺子只是自己验自己。

Session 03 之后软闸真的生效：产物越过 8 MiB、而可 rasterize 的数据层还没收满
⇒ 把剩下的全部收进来**重画一遍**（最多第二遍，还超就交给硬闸）。正常科研图
的预览 SVG 是几十到几百 KB，那第二遍一次都不会付。

（此前它是一个**刻意留的、有用例钉住的缺口**：`test_soft_band_still_passes_
through_today` 在 hybrid 落地那一刻当场红，逼着一起改，而不是靠谁记得回来。
它做到了——2026-08-29 Session 03 接线时它是第一条红的用例。）

---

## 5. 分阶段落地

| Session | 内容 | 状态 |
|---|---|---|
| 00 | 合成复现 + before 基线 + 本 ADR | 完成 |
| 01 | Large SVG Safety Guard（不变量 3 + raster 档 + 前端二道闸） | 完成 |
| 02 | Preview Complexity Analyzer（primitive / vertex 估算，喂 `estimated_*`） | 完成 |
| 03 | Hybrid Preview（mesh 层 rasterize，文字/轴/图例保持 vector） | 完成 |
| 04 | renderStore 的 SVG 内存预算 | 待做 |
| 05 | Diagnostics 与回归看护 | 待做 |
| 06 | 集成与 issue 收尾 | 待做 |

**顺序不能换。** 01 是止血：hybrid 还没有的时候，超限图至少不能再把浏览器
打死。02 在 01 的安全网之下才敢真的去遍历大 figure 的 artist 树。

### Session 02 落地了什么（2026-08-29）

`src/tavotto/engine/preview_complexity.py`：Figure → `PreviewPlan`
（mode / `estimated_*` / **该 rasterize 谁**）。它只算账——不 `savefig`、
不改 artist、不建大数组、不读 SVG。开销实测
[0.0165 ms vs `savefig` 的 10 540 ms](../perf-baseline.md#复杂度分析器开销issue-181session-02)。

成本模型不是拍脑袋，是从 matplotlib 自己的绘制路径抄下来的两条：
`Collection.draw` 的单形状快路（→ `draw_markers`，几何进 `<defs>`）与
`RendererSVG.draw_path_collection` 的成本取舍式。**并且与后端对拍**：同一张图
带 / 不带某个 artist 各 `savefig` 一次，差分出它真的摊出来的 `<path>` /
`<use>` / `<image>` 与顶点数。**十二格对拍里 primitive 逐个相等。**

对拍第一轮就抓出三处「我以为」——网格每 cell 是 5 个坐标对不是 4；空的
contour 层照样占一个 `<path>` 节点；`scatter(x, y, s=<数组>)` 掉出单形状快路、
**每个 marker 各自内联**（顶点数比 `s=标量` 多 500 倍）。三处都是模型偏低。

评审又抓出两处，两处都是**判据量错了对象**，都补进了对拍（2026-08-29）：

* **顶点抽样只取前 4096 条**。异构 collection（先小后大：等值面、分箱统计、
  地理边界）的重几何全排在窗口之外。实测 4206 条 path：真值 33 006，前缀抽样
  报 21 030（**63.7%**），改成跨全序列等距抽样后报 31 278（94.8%）。偏低方向
  = 漏判，正是这个分析器要防的事。
* **`visible=False` 的 artist 按全价记账**。后端对不可见的 artist 一个节点都不
  写（对拍两格的 `svg_delta_path` 都是 0）。偏高方向 = 一块藏起来的大 mesh 凭空
  逼出一次 hybrid，用户看到的是「明明没显示那层图，画面却糊了」。

**软闸仍然不改变任何行为**：分析器产出的是名单，把名单变成 `set_rasterized`
是 Session 03。`test_soft_band_still_passes_through_today` 照旧钉着那个缺口。

### Session 03 落地了什么（2026-08-29）

`src/tavotto/engine/preview_hybrid.py`：把名单变成 **`savefig` 那一瞬**的
`set_rasterized`，出窗口时逐个还回原值。三条纪律写在模块头——先读后写、
还原不许半途而废、还原失败要吵（`RestoreFailed`）。

**接线点是 `figsession.render()` 一处**，因此冷 build（`instrument_all()`）与
热 render（`do_render()`）落在同一条策略上。这不是实现细节：只在 render
request 上 rasterize 的话，用户**第一次打开** #181 那张图仍然要先等十几秒——
那不叫修好。playground 走 `browser._render()`，与桌面共用同一个
`preview_hybrid.save_preview_svg`。

合成 fixture（n=200，三块 4 万 cell 的 mesh）上的实测，A/B **在同一进程同一次
运行里交替**（同一张图、同一个会话、紧挨着的两次，只把预算抬走）：

| | 纯矢量 | hybrid | |
|---|---:|---:|---|
| 预览 SVG 字节 | 22 902 252 | **599 747** | 2.6% |
| `<path>` | 120 072 | **72** | 0.06% |
| `<image>` | 1 | 4 | 三块 mesh 各一张 + 色条色带 |
| `savefig` | 2 174 ms | **165 ms** | 13× |
| manifest | 逐字节相同 | 逐字节相同 | 不变量 1 |
| 导出 SVG | 120 072 个 `<path>` | 120 072 个 `<path>` | 不变量 2 |

默认规模（n=470）的前后对照见
[perf-baseline 的 Session 03 一节](../perf-baseline.md#大图预览hybrid-之后issue-181session-03)。

**gid 丢失是允许的**：rasterize 掉的 artist 在 SVG DOM 里没有自己的节点了
（`<g id="axes_0.collections_0">` 整个不出现）。不为此造隐藏占位节点、不重新
矢量化 mesh、不动 manifest 的 gid 语义——前端在 `findGidNode` 返回 null 时
安静退出、覆盖层接管，几何权威照旧是 exact manifest（§7 的「代价」第三条）。

---

## 6. 不做什么

* **不按 artist 类型特判**（`isinstance(artist, QuadMesh)`）。#181 的表面成因是
  `pcolormesh`，但成本的真实来源是 primitive 数量——`scatter` 十万个点、
  `contourf` 上千条等值线、`LineCollection` 一大把线段是同一个问题。判据必须
  问「有多少 primitive」，不能问「你是谁」。
* **不改 publication export 的语义**（不变量 2）。
* **不把 giant SVG 转成 base64 塞回 MCP**。MCP 那条路上 raster 预览走**受控
  尺寸的 PNG**，与 SVG 无关。
* **不做「大于 N MB 禁止打开」**。它作为 Session 01 的止血手段以
  `preview.mode = raster` 的形式存在——那是**换一种画法**，不是**拒绝服务**。
* **不拆 `inline_svg` 的原子配对**。SVG 与 manifest 必须同一次响应（web/AGENTS.md
  「渲染态」①）；`raster` 档下的正确形态是**两样都不给 SVG**，而不是让前端
  再跳一次 GET 去拿。

## 7. 代价

* 大图上编辑期的画面是位图，缩放到很大时会看到像素。**导出不受影响**
  （不变量 2），这是刻意的取舍：编辑期要的是响应速度，出版要的是精度。
* 多一条要维护的表示法分支，以及一份要与 Python 侧对齐的前端常量。
* raster 档下 SVG 局部样式预览（`svgPreviewStore` 的假实时那一套）用不上——
  DOM 里根本没有 gid 节点。既有实现在找不到节点时已经是**安静退出 + 覆盖层
  接管**，行为不变。
* **hybrid 档下同一件事发生在被 rasterize 的那几层上**（Session 03 实测）：
  它们在 SVG 里是 `<image>`，`findGidNode` 对它们返回 null，于是拖动那几层
  时没有 DOM 假实时，落点由后端权威渲染回来补上；文字 / 坐标轴 / 图例 /
  标注 / 普通曲线的 gid **一个都没少**，假实时在它们身上照常工作。
  边界因此是一句可验证的话：**假实时覆盖的是矢量层，不是数据层**
  （看护 `tests/test_preview_hybrid.py::test_rasterized_layers_may_lose_their_gid_node`
  与 `::test_text_axes_legend_and_ordinary_curves_stay_vector`）。

## 8. 看护

* `tests/test_issue181_large_preview.py` —— 合成 fixture 的确定性、规模、
  以及「它真的复现了机制」（一个 quad 一个 `<path>`）；
* `tests/test_preview_budget.py` —— 常量、判据、两侧同源、**超限时
  `read_text()` 一次都不调**；
* `tests/test_preview_complexity.py` —— 分析器的裁决、**成本模型与 SVG 后端
  的对拍**、以及「它什么都没改」（`rasterized` 前后逐个比对、`QuadMesh` 的
  paths 一次都没被建过）；
* `tests/test_preview_hybrid.py` —— 冷 build / 热 render 同策略、**精确还原**
  （含「原值是 True 的还回 True」与 `savefig` 抛异常那一路）、manifest 逐字节
  不变、导出保真、hybrid 产物上硬闸照旧生效、软闸那第二遍；
* `tests/test_browser_session.py` —— playground 那条入口上的同一条闸
  （SVG 生在内存里，判据挪到「交给 JS 之前」）；
* `web/src/canvas/panelPreviewMode.test.tsx` / `web/src/lib/previewBudget.test.ts`
  —— 三档显示行为、raster 下命中层仍在、前端二道闸；
* `docs/perf-baseline.md` 的「大图预览基线」—— 前后对照的唯一出处。
