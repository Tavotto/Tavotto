import { useTranslation } from 'react-i18next'
import { hasChildAdultSource } from '@/lib/journalSourceIdentity'
import { useDocumentStore } from '@/store/documentStore'

export function JournalSourceIdentityWarning() {
  const { t } = useTranslation('dialogs')
  const ref = useDocumentStore((state) => state.doc.profile?.journalRef)
  const sources = useDocumentStore((state) => state.doc.profile?.journalSources)
  if (!hasChildAdultSource(ref?.id, sources)) return null
  return <p data-journal-source-identity-warning role="status" className="text-sm text-ink">{t('journal.sourceIdentityWarning')}</p>
}
