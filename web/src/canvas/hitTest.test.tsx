/**
 * 画布命中：细长线状对象（箭头 / 直线形状）的沿线段透明命中线。
 *
 * 背景：修之前每个对象的命中区严格等于**未旋转包围盒**，而水平箭头 / 直线的 h
 * 被钳到 0.01mm（≈0.03 屏幕 px），鼠标怎么点都点不中；斜箭头反过来整个矩形包围盒
 * 吃点击、误伤下层面板。现在命中交给一条 stroke="transparent" 的线，外层 div 让位。
 *
 * jsdom 说明：jsdom 完全不做 CSS 命中测试（`pointer-events` 只是个字符串属性），
 * 所以这里断言的是**结构与数值**——命中线的宽度换算、pointerEvents 取值、以及
 * 事件从命中线冒泡到外层 div 的 handler 这条链路。真实浏览器里
 * 「祖先 pointer-events:none + 后代 stroke 仍可命中」那一半由审计的 Playwright
 * 实测背书（docs/audit/2026-08-17-ux-audit.md 缺陷 2a），单测这层复现不了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HIT_PX, cornerRadius, hitStrokeWidth } from '@/lib/shapeGeometry'
import { MIN_ZOOM, MAX_ZOOM, useViewportStore } from '@/store/viewportStore'
import { useUiStore } from '@/store/uiStore'
import type { ArrowObject, ShapeObject, TextObject } from '@/types/document'
import { ObjectView } from './ObjectView'
import { useQuickEdit } from './quickEditStore'

const ZOOMS = [MIN_ZOOM, 0.5, 1, 1.75, 3, MAX_ZOOM]

/** 水平箭头：拖拽画出来的真实形状——h 被 interactions 钳到 0.01mm */
const horizontalArrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a1',
  type: 'arrow',
  x: 10,
  y: 20,
  w: 40,
  h: 0.01,
  start: { rx: 0, ry: 0.5 },
  end: { rx: 1, ry: 0.5 },
  strokePt: 1,
  color: '#111111',
  head: 'end',
  ...over,
})

const lineShape = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: 'line',
  x: 10,
  y: 20,
  w: 40,
  h: 0.01,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

const textObj: TextObject = {
  id: 't1',
  type: 'text',
  x: 0,
  y: 0,
  w: 30,
  h: 8,
  text: 'abc',
  sizePt: 9,
  color: '#111111',
  align: 'left',
  bold: false,
  italic: false,
}

// TextView 用 ResizeObserver 量自适应字号，jsdom 没有；这里只关心它的命中属性
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  useViewportStore.setState({ zoom: 1, spaceDown: false })
  useUiStore.setState({ tool: 'select', editingTextId: null, cropTargetId: null })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

function render(node: React.ReactNode) {
  act(() => {
    root.render(node)
  })
}

const hitLine = () => container.querySelector('[data-hit-line]') as SVGLineElement | null
const objectDiv = () => container.querySelector('[data-object-id]') as HTMLDivElement

describe('hitStrokeWidth 换算', () => {
  it('世界层是 scale(zoom)，命中带在任何缩放下都不窄于 HIT_PX 屏幕像素', () => {
    for (const zoom of ZOOMS) {
      // 本层宽度 × zoom = 屏幕 CSS px
      expect(hitStrokeWidth(0.1, zoom) * zoom).toBeGreaterThanOrEqual(HIT_PX - 1e-9)
    }
  })

  it('可见线宽 / 箭头帽全宽更大时以它们为准（看得见就点得中）', () => {
    expect(hitStrokeWidth(100, 1)).toBe(100)
    expect(hitStrokeWidth(1, 8, 40)).toBe(40)
  })

  it('zoom 为 0 也不炸（fit 动画首帧、视口未上报尺寸时可能出现）', () => {
    expect(Number.isFinite(hitStrokeWidth(1, 0))).toBe(true)
  })
})

describe('箭头的命中线', () => {
  it('各缩放级别下渲染出的命中线都覆盖 ≥8 屏幕像素', () => {
    for (const zoom of ZOOMS) {
      useViewportStore.setState({ zoom })
      render(<ObjectView obj={horizontalArrow()} />)
      const sw = Number(hitLine()!.getAttribute('stroke-width'))
      expect(sw * zoom).toBeGreaterThanOrEqual(HIT_PX - 1e-9)
    }
  })

  it('命中线画在未回缩的 a→b 上并用 round 线帽，两端箭头帽都在带内', () => {
    render(<ObjectView obj={horizontalArrow()} />)
    const line = hitLine()!
    // start.rx=0 / end.rx=1 → 端点就是包围盒左右边，可见描边则为 triangle 端回缩后的
    expect(Number(line.getAttribute('x1'))).toBe(0)
    expect(Number(line.getAttribute('x2'))).toBeGreaterThan(0)
    expect(line.getAttribute('stroke')).toBe('transparent')
    expect(line.getAttribute('stroke-linecap')).toBe('round')
  })

  it('未锁定 + 选择工具：命中线吃 stroke，外层包围盒 div 让位', () => {
    render(<ObjectView obj={horizontalArrow()} />)
    expect(hitLine()!.style.pointerEvents).toBe('stroke')
    expect(objectDiv().style.pointerEvents).toBe('none')
  })

  it('locked：命中线与外层 div 都是 none（锁定对象不得重新变得可点选）', () => {
    render(<ObjectView obj={horizontalArrow({ locked: true })} />)
    expect(hitLine()!.style.pointerEvents).toBe('none')
    expect(objectDiv().style.pointerEvents).toBe('none')
  })

  it('绘制工具激活 / 空格平移时命中线也要 none —— 显式值不吃世界层的继承', () => {
    useUiStore.setState({ tool: 'rect' })
    render(<ObjectView obj={horizontalArrow()} />)
    expect(hitLine()!.style.pointerEvents).toBe('none')

    useUiStore.setState({ tool: 'select' })
    useViewportStore.setState({ spaceDown: true })
    render(<ObjectView obj={horizontalArrow()} />)
    expect(hitLine()!.style.pointerEvents).toBe('none')
  })
})

describe('直线形状的命中线', () => {
  it('line 分支有命中线，外层 div 让位', () => {
    render(<ObjectView obj={lineShape()} />)
    expect(hitLine()).not.toBeNull()
    expect(hitLine()!.style.pointerEvents).toBe('stroke')
    expect(objectDiv().style.pointerEvents).toBe('none')
  })

  it('缩小到最小档也有 ≥8 屏幕像素的命中带', () => {
    useViewportStore.setState({ zoom: MIN_ZOOM })
    render(<ObjectView obj={lineShape()} />)
    expect(Number(hitLine()!.getAttribute('stroke-width')) * MIN_ZOOM).toBeGreaterThanOrEqual(
      HIT_PX - 1e-9,
    )
  })

  it('rect / 文字这类有面积的对象不加命中线，外层 div 照旧承担命中', () => {
    render(<ObjectView obj={lineShape({ shape: 'rect', h: 20 })} />)
    expect(hitLine()).toBeNull()
    expect(objectDiv().style.pointerEvents).toBe('')

    render(<ObjectView obj={textObj} />)
    expect(hitLine()).toBeNull()
    expect(objectDiv().style.pointerEvents).toBe('')
  })
})

describe('命中线上的事件能到达外层 div 的 handler', () => {
  it('contextmenu：ObjectView.onContextMenu 跑到了（preventDefault + 弹层开在本对象上）', () => {
    useQuickEdit.setState({ target: null })
    render(<ObjectView obj={horizontalArrow()} />)
    const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    // dispatchEvent 返回 false == 有人调了 preventDefault
    expect(hitLine()!.dispatchEvent(ev)).toBe(false)
    expect(useQuickEdit.getState().target).toEqual({ kind: 'object', id: 'a1' })
  })

  it('dblclick：ObjectView.onDoubleClick 跑到了（它 stopPropagation，外层 spy 收不到）', () => {
    const outer = vi.fn()
    render(
      <div onDoubleClick={outer}>
        <ObjectView obj={horizontalArrow()} />
      </div>,
    )
    hitLine()!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }))
    expect(outer).not.toHaveBeenCalled()
  })

  it('对照组：命中线之外的事件确实会冒泡到外层 spy（上一条不是假阳性）', () => {
    const outer = vi.fn()
    render(
      <div onDoubleClick={outer}>
        <span data-probe="" />
      </div>,
    )
    container
      .querySelector('[data-probe]')!
      .dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }))
    expect(outer).toHaveBeenCalledTimes(1)
  })
})

describe('圆角矩形与后端对齐', () => {
  it('rx / ry 同值 —— 宽高悬殊时不再画成椭圆角', () => {
    render(<ObjectView obj={lineShape({ shape: 'rect', w: 60, h: 6, cornerRadius: 4 })} />)
    const rect = container.querySelector('rect')!
    expect(rect.getAttribute('rx')).toBe(rect.getAttribute('ry'))
  })

  it('半径钳到短边一半，与后端 min(w,h)*frac 同一结果', () => {
    // 后端：frac = min(r / min(w,h), 0.5)，d = min(w,h) * frac = min(r, min(w,h)/2)
    const backend = (r: number, w: number, h: number) =>
      Math.min(w, h) * Math.min(r / Math.max(Math.min(w, h), 0.001), 0.5)
    for (const [r, w, h] of [
      [4, 60, 6],
      [2, 40, 30],
      [50, 20, 20],
      [0.5, 8, 8],
    ]) {
      expect(cornerRadius(r, w, h)).toBeCloseTo(backend(r, w, h), 9)
    }
  })

  it('无圆角时不写 rx/ry', () => {
    render(<ObjectView obj={lineShape({ shape: 'rect', h: 20 })} />)
    const rect = container.querySelector('rect')!
    expect(rect.getAttribute('rx')).toBeNull()
  })
})
