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
