"""预览复杂度分析器：**在那 66 万个 `<path>` 生出来之前**就问出该怎么画。

## 这个模块回答一个问题

> 光看 Matplotlib 的 artist 图（不 savefig、不碰数据），这张图的编辑预览
> 要不要走 hybrid？如果要，**哪几个 artist** 该在预览里临时 rasterize？

Session 01 那道闸量的是 `stat().st_size`——它只能在 `savefig(svg)` **跑完
之后**回答。issue #181 的实测里那一步是 **11 789 ms**（热态的 98%），而用户
自己的 `pcolormesh` 只花了 74.6 ms。安全闸让那份产物不进内存与 DOM，**没有
让它不产生**。这里是把判定挪到那一步之前的那一半（ADR 0022 §5 的 Session 02）。

## 边界：这里只算账，一个字节都不改

**不 savefig、不改 artist、不建大数组、不读 SVG。** 分析器进 render 热路径，
它必须比它要省下的那件事便宜好几个数量级——否则每张普通图都替 #181 那张
买单。开销实测记在 `docs/perf-baseline.md` 的「复杂度分析器开销」一节。

尤其是**不设 `set_rasterized`**：产出的 `PreviewPlan.rasterized_artists` 只是
一份名单，真正在 `savefig` 前后设/还原它是 Session 03 的事。分析器改了 artist
就等于把「预览的表示法」写进了常驻 Figure，而常驻 Figure 是导出读的那一份
——ADR 0022 不变量 2 就是这么破的。

## 判据按 **artist family**，不按 pyplot API

```text
matplotlib API  →  artist 图  →  artist family  →  Tavotto policy
```

`pcolormesh` / `hist2d` / `sns.heatmap` 产出的都是 `QuadMesh`；`scatter` 与
`hexbin` 都是「一个 marker 定义 + 一堆实例」。判据问「有多少 primitive」，
不问「你是谁」（ADR 0022 §6）——所以用户自己继承出来的 `class MyMesh(QuadMesh)`
不用我们改一行代码就落进 mesh 族。family 的继承关系见
`docs/architecture/matplotlib-artist-capability-map.md` §1。

## 成本模型来自 SVG 后端自己的那两行，不是「数据点数 == SVG path 数」

`RendererBase._iter_collection` 里画的次数是 `N = max(len(paths), len(offsets))`
——**这是与 family 无关的那一条**，也是 DOM 节点数的来源。而 `<path>` 内联
还是 `<defs>` + `<use>`，由 `RendererSVG.draw_path_collection` 开头那个取舍式
决定（`_shares_geometry` 抄的就是它）。两者合起来解释了为什么

* `scatter(x, y, s=6)` 十二万个点 → **1** 个 marker path 进 defs + 12 万个
  `<use>`；而同一个 `PathCollection` 换成 `s=<数组>` 就掉出 `Collection.draw`
  的单形状快路，**12 万个 marker 各自内联**（500 点的对拍上顶点数 26 → 13 000）。
  同一个 API、同一个类，成本差三个数量级——这就是为什么定价要问 artist 的实况；
* `pcolormesh` 22 万个 cell → **22 万个各带几何的 `<path>`**（`uses_per_path`
  恒为 1，取舍式恒不成立）。

把它们都写成「数据点数 == SVG path 数」会同时高估前者的字节数、低估后者的
危险性。所以本模块出**两个**维度，判据也分两条：

| 维度 | 是什么 | 谁在保护 |
|---|---|---|
| `primitive_count` | 这个 artist 会摊成多少个 DOM 节点 | 浏览器 DOM（#181 的 663 533 个节点） |
| `vertex_count` | 会被序列化进 SVG 文本的坐标对数 | 字节数与解析耗时（contour 只有 41 个节点、23 万个顶点） |

`primitive_count` **不依赖**那个取舍式（内联的 `<path>` 与共享的 `<use>` 都
是一个节点），所以它是判据里更硬的那一半；`vertex_count` 是估算，取舍式将来
在 matplotlib 里改了它会失准，而失准的后果是估值偏差，不是漏保护。

## 认不出来的怎么办

ADR 0022 不变量 5 说「不认识时按贵的算」，Session 02 的 prompt 说「不要在
analyzer 里随意把 unknown 判成 raster」。两句话不矛盾，因为它们说的是两件事：

* **不假装它便宜**：认不出来的 artist 进 `PreviewPlan.unknown`，诊断看得见，
  绝不静默计成 0 就当图很小（那才是「按普通的算」）。
* **也不替它做 hybrid**：hybrid 的动作是「对这个 artist 设 rasterized」，
  而我们不知道一个不认识的 artist 被 rasterize 之后长什么样。名单里不敢放
  的东西，兜底的是 Session 01 那道按字节数的硬闸——**它不需要认识任何人**。

## 已知盲区（都是**有意**留的，且都有兜底）

兜底一律是 Session 01 那道按字节数的硬闸——**它不需要认识任何人**，所以每一条
盲区最坏的后果是「本该 hybrid 的图走了 raster」，不是崩。

| 盲区 | 后果 | 为什么先不做 |
|---|---|---|
| `ax.patches` / `ax.texts` 不计入 | 一个顶点数极多的 `Polygon` 不出现在账上 | 一个 patch 是一个 DOM 节点，撑不爆 DOM；而遍历它们要付 `get_path()` 的重算 |
| `Line2D` 计入但**不可 rasterize** | 一条百万点的曲线留在矢量层 | 那是策略：hybrid 承诺普通曲线保持矢量（#181 fixture 第四格） |
| `_single_shape_blit` 没算「marker 包围盒 < 整张图」那半句 | marker 大到跟整张图一样时**低估** | 算它要先有 `_prepare_points()` 的变换，热路径上不该付；这是退化情形 |
| gouraud `TriMesh` 按 path 数定价 | 低估——SVG 后端给每个三角形写渐变，比一条普通 path 贵 | `draw_gouraud_triangles` 的 SVG 成本还没实测，没有数据不写进模型 |
| `Collection._paths` 是私有名 | 改名后整族退成「量不出来」，不再推荐 hybrid | 失效方向是少一层保护，不是崩；见 `_materialised_paths` |
| contour 顶点估值偏低约 9% | 估值位数，不影响裁决 | 见 `_vertices_in_paths` 的实测表 |

纯标准库之外只依赖 matplotlib；与 `worker.py` 同一条 sys.path 纪律，平铺 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from matplotlib.collections import Collection, LineCollection, PathCollection, PolyCollection
from matplotlib.contour import ContourSet
from matplotlib.image import AxesImage
from matplotlib.lines import Line2D

import previewbudget
from manifest import _ordered_axes

__all__ = [
    "FAMILIES",
    "FAMILY_COLLECTION",
    "FAMILY_CONTOUR",
    "FAMILY_IMAGE",
    "FAMILY_LINE",
    "FAMILY_LINECOLL",
    "FAMILY_MESH",
    "FAMILY_POLY",
    "FAMILY_RASTERIZED",
    "FAMILY_SCATTER",
    "FAMILY_UNKNOWN",
    "FAMILY_UNMEASURED",
    "ArtistPreviewCost",
    "PreviewPlan",
    "analyze_preview_complexity",
    "plan_for_state",
]

# --------------------------------- family ---------------------------------
#: `QuadMesh`（pcolormesh / hist2d / heatmap）与 `PolyQuadMesh`（pcolor）：
#: 由 (M+1, N+1, 2) 的角点网格定义，**每个 cell 各带几何**。
FAMILY_MESH = "mesh"
#: `PathCollection`（scatter）：一个 marker 定义 + 一堆偏移实例。
FAMILY_SCATTER = "scatter"
#: `ContourSet` / `QuadContourSet`：3.10 起它自己就是一个 Collection，
#: 一层等值线一个 path——节点极少、顶点极多，两个维度在这里分得最开。
FAMILY_CONTOUR = "contour"
#: `PolyCollection` 及其子类（hexbin / fill_between / stackplot / violin /
#: quiver / barbs）。`FillBetweenPolyCollection` 与 `Quiver` 都在这一族里
#: ——按类名分派会把它们各算一次，按 family 就是同一条账。
FAMILY_POLY = "poly"
#: `LineCollection` 及 `EventCollection`（stem / errorbar / streamplot / eventplot）。
FAMILY_LINECOLL = "linecoll"
#: 其它 `Collection` 子类：族认得出、专用捷径没有，走通用 path/offset 模型。
FAMILY_COLLECTION = "collection"
#: `Line2D`：一条曲线在 SVG 里是**一个** `<path>`，不进 rasterize 名单
#: （hybrid 的契约是普通曲线保持矢量）。
FAMILY_LINE = "line"
#: `AxesImage`（imshow）：本来就是一张嵌入位图，一个 `<image>` 节点。
FAMILY_IMAGE = "image"
#: 认得出是 Collection、却便宜地量不出来（path 还没建、也没有角点网格）。
#: `TriMesh` 是现成的例子：`get_paths()` 会当场建出 8 万个 Path（实测 75 ms），
#: 而它的 draw 走 `draw_gouraud_triangles`，**根本不经过 paths**——替它建一次
#: 就是给 render 热路径凭空加一笔它本来不必付的钱。
FAMILY_UNMEASURED = "collection_unmeasured"
#: 完全不认识：第三方库的、matplotlib 新版本的、用户自己继承出来的。
FAMILY_UNKNOWN = "unknown"
#: 这一版**已经是位图**（`rasterized=True`）：在 SVG 里是一个 `<image>`。
#: 色条色带天生如此；Session 03 给 mesh 层设上之后它们也会落到这一族。
FAMILY_RASTERIZED = "rasterized"

FAMILIES = (
    FAMILY_MESH,
    FAMILY_SCATTER,
    FAMILY_CONTOUR,
    FAMILY_POLY,
    FAMILY_LINECOLL,
    FAMILY_COLLECTION,
    FAMILY_LINE,
    FAMILY_IMAGE,
    FAMILY_UNMEASURED,
    FAMILY_UNKNOWN,
    FAMILY_RASTERIZED,
)

#: hybrid 允许把哪些 family 临时 rasterize。**这是策略，不是能力**：
#: `set_rasterized` 在任何 Artist 上都设得进去，但 hybrid 的契约是
#: 「大型 mesh / collection 数据层临时 rasterize，文字、坐标轴、图例、标注、
#: 普通曲线保持 vector」（ADR 0022 §2 / #181 fixture 的第四格）。所以
#: `line` 不在这里；`image` 不在这里是另一个理由——它本来就是位图，
#: rasterize 它一分钱都省不下来。
RASTERIZABLE_FAMILIES = frozenset(
    {FAMILY_MESH, FAMILY_SCATTER, FAMILY_CONTOUR, FAMILY_POLY, FAMILY_LINECOLL, FAMILY_COLLECTION}
)

#: 逐条数顶点最多数这么多个 path，再往上按均值等比放大。
#:
#: 为什么可以这样：`primitive_count` 才是判据里更硬的那一半，而顶点数在超过
#: 这个量级之后**只影响估值的位数、不影响裁决**（4096 个 path 的图早就在
#: 别的判据上出局了）。为什么必须这样：60 000 条线段逐条 `len(p.vertices)`
#: 实测 1.9 ms，22 万个就是 7 ms——那是热路径上白付的钱。
#:
#: 抽样是**确定性**的（永远取前 N 个，不随机），所以同一张图两次分析出同一个数。
_VERTEX_SAMPLE_PATHS = 4096


@dataclass
class ArtistPreviewCost:
    """一个 artist 在**矢量预览**里要付的账。

    `artist` 是 worker 进程内的对象引用，**不发给前端**——协议里出去的只有
    `PreviewMetadata` 那几个数（ADR 0022 §2）。
    """

    artist: object
    family: str
    #: 会摊成多少个 DOM 节点（内联 `<path>` 与共享的 `<use>` 都算一个）。
    primitive_count: int
    #: 会被序列化进 SVG 文本的坐标对数。
    vertex_count: int
    #: 顶点数是逐条数出来的（True）还是按前 `_VERTEX_SAMPLE_PATHS` 个等比放大的。
    vertex_count_exact: bool = True
    #: hybrid **允许**对它 rasterize 吗（见 `RASTERIZABLE_FAMILIES`：这是策略）。
    rasterizable: bool = False
    #: 这一版预览里该不该真的 rasterize 它。`analyze_preview_complexity` 填。
    should_rasterize: bool = False


@dataclass
class PreviewPlan:
    """一次分析的结论。**`reason` 直接是协议里那个枚举**，没有第二套词表。"""

    mode: str
    reason: str
    #: 纯矢量画法下整张图的开销（= 所有认得出的 artist 之和）。
    estimated_primitives: int
    estimated_vertices: int
    #: 按这份 plan 画完之后**还留在矢量层**的开销。hybrid 下它才是浏览器要吃的量。
    vector_primitives: int
    vector_vertices: int
    #: 要在预览里临时 rasterize 的 artist（worker 内部引用，不发前端）。
    rasterized_artists: list = field(default_factory=list)
    #: 全部逐条账目，含没被选中的。诊断（Session 05）与用例读它。
    costs: list = field(default_factory=list)
    #: 一句人话，进 warnings / 诊断包，不进协议枚举。
    detail: str = ""

    @property
    def rasterized_artist_count(self) -> int:
        return len(self.rasterized_artists)

    def costs_by_family(self, family: str) -> list:
        return [c for c in self.costs if c.family == family]

    @property
    def unknown(self) -> list:
        """认不出来、或认得出却便宜地量不出来的那些。**不静默当成 0**。"""
        return [c for c in self.costs if c.family in (FAMILY_UNKNOWN, FAMILY_UNMEASURED)]


# ------------------------------ 便宜的读数 ---------------------------------
def _len0(x) -> int:
    """长度，量不出来就当 0。`get_offsets()` 之流回的可能是 masked array。"""
    try:
        return len(x)
    except Exception:  # noqa: BLE001
        return 0


def _already_rasterized(artist) -> bool:
    """这个 artist 这一版**本来就是位图**了吗。

    `rasterized=True` 的 artist 在 SVG 里是**一个 `<image>`**，不是 N 个
    `<path>`（实测：24×24 的 `pcolormesh` 从 576 个 `<path>` / 2880 个顶点
    变成 1 个 `<image>` / 0 个顶点）。按它的 family 定价就是给 DOM 记一笔
    根本不存在的账。

    这不是边角情形：**色条的色带（`cb.solids`）就是 matplotlib 自己设成
    `rasterized=True` 的 `QuadMesh`**——不认这一条，每张带色条的图都会凭空
    多背 256 个 primitive（#181 的 fixture 上模型算 662 959、后端实际
    662 773 + 1 个 `<image>`，差的就是它）。

    Session 03 给 mesh 层设上 `rasterized` 之后，再分析一次同一张图会走到
    这里——**那正是 hybrid 落地之后该看到的账**，不用另写一套。
    """
    try:
        return bool(artist.get_rasterized())
    except Exception:  # noqa: BLE001
        return False


def _mesh_cells(coll) -> int | None:
    """网格的 cell 数——**只读 `.shape`，一个元素都不碰**。

    `get_coordinates()` 是 `_MeshData` 给 `QuadMesh` / `PolyQuadMesh` 的公开
    getter（回的就是内部那份数组，不复制），形状 `(M+1, N+1, 2)` → M×N 个 cell。
    走这条而不是 `get_paths()` 是**硬要求**：`QuadMesh.get_paths()` 会当场把
    网格摊成 M×N 个 `Path` 对象（实测 40 000 个 cell 要 37.8 ms），而 QuadMesh
    的 draw 走 `draw_quad_mesh`、**从来不调它**——分析器替它建一次，就是在
    热路径上凭空加一笔 render 本来不付的钱。
    """
    get_coordinates = getattr(coll, "get_coordinates", None)
    if get_coordinates is None:
        return None
    try:
        shape = get_coordinates().shape
    except Exception:  # noqa: BLE001
        return None
    if len(shape) != 3 or shape[2] != 2:
        return None
    rows, cols = int(shape[0]) - 1, int(shape[1]) - 1
    if rows < 1 or cols < 1:
        return None
    return rows * cols


def _materialised_paths(coll):
    """已经建好的 path 列表；**还没建出来就回 None，绝不替它建**。

    这是本模块唯一一处 matplotlib 私有契约（`Collection._paths`），记在
    capability map §6 的表里。用**状态**而不是类名判，是因为「按需从网格现算
    paths」这件事在 `QuadMesh` 与 `TriMesh` 上都成立，将来还会有别的——而
    `_paths is None` 对它们全都成立，对 `PathCollection` / `PolyCollection` /
    `LineCollection` / `ContourSet` 则全都不成立（构造时就填好了，实测）。

    哪天 matplotlib 把这个属性改名，`getattr` 回 None → 我们走「量不出来」
    那一支 → 分析器不再推荐 hybrid，兜底回到 Session 01 那道按字节的硬闸。
    **失效的方向是少一层保护，不是多算一笔热路径开销、也不是崩**，这是刻意
    选的方向。`test_preview_complexity.py::test_quadmesh_paths_are_never_built`
    看着它。
    """
    if getattr(coll, "_paths", None) is None:
        return None
    try:
        return coll.get_paths()
    except Exception:  # noqa: BLE001
        return None


def _vertices_in_paths(paths) -> tuple[int, bool]:
    """(这些 path 的顶点总数, 是不是逐条数出来的)。见 `_VERTEX_SAMPLE_PATHS`。

    取 `len(p.vertices)`。对拍实测（把后端写出来的 `M`/`L`/`C`/`Q`/`z` 按各自
    吃掉的顶点数折算回去）：

    | 族 | 模型 | 后端实际 | 比 |
    |---|---|---|---|
    | mesh（24×24 pcolormesh） | 2880 | 2880 | 1.000 |
    | scatter（500 点） | 26 | 26 | 1.000 |
    | poly（300 个四边形） | 1500 | 1500 | 1.000 |
    | linecoll（400 段） | 800 | 800 | 1.000 |
    | contour（40×40 / 8 层） | 4845 | 5289 | **0.916** |

    **所以它不是「上界」，是「±10% 以内的估值」**——最后一行是唯一偏离的：
    被裁剪的等值线上，后端给每个 `CLOSEPOLY` 额外写一条回到起点的 `L`
    （实测多出来的 444 与 `CLOSEPOLY` 计数**逐个相等**）。这点偏差在一个
    10 万顶点的预算面前不影响任何裁决（普通图是几千），但**它偏低**，所以
    不能把它说成保守估计。`test_preview_complexity.py::
    test_vertex_model_tracks_what_the_backend_writes` 钉着那条实测带宽。

    **空 path 不过滤。** 直觉上「没有等值线的那一层画不出东西」，实测不是：
    8 层的 `contour` 有 10 个 path、其中 2 个空，而 SVG 里 **10 个 `<path>` 都
    在**——空的那两个照样是 DOM 节点。第一版按「非空」算，对拍当场把它抓成
    模型比后端**少** 2 个，那是错的那个方向。
    """
    n = len(paths)
    if n == 0:
        return 0, True
    sample = paths[:_VERTEX_SAMPLE_PATHS] if n > _VERTEX_SAMPLE_PATHS else paths
    total = 0
    for p in sample:
        try:
            total += len(p.vertices)
        except Exception:  # noqa: BLE001
            continue
    if n <= _VERTEX_SAMPLE_PATHS:
        return total, True
    return round(total / len(sample) * n), False


def _single_shape_blit(coll, paths) -> bool:
    """`Collection.draw` 的**单形状快路**：一条几何写一遍、blit 很多次。

    抄的是 `Collection.draw` 里 `do_single_path_optimization` 那串条件（公开
    getter 逐条对应，一个私有名都没用）。命中时它走 `renderer.draw_markers`，
    SVG 后端把 marker 收进 `<defs>`、每个 offset 出一个 `<use>`——**与
    `draw_path_collection` 的成本取舍式无关，根本不走那条路**。

    这条比取舍式重要得多：**`scatter(x, y, s=<数组>)` 一落到逐点大小上就掉出
    这条快路**（`get_transforms()` 变成每点一个），于是 500 个 marker 各自内联
    ——实测顶点数 26 → **13 000，五百倍**。只按取舍式建模会把这两种散点算成
    同一个成本（第一版就是这么错的，对拍加了那三格散点才抓出来）。

    唯一没抄的是最后那半句「marker 的包围盒要小于整张图」：算它要先有
    `_prepare_points()` 的变换，而那是热路径上不该付的钱。marker 大到跟整张图
    一样是退化情形，真出现时我们会**低估**——记在模块头的已知盲区表里。
    """
    if len(paths) != 1:
        return False
    try:
        return (
            len(coll.get_transforms()) <= 1
            and len(coll.get_facecolor()) == 1
            and len(coll.get_edgecolor()) == 1
            and len(coll.get_linewidth()) == 1
            and all(ls[1] is None for ls in coll.get_linestyle())
            and len(coll.get_antialiased()) == 1
            and len(coll.get_urls()) == 1
            and coll.get_hatch() is None
        )
    except Exception:  # noqa: BLE001
        return False


def _shares_geometry(coll, paths, n_path_ids: int, n_instances: int, verts_first: int) -> bool:
    """SVG 后端会不会把几何只写一遍、每个实例只出一个 `<use>`。

    两条路，**顺序与 matplotlib 自己的一致**：

    1. `Collection.draw` 的单形状快路（`_single_shape_blit`）→ `draw_markers`
       → 恒共享；
    2. 否则 `RendererSVG.draw_path_collection` 开头那个成本取舍式（3.10.8 /
       3.11.1 逐字相同）：

           uses_per_path = ceil(N / Npath_ids)
           len_path + 9 * uses_per_path + 3 < (len_path + 5) * uses_per_path

       以及 `_iter_collection_uses_per_path` 的前置——**面色与边色都空时它回 0**，
       取舍式当场不成立。

    实测三种散点各落在不同的分支上，对拍用例里那三格就是它们：
    `s=标量` 走 1；`c=数组`（面色 500 个）掉出 1、在 2 里仍然共享；
    `s=数组` 掉出 1、在 2 里 `uses` 被压成 1，于是**逐个内联**。

    这条只决定 `vertex_count`。`primitive_count` 两边一样（内联的 `<path>` 与
    共享的 `<use>` 都是一个 DOM 节点），所以万一将来 matplotlib 改了这套取舍，
    失准的是估值、不是保护。
    """
    if _single_shape_blit(coll, paths):
        return True
    if n_path_ids <= 0 or n_instances <= 0:
        return False
    try:
        if not (_len0(coll.get_facecolor()) or _len0(coll.get_edgecolor())):
            return False
    except Exception:  # noqa: BLE001
        return False
    uses = -(-n_instances // n_path_ids)  # ceil
    return verts_first + 9 * uses + 3 < (verts_first + 5) * uses


# ------------------------------ 逐族定价 -----------------------------------
#: 一个 mesh cell 在 SVG 里写出来的坐标对数。**5 不是 4**：
#: `QuadMesh._convert_mesh_to_paths()` 给每个 cell 一条 5 个顶点的路径
#: （四个角 + 回到起点，`codes` 是 None 所以没有 `z` 可省），后端逐个写成
#: `M L L L L`。对拍实测：24×24 的网格 = 576 个 `<path>` / **2880** 个 `M`/`L`
#: 命令 = 576 × 5。按 4 算会让每张网格图的顶点估值系统性低 20%——而低估是
#: 错的那个方向。
_MESH_VERTICES_PER_CELL = 5


def _cost_mesh(coll, cells: int) -> ArtistPreviewCost:
    """网格：**一个 cell 一个内联 `<path>`**。

    为什么恒内联：`draw_quad_mesh` 把网格摊成 M×N 个**互不相同**的 path、
    offsets 是空的，于是 `uses_per_path = 1`，取舍式变成
    `len_path + 12 < len_path + 5` ——永远不成立。#181 的 662 773 个 `<path>`
    就是这么来的（3 × 470² + 73）。
    """
    return ArtistPreviewCost(
        artist=coll,
        family=FAMILY_MESH,
        primitive_count=cells,
        vertex_count=cells * _MESH_VERTICES_PER_CELL,
        rasterizable=True,
    )


def _cost_collection(coll, family: str, paths) -> ArtistPreviewCost:
    """通用 Collection：`N = max(len(paths), len(offsets))` —— 与 family 无关的那条。

    `_iter_collection` 就是按这个 N 画的，所以它同时是 DOM 节点数。顶点数分两
    支：共享几何时 defs 里每个 path 只出现一次，内联时每个实例各摊一遍。
    """
    n_paths = len(paths)
    try:
        n_offsets = _len0(coll.get_offsets())
    except Exception:  # noqa: BLE001
        n_offsets = 0
    try:
        n_transforms = _len0(coll.get_transforms())
    except Exception:  # noqa: BLE001
        n_transforms = 0
    verts_in_paths, exact = _vertices_in_paths(paths)
    # `RendererBase._iter_collection` 画的次数：
    #     Npath_ids = max(len(paths), len(all_transforms))
    #     N         = max(Npath_ids, len(offsets))
    # **`all_transforms` 这一项不能省**：`scatter(x, y, s=<数组>)` 只有一条
    # marker path，却有每点一个的变换——漏掉它，一个逐点大小的散点会被算成
    # 「一个 primitive」。
    n_path_ids = max(n_paths, n_transforms)
    n_instances = max(n_path_ids, n_offsets)
    verts_first = 0
    if n_paths:
        try:
            verts_first = len(paths[0].vertices)
        except Exception:  # noqa: BLE001
            verts_first = 0
    if _shares_geometry(coll, paths, n_path_ids, n_instances, verts_first):
        # 几何进 defs：`scatter` 十二万个点在 SVG 文本里只有一份 marker 几何。
        vertices = verts_in_paths
    elif n_paths:
        # 逐个内联：每个实例各摊一遍平均顶点数（n_instances == n_paths 时
        # 正好回到「逐条数出来的那个和」）。
        vertices = round(verts_in_paths / n_paths * n_instances)
    else:
        vertices = 0
    return ArtistPreviewCost(
        artist=coll,
        family=family,
        primitive_count=n_instances,
        vertex_count=vertices,
        vertex_count_exact=exact,
        rasterizable=family in RASTERIZABLE_FAMILIES,
    )


def _collection_family(coll) -> str:
    """按 **family 继承关系**分派，顺序是被继承关系逼出来的、不是风格问题。

    `PolyQuadMesh` 同时是 `PolyCollection`，`FillBetweenPolyCollection` /
    `Quiver` / `Barbs` 也是；`EventCollection` 是 `LineCollection`；
    `QuadContourSet` 是 `ContourSet` 又是 `Collection`。网格那一支在
    `_classify` 里靠 `get_coordinates()` 先挑走（那是 duck typing，不是类名），
    所以到这儿的 `PolyCollection` 一定不是 `PolyQuadMesh`。
    """
    if isinstance(coll, ContourSet):
        return FAMILY_CONTOUR
    if isinstance(coll, PathCollection):
        return FAMILY_SCATTER
    if isinstance(coll, LineCollection):
        return FAMILY_LINECOLL
    if isinstance(coll, PolyCollection):
        return FAMILY_POLY
    return FAMILY_COLLECTION


def _classify(artist) -> ArtistPreviewCost:
    """一个 artist → 一笔账。**不认识也要回一笔**（family=unknown，不是丢掉）。"""
    if _already_rasterized(artist):
        return ArtistPreviewCost(
            artist=artist,
            family=FAMILY_RASTERIZED,
            primitive_count=1,
            vertex_count=0,
        )
    if isinstance(artist, Collection):
        cells = _mesh_cells(artist)
        if cells is not None:
            return _cost_mesh(artist, cells)
        paths = _materialised_paths(artist)
        if paths is None:
            # 认得出是 Collection、便宜地量不出来。**不当成 0**：进 unknown 清单，
            # 诊断看得见；也不进 rasterize 名单——量不出来就不知道该不该动它。
            return ArtistPreviewCost(
                artist=artist,
                family=FAMILY_UNMEASURED,
                primitive_count=0,
                vertex_count=0,
                vertex_count_exact=False,
            )
        return _cost_collection(artist, _collection_family(artist), paths)
    if isinstance(artist, Line2D):
        # 一条曲线 = 一个 `<path>`。顶点数走 `get_xdata()`（回的是内部数组的
        # 引用，不复制），`get_path()` 会触发 recache——热路径上不必付。
        return ArtistPreviewCost(
            artist=artist,
            family=FAMILY_LINE,
            primitive_count=1,
            vertex_count=_len0(artist.get_xdata()),
        )
    if isinstance(artist, AxesImage):
        return ArtistPreviewCost(
            artist=artist, family=FAMILY_IMAGE, primitive_count=1, vertex_count=0
        )
    return ArtistPreviewCost(
        artist=artist, family=FAMILY_UNKNOWN, primitive_count=0, vertex_count=0
    )


def _iter_artists(fig, skip_axes):
    """要算账的 artist，**确定性树序**。

    遍历的 axes 集合借 `manifest._ordered_axes`：`ax.inset_axes()` 与
    `secondary_[xy]axis()` 建出来的子 axes **不在 `fig.axes` 里**，插图里的
    大 mesh 漏掉了就是「分析器说这张图很小」。哪些 axes 存在只有一个答案，
    这里不另立一份。

    `ax.patches` / `ax.texts` 有意不算：一个 patch 是一个节点，它们撑不爆
    DOM；漏掉的那点顶点由 Session 01 按字节的硬闸兜底（见模块头「已知盲区」）。
    """
    for ax in _ordered_axes(fig)[0]:
        if ax in skip_axes:
            continue
        yield from ax.collections
        yield from ax.images
        yield from ax.lines
        yield from ax.artists
    yield from fig.artists


# ------------------------------ 裁决 ---------------------------------------
def _over_own_budget(cost: ArtistPreviewCost) -> bool:
    """这一个 artist 自己就超预算了吗。

    逐族的 primitive 预算只给 mesh 与 scatter：那两族的「一个 primitive」是
    有明确物理含义的东西（一个 cell、一个 marker 实例），数得准也调得动。
    其余族共用**顶点**预算 + 下面那条图级 primitive 预算——contour 只有几十
    个节点却有二十几万个顶点，只看节点数会整族漏掉。
    """
    if cost.family == FAMILY_MESH:
        if cost.primitive_count > previewbudget.MESH_CELL_BUDGET:
            return True
    elif cost.family == FAMILY_SCATTER:
        if cost.primitive_count > previewbudget.SCATTER_INSTANCE_BUDGET:
            return True
    return cost.vertex_count > previewbudget.COLLECTION_VERTEX_BUDGET


def analyze_preview_complexity(fig, *, skip_axes=frozenset()) -> PreviewPlan:
    """Figure → `PreviewPlan`。**只读**：不 savefig、不改 artist、不建数组。

    两轮裁决：

    1. **逐个**超出本族预算的 → 进名单（一个 22 万 cell 的 mesh 自己就该走
       位图，与图上还有什么无关）；
    2. 剩下的**加起来**仍然超 `TOTAL_VECTOR_PRIMITIVE_BUDGET` → 按开销从大到
       小继续收，直到装得下。少了这一轮，「二十个各 4 万 cell 的面板」每个都
       在族预算之内、合起来 80 万个节点照样把 DOM 打死。

    收不动的（认不出来、或按契约不该 rasterize 的普通曲线）**留在矢量层**，
    由 Session 01 那道按字节的硬闸兜底——它不需要认识任何人。
    """
    costs = [_classify(a) for a in _iter_artists(fig, skip_axes)]
    for cost in costs:
        if cost.rasterizable and _over_own_budget(cost):
            cost.should_rasterize = True

    # 第二轮按 (primitive, vertex, 树序) 降序收——树序在最后，是为了让两个
    # 一模一样贵的 artist 有个确定的先后（同一张图两次分析出同一份名单）。
    order = sorted(
        range(len(costs)),
        key=lambda i: (costs[i].primitive_count, costs[i].vertex_count, -i),
        reverse=True,
    )
    residual = sum(c.primitive_count for c in costs if not c.should_rasterize)
    for i in order:
        if residual <= previewbudget.TOTAL_VECTOR_PRIMITIVE_BUDGET:
            break
        cost = costs[i]
        if cost.should_rasterize or not cost.rasterizable:
            continue
        cost.should_rasterize = True
        residual -= cost.primitive_count

    picked = [c for c in costs if c.should_rasterize]
    total_primitives = sum(c.primitive_count for c in costs)
    total_vertices = sum(c.vertex_count for c in costs)
    vector_primitives = total_primitives - sum(c.primitive_count for c in picked)
    vector_vertices = total_vertices - sum(c.vertex_count for c in picked)

    if picked:
        mode = previewbudget.MODE_HYBRID
        reason = previewbudget.REASON_COMPLEXITY_BUDGET
        families = sorted({c.family for c in picked})
        detail = (
            f"{len(picked)} 个 artist（{', '.join(families)}）超出预览复杂度预算，"
            f"预览中临时 rasterize：矢量层 {total_primitives} → {vector_primitives} 个 primitive"
        )
    else:
        mode = previewbudget.MODE_VECTOR
        reason = previewbudget.REASON_NORMAL
        detail = f"{total_primitives} 个 primitive / {total_vertices} 个顶点，在预览复杂度预算之内"
        if vector_primitives > previewbudget.TOTAL_VECTOR_PRIMITIVE_BUDGET:
            # 超了预算却一个都收不动：全是认不出来的、或按契约不该 rasterize 的。
            # **不谎报 hybrid**——报一个我们做不到的 mode 比暂时不报更坏（前端会
            # 去等一份永远不来的混合产物）。兜底交给按字节的硬闸。
            blocked = sum(1 for c in costs if not c.rasterizable)
            detail = (
                f"{total_primitives} 个 primitive 超出矢量预算，但没有可安全 rasterize 的层"
                f"（{blocked} 个不可 rasterize）——保持 vector，由 SVG 硬闸兜底"
            )
    return PreviewPlan(
        mode=mode,
        reason=reason,
        estimated_primitives=total_primitives,
        estimated_vertices=total_vertices,
        vector_primitives=vector_primitives,
        vector_vertices=vector_vertices,
        rasterized_artists=[c.artist for c in picked],
        costs=costs,
        detail=detail,
    )


def plan_for_state(state) -> PreviewPlan:
    """`FigState` → `PreviewPlan`：**色条轴上的内部件不算用户的数据层**。

    `cb.solids` 是一个 `QuadMesh`、`cb.dividers` 是一个 `LineCollection`，两者
    每次 `_draw_all()` 都被删掉重建（capability map §4 的 `ephemeral`）。它们
    小到永远碰不到任何预算，但**名单里不该出现随时换身份的对象**——哪些轴是
    色条轴由 `manifest.instrument` 算过一次，这里读它，不另算一遍。
    """
    return analyze_preview_complexity(state.fig, skip_axes=state.colorbar_axes)
