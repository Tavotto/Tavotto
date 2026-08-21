"""最常见的 AI 输出形态：pyplot 状态机 + plt.show()，一次 savefig 都没有。"""
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.xlabel("index")
plt.ylabel("value")
plt.show()
