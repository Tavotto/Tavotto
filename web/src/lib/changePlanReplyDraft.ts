/**
 * 回复草稿（ADR 0206 §六）：方案应用之后，每条被处理的意见下给用户一句起好的回复。
 * **前端确定性生成，不调模型**：只从这一组的改动（`ChangePlanChange`：属性、改前、改后）现算一句人话，
 * 认不出的属性不瞎编，整句落到「已按意见调整」。用户可改，点「发送」才走现有的回复端点。
 *
 * 只产出文案参数，不碰文档、不联网；文案在 `ai:changePlan.draft.*`。
 */
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import type { ChangePlanChange } from '@/lib/api'

const dt = (key: string, values?: Record<string, unknown>) => translate(`changePlan.draft.${key}`, { ns: 'ai', ...(values ?? {}) })

/** 一句里最多说几处；多出来的并成「（另有 N 处）」 */
const MAX_SENTENCES = 2

const COLOR_PROPS = new Set(['color', 'facecolor', 'edgecolor', 'spine_color', 'spine_left_color', 'handle_color'])
const WIDTH_PROPS = new Set(['linewidth', 'lw', 'spine_linewidth', 'spine_left_linewidth', 'handle_linewidth', 'minor_width'])

/** matplotlib 的 `loc` 数字码 */
const LOC_CODES = ['best', 'upper right', 'upper left', 'lower left', 'lower right', 'right', 'center left', 'center right', 'lower center', 'upper center', 'center']
const LOC_KEYS = new Set(LOC_CODES.map((s) => s.replace(' ', '_')))

/** 数字显示：最多两位小数，去掉多余的 0；不是有限数字 = null */
function num(v: unknown): string | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : null
}

function locKey(v: unknown): string | null {
  if (typeof v === 'number') return LOC_CODES[v]?.replace(' ', '_') ?? null
  if (typeof v !== 'string') return null
  const k = v.trim().toLowerCase().replace(/[\s-]+/g, '_')
  return LOC_KEYS.has(k) ? k : null
}

const isBool = (v: unknown): v is boolean => typeof v === 'boolean'

/** 一条改动的说法：同一个模板、同样的参数 = 同一句，只是元素不同（合并成一句） */
interface Phrase {
  tpl: string
  params: Record<string, unknown>
}

function phrase(c: ChangePlanChange): Phrase | null {
  const { prop, before, after } = c
  if (prop === 'fontsize') {
    const to = num(after)
    if (!to) return null
    const from = num(before)
    return from && from !== to ? { tpl: 'fontsize', params: { from, to } } : { tpl: 'fontsizeTo', params: { to } }
  }
  if (WIDTH_PROPS.has(prop)) {
    const to = num(after)
    if (!to) return null
    const from = num(before)
    return from && from !== to ? { tpl: 'linewidth', params: { from, to } } : { tpl: 'linewidthTo', params: { to } }
  }
  if (COLOR_PROPS.has(prop)) return { tpl: 'color', params: {} }
  if (prop === 'visible') return isBool(after) ? { tpl: after ? 'show' : 'hide', params: {} } : null
  if (prop === 'frameon') return isBool(after) ? { tpl: after ? 'frameOn' : 'frameOff', params: {} } : null
  if (prop === 'loc') {
    const k = locKey(after)
    return k ? { tpl: 'move', params: { where: dt(`loc.${k}`) } } : { tpl: 'moveOther', params: {} }
  }
  if (prop === 'text' || prop === 'label') return typeof after === 'string' && after.trim() ? { tpl: 'text', params: { to: after.trim() } } : null
  if (prop === 'weight') {
    const w = typeof after === 'string' ? after.toLowerCase() : ''
    if (w === 'bold') return { tpl: 'bold', params: {} }
    return w === 'normal' || w === 'regular' ? { tpl: 'unbold', params: {} } : null
  }
  if (prop === 'alpha') return { tpl: 'alpha', params: {} }
  if (prop === 'markersize') {
    const to = num(after)
    return to ? { tpl: 'markersize', params: { to } } : null
  }
  if (prop === 'ncol') {
    const to = num(after)
    return to ? { tpl: 'ncol', params: { to } } : null
  }
  return null
}

/** 中文与英文 / 数字相邻处补一个空格（模板拼出来的「把Y 轴」读着像拼接） */
export function spaceCjk(s: string): string {
  return s.replace(/([\u4e00-\u9fff])([A-Za-z0-9])/g, '$1 $2').replace(/([A-Za-z0-9])([\u4e00-\u9fff])/g, '$1 $2')
}

/** 几处元素的合并称呼：两处以内报名字，再多说「这 N 处」 */
const elementsOf = (names: string[]): string => (names.length <= 2 ? listJoin(names) : dt('many', { count: names.length }))

/**
 * 一组改动的回复草稿：同一种说法（同属性、同改法）的多处合并成一句；认得出的至多说两类，其余并成「（另有 N 处）」；
 * 一句也认不出 = 「已按意见调整」。`labelOf`：元素 gid → 审阅者看到的名字（没有就说「这一处」）。
 */
export function draftReply(changes: readonly ChangePlanChange[], labelOf: (gid: string) => string | undefined): string {
  const merged = new Map<string, { p: Phrase; names: string[]; n: number }>()
  for (const c of changes) {
    const p = phrase(c)
    if (!p) continue
    const k = JSON.stringify([p.tpl, p.params])
    const m = merged.get(k) ?? { p, names: [], n: 0 }
    const name = labelOf(c.target_gid) || dt('thisOne')
    if (!m.names.includes(name)) m.names.push(name)
    m.n += 1
    merged.set(k, m)
  }
  if (!merged.size) return dt('fallback')
  const all = [...merged.values()]
  const shown = all.slice(0, MAX_SENTENCES)
  const head = shown.map((m) => dt(m.p.tpl, { ...m.p.params, el: elementsOf(m.names) })).join(dt('join'))
  const rest = all.slice(MAX_SENTENCES).reduce((a, m) => a + m.n, 0)
  return spaceCjk(rest > 0 ? `${head}${dt('more', { count: rest })}` : head)
}
