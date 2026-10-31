/**
 * Project Picker「全部项目」（审计 T02；手帐索引页 redesign-3 A3）：主入口固定、索引 + 详情独立滚动、同名可辨认、
 * 失效项成组可移除、输入框兼作筛选 / 路径打开、选中 + 详情卡。
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
  // 这些入口在「全部项目」视图里：主页的「浏览更多项目 / 其他打开方式」进去
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-home-all]')!.click()
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
const submitButton = () => host.querySelector<HTMLButtonElement>('form[role="search"] button[type="submit"]')
const rowOf = (name: string) => openButtons().find((b) => b.getAttribute('aria-label') === `打开项目 ${name}`)!
const MISSING = 'section[aria-label="已不存在的目录"]'
const detail = () => host.querySelector<HTMLElement>('[data-picker-detail]')
const click = (el: Element) => act(() => (el as HTMLElement).click())

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
    expect(header.textContent).toContain('浏览…')
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
    // 30 条都装进去了：26 条可打开 + 4 条失效（成组放在索引末尾）
    expect(host.textContent).toContain('全部项目')
    expect(host.textContent).toContain('30 个')
  })

  it('教程入口锚点还在（e2e 依赖）', () => {
    // 这个用例里教程 API 是 404 → 整行不出现；只守「没有别的东西占了这个锚点」
    expect(host.querySelectorAll('[data-onboarding-anchor="tutorial-entry"]').length).toBeLessThanOrEqual(1)
  })
})

describe('同名项目', () => {
  it('六个 figs 各自带能区分的父目录；不同名的路径在选中后的详情卡里', () => {
    const rows = openButtons()
    const figs = rows.filter((b) => b.getAttribute('aria-label') === '打开项目 figs')
    // 两个还在的 figs 在前面，四个失效的成组在后面
    expect(figs).toHaveLength(6)
    expect(figs[0].textContent).toContain('…/pytest-0/figs')
    expect(figs[1].textContent).toContain('…/pytest-1/figs')
    expect(figs[0].getAttribute('title')).toBe('/private/var/folders/T/pytest-of-jiaqi/pytest-0/figs')
    // 不同名的行不带后缀；整条路径在详情卡里
    const polish = rowOf('polish-mech')
    expect(polish.textContent).not.toContain('/Users/jiaqi')
    click(polish)
    expect(detail()!.textContent).toContain('/Users/jiaqi/Desktop/polish-mech')
    // 教程副本：详情卡显示徽标而不是路径
    click(rowOf('Tutorial'))
    expect(detail()!.textContent).toContain('教程项目')
    expect(detail()!.textContent).not.toContain('/data/tutorial')
  })
})

describe('已不存在的目录', () => {
  it('成组放在索引末尾（×、删除线），能全部移除；选中后详情卡的主按钮是「从列表移除」', () => {
    // 主列表里没有失效项
    expect(openButtons().filter((b) => !b.closest(MISSING)).some((b) => b.disabled)).toBe(false)
    const group = host.querySelector<HTMLElement>('section[aria-label="已不存在的目录"]')!
    expect(group.textContent).toContain('已不存在 4')
    const rows = group.querySelectorAll('li')
    expect(rows).toHaveLength(4)
    expect(rows[0].textContent).toContain('×')
    expect(rows[0].textContent).toContain('…/pytest-2/figs')
    expect(rows[0].querySelector('.line-through')).not.toBeNull()
    // 目录不在了是常态：行仍是漫游的一站（不 disabled），Enter / 双击不打开
    const opener = rows[0].querySelector<HTMLButtonElement>('[data-picker-open]')!
    expect(opener.disabled).toBe(false)
    click(opener)
    const card = detail()!
    expect(card.textContent).toContain('目录不存在')
    expect(card.querySelector('[data-picker-detail-open]')).toBeNull()
    click(card.querySelector('[data-picker-detail-remove]')!)
    expect(remove).toHaveBeenCalledWith('/private/var/folders/T/pytest-of-jiaqi/pytest-2/figs')

    const all = [...group.querySelectorAll('button')].find((b) => b.textContent === '全部移除')!
    click(all)
    expect(removeMany).toHaveBeenCalledTimes(1)
    expect(removeMany.mock.calls[0][0]).toEqual([2, 3, 4, 5].map((i) => `/private/var/folders/T/pytest-of-jiaqi/pytest-${i}/figs`))
  })
})

describe('选中 + 详情卡', () => {
  it('默认选第一个；单击选中，详情卡翻页；双击或 Enter 才打开', () => {
    // 第一个是教程副本：详情卡说「教程项目」，不露数据目录里的路径
    expect(detail()!.textContent).toContain('Tutorial')
    expect(detail()!.textContent).toContain('教程项目')
    expect(detail()!.textContent).not.toContain('/data/tutorial')
    expect(rowOf('Tutorial').closest('li')!.hasAttribute('data-selected')).toBe(true)

    click(rowOf('polish-mech'))
    expect(rowOf('polish-mech').closest('li')!.hasAttribute('data-selected')).toBe(true)
    expect(rowOf('Tutorial').closest('li')!.hasAttribute('data-selected')).toBe(false)
    expect(detail()!.textContent).toContain('/Users/jiaqi/Desktop/polish-mech')
    expect(open).not.toHaveBeenCalled()

    act(() => {
      rowOf('polish-mech').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/Desktop/polish-mech', false)

    open.mockClear()
    const key = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    act(() => {
      rowOf('2d 处理').dispatchEvent(key)
    })
    expect(key.defaultPrevented).toBe(true)
    expect(open).toHaveBeenCalledWith('/Volumes/T7 Touch/project/2d 处理', false)

    // 详情卡的黑色「打开」打开的是选中的那一个
    open.mockClear()
    click(rowOf('polish-mech'))
    click(detail()!.querySelector('[data-picker-detail-open]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/Desktop/polish-mech', false)
  })

  it('键盘聚焦也选中（↑↓ 漫游时详情跟着翻页）', () => {
    act(() => rowOf('polish-mech').focus())
    expect(detail()!.textContent).toContain('/Users/jiaqi/Desktop/polish-mech')
  })

  it('筛选把选中项筛掉后回到第一个；框里回车打开选中的那一个', () => {
    click(rowOf('polish-mech'))
    typeInto(pathInput(), 'project-')
    expect(detail()!.querySelector('h2')!.textContent).toBe('project-10')
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/work/project-10', false)
  })
})

describe('一列一个 Tab 停靠点', () => {
  it('索引（含末尾失效组）整列恰好一个 tabIndex=0', () => {
    const stops = [...host.querySelectorAll<HTMLElement>('[data-roving]')].filter((el) => el.tabIndex === 0)
    expect(stops).toHaveLength(1)
    expect(host.querySelectorAll('[data-roving]')).toHaveLength(30)
  })
})

describe('输入框：路径打开 / 名字筛选', () => {
  it('输入名字只留匹配项，两组都筛；唯一匹配时回车打开它', () => {
    typeInto(pathInput(), 'polish')
    expect(openButtons()).toHaveLength(1)
    expect(host.querySelector('section[aria-label="已不存在的目录"]')).toBeNull()
    // 名字态：框尾是「新建项目 · 浏览…」，没有提交钮；回车打开唯一匹配项
    expect(submitButton()).toBeNull()
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/Desktop/polish-mech', false)
  })

  it('多个匹配时回车打开选中的（默认第一个）；筛不到时说出来，回车什么也不开', () => {
    typeInto(pathInput(), 'figs')
    expect(openButtons().filter((b) => !b.closest(MISSING))).toHaveLength(2)
    expect(host.querySelector('section[aria-label="已不存在的目录"]')!.textContent).toContain('已不存在 4')
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/private/var/folders/T/pytest-of-jiaqi/pytest-0/figs', false)
    open.mockClear()
    typeInto(pathInput(), 'nothing-like-this')
    expect(openButtons()).toHaveLength(0)
    expect(host.querySelector('[data-picker-no-match]')!.textContent).toContain('没有与“nothing-like-this”匹配的最近项目')
    expect(detail()).toBeNull()
    submit(host.querySelector('form[role="search"]')!)
    expect(open).not.toHaveBeenCalled()
  })

  it('像路径就直接打开那条路径：框尾换成「打开 ↵」（含 ~ 与 Windows 盘符）', () => {
    typeInto(pathInput(), '/Users/jiaqi/elsewhere/figs')
    expect(submitButton()!.disabled).toBe(false)
    expect(submitButton()!.textContent).toBe('打开')
    expect(host.querySelector('form[role="search"]')!.textContent).not.toContain('新建项目')
    typeInto(pathInput(), '~/figs')
    expect(submitButton()).not.toBeNull()
    typeInto(pathInput(), 'C:\\Users\\me\\figs')
    expect(submitButton()).not.toBeNull()
    typeInto(pathInput(), '/Users/jiaqi/elsewhere/figs')
    submit(host.querySelector('form[role="search"]')!)
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/elsewhere/figs', false)
  })
})

describe('切换进行中（Codex #550）', () => {
  it('打开 / 新建 / 返回 / 回车提交 / 最近项目行一起置灰', async () => {
    await act(async () => {
      useProjectStore.setState({
        project: { open: true, id: 'p_cur', name: 'cur', figures_dir: '/cur' },
        switching: true,
      })
    })
    const buttons = [...host.querySelectorAll<HTMLButtonElement>('header button')]
    const byText = (t: string) => buttons.find((b) => b.textContent?.includes(t))!
    expect(byText('新建项目').disabled).toBe(true)
    expect(byText('浏览…').disabled).toBe(true)
    expect(byText('返回当前项目').disabled).toBe(true)
    expect(openButtons().length).toBeGreaterThan(20)
    // 可打开的行 + 详情卡的「打开」一起置灰；目录不在了的行不灰（能选中、能移除）
    expect(openButtons().filter((b) => !b.closest(MISSING)).every((b) => b.disabled)).toBe(true)
    expect(detail()!.querySelector<HTMLButtonElement>('[data-picker-detail-open]')!.disabled).toBe(true)
    // 路径态的提交钮也灰
    typeInto(pathInput(), '/x/y')
    expect(submitButton()!.disabled).toBe(true)
    typeInto(pathInput(), '')

    await act(async () => useProjectStore.setState({ switching: false }))
    expect(byText('返回当前项目').disabled).toBe(false)
    expect(openButtons().some((b) => !b.disabled)).toBe(true)
    expect(detail()!.querySelector<HTMLButtonElement>('[data-picker-detail-open]')!.disabled).toBe(false)
  })
})
