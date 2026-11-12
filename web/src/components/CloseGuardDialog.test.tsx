/**
 * 关窗三选一的文案（QA-WIN-34）：「不保存」的真实行为是保留本机自动保存的副本（docs/acceptance/desktop-close-guard.md §2），
 * 弹窗要和它对得上，别让人以为「不保存」等于丢掉改动。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

let fire: (() => void) | undefined
vi.mock('@/lib/desktop', () => ({
  onDesktopCloseRequested: vi.fn(async (cb: () => void) => {
    fire = cb
    return () => {}
  }),
  armDesktopCloseGuard: vi.fn(async () => {}),
  resolveDesktopCloseRequest: vi.fn(async () => {}),
}))
vi.mock('@/lib/closeGuard', () => ({
  beginCloseRequest: vi.fn(async () => true),
  answerCloseRequest: vi.fn(async () => 'cancelled'),
}))

import { CloseGuardDialog } from '@/components/CloseGuardDialog'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => {
  document.body.innerHTML = ''
})

describe('CloseGuardDialog 文案', () => {
  it('弹窗里点明「不保存」时本机草稿会保留', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(<CloseGuardDialog />))
    await act(async () => fire?.())
    const dialog = document.body.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('不保存')
    expect(dialog.textContent).toContain('本机草稿也会留着')
    await act(async () => root.unmount())
  })
})
