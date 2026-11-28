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
  /** 悬停说明（图内方案：合并掉的那几处各是什么、色值 hex） */
  title?: string
  /** 行尾一小段灰字（「3 处」） */
  extra?: string
  /** 颜色只画色块，不写色值（图内方案） */
  compact?: boolean
}
export interface ChangeGroup {
  key: string
  /** 第几步操作（补丁里的下标，0 起）；老后端没有 = 不能按步勾选 */
  op?: number
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
  // 非样式 / 少见的字段也各有准确的叫法（落到「样式」兜底的只剩真没见过的键）
  'subpaths', 'anchor', 'layoutPinned', 'start', 'end', 'shape', 'sides', 'starRatio', 'headStart', 'headEnd', 'headSizeStart', 'headSizeEnd',
  'fillGradient', 'strokeGradient', 'fillRule', 'strokeAlign', 'dashPattern', 'miterLimit', 'interpretation', 'textFormat', 'clipMask', 'clipOff',
])
/** 编组相关的键：写「编组」，值变成没有 = 「取消编组」 */
const GROUP_FIELDS = new Set(['groupId', 'groupPath'])
/** 值很长 / 是结构的字段：只说改了它，不列前后值 */
const NO_VALUE = new Set(['subpaths', 'anchor', 'start', 'end', 'fillGradient', 'strokeGradient', 'dashPattern', 'layoutPinned', 'clipMask', 'clipOff'])
const GEOM: Record<string, 'pos' | 'size' | 'rot'> = { x: 'pos', y: 'pos', w: 'size', h: 'size', rotationDeg: 'rot' }
const KINDS = ['text', 'arrow', 'shape', 'path']

const isEmpty = (v: unknown) => v === null || v === undefined || (Array.isArray(v) && v.length === 0)
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

/** 方位点的叫法（后端 `vectorscene` 的九个点） */
const POINTS = ['nw', 'n', 'ne', 'w', 'center', 'e', 'sw', 's', 'se']

/**
 * 新增对象「锚在哪」一行：取对象上落盘的持久锚点（`anchor.anchors[]`：子图方位点 / 数据点 / 图里的元素），子图换成界面叫法（第 N 张图），
 * 不出现 id 与短号；没有锚点（框住子图的 box 之类）= 没有这一行
 */
function anchorLineOf(snap: VectorSnapshot, result: VectorEditResult): (id: string) => ChangeLine | null {
  const objs = new Map((result.objects ?? []).map((o) => [o.id, o as unknown as { anchor?: { anchors?: Record<string, unknown>[] } }]))
  // 没量到的子图：标注是按它的外框放的，说「外框」（说准，不说成锚在图里的哪一点）
  const panel = (id: unknown) => {
    const label = typeof id === 'string' ? (snap.panelLabels.get(id) ?? vt('panelOther')) : vt('panelOther')
    const ref = typeof id === 'string' ? snap.panelRefs.get(id) : undefined
    return ref && result.unmeasured?.[ref] ? vt('panelFrame', { panel: label }) : label
  }
  return (id) => {
    const roles = objs.get(id)?.anchor?.anchors ?? []
    const where: string[] = []
    for (const r of roles) {
      const data = r.data as { panel?: unknown; x?: unknown; y?: unknown } | undefined
      const el = r.element as { panel?: unknown } | undefined
      const pn = r.panel as { id?: unknown; point?: unknown } | undefined
      const text = data
        ? vt('at.data', { panel: panel(data.panel), x: String(data.x), y: String(data.y) })
        : el
          ? vt('at.element', { panel: panel(el.panel) })
          : pn
            ? vt('at.panel', { panel: panel(pn.id), point: typeof pn.point === 'string' && POINTS.includes(pn.point) ? vt(`point.${pn.point}`) : '' }).trim()
            : null
      if (text && !where.includes(text)) where.push(text)
    }
    return where.length ? { label: vt('anchoredAt', { where: listJoin(where) }) } : null
  }
}

/** 按步骤（后端 `changes[].op`）分组的改动清单：一行 = 一步操作，行内按对象和字段；没有 `op`（老后端）时每个对象一行、不能勾选 */
export function changeGroups(snap: VectorSnapshot, result: VectorEditResult): ChangeGroup[] {
  const changes = result.changes ?? []
  const name = objectNamer(snap, result)
  const anchorOf = anchorLineOf(snap, result)
  const stepped = changes.length > 0 && changes.every((c) => typeof c.op === 'number')
  if (!stepped) return objectGroups(name, changes, anchorOf).map((g, i) => ({ ...g, key: `o${i}` }))
  const ops = [...new Set(changes.map((c) => c.op as number))].sort((a, b) => a - b)
  return ops.map((op) => {
    const per = objectGroups(name, changes.filter((c) => c.op === op), anchorOf)
    if (per.length === 1) return { ...per[0], key: `op${op}`, op }
    // 一步动了好几个对象：一行写全部对象，字段行前面带上是谁的
    const lines = per.flatMap((g) => g.lines.map((l) => ({ ...l, label: vt('lineOf', { subject: g.subject, label: l.label }) })))
    return { key: `op${op}`, op, subject: listJoin(per.map((g) => g.subject)), lines }
  })
}

function objectGroups(name: (id: string) => string, changes: NonNullable<VectorEditResult['changes']>, anchorOf: (id: string) => ChangeLine | null): ChangeGroup[] {
  const order: string[] = []
  const byId = new Map<string, { whole?: 'deleted' | 'added' | 'replaced'; fields: Map<string, { before: unknown; after: unknown }> }>()
  for (const c of changes) {
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
      const where = e.whole === 'added' || e.whole === 'replaced' ? anchorOf(id) : null
      out.push({ key: `o${i}`, subject: vt(e.whole, { subject }), lines: where ? [where] : [] })
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
      else if (GROUP_FIELDS.has(field)) {
        const label = vt(isEmpty(after) ? 'ungroup' : 'group')
        if (!lines.some((l) => l.label === label)) lines.push({ label })
      } else if (NO_VALUE.has(field)) lines.push({ label: vt(`field.${field}`) })
      else if (FIELDS.has(field)) lines.push({ label: vt(`field.${field}`), before: value(field, before), after: value(field, after) })
      else if (!lines.some((l) => l.label === vt('style'))) lines.push({ label: vt('style') })
    }
    if (geom.length) lines.unshift({ label: listJoin(geom) })
    out.push({ key: `o${i}`, subject, lines })
  })
  return out
}
