/**
 * Project Picker（审计 T02）：主入口固定、最近项目独立滚动、同名可辨认、
 * 失效项分组可移除、输入框兼作筛选。
 *
 * jsdom 没有布局引擎，「30 条时新建 / 打开仍在视口里」量不出来；这里守的是
 * **结构**（固定区 `shrink-0`、只有列表容器 `overflow-y-auto`），真实高度在
 * Playwright 里量（见提交说明）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { ProjectPicker } from '@/components/ProjectPicker'
import { useProjectStore } from '@/store/projectStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// 教程入口会问 /api/tutorial/status：404 = 宿主没有教程 API，整行不出现
globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch

const recentOf = (path: string, exists = true, extra: Partial<RecentProject> = {}): RecentProject => ({
  path,
  name: path.split('/').pop()!,
  last_opened: 0,
  exists,
  current: false,
  ...extra,
})

/** 截图 74 的形状：几个正常项目、六个同名 figs（四个已失效）、一个教程 */
function thirtyEntries(): RecentProject[] {
  const out: RecentProject[] = [
    recentOf('/data/tutorial/figs', true, { name: 'Tutorial', tutorial: true }),
    recentOf('/Volumes/T7 Touch/project/2d 处理'),
    recentOf('/Users/jiaqi/Desktop/polish-mech'),
    recentOf('/Users/jiaqi/Desktop/random_roughness_sq8.0_v1'),
  ]
  for (let i = 0; i < 6; i++) {
    out.push(recentOf(`/private/var/folders/T/pytest-of-jiaqi/pytest-${i}/figs`, i < 2))
  }
  while (out.length < 30) out.push(recentOf(`/Users/jiaqi/work/project-${out.length}`))
  return out
}

let root: Root
let host: HTMLDivElement
const open = vi.fn(async (_path: string, _create?: boolean): Promise<ProjectStatus> => ({ open: true }))
const remove = vi.fn(async (_path: string) => {})
const removeMany = vi.fn(async (_paths: string[]) => {})

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  // 教程入口挂载后会异步问一次 /api/tutorial/status：把那次状态更新等进 act 里
  await act(async () => {
    root.render(<ProjectPicker />)
  })
}

const typeInto = (input: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const submit = (form: HTMLFormElement) =>
  act(() => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })

const openButtons = () => [...host.querySelectorAll<HTMLButtonElement>('button[aria-label^="打开项目 "]')]
const pathInput = () => host.querySelector<HTMLInputElement>('input[aria-label="项目路径"]')!
const submitButton = () => host.querySelector<HTMLButtonElement>('form[role="search"] button[type="submit"]')!

beforeEach(async () => {
  open.mockClear()
  remove.mockClear()
  removeMany.mockClear()
  useProjectStore.setState({
    phase: 'none',
    project: null,
    recent: thirtyEntries(),
    opened: [],
    open,
    remove,
    removeMany,
  })
  await mount()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('版式：主入口固定，列表独立滚', () => {
  it('固定区 shrink-0，只有列表容器 overflow-y-auto，且它在 min-h-0 的 flex 列里', () => {
    const main = host.querySelector('main')!
    expect(main.className).toContain('h-full')
    expect(main.className).toContain('flex-col')
    const header = host.querySelector('header')!
    expect(header.className).toContain('shrink-0')
    // 主入口都在固定区里
    expect(header.textContent).toContain('新建项目')
    expect(header.textContent).toContain('浏览目录')
    expect(header.contains(pathInput())).toBe(true)
    const scroll = host.querySelector<HTMLElement>('[data-recent-scroll]')!
    expect(scroll.className).toContain('overflow-y-auto')
    expect(scroll.className).toContain('min-h-0')
    expect(scroll.className).toContain('flex-1')
    const section = scroll.parentElement!
    expect(section.className).toContain('min-h-0')
    expect(section.className).toContain('flex-1')
    // 页面外层不再整页滚动
    expect(host.firstElementChild!.className).not.toContain('overflow-y-auto')
    // 30 条都装进去了：26 条可打开 + 4 条失效
    expect(host.textContent).toContain('最近项目')
    expect(host.textContent).toContain('30 个')
  })

  it('教程入口锚点还在（e2e 依赖）', () => {
    // 这个用例里教程 API 是 404 → 整行不出现；只守「没有别的东西占了这个锚点」
    expect(host.querySelectorAll('[data-onboarding-anchor="tutorial-entry"]').length).toBeLessThanOrEqual(1)
  })
})

describe('同名项目', () => {
  it('六个 figs 各自带能区分的父目录；不同名的显示整条路径', () => {
    const rows = openButtons()
    const figs = rows.filter((b) => b.getAttribute('aria-label') === '打开项目 figs')
    // 两个还在的 figs 在主列表里
    expect(figs).toHaveLength(2)
    expect(figs[0].textContent).toContain('…/pytest-0/figs')
    expect(figs[1].textContent).toContain('…/pytest-1/figs')
    expect(figs[0].getAttribute('title')).toBe('/private/var/folders/T/pytest-of-jiaqi/pytest-0/figs')
    const polish = rows.find((b) => b.getAttribute('aria-label') === '打开项目 polish-mech')!
    expect(polish.textContent).toContain('/Users/jiaqi/Desktop/polish-mech')
    // 尾部保留：路径那一行是 rtl 裁切
    expect(polish.querySelector('[dir="rtl"]')).not.toBeNull()
    // 教程副本显示徽标而不是路径
    const tut = rows.find((b) => b.getAttribute('aria-label') === '打开项目 Tutorial')!
    expect(tut.textContent).toContain('教程项目')
    expect(tut.textContent).not.toContain('/data/tutorial')
  })
})

describe('已不存在的目录', () => {
  it('收成一组、默认折叠、能全部移除、展开后各自能移除', () => {
    // 主列表里没有失效项
    expect(openButtons().some((b) => b.disabled)).toBe(false)
    const group = host.querySelector<HTMLElement>('section[aria-label="已不存在的目录"]')!
    expect(group.textContent).toContain('4 个')
    const toggle = group.querySelector<HTMLButtonElement>('button[aria-expanded]')!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(group.querySelectorAll('li')).toHaveLength(0)

    act(() => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const rows = group.querySelectorAll('li')
    expect(rows).toHaveLength(4)
    expect(rows[0].textContent).toContain('目录不存在')
    expect(rows[0].textContent).toContain('…/pytest-2/figs')
    // 打不开：主按钮禁用；移除按钮常驻
    expect(rows[0].querySelector<HTMLButtonElement>('button[aria-label^="打开项目"]')!.disabled).toBe(true)
    const rm = rows[0].querySelector<HTMLButtonElement>('button[aria-label^="从列表移除"]')!
    expect(rm.className).not.toContain('opacity-0')
    act(() => rm.click())
    expect(remove).toHaveBeenCalledWith('/private/var/folders/T/pytest-of-jiaqi/pytest-2/figs')

    const all = [...group.querySelectorAll('button')].find((b) => b.textContent === '全部移除')!
    act(() => all.click())
    expect(removeMany).toHaveBeenCalledTimes(1)
    expect(removeMany.mock.calls[0][0]).toEqual([2, 3, 4, 5].map((i) => `/private/var/folders/T/pytest-of-jiaqi/pytest-${i}/figs`))
  })
})

describe('输入框：路径打开 / 名字筛选', () => {
  it('输入名字只留匹配项，两组都筛；唯一匹配时回车打开它', () => {
    typeInto(pathInput(), 'polish')
    expect(openButtons()).toHaveLength(1)
    expect(host.querySelector('section[aria-label="已不存在的目录"]')).toBeNull()
    expect(submitButton().disabled).toBe(false)
    expect(submitButton().getAttribute('aria-label')).toBe('打开 polish-mech')
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/Desktop/polish-mech', false)
  })

  it('多个匹配时没有目标；筛不到时说出来', () => {
    typeInto(pathInput(), 'figs')
    expect(openButtons()).toHaveLength(2)
    expect(host.querySelector('section[aria-label="已不存在的目录"]')!.textContent).toContain('4 个')
    expect(submitButton().disabled).toBe(true)
    typeInto(pathInput(), 'nothing-like-this')
    expect(openButtons()).toHaveLength(0)
    expect(host.textContent).toContain('没有与“nothing-like-this”匹配的最近项目')
    expect(submitButton().disabled).toBe(true)
  })

  it('像路径就直接打开那条路径', () => {
    typeInto(pathInput(), '/Users/jiaqi/elsewhere/figs')
    expect(submitButton().disabled).toBe(false)
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/elsewhere/figs', false)
  })
})
