import { useTranslation } from 'react-i18next'
import { nanophotonicsSourceState, NANOPHOTONICS_GUIDE } from '@/lib/journalNanophotonics'
import { listJoin } from '@/i18n/format'
import { useDocumentStore } from '@/store/documentStore'

/** Saved-source context for affected canvases, not a claim about a particular issue. */
export function NanophotonicsProblemNotice({ scope, inTree = false }: {
  scope: 'document' | 'active'
  inTree?: boolean
}) {
  const { t } = useTranslation('dialogs')
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const activeProfile = useDocumentStore((s) => s.doc.profile)
  const activeName = useDocumentStore((s) => s.doc.name)
  const canvases = useDocumentStore((s) => s.canvases)
  const groups: Record<'legacy' | 'unknown', string[]> = { legacy: [], unknown: [] }
  canvases.forEach((canvas, index) => {
    if (scope !== 'document' && canvas.id !== activeCanvasId) return
    const profile = canvas.id === activeCanvasId ? activeProfile : canvas.profile
    const state = nanophotonicsSourceState(profile?.journalRef?.id, profile?.journalSources)
    if (!state) return
    const name = canvas.id === activeCanvasId ? activeName : canvas.name
    groups[state].push(`${name} · ${index + 1}`)
  })
  const notices = (['legacy', 'unknown'] as const).filter((state) => groups[state].length).map((state) => (
    <li key={state} data-nanophotonics-problem-notice={state} className="px-2 pb-2 text-sm text-ink">
      <div role="status"><strong>{listJoin(groups[state])}</strong>{' '}
        {t(state === 'legacy' ? 'journal.nanophotonicsLegacyWarning' : 'journal.nanophotonicsUnknownWarning')}{' '}
        <a href={NANOPHOTONICS_GUIDE} target="_blank" rel="noreferrer" className="text-info-content hover:underline">{t('journal.nanophotonicsGuide')}</a>
      </div>
    </li>
  ))
  if (!notices.length) return null
  return inTree ? <>{notices}</> : <ul data-nanophotonics-empty-context>{notices}</ul>
}
