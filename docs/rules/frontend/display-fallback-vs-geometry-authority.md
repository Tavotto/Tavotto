# 显示回退 ≠ 几何权威（2026-08-26，issue #131；ADR 0017）

> 原文出自 `web/AGENTS.md`「显示回退 ≠ 几何权威（2026-08-26，issue #131；ADR 0017）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

**旧 SVG 可以继续显示；旧 manifest 不得作为几何写操作的权威输入。**
细则与理由在 `docs/adr/0017-display-fallback-vs-geometry-authority.md`，
动手前先读。要点：

* 两套 API，职责写在名字里。显示：`panelRender` / `usePanelRender` /
  `usePanelDisplayManifest` / `panelDisplayView`（可以退回 `latest[fileId]`）。
  几何权威：`exactPanelRender` / `exactPanelManifest` / `useExactPanelManifest`
  （**只认 `byKey[renderKeyOf(panel)]`**，且要求 `lastPatches` 与当前 overrides
  逐字相等、没被 markStale 标记）。
* **凡是读 `bbox` / `anchor` / `position` / `geometry` / `arrow_endpoints` /
  `follow_gids` / `geom_gid` / `size_mm` 之后要写文档的，一律走权威**：图内
  对齐分布、等宽等高、多选整组拖动、单文字拖动、axes 移动与缩放、成组缩放、
  命中测试、框选、选择框、吸附候选、orphan override 判定与清理。
  列元素 / 认 role / 画角标这类只读的继续用显示那份。
* `panelDisplayView` 是**判别联合**：`fallback` / `empty` 分支在类型上就没有
  `manifest` 字段。别把它改回「一个可选字段 + 一句注释」——issue #131 之前
  正是靠注释提醒的，没拦住。
* 权威缺席时**不是禁用一切**：画布照常显示上一张（不闪白）、命中层停摆、
  一个选择框都不画、`selectedGids` 不清空、入口置灰并说明「正在同步」，
  精确 manifest 回来后自动恢复。纯样式类（颜色/线宽/线型/alpha/visible）
  不依赖 bbox，继续走局部 SVG 预览，不受这道闸影响。
* **离散动作不许被连续手势吞掉**：`documentStore.commit` 在 `state.txn` 存在时
  会静默并入当前事务。对齐、分布、等宽等高、重置元素、清理 orphan、版本
  保存/恢复、写回历史恢复、undo/redo 执行前必须先
  `gestureCoordinator.finishActiveGesture()`。**光调 `endTxn()` 不够**——
  `useFieldGesture` 自己还有 open 标记、安静计时器、SVG 预览会话和挂起的定稿
  渲染，事务被外人收掉而 hook 不知情的话那些状态会一直悬着。
* **没有视觉位移就不写 override**：no-op 判据收在 `layoutBoxes()` 一处
  （round4 之后逐位相等 = 没有可表示的位移，阈值与写出去的 override 精度同源）。
  给「本来就在目标位置」的元素写一条等于当前值的绝对坐标，等于把标题/轴标签/
  图例从 matplotlib 自动布局里钉死，此后改字号改图幅它都不会再让位。
* override 的 upsert **原地改值**，不许 `filter(...)+push(...)`：override 数组
  的 JSON 就是变体键，顺序一变键就变 = 一次完全没必要的重渲染。
* 撤销的落点要还在：每个文件保留最近 4 档成功变体（有界），`latest` 按**请求
  序号**推进——乱序返回时旧变体只入库、不挪 `latest`，也不丢弃（同文件的另一个
  副本可能还等着它）。
* 布局版本预览按**版本自己的 overrides** 出图（`useVariantPng` →
  `preview_png`），出不来就退回磁盘图并明确标「近似预览」，**不许无提示地拿
  磁盘原图冒充版本视觉状态**。只给用户当前展开的那一份渲染。
* 看护：`store/geometryAuthority.test.ts`、`store/alignAction.test.ts`、
  `canvas/alignUndoConvergence.test.tsx`、`diagnostics/store.test.ts`（追踪环已并入
  诊断模块，ADR 0016）。
  测试里「这一版已经精确画好」用 `test/renderFixtures.ts` 的 `seedExactRender()`
  ——手写 `{manifest, status:'ready'}` 造出来的是真实渲染永远不会有的形状。
