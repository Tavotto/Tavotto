# ADR 0035：坐标轴边框的语义命中区与四边刻度模型

状态：**Accepted**
日期：2026-09-02
相关：[0017 几何权威](0017-exact-manifest-authority.md)（命中层只在精确 manifest 就位时
工作，边框几何随它下发）、[0032 属性能力层](0032-typography-capability-layer.md)
（「不支持」「没设过」是不同的答案；这里「方向未知」的边同样不摆假开关）、
[0030 统一检查与问题定位](0030-validation-and-problem-navigation.md)
（`tick-direction` 规则定位到刻度卡的方向档），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 「点哪里控制刻度」 | 按**视觉语义**：边框线内侧（数据区那一侧）的带控制这一边的**向内**刻度，外侧的带控制**向外**刻度，线本身附近是中性带（选中子图，不切刻度）。修改前示意图的刻度命中区写死在框外——刻度朝内时短线画在框里、点它却没反应 |
| 命中带的宽度 | 以**屏幕像素**计（鼠标 / 触控笔 2.5 px 中性 + 到 10 px；手指 4 px + 到 18 px），放大缩小都不变。devicePixelRatio 不进判据：指针坐标本来就是 CSS 像素 |
| 边框线从哪来 | 引擎按 `Spine.get_path()` 经它自己的 transform 变换后的两端下发（manifest 的 `spines`，figure 分数、y 向下）——**含 `set_position(("outward", n))` 的偏移**。不能用 `get_window_extent`：它把刻度伸出量也算进去了 |
| 哪些轴有 | 只有直角坐标轴（`ax.name == "rectilinear"` 且四条命名边框齐全）。极坐标（边框是圆弧）、3D（四条 `spines` 只是占位）、色条轴（刻度归色条元素）**不给** `spines`，前端因此不摆直接操作——不是摆一套点了会错的 |
| 哪些边有 | 拥有这一边的 axis 不可见（twinx 第二个 axes 的 x 轴）或线退化成一点（`secondary_xaxis` 的左右两条）不出；线不显示且刻度也不显示的边没有可见目标，**不设命中区** |
| 角落 | 两条边的带叠在一起时取垂直距离更近的；并列时先取此刻真画着刻度的那条，再按固定次序（下、左、上、右）。孪生轴 / secondary 与宿主的边框逐位重合，同一条规则 |
| 优先级 | `pickElement` 命中了文字 / 曲线 / 别的子图 / 刻度文字时边框命中区**让路**；只有命中 figure（图外空白、偏出去的边框）或那条边所属的子图本身（含铺满它的位图）才算。resize 手柄在 OverlaySvg 层、天然在上面 |
| 状态模型 | matplotlib 的 `direction` 是**一条轴**的属性（上下共用 / 左右共用，`tick_params` 没有按边分方向的入口），显不显示是**边**的属性（`ticks_<side>`）。每边的形态派生：`inward = ticks_side && direction ∈ {in, inout}`，`outward = ticks_side && direction ∈ {out, inout}` |
| 映射 | 开着 → 关：另一方向还开着就只改方向（inout → 单向）；另一方向也没开就关掉这一边（`ticks_<side>=false`，方向不动）。关着 → 开：这一边原本隐藏先打开；方向 = 轴上已含另一方向 ? `inout` : 这一方向。**方向落在轴上，同轴另一边（可见时）跟着变**——这是底层能力的边界，界面必须说出来（hover 文字点名、示意图 tooltip、连带的带浅色一起亮），不装作每边独立 |
| 一次点击 | 一份计划（`set` + `remove`）进**同一次 commit**：方向落在刻度元素、显隐落在子图元素，拆开会渲染出一帧「边开了、方向还是旧的」，撤销栈也多一条 |
| 三处同源 | 画布命中区、示意图的内 / 外两带、刻度卡的方向四档与「显示边」读同一份模型（`lib/tickSides.readAxesTickModel`）、走同一份计划函数、同一个 `applyTickSidePlan` |
| 属性页的「隐藏」 | 派生态（这条轴两边都不显示刻度线），不是第四个真值：选它写两边 `false`，方向不动；从它选回方向时**删掉两边的 override 回到脚本的边**（脚本一般至少开着下 / 左；脚本本来一边都不开的不替用户猜，示意图上再点一边即可） |
| 主 / 次刻度 | `length` / `width` 只动主刻度（与 `tick_params` 默认 `which="major"` 同口径），新增 `minor_length` / `minor_width` 只动次刻度。**语义变化**：此前 `which="both"`，改主刻度长度会把次刻度一起拉长。方向 / 颜色 / 字号仍主次同改 |
| 次刻度没开时 | 次刻度的长度 / 线宽照出：值读轴上 `_minor_tick_kw`（没有才按 rcParams），先设长度再开与反过来结果一样 |
| Hover 反馈 | 高亮条 + 一行状态文字（哪边 · 向内 / 向外 · 开着 / 关着 · 点击会怎样 · 连带谁），文字随面板旋转反转回来；**没有过渡动画**（reduced motion 下不会闪），只在 hover 期间存在。点击后不弹 toast |
| 磁盘格式 | **不升版**。`spines` 是渲染派生数据（不进文档）；新增的都是 override |

## 1. 背景

刻度与边框的示意图（`TickAndSpineDiagram`）把每条边的刻度命中区画在框外一圈：
刻度朝内（`direction=in`）时短线画在框里，点它什么都不发生，得去点框外那块
空白。这就是「刻度朝内却必须点击图框外侧才能控制」——命中区与视觉语义背道
而驰。画布上则根本没有边框 / 刻度线的命中：`ticks` 伪元素的包围盒圈的是刻度
文字，不是刻度线。

Prompt 16 要的是：点内侧控制向内、点外侧控制向外、两侧都开显示双侧；与属性
页的精确控制双向同步；zoom / DPR / 旋转下命中稳定；不支持的轴诚实降级。

## 2. 命中模型

```text
每条直角边框（manifest.spines[side]，引擎按画出来的那条线给端点）
  d      点到线的有符号垂直距离（屏幕 px，正 = 数据区那一侧）
  along  沿线位置（屏幕 px，容许端点外 band 之内）
  zone   |d| ≤ neutral → neutral；d > 0 → inner；d < 0 → outer；|d| > band → 不命中
```

`lib/tickSides.spineZoneAt` 是纯函数：分数坐标 + 「一个分数单位对应几个屏幕像素」
（面板内容边长 × zoom）进来，带宽在屏幕像素里定，所以 zoom 变了带不变；面板
旋转由 `ElementHitLayer.frac` 反旋转，命中函数不知道旋转这回事。`zoneRectFrac`
用同一把尺把带换算回分数画高亮条——高亮与命中逐像素重合。

## 3. 状态与计划

```text
readAxesTickModel(manifest, overrides, axesGid)
  sides[side] = { visible: ticks_<side>, direction: <axis>ticks.direction, inward, outward }
  只有「子图有 ticks_<side> 字段 + 那条轴的刻度元素有 direction 字段」的边才进模型

toggleSidePlan(model, side, 'inner' | 'outer')   → { set, remove, effect }
axisChoicePlan(model, axis, in|out|inout|hidden) → 同上
sideVisiblePlan(model, side, on)                 → 同上
effect.coupled = 方向那一步连带改到的同轴另一边（可见才算）
```

`store/actions.applyTickSidePlan` 把一份计划落成一次 commit（`finishActiveGesture`
+ 一次 `updateObject`），历史标签说明「哪边：显示 / 不显示 哪个方向」。

## 4. 明确不支持

| 情形 | 表现 |
|---|---|
| 极坐标 / 3D / 色条轴 | manifest 没有 `spines`，画布无命中区；示意图仍按 `ticks_<side>` 字段有无决定画不画（3D 没有这些字段） |
| 引擎没发某条轴的刻度元素（刻度文字整组没画） | 那两条边方向未知：画布无命中区；示意图退回单个 `ticks_<side>` 开关（命中带盖住内外两侧，画成 matplotlib 默认的朝外） |
| 按边分方向（下边朝内、上边朝外） | matplotlib 不支持；不伪造 |
| 次刻度单独的方向 | 沿用主次同改；属性面板有次刻度的长度 / 线宽 |

## 5. 迁移与兼容

* **`length` / `width` 的语义从 `which="both"` 改为 `which="major"`**：存量文档里
  有这两条 override 且脚本开着次刻度的，次刻度回到脚本自己的长度 / 线宽
  （matplotlib 默认 2 pt / 0.6 pt）。这是修正而不是回归——「主 / 次」的区分本来就
  该在；要次刻度一起变的用户在刻度卡里多一条 `minor_length` 可设。
* manifest 新字段 `spines`（axes）与 `minor_length` / `minor_width`（ticks）：老前端
  原样忽略；写回自检只比 gid 集合与几何。

## 6. 看护

* `tests/test_tick_sides_geometry.py`：四边几何在框沿、偏出去的边框、隐藏边框、
  twinx / secondary / polar / 3D / 色条轴、对数 / 反转不改几何；主 / 次分档、顺序
  无关、先设后开、像素真变 + 撤销回原样、3D 不出次刻度字段。
* `web/src/lib/tickSides.test.ts`：三带分类、zoom 不变、触控带、角落并列、偏出去
  的边框、无目标的边、in / out / inout / hidden 映射（含全状态扫描）、四档与显示边、
  孪生轴挑边。
* `web/src/canvas/spineZones.test.tsx`：hover 高亮 + 文字、离开即消失、连带的边、
  点击一次 commit + 选中、中线只选中、文字 / 刻度文字优先、zoom / 触控 / 旋转 /
  偏出去的边框 / 无 `spines`。
* `web/src/components/inspector/tickTaskCard.test.tsx`：示意图两带的 aria 状态与
  点击、一次历史、连带点名、四档与「隐藏」派生态、显示边开关、次刻度长度写入、
  `data-prop="direction"` 锚点、键盘可达。
