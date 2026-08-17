"""内置渲染环境的验收脚本：把整套科学栈都用上一遍，画一张真图。

这不是给用户看的示例，是 CI 的证据：Windows 桌面版的
`Magplot.exe → runtime/python.exe → engine/worker.py → 本脚本` 这条链路
真的能跑通，而且**不需要用户机器上有任何 Python**。

刻意不 import paper_style：内置 runtime 不该依赖任何图库方言，
worker 那边的 `import paper_style` 本来就在 try/except 里。

每个包都要真用一次，不能只 import——`import scipy` 成功、
`scipy.optimize` 里的 C 扩展加载失败，是 Windows 上最典型的一档。
"""
import numpy as np
import pandas as pd
import scipy.optimize as opt
import scipy.stats as stats
import seaborn as sns
from PIL import Image

import matplotlib
import matplotlib.pyplot as plt


def main():
    rng = np.random.default_rng(20260817)          # numpy
    t = np.linspace(0.0, 10.0, 120)
    y = 2.4 * np.exp(-t / 3.1) + rng.normal(0, 0.03, t.size)

    # scipy：曲线拟合 + 统计量（两条不同的 C 扩展路径）
    (a, tau), _ = opt.curve_fit(lambda x, a, tau: a * np.exp(-x / tau), t, y,
                                p0=(2.0, 3.0))
    r = stats.pearsonr(y, a * np.exp(-t / tau)).statistic

    # pandas：DataFrame + groupby（Windows 上的 C 扩展 + 时间处理）
    df = pd.DataFrame({"t": t, "signal": y,
                       "half": np.where(t < 5, "early", "late")})
    means = df.groupby("half", observed=True)["signal"].mean()

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(6.3, 2.4))
    ax.plot(t, y, ".", ms=2.5, color="#4C72B0", label="data")
    ax.plot(t, a * np.exp(-t / tau), lw=1.2, color="#C44E52",
            label=f"fit τ={tau:.2f} s")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Signal (a.u.)")
    ax.set_title(f"Decay (r={r:.4f})")
    ax.legend(loc="upper right", fontsize=7)

    # seaborn：吃 DataFrame 的那条路径
    sns.boxplot(data=df, x="half", y="signal", ax=ax2, width=0.5)
    ax2.set_title(f"early={means['early']:.2f}  late={means['late']:.2f}")
    ax2.set_xlabel("")

    # Pillow：内存里造一张图再读回来，不落盘
    img = Image.new("RGB", (8, 8), (200, 210, 230))
    assert img.size == (8, 8) and img.getpixel((0, 0))[2] == 230

    fig.tight_layout()
    fig.savefig("Fig_runtime_stack.pdf")
    print(f"matplotlib {matplotlib.__version__} numpy {np.__version__} "
          f"pandas {pd.__version__} seaborn {sns.__version__}")


if __name__ == "__main__":
    main()
