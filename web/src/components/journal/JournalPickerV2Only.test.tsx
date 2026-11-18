/**
 * 只在 v2 里有的期刊进选刊列表（ADR 0155 §14，2026-11-17 用户拍板）：列表只读已签名 index（不逐刊拉包）、
 * 与 v1 刊同一入口，选中后只按 v2 判；v2 不可用时每个终态都不报错、不卡住。夹具期刊，无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { loadCatalog } from '@/lib/journals'
import { JOURNAL_UI_DEV_KEY } from '@/lib/journalUi'
import type { V2Patch } from '@/lib/journalV2'
import { useJournalV2Sync } from '@/hooks/useJournalV2Sync'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { JournalPicker } from './JournalPicker'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const SRC = { url: 'https://example.invalid/v2/guide', kind: 'html' as const, retrieved: '2026-11-15', coverage: 'journal_specific' as const }
const patch = (): V2Patch => ({
  catalog_rev: 2,
  rules: { min_raster_dpi: { value: 450, src: 'v2:s1:journal', quote: 'At least 450 dpi.', qualifier: 'min', strength: 'required', stage: 'any' } },
  sources: { 'v2:s1:journal': SRC },
  confirm: [],
})
const ENTRY = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  label: `Only ${id}`,
  publisher: 'fx-v2-pub',
  publisher_label: 'V2 Publisher',
  issn: '',
  eissn: '',
  aliases: [],
  ...over,
})

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>
let calls: string[] = []
const route = (handler: Route) => {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${url}${init?.body ? ` ${String(init.body)}` : ''}`)
      return handler(url, init)
    }),
  )
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const online: Route = (url) => {
  if (url === '/api/journal-facts/index') {
    return json({ catalog_rev: 2, journals: [ENTRY('zz-only', { issn: '9999-0001' }), ENTRY('fx-alpha')] })
  }
  if (url === '/api/journal-facts/preflight') return json(patch())
  return json({})
}

let host: HTMLElement
let root: Root
const q = (sel: string) => document.body.querySelector(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const mount = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}
const open = async () => {
  await mount(<JournalPicker />)
  await click(q('[data-journal-picker]'))
  await act(async () => {}) // index 的请求落地
}
const profile = () => useDocumentStore.getState().doc.profile

beforeEach(async () => {
  await setLocale('zh-CN')
  localStorage.clear()
  localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useJournalV2Store.setState({ index: null, byJournal: {} })
  useUiStore.getState().setStatus(null)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_v2only')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('选刊列表里的 v2-only 刊', () => {
  it('同一个列表、同一个入口：与 v1 刊一起按出版社分组；v1 已有的刊不重复；列表只请求 index，不逐刊拉包', async () => {
    route(online)
    await open()
    expect(q('[data-journal-option="zz-only"]')?.textContent).toBe('Only zz-only')
    expect(q('[data-journal-group="fx-v2-pub"]')).not.toBeNull()
    expect(document.body.querySelectorAll('[data-journal-option="fx-alpha"]')).toHaveLength(1)
    expect(calls.filter((c) => c.startsWith('/api/journal-facts/index'))).toHaveLength(1)
    expect(calls.some((c) => c.includes('/api/journal-facts/preflight') || c.includes('/api/journal-facts/journal'))).toBe(false)
    // 再开一次不重复取
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-picker]'))
    await act(async () => {})
    expect(calls.filter((c) => c.startsWith('/api/journal-facts/index'))).toHaveLength(1)
  })

  it('搜索名称与 ISSN 都能命中 v2-only 刊', async () => {
    route(online)
    await open()
    const input = q('[data-journal-search]') as HTMLInputElement
    const type = async (v: string) =>
      act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, v)
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
    await type('9999-0001')
    expect([...document.body.querySelectorAll('[data-journal-option]')].map((e) => e.getAttribute('data-journal-option'))).toEqual(['zz-only'])
    await type('only zz')
    expect(q('[data-journal-option="zz-only"]')).not.toBeNull()
  })

  it('选中：绑定只含 v2 折出的规则，不补任何 v1 默认；页宽不动', async () => {
    route(online)
    await open()
    const w = useDocumentStore.getState().doc.page.w
    await click(q('[data-journal-option="zz-only"]'))
    await act(async () => {})
    expect(profile()?.journalRef).toEqual({ id: 'zz-only', catalogRev: 2, retrieved: '2026-11-15', label: 'Only zz-only' })
    expect(profile()?.journal).toEqual({ min_raster_dpi: 450 })
    expect(Object.keys(profile()?.journalSources ?? {})).toEqual(['min_raster_dpi'])
    expect(useDocumentStore.getState().doc.page.w).toBe(w)
    // v1 的 ensure（下载一本 v1 刊）不该被调
    expect(calls.some((c) => c.startsWith('/api/journal-catalog/journal'))).toBe(false)
  })

  it('一步撤销全回去；「不按期刊」退回选之前', async () => {
    route(online)
    await open()
    const before = profile()
    await click(q('[data-journal-option="zz-only"]'))
    await act(async () => {})
    expect(profile()?.journalRef?.id).toBe('zz-only')
    await act(async () => useDocumentStore.getState().undo())
    expect(profile()).toEqual(before)
  })

  it('index 取不到（离线又没缓存 / 被拒 / 开关关）：列表里没有这些刊，别的照旧，不报错', async () => {
    for (const reply of [() => json({ code: 'offline' }, 503), () => json({ code: 'journal_catalog_disabled' }, 403), () => Promise.reject(new Error('down'))]) {
      route((url) => (url === '/api/journal-facts/index' ? reply() : json({})))
      useJournalV2Store.setState({ index: null })
      await open()
      expect(q('[data-journal-option="zz-only"]')).toBeNull()
      expect(q('[data-journal-option="fx-alpha"]')).not.toBeNull()
      expect(useUiStore.getState().statusTone).not.toBe('error')
      await click(q('[data-journal-picker]')) // 关
      await act(async () => root.render(<></>))
    }
  })

  it('选中时取不到 v2（离线 / 被拒 / 补丁不合法）：保持现状，一条「需要联网」胶囊，不绑、不报错', async () => {
    for (const status of [503, 403, 404]) {
      route((url) => (url === '/api/journal-facts/index' ? online(url) : json({ code: 'x' }, status)))
      useJournalV2Store.setState({ index: null })
      await open()
      await click(q('[data-journal-option="zz-only"]'))
      await act(async () => {})
      expect(profile()?.journalRef).toBeUndefined()
      expect(useUiStore.getState().status).toEqual(expect.objectContaining({ key: 'journal.needsNetwork' }))
      expect(useUiStore.getState().statusTone).toBe('info')
      await act(async () => root.render(<></>))
    }
  })

  it('选中时额度用完：与 v1 远程刊同一终态——暂按「不按期刊」，一条胶囊', async () => {
    route((url) => (url === '/api/journal-facts/index' ? online(url) : json({ code: 'quota_hour' }, 429)))
    await open()
    await click(q('[data-journal-option="zz-only"]'))
    await act(async () => {})
    expect(profile()?.journalRef).toBeUndefined()
    expect(useUiStore.getState().status).toEqual(expect.objectContaining({ key: 'journal.quotaLater' }))
  })

  it('等 v2 的时候又点了别的 / 项目换了：这次结果丢弃，不去改别人的绑定', async () => {
    let release: (r: Response) => void = () => {}
    route((url) =>
      url === '/api/journal-facts/index'
        ? online(url)
        : new Promise<Response>((resolve) => {
            release = resolve
          }),
    )
    await open()
    await click(q('[data-journal-option="zz-only"]'))
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    await act(async () => release(json(patch())))
    expect(profile()?.journalRef).toBeUndefined()
  })
})

describe('已绑定 v2-only 刊的项目', () => {
  function Sync() {
    useJournalV2Sync()
    return null
  }

  it('离线重开：取不到 v2 时按绑定里存着的判，不改文档、不报错', async () => {
    route(online)
    await open()
    await click(q('[data-journal-option="zz-only"]'))
    await act(async () => {})
    const bound = structuredClone(profile())
    useJournalV2Store.setState({ byJournal: {} })
    route(() => {
      throw new Error('offline')
    })
    await act(async () => root.render(<Sync />))
    await act(async () => {})
    expect(profile()).toEqual(bound)
    expect(useUiStore.getState().statusTone).not.toBe('error')
  })
})

