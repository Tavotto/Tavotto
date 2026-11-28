/**
 * 任务清单（`ui/TaskChecklist`，2026-11-25 用户拍板「照视频的设计」）的契约：
 *   1. 步骤一开始就全列出来：进行中 / 没开始的步骤都在 DOM 里，行数从头到尾不变（不撑大）；
 *   2. 计数是「做完的 / 总数」，全做完时标题行换成实心勾（`data-task-all`）；
 *   3. 进行中那一步 `aria-current="step"`，胶囊只在进行中那步、给了 meta 才出现；
 *   4. 收起只在用户点 ▾ 时发生（aria-expanded 跟着变），再点展开。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TaskChecklist, type TaskStep } from './TaskChecklist'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const steps = (cur: number, meta?: string): TaskStep[] =>
  ['a', 'b', 'c', 'd'].map((id, i) => ({
    id,
    label: `步骤 ${id}`,
    state: i < cur ? 'done' : i === cur ? 'active' : 'pending',
    meta: i === cur ? meta : 'never-shown',
  }))

const render = (s: TaskStep[]) => act(async () => root.render(<TaskChecklist title="正在安装 openpyxl…" steps={s} data-x />))
const states = () => [...host.querySelectorAll('[data-task-step]')].map((li) => li.getAttribute('data-step-state'))

describe('TaskChecklist', () => {
  it('一开始就列全，往前走只换状态、不增减行', async () => {
    await render(steps(0))
    expect(states()).toEqual(['active', 'pending', 'pending', 'pending'])
    await render(steps(2))
    expect(states()).toEqual(['done', 'done', 'active', 'pending'])
    expect(host.querySelector('[data-task-count]')!.textContent).toBe('2/4')
    expect(host.querySelector('[aria-current="step"]')!.getAttribute('data-task-step')).toBe('c')
    expect(host.querySelector('[data-task-checklist]')!.hasAttribute('data-task-all')).toBe(false)
    // 调用方的 data-* 透传到外壳
    expect(host.querySelector('[data-task-checklist]')!.hasAttribute('data-x')).toBe(true)
  })

  it('胶囊只在进行中那一步、有 meta 时出现', async () => {
    await render(steps(1))
    expect(host.querySelector('[data-task-meta]')).toBeNull()
    await render(steps(1, '12 / 25 MB'))
    const pills = host.querySelectorAll('[data-task-meta]')
    expect(pills).toHaveLength(1)
    expect(pills[0].closest('[data-task-step]')!.getAttribute('data-task-step')).toBe('b')
    expect(pills[0].textContent).toBe('12 / 25 MB')
  })

  it('全做完：计数 4/4、data-task-all，没有 aria-current', async () => {
    await render(steps(4))
    expect(states()).toEqual(['done', 'done', 'done', 'done'])
    expect(host.querySelector('[data-task-count]')!.textContent).toBe('4/4')
    expect(host.querySelector('[data-task-checklist]')!.hasAttribute('data-task-all')).toBe(true)
    expect(host.querySelector('[aria-current]')).toBeNull()
  })

  it('收起只由用户点 ▾ 触发', async () => {
    await render(steps(1))
    const toggle = host.querySelector<HTMLButtonElement>('[data-task-toggle]')!
    const list = host.querySelector<HTMLOListElement>('[data-task-steps]')!
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(list.hidden).toBe(false)
    expect(toggle.getAttribute('aria-controls')).toBe(list.id)
    await act(async () => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(list.hidden).toBe(true)
    // 收起后状态更新照常进行，再展开看到的是最新的
    await render(steps(3))
    await act(async () => toggle.click())
    expect(list.hidden).toBe(false)
    expect(states()).toEqual(['done', 'done', 'done', 'active'])
  })

  it('bare：只有步骤、没有标题行与收起钮，行距收紧一档；busyIcon={null} 时标题行不画转圈', async () => {
    await act(async () => root.render(<TaskChecklist bare steps={steps(1)} />))
    expect(host.querySelector('button')).toBeNull()
    expect(host.querySelector('ol')!.hidden).toBe(false)
    expect(host.querySelector('[data-task-step]')!.className).toContain('h-6')
    await act(async () => root.render(<TaskChecklist title="正在修改脚本…" busyIcon={null} steps={steps(1)} />))
    expect(host.querySelector('[data-task-step]')!.className).toContain('h-7')
    // 只剩进行中那一步的转圈
    expect(host.querySelectorAll('.animate-spin')).toHaveLength(host.querySelectorAll('[data-task-step] .animate-spin').length)
  })

  it('bare + row：步骤排成一行（每步一样高、不换图标语言）；row 单独给、没有 bare 时照旧竖排', async () => {
    await act(async () => root.render(<TaskChecklist bare row steps={steps(1)} />))
    const list = host.querySelector<HTMLOListElement>('[data-task-steps]')!
    expect(list.dataset.taskRow).toBe('true')
    expect(list.className).toContain('flex-row')
    expect(host.querySelectorAll('[data-task-step]')).toHaveLength(4)
    expect(host.querySelector('[data-task-step]')!.className).toContain('h-5')
    expect(states()).toEqual(['done', 'active', 'pending', 'pending'])
    await act(async () => root.render(<TaskChecklist title="正在安装" row steps={steps(1)} />))
    expect(host.querySelector<HTMLOListElement>('[data-task-steps]')!.dataset.taskRow).toBeUndefined()
    expect(host.querySelector('[data-task-steps]')!.className).toContain('flex-col')
  })
})
