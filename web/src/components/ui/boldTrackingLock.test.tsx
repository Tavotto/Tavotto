/**
 * 树行标签的截断位置锁（UI 审计 G3）：选中 = 600 比 400 宽，截断的长名字会多吃字。
 * 锁的做法是把 600 与 400 的宽度差用负字距吃掉。jsdom 不排版，Range 的宽度在这里按字重桩出来
 * （每字 6px，600 时每字 6.2px），量的是「选中时字距是不是刚好抵掉差值」。
 */
import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBoldTrackingLock } from './useBoldWidthLock'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Label({ text, selected }: { text: string; selected: boolean }) {
  const ref = useRef<HTMLSpanElement>(null)
  useBoldTrackingLock(ref, selected, text)
  return (
    <span ref={ref} data-label className="truncate">
      {text}
    </span>
  )
}

let root: Root
let host: HTMLDivElement
let rangeCalls = 0
let boxWidth = 100 // 标签盒宽；jsdom 不排版，clientWidth 按它桩
const original = (Range.prototype as { getBoundingClientRect?: unknown }).getBoundingClientRect

beforeEach(() => {
  rangeCalls = 0
  boxWidth = 100
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => boxWidth })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect =
    function (this: Range) {
      rangeCalls++
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
    boxWidth = 3
    await render(true, '短')
    expect(parseFloat(label().style.letterSpacing)).toBeCloseTo(-0.2, 5)
  })

  it('量出来是 0（jsdom / 字体未就绪）：什么都不设', async () => {
    ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
      ({ width: 0 }) as DOMRect
    await render(true, '零宽专用的另一段名字 zero')
    expect(label().style.letterSpacing).toBe('')
  })

  it('不截断（600 的宽度放得下标签盒）：不加字距', async () => {
    boxWidth = 10_000
    await render(true, '不截断的短名 fits')
    expect(label().style.letterSpacing).toBe('')
  })

  it('只在 [selected, text] 变化时量：同样的 props 重渲染 / 同名多行，Range 不再被量', async () => {
    const text = '计数专用的一段很长的名字 counting counting'
    await render(true, text)
    const first = rangeCalls
    expect(first).toBe(2) // 400 与 600 各一次
    // 同 props 重渲染：effect 根本不触发（命中缓存也不算——连样式都不该读）
    const styleReads = vi.spyOn(window, 'getComputedStyle')
    for (let i = 0; i < 5; i++) await render(true, text)
    expect(styleReads).not.toHaveBeenCalled()
    styleReads.mockRestore()
    expect(rangeCalls).toBe(first)
    // 取消再选中（同名）：命中缓存，不再量
    await render(false, text)
    await render(true, text)
    expect(rangeCalls).toBe(first)
  })

  it('多选 200 行同名：只量一次（2 次 Range 读），重渲染 0 次', async () => {
    const text = '二百行共用的一段很长的名字 shared shared shared'
    const Many = () => (
      <>
        {Array.from({ length: 200 }, (_, i) => (
          <Label key={i} text={text} selected />
        ))}
      </>
    )
    await act(async () => root.render(<Many />))
    expect(rangeCalls).toBe(2)
    const spaced = host.querySelectorAll<HTMLElement>('[data-label]')
    expect([...spaced].every((el) => el.style.letterSpacing !== '')).toBe(true)
    await act(async () => root.render(<Many />))
    expect(rangeCalls).toBe(2)
  })
})
