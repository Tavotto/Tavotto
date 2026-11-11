import { useTranslation } from 'react-i18next'
import type { JournalFieldSource } from '@/lib/journals'
import { useDocumentStore } from '@/store/documentStore'

const JPER_ID = 'journal-of-planning-education-and-research'
const JPER_HISTORY_SOURCE = 'https://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf'

/** Scope the warning to the exact saved identity and historical source; never infer from a domain. */
export function hasJperHistoricalSource(
  journalId: string | undefined,
  sources: Record<string, JournalFieldSource> | undefined,
): boolean {
  if (journalId !== JPER_ID || !sources || typeof sources !== 'object' || Array.isArray(sources)) return false
  return Object.values(sources).some((source) => source && typeof source === 'object' && source.url === JPER_HISTORY_SOURCE)
}

export function JournalJperStageWarning() {
  const { t } = useTranslation('dialogs')
  const ref = useDocumentStore((state) => state.doc.profile?.journalRef)
  const sources = useDocumentStore((state) => state.doc.profile?.journalSources)
  if (!hasJperHistoricalSource(ref?.id, sources)) return null
  return <p data-journal-jper-stage-warning role="status" className="text-sm text-ink">{t('journal.jperStageWarning')}</p>
}
