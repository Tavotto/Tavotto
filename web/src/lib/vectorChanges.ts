/**
 * 矢量编辑方案的改动清单（ADR 0212 第 6b 期）：后端给结构化的 `changes`（`{id, field, before, after}` / `{id, deleted}` / `{id, added}`），
 * 前端按对象分组、翻成界面语言的人话——对象叫法「类型 + 名字或文字摘录」（不出现 id），字段名走 i18n（与属性面板同一叫法），
 * 数值带单位。后端的 `echo`（中文整句、带内部名字）只留在 title 里给排查用。
 */
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import type { VectorEditResult } from '@/lib/api'
import type { VectorSnapshot } from '@/lib/vectorEdit'
import type { CanvasObject } from '@/types/document'

export interface ChangeValue {
  text: string
  /** 颜色值：界面画一个色块 */
  color?: string
}
export interface ChangeLine {
  label: string
  before?: ChangeValue
  after?: ChangeValue
}
export interface ChangeGroup {
  key: string
  /** 一行整句（新增 / 删除）或对象叫法（改字段） */
  subject: string
  lines: ChangeLine[]
}

const vt = (key: string, values?: Record<string, unknown>) => translate(`changePlan.vector.${key}`, { ns: 'ai', ...(values ?? {}) })

/** 带单位的数值字段（单位 + 取整位数，照属性面板：pt 两位、mm 两位） */
const UNIT: Record<string, string> = { strokePt: 'pt', sizePt: 'pt', borderPt: 'pt', padding: 'mm', cornerRadius: 'mm', miterLimit: '' }
/** 有专门叫法的字段；其余一律「样式」 */
const FIELDS = new Set([
  'strokePt', 'sizePt', 'color', 'fill', 'bg', 'borderColor', 'borderPt', 'bold', 'italic', 'underline', 'text', 'align', 'fontFamily', 'lineHeight',
  'strokeOpacity', 'fillOpacity', 'dash', 'cornerRadius', 'locked', 'hidden', 'name', 'stroke', 'lineCap', 'lineJoin', 'padding',
])
const GEOM: Record<string, 'pos' | 'size' | 'rot'> = { x: 'pos', y: 'pos', w: 'size', h: 'size', rotationDeg: 'rot' }
const KINDS = ['text', 'arrow', 'shape', 'path']

const round = (n: number) => String(Math.round(n * 100) / 100)
const clip = (s: string, n = 12) => (s.length > n ? `${s.slice(0, n)}…` : s)

function value(field: string, v: unknown): ChangeValue {
  if (v === null || v === undefined) return { text: vt('none') }
  if (typeof v === 'boolean') return { text: vt(v ? 'on' : 'off') }
  if (typeof v === 'number') return { text: `${round(v)}${UNIT[field] ? ` ${UNIT[field]}` : ''}` }
  if (typeof v === 'string') {
    if (/^#[0-9a-f]{3,8}$/i.test(v)) return { text: v.toUpperCase(), color: v }
    return { text: field === 'text' || field === 'name' ? `「${clip(v, 16)}」` : v }
  }
  return { text: vt('style') }
}

/** 对象叫法：类型 + 名字（没有就用文字摘录）；同一叫法出现多次时加序号区分 */
export function objectNamer(snap: VectorSnapshot, result: VectorEditResult): (id: string) => string {
  const after = new Map((result.objects ?? []).map((o) => [o.id, o]))
  const base = (id: string): string => {
    const o = (snap.byId.get(id) ?? after.get(id)) as (CanvasObject & { name?: string; text?: string }) | undefined
    const kind = o && KINDS.includes(o.type) ? vt(`kind.${o.type}`) : vt('kind.object')
    const label = (o?.name || (o?.type === 'text' ? o.text : '') || '').trim().replace(/\s+/g, ' ')
    return label ? vt('named', { kind, name: clip(label) }) : kind
  }
  const bases = new Map<string, string>()
  const ids = [...new Set([...(result.changes ?? []).map((c) => c.id), ...(result.objects ?? []).map((o) => o.id)])]
  for (const id of ids) bases.set(id, base(id))
  const seen = new Map<string, number>()
  const final = new Map<string, string>()
  const count = new Map<string, number>()
  for (const b of bases.values()) count.set(b, (count.get(b) ?? 0) + 1)
  for (const [id, b] of bases) {
    const n = (seen.get(b) ?? 0) + 1
    seen.set(b, n)
    final.set(id, (count.get(b) ?? 1) > 1 ? `${b} ${n}` : b)
  }
  return (id) => final.get(id) ?? base(id)
}

/** 按对象分组的改动清单；没有结构化改动（老后端）就返回空，调用方退回显示复述原文 */
export function changeGroups(snap: VectorSnapshot, result: VectorEditResult): ChangeGroup[] {
  const name = objectNamer(snap, result)
  const order: string[] = []
  const byId = new Map<string, { whole?: 'deleted' | 'added' | 'replaced'; fields: Map<string, { before: unknown; after: unknown }> }>()
  for (const c of result.changes ?? []) {
    let e = byId.get(c.id)
    if (!e) {
      e = { fields: new Map() }
      byId.set(c.id, e)
      order.push(c.id)
    }
    if (c.deleted) e.whole = 'deleted'
    else if (c.added) e.whole ??= 'added'
    else if (c.replaced) e.whole ??= 'replaced'
    else if (c.field) {
      const old = e.fields.get(c.field)
      e.fields.set(c.field, { before: old ? old.before : c.before, after: c.after })
    }
  }
  const out: ChangeGroup[] = []
  order.forEach((id, i) => {
    const e = byId.get(id)!
    const subject = name(id)
    if (e.whole) {
      out.push({ key: `o${i}`, subject: vt(e.whole, { subject }), lines: [] })
      return
    }
    const lines: ChangeLine[] = []
    const geom: string[] = []
    for (const [field, { before, after }] of e.fields) {
      if (GEOM[field]) {
        const g = vt(`geom.${GEOM[field]}`)
        if (!geom.includes(g)) geom.push(g)
      } else if (field === 'nodes') lines.push({ label: vt('nodes', { from: round(Number(before)), to: round(Number(after)) }) })
      else if (field === 'z') lines.push({ label: vt('z') })
      else if (field === 'type' && after === 'path') lines.push({ label: vt('toPath') })
      else if (FIELDS.has(field)) lines.push({ label: vt(`field.${field}`), before: value(field, before), after: value(field, after) })
      else if (!lines.some((l) => l.label === vt('style'))) lines.push({ label: vt('style') })
    }
    if (geom.length) lines.unshift({ label: listJoin(geom) })
    out.push({ key: `o${i}`, subject, lines })
  })
  return out
}
