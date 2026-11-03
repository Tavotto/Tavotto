/**
 * 文字框（方案 X，2026-11-03）：静止时就是一个普通输入框（框内没有工具），右上角小后缀「TeX」切写法；
 * 编辑中框上方浮出胶囊格式条（portal，三组：B/I ｜ 上下标 ｜ 换行 + extra），焦点离开框与条后收起。
 * 按下样式与属性页的 `StyleToggle` 同一份（`PRESSED_GLYPH`）。`inline` 形态什么都没有。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { figureTextCodec } from '@/lib/richEditor'
import { PRESSED_GLYPH } from './buttonClass'
import { RichTextField } from './RichTextField'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const render = (props: Partial<Parameters<typeof RichTextField>[0]> = {}) =>
  act(() =>
    root.render(
      <TooltipProvider>
        <button data-before>前</button>
        <RichTextField aria-label="内容" value="abc" codec={figureTextCodec} onChange={() => {}} {...props} />
        <button data-after>后</button>
      </TooltipProvider>,
    ),
  )
const pill = () => document.querySelector<HTMLElement>('[data-rich-pill]')
const editor = () => container.querySelector<HTMLElement>('[data-rich-text="field"]')!
const modeBtn = () => container.querySelector<HTMLButtonElement>('[data-rich-action="mode"]')!
const flush = () => act(async () => void (await new Promise((r) => setTimeout(r, 5))))
const focusEditor = () => act(() => editor().focus())

describe('文字框', () => {
  it('静止：框内没有工具，没有格式条；右上角只有「TeX」后缀', () => {
    render()
    expect(container.querySelector('[data-rich-action="bold"]')).toBeNull()
    expect(pill()).toBeNull()
    expect(modeBtn().textContent).toBe('TeX')
    expect(modeBtn().getAttribute('aria-pressed')).toBe('false')
    expect(container.querySelector('.absolute')).toBeNull()
  })

  it('聚焦出胶囊（portal 在 body）：三组按钮次序，带 data-capsule，不在框的 DOM 里', async () => {
    render({ extra: <button data-rich-action="case" /> })
    focusEditor()
    await flush()
    const p = pill()!
    expect(p).not.toBeNull()
    expect(container.contains(p)).toBe(false)
    expect(p.hasAttribute('data-capsule')).toBe(true)
    expect(p.getAttribute('role')).toBe('toolbar')
    const order = [...p.querySelectorAll('[data-rich-action]')].map((b) => b.getAttribute('data-rich-action'))
    expect(order).toEqual(['bold', 'italic', 'sup', 'sub', 'break', 'case'])
  })

  it('按下样式与 StyleToggle 同源：aria-pressed + 加粗字形（PRESSED_GLYPH），不用 accent', async () => {
    render()
    focusEditor()
    await flush()
    const bold = () => pill()!.querySelector<HTMLButtonElement>('[data-rich-action="bold"]')!
    expect(bold().className).not.toContain('font-bold')
    act(() => bold().click())
    expect(bold().getAttribute('aria-pressed')).toBe('true')
    for (const c of PRESSED_GLYPH.split(' ')) expect(bold().className).toContain(c)
    expect(bold().className).not.toContain('accent')
  })

  it('焦点离开框与条后收起；移到条里不收', async () => {
    render()
    focusEditor()
    await flush()
    act(() => pill()!.querySelector<HTMLElement>('button')!.focus())
    await flush()
    expect(pill()).not.toBeNull()
    act(() => container.querySelector<HTMLElement>('[data-after]')!.focus())
    await flush()
    expect(pill()).toBeNull()
  })

  it('Tab 从框进条，Esc 回框（不冒泡给宿主）', async () => {
    render()
    focusEditor()
    await flush()
    act(() => {
      editor().dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    })
    expect(document.activeElement).toBe(pill()!.querySelector('button'))
    act(() => {
      document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(document.activeElement).toBe(editor())
  })

  it('后缀切换：点它进 / 出 LaTeX 源码，TeX 时实心且 aria-pressed', () => {
    render()
    act(() => modeBtn().click())
    expect(container.querySelector('[data-rich-source]')).not.toBeNull()
    expect(modeBtn().getAttribute('aria-pressed')).toBe('true')
    expect(modeBtn().className).toContain('bg-ink')
    act(() => modeBtn().click())
    expect(container.querySelector('[data-rich-source]')).toBeNull()
    expect(modeBtn().getAttribute('aria-pressed')).toBe('false')
  })

  it('inline 形态（画布上就地编辑）没有后缀也没有格式条', async () => {
    render({ variant: 'inline' })
    act(() => container.querySelector<HTMLElement>('[data-rich-text="inline"]')!.focus())
    await flush()
    expect(container.querySelector('[data-rich-action="mode"]')).toBeNull()
    expect(pill()).toBeNull()
  })
})
