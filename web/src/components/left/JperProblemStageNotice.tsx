import { useTranslation } from 'react-i18next'
import { hasJperHistoricalSource } from '@/components/journal/JournalJperStageWarning'
import { listJoin } from '@/i18n/format'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'

/** Canvas-level saved-source context. It does not attribute any issue or threshold. */
export function JperProblemStageNotice({ issues }: { issues: readonly ValidationIssue[] }) {
  const { t } = useTranslation('dialogs')
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const activeProfile = useDocumentStore((s) => s.doc.profile)
  const activeName = useDocumentStore((s) => s.doc.name)
  const canvases = useDocumentStore((s) => s.canvases)
  const relevant = new Set(issues.map((issue) => issue.objectRef.canvasId))
  const names = canvases.flatMap((canvas, index) => {
    if (!relevant.has(canvas.id)) return []
    // Active edits live in doc; inactive canvases keep their own saved binding.
    const profile = canvas.id === activeCanvasId ? activeProfile : canvas.profile
    if (!hasJperHistoricalSource(profile?.journalRef?.id, profile?.journalSources)) return []
    const name = canvas.id === activeCanvasId ? activeName : canvas.name
    return [`${name} · ${index + 1}`]
  })
  if (!names.length) return null
  return (
    <li data-jper-problem-stage-notice className="px-2 pb-2 text-sm text-ink">
      <div role="status"><strong>{listJoin(names)}</strong>{' '}{t('journal.jperProblemGeneralWarning')}</div>
    </li>
  )
}
