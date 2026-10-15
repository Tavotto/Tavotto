/**
 * 树行标签的截断位置锁（UI 审计 G3）：选中 = 600 比 400 宽，截断的长名字会多吃字。
 * 锁的做法是把 600 与 400 的宽度差用负字距吃掉。jsdom 不排版，Range 的宽度在这里按字重桩出来
 * （每字 6px，600 时每字 6.2px），量的是「选中时字距是不是刚好抵掉差值」。
 */
import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useBoldTrackingLock } from './useBoldWidthLock'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Label({ text, selected }: { text: string; selected: boolean }) {
  const ref = useRef<HTMLSpanElement>(null)
  useBoldTrackingLock(ref, selected)
  return (
    <span ref={ref} data-label className="truncate">
      {text}
    </span>
  )
}

let root: Root
let host: HTMLDivElement
const original = (Range.prototype as { getBoundingClientRect?: unknown }).getBoundingClientRect

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect =
    function (this: Range) {
      const el = this.commonAncestorContainer as HTMLElement
      const n = [...(el.textContent ?? '')].length
      const perChar = el.style.fontWeight === '600' ? 6.2 : 6
      return { width: n * perChar + n * parseFloat(el.style.letterSpacing || '0') } as DOMRect
    }
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  ;(Range.prototype as unknown as { getBoundingClientRect: unknown }).getBoundingClientRect = original
})

const label = () => host.querySelector<HTMLElement>('[data-label]')!
const render = (selected: boolean, text = '曲线 “Catalyst (k = 0.125 min⁻¹)”') =>
  act(async () => root.render(<Label text={text} selected={selected} />))

describe('useBoldTrackingLock', () => {
  it('未选中：不动字距', async () => {
    await render(false)
    expect(label().style.letterSpacing).toBe('')
  })

  it('选中：负字距恰好抵掉 600 比 400 多出的宽度（总宽 = 400 时的宽度）', async () => {
    const text = '曲线 “Catalyst (k = 0.125 min⁻¹)”'
    await render(true, text)
    const n = [...text].length
    const ls = parseFloat(label().style.letterSpacing)
    expect(ls).toBeLessThan(0)
    // 600 的总宽 + 字距 × 字数 = 400 的总宽
    expect(n * 6.2 + n * ls).toBeCloseTo(n * 6, 5)
  })

  it('选中 → 取消选中：字距还原；换字后重新量', async () => {
    await render(true)
    expect(label().style.letterSpacing).not.toBe('')
    await render(false)
    expect(label().style.letterSpacing).toBe('')
    await render(true, '短')
    expect(parseFloat(label().style.letterSpacing)).toBeCloseTo(-0.2, 5)
  })

  it('量出来是 0（jsdom / 字体未就绪）：什么都不设', async () => {
    ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
      ({ width: 0 }) as DOMRect
    await render(true)
    expect(label().style.letterSpacing).toBe('')
  })
})
