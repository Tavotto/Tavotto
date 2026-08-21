"""双曲线折线图 + 图例——pyplot 状态机，无 savefig。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(3.8, 2.4))
ax = fig.gca()
X = np.linspace(0.0, 6.0, 40)
ax.plot(X, np.sin(X), label="sin", color="#2F6FB2")
ax.plot(X, np.cos(X), label="cos", color="#B4473C", linestyle="--")
ax.set_title("Two curves")
ax.set_xlabel("x (rad)")
ax.set_ylabel("amplitude")
ax.legend()
plt.show()
