# ADR 0089：多选时浮动栏上的快速排版——图内同类多选出栏、画布多选文字加字号 / 颜色

状态：**Accepted**
日期：2026-09-26
相关：[0036 多选浮动 Context Bar](0036-multi-selection-context-bar.md)（外壳、落位、让位、共享参照）、
[0032 属性能力层](0032-typography-capability-layer.md)（浮动栏与属性页共用同一个排版适配器）、
[0082 属性页按页面 pt 显示](0082-inspector-shows-page-pt.md)（字号进出都是页面值）。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 起因 | 0.17.0 Beta 用户：「多选两个相似元素会有一个悬浮窗，快速排版（左对齐等）、快速设字号颜色，这个版本没有了」。复查结论是**从未存在过这个组合**，不是回归：图内多选从 v0.11.0 起就不出浮动栏（`ContextBar` 只认 `gids.length === 1`），画布多选栏从来没有字号 / 颜色。v0.16.0 与 0.17.0 在真实浏览器里逐项对照，表现一致。按用户的直接需求补上这项能力 |
| 图内多选（两个及以上图内元素）出不出栏 | **出**。`ContextBar` 新增目标 `elements`，内容在 `context-bar/ElementMultiBar.tsx` |
| 图内多选栏给什么 | 计数；**对齐**（六向，可对齐目标 ≥ 2 时；几何权威没就位时整排置灰、不消失）；**快速排版**（字体 / 字号 / 加粗 / 斜体 / 颜色，选区是同一文字家族时）；「全部属性」。两样都给不出（例如图例项 + 曲线）→ 不出栏 |
| 「给什么」的判据在哪 | `elementMultiPlan()` 一处；`ContextBar`（出不出）与 `ElementMultiBar`（画什么）读同一份，不各判一遍 |
| 对齐怎么落地 | 按钮只发意图：`alignSelectedPanelElements`——与 ElementInspector 对齐区同一个函数，点击那一刻从 store 现取几何权威（issue #131）。位置由容器决定的元素（图例项、刻度）不可对齐，只选它们时对齐行不出，不摆一排点了没用的按钮 |
| 排版怎么落地 | 与属性页 `styleBatch` **同一条判据**（`isTextLikeSelection`）、同一个适配器（`useFigureTypography` + `FIGURE_TEXT_BATCH_PROPS`，不含水平对齐）：一次改动 = 一次 `setOverrides` = 一条历史，撤销一次全组回去。混进 Shift 加选的画布标注时样式整组不给（跨 writer 原子写入仍是延后项，与 ElementInspector 的 `mixedWithAnnotations` 同一个理由），对齐照旧 |
| 画布多选 | 选区**全是画布文字**时，多选栏计数后接一行快速排版（`useCanvasTypography` 吃的就是数组，一次 `updateObjects` 一条历史）。混着面板 / 标注时不给 |
| 控件是不是第二份 | **不是。** 浮动栏上的文字控件只有 `context-bar/textQuick.tsx` 的 `TextQuickControls` 一份：图内 / 画布、单选 / 多选四处都画它，数据来自各自的适配器。mixed 时字号留空写「多个值」，色块取第一个目标的真实颜色 |
| 取色 | 取色盘拖着走会发一串 change：走适配器的连续写入（`write(…, immediate)` + 手势），整轮合成**一条**历史，失焦 / 安静计时收尾——不是每帧一次 commit |
| 右栏停靠时 | 规则照旧：文字控件按单选那条判据（`textBarCompact`）缩成字号 / 加粗 / 斜体，字体下拉与取色器让给右栏；画布多选栏的 T29 收缩形态不变 |
| 按下即藏 | 除 `pointerup` 外也听 `pointercancel`：系统接管指针时没有 pointerup，工具条会一直藏到下一次松手。**推测性加固**，没有用户现场复现 |
| 磁盘格式 | **不升版**，不新增文档字段 |

## 看护

- `web/src/canvas/context-bar/elementMultiBar.test.tsx`：出不出、给什么、写到哪、几条历史（图内与画布各一组），
  取色一轮一条历史，停靠缩减，`pointercancel`。
- `web/e2e/multi-selection-bar.spec.ts`：真实浏览器里浮动栏出现且在视口内；画布多选左对齐真的对齐；
  图内两个图例项改字号，真 matplotlib 重画的 manifest 里两个都是新值、画面变大，撤销一次回原样，重做后刷新仍是新值。
