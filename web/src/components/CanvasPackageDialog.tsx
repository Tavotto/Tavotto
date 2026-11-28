import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { useDocumentStore } from '@/store/documentStore'
import { exportPackage } from '@/store/packageActions'
import { Button } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { Dialog } from './ui/Dialog'

export function CanvasPackageDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation(['workspace', 'common'])
  const [source] = useState(() => {
    const { documentId, loadSeq, canvases, activeCanvasId } = useDocumentStore.getState()
    return { documentId, loadSeq, canvases, activeCanvasId, project: captureProjectEpoch() }
  })
  const [selected, setSelected] = useState(() => new Set([source.activeCanvasId]))
  const submit = () => {
    const { documentId, loadSeq } = useDocumentStore.getState()
    const canvasIds = source.canvases.filter(canvas => selected.has(canvas.id)).map(canvas => canvas.id)
    onClose()
    if (canvasIds.length && source.project.still() && documentId === source.documentId && loadSeq === source.loadSeq) {
      void exportPackage({ canvasIds })
    }
  }

  return (
    <Dialog
      open
      size="sm"
      onOpenChange={open => { if (!open) onClose() }}
      title={t('workspace:topbar.exportSelectedCanvases')}
      description={t('workspace:topbar.packageCanvasDescription')}
      footer={{
        secondary: <Button variant="secondary" onClick={onClose}>{t('common:actions.cancel')}</Button>,
        primary: (
          <Button variant="primary" disabled={selected.size === 0} onClick={submit}>
            {t('workspace:topbar.exportSelectedCanvases')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-1">
        {source.canvases.map(canvas => (
          <label key={canvas.id} className="flex min-h-9 items-center gap-2">
            <Checkbox
              data-package-canvas={canvas.id}
              checked={selected.has(canvas.id)}
              onChange={event => {
                const checked = event.currentTarget.checked
                setSelected(previous => {
                  const next = new Set(previous)
                  if (checked) next.add(canvas.id)
                  else next.delete(canvas.id)
                  return next
                })
              }}
            />
            <span className="min-w-0 break-words">{canvas.name}</span>
          </label>
        ))}
      </div>
    </Dialog>
  )
}
