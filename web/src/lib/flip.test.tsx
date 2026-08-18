/**
 * FLIP（列表重排的位移动画）。
 *
 * jsdom 没有布局也没有 Web Animations API，所以这里把 `getBoundingClientRect`
 * 与 `Element.prototype.animate` 都换成桩，断言的是**算出来的位移对不对**、
 * 以及那几条容易写错的边界：
 *
 *   - 新出现的项没有「原来的位置」，直接就位，不能从 (0,0) 飞过来；
 *   - 位置没变的项不该播动画（否则每次重渲染整列表都在抖）；
 *   - **滚动之后不该凭空播一段**——位置必须换算到容器内容坐标系。
 *     用视口坐标的话，滚动不触发重渲染，下一次因为别的原因重渲染时算出来的
 *     位移正好等于这期间滚过的距离。
 */
import { useRef } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flipCapture, useFlip } from './motion'

interface AnimCall {
  id: string
  dx: number
  dy: number
}

let calls: AnimCall[] = []
/** 最后一次动画写的是哪个 CSS 属性（画布对象必须是 translate，不能是 transform） */
let lastProp = ''
let container: HTMLDivElement
let root: Root

/** id → 它在「容器内容坐标系」里的位置；测试直接摆布它来模拟重排 */
const layout = new Map<string, { left: number; top: number }>()
/** 容器自己的位置与滚动量 */
const view = { left: 0, top: 0, scrollLeft: 0, scrollTop: 0 }

function stubGeometry() {
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
    configurable: true,
    value(this: HTMLElement) {
      const id = this.getAttribute('data-flip-id')
      if (id && layout.has(id)) {
        const p = layout.get(id)!
        // 视口坐标 = 内容坐标 + 容器左上角 − 滚动量
        return {
          left: p.left + view.left - view.scrollLeft,
          top: p.top + view.top - view.scrollTop,
          width: 100,
          height: 20,
        } as DOMRect
      }
      return { left: view.left, top: view.top, width: 200, height: 400 } as DOMRect
    },
  })
  Object.defineProperty(HTMLElement.prototype, 'scrollLeft', {
    configurable: true,
    get: () => view.scrollLeft,
  })
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get: () => view.scrollTop,
  })
  Object.defineProperty(HTMLElement.prototype, 'animate', {
    configurable: true,
    value(this: HTMLElement, frames: Keyframe[]) {
      const f0 = frames[0] as { transform?: string; translate?: string }
      lastProp = f0.translate != null ? 'translate' : 'transform'
      const from = String(f0.translate ?? f0.transform ?? '')
      const m = from.match(/(-?[\d.]+)px[ ,]+\s*(-?[\d.]+)px/)
      calls.push({
        id: this.getAttribute('data-flip-id') ?? '?',
        dx: m ? Number(m[1]) : NaN,
        dy: m ? Number(m[2]) : NaN,
      })
      return { finished: Promise.resolve() } as unknown as Animation
    },
  })
}

function List({ ids }: { ids: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useFlip(ref)
  return (
    <div ref={ref}>
      {ids.map((id) => (
        <div key={id} data-flip-id={id} />
      ))}
    </div>
  )
}

/** 按给定顺序把每一项摆成 24px 一行 */
function place(ids: string[]) {
  layout.clear()
  ids.forEach((id, i) => layout.set(id, { left: 0, top: i * 24 }))
}

const render = (ids: string[]) => act(() => root.render(<List ids={ids} />))

beforeEach(() => {
  calls = []
  lastProp = ''
  layout.clear()
  Object.assign(view, { left: 0, top: 0, scrollLeft: 0, scrollTop: 0 })
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
  stubGeometry()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  vi.unstubAllGlobals()
  act(() => root.unmount())
  container.remove()
})

describe('useFlip', () => {
  it('重排后每一项从原来的位置滑到新位置', () => {
    place(['a', 'b', 'c'])
    render(['a', 'b', 'c'])
    expect(calls, '首次渲染没有基准，不该播').toHaveLength(0)

    // a 挪到最后：a 往下两行，b/c 各往上一行
    place(['b', 'c', 'a'])
    calls = []
    render(['b', 'c', 'a'])

    const by = Object.fromEntries(calls.map((c) => [c.id, c]))
    expect(by.a.dy).toBe(-48) // 从 0 到 48，位移写的是「回到原处」的量
    expect(by.b.dy).toBe(24)
    expect(by.c.dy).toBe(24)
    expect(calls.every((c) => c.dx === 0)).toBe(true)
  })

  it('位置没变的项一个都不播', () => {
    place(['a', 'b'])
    render(['a', 'b'])
    calls = []
    render(['a', 'b'])
    expect(calls).toHaveLength(0)
  })

  it('新加入的项直接就位，不从别处飞过来', () => {
    place(['a'])
    render(['a'])
    place(['a', 'b'])
    calls = []
    render(['a', 'b'])
    expect(calls, 'a 没动、b 是新的').toHaveLength(0)
  })

  it('滚动之后不凭空播——位置换算到容器内容坐标系', () => {
    place(['a', 'b', 'c'])
    render(['a', 'b', 'c'])
    // 用户滚了 200px（不触发重渲染），随后因为别的原因重渲染一次
    view.scrollTop = 200
    calls = []
    render(['a', 'b', 'c'])
    expect(calls, '用视口坐标的话这里每一项都会滑 200px').toHaveLength(0)
  })

  it('容器整体挪位（侧栏展开）同样不该触发', () => {
    place(['a', 'b'])
    render(['a', 'b'])
    view.left = 300
    calls = []
    render(['a', 'b'])
    expect(calls).toHaveLength(0)
  })

  it('reduced-motion：一次都不播', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }))
    place(['a', 'b'])
    render(['a', 'b'])
    place(['b', 'a'])
    calls = []
    render(['b', 'a'])
    expect(calls).toHaveLength(0)
  })
})

describe('flipCapture（画布对象自动重排用）', () => {
  /** 造一个挂在 body 上的元素，位置由 layout 表决定 */
  function el(id: string): HTMLElement {
    const node = document.createElement('div')
    node.setAttribute('data-flip-id', id)
    document.body.appendChild(node)
    return node
  }

  it('改完位置后从原处滑过来，动的是 translate 属性而不是 transform', async () => {
    place(['a'])
    const node = el('a')
    const play = flipCapture([node])
    // 「提交」：把它挪到下面 48px
    layout.set('a', { left: 0, top: 48 })
    play()
    await new Promise((r) => requestAnimationFrame(r))
    await new Promise((r) => requestAnimationFrame(r))

    expect(calls).toHaveLength(1)
    expect(calls[0].dy).toBe(-48)
    // 画布对象的 transform 上挂着自己的 rotate/scale（旋转、翻转），
    // 拿 transform 播动画会在这 180ms 里把它们整个盖掉
    expect(lastProp).toBe('translate')
    node.remove()
  })

  it('位置没变就不播', async () => {
    place(['a'])
    const node = el('a')
    const play = flipCapture([node])
    play()
    await new Promise((r) => requestAnimationFrame(r))
    await new Promise((r) => requestAnimationFrame(r))
    expect(calls).toHaveLength(0)
    node.remove()
  })

  it('播之前元素已经被移除（对象被删）不报错', async () => {
    place(['a'])
    const node = el('a')
    const play = flipCapture([node])
    node.remove()
    layout.set('a', { left: 0, top: 48 })
    expect(() => play()).not.toThrow()
    await new Promise((r) => requestAnimationFrame(r))
    await new Promise((r) => requestAnimationFrame(r))
    expect(calls).toHaveLength(0)
  })

  it('reduced-motion：连量都不量', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }))
    place(['a'])
    const node = el('a')
    const play = flipCapture([node])
    layout.set('a', { left: 0, top: 48 })
    play()
    expect(calls).toHaveLength(0)
    node.remove()
  })
})
