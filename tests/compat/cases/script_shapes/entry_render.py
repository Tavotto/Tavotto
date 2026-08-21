"""入口叫 render()。"""
import matplotlib.pyplot as plt


def render():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2], [1, 0, 2])
    ax.set_title("entry=render")
    fig.savefig("shape_entry_render.pdf")
