"""曲线 + annotate 箭头——pyplot 状态机，无 savefig。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(3.8, 2.4))
ax = fig.gca()
T = np.linspace(0.0, 6.0, 40)
ax.plot(T, np.exp(-T / 3.0), color="#2F6FB2")
ax.annotate("decay", xy=(2.0, 0.51), xytext=(3.4, 0.80),
            arrowprops=dict(arrowstyle="->", color="#2A6F3C"))
ax.set_title("Annotated decay")
ax.set_xlabel("t (s)")
plt.show()
