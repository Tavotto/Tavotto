"""不变式用例的图库：一个脚本出四张图（InvMix / InvCont / InvCbar / InvPar / InvTight），
`tests/test_invariants_engine.py` 与 `tests/test_override_sequences.py` 共用。2026-09-18 从前者
逐字搬出——两套用例量的必须是同一批图。
"""

from __future__ import annotations

SCRIPT_NAME = "fig_invariants.py"
ENTRY = "main"
STEMS = ("InvMix", "InvCont", "InvCbar", "InvPar")

#: 一个脚本出三张图，一次 build 全捕获（build 是这套用例里唯一慢的一步）。
#: 每张图都刻意做得**元素互相重叠**——`zorder` 想被验出效果就得有东西挡；
#: 每张图都带图例——`label` 想被验出效果就得有地方显形。
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.patches import Arc, Circle, Rectangle
from mpl_toolkits.axes_grid1 import host_subplot


def main():
    rng = np.random.RandomState(0)
    x = np.linspace(0.5, 6.0, 24)

    # ---- InvMix：Collection 族 + Patch 族 + 图例 ----
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    # marker 用 `^` / `s`：默认就是 `o` 的话，「换成 o」测不出任何变化
    ax.scatter(x, np.sin(x) + 2.0, marker="^", s=60, label="pts")
    ax.scatter(x, np.cos(x) + 2.0, c=x, marker="s", s=60, label="mapped")
    ax.fill_between(x, 0.6, np.sin(x) + 1.2, alpha=0.35, label="band")
    # 3×3 的**大格子**：8×8 那种一格才十来个像素，网格线的线型与花纹在
    # 这个尺寸下画不出可分辨的差别，于是「改了没反应」全是夹具自己挡的
    ax.pcolormesh(np.linspace(0.5, 6.0, 4), np.linspace(-1.9, -0.4, 4), rng.rand(3, 3))
    ax.contour(np.linspace(0.5, 6.0, 8), np.linspace(3.2, 4.4, 8), rng.rand(8, 8))
    ax.eventplot([[1.0, 2.0, 3.0]], lineoffsets=0.2, linelengths=0.5)
    # 映射的线组：颜色由 colormap 每次 draw 重算，**不算线组那一族**；
    # `linestyles="--"` 是有意的（Collection 的线型反查要用未缩放规格）
    ax.add_collection(LineCollection(
        [[(0.5, -2.2), (6.0, -2.2)], [(0.5, -2.0), (6.0, -2.0)]],
        array=np.array([0.2, 0.8]), cmap="viridis", linestyles="--",
        clim=(-0.5, 1.5), linewidths=3))
    # 数组在、映射**不在**：脚本自己把颜色写死了，于是两个通道都没在映射
    # （matplotlib 的 `_set_mappable_flags` 只在 facecolor 不是 'none'、或者
    # edgecolor 没被显式设过时才置位）。给它 cmap 控件 = 三个死开关。
    ax.add_collection(LineCollection(
        [[(0.5, -2.4), (6.0, -2.4)]], colors="#804000",
        array=np.array([0.5]), cmap="viridis", linewidths=3))
    # 三个形状**叠在一起**：zorder 想验出效果，必须有东西可挡
    # **空心散点**（`facecolors="none"`）：`get_facecolor()` 长度为 0，而
    # marker 路径是闭合可填的——`set_facecolor` 实测改 1197 像素、draw 之后
    # 精确等于请求的颜色。「此刻有没有面色」当判据时它拿不到 facecolor 控件，
    # 那是**能改却不宣称**。夹具里原来两个散点一个实心一个映射，恰好把这一格
    # 漏掉了。marker 用 `D`：默认是 `o` 的话「换成 o」测不出变化。
    #
    # **必须排在本轴全部 collection 之后**——上面几条用例写死了
    # `collections_6` / `collections_7`，插在中间会把编号整个顶掉。
    ax.scatter(x, np.tan(x / 8.0) + 3.2, s=80, marker="D",
               facecolors="none", edgecolors="#8844CC", label="hollow")
    # **逐元素 alpha**：`get_alpha()` 回 ndarray。`float(ndarray)` 抛 TypeError，
    # 整份 manifest 建不出来——一张完全正常的图直接打不开（P1）。而且这个控件
    # 本身也用不了：matplotlib 的 `Artist.set_alpha` 里 `if alpha != self._alpha`
    # 对数组当场 ValueError（三个版本 × 三种 artist 实测一致，连
    # `set_alpha(None)` 都抛）。所以夹具里必须真有这么一个。
    ax.scatter(x, np.cos(x) - 1.6, s=70, marker="P",
               alpha=np.linspace(0.25, 0.95, len(x)), label="alpha-array")
    ax.add_patch(Rectangle((1.0, -0.2), 2.2, 0.9, facecolor="#B34700"))
    ax.add_patch(Circle((2.0, 0.25), 0.5, facecolor="#2A6F3C"))
    ax.add_patch(Arc((2.0, 0.25), 1.4, 1.4, theta1=0, theta2=270))
    # 带 marker 的曲线：`_markerfacecolor` 默认是字符串 `'auto'`（跟着线色走），
    # 而 `get_markerfacecolor()` 会把它解析成当前 color——「先改线色再改
    # marker 色」那条 P1 就藏在这个解析里，没有 marker 就测不到。
    ax.plot([0.8, 3.0, 5.6], [3.4, 3.9, 3.5], marker="o", markersize=11,
            label="mk")
    ax.set_xlim(0, 6.5)
    ax.set_ylim(-2.6, 4.6)
    ax.legend(loc="upper right")
    fig.savefig("InvMix.pdf")

    # ---- InvCont：三种容器 + 被消费成员的旧 gid ----
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    ax.stem([1.0, 2.0, 3.0], [1.0, 2.0, 1.5], linefmt="--", label="stems")
    ax.bar([5.0, 6.0], [1.6, 2.2], label="bars")
    ax.errorbar([8.0, 9.0], [1.0, 1.5], yerr=0.25, label="err", capsize=3)
    ax.legend(loc="upper left")
    fig.savefig("InvCont.pdf")

    # ---- InvScale：**严格为正**的数据，专供 scale ↔ lim 那一对 ----
    # 其余几张图的 y 轴都跨 0，`set_yscale("log")` 在那里会被 matplotlib 夹住、
    # 于是「换了对数轴之后自动缩放把 lim 挪走」这个副作用根本不发生——夹具
    # 自己把要测的东西挡掉了。**这种情况改夹具，不加豁免。**
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    ax.plot([1.0, 2.0, 3.0, 4.0], [1.0, 12.0, 45.0, 100.0], marker="o")
    fig.savefig("InvScale.pdf")

    # ---- InvCbarLC：**映射的线组 + 它的色条** ----
    # 面在映射时（imshow / pcolormesh）设 edgecolor 断不了映射，所以那条
    # 「色条与 mappable 同一道闸」测不出来。必须用**边在映射**的那种：
    # 线组没有面，颜色走边这条通道，`set_edgecolor` 一设映射就断。
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    segs = [[(0.0, i), (1.0, i)] for i in range(6)]
    lc_cb = LineCollection(segs, array=np.linspace(0.0, 1.0, 6),
                           cmap="viridis", linewidths=6)
    ax.add_collection(lc_cb)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.5, 5.5)
    fig.colorbar(lc_cb, ax=ax)
    fig.savefig("InvCbarLC.pdf")

    # ---- InvCbar：图像 + 色条（两个 gid 一份状态） ----
    fig, ax = plt.subplots(figsize=(3.6, 2.9))
    # 512×512 画进一个两英寸的轴 = 真正的**降采样**，`interpolation` 才有
    # 意义：放大时 matplotlib 的 antialiased 本来就退化成 nearest，拿
    # 8×8 去验这条属性，测出来的只会是「改了没反应」
    im = ax.imshow(rng.rand(512, 512), cmap="magma")
    cb = fig.colorbar(im, ax=ax, extend="both")
    cb.set_label("signal")
    fig.savefig("InvCbar.pdf")

    # ---- InvCbar2：**同一个 mappable 两条色条**（三个 gid 一份状态） ----
    # 论文图里很常见（右边一条竖的、下面再来一条横的）。两条色条的
    # cmap/vmin/vmax 都解析到同一个 `(images_0, prop)` 窄 key——那是别名簿记
    # 里唯一一处「一个窄 key 有**两个**广播端」的形态，而簿记原来只存得下一个。
    fig, ax = plt.subplots(figsize=(3.6, 2.9))
    im2 = ax.imshow(rng.rand(64, 64), cmap="viridis")
    fig.colorbar(im2, ax=ax, location="right")
    fig.colorbar(im2, ax=ax, location="bottom", fraction=0.046)
    # **独立 mappable 的两条色条**（同一张图，排在上面两条之后，gid 不受影响）。
    # 这条路与上面那对的区别在于：`ScalarMappable` **不是 Artist**，`HANDLERS`
    # 里没有它的 cmap，所以别名的窄成员**采不到原样**——共享原样只能走
    # 「对等广播端」那条回退。少了它，两条全撤之后停在中间态。
    sm = ScalarMappable(norm=Normalize(0.0, 1.0), cmap="viridis")
    fig.colorbar(sm, ax=ax, location="left")
    fig.colorbar(sm, ax=ax, location="top", fraction=0.046)
    fig.savefig("InvCbar2.pdf")

    # ---- InvPar：`axes_grid1` 的 host_subplot + twinx（**寄生轴**，#217） ----
    # 这一族轴既不在 `fig.axes` 也不在 `child_axes`，只挂在 `host.parasites`
    # 上——遍历漏了它，第二组数据在 Tavotto 里既列不出也改不了，而且**不报错**。
    # 放进这份夹具，是为了让寄生轴上的登记面吃到与其他图**同一套**扫描：能力
    # 真实、枚举可用、逐字还原。两条曲线都带 marker 与虚线，是为了让 marker 组
    # 与 linestyle 有可分辨的差；宿主与寄生各有 y 轴标签，两侧的文字类元素都扫得到。
    fig = plt.figure(figsize=(4.2, 3.2))
    host = host_subplot(111, figure=fig)
    par = host.twinx()
    # **四边留白必须够宽**：默认边距下两行的轴标签有一行落在画布外，于是
    # `linespacing` 改了也看不见——那是**夹具挡住了要测的东西**，不是这条属性
    # 不生效（同一个 prop 在纯 Text / 标题上实测都改得动像素）。这种情况改图。
    fig.subplots_adjust(left=0.26, bottom=0.26, right=0.74, top=0.84)
    host.plot(x, np.sin(x) + 2.0, marker="o", markersize=7, label="host")
    par.plot(x, np.cos(x) * 40.0 + 60.0, color="#B34700", marker="s",
             markersize=7, linestyle="--", label="parasite")
    host.set_xlabel("shared x")
    host.set_ylabel("host y")
    par.set_ylabel("parasite y")
    host.set_title("host + parasite")
    fig.savefig("InvPar.pdf")

    # ---- InvTight：**持久 tight 布局**（issue #162） ----
    # `layout="tight"` 会在图上挂一个常驻的 TightLayoutEngine，它每次绘制都把
    # 有 SubplotSpec 的子图位置整个算回去。#140 之前 `axes.position` 在这里是
    # silent wrong，#140 之后是「不宣称这条能力」，#162 之后靠
    # `overrides.PinnedTightLayoutEngine` 钉住。两个子图 + 轴标签是有意的：
    # 一个是被拖的，另一个用来验「我拖了 A，B 不该跟着跳」。
    fig, axs = plt.subplots(1, 2, figsize=(4.6, 2.4), layout="tight")
    for i, a in enumerate(axs):
        a.plot([0.0, 1.0], [0.0, i + 1.0], marker="o", label=f"s{i}")
        a.set_xlabel("x label")
        a.set_ylabel("y label")
    axs[0].legend(loc="upper left")
    fig.savefig("InvTight.pdf")
"""
