/**
 * 回复草稿（ADR 0206 §六）：方案应用之后，每条被处理的意见下给用户一句起好的回复。
 * **前端确定性生成，不调模型**：只从这一组的改动（`ChangePlanChange`：属性、改前、改后）现算一句人话，
 * 认不出的属性不瞎编，整句落到「已按意见调整」。用户可改，点「发送」才走现有的回复端点。
 *
 * 只产出文案参数，不碰文档、不联网；文案在 `ai:changePlan.draft.*`。
 */
import { t as translate } from '@/i18n'
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

/** 一条改动的一句话（认不出 = null） */
function sentence(c: ChangePlanChange, el: string): string | null {
  const { prop, before, after } = c
  if (prop === 'fontsize') {
    const to = num(after)
    if (!to) return null
    const from = num(before)
    return from && from !== to ? dt('fontsize', { el, from, to }) : dt('fontsizeTo', { el, to })
  }
  if (WIDTH_PROPS.has(prop)) {
    const to = num(after)
    if (!to) return null
    const from = num(before)
    return from && from !== to ? dt('linewidth', { el, from, to }) : dt('linewidthTo', { el, to })
  }
  if (COLOR_PROPS.has(prop)) return dt('color', { el })
  if (prop === 'visible') return isBool(after) ? dt(after ? 'show' : 'hide', { el }) : null
  if (prop === 'frameon') return isBool(after) ? dt(after ? 'frameOn' : 'frameOff', { el }) : null
  if (prop === 'loc') {
    const k = locKey(after)
    return k ? dt('move', { el, where: dt(`loc.${k}`) }) : dt('moveOther', { el })
  }
  if (prop === 'text' || prop === 'label') return typeof after === 'string' && after.trim() ? dt('text', { el, to: after.trim() }) : null
  if (prop === 'weight') {
    const w = typeof after === 'string' ? after.toLowerCase() : ''
    if (w === 'bold') return dt('bold', { el })
    return w === 'normal' || w === 'regular' ? dt('unbold', { el }) : null
  }
  if (prop === 'alpha') return dt('alpha', { el })
  if (prop === 'markersize') {
    const to = num(after)
    return to ? dt('markersize', { el, to }) : null
  }
  if (prop === 'ncol') {
    const to = num(after)
    return to ? dt('ncol', { el, to }) : null
  }
  return null
}

/**
 * 一组改动的回复草稿：认得出的改动各一句（最多两句，其余并成「另有 N 处」），一句也认不出 = 「已按意见调整」。
 * `labelOf`：元素 gid → 审阅者看到的名字（没有就说「这一处」）。
 */
export function draftReply(changes: readonly ChangePlanChange[], labelOf: (gid: string) => string | undefined): string {
  const parts: string[] = []
  for (const c of changes) {
    const s = sentence(c, labelOf(c.target_gid) || dt('thisOne'))
    if (s && !parts.includes(s)) parts.push(s)
  }
  if (!parts.length) return dt('fallback')
  const rest = changes.length - Math.min(parts.length, MAX_SENTENCES)
  const head = parts.slice(0, MAX_SENTENCES).join(dt('join'))
  return rest > 0 && parts.length >= MAX_SENTENCES ? `${head}${dt('more', { count: rest })}` : head
}
