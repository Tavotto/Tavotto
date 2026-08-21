"""入口是作者自己起的名字。worker 就是 getattr(module, entry)()，
任何合法标识符都行——把入口锁死成 main/render 会让这类图库整个用不了。"""
import matplotlib.pyplot as plt


def build_figure():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([3, 1, 2], color="#B4473C")
    ax.set_title("custom entry")
    fig.savefig("shape_custom_entry.pdf")
