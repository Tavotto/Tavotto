import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setLocale } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { JournalFactsView } from './JournalFactsView'

vi.mock('@/components/ui/Select', () => ({
  Select: ({ value, onChange, options, ariaLabel, disabled }: {
    value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; ariaLabel: string; disabled?: boolean
  }) => <select aria-label={ariaLabel} disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>,
}))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLElement
let root: Root
const panel = { id: 'panel-one', type: 'panel', name: 'Figure one', fileId: 'f.png', fileKind: 'raster', x: 0, y: 0, w: 10, h: 10, nativeW: 10, nativeH: 10, overrides: [] } as PanelObject
function bind(id = 'fx-one') {
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel], profile: { id: 'spec', journalRef: { id, label: `Journal ${id}`, catalogRev: 1, retrieved: '2026-01-01' } } } }))
}
function page(overrides = {}) {
  return { journal_id: 'fx-one', journal: { label: 'Fixture journal', publisher_id: 'fixture-publisher', issn: '1234-5678', eissn: '8765-4321' }, catalog_rev: 4, next_offset: null,
    sync_state: 'offline', sync_code: 'network', catalog_completeness: 'unknown', semantic_review: 'not_authenticated',
    facts: [{ id: 'f1', subject: 'raster_dpi', relation: 'gte', value: 600, unit: 'dpi', strength: 'recommended', stage: 'unknown', origin: 'journal', source: 's', quote: 'Original quote', conditions: { figure_kind: 'combination' }, condition_text: 'if suitable', context: [{ source: 's', quote: 'Heading context' }] }],
    coverage: [], sources: { s: { url: 'https://example.invalid/guide', retrieved: '2026-01-01' } }, ...overrides }
}
async function click(selector: string) {
  const el = host.querySelector(selector) as HTMLButtonElement | null
  expect(el).not.toBeNull()
  await act(async () => { el!.click() })
}
async function change(label: string, value: string) {
  const el = host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement
  await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })) })
}
function buttonWith(text: string): string {
  const buttons = [...host.querySelectorAll('button')]
  buttons.forEach((button) => button.removeAttribute('data-test-click'))
  const item = buttons.find((button) => button.textContent?.includes(text))
  expect(item).toBeTruthy()
  item!.setAttribute('data-test-click', '1')
  return 'button[data-test-click="1"]'
}

beforeEach(async () => {
  await setLocale('en-US')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_facts_ui')
  bind()
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host)
  await act(async () => root.render(<JournalFactsView />))
})
afterEach(async () => { await act(async () => root.unmount()); setCurrentProjectId(null); host.remove(); vi.restoreAllMocks() })

describe('source facts viewer', () => {
  it('opens an HTML fact at its text anchor while preserving the context PDF page', async () => {
    const item = { ...page().facts[0], anchor: '#:~:text=Original%20quote', context: [{ source: 'pdf', quote: 'Context on page seven' }] }
    const sources = { s: { url: 'https://example.invalid/guide', kind: 'html', retrieved: '2026-01-01' }, pdf: { url: 'https://example.invalid/guide.pdf', kind: 'pdf', page: 7, retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item], sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    const links = [...host.querySelectorAll('[data-fact-id="f1"] a')].map((link) => link.getAttribute('href'))
    expect(links).toEqual(['https://example.invalid/guide#:~:text=Original%20quote', 'https://example.invalid/guide.pdf#page=7'])
  })

  it('opens scoped-coverage PDFs at their recorded page', async () => {
    const coverage = [{ id: 'c', subject: 'font_size', result: 'not_stated', scope: 'Captured section', sources: ['pdf'] }]
    const sources = { pdf: { url: 'https://example.invalid/guide.pdf', kind: 'pdf', page: 7, retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [], coverage, sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-facts-page] a')?.getAttribute('href')).toBe('https://example.invalid/guide.pdf#page=7')
  })

  it('uses the PDF page for a PDF fact even if it also contains a text anchor', async () => {
    const item = { ...page().facts[0], source: 'pdf', anchor: '#:~:text=Original', context: undefined }
    const sources = { pdf: { url: 'https://example.invalid/guide.pdf', kind: 'pdf', page: 7, retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item], sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-fact-id="f1"] a')?.getAttribute('href')).toBe('https://example.invalid/guide.pdf#page=7')
  })

  it('does not reuse the fact anchor for separate context or duplicate an empty fragment', async () => {
    const item = { ...page().facts[0], anchor: '#:~:text=Original', context: [{ source: 's', quote: 'Other paragraph' }] }
    const sources = { s: { url: 'https://example.invalid/guide#', kind: 'html', retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item], sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    const links = [...host.querySelectorAll('[data-fact-id="f1"] a')].map((link) => link.getAttribute('href'))
    expect(links).toEqual(['https://example.invalid/guide#:~:text=Original', 'https://example.invalid/guide'])
  })

  it('keeps a valid text anchor on a PDF when no page is available', async () => {
    const item = { ...page().facts[0], source: 'pdf', anchor: '#:~:text=Original', context: undefined }
    const sources = { pdf: { url: 'https://example.invalid/guide.pdf', kind: 'pdf', retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item], sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-fact-id="f1"] a')?.getAttribute('href')).toBe('https://example.invalid/guide.pdf#:~:text=Original')
  })

  it.each(['javascript:alert(1)', '#other', '#:~:text=bad\nfragment', '#:~:text=\ud800'])('rejects invalid fact anchor %s', async (anchor) => {
    const item = { ...page().facts[0], anchor }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-facts-page]')).toBeNull()
  })

  it('accepts valid supplementary characters using the same character limit as the server', async () => {
    const anchor = '#:~:text=' + '🧪'.repeat(4087)
    const item = { ...page().facts[0], anchor, context: undefined }
    const sources = { s: { url: 'https://example.invalid/guide', kind: 'html', retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item], sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-facts-page] a')?.getAttribute('href')).toBe(`https://example.invalid/guide${anchor}`)
  })

  it.each(['http://example.invalid/guide', 'https://user:pass@example.invalid/guide', 'https://example.invalid/guide#other'])('does not link unsafe source URL %s', async (url) => {
    const sources = { s: { url, kind: 'html', retrieved: '2026-01-01' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ sources })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.querySelector('[data-facts-page] a')).toBeNull()
  })

  it('loads only on demand, shows source and unknown review without changing the document', async () => {
    const snapshot = JSON.stringify(useDocumentStore.getState().doc)
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page()), { status: 200 }))
    expect(fetcher).not.toHaveBeenCalled()
    expect(host.querySelector('[aria-expanded="false"]')).not.toBeNull()
    await click(buttonWith('View source facts'))
    expect(host.querySelector('[aria-expanded="true"]')).not.toBeNull()
    expect(fetcher).not.toHaveBeenCalled()
    await click(buttonWith('Read sources'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(host.textContent).toContain('Original quote')
    expect(host.textContent).toContain('Heading context')
    expect(host.textContent).toContain('independent semantic review is not authenticated')
    expect(host.textContent).toContain('Offline: showing verified cache')
    expect(host.textContent).toContain('Fixture journal')
    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(snapshot)
  })

  it('sends session object selection and revision-bound pagination', async () => {
    const inspection = { status: 'not_verifiable', facts: [{ id: 'f1', structured_conditions: { status: 'unknown' } }] }
    const first = page({ next_offset: 1, inspection })
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(first), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(page({ inspection })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await change('Object (optional)', 'panel:panel-one')
    await change('Figure kind', 'combination')
    await change('Submission stage', 'revision')
    await click(buttonWith('Read sources'))
    const firstBody = JSON.parse(String(fetcher.mock.calls[0][1]?.body))
    expect(firstBody.inspection).toEqual({ object_label: 'panel-one', figure_kind: 'combination', stage: 'revision' })
    expect(host.textContent).toContain('Field comparison (structural only; not applicability or compliance)')
    await click(buttonWith('Next page'))
    const second = JSON.parse(String(fetcher.mock.calls[1][1]?.body))
    expect(second.offset).toBe(1); expect(second.expected_catalog_rev).toBe(4)
  })

  it('does not show a delayed response after the journal changes', async () => {
    let resolve!: (r: Response) => void
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise((r) => { resolve = r }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    await act(async () => { bind('fx-two') })
    await act(async () => { resolve(new Response(JSON.stringify(page()), { status: 200 })) })
    expect(host.textContent).not.toContain('Original quote')
    expect(host.textContent).not.toContain('Fixture journal')
  })

  it('shows version failure without substituting the existing snapshot', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 'catalog_changed' }), { status: 409 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('catalog revision changed')
    expect(host.querySelector('[data-facts-page]')).toBeNull()
  })

  it('forgets classification and displayed results when the selected object changes', async () => {
    const inspection = { status: 'not_verifiable', facts: [{ id: 'f1', structured_conditions: { status: 'unknown' } }] }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ inspection })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await change('Object (optional)', 'panel:panel-one')
    await change('Figure kind', 'combination')
    await change('Submission stage', 'revision')
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Original quote')
    await act(async () => useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [{ ...panel, fileId: 'other.png' }] } })))
    expect(host.textContent).not.toContain('Original quote')
    expect((host.querySelector('select[aria-label="Object (optional)"]') as HTMLSelectElement).value).toBe('none')
    expect((host.querySelector('select[aria-label="Figure kind"]') as HTMLSelectElement).value).toBe('unknown')
  })

  it('rejects malformed successful responses rather than showing facts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [null] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Source facts could not be read')
    expect(host.querySelector('[data-facts-page]')).toBeNull()
  })


  it('clears already displayed facts immediately when the current project changes', async () => {
    setCurrentProjectId('project-a')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page()), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Original quote')
    await act(async () => setCurrentProjectId('project-b'))
    expect(host.textContent).not.toContain('Original quote')
    expect(host.textContent).toContain('View source facts')
  })


  it.each([
    ['gte', 'at least 600 dpi'],
    ['gt', 'above 600 dpi'],
    ['lte', 'at most 600 dpi'],
    ['lt', 'below 600 dpi'],
    ['eq', 'is 600 dpi'],
  ])('presents the supported %s relation without changing its meaning', async (relation, expected) => {
    const item = { ...page().facts[0], relation, conditions: { figure_kind: 'combination' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain(`Raster resolution ${expected}`)
    expect(host.textContent).toContain('Figure kind: Combination')
    expect(host.textContent).toContain('Recommended')
    expect(host.textContent).toContain('Journal entry')
    expect(host.textContent).not.toContain(`raster_dpi: ${relation}`)
  })

  it('preserves unknown conditions and source context without guessing aliases', async () => {
    const item = { ...page().facts[0], conditions: { figure_kind: 'line', fine_line: true } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Cannot interpret automatically')
    expect(host.textContent).toContain('"figure_kind":"line"')
    expect(host.textContent).toContain('"fine_line":true')
    expect(host.textContent).toContain('Original quote')
    expect(host.textContent).toContain('Heading context')
    expect(host.textContent).toContain('if suitable')
    expect(host.textContent).not.toContain('Figure kind: Line art')
  })

  it('shows supported alternatives as alternatives, not a joint requirement', async () => {
    const item = { ...page().facts[0], conditions: { figure_kind: { relation: 'in', value: ['line_art', 'halftone'] } } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Figure kind is one of: Line art, Halftone')
    expect(host.textContent).toContain('Not verified; independent semantic review is not authenticated')
  })

  it('does not interpret an unsupported subject or hide its value and unit', async () => {
    const item = { ...page().facts[0], subject: 'other_metric', relation: 'gte', value: 777, unit: 'px' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Cannot interpret automatically')
    expect(host.textContent).toContain('other_metric · gte · 777 px')
    expect(host.textContent).toContain('Original quote')
    expect(host.textContent).not.toContain('Raster resolution at least')
  })

  it('presents the supported fields in Chinese', async () => {
    await act(async () => setLocale('zh-CN'))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page()), { status: 200 }))
    await click(buttonWith('查看来源事实'))
    await click(buttonWith('读取来源'))
    expect(host.textContent).toContain('位图分辨率至少 600 dpi')
    expect(host.textContent).toContain('图类：组合图')
    expect(host.textContent).toContain('独立语义审校未认证')
  })


  it('retains publisher origin, derivation, and unknown stage or strength values', async () => {
    const item = { ...page().facts[0], origin: 'publisher', derivation: 'Only for this special case', stage: 'other_stage', strength: 'other_strength' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Publisher entry')
    expect(host.textContent).toContain('Only for this special case')
    expect(host.textContent).toContain('other_stage')
    expect(host.textContent).toContain('other_strength')
    expect(host.textContent).toContain('Cannot interpret automatically')
  })

  it('does not interpret unsupported units or interval endpoints', async () => {
    const item = { ...page().facts[0], unit: 'px', relation: 'interval', value: { lower: 100, upper: 200, lower_inclusive: true, upper_inclusive: false } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ facts: [item] })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Cannot interpret automatically')
    expect(host.textContent).toContain('"lower_inclusive":true')
    expect(host.textContent).toContain('"upper_inclusive":false')
    expect(host.textContent).toContain(' px')
    expect(host.textContent).toContain('Original quote')
  })


  it('distinguishes identically named objects and sends the selected stable ID', async () => {
    const other = { ...panel, id: 'panel-two' }
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel, other] } }))
    const inspection = { status: 'not_verifiable', facts: [{ id: 'f1', structured_conditions: { status: 'unknown' } }] }
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ inspection })), { status: 200 }))
    await click(buttonWith('View source facts'))
    const choices = [...host.querySelectorAll('select[aria-label="Object (optional)"] option')].map((o) => o.textContent)
    expect(choices).toContain('panel-one · Figure one')
    expect(choices).toContain('panel-two · Figure one')
    await change('Object (optional)', 'panel:panel-two')
    await click(buttonWith('Read sources'))
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).inspection.object_label).toBe('panel-two')
  })

  it('shows available v2 identity fields and leaves absent ISSNs unstated', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(page()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(page({ journal: { label: 'Fixture journal', publisher_id: 'other-publisher' } })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Journal ID: fx-one')
    expect(host.textContent).toContain('Publisher ID: fixture-publisher')
    expect(host.textContent).toContain('ISSN: 1234-5678')
    expect(host.textContent).toContain('eISSN: 8765-4321')
    await click(buttonWith('Restart from first page'))
    expect(host.textContent).toContain('Publisher ID: other-publisher')
    expect(host.textContent).not.toContain('1234-5678')
    expect(host.textContent).not.toContain('eISSN:')
  })

  it('gives session-specific recovery for 401 without claiming a data failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 'session_auth_required' }), { status: 401 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('No active session. Relaunch Tavotto and enter through the link it opens.')
    expect(host.querySelector('[data-facts-page]')).toBeNull()
  })

  it('permits an object literally named none without confusing it with no selection', async () => {
    const namedNone = { ...panel, id: 'none' }
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [namedNone] } }))
    const inspection = { status: 'not_verifiable', facts: [{ id: 'f1', structured_conditions: { status: 'unknown' } }] }
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page({ inspection })), { status: 200 }))
    await click(buttonWith('View source facts'))
    await change('Object (optional)', 'panel:none')
    await click(buttonWith('Read sources'))
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).inspection.object_label).toBe('none')
  })


  it('does not represent a generic document as journal-specific from origin alone', async () => {
    const item = { id: 'formats', subject: 'figure_format', relation: 'text', value: 'Vector graphics preferred', strength: 'preferred', stage: 'production', origin: 'journal', source: 's', review: { status: 'verified', reviewed: '2026-11-11', method: 'fixture' }, quote: 'Vector graphics formats (EPS, PDF) are strongly preferred to raster ones (JPEG, PNG).' }
    const mock = page({ facts: [item], sources: { s: { url: 'https://ems.press/author-guidelines', retrieved: '2026-11-04' } } })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(mock), { status: 200 }))
    await click(buttonWith('View source facts'))
    await click(buttonWith('Read sources'))
    expect(host.textContent).toContain('Catalog record label (unverified): Journal entry')
    expect(host.textContent).toContain('Source scope: Not established')
    expect(host.textContent).toContain('https://ems.press/author-guidelines')
    expect(host.textContent).toContain(item.quote)
    expect(host.textContent).not.toContain('Source level: Journal source')
  })

})
