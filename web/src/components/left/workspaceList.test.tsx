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
import { formatMessage } from '@/i18n'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ProjectSwitcher } from '@/components/ProjectSwitcher'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { WorkspaceList } from './WorkspaceList'

/** 桌面能力由 `lib/desktop` 决定；这里按用例切「桌面 / 浏览器」，并记下 reveal 了哪条路径 */
const desktop = vi.hoisted(() => ({ can: false, ok: true, revealed: [] as string[] }))
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  canRevealInFileManager: () => desktop.can,
  fileManagerKind: () => 'finder',
  revealProjectFolder: async (path: string) => {
    desktop.revealed.push(path)
    return desktop.ok
  },
}))

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

/**
 * 收藏操作按顺序记下来；mock 自己维护一份「服务端收藏」，按操作执行后回整张新列表
 * （与 `config.edit_pinned` 同语义：按路径认对象、执行时才查位置）。
 */
let ops: Record<string, unknown>[] = []
let failPinned = false
let server: string[] = []
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  if (url.includes('/api/projects/pinned') && init?.method === 'POST') {
    if (failPinned) {
      return new Response(JSON.stringify({ error: 'x', code: 'bad_request', params: {} }), { status: 400 })
    }
    const op = JSON.parse(String(init.body)) as { op: string; path: string; delta?: number; to_path?: string }
    ops.push(op)
    if (op.op === 'add' && !server.includes(op.path)) server = [...server, op.path]
    if (op.op === 'remove') server = server.filter((p) => p !== op.path)
    if (op.op === 'move' && server.includes(op.path)) {
      const from = server.indexOf(op.path)
      const to =
        op.to_path !== undefined
          ? server.indexOf(op.to_path)
          : Math.max(0, Math.min(server.length - 1, from + (op.delta ?? 0)))
      if (to >= 0) {
        const next = server.filter((p) => p !== op.path)
        next.splice(to, 0, op.path)
        server = next
      }
    }
    return new Response(JSON.stringify({ pinned: server.map((p) => entryOf(p)) }), { status: 200 })
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
  desktop.can = false
  desktop.ok = true
  desktop.revealed = []
  ops = []
  failPinned = false
  server = ['/a/Supplementary', '/b/Rebuttal']
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

  it('打开抽屉就取一次最新列表（别的标签页改过收藏不会有事件）', async () => {
    fetchMock.mockClear()
    await mount()
    const urls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(urls.filter((u) => u.includes('/api/projects/recent'))).toHaveLength(1)
  })

  it('点最近区的行就打开那个项目', async () => {
    await mount()
    const btn = section('recent')!.querySelector<HTMLButtonElement>('button[title="/work/p-3"]')!
    await act(async () => btn.click())
    expect(open).toHaveBeenCalledWith('/work/p-3', false)
  })

  it('收藏开关按路径发一个操作，界面以回来的那份为准', async () => {
    await mount()
    await act(async () => pinButton('/work/p-0').click())
    expect(ops).toEqual([{ op: 'add', path: '/work/p-0' }])
    expect(rowNames('pinned')).toEqual(['/a/Supplementary', '/b/Rebuttal', '/work/p-0'])
    expect(rowNames('recent')).not.toContain('/work/p-0')

    await act(async () => pinButton('/a/Supplementary').click())
    expect(ops[1]).toEqual({ op: 'remove', path: '/a/Supplementary' })
    expect(pinButton('/b/Rebuttal').getAttribute('aria-pressed')).toBe('true')
  })

  it('操作失败时收藏不动、状态栏说一句', async () => {
    // 只让收藏操作失败：抽屉挂载时还会发一次刷新，用 Once 会被它先吃掉
    failPinned = true
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

describe('当前项目按本标签页的项目认', () => {
  it('列表里的 current 还是上一次刷新的（旧项目），行的「当前」跟着 store 走', async () => {
    useProjectStore.setState({
      pinned: [entryOf('/a/Supplementary', { current: true }), entryOf(CURRENT)],
    })
    await mount()
    const btn = (path: string) =>
      section('pinned')!.querySelector<HTMLButtonElement>(`button[title="${path}"]`)!
    expect(btn(CURRENT).disabled).toBe(true)
    expect(btn(CURRENT).getAttribute('aria-current')).toBe('true')
    expect(btn('/a/Supplementary').disabled).toBe(false)
    expect(btn('/a/Supplementary').hasAttribute('aria-current')).toBe(false)
  })
})

describe('收藏拖动', () => {
  const rows = () => [...section('pinned')!.querySelectorAll<HTMLElement>('[data-workspace-row]')]
  const fire = (el: Element, type: string) => {
    const e = new Event(type, { bubbles: true, cancelable: true })
    act(() => {
      el.dispatchEvent(e)
    })
    return e
  }

  it('内部拖动：落在另一条上按路径发 move', async () => {
    await mount()
    const [a, b] = rows()
    fire(a, 'dragstart')
    expect(fire(b, 'dragover').defaultPrevented).toBe(true)
    await act(async () => {
      b.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }))
    })
    expect(ops).toEqual([{ op: 'move', path: '/a/Supplementary', to_path: '/b/Rebuttal' }])
  })

  it('拖动取消后，外部拖进来的东西不接、不挪', async () => {
    await mount()
    const [a, b] = rows()
    fire(a, 'dragstart')
    fire(a, 'dragend') // 取消 / 松在列表外：没有 drop
    expect(fire(b, 'dragover').defaultPrevented).toBe(false)
    await act(async () => {
      b.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }))
    })
    expect(ops).toEqual([])
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
  it('movePinned 按路径发 move（相对 / 拖到某一条），不发下标', async () => {
    await useProjectStore.getState().movePinned('/a/Supplementary', { delta: 1 })
    await useProjectStore.getState().movePinned('/a/Supplementary', { toPath: '/b/Rebuttal' })
    expect(ops).toEqual([
      { op: 'move', path: '/a/Supplementary', delta: 1 },
      { op: 'move', path: '/a/Supplementary', to_path: '/b/Rebuttal' },
    ])
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual([
      '/a/Supplementary',
      '/b/Rebuttal',
    ])
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

describe('右键「在 Finder 中打开」', () => {
  const row = (path: string) =>
    host.querySelector<HTMLElement>(`[data-workspace-row][data-project-path="${path}"]`)!
  const rightClick = (el: Element) => {
    const e = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 60 })
    act(() => {
      el.dispatchEvent(e)
    })
    return e
  }
  const menu = () => document.querySelector<HTMLElement>('[role="menu"]')
  const reveal = () => document.querySelector<HTMLElement>('[data-workspace-reveal]')

  it('桌面：右键行开出菜单，选它就按这一行的路径打开', async () => {
    desktop.can = true
    await mount()
    expect(rightClick(row('/work/p-3')).defaultPrevented).toBe(true)
    expect(menu()).not.toBeNull()
    expect(reveal()?.textContent).toBe('在 Finder 中打开')
    await act(async () => reveal()!.click())
    expect(desktop.revealed).toEqual(['/work/p-3'])
    expect(menu()).toBeNull()
  })

  it('桌面：右键当前项目的卡片也有这一项，打开的是当前项目', async () => {
    desktop.can = true
    await mount()
    rightClick(host.querySelector('[data-workspace-current]')!)
    await act(async () => reveal()!.click())
    expect(desktop.revealed).toEqual([CURRENT])
  })

  it('失败不静默：状态栏报错并带上完整路径', async () => {
    desktop.can = true
    desktop.ok = false
    await mount()
    rightClick(row('/b/Rebuttal'))
    await act(async () => reveal()!.click())
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(formatMessage(useUiStore.getState().status)).toContain('/b/Rebuttal')
  })

  it('已不存在的项目不给这一项', async () => {
    desktop.can = true
    useProjectStore.setState({ recent: [entryOf('/gone/x', { exists: false })] })
    await mount()
    rightClick(row('/gone/x'))
    expect(menu()).not.toBeNull()
    expect(reveal()).toBeNull()
  })

  it('浏览器模式不摆这一项（服务器可能不在这台机器上）', async () => {
    await mount()
    rightClick(row('/work/p-3'))
    expect(menu()).not.toBeNull()
    expect(reveal()).toBeNull()
  })
})
