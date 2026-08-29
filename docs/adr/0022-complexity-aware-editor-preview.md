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
| `TOTAL_VECTOR_NODE_BUDGET` | 24 000 | **节点闸**：收完之后矢量层还剩多少个 SVG 元素，超了就降 raster |

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
| 04 | renderStore 的 SVG 内存预算 | 完成 |
| 05 | Diagnostics 与回归看护 | **待做** |
| 06 | 集成与 issue 收尾 | 复核完成，**issue 不可关闭**（见 §9） |

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
| `savefig` | 2 174 ms | **165 ms** | 13×（这一行单独量的，其余出自探针） |
| manifest | 逐字节相同 | 逐字节相同 | 不变量 1 |
| 导出 SVG | 120 072 个 `<path>` | 120 072 个 `<path>` | 不变量 2 |

默认规模（n=470）的前后对照见
[perf-baseline 的 Session 03 一节](../perf-baseline.md#大图预览hybrid-之后issue-181session-03)。

**gid 丢失是允许的**：rasterize 掉的 artist 在 SVG DOM 里没有自己的节点了
（`<g id="axes_0.collections_0">` 整个不出现）。不为此造隐藏占位节点、不重新
矢量化 mesh、不动 manifest 的 gid 语义——前端在 `findGidNode` 返回 null 时
安静退出、覆盖层接管，几何权威照旧是 exact manifest（§7 的「代价」第三条）。

### Session 04 落地了什么（2026-08-29）

01–03 管住的是**单次预览**：一份 SVG 有多大、要不要读、要不要 rasterize。
剩下的第二类内存放大在前端的保留策略里：

> **条目数不是字节预算。** `renderStore` 的 `RECENT_VARIANTS = 4` 是撤销 /
> 版本恢复的落点，它对「留了多少字节」一无所知。hybrid 之后仍有 8～12 MiB
> 的预览 SVG × 4 档 × 几个文件 = 几百 MB 常驻在 JS 堆里，而每一份都是合法
> 缓存，`prune` 一个都不该清。

所以在**保留条目数**之外再加一条**保留字节数**的策略（`web/src/store/
renderStore.ts`）：

```text
entry-count policy   RECENT_VARIANTS = 4        留几档语义状态
byte-budget policy   SVG_RECENT_BUDGET_PER_FILE = 16 MiB
                     SVG_RECENT_BUDGET_GLOBAL   = 64 MiB
```

两个数量的是 **JS 侧驻留的 SVG 源文本 payload**，**不是 Chromium 渲染进程的
DOM 内存预算**——那一侧由硬闸与 hybrid 负责，两者不可互相冒充。

**超预算时丢掉的只有 `svg` 字符串**（`dropSvgPayload`）。manifest / rev /
lastPatches / wantPatches / timings / preview / status / stale 一个字都不动：

```text
语义状态           ≠           SVG 源 payload
manifest, rev, lastPatches …               svg: string
撤销、版本恢复、几何权威靠它               画布上那张图靠它
必须留                                     可以重新生成
```

「budget exceeded → delete PanelRender」是这一节最容易滑进去的错误：它会连
撤销落点、版本恢复与几何权威一起丢掉，换来的字节其实只有 `svg` 那一份。

**驱逐次序**两维：先「不在 `recent` 里的」（脚本变更后作废的、掉出档数的
——没人会撤销回去），再按 payload 落地序号丢最久没更新的。只按第二维排的
纯 LRU 会先丢真正的撤销落点。

**三条 pin**（清了就没画面的那几份）：画布上现存面板的变体键、每个文件的
`latest`（显示退路）、在途/渲染中。全被 pin 住时**宁可超预算**——预算管的是
可驱逐的历史 payload，不是显示所需的那一份。这一条对 Codex 内嵌画布是硬要求：
那里 `panelSrc()` 恒 null、`previewPngUrl()` 只对 raster 档有缓存位图，
**当前那份 SVG 就是唯一能显示的东西**，清掉 = 画布空白。

被丢掉 payload 的那一版在 `panelDisplayView` 里是**独立的一档 `evicted`**，
不是 `fallback`：画布挂的**就是这一版**，只是画法换成位图（与 raster 同一条
既有链路）。这是 §8 的「诚实 display strategy」——掉进 `fallback` 的话诊断会
说画布挂着另一个变体的图，而几何权威仍是这一版（issue #131 同款错配）；而
把它当成渲染失败，就是把一个主动的保护决定说成故障（不变量 5 的老问题）。
同时 `useEngineSync` 会为它**重排一次渲染**：桌面/playground 有引擎位图顶着，
内嵌画布没有第二条路，重画是那里唯一的出路。

合成压力（4 版 × 12 MiB 的真字符串，同一文件，**同一次运行里交替**）：

| | 全被 pin（= 本 Session 之前） | 预算可以动手 |
|---|---:|---:|
| 驻留 SVG payload | 48 MiB | **12 MiB** |
| `byKey` 条目 | 4 | 4 |
| 保留 manifest 的条目 | 4 | 4 |

两把独立的尺子（自己的记账 / V8 的 `heapUsed`）读数一致，详见
[perf-baseline 的 Session 04 一节](../perf-baseline.md#前端-svg-payload-的驻留字节issue-181session-04)
——那一节也记着第一次量错的形状：`--expose-gc` 传不到 vitest worker 时，
heapUsed 量的是「分配了多少」而不是「留下了多少」，两侧因此恒等。

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
* `web/src/store/svgMemoryBudget.test.ts` —— 字节预算：驻留字节收得住、
  **条目与语义状态一条不少**、pin（live/latest/在途）、驱逐次序的两维、
  clear/reset/prune 之后不留残账、`evicted` 那一档的显示与几何权威；
  配套 `web/src/hooks/useEngineSync.test.ts` 的「重排一次渲染」一组；
* `docs/perf-baseline.md` 的「大图预览基线」—— 前后对照的唯一出处。

---

## 9. Session 06 复核（2026-08-29）：结论是 **不可关闭**

全量绿（pytest 3 077 passed / 0 failed、web 1 366 tests、build / lint / i18n、
两个受管产物 `--check`、`smoke_app`、E2E 14 passed），n=470 复现逐字节一致，
浏览器侧**首次实测**（01–04 每一节都写着「还没量」的那一项）：

| | 修复前 | hybrid 之后 |
|---|---:|---:|
| SVG 元素总数（同一把尺子） | 663 533 | **818** |
| Chromium `Nodes` | not measured（挂进去就是症状本身） | **2 887** |
| JS 堆 | not measured | **2.47 MB** |
| 渲染进程 RSS（macOS headless Chromium） | not measured | **107–125 MB** |
| 解析 + 插入（`parseMs`） | not measured | 70–84 ms |
| **到画出来（`renderedMs`）** | not measured | **80–92 ms** |

开关 5 次每轮回到 6 个节点 / 0.63 MB；RSS 五轮共涨 17–18 MB，是 allocator
retention，不是线性增长。数据与复现命令见
[perf-baseline 的 Session 06 一节](../perf-baseline.md#浏览器侧首次实测issue-181session-06)。

五条不变量逐条仍然成立（manifest 逐字节相同且 `manifest_ms` 199.5 → 200.0 ms；
导出 120 072 个 `<path>` 一个不少而预览只有 72 个；`read_text` 1 → 0；
`ElementHitLayer` 与 `bitmapOnly` 无关；收不动时不谎报 hybrid）。

### 曾经缺的那一维已经补上（`TOTAL_VECTOR_NODE_BUDGET`）

四条复杂度预算量 primitive 数、两条字节闸量产物字节数。**渲染进程的代价按
节点走，而没有一条闸量它**——两者在 `pcolormesh` 上重合（所以 fixture 修好了），
在细小的不可 rasterize primitive 上分开：

| shape（`large_preview_svg.py --shape`） | 裁决 | `svg_bytes` | DOM 节点 | 渲染进程 RSS |
|---|---|---:|---:|---:|
| `mesh --n 470`（#181 fixture） | `hybrid` | 1 838 682 | 2 887 | 107–125 MB |
| **`lines --n 40000`（每条 3 点）** | **`vector` / `normal`——无闸触发** | 9 334 177 | **200 531** | **317–359 MB** |

第二行**可达**：`estimated_primitives = 40 000` 在
`TOTAL_VECTOR_PRIMITIVE_BUDGET`（50 000）之内；8.90 MiB 越过 8 MiB 软闸，但
`line` 按契约不可 rasterize，`escalate_plan` 返回 `None`；16 MiB 硬闸也没到。
40 000 次 `plot()` 是普通 matplotlib 写法；多面板画布把它乘上面板数，而
**每个 live 面板都被 pin 住、按设计永不驱逐**（Session 04 §7）。四面板的形状
对照（`paths --copies 4`，102 MB / 16 万个元素）实测**直接把渲染进程打死**
——那正是 #181 报告的那个结局。

**字节数不是节点数的代理，两个方向都成立**：560 条 × 2000 点是 21.4 MiB 却
只有 3 336 个节点（字节在 path 的 `d` 属性里），而**那一条字节闸看得见**
（越过 16 MiB 硬闸 → `raster`）；折线海只有 8.90 MiB 却有 20 万个节点，
**字节闸看不见它**。贵的是**大量细小的、不可 rasterize 的 primitive**，
不是大量字节。

**这一条已经修掉了**（单独一个 PR，配了 12 条变异验证）：

* `previewbudget.TOTAL_VECTOR_NODE_BUDGET = 24 000`——收完之后矢量层还剩多少
  个 SVG 元素。阈值锚在实测的挂载耗时上：1 万条线 = 20 000 个元素 / 103 ms
  放行，2 万条线 = 40 000 个元素 / 205 ms 拦下。
* `ArtistPreviewCost.node_count`：**primitive 数不等于节点数**。`Line2D` 是
  一个 `<path>` **外加一个 `<g>`**（对拍实测 2.01 个元素/条），逐实例着色
  且几何共享的散点同样翻倍（500 个 `<use>` + 501 个 `<g>`）。其余族 1:1。
* 两条图级预算**一起收敛**：能收的收满，收不动才降 raster——只看 primitive
  的话，四格各 1.5 万 cell 的图收掉一格就「达标」，却把 45 388 个元素交给 DOM。
* `resolve_mode` 新增 `plan_demands_raster`：**这个信号与字节无关**，4 万条
  `plot()` 只有 9.33 MB，两条字节闸一条都不响。

对拍顺带补掉一个豁免：`FAMILY_LINE` 原本被排除在成本对拍之外
（探针的 `skip` 里），而**被豁免的那一族正好是出问题的那一族**。

### 不可关闭的四条理由

1. **代码不在 `main` 上**：02/03/04 是三个未合并的 stacked PR。
2. **Session 05 整个没做**：诊断包分不出 vector / hybrid / raster / evicted /
   泄漏这五种状态（`PanelSnapshot` 里没有任何表示法字段）；浏览器结构性 DOM
   预算作为 CI gate 不存在；Windows WebView2 基准 **not locally measured**。
3. ~~存在实测可达的 freeze 路径~~ **已修**（上一节），但修复本身还没落 `main`。
4. **Windows 从没执行过这套代码**：`backend-platforms` 在普通 PR 上是
   `SKIPPED`，而 `svg_bytes` 是判定量、Windows 的 `\r\n` 会让同一张图
   显得大 3.8%。

### 量内存时先说清「谁的」

渲染进程 RSS 第一版按 `ps | grep -- '--type=renderer'` 全局求和，稳定得到
**5.7 GB**——那是这台机器上所有 Chromium（含用户自己开着的浏览器）。它稳定、
可复现、量纲也对，唯独**主语不是被测对象**。正确取法是启动前快照已有的
renderer pid、启动后取差集（`tests/support/browser_dom_probe.mjs`）。
同一族的还有一条：`663 533` 是标签数、`2 887` 是浏览器的 `Nodes`，
**两把尺子不能互相相除**。
