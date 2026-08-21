"""mplot3d：line / scatter / surface / wireframe / bar3d。

3D 里 Tavotto 的产品边界是「只有文字类元素与视角可编辑」，盒内数据属性
（spines/lim/scale）刻意禁用。CompatBench 记录的是这条边界本身。
"""
import matplotlib.pyplot as plt
import numpy as np

_t = np.linspace(0.0, 6.0, 30)
_x = np.linspace(-2.0, 2.0, 12)
XX, YY = np.meshgrid(_x, _x)
ZZ = np.exp(-(XX ** 2 + YY ** 2))


def main():
    fig = plt.figure(figsize=(3.4, 2.8))
    ax = fig.add_subplot(projection="3d")
    ax.plot(_t, np.sin(_t), np.cos(_t))
    ax.set_title("3D line")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    fig.savefig("art_3d_line.pdf")

    fig = plt.figure(figsize=(3.4, 2.8))
    ax = fig.add_subplot(projection="3d")
    ax.scatter(_t, np.sin(_t), np.cos(_t), s=14)
    ax.set_title("3D scatter")
    fig.savefig("art_3d_scatter.pdf")

    fig = plt.figure(figsize=(3.4, 2.8))
    ax = fig.add_subplot(projection="3d")
    ax.plot_surface(XX, YY, ZZ, cmap="viridis")
    ax.set_title("3D surface")
    fig.savefig("art_3d_surface.pdf")

    fig = plt.figure(figsize=(3.4, 2.8))
    ax = fig.add_subplot(projection="3d")
    ax.plot_wireframe(XX, YY, ZZ, linewidth=0.6)
    ax.set_title("3D wireframe")
    fig.savefig("art_3d_wireframe.pdf")

    fig = plt.figure(figsize=(3.4, 2.8))
    ax = fig.add_subplot(projection="3d")
    xs = np.arange(4, dtype="float64")
    ax.bar3d(xs, xs, np.zeros(4), 0.6, 0.6, np.array([1.0, 2.0, 3.0, 1.5]))
    ax.set_title("3D bar")
    fig.savefig("art_3d_bar.pdf")
