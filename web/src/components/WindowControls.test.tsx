/**
 * Windows 无框主窗口（窗框 `menu-button`）右上角的三颗窗口按钮：只在这个窗框里渲染，点了各调
 * 一条窗口命令，最大化后换成「还原」。浏览器 / macOS 不渲染——这里把窗口伪装成 Windows 壳。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WindowControls } from './WindowControls'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const win = vi.hoisted(() => {
  const state = { resized: null as null | (() => void) }
  return {
    state,
    minimize: vi.fn(async () => {}),
    toggleMaximize: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
    isMaximized: vi.fn(async () => false),
    onResized: vi.fn(async (cb: () => void) => {
      state.resized = cb
      return () => {}
    }),
  }
})
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => win }))

const w = window as unknown as Record<string, unknown>
let root: Root
let host: HTMLDivElement

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<WindowControls />))
}

beforeEach(() => {
  w.__TAURI_INTERNALS__ = {}
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  delete w.__TAURI_INTERNALS__
  delete w.__TAVOTTO_WINDOW_CHROME__
  vi.clearAllMocks()
})

const control = (action: string) => host.querySelector<HTMLButtonElement>(`[data-window-control="${action}"]`)

describe('WindowControls', () => {
  it('macOS（overlay）不渲染：红绿灯是系统的', async () => {
    w.__TAVOTTO_WINDOW_CHROME__ = 'overlay'
    await mount()
    expect(host.querySelector('[data-window-controls]')).toBeNull()
  })

  it('Windows：三颗按钮各调一条窗口命令', async () => {
    w.__TAVOTTO_WINDOW_CHROME__ = 'menu-button'
    await mount()
    await act(async () => control('minimize')!.click())
    await act(async () => control('toggle-maximize')!.click())
    await act(async () => control('close')!.click())
    expect(win.minimize).toHaveBeenCalledTimes(1)
    expect(win.toggleMaximize).toHaveBeenCalledTimes(1)
    expect(win.close).toHaveBeenCalledTimes(1)
    for (const a of ['minimize', 'toggle-maximize', 'close']) {
      expect(control(a)!.getAttribute('aria-label')).toBeTruthy()
    }
  })

  it('容器在引导层之上（z-window-controls）：教程期间按钮仍点得到', async () => {
    w.__TAVOTTO_WINDOW_CHROME__ = 'menu-button'
    await mount()
    const box = host.querySelector('[data-window-controls]')!
    expect(box.classList.contains('z-window-controls')).toBe(true)
    expect(box.classList.contains('z-overlay')).toBe(false)
  })

  it('最大化之后按钮换成「还原」，还原后换回来', async () => {
    w.__TAVOTTO_WINDOW_CHROME__ = 'menu-button'
    await mount()
    const label = () => control('toggle-maximize')!.getAttribute('aria-label')
    const before = label()
    win.isMaximized.mockResolvedValueOnce(true)
    await act(async () => win.state.resized!())
    expect(label()).not.toBe(before)
    win.isMaximized.mockResolvedValueOnce(false)
    await act(async () => win.state.resized!())
    expect(label()).toBe(before)
  })
})
