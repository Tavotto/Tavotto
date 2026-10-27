/**
 * Trace waterfall 的行为：诚实规则（point 不画宽条、unmeasured 虚线、null 不写 0、没结束的画到 now）
 * 与交互（搜索 / 折叠 / popover 的开关）。选择器只认 data-trace-*。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TraceWaterfall } from './TraceWaterfall'
import type { WaterfallModel, WaterfallSpan } from './types'

const span = (over: Partial<WaterfallSpan> & Pick<WaterfallSpan, 'id' | 'name' | 'kind' | 'start'>): WaterfallSpan => ({
  timing: 'measured',
  attempt: 1,
  retry: false,
  icon: 'tool',
  cost: null,
  tokensIn: null,
  tokensOut: null,
  firstFailure: false,
  facts: [],
  ...over,
})

const spans: WaterfallSpan[] = [
  span({ id: 'a', name: 'Root agent', kind: 'agent', start: 0, end: 10, icon: 'agent', status: 'ok' }),
  span({ id: 'm1', parentId: 'a', name: 'Plan', kind: 'model', start: 0, end: 2, icon: 'model', status: 'ok' }),
  span({
    id: 't1',
    parentId: 'a',
    name: 'Read file',
    kind: 'tool',
    start: 2,
    end: 4,
    status: 'ok',
    cost: 0.02,
    facts: [{ label: 'Path', value: 'figure.py' }],
  }),
  span({ id: 'b', parentId: 'a', name: 'Sub agent', kind: 'agent', start: 4, end: 9, icon: 'agent', status: 'ok' }),
  span({ id: 'p1', parentId: 'b', name: 'Checkpoint', kind: 'phase', start: 5, timing: 'point', icon: 'phase' }),
  span({ id: 'u1', parentId: 'b', name: 'Lost step', kind: 'tool', start: 6, timing: 'unmeasured', status: 'unknown' }),
  span({
    id: 't2',
    parentId: 'b',
    name: 'Run script',
    kind: 'tool',
    start: 7,
    end: 8,
    status: 'error',
    firstFailure: true,
  }),
  span({ id: 'r1', parentId: 'b', name: 'Still going', kind: 'model', start: 8.5, icon: 'model' }),
]
const model: WaterfallModel = { spans, duration: 10, running: true }

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

const render = (m: WaterfallModel = model, now = 9) =>
  act(() => {
    root.render(<TraceWaterfall model={m} now={now} />)
  })
const q = (sel: string, el: ParentNode = document) => el.querySelector<HTMLElement>(sel)
const row = (id: string) => q(`[data-trace-row][data-trace-span-id="${id}"]`)
const click = (el: HTMLElement | null) =>
  act(() => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
const type = (value: string) =>
  act(() => {
    const input = q('[data-trace-search]') as HTMLInputElement
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    set.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })

describe('TraceWaterfall', () => {
  it('嵌套 model 每个 span 一行', () => {
    render()
    expect([...document.querySelectorAll('[data-trace-row]')].map((r) => r.getAttribute('data-trace-span-id'))).toEqual([
      'a', 'm1', 't1', 'b', 'p1', 'u1', 't2', 'r1',
    ])
  })

  it('point 只画点，不画宽条', () => {
    render()
    const r = row('p1')!
    const marker = q('[data-trace-marker]', r)!
    expect(marker.getAttribute('data-timing')).toBe('point')
    expect(q('[data-trace-bar]', r)).toBeNull()
    expect(marker.style.width).toBe('')
    // 父泳道里也没有为它切出一段实心条
    expect([...row('b')!.querySelectorAll('[data-trace-bar]')].some((b) => b.getAttribute('data-trace-span-id') === 'p1')).toBe(false)
  })

  it('unmeasured 是虚线框，不是实心条', () => {
    render()
    const bar = q('[data-trace-bar]', row('u1')!)!
    expect(bar.getAttribute('data-bar-style')).toBe('dashed')
    expect(bar.className).toContain('border-dashed')
    expect(bar.className).not.toMatch(/\bbg-(accent|sel|dim|danger)/)
  })

  it('cost / tokens 为 null 时指标条写 —，不写 0', () => {
    render({ ...model, spans: spans.map((s) => ({ ...s, cost: null })) })
    expect(q('[data-trace-metric="cost"] [data-value]')!.getAttribute('data-value')).toBe('—')
    expect(q('[data-trace-metric="tokens"] [data-value]')!.getAttribute('data-value')).toBe('—')
    // 有值时不再是 —
    render()
    expect(q('[data-trace-metric="cost"] [data-value]')!.getAttribute('data-value')).toBe('$0.02')
  })

  it('有不可观察的 Agent 时调用次数写 —；根的失败不与子的失败重复计', () => {
    const v = (id: string) => q(`[data-trace-metric="${id}"] [data-value]`)!.getAttribute('data-value')
    render({
      duration: 3,
      running: false,
      spans: [
        span({ id: 'r', name: 'Task', kind: 'agent', start: 0, end: 3, status: 'error' }),
        span({ id: 'x', parentId: 'r', name: 'Agent run', kind: 'agent', start: 0, end: 3, status: 'error', opaque: true }),
      ],
    })
    expect(v('modelCalls')).toBe('—')
    expect(v('toolCalls')).toBe('—')
    expect(v('errors')).toBe('1')
    // 只有根失败（进程没起来，没有子）：数根
    render({ duration: 1, running: false, spans: [span({ id: 'r', name: 'Task', kind: 'agent', start: 0, end: 1, status: 'error' })] })
    expect(v('errors')).toBe('1')
    // 看得见的 Agent：真的是 0 就写 0
    render({ duration: 1, running: false, spans: [span({ id: 'r', name: 'Task', kind: 'agent', start: 0, end: 1, status: 'ok' })] })
    expect(v('modelCalls')).toBe('0')
  })

  it('整段的触发区在子步骤的触发区之下（DOM 在前）：点彩色子段打开的是那一步', () => {
    render()
    const timeline = row('a')!.querySelector('[data-trace-timeline]')!
    const triggers = [...timeline.querySelectorAll('[data-trace-trigger]')]
    const lane = triggers.findIndex((el) => el.getAttribute('data-scope') === 'lane')
    const steps = triggers.map((el, i) => (el.getAttribute('data-scope') === 'step' ? i : -1)).filter((i) => i >= 0)
    expect(lane).toBe(0)
    expect(steps.length).toBeGreaterThan(0)
  })

  it('搜索过滤，并在没有命中时出空态', () => {
    render()
    type('script')
    const ids = [...document.querySelectorAll('[data-trace-row]')].map((r) => r.getAttribute('data-trace-span-id'))
    expect(ids).toEqual(['a', 'b', 't2'])
    type('zzz-nothing')
    expect(q('[data-trace-empty]')).not.toBeNull()
    expect(document.querySelectorAll('[data-trace-row]').length).toBe(0)
    type('')
    expect(q('[data-trace-empty]')).toBeNull()
    expect(document.querySelectorAll('[data-trace-row]').length).toBe(8)
  })

  it('折叠智能体后隐藏它的子项', () => {
    render()
    click(q('[data-trace-toggle]', row('b')!))
    expect(row('t2')).toBeNull()
    expect(row('p1')).toBeNull()
    expect(row('b')).not.toBeNull()
    click(q('[data-trace-toggle]', row('b')!))
    expect(row('t2')).not.toBeNull()
  })

  it('点条开 popover，显示事实表；Esc 关闭', () => {
    render()
    click(q('[data-trace-trigger][data-scope="lane"]', row('t1')!))
    const pop = q('[data-trace-popover]')!
    expect(pop).not.toBeNull()
    expect(pop.getAttribute('data-trace-span-id')).toBe('t1')
    expect(pop.textContent).toContain('figure.py')
    expect(q('[data-trace-fact="cost"]', pop)).not.toBeNull()
    // tokens 未知 → 没有这两格
    expect(q('[data-trace-fact="tokensIn"]', pop)).toBeNull()
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(q('[data-trace-popover]')).toBeNull()
  })

  it('首个失败带标记，且与「最慢」是两种徽章', () => {
    render()
    expect(q('[data-trace-badge="first-failure"]', row('t2')!)).not.toBeNull()
    expect(q('[data-trace-badge="first-failure"]', row('t1')!)).toBeNull()
    const slow = q('[data-trace-badge="slowest"]')
    expect(slow).not.toBeNull()
    expect(slow!.closest('[data-trace-row]')!.getAttribute('data-trace-span-id')).not.toBe('t2')
  })

  it('没有结束时刻的 span 画到 now', () => {
    render(model, 9)
    const bar = q('[data-trace-bar]', row('r1')!)!
    expect(bar.getAttribute('data-trace-end')).toBe('9')
    expect(bar.hasAttribute('data-running')).toBe(true)
    render(model, 9.5)
    expect(q('[data-trace-bar]', row('r1')!)!.getAttribute('data-trace-end')).toBe('9.5')
  })

  it('error 用危险色，cancelled / unknown 用中性色', () => {
    render()
    expect(q('[data-trace-bar]', row('t2')!)!.className).toContain('bg-danger')
    const cancelled = spans.map((s) => (s.id === 't1' ? { ...s, status: 'cancelled' as const } : s))
    render({ ...model, spans: cancelled })
    const cls = q('[data-trace-bar]', row('t1')!)!.className
    expect(cls).toContain('bg-ink-faint')
    expect(cls).not.toContain('bg-ok')
  })
})
