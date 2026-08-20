"""验收 corpus 之三：文字、图例、注释箭头、位图、科学计数法、中文。

集中在 Tavotto 里**文字与非矢量内容**这一侧，几条直接对应 CLAUDE.md 记录过的坑：

* 图例重建后必须重挂 `_legend_box.set_offset`，否则导出时图例整块消失；
* `annotate` 的 arrow_patch 端点由注释机制每次 draw 重定位，**刻意不出端点**；
* `image.alpha` 实测不可预览（透明度烤进 PNG 栅格），必须回退后端重画；
* 图内元素文字走 mathtext（`cm$^{-1}$`），与画布标注的 `^{}`/`_{}` 是两套。

中文那个 case 在 manifest 里标了 `visual: false`——理由写在 manifest 的
`visual_skip_reason` 字段里，不在这里重复。
"""
import numpy as np

import matplotlib.pyplot as plt


def legend_variants():
    """多列图例 + 框线。ncol 是重建型改动，历来是图例消失的高发路径。"""
    x = np.linspace(0, 8, 100)
    fig, ax = plt.subplots(figsize=(3.8, 2.5))
    for k, color in enumerate(["#1f4e79", "#c1440e", "#2a6f4e", "#4a2c6b"]):
        ax.plot(x, np.sin(x + k * 0.5) * (1 - k * 0.15), lw=1.1,
                color=color, label=f"series {k + 1}")
    ax.set_title("Legend, 2 columns")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.legend(ncol=2, fontsize=7, frameon=True, loc="lower left")
    fig.tight_layout()
    fig.savefig("c03_legend.pdf")


def annotations():
    """注释箭头 + 文本框。annotate 的箭头不出端点，是刻意的产品语义。"""
    x = np.linspace(0, 10, 200)
    y = np.exp(-((x - 4) ** 2) / 2.0)
    fig, ax = plt.subplots(figsize=(3.6, 2.5))
    ax.plot(x, y, lw=1.4, color="#1f4e79")
    ax.annotate("peak", xy=(4.0, 1.0), xytext=(6.5, 0.8),
                arrowprops=dict(arrowstyle="->", lw=1.0, color="#333333"),
                fontsize=9)
    ax.annotate("baseline", xy=(9.0, 0.02), xytext=(6.2, 0.25),
                arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#a8331c"),
                fontsize=8, color="#a8331c")
    ax.text(0.4, 0.85, "FWHM $=2\\sqrt{2\\ln 2}\\,\\sigma$", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", fc="#f3f1ea", ec="#cfc9b8", lw=0.6))
    ax.set_title("Annotations")
    ax.set_xlabel("position")
    fig.tight_layout()
    fig.savefig("c03_annotations.pdf")


def sci_notation():
    """科学计数法刻度 + 长轴标签。offset text 的归属是另一处易错点。"""
    x = np.linspace(0, 5e-6, 80)
    y = x * 2.5e12 + 1.0e6
    fig, ax = plt.subplots(figsize=(3.8, 2.5))
    ax.plot(x, y, lw=1.3, color="#2a6f4e")
    ax.set_xlabel("Displacement measured along the optical axis (m)")
    ax.set_ylabel("Detector counts")
    ax.set_title("Scientific notation & long labels")
    ax.ticklabel_format(axis="both", style="sci", scilimits=(-2, 3))
    fig.tight_layout()
    fig.savefig("c03_scinotation.pdf")


def image_panel():
    """imshow 位图 + 色条 extend。

    位图内部的文字属于 `not_verifiable` 那一档——预检查不了，需要人工确认。
    这里放一张真位图，让那条判定在验收里始终有覆盖。
    """
    y, x = np.mgrid[0:64, 0:64]
    z = np.sin(x / 6.0) * np.cos(y / 9.0) + (x - 32) / 96.0
    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    im = ax.imshow(z, cmap="magma", origin="lower", aspect="auto",
                   vmin=-1.0, vmax=1.2)
    cb = fig.colorbar(im, ax=ax, extend="both")
    cb.set_label("signal (a.u.)")
    ax.set_title("imshow + extended colorbar")
    ax.set_xlabel("px")
    ax.set_ylabel("px")
    fig.tight_layout()
    fig.savefig("c03_image.pdf")


def cjk_labels():
    """中文标签。

    **这个 case 在 manifest 里 visual: false**：CJK 字体在不同机器/不同
    fonts-noto 版本下的字形与度量都可能不同，像素比对会在一次与产品无关的
    字体包升级里整片变红。结构与导出仍然验——中文本身必须能画出来，
    只是不拿它当像素基线。
    """
    # **显式声明 CJK 字体**：matplotlib 默认的 DejaVu Sans 没有中文字形，
    # 不声明的话画出来是一排方框，而日志里只有一堆 "Glyph missing" 警告——
    # 图照样生成、测试照样通过，没人会发现中文其实没画出来。
    # 回退链留着 DejaVu：字体没装时至少别让整个脚本崩掉，
    # 而 manifest 里这个 case 本来就不做像素比对。
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans SC",
        "WenQuanYi Zen Hei", "PingFang SC", "Microsoft YaHei", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    labels = ["对照", "低剂量", "中剂量", "高剂量", "极高"]
    vals = [3.2, 5.8, 7.4, 8.1, 8.4]
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(range(len(vals)), vals, "o-", lw=1.3, color="#1f4e79")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("转化率（%）")
    ax.set_title("中文标签与刻度")
    ax.grid(True, lw=0.4, alpha=0.35)
    fig.tight_layout()
    fig.savefig("c03_cjk.pdf")


def main():
    legend_variants()
    annotations()
    sci_notation()
    image_panel()
    cjk_labels()


if __name__ == "__main__":
    main()
