# ADR 0134：CONSENT_VERSION 2 → 3——导出终态事件 `export_result` 与诊断报告的可选错误摘要

日期：2026-10-16 · 状态：已接受（维护者裁决）· 关联：[0041 §4](0041-codex-ai-refresh-and-telemetry-integration.md)（1 → 2 的先例）、
[0019 §十二](0019-controlled-dependency-repair.md)（EVENTS 扩容要升版的理由）、[0118 发送问题反馈](0118-diagnostics-send-report.md)；
规则全文 `docs/rules/backend/telemetry.md`、`docs/rules/backend/diagnostics.md`。

## 背景

本批带来两个新的采集范围：

1. **`export_result`**：每个被受理的导出作业终局时一条（done / partial / failed / cancelled，加请求格式布尔与阶段 / 粗错误类闭集）。
2. **可选错误摘要**：用户在诊断报告上**明确点了发送**时，若匿名统计开着，报告服务可把一条不含内容的摘要
   （同意快照 + 内部组件 / 操作 / 稳定错误码）转成第三方错误追踪事件。它走报告服务（`engine/diagsummary.py` →
   `POST /v1/reports/init` 的 `error_summary`），**不走**遥测事件通道。

此前这一分支的草稿曾判「`export_result` 与已有事件同类，不升版」。维护者推翻了它：两个范围一并升版，一次，让所有人重新表态一次。

## 决策

- `telemetry.CONSENT_VERSION` 从 2 升到 3，**只升这一次**，同时覆盖上面两项。经既有机制 `needs_reconsent`：
  v2 的同意当场失效（`enabled()` 为 false，**一个事件都不发，包括 `export_result` 和老事件**），界面重新征求。
- **重新同意不换 `install_id`**；**说过「不」的人不被再问**（`needs_reconsent` 只对 `enabled` 且版本旧的成立），
  `unset` 仍是 `unset` 且不生成 `install_id`；`TAVOTTO_NO_TELEMETRY=1` 压过一切、从不弹框。三条都有用例
  （`tests/test_telemetry.py`、`tests/test_telemetry_v3_consent.py`）。
- 摘要的同意快照 `{telemetry_consent, no_telemetry_env, scope_version: 3, purposes: ["error_summary"]}` 在**最终点击发送那一刻**
  生成，不含 `install_id` 与时间戳；统计没开 / 旧版同意 / 硬开关 = 不带摘要，只走自有报告。没有合格的内部事实（只认引擎登记的
  导出 / 依赖作业的稳定错误码；用户脚本的运行与准备不算）也不带。点「发送诊断」**不表示**同意未来自动上报。
- **部署顺序**：服务端 `extra="forbid"`，所以必须**先部署 infra #28（diagnostics-api 1.1.0），再发这个客户端**。
  客户端另有保险：带摘要的 init 若被当作请求形状错拒（`bad_request`），去掉摘要重发一次，报告照样送达；本会话此后不再带。
  服务端的摘要开关生产默认 off，所以即使客户端带了，在运维开启之前也不会产生任何第三方事件。
- 代理侧 `export_result` 白名单（`services/telemetry_proxy`）同样要先于客户端部署，否则被 400 静默丢弃。

## 后果

- 升级后的第一次启动，已同意 v2 的用户会被再问一次；界面文案列出新增的两项。
- 隐私说明（`docs/privacy.md`）、事件表（`docs/analytics/telemetry-events.md` v3 一节）、同意框文案与 `TELEMETRY_DISCLOSED_EVENTS` 同步。
- 已提交并进入服务端处理的摘要不保证撤回；关闭统计只停止之后的客户端统计、阻止之后的报告再产生摘要。UI / 文档不承诺实时远程撤销。
