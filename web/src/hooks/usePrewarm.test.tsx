import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { armPrewarm, PrewarmSync, PREWARM_DELAY_MS, resetPrewarmForTest } from './usePrewarm'
import * as api from '@/lib/api'
import type { CanvasObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (fileId: string, script?: string) => ({ type: 'panel', id: fileId, fileId, script }) as unknown as CanvasObject
const setObjects = (objects: CanvasObject[]) =>
  act(() => {
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects } }))
  })

beforeEach(() => {
  vi.useFakeTimers()
  resetPrewarmForTest()
  // 无 requestIdleCallback：到点直接 fire
  vi.stubGlobal('requestIdleCallback', undefined)
  setObjects([])
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('armPrewarm', () => {
  it('prewarms the first scripted panel once, after the settle delay', () => {
    const post = vi.spyOn(api, 'postEnginePrewarm').mockResolvedValue()
    setObjects([panel('a.pdf'), panel('b.pdf', 'b.py')])
    const stop = armPrewarm('p1')
    vi.advanceTimersByTime(PREWARM_DELAY_MS - 1)
    expect(post).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(post).toHaveBeenCalledExactlyOnceWith('b.pdf')
    // 触发后已退订：再改文档不会再发
    setObjects([panel('c.pdf', 'c.py')])
    vi.advanceTimersByTime(PREWARM_DELAY_MS * 2)
    expect(post).toHaveBeenCalledOnce()
    stop()
    // 同一项目再布防也不再发
    armPrewarm('p1')
    vi.advanceTimersByTime(PREWARM_DELAY_MS * 2)
    expect(post).toHaveBeenCalledOnce()
  })

  it('waits for a scripted panel, restarting the delay when the document changes', () => {
    const post = vi.spyOn(api, 'postEnginePrewarm').mockResolvedValue()
    const stop = armPrewarm('p2')
    vi.advanceTimersByTime(PREWARM_DELAY_MS * 2)
    expect(post).not.toHaveBeenCalled()
    setObjects([panel('b.pdf', 'b.py')])
    vi.advanceTimersByTime(PREWARM_DELAY_MS - 10)
    setObjects([panel('b.pdf', 'b.py'), panel('d.pdf', 'd.py')]) // 又变了：重新计时
    vi.advanceTimersByTime(PREWARM_DELAY_MS - 10)
    expect(post).not.toHaveBeenCalled()
    vi.advanceTimersByTime(10)
    expect(post).toHaveBeenCalledExactlyOnceWith('b.pdf')
    stop()
  })

  it('cleanup unsubscribes from the document store and cancels the pending timer', () => {
    const post = vi.spyOn(api, 'postEnginePrewarm').mockResolvedValue()
    setObjects([panel('b.pdf', 'b.py')])
    const unsubscribe = vi.fn()
    const subscribe = vi.spyOn(useDocumentStore, 'subscribe').mockImplementation(() => unsubscribe)
    const stop = armPrewarm('p3')
    expect(subscribe).toHaveBeenCalledOnce()
    stop()
    expect(unsubscribe).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(PREWARM_DELAY_MS * 2)
    expect(post).not.toHaveBeenCalled()
  })
})

describe('PrewarmSync', () => {
  it('is a leaf: a host that renders it does not re-render when the document objects change', () => {
    vi.spyOn(api, 'postEnginePrewarm').mockResolvedValue()
    useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p4' } as never })
    let hostRenders = 0
    function Host() {
      hostRenders += 1
      return <PrewarmSync />
    }
    const root = createRoot(document.createElement('div'))
    act(() => root.render(<Host />))
    const before = hostRenders
    setObjects([panel('b.pdf', 'b.py')])
    setObjects([panel('b.pdf', 'b.py'), panel('c.pdf', 'c.py')])
    expect(hostRenders).toBe(before)
    act(() => root.unmount())
  })
})
