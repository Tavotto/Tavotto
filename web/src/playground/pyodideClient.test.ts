/**
 * PlaygroundClient 的协议纪律：
 *   * 只接受「id 对得上 + 形状合法」的消息——Worker 里跑的是访客自己的
 *     Python，postMessage 它也摸得到，来路不明的消息必须当不存在；
 *   * 超时 = terminate + 全部在途请求一起拒——会话作废，不假装还能用；
 *   * abort 只放弃单条结果，不碰会话。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlaygroundClient, PlaygroundError, PHASE_TIMEOUT_MS } from './pyodideClient'
import { isWorkerResponse } from './protocol'

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

beforeEach(() => {
  FakeWorker.instances = []
  vi.stubGlobal('Worker', FakeWorker as unknown as typeof Worker)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const lastWorker = () => FakeWorker.instances.at(-1)!

describe('PlaygroundClient', () => {
  it('配对响应按 id 解析；成功拿 result', async () => {
    const c = new PlaygroundClient()
    const p = c.open('Fig1')
    const req = lastWorker().posted[0]
    lastWorker().emit({ id: req.id, ok: true, result: { stem: 'Fig1', manifest: {}, svg: '<svg/>' } })
    await expect(p).resolves.toMatchObject({ stem: 'Fig1' })
  })

  it('形状不合法或 id 对不上的消息一律丢弃', async () => {
    const c = new PlaygroundClient()
    const p = c.open('Fig1')
    const w = lastWorker()
    const id = w.posted[0].id as number
    // 全部该被无视：非对象 / 无 id / 未知 id / ok 缺失 / 假 progress
    w.emit('gibberish')
    w.emit({ ok: true })
    w.emit({ id: id + 999, ok: true, result: {} })
    w.emit({ id, hello: 'world' })
    w.emit({ id, progress: 'not-a-phase' })
    // 真正的响应仍然到达
    w.emit({ id, ok: true, result: { stem: 'Fig1' } })
    await expect(p).resolves.toMatchObject({ stem: 'Fig1' })
  })

  it('失败响应转成带 code 的 PlaygroundError，并保留分诊字段', async () => {
    const c = new PlaygroundClient()
    const p = c.load('f.py', 'import rdkit', {})
    const w = lastWorker()
    w.emit({
      id: w.posted[0].id,
      ok: false,
      code: 'unsupported_import',
      message: 'no rdkit',
      modules: ['rdkit'],
    })
    const err = (await p.catch((e) => e)) as PlaygroundError
    expect(err).toBeInstanceOf(PlaygroundError)
    expect(err.failure.code).toBe('unsupported_import')
    expect(err.failure.modules).toEqual(['rdkit'])
    // 渲染链路认 EngineError：module 字段带上第一个缺失模块
    expect(err.toEngineError().code).toBe('unsupported_import')
    expect(err.toEngineError().module).toBe('rdkit')
  })

  it('超时 = terminate + 所有在途请求一起拒，会话此后不可用', async () => {
    const c = new PlaygroundClient()
    const a = c.render('F', [])
    const b = c.render('F', [])
    const caughtA = a.catch((e) => e)
    const caughtB = b.catch((e) => e)
    vi.advanceTimersByTime(31_000)
    expect(lastWorker().terminated).toBe(true)
    expect(((await caughtA) as PlaygroundError).failure.code).toBe('timeout')
    expect(((await caughtB) as PlaygroundError).failure.code).toBe('timeout')
    expect(c.terminated).toBe(true)
    // 会话作废后的新请求立即拒绝（换文件 = 新 client）
    await expect(c.render('F', [])).rejects.toMatchObject({ failure: { code: 'timeout' } })
  })

  it('progress 重置计时器并换上该阶段的限额（下载慢不该按脚本限额掐）', async () => {
    const c = new PlaygroundClient()
    const p = c.init('https://cdn/', 'https://x/engine.zip')
    const w = lastWorker()
    const id = w.posted[0].id as number
    // runtime 阶段（180s）快到点时进入 engine 阶段 → 计时器重置为 60s
    vi.advanceTimersByTime(PHASE_TIMEOUT_MS.runtime - 1000)
    w.emit({ id, progress: 'engine' })
    vi.advanceTimersByTime(50_000)
    expect(w.terminated).toBe(false)
    w.emit({ id, ok: true, result: {} })
    await expect(p).resolves.toBeUndefined()
  })

  it('init 幂等去重：预热起过之后再调不会发第二条 init', async () => {
    const c = new PlaygroundClient()
    const a = c.init('https://cdn/', 'https://x/engine.zip')
    const b = c.init('https://cdn/', 'https://x/engine.zip')
    const w = lastWorker()
    expect(w.posted.filter((m) => m.type === 'init')).toHaveLength(1)
    expect(c.ready).toBe(false) // 「在路上」不算就位
    w.emit({ id: w.posted[0].id, ok: true, result: {} })
    await Promise.all([a, b])
    expect(c.ready).toBe(true)
    await c.init('https://cdn/', 'https://x/engine.zip')
    expect(w.posted.filter((m) => m.type === 'init')).toHaveLength(1)
  })

  it('init 失败不留 latch：下一次调用重新来过', async () => {
    const c = new PlaygroundClient()
    const first = c.init('https://cdn/', 'https://x/engine.zip').catch((e) => e)
    const w = lastWorker()
    w.emit({ id: w.posted[0].id, ok: false, code: 'runtime_failure', message: 'CDN 挂了' })
    expect(((await first) as PlaygroundError).failure.code).toBe('runtime_failure')
    expect(c.ready).toBe(false)
    void c.init('https://cdn/', 'https://x/engine.zip')
    expect(w.posted.filter((m) => m.type === 'init')).toHaveLength(2)
  })

  it('load 把完整性字段一起带回来（缺了就是空串/0，绝不编）', async () => {
    const c = new PlaygroundClient()
    const p = c.load('f.py', 'x = 1', {})
    const w = lastWorker()
    w.emit({
      id: w.posted[0].id,
      ok: true,
      result: { figures: [], log: '', script: 'f.py', source_sha256: 'abc', source_bytes: 5 },
    })
    await expect(p).resolves.toMatchObject({ script: 'f.py', source_sha256: 'abc', source_bytes: 5 })

    const c2 = new PlaygroundClient()
    const p2 = c2.load('f.py', 'x = 1', {})
    const w2 = lastWorker()
    w2.emit({ id: w2.posted[0].id, ok: true, result: { figures: [] } })
    await expect(p2).resolves.toMatchObject({ script: '', source_sha256: '', source_bytes: 0 })
  })

  it('sourceStatus 是一条独立命令（不搭渲染的顺风车）', async () => {
    const c = new PlaygroundClient()
    const p = c.sourceStatus()
    const w = lastWorker()
    expect(w.posted[0]).toMatchObject({ type: 'sourceStatus' })
    w.emit({ id: w.posted[0].id, ok: true, result: { script: 'f.py', sha256: 'deadbeef', bytes: 12 } })
    await expect(p).resolves.toEqual({ script: 'f.py', sha256: 'deadbeef', bytes: 12 })
  })

  it('abort 只放弃这一条的结果，不终结会话', async () => {
    const c = new PlaygroundClient()
    const ctrl = new AbortController()
    const p = c.render('F', [], undefined, ctrl.signal)
    const caught = p.catch((e) => e)
    ctrl.abort()
    expect(((await caught) as PlaygroundError).failure.code).toBe('aborted')
    expect(lastWorker().terminated).toBe(false)
    expect(c.terminated).toBe(false)
  })
})

describe('isWorkerResponse', () => {
  it.each([
    [null, false],
    ['str', false],
    [{ id: 'x', ok: true, result: {} }, false],
    [{ id: 1 }, false],
    [{ id: 1, ok: true, result: {} }, true],
    [{ id: 1, ok: false, code: 'x', message: 'y' }, true],
    [{ id: 1, ok: false }, false],
    [{ id: 1, progress: 'packages' }, true],
    [{ id: 1, progress: 'evil' }, false],
  ])('%j → %s', (v, want) => {
    expect(isWorkerResponse(v)).toBe(want)
  })
})
