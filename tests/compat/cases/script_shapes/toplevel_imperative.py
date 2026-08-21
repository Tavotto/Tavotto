"""顶层直写，没有任何函数——脚本 import 的一刻就把图画完了。"""
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.6, 2.4))
ax.bar(["a", "b", "c"], [3, 5, 2])
ax.set_title("Top level")
fig.savefig("shape_toplevel.pdf")
