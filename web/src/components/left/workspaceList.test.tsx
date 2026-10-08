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
/** 非 null 时每个收藏请求都挂起，直到用例按顺序放行（模拟请求还在路上） */
let held: (() => void)[] | null = null
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  if (url.includes('/api/projects/pinned') && init?.method === 'POST') {
    if (held) await new Promise<void>((r) => held!.push(r))
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
  held = null
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
    // aria-disabled 而不是 disabled（焦点还要落得进来，见键位契约那组）；点了不打开
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    await act(async () => btn.click())
    expect(open).not.toHaveBeenCalled()
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
      section('pinned')!.querySelector<HTMLButtonElement>(`button[title="${path}"]`)
    // 当前项目只在顶上的卡里（审计 §10.3：此前收藏了的当前项目一屏画两次选中）
    expect(btn(CURRENT), '当前项目不在收藏区重复').toBeNull()
    expect(section('current')!.querySelector('[data-workspace-current]')!.getAttribute('data-card')).toBe('subtle')
    expect(btn('/a/Supplementary')!.disabled).toBe(false)
    expect(btn('/a/Supplementary')!.hasAttribute('aria-current')).toBe(false)
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

describe('当前项目被收藏时，排序只认可见的收藏行（Codex #832）', () => {
  const X = '/x/Thesis'
  const Y = '/y/Poster'
  const row = (path: string) =>
    host.querySelector<HTMLElement>(`[data-workspace-row][data-project-path="${path}"]`)!
  const openMenu = (path: string) =>
    act(() => {
      row(path).dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 60 }))
    })
  const moveItem = (dir: 'up' | 'down') => document.querySelector<HTMLElement>(`[data-project-move="${dir}"]`)!
  const closeMenu = () =>
    act(() => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })

  beforeEach(() => {
    server = [X, CURRENT, Y]
    useProjectStore.setState({ pinned: server.map((p) => entryOf(p)) })
  })

  it('上移 Y 越过藏起来的当前项目，一下就排到 X 前面', async () => {
    await mount()
    expect(rowNames('pinned')).toEqual([X, Y])
    openMenu(Y)
    await act(async () => moveItem('up').click())
    expect(ops).toEqual([{ op: 'move', path: Y, to_path: X }])
    expect(rowNames('pinned')).toEqual([Y, X])
  })

  it('⌥↓ 在 X 上同理：与可见的下一行换位', async () => {
    await mount()
    await act(async () => {
      row(X).querySelector<HTMLButtonElement>('[data-workspace-open]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true, cancelable: true }),
      )
    })
    expect(ops).toEqual([{ op: 'move', path: X, to_path: Y }])
    expect(rowNames('pinned')).toEqual([Y, X])
  })

  // 藏起来的当前项目分别压在两端：按整张收藏算的话，可见的首行还能「上移」、末行还能「下移」
  it.each([
    ['当前项目在最前', [CURRENT, X, Y]],
    ['当前项目在最后', [X, Y, CURRENT]],
  ])('可见的首行上移、末行下移停用，⌥↓ 也不发（%s）', async (_label, order) => {
    server = order
    useProjectStore.setState({ pinned: server.map((p) => entryOf(p)) })
    await mount()
    openMenu(X)
    expect(moveItem('up').hasAttribute('data-disabled')).toBe(true)
    expect(moveItem('down').hasAttribute('data-disabled')).toBe(false)
    closeMenu()
    openMenu(Y)
    expect(moveItem('up').hasAttribute('data-disabled')).toBe(false)
    expect(moveItem('down').hasAttribute('data-disabled')).toBe(true)
    closeMenu()
    await act(async () => {
      row(Y).querySelector<HTMLButtonElement>('[data-workspace-open]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true, cancelable: true }),
      )
    })
    expect(ops).toEqual([])
  })
})

describe('连按两下 ⌥↓、前一下还没回来（Codex #832）', () => {
  const altDown = (path: string) =>
    act(async () => {
      host
        .querySelector<HTMLElement>(`[data-workspace-row][data-project-path="${path}"]`)!
        .querySelector<HTMLButtonElement>('[data-workspace-open]')!
        .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true, cancelable: true }))
    })
  /** 放行挂着的第一个请求，等它回包落地、队列把下一个发出去 */
  const releaseNext = async () => {
    for (let i = 0; i < 20 && !held!.length; i++) await act(async () => {})
    await act(async () => held!.shift()!())
    for (let i = 0; i < 5; i++) await act(async () => {})
  }

  it.each([
    ['A B C', ['/a/A', '/b/B', '/c/C'], ['/b/B', '/c/C', '/a/A'], ['/b/B', '/c/C', '/a/A']],
    // 藏起来的当前项目压在中间：第二下也要按那时可见的邻居算，越过它
    [
      '当前项目藏在中间',
      ['/a/A', '/b/B', CURRENT, '/c/C'],
      ['/b/B', CURRENT, '/c/C', '/a/A'],
      ['/b/B', '/c/C', '/a/A'],
    ],
  ])('挪两格，而不是挪过去又挪回来（%s）', async (_label, start, end, visible) => {
    server = [...start]
    useProjectStore.setState({ pinned: server.map((p) => entryOf(p)) })
    held = []
    await mount()
    await altDown('/a/A')
    await altDown('/a/A')
    await releaseNext()
    await releaseNext()
    expect(server).toEqual(end)
    expect(rowNames('pinned')).toEqual(visible)
    expect(ops).toEqual([
      { op: 'move', path: '/a/A', to_path: '/b/B' },
      { op: 'move', path: '/a/A', to_path: '/c/C' },
    ])
  })
})

describe('切换中', () => {
  it('有一次切换在进行时，所有「打开」入口都置灰，不只是正在打开的那一行', async () => {
    useProjectStore.setState({ switching: true })
    await mount()
    const opens = [...host.querySelectorAll<HTMLButtonElement>('[data-workspace-row] button[title]')]
    expect(opens.length).toBeGreaterThan(3)
    expect(opens.every((b) => b.disabled)).toBe(true)
    const footer = [...host.querySelectorAll<HTMLButtonElement>('[data-workspace-footer] button')]
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

  it('当前项目的目录已不在（exists: false）时卡片也不给这一项', async () => {
    desktop.can = true
    useProjectStore.setState({ project: { ...current, exists: false } })
    await mount()
    rightClick(host.querySelector('[data-workspace-current]')!)
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

describe('键位契约与拖放落点（2026-10-07 设计审计 §10.3）', () => {
  const key = (el: Element, k: string, init: KeyboardEventInit = {}) =>
    act(() => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
    })
  const opens = () => [...host.querySelectorAll<HTMLButtonElement>('[data-workspace-open]')]

  it('一列一个 Tab 停靠点：↑↓ 在行间走，焦点到哪一行哪一行就是停靠点', async () => {
    await mount()
    const tabbable = () => opens().filter((b) => b.tabIndex === 0)
    expect(tabbable()).toHaveLength(1)
    act(() => opens()[0].focus())
    key(opens()[0], 'ArrowDown')
    expect(document.activeElement).toBe(opens()[1])
    expect(tabbable()).toEqual([opens()[1]])
    key(opens()[1], 'ArrowUp')
    expect(document.activeElement).toBe(opens()[0])
  })

  it('收藏行 ⌥↓ = 下移（拖动的键盘那条路，按路径发 move）', async () => {
    await mount()
    const first = section('pinned')!.querySelector<HTMLElement>('[data-workspace-row]')!
    await act(async () => {
      first.querySelector<HTMLButtonElement>('[data-workspace-open]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true, cancelable: true }),
      )
    })
    // 与可见的下一行换位：按路径挪到它此刻的位置（跳过藏在顶上卡里的当前项目，见下一组）
    expect(ops).toEqual([{ op: 'move', path: '/a/Supplementary', to_path: '/b/Rebuttal' }])
  })

  it('⇧F10 在行上开出与「⋯」同一份菜单', async () => {
    await mount()
    const r = section('recent')!.querySelector<HTMLElement>('[data-workspace-row]')!
    key(r.querySelector('[data-workspace-open]')!, 'F10', { shiftKey: true })
    expect(document.querySelector('[role="menu"]')).not.toBeNull()
  })

  /** 键盘能落到的元素：tabIndex ≥ 0 且没 disabled（jsdom 不算 Tab 顺序，这就是判据） */
  const reachable = (el: HTMLElement | null) =>
    !!el && el.tabIndex >= 0 && !(el as HTMLButtonElement).disabled

  it('当前项目卡的「⋯」在 Tab 顺序里（卡本身不可聚焦）；⇧F10 在它上面开出菜单（Codex #832）', async () => {
    await mount()
    const card = host.querySelector<HTMLElement>('[data-workspace-current]')!
    const more = card.querySelector<HTMLButtonElement>('[data-row-menu-trigger]')!
    // 卡里没有别的可聚焦后代：⋯ 不进 Tab 顺序的话，收藏 / 新标签页 / 接入状态键盘一个都够不着
    expect([...card.querySelectorAll<HTMLElement>('button, [tabindex]')].filter(reachable)).toEqual([more])
    act(() => more.focus())
    key(more, 'F10', { shiftKey: true })
    expect(document.querySelector('[role="menu"]')).not.toBeNull()
  })

  it('目录已不在的行：「打开」仍可聚焦（aria-disabled）、点了不打开；行里的 ⋯ 跟着进 Tab 顺序（Codex #832）', async () => {
    useProjectStore.setState({ recent: [entryOf('/work/live'), entryOf('/gone/x', { exists: false })] })
    await mount()
    const r = host.querySelector<HTMLElement>('[data-workspace-row][data-project-path="/gone/x"]')!
    const opener = r.querySelector<HTMLButtonElement>('[data-workspace-open]')!
    expect(opener.disabled).toBe(false)
    expect(opener.getAttribute('aria-disabled')).toBe('true')
    // 漫游列表把它算作一站：↓ 从上一行走得到它
    const prev = opens()[opens().indexOf(opener) - 1]
    act(() => prev.focus())
    key(prev, 'ArrowDown')
    expect(document.activeElement).toBe(opener)
    expect(reachable(opener)).toBe(true)
    // 焦点在行里 → ⋯ 进 Tab 顺序（移除在里面），⇧F10 开同一份
    expect(reachable(r.querySelector<HTMLElement>('[data-row-menu-trigger]'))).toBe(true)
    key(opener, 'F10', { shiftKey: true })
    expect(document.querySelector('[data-project-remove]')).not.toBeNull()
    act(() => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    // 仍然打不开：点击 / 回车都不发 open
    await act(async () => opener.click())
    expect(open).not.toHaveBeenCalled()
  })

  it('拖到另一条收藏上时画落点线（2px accent + 圆点），离开就撤', async () => {
    await mount()
    const [a, b] = [...section('pinned')!.querySelectorAll<HTMLElement>('[data-workspace-row]')]
    act(() => {
      a.dispatchEvent(new Event('dragstart', { bubbles: true }))
      b.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }))
    })
    const line = b.querySelector<HTMLElement>(':scope > span[aria-hidden]')!
    expect(line.className).toContain('bg-accent')
    // 从上面拖下来：落下之后它占这一行，线画在下缘
    expect(line.className).toContain('-bottom-px')
    act(() => {
      b.dispatchEvent(new Event('dragleave', { bubbles: true }))
    })
    expect(line.className).toBe('hidden')
  })
})
