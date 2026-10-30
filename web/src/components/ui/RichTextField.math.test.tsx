/**
 * 公式预览（ADR 0152 后续）：
 *   A. 图内文字的公式块在所见即所得里排成真公式，族是 `face:<图上那张脸>`；宿主换了族就地重画（不重建编辑器）。
 *   B. LaTeX 源码模式下方的预览条：只画公式、停手 250 ms 后才排、错误显示原因、没有公式不出现。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { __resetMathForTests } from '@/lib/mathtext'
import { annotationCodec, figureTextCodec } from '@/lib/richEditor'
import { previewFormulas, PREVIEW_DEBOUNCE_MS } from './MathPreviewStrip'
import { RichTextField, richViewOf } from './RichTextField'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
let requests: { src: string; family: string }[][]

const OK = { ok: true, width: 2, ascent: 0.8, descent: 0.2, segments: [['M', 0, 0], ['L', 2, 0], ['Z']] }

beforeEach(() => {
  __resetMathForTests()
  requests = []
  vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
    const items = (JSON.parse(String(init.body)) as { items: { src: string; family: string }[] }).items
    requests.push(items)
    const results = items.map((it) => (it.src.includes('bad') ? { ok: false, error: 'Unknown symbol: \\bad' } : OK))
    return new Response(JSON.stringify({ ok: true, results }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const flushNet = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)))
const render = (props: Partial<Parameters<typeof RichTextField>[0]> = {}) =>
  act(() =>
    root.render(
      <TooltipProvider>
        <RichTextField
          aria-label="内容"
          value="T = $x^2$ K"
          codec={figureTextCodec}
          onChange={() => {}}
          mathFamily="face:DejaVu Serif"
          sizePx={13}
          {...props}
        />
      </TooltipProvider>,
    ),
  )
const editor = () => container.querySelector('[data-rich-text="field"]')!
const source = () => container.querySelector<HTMLTextAreaElement>('[data-rich-source]')
const strip = () => container.querySelector('[data-math-preview]')
const click = (action: string) =>
  act(() => container.querySelector<HTMLButtonElement>(`[data-rich-action="${action}"]`)!.click())
const typeSource = (v: string) =>
  act(() => {
    const ta = source()!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(ta, v)
    ta.dispatchEvent(new Event('input', { bubbles: true }))
  })

describe('A. 图内文字的公式块排成真公式', () => {
  it('公式块按 face: 族请求、排好后画轮廓，不再是源码', async () => {
    vi.useFakeTimers()
    render()
    await flushNet()
    expect(requests.flat()).toEqual([{ src: 'x^2', family: 'face:DejaVu Serif' }])
    const chip = editor().querySelector('[data-rt-math] [data-math-state]')!
    expect(chip.getAttribute('data-math-state')).toBe('ok')
    expect(chip.querySelector('path')).not.toBeNull()
  })

  it('宿主换了族：已有的公式块就地重画，编辑器不重建（焦点 / 选区不丢）', async () => {
    vi.useFakeTimers()
    render()
    await flushNet()
    const view = richViewOf(editor())
    render({ mathFamily: 'face:Times New Roman' })
    await flushNet()
    expect(requests.flat().map((r) => r.family)).toEqual(['face:DejaVu Serif', 'face:Times New Roman'])
    expect(richViewOf(editor())).toBe(view)
    expect(editor().querySelector('[data-rt-math] [data-math-state]')!.getAttribute('data-math-state')).toBe('ok')
  })

  it('没传 mathFamily：公式块仍显示源码（不请求）', async () => {
    vi.useFakeTimers()
    render({ mathFamily: undefined })
    await flushNet()
    expect(requests).toEqual([])
    expect(editor().querySelector('[data-rt-math]')!.textContent).toBe('$x^2$')
  })
})

describe('B. 源码模式下的预览条', () => {
  it('切到源码就出现，画的是当前源码里的公式', async () => {
    vi.useFakeTimers()
    render()
    await flushNet()
    click('mode')
    await flushNet()
    expect(strip()!.getAttribute('aria-label')).toBeTruthy()
    expect(strip()!.querySelector('[data-math-preview-item="ok"]')!.getAttribute('aria-label')).toBe('x^2')
  })

  it('停手 250 ms 才去排；连续打字只排最后一版', async () => {
    vi.useFakeTimers()
    render()
    await flushNet()
    click('mode')
    await flushNet()
    requests = []
    typeSource('a $y$')
    await act(async () => void (await vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS - 50)))
    typeSource('a $yy$')
    await act(async () => void (await vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS - 50)))
    expect(requests).toEqual([]) // 还在抖动窗口里：一个请求都没发
    await act(async () => void (await vi.advanceTimersByTimeAsync(100)))
    expect(requests.flat()).toEqual([{ src: 'yy', family: 'face:DejaVu Serif' }])
    expect(strip()!.querySelector('[data-math-preview-item]')!.getAttribute('aria-label')).toBe('yy')
  })

  it('排不出来：红色波浪线的源码旁边写原因', async () => {
    vi.useFakeTimers()
    render({ value: 'plain' })
    await flushNet()
    click('mode')
    typeSource('$\\bad$')
    await act(async () => void (await vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS + 10)))
    const item = strip()!.querySelector('[data-math-preview-item="error"]')!
    expect(item.textContent).toContain('$\\bad$')
    expect(item.textContent).toContain('Unknown symbol: \\bad')
  })

  it('没有公式：整条不出现；公式删光后也消失', async () => {
    vi.useFakeTimers()
    render({ value: 'plain text' })
    await flushNet()
    click('mode')
    await flushNet()
    expect(strip()).toBeNull()
    typeSource('with $z$')
    await act(async () => void (await vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS + 10)))
    expect(strip()).not.toBeNull()
    typeSource('with z')
    await act(async () => void (await vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS + 10)))
    expect(strip()).toBeNull()
  })

  it('标注也有（LaTeX 子集里的公式）', async () => {
    vi.useFakeTimers()
    render({ value: 'k = $A e^{-E}$', codec: annotationCodec('latex'), mathFamily: 'serif' })
    await flushNet()
    click('mode')
    await flushNet()
    expect(requests.flat().at(-1)).toEqual({ src: 'A e^{-E}', family: 'serif' })
    expect(strip()!.querySelector('[data-math-preview-item="ok"]')).not.toBeNull()
  })

  it('没传 mathFamily：不出预览条', async () => {
    vi.useFakeTimers()
    render({ mathFamily: undefined })
    click('mode')
    await flushNet()
    expect(strip()).toBeNull()
    expect(requests).toEqual([])
  })
})

describe('previewFormulas：取光标所在行的公式', () => {
  it('只取光标那一行、去重、最多 4 个', () => {
    const src = 'a $x$\nb $y$ $y$ $z$\nc $w$'
    expect(previewFormulas(src, 0, figureTextCodec)).toEqual(['x'])
    expect(previewFormulas(src, src.indexOf('b'), figureTextCodec)).toEqual(['y', 'z'])
    const many = '$a$ $b$ $c$ $d$ $e$ $f$'
    expect(previewFormulas(many, many.length, figureTextCodec)).toEqual(['a', 'b', 'c', 'd'])
  })
  it('没有公式 / 光标越界都不抛', () => {
    expect(previewFormulas('plain', 99, figureTextCodec)).toEqual([])
    expect(previewFormulas('', 0, figureTextCodec)).toEqual([])
  })
})
