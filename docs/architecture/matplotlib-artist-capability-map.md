# Matplotlib Artist 能力地图

> 基准版本：**桌面内置 runtime 的 matplotlib 3.11.1**（`packaging/runtime-lock.json`）。
> 浏览器 playground 跑 **3.10.8**（`packaging/playground-runtime.json`）——核心对象模型
> 两版一致，差异逐条记在最后一节。本文所有结论都在这两个版本上**实跑验证过**，
> 不是照着文档或旧博客写的。
>
> 改 `manifest.py` / `overrides.py` 之前先读这里；升级 matplotlib 之前读
> `docs/ci/matplotlib-upgrade-checklist.md`。

## 0. 一句话

Tavotto 不是「支持 N 个 matplotlib API」，而是**理解 matplotlib 的 artist 对象模型**。
判断一个设计对不对，问题只有一个：

> matplotlib 明天加一个新的 pyplot 函数，只要它最终仍然产出已有 family 的 artist，
> Tavotto 是否**不用改代码**就理解它？

`plt.hist` / `plt.stem` / `plt.violinplot` / `plt.contour` / `plt.pcolormesh` 各写一套
兼容代码，是在给一张永远补不完的 `isinstance` 表续命。真正要建的是这条链：

```
matplotlib API  →  artist 图  →  artist family  →  Tavotto 能力  →  语义可编辑元素
```

## 1. matplotlib 的 artist family（3.11.1 实测继承关系）

```
Artist
├── Line2D                                     曲线（含 marker）
├── Text ── Annotation                         文字
├── Patch                                      形状
│   ├── Rectangle ── (bar / axhspan / hist)
│   ├── Polygon ── FancyArrow
│   ├── PathPatch ── StepPatch                 (stairs)
│   ├── Ellipse ── Circle / Arc
│   ├── Wedge                                  (pie)
│   ├── RegularPolygon ── CirclePolygon
│   ├── FancyBboxPatch / Annulus / Shadow / Arrow
│   ├── FancyArrowPatch ── ConnectionPatch     ★ 另有端点契约
│   └── Spine                                  ★ 结构件，由边框模型代表
├── Collection ── ColorizingArtist ── _ScalarMappable
│   ├── PathCollection            (scatter)          _CollectionWithSizes
│   ├── PolyCollection            (hexbin)           _CollectionWithSizes
│   │   ├── FillBetweenPolyCollection  (fill_between / stackplot / violinplot / kde)
│   │   ├── PolyQuadMesh               (pcolor)      + _MeshData
│   │   └── Quiver / Barbs             (quiver / barbs)
│   ├── LineCollection            (stem / errorbar / streamplot / violin)
│   │   └── EventCollection       (eventplot)
│   ├── QuadMesh                  (pcolormesh / hist2d / sns.heatmap)  + _MeshData
│   ├── ContourSet ── QuadContourSet   (contour / contourf)
│   ├── PatchCollection / TriMesh / RegularPolyCollection / EllipseCollection …
├── AxesImage ── ColorizingArtist                (imshow)
├── Legend / Axis / Table / Axes / Figure
└── Container（**不是 Artist**，是 tuple 的子类）
    ├── BarContainer / ErrorbarContainer / StemContainer
    └── PieContainer                             ★ 只有 3.11+，3.10 的 pie 回 tuple
```

三条一眼就该记住的：

1. **`ContourSet` 自 3.10 起就是一个 `Collection`**，住在 `ax.collections` 里，
   `cs.collections` 属性**已经没有了**。照旧版博客写的实现会当场 AttributeError。
2. **`Quiver` / `Barbs` / `PolyQuadMesh` / `FillBetweenPolyCollection` 都是
   `PolyCollection` 的子类**——按类名分派会把它们全归成「填充区域」。
3. **`ColorizingArtist` 把 Collection 与 AxesImage 统一在一起**，
   `cmap` / `clim` 的语义两边逐字相同。

## 2. Tavotto 的能力层

### 2.1 一条 prop 只写一次

`overrides.py` 里三张表：

| 表 | 覆盖 | 注册到 |
| --- | --- | --- |
| `_COLLECTION_CAPS` | label / facecolor / edgecolor / linewidth / linestyle / hatch / size / marker / cmap / vmin / vmax / alpha / visible / zorder | `("collection", *)` |
| `_PATCH_CAPS` | facecolor / edgecolor / linewidth / linestyle / hatch / fill / alpha / visible / zorder | `("patch", *)`、`("bar", *)` |
| `_GENERIC_CAPS` | visible / zorder | `("artist", *)` |

`_install_caps()` 用 **`setdefault`**：族里已有的**专用**契约（色条的 label、柱的
`bar_width`、箭头的端点、散点 marker 的还原…）永远优先。能力层是补齐重复的那层，
不是推翻既有裁决的那层。

### 2.2 能力按**真实 getter 实况**判，不按类名

`collection_caps(coll)` 回一个能力集：

| 能力 | 判据 | 为什么不按类名 |
| --- | --- | --- |
| `stroke` | 恒真 | 现在没有边 ≠ 加不上边（给 `pcolormesh` 加网格线是常见需求） |
| `fill` | `len(get_facecolor())` 且**没在做颜色映射** | 见下面那条 ★ |
| `mapped` | `get_array() is not None` | 同一个 `PathCollection`，`scatter(x,y)` 不映射、`scatter(x,y,c=z)` 映射 |
| `sizes` | 有 `get_sizes()` 且非空 | `_CollectionWithSizes` 里 `PolyCollection` 也在，但它的 sizes 是空的 |
| `marker` | `sizes` 且 **`isinstance(PathCollection)`** | `set_paths` 对散点是换 marker，对多边形集合是把用户几何整个换掉——那是改数据 |

★ **这条是整个能力层存在的理由**：颜色映射中的 Collection，它的 facecolors 每次
draw 由 `Collection.update_scalarmappable()` 从数组重算。`set_facecolor("red")` 之后
再 draw 一次，颜色**原样变回去**（3.10.8 / 3.11.1 实测一致）。给它一个填充色控件，
用户点了、界面显示改了、图纹丝不动——这比不给控件坏得多。

改动前 Tavotto 恰好踩着这条：`pcolor()` 的 `PolyQuadMesh` 与 `hexbin()` 的
`PolyCollection` 都被当成「填充区域」登记，`facecolor` / `edgecolor` 照出。

### 2.3 登记优先级（`instrument`）

```
语义容器（BarContainer / ErrorbarContainer / StemContainer）
      ↓  成员进 skip_ids，不再单独登记
逐个用户 artist（lines / images / collections / patches / texts / legend）
      ↓
ax.artists / ax.tables 的兜底（只开 visible / zorder）
      ↓
census()：既没登记、也不是结构件的 → manifest 的 `unsupported` 诊断清单
```

`skip_ids` 就是「consumption model」：容器声明「这些 children 由我代表」。
普查那一侧有对应物——被消费的成员不算漏。

## 3. 支持矩阵

分五档，不是「支持 / 不支持」两档。

### 3.1 完整语义支持（Full semantic support）

| Family | 代表类 | 语义元素 | 备注 |
| --- | --- | --- | --- |
| Line | `Line2D`（含用户子类） | `axes_i.lines_j` | 颜色/线宽/线型/marker/几何轮廓 |
| Text | `Text` / `Annotation` | `axes_i.texts_j`、标题、轴标签、图例项 | 可拖、上下标、描边、背景框 |
| Patch | `Rectangle` `Polygon` `PathPatch` `Wedge` `Circle` `Ellipse` `Arc` `StepPatch` `FancyBboxPatch` `Annulus` `RegularPolygon` + 用户子类 | `axes_i.patches_j` | 填充/描边/花纹/线型；**几何不给编辑**（几何=数据） |
| Arrow | `FancyArrowPatch` `ConnectionPatch` | `axes_i.arrows_j` | 独立箭头另有端点拖动；`annotate` 的箭头**不给**端点 |
| Image | `AxesImage` | `axes_i.images_j` | cmap/clim/插值/单色渐变换基色 |
| Colorbar | `Colorbar`（代理） | `axes_i.colorbar` + 语义身份 `cbar:<宿主>:<序号>` | 方向/extend 是就地结构改造 |
| Legend | `Legend` | `axes_i.legend` | 条目顺序等属性走重建 |
| Axes / Axes3D | `Axes` | `axes_i` | 落位、网格、边框模型、刻度模型、3D 视角 |
| 容器 | `BarContainer` `ErrorbarContainer` `StemContainer` | `barseries_j` / `errorbar_j` / `stemseries_j` | 成员被消费，统一改、按成员还原 |

### 3.2 Family 基础支持（Basic family support）

| Family | 代表类 | 语义元素 | 开放什么 |
| --- | --- | --- | --- |
| 线组 | `LineCollection` `EventCollection` | `axes_i.linecoll_j` | `color` / 线宽 / 线型 / alpha / visible / zorder（Line2D 的口径；**标量映射的不走这族**，见下一行） |
| Collection·描边型 | `ContourSet` 与标量映射的 `LineCollection` | `axes_i.collections_j` | 描边（edgecolor/宽/线型）、alpha、visible、zorder；映射的另给 cmap/clim。**没有面就不给花纹**（`get_facecolor()` 长度实测为 0） |
| Collection·填充型 | `PolyCollection` `FillBetweenPolyCollection` `Quiver` `Barbs` | `axes_i.fill_j` | 上面那些 + facecolor（未映射时） |
| Collection·网格型 | `QuadMesh` `TriMesh` | `axes_i.collections_j` | cmap/vmin/vmax + 描边（边色 / 线宽，可加网格线）；**不给 facecolor**，也**不给花纹与线型**——它们走 `renderer.draw_quad_mesh` / `draw_gouraud_triangles`，那两个渲染原语只接边色与线宽（实测 hatch/linestyle 各 0 像素，判据 `overrides.honours_stroke_style`） |
| Collection·网格型（通用路径） | `PolyQuadMesh`（`pcolor`） | `axes_i.fill_j` | 与填充型相同——它走 Collection 的通用绘制路径，花纹与线型**都认**（实测 10692 / 1100）。`pcolor` 与 `pcolormesh` 落在两侧，所以这不是「网格图不支持」，是「那个渲染原语不支持」 |
| 散点 | `PathCollection` | `axes_i.scatter_j` | 再加 size / marker 整体替换 |

> **`cmap` / `vmin` / `vmax` 只在「映射此刻真的在决定颜色」时出现**
> （`overrides.color_mapping_is_live`，照抄 matplotlib 的
> `Collection._set_mappable_flags()` 规则）。「有数组」不等于「在映射」：
> `LineCollection(..., colors="red", array=z)` 两个通道都没在映射，
> 而**用户设过 `edgecolor` 之后，映射的线组也会进入同一个状态**——那时
> 三个色图控件一个像素都改不动（实测），所以它们会**暂时消失**，撤掉边色
> override 之后自己回来。这与 family 判据（`is_color_mapped`，只看数组在不
> 在）是**两个问题**：family 必须在一次会话里恒定，否则 gid 与 handler 家族
> 会随用户的 override 翻脸。

### 3.3 只识别、不编辑（Render-only / recognized）

| 对象 | 元素 | 开放什么 | 为什么只有这些 |
| --- | --- | --- | --- |
| `ax.artists` 里的任意 Artist（`AnchoredText`、用户自定义 Artist…） | `axes_i.artists_j`（role `artist`） | `visible` / `zorder` | 两者由 draw 的公共机制兑现，**任何子类都逃不掉**；`alpha` 要靠每个 artist 自己在 draw 里读，基类不保证 |
| `matplotlib.table.Table` | `axes_i.tables_j` | 同上 | 单元格模型另有一套，本阶段不建 |

### 3.4 有意不开放（Unsupported by design）

产品边界：**artist 属性表达得了的展示改动 → Tavotto；数据与图形结构 → 回代码。**
所以「matplotlib 里有 setter」≠「Tavotto 应该开放」：

| setter | 为什么不开放 |
| --- | --- |
| `Line2D.set_data` / `PathCollection.set_offsets` / `AxesImage.set_data` | 改的是数据本身 |
| `PolyCollection.set_verts` / `Patch` 的几何（`set_xy` / `set_width` / `set_radius` …） | 图元几何由数据决定；柱宽是唯一例外（排版语义明确，且保持柱中心不动） |
| `set_norm`（LogNorm / BoundaryNorm …） | 换 norm 改的是「数据怎么被解释成颜色」，属于科学结论。`vmin`/`vmax` 只是同一个 norm 的定义域，等价于脚本里的 `clim=`，仍在展示范畴 |
| `ContourSet` 的层级（`levels`） | 等值线的层级是分析参数 |
| `Axes` 的 `spines` 位置、`set_xlim` 之外的数据语义 | 见 `compatibility.md` |

### 3.5 结构件，有意不出现在元素表（Internal）

`XAxis` / `YAxis` / `ZAxis` 及其子树、`Spine`、`ax.patch`、`fig.patch`，
以及**色条轴上的全部内部件**：色带 `cb.solids`（一个 QuadMesh）、分隔线
`cb.dividers`（一个 LineCollection）、`extend` 的两个延伸三角（PathPatch）。
它们各自由刻度模型、边框模型、`facecolor`、色条代理代表。

> **把 Collection / Patch 整族打开时最容易漏进来的就是这几个。** 它们每次
> `_draw_all()` 都被删掉重建，登记进去等于在元素表里放几个随时换身份的幽灵
> 条目，而且与色条代理重复。规矩是：**色条轴对外只有一个元素
> `axes_i.colorbar`**（外加它自己的刻度组）。
> `tests/test_artist_families.py::test_colorbar_axes_expose_only_the_colorbar` 看护。

普查把这些归成 `internal`，**不算「Tavotto 漏掉了」**——不这么分类的话，每张带
`extend=` 的图都会凭空多出一条「漏掉了 PathPatch」，普查一旦开始喊狼来了，
真正的缺口就没人看了。

## 4. 生命周期分级

持久保存 artist 引用之前先看这张表。

| 分级 | 对象 | 含义 | Tavotto 的对策 |
| --- | --- | --- | --- |
| `stable` | `Line2D` `Patch` `Collection` `AxesImage` `Axes` `Figure` | 建出来就在那儿 | 直接持引用 |
| `mostly_stable` | `Text`（标题 / 轴标签 / 图内文字 / 图例项） | 一般常驻；图例重建会换掉 texts | 图例重建后 `_reindex_legend_children` 重挂 |
| `conditionally_rebuilt` | 刻度标签、offset text | 每次 draw 由 Locator + Formatter 重新生成 | `TickSet` / `TickLabel` 伪元素 + 每次 `build_manifest` 重新登记；单条文字靠冻结整条轴才留得住 |
| `conditionally_rebuilt` | `Legend` 的内部盒子与 handles | 改 `ncol` / 条目顺序要重建 | 重建后必须 `_legend_box.set_offset(leg._findoffset)`，否则导出时图例整块消失 |
| `ephemeral` | 色条的延伸三角（`extend` 的 PathPatch）、色带网格 | 每次 `_draw_all()` 删掉重建 | **不登记**；色条走代理 |
| `ephemeral` | `contour` 的 `labelTexts` | `clabel()` 生成，改 levels 就没了 | 它们进 `ax.texts`，按普通文字登记（脚本重跑即重建） |
| `proxy_required` | `Colorbar` | artist 树上没有自己的名字 | `ColorbarProxy` + 语义身份 `cbar:<宿主>:<序号>` |
| `proxy_required` | `Container` | 不是 Artist（tuple 子类） | `SeriesGroup` 伪元素 |

**不要持久保存 ephemeral artist 的引用。**

## 5. 身份（identity）

| 层次 | 例子 | 谁在用 |
| --- | --- | --- |
| 结构位置 | `axes_3.lines_2`（`fig.axes` / `ax.lines` 的下标） | 绝大多数元素 |
| 语义身份 | `cbar:axes_0:0`（宿主 + 序号） | 色条（随 manifest 的 `colorbar_key` 下发） |
| 序号身份 | `axes_0.xticklabels_7`（第 7 个主刻度） | 刻度文字——改 xlim 之后第 7 条可能已是另一个数，这是索引身份的固有代价 |
| 别名 | `axes_i.lines_k` → 被 stem 容器消费的 markerline | 只进 `state.index`、不进元素表：界面上不多出条目，历史 override 仍落在同一个 artist 上 |

**结构位置身份的漂移风险**：`axes_i` 随 `fig.axes` 排序，`lines_j` / `patches_j` /
`collections_j` 随各自列表的下标。脚本改动导致列表长度变化时，旧 gid 会指向另一个
artist——这是已知代价，产品侧靠「脚本 sha1 变了就重建会话 + 写回前比对」兜住
（见 CLAUDE.md 的写回事务一节）。**本次把从前没登记的 Collection / Patch 补登记进来
不会挪动任何已有 gid**，因为序号取的一直是所属列表的下标、不是「第几个散点」。

## 6. 我们有意依赖的 matplotlib 私有契约

升级 matplotlib 时这几处最先破。

| 位置 | 依赖 | 为什么公开 API 不够 | 谁看着 |
| --- | --- | --- | --- |
| `overrides._cb_reorient` / `_set_cb_extend` | `cb._inside`、`cb._reset_locator_formatter_scale()`、`cb._draw_all()`、`_ColorbarAxesLocator` 的 `box_aspect` 行为 | 公开 API 只有「销毁重建色条」，那会打乱 `fig.axes` 编号、废掉全部 gid | `tests/test_colorbar_orientation.py` |
| `overrides._legend_rebuild_setter` | `leg._ncols`、`leg._legend_box`、`leg._findoffset` | 改列数没有公开的就地 setter | `tests/test_legend_text.py` |
| `overrides.tick_cfg` / `apply_tick_model` | `axis._mm_tick_cfg`（我们自己挂的）、locator/formatter 的实况读数 | 「用户没表态就保持脚本原样」需要记住原样 | `tests/test_axes_ticks_scale.py` |
| `overrides._AxisArrow3D` | `axis3d._get_coord_info` / `_get_axis_line_edge_points` | 3D 轴线落边每帧现算，没有公开接口 | `tests/test_worker_roundtrip.py` |
| `manifest` / `pathgeom` | `FancyArrowPatch._posA_posB` | 端点没有公开 getter | `tests/test_manifest_geometry.py` |
| `overrides._arrowstyle_name` | `ArrowStyle._style_list` | 反查注册名没有公开接口 | 同上 |
| `preview_complexity._materialised_paths` | `Collection._paths` **是不是 None**（只读状态，不调方法） | 要区分「paths 已经建好」与「按需现建」。后者上调一次 `get_paths()` 就是当场造出 M×N 个 `Path`（`TriMesh` 实测 75 ms / 8 万三角形），而它的 draw 走 `draw_gouraud_triangles`、**根本不经过 paths**——那笔钱连 render 自己都不付。公开 API 里没有「便宜地问一句建没建」的办法 | `tests/test_preview_complexity.py::test_unbuilt_lazy_collection_is_reported_not_priced_as_zero` |

**能力层本身一个私有 API 都没用**：`collection_caps` / `_COLLECTION_CAPS` /
`_PATCH_CAPS` 里全是公开 getter/setter。这是刻意的——family 抽象要能扛升级。

复杂度分析器（`preview_complexity`）同样只用公开 getter，上表那一条是唯一
例外，而且它的**失效方向是选过的**：`_paths` 改名之后 `getattr` 回 None →
整族退成「量不出来」→ 分析器不再推荐 hybrid，兜底回到按字节数的 SVG 硬闸。
少一层保护，不是崩、也不是在热路径上白付一笔钱。

## 7. 桌面（3.11.1）与浏览器（3.10.8）的差异

| 事实 | 3.11.1 | 3.10.8 | 对 Tavotto 的影响 |
| --- | --- | --- | --- |
| `ColorizingArtist` | 有 | 有 | 无（我们用鸭子类型 `get_array()`，两版都不 import 它） |
| `ContourSet is Collection` | 是 | 是 | 无 |
| `FillBetweenPolyCollection` / `PolyQuadMesh` | 有 | 有 | 无 |
| `StemContainer` | 有 | 有 | 无 |
| **`PieContainer`** | **有** | **没有**（`ax.pie` 回 tuple） | **有**：所以 pie 的支持建在 `ax.patches` 的 `Wedge` 上，**不依赖 `PieContainer`** |
| 映射 Collection 的 `set_facecolor` 被 draw 覆盖 | 是 | 是 | 无 |

结论：**核心对象模型两版一致**，唯一的版本相关点（`PieContainer`）已经绕开。

## 8. 已知缺口

| 缺口 | 现状 | 为什么先不做 |
| --- | --- | --- |
| `streamplot` 出 50+ 个独立 `FancyArrowPatch` | 每个都成一条可拖端点的「箭头 N」，元素树被淹没 | `StreamplotSet` 不挂在 `ax` 上、箭头没有任何结构标记，只能靠启发式猜——猜错会伤到正常图。等 CompatBench 的真实数据再定 |
| `boxplot` 出 14 条散装 `Line2D` | 各自可编辑，但没有「这是一组箱线」的语义 | matplotlib 的 `boxplot` 回 dict、不进 `ax.containers`；seaborn 的 `BoxPlotContainer` 是它自己的类。做成容器需要一套跨库的识别规则 |
| `Table` 只有 visible/zorder | 单元格不可编辑 | 单元格模型是另一套 |
| 新 role 的界面显示名 | 前端 `roleName()` 回落到通用说法 | 加 i18n key 要动 `web/src`，就得连带重建 MCP 画布与 playground 两个产物——与本次引擎改动分开走 |
| `norm` 不开放 | 只有 vmin/vmax | 见 3.4 |
