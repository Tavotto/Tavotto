/**
 * 素材清单的并发治理（Prompt 06 §四）。
 *
 * `load()` 今天有七个触发点，其中「统一刷新连发 registry.changed +
 * assets.changed」让**同一批里被调好几次**成为常态。这一份钉四件事：
 * 合并、旧响应不覆盖新响应、换项目的响应作废、后台失败不清空已有数据。
 *
 * 全部用**手动 gate 的假 fetch**，没有一处 sleep——「谁先返回」正是被测的
 * 那一维，交给时序去决定的话，用例本身就成了它要防的那个缺陷。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn(),
}))
vi.mock('@/lib/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/session')>()),
  currentProjectId: vi.fn(() => project),
}))

import { fetchPanels, type PanelInfo, type PanelsResponse } from '@/lib/api'
import { resetAssetLoadBookkeeping, useAssetStore } from './assetStore'

const mockFetch = vi.mocked(fetchPanels)

/** 本标签页当前认领的项目；用例直接改它来模拟切项目 */
let project: string | null = 'p1'

const panel = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...over,
})

const resp = (ids: string[], dir = '/figs'): PanelsResponse => ({
  figures_dir: dir,
  panels: ids.map((id) => panel(id)),
})

/** 一次挂起的 fetch：`settle()` 之前它不返回 */
function gate(value: PanelsResponse | Error) {
  let release!: () => void
  const promise = new Promise<PanelsResponse>((resolve, reject) => {
    release = () => (value instanceof Error ? reject(value) : resolve(value))
  })
  return { promise, settle: release }
}

/** 把在途微任务放干净（假 fetch 全是立刻 resolve 的 Promise） */
const tick = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}

const s = () => useAssetStore.getState()

beforeEach(() => {
  project = 'p1'
  mockFetch.mockReset()
  resetAssetLoadBookkeeping()
  useAssetStore.setState({
    panels: [],
    byId: {},
    figuresDir: '',
    loading: false,
    loaded: false,
    error: null,
  })
})

describe('合并', () => {
  it('同一批事件里的多次 load 只发一个**在途**请求，且都拿到权威数据', async () => {
    const g = gate(resp(['a.pdf']))
    mockFetch.mockReturnValue(g.promise)

    const first = s().load()
    const second = s().load()
    expect(mockFetch).toHaveBeenCalledTimes(1)

    g.settle()
    const [r1, r2] = await Promise.all([first, second])
    expect(r1?.panels.map((p) => p.id)).toEqual(['a.pdf'])
    expect(r2?.panels.map((p) => p.id)).toEqual(['a.pdf'])
    expect(s().panels.map((p) => p.id)).toEqual(['a.pdf'])
    expect(s().loaded).toBe(true)
  })

  it('在途期间来的调用会在本次落地之后补问一遍（合并的是请求，不是问题）', async () => {
    // 服务端读完目录的那一刻 b.pdf 还不存在；它是第二条事件带来的。
    // 把在途那一份原样还给第二个调用方 = 让它错过自己的那条事件。
    const g = gate(resp(['a.pdf']))
    mockFetch.mockReturnValueOnce(g.promise).mockResolvedValueOnce(resp(['a.pdf', 'b.pdf']))

    const first = s().load()
    const second = s().load()
    expect(mockFetch).toHaveBeenCalledTimes(1)

    g.settle()
    expect((await first)?.panels.map((p) => p.id)).toEqual(['a.pdf'])

    expect((await second)?.panels.map((p) => p.id)).toEqual(['a.pdf', 'b.pdf'])
    expect(mockFetch).toHaveBeenCalledTimes(2)
    expect(s().panels.map((p) => p.id)).toEqual(['a.pdf', 'b.pdf'])
  })

  it('在途期间来几次都只补问一次（补问本身也要合并）', async () => {
    const g = gate(resp(['a.pdf']))
    mockFetch.mockReturnValueOnce(g.promise).mockResolvedValue(resp(['a.pdf', 'b.pdf']))

    const first = s().load()
    const rest = [s().load(), s().load(), s().load()]
    g.settle()
    await Promise.all([first, ...rest])

    // 1 次在途 + 1 次补问；三个后来者共用同一次补问
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('补问期间换了项目就不补：那份清单属于别人的图库', async () => {
    const g = gate(resp(['p1.pdf']))
    mockFetch.mockReturnValueOnce(g.promise).mockResolvedValue(resp(['p2.pdf']))

    const first = s().load()
    const second = s().load()
    project = 'p2' // 用户在响应落地之前切了图库
    g.settle()

    expect(await first).toBeNull()
    expect(await second).toBeNull()
    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(s().panels).toEqual([])
  })

  it('上一次结束之后来的新事件会另发一次（合并不是吞掉）', async () => {
    mockFetch.mockResolvedValueOnce(resp(['a.pdf'])).mockResolvedValueOnce(resp(['a.pdf', 'b.pdf']))
    await s().load()
    await s().load()
    expect(mockFetch).toHaveBeenCalledTimes(2)
    expect(s().panels.map((p) => p.id)).toEqual(['a.pdf', 'b.pdf'])
  })

  it('force 不被在途请求吞掉：按了刷新就一定有一次新的请求', async () => {
    const slow = gate(resp(['old.pdf']))
    mockFetch.mockReturnValueOnce(slow.promise).mockResolvedValueOnce(resp(['new.pdf']))

    const background = s().load()
    const manual = s().load({ force: true })
    expect(mockFetch).toHaveBeenCalledTimes(2)

    await manual
    expect(s().panels.map((p) => p.id)).toEqual(['new.pdf'])

    // 慢的那次后到，但它是**旧**的一次：不许把手动刷新的结果盖回去
    slow.settle()
    expect(await background).toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['new.pdf'])
  })
})

describe('旧响应不覆盖新响应', () => {
  it('先发的请求后返回时被丢弃', async () => {
    const slow = gate(resp(['old.pdf']))
    const fast = gate(resp(['new.pdf']))
    mockFetch.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)

    const a = s().load()
    const b = s().load({ force: true })

    fast.settle()
    expect(await b).not.toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['new.pdf'])

    slow.settle()
    expect(await a).toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['new.pdf'])
    expect(s().figuresDir).toBe('/figs')
  })

  it('旧请求失败也不许把新数据的 error 位置上', async () => {
    const slow = gate(new Error('旧请求超时'))
    const fast = gate(resp(['new.pdf']))
    mockFetch.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)

    const a = s().load()
    const b = s().load({ force: true })
    fast.settle()
    await b
    slow.settle()
    await a

    expect(s().error).toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['new.pdf'])
  })
})

describe('项目隔离', () => {
  it('切项目之后旧项目的响应被丢弃', async () => {
    const g = gate(resp(['p1.pdf']))
    mockFetch.mockReturnValue(g.promise)

    const pending = s().load()
    project = 'p2' // 用户切了图库
    g.settle()

    expect(await pending).toBeNull()
    expect(s().panels).toEqual([])
    expect(s().loaded).toBe(false)
  })

  it('切项目之后的合并不复用旧项目的在途请求', async () => {
    const g1 = gate(resp(['p1.pdf']))
    const g2 = gate(resp(['p2.pdf']))
    mockFetch.mockReturnValueOnce(g1.promise).mockReturnValueOnce(g2.promise)

    const first = s().load()
    project = 'p2'
    const second = s().load()
    expect(mockFetch).toHaveBeenCalledTimes(2)

    g2.settle()
    await second
    g1.settle()
    expect(await first).toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['p2.pdf'])
  })

  it('换项目之后，旧项目那次失败的请求不许把错误记到新项目头上', async () => {
    const g = gate(new Error('旧项目的后端挂了'))
    mockFetch.mockReturnValue(g.promise)

    const pending = s().load()
    project = 'p2'
    g.settle()

    expect(await pending).toBeNull()
    expect(s().error).toBeNull()
  })

  it('跟随后端默认项目（null）与某个具体 id 是两个取值，不合并', async () => {
    project = null
    const g1 = gate(resp(['default.pdf']))
    const g2 = gate(resp(['claimed.pdf']))
    mockFetch.mockReturnValueOnce(g1.promise).mockReturnValueOnce(g2.promise)

    const first = s().load()
    project = 'p9'
    const second = s().load()
    expect(mockFetch).toHaveBeenCalledTimes(2)

    g2.settle()
    await second
    g1.settle()
    expect(await first).toBeNull()
    expect(s().panels.map((p) => p.id)).toEqual(['claimed.pdf'])
  })
})

describe('失败的处置', () => {
  it('后台刷新失败：panels / byId 一个都不清，只多一条非阻塞错误', async () => {
    mockFetch.mockResolvedValueOnce(resp(['a.pdf']))
    await s().load()
    expect(s().panels).toHaveLength(1)

    mockFetch.mockRejectedValueOnce(new Error('后端不可达'))
    expect(await s().load()).toBeNull()

    expect(s().panels.map((p) => p.id)).toEqual(['a.pdf'])
    expect(s().byId['a.pdf']).toBeDefined()
    expect(s().loaded).toBe(true)
    expect(s().error).toBe('后端不可达')
    expect(s().loading).toBe(false)
  })

  it('下一次事件能把失败恢复掉', async () => {
    mockFetch.mockRejectedValueOnce(new Error('抖了一下'))
    await s().load()
    expect(s().error).toBe('抖了一下')

    mockFetch.mockResolvedValueOnce(resp(['a.pdf']))
    await s().load()
    expect(s().error).toBeNull()
    expect(s().loaded).toBe(true)
  })

  it('首次加载失败仍是「没加载过」——界面据此显示 EmptyState 而不是空列表', async () => {
    mockFetch.mockRejectedValueOnce(new Error('读不到'))
    expect(await s().load()).toBeNull()
    expect(s().loaded).toBe(false)
    expect(s().error).toBe('读不到')
  })
})

describe('loading', () => {
  it('还有更新的请求在途时不收 loading（转圈不闪）', async () => {
    const slow = gate(resp(['old.pdf']))
    const fast = gate(resp(['new.pdf']))
    mockFetch.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)

    const a = s().load()
    const b = s().load({ force: true })
    slow.settle()
    await a
    expect(s().loading).toBe(true)

    fast.settle()
    await b
    await tick()
    expect(s().loading).toBe(false)
  })
})

describe('refresh()', () => {
  it('与 load() 走同一条合并路径，返回本次生效的响应', async () => {
    const g = gate(resp(['a.pdf']))
    mockFetch.mockReturnValue(g.promise)
    const viaLoad = s().load()
    const viaRefresh = s().refresh()
    expect(mockFetch).toHaveBeenCalledTimes(1)
    g.settle()
    expect(await viaRefresh).toBe(await viaLoad)
  })
})
