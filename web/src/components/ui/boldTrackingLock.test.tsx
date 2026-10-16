/**
 * 树行标签的截断位置锁（UI 审计 G3）：选中 = 600 比 400 宽，截断的长名字会多吃字。
 * 锁的做法是把 600 与 400 的宽度差用负字距吃掉。jsdom 不排版，Range 的宽度在这里按字重桩出来
 * （每字 6px，600 时每字 6.2px），量的是「选中时字距是不是刚好抵掉差值」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBoldTrackingLock } from './useBoldWidthLock'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Label({ text, selected, editing = false }: { text: string; selected: boolean; editing?: boolean }) {
  const ref = useBoldTrackingLock<HTMLSpanElement>(selected, text)
  // 行内改名：输入框顶替标签，关掉后标签重新挂载（与 LayerRow 同形状）
  if (editing) return <input data-input defaultValue={text} />
  return (
    <span ref={ref} data-label className="truncate">
      {text}
    </span>
  )
}

// 共用的 ResizeObserver 是模块级懒建单例：桩必须在第一次渲染前就位，并记下建了几个、观察着谁
const ro = {
  instances: 0,
  observed: new Set<Element>(),
  fire: (_el: Element) => {},
}
globalThis.ResizeObserver = class {
  cb: (entries: { target: Element }[]) => void
  constructor(cb: (entries: { target: Element }[]) => void) {
    ro.instances++
    this.cb = cb
    ro.fire = (el) => this.cb([{ target: el }])
  }
  observe(el: Element) {
    ro.observed.add(el)
  }
  unobserve(el: Element) {
    ro.observed.delete(el)
  }
  disconnect() {}
} as unknown as typeof ResizeObserver

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
      // 与浏览器一致：宽度与字距都按「渲染单位」计——连续空白折叠成一个、组合符 / ZWJ 序列算一个字素簇
      const n = renderedUnits(el.textContent ?? '')
      const perChar = el.style.fontWeight === '600' ? 6.2 : 6
      const ls = parseFloat(el.style.letterSpacing || '0')
      // 连字模型：字距为 0 时 `fi` 连成一个字形、每处窄 1.5px；字距非 0 时浏览器关掉连字
      const ligatures = ls === 0 ? ligatureCount(el.textContent ?? '') : 0
      return { width: n * perChar + n * ls - ligatures * 1.5 } as DOMRect
    }
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  ;(Range.prototype as unknown as { getBoundingClientRect: unknown }).getBoundingClientRect = original
})

const n600 = (g: number) => g * 6.2
// 浏览器怎么排版就怎么数（测试里的「浏览器」）：`truncate` 折叠连续空白，再按字素簇
const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' })
const ligatureCount = (text: string) => (text.match(/fi/g) ?? []).length
const renderedUnits = (text: string) => [...seg.segment(text.replace(/\s+/g, ' '))].length
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

  // 补偿后 600 的总宽 = 400 的总宽（桩按渲染单位计宽，字距对每个单位生效，与浏览器一致）
  const lockedWidth = (text: string) => {
    const units = renderedUnits(text)
    const ls = parseFloat(label().style.letterSpacing)
    // 400（未选中，字距 0，连字开着）真正画出来的宽度；600 选中后字距非 0、连字关闭
    return { w600: n600(units) + units * ls, w400: units * 6 - ligatureCount(text) * 1.5, ls }
  }

  it('连续多个空格（truncate 折叠成一个）：按渲染单位补偿，不按源串字数', async () => {
    const text = 'Catalyst        result      with     gaps  and   more   long   text'
    expect(renderedUnits(text)).toBeLessThan([...text].length)
    await render(true, text)
    const { w600, w400, ls } = lockedWidth(text)
    expect(ls).toBeLessThan(0)
    expect(w600).toBeCloseTo(w400, 5)
  })

  it('带连字的名字：基准在连字关闭的状态下量，补偿后 600 的总宽仍等于 400 真正画出来的宽度', async () => {
    const text = 'Specific final profile fitting of first-order kinetic fits'
    expect(ligatureCount(text)).toBeGreaterThanOrEqual(4)
    await render(true, text)
    const { w600, w400, ls } = lockedWidth(text)
    expect(ls).toBeLessThan(0)
    expect(w600).toBeCloseTo(w400, 5)
  })

  it('组合符 / ZWJ emoji：补偿后 600 的总宽仍等于 400 的总宽', async () => {
    const text = '图例项 “Re\u0301sume\u0301 👨\u200d👩\u200d👧 data”'
    expect(renderedUnits(text)).toBeLessThan([...text].length)
    await render(true, text)
    const { w600, w400, ls } = lockedWidth(text)
    expect(ls).toBeLessThan(0)
    expect(w600).toBeCloseTo(w400, 5)
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
    expect(first).toBe(3) // 400、600、600 + 1px 字距（量单位数）各一次
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

  it('多选 200 行同名：只量一次（3 次 Range 读），重渲染 0 次', async () => {
    const text = '二百行共用的一段很长的名字 shared shared shared'
    const Many = () => (
      <>
        {Array.from({ length: 200 }, (_, i) => (
          <Label key={i} text={text} selected />
        ))}
      </>
    )
    await act(async () => root.render(<Many />))
    expect(rangeCalls).toBe(3)
    const spaced = host.querySelectorAll<HTMLElement>('[data-label]')
    expect([...spaced].every((el) => el.style.letterSpacing !== '')).toBe(true)
    await act(async () => root.render(<Many />))
    expect(rangeCalls).toBe(3)
  })

  it('选中长名 → 进入改名（标签被输入框顶替）→ 取消：重新挂载的标签上字距仍在', async () => {
    const text = '改名取消专用的一段很长的名字 rename rename rename'
    const at = (editing: boolean) =>
      act(async () => root.render(<Label text={text} selected editing={editing} />))
    await at(false)
    const locked = label().style.letterSpacing
    expect(locked).not.toBe('')
    await at(true)
    expect(host.querySelector('[data-label]')).toBeNull()
    await at(false)
    expect(label().style.letterSpacing).toBe(locked)
  })

  it('选中期间标签盒变窄 / 变宽：截断状态切换时字距跟着加 / 撤（只用缓存宽度，不再量 Range）', async () => {
    const text = '尺寸变化专用的一段很长的名字 resize resize resize'
    boxWidth = 10_000 // 放得下：不锁
    await render(true, text)
    expect(label().style.letterSpacing).toBe('')
    const calls = rangeCalls
    boxWidth = 50 // 抽屉拖窄：放不下了
    await act(async () => ro.fire(label()))
    expect(parseFloat(label().style.letterSpacing)).toBeLessThan(0)
    boxWidth = 10_000 // 拉宽：又放得下
    await act(async () => ro.fire(label()))
    expect(label().style.letterSpacing).toBe('')
    expect(rangeCalls).toBe(calls)
  })

  it('取消选中 / 卸载即断开观察；200 行多选只有一个 observer', async () => {
    const text = '观察者专用的一段很长的名字 observer observer observer'
    const before = ro.instances
    const Many = ({ selected }: { selected: boolean }) => (
      <>
        {Array.from({ length: 200 }, (_, i) => (
          <Label key={i} text={text} selected={selected} />
        ))}
      </>
    )
    await act(async () => root.render(<Many selected />))
    expect(ro.instances - before).toBeLessThanOrEqual(1) // 单例：最多在本文件第一次用到时建一个
    expect(ro.instances).toBe(1)
    const labels = [...host.querySelectorAll('[data-label]')]
    expect(labels.every((el) => ro.observed.has(el))).toBe(true)
    await act(async () => root.render(<Many selected={false} />))
    expect(labels.some((el) => ro.observed.has(el))).toBe(false)
    await act(async () => root.render(<Many selected />))
    const again = [...host.querySelectorAll('[data-label]')]
    await act(async () => root.unmount())
    expect(again.some((el) => ro.observed.has(el))).toBe(false)
    root = createRoot(host)
  })

  it('有宽差的标签原地改成无宽差的文字：之后 resize 回调不会把旧字距套上去', async () => {
    const wide = '改名换字专用的一段很长的名字 latin latin latin latin'
    boxWidth = 50
    await render(true, wide)
    expect(label().style.letterSpacing).not.toBe('')
    expect(ro.observed.has(label())).toBe(true)
    // 改成「定宽」文字：400 与 600 一样宽（桩里按字数线性，这里让 600 的单字宽等于 400 的）
    const orig = (Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect
    ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = function (this: Range) {
      const el = this.commonAncestorContainer as HTMLElement
      return { width: [...(el.textContent ?? '')].length * 6 } as DOMRect
    }
    await render(true, '定宽的另一段文字不再有宽差 cjk cjk cjk cjk')
    expect(label().style.letterSpacing).toBe('')
    expect(ro.observed.has(label())).toBe(false)
    boxWidth = 10
    await act(async () => ro.fire(label()))
    expect(label().style.letterSpacing).toBe('')
    ;(Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = orig
  })
})
