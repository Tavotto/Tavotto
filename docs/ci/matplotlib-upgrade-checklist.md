# 升级 matplotlib 之前要过的闸

**触发条件**：改动 `packaging/runtime-lock.json` 或
`packaging/playground-runtime.json` 里的 matplotlib 版本。

Tavotto 是把 figure 常驻内存、直接 mutate artist 再重画的编辑器，所以它对
matplotlib 的对象模型有**结构性依赖**——比普通调用方深得多。matplotlib 的
小版本从来不承诺内部结构不变，而这些变化几乎不以异常的形式出现，只表现为
「某类元素点不中了」「改了没反应」「撤销回不去」。

对象模型的完整版在
[`docs/architecture/matplotlib-artist-capability-map.md`](../architecture/matplotlib-artist-capability-map.md)。

## 0. 先跑普查，读 diff

```bash
# 用**新**版本的解释器
python scripts/dev/matplotlib_artist_census.py --api --with-seaborn
# 与旧版本对照（把旧的 manifest.py/overrides.py 拿出来指过去）
TAVOTTO_CENSUS_ENGINE=/path/to/old/engine python scripts/dev/matplotlib_artist_census.py --api
```

「漏掉的类」一栏从空变成非空 = 上游换了某个 API 的产物类型。**这一条比下面
所有单项都灵**，因为它是按真实运行结果说话的。

## 1. 继承关系有没有变

```python
from matplotlib.collections import Collection, PolyCollection, PathCollection
from matplotlib.contour import ContourSet
from matplotlib.patches import Patch
assert issubclass(ContourSet, Collection)          # 3.10 起成立；再变就得改登记
assert issubclass(Quiver, PolyCollection)
```

Tavotto 的 family 分派全靠 `isinstance`。某个类**搬出** family（例如
`ContourSet` 哪天不再是 `Collection`）会让它整族的能力一起消失，而且不报错。

## 2. Collection 的能力探针还准不准

`overrides.collection_caps()` 的四条判据全部基于运行时实况，逐条复验：

| 判据 | 复验 |
| --- | --- |
| `get_array() is not None` ⇒ 颜色映射 | 映射中的 collection `set_facecolor` 后 draw，颜色必须**变回去**（这条一旦不再成立，说明上游改了 `update_scalarmappable`，Tavotto 可以放开 facecolor） |
| `len(get_facecolor())` ⇒ 有填充 | `LineCollection` 必须仍是 0 |
| `get_sizes()` ⇒ 有标记大小 | `PolyCollection` / `Quiver` 必须仍是空 |
| `PathCollection` ⇒ 可换 marker | `set_paths` 对散点仍是换 marker |

`tests/test_artist_families.py::test_color_mapped_collections_do_not_advertise_facecolor`
就是第一条的看护。

## 3. Container 家族

```python
import matplotlib.container as c
[n for n in dir(c) if not n.startswith("_")]
```

* 新增的 Container（`PieContainer` 就是 3.11 才有的）要评估是否值得做成语义容器；
* **不要**让实现依赖只在新版存在的 Container——浏览器 playground 落后一到两个小版本。
  pie 的支持建在 `ax.patches` 的 `Wedge` 上，正是为了这个。

## 4. 生命周期（最容易静默破的一档）

| 检查 | 破了的症状 |
| --- | --- |
| 刻度标签仍由 Locator + Formatter 每次 draw 重建 | 单条刻度文字改完自己变回去 |
| `Legend` 重建后仍需 `_legend_box.set_offset(leg._findoffset)` | 导出的 PDF 里图例整块消失 |
| 色条 `_inside` / `_reset_locator_formatter_scale` / `_draw_all` 还在 | 翻转方向 / 开 extend 当场抛异常 |
| `_ColorbarAxesLocator` 仍在 `extend=="neither"` 时不收回 `box_aspect` | 「开了又关」的色条比从没开过的宽 10% |
| `axis3d._get_coord_info` / `_get_axis_line_edge_points` 还在 | 3D 轴箭头落错边 |

## 5. 私有 API 清单

能力地图第 6 节列了全部有意依赖的私有契约与各自的看护用例。逐条跑那些用例。

## 6. 必跑的测试

```bash
.venv/bin/python -m pytest tests/test_artist_families.py \
    tests/test_worker_roundtrip.py tests/test_engine_variants.py \
    tests/test_manifest_geometry.py tests/test_colorbar_orientation.py \
    tests/test_axes_ticks_scale.py tests/test_legend_text.py \
    tests/test_equivalence_matrix.py tests/test_write_back.py \
    tests/test_browser_session.py
```

`test_equivalence_matrix.py` 是最终验收物：**热态 == 清空重放 == 全新 worker ==
写回后重开**。matplotlib 换版本后这四路仍要收敛到同一个几何。

## 7. 两个 runtime 都要过

* 桌面：`packaging/runtime-lock.json`，三个 target 的闭包**刻意逐字相同**
  （`test_all_targets_pin_the_same_versions` 看护）；
* 浏览器：`packaging/playground-runtime.json` + Pyodide 的可用版本——
  Pyodide 只带它自己编好的那几个版本，**不是想钉哪个就钉哪个**。

两边版本必然会有落差。落差本身没问题，**落差跨过一次对象模型变更才有问题**——
所以升级任一侧之后，能力地图第 7 节那张差异表都要重新核一遍。

## 8. 产物重建

引擎四模块（`manifest.py` / `overrides.py` / `pathgeom.py` / `patchspec.py`）
任一改动之后：

```bash
python scripts/build_browser_playground.py     # web/dist-playground/
python scripts/build_mcp_widget.py             # 产物不进 git，动过 web/src 就重建
```

然后到网站仓库 `pnpm sync-playground`。
