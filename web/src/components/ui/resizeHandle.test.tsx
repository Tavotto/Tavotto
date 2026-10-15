/**
 * 宽度把手（G9）：双击复位、拖动中 Esc 回滚、pointercancel 回滚、键盘步进与 Home/End、
 * 拖拽态、ARIA 值域。两侧（左抽屉右缘 / 属性栏左缘）方向相反，各量一遍。
 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ResizeHandle } from './ResizeHandle'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
let width = 0
const changes: number[] = []

function Harness({ side, start = 300 }: { side: 'left' | 'right'; start?: number }) {
  const [v, setV] = useState(start)
  width = v
  return (
    <div style={{ position: 'relative' }}>
      <ResizeHandle
        side={side}
        value={v}
        min={280}
        max={360}
        defaultValue={300}
        // 与 uiStore 的 setter 一样夹值 + 取整
        onChange={(px) => {
          const c = Math.min(360, Math.max(280, Math.round(px)))
          changes.push(c)
          setV(c)
        }}
        label="调整宽度"
      />
    </div>
  )
}

const sep = () => host.querySelector<HTMLElement>('[role="separator"]')!
const line = () => host.querySelector<HTMLElement>('[data-drawer-resize-line]')!

function pointer(el: Element, type: string, clientX: number, extra: Record<string, unknown> = {}) {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button: 0 })
  Object.defineProperty(e, 'pointerId', { value: 1 })
  for (const [k, v] of Object.entries(extra)) Object.defineProperty(e, k, { value: v })
  return act(async () => void el.dispatchEvent(e))
}
const key = (el: Element, k: string, init: KeyboardEventInit = {}) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init })))

beforeEach(() => {
  changes.length = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
const mount = (side: 'left' | 'right', start?: number) => act(async () => root.render(<Harness side={side} start={start} />))

describe('ResizeHandle', () => {
  it('role=separator 带 aria-valuenow / min / max 与可达名，是一个 Tab 停靠点', async () => {
    await mount('right')
    const el = sep()
    expect(el.getAttribute('aria-orientation')).toBe('vertical')
    expect(el.getAttribute('aria-valuenow')).toBe('300')
    expect(el.getAttribute('aria-valuemin')).toBe('280')
    expect(el.getAttribute('aria-valuemax')).toBe('360')
    expect(el.getAttribute('aria-label')).toBe('调整宽度')
    expect(el.tabIndex).toBe(0)
  })

  it('双击复位到默认宽度', async () => {
    await mount('right', 350)
    expect(width).toBe(350)
    await act(async () => void sep().dispatchEvent(new MouseEvent('dblclick', { bubbles: true })))
    expect(width).toBe(300)
    expect(sep().getAttribute('aria-valuenow')).toBe('300')
  })

  it('拖动：右缘把手向右 = 变宽，左缘把手向左 = 变宽；拖拽态 data-dragging，松手后消失', async () => {
    await mount('right')
    await pointer(sep(), 'pointerdown', 100)
    expect(sep().dataset.dragging).toBe('true')
    await pointer(sep(), 'pointermove', 130)
    expect(width).toBe(330)
    await pointer(sep(), 'pointerup', 130)
    expect(sep().dataset.dragging).toBeUndefined()
    // 松手之后再动，不再改宽
    await pointer(sep(), 'pointermove', 90)
    expect(width).toBe(330)

    await act(async () => root.render(<Harness side="left" start={300} key="l" />))
    await pointer(sep(), 'pointerdown', 100)
    await pointer(sep(), 'pointermove', 70)
    expect(width).toBe(330)
    await pointer(sep(), 'pointerup', 70)
  })

  it('拖动中 Esc 回滚到按下时的宽度，并结束拖拽；Esc 不再冒泡给别人', async () => {
    await mount('right', 300)
    let leaked = false
    const spy = () => (leaked = true)
    window.addEventListener('keydown', spy) // 冒泡阶段：捕获阶段已 stopPropagation，到不了
    await pointer(sep(), 'pointerdown', 100)
    await pointer(sep(), 'pointermove', 140)
    expect(width).toBe(340)
    await key(document.body, 'Escape')
    expect(width).toBe(300)
    expect(sep().dataset.dragging).toBeUndefined()
    expect(leaked).toBe(false)
    // 回滚之后的 pointermove / pointerup 不再改宽
    await pointer(sep(), 'pointermove', 200)
    await pointer(sep(), 'pointerup', 200)
    expect(width).toBe(300)
    window.removeEventListener('keydown', spy)
  })

  it('没在拖时 Esc 不被吞（留给别的处理者）', async () => {
    await mount('right')
    let seen = false
    const spy = () => (seen = true)
    window.addEventListener('keydown', spy)
    await key(sep(), 'Escape')
    expect(seen).toBe(true)
    window.removeEventListener('keydown', spy)
  })

  it('pointercancel（系统打断）同样回滚', async () => {
    await mount('right', 300)
    await pointer(sep(), 'pointerdown', 100)
    await pointer(sep(), 'pointermove', 150)
    expect(width).toBe(350)
    await pointer(sep(), 'pointercancel', 150)
    expect(width).toBe(300)
    expect(sep().dataset.dragging).toBeUndefined()
  })

  it('键盘：朝变宽方向步进 16；两侧方向相反；夹在值域内', async () => {
    await mount('right', 300)
    await key(sep(), 'ArrowRight')
    expect(width).toBe(316)
    await key(sep(), 'ArrowLeft')
    await key(sep(), 'ArrowLeft')
    expect(width).toBe(284)
    await key(sep(), 'ArrowLeft')
    expect(width).toBe(280)

    await act(async () => root.render(<Harness side="left" start={300} key="l" />))
    await key(sep(), 'ArrowLeft')
    expect(width).toBe(316)
    await key(sep(), 'ArrowRight')
    expect(width).toBe(300)
  })

  it('Home / End 到下限 / 上限', async () => {
    await mount('left', 300)
    await key(sep(), 'End')
    expect(width).toBe(360)
    expect(sep().getAttribute('aria-valuenow')).toBe('360')
    await key(sep(), 'Home')
    expect(width).toBe(280)
  })

  it('带修饰键的方向键不处理；无关键不 preventDefault', async () => {
    await mount('right', 300)
    await key(sep(), 'ArrowRight', { metaKey: true })
    expect(width).toBe(300)
    const e = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true })
    await act(async () => void sep().dispatchEvent(e))
    expect(e.defaultPrevented).toBe(false)
  })

  it('发丝线钩子在，拖拽态时线是 2px accent（不靠 hover）', async () => {
    await mount('right')
    expect(line()).not.toBeNull()
    expect(line().className).toMatch(/group-data-\[dragging\]\/resize:bg-accent/)
    expect(line().className).toMatch(/group-data-\[dragging\]\/resize:w-0\.5/)
  })
})
