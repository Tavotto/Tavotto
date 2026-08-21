"""均值曲线 + 误差带——pyplot 状态机，无 savefig。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(3.8, 2.4))
ax = fig.gca()
X = np.linspace(0.0, 5.0, 30)
Y = np.sqrt(X)
ax.plot(X, Y, color="#B4473C", label="mean")
ax.fill_between(X, Y - 0.15, Y + 0.15, alpha=0.3, label="±sd")
ax.set_title("Error band")
ax.legend()
plt.show()
