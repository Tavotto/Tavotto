"""验收 corpus 之二：多子图、双轴、对数轴、自动布局。

这一组针对的是 Tavotto 里**几何最容易出岔子**的那些形态。CLAUDE.md 里有两条
直接相关的记录：

* 子图 position 的应用有专门的规范顺序档位，且 `aspect="equal"` 的子图
  只有 draw 才 apply_aspect，几何组应用完必须先刷新布局；
* `set_[xy]scale` 会把 locator/formatter 整套换掉，所以刻度类 prop 必须
  每次重放，而不能走「值没变就跳过」的捷径。

对数轴与 twinx 正是这两条的现场，放进 corpus 是为了让「热态所见 == 全量重放」
在真实图形上被持续验证，而不只是在单元测试的合成 figure 上。
"""

import matplotlib.pyplot as plt
import numpy as np


def subplots_grid():
    """2×2 网格：每格一种线型，验证多 axes 下 gid 的稳定性。"""
    x = np.linspace(0, 6, 100)
    fig, axes = plt.subplots(2, 2, figsize=(4.6, 3.4))
    styles = [("-", "#1f4e79"), ("--", "#c1440e"), (":", "#2a6f4e"), ("-.", "#4a2c6b")]
    for k, (ax, (ls, color)) in enumerate(zip(axes.ravel(), styles)):
        ax.plot(x, np.sin(x + k * 0.6), ls=ls, color=color, lw=1.2)
        ax.set_title(f"panel {k + 1}", fontsize=8)
        ax.tick_params(labelsize=7)
    fig.suptitle("2×2 subplots")
    fig.tight_layout()
    fig.savefig("c02_subplots.pdf")


def twin_axes():
    """twinx：两条 y 轴共享 x。轴标签归属容易搞错，是回归的高发区。"""
    x = np.linspace(0, 12, 120)
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(x, np.exp(-x / 5) * 100, color="#1f4e79", lw=1.3)
    ax.set_xlabel("time (h)")
    ax.set_ylabel("concentration", color="#1f4e79")
    ax.tick_params(axis="y", labelcolor="#1f4e79")

    ax2 = ax.twinx()
    ax2.plot(x, 1 - np.exp(-x / 3), color="#c1440e", lw=1.3, ls="--")
    ax2.set_ylabel("conversion", color="#c1440e")
    ax2.tick_params(axis="y", labelcolor="#c1440e")
    ax.set_title("Twin y axes")
    fig.tight_layout()
    fig.savefig("c02_twinx.pdf")


def log_scale():
    """对数轴：locator 是 LogLocator。

    「没表态 = 用脚本原样」这条约定的现场——把 LogLocator 换成 AutoLocator
    就是把用户的图改了，而那种改动在像素上极其显眼。
    """
    x = np.logspace(0, 4, 60)
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.loglog(x, x**-0.8 * 1e3, lw=1.3, color="#2a6f4e", label="slope −0.8")
    ax.loglog(x, x**-1.2 * 1e4, lw=1.3, ls="--", color="#a8331c", label="slope −1.2")
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("power")
    ax.set_title("Log–log")
    ax.grid(True, which="both", lw=0.3, alpha=0.35)
    ax.legend(loc="lower left", fontsize=7)
    fig.savefig("c02_loglog.pdf")


def constrained():
    """constrained_layout + 色条。

    色条方向/extend 在 Tavotto 里是**就地结构改造**（`_cb_reorient`），
    落位由 matplotlib 自己的 `_ColorbarAxesLocator` 每帧重算。这个 case
    让那条路径在真实图上一直有覆盖。
    """
    y, x = np.mgrid[0:40, 0:60]
    z = np.sin(x / 7.0) * np.cos(y / 5.0)
    fig, ax = plt.subplots(figsize=(3.8, 2.6), layout="constrained")
    im = ax.pcolormesh(z, cmap="viridis", shading="auto")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("amplitude")
    ax.set_title("constrained_layout + colorbar")
    ax.set_xlabel("column")
    ax.set_ylabel("row")
    fig.savefig("c02_constrained.pdf")


def main():
    subplots_grid()
    twin_axes()
    log_scale()
    constrained()


if __name__ == "__main__":
    main()
