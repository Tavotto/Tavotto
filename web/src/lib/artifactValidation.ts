/** Validation only: coordinates are defined by the existing effective figure.frame. */
import { effectiveOverride } from './effectiveOverride'
import type { Manifest } from './api'
import type { PanelObject, PanelOverride } from '@/types/document'

export const FIGSIZE_SOURCE_POLICY = 'selected-figsize-v1' as const
/** 来源驱动采用（ADR 0139 PR2，实验开关后面）：与 `selected-figsize-v1` 并存，永不共用 guard 形状 */
export const SCRIPT_BACKED_SOURCE_POLICY = 'script-backed-figure-v1' as const
export interface ArtifactValidation {
  version: 1
  requiredFrame: 'figsize'
  sourceId: string
  bytesSha256: string
  sizeBytes: number
}
/**
 * 来源驱动采用的 guard（面板上的**并列可选字段** `sourceBackedFigure`，ADR 0139 §六）。
 * 不复用 `ArtifactValidation` 的形状或字段名：旧前端读到它会忽略，把面板当未采用 PNG 显示原像素。
 * - `exact`：重画与原件逐像素相同；`reconstructed`：用户对照过原图 / 重画后确认以这份重画为编辑基础。
 * - `reconstructionSha256`：确认（或准入）时那份**零编辑重画**的字节摘要——带真实编辑的请求必须原样带回。
 */
export interface SourceBackedFigure {
  version: 1
  policy: typeof SCRIPT_BACKED_SOURCE_POLICY
  sourceId: string
  bytesSha256: string
  sizeBytes: number
  status: 'exact' | 'reconstructed'
  reconstructionSha256: string
  confirmedAt?: string
}
/** 面板上任一种「已采用」guard */
export type AdoptionGuard = ArtifactValidation | SourceBackedFigure
export type SourcePolicy = typeof FIGSIZE_SOURCE_POLICY | typeof SCRIPT_BACKED_SOURCE_POLICY
export interface ArtifactRequestFields {
  source_policy?: SourcePolicy
  expected_source?: { bytes_sha256: string; size_bytes: number }
  /** 只在来源驱动策略且带真实编辑时发：用户确认的那份重画的摘要（缺失 409 `confirmation_required`） */
  confirmed_reconstruction_sha256?: string
}
export class ArtifactValidationError extends Error {
  readonly code = 'artifact_source_unavailable'
  readonly reason: string
  constructor(reason: string) {
    super('The saved source cannot be validated; edits were preserved.')
    this.reason = reason
  }
}
type Subject = Pick<PanelObject, 'fileId' | 'overrides'> &
  Partial<Pick<PanelObject, 'artifactValidation' | 'sourceBackedFigure'>>
const HEX64 = /^[0-9a-f]{64}$/
const bytesValid = (hash: unknown, size: unknown) =>
  typeof hash === 'string' && /^[0-9a-f]{64}$/.test(hash) &&
  typeof size === 'number' && Number.isSafeInteger(size) && size > 0

const SOURCE_BACKED_KEYS = new Set([
  'version', 'policy', 'sourceId', 'bytesSha256', 'sizeBytes', 'status', 'reconstructionSha256', 'confirmedAt',
])

/** 来源驱动 guard 的形状判据：未知键 / 畸形 / 版本不对一律 false（不当 legacy）。 */
export function isSourceBackedFigure(value: unknown): value is SourceBackedFigure {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const g = value as Record<string, unknown>
  if (Object.keys(g).some((k) => !SOURCE_BACKED_KEYS.has(k))) return false
  if (g.version !== 1 || g.policy !== SCRIPT_BACKED_SOURCE_POLICY || typeof g.sourceId !== 'string') return false
  if (!bytesValid(g.bytesSha256, g.sizeBytes)) return false
  if (g.status !== 'exact' && g.status !== 'reconstructed') return false
  if (typeof g.reconstructionSha256 !== 'string' || !HEX64.test(g.reconstructionSha256)) return false
  if (g.confirmedAt !== undefined && (typeof g.confirmedAt !== 'string' || !g.confirmedAt)) return false
  // 「用户确认过」必须有确认时刻；exact 没有确认这回事
  if (g.status === 'reconstructed' && g.confirmedAt === undefined) return false
  return true
}

/** 这块面板带着哪一种 guard 字段（字段在场就算，哪怕值是畸形 / null——在场但无效绝不当 legacy） */
export const hasStrictGuard = (panel: object): boolean => Object.hasOwn(panel, 'artifactValidation')
export const hasSourceBackedGuard = (panel: object): boolean => Object.hasOwn(panel, 'sourceBackedFigure')

/**
 * 「这块面板处于哪种选中上下文」的**唯一判据**（ADR 0139 PR2）：
 * `strict`（精确策略）/ `source`（来源驱动）/ `none`（未采用）。两个字段同时在场、或值畸形，
 * 一律是 `invalid`（fail closed）。所有「已采用面板的拒绝写回 / 重建 / 同步 / 自动修复 / 渲染键 / 请求字段」
 * 都经它，不再各自 `Object.hasOwn(panel, 'artifactValidation')`。
 */
export type AdoptionContext =
  | { kind: 'none' }
  | { kind: 'strict'; guard: ArtifactValidation }
  | { kind: 'source'; guard: SourceBackedFigure }
  | { kind: 'invalid' }

export function adoptionOf(panel: object): AdoptionContext {
  const strict = hasStrictGuard(panel), source = hasSourceBackedGuard(panel)
  if (!strict && !source) return { kind: 'none' }
  const p = panel as { artifactValidation?: unknown; sourceBackedFigure?: unknown }
  if (strict && source) return { kind: 'invalid' }
  if (source) return isSourceBackedFigure(p.sourceBackedFigure) ? { kind: 'source', guard: p.sourceBackedFigure } : { kind: 'invalid' }
  const g = p.artifactValidation as ArtifactValidation | null | undefined
  return g && typeof g === 'object' ? { kind: 'strict', guard: g } : { kind: 'invalid' }
}

/** 面板已采用（任一种 guard 字段在场，含畸形的）——取代散落各处的 `Object.hasOwn(panel,'artifactValidation')` */
export const isAdoptedPanel = (panel: object): boolean => hasStrictGuard(panel) || hasSourceBackedGuard(panel)

/** 面板上那份 guard 值（无 guard = undefined；字段在场但畸形 / 双 guard = null）。渲染键与请求账本只认它。 */
export function adoptionGuardOf(panel: object): AdoptionGuard | null | undefined {
  const ctx = adoptionOf(panel)
  if (ctx.kind === 'none') return undefined
  return ctx.kind === 'invalid' ? null : ctx.guard
}

/** 反过来：已知 guard 值（`adoptionGuardOf` 的返回）+ 文件 + overrides → 能喂给 `artifactRequestFields` 的主体 */
export function subjectOf(fileId: string, overrides: PanelObject['overrides'], guard: AdoptionGuard | null | undefined): Subject {
  if (guard === undefined) return { fileId, overrides }
  if (guard === null) return { fileId, overrides, artifactValidation: null as unknown as ArtifactValidation }
  return 'policy' in guard
    ? { fileId, overrides, sourceBackedFigure: guard }
    : { fileId, overrides, artifactValidation: guard }
}

export const policyOfGuard = (guard: AdoptionGuard): SourcePolicy =>
  'policy' in guard ? SCRIPT_BACKED_SOURCE_POLICY : FIGSIZE_SOURCE_POLICY

/** 清掉两种 guard（重置 / 换素材 / 复制粘贴重链接 / 采用新图幅：面板不再是「已采用」） */
export function clearAdoption(panel: Partial<PanelObject>): void {
  delete panel.artifactValidation
  delete panel.sourceBackedFigure
}

/** Present-but-invalid never becomes legacy. Last matching FRAME wins, like the worker. */
export function artifactValidationIssue(panel: Subject): string | null {
  const ctx = adoptionOf(panel)
  if (ctx.kind === 'none') return null
  if (ctx.kind === 'invalid') return 'invalid_guard'
  const guard: AdoptionGuard = ctx.guard
  if (ctx.kind === 'strict') {
    const g = ctx.guard
    if (g.version !== 1 || g.requiredFrame !== 'figsize' || typeof g.sourceId !== 'string' ||
        !bytesValid(g.bytesSha256, g.sizeBytes)) return 'invalid_guard'
  }
  if (guard.sourceId !== panel.fileId) return 'source_changed'
  if (effectiveOverride(panel.overrides, 'figure', 'frame')?.value !== 'figsize') return 'frame_changed'
  return null
}

/**
 * 来源驱动采用 + 零编辑（只有结构性 FRAME）：此时显示 / 原图导出都用磁盘原像素，不出引擎图。
 * guard 无效（`artifactValidationIssue`）时为 false——无效的采用记录不凭空给出「原像素」结论，走各处的 fail-closed。
 */
export function isSourceBackedZeroEdit(panel: Subject): boolean {
  return adoptionOf(panel).kind === 'source' && !artifactValidationIssue(panel) && !hasRealEdits(panel.overrides)
}

/** 真实编辑 = 结构性 `figure.frame` 以外的任何 override（与后端 `_real_edits` 同源） */
export const hasRealEdits = (overrides: readonly { gid: string; prop: string }[]): boolean =>
  overrides.some((p) => !(p.gid === 'figure' && p.prop === 'frame'))

/**
 * 未采用的 PNG 源图：磁盘上的 PNG + 脚本，没动过（无 override）、还没经 `admitPngEntry` 验过字节身份。
 * 它的唯一合法首渲染是带 `source_policy` 的采用请求；任何别的路径（跨图邻居、同步器）发出的
 * 无策略 patches=[] 渲染都会让脚本重跑出一张没对过字节的图。进图内编辑的入口与这些路径共用这一个判据。
 */
export function isUnadoptedPngSource(
  panel: Pick<PanelObject, 'fileKind' | 'fileId' | 'script' | 'overrides'>,
): boolean {
  return panel.fileKind === 'raster' && /\.png$/i.test(panel.fileId) && !!panel.script && panel.overrides.length === 0
}

export function artifactRequestFields(panel: Subject): ArtifactRequestFields {
  const issue = artifactValidationIssue(panel)
  if (issue) throw new ArtifactValidationError(issue)
  const ctx = adoptionOf(panel)
  if (ctx.kind === 'none') return {}
  if (ctx.kind === 'strict') {
    return {
      source_policy: FIGSIZE_SOURCE_POLICY,
      expected_source: { bytes_sha256: ctx.guard.bytesSha256, size_bytes: ctx.guard.sizeBytes },
    }
  }
  if (ctx.kind !== 'source') throw new ArtifactValidationError('invalid_guard')
  return {
    source_policy: SCRIPT_BACKED_SOURCE_POLICY,
    expected_source: { bytes_sha256: ctx.guard.bytesSha256, size_bytes: ctx.guard.sizeBytes },
    // 零编辑 = 原像素，不需要确认；有真实编辑就必须带用户确认的那份重画的摘要
    ...(hasRealEdits(panel.overrides) ? { confirmed_reconstruction_sha256: ctx.guard.reconstructionSha256 } : {}),
  }
}

/** Invalid saved data may have an error key, but never publishable geometry. */
export function artifactKeySuffix(guard: unknown): string {
  return guard === undefined ? '' : `\u0000${JSON.stringify(guard)}`
}

/** Old servers that ignore the opt-in cannot admit a panel or publish selected pixels. */
export function verifiedSource(
  source: unknown,
  fileId: string,
  policy: SourcePolicy,
  expected?: Pick<AdoptionGuard, 'bytesSha256' | 'sizeBytes'>,
): { sourceId: string; bytesSha256: string; sizeBytes: number } {
  const value = source as Record<string, unknown> | null
  if (!value || value.render_policy !== policy || value.origin !== 'static' ||
      value.kind !== 'png' || typeof value.source_id !== 'string' ||
      !bytesValid(value.bytes_sha256, value.size_bytes)) {
    throw new ArtifactValidationError('unsupported_response')
  }
  if (expected && (value.bytes_sha256 !== expected.bytesSha256 || value.size_bytes !== expected.sizeBytes)) {
    throw new ArtifactValidationError('source_changed')
  }
  return { sourceId: fileId, bytesSha256: value.bytes_sha256 as string, sizeBytes: value.size_bytes as number }
}

export function validationFromSource(
  source: unknown,
  fileId: string,
  expected?: AdoptionGuard,
): ArtifactValidation {
  const policy = expected ? policyOfGuard(expected) : FIGSIZE_SOURCE_POLICY
  const v = verifiedSource(source, fileId, policy, expected)
  return { version: 1, requiredFrame: 'figsize', sourceId: v.sourceId, bytesSha256: v.bytesSha256, sizeBytes: v.sizeBytes }
}

/** 来源驱动准入响应里的结论（`source_backed`）：形状不对 = 老后端 / 不支持，一律 `unsupported_response` */
export interface SourceBackedVerdict {
  status: 'exact' | 'needs_confirmation'
  reconstructionSha256: string
  metrics: { differingPixels: number; differingRatio: number; totalPixels: number; bbox: [number, number, number, number] | null }
}

export function verdictFromResponse(raw: unknown): SourceBackedVerdict {
  const v = raw as Record<string, unknown> | null
  const m = (v?.metrics ?? null) as Record<string, unknown> | null
  const num = (x: unknown) => typeof x === 'number' && Number.isFinite(x) && x >= 0
  const bbox = m?.bbox
  const bboxOk = bbox == null || (Array.isArray(bbox) && bbox.length === 4 && bbox.every(num))
  if (!v || (v.status !== 'exact' && v.status !== 'needs_confirmation') ||
      typeof v.reconstruction_sha256 !== 'string' || !HEX64.test(v.reconstruction_sha256) ||
      !m || !num(m.differing_pixels) || !num(m.differing_ratio) || !num(m.total_pixels) || !bboxOk) {
    throw new ArtifactValidationError('unsupported_response')
  }
  return {
    status: v.status,
    reconstructionSha256: v.reconstruction_sha256,
    metrics: {
      differingPixels: m.differing_pixels as number,
      differingRatio: m.differing_ratio as number,
      totalPixels: m.total_pixels as number,
      bbox: (bbox ?? null) as [number, number, number, number] | null,
    },
  }
}

/** 由准入结论 + 已核对的来源身份造 guard；`confirmedAt` 只在用户确认的那一刻传入 */
export function sourceBackedGuard(
  source: { sourceId: string; bytesSha256: string; sizeBytes: number },
  verdict: SourceBackedVerdict,
  confirmedAt?: string,
): SourceBackedFigure {
  return {
    version: 1,
    policy: SCRIPT_BACKED_SOURCE_POLICY,
    sourceId: source.sourceId,
    bytesSha256: source.bytesSha256,
    sizeBytes: source.sizeBytes,
    status: confirmedAt === undefined ? 'exact' : 'reconstructed',
    reconstructionSha256: verdict.reconstructionSha256,
    ...(confirmedAt === undefined ? {} : { confirmedAt }),
  }
}

/** Only the sole effective structural FRAME is unedited for original-file export. */
export function originalArtifactOverrides(panel: Subject): PanelOverride[] {
  artifactRequestFields(panel)
  return isAdoptedPanel(panel) && !hasRealEdits(panel.overrides) ? [] : panel.overrides
}

export function refuseArtifactOperation(panel: Subject): void {
  artifactRequestFields(panel)
  // 严格与来源驱动两种上下文同样拒绝：原件永远不被写（ADR 0139 §五.3）
  if (isAdoptedPanel(panel)) throw new ArtifactValidationError('writeback_not_supported')
}

// These two transient intents own no document state and are never serialized.
let entryEpoch = 0
let entryPanel: string | null = null
type EntryRenderEpochChange = { from: number; to: number }
let entryRetry: ((renderEpochChange?: EntryRenderEpochChange) => void) | null = null
export function beginArtifactEntry(panelId: string): number {
  entryRetry = null
  entryPanel = panelId
  return ++entryEpoch
}
export function cancelArtifactEntry(): void {
  entryRetry = null
  entryPanel = null
  entryEpoch++
}
export const artifactEntryCurrent = (epoch: number): boolean => epoch === entryEpoch
export const artifactEntryPending = (panelId: string): boolean => entryPanel === panelId

/** Keep the explicit edit intent while the existing environment dialog awaits an answer. */
export function deferArtifactEntry(epoch: number, retry: NonNullable<typeof entryRetry>): void {
  if (!artifactEntryCurrent(epoch)) return
  entryPanel = null
  entryRetry = retry
}
/** A successful input remap may transfer only its own render invalidation to the waiting intent. */
export function retryArtifactEntry(renderEpochChange?: EntryRenderEpochChange): void {
  const retry = entryRetry
  entryRetry = null
  retry?.(renderEpochChange)
}

let editIntent: { id: string; guard: string; stillOwner: () => boolean } | null = null
export function clearArtifactEditIntent(): void {
  editIntent = null
}
export function rememberArtifactEdit(panel: PanelObject, stillOwner: () => boolean): void {
  editIntent = isAdoptedPanel(panel) ? {
    id: panel.id,
    guard: artifactKeySuffix(adoptionGuardOf(panel)),
    stillOwner,
  } : null
}
export function artifactEditIntentChanged(panel: PanelObject | undefined): boolean {
  return !!editIntent && (!panel || panel.id !== editIntent.id ||
    artifactKeySuffix(adoptionGuardOf(panel!)) !== editIntent.guard || !editIntent.stillOwner())
}

/** A color-only edit still needs the selected source's explicit visibility on reset. */
export function artifactBackgroundOriginal(panel: Subject, manifest: Manifest | null): boolean | undefined {
  if (!isAdoptedPanel(panel) || !effectiveOverride(panel.overrides, 'figure', 'facecolor')) return
  const original = manifest?.elements.find(e => e.gid === 'figure')?.editable
    .find(f => f.prop === 'transparent')?.value_original
  return typeof original === 'boolean' ? original : undefined
}
