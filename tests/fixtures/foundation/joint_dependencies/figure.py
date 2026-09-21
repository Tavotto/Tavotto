"""U04 合成 fixture ⑧：脚本开跑就要三个**只存在于测试 wheelhouse 里**的纯 Python 包。

`tavotto_test_alpha` / `tavotto_test_beta` / `tavotto_test_gamma` 由用例现造成 wheel
（`tests/support/dependency_repair.build_wheel`：模块体只有 `VALUE = 42` 与 `NAME`），项目的
`requirements.txt` 按 PEP 508 的完整形态声明它们——带 extra、带上界、带一条 marker 为假的、
外加一个**未选的组** `requirements-train.txt`。真值：y = VALUE · [1, 2, 3] = [42, 84, 126]。

原生参考：先把三个包装进一个 venv 再 `python figure.py`；本目录不该出现任何新文件。
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot
import tavotto_test_alpha  # noqa: E402
import tavotto_test_beta  # noqa: E402
import tavotto_test_gamma  # noqa: E402

value = tavotto_test_alpha.VALUE
assert tavotto_test_beta.VALUE == value and tavotto_test_gamma.VALUE == value
xs = [1, 2, 3]
ys = [value * x for x in xs]

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="o", label="y = 42x")
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.legend()
fig.savefig("figure.pdf")
