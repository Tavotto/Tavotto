/**
 * 变换面板（ADR 0185 M4 第二批）走真的 store、真的属性栏与真的快捷键：
 * 参考点决定 X / Y 报哪一点、改 W / H 以它为不动点；等比锁；缩放描边和效果；绕参考点旋转；
 * 镜像（不对称的形状换成路径、面板切 flip、文字只翻位置）；倾斜（烤进路径、文字跳过）；
 * 整组（剪切组按蒙版范围）当一个整体变换；⌥⌘D 再次变换；⌘D 重复复制和位移；剪切组的选框按蒙版范围。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { TransformSection } from '@/components/inspector/TransformSection'
import { visibleBounds } from '@/lib/transform'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import { emptyProject, type CanvasObject, type PathObject } from '@/types/document'
import { duplicateSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { makeClipMask } from './groupActions'
import { useIsolationStore } from './isolationStore'
import { useSelectionStore } from './selectionStore'
import { reflectSelection, skewSelection, transformAgain, transformSelection } from './transformActions'
import { useTransformPrefs } from './transformPrefs'
import { useUiStore } from './uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Host() {
  useKeyboard()
  return null
}
let root: Root
let host: HTMLDivElement
beforeAll(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterAll(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const rect = (id: string, x: number, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x, y: 10, w: 20, h: 10, strokePt: 1, color: '#000000', fill: '#ff0000', ...extra }) as CanvasObject
const text = (id: string, x: number): CanvasObject =>
  ({ id, type: 'text', x, y: 10, w: 20, h: 6, text: 'A', sizePt: 8, bold: false, color: '#000000', align: 'left' }) as CanvasObject
const panel = (id: string, x: number): CanvasObject =>
  ({ id, type: 'panel', fileId: 'f.pdf', fileKind: 'pdf', nativeW: 40, nativeH: 30, overrides: [], x, y: 0, w: 40, h: 30 }) as CanvasObject

const objs = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objs().find((o) => o.id === id)!
const past = () => useDocumentStore.getState().past.length

async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_tf_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useIsolationStore.getState().clear()
  useSelectionStore.getState().set(select)
}

beforeEach(() => {
  useTransformPrefs.setState({ refPoint: 'tl', lockRatio: false, scaleStrokes: false, lastOp: null, lastDup: null })
  useUiStore.setState({ tool: 'select', editingTextId: null, elementPanelId: null, cropTargetId: null })
})

/* ----------------------------------- 属性栏 ----------------------------------- */
let panelHost: HTMLDivElement
let panelRoot: Root
async function mountPanel(ids: string[]) {
  panelHost = document.createElement('div')
  document.body.appendChild(panelHost)
  panelRoot = createRoot(panelHost)
  const render = () =>
    panelRoot.render(
      <TooltipProvider>
        <TransformSection objs={objs().filter((o) => ids.includes(o.id))} />
      </TooltipProvider>,
    )
  await act(async () => render())
  return {
    rerender: () => act(async () => render()),
    unmount: () => act(async () => panelRoot.unmount()).then(() => panelHost.remove()),
  }
}
const field = (p: string) => panelHost.querySelector<HTMLInputElement>(`[data-inspector-prop="transform.${p}"]`)!
async function typeNumber(input: HTMLInputElement, value: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  })
}

describe('属性栏', () => {
  it('参考点：X / Y 报那一点，改 W 以它为不动点；九宫格是一组单选', async () => {
    await seed([rect('r', 10)], ['r'])
    const p = await mountPanel(['r'])
    expect(parseFloat(field('x').value)).toBeCloseTo(10)
    await act(async () => panelHost.querySelector<HTMLButtonElement>('[data-ref-point="r"]')!.click())
    await p.rerender()
    expect(useTransformPrefs.getState().refPoint).toBe('r')
    expect(panelHost.querySelector('[data-ref-point="r"]')!.getAttribute('aria-checked')).toBe('true')
    expect(parseFloat(field('x').value)).toBeCloseTo(30) // 右边
    expect(parseFloat(field('y').value)).toBeCloseTo(15) // 中线
    await typeNumber(field('w'), '40')
    expect(byId('r')).toMatchObject({ x: -10, w: 40 }) // 右边不动
    await p.unmount()
  })

  it('等比锁：改 W 高跟着变；缩放描边和效果：线宽跟着 √(kx·ky)', async () => {
    await seed([rect('r', 10)], ['r'])
    useTransformPrefs.setState({ lockRatio: true, scaleStrokes: true })
    const p = await mountPanel(['r'])
    await typeNumber(field('w'), '40')
    expect(byId('r')).toMatchObject({ w: 40, h: 20 })
    expect((byId('r') as { strokePt: number }).strokePt).toBeCloseTo(2)
    await p.unmount()
  })

  it('绕参考点旋转：参考点在左上，左上角不动', async () => {
    await seed([rect('r', 10)], ['r'])
    const p = await mountPanel(['r'])
    await typeNumber(field('rotation'), '90')
    const o = byId('r')
    expect(o.rotationDeg).toBe(90)
    // 旋转后左上角（框 x,y 在页面上转过去的那个点）仍在 (10, 10)
    const c = [o.x + o.w / 2, o.y + o.h / 2]
    const corner = [c[0] - (-o.h / 2), c[1] + (-o.w / 2)] // 绕中心转 90°：(-w/2,-h/2) → (h/2, -w/2)
    expect(corner[0]).toBeCloseTo(10)
    expect(corner[1]).toBeCloseTo(10)
    await p.unmount()
  })

  it('剪切组当一个整体：X / W 是蒙版的范围，改 W 整组缩放', async () => {
    const mask = rect('m', 20, { shape: 'ellipse', w: 10, h: 10, stroke: false } as Partial<CanvasObject>)
    await seed([rect('a', 0, { w: 60, h: 40, y: 0 }), mask], ['a', 'm'])
    makeClipMask()
    const ids = ['a', 'm']
    const p = await mountPanel(ids)
    expect(parseFloat(field('x').value)).toBeCloseTo(20)
    expect(parseFloat(field('w').value)).toBeCloseTo(10)
    await typeNumber(field('w'), '20')
    // 参考点左上（20, 10）不动，横向放大两倍
    expect(byId('m')).toMatchObject({ x: 20, w: 20 })
    expect(byId('a')).toMatchObject({ x: -20, w: 120 })
    await p.unmount()
  })
})

/* ----------------------------------- 动作 ----------------------------------- */
describe('镜像', () => {
  it('矩形只翻位置；三角形上下翻换成路径；面板切 flipH 且角度取反；文字只翻位置', async () => {
    await seed(
      [rect('r', 0), rect('tri', 30, { shape: 'triangle' } as Partial<CanvasObject>), text('t', 60), { ...panel('p', 90), rotation: 90 } as CanvasObject],
      ['r', 'tri', 't', 'p'],
    )
    useTransformPrefs.setState({ refPoint: 'c' })
    expect(reflectSelection('h')).toBe(true)
    // 选区范围 x ∈ [0, 130]，中线 65
    expect(byId('r').x).toBeCloseTo(110)
    expect(byId('t').x).toBeCloseTo(50)
    expect(byId('p')).toMatchObject({ flipH: true, rotation: 270 })
    expect(byId('tri').type).toBe('shape') // 左右对称，不用换
    expect(reflectSelection('v')).toBe(true)
    const tri = byId('tri') as PathObject
    expect(tri.type).toBe('path')
    // 尖朝下：顶点（比例 y 最大的那个）在中间
    const tip = tri.subpaths[0].nodes.reduce((a, n) => (n.p[1] > a.p[1] ? n : a))
    expect(tip.p[0]).toBeCloseTo(0.5)
    expect(past()).toBe(2)
  })

  it('旋转过的路径：角度取反、锚点在自身框里翻', async () => {
    const path = {
      id: 'q', type: 'path', x: 0, y: 0, w: 10, h: 10, rotationDeg: 30, strokePt: 1, color: '#000', fill: null,
      subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 0.5] }] }],
    } as unknown as CanvasObject
    await seed([path], ['q'])
    reflectSelection('h')
    const q = byId('q') as PathObject
    expect(q.rotationDeg).toBe(-30)
    expect(q.subpaths[0].nodes[0].p).toEqual([1, 0])
  })
})

describe('整体缩放', () => {
  it('非等比缩放一组：形状照拉，面板保持宽高比（取 √(kx·ky)）', async () => {
    await seed([panel('p', 0), rect('r', 50)], ['p', 'r'])
    expect(transformSelection({ kind: 'scale', kx: 4, ky: 1, rp: 'tl' })).toBe(true)
    const p = byId('p')
    expect(p.w / p.h).toBeCloseTo(40 / 30)
    expect(p.w).toBeCloseTo(80) // √4 = 2 倍
    expect(byId('r')).toMatchObject({ w: 80, h: 10 })
  })
})

describe('倾斜', () => {
  it('形状换成路径、倾斜烤进锚点；文字跳过并说一句', async () => {
    await seed([rect('r', 0), text('t', 40)], ['r', 't'])
    useTransformPrefs.setState({ refPoint: 'b' })
    expect(skewSelection(45)).toBe(true)
    const r = byId('r') as PathObject
    expect(r.type).toBe('path')
    expect(r.w).toBeGreaterThan(20) // 上边往右挪了 10
    expect(byId('t')).toMatchObject({ x: 40, type: 'text' })
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('只选了文字：不动、不进历史', async () => {
    await seed([text('t', 0)], ['t'])
    expect(skewSelection(30)).toBe(false)
    expect(past()).toBe(0)
  })
})

describe('再次变换与重复复制', () => {
  it('⌥⌘D 把上一次变换作用到现在的选区', async () => {
    await seed([rect('a', 0), rect('b', 50)], ['a'])
    transformSelection({ kind: 'move', dx: 5, dy: 0 })
    useSelectionStore.getState().set(['b'])
    const ev = new KeyboardEvent('keydown', { key: '∂', code: 'KeyD', ctrlKey: true, altKey: true, bubbles: true, cancelable: true })
    act(() => {
      window.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(true)
    expect(byId('b').x).toBe(55)
    expect(byId('a').x).toBe(5)
    // ⌥⌘D 不是 ⌘D：没有多出副本
    expect(objs()).toHaveLength(2)
    expect(transformAgain()).toBe(true)
    expect(byId('b').x).toBe(60)
  })

  it('⌘D 之后把副本挪开，再按 ⌘D：按同样的位移再复制一份', async () => {
    await seed([rect('a', 0)], ['a'])
    duplicateSelected()
    const c1 = useSelectionStore.getState().ids[0]
    expect(byId(c1).x).toBe(byId('a').x + DUPLICATE_OFFSET_MM) // 第一次照旧错开一点
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      const o = d.objects.find((x) => x.id === c1)!
      o.x = 30
      o.y = 10
    })
    duplicateSelected()
    const c2 = useSelectionStore.getState().ids[0]
    expect(byId(c2)).toMatchObject({ x: 60, y: 10 })
    duplicateSelected()
    expect(byId(useSelectionStore.getState().ids[0])).toMatchObject({ x: 90, y: 10 })
  })
})

describe('剪切组的选框', () => {
  it('看得见的范围 = 内容与蒙版外接框的交', async () => {
    const mask = rect('m', 20, { shape: 'ellipse', w: 10, h: 10, stroke: false } as Partial<CanvasObject>)
    await seed([rect('a', 0, { w: 60, h: 40, y: 0 }), mask], ['a', 'm'])
    makeClipMask()
    const b = visibleBounds(objs().filter((o) => !o.hidden), objs())!
    expect(b.x).toBeCloseTo(20)
    expect(b.w).toBeCloseTo(10)
    expect(b.y).toBeCloseTo(10)
  })
})
