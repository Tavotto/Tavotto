"""目录名与文件名里都有空格（runner 放到「my figures/spaced panel.py」）。

文件名带空格意味着它不是合法的模块名，只能走 `entry="__main__"`（runpy）——
这正是真实用户会撞上的那一档。
"""
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.6, 2.4))
ax.plot([4, 3, 1, 2])
ax.set_title("Spaced dir")
fig.savefig("shape_spaced_dir.pdf")
