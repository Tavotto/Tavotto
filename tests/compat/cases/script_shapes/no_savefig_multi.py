"""三张 pyplot Figure，一次 savefig 都没有。

fallback stem 必须确定、稳定、互不相同——中间那次 plt.close() 会让
「按 figure 号编号」的写法跳号，用户的 override 于是挂在不存在的 stem 上。
"""
import matplotlib.pyplot as plt

plt.figure(figsize=(3.0, 2.0))
plt.plot([1, 2, 3])
plt.title("first")

throwaway = plt.figure()
plt.close(throwaway)

plt.figure(figsize=(3.0, 2.0))
plt.plot([3, 2, 1])
plt.title("second")

plt.figure(figsize=(3.0, 2.0))
plt.plot([2, 3, 1])
plt.title("third")

plt.show()
