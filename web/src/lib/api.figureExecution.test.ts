import { afterEach, describe, expect, it, vi } from 'vitest'
import { EngineError, engineRender, subscribeEvents } from './api'

const fact = { scope: 'owner', script: 'plot.py', run_config: 'A', source_ids: [], receipt_ids: ['retired'], execution_id: 'started-1' }
afterEach(() => vi.unstubAllGlobals())
describe('execution facts survive transport failures and live SSE', () => {
  it('preserves an actual execution fact when render HTTP fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: 'script failed', code: 'script_error', figure_snapshot_invalidation: fact,
    }), { status: 500 })))
    const error = await engineRender('runtime:old-output', []).catch(e => e)
    expect(error).toBeInstanceOf(EngineError)
    expect(error.figureSnapshotInvalidation).toEqual(fact)
  })
  it('subscribes to figure.snapshot_invalidated and keeps project/scope/execution identity intact', () => {
    const listeners = new Map<string, (e: MessageEvent) => void>()
    const close = vi.fn()
    vi.stubGlobal('EventSource', class {
      addEventListener(kind: string, handler: (e: MessageEvent) => void) { listeners.set(kind, handler) }
      close = close
    })
    const consume = vi.fn()
    const stop = subscribeEvents(consume)
    expect(listeners.has('figure.snapshot_invalidated')).toBe(true)
    listeners.get('figure.snapshot_invalidated')!(new MessageEvent('figure.snapshot_invalidated', { data: JSON.stringify({ pj: 'A', ...fact }) }))
    expect(consume).toHaveBeenCalledExactlyOnceWith({ kind: 'figure.snapshot_invalidated', pj: 'A', ...fact })
    stop(); expect(close).toHaveBeenCalledOnce()
  })
})
