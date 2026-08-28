# ADR 0021：Complexity-Aware Editor Preview（预览表示法与语义编辑解耦）

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
| `EDITOR_SVG_SOFT_LIMIT_BYTES` | 8 MiB | **hybrid 的触发线**（Session 02/03 落地） |
| `EDITOR_SVG_HARD_LIMIT_BYTES` | 16 MiB | **raster 的硬闸**：超过就不读 |

两个数字都是**可调的策略**，不是物理常数；调它们要带上新的测量。选这两个初值
的理由：Phase E 基线里普通科研图的预览 SVG 是几十到几百 KB，含 imshow 的
面板最坏 827 KB——8 MiB 比它们高一个数量级以上，正常图一张都不会被碰到；
而 #181 那张是 126 MB，比硬闸高 7.8 倍，怎么调都在闸外。

**软闸在 hybrid 落地之前不改变任何行为**（8–16 MiB 的图照旧内联 SVG）。这个
缺口是刻意留的、有用例钉住的——Session 02 实现 hybrid 时那条用例会当场红，
逼着一起改，而不是靠谁记得回来。

---

## 5. 分阶段落地

| Session | 内容 | 状态 |
|---|---|---|
| 00 | 合成复现 + before 基线 + 本 ADR | 完成 |
| 01 | Large SVG Safety Guard（不变量 3 + raster 档 + 前端二道闸） | 完成 |
| 02 | Preview Complexity Analyzer（primitive / vertex 估算，喂 `estimated_*`） | 待做 |
| 03 | Hybrid Preview（mesh 层 rasterize，文字/轴/图例保持 vector） | 待做 |
| 04 | renderStore 的 SVG 内存预算 | 待做 |
| 05 | Diagnostics 与回归看护 | 待做 |
| 06 | 集成与 issue 收尾 | 待做 |

**顺序不能换。** 01 是止血：hybrid 还没有的时候，超限图至少不能再把浏览器
打死。02 在 01 的安全网之下才敢真的去遍历大 figure 的 artist 树。

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
  接管**，行为不变，但覆盖层的能力边界要在 Session 03 一并交代。

## 8. 看护

* `tests/test_issue181_large_preview.py` —— 合成 fixture 的确定性、规模、
  以及「它真的复现了机制」（一个 quad 一个 `<path>`）；
* `tests/test_preview_budget.py` —— 常量、判据、两侧同源、**超限时
  `read_text()` 一次都不调**；
* `tests/test_browser_session.py` —— playground 那条入口上的同一条闸
  （SVG 生在内存里，判据挪到「交给 JS 之前」）；
* `web/src/canvas/panelPreviewMode.test.tsx` / `web/src/lib/previewBudget.test.ts`
  —— 三档显示行为、raster 下命中层仍在、前端二道闸；
* `docs/perf-baseline.md` 的「大图预览基线」—— 前后对照的唯一出处。
