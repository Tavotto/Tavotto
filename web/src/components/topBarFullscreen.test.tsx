/**
 * macOS 全屏时顶栏收回给红绿灯让的那段（PR6）：
 *   * overlay 窗框、非全屏：`paddingLeft = TRAFFIC_LIGHTS_INSET`；
 *   * 全屏事件到：内联左内边距撤掉（落回类名里的 px-3）；退出全屏：恢复；
 *   * 非 overlay（浏览器 / Windows）：从来没有内联左内边距，事件也不影响。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TopBar } from '@/components/TopBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { TRAFFIC_LIGHTS_INSET } from '@/lib/desktop'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const tauri = vi.hoisted(() => ({
  handler: null as null | ((e: { payload: unknown }) => void),
  listen: vi.fn(async (_n: string, cb: (e: { payload: unknown }) => void) => {
    tauri.handler = cb
    return () => {
      tauri.handler = null
    }
  }),
  invoke: vi.fn(async (..._a: unknown[]): Promise<unknown> => false),
}))
vi.mock('@tauri-apps/api/event', () => ({ listen: tauri.listen }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: tauri.invoke }))

const w = window as unknown as Record<string, unknown>
let root: Root
let host: HTMLDivElement

async function mountTopBar() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <TopBar />
      </TooltipProvider>,
    )
  })
}
const bar = () => host.querySelector<HTMLElement>('[data-topbar]')!

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fs')
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  delete w.__TAURI_INTERNALS__
  delete w.__TAVOTTO_WINDOW_CHROME__
  tauri.handler = null
  vi.clearAllMocks()
})

describe('顶栏左侧让位随 macOS 全屏收放', () => {
  it('overlay：非全屏让 88；全屏事件到撤掉；退出全屏恢复', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_WINDOW_CHROME__ = 'overlay'
    await mountTopBar()
    expect(bar().style.paddingLeft).toBe(`${TRAFFIC_LIGHTS_INSET}px`)
    await act(async () => tauri.handler!({ payload: { fullscreen: true } }))
    expect(bar().style.paddingLeft).toBe('')
    await act(async () => tauri.handler!({ payload: { fullscreen: false } }))
    expect(bar().style.paddingLeft).toBe(`${TRAFFIC_LIGHTS_INSET}px`)
  })

  it('过渡只在进入方向：退出（false）时没有 padding 过渡类，占位立即恢复', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_WINDOW_CHROME__ = 'overlay'
    await mountTopBar()
    expect(bar().className).not.toContain('transition-[padding-left]')
    await act(async () => tauri.handler!({ payload: { fullscreen: true } }))
    expect(bar().className).toContain('transition-[padding-left]')
    await act(async () => tauri.handler!({ payload: { fullscreen: false } }))
    expect(bar().className).not.toContain('transition-[padding-left]')
    expect(bar().style.paddingLeft).toBe(`${TRAFFIC_LIGHTS_INSET}px`)
  })

  it('overlay：挂载时已在全屏（回读为真）也不让位', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_WINDOW_CHROME__ = 'overlay'
    tauri.invoke.mockResolvedValue(true)
    await mountTopBar()
    expect(bar().style.paddingLeft).toBe('')
  })

  it('非 overlay（Windows menu-button / 浏览器）：没有内联左内边距，也不订阅', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_WINDOW_CHROME__ = 'menu-button'
    await mountTopBar()
    expect(bar().style.paddingLeft).toBe('')
    expect(tauri.listen).not.toHaveBeenCalled()
  })
})
