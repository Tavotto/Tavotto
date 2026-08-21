"""直方图——OO API + main()。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    sample = np.concatenate([np.linspace(0.0, 3.0, 40),
                             np.linspace(1.0, 2.0, 60)])
    ax.hist(sample, bins=10, color="#5B8C5A", edgecolor="white")
    ax.set_title("Histogram")
    ax.set_xlabel("value")
    ax.set_ylabel("frequency")
    fig.savefig("mm_histogram_oo.pdf")
