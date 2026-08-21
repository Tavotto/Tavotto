"""imshow + 色条——OO API + main()。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    grid = np.arange(36, dtype="float64").reshape(6, 6)
    im = ax.imshow(grid, cmap="viridis")
    fig.colorbar(im, ax=ax).set_label("intensity")
    ax.set_title("Image with colorbar")
    fig.savefig("mm_imagecbar_oo.pdf")
