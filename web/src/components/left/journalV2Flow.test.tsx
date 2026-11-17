/**
 * v2 事实进预检的界面与存取（ADR 0155 §14）：打开项目自动更新（一次历史、幂等、取不到不动）、
 * 「需要你确认」不计数不标色、点开出处卡带原文截图缩略图。夹具期刊，无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { loadCatalog } from '@/lib/journals'
import type { V2Patch } from '@/lib/journalV2'
import { useJournalV2Sync } from '@/hooks/useJournalV2Sync'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { refreshJournalV2, selectJournal } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import { emptyProject } from '@/types/document'
import { registerGesture } from '@/store/gestureCoordinator'
import { ConfirmEntry } from './ProblemTreeRows'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const SRC = { url: 'https://example.invalid/v2/guide', kind: 'html' as const, retrieved: '2026-11-15', coverage: 'journal_specific' as const }
const FILE = `snapshots/${'b'.repeat(64)}.webp`
const patch = (over: Partial<V2Patch> = {}): V2Patch => ({
  catalog_rev: 2,
  rules: { min_raster_dpi: { value: 450, src: 'v2:s1:journal', quote: 'At least 450 dpi.', qualifier: 'min', strength: 'required', stage: 'any' } },
  sources: { 'v2:s1:journal': SRC },
  confirm: [
    { id: 'c1', subject: 'min_line_width', relation: 'gte', value: 2, unit: 'pt', condition: '', quote: 'Lines should be at least 2 pt wide', strength: 'recommended', stage: 'unknown', snapshot: { file: FILE, width: 10, height: 10 }, source: SRC },
    { id: 'c2', subject: 'width', relation: 'gte', value: 4, unit: 'in', condition: 'One column width figures', quote: 'One column width figures at least 4 in', strength: 'recommended', stage: 'unknown', source: SRC },
  ],
  ...over,
})

let host: HTMLDivElement
let root: Root
const pastCount = () => useDocumentStore.getState().past.length
const journal = () => useDocumentStore.getState().doc.profile?.journal as Record<string, unknown> | undefined

function Probe() {
  useJournalV2Sync()
  return null
}

beforeEach(async () => {
  await setLocale('zh-CN')
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useJournalV2Store.setState({ byJournal: {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_v2_flow')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
  vi.restoreAllMocks()
})

describe('refreshJournalV2（打开项目自动更新）', () => {
  it('叠上去 = 一次历史；同一份再来不写；撤销回到只有 v1', () => {
    selectJournal('fx-beta')
    const v1 = journal()!.min_raster_dpi
    const before = pastCount()
    refreshJournalV2('fx-beta', patch())
    expect(journal()!.min_raster_dpi).toBe(450)
    expect(pastCount()).toBe(before + 1)
    refreshJournalV2('fx-beta', patch())
    expect(pastCount()).toBe(before + 1)
    useDocumentStore.getState().undo()
    expect(journal()!.min_raster_dpi).toBe(v1)
  })
  it('取不到（null）/ 不是当前绑定的刊 / 没按期刊选过：什么都不写', () => {
    refreshJournalV2('fx-beta', patch())
    expect(pastCount()).toBe(0)
    selectJournal('fx-beta')
    const before = pastCount()
    refreshJournalV2('fx-beta', null)
    refreshJournalV2('fx-alpha', patch())
    expect(pastCount()).toBe(before)
  })
})

describe('useJournalV2Sync', () => {
  it('已经叠过同一份 v2 的项目再打开：不写历史、不置脏（每次打开都不变的项目不能每次都变脏）', async () => {
    selectJournal('fx-beta')
    refreshJournalV2('fx-beta', patch())
    // 模拟存盘 → 重新打开：换一份内容相同、键序不同的文档
    const saved = JSON.parse(
      JSON.stringify(useDocumentStore.getState().doc, (_k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).reverse()) : v)),
    )
    await useDocumentStore.getState().switchDocument(saved, 'd_v2_reopen')
    useJournalV2Store.setState({ byJournal: {} })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(patch()), { status: 200 }))
    await act(async () => root.render(<Probe />))
    await act(async () => { await Promise.resolve() })
    expect(pastCount()).toBe(0)
    expect(useDocumentStore.getState().dirty).toBe(false)
  })
  it('打开已按期刊检查的项目：取到 v2 就自动叠上；取不到不动', async () => {
    selectJournal('fx-beta')
    useJournalV2Store.setState({ byJournal: {} })
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(patch()), { status: 200 }))
    await act(async () => root.render(<Probe />))
    await act(async () => { await Promise.resolve() })
    expect(spy).toHaveBeenCalledTimes(1)
    expect(journal()!.min_raster_dpi).toBe(450)
  })
  it('响应落在拖动 / 连续编辑中途：不打断它、不插进它的撤销，等手势收尾再叠', async () => {
    selectJournal('fx-beta')
    useJournalV2Store.setState({ byJournal: {} })
    const before = pastCount()
    const unregister = registerGesture(() => {})
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(patch()), { status: 200 }))
    await act(async () => root.render(<Probe />))
    await act(async () => { await Promise.resolve() })
    expect(pastCount()).toBe(before)
    expect(journal()!.min_raster_dpi).not.toBe(450)
    await act(async () => unregister())
    expect(journal()!.min_raster_dpi).toBe(450)
    expect(pastCount()).toBe(before + 1)
  })
  it('离线：绑定不变、不报错', async () => {
    selectJournal('fx-beta')
    const before = journal()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
    await act(async () => root.render(<Probe />))
    await act(async () => { await Promise.resolve() })
    expect(journal()).toEqual(before)
  })
})

describe('「需要你确认」组', () => {
  const mount = async () =>
    act(async () => root.render(<TooltipProvider><ul><ConfirmEntry /></ul></TooltipProvider>))
  it('取到 v2 才出现；折叠；标题带数；不画等级记号，不带底色', async () => {
    selectJournal('fx-beta')
    await mount()
    expect(host.querySelector('[data-confirm-group]')).toBeNull()
    await act(async () => useJournalV2Store.getState().remember('fx-beta', patch()))
    const toggle = host.querySelector<HTMLElement>('[data-confirm-group-toggle]')!
    expect(toggle.textContent).toBe('需要你确认（2）')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector('[data-confirm-row]')).toBeNull()
    expect(host.querySelector('[data-issue-group-mark]')).toBeNull()
    expect(host.innerHTML).not.toMatch(/bg-(warn|danger|info|ok|success)/)
  })
  it('展开：要求 + 灰字原文条件（逐字）；点开是出处卡，v2 原文截图缩略图走本地接口', async () => {
    selectJournal('fx-beta')
    await act(async () => useJournalV2Store.getState().remember('fx-beta', patch()))
    await mount()
    await act(async () => host.querySelector<HTMLElement>('[data-confirm-group-toggle]')!.click())
    const rows = [...host.querySelectorAll<HTMLElement>('[data-confirm-row]')]
    expect(rows.map((r) => r.textContent)).toEqual(['线宽 ≥ 2 pt', '图宽 ≥ 4 in · 原文条件：One column width figures'])
    await act(async () => rows[0].click())
    const card = document.body.querySelector('[data-journal-card]')!
    expect(card.textContent).toContain('Lines should be at least 2 pt wide')
    expect(card.textContent).toContain('建议')
    expect(card.querySelector('[data-journal-thumb] img')?.getAttribute('src')).toContain('/api/journal-facts/snapshot?journal_id=fx-beta')
  })
  it('v2 取不到（null）/ 没有要确认的：不出现', async () => {
    selectJournal('fx-beta')
    await act(async () => useJournalV2Store.getState().remember('fx-beta', null))
    await mount()
    expect(host.querySelector('[data-confirm-group]')).toBeNull()
    await act(async () => useJournalV2Store.getState().remember('fx-beta', patch({ confirm: [] })))
    expect(host.querySelector('[data-confirm-group]')).toBeNull()
  })
})
