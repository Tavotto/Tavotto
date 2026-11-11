import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { journalSources, loadCatalog, type JournalFieldSource } from '@/lib/journals'
import { useDocumentStore } from '@/store/documentStore'
import { emptyDocument } from '@/types/document'
import { hasChildAdultSource } from '@/lib/journalSourceIdentity'
import { JournalSourceIdentityWarning } from './JournalSourceIdentityWarning'

const child = 'applied-neuropsychology-child'
const adult = 'https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode=hapn21'
const correct = 'https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode=hapc20'
const fixture = journalSources('fx-beta', loadCatalog(vectors.catalog)).min_raster_dpi
const sources = (url: string, link = url): Record<string, JournalFieldSource> => ({
  min_raster_dpi: { ...fixture, url, link },
})
globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => { vi.restoreAllMocks() })

it('matches only the Child ID and a saved Adult source, including case differences', () => {
  expect(hasChildAdultSource(child, sources(adult))).toBe(true)
  expect(hasChildAdultSource(child, sources('https://WWW.TANDFONLINE.COM:443/action/authorSubmission?journalCode=hapn21&show=instructions'))).toBe(true)
  expect(hasChildAdultSource(child.toUpperCase(), sources(adult))).toBe(false)
  expect(hasChildAdultSource(child, sources(correct, adult))).toBe(true)
  expect(hasChildAdultSource(child, undefined)).toBe(false)
  expect(hasChildAdultSource(child, sources(correct))).toBe(false)
  expect(hasChildAdultSource('applied-neuropsychology-adult', sources(adult))).toBe(false)
  expect(hasChildAdultSource(child, sources('https://www.tandfonline.com.evil.test/action/authorSubmission?journalCode=hapn21&show=instructions'))).toBe(false)
  for (const url of [
    'https://www.tandfonline.com/action/authorSubmission?journalCode=hapn21&show=instructions&extra=1',
    'https://www.tandfonline.com/action/authorSubmission?%73how=instructions&journalCode=hapn21',
    'https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode=hapn%32%31',
    'https://www.tandfonline.com/action/authorSubmission?journalCode=hapn21',
    'https://www.tandfonline.com/action/authorSubmission?journalCode=hapn21&show=instructions&show=instructions',
    'http://www.tandfonline.com/action/authorSubmission?journalCode=hapn21&show=instructions',
    'https://www.tandfonline.com:444/action/authorSubmission?journalCode=hapn21&show=instructions',
    'https://user@www.tandfonline.com/action/authorSubmission?journalCode=hapn21&show=instructions',
    'https://www.tandfonline.com/action/AUTHORSUBMISSION?journalCode=hapn21&show=instructions',
    'https://www.tandfonline.com/action/authorSubmission?JOURNALCODE=hapn21&show=instructions',
    'https://www.tandfonline.com/action/authorSubmission?journalCode=HAPN21&show=instructions',
    'https://www.tandfonline.com/action/authorSubmission?journalCode=hapn21&JOURNALCODE=hapc20&show=instructions',
  ]) expect(hasChildAdultSource(child, sources(url))).toBe(false)

  expect(hasChildAdultSource(child, sources('https://www.tandfonline.com/action/authorSubmission?journalCode=hapc20&journalCode=hapn21&show=instructions'))).toBe(false)
  expect(hasChildAdultSource(child, sources('https://www.tandfonline.com/action/authorSubmission?journalCode=hapn210&show=instructions'))).toBe(false)
})

it('shows offline from an old saved snapshot without changing it, then clears for a corrected snapshot', async () => {
  await setLocale('en-US')
  const savedLegacy = { ...emptyDocument(), profile: {
    id: 'journal-base-v1', journalRef: { id: child, label: 'Child', catalogRev: 6, retrieved: '2026-11-02' },
    journalSources: sources(adult), snapshot: { min_raster_dpi: 300 },
  } }
  const persisted = JSON.parse(JSON.stringify(savedLegacy))
  expect(await useDocumentStore.getState().switchDocument(persisted, 'd_identity_old')).toBe(true)
  const saved = structuredClone(useDocumentStore.getState().doc)
  const before = JSON.stringify(saved)
  const fetcher = vi.spyOn(globalThis, 'fetch')
  const host = document.createElement('div'); document.body.appendChild(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<JournalSourceIdentityWarning />))
    expect(host.querySelector('[data-journal-source-identity-warning]')?.textContent).toContain('source identity needs verification')
    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(before)
    expect(fetcher).not.toHaveBeenCalled()
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: sources(correct) } } })))
    expect(host.querySelector('[data-journal-source-identity-warning]')).toBeNull()
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile: { ...s.doc.profile!, journalSources: undefined } } })))
    expect(host.querySelector('[data-journal-source-identity-warning]')).toBeNull()
  } finally {
    await act(async () => root.unmount()); host.remove()
  }
})
