import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import { journalSources, loadCatalog, type JournalFieldSource } from './journals'
import { tipForSource } from './journalTip'

const CAT = loadCatalog(vectors.catalog)

describe('tipForSource 的 sourceId（ADR 0213）', () => {
  const v1 = journalSources('fx-alpha', CAT)['min_raster_dpi']

  it('v1 条目照旧发 src', () => {
    expect(v1.fact_id).toBeUndefined()
    expect(tipForSource('Fixture Alpha', 'fx-alpha', 'min_raster_dpi', v1).sourceId).toBe(v1.src)
  })

  it('折叠的 v2 规则发 v2:<事实 id>，不发带第二个冒号的 src', () => {
    const v2: JournalFieldSource = { ...v1, src: 'v2:s1:journal', fact_id: 'fact-7' }
    expect(tipForSource('Fixture Alpha', 'fx-alpha', 'min_raster_dpi', v2).sourceId).toBe('v2:fact-7')
  })
})
