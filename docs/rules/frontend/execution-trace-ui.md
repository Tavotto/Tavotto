# 执行记录（Waterfall）：只画后端的执行追踪（ADR 0151 PR3）

> 2026-10-27 随 Execution Trace PR3 新增。这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行（由下面的 `agents-route` 块生成）。
> 看护用例：`src/lib/execTrace.test.ts`、`src/components/ai/traceWaterfall/traceWaterfall.test.tsx`、`e2e/exec-trace.spec.ts`（真后端，中英各一遍）、
> `e2e/exec-trace-perf.spec.ts`（大 Trace 性能预算：不进黄金路径，经 `playwright.perf.config.ts` 在 performance-agent workflow 里跑；本机 `cd web && npx playwright test -c playwright.perf.config.ts e2e/exec-trace-perf.spec.ts --project=chromium`；首渲的长任务不计入实时窗口）；后端端点 `tests/test_exectrace_api.py`。

<!-- agents-route
sheet: web/AGENTS.md
order: 225
改到: `lib/execTrace.ts`、`components/ai/traceWaterfall/*`、`components/ai/TraceDetails.tsx`、`fetchExecTrace`
主题: 执行记录 Waterfall（ADR 0151） → `execution-trace-ui.md`
必守要点: 转换只在 `lib/execTrace.ts`；不判定、不补 Span、不拿相邻时间算耗时；不知道就写 —；权威是 REST 快照
-->

- **数据只有一条路**：`fetchExecTrace(kind, ref, pj)` → `/api/exec-trace`（按项目认领，别的项目的 id 读作「没有」）→ `lib/execTrace.toWaterfall()`
  → `traceWaterfall/types.ts` 的展示模型 → `TraceWaterfall` 只画。组件里不出现第二份状态映射、不读 DTO。
- **诚实规则**（组件与适配层都要守）：`end` 缺 = 还在跑；`timing='point'` 只画点不画宽条；`unmeasured` 只画虚线框；`reported` 照常画、
  popover 说明来历；`cost` / token 是 `null` 就写「—」不写 0；有不可观察的 Agent（`opaque`）时模型 / 工具调用数写「—」；
  失败计数不把根的连带失败重复算；「首个失败」与「最慢」是两个不同的标记，永不由最慢推出根因。
- **入口**：转录里每一轮的「执行记录」折叠（默认收起、展开才请求）、任务历史一行的「技术详情」里同一个组件（刷新 / 重启后已结束的那一轮只在这里，
  从后端保留期内的落盘快照恢复）。不加顶级导航。
- **实时**：运行中且展开时每 1s 取一次权威快照（不新开 SSE 事件，SSE 满了会丢）；「此刻」每 250ms 走一格，只经 `now` 传下去；
  运行中的时间轴取阶梯值（`traceDomain`），行 `memo` 只在含未结束 Span 时随 `now` 重画——预算在 `exec-trace-perf.spec.ts`。
- **状态**：加载 / 运行中 / 完成 / 失败 / 取消 / 中断（`interrupted` 说明之后不知道）/ 截断（省略 N 条）/ 不完整 / 没有记录 / 追踪关闭 / 版本不支持
  （`supported()` 主版本号）/ 读取失败（重试），各自 `data-ai-trace-state`。
- **视觉**：语义 token（泳道走 accent 族、失败 danger、取消 / 未知中性），图标只来自 `components/ui/icons` 与设置里的 `AgentIcon`；
  连线是唯一的内联 `<svg>`（`iconography.test` 登记豁免 1 处）。选择器只认 `data-trace-*` / `data-ai-trace*`。
