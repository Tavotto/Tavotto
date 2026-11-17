import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import type { JournalFieldSource } from '@/lib/journals'
import { SourceCardBody } from './SourceButton'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLElement
let root: Root
const source = {
  value: 300, value_alt: null, qualifier: 'min', stage: 'any', mark: '300 dpi', derivation: '', snapshot: null, unset: false,
  quote: 'Images must be at least 300 dpi.', src: '', rule_scope: 'journal', source_scope: 'journal', coverage: 'journal_specific',
  affects: 'preflight', url: 'https://example.invalid/g', link: 'https://example.invalid/g', anchor: '', kind: 'html', page: null,
  retrieved: '2026-11-15', archive_url: '', overrides: null,
} as unknown as JournalFieldSource

beforeEach(async () => {
  await setLocale('zh-CN')
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

describe('出处卡的 v2 截图缩略图', () => {
  it('调用方给了地址才画，在底栏之后', async () => {
    await act(async () => root.render(<SourceCardBody journal="J" entries={[['min_raster_dpi', source]]} thumb="/api/journal-facts/snapshot?x=1" />))
    const card = host.querySelector('[data-journal-card]')!
    expect(card.querySelector('[data-journal-thumb] img')?.getAttribute('src')).toBe('/api/journal-facts/snapshot?x=1')
    expect(card.lastElementChild?.hasAttribute('data-journal-thumb')).toBe(true)
  })
  it('没给就什么都不画（现有卡片不变）', async () => {
    await act(async () => root.render(<SourceCardBody journal="J" entries={[['min_raster_dpi', source]]} />))
    expect(host.querySelector('[data-journal-thumb]')).toBeNull()
  })
})
