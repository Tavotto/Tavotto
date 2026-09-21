"""U05 合成 fixture ⑨：干净机器——脚本要一个**只存在于测试 wheelhouse 里**的纯 Python 包，而这台机器
没有任何可用的 Python（用例把发现链末端置空），Tavotto 得先自备一份私有 Python，再建受管环境装它。

`tavotto_test_alpha` 由用例现造成 wheel（`tests/support/dependency_repair.build_wheel`：模块体只有
`VALUE = 42` 与 `NAME`）。真值：y = VALUE · [1, 2, 3] = [42, 84, 126]。

原生参考：把包装进一个 venv 再 `python figure.py`；本目录不该出现任何新文件。
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot
import tavotto_test_alpha  # noqa: E402

value = tavotto_test_alpha.VALUE
xs = [1, 2, 3]
ys = [value * x for x in xs]

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="o")
ax.set_title("u05 private python")
fig.savefig("figure.pdf")
