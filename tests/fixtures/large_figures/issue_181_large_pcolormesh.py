"""Issue #181 的合成复现：一张「大型 mesh 科研图」，不含任何用户数据。

Issue #181 是**性能与架构**问题：几十万个 vector primitive 的预览 SVG 一路
穿过 worker → JSON → Flask → 浏览器 DOM。要判断任何改动是否真的有效，就得先
有一张能在本机重复画出来、且**规模可调**的图。用户的真实 CSV / SVG /
诊断包一律不进仓库——这里的数据全部由 `np.random.default_rng(181)` 现生成，
同一个 n 在任何机器上都是同一张图。

结构刻意做成**真实论文图的样子**而不是「一个巨大的 mesh」：

    2 x 2
    ├─ (0,0) QuadMesh + colorbar     ← 大头
    ├─ (0,1) QuadMesh                ← 大头
    ├─ (1,0) QuadMesh                ← 大头
    └─ (1,1) 两条普通曲线 + 图例      ← 正常矢量语义，必须继续可编辑

第四格是**判据的一部分**，不是装饰：#181 的最终解法（complexity-aware hybrid
preview，ADR 0022）要求 mesh 层可以临时 rasterize、而文字/坐标轴/图例/普通
曲线保持 vector。一张只有 mesh 的图问不出「hybrid 有没有把该留的留住」。

规模旋钮：环境变量 `TAVOTTO_ISSUE181_MESH_N`（每个 mesh 的边长，默认 470
≈ 22 万 cells/panel、约 66 万 primitive 合计）。测试用小 n 跑得快，基线用
默认 n 复现 #181 的量级。

**不要把跑出来的 SVG/PDF 提交进仓库**（默认 n 下 SVG 一百多 MB）——这个脚本
在需要时现跑。
"""

import hashlib
import os

import matplotlib.pyplot as plt
import numpy as np

#: 每个 mesh 的边长。默认值让三块 mesh 合计约 66 万个 quad——与 issue #181
#: 里那张 134 MB 预览 SVG 同一量级（实测约 173 bytes/cell）。
DEFAULT_MESH_N = 470

#: 随机数种子。**钉死**：改了它就换了一张图，历史基线数据全部作废。
SEED = 181

#: 产物 stem。Tavotto 按 stem 索引一切（注册表、面板、渲染态）。
STEM = "Issue181_large_pcolormesh"


def mesh_n() -> int:
    """本次要用的 mesh 边长。非法值当场抛——静默回落到默认值会让基线数据
    与它自称的规模对不上，那比报错难查得多。"""
    raw = os.environ.get("TAVOTTO_ISSUE181_MESH_N")
    if raw is None:
        return DEFAULT_MESH_N
    n = int(raw)
    if n < 2:
        raise ValueError(f"TAVOTTO_ISSUE181_MESH_N 至少是 2: {n}")
    return n


def mesh_data(n: int) -> list[np.ndarray]:
    """三块 mesh 的数据。同一个 n → 逐位相同的数组（同一个 Generator 流）。"""
    rng = np.random.default_rng(SEED)
    fields = []
    gx, gy = np.meshgrid(np.linspace(-3, 3, n), np.linspace(-3, 3, n))
    for k in range(3):
        # 结构项（可复现的「信号」）+ 噪声：看起来像测量数据，而不是纯噪点，
        # 这样 colormap 的取值分布也接近真实图（影响 SVG 里 fill 的重复度）。
        signal = np.sin((k + 1) * gx) * np.cos((k + 1) * gy)
        fields.append(signal + 0.35 * rng.standard_normal((n, n)))
    return fields


def data_digest(n: int) -> str:
    """三块 mesh 数据的 sha256。**跨进程判定确定性**用的就是它。"""
    h = hashlib.sha256()
    for field in mesh_data(n):
        h.update(np.ascontiguousarray(field, dtype="<f8").tobytes())
    return h.hexdigest()


def build(n: int | None = None):
    """画出这张图并返回 Figure（不落盘——落盘归调用方/worker）。"""
    n = mesh_n() if n is None else n
    fields = mesh_data(n)
    edges = np.linspace(0, 1, n + 1)

    fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.6), constrained_layout=True)
    fig.suptitle("Synthetic large-mesh reproduction (issue #181)")

    labels = ("Channel A", "Channel B", "Channel C")
    for i, (ax, field, label) in enumerate(zip(axes.flat, fields, labels)):
        mesh = ax.pcolormesh(edges, edges, field, shading="flat", cmap="viridis")
        ax.set_title(label)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        if i == 0:
            # 色条是正常语义结构的一部分：hybrid preview 之后它必须还在
            fig.colorbar(mesh, ax=ax, label="Intensity (a.u.)")

    # 第四格刻意是普通矢量内容：hybrid 之后它不许被 rasterize
    ax = axes.flat[3]
    t = np.linspace(0, 10, 400)
    ax.plot(t, np.exp(-t / 4), lw=1.2, label="decay")
    ax.plot(t, np.exp(-t / 9), lw=1.2, ls="--", label="slow decay")
    ax.set_title("Vector control panel")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Signal")
    ax.legend(loc="upper right")

    return fig


def main():
    fig = build()
    # worker 里 savefig 被拦截（沙盒纪律），这一句只是「认领 stem」；
    # 独立运行时它会真的写一个很大的文件——所以别在仓库里裸跑。
    fig.savefig(f"{STEM}.pdf")


if __name__ == "__main__":
    main()
