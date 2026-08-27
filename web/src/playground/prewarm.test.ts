/**
 * 预热的三条纪律（ADR 0007 §边界 一条都没松）：
 *   * 尊重 saveData 与「非常慢」的连接；Network Information API 不存在时
 *     照常预热（Safari/Firefox 上它整个没有，不能因此变成永不预热，
 *     更不能因此抛异常）；
 *   * **只有一条初始化路径**——预热中点了示例接的是同一个 Worker，
 *     绝不出现「一个在暖、一个在起」；
 *   * 预热是优化不是依赖：失败悄悄退回 cold，用户真开会话时按正常路径重来。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class FakeWorker {
  static instances: FakeWorker[] = []
  posted: Record<string, unknown>[] = []
  terminated = false
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  constructor() {
    FakeWorker.instances.push(this)
  }
  postMessage(m: Record<string, unknown>) {
    this.posted.push(m)
  }
  terminate() {
    this.terminated = true
  }
  emit(data: unknown) {
    this.onmessage?.({ data } as MessageEvent)
  }
}

/** 每个用例一份干净的模块状态（prewarm 的账本是模块级的）。 */
async function freshModule() {
  vi.resetModules()
  return import('./prewarm')
}

const setConnection = (value: unknown) => {
  Object.defineProperty(navigator, 'connection', { value, configurable: true })
}

beforeEach(() => {
  FakeWorker.instances = []
  vi.stubGlobal('Worker', FakeWorker as unknown as typeof Worker)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  Reflect.deleteProperty(navigator, 'connection')
})

describe('shouldPrewarm', () => {
  it('没有 Network Information API 时照常预热（Safari/Firefox 不能因此瘫掉）', async () => {
    const { shouldPrewarm } = await freshModule()
    expect(shouldPrewarm({} as Navigator)).toBe(true)
  })

  it('saveData === true 就不预热', async () => {
    const { shouldPrewarm } = await freshModule()
    expect(shouldPrewarm({ connection: { saveData: true } } as unknown as Navigator)).toBe(false)
  })

  it('明确「非常慢」的连接不预热，够用的照常', async () => {
    const { shouldPrewarm } = await freshModule()
    const nav = (effectiveType: string) =>
      ({ connection: { effectiveType } }) as unknown as Navigator
    expect(shouldPrewarm(nav('slow-2g'))).toBe(false)
    expect(shouldPrewarm(nav('2g'))).toBe(false)
    expect(shouldPrewarm(nav('3g'))).toBe(true)
    expect(shouldPrewarm(nav('4g'))).toBe(true)
  })

  it('connection 存在但什么都没说 → 保守默认是预热', async () => {
    const { shouldPrewarm } = await freshModule()
    expect(shouldPrewarm({ connection: {} } as unknown as Navigator)).toBe(true)
  })
})

describe('onIdle', () => {
  it('有 requestIdleCallback 就用它，并且可取消', async () => {
    const { onIdle } = await freshModule()
    const cancel = vi.fn()
    let cb: (() => void) | null = null
    vi.stubGlobal('requestIdleCallback', (fn: () => void) => {
      cb = fn
      return 7
    })
    vi.stubGlobal('cancelIdleCallback', cancel)
    const fn = vi.fn()
    const stop = onIdle(fn)
    expect(cb).toBeTypeOf('function')
    stop()
    expect(cancel).toHaveBeenCalledWith(7)
  })

  it('没有 requestIdleCallback 时退回 setTimeout（首帧不等它）', async () => {
    vi.useFakeTimers()
    const { onIdle } = await freshModule()
    vi.stubGlobal('requestIdleCallback', undefined)
    const fn = vi.fn()
    onIdle(fn, 400)
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(400)
    expect(fn).toHaveBeenCalledOnce()
  })

  it('退回路径同样可取消——用户先动手了就不必再预热', async () => {
    vi.useFakeTimers()
    const { onIdle } = await freshModule()
    vi.stubGlobal('requestIdleCallback', undefined)
    const fn = vi.fn()
    onIdle(fn, 400)()
    vi.advanceTimersByTime(1_000)
    expect(fn).not.toHaveBeenCalled()
  })
})

describe('暖机账本', () => {
  it('预热起一个 Worker 并发 init；重复调用不会起第二个', async () => {
    const { prewarm, warmState } = await freshModule()
    prewarm()
    prewarm()
    prewarm()
    expect(FakeWorker.instances).toHaveLength(1)
    expect(FakeWorker.instances[0].posted.filter((m) => m.type === 'init')).toHaveLength(1)
    expect(warmState()).toBe('warming')
  })

  it('saveData 下一个 Worker 都不起', async () => {
    setConnection({ saveData: true })
    const { prewarm, warmState } = await freshModule()
    prewarm()
    expect(FakeWorker.instances).toHaveLength(0)
    expect(warmState()).toBe('cold')
  })

  it('预热完成 → ready；取走的就是同一个 client，不重发 init', async () => {
    const { prewarm, takeWarmClient, warmState } = await freshModule()
    prewarm()
    const w = FakeWorker.instances[0]
    w.emit({ id: w.posted[0].id, ok: true, result: {} })
    await vi.waitFor(() => expect(warmState()).toBe('ready'))

    const { client, wasWarm } = takeWarmClient()
    expect(wasWarm).toBe(true)
    expect(FakeWorker.instances).toHaveLength(1)
    // 会话侧照常 init：命中已完成的 latch，不会再发一条 init
    await client.init('https://cdn/', 'https://x/engine.zip')
    expect(w.posted.filter((m) => m.type === 'init')).toHaveLength(1)
    // 取走之后账本清空——同一个 Worker 绝不能有两个地方能 terminate 它
    expect(warmState()).toBe('cold')
  })

  it('预热**中**就被取走：接的是同一个在途 init，不起第二个 Worker', async () => {
    const { prewarm, takeWarmClient } = await freshModule()
    prewarm()
    const w = FakeWorker.instances[0]
    const { client, wasWarm } = takeWarmClient()
    expect(wasWarm).toBe(false) // 还没暖好，但已经在暖
    const p = client.init('https://cdn/', 'https://x/engine.zip')
    expect(FakeWorker.instances).toHaveLength(1)
    expect(w.posted.filter((m) => m.type === 'init')).toHaveLength(1)
    w.emit({ id: w.posted[0].id, ok: true, result: {} })
    await expect(p).resolves.toBeUndefined()
  })

  it('预热失败：悄悄退回 cold 并收掉半死的 Worker，下一次取到的是全新的', async () => {
    const { prewarm, takeWarmClient, warmState } = await freshModule()
    prewarm()
    const first = FakeWorker.instances[0]
    first.emit({
      id: first.posted[0].id,
      ok: false,
      code: 'runtime_failure',
      message: 'CDN 挂了',
    })
    await vi.waitFor(() => expect(warmState()).toBe('cold'))
    expect(first.terminated).toBe(true)

    const { client, wasWarm } = takeWarmClient()
    expect(wasWarm).toBe(false)
    expect(client.terminated).toBe(false)
    expect(FakeWorker.instances).toHaveLength(2)
  })

  it('Worker 在预热期间崩溃：取到的是全新的，不是那具尸体', async () => {
    const { prewarm, takeWarmClient } = await freshModule()
    prewarm()
    FakeWorker.instances[0].onerror?.({})
    const { client, wasWarm } = takeWarmClient()
    expect(wasWarm).toBe(false)
    expect(client.terminated).toBe(false)
    expect(FakeWorker.instances).toHaveLength(2)
  })

  it('从没预热过也能取：现起一个', async () => {
    const { takeWarmClient, warmState } = await freshModule()
    const { client, wasWarm } = takeWarmClient()
    expect(wasWarm).toBe(false)
    expect(client.terminated).toBe(false)
    expect(warmState()).toBe('cold')
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('discardWarmClient 杀掉暖着的那个并回到 cold（页面卸载）', async () => {
    const { prewarm, discardWarmClient, warmState } = await freshModule()
    prewarm()
    discardWarmClient()
    expect(FakeWorker.instances[0].terminated).toBe(true)
    expect(warmState()).toBe('cold')
    discardWarmClient() // 幂等
    expect(warmState()).toBe('cold')
  })
})

describe('schedulePrewarm：「取消」之后不许立刻又下载起来', () => {
  // 每条用例都必须收掉自己的订阅——不收的话 window 上的监听器会活到下一条
  // 用例里，被它的 dispatchEvent 再触发一次（第一版就是这么多出一个 Worker
  // 的）。这同时是组件侧的纪律：effect 的 cleanup 必须摘掉这两个监听。
  let stop: (() => void) | null = null
  const schedule = (m: typeof import('./prewarm'), afterCancel = false) => {
    stop = m.schedulePrewarm({ afterCancel })
  }
  afterEach(() => {
    stop?.()
    stop = null
  })

  it('正常回到空状态：照常排一次空闲预热', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m)
    vi.advanceTimersByTime(500)
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('取消之后：空闲回调过去了也不起 Worker', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    vi.advanceTimersByTime(5_000)
    expect(FakeWorker.instances).toHaveLength(0)
    expect(m.warmState()).toBe('cold')
  })

  it('取消之后，用户再次表达意图（指针按下）才重新排', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    vi.advanceTimersByTime(5_000)
    expect(FakeWorker.instances).toHaveLength(0)

    globalThis.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(500)
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('按键同样算意图（键盘用户不该被落下）', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    globalThis.dispatchEvent(new Event('keydown'))
    vi.advanceTimersByTime(500)
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('意图只算一次：连按不会排出第二个 Worker', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    globalThis.dispatchEvent(new Event('pointerdown'))
    globalThis.dispatchEvent(new Event('keydown'))
    globalThis.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(5_000)
    expect(FakeWorker.instances).toHaveLength(1)
  })

  it('离开空状态时取消订阅：之后的意图不再唤起预热', async () => {
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    stop?.()
    stop = null
    globalThis.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(5_000)
    expect(FakeWorker.instances).toHaveLength(0)
  })

  it('saveData 下，连意图也不会让它起 Worker（既有边界没被绕开）', async () => {
    setConnection({ saveData: true })
    const m = await freshModule()
    vi.useFakeTimers()
    schedule(m, true)
    globalThis.dispatchEvent(new Event('keydown'))
    vi.advanceTimersByTime(5_000)
    expect(FakeWorker.instances).toHaveLength(0)
  })
})
