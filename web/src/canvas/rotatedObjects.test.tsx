/**
 * 旋转对象（text / arrow / shape 的任意角度 rotationDeg）的手柄与拖拽。
 *
 * 背景：x/y/w/h 恒为**未旋转**包围盒，旋转只由 ObjectView 的 CSS rotate 呈现。
 * 修之前 OverlaySvg 的选择框 / 八个手柄 / 端点圆圈全用未旋转包围盒算屏幕坐标、
 * 从不读 rotationDeg（转 90° 后手柄离真实图形约半个对角线），interactions 的
 * 缩放与端点拖拽也把屏幕位移原样喂给局部系公式。见
 * docs/audit/2026-08-17-ux-audit.md Part 2 的 critical 条目。
 *
 * 这里的正变换（局部点 → 页面上的可见位置）全部用 cos/sin 现算，不复用被测
 * 代码的 rotateVecDeg，免得公式写错时测试跟着一起错。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { anchoredRect, resizeRect, rotateVecDeg, unrotateVecDeg } from '@/lib/geometry'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import {
  emptyProject,
  rotateVec,
  type CanvasObject,
  type PanelRotation,
  type ShapeObject,
} from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { startEndpointDrag, startResizeDrag } from './interactions'

type Pt = { x: number; y: number }

/** 局部（未旋转包围盒）点 → 页面上的可见位置：绕包围盒中心顺时针转 deg */
function visible(o: { x: number; y: number; w: number; h: number }, p: Pt, deg: number): Pt {
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  const rad = (deg * Math.PI) / 180
  const c = Math.cos(rad)
  const s = Math.sin(rad)
  const dx = p.x - cx
  const dy = p.y - cy
  return { x: cx + dx * c - dy * s, y: cy + dx * s + dy * c }
}

/* ------------------------------ 测试用对象 ------------------------------- */

const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 'r1',
  type: 'shape',
  shape: 'rect',
  x: 0,
  y: 0,
  w: 40,
  h: 20,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

const diagLine = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: 'line',
  x: 0,
  y: 0,
  w: 40,
  h: 40,
  start: { rx: 0, ry: 0 },
  end: { rx: 1, ry: 1 },
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

/* ------------------------------ 指针事件桩 ------------------------------- */

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

/** jsdom 没有 PointerEvent 构造器，用同名 MouseEvent —— 监听器按事件名派发 */
const fire = (type: 'pointermove' | 'pointerup', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

/** mm → client px（zoom=1 / pan=0 / origin=0） */
const px = (mm: number) => mmToWorld(mm)

const objects = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objects().find((o) => o.id === id)!

/* ------------------------------ 渲染骨架 --------------------------------- */

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  // 关掉吸附：本文件要的是屏幕位移 → 几何的原样换算，不掺吸附修正
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null, cropTargetId: null })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rot')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const render = (node: React.ReactNode) =>
  act(() => {
    root.render(node)
  })

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 放一个对象进文档并选中它，然后画覆盖层 */
function show(obj: CanvasObject) {
  useDocumentStore.getState().commit(literal('加对象'), (d) => {
    d.objects.push(obj)
  })
  useSelectionStore.getState().set([obj.id])
  render(<OverlaySvg />)
}

/** 手柄 / 端点在屏幕上的真实落点：读 <g transform="rotate(deg cx cy)"> 现算 */
function screenPos(el: Element): Pt {
  const isCircle = el.tagName.toLowerCase() === 'circle'
  const p: Pt = isCircle
    ? { x: Number(el.getAttribute('cx')), y: Number(el.getAttribute('cy')) }
    : {
        x: Number(el.getAttribute('x')) + Number(el.getAttribute('width')) / 2,
        y: Number(el.getAttribute('y')) + Number(el.getAttribute('height')) / 2,
      }
  const g = el.closest('g[transform]')
  const tr = g?.getAttribute('transform')
  if (!tr) return p
  const [deg, cx, cy] = tr.match(/-?[\d.]+/g)!.map(Number)
  const rad = (deg * Math.PI) / 180
  const c = Math.cos(rad)
  const s = Math.sin(rad)
  return { x: cx + (p.x - cx) * c - (p.y - cy) * s, y: cy + (p.x - cx) * s + (p.y - cy) * c }
}

const handle = (dir: string) => container.querySelector(`[data-handle="${dir}"]`)!
const endpoint = (key: string) => container.querySelector(`[data-endpoint="${key}"]`)!

/* -------------------------------- 用例 ----------------------------------- */

describe('rotateVecDeg', () => {
  it('直角整数倍与既有 rotateVec 逐位一致（含负角与超过一圈）', () => {
    const quarters: PanelRotation[] = [0, 90, 180, 270]
    for (const r of quarters) {
      for (const [x, y] of [
        [1, 0],
        [0, 1],
        [3.7, -2.4],
        [-11.25, 0.01],
      ]) {
        expect(rotateVecDeg(x, y, r)).toEqual(rotateVec(x, y, r))
        expect(rotateVecDeg(x, y, r - 360)).toEqual(rotateVec(x, y, r))
        expect(rotateVecDeg(x, y, r + 720)).toEqual(rotateVec(x, y, r))
      }
    }
  })

  it('任意角度：顺时针、y 向下（与 CSS rotate 同向），逆变换往返还原', () => {
    // 顺时针 90° 把 +x 转到 +y（屏幕向下）
    const [x90, y90] = rotateVecDeg(10, 0, 90)
    expect(x90).toBeCloseTo(0, 9)
    expect(y90).toBeCloseTo(10, 9)

    const [x45, y45] = rotateVecDeg(10, 0, 45)
    expect(x45).toBeCloseTo(7.0710678, 6)
    expect(y45).toBeCloseTo(7.0710678, 6)

    const [bx, by] = unrotateVecDeg(x45, y45, 45)
    expect(bx).toBeCloseTo(10, 9)
    expect(by).toBeCloseTo(0, 9)
  })
})

describe('手柄画在真实图形上', () => {
  it('转 90°：e 手柄落在可见图形的东边中点（也就是屏幕正下方）', () => {
    const obj = rect({ rotationDeg: 90 })
    show(obj)

    // 局部东边中点 (40,10) 绕中心 (20,10) 顺时针 90° → (20,30)
    const want = visible(obj, { x: obj.x + obj.w, y: obj.y + obj.h / 2 }, 90)
    expect(want).toEqual({ x: 20, y: 30 })

    const got = screenPos(handle('e'))
    expect(got.x).toBeCloseTo(px(want.x), 6)
    expect(got.y).toBeCloseTo(px(want.y), 6)
  })

  it('转 45°：四角与四边中点逐个对上可见图形的同名点', () => {
    const obj = rect({ rotationDeg: 45 })
    show(obj)

    const local: Record<string, Pt> = {
      nw: { x: obj.x, y: obj.y },
      n: { x: obj.x + obj.w / 2, y: obj.y },
      ne: { x: obj.x + obj.w, y: obj.y },
      e: { x: obj.x + obj.w, y: obj.y + obj.h / 2 },
      se: { x: obj.x + obj.w, y: obj.y + obj.h },
      s: { x: obj.x + obj.w / 2, y: obj.y + obj.h },
      sw: { x: obj.x, y: obj.y + obj.h },
      w: { x: obj.x, y: obj.y + obj.h / 2 },
    }
    for (const [dir, p] of Object.entries(local)) {
      const want = visible(obj, p, 45)
      const got = screenPos(handle(dir))
      expect(`${dir}:${got.x.toFixed(4)},${got.y.toFixed(4)}`).toBe(
        `${dir}:${px(want.x).toFixed(4)},${px(want.y).toFixed(4)}`,
      )
    }
  })

  it('未旋转的对象不套 transform（既有渲染逐位不变）', () => {
    show(rect())
    expect(handle('e').closest('g[transform]')).toBeNull()
    const p = screenPos(handle('e'))
    expect(p.x).toBeCloseTo(px(40), 6)
    expect(p.y).toBeCloseTo(px(10), 6)
  })

  it('线状对象的端点圆圈同样跟着转', () => {
    const obj = diagLine({ rotationDeg: 90 })
    show(obj)

    const want = visible(obj, { x: obj.x + obj.w, y: obj.y + obj.h }, 90) // end = (40,40)
    const got = screenPos(endpoint('end'))
    expect(got.x).toBeCloseTo(px(want.x), 6)
    expect(got.y).toBeCloseTo(px(want.y), 6)
  })

  it('手柄光标按旋转换档：转 90° 后东手柄是竖直光标', () => {
    show(rect({ rotationDeg: 90 }))
    expect((handle('e') as SVGElement).style.cursor).toBe('ns-resize')
    expect((handle('se') as SVGElement).style.cursor).toBe('nesw-resize')
  })

  it('未旋转时光标表原样', () => {
    show(rect())
    expect((handle('e') as SVGElement).style.cursor).toBe('ew-resize')
    expect((handle('se') as SVGElement).style.cursor).toBe('nwse-resize')
  })
})

describe('anchoredRect', () => {
  it('θ=0 直接把 resizeRect 的结果原样传出（同一个对象引用，逐位一致）', () => {
    const orig = { x: 10, y: 20, w: 40, h: 20 }
    const next = resizeRect(orig, 'se', 7.3, -2.1, false)
    expect(anchoredRect(orig, next, 'se', 0)).toBe(next)
  })

  it('转 90° 拖 e：锚点（西边中点）在页面上纹丝不动', () => {
    const orig = { x: 0, y: 0, w: 40, h: 20 }
    const next = resizeRect(orig, 'e', 10, 0, false)
    const placed = anchoredRect(orig, next, 'e', 90)

    expect(placed).toEqual({ x: -5, y: 5, w: 50, h: 20 })
    const before = visible(orig, { x: orig.x, y: orig.y + orig.h / 2 }, 90)
    const after = visible(placed, { x: placed.x, y: placed.y + placed.h / 2 }, 90)
    expect(after.x).toBeCloseTo(before.x, 9)
    expect(after.y).toBeCloseTo(before.y, 9)
  })

  it('八个方向 × 三个角度：对面锚点全都不动', () => {
    const orig = { x: 12, y: -3, w: 40, h: 25 }
    const anchor = (r: typeof orig, dir: string) => ({
      x: dir.includes('e') ? r.x : dir.includes('w') ? r.x + r.w : r.x + r.w / 2,
      y: dir.includes('s') ? r.y : dir.includes('n') ? r.y + r.h : r.y + r.h / 2,
    })
    for (const deg of [30, 90, 217.5]) {
      for (const dir of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const) {
        const next = resizeRect(orig, dir, 6.5, -4.25, false)
        const placed = anchoredRect(orig, next, dir, deg)
        const before = visible(orig, anchor(orig, dir), deg)
        const after = visible(placed, anchor(placed, dir), deg)
        expect(`${dir}@${deg}:${after.x.toFixed(9)},${after.y.toFixed(9)}`).toBe(
          `${dir}@${deg}:${before.x.toFixed(9)},${before.y.toFixed(9)}`,
        )
      }
    }
  })
})

describe('拖手柄缩放旋转过的对象', () => {
  it('转 90° 拖 e 手柄向屏幕下方：局部系里变宽，不是变高', () => {
    show(rect({ rotationDeg: 90 }))
    startResizeDrag(down(0, 0), 'r1', 'e')
    fire('pointermove', 0, px(10))
    fire('pointerup', 0, px(10))

    const o = byId('r1')
    expect(o.w).toBeCloseTo(50, 6)
    expect(o.h).toBeCloseTo(20, 6)
    // 锚点（局部西边中点）的页面位置不动
    const before = visible(rect({ rotationDeg: 90 }), { x: 0, y: 10 }, 90)
    const after = visible(o, { x: o.x, y: o.y + o.h / 2 }, 90)
    expect(after.x).toBeCloseTo(before.x, 6)
    expect(after.y).toBeCloseTo(before.y, 6)
    // 拖的是「可见图形的下边」，图形就该往下长
    expect(visible(o, { x: o.x + o.w, y: o.y + o.h / 2 }, 90).y).toBeCloseTo(40, 6)
  })

  it('转 90° 拖 e 手柄向屏幕右方（垂直于该手柄）：尺寸不变', () => {
    show(rect({ rotationDeg: 90 }))
    startResizeDrag(down(0, 0), 'r1', 'e')
    fire('pointermove', px(10), 0)
    fire('pointerup', px(10), 0)

    const o = byId('r1')
    expect(o.w).toBeCloseTo(40, 6)
    expect(o.h).toBeCloseTo(20, 6)
  })

  it('θ=0 回归：与既有行为逐位一致（x/y 不动，w 加位移）', () => {
    show(rect({ x: 10, y: 20 }))
    startResizeDrag(down(0, 0), 'r1', 'e')
    fire('pointermove', px(10), px(5))
    fire('pointerup', px(10), px(5))

    const o = byId('r1')
    const want = resizeRect({ x: 10, y: 20, w: 40, h: 20 }, 'e', 10, 5, false)
    expect([o.x, o.y, o.w, o.h]).toEqual([want.x, want.y, want.w, want.h])
    expect([o.x, o.y, o.w, o.h]).toEqual([10, 20, 50, 20])
  })

  it('转 45° 拖 se 角（等比）：锚点仍是西北角，尺寸按局部位移算', () => {
    const orig = rect({ rotationDeg: 45 })
    show(orig)
    startResizeDrag(down(0, 0), 'r1', 'se')
    fire('pointermove', px(10), px(10))
    fire('pointerup', px(10), px(10))

    const o = byId('r1')
    // 屏幕位移 (10,10) 反旋转 45° → 局部 (14.142, 0)；等比后 w=54.142、h=w/2
    expect(o.w).toBeCloseTo(40 + 10 * Math.SQRT2, 6)
    expect(o.h).toBeCloseTo(o.w / 2, 6)
    const before = visible(orig, { x: orig.x, y: orig.y }, 45)
    const after = visible(o, { x: o.x, y: o.y }, 45)
    expect(after.x).toBeCloseTo(before.x, 6)
    expect(after.y).toBeCloseTo(before.y, 6)
  })
})

describe('拖旋转过的线状对象的端点', () => {
  it('转 45° 把 end 拖到屏幕某点：端点的可见位置往返一致', () => {
    const orig = diagLine({ rotationDeg: 45 })
    show(orig)
    const from = visible(orig, { x: 40, y: 40 }, 45)
    const want = { x: from.x + 10, y: from.y - 4 }

    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', px(10), px(-4))
    fire('pointerup', px(10), px(-4))

    const o = byId('s1') as ShapeObject
    const got = visible(o, { x: o.x + o.end!.rx * o.w, y: o.y + o.end!.ry * o.h }, 45)
    expect(got.x).toBeCloseTo(want.x, 6)
    expect(got.y).toBeCloseTo(want.y, 6)
  })

  it('转 90° 同理（直角分支也得跟住指针）', () => {
    const orig = diagLine({ rotationDeg: 90 })
    show(orig)
    const from = visible(orig, { x: 40, y: 40 }, 90)

    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', px(6), px(9))
    fire('pointerup', px(6), px(9))

    const o = byId('s1') as ShapeObject
    const got = visible(o, { x: o.x + o.end!.rx * o.w, y: o.y + o.end!.ry * o.h }, 90)
    expect(got.x).toBeCloseTo(from.x + 6, 6)
    expect(got.y).toBeCloseTo(from.y + 9, 6)
  })

  it('θ=0 回归：端点位移仍是屏幕位移原样（既有行为）', () => {
    show(diagLine())
    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', px(10), px(-4))
    fire('pointerup', px(10), px(-4))

    const o = byId('s1') as ShapeObject
    expect(o.x + o.end!.rx * o.w).toBeCloseTo(50, 6)
    expect(o.y + o.end!.ry * o.h).toBeCloseTo(36, 6)
  })
})
