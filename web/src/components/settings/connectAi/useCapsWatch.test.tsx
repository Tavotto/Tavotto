import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiStore } from '@/store/aiStore'
import { CAPS_WATCH_MS, useCapsWatch } from './useCapsWatch'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Probe({ active, poll }: { active: boolean; poll?: boolean }) {
  useCapsWatch(active, { poll })
  return null
}

let root: Root
let host: HTMLDivElement
let loadCaps: ReturnType<typeof vi.fn>
let hidden = false

beforeEach(() => {
  vi.useFakeTimers()
  hidden = false
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') })
  loadCaps = vi.fn().mockResolvedValue(undefined)
  useAiStore.setState({ loadCaps } as never)
  host = document.createElement('div')
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  vi.useRealTimers()
})

const mount = (active: boolean, poll?: boolean) => act(async () => root.render(<Probe active={active} poll={poll} />))

describe('useCapsWatch', () => {
  it('开着时每 3 秒强刷一次；页面不可见时暂停', async () => {
    await mount(true)
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS))
    expect(loadCaps).toHaveBeenCalledTimes(1)
    expect(loadCaps).toHaveBeenCalledWith(true)
    hidden = true
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS * 3))
    expect(loadCaps).toHaveBeenCalledTimes(1)
    hidden = false
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS))
    expect(loadCaps).toHaveBeenCalledTimes(2)
  })

  it('回到前台（focus / visibilitychange）立刻刷一次', async () => {
    await mount(true)
    await act(async () => void window.dispatchEvent(new Event('focus')))
    expect(loadCaps).toHaveBeenCalledTimes(1)
    await act(async () => void vi.advanceTimersByTime(0))
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')))
    expect(loadCaps).toHaveBeenCalledTimes(2)
  })

  it('同一时刻只一个在飞的刷新', async () => {
    let resolve!: () => void
    loadCaps.mockReturnValue(new Promise<void>((r) => (resolve = r)))
    await mount(true)
    await act(async () => void window.dispatchEvent(new Event('focus')))
    await act(async () => void window.dispatchEvent(new Event('focus')))
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS))
    expect(loadCaps).toHaveBeenCalledTimes(1)
    await act(async () => resolve())
  })

  it('poll=false 只在 focus 时刷；卸载即停', async () => {
    await mount(true, false)
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS * 3))
    expect(loadCaps).not.toHaveBeenCalled()
    await act(async () => void window.dispatchEvent(new Event('focus')))
    expect(loadCaps).toHaveBeenCalledTimes(1)
    await mount(false)
    await act(async () => void vi.advanceTimersByTime(CAPS_WATCH_MS * 3))
    await act(async () => void window.dispatchEvent(new Event('focus')))
    expect(loadCaps).toHaveBeenCalledTimes(1)
  })
})
