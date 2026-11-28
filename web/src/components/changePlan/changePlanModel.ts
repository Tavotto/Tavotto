import type { TaskStep } from '../ui/TaskChecklist'
import { formatMessage, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { backendCodeMsg, type ChangePlanBox, type ChangePlanChange, type ChangePlanGroup, type ChangePlanUnhandledReason } from '@/lib/api'
import { DATA_DECLINED, POLL_TIMEOUT, allOutOfScope, declinedGroups, isCanvasPlan, pendingConfirm, scriptPassable, todoChecked, todoQueued, todosOf, type PlanEntry } from '@/store/changePlanStore'
import { failedChecks, touchedCount } from '@/lib/vectorEdit'
import type { ChangeLine, ChangeValue } from '@/lib/vectorChanges'
import { propLabel } from '../inspector/roles/registry'
import type { PlotStage } from './PlotSketch'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'

import { effectiveStatus as effectiveStatusOf, type PlanSource } from '@/lib/changePlanApply'
import { useDocumentStore } from '@/store/documentStore'

/** 卡片此刻的状态（`lib/changePlanApply.effectiveStatus`）；整版 / 矢量编辑卡另按文档历史派生，矢量编辑卡待确认时点名的对象变了 = 图已改过 */
export function effectiveStatus(e: Parameters<typeof effectiveStatusOf>[0] & { vector?: { holds: () => boolean; landed: () => boolean; result?: unknown } }, current: readonly unknown[] | null, source?: PlanSource | null) {
  // 矢量编辑卡按**对象状态**派生：点名的对象还是出发时那样 = 待确认（含撤回之后）；方案写进去的样子还在 = 已应用；都不是 = 图改过了。
  // 不认历史条目：应用之后文字量好尺寸的回写会把那一条历史换成合并后的新条目
  if (e.kind === 'vector' && (e.status === 'ready' || e.status === 'applied') && e.plan && e.vector?.result) {
    if (e.vector.holds()) return 'ready'
    return e.status === 'applied' && e.vector.landed() ? 'applied' : 'stale'
  }
  const h = useDocumentStore.getState()
  return effectiveStatusOf(e, current, source, e.kind === 'layout' ? { past: h.past, future: h.future } : undefined)
}

/** 修改方案的文案助手：命名空间 + 前缀固定在这一处，调用点只写最后一段 */
export const cp = (key: string, values?: Record<string, unknown>) => translate(`changePlan.${key}`, { ns: 'ai', ...(values ?? {}) })

/** 画面比例坐标 [x, y, w, h]，0–1，原点在图的左上角 */
export type PlanBox = [number, number, number, number]

/** 一个改动组在界面上的样子（清单一行 + 舞台上的细框） */
export interface GroupView {
  /** 清单行第二行的整句（一键排版的组说「N 张图」）；缺省按「谁 · N 处」；空串 = 不要第二行 */
  metaText?: string
  /** 整批要或不要的一行（矢量编辑方案里每条复述）：不画勾选框，应用 / 放弃是整份的 */
  fixed?: boolean
  /** 这一行下面的字段改动（矢量编辑方案，已翻成界面语言） */
  lines?: ChangeLine[]
  /** 这一行下面的一句灰字（矢量编辑方案：因依赖了没勾的步而自动取消） */
  note?: string
  /** 悬停说明（矢量编辑方案：后端的复述原文，排查用） */
  detail?: string
  key: string
  /** 这一处改了什么：对应那条意见的原话（截短过的），没有就说「第 N 条意见」 */
  summary: string
  reviewers: string[]
  commentNumbers: number[]
  /** 续改出的版本里，这一组是按用户批注改的（清单里置顶、单独标注） */
  fromNote: boolean
  /** 这一组里带着自动腾出的边距（`change.auto === 'room'`）：清单行的灰字里多说一句 */
  room?: boolean
  /**
   * 改动处的框，一处一个；`before` 是同一处在修改前的位置（按元素 gid 配对，没有 = 改前没有这一处）。
   * `gid`：哪个元素（第 2 版起「修改前」换成上一版时按它取上一版里的位置）；`room`：这一处只是自动留边距挪了子图，悬停不画整块大框
   */
  boxes: { after: PlanBox; before?: PlanBox; gid?: string; room?: boolean }[]
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

/** 元素 gid → 界面上的名字（取不到就不写名字，不露 gid） */
export type LabelOf = (gid: string) => string | undefined

/**
 * `labelOf`：给了（弹窗里，按这张图的元素表）= 图内方案每组下面逐行列出改了什么（元素名 · 属性 前 → 后）；不给 = 不列（卡片摘要只要计数）。
 */
export function groupViews(e: PlanEntry, labelOf?: LabelOf): GroupView[] {
  const plan = e.plan
  if (!plan) return []
  // 答了「不改」的组整组不要：不进清单，它那条意见在没处理列表里（`unhandledViews`）
  return plan.groups.flatMap((g: ChangePlanGroup, i) => {
    if (e.answers?.[g.key] === 'no') return []
    const v = groupView(e, g, i)
    const inFigure = e.kind !== 'vector' && e.kind !== 'layout' && g.kind !== 'script'
    const lines = inFigure && labelOf ? changeLines(g.changes, labelOf) : undefined
    return [{ ...v, room: g.changes.some((c) => c.auto === 'room'), ...(inFigure ? { boxes: v.boxes.map((b) => ({ ...b, room: !!b.gid && roomOnly(g, b.gid) })) } : {}), ...(lines?.length ? { lines } : {}) }]
  })
}

/** 这个元素在这一组里只有自动留边距那一条改动（子图整体挪了一点）：不当成「改了这一处」画框 */
const roomOnly = (g: ChangePlanGroup, gid: string): boolean => {
  const own = g.changes.filter((c) => c.target_gid === gid)
  return own.length > 0 && own.every((c) => c.auto === 'room')
}

/** 图内方案一组里的每处改动（与矢量方案同一种行）：自动留边距那条不列（清单另说一句） */
export function changeLines(changes: readonly ChangePlanChange[], labelOf: LabelOf): ChangeLine[] {
  return changes
    .filter((c) => c.auto !== 'room')
    .map((c) => {
      const name = labelOf(c.target_gid)
      const prop = propLabel(c.prop)
      const after = changeValue(c.prop, c.after)
      const before = after ? changeValue(c.prop, c.before) : undefined
      return { label: name ? cp('dialog.changeLine', { el: name, prop }) : prop, ...(after ? { after } : {}), ...(after && before ? { before } : {}) }
    })
}

const VAL_TEXT_PROPS = /text|label|title/i
const round2 = (n: number) => String(Math.round(n * 100) / 100)
const hex2 = (n: number) => Math.round(Math.min(1, Math.max(0, n)) * 255).toString(16).padStart(2, '0')

/** 一个属性值的显示（manifest 的展示值，只给人看）：数字两位小数、颜色带色块、开关说开 / 关；认不出的结构不列值 */
export function changeValue(prop: string, v: unknown): ChangeValue | undefined {
  if (v === null || v === undefined) return { text: cp('vector.none') }
  if (typeof v === 'boolean') return { text: cp(v ? 'vector.on' : 'vector.off') }
  if (typeof v === 'number') return Number.isFinite(v) ? { text: round2(v) } : undefined
  if (typeof v === 'string') {
    const t = v.trim()
    if (/^#[0-9a-f]{3,8}$/i.test(t)) return { text: t.toUpperCase(), color: t }
    if (!t) return { text: cp('vector.none') }
    return { text: VAL_TEXT_PROPS.test(prop) ? `「${clip(t, 16)}」` : clip(t, 16) }
  }
  if (Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === 'number' && Number.isFinite(x))) {
    const ns = v as number[]
    // RGB(A) 0–1 的颜色：画成色块
    if (/colou?r/i.test(prop) && (ns.length === 3 || ns.length === 4) && ns.every((x) => x >= 0 && x <= 1)) {
      const h = `#${ns.slice(0, 3).map(hex2).join('')}`.toUpperCase()
      return { text: h, color: h }
    }
    if (ns.length === 2) return { text: `${round2(ns[0])} – ${round2(ns[1])}` }
    if (ns.length <= 4) return { text: ns.map(round2).join(', ') }
  }
  return undefined
}

/** 生成中那幅小画走到哪一步：跟着三步清单正在做的那一步（卡片与弹窗同一套） */
export function sketchStageOf(steps: TaskStep[] | null): PlotStage {
  const active = steps?.find((x) => x.state === 'active')?.id
  return active === 'before' ? 'read' : active === 'after' ? 'draw' : 'think'
}

function groupView(e: PlanEntry, g: ChangePlanGroup, i: number): GroupView {
  const numberOf = (id?: string) => commentNumber(e, id)
  {
    // 矢量编辑方案：一条复述一行（后端的话原样，不画勾选与画面框），补丁的操作有先后、不能只要其中几条
    if (e.kind === 'vector') return { key: g.key, summary: g.label, reviewers: [], commentNumbers: [], fromNote: false, fixed: !e.vector?.ops, metaText: '', boxes: [], note: e.vector?.blocked?.[g.key] ? cp('vector.blocked') : undefined, lines: e.vector?.groups?.find((x) => x.key === g.key)?.lines, detail: e.vector?.result?.echo?.join('\n') }
    if (e.kind === 'layout') {
      return {
        key: g.key,
        summary: g.label,
        reviewers: [],
        commentNumbers: [],
        fromNote: false,
        metaText: cp('dialog.layoutGroupMeta', { count: e.layout?.count ?? g.boxes.after.length }),
        boxes: g.boxes.after.map((b) => ({ after: b.box as PlanBox, before: (g.boxes.before ?? []).find((x) => x.gid === b.gid)?.box as PlanBox | undefined })),
      }
    }
    // 脚本修改组：没有画面框（改动在脚本里，看 diff）；标题就是用户的原话
    if (g.kind === 'script') return { key: g.key, summary: clip(e.instruction ?? g.label) || cp('dialog.groupN', { n: i + 1 }), reviewers: [], commentNumbers: [], fromNote: false, boxes: [] }
    const fromNote = g.origin === 'note'
    const c = !fromNote && g.comment_id ? e.comments.find((x) => x.id === g.comment_id) : undefined
    const n = fromNote ? 0 : numberOf(g.comment_id)
    // 助手来源：这一组就是用户那条指令（原话作标题，没有审阅者与编号）
    if (e.from === 'assistant' && !fromNote) {
      const before = new Map<string, ChangePlanBox>((g.boxes.before ?? []).map((b) => [b.gid, b]))
      return {
        key: g.key,
        // 建议待办卡：这一组标题是那条待办的一句话（不是整条指令）
        summary: clip(todosOf(e).find((t) => t.group_key === g.key)?.label ?? e.instruction ?? g.label) || cp('dialog.groupN', { n: i + 1 }),
        reviewers: [],
        commentNumbers: [],
        fromNote: false,
        boxes: g.boxes.after.map((b) => ({ after: b.box as PlanBox, before: before.get(b.gid)?.box as PlanBox | undefined, gid: b.gid })),
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
      boxes: after.map((b) => ({ after: b.box as PlanBox, before: before.get(b.gid)?.box as PlanBox | undefined, gid: b.gid })),
    }
  }
}

/** 一条没处理的意见：原话 + 谁说的 + 一句话原因（闭集短码 → 文案） */
export function unhandledViews(e: PlanEntry): UnhandledView[] {
  if (e.kind === 'vector') return vectorUnhandledViews(e)
  // 答了「不改」的对数轴下限：整组没动，那条意见在没处理列表里写一句原因
  const declined = declinedGroups(e).map((g, i): UnhandledView => {
    const c = g.comment_id ? e.comments.find((x) => x.id === g.comment_id) : undefined
    const text = e.from === 'assistant' ? clip(e.instruction ?? '') : clip(c?.text ?? g.label)
    return { id: g.comment_id ?? `d${i}`, text: text || cp('dialog.groupN', { n: i + 1 }), reviewer: e.from === 'assistant' ? '' : (c?.author ?? ''), reason: unhandledReason('log_limit_declined') }
  })
  return [...unhandledViewsOf(e), ...declined]
}

function unhandledViewsOf(e: PlanEntry): UnhandledView[] {
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

/** 后端 `vectoredit.ERROR_CODES` 的闭集：每个码一句人话（后端那句是写给模型 / Agent 看的，不上界面） */
const VECTOR_CODES = ['bad_op', 'unknown_object', 'not_vector', 'bad_field', 'bad_value', 'out_of_range', 'duplicate_target', 'kernel_unavailable', 'too_many_steps'] as const

/** 矢量编辑方案里没能照做的几条：带码的按码翻成人话，没码的是模型自己写的一句原因，照显示 */
function vectorUnhandledViews(e: PlanEntry): UnhandledView[] {
  // 一行一句原因（没有「谁说的」那一层，也不重复整条指令）
  return (e.vector?.result?.unhandled ?? []).map((u, i) => ({
    id: `u${i}`,
    text: (VECTOR_CODES as readonly string[]).includes(u.code ?? '') ? cp(`unhandled.vector.${u.code}`, { count: u.count ?? 0, max: u.max ?? 0 }) : clip(u.reason, 80) || cp('unhandled.vector.bad_op'),
    reviewer: '',
    reason: '',
  }))
}

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
    other: un.filter((u) => u.reason !== 'needs_script').length + declinedGroups(e).length,
  }
}

/** 卡片上那一句：按几条意见改了几处（还有几条没改） */
export function summaryLine(e: PlanEntry): string {
  const script = scriptInfo(e)
  if (script) return cp('card.summaryScript', { added: script.added, removed: script.removed })
  const t = planTally(e)
  // 数量带单位的短语（「5 处」「1 place」）由各自的复数键出，句子里只放短语
  const places = placesPhrase(t.places)
  // 续改出的版本：先说这版按批注又改了几处，再说按意见改的
  if (t.notes > 0) return cp('card.summaryNotes', { notes: cp('card.notesN', { count: t.notes }), places: placesPhrase(t.notePlaces) })
  if (e.kind === 'layout') return cp('card.summaryLayout', { count: e.layout?.count ?? 0 })
  if (e.kind === 'vector' && e.vector?.result) {
    const count = touchedCount(e.vector.result)
    if (e.vector.failed) return cp('dialog.vectorRecomputeFailed')
    const fails = failedChecks(e.vector.result)
    const rest = e.vector.result.unhandled?.length ?? 0
    if (fails > 0) return cp('card.summaryVectorChecks', { count, fails })
    return rest > 0 ? cp('card.summaryVectorRest', { count, rest }) : cp('card.summaryVector', { count })
  }
  if (e.from === 'assistant') {
    if (t.script > 0) return cp('card.summaryAssistantScript', { places })
    return t.other > 0 ? cp('card.summaryAssistantOther', { places }) : cp('card.summaryAssistant', { places })
  }
  const done = cp('card.commentsN', { count: t.done })
  if (t.other > 0) return cp('card.summaryOther', { done, places, rest: t.script + t.other })
  return t.script > 0 ? cp('card.summary', { done, places, count: t.script }) : cp('card.summaryAll', { done, places })
}

/** 「N 处」/ "N places"：数量与单位在一起，按各语言的复数规则 */
export const placesPhrase = (count: number): string => cp('card.placesN', { count })

/** 脚本修改方案里那一组的内容（图内方案没有） */
export const scriptInfo = (e: Pick<PlanEntry, 'plan'>) => e.plan?.groups.find((g) => g.kind === 'script')?.script

/** 脚本修改方案（一张卡只含一类：不是图内修改） */
export const isScriptPlan = (e: Pick<PlanEntry, 'plan'>): boolean => e.plan?.kind === 'script'

/** 矢量编辑方案此刻不能应用：正在按勾选重算，或一步都没勾 */
export const vectorCannotApply = (e: Pick<PlanEntry, 'kind' | 'vector' | 'plan' | 'unchecked'>): boolean =>
  e.kind === 'vector' && (!!e.vector?.busy || !!e.vector?.failed || (e.plan?.groups ?? []).every((g) => e.unchecked[g.key]))

/** 失败之后用户能走的下一步（每个稳定码一个，绝不留死胡同） */
export type FailureAction = 'connect' | 'retry' | 'regenerate' | 'dismiss' | 'script' | 'restore'

export function failureAction(code: string, params?: Record<string, unknown>): FailureAction {
  // 生成期间脚本被改过（冲突，已备份、没自动还原）：主动作是用户点了才按起跑快照恢复
  if (code === 'change_plan_script_overreach' && params?.target_changed === true && typeof params.backup === 'string' && typeof params.current_sha256 === 'string') return 'restore'
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
    case 'change_plan_script_check_failed':
    case 'change_plan_script_no_change':
    case 'change_plan_script_agent_failed':
    case 'change_plan_script_figure_missing':
    case 'change_plan_script_no_preview':
    case 'change_plan_script_overreach':
      return 'retry'
    case 'vector_kernel_unavailable':
      return 'dismiss'
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
  // 矢量编辑：布尔 / 偏移这类要几何内核，这台电脑上没装好——其余编辑照常（一句话，图没有改动）
  if (code === 'vector_kernel_unavailable') return cp('card.vectorKernel')
  // 前端本地码：数据会变的脚本方案，用户答了「先不改」
  if (code === DATA_DECLINED) return cp('card.dataDeclined')
  // 外部 CLI 越界（R-03）：一句话说清脚本怎么样了（还原了 / 没替你还原 / 没动），文件名由卡片另列
  if (code === 'change_plan_script_overreach') {
    return cp(params?.target_changed === true ? 'card.overreachChanged' : 'card.overreachOthers')
  }
  const known = (c: string) => formatMessage(backendCodeMsg(c, params, ''))
  // 本地的超时码与不认识的码都落到「生成失败，图没有改动」这一句，不留空白
  return (code === POLL_TIMEOUT ? '' : known(code)) || known('change_plan_failed')
}

/** 越界被改的文件名（最多 5 个）+「等 N 个」；没有文件名（只是目标脚本被改）= 空串 */
export function overreachFiles(params?: Record<string, unknown>): string {
  const files = Array.isArray(params?.files) ? (params.files as unknown[]).filter((f): f is string => typeof f === 'string') : []
  if (!files.length) return ''
  const total = typeof params?.total === 'number' ? params.total : files.length
  return total > files.length ? cp('card.overreachMore', { files: listJoin(files), count: total }) : listJoin(files)
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

/**
 * 这张卡能不能「批注重改」：审阅 / 助手来源的图内方案与**脚本方案**（`ChangePlanDialog` 据此显示批注切换、「写批注」与悬停写批注）。
 * 脚本方案（含审阅意见转出的脚本卡）重改 = 改脚本 Agent 以上一版候选为起点接着改，基线仍是原脚本（ADR 0206 修订 2026-11-28）。
 * 一键排版的整版方案（`quick_layout`）与矢量编辑方案没有逐元素的改动可批注，不能重改。
 */
export function canRevise(e: Pick<PlanEntry, 'kind' | 'from' | 'plan'>): boolean {
  if (e.kind === 'layout' || e.kind === 'vector') return false
  const from = e.from ?? 'review'
  return (from === 'review' || from === 'assistant') && e.plan?.source !== 'quick_layout'
}

/**
 * 生成中卡片的任务清单（2026-11-26，照准备 / 修复的任务清单）：三步按后端 `phase` 勾，不靠 Agent 的计划工具。
 * 图内修改 = 读取这张图 → 想怎么改 → 画出修改后的图；脚本修改中间一步是「改脚本」（Agent 在候选里改 + 预检）。
 */
export function planSteps(e: Pick<PlanEntry, 'kind' | 'phase' | 'request' | 'plan' | 'revising'>): TaskStep[] | null {
  // 一键排版没有阶段；方案已经回来、正在交给改脚本的那一小会儿（不是重改）也不再画步骤，照旧一句话
  if (isCanvasPlan(e) || (e.plan && e.revising === undefined)) return null
  const script = e.request.mode === 'script' || e.phase === 'agent'
  const at = e.phase === 'render_after' ? 2 : e.phase === 'model' || e.phase === 'agent' ? 1 : 0
  const ids = ['before', script ? 'agent' : 'model', 'after'] as const
  return ids.map((id, i) => ({ id, label: cp(`card.steps.${id}`), state: i < at ? 'done' : i === at ? 'active' : 'pending' }))
}

/**
 * 助手来源、还没弄清这句话是要改图还是只是聊天 / 提问的那一小会儿（模型还没回话）：不出「修改方案」卡，只一行思考小球。
 * 是聊天就直接交给普通对话，不会先闪一张卡再消失（用户 2026-11-27）——方案回来、正在交给普通对话的那一下（整条 out_of_scope）也还是这一行；要改图的，方案一回来卡就出（停在「画出修改后的图」那一步）。
 */
export function planUndecided(e: Pick<PlanEntry, 'from' | 'kind' | 'status' | 'plan' | 'revising' | 'request' | 'phase'>): boolean {
  // 整版 / 矢量编辑方案不在此列：用户选了对象再发的就是改图指令（没有「是不是聊天」要判，也没有落回普通对话的路），生成中就是一张卡
  return e.from === 'assistant' && !isCanvasPlan(e) && e.status === 'generating' && (!e.plan || allOutOfScope(e.plan)) && e.revising === undefined && e.request.mode !== 'script' && e.phase !== 'render_after' && e.phase !== 'agent'
}

/** 建议待办卡此刻走到哪一步（`SuggestionCard` 据此画）；全由方案条目与它续出的脚本方案卡派生，不另存状态 */
export type TodoPhase = 'choose' | 'running' | 'asking' | 'done'

/**
 * - `choose`：可勾选（还没点 / 什么都没改成又回来了）；
 * - `running`：脚本方案在生成、出好正在应用、或后面还有排着没开始的脚本待办；
 * - `asking`：脚本方案改了画出来的数据，等用户回答；
 * - `done`：至少有一类已经应用（图内那次 commit 还在、或有脚本已应用），且没有在做 / 排着的。
 * `scripts`：已经开始做的脚本待办的方案卡（按开始顺序）；`figureApplied`：图内待办此刻是已应用（以文档为准，⌘Z 撤回后不算）。
 */
export function todoPhase(e: Pick<PlanEntry, 'todoRun' | 'todoOff' | 'status'>, scripts: PlanEntry[], figureApplied: boolean): TodoPhase {
  const run = e.todoRun
  const cur = scripts.at(-1)
  // 第一条脚本待办还在发出去的路上（还没有方案条目）才算在做；开始过、但那张脚本卡已经被收起 / 收掉了（条目不在了）= 没改，不算在做——
  // 真机 2026-11-27：失败的脚本卡点「收起」后，已经落定的建议卡又转起圈来、再也停不下
  if (run && run.script.length > 0 && run.scripts.length === 0) return 'running'
  if (cur && cur.status !== 'applied') {
    if (cur.status === 'generating') return 'running'
    if (cur.status === 'ready' && pendingConfirm(cur)) return 'asking'
    if (cur.status === 'ready' && !cur.notice) return 'running'
  }
  // 上一条应用了 / 答了「先不改」、后面还排着没跳过的：马上接着做下一条
  // 上一条的卡被收起了（条目不在）= 没成，与 `advanceTodos` 一样停在这里，不再假装在做
  if (run && cur && scriptPassable(cur) && todoQueued(e).length > 0) return 'running'
  if (figureApplied || scripts.some((s) => s.status === 'applied')) return 'done'
  return 'choose'
}

/** 已改了哪几项（待办 key）：图内那次 commit 还在的 + 已应用的脚本待办；没经「改这 N 项」直接在弹窗里应用的，按此刻勾着的图内待办算 */
export function todosDone(e: PlanEntry, scripts: { todo: string; entry: PlanEntry }[], figureApplied: boolean): string[] {
  const run = e.todoRun
  const figure = figureApplied ? (run?.figure ?? todosOf(e).filter((t) => t.kind === 'figure' && todoChecked(e, t)).map((t) => t.key)) : []
  return [...figure, ...scripts.filter((x) => x.entry.status === 'applied').map((x) => x.todo)]
}

/**
 * 建议待办卡续出来的脚本方案卡，哪些还要单独画：只剩**越界**（Agent 动了别的文件——那张卡带「恢复到开始前的版本」与文件清单，是安全上的动作）。
 * 其余没做成的（Agent 没改、检查没过、图已改过、没能写进脚本……）都并进建议卡，一条一句原因（维护者 2026-11-27「并进去吧」）。
 */
export function todoScriptCardShown(e: Pick<PlanEntry, 'todoOf' | 'status' | 'failure'>): boolean {
  return !!e.todoOf && e.status === 'failed' && e.failure?.code === 'change_plan_script_overreach'
}

const REASON_MAX = 120
/** 并进建议卡的那一句「为什么没做成」：Agent 自己说了话就用它（第一段、截短），否则用这张卡本来那句失败文案 */
export function todoFailReason(e: Pick<PlanEntry, 'status' | 'failure' | 'notice'>): string {
  const said = e.failure?.params?.agent_reply
  if (typeof said === 'string' && said.trim()) {
    // Agent 被要求第一句就说为什么；没照做时，优先取「原因：…」那段（真机：第一段常是「我读完了…先停下确认」这类过场话）
    const paras = said.trim().split(/\n\s*\n/).map((x) => x.replace(/[*`_#>]/g, '').replace(/\s+/g, ' ').trim()).filter(Boolean)
    const why = paras.find((x) => /^(原因|因为|理由|Reason|Because)[:：\s]/i.test(x))
    // 「原因：」「未做改动：」这类前缀卡上已经说了（「…」没做成：），去掉
    const first = (why ?? paras[0] ?? '').replace(/^((原因|理由|Reason)[:：]\s*)?((未做改动|没有改动|未改动|没改|No change(s)? made)[:：，,。.]\s*)?/i, '')
    return first.length > REASON_MAX ? `${first.slice(0, REASON_MAX - 1)}…` : first
  }
  if (e.status === 'stale') return cp('card.stale')
  if (e.status === 'ready' && e.notice) return cp('card.applyFailed')
  return failureText(e.failure?.code ?? 'change_plan_failed', e.failure?.params, true)
}
