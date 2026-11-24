import { formatMessage, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { backendCodeMsg, type ChangePlanBox, type ChangePlanGroup, type ChangePlanUnhandledReason } from '@/lib/api'
import { POLL_TIMEOUT, type PlanEntry } from '@/store/changePlanStore'

export { effectiveStatus } from '@/lib/changePlanApply'

/** 修改方案的文案助手：命名空间 + 前缀固定在这一处，调用点只写最后一段 */
export const cp = (key: string, values?: Record<string, unknown>) => translate(`changePlan.${key}`, { ns: 'ai', ...(values ?? {}) })

/** 画面比例坐标 [x, y, w, h]，0–1，原点在图的左上角 */
export type PlanBox = [number, number, number, number]

/** 一个改动组在界面上的样子（清单一行 + 舞台上的细框） */
export interface GroupView {
  key: string
  /** 这一处改了什么：对应那条意见的原话（截短过的），没有就说「第 N 条意见」 */
  summary: string
  reviewers: string[]
  commentNumbers: number[]
  /** 改动处的框，一处一个；`before` 是同一处在修改前的位置（按元素 gid 配对，没有 = 改前没有这一处） */
  boxes: { after: PlanBox; before?: PlanBox }[]
}

export interface UnhandledView {
  id: string
  text: string
  reviewer: string
  reason: string
}

const clip = (s: string, n = 40) => (s.length > n ? `${s.slice(0, n)}…` : s)

export function groupViews(e: PlanEntry): GroupView[] {
  const plan = e.plan
  if (!plan) return []
  const numberOf = (id?: string) => (id ? e.comments.findIndex((c) => c.id === id) + 1 : 0)
  return plan.groups.map((g: ChangePlanGroup, i) => {
    const c = g.comment_id ? e.comments.find((x) => x.id === g.comment_id) : undefined
    const n = numberOf(g.comment_id)
    const before = new Map<string, ChangePlanBox>((g.boxes.before ?? []).map((b) => [b.gid, b]))
    const after = g.boxes.after.length ? g.boxes.after : []
    return {
      key: g.key,
      summary: clip(c?.text ?? g.label) || cp('dialog.groupN', { n: i + 1 }),
      reviewers: c ? [c.author] : [],
      commentNumbers: n ? [n] : [],
      boxes: after.map((b) => ({ after: b.box as PlanBox, before: before.get(b.gid)?.box as PlanBox | undefined })),
    }
  })
}

/** 一条没处理的意见：原话 + 谁说的 + 一句话原因（闭集短码 → 文案） */
export function unhandledViews(e: PlanEntry): UnhandledView[] {
  return (e.plan?.unhandled ?? []).map((u, i) => {
    const c = u.comment_id ? e.comments.find((x) => x.id === u.comment_id) : undefined
    return {
      id: u.comment_id ?? `u${i}`,
      text: clip(c?.text ?? '') || cp('dialog.groupN', { n: i + 1 }),
      reviewer: c?.author ?? '',
      reason: unhandledReason(u.reason),
    }
  })
}

export const unhandledReason = (r: ChangePlanUnhandledReason): string => cp(`unhandled.${r}`)

/** 一版方案里勾选的改动：处理了几条意见、改了几处；没改的几条里有几条是要改脚本的 */
export function planTally(e: PlanEntry) {
  const views = groupViews(e).filter((g) => !e.unchecked[g.key])
  const un = e.plan?.unhandled ?? []
  return {
    done: new Set(views.flatMap((g) => g.commentNumbers)).size || views.length,
    places: views.reduce((a, g) => a + g.boxes.length, 0) || views.length,
    script: un.filter((u) => u.reason === 'needs_script').length,
    other: un.filter((u) => u.reason !== 'needs_script').length,
  }
}

/** 卡片上那一句：按几条意见改了几处（还有几条没改） */
export function summaryLine(e: PlanEntry): string {
  const t = planTally(e)
  if (t.other > 0) return cp('card.summaryOther', { done: t.done, places: t.places, rest: t.script + t.other })
  return t.script > 0 ? cp('card.summary', { done: t.done, places: t.places, script: t.script }) : cp('card.summaryAll', { done: t.done, places: t.places })
}

/** 失败之后用户能走的下一步（每个稳定码一个，绝不留死胡同） */
export type FailureAction = 'connect' | 'retry' | 'regenerate' | 'dismiss'

export function failureAction(code: string): FailureAction {
  switch (code) {
    case 'change_plan_no_connection':
    case 'change_plan_unsupported_connection':
    case 'change_plan_model_auth':
      return 'connect'
    case 'change_plan_figure_changed':
    case 'change_plan_unknown':
      return 'regenerate'
    case 'change_plan_busy':
    case 'change_plan_model_quota':
    case 'change_plan_model_timeout':
    case 'change_plan_model_failed':
    case 'change_plan_render_failed':
    case 'change_plan_failed':
    case POLL_TIMEOUT:
    case 'invalid_patches':
      return 'retry'
    default:
      // 没有可再试的路（native / 没脚本 / 没意见 / 太长 / 意见都改不了 / 不认识的码）：收起这张卡
      return 'dismiss'
  }
}

/** 卡片上那一句灰字：稳定码 → `errors:backend.<code>`（后端的 `error` 原文只在没有文案时露出） */
export function failureText(code: string, params?: Record<string, unknown>): string {
  if (code === 'change_plan_empty') return cp('card.empty')
  const known = (c: string) => formatMessage(backendCodeMsg(c, params, ''))
  // 本地的超时码与不认识的码都落到「生成失败，图没有改动」这一句，不留空白
  return (code === POLL_TIMEOUT ? '' : known(code)) || known('change_plan_failed')
}

export const whoOf = (names: string[]) => listJoin([...new Set(names)])
