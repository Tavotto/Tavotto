# Matplotlib 源码架构审计 + Artist family 重构（2026-08-21）

> 目的不是「Tavotto 支持 300 个 matplotlib API」，而是
> **Tavotto 理解 matplotlib 的对象模型，于是大量新 API 自然落进已有 family**。
>
> 对象模型与支持矩阵：[`docs/architecture/matplotlib-artist-capability-map.md`](../architecture/matplotlib-artist-capability-map.md)
> 升级 matplotlib：[`docs/ci/matplotlib-upgrade-checklist.md`](../ci/matplotlib-upgrade-checklist.md)
> 给 CompatBench 的建议 case：本文最后一节

## 1. 审计了什么

**基准版本**：桌面内置 runtime 的 **matplotlib 3.11.1**（+ numpy 2.5.2 /
pandas 3.0.5 / seaborn 0.13.2 / scipy 1.18.0，即 `runtime-lock.json` 的完整闭包）。
浏览器 playground 的 **3.10.8** 逐条对照。两个版本都是真装真跑，不是照文档推断。

读过的上游源码：`artist.py`、`figure.py`、`axes/_base.py`、`axes/_axes.py`、
`collections.py`、`patches.py`、`path.py`、`container.py`、`colorizer.py`、`cm.py`、
`colors.py`、`image.py`、`colorbar.py`、`contour.py`、`text.py`、`axis.py`、
`ticker.py`、`legend.py`、`quiver.py`、`streamplot.py`、`table.py`、
`mpl_toolkits/mplot3d/{axes3d,art3d}.py`。

**实跑的 artist 普查**：27 个 matplotlib API + 8 个 seaborn + 5 个 pandas，
每个都记录返回类型、`ax.containers/lines/patches/collections/images/texts/artists/tables`
的实际内容（工具：`scripts/dev/matplotlib_artist_census.py`）。

## 2. 核心发现

### 2.1 `ContourSet` 已经是 `Collection`（3.10 起）

住在 `ax.collections` 里，`cs.collections` 属性没有了。照旧版实现写的代码会
AttributeError。

### 2.2 `PolyCollection` 是个大杂烩

`FillBetweenPolyCollection`（fill_between / stackplot / violinplot / kdeplot）、
`PolyQuadMesh`（pcolor）、`Quiver`、`Barbs` 全是它的子类。**按类名分派会把它们
一律归成「填充区域」**。

### 2.3 ★ 颜色映射的 Collection，`set_facecolor` 会被 draw 原样盖回去

```python
sc = ax.scatter(x, y, c=z)
sc.set_facecolor("#ff0000"); fig.canvas.draw()
sc.get_facecolor()[0]      # → viridis 的颜色，红色没了
```

`Collection.update_scalarmappable()` 在每次 draw 时从 `_A` 重算 facecolors。
3.10.8 与 3.11.1 行为一致。

**这条决定了整个能力层的形状**：能力必须按「这个对象**此刻**真的能改什么」判，
不能按类名。改动前 Tavotto 恰好踩着它——`pcolor()` / `hexbin()` 都被登记成
「填充区域」并暴露 `facecolor`。

### 2.4 上层库不需要专有 handler

seaborn 与 pandas 的 13 个代表 API **没有产出任何 matplotlib 之外的 Artist**。
唯一的新类是 `seaborn.categorical.BoxPlotContainer`（一个 Container），而它的
成员（PathPatch / Line2D）本来就各自可编辑。

> 结论：**不做 `SeabornHandler` / `PandasHandler`**。支持底层 family，上层库自然受益。
> `sns.heatmap` 从「整块看不见」变成可编辑，靠的就是 QuadMesh 进了 Collection family。

### 2.5 生命周期分四档

`stable` / `conditionally_rebuilt`（刻度标签、图例内部盒）/ `ephemeral`（色条延伸
三角、色带网格）/ `proxy_required`（Colorbar、Container）。Tavotto 早先在刻度标签
上踩过这个坑，本次把同类模式系统地找了一遍并写进能力地图第 4 节。

### 2.6 `PieContainer` 只有 3.11 有

3.10.8 的 `ax.pie` 回一个普通 tuple。所以 pie 的支持建在 `ax.patches` 的 `Wedge` 上
——**两个 runtime 通用**。（任务书提醒「不要预设 PieContainer 存在」，实测是「新版
有、旧版没有」，两个都要照顾。）

## 3. 重构前的技术债

1. **`_cls_key` 是一张逐个类名的 `isinstance` 表**。表里没有的类 = 整族看不见。
   `LineCollection` / `QuadMesh` / `ContourSet` / `EventCollection` / `Wedge` /
   `Circle` / `Rectangle`(非柱) / `Table` 都在表外。
2. **同一条 prop 有 5 份逐字相同的实现**。`visible` 出现在 12 个 family key 上、
   `alpha` 8 个、`zorder` 7 个、`facecolor` 7 个、`linewidth` 6 个。
   重复不致命，**分叉**才是——改了一处忘了另一处，没有任何东西会报出来。
3. **能力按类名开放，不按实况**——于是有了 2.3 那种「界面说改了、画面没动」。
4. **登记不上的 artist 静默消失**。既不在元素表里，也不在任何诊断输出里。
   用户只能说「我图里那块东西点不中」。
5. **多数 Collection 的包围盒量不出来**。`get_window_extent` 回无穷大空框，
   `build_manifest` 的 `width<=0 and height<=0` 恰好成立 → 元素被丢掉。
   `pcolor` / `hexbin` 就是这么在「已登记」的情况下仍然不出现在界面上的。
6. **`ax.artists` / `ax.tables` 从来没被遍历过**。

## 4. 重构后

`overrides.py`：

```
_cls_key(artist)                       # 专用契约 → family → 通用兜底
    ticklabel / ticks / bar_series / errorbar / stem_series / colorbar
    figure / text / arrowpatch / line / legend / axes / image / bar
    → Patch      ⇒ "patch"          （含用户子类）
    → Collection ⇒ "collection"     （含用户子类）
    → Artist     ⇒ "artist"         （只开 visible / zorder）

collection_caps(coll) → {base, stroke, fill?, mapped?, sizes?, marker?}
                        ★ 按真实 getter 实况判，不按类名

_COLLECTION_CAPS / _PATCH_CAPS / _GENERIC_CAPS
    ── _install_caps(key, caps)   # setdefault：族里的专用契约永远优先
```

`manifest.py`：

```
instrument()
  ① 语义容器（Bar / Errorbar / Stem）→ 成员进 skip_ids（consumption model）
  ② 逐个用户 artist（lines / images / collections / patches / texts / legend）
  ③ ax.artists / ax.tables 的通用兜底
  ④ census(fig, state) → manifest 的可选 `unsupported` 诊断清单

_collection_fields(coll, label=)   # 字段表由能力探针驱动
_collection_bbox(coll, renderer)   # window_extent 不可用时退 get_tightbbox
_colormap_fields(m)                # Collection 与 AxesImage 共用
```

**没有新建 framework，没有新增引擎模块**（新模块要同步进
`build_browser_playground.py` 的 `ENGINE_MODULES`，而那个文件正被并行的
CompatBench 分支改着）。既有的 `HANDLERS` 注册表原样保留，能力层长在它上面。

## 5. 改了哪些文件

| 文件 | 改动 |
| --- | --- |
| `src/tavotto/engine/overrides.py` | 能力层三张表 + `collection_caps` / `is_color_mapped` / `_install_caps`；`_cls_key` 按 family 分派；stem 容器 handler；`_set_linestyle`（还原路径的 dash 规格）；`FigState.index_ids` / `.unregistered`；删掉 scatter/fill/patch/bar 的 40 行重复条目 |
| `src/tavotto/engine/manifest.py` | StemContainer 登记 + 旧 gid 别名；Collection 全族登记（**色条轴除外**）；Patch 全族登记；`ax.artists`/`ax.tables` 兜底；`census()`；`_collection_fields` 改为能力驱动；`_colormap_fields` 抽出（image 复用）；`_collection_bbox`；patch 加 hatch |
| `tests/test_artist_families.py` | **新增**：族覆盖 / 能力探针 / 自定义子类 / 未知 artist / 全属性还原 / gid 兼容 / 零 patch 不变 |
| `tests/test_equivalence_matrix.py` | 新增场景 s7（EqvFam）+ 两组 patch + 一条写回四路 |
| `scripts/dev/matplotlib_artist_census.py` | **新增**：普查工具 |
| `docs/architecture/matplotlib-artist-capability-map.md` | **新增**：能力地图 |
| `docs/ci/matplotlib-upgrade-checklist.md` | **新增**：升级检查单 |
| `CLAUDE.md` | 渲染引擎一节加「Artist family 能力层」 |
| `codex-plugin/skills/tavotto-figure/references/compatibility.md` | 能改/不能改两张表按新能力更新 |

**没有碰**：`tests/compat/**`、`scripts/ci/compat_matrix.py`、
`docs/ci/matplotlib-compatibility.md`、任何 CompatBench baseline/report、
`web/**`、`scripts/build_browser_playground.py`。

## 6. 新支持 / 改善的 family

普查（27 个 matplotlib + 13 个 seaborn/pandas API，matplotlib 3.11.1）：

```
改动前：21 个用户可见 artist 没有语义模型，分 7 个类
        LineCollection ×7   QuadMesh ×5   Wedge ×3   EventCollection ×2
        QuadContourSet ×2   Table ×1      Rectangle ×1
改动后：0
```

逐条：

| API | 改动前 | 改动后 |
| --- | --- | --- |
| `pcolormesh` / `hist2d` / `sns.heatmap` | 元素表里没有 | `collections_j`：cmap / vmin / vmax / 网格线 / 花纹 |
| `pcolor` | 登记成「填充区域」，但包围盒量不出来 → **界面上不存在**；且 facecolor 是假的 | `fill_j`：cmap / vmin / vmax / 描边；**不再给假的 facecolor** |
| `hexbin` | 同上 | 同上 |
| `contour` / `contourf` | 元素表里没有 | `collections_j`：线色 / 线宽 / 线型 / cmap |
| `eventplot` | 元素表里没有 | `collections_j`：线色 / 线宽 |
| `stem` | markerline 与 baseline 是两条无名曲线，茎完全不见 | `stemseries_j` 一条系列（色 / 宽 / 线型 / marker / 大小），baseline 仍单独可编辑 |
| `violinplot` | 只有 2 块填充 | 填充 + 3 条 LineCollection（中位线 / 极值线） |
| `pie` | 元素表里没有 | 每个扇形 `patches_j`：填充 / 描边 / 花纹 |
| `axhspan` / `axvspan` | 元素表里没有 | `patches_j` |
| `stairs` | 已支持（StepPatch 恰好是 PathPatch） | 多了 hatch |
| `add_patch(Circle/Ellipse/Arc/Wedge/FancyBboxPatch/Annulus/RegularPolygon)` | 元素表里没有 | `patches_j`，全族 |
| `ax.table` | 元素表里没有 | `tables_j`：visible / zorder（识别档） |
| `ax.add_artist(任意 Artist)` | 元素表里没有 | `artists_j`：visible / zorder（识别档） |
| `scatter(c=…)` | facecolor 控件是假的 | 改为 cmap / vmin / vmax |
| 所有 Patch / Collection | — | 新增 `hatch`（黑白印刷区分同色区块） |
| 所有 Collection | — | 新增 `linestyle` |
| `sns.heatmap` / `pd.plot.scatter` | 有缺口 | 缺口清零 |

## 7. 未知 artist 的行为

| 情形 | 行为 |
| --- | --- |
| `class MyPatch(Rectangle)` / `class MyLine(Line2D)` | **自动**落进对应 family，拿到全套族属性。零代码改动——这正是 family 抽象的价值 |
| `class Doodad(Artist)`（完全自定义） | 图照画；进元素表（role `artist`），只开 `visible` / `zorder`；`alpha` **不给**（基类不保证 artist 会读它） |
| 既没登记、也不是结构件 | 进 manifest 的 `unsupported` 诊断清单（类名 + 归属 + 数量） |
| 容器消费掉的成员 | 不算漏——由容器代表 |

`tests/test_artist_families.py::test_unknown_artist_neither_crashes_nor_vanishes`
与 `::test_custom_subclass_inherits_family_support` 看护。

## 8. gid / 历史 override 兼容

**没有 breaking change。**

* `axes_i.scatter_j` / `axes_i.fill_j` / `axes_i.patches_j` / `axes_i.arrows_j`
  的序号取的一直是**所属列表**（`ax.collections` / `ax.patches`）的下标，不是
  「第几个散点」。把从前没登记的补登记进来不挪动任何已有名字。
* 唯一被容器吃掉的旧元素是 `ax.stem()` 的 markerline（从前是 `axes_i.lines_k`）。
  它登记了**旧 gid 别名**：只进 `state.index`、不进元素表 —— 界面上不多出条目，
  历史 override 仍落在同一个 artist 上（`ColorbarProxy` 的语义身份同思路）。
* `_cls_key` 的返回值**不持久化**，只用于 HANDLERS 查表与字段分派。
* manifest 的 `role` 对既有元素一个字符没变；新元素用新 role
  （`collection` / `stem_series` / `artist`）。前端对未知 role 全链路优雅降级
  （`roleName()` 回落到通用说法，`STYLE_ADAPTERS` 里没有就退回后端渲染）。
* manifest 新增的顶层 `unsupported` 是可选键、只在非空时出现；
  `app._compare_manifests` 只比 gid 集合 / bbox / anchor / size_mm，不看它。

## 9. Browser / Desktop 共享引擎

* **没有新增引擎模块**，`build_browser_playground.py` 的 `ENGINE_MODULES` 一行没动。
* **没有新增 import**：`Collection` / `Patch` / `Artist` / `Axis` / `StemContainer`
  都在 matplotlib 里，两个版本都有。颜色映射的探针用**鸭子类型**
  （`get_array()`）而不是 import `ColorizingArtist`——对版本最宽容。
* 没有引入 CPython-only / OS-only / 文件系统依赖。
* `tests/test_browser_session.py` 全绿（本地 worker 解释器正是 **matplotlib 3.10.8**，
  与 playground 同版本，所以这套改动实际是在浏览器那一版上验证的）。
* **待办**：引擎四模块改过之后要重建 `web/dist-playground/` 并到网站仓库
  `pnpm sync-playground`。本次没跑（产物未进本仓库 git，且构建脚本正被并行分支改动）。

## 10. 跑过的测试

```
tests/test_artist_families.py                 30 passed        （新增）
tests/test_equivalence_matrix.py              25 passed / 6 skipped（workerd 未构建）
tests/test_worker_roundtrip.py                passed
tests/test_engine_variants.py                 passed
tests/test_manifest_geometry.py               passed
tests/test_colorbar_orientation.py            passed
tests/test_axes_ticks_scale.py                passed
tests/test_legend_text.py                     passed
tests/test_browser_session.py                 passed
tests/test_write_back.py                      passed
全量 pytest                                    见 §11
```

四路等价性新增场景 **s7（EqvFam）**：fill_between / pcolormesh / contour /
stem / pie 的 Wedge，两组 patch，外加一条**写回原件 → 重开**的完整四路。

**测试当场抓到两个真问题。**

其一是重构自己引入的：把 Collection 整族打开之后，**色条的内部件跟着漏了进来**
——色带 `cb.solids`（QuadMesh）成了 `axes_i.collections_j`，一个可编辑元素。
它每次 `_draw_all()` 都被删掉重建，而且与色条代理重复。规矩定成「**色条轴对外
只有 `axes_i.colorbar` 一个元素**」，`instrument` 的 collections 循环补上与 patches
循环同样的 `is_cbax` 守卫，普查那侧把色条轴的 patches/collections 一并归成结构件
（否则每张带 `extend=` 的图都会凭空多出一条「漏掉了 PathPatch」）。
看护 `test_colorbar_axes_expose_only_the_colorbar`。

其二是本来就有的：`linestyle` 的还原路径。用户发来的是名字（`"--"`），
还原放回来的却是 matplotlib 自己的 dash 规格（Collection 的 `get_linestyle()` 回
`[(0.0, None)]`）。无脑 `str(v)` 把它 stringify 成 `"[(np.float64(0.0), None)]"`，
`set_linestyle` 当场抛 ValueError——**用户按了撤销、线型回不去**。
只测「setter 跑得通」永远抓不到这条，只有「改 → 撤销 → 逐字比原值」才行。

## 11. 剩余风险（分级）

### P0（1.0 前必须确认，本次已全部验证）

* 零 patch 时 figure 不变 —— `test_instrument_does_not_mutate_the_figure` ✅
* 既有 gid 不变 —— §8 ✅
* 旧 override 不会指向另一个 artist —— 别名机制 + `_cls_key` 不持久化 ✅
* 未知 artist 不让 figure 崩 —— `test_unknown_artist_neither_crashes_nor_vanishes` ✅
* 色条内部件不漏进元素表 —— `test_colorbar_axes_expose_only_the_colorbar` ✅
* 还原回得去 —— `test_every_family_prop_restores_exactly`（每个 family 每条属性）✅
* 热态 == 全量重放 == 全新 worker == 写回后重开 —— 等价矩阵 s7 ✅
* Browser 共享引擎不崩 —— `test_browser_session.py` ✅
* 写回不损坏 —— 等价矩阵的 s7 写回腿 ✅

### P1

* **`streamplot` 的元素爆炸**：50+ 个独立 `FancyArrowPatch`，每个都带可拖端点。
  这是**改动前就有**的行为，本次没动。`StreamplotSet` 不挂在 `ax` 上、箭头没有
  任何结构标记 —— 只能靠启发式，猜错会伤到正常图。等 CompatBench 的真实数据。
* **`boxplot` 没有系列语义**：14 条散装 `Line2D`。matplotlib 的 `boxplot` 回 dict、
  不进 `ax.containers`；seaborn 有自己的 `BoxPlotContainer`。做成容器要一套跨库规则。
* **新 role 的界面显示名**回落到通用说法。加 i18n key 要动 `web/src`，
  就得连带重建 MCP 画布与 playground 两个产物——与引擎改动分开走。
* **`get_tightbbox` 退路对稀疏 Collection 偏大**（LineCollection 实测取到整块子图），
  命中区域比图形本身宽。取舍是「点得中」优于「不存在」，但值得后续按真实路径收紧。

### P2

* **`ContourSet` 是一个元素还是一组**：现在是一个（跟着上游 3.10 的模型走）。
  用户想单独改某一条等值线时做不到 —— 需要 `levels` 级的伪元素，属于新设计。
* **`Table` 只有识别档**，单元格模型是另一套。
* **色条 / 图例 / 刻度 / 3D 的私有 API 依赖**（能力地图第 6 节）是升级时最先破的地方；
  能力层本身**一个私有 API 都没用**。
* **`PieContainer`（3.11+）** 将来可以给 pie 一层系列语义（统一改所有扇形），
  但要等浏览器 runtime 也到 3.11。

## 12. 与并行 CompatBench 分支的交叠

本次工作在独立 worktree `feat/matplotlib-source-audit` 上做，
**未触碰** `tests/compat/**`、`scripts/ci/compat_*`、
`docs/ci/matplotlib-compatibility.md`、任何 baseline，以及 `web/**`。

但两边**都动了共享引擎的两个文件**，交叠必须如实记账：

### 撞在一起的（5 处，语义完全相同）

两边独立地做了**同一个** Patch family 泛化——`isinstance(pt, Patch)` /
`isinstance(artist, Patch)`，连理由都一样（Wedge / axhspan 的 Rectangle /
Circle 从前在界面上不存在）。冲突是**文本冲突不是语义冲突**，只是注释措辞不同：

| 文件 | 位置 |
| --- | --- |
| `manifest.py` | `from matplotlib.patches import …` |
| `manifest.py` | `ax.patches` 循环的注释块 |
| `manifest.py` | `elif isinstance(pt, Patch)` |
| `overrides.py` | `from matplotlib.patches import …` |
| `overrides.py` | `_cls_key` 的 Patch 分支 |

合并办法：留任一侧的 `isinstance(..., Patch)`，import 行取并集
（本分支还额外要 `Collection` / `Artist` / `Axis` / `StemContainer`）。

> 两条独立路径撞出同一个结论，本身就是这个方向对了的旁证——一边是从源码
> 继承关系推的，一边是被真实脚本的失败 case 逼出来的。

### 只在 CompatBench 侧的（本分支没有，合并时要保留）

`overrides._set_legend_fontsize`：`("legend", "fontsize")` 的 getter 回**逐条**
列表，setter 只吃标量，于是改过图例字号之后**撤销回不去**
（`float() argument must be a string or a real number, not 'list'`）。

**这与本分支修的 `linestyle` 是同一类 bug**：getter 回的形状 ≠ setter 吃的形状，
而 restore 走的正是 `setter(artist, originals[key])`，所以只在撤销那一刻才炸。

合并之后有一个一行的收口：把 `"fontsize"` 加进
`tests/test_artist_families.py` 的 `_FAMILY_PROPS`，
`test_every_family_prop_restores_exactly` 就会覆盖它。**已经实测过**——在本分支
（没有那个修复）上加这一个词，用例当场复现出一字不差的同一条错误。
一个一次性的 CompatBench 发现于是变成一条常驻的通用防线。

## 13. 给 CompatBench 的建议 case

按「最能暴露真实兼容缺口」排序。**本次没有改动任何 CompatBench 文件与 baseline
——数字变化应当由它自己重跑来证明。**

**高价值（本次新支持，需要基线数字）**

1. `pcolormesh` + colorbar（cmap / clim / 加网格线）—— 科研图第一大类
2. `sns.heatmap`（QuadMesh 走 seaborn 那条路）
3. `contour` + `contourf` + `clabel`（线宽 / 线色 / cmap；labelTexts 的生命周期）
4. `stem`（容器语义 + baseline 单独可编辑 + 旧 gid 别名仍能命中）
5. `pie`（Wedge 的填充 / 花纹；3.10 与 3.11 的返回类型不同）
6. `violinplot`（FillBetween + LineCollection 混合）
7. `hexbin`（映射的 PolyCollection —— 确认 facecolor **不**出现在 manifest 里）
8. `axhspan` / `axvspan` + `add_patch(Circle/Ellipse)`

**回归防线（这几条一旦红，说明能力探针的判据被上游改了）**

9. `scatter(c=z)`：manifest 里**不许**有 facecolor，必须有 cmap/vmin/vmax
10. 每个 family 每条属性的「改 → 撤销 → 逐字回原值」（可直接照
    `tests/test_artist_families.py::test_every_family_prop_restores_exactly` 的形状）
11. 自定义子类：`class MyPatch(Rectangle)` / `class MyLine(Line2D)` 必须自动落族
12. 完全自定义 `Artist`：不崩、进元素表、只有 visible/zorder

**已知缺口（预期红或降级，别当成回归）**

13. `streamplot`：元素爆炸（P1）
14. `boxplot`：无系列语义（P1）
15. `ax.table`：只有识别档

**版本矩阵**

16. 同一份脚本在 3.10.8（浏览器）与 3.11.1（桌面）上的 manifest 元素集合对比
    ——`PieContainer` 是已知的唯一版本相关点，出现别的差异就是新的对象模型变更

## 14. 合并 CompatBench（#49）之后：LineCollection 归属的裁决

两条分支独立做了同一个 Patch family 泛化（文本冲突、零语义冲突，按第 12 节
预期）。真正需要裁决的只有一处：CompatBench 侧新开了**线组**这一族
（`linecoll`），而本分支的通用 Collection 族在类型上把它整个包住了。

**裁决：`linecoll` 保留为独立的一族，排在通用 `collection` 之前。**

不是因为 family 抽象在这里失效，而是因为**对外的名字已经发出去了**：线组那族
的 prop 叫 `color`（Line2D 的口径），Collection 族叫 `facecolor`/`edgecolor`；
gid 是 `axes_i.linecoll_j`，不是 `collections_j`。合并两族等于把存量文档里的
prop 名与 gid 一起换掉，而 override 是按 `{gid, prop}` 存的。族抽象省的是
**实现**里的重复，不是改掉已经承诺过的接口的理由。

三条随之而来的收口：

1. **标量映射的 LineCollection 改走通用分支**（CompatBench 侧是「一律不登记」）。
   实测（mpl 3.10.8）纠正了当时的判断：`Collection._set_mappable_flags` 只在
   `_original_edgecolor is None` 时才把边设成映射的，用户一旦显式
   `set_edgecolor(...)`，映射当场关掉、颜色**留得住**——所以「设了下一帧被顶
   回去」在描边这条路上并不成立（填充那条成立，`collection_caps` 本来就挡着）。
   但 `color` 那个**单值**口径表达不了逐条映射出来的颜色，所以它进的是按
   `collection_caps()` 实况说话的通用分支，而不是线组族。
   等值线仍然两条判据都不沾（`QuadContourSet` 不是 LineCollection 子类），
   `test_contour_is_still_not_registered_as_line_collections` 与本分支的
   `axes_0.collections_4` 用例同时成立。
   `eventplot` 的 EventCollection 是 LineCollection 子类，因此归线组族。

2. **未缩放 dash 的 getter 推广到整个 Collection 族**。CompatBench 侧的
   `_get_linecoll_ls` 修的是「`get_linestyle()` 回缩放过的 dash、
   `set_linestyle()` 再缩放一遍，每撤销一次疏一档」——这是 `Collection` 基类的
   毛病，不是线组独有的。本分支的 `_COLLECTION_CAPS["linestyle"]` 原本正是那条
   有问题的写法，合并时换成了同一对 getter/setter。

3. **`_FAMILY_PROPS` 加上 `"fontsize"`**（第 12 节说的那个一行收口），
   四路等价性矩阵同时保留两边新加的场景：s7 `EqvFam`（本分支）与 s8
   `EqvAlias`（CompatBench）。

4. **「标量映射的网格整个不登记」这条被推翻了**（CompatBench 侧新加的
   `test_scalar_mapped_meshes_stay_out_of_the_manifest`）。取舍没变——
   `facecolor` 仍然一个字都不给——但判据从「元素表的黑名单」换成了
   `collection_caps()` 的能力探针：`pcolor` / `pcolormesh` / `hexbin` 进元素
   表，只开 cmap / vmin / vmax 与描边。旧写法的代价是用户连改色图、改 clim、
   加网格线都做不到，而**那三件事是真的生效的**。用例改名为
   `test_scalar_mapped_meshes_never_advertise_facecolor` 并守住新判据。

## 15. Codex 在 PR 上报的 P2：别名 gid 与系列的重叠（已修）

自动审查提的那条**实测复现得到**，而且比它描述的更宽：容器消费掉的成员只在
`state.index` 里留了一条旧 gid 别名，`apply` 的别名反查表只扫元素表，于是
「别名」与「系列」这两个指着同一个 artist 的 gid **不算同一组**。

文档里同时留着历史的 `axes_0.lines_0.color`（markerline 容器化之前的名字）与
`axes_0.stemseries_0.color` 时，只撤掉前者：还原把 markerline 写回脚本原样，
系列那条「值没变」于是走了跳过的捷径——**茎是新颜色、marker 退回原色**，
而全量重放两者都是新颜色。热态 ≠ 重放，写回自检 `_compare_manifests` 只比几何、
看不见颜色，坏状态会直接写进用户的原件。

修法不新造机制，走既有的那套：

* `apply` 的反查表 `_reverse_index()` 补上 **index-only 的别名 gid**
  （元素表里已有的优先，别名只作补充）；
* `ALIAS_GROUPS[("stem_series", …)]` 登记 `color` / `alpha` / `visible` /
  `zorder` / `marker` / `markersize`——**`linewidth` / `linestyle` 不登记**，
  stem 系列的这两条只写茎（`_stem_stems`），碰不到 markerline，没有重叠就不该
  硬编成一组。

顺带把顺序也定死了：`_rank` 让组内窄 prop 排在广播 prop 之后，所以「别名 +
系列」这组 patch 无论列表序怎么写都落成同一张图（实测两种顺序逐位相同），
全撤之后逐位回到脚本原样。看护
`tests/test_artist_families.py::test_removing_a_legacy_alias_override_replays_the_series`
——去掉修复它当场红。

## 16. 第二轮自动审查的两条 P2（都实测复现，都已修）

### 16.1 登记与 dispatch 用了两条判据

§14 的裁决把标量映射的 LineCollection 放进通用 `collection` 分支，但
`_cls_key` 仍然无条件对**任何** LineCollection 回 `linecoll`——两条判据分开写，
当场就漂开了：元素表说它是通用 collection（gid `collections_j`），检查器却按
线组给了 `color`，而 `HANDLERS[("linecoll", …)]` 根本不在这个元素上。
那个控件在界面上看得见、一个像素都改不动，而且不报任何错。

判据收成**一个函数** `overrides.is_linecoll_family(artist)`，`manifest.instrument`
与 `_cls_key` 都问它。看护
`test_mapped_line_collections_leave_the_linecoll_family`。

### 16.2 色条与它的 mappable 是同一份状态、两个 gid

`("colorbar", "cmap"/"vmin"/"vmax")` 写的是 `cb.mappable`，而那个 mappable
自己也是元素表里的一条。**这条重叠不是本次新开的**——`("image","cmap")` 与
`("colorbar","cmap")` 一直落在同一个 AxesImage 上；Collection 族开放
cmap/vmin/vmax 只是把它扩到了 pcolormesh / contour / scatter(c=z)。既然
§15 已经把机制建好，一起收了。

实测（`imshow` + colorbar）两个症状：

* 两条都设过、只撤掉 mappable 那条 → 热态 viridis、全量重放 magma；
* 两条**全撤** → 停在**中间态**（实测 plasma，回不到 viridis）。用户按了撤销、
  图还是花的，而且再也回不去。第二条正是「广播端动手之前先采下组员的脚本
  原样」那段逻辑存在的理由。

`ALIAS_GROUPS[("colorbar", cmap/vmin/vmax)] = _alias_colorbar_mappable(...)`。
两条都设过时**图元自己那条说了算**（`_rank` 的组内次序：色条是 mappable 的
一个视图，不是反过来）。看护 `test_colorbar_and_its_mappable_are_one_alias_group`
——去掉修复两条断言各自当场红。

### 这三条 P2 是同一个形状

§15 与本节的两条都是「**两个 gid 指着同一份状态**」，而 `apply` 的
「值没变就跳过」捷径对它们是错的。别名组 + `dirty_groups` 就是为这件事建的；
以后再开放任何一条「同一份状态的第二个入口」，先问一句它该不该进
`ALIAS_GROUPS`。

## 17. 第三轮自动审查的三条 P2（都实测复现，都已修）

### 17.1 茎的线型也是「缩放过的 dash 回灌」那个坑

`("stem_series", "linestyle")` 的 getter 是 `get_linestyle()`，而茎是
LineCollection——`set_linestyle()` 会把喂进去的值再缩一遍。实测
`ax.stem(..., linefmt="--")` 在默认 lw=1.5 下 **5.55 → 8.325 → 12.49**，
每撤销一次 ×1.5。§14 已经为整个 Collection 族修过同一个坑
（`_get_linecoll_ls`），茎是它的**第二个入口**，当时漏了。

顺带揪出一个**显示层**的谎：`_stem_fields` 用的是 Line2D 那条
`_linestyle_name`，它只认字符串线型，喂给 Collection 时任何 dash 都回实线
占位——`linefmt="--"` 画出来是虚线、检查器却说实线。改用
`_linecoll_linestyle_name`。这也是为什么第一版看护用例是**瞎的**：显示值
前后都是 `"-"`，怎么撤都「相等」。

### 17.2 花纹画在面上，而「有没有面」与「面归不归用户改」是两件事

`_collection_fields` 无条件给 `hatch`。`fill` 那道闸问的是「facecolor 归不归
用户改」，花纹问的是另一件事。实测 `get_facecolor()` 的长度：
pcolormesh 36 / contourf 7 / fill_between 1 / scatter 1 —— 有面；
**contour 0 / LineCollection 0** —— 连面都没有。给后者花纹就是一个设得进
状态、画面上一个像素都不变的开关，正是这套能力探针存在的理由。

`collection_caps` 因此多一条 `faces`（`get_facecolor()` 非空），
`fill` = `faces` 且没在映射，`hatch` 跟 `faces` 走。于是映射的 QuadMesh
**给花纹、不给 facecolor**——两件事分开之后这个组合才表达得出来。

### 17.3 登记了却量不出几何的元素，两头都不出现

`census` 判「已知」用的是**登记表**。一个只实现 `draw()`、没重写
`get_window_extent()` 的自定义 Artist（基类回空框）于是：登记 → 普查认为
它已知 → `build_manifest` 量不出框把它 `continue` 掉。它在 `elements` 与
`unsupported` **两头都不出现**——普查存在的理由被绕过了。§35 的底线是
「不许静默消失」，而这正是一次静默消失。

`build_manifest` 现在记一本丢弃账本并入 `unsupported`（带 `reason`）。
**刻度那种正常的来去不报**（`ticks`/`ticklabel` 与空文字）：换 locator、改
xlim、翻色条方向都会让整组刻度重来，把它们报进去等于让诊断喊狼来了，
而喊狼来了之后真缺口就没人看了——与 `census` 自己那条纪律同源。

看护：`test_stem_linestyle_undo_does_not_widen_the_dashes` /
`test_hatch_is_offered_only_where_there_are_faces` /
`test_registered_artists_without_geometry_are_reported_not_dropped`，
去掉各自的修复都当场红。
