import { useTranslation } from 'react-i18next'
import { nanophotonicsSourceState, NANOPHOTONICS_GUIDE } from '@/lib/journalNanophotonics'
import { useDocumentStore } from '@/store/documentStore'

export function JournalNanophotonicsWarning() {
  const { t } = useTranslation('dialogs')
  const ref = useDocumentStore((state) => state.doc.profile?.journalRef)
  const sources = useDocumentStore((state) => state.doc.profile?.journalSources)
  const state = nanophotonicsSourceState(ref?.id, sources)
  if (!state) return null
  return (
    <p data-journal-nanophotonics-warning={state} role="status" className="text-sm text-ink">
      {t(state === 'legacy' ? 'journal.nanophotonicsLegacyWarning' : 'journal.nanophotonicsUnknownWarning')}{' '}
      <a href={NANOPHOTONICS_GUIDE} target="_blank" rel="noreferrer" className="text-info-content hover:underline">{t('journal.nanophotonicsGuide')}</a>
    </p>
  )
}
