/**
 * 箭头的「位置与尺寸」折叠（与文字的 T27 同一套 `foldKey` 机制）：
 *   1. 默认收起，摘要报出当前 X / Y，外观（ArrowSection）排在它之前；
 *   2. 展开后 X / Y / W / H / 旋转一条不少；
 *   3. 展开状态按键记在 inspectorPrefs，展开 / 收起不写文档、不进历史；
 *   4. 形状（W / H 是常改项）不折叠，位置与尺寸仍是平铺分组。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject } from '@/types/document'
import { Inspector } from './Inspector'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const arrow = {
  id: 'a1', type: 'arrow', x: 5, y: 30, w: 20, h: 5,
  start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 },
  strokePt: 1, color: '#111111', head: 'end',
} as unknown as CanvasObject
const shape = {
  id: 's1', type: 'shape', shape: 'rect', x: 7, y: 9, w: 20, h: 10,
  fill: '#ffffff', stroke: '#000000', strokePt: 1,
} as unknown as CanvasObject

let root: Root
let host: HTMLDivElement
const q = <T extends Element>(sel: string) => host.querySelector<T>(sel)
const all = (sel: string) => [...host.querySelectorAll(sel)]
/** 稳定锚点 `data-fold`（= foldKey），不认文案 */
const disclosure = () => q<HTMLButtonElement>('[data-fold="arrow-transform"] > button') ?? undefined
const past = () => useDocumentStore.getState().past.length

async function mount(obj: CanvasObject) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_arrow_fold')
  useDocumentStore.getState().commit(literal('加对象'), (d) => {
    d.objects.push(obj)
  })
  useUiStore.setState({ rightOpen: true, rightTab: 'properties', layout: 'wide', elementPanelId: null })
  useSelectionStore.getState().set([obj.id])
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <Inspector />
      </TooltipProvider>,
    )
  })
}

beforeEach(() => {
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('箭头：位置与尺寸折叠', () => {
  it('默认收起，摘要报出当前 X / Y；不挂载坐标输入框', async () => {
    await mount(arrow)
    const d = disclosure()!
    expect(d).toBeDefined()
    expect(d.getAttribute('aria-expanded')).toBe('false')
    expect(d.textContent).toContain('5')
    expect(d.textContent).toContain('30')
    expect(q('[data-transform-folded]')).toBeNull()
  })

  it('外观分组排在折叠行之前', async () => {
    await mount(arrow)
    const fold = q('[data-fold="arrow-transform"]')!
    expect(fold).toBeTruthy()
    // 折叠行之前至少有一个分组（箭头外观）：按文档顺序判，不认标题文案
    const before = all('[data-section]').filter(
      (s) => !fold.contains(s) && s.compareDocumentPosition(fold) & Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(before.length).toBeGreaterThan(0)
  })

  it('展开后 X / Y / W / H / 旋转一条不少；展开收起不写文档、不进历史', async () => {
    await mount(arrow)
    const before = past()
    await act(async () => disclosure()!.click())
    const body = q('[data-transform-folded]')!
    expect(body.querySelectorAll('input').length).toBeGreaterThanOrEqual(5)
    expect(body.textContent).toContain('旋转')
    expect(useInspectorPrefs.getState().moreOpen['arrow-transform']).toBe(true)
    await act(async () => disclosure()!.click())
    expect(disclosure()!.getAttribute('aria-expanded')).toBe('false')
    expect(useInspectorPrefs.getState().moreOpen['arrow-transform']).toBe(false)
    expect(past()).toBe(before)
  })
})

describe('形状：位置与尺寸不折叠', () => {
  it('仍是平铺分组，没有折叠壳', async () => {
    await mount(shape)
    expect(disclosure()).toBeUndefined()
    expect(q('[data-transform-folded]')).toBeNull()
    expect(q('[data-transform-section]')).toBeTruthy()
  })
})
