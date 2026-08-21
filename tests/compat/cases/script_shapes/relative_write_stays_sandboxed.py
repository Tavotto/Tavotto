"""脚本用相对路径**写**中间文件，再读回来。

写必须落在沙盒里（绝不污染用户项目），而读要先看沙盒里那一份——
「只读回退」不能把脚本刚写出来的东西顶掉。
"""
import matplotlib.pyplot as plt


def main():
    with open("scratch_values.txt", "w", encoding="utf-8") as fh:
        fh.write("7 5 3 1\n")
    with open("scratch_values.txt", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split()]
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(ys, marker="d")
    ax.set_title("Sandboxed write")
    fig.savefig("shape_sandbox_write.pdf")
