# 普通交互性能探针（ADR 0075 增量）

> 2026-10-14：保持拖动探针 `tavotto-perf-probe/1` schema，不改文档或预设行为。

## 现场验证

1. 设置 → 诊断 → 给开发者 → 记录拖动性能 → 开始。
2. 在空白、50 对象、500 对象、复杂 SVG 四种文档中，分别多次插入内置科学符号（α / ² / μm）和预设。
3. 完成并保存报告，运行 `python scripts/perf_report.py report.json --html perf.html`。
4. 查「普通交互动作」一栏：`symbol.insert` / `preset.insert` 的同步 P50、P95、最大值，双 rAF P95 和三段耗时。

同步阶段是 `document.commit` / `selection.update` / `ui.status`；剩余未覆盖时间不能推断为字体问题。双 rAF 只代表到第二个动画帧回调的时间，**不是真实 GPU 呈现延迟**。字体回退、ResizeObserver、React commit、自动保存与窗口合成仍需要 Instruments / Web Inspector 继续追踪。

## 判定边界

- 关闭录制时全部入口直通，无新监听、计时、帧循环和分配。
- 每个 session 最多存 120 条通用动作，超出时原样执行；不记录字符、预设 id、文档文本、路径。
- 录制结束后异步的双 rAF 不再修改已交付报告；若结束得太早，报告中写 null 而不伪造数字。
- 与拖动探针同时开启时，拖动过程不重复建立普通动作，旧版报告没有 `actions` 仍可分析。
- UI 显示的 P95 是样本分位数，不足 20 次仅用于定位、不可作为回归硬门禁。
- 本 PR 不修改符号或预设的编辑逻辑；下一步需要在真实 WKWebView 上采样后才能实施优化。
