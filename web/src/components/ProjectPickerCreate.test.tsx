/**
 * 「新建项目」的目录选择（审计 T03：目录选择优先沿用系统文件选择体验）。
 *
 * 桌面壳里「浏览目录」早就用系统选择器了，只有「新建项目」还把用户丢进
 * Tavotto 自画的服务器端目录浏览器——同一屏上的两颗相邻按钮给出两种不同的
 * 选目录体验。现在两颗都先走系统选择器，新建再多问一个名字。
 *
 * 浏览器模式没有系统选择器可用（本地单用户应用，浏览的就是本机磁盘），
 * 那条路径一个字节都不动。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus } from '@/lib/api'
import { ProjectPicker } from '@/components/ProjectPicker'
import { useProjectStore } from '@/store/projectStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async (url: unknown) => {
  // 目录浏览器要有东西可列，否则它只显示一句错误，判据就分不出「哪个弹窗」
  if (String(url).includes('/api/projects/browse')) {
    return new Response(
      JSON.stringify({ path: '/Users/jiaqi', parent: '/Users', dirs: [{ name: 'Desktop', path: '/Users/jiaqi/Desktop' }] }),
      { status: 200 },
    )
  }
  // 教程入口问 /api/tutorial/status：404 = 宿主没有教程 API，整行不出现
  return new Response('{}', { status: 404 })
}) as typeof fetch

const desktop = vi.hoisted(() => ({
  isDesktop: vi.fn(() => true),
  pickDirectory: vi.fn(async (_title?: string): Promise<string | null> => '/Users/jiaqi/Desktop'),
}))
vi.mock('@/lib/desktop', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  ...desktop,
}))

let root: Root
let host: HTMLDivElement
const open = vi.fn(async (_path: string, _create?: boolean): Promise<ProjectStatus> => ({ open: true }))

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ProjectPicker />)
  })
}

const button = (label: string) =>
  [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (b) => b.textContent?.trim() === label,
  )

/** 弹窗里那个输入框（`type` 是属性默认值，DOM 上没这个 attribute，选不中） */
const nameInput = () => document.querySelector<HTMLInputElement>('[role="dialog"] input')!

const typeInto = (input: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(async () => {
  document.body.innerHTML = ''
  open.mockClear()
  desktop.isDesktop.mockReturnValue(true)
  desktop.pickDirectory.mockResolvedValue('/Users/jiaqi/Desktop')
  useProjectStore.setState({
    phase: 'none',
    project: null,
    recent: [],
    opened: [],
    open,
    remove: vi.fn(async () => {}),
    removeMany: vi.fn(async () => {}),
  })
  await mount()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('桌面壳的「新建项目」', () => {
  it('先弹系统目录选择器，再只问一个名字，最后按 上级/名字 建项目', async () => {
    await act(async () => button('新建项目')!.click())
    expect(desktop.pickDirectory).toHaveBeenCalledTimes(1)
    // 选完目录还没建：得先有个名字
    expect(open).not.toHaveBeenCalled()

    expect(document.body.textContent).toContain('/Users/jiaqi/Desktop')
    typeInto(nameInput(), 'my_paper_figures')
    await act(async () => button('在此新建')!.click())
    expect(open).toHaveBeenCalledWith('/Users/jiaqi/Desktop/my_paper_figures', true)
  })

  it('名字为空时建不了（不许悄悄拿上级目录当项目）', async () => {
    await act(async () => button('新建项目')!.click())
    const confirm = button('在此新建')!
    expect(confirm.disabled).toBe(true)
    typeInto(nameInput(), '   ')
    expect(button('在此新建')!.disabled).toBe(true)
    expect(open).not.toHaveBeenCalled()
  })

  it('在系统选择器里点取消：什么都不发生', async () => {
    desktop.pickDirectory.mockResolvedValue(null)
    await act(async () => button('新建项目')!.click())
    expect(open).not.toHaveBeenCalled()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('浏览器模式仍走服务器端目录浏览器，不去问系统选择器', async () => {
    desktop.isDesktop.mockReturnValue(false)
    desktop.pickDirectory.mockClear()
    await act(async () => button('新建项目')!.click())
    expect(desktop.pickDirectory).not.toHaveBeenCalled()
    // 判据要指着目录浏览器**自己**的部件：「新建项目」既是弹窗标题也是那颗
    // 按钮的文字，拿它当判据两条路径都绿
    const dlg = document.querySelector('[role="dialog"]')!
    expect(dlg.querySelector('input[aria-label="当前路径"]')).not.toBeNull()
    expect(dlg.textContent).toContain('Desktop')
    // 只问名字的那个弹窗会说「将在 … 下新建」；目录浏览器不说这句
    expect(dlg.textContent).not.toContain('将在')
  })
})
