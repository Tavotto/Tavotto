// ChartModel / Binding 契约的前端镜像（ADR 0182；与 src/tavotto/engine/chartmodel.py
// 严格同源）。判据的唯一出处是引擎侧；这里只镜像闭集与校验，供 UI 判定
// 「这份模型能做什么」，不承担第二份权威。两侧共同消费
// tests/golden/chart_model_vectors.json（唯一产生者 scripts/gen_chart_model_vectors.py）。

export const MODEL_SCHEMA = 'tavotto.chart-model/1'

// 来源种类闭集（engine/chartmodel.py SOURCE_KINDS）。
export const SOURCE_KINDS = ['figure', 'declarative'] as const
export type ChartSourceKind = (typeof SOURCE_KINDS)[number]

// 轴尺度闭集（SCALES）。
export const SCALES = ['linear', 'log'] as const
export type ChartScale = (typeof SCALES)[number]

// 轴维度闭集（AXIS_KINDS）：categorical = 类别位置（0..n-1），绝不套连续数值映射。
export const AXIS_KINDS = ['numeric', 'categorical'] as const
export type ChartAxisKind = (typeof AXIS_KINDS)[number]

// 系列种类闭集（SERIES_KINDS）。
export const SERIES_KINDS = ['line', 'scatter', 'bar', 'errorbar', 'image'] as const
export type ChartSeriesKind = (typeof SERIES_KINDS)[number]

// 系列样式可写键闭集（SERIES_STYLE_KEYS）。
export const SERIES_STYLE_KEYS = [
  'color',
  'linewidth',
  'linestyle',
  'marker',
  'markersize',
  'alpha',
  'facecolor',
  'edgecolor',
  'bar_width',
  'label',
  'cmap',
  'vmin',
  'vmax',
] as const
export type ChartSeriesStyleKey = (typeof SERIES_STYLE_KEYS)[number]

// 稳定错误码闭集（ERROR_CODES）。
export const CHART_ERROR_CODES = [
  'chart_model_unsupported',
  'chart_model_invalid',
  'chart_source_mismatch',
  'chart_data_changed',
  'chart_duplicate_id',
  'chart_binding_stale',
  'chart_node_missing',
  'chart_data_invalid',
  'chart_engine_mismatch',
] as const
export type ChartErrorCode = (typeof CHART_ERROR_CODES)[number]

export interface ChartAxes {
  id: string
  rect: [number, number, number, number]
  xlim: [number, number]
  ylim: [number, number]
  xscale: ChartScale
  yscale: ChartScale
  invert_x: boolean
  invert_y: boolean
  xlabel?: string
  ylabel?: string
  kind?: ChartAxisKind
  categories?: string[]
}

export interface ChartSeries {
  id: string
  kind: ChartSeriesKind
  axes: string
  node: string
  x: string
  y: string
  yerr?: string
  grid?: string
  cbar_node?: string
  style?: Partial<Record<ChartSeriesStyleKey, number | string>>
}

export interface ChartDataset {
  columns: Record<string, number[]>
  grids?: Record<string, { values: number[][] }>
}

export interface ChartModel {
  schema: string
  rev: number
  axes: ChartAxes[]
  series: ChartSeries[]
  dataset: ChartDataset
  scene_digest: string
  data_digest: string
  engine: { matplotlib: string, numpy: string, tavotto: string }
}

export interface ChartLegendEntry {
  /** 用户确认的原始序（≥0、唯一）。 */
  index: number
  /** 确认预览里旧图例该条目的显示文字（随合同持久化，不运行时反推旧 SVG）。 */
  label: string
  /** 缺席 = custom（保持脚本原样示意）；在场 = follow_source（模型 series id）。 */
  series?: string
}

export interface ChartLegendBinding {
  /** 图例 role 名（`legend:<id>`），进 chartmanifest。 */
  id: string
  /** 旧 SVG 图例区域节点 id（场景节点；不得与 entries 的 node 重复）。 */
  node: string
  /** [x, y, w, h] 场景用户单位（与 axes.rect 同基准；区域外接矩形左上角对齐 legend 盒左上角）。 */
  rect: [number, number, number, number]
  entries: ChartLegendEntry[]
}

export interface ChartBinding {
  model_rev: number
  scene_digest: string
  entries: { object: string, node: string }[]
  legend?: ChartLegendBinding[]
}

export interface ChartVerdict {
  ok: boolean
  code?: ChartErrorCode
}

const ID_RE = /^[a-z][a-z0-9_]{0,63}$/
const OBJECT_RE = /^(axes|series):([a-z][a-z0-9_]{0,63})$/
const SCHEMA_RE = /^tavotto\.chart-model\/(\d+)$/

// ---------------------------------------------------------------------------
// 与引擎侧 digest_of 逐字节同源的摘要：identityBytes（类型标记 + IEEE 754 位
// 模式，见 engine/chartmodel.py 的 identity_bytes——1 与 1.0 是同一个值，非整值
// 按 big-endian double 的 hex 记）+ 同步 sha256（跨语言合同，必须与 Python
// hashlib 逐位一致，不能加盐不能截断）。
// ---------------------------------------------------------------------------

const INT53 = 2 ** 53

function f64Hex(v: number): string {
  const buf = new ArrayBuffer(8)
  new DataView(buf).setFloat64(0, v)
  return Array.from(new Uint8Array(buf), b => b.toString(16).padStart(2, '0')).join('')
}

function utf8Hex(s: string): string {
  return Array.from(new TextEncoder().encode(s), b => b.toString(16).padStart(2, '0')).join('')
}

/** 引擎侧 `identity_bytes` 的镜像：产出 ASCII 字节串，两侧逐字节一致。 */
export function identityBytes(value: unknown): string {
  if (value === null) return 'N'
  if (typeof value === 'boolean') return value ? 'T' : 'F'
  if (typeof value === 'number') {
    if (Number.isInteger(value) && Math.abs(value) < INT53) return `i${value};`
    return `d${f64Hex(value)}`
  }
  if (typeof value === 'string') return `s${utf8Hex(value).length / 2}:${utf8Hex(value)}`
  if (Array.isArray(value)) return `[${value.map(identityBytes).join('')}]`
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>).sort()
    return `{${keys.map(k => `${identityBytes(k)}${identityBytes((value as Record<string, unknown>)[k])}`).join('')}}`
  }
  throw new Error('identity: unsupported value')
}

function sha256Hex(text: string): string {
  const bytes = new TextEncoder().encode(text)
  const H = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ])
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ])
  const bitLen = bytes.length * 8
  const padded = new Uint8Array((((bytes.length + 8) >> 6) + 1) << 6)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  const dv = new DataView(padded.buffer)
  dv.setUint32(padded.length - 8, Math.floor(bitLen / 2 ** 32))
  dv.setUint32(padded.length - 4, bitLen >>> 0)
  const w = new Uint32Array(64)
  const rr = (x: number, n: number) => (x >>> n) | (x << (32 - n))
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4)
    for (let i = 16; i < 64; i++) {
      const s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = H
    for (let i = 0; i < 64; i++) {
      const S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)
      const ch = (e & f) ^ (~e & g)
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0
      const S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (S0 + maj) >>> 0
      h = g; g = f; f = e
      e = (d + t1) >>> 0
      d = c; c = b; b = a
      a = (t1 + t2) >>> 0
    }
    H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0
    H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0
  }
  let hex = ''
  for (let i = 0; i < 8; i++) hex += H[i].toString(16).padStart(8, '0')
  return hex
}

/** 引擎侧 `digest_of` 的镜像：模型/数据身份的唯一算法。 */
export function digestOf(value: unknown): string {
  return sha256Hex(identityBytes(value))
}

function invalid(): ChartVerdict {
  return { ok: false, code: 'chart_model_invalid' }
}

function reject(code: ChartErrorCode): ChartVerdict {
  return { ok: false, code }
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function keysAllowed(obj: object, allowed: readonly string[]): boolean {
  return Object.keys(obj).every(k => (allowed as readonly string[]).includes(k))
}

/** 与引擎侧 validate_model 同一判据（闭集、有限数、摘要一致性、唯一 id）。 */
export function validateChartModel(model: unknown): ChartVerdict {
  if (typeof model !== 'object' || model === null) return invalid()
  const m = model as Record<string, unknown>
  if (!keysAllowed(m, ['schema', 'rev', 'axes', 'series', 'dataset', 'scene_digest', 'data_digest', 'engine'])) return invalid()
  const schema = m.schema
  if (typeof schema !== 'string' || !SCHEMA_RE.test(schema)) return invalid()
  const major = Number(SCHEMA_RE.exec(schema)![1])
  if (major > 1) return reject('chart_model_unsupported')
  if (typeof m.rev !== 'number' || !Number.isInteger(m.rev) || m.rev < 1) return invalid()

  const axes = m.axes
  if (!Array.isArray(axes) || axes.length === 0) return invalid()
  const ids = new Set<string>()
  for (const raw of axes) {
    if (typeof raw !== 'object' || raw === null) return invalid()
    const ax = raw as Record<string, unknown>
    if (!keysAllowed(ax, ['id', 'rect', 'xlim', 'ylim', 'xscale', 'yscale', 'invert_x', 'invert_y', 'xlabel', 'ylabel', 'kind', 'categories'])) return invalid()
    if (typeof ax.id !== 'string' || !ID_RE.test(ax.id)) return invalid()
    if (ids.has(ax.id)) return reject('chart_duplicate_id')
    ids.add(ax.id)
    const rect = ax.rect
    if (!Array.isArray(rect) || rect.length !== 4
      || !rect.every(v => isFiniteNumber(v))) return invalid()
    if ((rect[2] as number) <= 0 || (rect[3] as number) <= 0) return invalid()
    for (const key of ['xlim', 'ylim'] as const) {
      const lim = ax[key]
      if (!Array.isArray(lim) || lim.length !== 2 || !lim.every(isFiniteNumber)
        || (lim[0] as number) === (lim[1] as number)) return invalid()
    }
    for (const key of ['xscale', 'yscale'] as const) {
      if (!SCALES.includes(ax[key] as ChartScale)) return invalid()
    }
    for (const key of ['invert_x', 'invert_y'] as const) {
      if (typeof ax[key] !== 'boolean') return invalid()
    }
    const axisKind = (ax.kind ?? 'numeric') as ChartAxisKind
    if (!AXIS_KINDS.includes(axisKind)) return invalid()
    if (axisKind === 'categorical') {
      const cats = ax.categories
      if (!Array.isArray(cats) || cats.length === 0
        || !cats.every(c => typeof c === 'string' && c)) return invalid()
    }
    // log 轴的范围必须为正（引擎侧同款判据）
    for (const pair of [['xlim', 'xscale'], ['ylim', 'yscale']] as const) {
      const [limKey, scaleKey] = pair
      if (ax[scaleKey] === 'log') {
        const lim = ax[limKey] as number[]
        if (!lim.every(v => (v as number) > 0)) return reject('chart_data_invalid')
      }
    }
  }

  const dataset = m.dataset
  if (typeof dataset !== 'object' || dataset === null) return invalid()
  const ds = dataset as Record<string, unknown>
  if (!keysAllowed(ds, ['columns', 'grids'])) return invalid()
  const columns = ds.columns
  if (typeof columns !== 'object' || columns === null) return invalid()
  const colNames = new Set<string>()
  for (const [name, col] of Object.entries(columns as Record<string, unknown>)) {
    if (!name) return invalid()
    colNames.add(name)
    if (!Array.isArray(col)) return invalid()
    // 数值列元素必须是有限数；文本列（类别/标签）允许非空字符串
    if (!col.every(v => isFiniteNumber(v) || (typeof v === 'string' && v))) return invalid()
  }

  // 网格（热图/标量场）：矩形、有限数（engine/chartmodel.py grids 同款判据）
  const grids = ds.grids
  if (grids !== undefined) {
    if (typeof grids !== 'object' || grids === null) return invalid()
    for (const grid of Object.values(grids as Record<string, unknown>)) {
      if (typeof grid !== 'object' || grid === null) return invalid()
      const g = grid as Record<string, unknown>
      if (!keysAllowed(g, ['values'])) return invalid()
      const values = g.values
      if (!Array.isArray(values) || values.length === 0) return reject('chart_data_invalid')
      let width = -1
      for (const row of values as unknown[]) {
        if (!Array.isArray(row) || row.length === 0) return reject('chart_data_invalid')
        if (width === -1) width = row.length
        else if (row.length !== width) return reject('chart_data_invalid')
        if (!row.every(isFiniteNumber)) return reject('chart_data_invalid')
      }
    }
  }

  const series = m.series
  if (!Array.isArray(series) || series.length === 0) return invalid()
  const seriesIds = new Set<string>()
  for (const raw of series) {
    if (typeof raw !== 'object' || raw === null) return invalid()
    const sr = raw as Record<string, unknown>
    if (!keysAllowed(sr, ['id', 'kind', 'axes', 'node', 'x', 'y', 'yerr', 'grid', 'cbar_node', 'style'])) return invalid()
    if (typeof sr.id !== 'string' || !ID_RE.test(sr.id)) return invalid()
    if (ids.has(sr.id) || seriesIds.has(sr.id)) return reject('chart_duplicate_id')
    seriesIds.add(sr.id)
    if (!SERIES_KINDS.includes(sr.kind as ChartSeriesKind)) return invalid()
    if (typeof sr.axes !== 'string' || !ids.has(sr.axes)) return invalid()
    if (typeof sr.node !== 'string' || !sr.node) return invalid()
    if (sr.kind === 'image') {
      if (typeof sr.grid !== 'string'
        || !Object.keys((ds.grids as Record<string, unknown>) ?? {}).includes(sr.grid)) {
        return reject('chart_data_invalid')
      }
    } else {
      for (const key of ['x', 'y'] as const) {
        if (typeof sr[key] !== 'string' || !colNames.has(sr[key] as string)) return reject('chart_data_invalid')
      }
    }
    if (sr.style !== undefined) {
      if (typeof sr.style !== 'object' || sr.style === null) return invalid()
      if (!keysAllowed(sr.style, SERIES_STYLE_KEYS)) return invalid()
    }
  }

  if (typeof m.scene_digest !== 'string' || !m.scene_digest) return invalid()
  if (typeof m.data_digest !== 'string' || !m.data_digest) return invalid()
  // 摘要口径与引擎侧一致：有 grids 时一并进入（否则只按 columns）
  const digestPayload
    = ds.grids !== undefined && Object.keys(ds.grids as object).length > 0
      ? { columns, grids: ds.grids }
      : { columns }
  if (m.data_digest !== digestOf(digestPayload)) return reject('chart_data_changed')
  const engine = m.engine
  if (typeof engine !== 'object' || engine === null) return invalid()
  const en = engine as Record<string, unknown>
  if (!keysAllowed(en, ['matplotlib', 'numpy', 'tavotto'])) return invalid()
  for (const key of ['matplotlib', 'numpy', 'tavotto'] as const) {
    if (typeof en[key] !== 'string' || !(en[key] as string)) return invalid()
  }
  return { ok: true }
}

/** 与引擎侧 validate_binding 同一判据（代际一致 + 场景一致 + 对象/节点唯一）。 */
export function validateChartBinding(binding: unknown, model: ChartModel): ChartVerdict {
  if (typeof binding !== 'object' || binding === null) return invalid()
  const b = binding as Record<string, unknown>
  if (!keysAllowed(b, ['model_rev', 'scene_digest', 'entries', 'legend'])) return invalid()
  if (b.model_rev !== model.rev) return reject('chart_binding_stale')
  if (b.scene_digest !== model.scene_digest) return reject('chart_source_mismatch')
  const entries = b.entries
  if (!Array.isArray(entries) || entries.length === 0) return invalid()
  const known = new Set([
    ...model.axes.map(ax => `axes:${ax.id}`),
    ...model.series.map(sr => `series:${sr.id}`),
  ])
  const seriesObjects = new Set<string>()
  const nodes = new Set<string>()
  for (const raw of entries) {
    if (typeof raw !== 'object' || raw === null) return invalid()
    const e = raw as Record<string, unknown>
    if (!keysAllowed(e, ['object', 'node'])) return invalid()
    if (typeof e.object !== 'string' || !OBJECT_RE.test(e.object) || !known.has(e.object)) return invalid()
    // 系列与数据节点一一对应；轴区域可指定多个节点（普通 SVG 的轴是散件）
    if (e.object.startsWith('series:')) {
      if (seriesObjects.has(e.object)) return reject('chart_duplicate_id')
      seriesObjects.add(e.object)
    }
    if (typeof e.node !== 'string' || !e.node) return invalid()
    if (nodes.has(e.node)) return reject('chart_duplicate_id')
    nodes.add(e.node)
  }
  // 可选 legend 合同（N4）：与 engine/chartmodel.py validate_legend_spec 同一判据。
  if (b.legend !== undefined) {
    const legend = b.legend
    if (!Array.isArray(legend) || legend.length === 0) return invalid()
    const legendIds = new Set<string>()
    const legendNodes = new Set<string>()
    for (const rawItem of legend) {
      if (typeof rawItem !== 'object' || rawItem === null) return invalid()
      const item = rawItem as Record<string, unknown>
      if (!keysAllowed(item, ['id', 'node', 'rect', 'entries'])) return invalid()
      if (typeof item.id !== 'string' || !ID_RE.test(item.id)) return invalid()
      if (legendIds.has(item.id)) return reject('chart_duplicate_id')
      legendIds.add(item.id)
      if (typeof item.node !== 'string' || !item.node) return invalid()
      if (legendNodes.has(item.node)) return reject('chart_duplicate_id')
      legendNodes.add(item.node)
      const rect = item.rect
      if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(isFiniteNumber)) return invalid()
      if ((rect[2] as number) <= 0 || (rect[3] as number) <= 0) return invalid()
      const items = item.entries
      if (!Array.isArray(items) || items.length === 0) return invalid()
      const indexes = new Set<number>()
      for (const rawEntry of items) {
        if (typeof rawEntry !== 'object' || rawEntry === null) return invalid()
        const le = rawEntry as Record<string, unknown>
        if (!keysAllowed(le, ['index', 'series', 'label'])) return invalid()
        if (typeof le.index !== 'number' || !Number.isInteger(le.index) || le.index < 0) return invalid()
        if (indexes.has(le.index)) return reject('chart_duplicate_id')
        indexes.add(le.index)
        if (typeof le.label !== 'string' || !le.label) return invalid()
        if (le.series !== undefined
          && (typeof le.series !== 'string' || !model.series.some(sr => sr.id === le.series))) return invalid()
      }
    }
    // 图例区域节点与绑定条目节点互斥（engine 侧同一判据）
    for (const node of legendNodes) {
      if (nodes.has(node)) return reject('chart_duplicate_id')
    }
  }
  return { ok: true }
}
