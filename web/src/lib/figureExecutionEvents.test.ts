import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => vi.resetModules())
const fact = { scope: 'owner', script: 'plot.py', run_config: null, execution_id: 'execution-1', source_ids: [] }
describe('execution evidence relay', () => {
  it('queues early evidence until the coordinator registers, preserving receipt deltas', async () => {
    const { observeFigureExecution, registerFigureExecutionConsumer } = await import('./figureExecutionEvents')
    observeFigureExecution(fact, 'A')
    const consume = vi.fn()
    registerFigureExecutionConsumer(consume)
    expect(consume).toHaveBeenCalledExactlyOnceWith(fact, 'A')
    const delta = { ...fact, receipt_ids: ['old-receipt'] }
    observeFigureExecution(delta, 'A')
    expect(consume).toHaveBeenLastCalledWith(delta, 'A')
  })
  it.each([undefined, {}, { ...fact, scope: undefined }, { ...fact, scope: 'guessed' },
    { ...fact, run_config: undefined }, { ...fact, source_ids: [''] },
    { ...fact, receipt_ids: [42] }, { ...fact, execution_id: '' },
  ])('rejects missing or open-ended evidence %j', async value => {
    const { observeFigureExecution, registerFigureExecutionConsumer } = await import('./figureExecutionEvents')
    const consume = vi.fn(); registerFigureExecutionConsumer(consume)
    observeFigureExecution(value, 'A')
    expect(consume).not.toHaveBeenCalled()
  })
})
