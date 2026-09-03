# ADR 0036：多选浮动 Context Bar、共享排列参照与主选语义

状态：**Accepted**
日期：2026-09-02
相关：[0032 属性能力层](0032-typography-capability-layer.md)（浮动栏与属性页共用同一份
adapter / action 的先例）、[0016 前端诊断](0016-diagnostics-v2-frontend-state-tracing.md)（本地活动信号
**不是**诊断事件，也不是遥测），[0035 坐标轴刻度直接操作](0035-axis-tick-direct-manipulation.md)
（「多处入口同源 = 只有一份」的同一条纪律），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 多选后高频排列动作在哪 | 选出**两个及以上画布对象**时，联合选区上方出现紧凑浮动栏：计数、参照三选一、六向对齐、水平 / 垂直分布、等宽 / 等高、成组（选区里有组时多一个取消成组）、「更多」。来源不区分（Shift 点、框选、⌘A、图层树、程序化选择） |
| 它是不是第二套排列系统 | **不是。** 每颗按钮只发意图，落地全部走 `store/actions`（`alignSelectedTo` / `groupSelected` / `ungroupSelected`）——与右侧 `ArrangeSection` 同一个函数、同一条历史标签、同一份锁定 / 成组判据；按钮表（图标 / 顺序 / 最少对象数）在 `inspector/arrangeButtons.ts` 一份 |
| 参照（选区 / 画布 / 主选）存在哪 | `store/arrangeStore`：UI 会话状态。不进 `FigureDocument`、不进撤销、不 persist、切文档不重置；存枚举不存翻译。此前它是 `ArrangeSection` 的模块级变量——浮动栏要读就得再抄一份 |
| 主选是谁 | **`selection.ids` 末位**（既有语义，点击已选中的对象会把它提为末位）。多选时它的轮廓 2 px、其余 1 px，并挂 `data-primary-selection`；联合框挂 `data-multi-selection-bounds`。不大面积改色、不加第二套几何 |
| 浮动栏锚在哪 | 联合选区（`boundsOf(selected)`）经**与 OverlaySvg 同一份换算**（`mmToViewX/Y` + `mmToPx` + 视口原点）得到窗口坐标，**不查 DOM**。zoom / pan / 侧栏 / 窗口 / 对象移动都重贴 |
| 上方放不下 | 翻到下方；再不够贴窗口底边。左右夹在停靠的侧栏之间（放不下贴左）。规则是纯函数 `context-bar/position.ts` |
| 宽度不够 | 两道判据：静态阈值（两侧之间 < 600 px）**或**量出来比可用宽度还宽 → 压缩成「对齐 / 分布 / 尺寸」三个弹层入口 + 成组 + 更多。工具条盒子用 `width: max-content`，否则 `fixed` 盒子会被「left 到视口右沿」的可用宽度压扁，量到的不是自然宽度 |
| 什么时候不出现 | 图内编辑态（含 shift 加选标注的混排选区——那归 ElementInspector 的对齐工具条）、裁剪、文字编辑、QuickEdit、非选择工具、narrow 覆盖式抽屉开着、模态弹窗 / 命令面板盖着；任何交互（move / resize / marquee / pan / guide / draw / crop）期间与 pointerdown 期间隐藏，结束后选区仍 ≥ 2 就回来 |
| Esc | 焦点在栏内：拦下事件，只关本次显示，选区不动；焦点在外：不拦（全局 Esc 照常逐层退），本次显示同样关。选区一变重新允许出现；仅缩放 / 平移不解除也不重复关 |
| 锁定对象 | `alignSelectedTo` 现在与拖动同一套判据（`movableTargets`）：锁定对象与含锁定成员的组**不动**，但**仍算进选区参照框**（锁的是位置，不是参与排列的资格）；全部锁定时不进历史、提示先解锁 |
| 离散动作与手势 | 对齐 / 成组 / 取消成组执行前 `finishActiveGesture()`——此前没有，字号还在安静计时里时点对齐会被并进同一条历史 |
| 后续新手提示要的信号 | `lib/activity.ts`：`tavotto:activity` 本地 CustomEvent，`detail` 只有闭集 `kind` + 枚举 + 计数（`selection.aligned` / `selection.grouped` / `selection.ungrouped`），无对象 id、无文字、无文件名；派发失败被吞。核心 action **不 import** 任何 onboarding 模块；它不是遥测 |
| 分布 disabled 怎么说明 | 少于三个对象时按钮 `aria-disabled` + 仍可聚焦 + tooltip 说明「≥3 个对象」（原生 `disabled` 不发 pointer 事件，说明会一起消失）；点了不动 |
| 气泡与点击 | Tooltip（含 Radix 定位外壳）`pointer-events: none`：弹层自动聚焦第一个分段项时它的气泡停在下一排按钮上，真浏览器里点上去什么都不发生 |
| 磁盘格式 | **不升版**，不新增文档字段 |

## 1. 背景

右侧属性页早就有对齐 / 分布 / 等宽等高 / 成组，但多选之后这些动作离鼠标很远，
而且参照三选一收在属性页里，普通用户发现不了（审计 P9 的多选版）。单选已经有
贴着选择框的 `ContextBar`，多选时它直接不出现。

## 2. 结构

```text
web/src/canvas/context-bar/
  ContextBar.tsx        外壳：目标解析（element / object / multi）、出现与让位、落位、Esc、
                        拖动隐藏、portal。对外仍是 export function ContextBar()
  SingleObjectBar.tsx   原来的画布对象快捷属性（原样搬家）
  ElementBar.tsx        原来的图内元素快捷属性（原样搬家）
  MultiSelectionBar.tsx 多选：计数 / 参照 / 对齐 / 分布 / 尺寸 / 成组 / 更多；full 与 compact 两档
  position.ts           纯函数：placeToolbar / sidebarInsets / barVariant / selectionScreenRect
  elementQuick.ts / openArrange.ts / text.ts / shared.tsx   小助手
web/src/store/arrangeStore.ts          参照的唯一持有者
web/src/components/inspector/arrangeButtons.ts   按钮表的唯一出处
web/src/lib/activity.ts                本地活动信号
```

## 3. 不做的事

* 不把 group 当成一个整体对齐（成员各自对齐到参照框，与属性页此前行为一致）；
  组的整体语义留给布局组（`layoutGroups`）。
* 不做 coachmark（Prompt 21）；本轮只留稳定锚点与活动信号。
* 不给分布加「以锁定对象为固定锚」的算法：锁定对象被排除在外，其余在选区框里
  等距。写进限制。

## 4. 看护

`canvas/context-bar/position.test.ts`（13）、`multiSelectionBar.test.tsx`（42）、
`canvas/primarySelection.test.tsx`（5）、`store/alignSelectedTo.test.ts`（17）、
`store/arrangeStore.test.ts`（2）、既有 `canvas/contextBar.test.tsx`（多选那条改成
「换成多选栏」）。变异反证 14 条全红（`TEST_MATRIX.md`）。真浏览器（chromium）
跑过完整档 / 压缩档 / 弹层内操作 / 拖动隐藏 / 参照与属性页同步，截图在会话 scratchpad。
