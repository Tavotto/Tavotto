/** Validation only: coordinates are defined by the existing effective figure.frame. */
import { effectiveOverride } from './effectiveOverride'
import type { Manifest } from './api'
import type { PanelObject, PanelOverride } from '@/types/document'

export const FIGSIZE_SOURCE_POLICY = 'selected-figsize-v1' as const
export interface ArtifactValidation {
  version: 1
  requiredFrame: 'figsize'
  sourceId: string
  bytesSha256: string
  sizeBytes: number
}
export interface ArtifactRequestFields {
  source_policy?: typeof FIGSIZE_SOURCE_POLICY
  expected_source?: { bytes_sha256: string; size_bytes: number }
}
export class ArtifactValidationError extends Error {
  readonly code = 'artifact_source_unavailable'
  readonly reason: string
  constructor(reason: string) {
    super('The saved source cannot be validated; edits were preserved.')
    this.reason = reason
  }
}
type Subject = Pick<PanelObject, 'fileId' | 'overrides' | 'artifactValidation'>
const bytesValid = (hash: unknown, size: unknown) =>
  typeof hash === 'string' && /^[0-9a-f]{64}$/.test(hash) &&
  typeof size === 'number' && Number.isSafeInteger(size) && size > 0

/** Present-but-invalid never becomes legacy. Last matching FRAME wins, like the worker. */
export function artifactValidationIssue(panel: Subject): string | null {
  if (!Object.hasOwn(panel, 'artifactValidation')) return null
  const guard = panel.artifactValidation
  if (!guard || typeof guard !== 'object' || guard.version !== 1 ||
      guard.requiredFrame !== 'figsize' || typeof guard.sourceId !== 'string' ||
      !bytesValid(guard.bytesSha256, guard.sizeBytes)) return 'invalid_guard'
  if (guard.sourceId !== panel.fileId) return 'source_changed'
  if (effectiveOverride(panel.overrides, 'figure', 'frame')?.value !== 'figsize') return 'frame_changed'
  return null
}

export function artifactRequestFields(panel: Subject): ArtifactRequestFields {
  const issue = artifactValidationIssue(panel)
  if (issue) throw new ArtifactValidationError(issue)
  const guard = panel.artifactValidation
  return guard ? {
    source_policy: FIGSIZE_SOURCE_POLICY,
    expected_source: { bytes_sha256: guard.bytesSha256, size_bytes: guard.sizeBytes },
  } : {}
}

/** Invalid saved data may have an error key, but never publishable geometry. */
export function artifactKeySuffix(guard: unknown): string {
  return guard === undefined ? '' : `\u0000${JSON.stringify(guard)}`
}

/** Old servers that ignore the opt-in cannot admit a panel or publish selected pixels. */
export function validationFromSource(
  source: unknown,
  fileId: string,
  expected?: ArtifactValidation,
): ArtifactValidation {
  const value = source as Record<string, unknown> | null
  if (!value || value.render_policy !== FIGSIZE_SOURCE_POLICY || value.origin !== 'static' ||
      value.kind !== 'png' || typeof value.source_id !== 'string' ||
      !bytesValid(value.bytes_sha256, value.size_bytes)) {
    throw new ArtifactValidationError('unsupported_response')
  }
  if (expected && (value.bytes_sha256 !== expected.bytesSha256 || value.size_bytes !== expected.sizeBytes)) {
    throw new ArtifactValidationError('source_changed')
  }
  return {
    version: 1,
    requiredFrame: 'figsize',
    sourceId: fileId,
    bytesSha256: value.bytes_sha256 as string,
    sizeBytes: value.size_bytes as number,
  }
}

/** Only the sole effective structural FRAME is unedited for original-file export. */
export function originalArtifactOverrides(panel: Subject): PanelOverride[] {
  artifactRequestFields(panel)
  const onlyFrame = panel.overrides.every(p => p.gid === 'figure' && p.prop === 'frame')
  return panel.artifactValidation && onlyFrame ? [] : panel.overrides
}

export function refuseArtifactOperation(panel: Subject): void {
  artifactRequestFields(panel)
  if (panel.artifactValidation) throw new ArtifactValidationError('writeback_not_supported')
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
  editIntent = panel.artifactValidation ? {
    id: panel.id,
    guard: artifactKeySuffix(panel.artifactValidation),
    stillOwner,
  } : null
}
export function artifactEditIntentChanged(panel: PanelObject | undefined): boolean {
  return !!editIntent && (!panel || panel.id !== editIntent.id ||
    artifactKeySuffix(panel.artifactValidation) !== editIntent.guard || !editIntent.stillOwner())
}

/** A color-only edit still needs the selected source's explicit visibility on reset. */
export function artifactBackgroundOriginal(panel: Subject, manifest: Manifest | null): boolean | undefined {
  if (!panel.artifactValidation || !effectiveOverride(panel.overrides, 'figure', 'facecolor')) return
  const original = manifest?.elements.find(e => e.gid === 'figure')?.editable
    .find(f => f.prop === 'transparent')?.value_original
  return typeof original === 'boolean' ? original : undefined
}
