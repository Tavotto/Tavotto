/**
 * 加载视图契约（§29.6）：真实阶段列表（无伪百分比）、案例名与真实文件名、
 * 当前阶段 aria-live 可读、取消回调可用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlaygroundLoading } from './PlaygroundLoading'

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

describe('PlaygroundLoading', () => {
  it('显示案例名 + 真实阶段列表，当前阶段与已完成阶段形态不同（不只靠颜色）', () => {
    act(() => {
      root.render(
        <PlaygroundLoading
          phase="packages"
          filename="kinetics.py"
          title="反应动力学"
          onCancel={vi.fn()}
        />,
      )
    })
    expect(container.textContent).toContain('正在准备「反应动力学」')
    expect(container.textContent).toContain('加载 Python 运行时')
    expect(container.textContent).toContain('运行 kinetics.py')
    // 无伪百分比
    expect(container.textContent).not.toMatch(/\d+\s*%/)
    // aria-live 列表存在
    expect(container.querySelector('ol[aria-live="polite"]')).toBeTruthy()
    // 已完成 = check 图标；进行中 = spinner；未开始 = 空圈——三种形状
    const items = container.querySelectorAll('ol > li')
    expect(items).toHaveLength(5)
    expect(items[0].querySelector('svg')).toBeTruthy() // runtime 完成
    expect(items[2].querySelector('.animate-spin')).toBeTruthy() // packages 进行中
    expect(items[3].querySelector('span.rounded-full')).toBeTruthy() // script 未开始
  })

  it('取消按钮触发 onCancel', () => {
    const onCancel = vi.fn()
    act(() => {
      root.render(
        <PlaygroundLoading phase="runtime" filename="a.py" title="a.py" onCancel={onCancel} />,
      )
    })
    const btn = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('取消'),
    )!
    act(() => btn.click())
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
