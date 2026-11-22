/** 费用币种：人民币条目显示 ¥、原样不换算；一棵树里币种混了就不汇总（不把人民币当美元加）。 */
import { describe, expect, it } from 'vitest'
import { buildTree, computeMetrics, formatCost } from './utils'
import type { WaterfallModel, WaterfallSpan } from './types'

const span = (over: Partial<WaterfallSpan> & Pick<WaterfallSpan, 'id' | 'name' | 'kind' | 'start'>): WaterfallSpan => ({
  timing: 'measured',
  attempt: 1,
  retry: false,
  icon: 'model',
  cost: null,
  tokensIn: null,
  tokensOut: null,
  firstFailure: false,
  facts: [],
  end: 1,
  status: 'ok',
  ...over,
})

const model = (spans: WaterfallSpan[]): WaterfallModel => ({ spans, duration: 3, running: false } as unknown as WaterfallModel)

describe('trace cost currency', () => {
  it('formatCost 按币种写符号，缺省仍是美元', () => {
    expect(formatCost(0.02)).toBe('$0.02')
    expect(formatCost(0.02, 'USD')).toBe('$0.02')
    expect(formatCost(1.5, 'CNY')).toBe('¥1.50')
  })

  it('同币种累加，指标条带上币种', () => {
    const m = model([
      span({ id: 'a', name: 'Agent', kind: 'agent', start: 0, icon: 'agent' }),
      span({ id: 'x', parentId: 'a', name: 'm', kind: 'model', start: 0, cost: 1, costCurrency: 'CNY' }),
      span({ id: 'y', parentId: 'a', name: 'm', kind: 'model', start: 1, cost: 2, costCurrency: 'CNY' }),
    ])
    const metrics = computeMetrics(buildTree(m.spans), m, 3)
    expect(metrics.cost).toBe(3)
    expect(metrics.costCurrency).toBe('CNY')
  })

  it('币种混了不汇总，写不知道而不是错误的加总', () => {
    const m = model([
      span({ id: 'a', name: 'Agent', kind: 'agent', start: 0, icon: 'agent' }),
      span({ id: 'x', parentId: 'a', name: 'm', kind: 'model', start: 0, cost: 1, costCurrency: 'CNY' }),
      span({ id: 'y', parentId: 'a', name: 'm', kind: 'model', start: 1, cost: 2 }),
    ])
    const metrics = computeMetrics(buildTree(m.spans), m, 3)
    expect(metrics.cost).toBeNull()
    expect(metrics.costCurrency).toBeNull()
  })
})
