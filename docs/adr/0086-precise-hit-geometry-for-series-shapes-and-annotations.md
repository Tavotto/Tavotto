# ADR 0086：误差棒、茎叶系列、Patch 全家族与注释的精确选中轮廓

日期：2026-09-26 · 状态：**Accepted**（2026-10-18 修订命中评分，见文末）
相关：`docs/rules/backend/marker-and-path-geometry.md`（路径几何 `geometry`）、
`docs/rules/frontend/hit-and-selection-geometry.md`（前端命中与描示）、
[0051 保形归一化](0051-preserving-normalization.md) §3（「误差棒容器没有 geometry、图例压它只标风险」**不变**，见「不在本次范围」）

## 问题

用户反馈：画布上选中误差棒时，蓝色高亮贴不住误差棒——罩着一个矩形，点在误差线旁边的空白也选中它，
几组误差棒交错时点这一组选中的是另一组。用户同时说「这类无法精准选中的问题似乎有很多」。

误差棒（`ErrorbarContainer`）与茎叶系列（`StemContainer`）在 manifest 里是伪元素（`SeriesGroup`），
SVG 里没有自己的节点，manifest 只给成员的并集 bbox。前端对没有 `geometry` 的元素按 bbox 命中、画矩形，
所以选中框盖住数据点、误差线、帽之间的全部空白；命中评分按 bbox 面积，几组交错时 bbox 小的那组恒胜。

## 排查方法

对引擎能识别的每一族 artist，用真实 matplotlib 脚本量两样东西，两侧互相独立：

- **墨迹**：Agg 整图渲染，减去「把这个元素的 artist 全藏起来再渲染」，逐像素差；
- **命中区 / 高亮**：按前端 `pickElementStack` 的判据现算（有 `geometry` 的沿路径 1.5 mm 或半线宽、
  `fill` 算内部、按 `clip` 裁；箭头按端点线段；其余 bbox ± 0.004）。

每个元素报三个数：`cov`（墨迹落在命中区里的比例，低 = 点在墨迹上选不中、高亮漏一截）、
`blank`（命中区里离墨迹超过容差的比例，高 = 高亮罩住空白）、`steal`（它独占的墨迹上，
按前端评分胜出的却是别的元素的比例，高 = 点它选中别人）。真浏览器里另跑误差棒的 e2e：
覆盖层描出来的那条 path 与 matplotlib SVG 上误差线 / 帽 / 数据点的墨迹逐点比距离。

## 裁决

### 一、误差棒 / 茎叶系列出成员几何的并（`pathgeom.series_group_geometry`）

每个成员按自己那一族出几何，与它单独登记时是同一份实现（`element_geometry`）：

- 连线的数据线描折线；
- 只有 marker 的数据线与 markerline 逐颗描 marker；
- 误差线与茎（LineCollection）逐条描线段；
- 帽（只有 marker 的 Line2D）逐颗描。

合成一份，隐藏的成员不描。**画着东西的成员给不出几何时整组退回 bbox**。原因是只描一部分的话，
缺的那部分墨迹既点不中也框不到，比 bbox 更糟。给不出几何的情况有两种：marker 超过 `MAX_MARKERS`，
或者点数预算不够。

`fill` 的合成规则：任一成员实心，**并且**没有别的成员带着多于两个点的开放折线，才是实心。
原因是前端把 `fill` 下的子路径一律按面积算，连线的数据线会被当成多边形。这与「有连线又有 marker
的 Line2D 只描折线」是同一个理由。

### 二、Patch 全家族描真实路径

从前只有 Polygon / PathPatch 有轮廓，下面这些都退回 bbox：

- 圆、斜椭圆、饼图扇形；
- 圆角框；
- axhspan / axvspan 的矩形。

斜椭圆的 bbox 四角能选中它，描边伸在 bbox 外的那半圈线宽点不中。饼图相邻扇形的 bbox 互相罩住，
点大扇形选中的是 bbox 更小的邻居。现在一律走 `get_path()` + `get_transform()`，与 `Patch.draw` 同一条路。
只有阴影线、不填色的形状，内部也算墨迹。单根柱（`bar`）仍用 bbox，它本来就是矩形。

### 三、注释文字与箭头、带底框的文字

- **注释的文字框只是字**。`Annotation.get_window_extent` 把箭头也并进来：「peak」的选中框一直伸到
  箭头尖，点箭头与字之间的空白也选中这段字，而箭头自己是另一个元素。
- **带字注释的箭头描真实箭杆 + 箭头**，只按描边命中。它不出端点（端点归注释管、不能单独拖），
  从前只有罩住弯箭杆的 bbox。开放的弯箭杆若按面积算，会把弧与弦之间的空白也算进去。
- **带底框的文字（`bbox=dict(...)`）框含底框**。从前框只圈着字，点在底色上选不中它，
  这时的 `cov` 只有 0.39。

bbox 的这两处变化，对拖动锚点没有影响：文字的 `anchor` 由 `get_position()` 算，不看 bbox。
对齐与吸附拿到的是画出来的那块。

## 不在本次范围（排查表里记录，另开 issue）

- ~~命中评分只看「墨迹面积」，不看「点在谁的墨迹上」~~ **已于 2026-10-18 修订，见文末「修订：命中分层」。**
- **刻度文字组（`ticks`）用一个矩形罩住整排标签**。极坐标的角度标签绕一圈，空白达 0.92。
  可以改成逐标签矩形的 multi_path，这属于文字组的矩形语义决策。
- 以下几处的 bbox 比描边小半个线宽，`cov` 在 0.87–0.96：
  - 色条延伸三角（在色条轴 bbox 之外）；
  - AnchoredText；
  - 表格边框。
- **有连线又有大 marker 的曲线**（`"-^"`、ms 大于约 8）：marker 尖伸出 1.5 mm 容差（`cov` 约 0.97）。
  要并存得先给 geometry 分层（每条子路径自己的 fill），这次没有动。

- **干涉检测（`interference.py`）对误差棒 / 茎叶系列仍按包围盒判**（`BBOX_BASIS_ROLES`）。
  拿新 geometry 把「图例压住数据」从风险升成确定，会让保形归一化在 `test_mcp_normalize` 的 Two 上
  从完成变成约束冲突：缩到 120 mm 后，图例左上角真的压着橙色误差棒的折线。这是归一化契约的改变，
  要单独裁决。
- **干涉检测只看路径的边，不看填充内部**。本 ADR 让圆、椭圆、扇形等 Patch 有了 geometry：图例整个
  落在一大块实心形状**内部**、却不碰它的边时，从前按 bbox 报风险，现在不报。Polygon / fill_between
  早就是这样，修法是 `fill` 的几何在 `_segments_hit_rect` 之外再判「矩形在内部」。

- **快速编辑里 Fig1_kinetics 的 X 轴标题被切掉半截、点不中**（v0.16.0 起就有）：**是显示裁剪，不是命中区**。
  脚本用 `savefig(bbox_inches="tight", pad_inches=0.02)` 存盘，磁盘原件是 75.26 × 58.68 mm，轴标题完好。
  画布显示的是 live 图，图幅按 figsize 算，是 80 × 57.6 mm，轴标题的 bbox（y 0.980–1.030）有一半伸在图幅外，
  SVG 在图幅边界被裁掉。真浏览器实测（隔离实例，94% 缩放）：
  - 图幅内那条约 3 px 的可见部分**点得中**，选中的是 X 轴标题；
  - 图幅外那半截点下去落在舞台上，选区不变；
  - 选中框按 bbox 画，会伸出图幅外。

  命中区与画出来的一致，这次没有可修的命中缺陷。根因是「tight 当不当图幅」，已登记在
  `docs/rules/backend/figure-capture-and-execution.md` 与 ADR 0051 §3、0080，属于 ADR 级未决问题。
  建议两步：
  1. 把 savefig 的 kwargs 记进捕获描述符；
  2. 由新 ADR 裁决是否把 tight 包围盒当作 live 图幅。这会改几何权威的坐标系、预览、导出与写回。

  在那之前，用户可以做两件事：预检 `element-outside-figure` 会把裁切说出来；元素树里的「X 轴」一行可以选中它。

## 看护

- `tests/test_series_shape_geometry.py`：误差棒四种写法、茎叶、上限整组退回、斜椭圆、阴影线、
  饼图扇形、注释文字框、注释箭头、带底框文字。
- `web/e2e/element-path-selection.spec.ts` 的误差棒用例：真浏览器 + 真 matplotlib。它断言四件事：
  - 点误差线选中的是它自己那组；
  - 选中轮廓与 SVG 墨迹逐点距离小于 3 px；
  - 没有矩形框；
  - 组内空白不再选中。
- `tests/test_manifest_geometry.py`：既有各族。

## 修订：命中分层（2026-10-18）

「已知未修」的那一条落地了。触发它的是一张真实用户图（斜投影手搓的「三维线图」：2D 轴上每条
彩色系列线下面有一片同色半透明 Polygon「幕墙」、8 片灰色截面、一个 28 面的 PolyCollection，
再加几十条 `ax.plot` 的短虚线网格段）：沿 5 条系列线各取 29 个点点击，只有 1–5 次选中线本身，
其余被填充内部（细长薄片面积恒比线的墨迹面积小）和短网格段（墨迹面积更小）抢走。

### 原则

命中优先级反映**用户在光标处看到的东西**：描边命中（光标在可见墨迹的容差内）只要没被画在它
上面的不透明填充盖住，就优先于「仅落在填充内部」；半透明填充不挡线。

### 一、引擎给 geometry 补两项绘制事实（加字段协议，老前端忽略）

* `paint_order`：真实 draw 顺序里的序号，越大越晚画。**主语**是 manifest 测量那一刻的 `fig` 对象
  （与同一轮量出的 geometry 同进程、同状态）：Figure / Axes 的 `get_children()` 按 zorder 稳定
  升序、递归展开（`pathgeom.paint_ranks`），与 matplotlib 的 draw 同一规则——zorder 相同按添加次序，
  不同 axes 按 axes 的 zorder（twin 轴整个在宿主之后），inset 等子轴按它自己的 zorder 排位。
  不是脚本里的 zorder 字面值，也不是 manifest 登记序（登记序按种类分组，不是 draw 次序）。伪元素
  （柱形系列 / 误差棒 / 茎叶）取成员里最晚画的，**但只在成员同处一层时才给**：成员 rank 区间 `[min, max]` 内夹着非成员 artist（例：一根柱 zorder 5、其余柱默认、线 zorder 4）就不写 `paint_order`，次序未知＝不盖别人也不被盖，免得整组被报在线之上而把低柱上方的线降层。不用「成员 zorder 全相同」：误差棒的误差线与数据线 zorder 差 0.1 却紧挨着画。端点箭头（`arrow_endpoints`，不出 geometry）把同一个次序写在**元素级** `paint_order`，前端按它参与遮挡降级（箭头无填充，只会被盖）。按 bbox 命中的墨迹类（text / title / axis_label / legend / legend_text）同理写元素级次序，主语是 `paint_ranks`：Figure / Axes 的 children 按 draw 顺序给号，叶子的**全部后代继承叶子的号**（它们由叶子自己的 draw 一并画出）——`legend_text` 即所属图例的号、`axis_label` 即所属 Axis 的号、`annotate('', …)` 的箭头即所属 Annotation 的号（`Annotation.get_children()` 不含 `arrow_patch`，在 `paint_ranks` 那一处显式补上）。一条继承规则盖住「子元素没次序」整族，不按角色逐个找所属者；拿不到不写。前端只让**次序已知**的墨迹 bbox 进 tier 0（可被后画的不透明填充降层），次序未知的留 tier 1 按旧面积评分。3D 子轴内容不给（按深度而非 zorder 画）。
* `fill_alpha`：有填充的 geometry 才给，填充此刻画出来的不透明度。**读 `get_facecolor()` 的 alpha，
  不乘**：matplotlib 的 `set_alpha` 是替换颜色里的 alpha（实测 facecolor alpha .5 + artist alpha .8
  画出来是 .8，像素验证）。逐元素色（多色 PolyCollection、逐柱不同 alpha）取**最小值**（保守：
  宁可认为盖不住，下面的线仍可选）；根本没有填充的成员（误差线；Patch 的 `fill=False` / `facecolor='none'`——后者 `get_facecolor()` 回的是 (r,g,b,0) 而非空数组，要先查 `get_fill()` 与 `_original_facecolor`；Collection 族的 'none' 才是空数组；已知不可区分：`bar(color=[..., 'none'])` 在 matplotlib 里已被转成 RGBA 元组、`_original_facecolor` 不再是 'none'，与真 alpha=0 无从分辨，按 0.0 保守报——下面的线仍可选）不计入，填充了但 alpha=0 的成员计入（值 0，整组按最保守的报）；全部成员都没填充则不给该字段。组的 `paint_order` / `fill_alpha` 只从**此刻真画出来**的成员算（`_member_draws`，与组几何跳过隐藏 / 空成员同一判据）——绘制事实描述的必须是 geometry 里实际有的那些路径。

放在 `ElementGeometry` 上而不是元素顶层：它们只在几何参与命中时才有意义，随 geometry 同生同灭，
盖章在所有 geometry 分支之后的一处（`pathgeom.stamp_paint`），不碰各族的出几何代码。

### 二、前端 `pickElementStack` 分三层

| 层 | 内容 |
| --- | --- |
| tier 0 | 描边命中：容差内的曲线 / 空心路径 / **自己画了边线**（`stroke`）的填充元素的边线 / 箭头 / 字与图例的框（字本身就是墨迹，落在 0 档，与旧评分逐位相同），且没被盖住 |
| tier 1 | 其余按旧面积评分：仅落在填充内部（或外沿容差带）、背景类 bbox（子图、图像、色条、刻度外壳、退回 bbox 的散点…） |
| tier 2 | 被盖住的几何候选（线或填充）：看不见，只留给 ⌥ 轮换，排在背景容器之后 |

* **「被盖住」**：存在另一个元素，光标落在它的填充内部、`fill_alpha ≥ 0.5`（`OPAQUE_FILL_ALPHA`）、
  且 `paint_order` 比候选大。次序未知（缺字段）不判遮挡；锁定的填充照样盖（画面上它在），隐藏的不盖。
* **阈值 0.5** 是肉眼分界：alpha 0.1–0.3 的幕墙下线依旧看得清、用户点它就是要选线；过半之后底下
  已退成背景。
* **tier 0 内部**：先按「离可见墨迹边缘」的距离量化成 0.5 mm 一档（先扣半线宽：点在画出来的粗线上
  是 0 档），再按墨迹面积（旧口径），再按 `paint_order` 大者（上层）优先，最后登记序。
  距离在面积之前，短网格段才不会因墨迹面积小抢走正落在长曲线上的点击。
* **没画边线的填充**（半透明幕墙）边界上什么也看不到：只算填充类，不升到 tier 0；它外沿 1.5 mm
  容差带照旧命中（旧行为，tier 1，旧评分），否则小提琴本体边缘会点不中。
* **tier 1 里**面积规则不变，唯一变化是上层不透明填充压过被它盖住的下层（后者进 tier 2）。

### 三、结果

* 真实图（夹具 `web/src/canvas/__fixtures__/obliqueProjection.manifest.json`，新引擎输出）：5 条系列线
  沿线点击 28/29、24/25（该线只有 7 个折点）、29/29、28/29、29/29 选中自己；剩下的几次是相邻系列线
  交叠处选了更细的邻线、或网格段正好压在线上（真实的歧义，⌥ 可换）。点在幕墙空白处仍选中幕墙。
* 排查表：箱线图各段（中位线 / 须 / 帽）此前自点命中 0/4 的几条现在 2/4，其余是须与帽端点交接处，
  本来就在彼此容差内；小提琴中线 0/3 → 3/3；极坐标曲线上的径向刻度偷点 3 次 → 0。
  **极坐标「径向刻度被填充抢走」没能复现**：简单极坐标图里刻度标签中心点击旧、新评分都选中自己，
  原先的实测大概出在另一种配置（刻度标签被带填充的大 patch 压住），未改。

### 被否的方案

* **只对 `pickElementStack` 的面积评分乘系数**：线与填充的面积量纲不可比，系数要随图调；且表达不了
  「被不透明的上层填充盖住」。
* **只用 zorder / 登记序判遮挡**：登记序不是 draw 序；脚本的 zorder 字面值不含跨 axes（twin 轴）
  与同 zorder 的添加次序。次序必须由引擎在测量时给。
* **前端自己按 alpha 相乘推不透明度**：matplotlib 是替换，乘出来是错的；事实在后端，前端只消费。
* **所有 bbox 元素都进描边层按距离比**：图像 / 色条 / 退回 bbox 的散点会在「线 1 mm 之外」赢过线，
  旧评分里是线赢。只有字与图例（本身就是墨迹）进 tier 0。
* **tier 0 内同档先比绘制次序再比面积**：规格是先面积后次序；真实图上实测结果满足 ≥27/29，不需要破例。

### 取舍与仍未解决

* 线正穿过散点 marker 中心时，线（描边层）赢过 marker（仅填充内部）；marker 在线上方且不透明时反过来。
  这是「看得见谁就选谁」的直接结果；点 marker 边缘（离线超过容差）仍选 marker。
* 刻度文字组的矩形语义、干涉检测、`tight` 图幅等其余「不在本次范围」条目不变。
* 3D 子轴内容没有 `paint_order`，不参与遮挡判断（3D 登记另有并行改动）。

看护：`web/src/canvas/pickStrokeLayers.test.ts`（每条规则一组，逐条变异反证）、
`web/src/canvas/obliqueProjectionPick.test.ts`（真实图夹具走前端同一套评分）、
`tests/test_paint_facts_geometry.py`（真 matplotlib：次序与 alpha 语义）。
