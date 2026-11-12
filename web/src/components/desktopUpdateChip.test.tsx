/**
 * 「新版已就绪」顶栏芯片（后台自动下载下好之后）。形态同 BuildMismatchChip：24 高胶囊、不进文档流、
 * 不是对话框。主语：认 `data-update-ready`；「没有模态」= 文档里没有 `data-dialog`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const desktop = vi.hoisted(() => ({ is: true }))
vi.mock('@/lib/desktop', () => ({
  isDesktop: () => desktop.is,
  checkDesktopUpdate: vi.fn(),
  downloadDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(async () => 'mirror'),
  relaunchDesktop: vi.fn(async () => {}),
}))

import { installDesktopUpdate, relaunchDesktop } from '@/lib/desktop'
import { useUpdateStore } from '@/store/updateStore'
import { DesktopUpdateChip } from './DesktopUpdateChip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
const mount = () => act(async () => root.render(<DesktopUpdateChip />))
const READY = { version: '9.9.9', source: 'mirror' as const }

beforeEach(() => {
  desktop.is = true
  useUpdateStore.setState(
    { ...useUpdateStore.getInitialState(), desktopUpdate: { version: '9.9.9' }, desktopReady: READY },
    true,
  )
  vi.mocked(installDesktopUpdate).mockClear()
  vi.mocked(relaunchDesktop).mockClear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('DesktopUpdateChip', () => {
  it('没有已下好的包 / 浏览器里 / 已经装好等重启时：什么都不画', async () => {
    useUpdateStore.setState({ desktopReady: null })
    await mount()
    expect(host.querySelector('[data-update-ready]')).toBeNull()

    useUpdateStore.setState({ desktopReady: READY })
    desktop.is = false
    await mount()
    expect(host.querySelector('[data-update-ready]')).toBeNull()

    desktop.is = true
    useUpdateStore.setState({ desktopPhase: 'installed' })
    await mount()
    expect(host.querySelector('[data-update-ready]')).toBeNull()
  })

  it('下载失败的状态（只有 desktopUpdate、没有 desktopReady、没有错误）不出任何芯片', async () => {
    useUpdateStore.setState({ desktopReady: null, desktopError: null })
    await mount()
    expect(host.querySelector('[data-update-ready]')).toBeNull()
  })

  it('是 24 高的芯片按钮：accent 圆点 + 一句短语；窄屏只留圆点；不是对话框', async () => {
    await mount()
    const chip = host.querySelector<HTMLButtonElement>('[data-update-ready]')!
    expect(chip.tagName).toBe('BUTTON')
    expect(chip.className).toMatch(/\bh-6\b/)
    expect(chip.className).toMatch(/\brounded-full\b/)
    expect(chip.className).toMatch(/max-\[899px\]:w-6/)
    expect(chip.textContent).toBe('新版已就绪')
    expect(chip.querySelector('[aria-hidden]')!.className).toMatch(/\bsize-1\.5\b.*\bbg-accent\b/)
    expect(chip.querySelector('span:not([aria-hidden])')!.className).toMatch(/max-\[899px\]:sr-only/)
    expect(document.querySelector('[data-dialog]')).toBeNull()
    expect(document.activeElement).toBe(document.body)
  })

  it('点开是一句话 + 一个主按钮；按钮 = 装已下好的包 + 重启，各一次', async () => {
    await mount()
    await act(async () => host.querySelector<HTMLButtonElement>('[data-update-ready]')!.click())
    const pop = document.querySelector('[data-update-ready-popover]')!
    expect(pop.querySelector('p')!.textContent).toBe('新版 9.9.9 已下载好，退出时会自动装上。')
    expect(pop.querySelectorAll('button')).toHaveLength(1)
    const btn = pop.querySelector<HTMLButtonElement>('[data-update-ready-restart]')!
    expect(btn.textContent).toBe('现在重启')
    await act(async () => btn.click())
    expect(installDesktopUpdate).toHaveBeenCalledOnce()
    expect(relaunchDesktop).toHaveBeenCalledOnce()
  })
})
