"""面板脚本躺在子目录里。图库根与脚本自己的目录都要进 sys.path。"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 5, 3, 4])
    ax.set_title("Subdirectory panel")
    fig.savefig("shape_subdir.pdf")
