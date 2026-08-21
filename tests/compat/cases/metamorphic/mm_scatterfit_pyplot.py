"""散点 + 拟合线——pyplot 状态机，无 savefig。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(3.8, 2.4))
ax = fig.gca()
X = np.linspace(0.0, 5.0, 20)
Y = 1.8 * X + 0.6
ax.scatter(X, Y + np.array([0.3, -0.2] * 10), s=18, label="data")
ax.plot(X, Y, color="#5B8C5A", label="fit")
ax.set_title("Scatter with fit")
ax.set_xlabel("dose")
ax.set_ylabel("response")
ax.legend()
plt.show()
