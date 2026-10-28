# 执行记录（Waterfall）：只画后端的执行追踪（ADR 0151 PR3）

> 2026-10-27 随 Execution Trace PR3 新增。这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行（由下面的 `agents-route` 块生成）。
> 看护用例：`src/lib/execTrace.test.ts`、`src/components/ai/traceWaterfall/traceWaterfall.test.tsx`、`.../replay.test.tsx`、`src/components/ai/traceDetails.test.tsx`、`src/components/ai/historyRow.test.tsx`、`e2e/exec-trace.spec.ts`（真后端，中英各一遍）、
> `e2e/exec-trace-perf.spec.ts`（大 Trace 性能预算：不进黄金路径，经 `playwright.perf.config.ts` 在 performance-agent workflow 里跑；本机 `cd web && npx playwright test -c playwright.perf.config.ts e2e/exec-trace-perf.spec.ts --project=chromium`；首渲的长任务不计入实时窗口）；后端端点 `tests/test_exectrace_api.py`。

<!-- agents-route
sheet: web/AGENTS.md
order: 225
改到: `lib/execTrace.ts`、`components/ai/traceWaterfall/*`、`components/ai/TraceDetails.tsx`、`store/traceDialogStore.ts`、`fetchExecTrace`
主题: 执行记录 Waterfall（ADR 0151） → `execution-trace-ui.md`
必守要点: 转换只在 `lib/execTrace.ts`；不判定、不补 Span、不拿相邻时间算耗时；不知道就写 —；权威是 REST 快照
-->

- **数据只有一条路**：`fetchExecTrace(kind, ref, pj)` → `/api/exec-trace`（按项目认领，别的项目的 id 读作「没有」）→ `lib/execTrace.toWaterfall()`
  → `traceWaterfall/types.ts` 的展示模型 → `TraceWaterfall` 只画。组件里不出现第二份状态映射、不读 DTO。
- **诚实规则**（组件与适配层都要守）：`end` 缺 = 还在跑；`timing='point'` 只画点不画宽条；`unmeasured` 只画虚线框；`reported` 照常画、
  popover 说明来历；`cost` / token 是 `null` 就写「—」不写 0；有不可观察的 Agent（`opaque`）时模型调用数写「—」（数不出来）；工具调用数只数经我们自己入口观察到的那些，当**下界**写 `≥N`，一个都没观察到才写「—」，看得全就是准数；斜纹只属于真的等待 Span（`kind==='wait'`），被子智能体盖住的时间画 `delegate`（淡泳道），不是「等」；
  失败计数不把根的连带失败重复算；「首个失败」与「最慢」是两个不同的标记，永不由最慢推出根因。
- **入口**：窄栏（转录里每一轮、智能修复卡的折叠详情里）只放一行「执行记录」，点开是一张**宽的浮动卡**（`TraceDialog`，共用 `ui/Dialog` 的 `shell` 宽度；
  窄栏放不下 Waterfall）；`TraceDetails` / `TraceDialog` 按 `kind` 泛化（`ai_task` 用 AI 会话 id，`fix_session` 用修复的准备会话 id `ref.session_id`）。
  智能修复卡的入口随 `entry.fixId` 出现（跑着 / 等着 / `done`）；`fixed` / 取消 / 交接时条目被清掉，入口随卡片一起消失。
  任务历史的一行没有「技术详情」折叠：元信息行的悬浮 / 点击详情卡里有脚本、快照、失败原因、执行记录缩略条与「查看执行记录」，按钮直接开同一张宽卡——**先收起任务历史弹层与详情卡 / 模型卡再开**：宽卡的状态在 `store/traceDialogStore`、由弹层外面常驻的 `TraceDialogHost`（`TaskHistoryPopover` 里）画，不挂在那一行里（行随弹层卸载；`z-popover` 比 `z-dialog` 高，留着会压在 Waterfall 上）。
  浮层（`SpanPopover`）在对话框里挂进对话框本身（Radix 模态会吞掉挂在 body 上的浮层的指针事件，还把点它当成点外面）。不加顶级导航。
- **实时**：运行中且宽卡打开时每 1s 取一次权威快照（不新开 SSE 事件，SSE 满了会丢）；「此刻」每 250ms 走一格，只经 `now` 传下去；
  运行中的时间轴取阶梯值（`traceDomain`），行 `memo` 只在含未结束 Span 时随 `now` 重画——预算在 `exec-trace-perf.spec.ts`。
- **回放**（已结束的 Trace）：宽卡打开就从 0 自动播一遍，整段压缩到约 4 秒（`自动` 档；比 4 秒短的按实际时间，另有 1× / 10×），
  `prefers-reduced-motion` 下不自动播、停在终点；回放条（`data-trace-playback*`）在指标条下：播放 / 暂停、重播、`0:12 / 0:33`、速度、可拖的进度条——
  进度条宽度 = 总时长占时间轴的比例，拇指与现在线同位。运行中的 Trace 走实时、没有回放条；跑着时开的卡收尾后出现回放条但不自动播。
  「某一刻的样子」只是对同一份数据的**视图变换**（`traceWaterfall/utils` 的 `endOf` / `isRunning` / `settledAt` / `notStartedAt`，`model.replay` 打开）：
  没开始的行不画，开始了没结束的画到此刻并当作还在跑，已结束的画全；不编 Span、不改总时长；还没结束的调用，结局（失败色 / 失败角标 / 最慢 / 费用）**此刻不露**。
  指标条回放时仍是**整份**的总数（不随进度变，部分和会把没结束的调用的费用写早了）。行 `memo` 回放时按 `rowUnchangedBetween`（这一格对这行的时间范围有没有影响）跳过，预算在 `exec-trace-perf.spec.ts` 的回放用例。
- **状态**：加载 / 运行中 / 完成 / 失败 / 取消 / 中断（`interrupted` 说明之后不知道）/ 截断（省略 N 条）/ 不完整 / 没有记录 / 追踪关闭 / 版本不支持
  （`supported()` 主版本号）/ 读取失败（重试），各自 `data-ai-trace-state`。
- **视觉**：语义 token（泳道走 accent 族、失败 danger、取消 / 未知中性），图标只来自 `components/ui/icons` 与设置里的 `AgentIcon`；
  连线是唯一的内联 `<svg>`（`iconography.test` 登记豁免 1 处）。选择器只认 `data-trace-*` / `data-ai-trace*`。
- **行与图标**：名称列的主文字：工具行是工具 id（种类由图标说，全名在 `title` 与详情卡里），其余是阶段名；图标不带框，一律图标集里的实心孪生
  （`filled`）按种类上语义色，厂商标用无框的 `AgentIcon`。吸顶行底色一直不透明；已结束的时间轴按像素给末尾时长小字留边（`traceDomain`）。
