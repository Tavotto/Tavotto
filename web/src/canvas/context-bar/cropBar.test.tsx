/**
 * 裁剪态的「完成 / 取消」（审计 T26）：
 *   1. 进裁剪时选区旁出现完成 / 取消 / 重置裁剪，别的浮动条让位；
 *   2. 取消还原到**进裁剪那一刻**（不是还原到「从没裁剪过」），一条历史；
 *   3. Enter = 完成、Esc = 取消，与按钮调的是同一对 action；
 *   4. 没经 `beginCrop` 进来的裁剪态没有基线：取消降级成单纯退出，不改文档。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useKeyboard } from '@/hooks/useKeyboard'
import { beginCrop, cancelCrop } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { useQuickEdit } from '../quickEditStore'
import { ContextBar } from './ContextBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 10,
    y: 10,
    w: 80,
    h: 60,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    overrides: [],
  }) as unknown as PanelObject

/** Enter / Esc 走的是全局键盘层，不是工具条自己的监听——所以得把它一起挂上 */
function Keys() {
  useKeyboard()
  return null
}

let root: Root
let mountEl: HTMLDivElement

const bar = () => document.querySelector<HTMLElement>('[data-context-bar]')
const action = (name: string) =>
  document.querySelector<HTMLButtonElement>(`[data-crop-action="${name}"]`)
const live = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
const past = () => useDocumentStore.getState().past
const key = (k: string) =>
  act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
  })

/** 模拟用户在画布上拖出来的一轮裁剪（与 startCropDrag 写的是同一批字段） */
const dragCropTo = (crop: { x: number; y: number; w: number; h: number }) =>
  act(async () => {
    useDocumentStore.getState().commit(literal('调整裁剪'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1')
      if (o?.type !== 'panel') return
      o.crop = crop
      o.w = 80 * crop.w
      o.h = 60 * crop.h
    })
  })

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true, writable: true })
  Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true, writable: true })
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    cropBaseline: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
    exportOpen: false,
    settingsOpen: false,
    confirm: null,
  })
  useQuickEdit.getState().close()
  useInteractionStore.getState().end()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cropbar')
  useDocumentStore.getState().commit(literal('放面板'), (d) => {
    d.objects.push(panelOf())
  })
  useDocumentStore.setState({ past: [], future: [] })
  const anchor = document.createElement('div')
  anchor.setAttribute('data-object-id', 'p1')
  document.body.appendChild(anchor)
  useSelectionStore.getState().set(['p1'])
  mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <Keys />
        <ContextBar />
      </TooltipProvider>,
    )
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  useSelectionStore.getState().clear()
  document.body.innerHTML = ''
})

describe('裁剪态：完成 / 取消就在选区旁边', () => {
  it('进裁剪后浮动条换成裁剪条，单选那套快捷属性让位', async () => {
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('object')
    await act(async () => beginCrop('p1'))
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('crop')
    expect(action('done')).not.toBeNull()
    expect(action('cancel')).not.toBeNull()
    expect(action('reset')).not.toBeNull()
    // 裁剪不是「属性」上下文：单选栏的裁剪 / 适应按钮不该同时在场
    expect(bar()!.querySelector('[aria-label="裁剪"]')).toBeNull()
  })

  it('cropTargetId 指不到面板时谁都不出（旧的整条让位规则没变）', async () => {
    await act(async () => useUiStore.getState().setCropTarget('nope'))
    expect(bar()).toBeNull()
  })

  it('完成：保留当前取景，退出裁剪态，不额外进历史', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    const depth = past().length
    await act(async () => action('done')!.click())
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(live().crop).toEqual({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    expect(past()).toHaveLength(depth)
  })

  it('取消：还原到进裁剪那一刻，连包围盒一起还', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    expect(live().w).toBe(40)
    await act(async () => action('cancel')!.click())
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(live().crop).toBeUndefined()
    expect(live().w).toBe(80)
    expect(live().h).toBe(60)
  })

  it('取消还的是「进来那一刻」，不是「从没裁剪过」', async () => {
    // 先有一份旧裁剪，再进裁剪、再改一轮
    await dragCropTo({ x: 0, y: 0, w: 0.8, h: 0.8 })
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.3, y: 0.3, w: 0.4, h: 0.4 })
    await act(async () => action('cancel')!.click())
    expect(live().crop).toEqual({ x: 0, y: 0, w: 0.8, h: 0.8 })
  })

  it('没动过就取消：不进历史（一条空的「取消裁剪」比没有更坏）', async () => {
    await act(async () => beginCrop('p1'))
    const depth = past().length
    await act(async () => action('cancel')!.click())
    expect(past()).toHaveLength(depth)
  })

  it('取消是可撤销的一条历史', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    const depth = past().length
    await act(async () => action('cancel')!.click())
    expect(past()).toHaveLength(depth + 1)
    await act(async () => useDocumentStore.getState().undo())
    expect(live().crop).toEqual({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
  })
})

describe('键盘：Enter = 完成、Esc = 取消', () => {
  it('Esc 还原并退出——与按钮同一个 action', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    await key('Escape')
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(live().crop).toBeUndefined()
  })

  it('Enter 保留并退出', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    await key('Enter')
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(live().crop).toEqual({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
  })
})

describe('基线只在它还对得上的时候用', () => {
  it('不经 beginCrop 进来的裁剪态没有基线：取消只退出，不改文档', async () => {
    await act(async () => useUiStore.getState().setCropTarget('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    await act(async () => cancelCrop())
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(live().crop).toEqual({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
  })

  it('基线属于别的对象时不拿它去改这一个', async () => {
    await act(async () => beginCrop('p1'))
    await dragCropTo({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
    // 基线还是 p1 的，但此刻裁的换成了别的 id
    await act(async () => useUiStore.setState({ cropTargetId: 'other' }))
    await act(async () => cancelCrop())
    expect(live().crop).toEqual({ x: 0.1, y: 0.1, w: 0.5, h: 0.5 })
  })
})
