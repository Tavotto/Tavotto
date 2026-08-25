# ADR 0010：Inspector 展示层（presentation registry）、右栏外壳与助手分离

日期：2026-08-25 · 状态：已接受 · 关联：docs/ux/INSPECTOR_REDESIGN.md

## 背景

右侧属性栏此前是 manifest 的直接投影：字段类型决定控件（enum → Select）、
引擎分组决定折叠结构、296–320px 的定宽决定了「字体/字号挤成无标签一行」。
1.0 前的 UX 审计（INSPECTOR_REDESIGN.md §1）确认了七类结构性问题，
其中两条直接违背可用性底线：1366×768 下左树与属性栏互斥；
高频属性折叠在「值偏离中性默认才展开」的组里。

## 决定

### 1. manifest 之上加一层前端展示模型，而不是改协议

`web/src/components/inspector/presentation/` 是新的**展示注册表**：
按角色决定字段的层级（primary / more / advanced）、顺序与控件形态
（`line-style` / `marker` / `colormap` / `legend-position` / `side-toggles` /
`font` / …）。manifest 仍是**能力与取值的唯一权威**——注册表只回答
「摆在哪、长什么样、叫什么」，字段 manifest 里没有就绝不渲染，
注册表不认识的字段进 more/advanced 兜底、原文显示，一个都不丢。

否决的替代方案：让引擎在 manifest 里发展示元数据（priority/control）。
那会把 UI 决策焊进 worker 协议与写回校验的 manifest 比对里，
且每次调界面都要动引擎 + golden vectors。展示属于前端。

### 2. 右栏 320–480px（默认 360），断点 wide 降到 1280

- `RIGHT_MIN/MAX` 从 296/320 改为 320/480，`PREFS_VERSION` 升到 2：
  旧 blob 里 ≤320 的宽度一次性迁到 360（那是旧上限的产物，不是用户偏好）；
  v1 用户主动改过的 rightOpen/rightPinned **不再**被 v2 迁移触碰。
- `WIDE` 从 1440 降到 1280：1366×768 是最常见的笔记本档位，左树 + 画布 +
  属性栏必须共存。1024–1279 维持互斥停靠（双停靠会把画布压破 600px），
  <1024 维持覆盖式抽屉。

### 3. 助手移出对象上下文的 tab 行，但暂不拆成独立 Dock

属性/画布是「当前对象/当前文档」的上下文，助手是独立工作流。tab 行收敛为
两页；助手成为右栏头部带运行状态点的独立入口（仍渲染同一份 AssistantPanel）。
`autoShowProperties` 改为**选中对象一律切回属性页**——助手会话状态在
aiStore 里，切走不丢。

暂不把 AssistantPanel 拆成独立 Dock/抽屉的原因：App 外壳的三栏布局、
usePresence 动效、narrow 断点的覆盖层与 e2e 都围绕「右侧单一 aside」搭建，
拆成第四个停靠面时这些全部要重排；而本轮的用户价值（选中对象立即见属性、
助手不占属性入口）用「独立入口 + 选中即回属性 + 运行状态点」已经全部拿到。
Dock 化留给 post-1.0（见 docs/1.0-release-readiness.md 的 backlog 惯例）。

### 4. 折叠模型：三层，取代「十几个小组 + 中性值判据」

primary 永远展开；more 是唯一的中频折叠区，展开状态**按角色**持久化在
`tavotto.inspector`（localStorage，不进文档 schema）；source & advanced
默认关闭，收纳脚本/写回/历史/gid/诊断。`groupHasContent`（中性值判据）
只保留给未建档角色的兜底。折叠着的 more 在标题右侧显示「N 项已修改」。

## 不变式（本 ADR 不改变的东西）

worker 协议、manifest 结构、override 应用语义、写回三段事务、全量重放、
undo/redo 事务模型、`useElementWriter`/`useFieldGesture` 的写入通道、
文档 schema——一个字节不动。所有新控件写入的仍是 Matplotlib 原始 enum 值。
