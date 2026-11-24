/**
 * ADR 0185 M6 补全：关键对象（多选后再点一下其中一个）、按指定间距分布以关键对象为准、阵列（网格 / 环形）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ObjectView } from '@/canvas/ObjectView'
import { ArrangeSection } from '@/components/inspector/ArrangeSection'
import { emptyProject, type CanvasObject } from '@/types/document'
import { alignSelectedTo, duplicateSelected, setSelectionSpacing } from './actions'
import { useArrangeStore } from './arrangeStore'
import { useDocumentStore } from './documentStore'
import { useInspectorPrefs } from './inspectorPrefs'
import { keyObjectId } from './keyObject'
import { useSelectionStore } from './selectionStore'
import { arraySelection } from './transformActions'
import { useTransformPrefs } from './transformPrefs'
import { useUiStore } from './uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const rect = (id: string, x: number, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x, y: 10, w: 10, h: 5, strokePt: 1, color: '#000000', fill: '#ff0000', ...extra }) as CanvasObject
const objs = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objs().find((o) => o.id === id)!
const past = () => useDocumentStore.getState().past.length

async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_m6_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(select)
}

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  useArrangeStore.setState({ alignRef: 'selection' })
  useTransformPrefs.setState({ lastDup: null, lastOp: null })
  useUiStore.setState({ tool: 'select', elementPanelId: null, editingTextId: null, cropTargetId: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const down = (el: Element, x = 5, y = 5) =>
  act(() => {
    el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: x, clientY: y }))
  })
const up = (x = 5, y = 5) =>
  act(() => {
    window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: x, clientY: y }))
  })

describe('关键对象', () => {
  it('多选后再点一下（不拖）：成为关键对象，参照切到主选；再点一下取消；拖动不算点', async () => {
    await seed([rect('a', 0), rect('b', 30), rect('c', 60)], ['a', 'b', 'c'])
    await act(async () => root.render(<>{objs().map((o) => <ObjectView key={o.id} obj={o} />)}</>))
    const b = host.querySelector('[data-object-id="b"]')!
    down(b)
    up()
    expect(keyObjectId()).toBe('b')
    expect(useSelectionStore.getState().ids).toEqual(['a', 'c', 'b'])
    // 再点一下关键对象：取消
    down(b)
    up()
    expect(keyObjectId()).toBeNull()
    // 拖动（松开离按下 > 3 px）：不设关键对象
    down(host.querySelector('[data-object-id="a"]')!, 5, 5)
    up(40, 5)
    expect(keyObjectId()).toBeNull()
  })

  it('对齐以关键对象为准、它自己不动', async () => {
    await seed([rect('a', 0, { y: 0 }), rect('b', 30, { y: 20 }), rect('c', 60, { y: 40 })], ['a', 'c', 'b'])
    useArrangeStore.setState({ alignRef: 'primary' })
    alignSelectedTo('top', useArrangeStore.getState().alignRef)
    expect([byId('a').y, byId('b').y, byId('c').y]).toEqual([20, 20, 20])
  })

  it('按指定间距分布：有关键对象时它不动，其余排在它两边；没有就是最左边那个不动', async () => {
    await seed([rect('a', 0), rect('b', 30), rect('c', 60)], ['a', 'c', 'b'])
    useArrangeStore.setState({ alignRef: 'primary' })
    setSelectionSpacing('x', 2)
    expect([byId('a').x, byId('b').x, byId('c').x]).toEqual([18, 30, 42])
    useArrangeStore.setState({ alignRef: 'selection' })
    setSelectionSpacing('x', 5)
    expect([byId('a').x, byId('b').x, byId('c').x]).toEqual([18, 33, 48])
  })
})

describe('阵列', () => {
  it('网格 2×3：一次 commit、追加 5 份在最上面、选中原件和副本；之后 ⌘D 照旧从头错开', async () => {
    await seed([rect('a', 0)], ['a'])
    expect(arraySelection({ kind: 'grid', rows: 2, cols: 3, gapX: 2, gapY: 1 })).toBe(true)
    expect(objs()).toHaveLength(6)
    expect(past()).toBe(1)
    expect(objs().map((o) => [o.x, o.y])).toEqual([[0, 10], [12, 10], [24, 10], [0, 16], [12, 16], [24, 16]])
    expect(useSelectionStore.getState().ids).toHaveLength(6)
    useSelectionStore.getState().set(['a'])
    duplicateSelected()
    expect(objs().at(-1)!.x).toBe(4)
  })

  it('组按单元补齐：点中组里一个，整组一起阵列', async () => {
    await seed([rect('a', 0, { groupId: 'G' }), rect('b', 12, { groupId: 'G' })], ['a'])
    arraySelection({ kind: 'grid', rows: 1, cols: 2, gapX: 1, gapY: 0 })
    expect(objs()).toHaveLength(4)
  })

  it('超过上限不做、说一句；份数不到 2 不做', async () => {
    await seed([rect('a', 0)], ['a'])
    expect(arraySelection({ kind: 'grid', rows: 30, cols: 30, gapX: 0, gapY: 0 })).toBe(false)
    expect(useUiStore.getState().status).toBeTruthy()
    expect(arraySelection({ kind: 'radial', count: 1, radius: 5, rotate: true })).toBe(false)
    expect(past()).toBe(0)
  })

  it('属性栏「阵列」：切到环形、填份数、点生成', async () => {
    await seed([rect('a', 0)], ['a'])
    useInspectorPrefs.setState({ foldOpen: { 'arrange:single:array': true } })
    await act(async () =>
      root.render(
        <TooltipProvider>
          <ArrangeSection count={1} />
        </TooltipProvider>,
      ),
    )
    const radial = [...host.querySelectorAll('[role="radio"]')].find((r) => r.textContent === '环形') as HTMLElement
    await act(async () => radial.click())
    const btn = host.querySelector<HTMLButtonElement>('[data-array-make]')!
    expect(btn.textContent).toContain('5')
    await act(async () => btn.click())
    expect(objs()).toHaveLength(6)
  })
})
