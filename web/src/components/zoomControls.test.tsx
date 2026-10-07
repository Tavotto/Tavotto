/**
 * 缩放菜单（2026-10-07 设计审计 §10.1）：顶上一格可以直接敲倍率；「缩放到选中」（⇧2）没有选中时置灰、
 * 有选中时把选区的包围盒放进视野（`fitRectAnimated`，与「适应画布」同一条补间）。
 * 主语：认 `data-zoom-menu` / `data-zoom-value-row` / `data-zoom-selection-item`；动作认 viewport store 的调用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ZoomControls } from './ZoomControls'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type TextObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const text: TextObject = {
  id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 10, y: 20, w: 30, h: 8,
}

let root: Root
let host: HTMLDivElement

const open = async () => {
  const trigger = host.querySelector<HTMLElement>('[data-zoom-menu]')!
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    await new Promise((r) => setTimeout(r, 0))
  })
}
const item = () => document.querySelector<HTMLElement>('[data-zoom-selection-item]')!

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_zoom')
  useDocumentStore.getState().silent((d) => {
    d.objects.push(text)
  })
  useSelectionStore.setState({ ids: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<ZoomControls />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('缩放菜单', () => {
  it('顶上有一格倍率输入', async () => {
    await open()
    expect(document.querySelector('[data-zoom-value-row] input')).not.toBeNull()
  })

  it('没有选中：「缩放到选中」置灰', async () => {
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(true)
  })

  it('有选中：把选区包围盒交给 fitRectAnimated', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useSelectionStore.setState({ ids: ['t1'] }))
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(false)
    await act(async () => item().click())
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 30, h: 8 })
  })
})
