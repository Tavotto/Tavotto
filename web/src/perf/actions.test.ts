/**
 * 普通点击动作的探针单测：无录制零开销；有录制时拆分三段；
 * 双 rAF 是可观测代理，不会被误称为真实画面呈现。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { perfAction, perfActionPhase, perfStart, perfStop } from './core'

let queue: FrameRequestCallback[] = []
let frameTs = 0
function tick(ms: number) {
  frameTs += ms
  const callbacks = queue
  queue = []
  for (const callback of callbacks) callback(frameTs)
}

beforeEach(() => {
  queue = []
  frameTs = 1000
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    queue.push(cb)
    return queue.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})
afterEach(() => {
  perfStop()
  vi.unstubAllGlobals()
})

describe('普通交互动作', () => {
  it('没录制时直通，不挂新的 rAF', () => {
    const fn = vi.fn(() => 42)
    expect(perfAction('symbol.insert', () =>
      perfActionPhase('document.commit', fn),
    )).toBe(42)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(queue).toHaveLength(0)
  })

  it('记录阶段、成功状态与两帧代理；中途保存不能杜撰上屏时间', () => {
    expect(perfStart()).toBe(true)
    perfAction('symbol.insert', () => {
      perfActionPhase('document.commit', () => undefined)
      perfActionPhase('selection.update', () => undefined)
      perfActionPhase('ui.status', () => undefined)
    })
    tick(16.7)
    tick(16.7)
    const raw = perfStop()!
    expect(raw.actions).toHaveLength(1)
    const action = raw.actions[0]
    expect(action.kind).toBe('symbol.insert')
    expect(action.ok).toBe(true)
    expect(action.sync_ms).toBeGreaterThanOrEqual(0)
    expect(action.two_raf_ms).not.toBeNull()
    expect(Object.keys(action.phases).sort()).toEqual(
      ['document.commit', 'selection.update', 'ui.status'],
    )
    expect(JSON.stringify(raw)).not.toContain('H2O')
  })

  it('异常原样抛出并被记作失败；停止后未完成的代理保持 null', () => {
    perfStart()
    expect(() => perfAction('preset.insert', () => {
      perfActionPhase('document.commit', () => { throw new Error('source text') })
    })).toThrow('source text')
    const record = perfStop()!.actions[0]
    expect(record.ok).toBe(false)
    expect(record.two_raf_ms).toBeNull()
  })

  it('拖动中的点击不破坏拖动片段，也不重复创建动作', async () => {
    const { perfSegmentBegin, perfSegmentEnd } = await import('./core')
    perfStart()
    perfSegmentBegin('move')
    perfAction('symbol.insert', () => undefined)
    perfSegmentEnd()
    const raw = perfStop()!
    expect(raw.segments).toHaveLength(1)
    expect(raw.actions).toHaveLength(0)
  })
})
