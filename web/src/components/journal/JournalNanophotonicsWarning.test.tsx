import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { journalSources, loadCatalog, type JournalFieldSource } from '@/lib/journals'
import { useDocumentStore } from '@/store/documentStore'
import { emptyDocument } from '@/types/document'
import { nanophotonicsSourceState, NANOPHOTONICS_GUIDE } from '@/lib/journalNanophotonics'
import { JournalNanophotonicsWarning } from './JournalNanophotonicsWarning'
const id = 'nanophotonics'
const url = 'https://www.degruyterbrill.com/journal/key/nanoph/html'
const fixture = journalSources('fx-beta', loadCatalog(vectors.catalog)).min_raster_dpi
const sources = (sourceUrl: string, link = sourceUrl): Record<string, JournalFieldSource> => ({ min_raster_dpi: { ...fixture, url: sourceUrl, link } })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks() })
it('matches only the precise saved journal and raster source', () => {
  expect(nanophotonicsSourceState(id, sources(url))).toBe('legacy')
  expect(nanophotonicsSourceState(id, undefined)).toBe('unknown')
  expect(nanophotonicsSourceState(id, {})).toBe('unknown')
  expect(nanophotonicsSourceState('another-journal', sources(url))).toBeNull()
  expect(nanophotonicsSourceState(id.toUpperCase(), sources(url))).toBeNull()
  expect(nanophotonicsSourceState(id, sources('https://example.invalid', url))).toBeNull()
  expect(nanophotonicsSourceState(id, { 'info.font_names_raw': sources(url).min_raster_dpi })).toBe('unknown')
  for (const bad of [null, [], 'x', 42, { min_raster_dpi: null }, { min_raster_dpi: [] }, { min_raster_dpi: 'x' }, { min_raster_dpi: {} }, { min_raster_dpi: { url: null } }, { min_raster_dpi: { url: '' } }, { min_raster_dpi: { url: '  ' } }, { min_raster_dpi: { url: 99 } }, { min_raster_dpi: { url: 'not a URL' } }, { min_raster_dpi: { url: 'ftp://example.invalid/' } }, { min_raster_dpi: { url: 'http://example.invalid/' } }, { min_raster_dpi: { url: 'https://user@example.invalid/' } }]) {
    expect(nanophotonicsSourceState(id, bad as never)).toBe('unknown')
    expect(nanophotonicsSourceState('another-journal', bad as never)).toBeNull()
  }
  for (const bad of [
    'https://www.degruyterbrill.com.evil.invalid/journal/key/nanoph/html',
    'https://www.degruyterbrill.com/journal/key/nanoph/html?x=1',
    'https://www.degruyterbrill.com/journal/key/nanoph/html#section',
    'https://www.degruyterbrill.com/journal/key/other/html',
  ]) expect(nanophotonicsSourceState(id, sources(bad))).toBeNull()
})
it('warns offline from legacy evidence without changing the project and clears when evidence changes', async () => {
  await setLocale('en-US')
  const source = { ...fixture, url, link: url, value: 600 }
  delete (source as Partial<JournalFieldSource>).coverage
  delete (source as Partial<JournalFieldSource>).source_scope
  delete (source as Partial<JournalFieldSource>).rule_scope
  const persisted = JSON.parse(JSON.stringify({ ...emptyDocument(), profile: {
    id: 'journal-base-v1', journalRef: { id, label: 'Nanophotonics', catalogRev: 6, retrieved: '2026-11-03' },
    journalSources: { min_raster_dpi: source }, snapshot: { min_raster_dpi: 600 },
  } }))
  expect(await useDocumentStore.getState().switchDocument(persisted, 'd_nanophotonics_old')).toBe(true)
  const before = structuredClone(useDocumentStore.getState().doc)
  const fetcher = vi.spyOn(globalThis, 'fetch')
  const host = document.createElement('div'); document.body.appendChild(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<JournalNanophotonicsWarning />))
    const warning = host.querySelector('[data-journal-nanophotonics-warning]')
    expect(warning?.textContent).toContain('resolution sources from De Gruyter may be outdated')
    expect(warning?.textContent).toContain('effective project settings')
    expect(warning?.textContent).toContain('does not attribute any specific issue')
    expect(warning?.textContent).toContain('image-specific conditions')
    expect(warning?.getAttribute('role')).toBe('status')
    expect(warning?.querySelector('a')?.getAttribute('href')).toBe(NANOPHOTONICS_GUIDE)
    expect(useDocumentStore.getState().doc).toEqual(before)
    expect(fetcher).not.toHaveBeenCalled()
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: undefined, snapshot: undefined } } })))
    expect(host.querySelector('[data-journal-nanophotonics-warning]')?.getAttribute('data-journal-nanophotonics-warning')).toBe('unknown')
    expect(host.querySelector('[data-journal-nanophotonics-warning]')?.textContent).toContain('missing or cannot be identified')
    expect(host.querySelector('[data-journal-nanophotonics-warning]')?.textContent).not.toContain('De Gruyter')
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: sources('https://example.invalid') } } })))
    expect(host.querySelector('[data-journal-nanophotonics-warning]')).toBeNull()
  } finally { await act(async () => root.unmount()); host.remove() }
})
