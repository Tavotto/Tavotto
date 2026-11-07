import { validFigureSnapshot } from './figureSnapshot'
import type { FigureSnapshot } from '@/types/document'

/** Display identity only. Receipt/hash/archive validity remains a backend fact. */
export function figureSnapshotPreviewIdentity(reference: unknown): string | null {
  const sourceId = reference && typeof reference === 'object' ? (reference as { sourceId?: unknown }).sourceId : undefined
  if (typeof sourceId !== 'string' || !sourceId || !validFigureSnapshot(reference, sourceId)) return null
  const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort()
      .map(key => [key, canonical((value as Record<string, unknown>)[key])])) : value
  try { return JSON.stringify(canonical(JSON.parse(JSON.stringify(reference)))) }
  catch { return null }
}

/** JSON member order is immaterial; every actual reference field still participates. */
export function sameFigureSnapshotReference(expected: unknown, actual: unknown): boolean {
  const key = figureSnapshotPreviewIdentity(expected)
  return key !== null && key === figureSnapshotPreviewIdentity(actual)
}

/** A malformed explicit reference must not become an ordinary current-file request. */
export function hasFigureSnapshotPreview(panel: { figureSnapshot?: unknown; chart?: unknown; chartSource?: unknown }): boolean {
  return panel.figureSnapshot !== undefined && panel.chart === undefined && panel.chartSource === undefined
}

/** Keep existing consumers' positional calls unchanged when no archive is selected. */
export function figureSnapshotPreviewArgs(panel: { figureSnapshot?: unknown; chart?: unknown; chartSource?: unknown }): [] | [FigureSnapshot] {
  return hasFigureSnapshotPreview(panel) ? [panel.figureSnapshot as FigureSnapshot] : []
}
