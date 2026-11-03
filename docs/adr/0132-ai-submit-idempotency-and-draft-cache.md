# ADR 0132：AI 修改的提交幂等与草稿持久化

状态：已采纳（PR4「AI 草稿与提交可靠性」）

## 背景

改图助手把一条自然语言指令交给本机编码 Agent，Agent 会**改用户的脚本**。审计发现三处会让
同一条指令被无意执行两次、或让用户写好的指令丢掉：

1. `/api/ai/run` 没有任何去重。前端 `fetch` 没有超时；网络断 / 响应丢了，界面当「失败」给出「重试」，
   而请求其实已被后端接受——重试 = 第二个任务。会话级「重试」也不看 `changed`，原任务已改过脚本时再改一遍。
2. 后端 `run()` 不查 `script_busy`，并发保护只在前端的 `runningHere`；刷新、A→B→A 回切、双标签都能对同一脚本并发起两个 Agent。
3. 草稿只在 React state：右栏切页签（`Inspector` 条件挂载）、刷新、关窗全丢；而若存浏览器 localStorage，桌面端口一变（换 origin）也全丢。

## 决定

- **提交账本在后端**：请求体带 `client_request_id`；`ai_history` 加列 + (project, id) 部分唯一索引；
  同 (项目, id) 再 POST 返回既有会话，不 spawn。`GET /api/ai/requests/<id>` 供前端核对「这条到底有没有被接受」。
  不确定是否已执行时**不得静默重发**：前端进入「已发出未确认」，禁用普通重试，只给「查询结果」；
  查到不存在才用**同一个 id** 重发（后端去重兜住与迟到原请求的竞态）。明确的 4xx/5xx 才算失败，换新 id。
- **后端并发保护**：`run()` 同脚本忙 → 409 `ai_script_busy`。
- **不做自动重试 / 退避**：自动重试 = 自动重复执行。
- **会话级「重试」遇到 `changed=true` 先二次确认**（说明上次可能已改过脚本）；重试永远新 id（用户有意的新任务）。
- **草稿与待确认账本存后端**（用户裁决 2026-10-15：不得以 localStorage 的便利代替安全与持久化设计；
  修订了本 ADR 先前「localStorage 只当缓存、端口变化丢草稿是已知降级」的写法）。理由三条：
  ① 桌面端口被占会换 origin，localStorage 随之整个清空，用户写了一半的指令不该因此消失；
  ② 与 `ai_history` 同一边界——用户本机 `data_dir()`，指令原文本来就在那里；
  ③ 多标签页要「后端为准」，浏览器存储做不到条件删除（读-判-删不是一个事务）。
  做法：`ai_history.sqlite3` 里两张表，主语 (项目, fileId[, rid])：`ai_drafts`（全文覆盖，最后写入为准）与
  `ai_pending`（待确认提交，**按 rid 一条**，每面板最多 8 条**未解决的**，满了拒绝新的（409 `ai_pending_limit`）、**绝不淘汰旧的**——旧的可能正是后端已接受、等着被查询的那条；记提交时的原文与目标）。
  端点（全部走会话认证 ADR 0008 与 `require_project` 隔离）：`GET/PUT/DELETE /api/ai/drafts`
  （DELETE 带 `if_text`，文本对不上不删）、`PUT|DELETE /api/ai/drafts/pending/<rid>`、
  `POST /api/ai/drafts/pending/<rid>/accept`（一个事务里清账本，且仅当草稿文本仍等于提交原文才删草稿）。
  单稿 ≤ 20 KB（超出 413 `ai_draft_too_large`，**不截断**，旧稿一并删）、每项目 30 份、30 天过期
  （启动与写入时有界清理）。前端防抖 600 ms，pagehide / 卸载用 `fetch keepalive` 冲刷，失败在内存里留着 3 秒后重试，
  窗口重新获得焦点时再读一次（本地有未写出的改动时不覆盖）；**前端不再向任何浏览器存储写草稿**。
  不放项目文件夹 `tavottofile/`（会随项目包流动，草稿泄给别人）。
- **幂等记录先落库再 spawn**：`run()` 在同一把锁里先写 `ai_history`（含 `ai_requests` 账本），再起 CLI；
  写库失败不 spawn；spawn 失败把记录标成 `failed`（不是删掉）。`ai_requests` 独立于用户可删的历史，
  删历史后同 rid 查询得 `history_deleted`（已执行过），前端据此不重发。
- 草稿与请求 id **不进**日志、遥测、诊断包 frontend-state、interaction trace。
- 只在桌面 / 浏览器模式启用：Codex 内嵌画布与浏览器 playground 没有助手面板，不引入草稿模块（import 架构门禁）。

## 后果

- 新错误码 `ai_script_busy`、`ai_request_not_found`、`ai_draft_too_large`（中英文案，`test_error_codes`）。
- 运行中会话的刷新后恢复（只读占位）不在本 ADR：后端忙检查已保证不会并发双跑，占位 UI 另议。
