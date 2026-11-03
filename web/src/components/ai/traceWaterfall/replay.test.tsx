/**
 * 按时间回放（已结束的 Trace）：「某一刻的样子」是对同一份数据的纯视图变换——没开始的不画，开始了没结束的画到那一刻，
 * 已结束的画全；不编 Span、不改总时长、不提前露出结局。时钟（速率 / 读数）也在这里断言。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { TraceWaterfall } from './TraceWaterfall'
import { formatClock, playbackRate } from './usePlayback'
import { buildTree, endOf, isRunning, leafType, notStartedAt, rowUnchangedBetween, settledAt, spanFigure } from './utils'
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
  span({ id: 'a', name: 'Root', kind: 'agent', start: 0, end: 10, icon: 'agent', status: 'ok' }),
  span({ id: 'm1', parentId: 'a', name: 'Plan', kind: 'model', start: 0, end: 2, status: 'ok', cost: 0.5 }),
  span({ id: 'fail', parentId: 'a', name: 'Run', kind: 'tool', start: 3, end: 6, status: 'error', firstFailure: true }),
  span({ id: 'pt', parentId: 'a', name: 'Mark', kind: 'phase', start: 7, end: 7, timing: 'point', icon: 'stage' }),
  span({ id: 'late', parentId: 'a', name: 'Late', kind: 'tool', start: 8, end: 9, status: 'ok' }),
]
const final: WaterfallModel = { spans, duration: 10, running: false }
const replay: WaterfallModel = { ...final, replay: true }
const node = (id: string) => {
  const found = [...buildTree(spans)[0].children].find((n) => n.span.id === id)
  return found!
}

describe('回放的视图变换（纯函数）', () => {
  it('endOf：回放中结束得比此刻晚的只画到此刻；已结束的、非回放的不变', () => {
    const s = spans[2] // 3 → 6
    expect(endOf(s, 4, true)).toBe(4)
    expect(endOf(s, 9, true)).toBe(6)
    expect(endOf(s, 4, false)).toBe(6)
    expect(endOf(s, 1, true)).toBe(3) // 还没开始：不会画出负长度
    expect(endOf({ ...s, end: undefined }, 5, true)).toBe(5)
  })

  it('isRunning：回放中 = 开始了、还没结束；点 / 虚线框不算；不回放时仍只看「没结束 + Trace 在跑」', () => {
    const s = spans[2]
    expect(isRunning(s, replay, 4)).toBe(true)
    expect(isRunning(s, replay, 2)).toBe(false)
    expect(isRunning(s, replay, 6)).toBe(false)
    expect(isRunning(spans[3], replay, 7)).toBe(false)
    expect(isRunning(s, final, 4)).toBe(false)
    expect(isRunning({ ...s, end: undefined }, { ...final, running: true }, 4)).toBe(true)
  })

  it('settledAt / notStartedAt：结局（失败、费用）到结束才露出；点出现即定论', () => {
    const f = spans[2]
    expect(settledAt(f, 4, true)).toBe(false)
    expect(settledAt(f, 6, true)).toBe(true)
    expect(settledAt(f, 4, false)).toBe(true)
    expect(settledAt(spans[3], 7, true)).toBe(true)
    expect(notStartedAt(f, 2, true)).toBe(true)
    expect(notStartedAt(f, 3, true)).toBe(false)
    expect(notStartedAt(f, 0, false)).toBe(false)
  })

  it('leafType：没结束的失败调用按种类画，结束了才变红', () => {
    expect(leafType(spans[2], true)).toBe('tool')
    expect(leafType(spans[2], false)).toBe('failed')
  })

  it('spanFigure：没结束的只写已过去的时长，不提前写费用', () => {
    const m1 = node('m1')
    expect(spanFigure(m1, 1, true)).toBe('1s')
    expect(spanFigure(m1, 3, true)).toContain('$0.50')
    expect(spanFigure(m1, 1, false)).toContain('$0.50')
  })

  it('rowUnchangedBetween：都在行开始之前、或都在彻底结束之后，这一格对这行没影响', () => {
    const m1 = node('m1') // 0 → 2
    const late = node('late') // 8 → 9
    expect(rowUnchangedBetween(m1, 3, 4)).toBe(true)
    expect(rowUnchangedBetween(m1, 1, 1.5)).toBe(false)
    expect(rowUnchangedBetween(late, 1, 2)).toBe(true)
    expect(rowUnchangedBetween(late, 8.2, 8.4)).toBe(false)
    // 有区间却没结束的行：终点是无穷，永远在变
    const open = { ...late.span, end: undefined }
    expect(rowUnchangedBetween({ ...late, span: open }, 20, 21)).toBe(false)
    const unknown = { ...open, timing: 'unmeasured' as const }
    expect(rowUnchangedBetween({ ...late, span: unknown }, 20, 21)).toBe(false)
  })
})

describe('时钟', () => {
  it('playbackRate：自动档把整段压缩到约 4 秒；太短的不放慢；1× / 10× 固定', () => {
    expect(playbackRate('auto', 40)).toBe(10)
    expect(playbackRate('auto', 2)).toBe(1)
    expect(playbackRate('realtime', 40)).toBe(1)
    expect(playbackRate('fast', 40)).toBe(10)
  })
  it('formatClock：m:ss，向下取整，负数当 0', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(12.9)).toBe('0:12')
    expect(formatClock(75)).toBe('1:15')
    expect(formatClock(-3)).toBe('0:00')
  })
})

describe('回放中的 Waterfall', () => {
  let host: HTMLDivElement
  let root: Root
  beforeEach(() => {
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
  })
  const show = (m: WaterfallModel, now: number) =>
    act(() => {
      root.render(
        <TooltipProvider>
          <TraceWaterfall model={m} now={now} />
        </TooltipProvider>,
      )
    })
  const ids = () => [...document.querySelectorAll('[data-trace-row]')].map((r) => r.getAttribute('data-trace-span-id'))
  const q = (sel: string) => document.querySelector<HTMLElement>(sel)

  it('还没开始的行不画；点到了才出现；回放结束（非回放模型）画全', () => {
    show(replay, 1)
    expect(ids()).toEqual(['a', 'm1'])
    show(replay, 5)
    expect(ids()).toEqual(['a', 'm1', 'fail'])
    show(replay, 7)
    expect(ids()).toContain('pt')
    expect(ids()).not.toContain('late')
    show(final, 10)
    expect(ids()).toEqual(['a', 'm1', 'fail', 'pt', 'late'])
  })

  it('开始了没结束的画到此刻、带运行标记，结局（失败）还没露出；结束后才变失败', () => {
    show(replay, 4)
    const row = q('[data-trace-row][data-trace-span-id="fail"]')!
    expect(row.getAttribute('data-running')).toBe('true')
    expect(row.getAttribute('data-status')).toBe('none')
    expect(row.querySelector('[data-trace-status-glyph="error"]')).toBeNull()
    expect(q('[data-trace-badge="first-failure"]')).toBeNull()
    const bar = row.querySelector('[data-trace-bar]')!
    expect(bar.getAttribute('data-trace-end')).toBe('4')
    expect(bar.getAttribute('data-bar-type')).toBe('tool')
    show(replay, 6.5)
    const done = q('[data-trace-row][data-trace-span-id="fail"]')!
    expect(done.getAttribute('data-running')).toBeNull()
    expect(done.getAttribute('data-status')).toBe('error')
    expect(done.querySelector('[data-trace-bar]')!.getAttribute('data-bar-type')).toBe('failed')
    expect(q('[data-trace-badge="first-failure"]')).not.toBeNull()
  })

  it('现在线跟着回放时刻；指标条仍是整份总数（总时长不变）', () => {
    show(replay, 4)
    expect(q('[data-trace-now]')).not.toBeNull()
    expect(q('[data-trace-metric="duration"] dd')!.getAttribute('data-value')).toBe('10s')
    show(final, 10)
    expect(q('[data-trace-now]')).toBeNull()
  })

  it('回放切换到完整结果时，未结束和未知结束的行仍更新；重播时结局重新隐藏', () => {
    const items = [
      span({ id: 'unfinished', name: 'Unfinished', kind: 'tool', start: 0, status: 'error' }),
      span({ id: 'unknown', name: 'Unknown', kind: 'tool', start: 0, timing: 'unmeasured' }),
    ]
    const complete: WaterfallModel = { spans: items, duration: 10, running: false }
    const playing: WaterfallModel = { ...complete, replay: true }
    show(playing, 5)
    expect(q('[data-trace-row][data-trace-span-id="unfinished"]')!.getAttribute('data-running')).toBe('true')
    expect(q('[data-trace-row][data-trace-span-id="unknown"] [data-trace-bar]')!.getAttribute('data-trace-end')).toBe('5')
    show(complete, 10)
    expect(q('[data-trace-row][data-trace-span-id="unfinished"]')!.getAttribute('data-running')).toBeNull()
    expect(q('[data-trace-row][data-trace-span-id="unfinished"]')!.getAttribute('data-status')).toBe('error')
    expect(q('[data-trace-row][data-trace-span-id="unknown"] [data-trace-bar]')!.getAttribute('data-trace-end')).toBe('10')
    show(playing, 5)
    expect(q('[data-trace-row][data-trace-span-id="unfinished"]')!.getAttribute('data-status')).toBe('none')
    expect(q('[data-trace-row][data-trace-span-id="unknown"] [data-trace-bar]')!.getAttribute('data-trace-end')).toBe('5')
  })

  it('折叠箭头只在有「已出现」的子步骤时画', () => {
    const lateKid: WaterfallModel = {
      replay: true,
      running: false,
      duration: 10,
      spans: [spans[0], span({ id: 'k', parentId: 'a', name: 'Kid', kind: 'tool', start: 2, end: 3, status: 'ok' })],
    }
    show(lateKid, 1)
    expect(q('[data-trace-row][data-trace-span-id="a"] [data-trace-toggle]')).toBeNull()
    show(lateKid, 2.5)
    expect(q('[data-trace-row][data-trace-span-id="a"] [data-trace-toggle]')).not.toBeNull()
  })
})
