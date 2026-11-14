import { useTranslation } from 'react-i18next'
import type { JournalFieldSource } from '@/lib/journals'
import { useDocumentStore } from '@/store/documentStore'

const URL = 'https://www.tandfonline.com/action/authorSubmission?journalCode=ulks20'
const SNAPSHOT = 'snapshots/23e6dd7af91c88e09a76d706b4ce3f4e84879060cb362ee59f0ac387d45b362e.webp'

/**
 * Conservative saved-metadata match, not authentication of a source or screenshot.
 * The content-addressed file name is one identifier; this function does not read image bytes.
 */
export function hasLeukosHistoricalSource(
  journalId: string | undefined,
  sources: Record<string, JournalFieldSource> | undefined,
): boolean {
  if (journalId !== 'leukos' || !sources || typeof sources !== 'object' || Array.isArray(sources)) return false
  const source = sources.min_raster_dpi
  return !!source && typeof source === 'object' && source.url === URL &&
    typeof source.quote === 'string' && source.quote.includes('Figures for final submission') &&
    source.quote.includes('300 dpi for color') && source.snapshot?.file === SNAPSHOT &&
    source.snapshot.width === 1058 && source.snapshot.height === 519 &&
    source.stage === 'production' && source.value === 300 && source.qualifier === 'min' &&
    source.affects === 'preflight' && source.unset === false &&
    source.rule_scope === 'journal' && source.source_scope === 'journal' &&
    source.coverage === 'journal_specific'
}

export function JournalLeukosScopeWarning() {
  const { t } = useTranslation('dialogs')
  const ref = useDocumentStore((state) => state.doc.profile?.journalRef)
  const sources = useDocumentStore((state) => state.doc.profile?.journalSources)
  if (!hasLeukosHistoricalSource(ref?.id, sources)) return null
  return <p data-leukos-scope-warning role="status" className="text-sm text-ink">{t('journal.leukosScopeWarning')}</p>
}
