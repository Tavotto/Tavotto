/**
 * 诊断事件的 **allowlist 序列化器**（ADR 0016 §4）——诊断包的唯一出口。
 *
 * 为什么不是 denylist：Tavotto 后续每加一个功能，都可能往 store 里塞进论文
 * 标题、annotation 文本或绝对路径。「先 dump 再删敏感字段」的失效方式是
 * **静默**的——没有任何一条用例会因为「新字段忘了删」而变红，而泄漏已经发生。
 *
 * 关键的实现细节是**遍历方向**：下面的 `serialize` 遍历的是 **schema**，
 * 不是输入对象的键。输入里多出来的字段不是「被过滤掉」，而是**根本没被读过**。
 * 这两者在正确的实现里结果相同，在有 bug 的实现里差别就是一次泄漏。
 *
 * 与 `types.ts` 的分工：类型挡住写代码时的手滑（编译期），这里挡住类型被
 * `as any` 掉之后的一切（运行期）。两道防线各有判据，少一道都有用例红。
 */
import { HASH_PATTERN, diagnosticHash } from './hash'
import type { DiagnosticEvent, DiagnosticEventType, RecordedEvent } from './types'

/**
 * 技术 gid 的形状：`axes_0.title` / `fig.texts_0` / `cbar:axes_0:0` / `figure`。
 *
 * **必须小写字母开头、且不含大写**。引擎编出来的 gid 无一例外是这个样子
 * （`axes_{i}` / `fig.` / `texts_` / `xticklabels_` / `legend_` / `cbar:`），
 * 而「只按字符集判断」挡不住 `SUPER_SECRET_API_KEY_67890` 那种全大写下划线串
 * ——它字符集完全合法。gid 是少数几个**原样进诊断包**的字符串之一
 * （§9 的默认允许项），判据松一点点，标识位就成了内容的通道。
 *
 * 判错的代价是可控的：真 gid 万一有大写，它只是变成 `gid:<hash>`，
 * 诊断少一点可读性，不会漏也不会崩。
 */
const GID_PATTERN = /^[a-z][a-z0-9_.:-]{0,63}$/
/** 内部操作键 / 历史标签 key：`alignMode.left` / `element.setProp` */
const KEY_PATTERN = /^[A-Za-z0-9_.-]{1,48}$/
/** matplotlib 属性名 / 文档字段名 */
const PROP_PATTERN = /^[A-Za-z0-9_.-]{1,48}$/

const MAX_INT = 1_000_000_000
/**
 * 时间戳的上界单独给。epoch 毫秒现在就是 1.7e12，用 MAX_INT 卡它等于把**每一条**
 * 事件都判为非法——而且是在导出那一刻才发作，环里看着好好的。
 * 4e12 ≈ 公元 2096 年。
 */
const MAX_TIMESTAMP = 4_000_000_000_000
/** 几何数组：每条最多 8 个数（endpoints_frac 是 4，bbox 是 4，anchor 是 2） */
const MAX_GEOM_NUMS = 8

type FieldKind =
  | { k: 'bool' }
  | { k: 'int' }
  | { k: 'num' }
  /** 带前缀的短 hash，或 null */
  | { k: 'hash' }
  | { k: 'gid' }
  | { k: 'key' }
  | { k: 'enum'; values: readonly string[] }
  | { k: 'gids'; max: number }
  | { k: 'patches'; max: number }
  | { k: 'geom'; max: number }
  | { k: 'nums'; max: number }

const BOOL: FieldKind = { k: 'bool' }
const INT: FieldKind = { k: 'int' }
const HASH: FieldKind = { k: 'hash' }
const GID: FieldKind = { k: 'gid' }
const KEY: FieldKind = { k: 'key' }
const NUMS = (max: number): FieldKind => ({ k: 'nums', max })
const ENUM = (...values: string[]): FieldKind => ({ k: 'enum', values })

/* -------------------------------------------------------------------------- */
/*  每种事件的字段表。**这张表就是隐私边界本身**                                  */
/* -------------------------------------------------------------------------- */

const HISTORY_COUNTS = {
  past_count: INT,
  future_count: INT,
} as const

const VARIANT_TRIPLE = {
  document_variant: HASH,
  display_variant: HASH,
  authority_variant: HASH,
} as const

export const EVENT_SCHEMA: Record<DiagnosticEventType, Record<string, FieldKind>> = {
  /* ---- Document / History ---- */
  'document.commit': {
    label_key: KEY,
    patch_count: INT,
    ...HISTORY_COUNTS,
    txn_open: BOOL,
    document_hash_before: HASH,
    document_hash_after: HASH,
    patches: { k: 'patches', max: 24 },
  },
  'transaction.begin': { label_key: KEY, replaced_open_txn: BOOL },
  'transaction.end': {
    label_key: KEY,
    patch_count: INT,
    past_count: INT,
    document_hash_after: HASH,
  },
  'transaction.cancel': { label_key: KEY, patch_count: INT },
  'undo.request': { ...HISTORY_COUNTS, txn_open: BOOL },
  'redo.request': { ...HISTORY_COUNTS, txn_open: BOOL },
  'undo.complete': {
    ok: BOOL,
    label_key: KEY,
    ...HISTORY_COUNTS,
    document_hash_before: HASH,
    document_hash_after: HASH,
  },
  'redo.complete': {
    ok: BOOL,
    label_key: KEY,
    ...HISTORY_COUNTS,
    document_hash_before: HASH,
    document_hash_after: HASH,
  },

  /* ---- Selection ---- */
  'selection.changed': {
    panel: HASH,
    selection_kind: ENUM('none', 'element', 'object', 'mixed'),
    selected_count: INT,
    selected_gids: { k: 'gids', max: 24 },
    object_count: INT,
  },

  /* ---- Render ---- */
  'render.request': {
    file: HASH,
    variant: HASH,
    policy: ENUM('immediate', 'defer', 'none', 'sync'),
    preview_dpi: INT,
  },
  'render.success': {
    file: HASH,
    variant: HASH,
    duration_ms: INT,
    element_count: INT,
    size_mm: NUMS(2),
    warning_count: INT,
    rev: INT,
  },
  'render.error': { file: HASH, variant: HASH, duration_ms: INT, code: KEY },
  'render.stale': { file: HASH, variant_count: INT },
  'render.svg_evicted': {
    file: HASH,
    variant: HASH,
    scope: ENUM('file', 'global'),
    bytes: INT,
  },
  'display.source_changed': {
    panel: HASH,
    file: HASH,
    ...VARIANT_TRIPLE,
    exact: BOOL,
    render_status: ENUM('idle', 'rendering', 'ready', 'error'),
    stale: BOOL,
  },
  'authority.ready': { panel: HASH, document_variant: HASH, authority_variant: HASH },
  'authority.unavailable': { panel: HASH, document_variant: HASH, authority_variant: HASH },

  /* ---- Element interaction ---- */
  'element.drag.begin': dragBegin(),
  'axes.drag.begin': dragBegin(),
  'resize.begin': dragBegin(),
  'element.drag.commit': dragCommit(),
  'axes.drag.commit': dragCommit(),
  'resize.commit': dragCommit(),
  'element.drag.cancel': { panel: HASH, gid: GID, cancelled: BOOL },

  /* ---- Alignment ---- */
  'align.request': {
    mode: KEY,
    panel: HASH,
    selected_count: INT,
    ...VARIANT_TRIPLE,
    exact_authority: BOOL,
    input_geometry: { k: 'geom', max: 32 },
  },
  'align.blocked': {
    mode: KEY,
    panel: HASH,
    reason: ENUM('authority_unavailable', 'authority_stale', 'no_manifest', 'panel_missing'),
    ...VARIANT_TRIPLE,
  },
  'align.commit': {
    mode: KEY,
    panel: HASH,
    selected_count: INT,
    ...VARIANT_TRIPLE,
    exact_authority: BOOL,
    input_geometry: { k: 'geom', max: 32 },
    output_geometry: { k: 'geom', max: 32 },
    patch_count: INT,
    move_count: INT,
  },
  'align.noop': {
    mode: KEY,
    panel: HASH,
    reason: ENUM('empty_selection', 'no_geometry_change', 'nothing_to_write'),
  },

  /* ---- Preview ---- */
  'preview.begin': {
    session: HASH,
    panel: HASH,
    render_variant: HASH,
    history_mode: ENUM('gesture', 'granular'),
  },
  'preview.commit': {
    session: HASH,
    panel: HASH,
    render_variant: HASH,
    await_variant: HASH,
    patch_count: INT,
  },
  'preview.cancel': previewEnd(),
  'preview.retire': previewEnd(),

  /* ---- Layout version ---- */
  'layout_version.save': { version: HASH, document_hash: HASH, auto: BOOL },
  'layout_version.restore.request': {
    version: HASH,
    document_hash: HASH,
    ...HISTORY_COUNTS,
  },
  'layout_version.restore.complete': {
    version: HASH,
    document_hash_before: HASH,
    document_hash_after: HASH,
    auto_backup_created: BOOL,
    ...HISTORY_COUNTS,
  },

  /* ---- Diagnostics 自身 ---- */
  'invariant.violation': {
    kind: ENUM(
      'geometry_authority_mismatch',
      'selected_gid_missing_from_exact_manifest',
      'standalone_action_inside_unrelated_transaction',
      'document_display_variant_diverged',
      'undo_complete_but_authority_stale',
      'preview_session_survived_commit',
    ),
    operation: KEY,
    panel: HASH,
    ...VARIANT_TRIPLE,
  },
  'diagnostics.export': { trace_count: INT, panel_count: INT },
}

function dragBegin(): Record<string, FieldKind> {
  return {
    panel: HASH,
    gid: GID,
    prop: KEY,
    ...VARIANT_TRIPLE,
    exact_authority: BOOL,
    anchor_from_document: BOOL,
  }
}

function dragCommit(): Record<string, FieldKind> {
  return {
    panel: HASH,
    gid: GID,
    prop: KEY,
    patch_count: INT,
    document_variant: HASH,
    authority_variant: HASH,
    exact_authority: BOOL,
  }
}

function previewEnd(): Record<string, FieldKind> {
  return {
    session: HASH,
    panel: HASH,
    reason: ENUM(
      'pointer_cancel',
      'committed',
      'authority_swapped',
      'authority_failed',
      'reset',
    ),
    duration_ms: INT,
  }
}

/* -------------------------------------------------------------------------- */
/*  逐 kind 的取值器                                                            */
/* -------------------------------------------------------------------------- */

/** 有限数字，六位小数——分数坐标够用，也顺手掐掉 1e-17 那种噪声尾巴 */
function num(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return Math.round(value * 1e6) / 1e6
}

function int(value: unknown, max: number = MAX_INT): number | undefined {
  // bool 是 number 的近亲：`patch_count: true` 这种值不能悄悄通过
  if (typeof value !== 'number' || !Number.isInteger(value)) return undefined
  if (value < 0 || value > max) return undefined
  return value
}

/**
 * gid：形状对就原样留（`axes_0.title` 是技术标识，对排障有用且不含用户内容）；
 * **形状不对就换成 hash**。gid 现在确实是结构性的，但判据不能建立在
 * 「现在恰好是」上面——哪天 gid 规则变了、混进用户文字，这条把它挡在外面。
 */
function gid(value: unknown): string | undefined {
  if (typeof value !== 'string' || !value) return undefined
  return GID_PATTERN.test(value) ? value : `gid:${diagnosticHash(value)}`
}

function keyed(value: unknown, pattern: RegExp): string | undefined {
  if (typeof value !== 'string' || !pattern.test(value)) return undefined
  return value
}

function hash(value: unknown): string | null | undefined {
  if (value === null) return null
  if (typeof value !== 'string') return undefined
  // **不 hash 一遍兜底**：走到这里说明调用点忘了 hash，而它手里那个原值
  // 可能就是一条路径。丢掉字段是安全的；顺手替它 hash 会让「忘了 hash」
  // 永远不被发现，下一个字段就没这么幸运了。
  return HASH_PATTERN.test(value) ? value : undefined
}

function numberList(value: unknown, max: number): number[] | undefined {
  if (!Array.isArray(value)) return undefined
  const out: number[] = []
  for (const v of value.slice(0, max)) {
    const n = num(v)
    if (n === undefined) return undefined
    out.push(n)
  }
  return out.length ? out : undefined
}

function patchList(value: unknown, max: number): Record<string, string>[] | undefined {
  if (!Array.isArray(value)) return undefined
  const out: Record<string, string>[] = []
  for (const raw of value.slice(0, max)) {
    if (!raw || typeof raw !== 'object') continue
    const src = raw as Record<string, unknown>
    const prop = keyed(src.prop, PROP_PATTERN)
    if (!prop) continue
    const entry: Record<string, string> = { prop }
    const g = src.gid === undefined ? undefined : gid(src.gid)
    if (g) entry.gid = g
    const domain = src.domain === undefined ? undefined : keyed(src.domain, KEY_PATTERN)
    if (domain) entry.domain = domain
    // **value 不在这里，也永远不会在这里**（types.ts 的 PatchRef 里就没有它）
    out.push(entry)
  }
  return out.length ? out : undefined
}

function geomList(value: unknown, max: number): Record<string, unknown>[] | undefined {
  if (!Array.isArray(value)) return undefined
  const out: Record<string, unknown>[] = []
  for (const raw of value.slice(0, max)) {
    if (!raw || typeof raw !== 'object') continue
    const src = raw as Record<string, unknown>
    const g = gid(src.gid)
    if (!g) continue
    const entry: Record<string, unknown> = { gid: g }
    const bbox = numberList(src.bbox, MAX_GEOM_NUMS)
    if (bbox) entry.bbox = bbox
    const anchor = numberList(src.anchor, MAX_GEOM_NUMS)
    if (anchor) entry.anchor = anchor
    out.push(entry)
  }
  return out.length ? out : undefined
}

function take(kind: FieldKind, value: unknown): unknown {
  switch (kind.k) {
    case 'bool':
      return typeof value === 'boolean' ? value : undefined
    case 'int':
      return int(value)
    case 'num':
      return num(value)
    case 'hash':
      return hash(value)
    case 'gid':
      return gid(value)
    case 'key':
      return keyed(value, KEY_PATTERN)
    case 'enum':
      return typeof value === 'string' && kind.values.includes(value) ? value : undefined
    case 'gids': {
      if (!Array.isArray(value)) return undefined
      const out = value.slice(0, kind.max).map(gid).filter((g): g is string => !!g)
      return out.length ? out : undefined
    }
    case 'patches':
      return patchList(value, kind.max)
    case 'geom':
      return geomList(value, kind.max)
    case 'nums':
      return numberList(value, kind.max)
  }
}

/* -------------------------------------------------------------------------- */

/**
 * 按 schema **拉取**字段。注意方向：遍历的是 schema 的键，不是 `ev` 的键——
 * 输入里多出来的字段根本没被读过，而不是「读了再丢掉」。
 *
 * 返回 null = 这个事件类型不在表里，整条丢弃。
 */
export function serializeEvent(ev: DiagnosticEvent): Record<string, unknown> | null {
  const schema = EVENT_SCHEMA[ev.type]
  if (!schema) return null
  const src = ev as unknown as Record<string, unknown>
  const out: Record<string, unknown> = { type: ev.type }
  for (const [field, kind] of Object.entries(schema)) {
    if (!(field in src)) continue
    const value = take(kind, src[field])
    if (value === undefined) continue
    out[field] = value
  }
  return out
}

/**
 * 给**快照**用的两个取值器。快照不走事件 schema（它不是事件），但里面同样有
 * 两个直接取自 store 的字符串字段——技术 gid 与历史标签 key。它们必须过
 * 与事件里同一份判据，否则「事件里的 gid 有人把关、快照里的没有」就是一条
 * 绕过防线的近路。
 */
export const safeGid = (value: unknown): string | null => gid(value) ?? null
export const safeKey = (value: unknown): string | null => keyed(value, KEY_PATTERN) ?? null

/** 一条已经落进环形缓冲的记录再过一遍表——导出前的最后一道，幂等 */
export function serializeRecorded(rec: RecordedEvent): Record<string, unknown> | null {
  const body = serializeEvent(rec as unknown as DiagnosticEvent)
  if (!body) return null
  const seq = int(rec.seq)
  const ts = int(rec.ts, MAX_TIMESTAMP)
  const tMs = int(rec.t_ms)
  if (seq === undefined || ts === undefined || tMs === undefined) return null
  return { seq, ts, t_ms: tMs, ...body }
}
