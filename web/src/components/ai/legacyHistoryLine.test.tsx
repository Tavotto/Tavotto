/**
 * 任务历史底部「找回之前的任务」（#2282 补救 B）：没有可找回的不出现；点「找回」原地确认（不弹全局确认框）；
 * 确认后才真的调后端；找回中不留按钮；做完这一行消失并刷新列表；没做成给「再试」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAiHistoryLegacy, restoreAiHistoryLegacy } from '@/lib/api'
import { LegacyHistoryLine } from './LegacyHistoryLine'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiHistoryLegacy: vi.fn(),
  restoreAiHistoryLegacy: vi.fn(),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchMock = vi.mocked(fetchAiHistoryLegacy)
const restoreMock = vi.mocked(restoreAiHistoryLegacy)
let host: HTMLDivElement
let root: Root

async function mount(onRestored = vi.fn()) {
  await act(async () => {
    root.render(<LegacyHistoryLine pj="pj-1" onRestored={onRestored} />)
  })
  return onRestored
}
const line = () => host.querySelector<HTMLElement>('[data-ai-history-legacy]')
const click = async (sel: string) => {
  await act(async () => {
    host.querySelector<HTMLButtonElement>(sel)!.click()
  })
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  fetchMock.mockReset()
  restoreMock.mockReset()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('LegacyHistoryLine', () => {
  it('nothing to bring back → no line at all', async () => {
    fetchMock.mockResolvedValue({ missing: 0 })
    await mount()
    expect(fetchMock).toHaveBeenCalledWith('pj-1')
    expect(line()).toBeNull()
  })

  it('a failed count also hides the line instead of showing an error', async () => {
    fetchMock.mockRejectedValue(new Error('boom'))
    await mount()
    expect(line()).toBeNull()
  })

  it('asks in place first, restores only after confirming, then disappears and refreshes', async () => {
    fetchMock.mockResolvedValue({ missing: 12 })
    let finish!: (v: { outcome: 'done'; missing: number }) => void
    restoreMock.mockReturnValue(new Promise((r) => (finish = r)))
    const onRestored = await mount()
    expect(line()?.dataset.aiHistoryLegacy).toBe('idle')
    expect(line()?.textContent).toContain('12')

    await click('[data-ai-history-legacy-restore]')
    expect(line()?.dataset.aiHistoryLegacy).toBe('confirm')
    expect(restoreMock).not.toHaveBeenCalled()
    await click('[data-confirm-cancel]')
    expect(line()?.dataset.aiHistoryLegacy).toBe('idle')

    await click('[data-ai-history-legacy-restore]')
    await click('[data-confirm-ok]')
    expect(restoreMock).toHaveBeenCalledWith('pj-1')
    expect(line()?.dataset.aiHistoryLegacy).toBe('busy')
    expect(line()?.querySelector('button')).toBeNull()

    await act(async () => finish({ outcome: 'done', missing: 0 }))
    expect(line()).toBeNull()
    expect(onRestored).toHaveBeenCalledTimes(1)
  })

  it('not done (busy elsewhere / failed) → says so and offers a retry that restores directly', async () => {
    fetchMock.mockResolvedValue({ missing: 3 })
    restoreMock.mockResolvedValueOnce({ outcome: 'deferred', missing: 3 })
    restoreMock.mockResolvedValueOnce({ outcome: 'done', missing: 0 })
    const onRestored = await mount()
    await click('[data-ai-history-legacy-restore]')
    await click('[data-confirm-ok]')
    expect(line()?.dataset.aiHistoryLegacy).toBe('failed')
    expect(onRestored).not.toHaveBeenCalled()
    await click('[data-ai-history-legacy-restore]')
    expect(restoreMock).toHaveBeenCalledTimes(2)
    expect(line()).toBeNull()
    expect(onRestored).toHaveBeenCalledTimes(1)
  })
})
