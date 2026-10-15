import type { CapturedFigureDescriptor } from '@/lib/api'
import type { CanvasObject } from '@/types/document'

const normalized = (path: string): string => path.replace(/\\/g, '/')

/** Sources actually represented by a successful capture; runtime IDs remain opaque/configuration-specific. */
export function capturedFigureSources(
  descriptors: readonly CapturedFigureDescriptor[],
  objects: readonly CanvasObject[],
): string[] {
  // The captured original is an exact project-relative source even if its panel was removed mid-run.
  const ids = new Set(descriptors.flatMap((d) =>
    [d.asset_id, ...(d.original_artifact ? [normalized(d.original_artifact)] : [])],
  ).filter(Boolean))
  for (const panel of objects) {
    if (panel.type !== 'panel' || panel.fileKind === 'runtime' || !panel.script) continue
    const script = normalized(panel.script)
    const file = normalized(panel.fileId)
    const stem = file.replace(/\.[^/.]+$/, '')
    if (descriptors.some((d) =>
      normalized(d.script) === script &&
      ((!d.run_config && normalized(d.stem) === stem) ||
        (!!d.original_artifact && normalized(d.original_artifact) === file)),
    )) ids.add(panel.fileId)
  }
  return [...ids]
}
