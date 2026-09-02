/**
 * 多选时的主选标记（OverlaySvg）：末位 id 的轮廓更粗并挂 `data-primary-selection`；
 * 联合框挂 `data-multi-selection-bounds`——浮动栏与后续的新手提示锚在这两个节点上。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ArrowObject, type TextObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const text = (id: string, x: number): TextObject =>
  ({ id, type: 'text', text: id, sizePt: 10, bold: false, color: '#000', align: 'left', x, y: 20, w: 20, h: 8 }) as TextObject
const arrow = (id: string): ArrowObject =>
  ({ id, type: 'arrow', x: 100, y: 100, w: 30, h: 10, strokePt: 1, color: '#000', head: 'end' }) as ArrowObject

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ elementPanelId: null, cropTargetId: null, editingTextId: null, selectedGids: [] })
  useInteractionStore.getState().end()
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_primary')
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(text('t1', 10), text('t2', 50), arrow('a1'))
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<OverlaySvg />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

const select = (ids: string[]) => act(async () => useSelectionStore.getState().set(ids))
const primary = () => container.querySelectorAll('[data-primary-selection]')
const bounds = () => container.querySelector('[data-multi-selection-bounds]')

describe('主选标记', () => {
  it('单选：没有主选标记、没有联合框', async () => {
    await select(['t1'])
    expect(primary()).toHaveLength(0)
    expect(bounds()).toBeNull()
  })

  it('多选：末位 id 是主选，只有它一个；轮廓 2px、其余 1px；联合框挂锚点', async () => {
    await select(['t1', 't2'])
    expect(primary()).toHaveLength(1)
    expect(primary()[0].getAttribute('data-primary-selection')).toBe('t2')
    expect(primary()[0].getAttribute('stroke-width')).toBe('2')
    expect(bounds()).not.toBeNull()
    const others = [...container.querySelectorAll('rect[stroke-width="1"]')].filter(
      (r) => !r.hasAttribute('data-multi-selection-bounds'),
    )
    expect(others.length).toBeGreaterThanOrEqual(1)
  })

  it('主选跟 ids 顺序走：把 t1 提为主选，标记就换到 t1', async () => {
    await select(['t1', 't2'])
    await select(['t2', 't1'])
    expect(primary()[0].getAttribute('data-primary-selection')).toBe('t1')
  })

  it('线状对象做主选：沿线的描示带同一个锚点、更粗', async () => {
    await select(['t1', 'a1'])
    const p = primary()[0]
    expect(p.tagName.toLowerCase()).toBe('line')
    expect(p.getAttribute('data-primary-selection')).toBe('a1')
    expect(Number(p.getAttribute('stroke-width'))).toBeGreaterThan(1.5)
  })

  it('联合框的几何 = 选中对象包围盒（1mm ≈ 3.78px，取整后半像素对齐）', async () => {
    await select(['t1', 't2'])
    const r = bounds()!
    // t1.x = 10mm → 37.8px；联合宽 = 50+20-10 = 60mm → 226.8px
    expect(Number(r.getAttribute('x'))).toBeCloseTo(38.5, 0)
    expect(Number(r.getAttribute('width'))).toBeCloseTo(226, 0)
  })
})
