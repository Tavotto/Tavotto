# 前端诊断：状态快照与交互轨迹（2026-08-27，ADR 0016）

> 原文出自 `web/AGENTS.md`「前端诊断：状态快照与交互轨迹（2026-08-27，ADR 0016）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0016-diagnostics-v2-frontend-state-tracing.md`，改动前先读。
模块是 `web/src/diagnostics/`，业务代码**只 import `@/diagnostics`**。

- **权威判据不在这里**：诊断报的 `authority_variant` 一律委托
  `docs/rules/frontend/display-fallback-vs-geometry-authority.md` 的 `exactPanelRender`（ADR 0017）。诊断**绝不另立一份判据**——否则会出现
  「诊断说权威就绪、写路径当场拒绝」，两边各说各话。因此权威只有两种取值：
  **就是当前这一版，或者根本没有**；「来自别的变体的权威」这个概念不存在。
  ADR 0017 的追踪环（原 lib/authorityTrace.ts）已并入本模块，别再建第二个环。
- **只观察，不当真源**：诊断不参与任何业务判断，快照是**读**业务 store 得来的，
  不维护影子状态（影子状态会漂移，漂移的诊断比没有诊断更坏）。
  `recordDiagnosticEvent` 整体吞异常——诊断把一次编辑弄挂比没有诊断糟得多。
- **隐私是两道判据不同的防线**：`types.ts` 的可辨识联合挡编译期手滑（事件里写
  `text: element.label` 直接 TS 报错），`sanitize.ts` 的**逐事件字段表**挡运行期。
  序列化**遍历 schema 而不是输入的键**——多出来的字段是「根本没被读过」，
  不是「读了再丢掉」。**写入即脱敏**：环里物理上不存在未脱敏的数据。
- **`data-display-key` 是对外暴露面**：它落在 DOM 上（e2e 读它、用户也看得到），
  用的是 `diagnosticHash`。`diagnostics/privacy.test.ts` 是它不泄漏文件名与
  override 原文的唯一看护，别删。
- **定长 240 条、纯内存**：不写磁盘、不自动上传、不进 telemetry；只有用户点
  「导出诊断包」才 POST 给本机后端。**切项目要 `clearDiagnosticTrace()`**
  （已接进 `resetForNewProject`）——否则新项目的包会带着上一个项目的操作序列。
  `seq` 刻意不重置：编号缺口是「这里被清过 / 被环挤掉」的唯一线索。
- **不记 mousemove、不记每一帧预览**；document 摘要只在真状态边界算，
  且靠 immer 的结构共享 + WeakMap 缓存（`digest.ts`），改一个对象只 hash 一个。
- 看护：`web/src/diagnostics/*.test.ts` + `tests/test_diagnostics_bundle.py`
  （服务端第二道校验、ZIP、端到端隐私回归）。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 权威判据委托 `exactPanelRender`
- 只观察不当真源、吞异常
- 序列化遍历 schema 不遍历输入
- 定长 240 纯内存、切项目清环
