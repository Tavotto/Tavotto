/**
 * Code Sheet 契约（§29.4）：正确文件名、完整源码、行号不进复制、Esc 关闭、
 * 「用这个案例开始」交出正确案例、打开代码**不触发任何 Worker/Pyodide**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FEATURED_EXAMPLE } from '../examples'
import { ExampleCodeSheet } from './ExampleCodeSheet'

let container: HTMLDivElement
let root: Root
let workerConstructions: unknown[][]

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  // 打开代码绝不触发 Pyodide：任何 Worker 构造都算违规
  workerConstructions = []
  vi.stubGlobal(
    'Worker',
    class {
      constructor(...args: unknown[]) {
        workerConstructions.push(args)
      }
      postMessage() {}
      terminate() {}
    },
  )
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

const renderSheet = (onClose = vi.fn(), onStart = vi.fn()) => {
  act(() => {
    root.render(
      <ExampleCodeSheet example={FEATURED_EXAMPLE} onClose={onClose} onStart={onStart} />,
    )
  })
  return { onClose, onStart }
}

// Radix Dialog 渲染进 portal——查询要打在 document.body 上
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]')

describe('ExampleCodeSheet', () => {
  it('显示文件名、完整源码与行号', () => {
    renderSheet()
    const d = dialog()!
    expect(d.textContent).toContain('kinetics.py')
    // 完整源码逐行都在
    for (const line of FEATURED_EXAMPLE.source.trim().split('\n')) {
      if (line.trim()) expect(d.textContent).toContain(line.trim().slice(0, 24))
    }
    expect(d.textContent).toContain('fontsize=9')
    // 行号列存在，且对屏幕阅读器隐藏、不可选中（复制不带行号）
    const gutter = d.querySelector('pre span[aria-hidden]')!
    expect(gutter.textContent).toContain('1')
    expect(gutter.className).toContain('select-none')
  })

  it('复制代码写入的是源码本身，不含行号', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    renderSheet()
    const copy = [...dialog()!.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('复制代码'),
    )!
    await act(async () => copy.click())
    expect(writeText).toHaveBeenCalledWith(FEATURED_EXAMPLE.source)
  })

  it('「用这个案例开始」交出正确的 filename 与 source', () => {
    const { onStart } = renderSheet()
    const start = [...dialog()!.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('用这个案例开始'),
    )!
    act(() => start.click())
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onStart.mock.calls[0][0].filename).toBe('kinetics.py')
    expect(onStart.mock.calls[0][0].source).toBe(FEATURED_EXAMPLE.source)
  })

  it('Esc 关闭', () => {
    const { onClose } = renderSheet()
    act(() => {
      dialog()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('打开代码不触发任何 Worker（Pyodide 零加载）', () => {
    renderSheet()
    expect(workerConstructions).toHaveLength(0)
  })
})
