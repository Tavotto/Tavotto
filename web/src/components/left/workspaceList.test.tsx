/**
 * 左栏「工作区」抽屉：当前项目 + 收藏 + 最近，切项目一次点击。
 *
 * 守的几件事：
 *  - 三个区各放各的：最近区不重复收藏里的、也不重复当前项目；最近列表**不截断**
 *    （顶栏旧菜单只列六条，抽屉就是为了看全）；
 *  - 收藏开关发的是**整张列表**（PUT /api/projects/pinned），界面以回来的那份为准；
 *  - 排序：上移 / 下移发重排后的整张列表；筛选中不能拖；
 *  - 顶栏项目名开 / 关的就是这个抽屉，不再有第二份列表。
 *
 * 定位一律认 `data-workspace-*` / `data-project-switcher`，不认文案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ProjectSwitcher } from '@/components/ProjectSwitcher'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { WorkspaceList } from './WorkspaceList'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const entryOf = (path: string, extra: Partial<RecentProject> = {}): RecentProject => ({
  path,
  name: path.split('/').pop()!,
  last_opened: 0,
  exists: true,
  current: false,
  ...extra,
})

const CURRENT = '/papers/nature/figs'
const current: ProjectStatus = {
  open: true,
  id: 'p-cur',
  name: 'figs',
  figures_dir: CURRENT,
  scripts: 12,
} as ProjectStatus

/** PUT 的请求体按顺序记下来；回包 = 按请求体里的路径现造的条目 */
let puts: string[][] = []
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  if (url.includes('/api/projects/pinned') && init?.method === 'PUT') {
    const { paths } = JSON.parse(String(init.body)) as { paths: string[] }
    puts.push(paths)
    return new Response(JSON.stringify({ pinned: paths.map((p) => entryOf(p)) }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
})
globalThis.fetch = fetchMock as unknown as typeof fetch

let root: Root
let host: HTMLDivElement
const open = vi.fn(async (_path: string, _create?: boolean): Promise<ProjectStatus> => current)

async function mount(node: React.ReactNode = <WorkspaceList />) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

const section = (id: string) => host.querySelector(`[data-workspace-section="${id}"]`)
const rowNames = (id: string) =>
  [...(section(id)?.querySelectorAll('[data-workspace-row] button[title]') ?? [])]
    .map((b) => b.getAttribute('title'))
const pinButton = (path: string) =>
  [...host.querySelectorAll<HTMLElement>('[data-workspace-row]')]
    .find((li) => li.querySelector(`button[title="${path}"]`))!
    .querySelector<HTMLButtonElement>('[data-workspace-pin]')!

beforeEach(() => {
  puts = []
  open.mockClear()
  useProjectStore.setState({
    project: current,
    phase: 'open',
    open,
    pinned: [entryOf('/a/Supplementary'), entryOf('/b/Rebuttal')],
    recent: [
      entryOf(CURRENT, { current: true }),
      entryOf('/b/Rebuttal'),
      ...Array.from({ length: 12 }, (_, i) => entryOf(`/work/p-${i}`)),
    ],
    opened: [],
  })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('WorkspaceList', () => {
  it('三个区各放各的，最近区不截断、不重复收藏与当前项目', async () => {
    await mount()
    expect(section('current')?.textContent).toContain('figs')
    expect(rowNames('pinned')).toEqual(['/a/Supplementary', '/b/Rebuttal'])
    const recent = rowNames('recent')
    expect(recent).toHaveLength(12)
    expect(recent).not.toContain('/b/Rebuttal')
    expect(recent).not.toContain(CURRENT)
  })

  it('点最近区的行就打开那个项目', async () => {
    await mount()
    const btn = section('recent')!.querySelector<HTMLButtonElement>('button[title="/work/p-3"]')!
    await act(async () => btn.click())
    expect(open).toHaveBeenCalledWith('/work/p-3', false)
  })

  it('收藏开关发整张列表，界面以回来的那份为准', async () => {
    await mount()
    await act(async () => pinButton('/work/p-0').click())
    expect(puts).toEqual([['/a/Supplementary', '/b/Rebuttal', '/work/p-0']])
    expect(rowNames('pinned')).toEqual(['/a/Supplementary', '/b/Rebuttal', '/work/p-0'])
    expect(rowNames('recent')).not.toContain('/work/p-0')

    await act(async () => pinButton('/a/Supplementary').click())
    expect(puts[1]).toEqual(['/b/Rebuttal', '/work/p-0'])
    expect(pinButton('/b/Rebuttal').getAttribute('aria-pressed')).toBe('true')
  })

  it('PUT 失败时收藏不动、状态栏说一句', async () => {
    fetchMock.mockImplementationOnce(async () =>
      new Response(JSON.stringify({ error: 'x', code: 'bad_request', params: {} }), { status: 400 }),
    )
    await mount()
    const before = useProjectStore.getState().pinned
    await act(async () => pinButton('/work/p-1').click())
    expect(useProjectStore.getState().pinned).toBe(before)
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('筛选：只留匹配的行；筛选中收藏行不能拖', async () => {
    await mount()
    const input = host.querySelector<HTMLInputElement>('[data-workspace-list] input')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    const draggable = () =>
      section('pinned')!.querySelector('[data-workspace-row]')!.getAttribute('draggable')
    expect(draggable()).toBe('true')
    act(() => {
      setter.call(input, 'Supp')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(rowNames('pinned')).toEqual(['/a/Supplementary'])
    expect(section('recent')).toBeNull()
    expect(draggable()).toBe('false')
  })

  it('失效的项目打不开；多于一个时给「全部移除」', async () => {
    useProjectStore.setState({
      recent: [entryOf('/gone/x', { exists: false }), entryOf('/gone/y', { exists: false })],
    })
    await mount()
    const btn = section('recent')!.querySelector<HTMLButtonElement>('button[title="/gone/x"]')!
    expect(btn.disabled).toBe(true)
    expect(section('recent')!.querySelectorAll('li:not([data-workspace-row]) button')).toHaveLength(1)
  })
})

describe('切换中', () => {
  it('有一次切换在进行时，所有「打开」入口都置灰，不只是正在打开的那一行', async () => {
    useProjectStore.setState({ switching: true })
    await mount()
    const opens = [...host.querySelectorAll<HTMLButtonElement>('[data-workspace-row] button[title]')]
    expect(opens.length).toBeGreaterThan(3)
    expect(opens.every((b) => b.disabled)).toBe(true)
    const footer = [...host.querySelectorAll<HTMLButtonElement>('[data-workspace-list] > div:last-of-type button')]
    expect(footer).toHaveLength(2)
    expect(footer.every((b) => b.disabled)).toBe(true)
    useProjectStore.setState({ switching: false })
  })
})

describe('projectStore 收藏排序', () => {
  it('movePinned 发重排后的整张列表；越界什么都不发', async () => {
    await useProjectStore.getState().movePinned(0, 1)
    expect(puts).toEqual([['/b/Rebuttal', '/a/Supplementary']])
    await useProjectStore.getState().movePinned(0, 5)
    expect(puts).toHaveLength(1)
  })
})

describe('顶栏项目名', () => {
  it('开 / 关的就是工作区抽屉', async () => {
    useUiStore.setState({ leftOpen: false, leftTab: 'assets' })
    await mount(<ProjectSwitcher />)
    const btn = host.querySelector<HTMLButtonElement>('[data-project-switcher]')!
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    await act(async () => btn.click())
    expect(useUiStore.getState().leftTab).toBe('workspace')
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    await act(async () => btn.click())
    expect(useUiStore.getState().leftOpen).toBe(false)
  })
})
