/** Test-only evidence from a real autosave response; browser recovery indexes are not live document IDs. */
export interface AutosavePanel {
  id: string
  type: 'panel'
  fileId: string
  overrides: { gid: string; prop: string; value: unknown }[]
}

export interface AutosaveTarget {
  panelId: string
  fileId: string
  requiredOverrides: readonly { gid?: string; prop: string; value?: unknown }[]
}

interface ResponseEvidence {
  ok(): boolean
  url(): string
  request(): { method(): string; postDataJSON(): unknown }
}

/** This identifies the written document, not queue completion: callers must still await formal Save. */
export function autosavePanelEvidence(
  response: ResponseEvidence,
  target: AutosaveTarget,
): { docId: string; panel: AutosavePanel } | null {
  if (!response.ok() || response.request().method() !== 'PUT') return null
  try {
    const match = /^\/api\/autosave\/([^/]+)$/.exec(new URL(response.url()).pathname)
    if (!match) return null
    const docId = decodeURIComponent(match[1])
    if (!docId || docId.includes('/') || docId.includes('\\')) return null
    const body = response.request().postDataJSON() as { canvases?: { objects?: unknown[] }[] } | null
    if (!Array.isArray(body?.canvases)) return null
    const objects = body.canvases.flatMap((canvas) => Array.isArray(canvas.objects) ? canvas.objects : [])
    const candidates = objects.filter((value) => value && typeof value === 'object' &&
      (value as { id?: unknown }).id === target.panelId)
    // The target is one concrete object. Duplicate IDs are corrupt evidence, not a reason to take the first.
    if (candidates.length !== 1) return null
    const panel = candidates[0] as AutosavePanel
    if (panel.type !== 'panel' || panel.fileId !== target.fileId || !Array.isArray(panel.overrides)) return null
    if (!target.requiredOverrides.every((wanted) => panel.overrides.some((override) =>
      override.prop === wanted.prop && (wanted.gid === undefined || override.gid === wanted.gid) &&
      (!Object.hasOwn(wanted, 'value') || JSON.stringify(override.value) === JSON.stringify(wanted.value)),
    ))) return null
    return { docId, panel }
  } catch {
    return null
  }
}
