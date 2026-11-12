/**
 * 远程期刊目录的前端一侧：入口关着不发请求、索引同步走 setCatalog 同一道校验、倒退不采信、
 * 选中时按需取这一本（种子 / 已下载的不请求）、额度 / 离线 / 被拒各自的结果。夹具期刊（`fx-*`）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import { loadCatalog } from '@/lib/journals'
import {
  BUNDLED_ASSET_BASE,
  JOURNAL_CATALOG_CACHE_KEY,
  REMOTE_ASSET_BASE,
  useJournalCatalogStore,
} from '@/store/journalCatalogStore'
import { ensureJournalReady, syncJournalCatalog } from './journalCatalogSync'

// 入口常量已打开（ADR 0155 §11）；「入口关着」的用例靠这个开关模拟关闭
let uiOn = true
vi.mock('@/lib/journalUi', async (orig) => ({
  ...(await orig<typeof import('@/lib/journalUi')>()),
  journalCatalogUi: () => uiOn,
}))

type Json = Record<string, unknown>
const cat = (rev: number) => ({ ...structuredClone(vectors.catalog), catalog_rev: rev })

/** 骨架：没下载的刊只有名称与出版社 */
function skeleton(rev: number) {
  const full = cat(rev) as unknown as { publishers: Json; journals: Record<string, Json> }
  const pubs: Json = {}
  for (const [k, v] of Object.entries(full.publishers)) pubs[k] = { label: (v as Json).label }
  const jr: Json = {}
  for (const [k, v] of Object.entries(full.journals)) jr[k] = { label: v.label, publisher: v.publisher }
  return { schema: 1, catalog_rev: rev, publishers: pubs, journals: jr }
}

let calls: { url: string; body: Json }[] = []
let reply: (url: string, body: Json) => unknown

beforeEach(() => {
  localStorage.clear()
  uiOn = true
  calls = []
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body)) as Json
    calls.push({ url, body })
    const out = reply(url, body)
    if (out instanceof Error) throw out
    return new Response(JSON.stringify(out), { status: 200 })
  })
  useJournalCatalogStore.setState({
    catalog: loadCatalog(),
    assetBase: BUNDLED_ASSET_BASE,
    loaded: null,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
  useJournalCatalogStore.setState({ catalog: loadCatalog(), assetBase: BUNDLED_ASSET_BASE, loaded: null })
})

describe('syncJournalCatalog', () => {
  it('入口关着时一个请求都不发', async () => {
    uiOn = false
    reply = () => ({})
    await syncJournalCatalog()
    expect(calls).toEqual([])
  })

  it('后端给了新索引：走 setCatalog，资产根换成远程，记下已可用的刊', async () => {
    reply = () => ({ fetch: 'updated', rev: 5, catalog: skeleton(5), loaded: ['fx-beta'] })
    await syncJournalCatalog()
    const s = useJournalCatalogStore.getState()
    expect(s.catalog.catalog_rev).toBe(5)
    expect(s.assetBase).toBe(REMOTE_ASSET_BASE)
    expect(s.loaded?.has('fx-beta')).toBe(true)
    expect(calls[0].url).toBe('/api/journal-catalog/sync')
    expect(calls[0].body).toEqual({ have: 1, force: false, locale: 'zh-CN' }) // 只带版本号与界面语言（选入口先后用）
  })

  it('目录校验不过：整份弃用，目录不动', async () => {
    const bad = skeleton(5)
    ;(bad.journals as Json)['fx-beta'] = { label: '', publisher: 'nope' }
    reply = () => ({ fetch: 'updated', rev: 5, catalog: bad, loaded: [] })
    await syncJournalCatalog()
    expect(useJournalCatalogStore.getState().catalog.catalog_rev).toBe(1)
    expect(useJournalCatalogStore.getState().assetBase).toBe(BUNDLED_ASSET_BASE)
  })

  it('版本倒退不采信', async () => {
    useJournalCatalogStore.getState().setCatalog(skeleton(7), REMOTE_ASSET_BASE)
    reply = () => ({ fetch: 'updated', rev: 5, catalog: skeleton(5), loaded: [] })
    await syncJournalCatalog()
    expect(useJournalCatalogStore.getState().catalog.catalog_rev).toBe(7)
  })

  it('后端没响应：静默，目录不动', async () => {
    reply = () => new Error('down')
    await expect(syncJournalCatalog()).resolves.toBeUndefined()
    expect(useJournalCatalogStore.getState().catalog.catalog_rev).toBe(1)
  })

  it('远程目录的资产根随缓存一起存下来，重开后快照仍走本机后端', () => {
    useJournalCatalogStore.getState().setCatalog(skeleton(5), REMOTE_ASSET_BASE)
    expect(localStorage.getItem('tavotto.journalCatalog.assetBase')).toBe(REMOTE_ASSET_BASE)
    expect(localStorage.getItem(JOURNAL_CATALOG_CACHE_KEY)).toBeTruthy()
  })
})

describe('syncJournalCatalog 与 loaded', () => {
  it('目录本体没被采信（校验不过）时，不记 loaded：手里还是旧的，不能说某刊已可用', async () => {
    const bad = skeleton(5)
    ;(bad.journals as Json)['fx-beta'] = { label: '', publisher: 'nope' }
    reply = () => ({ fetch: 'updated', rev: 5, catalog: bad, loaded: ['fx-beta'] })
    await syncJournalCatalog()
    expect(useJournalCatalogStore.getState().loaded).toBeNull()
  })
})

describe('ensureJournalReady', () => {
  const remote = () => {
    useJournalCatalogStore.getState().setCatalog(skeleton(5), REMOTE_ASSET_BASE)
    useJournalCatalogStore.getState().setLoaded([])
  }
  const bundle = () => {
    const full = cat(5) as unknown as { publishers: Record<string, unknown>; journals: Record<string, Json> }
    const j = full.journals['fx-beta']
    return {
      journal_id: 'fx-beta',
      journal: j,
      publisher_id: j.publisher as string,
      publisher: full.publishers[j.publisher as string],
    }
  }

  it('没用远程目录（种子）时不发请求', async () => {
    reply = () => ({})
    expect(await ensureJournalReady('fx-beta')).toEqual({ state: 'ready', retryAfterS: null })
    expect(calls).toEqual([])
  })

  it('入口关着时不发请求', async () => {
    remote()
    uiOn = false
    reply = () => ({})
    expect((await ensureJournalReady('fx-beta')).state).toBe('ready')
    expect(calls).toEqual([])
  })

  it('远程目录里没下载的刊：只带刊 id，成功后并入目录且校验通过', async () => {
    remote()
    reply = () => ({ state: 'ready', bundle: bundle() })
    expect((await ensureJournalReady('fx-beta')).state).toBe('ready')
    expect(calls).toHaveLength(1)
    expect(calls[0]).toEqual({ url: '/api/journal-catalog/journal', body: { id: 'fx-beta', locale: 'zh-CN' } })
    const s = useJournalCatalogStore.getState()
    expect(Object.keys((s.catalog.journals['fx-beta'] as unknown as Json).rules as Json).length).toBeGreaterThan(0)
    expect(s.loaded?.has('fx-beta')).toBe(true)
  })

  it('已下载过的刊不再请求', async () => {
    remote()
    useJournalCatalogStore.getState().setLoaded(['fx-beta'])
    reply = () => ({})
    expect((await ensureJournalReady('fx-beta')).state).toBe('ready')
    expect(calls).toEqual([])
  })

  it('额度用完：回 quota 与等待秒数，目录不动', async () => {
    remote()
    reply = () => ({ state: 'quota', code: 'quota_day', retry_after_s: 3600, bundle: null })
    expect(await ensureJournalReady('fx-beta')).toEqual({ state: 'quota', retryAfterS: 3600 })
    expect((useJournalCatalogStore.getState().catalog.journals['fx-beta'] as unknown as Json).rules).toBeUndefined()
  })

  it('没网又没缓存 / 请求失败：offline', async () => {
    remote()
    reply = () => ({ state: 'offline', code: 'network', bundle: null })
    expect((await ensureJournalReady('fx-beta')).state).toBe('offline')
    reply = () => new Error('down')
    expect((await ensureJournalReady('fx-beta')).state).toBe('offline')
  })

  it('刊包并入后校验不过：整包弃用，回 rejected', async () => {
    remote()
    const b = bundle()
    ;(b.journal as Json).rules = { 'widths_mm.single': { value: 1, src: 'missing', quote: 'x' } }
    reply = () => ({ state: 'ready', bundle: b })
    expect((await ensureJournalReady('fx-beta')).state).toBe('rejected')
    expect((useJournalCatalogStore.getState().catalog.journals['fx-beta'] as unknown as Json).rules).toBeUndefined()
  })

  it('两次下载重叠：后完成的并入的是最新目录，先完成的那本不丢', async () => {
    remote()
    const full = cat(5) as unknown as { publishers: Record<string, unknown>; journals: Record<string, Json> }
    const bundleFor = (id: string) => ({
      journal_id: id,
      journal: full.journals[id],
      publisher_id: full.journals[id].publisher as string,
      publisher: full.publishers[full.journals[id].publisher as string],
    })
    const releases = new Map<string, () => void>()
    vi.stubGlobal('fetch', (_url: string, init: RequestInit) => {
      const id = (JSON.parse(String(init.body)) as Json).id as string
      return new Promise<Response>((resolve) => {
        releases.set(id, () => resolve(new Response(JSON.stringify({ state: 'ready', bundle: bundleFor(id) }), { status: 200 })))
      })
    })
    const a = ensureJournalReady('fx-zeta')
    const b = ensureJournalReady('fx-beta')
    await vi.waitFor(() => expect(releases.size).toBe(2))
    releases.get('fx-zeta')!()
    await a
    releases.get('fx-beta')!()
    await b
    const s = useJournalCatalogStore.getState()
    const hasRules = (id: string) => Object.keys(((s.catalog.journals[id] as unknown as Json).rules ?? {}) as Json).length > 0
    expect(hasRules('fx-zeta')).toBe(true)
    expect(hasRules('fx-beta')).toBe(true)
    expect(s.loaded?.has('fx-zeta') && s.loaded?.has('fx-beta')).toBe(true)
  })
})
