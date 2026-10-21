# ADR 0135：3D 轴上的数据图元进元素表——曲面 / 等值面 / 散点 / 曲线 / 线框可点、可改样式，色条与曲面成色阶兄弟

日期：2026-10-18 · 状态：**Accepted**（兼容性修复，实现随本 PR；本地验证，未推送）
相关：[0100 matplotlib 拖动全族排查](0100-matplotlib-drag-coverage.md)（拖不动的说出为什么）、
[0086 命中几何的全族排查](0086-precise-hit-geometry-for-series-shapes-and-annotations.md)（轮廓 / bbox / 预算）、
[0083 override 目标身份](0083-override-target-identity.md)；`docs/rules/backend/axes-and-artist-families.md`、
`docs/rules/backend/colorbar.md`、`docs/rules/backend/override-application-and-replay.md`、
`docs/rules/repo/predicate-subject.md`

## 问题

用户的「三维连续场云图」复现包（`04_三维连续场云图`：B 数据驱动重绘、C 细网格曲面、D 离散节点）里，
曲面、3D `contourf` 底投影、3D 散点、`plot3D` 曲线整张图**点不中、改不了颜色**：

- `manifest.instrument` 在 3D 轴上 `if not is3d:` 把全部数据系列跳过，于是 `Poly3DCollection`
  （`plot_surface` / `plot_trisurf` / `bar3d`）、`QuadContourSet3D`、`Path3DCollection`、`Line3D`、
  `Line3DCollection` 全进了 `unsupported`——B / C / D 三个变体实测各漏 3 / 2 / 2 个；
- 色条是 `fig.colorbar(ScalarMappable(norm=norm, cmap=cmap))`（独立 mappable，图上没有对应图元）。
  改色条 cmap 只改了色条自己，曲面纹丝不动：`colorbarmodel.scale_siblings` 按 norm 对象身份找兄弟，
  **只在已登记元素里找**，曲面没登记，就找不到。

此前 CompatBench 把 3D 数据记成 `unsupported_by_design`（「3D 数据属于数据，改它该回代码」）。那句
话对**位置 / 数据偏移**仍然成立，对**颜色 / 色图 / 线宽 / 透明度**不成立——后者与 2D 同族的
样式字段在语义上没有区别，用户要改的恰恰是它。本 ADR 把边界挪到该在的地方。

## 实测（matplotlib 3.8.4 / 3.10.9 / 3.11.2，三个版本逐格一致）

不假设 3D 子类的 setter 行为，逐个类量了「apply → draw → 像素真的变没变」与「热态 == 冷启动重放」，
量出来的坑决定了下面「放什么、怎么放」：

| 事实 | 后果 |
| --- | --- |
| `Axes3D.draw` 在 `computed_zorder=True`（默认）时**每帧重写 3D 集合与 Patch 的 `zorder`**（不含 `Line3D`）——`set_zorder` 一个像素不变，读回来的是上一帧被覆盖的值 | Collection / Patch 的 `zorder` 字段不放（`computed_zorder=False` 时才放，判据是这条轴**此刻**的实况；`Axes3D.draw` 只重写这两族，3.8.4 / 3.10.9 / 3.11.2 读源码核实）。**`Line3D` 的 zorder 不被重写、照给**（Codex #1159 P1 修订） |
| `Poly3DCollection.set_alpha(None)` 清不掉烤进 `_facecolor3d` 的 alpha（`to_rgba_array(旧数组, None)` 保留数组自带的 alpha 列）；`set_alpha` 不碰边色、`set_edgecolor` 却按**当时的** alpha 合成边色 | 单色曲面 / `bar3d` 设 alpha 再撤销，面色停在设过的值（像素差 3437）；边色与 alpha 谁先谁后画出两张图（像素差 3431）——热会话逐步累加与冷启动按列表序重放因此分叉，写回像素门 409。面色 / 边色 / alpha 改完一律规范化（`overrides._canonical_poly3d_colors`）：三者的设定都回到脚本原样时，把首次触碰前记下的四份数组**原样放回**（撤销逐位回到 mplot3d 自己留下的状态，含它的怪癖：脚本事后 `set_alpha` 留下的不透明边、`set_edgecolor("face")` 抄来的深度序面色）；否则按**原始设定 + 此刻 alpha** 重建，口径同 2D `Collection.set_alpha`（**alpha 同时落在面与边上**——与 mplot3d 原生「事后 `set_alpha` 不碰边」不同，但与构造时传 alpha、与 2D 一致，且不再取决于谁先谁后） |
| `Path3DCollection.do_3d_projection` 每帧把 `_sizes` / `_linewidths` 重排成深度序，`get_facecolor()` 返回深度排序并乘过 `depthshade` alpha 的数组（`[0]` 随视角变） | 字段值与 handler getter 一律读脚本顺序的那一份（`_sizes3d` / `_linewidths3d` / 未排序的 `_facecolors`）：`overrides.collection_sizes` / `collection_linewidths` / `collection_face_rgba` / `collection_edge_rgba` 唯一出处；不放逐点颜色 / 深度 alpha |
| 轮廓的偏移与 `get_transforms()`（来自最后一次 draw 的深度序大小）对不上号 | 逐点大小不同的 3D 散点轮廓摆到别人的位置上；`pathgeom._drawn_offsets` 用 draw 时那份 `_offset_zordered`（被遮罩的点填 NaN，`_stamp_markers` 本来就丢非有限偏移） |
| `Axes3D.draw` 只对**可见**的集合调 `do_3d_projection`（整条轴隐藏时一个都不调） | 隐藏的元素带着上一次可见时的投影：先转视角再隐藏，热态与冷启动重放量出两套 bbox / 轮廓，写回自检 409。`manifest._refresh_hidden_3d_projections` 按此刻视角补投影（隐藏的元素仍在元素树里，要能点着取消隐藏，几何要真） |
| `linewidth=0` 的集合上 `linestyle="--"` 被 `Collection._bcast_lwls` 缩放成全 0 的 dash；Agg 的 C++ 路径不检查，**SVG / PDF 的 `gc.set_dashes` 当场抛 `ValueError: At least one value in the dash list must be positive`**，需同时有边色 + 花纹（2D 的 `fill_between` 逐位同样复现） | 3D 曲面惯用 `linewidth=0`，用户先选虚线再调线宽就会让预览画不出来。Collection 的线宽 / 线型 setter 之后把缩放成全 0 的 dash 退成实线（`overrides._heal_zero_dashes`；`_us_linestyles` 原样保留，线宽再设成正数时 matplotlib 自己重新缩放） |
| `Collection3D`（`contour` / `contourf` 的 `QuadContourSet3D`）的 `_paths` 每帧由 `do_3d_projection` 重写；`Line3D.set_data` 每帧被投影结果覆盖 | 位置 / 数据偏移 / `set_data_3d` 一概不放；样式（颜色 / 线宽 / 线型 / marker / 花纹）经 `Collection.draw` / `Line2D.draw` 兑现，照放 |

## 裁决

### 一、按 2D 同族登记，gid 与 2D 同一条规则

`manifest.instrument` 的曲线与 Collection 登记段 **2D / 3D 共用同一段代码**（不另起一套）：gid 前缀只有
`_collection_gid_prefix` 一处出处，所以 `Poly3DCollection`（`PolyCollection` 子类）是 `axes_i.fill_j`、
`Path3DCollection` 是 `axes_i.scatter_j`、`Line3DCollection` 是 `axes_i.linecoll_j`、
`QuadContourSet3D` 是 `axes_i.collections_j`、`Line3D` 是 `axes_i.lines_j`——序号仍是所属列表
（`ax.collections` / `ax.lines`）的下标。**3D 轴以前没有这些 gid，没有存量 override 兼容问题；2D 的
gid 一个都没挪**。能改什么由 `collection_caps()` 与 `_line_fields` 的**实况**决定，不按类名。

### 二、放出的字段 / 不放的字段

放：颜色 / 色图 / vmin / vmax / 透明度 / 线宽 / 线型 / 花纹 / marker / 大小 / 可见 / 标签（各族按 2D 同一张
能力表）。**不放**：Collection 的 `zorder`（`computed_zorder=True` 时；`Line3D` 的照给）、位置 / 拖动 / 数据偏移、`depthshade` 下的逐点颜色
与深度 alpha、`Line3D` 的数据（`set_data_3d`）。**不可拖**：manifest `draggable=False`，前端落到 ADR 0100 的
`series` 理由（「位置由数据决定；要挪整个子图，拖子图空白处」，3D 子图本身可拖）。面状色图集合在 2D 里
「代理给宿主子图」那条（`_is_area_field`）对 3D 不成立（曲面是投影出来的菱形、不铺满子图），3D 不代理。

### 三、几何

draw 之后 3D artist 持有投影后的 2D 路径，复用现有 `pathgeom.element_geometry`（`Poly3DCollection` 走
`PolyCollection` 那支、`Path3DCollection` 走 `PathCollection` 的 marker 轮廓、`Line3D` 走 `Line2D`、
contour / 线框走通用 `Collection`）。超点数预算（160×160 的 `plot_surface` = 25 281 个面）按现有规则退回 bbox，
而且是**早退**：`_collection_subpaths` 一超预算当场收手，C 变体 fig7a 展开 507 个面片就停了，manifest 构建
0.21 s → 0.27 s。看护按**展开次数**数，不按时钟（时钟判据在慢机器上会假红）。

### 四、色阶兄弟

独立 `ScalarMappable` 色条与共用同一个 Norm 对象的曲面 / 3D contourf 因为登记了而自动成组（判据仍是
`colorbarmodel.scale_siblings`，一行没改）：**从色条改 cmap，三者一起变；vmin / vmax 经共用 norm 从任一处改
三者一起变；撤销逐字回到脚本原样**（含没注册的自定义色图 `coolwarm_preview`：`cmap_current.custom` 照常下发）。
**修订（2026-10-18，用户裁决）：色阶组任一成员改 cmap 都广播整组**（曲面 / 底投影 / 色条，2D 的 imshow / pcolormesh / contourf
兄弟同理；没有色条、只是共用 Norm 也广播——判据都是「共用 Norm 对象 = 同一色阶」）。推翻了原先「从曲面改 cmap 只改曲面、兄弟窄 override
压过色条广播」。撤销各回各的原样（含自定义未注册色图），重做再整组变；历史文档里组内多条 cmap override：文档序靠后的赢，不报 warning、
不阻断写回；前端写入口只留一条。原规则的修订记录在 `docs/rules/backend/colorbar.md`（色阶兄弟一段）。已接受的代价：2D 里两块共用 Norm、
只想改其中一块的用法也会整组变。看护 `tests/test_scale_group_cmap.py`。

### 五、容器与 Patch3D 暂不做

3D 的 `errorbar` / `stem` / `bar`（`ErrorbarContainer` 等）不做语义系列：成员（`Line3D` / `Line3DCollection` /
`Patch3D`）**不**当独立曲线登记、也不假装被代表了，如实留在 `unsupported`（census）。`ax.bar` 的 `Patch3D`
同理——若登记成 `patch` 会误给 `pos_frac` 拖动（位置是数据）。

## 不做 / 遗留（诚实记账）

1. **共用 Norm 的两个 mappable 各自叠加窄 `vmin` / `vmax` override**：后采的「脚本原样」已被先改的那个经共用
   norm 污染，撤销次序不同停在不同值。**2D 的 `InvShared` 逐位同样复现（存量缺口，不是本 ADR 引入）**，
   3D 因曲面与 contourf 常共用 norm 而更容易碰到；色条广播 + 窄兄弟那几种组合有 `K-3d-*` 用例钉着、是干净的。
   修法是把窄 `vmin` / `vmax` 也纳入色阶兄弟的代采原样，要动别名组的次序规则，单独做（跟进 issue）。
2. `value_original`（「脚本原样」的显示提示）在同一元素上两条互相牵动的 prop 逐步累加时与冷启动分叉
   （2D 的线色 → `markerfacecolor`、散点面色 → `edgecolor` 同样）：只是显示提示，状态 / 像素 / 其余字段一致。
3. `depthshade` 开关、`axlim_clip`、3D 图例项的逐点样式不做字段；`Line3D` 在**整条 3D 轴隐藏**时不重投影
   （只有集合有 `do_3d_projection`），几何停在隐藏前——轴隐藏时它本来不可点。
4. CompatBench 的 5 个 `art_3d_*` case 从 `unsupported_by_design` 升为 `full_support`，期望里补了数据图元的
   可编辑字段与变异（`tests/compat/manifest.json` / `baseline.json`）。

## 看护

- `tests/test_mplot3d_data_artists.py`：三张图里每个 3D 数据图元的登记 / gid / role / 字段全集（Collection 没有 `zorder`、Line3D 有，
  `draggable=False`、无宿主代理）；`computed_zorder=False` 时 zorder 给且改得动像素；3D 容器不重复登记；
  bbox / 轮廓对真实墨迹（`tests/support/mplot3d_probe.py` 在 worker 解释器里量）；160×160 曲面早退（展开次数）；
  逐点大小对号；独立色条 ↔ 曲面 ↔ 3D contourf 成组、撤销回自定义色图；每个图元的每条字段逐步累加后热态 ==
  冷启动重放（再撤一半仍相等）；先转视角再隐藏热态 == 冷启动；alpha 撤销还原；边色与 alpha 与顺序无关；
  线宽 0 的虚线 + 边色 + 花纹不再让 SVG 预览崩。
- `tests/test_invariants_engine.py`：`Inv3D` / `Inv3DP` / `Inv3DS` 三张图并入通用扫描（能力真实——每个宣称的
  字段 apply 后像素真的变、枚举可用、逐字还原像素 + manifest），外加 `K-3d-*` 三条色阶兄弟的热态 == 重放。
  `overridesample._ENABLERS` 补了「边色要先有一条看得见的边」（`linewidth=0` 的曲面上边色画不出来）。
- 三个 matplotlib 版本（3.8.4 / 3.10.9 / 3.11.2）都在本地跑过上面两个文件；`docs/ci/matplotlib-upgrade-checklist.md`
  §4 登记了新增的私有契约，升级 matplotlib 时逐条核。
