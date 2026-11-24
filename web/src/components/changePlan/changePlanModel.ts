import { formatMessage, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { backendCodeMsg, type ChangePlanBox, type ChangePlanGroup, type ChangePlanUnhandledReason } from '@/lib/api'
import { POLL_TIMEOUT, type PlanEntry } from '@/store/changePlanStore'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'

import { effectiveStatus as effectiveStatusOf, type PlanSource } from '@/lib/changePlanApply'
import { useDocumentStore } from '@/store/documentStore'

/** 卡片此刻的状态（`lib/changePlanApply.effectiveStatus`）；一键排版卡另按文档历史派生 */
export function effectiveStatus(e: Parameters<typeof effectiveStatusOf>[0], current: readonly unknown[] | null, source?: PlanSource | null) {
  const h = useDocumentStore.getState()
  return effectiveStatusOf(e, current, source, e.kind === 'layout' ? { past: h.past, future: h.future } : undefined)
}

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
  /** 续改出的版本里，这一组是按用户批注改的（清单里置顶、单独标注） */
  fromNote: boolean
  /** 改动处的框，一处一个；`before` 是同一处在修改前的位置（按元素 gid 配对，没有 = 改前没有这一处） */
  boxes: { after: PlanBox; before?: PlanBox }[]
}

export interface UnhandledView {
  id: string
  text: string
  reviewer: string
  reason: string
}

/** 一条意见在界面上的编号：审阅页签给的（与页签一致）优先，没有就按这批意见里的位置 */
export const commentNumber = (e: Pick<PlanEntry, 'comments' | 'request'>, id?: string): number => {
  if (!id) return 0
  const n = e.request.numbers?.[id]
  return n ?? e.comments.findIndex((c) => c.id === id) + 1
}

const clip = (s: string, n = 40) => (s.length > n ? `${s.slice(0, n)}…` : s)

export function groupViews(e: PlanEntry): GroupView[] {
  const plan = e.plan
  if (!plan) return []
  const numberOf = (id?: string) => commentNumber(e, id)
  return plan.groups.map((g: ChangePlanGroup, i) => {
    const fromNote = g.origin === 'note'
    const c = !fromNote && g.comment_id ? e.comments.find((x) => x.id === g.comment_id) : undefined
    const n = fromNote ? 0 : numberOf(g.comment_id)
    // 助手来源：这一组就是用户那条指令（原话作标题，没有审阅者与编号）
    if (e.from === 'assistant' && !fromNote) {
      const before = new Map<string, ChangePlanBox>((g.boxes.before ?? []).map((b) => [b.gid, b]))
      return {
        key: g.key,
        summary: clip(e.instruction ?? g.label) || cp('dialog.groupN', { n: i + 1 }),
        reviewers: [],
        commentNumbers: [],
        boxes: g.boxes.after.map((b) => ({ after: b.box as PlanBox, before: before.get(b.gid)?.box as PlanBox | undefined })),
      }
    }
    const before = new Map<string, ChangePlanBox>((g.boxes.before ?? []).map((b) => [b.gid, b]))
    const after = g.boxes.after.length ? g.boxes.after : []
    return {
      key: g.key,
      summary: clip(c?.text ?? g.label) || cp('dialog.groupN', { n: i + 1 }),
      reviewers: c ? [c.author] : [],
      commentNumbers: n ? [n] : [],
      fromNote,
      boxes: after.map((b) => ({ after: b.box as PlanBox, before: before.get(b.gid)?.box as PlanBox | undefined })),
    }
  })
}

/** 一条没处理的意见：原话 + 谁说的 + 一句话原因（闭集短码 → 文案） */
export function unhandledViews(e: PlanEntry): UnhandledView[] {
  return (e.plan?.unhandled ?? []).map((u, i) => {
    const c = u.comment_id ? e.comments.find((x) => x.id === u.comment_id) : undefined
    if (e.from === 'assistant') {
      const reason = u.reason === 'unclear' ? cp('unhandled.unclearAssistant') : unhandledReason(u.reason)
      return { id: u.comment_id ?? `u${i}`, text: clip(e.instruction ?? ''), reviewer: '', reason }
    }
    return {
      id: u.comment_id ?? `u${i}`,
      text: clip(c?.text ?? (u.comment_id ? e.noteTexts?.[u.comment_id] : '') ?? '') || cp('dialog.groupN', { n: i + 1 }),
      reviewer: c?.author ?? '',
      reason: unhandledReason(u.reason),
    }
  })
}

export const unhandledReason = (r: ChangePlanUnhandledReason): string => cp(`unhandled.${r}`)

/** 一版方案里勾选的改动：处理了几条意见、改了几处；没改的几条里有几条是要改脚本的 */
export function planTally(e: PlanEntry) {
  const all = groupViews(e).filter((g) => !e.unchecked[g.key])
  const views = all.filter((g) => !g.fromNote)
  const notes = all.filter((g) => g.fromNote)
  const un = e.plan?.unhandled ?? []
  return {
    notes: notes.length,
    notePlaces: notes.reduce((a, g) => a + g.boxes.length, 0) || notes.length,
    done: new Set(views.flatMap((g) => g.commentNumbers)).size || views.length,
    places: views.reduce((a, g) => a + g.boxes.length, 0) || views.length,
    script: un.filter((u) => u.reason === 'needs_script').length,
    other: un.filter((u) => u.reason !== 'needs_script').length,
  }
}

/** 卡片上那一句：按几条意见改了几处（还有几条没改） */
export function summaryLine(e: PlanEntry): string {
  const t = planTally(e)
  // 续改出的版本：先说这版按批注又改了几处，再说按意见改的
  if (t.notes > 0) return cp('card.summaryNotes', { n: t.notes, places: t.notePlaces })
  if (e.kind === 'layout') return cp('card.summaryLayout', { count: e.layout?.count ?? 0 })
  if (e.from === 'assistant') {
    if (t.script > 0) return cp('card.summaryAssistantScript', { places: t.places })
    return t.other > 0 ? cp('card.summaryAssistantOther', { places: t.places }) : cp('card.summaryAssistant', { places: t.places })
  }
  if (t.other > 0) return cp('card.summaryOther', { done: t.done, places: t.places, rest: t.script + t.other })
  return t.script > 0 ? cp('card.summary', { done: t.done, places: t.places, script: t.script }) : cp('card.summaryAll', { done: t.done, places: t.places })
}

/** 失败之后用户能走的下一步（每个稳定码一个，绝不留死胡同） */
export type FailureAction = 'connect' | 'retry' | 'regenerate' | 'dismiss' | 'script'

export function failureAction(code: string): FailureAction {
  switch (code) {
    case 'change_plan_no_connection':
    case 'change_plan_unsupported_connection':
    case 'change_plan_model_auth':
      return 'connect'
    case 'change_plan_script_failed':
      return 'script'
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
export function failureText(code: string, params?: Record<string, unknown>, assistant = false): string {
  if (code === 'change_plan_empty') return cp(assistant ? 'card.emptyAssistant' : 'card.empty')
  if (code === 'change_plan_script_failed') return cp('card.scriptFailed')
  const known = (c: string) => formatMessage(backendCodeMsg(c, params, ''))
  // 本地的超时码与不认识的码都落到「生成失败，图没有改动」这一句，不留空白
  return (code === POLL_TIMEOUT ? '' : known(code)) || known('change_plan_failed')
}

export const whoOf = (names: string[]) => listJoin([...new Set(names)])

/** 付费功能键：在改好的图上写批注与「按批注重改」是同一项权益（引擎 `license.FEATURES`，ADR 0193 §一） */
export const CHANGE_PLAN_FEATURE = 'change-plan-rerun'

/**
 * 批注入口怎么画：授权入口没出现（`visible` 为假：上线开关关着 / 老后端）= 批注的三处入口整个不画（与一键排版同一规矩）；
 * 出现了但没解锁 = 切换项与「写批注」带锁（`PremiumLock`）；`pro` = 解锁了。真正的权益判断在后端。
 */
export function useChangePlanAccess(): { visible: boolean; pro: boolean } {
  const status = useLicenseStore((s) => s.status)
  return { visible: licenseVisible(status), pro: licenseHas(status, CHANGE_PLAN_FEATURE) }
}

/** 「写批注」一次性提示：本机记一笔，看过就不再出（存储不可用就每次都出一次，无伤大雅） */
const HINT_KEY = 'tavotto.changePlan.noteHintSeen'
export const noteHintSeen = (): boolean => {
  try {
    return localStorage.getItem(HINT_KEY) === '1'
  } catch {
    return false
  }
}
export const markNoteHintSeen = (): void => {
  try {
    localStorage.setItem(HINT_KEY, '1')
  } catch {
    /* 存不下不影响本次 */
  }
}
