"""「这张图上有哪些 axes、按什么顺序编号」——全产品唯一的一份遍历权威。

`manifest`（登记元素、普查）、`overrides`（按序号现解刻度文字、色条随行、双生轴亲缘）、
`preview_complexity`（估算 primitive）都从这里取 `ordered_axes`，谁都不许再抄一遍
`fig.axes` + `child_axes` + `parasites` 的合并（看护：`tests/test_axes_traversal_authority.py`
的源码级门禁）。

这个模块**只依赖标准库**：它只按属性名 `axes` / `child_axes` / `parasites` 走对象图，
不 import matplotlib，也不 import `manifest` / `overrides`——它是那两个模块共同的底层。
2026-09-17 之前它是 `manifest._ordered_axes`，`overrides` 只能经 `_sibling("manifest")`
延后反取（manifest 在模块层 import overrides，反过来在模块层 import 会成环）；提到这里之后
两边都在模块层平铺 import 它，那个环没了。正文逐字未改。

平铺 import 的纪律与别的引擎兄弟模块相同：safe worker 把 engine 目录塞进 `sys.path`
后裸 `import axestraversal`；native bridge 由 `bridge_runner._PHASE2` 装进私有包、
`bridgeboot._TOPLEVEL_TO_RESTORE` 把顶层名字还给用户（两张表由
`tests/bridge/test_bridge_namespace.py` 从 AST 反推闭包校验）；浏览器 playground 的
`ENGINE_FILES` 与 PyInstaller spec 同样列了它（`test_playground_build.py` /
`test_runtime_build.py` 反推校验）。
"""

from __future__ import annotations


def axis_drawn(ax, which: str) -> bool:
    """这条轴（刻度线、刻度文字、网格线、轴标签）此刻**画不画**——唯一判据。

    `Axis` 自己的 `get_visible()` 只是其中一半：`ax.set_axis_off()`（`axison =
    False`）时 `Axes.draw` 把整条轴从待画列表里摘掉，而轴上每一个 Text / Line2D
    的 visible 都还是 True、位置也还在算——按它们自己的 visible 判，就会在图外
    （x 刻度文字落到 y>1）和相邻面板上（右边那张图的 y 刻度文字压在左图上）
    摆出一整组看不见的命中框（2026-09-24，用户的 PRB 双联图：两张 imshow 位图
    `set_axis_off()`，点左图右缘选中的是右图的「Y 刻度文字」）。3D 轴的开关是
    另一个属性（`Axes3D.set_axis_off` 写 `_axis3don`），同一处分开问。
    """
    # 3D 轴**只看** `_axis3don`：`Axes3D.__init__` 自己把 `axison` 置成 False（它的
    # 轴由 `axis3d` 在投影里画，不走 2D 那条），拿 axison 判会把每张 3D 图的刻度都丢掉
    on = ax._axis3don if hasattr(ax, "_axis3don") else getattr(ax, "axison", True)
    if not on:
        return False
    axis = getattr(ax, f"{which}axis", None)
    return axis is not None and bool(axis.get_visible())


def frame_drawn(ax) -> bool:
    """边框（spines）与背景矩形（`ax.patch`）画不画。

    `Axes.draw` 的条件是 `axison and _frameon`：`set_axis_off()` 与
    `set_frame_on(False)` 都会把四条边框和背景一起摘掉，而 `Spine.get_visible()`
    照旧是 True。判据与 matplotlib 同一个合取式，别拆成两处各判一半。
    """
    if not getattr(ax, "axison", True):
        return False
    get_frame_on = getattr(ax, "get_frame_on", None)
    return bool(get_frame_on()) if callable(get_frame_on) else True


def ordered_axes(fig) -> tuple[list, set, set]:
    """(全部 axes（含子 axes 与寄生轴）, 子 axes 的 id 集合, 寄生轴的 id 集合)。

    `fig.axes` 只收 `add_subplot` / `add_axes` 建出来的那些。有**两族**轴不在
    里面，各挂在各自的属性上：

      * **子 axes**：`ax.inset_axes(...)` 与 `ax.secondary_[xy]axis(...)`
        建出来的挂在 `ax.child_axes` 上——不遍历它们的话，插图里的曲线选不中、
        次坐标轴的标签也改不了；
      * **寄生轴**：`mpl_toolkits.axes_grid1`（与 `axisartist`）的
        `host_subplot(...).twinx()` 建出来的挂在 `host.parasites` 上。它们
        **既不在 `fig.axes` 也不在 `child_axes`**（宿主在自己的 `draw()` 里把
        `ax.get_children()` 临时接到孩子列表上代画），于是整条第二组数据连同
        它的右轴一起不进 manifest：列不出、也改不了，而且**不报错**
        （issue #217）。

    **子 axes 与寄生轴一律排在所有 `fig.axes` 之后**，编号继续 `axes_{i}`。
    这条不是风格问题：`axes_i` 会进用户文档（override 的 gid），存量文档里的
    编号一个字节都不能变。插在中间会让「同一张图、同一个 gid」在升级前后指向
    不同的 axes——那是数据级的错位。

    **寄生轴单独走第二趟，不与 `child_axes` 合成一趟**，理由同上：一张
    `host_subplot` 上既开了 `twinx()` 又开了 `inset_axes()` 的图，合成一趟会
    按属性先后把寄生轴排到插图前面，把那个插图**已经发出去的** `axes_i` 顶掉
    一位。两趟走完，本次改动之前的那份序列是新序列的**严格前缀**——存量文档
    里的每一个 `axes_i` 都还指向同一个 axes，新认出来的只在末尾追加。

    逐层广度优先（同一层的兄弟排完再下一层），所以同一个脚本每次跑出来的
    gid 串完全一致；插图里再开插图、寄生轴上再开插图也照样确定。按 `id()`
    去重防环。
    """
    out = list(fig.axes)
    seen = {id(a) for a in out}
    children: set = set()
    parasites: set = set()

    def _absorb(frontier: list, sources: tuple[tuple[str, set], ...]) -> None:
        """把 `sources` 点名的属性逐层收进 `out`（每条是「属性名, 归入的集合」）。"""
        while frontier:
            nxt = []
            for parent in frontier:
                for attr, bucket in sources:
                    for kid in getattr(parent, attr, None) or []:
                        if id(kid) in seen:
                            continue
                        seen.add(id(kid))
                        bucket.add(id(kid))
                        out.append(kid)
                        nxt.append(kid)
            frontier = nxt

    _absorb(list(out), (("child_axes", children),))
    # 第二趟从**当前全部**已知 axes 起步：寄生轴可能开在插图上，插图也可能开在
    # 寄生轴上，两个方向都要走得到。第一趟收过的由 `seen` 挡住，不会重排。
    _absorb(list(out), (("parasites", parasites), ("child_axes", children)))
    return out, children, parasites
