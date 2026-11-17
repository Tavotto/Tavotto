import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import type { JournalFieldSource } from '@/lib/journals'
import { SourceCardBody } from './SourceButton'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLElement
let root: Root
const FILE = `snapshots/${'a'.repeat(64)}.webp`
const make = (over: Partial<JournalFieldSource>) =>
  ({
    value: 300, value_alt: null, qualifier: 'min', stage: 'any', mark: '300 dpi', derivation: '', snapshot: null, unset: false,
    quote: 'Images must be at least 300 dpi.', src: 'v2:s1:journal', rule_scope: 'journal', source_scope: 'journal', coverage: 'journal_specific',
    affects: 'preflight', url: 'https://example.invalid/g', link: 'https://example.invalid/g', anchor: '', kind: 'html', page: null,
    retrieved: '2026-11-15', archive_url: '', overrides: null, ...over,
  }) as JournalFieldSource

beforeEach(async () => {
  await setLocale('zh-CN')
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

describe('出处卡的 v2 截图缩略图', () => {
  it('v2 条目带截图且给了期刊 id：摘录在顶上、缩略图在底栏之后（走本地接口），并标出新版数据', async () => {
    await act(async () => root.render(<SourceCardBody journal="J" journalId="fx-j" entries={[['min_raster_dpi', make({ snapshot: { file: FILE, width: 10, height: 10 } })]]} />))
    const card = host.querySelector('[data-journal-card]')!
    const src = card.querySelector('[data-journal-thumb] img')?.getAttribute('src') ?? ''
    expect(src).toBe(`/api/journal-facts/snapshot?journal_id=fx-j&file=${encodeURIComponent(FILE)}`)
    expect(card.lastElementChild?.hasAttribute('data-journal-thumb')).toBe(true)
    expect(card.querySelector('[data-journal-quote]')).not.toBeNull()
    expect(card.querySelector('[data-journal-snapshot]')).toBeNull()
    expect(card.textContent).toContain('新版数据')
  })
  it('v1 条目、或没给期刊 id、或没有截图：什么都不加（现有卡片不变）', async () => {
    const v1 = make({ src: 'a', snapshot: { file: FILE, width: 10, height: 10 } })
    await act(async () => root.render(<SourceCardBody journal="J" journalId="fx-j" entries={[['min_raster_dpi', v1]]} />))
    expect(host.querySelector('[data-journal-thumb]')).toBeNull()
    expect(host.textContent).not.toContain('新版数据')
    await act(async () => root.render(<SourceCardBody journal="J" entries={[['min_raster_dpi', make({ snapshot: { file: FILE, width: 10, height: 10 } })]]} />))
    expect(host.querySelector('[data-journal-thumb]')).toBeNull()
    await act(async () => root.render(<SourceCardBody journal="J" journalId="fx-j" entries={[['min_raster_dpi', make({})]]} />))
    expect(host.querySelector('[data-journal-thumb]')).toBeNull()
  })
})
