/**
 * 路径文字（ADR 0190）的浮动栏让开字：离线以后字常伸出路径的框，浮动栏按「框 ∪ 字」的范围落位，不压在字上。
 * jsdom 没有布局：框与字的屏幕矩形打桩给。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { usePathTextGlyphStore } from '@/store/pathTextGlyphStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PathTextObject } from '@/types/document'
import { ContextBar } from './ContextBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const BOX = { left: 300, top: 400, width: 200, height: 80 }
const GLYPHS = { left: 290, top: 330, width: 220, height: 40 }
const BAR = { w: 120, h: 32 }
const rect = (b: { left: number; top: number; width: number; height: number }) =>
  ({ ...b, x: b.left, y: b.top, right: b.left + b.width, bottom: b.top + b.height, toJSON: () => b }) as DOMRect
const ZERO = { left: 0, top: 0, width: 0, height: 0 }

const original = Element.prototype.getBoundingClientRect
const ow = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
const oh = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')
let root: Root
let glyphsDrawn = true

const pt: PathTextObject = {
  id: 'pt', type: 'pathText', x: 10, y: 20, w: 60, h: 20, text: 'Label', sizePt: 10, bold: false, color: '#000000',
  subpaths: [{ closed: false, nodes: [{ p: [0, 1] }, { p: [1, 1] }] }], baselineOffsetPt: 17,
}

beforeEach(async () => {
  document.body.innerHTML = ''
  Element.prototype.getBoundingClientRect = function () {
    if (this.hasAttribute('data-object-id')) return rect(BOX)
    if (this.hasAttribute('data-path-text-glyphs')) return rect(glyphsDrawn ? GLYPHS : ZERO)
    return rect(ZERO)
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return this.hasAttribute('data-context-bar') ? BAR.w : 0 } })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return this.hasAttribute('data-context-bar') ? BAR.h : 0 } })
  useUiStore.setState({ elementPanelId: null, selectedGids: [], editingTextId: null, cropTargetId: null, tool: 'select', layout: 'wide', leftOpen: false, rightOpen: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ptbar')
  useDocumentStore.getState().commit(literal('放'), (d) => void d.objects.push(pt))
  // 画布上的节点：对象框 + 里面画好的字
  const node = document.createElement('div')
  node.setAttribute('data-object-id', 'pt')
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  g.setAttribute('data-path-text-glyphs', '')
  svg.appendChild(g)
  node.appendChild(svg)
  document.body.appendChild(node)
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => root.render(<TooltipProvider><ContextBar /></TooltipProvider>))
})

afterEach(async () => {
  await act(async () => root.unmount())
  Element.prototype.getBoundingClientRect = original
  if (ow) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', ow)
  if (oh) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', oh)
  useSelectionStore.getState().clear()
  glyphsDrawn = true
  document.body.innerHTML = ''
})

const bar = () => document.querySelector<HTMLElement>('[data-context-bar]')

describe('路径文字的浮动栏', () => {
  it('停在字的上方，不压在伸出框外的字上；字画完（范围变了）会重新让开', async () => {
    glyphsDrawn = false
    await act(async () => useSelectionStore.getState().set(['pt']))
    await act(async () => new Promise((r) => setTimeout(r, 0)))
    const before = parseFloat(bar()!.style.top)
    expect(before + BAR.h).toBeLessThanOrEqual(BOX.top)
    expect(before + BAR.h).toBeGreaterThan(GLYPHS.top) // 字还没画出来时按框放：会压在字上
    glyphsDrawn = true
    await act(async () => usePathTextGlyphStore.getState().setBounds('pt', { x: -1, y: -25, w: 62, h: 12 }))
    await act(async () => new Promise((r) => setTimeout(r, 0)))
    expect(parseFloat(bar()!.style.top) + BAR.h).toBeLessThanOrEqual(GLYPHS.top)
  })
})
