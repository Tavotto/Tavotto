/**
 * 期刊选择器的「写到哪」（`JournalPickTarget`）：不传 = 写当前画布的文档（设置里的用法，行为不变）；
 * 传了就只调目标的函数、不碰文档；等下载期间归属变了（`origin`）就丢弃这次结果。网络 / 额度 / 序号那一整套只有选择器里一份。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { loadCatalog } from '@/lib/journals'
import { JOURNAL_UI_DEV_KEY } from '@/lib/journalUi'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { emptyProject } from '@/types/document'
import { JournalPicker, type JournalPickTarget } from './JournalPicker'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const gate = vi.hoisted(() => ({ fn: null as null | ((id: string) => Promise<{ state: string; retryAfterS: number | null }>) }))
vi.mock('@/lib/journalCatalogSync', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/lib/journalCatalogSync')>()
  return { ...orig, ensureJournalReady: (id: string) => (gate.fn ? gate.fn(id) : orig.ensureJournalReady(id)) }
})

let host: HTMLElement
let root: Root
const q = (sel: string) => document.body.querySelector(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const mount = (node: React.ReactNode) =>
  act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })

beforeEach(async () => {
  localStorage.clear()
  localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_picker_target')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  gate.fn = null
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

const target = (over: Partial<JournalPickTarget> = {}): JournalPickTarget => ({
  ref: undefined, select: vi.fn(), selectV2Only: vi.fn(), clear: vi.fn(), origin: () => 'o', ...over,
})

describe('缺省目标 = 写当前画布', () => {
  it('触发器留着 id 与 data-journal-picker；点选写进文档，「不按期刊」退回', async () => {
    await mount(<JournalPicker />)
    const trigger = q('[data-journal-picker]')!
    expect(trigger.id).toBe('journal-picker')
    expect(trigger.className).toContain('w-65')
    expect(q('[data-draft-journal-picker]')).toBeNull()
    await click(trigger)
    await click(q('[data-journal-option="fx-beta"]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-beta')
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-none]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })
})

describe('传了目标', () => {
  it('选定只调目标的 select，文档一个字节不动；触发器用自己的锚点、不带 id；类名并进去、可覆盖宽度', async () => {
    const t = target()
    const doc0 = useDocumentStore.getState().doc
    await mount(<JournalPicker target={t} triggerClassName="w-full" />)
    const trigger = q('[data-draft-journal-picker]')!
    expect(trigger.id).toBe('')
    expect(q('[data-journal-picker]')).toBeNull()
    expect(trigger.className).toContain('w-full')
    expect(trigger.className).not.toContain('w-65')
    await click(trigger)
    await click(q('[data-journal-option="fx-beta"]'))
    expect(t.select).toHaveBeenCalledTimes(1)
    expect(vi.mocked(t.select).mock.calls[0]![0]).toBe('fx-beta')
    expect(useDocumentStore.getState().doc).toBe(doc0)
  })

  it('「不按期刊」调目标的 clear；列表里的选中项与触发器名字跟目标的 ref 走（不看文档）', async () => {
    const t = target({ ref: { id: 'fx-alpha', label: 'Fixture Alpha' } })
    await mount(<JournalPicker target={t} />)
    expect(q('[data-draft-journal-picker]')!.textContent).toContain('Fixture Alpha')
    await click(q('[data-draft-journal-picker]'))
    expect(q('[data-journal-option="fx-alpha"]')!.getAttribute('aria-selected')).toBe('true')
    await click(q('[data-journal-none]'))
    expect(t.clear).toHaveBeenCalledTimes(1)
  })

  it('等下载期间归属变了（origin 不同）：这次结果丢弃，不写', async () => {
    let n = 0
    const t = target({ origin: () => String(n++) })
    await mount(<JournalPicker target={t} />)
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    expect(t.select).not.toHaveBeenCalled()
  })

  it('disabled：触发器禁用', async () => {
    await mount(<JournalPicker target={target()} disabled />)
    expect((q('[data-draft-journal-picker]') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('点选立即反馈（等下载期间）', () => {
  const deferred = () => {
    let resolve!: (v: { state: string; retryAfterS: number | null }) => void
    const p = new Promise<{ state: string; retryAfterS: number | null }>((r) => (resolve = r))
    return { p, resolve }
  }
  const text = () => q('[data-draft-journal-picker]')!.textContent
  const busy = () => q('[data-draft-journal-picker]')!.getAttribute('aria-busy')
  /** 点一项，但不等网络：只让点击本身的同步部分落地 */
  const clickNow = (sel: string) => act(() => void (q(sel) as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true })))

  it('点击那一刻：浮层已关、触发器已是新刊名、标了「还在取」；取到后才提交，状态撤掉', async () => {
    const d = deferred()
    gate.fn = () => d.p
    const t = target({ ref: { id: 'fx-alpha', label: 'Fixture Alpha' } })
    await mount(<JournalPicker target={t} />)
    await click(q('[data-draft-journal-picker]'))
    await clickNow('[data-journal-option="fx-beta"]')
    expect(q('[data-journal-list]')).toBeNull()
    expect(text()).toContain('Fixture Beta')
    expect(busy()).toBe('true')
    expect(t.select).not.toHaveBeenCalled()
    await act(async () => d.resolve({ state: 'ready', retryAfterS: null }))
    expect(t.select).toHaveBeenCalledTimes(1)
    expect(busy()).toBeNull()
  })

  it('取失败：触发器回到原来的刊名，不提交', async () => {
    const d = deferred()
    gate.fn = () => d.p
    const t = target({ ref: { id: 'fx-alpha', label: 'Fixture Alpha' } })
    await mount(<JournalPicker target={t} />)
    await click(q('[data-draft-journal-picker]'))
    await clickNow('[data-journal-option="fx-beta"]')
    expect(text()).toContain('Fixture Beta')
    await act(async () => d.resolve({ state: 'offline', retryAfterS: null }))
    expect(text()).toContain('Fixture Alpha')
    expect(busy()).toBeNull()
    expect(t.select).not.toHaveBeenCalled()
  })

  it('额度用完：照旧走 clear，触发器回到 ref 的刊名', async () => {
    const d = deferred()
    gate.fn = () => d.p
    const t = target({ ref: { id: 'fx-alpha', label: 'Fixture Alpha' } })
    await mount(<JournalPicker target={t} />)
    await click(q('[data-draft-journal-picker]'))
    await clickNow('[data-journal-option="fx-beta"]')
    await act(async () => d.resolve({ state: 'quota', retryAfterS: null }))
    expect(t.clear).toHaveBeenCalledTimes(1)
    expect(text()).toContain('Fixture Alpha')
  })

  it('被新选择取代：先点的那次结果丢弃；触发器停在后点的刊名，直到后点的取到', async () => {
    const first = deferred()
    const second = deferred()
    const queue = [first, second]
    gate.fn = () => queue.shift()!.p
    const t = target({ ref: { id: 'fx-alpha', label: 'Fixture Alpha' } })
    await mount(<JournalPicker target={t} />)
    await click(q('[data-draft-journal-picker]'))
    await clickNow('[data-journal-option="fx-beta"]')
    await click(q('[data-draft-journal-picker]'))
    await clickNow('[data-journal-option="fx-gamma"]')
    await act(async () => first.resolve({ state: 'ready', retryAfterS: null }))
    expect(t.select).not.toHaveBeenCalled()
    expect(busy()).toBe('true') // 先点的那次结束没把后点的状态撤掉
    expect(text()).not.toContain('Fixture Beta')
    await act(async () => second.resolve({ state: 'ready', retryAfterS: null }))
    expect(vi.mocked(t.select).mock.calls.map((c) => c[0])).toEqual(['fx-gamma'])
    expect(busy()).toBeNull()
  })

  it('设置里的选择器（缺省目标）同样：点击即关、即显新名', async () => {
    const d = deferred()
    gate.fn = () => d.p
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await clickNow('[data-journal-option="fx-beta"]')
    expect(q('[data-journal-list]')).toBeNull()
    expect(q('[data-journal-picker]')!.textContent).toContain('Fixture Beta')
    expect(useDocumentStore.getState().doc.profile).toBeUndefined()
    await act(async () => d.resolve({ state: 'ready', retryAfterS: null }))
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-beta')
  })
})
