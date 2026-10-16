/**
 * #1080：Windows 桌面版（窗框 `menu-button`）的主页左上角有「菜单」按钮，它是带气泡的 IconButton，
 * 需要 `TooltipProvider`。选择器不在工作台的 provider 里，主页与「全部项目」两个视图都要自带一层，
 * 否则主页首渲染就抛 `Tooltip must be used within TooltipProvider`，整屏掉到「界面出错了」。
 *
 * 浏览器 / macOS 都不渲染这颗按钮，所以别的用例量不到——这里把窗口伪装成 Windows 壳。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProjectPicker } from '@/components/ProjectPicker'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  const w = window as unknown as Record<string, unknown>
  w.__TAURI_INTERNALS__ = { invoke: async () => null }
  w.__TAVOTTO_WINDOW_CHROME__ = 'menu-button'
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  const w = window as unknown as Record<string, unknown>
  delete w.__TAURI_INTERNALS__
  delete w.__TAVOTTO_WINDOW_CHROME__
  vi.restoreAllMocks()
})

describe('ProjectPicker 在 Windows 桌面窗框里', () => {
  it('主页渲染得出来，且左上角有「菜单」按钮', async () => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<ProjectPicker />)
    })
    expect(host.querySelector('[data-home-variant]')).not.toBeNull()
    expect(host.querySelector('[data-app-menu-button]')).not.toBeNull()
  })

  it('「全部项目」视图也渲染得出来，且有「菜单」按钮', async () => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<ProjectPicker />)
    })
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[data-home-all]')!.click()
    })
    expect(host.querySelector('[data-home-back]')).not.toBeNull()
    expect(host.querySelector('[data-app-menu-button]')).not.toBeNull()
  })
})
