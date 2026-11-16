import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { setLocale } from '@/i18n'
import type { JournalFieldSource } from '@/lib/journals'
import { leukosSourceFixture } from '@/test/leukosSource.fixture'
import { useDocumentStore } from '@/store/documentStore'
import { emptyDocument } from '@/types/document'
import { hasLeukosHistoricalSource, JournalLeukosScopeWarning } from './JournalLeukosScopeWarning'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const fixture = leukosSourceFixture()
const sources = (source: JournalFieldSource | undefined) => source ? { min_raster_dpi: source } : undefined

it('matches only the exact saved source and fails closed for missing or altered evidence', () => {
  const missingStage = { ...fixture }
  delete (missingStage as Partial<JournalFieldSource>).stage
  const missingSnapshot = { ...fixture }
  delete (missingSnapshot as Partial<JournalFieldSource>).snapshot
  expect(hasLeukosHistoricalSource('leukos', sources(fixture))).toBe(true)
  expect(hasLeukosHistoricalSource('other', sources(fixture))).toBe(false)
  expect(hasLeukosHistoricalSource('LEUKOS', sources(fixture))).toBe(false)
  expect(hasLeukosHistoricalSource('leukos', undefined)).toBe(false)
  expect(hasLeukosHistoricalSource('leukos', {})).toBe(false)
  for (const altered of [
    { ...fixture, url: 'https://example.invalid', link: fixture.url },
    { ...fixture, url: `${fixture.url}#x` },
    { ...fixture, quote: fixture.quote.replace('final submission', 'initial submission') },
    { ...fixture, quote: fixture.quote.replace('300 dpi for color', '300 dpi for colour') },
    { ...fixture, snapshot: null }, missingSnapshot,
    { ...fixture, snapshot: { ...fixture.snapshot!, file: 'snapshots/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff.webp' } },
    { ...fixture, snapshot: { ...fixture.snapshot!, file: `${fixture.snapshot!.file}?other` } },
    { ...fixture, snapshot: { ...fixture.snapshot!, width: 1057 } },
    { ...fixture, snapshot: { ...fixture.snapshot!, height: 518 } },
    { ...fixture, stage: 'submission' as const },
    { ...fixture, stage: 'any' as const },
    missingStage,
    { ...fixture, stage: 'unknown' } as unknown as JournalFieldSource,
    { ...fixture, value: 600 }, { ...fixture, qualifier: 'max' as const },
    { ...fixture, affects: 'info' as const }, { ...fixture, unset: true },
  ]) expect(hasLeukosHistoricalSource('leukos', sources(altered))).toBe(false)
})

it('matches an intact JSON-round-tripped saved binding', () => {
  const saved = JSON.parse(JSON.stringify({ journalRef: { id: 'leukos' }, journalSources: sources(fixture) }))
  expect(hasLeukosHistoricalSource(saved.journalRef.id, saved.journalSources)).toBe(true)
})

it.each([
  ['publisher rule scope', 'rule_scope', 'publisher'],
  ['missing rule scope', 'rule_scope', undefined],
  ['publisher source scope', 'source_scope', 'publisher'],
  ['missing source scope', 'source_scope', undefined],
  ['publisher-general coverage', 'coverage', 'publisher_general'],
  ['sample coverage', 'coverage', 'single_journal_sample'],
  ['missing coverage', 'coverage', undefined],
] as const)('rejects %s', (_label, field, value) => {
  const altered = { ...fixture }
  if (value === undefined) delete (altered as Partial<JournalFieldSource>)[field]
  else Object.assign(altered, { [field]: value })
  expect(hasLeukosHistoricalSource('leukos', sources(altered))).toBe(false)
})

it('shows bilingual saved-source limitations without a network request or snapshot mutation', async () => {
  const doc = JSON.parse(JSON.stringify({ ...emptyDocument(), profile: {
    id: 'journal-base-v1', journalRef: { id: 'leukos', label: 'LEUKOS', catalogRev: 6, retrieved: '2026-11-03' },
    journalSources: sources(fixture), snapshot: { min_raster_dpi: 300 },
  } }))
  await useDocumentStore.getState().switchDocument(doc, 'd_leukos_old')
  const before = structuredClone(useDocumentStore.getState().doc)
  const fetcher = vi.spyOn(globalThis, 'fetch')
  const host = document.createElement('div'); document.body.appendChild(host); const root = createRoot(host)
  try {
    await setLocale('zh-CN')
    await act(async () => root.render(<JournalLeukosScopeWarning />))
    const notice = () => host.querySelector('[data-leukos-scope-warning]')
    expect(notice()?.textContent).toContain('历史来源针对终稿并区分图类')
    expect(notice()?.textContent).toContain('预检未区分投稿阶段和图类')
    expect(notice()?.textContent).toContain('现行要求未知')
    await act(async () => { await setLocale('en-US') })
    expect(notice()?.textContent).toContain('saved historical source')
    expect(notice()?.textContent).toContain('does not distinguish submission stages or figure types')
    expect(notice()?.textContent).toContain('current requirements are unverified')
    expect(useDocumentStore.getState().doc).toEqual(before)
    expect(fetcher).not.toHaveBeenCalled()
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: undefined } } })))
    expect(notice()).toBeNull()
  } finally { await act(async () => root.unmount()); host.remove(); fetcher.mockRestore() }
})
