/**
 * Waterfall 的展示模型（ADR 0151 PR3）。后端 DTO → 这里的转换只在 `lib/execTrace.ts` 一处；组件只画、不判定。
 *
 * 诚实规则（组件必须照做）：
 * - `end === undefined` = 还在跑，条画到 `now`；
 * - `timing === 'point'` = 只知道时刻：画一个点 / 竖刻度，**不画宽条**；
 * - `timing === 'unmeasured'` = 区间没量到（崩溃 / 冻结时还开着）：虚线框，不画实心条；
 * - `timing === 'reported'` = 时长是别的进程报的：照常画，popover 里说明；
 * - `cost` / `tokensIn` / `tokensOut` 是 `null` = 不知道：不显示该指标（不是 0）。
 */

export type WaterfallKind = 'agent' | 'model' | 'tool' | 'phase' | 'wait'
export type WaterfallStatus = 'ok' | 'error' | 'cancelled' | 'unknown'
export type WaterfallTiming = 'measured' | 'reported' | 'point' | 'unmeasured'
/** 图标由组件按键选（`components/ui/icons` 与已有的厂商图标），适配层不产 ReactNode */
export type WaterfallIcon = 'task' | 'codex' | 'claude' | 'builtin' | 'agent' | 'model' | 'tool' | 'wait' | 'phase' | 'stage'

export interface WaterfallFact {
  /** 已翻译的标签与值（popover 的事实表逐行显示） */
  label: string
  value: string
}

export interface WaterfallSpan {
  id: string
  parentId?: string
  kind: WaterfallKind
  /** 已翻译的阶段名 */
  name: string
  /** 第二行 / 右侧的补充（工具 id、Agent 名），可空 */
  detail?: string
  /** 相对 Trace 起点的秒 */
  start: number
  end?: number
  status?: WaterfallStatus
  timing: WaterfallTiming
  attempt: number
  /** attempt > 1：重试 / 修正轮 */
  retry: boolean
  icon: WaterfallIcon
  /** 美元估算；null = 不知道 */
  cost: number | null
  tokensIn: number | null
  tokensOut: number | null
  /** 这是整条 Trace 的第一次失败（根因候选，不是「最慢」） */
  firstFailure: boolean
  /** 这一段的内部步骤不可观察（第三方 CLI）：它下面「没有模型 / 工具调用」不等于真的没有 */
  opaque?: boolean
  facts: WaterfallFact[]
}

export interface WaterfallModel {
  spans: WaterfallSpan[]
  /** 秒：已结束 = 根的时长；运行中 = 截至 `now` */
  duration: number
  running: boolean
}
