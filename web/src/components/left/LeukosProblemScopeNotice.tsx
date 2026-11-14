import { useTranslation } from 'react-i18next'
import { hasLeukosHistoricalSource } from '@/components/journal/JournalLeukosScopeWarning'
import { listJoin } from '@/i18n/format'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'

/** Canvas-level saved-source context. It does not attribute any issue or threshold. */
export function LeukosProblemScopeNotice({ issues, emptyScope }: {
  issues: readonly ValidationIssue[]
  /** With no visible issues, show saved-source context for the selected scope. */
  emptyScope?: 'document' | 'active'
}) {
  const { t } = useTranslation('dialogs')
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const activeProfile = useDocumentStore((s) => s.doc.profile)
  const activeName = useDocumentStore((s) => s.doc.name)
  const canvases = useDocumentStore((s) => s.canvases)
  const relevant = new Set(emptyScope === 'document'
    ? canvases.map((canvas) => canvas.id)
    : emptyScope === 'active'
      ? [activeCanvasId]
      : issues.map((issue) => issue.objectRef.canvasId))
  const names = canvases.flatMap((canvas, index) => {
    if (!relevant.has(canvas.id)) return []
    // Active edits live in doc; inactive canvases keep their own saved binding.
    const profile = canvas.id === activeCanvasId ? activeProfile : canvas.profile
    if (!hasLeukosHistoricalSource(profile?.journalRef?.id, profile?.journalSources)) return []
    const name = canvas.id === activeCanvasId ? activeName : canvas.name
    return [`${name} · ${index + 1}`]
  })
  if (!names.length) return null
  const notice = (
    <li data-leukos-problem-scope-notice className="px-2 pb-2 text-sm text-ink">
      <div role="status"><strong>{listJoin(names)}</strong>{' '}{t('journal.leukosProblemScopeWarning')}</div>
    </li>
  )
  return emptyScope ? <ul data-leukos-empty-scope-context>{notice}</ul> : notice
}
