import { afterEach, expect, it, vi } from 'vitest'
import { subscribeEvents } from './api'

afterEach(() => vi.unstubAllGlobals())

it('准备事件通过真实订阅注册与解析链交给消费者', () => {
  const streams: EventTarget[] = []
  const close = vi.fn()
  class Source extends EventTarget {
    readyState = 1
    static CLOSED = 2
    close = close
    constructor(_url: string) {
      super()
      streams.push(this)
    }
  }
  vi.stubGlobal('EventSource', Source)
  const receive = vi.fn()
  const unsubscribe = subscribeEvents(receive)
  streams[0]!.dispatchEvent(new MessageEvent('render.preparing', {
    data: JSON.stringify({ pj: 'project_a', id: 'slow.png' }),
  }))
  expect(receive).toHaveBeenCalledExactlyOnceWith({ kind: 'render.preparing', pj: 'project_a', id: 'slow.png' })
  unsubscribe()
  expect(close).toHaveBeenCalledOnce()
})

it('render.progress 在订阅里注册并解析（EVENT_KINDS 漏登会让引擎的进度被前端静默丢掉）', () => {
  const streams: EventTarget[] = []
  class Source extends EventTarget {
    readyState = 1
    static CLOSED = 2
    close = vi.fn()
    constructor(_url: string) {
      super()
      streams.push(this)
    }
  }
  vi.stubGlobal('EventSource', Source)
  const receive = vi.fn()
  subscribeEvents(receive)
  streams[0]!.dispatchEvent(new MessageEvent('render.progress', {
    data: JSON.stringify({ pj: 'project_a', id: 'slow.png' }),
  }))
  expect(receive).toHaveBeenCalledExactlyOnceWith({ kind: 'render.progress', pj: 'project_a', id: 'slow.png' })
})
