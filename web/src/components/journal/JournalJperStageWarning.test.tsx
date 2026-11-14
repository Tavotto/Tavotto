import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { journalSources, loadCatalog, type JournalFieldSource } from '@/lib/journals'
import { useDocumentStore } from '@/store/documentStore'
import { emptyDocument } from '@/types/document'
import { hasJperHistoricalSource, JournalJperStageWarning } from './JournalJperStageWarning'

const id = 'journal-of-planning-education-and-research'
const url = 'https://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf'
const fixture = journalSources('fx-beta', loadCatalog(vectors.catalog)).min_raster_dpi
const sources = (sourceUrl: string, link = sourceUrl): Record<string, JournalFieldSource> => ({
  max_font_size_pt: { ...fixture, url: sourceUrl, link },
})
globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => { vi.restoreAllMocks() })

it('requires the exact journal ID and saved source URL', () => {
  expect(hasJperHistoricalSource(id, sources(url))).toBe(true)
  expect(hasJperHistoricalSource(id, { ...sources('https://example.invalid'), min_raster_dpi: { ...fixture, url } })).toBe(true)
  expect(hasJperHistoricalSource(id.toUpperCase(), sources(url))).toBe(false)
  expect(hasJperHistoricalSource('another-journal', sources(url))).toBe(false)
  expect(hasJperHistoricalSource(id, undefined)).toBe(false)
  expect(hasJperHistoricalSource(id, {})).toBe(false)
  expect(hasJperHistoricalSource(id, sources('https://example.invalid', url))).toBe(false)
  for (const bad of [
    'http://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf',
    'https://journals.sagepub.com.evil.invalid/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf',
    'https://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf?x=1',
    'https://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf#page=1',
    'https://journals.sagepub.com/pb-assets/jper-guidelines-for-submission-of-accepted-manuscripts-2019.pdf',
    'https://user@journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf',
    'https://journals.sagepub.com/pb-assets/JPER-Guidelines-for-Submission-of-Accepted-Manuscripts-2019.pdf.evil',
  ]) expect(hasJperHistoricalSource(id, sources(bad))).toBe(false)
})

it('warns from an old saved project offline without changing it and clears if source is removed', async () => {
  await setLocale('en-US')
  const legacySource = { ...fixture, url, link: url }
  delete (legacySource as Partial<JournalFieldSource>).coverage
  delete (legacySource as Partial<JournalFieldSource>).source_scope
  delete (legacySource as Partial<JournalFieldSource>).rule_scope
  const persisted = JSON.parse(JSON.stringify({ ...emptyDocument(), profile: {
    id: 'journal-base-v1', journalRef: { id, label: 'JPER', catalogRev: 6, retrieved: '2026-11-03' },
    journalSources: { max_font_size_pt: legacySource }, snapshot: { max_font_size_pt: 12 },
  } }))
  expect(await useDocumentStore.getState().switchDocument(persisted, 'd_jper_old')).toBe(true)
  const before = structuredClone(useDocumentStore.getState().doc)
  const fetcher = vi.spyOn(globalThis, 'fetch')
  const host = document.createElement('div'); document.body.appendChild(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<JournalJperStageWarning />))
    const warning = host.querySelector('[data-journal-jper-stage-warning]')
    expect(warning?.textContent).toContain('historical guidelines for the final version of accepted manuscripts')
    expect(warning?.textContent).toContain('does not distinguish submission stages')
    expect(warning?.textContent).toContain('Current requirements have not been verified')
    expect(warning?.getAttribute('role')).toBe('status')
    expect(useDocumentStore.getState().doc).toEqual(before)
    expect(fetcher).not.toHaveBeenCalled()
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: undefined } } })))
    expect(host.querySelector('[data-journal-jper-stage-warning]')).toBeNull()
  } finally {
    await act(async () => root.unmount()); host.remove()
  }
})
