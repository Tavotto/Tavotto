"""脚本与产物都在中文目录/中文文件名下（runner 会把它放到「图表 目录/」里）。"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([2, 1, 4, 3])
    ax.set_title("CJK path")
    fig.savefig("shape_cjk_path.pdf")
