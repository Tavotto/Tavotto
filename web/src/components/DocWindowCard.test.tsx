/**
 * 同一份排版在两个窗口开着：画布中间的卡（ADR 0181）。一句话 + 主按钮 + 次按钮；两种角色文案不同、动作相同的两个入口。
 * 选择器认稳定的 `data-doc-window-*`，不认文案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const takeOverHere = vi.fn(async () => true)
const saveCopyHere = vi.fn(async () => true)
vi.mock('@/store/docWindow', async (orig) => ({
  ...(await orig<typeof import('@/store/docWindow')>()),
  takeOverHere: () => takeOverHere(),
  saveCopyHere: () => saveCopyHere(),
}))

import { DocWindowCard } from './DocWindowCard'
import { setWindowHold, useDocumentStore } from '@/store/documentStore'
import { useDocWindowStore } from '@/store/docWindow'
import { emptyProject } from '@/types/document'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

let root: Root
let host: HTMLElement
const card = () => document.querySelector<HTMLElement>('[data-doc-window-card]')
const btn = (a: string) => document.querySelector<HTMLButtonElement>(`[data-doc-window-action="${a}"]`)!

beforeEach(async () => {
  vi.clearAllMocks()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_card')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<DocWindowCard />))
})
afterEach(async () => {
  await act(async () => setWindowHold(null))
  await act(async () => root.unmount())
  host.remove()
})

describe('DocWindowCard', () => {
  it('没有编辑权问题时不摆；让出途中（yielding）也不摆', async () => {
    expect(card()).toBeNull()
    await act(async () => setWindowHold({ role: 'yielding', docId: 'd_card' }))
    expect(card()).toBeNull()
  })

  it('后开的窗口：一句话、主按钮「在这里编辑」、次按钮「存成副本」，点了各调各的', async () => {
    await act(async () => setWindowHold({ role: 'contested', docId: 'd_card' }))
    expect(card()!.dataset.docWindowRole).toBe('contested')
    expect(card()!.textContent).toContain('这份排版已在另一个窗口打开。')
    expect(btn('take').textContent).toBe('在这里编辑')
    expect(btn('take').getAttribute('data-variant')).toBe('primary')
    expect(btn('copy').textContent).toBe('存成副本')
    expect(btn('copy').getAttribute('data-variant')).not.toBe('primary')
    await act(async () => btn('take').click())
    expect(takeOverHere).toHaveBeenCalledTimes(1)
    await act(async () => btn('copy').click())
    expect(saveCopyHere).toHaveBeenCalledTimes(1)
  })

  it('被接管的窗口：「拿回来编辑」；忙的时候两个按钮都禁用', async () => {
    await act(async () => setWindowHold({ role: 'yielded', docId: 'd_card' }))
    expect(card()!.textContent).toContain('这份排版正在另一个窗口编辑。')
    expect(btn('take').textContent).toBe('拿回来编辑')
    await act(async () => useDocWindowStore.setState({ busy: true }))
    expect(btn('take').disabled).toBe(true)
    expect(btn('copy').disabled).toBe(true)
    await act(async () => useDocWindowStore.setState({ busy: false }))
  })

  it('卡上的按下不漏给画布', async () => {
    await act(async () => setWindowHold({ role: 'contested', docId: 'd_card' }))
    const seen = vi.fn()
    document.body.addEventListener('pointerdown', seen)
    await act(async () => {
      card()!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    })
    document.body.removeEventListener('pointerdown', seen)
    expect(seen).not.toHaveBeenCalled()
  })
})
