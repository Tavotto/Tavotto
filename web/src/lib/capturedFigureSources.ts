import type { CapturedFigureDescriptor, FigureSnapshotInvalidation } from '@/lib/api'
import type { CanvasObject } from '@/types/document'

export type CapturedFigureSource = Pick<CapturedFigureDescriptor,
  'asset_id' | 'script' | 'stem' | 'original_artifact' | 'run_config'>

const normalized = (path: string): string => path.replace(/\\/g, '/')

type ExecutionOwner = Pick<FigureSnapshotInvalidation, 'script' | 'run_config'>

/** Runtime ownership is the exact script/configuration pair; opaque asset IDs are never decoded. */
export const figureExecutionOwnerKey = (owner: ExecutionOwner): string =>
  JSON.stringify([normalized(owner.script), owner.run_config])

/** Retire the previous output set, including figures absent from this execution's capture list. */
export function executedFigureSources(
  execution: ExecutionOwner & Pick<FigureSnapshotInvalidation, 'source_ids'>,
  objects: readonly CanvasObject[],
  knownCaptures: readonly CapturedFigureSource[] = [],
): string[] {
  const owner = figureExecutionOwnerKey(execution)
  const prior = knownCaptures.filter((d) =>
    figureExecutionOwnerKey({ script: d.script, run_config: d.run_config ?? null }) === owner,
  )
  // Disk panels have no persisted configuration identity. Only a current backend binding
  // can retire them; a shared script or stem is not evidence that a configured run owns the file.
  const ids = new Set(capturedFigureSources([], objects, [
    ...execution.source_ids, ...prior.map((d) => d.asset_id),
  ]))
  for (const panel of objects) {
    if (panel.type !== 'panel' || panel.fileKind !== 'runtime' || !panel.source) continue
    if (figureExecutionOwnerKey({ script: panel.source.script, run_config: panel.source.runConfig ?? null }) === owner) {
      ids.add(panel.fileId)
    }
  }
  return [...ids]
}

/** Sources actually represented by a successful capture; runtime IDs remain opaque/configuration-specific. */
export function capturedFigureSources(
  descriptors: readonly CapturedFigureSource[],
  objects: readonly CanvasObject[],
  exactSources: readonly string[] = [],
): string[] {
  // The captured original is an exact project-relative source even if its panel was removed mid-run.
  const ids = new Set([...exactSources, ...descriptors.flatMap((d) =>
    [d.asset_id, ...(d.original_artifact ? [normalized(d.original_artifact)] : [])],
  ).filter(Boolean)])
  for (const panel of objects) {
    if (panel.type !== 'panel' || panel.fileKind === 'runtime') continue
    const file = normalized(panel.fileId)
    // Retained exact artifacts also map back to legacy document IDs with Windows separators.
    // They need no script/stem inference and must never broaden into a sibling configuration.
    if (exactSources.some((source) => normalized(source) === file)) ids.add(panel.fileId)
    if (!panel.script) continue
    const script = normalized(panel.script)
    const stem = file.replace(/\.[^/.]+$/, '')
    if (descriptors.some((d) =>
      normalized(d.script) === script &&
      ((!d.run_config && normalized(d.stem) === stem) ||
        (!!d.original_artifact && normalized(d.original_artifact) === file)),
    )) ids.add(panel.fileId)
  }
  return [...ids]
}
