# ADR 0195：脚本写死交互（GUI）后端时，safe 与 native 都中和它

日期：2026-11-19 · 状态：**Accepted**（维护者 2026-11-19 裁决）
相关：[0014 safe / native 执行档](0014-safe-native-execution-profiles.md)、[0020 native matplotlib bridge](0020-native-matplotlib-bridge.md)、
[0153 跳过读不了的 import 位置](0153-skip-unreadable-import-locations.md)（同类的有意偏离）；规则全文 `docs/rules/backend/execution-entries.md`「中和写死的 GUI 后端」。

## 动机

Tavotto 永远不弹窗口。用户脚本里常见 `matplotlib.use("TkAgg")` / `"Qt5Agg"` / `"MacOSX"` 再 `plt.show()`。没有 tkinter / Qt 的环境里它以
`missing_dependency` 失败（评测里内置 AI 为此换 Python、弹 9 张卡、跑 16 分钟仍失败，换环境救不了）；有的环境里 native 的 GUI 后端还可能进主循环卡住。

## 决定

维护者 2026-11-19 裁决：**只对 GUI 后端**，脚本要求切到它时 Tavotto **不切换**，保持非交互后端（safe 本来是 Agg；native 取显式的非交互后端，没有就 `agg`），并记一条只含后端名的事实。
Agg / pdf / svg / ps / cairo / `module://…` 等非交互或判不出的后端名原样尊重。

* **判据只有一处**：`bridgeboot.GuiBackendNeutralizer.is_gui_backend`，以 matplotlib 自己的后端登记表（`backend_registry` 的 INTERACTIVE 过滤；旧版 `rcsetup.interactive_bk`）为准，大小写不敏感；读不到登记表 = 判不出 = 不动。
* **装配点只有一个**：`bridgeboot.install_gui_backend_neutralizer()`。包装两个入口：`matplotlib.use` 与 `pyplot.switch_backend`（pyplot 首次建图时自己也经它按 `rcParams["backend"]` 切后端，所以 `rcParams["backend"]`、`MPLBACKEND`、matplotlibrc 里写 GUI 后端也被挡住）。
  safe worker 在 `matplotlib.use("Agg")` 之后装；native 经 `PostImportHook` 在 `matplotlib` / `matplotlib.pyplot` 载入完成的那一刻装，**不提前 import matplotlib**。
* **native 的 `plt.show()`**：原有 show 钩子从不调用真 show（进屏障或立即返回），所以不进 GUI 主循环；本 ADR 只加用例钉住。
* **`matplotlib_patched`**：包装先于可信方法面基线冻结（safe：`freeze_trusted_surface` 之前；native：基线在首次存盘记账时才拍，晚于 import 钩子），pyplot 则属「首次见到的模块」、认引擎目录来源，所以不会被判成脚本 patch 了 matplotlib，图仍可完整编辑。
* **留痕**：worker.log 的 `[gui-backend] {"backend": "tkagg"}`（safe）+ 回执 `runtime.gui_backends_neutralized`（两条入口；native 的 `--report` 同名字段）。只有登记表闭集里的后端名，没有路径；`receipt.project_gui_backends` 是公开投影的唯一出处。

## 有意偏离

native 的契约是与 `python fig.py` 逐字一致；本条在 GUI 后端这一点上偏离。偏离只发生在「脚本要窗口」那一刻，而 Tavotto 不开窗口。非 GUI 后端不在偏离范围。

看护：`tests/test_gui_backend_neutralized.py`。
