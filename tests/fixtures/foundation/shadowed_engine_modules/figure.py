"""U03 合成 fixture ⑦：本地实验室模块 + 与引擎重名的模块（FO19，issue #447）。

`import manifest` / `import overrides` 必须命中**本目录**的那两份（引擎的同名模块要
留在私有包里）；`lab_utils` 是本地包，不在 PyPI 上。三个 sentinel 写进标题文字：
`user-manifest|user-overrides|user-lab_utils`——引擎模块被错拿时 `manifest.WHO`
直接 AttributeError，图一张都出不来。真值见 `truth.json`：y = RUNS 的值 × SCALE。
"""

import lab_utils
import matplotlib

import manifest
import overrides

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

xs = [i for i, _ in enumerate(manifest.RUNS)]
ys = lab_utils.scaled([v for _, v in manifest.RUNS], overrides.SCALE)

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="o")
ax.set_title("|".join((manifest.WHO, overrides.WHO, lab_utils.WHO)))
fig.savefig("figure.pdf")
