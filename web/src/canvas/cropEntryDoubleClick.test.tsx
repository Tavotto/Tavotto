/**
 * 进裁剪的入口只有一条：`actions.beginCrop`（web/AGENTS.md「撤销防线与裁剪」）。
 *
 * 画布上**双击**普通面板是其中一个入口，而它一度直接调 `setCropTarget(id)`
 * ——那条路不拍基线（`cropBaseline` 留在 null），于是这一轮裁完之后
 * 「取消」/ Esc 只是退出裁剪态，用户拖出来的取景窗留在原地不还原。
 *
 * 所以这里的判据不是「双击之后调了 beginCrop」（那只量到了实现），而是
 * **双击 → 拖出新取景窗 → 取消 → 回到双击那一刻**。面板一开始就带着一份
 * 旧裁剪，用来把「还原到进来那一刻」与「重置裁剪」区分开：还原后必须是
 * 那份旧裁剪，不是 undefined。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { cancelCrop } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ObjectView } from './ObjectView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

/** 双击那一刻的取景窗：已经裁过一轮的普通面板（没有 script，没有旋转） */
const ENTRY_CROP = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 10,
    y: 10,
    w: 64,
    h: 48,
    crop: { ...ENTRY_CROP },
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let mountEl: HTMLDivElement

const live = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject

/** 模拟用户在裁剪态里拖手柄拖出的一轮新取景（与 startCropDrag 写的是同一批字段） */
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

const doubleClickPanel = () =>
  act(async () => {
    const el = document.querySelector<HTMLElement>('[data-object-id="p1"]')
    if (!el) throw new Error('画布上没有这个对象，用例的前提就没成立')
    el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }))
  })

beforeEach(async () => {
  document.body.innerHTML = ''
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    cropBaseline: null,
    tool: 'select',
  })
  useInteractionStore.getState().end()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, spaceDown: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_crop_dblclick')
  useDocumentStore.getState().commit(literal('放面板'), (d) => {
    d.objects.push(panelOf())
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(['p1'])
  mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(<ObjectView obj={live()} />)
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  mountEl.remove()
})

it('双击进裁剪 → 改取景窗 → 取消：回到双击那一刻的取景窗', async () => {
  await doubleClickPanel()
  expect(useUiStore.getState().cropTargetId).toBe('p1')

  await dragCropTo({ x: 0.3, y: 0.25, w: 0.4, h: 0.5 })
  expect(live().crop).toEqual({ x: 0.3, y: 0.25, w: 0.4, h: 0.5 })

  await act(async () => cancelCrop())

  expect(useUiStore.getState().cropTargetId).toBeNull()
  // 还原到**双击那一刻**：既不是拖完的那一份，也不是「从没裁剪过」
  expect(live().crop).toEqual(ENTRY_CROP)
  expect(live().w).toBe(64)
  expect(live().h).toBe(48)
})

it('双击那一刻就拍下基线：基线属于被双击的那个面板', async () => {
  await doubleClickPanel()
  expect(useUiStore.getState().cropBaseline).toEqual({
    id: 'p1',
    crop: ENTRY_CROP,
    x: 10,
    y: 10,
    w: 64,
    h: 48,
  })
})
