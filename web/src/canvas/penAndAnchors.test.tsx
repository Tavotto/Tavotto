/**
 * 钢笔与直接选择在画布上的接线（ADR 0185 M1）。判据的主语：
 * - 画的过程中**文档一个字不动**（只有预览平面有草稿）；完成 = **一次** commit、一个 `path` 对象，包围盒是曲线的实际范围；
 *   撤销一次回到画之前；
 * - Enter / Esc 结束成开放路径，点起点闭合；换工具、离散动作（撤销）先把画到一半的落定；
 * - 锚点编辑：拖锚点 / 拖手柄一次拖动一条历史、没拖的锚点在页面上不动；Delete / + / − / ⇧C 走同一张命令表；
 *   Esc 退出锚点编辑；右键菜单与命令面板列的是同一组命令。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { pathToMm, rotatePoint, type XY } from '@/lib/pathEdit'
import { pathGeometry, segsExtent } from '@/lib/pathGeom'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { editAnchors, PATH_COMMANDS } from '@/store/pathActions'
import { activePathEdit, usePathEditStore } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { pathDraftPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PathObject } from '@/types/document'
import { penDrawing, penPointerDown, resetPen } from './penController'
import { startAnchorDrag, startHandleDrag } from './pathEditDrag'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* ------------------------------ 指针 / 按键桩 ------------------------------ */

const px = (mm: number) => mmToWorld(mm) // zoom 1 / pan 0 / origin 0 时 clientToMm 的逆
const down = (xMm: number, yMm: number, extra: Record<string, unknown> = {}) =>
  ({
    clientX: px(xMm),
    clientY: px(yMm),
    button: 0,
    shiftKey: false,
    altKey: false,
    preventDefault() {},
    stopPropagation() {},
    nativeEvent: new MouseEvent('pointerdown'),
    ...extra,
  }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup', xMm: number, yMm: number, init: MouseEventInit = {}) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: px(xMm), clientY: px(yMm), bubbles: true, ...init }))
const key = (k: string, init: KeyboardEventInit = {}) =>
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
  })

/** 钢笔点一下（按下 + 松手） */
const penClick = (x: number, y: number) =>
  act(() => {
    penPointerDown(down(x, y))
    fire('pointerup', x, y)
  })
/** 钢笔按下拖到 (dx, dy) 再松手 */
const penDrag = (x: number, y: number, tx: number, ty: number) =>
  act(() => {
    penPointerDown(down(x, y))
    fire('pointermove', tx, ty)
    fire('pointerup', tx, ty)
  })

const objects = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length
const onlyPath = () => objects()[0] as PathObject
const pagePoints = (o: PathObject): XY[] => pathToMm(o).flatMap((sp) => sp.nodes.map((n) => rotatePoint(o, n.p)))

function Host() {
  useKeyboard()
  return null
}
let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null, editingTextId: null, cropTargetId: null })
  useSelectionStore.getState().clear()
  usePathEditStore.getState().exit()
  resetPen()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pen_' + Math.random())
  useDocumentStore.setState({ past: [], future: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterEach(async () => {
  resetPen()
  await act(async () => root.unmount())
  host.remove()
})

describe('钢笔', () => {
  beforeEach(() => useUiStore.setState({ tool: 'pen' }))

  it('画的过程只走预览平面；Enter 完成 = 一次 commit；撤销一次回到画之前', async () => {
    await penClick(10, 10)
    await penDrag(30, 10, 35, 20) // 平滑点
    await penClick(40, 40)
    expect(objects()).toHaveLength(0)
    expect(past()).toBe(0)
    expect(pathDraftPreview()?.nodes).toHaveLength(3)
    await key('Enter')
    expect(pathDraftPreview()).toBeNull()
    expect(past()).toBe(1)
    const p = onlyPath()
    expect(p.type).toBe('path')
    expect(p.subpaths).toHaveLength(1)
    expect(p.subpaths[0].closed).toBe(false)
    expect(p.subpaths[0].nodes[1].smooth).toBe(true)
    expect(useSelectionStore.getState().ids).toEqual([p.id])
    // 包围盒 = 曲线实际范围：比例坐标里曲线正好铺满 [0,1]
    const e = segsExtent(pathGeometry(p, 1, 1, 0).fill)!
    for (const [v, want] of [[e.x0, 0], [e.y0, 0], [e.x1, 1], [e.y1, 1]]) expect(v).toBeCloseTo(want, 9)
    // 锚点落在点下去的位置（页面 mm）
    const pts = pagePoints(p)
    for (const [got, want] of [[pts[0], [10, 10]], [pts[1], [30, 10]], [pts[2], [40, 40]]] as [XY, XY][]) {
      expect(got[0]).toBeCloseTo(want[0], 6)
      expect(got[1]).toBeCloseTo(want[1], 6)
    }
    useDocumentStore.getState().undo()
    expect(objects()).toHaveLength(0)
  })

  it('点回起点：闭合并完成', async () => {
    await penClick(0, 0)
    await penClick(20, 0)
    await penClick(20, 20)
    await penClick(0.3, 0.2)
    expect(past()).toBe(1)
    expect(onlyPath().subpaths[0].closed).toBe(true)
    expect(penDrawing()).toBe(false)
  })

  it('Esc 结束成开放路径；只点了一个点就 Esc：什么都不留', async () => {
    await penClick(0, 0)
    await penClick(10, 5)
    await key('Escape')
    expect(onlyPath().subpaths[0].closed).toBe(false)
    expect(useUiStore.getState().tool).toBe('pen') // Esc 这一下只结束路径，不顺手退工具
    await penClick(50, 50)
    await key('Escape')
    expect(objects()).toHaveLength(1)
    expect(pathDraftPreview()).toBeNull()
  })

  it('换工具 / 离散动作：画到一半的先按开放路径落定，不悬在预览平面上', async () => {
    await penClick(0, 0)
    await penClick(10, 5)
    act(() => useUiStore.getState().setTool('select'))
    expect(objects()).toHaveLength(1)
    useUiStore.setState({ tool: 'pen' })
    await penClick(0, 30)
    await penClick(10, 35)
    act(() => finishActiveGesture())
    expect(objects()).toHaveLength(2)
    expect(past()).toBe(2)
  })

  it('P 键切到钢笔', async () => {
    useUiStore.setState({ tool: 'select' })
    await key('p')
    expect(useUiStore.getState().tool).toBe('pen')
  })
})

/* ------------------------------ 直接选择 ------------------------------ */

const seedPath = async (over: Partial<PathObject> = {}) => {
  const p: PathObject = {
    id: 'p1',
    type: 'path',
    x: 10,
    y: 20,
    w: 40,
    h: 30,
    strokePt: 1,
    color: '#000',
    fill: null,
    subpaths: [
      { closed: false, nodes: [{ p: [0, 1] }, { p: [0.5, 0], in: [0.25, 0], out: [0.75, 0], smooth: true }, { p: [1, 1] }] },
    ],
    ...over,
  }
  useDocumentStore.getState().commit(literal('放路径'), (d) => {
    d.objects.push(p)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set([p.id])
  return p
}

describe('直接选择（锚点编辑）', () => {
  it('Enter 进锚点编辑、Esc 退出（选区留着）', async () => {
    await seedPath()
    await key('Enter')
    expect(activePathEdit()?.path.id).toBe('p1')
    await key('Escape')
    expect(activePathEdit()).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it.each([0, 40])('拖锚点（旋转 %s°）：一次拖动一条历史，包围盒重算后没拖的锚点在页面上不动', async (deg) => {
    const base = await seedPath({ rotationDeg: deg || undefined })
    editAnchors('p1')
    const before = pagePoints(base)
    await act(() => {
      startAnchorDrag(down(30, 20), base, { sp: 0, i: 1 })
      fire('pointermove', 30, 10)
      fire('pointermove', 30, 5)
      fire('pointerup', 30, 5)
    })
    expect(past()).toBe(1)
    const after = pagePoints(onlyPath())
    for (const i of [0, 2]) {
      expect(after[i][0]).toBeCloseTo(before[i][0], 9)
      expect(after[i][1]).toBeCloseTo(before[i][1], 9)
    }
    // 拖的那个在页面上跟着指针走（旋转过的也一样：位移先转回对象的未旋转空间再用）
    expect(after[1][0]).toBeCloseTo(before[1][0], 9)
    expect(after[1][1]).toBeCloseTo(before[1][1] - 15, 9)
    expect(onlyPath().h).toBeGreaterThan(base.h)
    useDocumentStore.getState().undo()
    expect(onlyPath()).toEqual(base)
  })

  it('⇧ 点锚点 = 加选，不拖；按下不动松手 = 不进历史', async () => {
    const base = await seedPath()
    editAnchors('p1')
    await act(() => {
      startAnchorDrag(down(10, 50), base, { sp: 0, i: 0 })
      fire('pointerup', 10, 50)
    })
    await act(() => startAnchorDrag(down(50, 50, { shiftKey: true }), base, { sp: 0, i: 2 }))
    expect(usePathEditStore.getState().anchors.sort()).toEqual(['0:0', '0:2'])
    expect(past()).toBe(0)
  })

  it('拖平滑点的手柄：另一侧共线；⌥ 拆开', async () => {
    const base = await seedPath()
    editAnchors('p1')
    usePathEditStore.getState().setAnchors(['0:1'])
    await act(() => {
      startHandleDrag(down(40, 20), base, { sp: 0, i: 1 }, 'out')
      fire('pointermove', 40, 30)
      fire('pointerup', 40, 30)
    })
    const n = pathToMm(onlyPath())[0].nodes[1]
    const vin = [n.in[0] - n.p[0], n.in[1] - n.p[1]]
    const vout = [n.out[0] - n.p[0], n.out[1] - n.p[1]]
    expect(vin[0] * vout[1] - vin[1] * vout[0]).toBeCloseTo(0, 9)
    expect(n.smooth).toBe(true)
    const cur = onlyPath()
    await act(() => {
      startHandleDrag(down(40, 30), cur, { sp: 0, i: 1 }, 'out')
      fire('pointermove', 45, 25, { altKey: true })
      fire('pointerup', 45, 25, { altKey: true })
    })
    const m = pathToMm(onlyPath())[0].nodes[1]
    expect(m.smooth).toBe(false)
    expect(m.in[0]).toBeCloseTo(n.in[0], 9)
    expect(past()).toBe(2)
  })

  it('Delete 删的是选中的锚点（不是整条路径）；− 同一件事', async () => {
    await seedPath()
    editAnchors('p1')
    usePathEditStore.getState().setAnchors(['0:1'])
    await key('Delete')
    expect(onlyPath().subpaths[0].nodes).toHaveLength(2)
    usePathEditStore.getState().setAnchors(['0:0'])
    await key('-')
    // 只剩一个锚点的子路径去掉 → 整条路径没了，选区一并清掉
    expect(objects()).toHaveLength(0)
    expect(past()).toBe(2)
  })

  it('+ 加锚点（选中锚点后面那段的中点，新点被选上）；⇧C 转换锚点', async () => {
    await seedPath()
    editAnchors('p1')
    usePathEditStore.getState().setAnchors(['0:0'])
    await key('+', { shiftKey: true })
    expect(onlyPath().subpaths[0].nodes).toHaveLength(4)
    expect(usePathEditStore.getState().anchors).toEqual(['0:1'])
    await key('C', { shiftKey: true })
    expect(onlyPath().subpaths[0].nodes[1].smooth).toBeUndefined() // 曲线段中点是平滑点 → 转成尖角
    expect(past()).toBe(2)
  })

  it('不在锚点编辑里：+ / − / ⇧C 不消费、什么都不发生', async () => {
    await seedPath()
    for (const [k, init] of [['+', { shiftKey: true }], ['-', {}], ['C', { shiftKey: true }]] as const) {
      const ev = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init })
      act(() => {
        window.dispatchEvent(ev)
      })
      expect(ev.defaultPrevented).toBe(false)
    }
    expect(past()).toBe(0)
  })

  it('命令表：不在编辑时只有「钢笔 / 编辑锚点」可用，进了编辑才有加删转换', async () => {
    await seedPath()
    const avail = () => PATH_COMMANDS.filter((c) => c.available()).map((c) => c.id)
    expect(avail()).toEqual(['pen-tool', 'edit-anchors'])
    editAnchors('p1')
    expect(avail()).toEqual(['pen-tool', 'add-anchor'])
    usePathEditStore.getState().setAnchors(['0:1'])
    expect(avail()).toEqual(['pen-tool', 'add-anchor', 'delete-anchor', 'convert-anchor'])
  })
})

describe('右键菜单：与命令表同一份', () => {
  const openMenu = async () => {
    const { QuickEdit } = await import('./QuickEdit')
    const { useQuickEdit } = await import('./quickEditStore')
    const { TooltipProvider } = await import('@/components/ui/Tooltip')
    await act(async () =>
      root.render(
        <TooltipProvider>
          <Host />
          <QuickEdit />
        </TooltipProvider>,
      ),
    )
    await act(async () => useQuickEdit.getState().open({ kind: 'object', id: 'p1' }, 100, 80))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    return {
      keys: () => [...document.querySelectorAll<HTMLElement>('[data-quick-item]')].map((el) => el.dataset.quickItem),
      close: () => act(() => useQuickEdit.getState().close()),
    }
  }
  if (!('ResizeObserver' in globalThis)) {
    Object.defineProperty(globalThis, 'ResizeObserver', {
      value: class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
      configurable: true,
      writable: true,
    })
  }

  it('路径：有「编辑锚点」；进了锚点编辑、选着锚点：加 / 删 / 转换，点了就做', async () => {
    await seedPath()
    let m = await openMenu()
    expect(m.keys()).toContain('edit-anchors')
    expect(m.keys()).not.toContain('add-anchor')
    await m.close()
    editAnchors('p1')
    usePathEditStore.getState().setAnchors(['0:1'])
    m = await openMenu()
    expect(m.keys()).toEqual(expect.arrayContaining(['add-anchor', 'delete-anchor', 'convert-anchor']))
    expect(m.keys()).not.toContain('edit-anchors')
    await act(async () => document.querySelector<HTMLElement>('[data-quick-item="delete-anchor"]')!.click())
    expect(onlyPath().subpaths[0].nodes).toHaveLength(2)
  })
})
