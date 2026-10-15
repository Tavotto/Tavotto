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
import { useProjectStore } from '@/store/projectStore'
import { emptyProject } from '@/types/document'
import { CanvasList } from './CanvasList'
import { WorkspaceList } from './WorkspaceList'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
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

  it('名词说明不再常驻在列表下方，在标题行 ⓘ 的 tooltip 里', async () => {
    seed(2)
    await mount(<WorkspaceList />)
    const hint = host.querySelector('[data-workspace-hint]')
    // 没有外壳时动作槽就地画出：ⓘ 是个带可达名的按钮，而不是一段常驻文字
    expect(hint?.tagName).toBe('BUTTON')
    expect(hint?.getAttribute('aria-label')).toBe('关于项目与排版')
    expect(host.textContent).not.toContain('项目是你的脚本文件夹')
  })

  it('一个都没有：一行说明 + 「打开文件夹…」文字动作', async () => {
    seed(0)
    await mount(<WorkspaceList />)
    const line = host.querySelector<HTMLElement>('[data-empty-line]')!
    expect(line.textContent).toContain('打开过的项目会出现在这里')
    expect(line.querySelector('button')!.textContent).toBe('打开文件夹…')
  })
})
