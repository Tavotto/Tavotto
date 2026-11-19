import vectors from '../../../tests/golden/journal_vectors.json'
import { journalSources, loadCatalog, type JournalFieldSource } from '@/lib/journals'

/** Synthetic copy of the historically observed LEUKOS source shape; no private bundle needed. */
export function leukosSourceFixture(): JournalFieldSource {
  const source = journalSources('fx-beta', loadCatalog(vectors.catalog)).min_raster_dpi
  const url = 'https://www.tandfonline.com/action/authorSubmission?journalCode=ulks20'
  return {
    ...source,
    url,
    link: url,
    quote: 'Figures for final submission … 300 dpi for color',
    snapshot: {
      file: 'snapshots/23e6dd7af91c88e09a76d706b4ce3f4e84879060cb362ee59f0ac387d45b362e.webp',
      width: 1058,
      height: 519,
    },
    value: 300,
    qualifier: 'min',
    stage: 'production',
    rule_scope: 'journal',
    source_scope: 'journal',
    coverage: 'journal_specific',
    affects: 'preflight',
    unset: false,
  }
}
