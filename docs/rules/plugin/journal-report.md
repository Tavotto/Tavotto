# Agent 提交期刊线索：`tavotto_report_journal_issue`（ADR 0213 三期）

> 2026-11-27 新增。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。线缆契约、入库流水线、回信见 ADR 0213；发信管线见 ADR 0145 与 `engine/contactsend.py`。
> 看护用例：`tests/test_mcp_report_journal_issue.py`（零网络 / 两步确认 / 令牌绑内容 / 开关 / 参数 / 本机取值 / 遥测）、
> `tests/test_mcp_error_next.py`（码登记）、`tests/test_mcp_usage.py`（走 `other` 桶）、`tests/test_mcp_resolver.py`（`NORMAL_TOOLS`）。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 66
改到: `tavotto_report_journal_issue`、`bridge.report_journal_issue()`
主题: Agent 提交期刊线索 → `journal-report.md`
必守要点: 未确认零网络；令牌绑内容；现值只取本机目录；不收数值；失败不重试
-->

- **同一条通道，不写第二份**：发送走 `contactsend.start`（`kind=journal`），校验走 `contactsend.build_draft` → `clean_journal`；
  回信与「我的来信」与桌面「这条不对」同一份本机记录。桥只翻译，`contactsend` 是**可选引擎模块**（只经 `_optional_engine`）。
- **先问再发（照 ADR 0205 的 `explicit_confirm`）**：不带有效确认的调用**不联网**（不调 `start`、不碰 `diagsend._send`），回
  `status: needs_confirmation` / `code: journal_report_confirmation_required`（登记在 `usage._NEEDS_INPUT`）、`will_send`
  （刊、规则键、目录现值、问题类型、链接、正文字数与前 200 字——显示的是 `clean_journal` 之后真会发出去的那份）、`confirm_token`
  与 `next = ask_user(…, ["发送…", "先不发"])`。服务端自己绝不代填确认。
- **令牌绑内容**：`confirm_token` = 净化后的头 + 正文的摘要。第二次调用必须同时带 `explicit_confirm=true` 与第一次给的令牌，对不上
  （内容变了、没带令牌）一律回预览，对不上而带了旧令牌时加 `token_stale: true`。
- **不收数值、不信任 Agent 的目录数据**：schema 里没有 value / current / catalog；`current`（目录里那一条的序列化值，≤200 字）、
  出处 id、v1 / v2 `catalog_rev` 都由工具读本机目录（`journalremote.load_cached`，不联网；v2 读 `journalfacts_remote.cached_bundle` 折叠结果）。
  `wrong` 的 `rule_key` 必须在本机目录里存在，否则回 `journal_rule_unknown` + `known_rule_keys`；`source_id` 要与目录对得上；v2 折叠规则的出处 id 是 `v2:<事实 id>`（`journalfacts_fold` 的 `sources[…].fact_id`，与界面「这条不对」同一规则，**不是**带第二个冒号的折叠 `src`）。
- **参数按类型收**：wrong = journal_id + rule_key（+ source_id / issue / url / body）；missing = query / url（至少一个）+ body；
  editor = journal_id + body（编辑部原话）+ url。issue 只随 wrong；别的组合回 `bad_arguments`。Agent 给的 url 不合规（非 https、含账号、过长）
  要明说，不静默少发。
- **开关**：期刊库开关（`JOURNAL_CATALOG_AGENTS`）、`contactsend.enabled()`（`TAVOTTO_NO_CONTACT`）、未设 `TAVOTTO_NO_JOURNAL_SYNC`
  三者同时成立才进 `tools/list`；关着调用回 `journal_report_disabled`。`codex.mcp.json` 的 `env_vars` 与 `integrations/configure.py`
  的 `PROBE_ENV_KEEP` / `CARRIED_ENV` 透传这两个硬开关。工具名**不进** `usage.TOOLS`（走 `other` 桶，不升同意版本）；`NORMAL_TOOLS` 要有它。
- **发送与失败**：同步等 ≤ 90 秒（`JOURNAL_REPORT_WAIT_S`）；成功回 `message_id`（TVM-…）与「我们会核对出版社原文，结论会回信」；
  超时回 `in_progress`，再调同样的确认参数接上结论（`_JOURNAL_REPORT_PENDING`，不重复发）；同一令牌成功后再确认回放原结论
  （`already_sent`）。失败回 `journal_report_failed` + `failure`（`contactsend.FAILURES` 闭集原样）+ `retryable` + `retry_after`，**不重试、不改写**，
  是否再发由用户决定。
