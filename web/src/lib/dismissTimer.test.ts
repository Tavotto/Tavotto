/**
 * 会让路的自动收起计时器（通知轨的两个来源共用）。
 *
 * 判据的主语是**剩余时间**：按住 / 页面不可见时不走表，放开后只排剩下的那截——
 * 「放开后重新数满」与「后台的时间一次落下」两种错都在这里被抓。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDismissTimer } from './dismissTimer'

let visibility: DocumentVisibilityState = 'visible'
const setVisibility = (v: DocumentVisibilityState) => {
  visibility = v
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('dismissTimer', () => {
  it('没人按着：到时触发一次，之后不再触发', () => {
    const t = createDismissTimer()
    const fire = vi.fn()
    t.start(1000, fire)
    expect(t.running).toBe(true)
    vi.advanceTimersByTime(999)
    expect(fire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fire).toHaveBeenCalledTimes(1)
    expect(t.running).toBe(false)
    vi.advanceTimersByTime(5000)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('按住时不走表；放开后只排剩下的那截，不重新数满', () => {
    const t = createDismissTimer()
    const fire = vi.fn()
    t.start(1000, fire)
    vi.advanceTimersByTime(400)
    t.hold('pointer')
    expect(t.running).toBe(false)
    vi.advanceTimersByTime(10_000)
    expect(fire, '按着的时候永远不触发').not.toHaveBeenCalled()
    t.release('pointer')
    expect(t.running).toBe(true)
    vi.advanceTimersByTime(599)
    expect(fire, '放开后剩 600ms，599 还没到').not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('两个原因各算各的：指针走了、焦点还在，照样不走', () => {
    const t = createDismissTimer()
    const fire = vi.fn()
    t.start(1000, fire)
    t.hold('pointer')
    t.hold('focus')
    t.hold('pointer') // 同一个原因按两次算一次
    t.release('pointer')
    vi.advanceTimersByTime(10_000)
    expect(fire).not.toHaveBeenCalled()
    t.release('focus')
    vi.advanceTimersByTime(1000)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('页面不可见时不走表；回来接着数剩下的，后台那段不一次落下', () => {
    const t = createDismissTimer()
    const fire = vi.fn()
    t.start(1000, fire)
    vi.advanceTimersByTime(300)
    setVisibility('hidden')
    expect(t.running).toBe(false)
    vi.advanceTimersByTime(60_000)
    expect(fire, '切走一分钟，回来时它还得在').not.toHaveBeenCalled()
    setVisibility('visible')
    vi.advanceTimersByTime(699)
    expect(fire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('页面本来就不可见时 start：等到可见才开始数', () => {
    const t = createDismissTimer()
    const fire = vi.fn()
    setVisibility('hidden')
    t.start(1000, fire)
    expect(t.running).toBe(false)
    vi.advanceTimersByTime(5000)
    expect(fire).not.toHaveBeenCalled()
    setVisibility('visible')
    vi.advanceTimersByTime(1000)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('再次 start 时上一次的 hold 还算数：指针停在 toast 上，新状态顶掉旧状态也不走', () => {
    const t = createDismissTimer()
    const a = vi.fn()
    const b = vi.fn()
    t.start(1000, a)
    t.hold('pointer')
    t.start(1000, b)
    vi.advanceTimersByTime(10_000)
    expect(a, '被顶掉的那一轮永远不触发').not.toHaveBeenCalled()
    expect(b).not.toHaveBeenCalled()
    t.release('pointer')
    vi.advanceTimersByTime(1000)
    expect(b).toHaveBeenCalledTimes(1)
    expect(a).not.toHaveBeenCalled()
  })

  it('cancel 之后不触发；hold 账本仍在，下一轮 start 仍等它放开', () => {
    const t = createDismissTimer()
    const a = vi.fn()
    t.start(1000, a)
    t.hold('focus')
    t.cancel()
    t.release('focus')
    vi.advanceTimersByTime(5000)
    expect(a).not.toHaveBeenCalled()
    const b = vi.fn()
    t.hold('focus')
    t.start(1000, b)
    vi.advanceTimersByTime(5000)
    expect(b).not.toHaveBeenCalled()
    t.release('focus')
    vi.advanceTimersByTime(1000)
    expect(b).toHaveBeenCalledTimes(1)
  })
})
