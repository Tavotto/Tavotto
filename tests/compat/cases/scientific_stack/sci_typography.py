"""排版长尾：mathtext 与中文标签。

中文这一档对**字体环境**敏感：环境里没有 CJK 字体时画出来是豆腐块，
那是环境依赖，不是 Tavotto 的引擎 bug——CompatBench 把它分类成
`environment_dependency` 并显式记录前提。
"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(0.5, 4.0, 30)


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    ax.plot(X, 1.0 / X)
    ax.set_title(r"Raman shift $\nu$ / $\mathrm{cm}^{-1}$")
    ax.set_xlabel(r"Wavenumber (cm$^{-1}$)")
    ax.set_ylabel(r"$I/I_0$")
    ax.text(0.15, 0.75, r"$\alpha_2^3 + \sqrt{\beta}$", transform=ax.transAxes)
    fig.savefig("sci_mathtext.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    ax.plot(X, np.sqrt(X))
    ax.set_title("拉曼位移谱图")
    ax.set_xlabel("波数 (cm$^{-1}$)")
    ax.set_ylabel("强度（任意单位）")
    ax.text(0.12, 0.78, "峰位标注", transform=ax.transAxes)
    fig.savefig("sci_cjk.pdf")
