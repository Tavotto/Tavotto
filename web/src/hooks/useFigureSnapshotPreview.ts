import { useEffect, useState } from 'react'
import { fetchFigureSnapshotPreview } from '@/lib/api'
import { figureSnapshotPreviewIdentity, hasFigureSnapshotPreview } from '@/lib/figureSnapshotPreview'
import { useDocumentPreviewOwner } from '@/hooks/useDocumentPreviewOwner'
import type { PreviewOwner } from '@/lib/previewOwner'
import type { FigureSnapshot, PanelObject } from '@/types/document'

type Preview = { url: string | null; loading: boolean; failed: boolean }
const IDLE: Preview = { url: null, loading: false, failed: false }

/** Archived SVG stays in image context; it is never an editable inline manifest. */
export function useFigureSnapshotPreview(panel: PanelObject, enabled: boolean, capturedOwner?: PreviewOwner): Preview {
  const owner = useDocumentPreviewOwner(panel, capturedOwner)
  const wanted = enabled && hasFigureSnapshotPreview(panel)
  const reference = figureSnapshotPreviewIdentity(panel.figureSnapshot)
  const active = owner.still()
  const key = JSON.stringify([owner.key, panel.fileId, panel.overrides, reference])
  const [state, setState] = useState<Preview & { key: string | null }>({ ...IDLE, key: null })

  useEffect(() => {
    if (!wanted || !active || reference === null) return
    const input = structuredClone({ id: panel.fileId, patches: panel.overrides, reference: panel.figureSnapshot as FigureSnapshot })
    const controller = new AbortController()
    let url: string | null = null
    setState({ key, url: null, loading: true, failed: false })
    void fetchFigureSnapshotPreview(input.id, input.patches, input.reference, controller.signal, owner.pj)
      .then(preview => {
        if (controller.signal.aborted || !owner.still()) return
        url = URL.createObjectURL(new Blob([preview.svg], { type: 'image/svg+xml' }))
        setState({ key, url, loading: false, failed: false })
      })
      .catch(() => {
        if (!controller.signal.aborted && owner.still()) setState({ key, url: null, loading: false, failed: true })
      })
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
    // The immutable request and document owner are represented by key/active.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, wanted, active, reference])

  if (!wanted || !active) return IDLE
  if (reference === null) return { ...IDLE, failed: true }
  return state.key === key ? state : { url: null, loading: true, failed: false }
}
