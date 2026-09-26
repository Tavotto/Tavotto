/**
 * 主页（新手版 / 老手版）：哪一版只由 onboarding 状态决定；三个导入入口（按钮 /
 * 拖放区 / 拖放）都落到「打开脚本所在的目录」；示例入口分「开始教程」与「只打开示例」；
 * 最近项目只摆点得开的那几条，其余功能在「全部项目」里一个不少。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { ProjectPicker } from '@/components/ProjectPicker'
import { configureOnboardingPersistence, useOnboardingStore, type OnboardingStatus } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useTutorialStore } from '@/lib/onboarding/tutorial'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const desktop = vi.hoisted(() => {
  const state: { handler: ((d: unknown) => void) | null; offs: number } = { handler: null, offs: 0 }
  return {
    state,
    isDesktop: vi.fn(() => true),
    pickScriptFile: vi.fn(async (_title?: string): Promise<string | null> => '/Users/me/paper/plot.py'),
    nativeFileDropAvailable: vi.fn(async () => false),
    onNativeFileDrop: vi.fn(async (h: (d: unknown) => void) => {
      state.handler = h
      return () => {
        state.handler = null
        state.offs++
      }
    }),
  }
})
vi.mock('@/lib/desktop', async (orig) => ({ ...(await orig<Record<string, unknown>>()), ...desktop }))

const tutorial = vi.hoisted(() => ({
  runTutorialEntry: vi.fn(async () => ({ ok: true, kind: 'started' })),
  openSampleProject: vi.fn(async () => ({ ok: true, kind: 'opened' })),
}))
vi.mock('@/lib/onboarding/tutorial', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  ...tutorial,
}))

let tutorialApi: 'ok' | 'broken' | 'none' | 'pending' = 'ok'
globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  if (u.includes('/api/tutorial')) {
    if (tutorialApi === 'pending') return new Promise<Response>(() => {})
    if (tutorialApi === 'none') return new Response('{}', { status: 404 })
    return new Response(JSON.stringify({ available: tutorialApi === 'ok', problems: [] }), { status: 200 })
  }
  if (u.includes('/api/projects/browse')) {
    return new Response(JSON.stringify({ path: '/srv', parent: '/', dirs: [] }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const NOW = Date.now()
const recentOf = (path: string, extra: Partial<RecentProject> = {}): RecentProject => ({
  path,
  name: path.split('/').pop()!,
  last_opened: NOW - 2 * 3600_000,
  exists: true,
  current: false,
  ...extra,
})
const RECENT: RecentProject[] = [
  recentOf('/data/tutorial/Tutorial', { name: 'Tutorial', tutorial: true }),
  recentOf('/Users/me/Desktop/poster', { last_opened: NOW - 26 * 3600_000 }),
  recentOf('/Users/me/gone', { exists: false }),
  recentOf('/Users/me/Projects/kinetics'),
  recentOf('/Users/me/Research/paper_figures'),
  recentOf('/Users/me/Research/thesis'),
  recentOf('/Users/me/Research/old'),
]

let root: Root
let host: HTMLDivElement
const open = vi.fn(async (_path: string, _create?: boolean): Promise<ProjectStatus> => ({ open: true }))
const remove = vi.fn(async (_path: string) => {})

function setOnboarding(status: OnboardingStatus) {
  useOnboardingStore.setState({ status })
}

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ProjectPicker />)
  })
}

const main = () => host.querySelector<HTMLElement>('main')!
const buttonByText = (text: string) =>
  [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.includes(text))
const click = async (el: HTMLElement) => {
  await act(async () => el.click())
}

beforeEach(() => {
  tutorialApi = 'ok'
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useTutorialStore.setState({ status: null, meta: null, busy: null, failure: null })
  useProjectStore.setState({ phase: 'none', project: null, recent: RECENT, opened: [], switching: false, open, remove })
  open.mockClear()
  remove.mockClear()
  desktop.isDesktop.mockReturnValue(true)
  desktop.pickScriptFile.mockClear()
  desktop.nativeFileDropAvailable.mockResolvedValue(false)
  desktop.state.handler = null
  desktop.state.offs = 0
  useUiStore.setState({ status: null })
  tutorial.runTutorialEntry.mockClear()
  tutorial.openSampleProject.mockClear()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('两版切换判据', () => {
  it.each([
    ['not_started', 'newcomer'],
    ['active', 'newcomer'],
    ['paused', 'newcomer'],
    ['completed', 'returning'],
    ['skipped', 'returning'],
  ] as const)('onboarding %s → %s', async (status, variant) => {
    setOnboarding(status)
    await mount()
    expect(main().dataset.homeVariant).toBe(variant)
  })

  it('状态在屏上变了，版式跟着变（同一窗口里走完教程再回来）', async () => {
    await mount()
    expect(main().dataset.homeVariant).toBe('newcomer')
    await act(async () => setOnboarding('completed'))
    expect(main().dataset.homeVariant).toBe('returning')
  })

  it('渲染主页不写 onboarding（派生状态不覆盖偏好）', async () => {
    const writes: string[] = []
    configureOnboardingPersistence({ read: () => null, write: (raw) => writes.push(raw), remove: () => {} })
    await mount()
    expect(writes).toEqual([])
  })
})

describe('新手版', () => {
  it('三步 + 主按钮开始教程；暂停过的人看到「继续」', async () => {
    await mount()
    expect(host.querySelectorAll('ol > li')).toHaveLength(3)
    const primary = host.querySelector<HTMLButtonElement>('[data-onboarding-anchor="tutorial-entry"]')!
    expect(primary.textContent).toContain('用示例体验一次')
    await click(primary)
    expect(tutorial.runTutorialEntry).toHaveBeenCalledWith('picker')
    await act(async () => setOnboarding('paused'))
    expect(host.querySelector('[data-onboarding-anchor="tutorial-entry"]')!.textContent).toContain('继续示例教程')
  })

  it('「已随安装包内置」只在后端验过资源时说；资源坏了说重新安装；没有教程 API 时整块不出现', async () => {
    await mount()
    expect(host.textContent).toContain('已随安装包内置示例脚本')
    act(() => root.unmount())
    host.remove()

    tutorialApi = 'broken'
    useTutorialStore.setState({ status: null, failure: null })
    await mount()
    expect(host.textContent).not.toContain('已随安装包内置示例脚本')
    expect(host.textContent).toContain('请重新安装')
    expect(host.querySelector<HTMLButtonElement>('[data-onboarding-anchor="tutorial-entry"]')!.disabled).toBe(true)
    act(() => root.unmount())
    host.remove()

    tutorialApi = 'none'
    useTutorialStore.setState({ status: null, failure: null })
    await mount()
    expect(host.querySelector('[data-onboarding-anchor="tutorial-entry"]')).toBeNull()
    expect(host.textContent).not.toContain('已随安装包内置示例脚本')
    // 导入入口不受影响
    expect(host.querySelector('[data-home-import]')).not.toBeNull()
  })

  it('资源探测还没回来：那句「已内置」先不说（还不知道成不成立）', async () => {
    tutorialApi = 'pending'
    await mount()
    expect(host.querySelector('[data-onboarding-anchor="tutorial-entry"]')).not.toBeNull()
    expect(host.textContent).not.toContain('已随安装包内置示例脚本')
  })

  it('「导入我的脚本」：桌面壳原生选 .py → 打开它所在的目录', async () => {
    await mount()
    await click(host.querySelector<HTMLButtonElement>('[data-home-import]')!)
    expect(desktop.pickScriptFile).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledWith('/Users/me/paper', false)
  })

  it('浏览器模式：退回服务器端目录浏览器，标题说的是「脚本所在的文件夹」', async () => {
    desktop.isDesktop.mockReturnValue(false)
    await mount()
    await click(host.querySelector<HTMLButtonElement>('[data-home-import]')!)
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain('选择脚本所在的文件夹')
  })

  it('最近项目：最多三张卡、只摆点得开的、教程显示徽标、时间是「打开于」', async () => {
    await mount()
    const items = [...host.querySelectorAll<HTMLElement>('[data-home-recent]')]
    expect(items.map((li) => li.dataset.homeRecent)).toEqual([
      '/data/tutorial/Tutorial',
      '/Users/me/Desktop/poster',
      '/Users/me/Projects/kinetics',
    ])
    expect(items[0].textContent).toContain('教程项目')
    expect(items[1].textContent).toContain('打开于')
    expect(host.textContent).toContain('7 个')
    await click(items[1].querySelector<HTMLButtonElement>('button[aria-label="打开项目 poster"]')!)
    expect(open).toHaveBeenCalledWith('/Users/me/Desktop/poster', false)
  })
})

describe('老手版', () => {
  beforeEach(() => setOnboarding('completed'))

  it('示例按钮只打开示例项目（不重开教程）', async () => {
    await mount()
    await click(host.querySelector<HTMLButtonElement>('[data-home-sample]')!)
    expect(tutorial.openSampleProject).toHaveBeenCalledWith('picker')
    expect(tutorial.runTutorialEntry).not.toHaveBeenCalled()
  })

  it('拖放区是一颗按钮：点它 = 选择文件', async () => {
    await mount()
    const zone = host.querySelector<HTMLButtonElement>('[data-home-dropzone]')!
    expect(zone.tagName).toBe('BUTTON')
    await click(zone)
    expect(open).toHaveBeenCalledWith('/Users/me/paper', false)
  })

  const dropOn = async (el: HTMLElement, data: { uris?: string; files?: string[] }) => {
    const types = [...(data.uris ? ['text/uri-list'] : []), ...(data.files?.length ? ['Files'] : [])]
    const dt = {
      types,
      getData: (f: string) => (f === 'text/uri-list' ? (data.uris ?? '') : ''),
      files: (data.files ?? []).map((name) => ({ name })),
      dropEffect: 'none',
    }
    const ev = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(ev, 'dataTransfer', { value: dt })
    await act(async () => {
      el.dispatchEvent(ev)
    })
    return ev
  }

  it('放下带路径的 .py：直接打开它所在的目录', async () => {
    await mount()
    const ev = await dropOn(host.querySelector('[data-home-dropzone]')!, { uris: 'file:///Users/me/fig/plot.py', files: ['plot.py'] })
    expect(ev.defaultPrevented).toBe(true)
    expect(open).toHaveBeenCalledWith('/Users/me/fig', false)
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
  })

  it('放下的 .py 没有路径（浏览器 / 桌面壳的常态）：说清是哪个文件，退回选择器', async () => {
    await mount()
    // 用户在选择器里取消：那句说明留着，什么都不打开
    desktop.pickScriptFile.mockResolvedValueOnce(null)
    await dropOn(main(), { files: ['figure.py'] })
    expect(host.textContent).toContain('「figure.py」')
    expect(desktop.pickScriptFile).toHaveBeenCalledTimes(1)
    expect(open).not.toHaveBeenCalled()
    // 选中了：打开它所在的目录，说明收起
    await dropOn(main(), { files: ['figure.py'] })
    expect(open).toHaveBeenCalledWith('/Users/me/paper', false)
    expect(host.textContent).not.toContain('「figure.py」')
  })

  it('放下的不是 .py：报错、什么都不打开', async () => {
    await mount()
    await dropOn(main(), { files: ['fig.pdf'] })
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('fig.pdf')
    expect(open).not.toHaveBeenCalled()
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
  })

  it('最近项目：最多五行、带路径与相对时间；⋯ 菜单里能移除', async () => {
    await mount()
    const rows = [...host.querySelectorAll<HTMLElement>('[data-home-recent]')]
    expect(rows).toHaveLength(5)
    expect(rows.some((r) => r.dataset.homeRecent === '/Users/me/gone')).toBe(false)
    const poster = rows[1]
    expect(poster.textContent).toContain('/Users/me/Desktop/poster')
    expect(poster.textContent).toContain('1天前')
    const more = poster.querySelector<HTMLButtonElement>('button[aria-label="poster 的更多操作"]')!
    await act(async () => {
      more.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    const items = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')]
    expect(items.map((i) => i.textContent)).toEqual(['打开', '从列表移除，不删文件'])
    await click(items[1])
    expect(remove).toHaveBeenCalledWith('/Users/me/Desktop/poster')
  })
})

describe('系统拖放（桌面壳交来真实路径，ADR 0092）', () => {
  beforeEach(() => {
    setOnboarding('completed')
    desktop.nativeFileDropAvailable.mockResolvedValue(true)
  })
  afterEach(() => vi.useRealTimers())

  const fire = async (d: unknown) => {
    await act(async () => desktop.state.handler!(d))
  }
  const status = () => useUiStore.getState().status
  const statusKey = () => (status()?.message as { key?: string } | undefined)?.key ?? (status() as { key?: string } | null)?.key

  it('.py：直接打开它所在的文件夹，不弹选择器；通知说出是哪个脚本', async () => {
    await mount()
    await fire({ kind: 'script', folder: '/Users/me/fig', script: '/Users/me/fig/plot.py', name: 'plot.py', ignored: 0 })
    expect(open).toHaveBeenCalledWith('/Users/me/fig', false)
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
    expect(JSON.stringify(status())).toContain('home.import.openedScript')
    expect(JSON.stringify(status())).toContain('plot.py')
  })

  it('文件夹直接当项目；多拖了几个说只用了哪个', async () => {
    await mount()
    await fire({ kind: 'folder', folder: '/Users/me/paper', name: 'paper', ignored: 0 })
    expect(open).toHaveBeenCalledWith('/Users/me/paper', false)
    expect(JSON.stringify(status())).toContain('home.import.openedFolder')
    await fire({ kind: 'script', folder: '/a', script: '/a/b.py', name: 'b.py', ignored: 2 })
    expect(JSON.stringify(status())).toContain('home.import.droppedMany')
    expect(JSON.stringify(status())).toContain('"total":3')
  })

  it('不支持的类型：说不收，什么都不打开', async () => {
    await mount()
    await fire({ kind: 'unsupported', name: 'fig.pdf' })
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('fig.pdf')
    expect(open).not.toHaveBeenCalled()
  })

  it('页面自己的 drop（只有文件名）不再弹选择器：等壳的事件；壳没发才降级', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    await mount()
    const dropFile = async () => {
      const ev = new Event('drop', { bubbles: true, cancelable: true })
      Object.defineProperty(ev, 'dataTransfer', {
        value: { types: ['Files'], getData: () => '', files: [{ name: 'figure.py' }], dropEffect: 'none' },
      })
      await act(async () => {
        main().dispatchEvent(ev)
      })
    }
    await dropFile()
    await fire({ kind: 'script', folder: '/Users/me/fig', script: '/Users/me/fig/figure.py', name: 'figure.py', ignored: 0 })
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledTimes(1)

    // 壳这次没交来路径：等满了才退回选择器
    open.mockClear()
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    await dropFile()
    expect(desktop.pickScriptFile).not.toHaveBeenCalled()
    await act(async () => {
      vi.advanceTimersByTime(1500)
    })
    expect(desktop.pickScriptFile).toHaveBeenCalledTimes(1)
  })

  it('拖放区的说明跟着能力走：拿得到路径说「拖进来」，拿不到如实说还要选一次', async () => {
    await mount()
    expect(host.querySelector('[data-home-dropzone]')!.textContent).toContain('拖入 .py 文件或项目文件夹')
    act(() => root.unmount())
    host.remove()
    desktop.nativeFileDropAvailable.mockResolvedValue(false)
    await mount()
    expect(host.querySelector('[data-home-dropzone]')!.textContent).toContain('还要在弹出的窗口里选一次')
  })

  it('只有主页订阅：离开主页（进编辑器 / 全部项目）就退订，壳的事件落空', async () => {
    await mount()
    expect(desktop.state.handler).not.toBeNull()
    await click(host.querySelector<HTMLButtonElement>('[data-home-all]')!)
    expect(desktop.state.handler).toBeNull()
    expect(desktop.state.offs).toBe(1)
  })
})

describe('「全部项目」：设计稿上没有位置的功能都在这儿', () => {
  it('浏览更多 → 新建 / 打开文件夹 / 路径框 / 失效目录 / 教程入口；返回主页回来', async () => {
    await mount()
    await click(host.querySelector<HTMLButtonElement>('[data-home-all]')!)
    expect(buttonByText('新建项目')).toBeDefined()
    expect(buttonByText('浏览目录')).toBeDefined()
    expect(host.querySelector('input[aria-label="项目路径"]')).not.toBeNull()
    expect(host.querySelector('section[aria-label="已不存在的目录"]')).not.toBeNull()
    expect(host.querySelector('[data-onboarding-anchor="tutorial-entry"]')!.textContent).toContain('用示例了解')
    await click(host.querySelector<HTMLButtonElement>('[data-home-back]')!)
    expect(main().dataset.homeVariant).toBe('newcomer')
  })

  it('一条最近项目都没有：主页仍有通往「新建 / 按路径打开」的入口', async () => {
    useProjectStore.setState({ recent: [] })
    await mount()
    const link = host.querySelector<HTMLButtonElement>('[data-home-all]')!
    expect(link.textContent).toContain('新建项目或按路径打开')
    await click(link)
    expect(host.querySelector('input[aria-label="项目路径"]')).not.toBeNull()
  })

  it('返回当前项目（设置里「切换项目」进来时）在主页上也有', async () => {
    useProjectStore.setState({ project: { open: true, id: 'p', name: 'cur', figures_dir: '/cur' } })
    await mount()
    expect(buttonByText('返回当前项目')).toBeDefined()
  })
})
