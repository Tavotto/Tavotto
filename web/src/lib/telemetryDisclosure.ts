/**
 * 「会发送哪些数据」这一段要列的东西（UI/UX 审计 T49）。
 *
 * 改动前，关于页那段说明是一整句散文（`about.telemetry.sendsBefore`），它写
 * 下的那天是对的，之后 `EVENTS` 加了九条而那句话没动——真机上已经在发「用了
 * 哪个改图助手」「更新装到哪个版本」，说明里一个字都没有。**隐私说明漏一条
 * 和多写一条同样坏**：多写的那条让人以为我们采得更多，漏掉的那条是用户没有
 * 同意过的采集。
 *
 * 所以这里不是一段散文，而是一个**闭集**：每条事件一行，行文在
 * `dialogs:settings.about.telemetry.sends.<事件名>`。它与
 * `src/tavotto/engine/telemetry.py` 的 `EVENTS` 是严格同源对，由
 * `tests/test_telemetry_disclosure.py` 逐条比对——加一条事件而不写它的说明，
 * 那条用例当场红。
 *
 * 顺序照抄 `EVENTS`：那张表按「同意 → 会话 → 编辑 → 产出 → 集成 → 维护」
 * 排，读起来就是用户在产品里走过的路。
 */
export const TELEMETRY_DISCLOSED_EVENTS = [
  'telemetry_enabled',
  'app_started',
  'figure_opened',
  'figure_edit_completed',
  'canvas_created',
  'preflight_completed',
  'export_completed',
  'ai_assistant_invoked',
  'update_completed',
  'project_refresh_completed',
  'project_readiness_opened',
  'tutorial_started',
  'tutorial_step_completed',
  'tutorial_completed',
  'context_bar_multi_used',
  'document_saved',
  'recovery_action',
  'package_action',
] as const

export type TelemetryDisclosedEvent = (typeof TELEMETRY_DISCLOSED_EVENTS)[number]
