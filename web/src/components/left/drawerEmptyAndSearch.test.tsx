/**
 * 抽屉空态与搜索框（2026-10-16，UI 2.0 §09）：
 *  - 条目少于 6 个时不显示搜索框，有搜索词时保留（不把人困在筛空的列表里）；
 *  - 空态只有「一行 ink-3 灰字 + 至多一个文字动作」：没有图标底座、没有标题；
 *  - 项目抽屉的常驻名词说明进标题行 ⓘ 的 tooltip，不再占列表下方。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { SEARCH_MIN_ITEMS } from '@/components/ui/searchThreshold'
import { useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { useAssetBrowseStore } from '@/store/assetBrowseStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useProjectStore } from '@/store/projectStore'
import { emptyProject } from '@/types/document'
import { AssetBrowser } from './AssetBrowser'
import { CanvasList } from './CanvasList'
import { WorkspaceList } from './WorkspaceList'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

globalThis.fetch = vi.fn(async () => new Response('{}', { status: 404 })) as typeof fetch

let root: Root
let host: HTMLDivElement

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const search = () => host.querySelector<HTMLInputElement>('input')
const type = (el: HTMLInputElement, v: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })

describe('画布抽屉', () => {
  const seed = async (n: number) => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_drawer_empty')
    for (let i = 1; i < n; i++) useDocumentStore.getState().addCanvas()
  }
  beforeEach(() => {
    localStorage.clear()
    document.body.innerHTML = ''
  })

  it('阈值是 6：5 张没有搜索框，6 张有', async () => {
    expect(SEARCH_MIN_ITEMS).toBe(6)
    await seed(SEARCH_MIN_ITEMS - 1)
    await mount(<CanvasList />)
    expect(search()).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    await seed(SEARCH_MIN_ITEMS)
    await mount(<CanvasList />)
    expect(search()).not.toBeNull()
  })

  it('搜不到：一行灰字 + 「清除搜索」文字动作，点它清空且搜索框保留；没有图标底座', async () => {
    await seed(SEARCH_MIN_ITEMS)
    await mount(<CanvasList />)
    await type(search()!, 'zzz-no-such')
    const line = host.querySelector<HTMLElement>('[data-empty-line]')!
    expect(line).toBeTruthy()
    expect(host.querySelector('[data-empty-state]')).toBeNull()
    expect(line.querySelector('svg')).toBeNull()
    const act1 = line.querySelector<HTMLButtonElement>('button')!
    expect(act1.className).toContain('text-info-content')
    await act(async () => act1.click())
    expect(host.querySelector('[data-empty-line]')).toBeNull()
    expect(search()!.value).toBe('')
  })
})

describe('项目抽屉', () => {
  const entryOf = (path: string): RecentProject => ({
    path, name: path.split('/').pop()!, last_opened: 0, exists: true, current: false,
  })
  const project = { open: true, id: 'p', name: 'figs', figures_dir: '/x/figs', scripts: 1 } as ProjectStatus
  const seed = (n: number) =>
    useProjectStore.setState({
      project,
      phase: 'open',
      pinned: [],
      recent: Array.from({ length: n }, (_, i) => entryOf(`/w/p-${i}`)),
      opened: [],
    })

  it('少于 6 个没有搜索框；6 个有', async () => {
    seed(SEARCH_MIN_ITEMS - 1)
    await mount(<WorkspaceList />)
    expect(search()).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    seed(SEARCH_MIN_ITEMS)
    await mount(<WorkspaceList />)
    expect(search()).not.toBeNull()
  })

  it('阈值按实际可搜的行算：当前项目不算；置顶同时出现在最近里只算一次', async () => {
    // 当前项目 + 另外 5 个最近：只有 5 行可搜 → 隐藏（直接相加会算成 6）
    useProjectStore.setState({
      project,
      phase: 'open',
      pinned: [],
      recent: [{ ...entryOf('/x/figs'), current: true }, ...Array.from({ length: 5 }, (_, i) => entryOf(`/w/p-${i}`))],
      opened: [],
    })
    await mount(<WorkspaceList />)
    expect(search()).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    // 3 个置顶，且都同时在最近里：只有 3 行（加 3 个别的最近 = 6 行 → 显示；少 1 个 = 5 行 → 隐藏）
    const pins = ['/a/1', '/a/2', '/a/3'].map(entryOf)
    useProjectStore.setState({
      project,
      phase: 'open',
      pinned: pins,
      recent: [...pins, ...['/w/a', '/w/b'].map(entryOf)],
      opened: [],
    })
    await mount(<WorkspaceList />)
    expect(search()).toBeNull() // 3 + 2 = 5 行（直接相加会是 8）
    await act(async () => root.unmount())
    host.remove()
    useProjectStore.setState({ recent: [...pins, ...['/w/a', '/w/b', '/w/c'].map(entryOf)] })
    await mount(<WorkspaceList />)
    expect(search()).not.toBeNull() // 3 + 3 = 6 行
  })

  it('名词说明不再常驻在列表下方，在标题行 ⓘ 的 tooltip 里', async () => {
    seed(2)
    await mount(<WorkspaceList />)
    const hint = host.querySelector('[data-workspace-hint]')
    // 没有外壳时动作槽就地画出：ⓘ 是个带可达名的按钮，而不是一段常驻文字
    expect(hint?.tagName).toBe('BUTTON')
    expect(hint?.getAttribute('aria-label')).toBe('关于项目与排版')
    expect(host.textContent).not.toContain('项目是你的脚本文件夹')
  })

  it('一个都没有（起步屏）：只有一行说明，没有按钮——起步屏只有画布中央那一个动作', async () => {
    seed(0)
    await mount(<WorkspaceList />)
    const line = host.querySelector<HTMLElement>('[data-empty-line]')!
    expect(line.textContent).toContain('打开过的项目会出现在这里')
    expect(line.querySelector('button')).toBeNull()
  })
})

describe('素材抽屉', () => {
  it('项目里一张图都没有（起步屏）：只有一行说明，没有按钮', async () => {
    useAssetStore.setState({ panels: [], byId: {}, unsupported: [], loaded: true, loading: false, error: null })
    await mount(<AssetBrowser />)
    const line = host.querySelector<HTMLElement>('[data-asset-empty]')!
    expect(line).toBeTruthy()
    expect(line.textContent).toContain('项目里还没有图')
    expect(line.querySelector('button')).toBeNull()
  })
})

describe('素材抽屉的搜索框按「图 + 不支持的文件 + 脚本」合计算', () => {
  const scripts = (n: number) =>
    useScriptLibraryStore.setState({
      loaded: true,
      loading: false,
      error: null,
      view: {
        source: null,
        scripts: {},
        candidates: [],
        conflicts: {},
        all_scripts: Array.from({ length: n }, (_, i) => ({ script: `s${i}.py` })),
      } as never,
    })
  const panels = (n: number) => {
    const list = Array.from({ length: n }, (_, i) => ({ id: `F${i}.pdf`, name: `F${i}`, kind: 'pdf', folder: '' }))
    useAssetStore.setState({
      panels: list as never,
      byId: Object.fromEntries(list.map((p) => [p.id, p])) as never,
      unsupported: [],
      loaded: true,
      loading: false,
      error: null,
    })
  }
  // 脚本区收起：这里量的是搜索框，不是脚本行（有搜索词时才强制展开）
  beforeEach(() => useAssetBrowseStore.getState().setScriptsOpen(false))
  const searchBox = () => host.querySelector('input[aria-label="搜索图"]')

  it('0 张图 + 10 个脚本：搜索框可见（它过滤下面的脚本区）', async () => {
    panels(0)
    scripts(10)
    await mount(<AssetBrowser />)
    expect(searchBox()).not.toBeNull()
  })

  it('加载中不显示；失败时显示；加载完成后按总数定', async () => {
    panels(2)
    useScriptLibraryStore.setState({ loaded: false, loading: true, error: null, view: null })
    const load = vi.spyOn(useScriptLibraryStore.getState(), 'load').mockImplementation(async () => null as never)
    await mount(<AssetBrowser />)
    expect(searchBox(), '加载中').toBeNull()
    await act(async () => useScriptLibraryStore.setState({ loading: false, error: 'boom' }))
    expect(searchBox(), '失败').not.toBeNull()
    await act(async () => {
      scripts(2)
    })
    expect(searchBox(), '加载完成、合计 4').toBeNull()
    await act(async () => {
      scripts(10)
    })
    expect(searchBox(), '加载完成、合计 12').not.toBeNull()
    load.mockRestore()
  })

  it('脚本清单加载中：已知的图有 8 张直接显示；只有 2 张仍等待', async () => {
    useScriptLibraryStore.setState({ loaded: false, loading: true, error: null, view: null })
    const load = vi.spyOn(useScriptLibraryStore.getState(), 'load').mockImplementation(async () => null as never)
    panels(8)
    await mount(<AssetBrowser />)
    expect(searchBox(), '8 张图、清单加载中').not.toBeNull()
    await act(async () => root.unmount())
    host.remove()
    panels(2)
    await mount(<AssetBrowser />)
    expect(searchBox(), '2 张图、清单加载中').toBeNull()
    load.mockRestore()
  })

  it('项目 A 的清单取失败后切到 B：load 会再被调用一次（effect 依赖项目代际）', async () => {
    panels(2)
    useScriptLibraryStore.setState({ loaded: false, loading: false, error: null, view: null })
    const load = vi.spyOn(useScriptLibraryStore.getState(), 'load').mockImplementation(async () => {
      useScriptLibraryStore.setState({ error: 'boom' }) // A：失败，loaded 仍是 false
      return null as never
    })
    await mount(<AssetBrowser />)
    await act(async () => {})
    expect(load).toHaveBeenCalledTimes(1)
    // 切到 B：store 清空（loaded 前后都是 false）
    await act(async () => useScriptLibraryStore.getState().clear())
    await act(async () => {})
    expect(load).toHaveBeenCalledTimes(2)
    load.mockRestore()
  })

  it('整个加载过程中搜索框不会先出现再消失（小项目：从头到尾都不出现）', async () => {
    panels(2)
    useScriptLibraryStore.setState({ loaded: false, loading: false, error: null, view: null })
    const seen: boolean[] = []
    const record = () => seen.push(searchBox() !== null)
    // 触发加载的 effect 之前、加载中、加载完成：每一步都记一次
    const load = vi.spyOn(useScriptLibraryStore.getState(), 'load').mockImplementation(async () => {
      useScriptLibraryStore.setState({ loading: true })
      return null as never
    })
    await mount(<AssetBrowser />)
    record()
    await act(async () => {})
    record()
    await act(async () => scripts(2))
    record()
    expect(seen).toEqual([false, false, false])
    // 通则：序列里不允许出现 true → false
    for (let i = 1; i < seen.length; i++) expect(seen[i - 1] && !seen[i]).toBe(false)
    load.mockRestore()
  })

  it('折叠脚本区后换项目（清单被清空）：抽屉自己把清单取回来，2 张图 + 10 个脚本时搜索框可见', async () => {
    panels(2)
    // 换项目：清单清空、脚本区折叠（ScriptLibrary 不挂载，不会替它取）
    useScriptLibraryStore.setState({ loaded: false, loading: false, view: null })
    const fetchMock = vi.fn(async (url: string) =>
      String(url).includes('/api/scripts') || String(url).includes('registry')
        ? new Response(
            JSON.stringify({
              source: null,
              scripts: {},
              candidates: [],
              conflicts: {},
              all_scripts: Array.from({ length: 10 }, (_, i) => ({ script: `s${i}.py`, registered: false, static_stems: [], entry_candidates: [], reason: 'ok', can_probe: true })),
            }),
            { status: 200 },
          )
        : new Response('{}', { status: 404 }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const load = vi.spyOn(useScriptLibraryStore.getState(), 'load').mockImplementation(async () => {
      scripts(10)
      return null as never
    })
    await mount(<AssetBrowser />)
    await act(async () => {})
    expect(load).toHaveBeenCalled()
    expect(searchBox()).not.toBeNull()
    load.mockRestore()
  })

  it('2 张图 + 2 个脚本：合计 4 个，隐藏', async () => {
    panels(2)
    scripts(2)
    await mount(<AssetBrowser />)
    expect(searchBox()).toBeNull()
  })
})
