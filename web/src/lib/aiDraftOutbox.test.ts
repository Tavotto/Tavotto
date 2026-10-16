/**
 * 发件箱的「基线未知」模式（首读失败）只在还没有任何一次写成功时成立：写成功过一次就退出，
 * 之后的 stale 若带着本页没见过的戳（别的标签页写了更新稿），按跨标签页规则放弃，不以本地为准重写（#1098 Codex）。
 * 替身是 fetch 层：发件箱在全局 setup 里已被加载，模块 mock 换不掉它对 `./aiDraft` 的引用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { __resetWriteStampForTests, textHash } from './aiDraft'
import * as outbox from './aiDraftOutbox'

const puts: { text: string; stamp: number }[] = []
let next: { stale: boolean; stamp: number; tooLarge?: boolean }[] = []
const realFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
      puts.push({ text: body.text, stamp: body.stamp })
      const r = next.shift() ?? { stale: false, stamp: body.stamp }
      if (r.tooLarge) {
        // 与后端一致：超限写入留了墓碑，413 带回那一行的戳
        return new Response(
          JSON.stringify({ error: 'x', code: 'ai_draft_too_large', params: { limit_kb: 20 }, stamp: r.stamp }),
          { status: 413, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ revision: 1, stamp: r.stamp, ...(r.stale ? { stale: true } : {}) }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { status: 404 })
  }) as typeof fetch
})

afterEach(() => {
  globalThis.fetch = realFetch
  outbox.__resetOutboxForTests()
  __resetWriteStampForTests() // 写入戳下限是模块级状态：上一条用例观察到的 9e15 不能抬高这一条的戳
  puts.length = 0
  next = []
})

const jsonResp = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

describe('aiDraftOutbox · 同一版本的重复写入：戳相等且文本哈希相同才算已接受', () => {
  const putStale = (hashOf: (body: { text: string }) => string) => {
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        // 服务器回 stale：戳等于这次 PUT 自己带的戳；`text_hash` 是服务器那一行当前文本的哈希
        return jsonResp({ revision: 1, stamp: body.stamp, stale: true, text_hash: hashOf(body) })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
  }

  it('等戳且同内容（自己的另一次尝试先落地）：按已接受收尾，不换戳重写、不放弃', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.advance('p', 'f', 1) // 基线已知：没有这条规则时会被判为「别处写了更新稿」而放弃
    outbox.post('p', 'f', 'v1')
    putStale((b) => textHash(b.text))
    await outbox.flush('p', 'f')
    expect(puts).toHaveLength(1) // 没有换戳重写
    expect(events).toContain('accepted')
    expect(events).not.toContain('abandoned')
    expect(outbox.peek('p', 'f')).toBeNull()
  })

  it('等戳但内容不同（别的页面碰巧同戳、赢了）：按普通 stale 规则走——放弃，交给对账；哪怕只发过一次以上尝试也一样', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.advance('p', 'f', 1)
    outbox.post('p', 'f', 'v1')
    putStale(() => textHash('别的页面的另一份文本'))
    void outbox.flush('p', 'f', true) // 先有过一次 keepalive 尝试（以前靠 attempts > 1 会在这里误判成已接受）
    await outbox.flush('p', 'f')
    expect(events).not.toContain('accepted') // 对照：只看戳 / 计数会把输掉的 PUT 误判成已保存
    expect(events.at(-1)).toBe('abandoned')
  })

  it('stale 响应没带 text_hash（老后端）：不能确认是同一份内容，按普通 stale 规则走', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.advance('p', 'f', 1)
    outbox.post('p', 'f', 'v1')
    putStale(() => undefined as unknown as string)
    await outbox.flush('p', 'f')
    expect(events.at(-1)).toBe('abandoned')
  })
})

describe('aiDraftOutbox · 槽不变式', () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
  afterEach(() => vi.useRealTimers())

  it('A 在途时编辑出 B，B 的防抖到期后复用 A 的 promise；A 落地为 unavailable——不靠 focus / online，B 也按退避写成功', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    let failFirst!: () => void
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>((_, rej) => (failFirst = () => rej(new TypeError('down')))) // A 在途
        return jsonResp({ revision: 1, stamp: body.stamp })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'A')
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10) // A 发出，在途
    outbox.post('p', 'f', 'B') // 编辑出 B
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10) // B 的防抖到期：复用 A 的 promise，清掉自己的定时器
    failFirst() // A 落地为 unavailable（不是最新版本，自己不安排重试）
    await vi.advanceTimersByTimeAsync(10)
    expect(puts).toHaveLength(1) // 失败之后按退避，不是立刻连发（立刻连发会把挂掉的后端打成死循环）
    await vi.advanceTimersByTimeAsync(outbox.RETRY_MS + 10) // 只靠退避定时器，没有 focus / online
    expect(puts.map((x) => x.text)).toEqual(['A', 'B']) // 对照：没有槽不变式时 B 永远挂在发件箱里
    expect(outbox.has('p', 'f')).toBe(false)
  })

  it('A 在途时编辑出 B，A 落地为其他结果（已接受）：B 立即发送，不等任何定时器', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    let okFirst!: () => void
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>((r) => (okFirst = () => r(jsonResp({ revision: 1, stamp: body.stamp }))))
        return jsonResp({ revision: 1, stamp: body.stamp })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'A')
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10)
    outbox.post('p', 'f', 'B')
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10)
    okFirst()
    await vi.advanceTimersByTimeAsync(10)
    expect(puts.map((x) => x.text)).toEqual(['A', 'B'])
    expect(outbox.has('p', 'f')).toBe(false)
  })
})

describe('aiDraftOutbox · inflight 只跟踪普通 PUT', () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
  afterEach(() => vi.useRealTimers())

  it('keepalive 连接半开挂住：不占用 inflight，随后新的编辑照常写入、普通请求成功后槽被清', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>(() => {}) // keepalive 挂住
        return jsonResp({ revision: 1, stamp: body.stamp })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'v1')
    void outbox.flush('p', 'f', true) // keepalive：挂住
    outbox.post('p', 'f', 'v2') // 新的编辑
    await outbox.flush('p', 'f') // 对照：keepalive 替换了 inflight 时，这里会一直复用那个卡住的 promise
    expect(puts.map((x) => x.text)).toEqual(['v1', 'v2'])
    expect(outbox.has('p', 'f')).toBe(false)
  })

  it('页面转入后台时发的 keepalive 一直挂住、之后再没有任何可见性事件：兜底定时器到点走一次普通 PUT，成功后槽被清；同槽不并发两个 PUT', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    let concurrent = 0
    let maxConcurrent = 0
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>(() => {}) // keepalive：半开，永远不落地
        concurrent++
        maxConcurrent = Math.max(maxConcurrent, concurrent)
        await Promise.resolve()
        concurrent--
        return jsonResp({ revision: 1, stamp: body.stamp })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'v1')
    outbox.flushAll(true) // visibilitychange → hidden：清掉防抖定时器、发 keepalive
    expect(puts.map((x) => x.text)).toEqual(['v1'])
    expect(outbox.has('p', 'f')).toBe(true)
    await vi.advanceTimersByTimeAsync(outbox.RETRY_MS - 100)
    expect(puts).toHaveLength(1) // 还没到兜底时间
    await vi.advanceTimersByTimeAsync(200) // 对照：没有兜底时，这里永远不会有第二次 PUT
    expect(puts.map((x) => x.text)).toEqual(['v1', 'v1'])
    expect(puts[1]!.stamp).toBe(puts[0]!.stamp) // 同戳同内容：服务器幂等，不会写两次
    expect(outbox.has('p', 'f')).toBe(false)
    expect(maxConcurrent).toBe(1)
  })

  it('超时之后迟到的原请求成功了（它其实写成功了）：落地收尾，槽被清，不再重试', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    let resolveFirst!: (r: Response) => void
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>((r) => (resolveFirst = r)) // 第一次一直挂，稍后才回
        throw new TypeError('down') // 重试都失败
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'v1')
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10)
    await vi.advanceTimersByTimeAsync(outbox.INFLIGHT_TIMEOUT_MS + 10) // 超时
    await vi.advanceTimersByTimeAsync(outbox.RETRY_MS + 10) // 重试（失败）
    expect(outbox.has('p', 'f')).toBe(true)
    resolveFirst(jsonResp({ revision: 1, stamp: puts[0].stamp })) // 原请求迟到地成功了
    await vi.advanceTimersByTimeAsync(10)
    expect(outbox.has('p', 'f')).toBe(false) // 迟到的结果也要处理：对照——被丢弃时槽会一直重试到天荒地老
  })

  it('普通请求挂住超过超时：按 unavailable 走正常重试，重试成功后槽被清', async () => {
    outbox.advance('p', 'f', 1)
    let n = 0
    const real = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
        puts.push({ text: body.text, stamp: body.stamp })
        n++
        if (n === 1) return new Promise<Response>(() => {}) // 第一次普通请求挂住
        return jsonResp({ revision: 1, stamp: body.stamp })
      }
      return real(url as RequestInfo, init)
    }) as typeof fetch
    outbox.post('p', 'f', 'v1')
    await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10) // 第一次发出（挂住）
    expect(puts).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(outbox.INFLIGHT_TIMEOUT_MS + 10) // 超时 → unavailable → 排退避重试
    await vi.advanceTimersByTimeAsync(outbox.RETRY_MS + 10)
    expect(puts.map((x) => x.text)).toEqual(['v1', 'v1']) // 重试了
    expect(outbox.has('p', 'f')).toBe(false)
  })
})

describe('aiDraftOutbox · 基线未知模式', () => {
  it('首读失败后第一次写成功，就退出基线未知：之后遇到没见过的戳被判 stale 时放弃，不换戳重写', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.markUnknown('p', 'f')

    outbox.post('p', 'f', 'v1')
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['v1'])
    expect(events).toContain('accepted')

    // 别的标签页写了更新稿（戳本页没见过）；本页下一版晚到被判 stale
    next = [{ stale: true, stamp: 9e15 }]
    outbox.post('p', 'f', 'v2')
    await outbox.flush('p', 'f')

    expect(puts.map((x) => x.text)).toEqual(['v1', 'v2']) // v2 只发了一次，没有换戳重写
    expect(events.at(-1)).toBe('abandoned')
    expect(outbox.peek('p', 'f')).toBeNull()
  })

  it('首读失败后第一次被接受的写入是删除（空文本留墓碑）：同样退出基线未知，之后没见过的戳按跨标签页规则放弃', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.markUnknown('p', 'f')
    outbox.post('p', 'f', '')
    await outbox.flush('p', 'f')
    expect(events).toContain('accepted')
    next = [{ stale: true, stamp: 9e15 }]
    outbox.post('p', 'f', 'v2')
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['', 'v2'])
    expect(events.at(-1)).toBe('abandoned')
  })

  it('首读失败后第一次写超限（413 带墓碑的戳）：带戳即退出基线未知，之后没见过的戳放弃，不换戳重写', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind === 'accepted' ? `accepted:${e.result}` : e.kind))
    outbox.markUnknown('p', 'f')
    next = [{ stale: false, stamp: 5e15, tooLarge: true }]
    outbox.post('p', 'f', 'x'.repeat(100))
    await outbox.flush('p', 'f')
    expect(events).toContain('accepted:too_large')
    next = [{ stale: true, stamp: 9e15 }]
    outbox.post('p', 'f', 'v2')
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['x'.repeat(100), 'v2']) // v2 只发了一次
    expect(events.at(-1)).toBe('abandoned')
  })

  it('对照：一次都没写成功（仍是基线未知）时，没见过的戳按「已见」处理，以本地为准换戳重写', async () => {
    outbox.markUnknown('p', 'f')
    next = [{ stale: true, stamp: 9e15 }]
    outbox.post('p', 'f', 'v1')
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['v1', 'v1'])
    expect(puts[1].stamp).toBeGreaterThan(puts[0].stamp)
  })
})

describe('aiDraftOutbox · 每版本基线与 stale 上限', () => {
  it('基线：对账之后读到的戳只影响之后新编辑的基线，不改已经 post 的版本——A 在途、别处保存了 B、对账读到 B，A 的 stale 仍被放弃', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.advance('p', 'f', 100) // A 编辑时输入框显示的文本对应的服务器戳
    outbox.post('p', 'f', 'A 版') // A 的基线 = 100
    outbox.noteRead('p', 'f') // 对账读到了别处的 B，但没有应用到输入框（本地有未落定的版本）：不推进基线
    next = [{ stale: true, stamp: 9e15 }]
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['A 版']) // A 没有被提升重写
    expect(events.at(-1)).toBe('abandoned')
  })

  it('基线 = 输入框当前显示的文本对应的服务器戳：读到别处的 B 但没应用，用户接着输入，新版本的基线仍是旧的——stale 时被放弃，B 保留', async () => {
    const events: string[] = []
    outbox.subscribe('p', 'f', (e) => events.push(e.kind))
    outbox.advance('p', 'f', 100) // 输入框显示的是戳 100 的那份文本
    outbox.post('p', 'f', 'A1') // 本地有延迟编辑
    outbox.noteRead('p', 'f') // 别处保存了 B（戳 9e15），对账读到了，但没应用
    outbox.post('p', 'f', 'A2') // 用户接着输入：A2 的基线必须还是 100，不是 B 的戳
    next = [{ stale: true, stamp: 9e15 }]
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['A2']) // 只发了 A2 一次，没有被提升重写
    expect(events.at(-1)).toBe('abandoned')
  })

  it('基线：stale 回的戳不推进基线（本页没有采用那份文本）', async () => {
    outbox.advance('p', 'f', 100)
    outbox.post('p', 'f', 'A')
    next = [{ stale: true, stamp: 9e15 }]
    await outbox.flush('p', 'f') // 放弃
    outbox.post('p', 'f', 'A 之后又写') // 新版本的基线仍是 100（没有采用 9e15 那份）
    next = [{ stale: true, stamp: 9e15 }]
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['A', 'A 之后又写']) // 第二次同样被放弃，没有以 9e15 为基线提升重写
  })

  it('基线：服务器戳 ≤ 这一版自己的基线（墙钟回拨一类）才提升，且用更大的戳重写', async () => {
    outbox.advance('p', 'f', 500)
    outbox.post('p', 'f', 'A 版') // 基线 500
    next = [{ stale: true, stamp: 400 }] // ≤ 基线
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['A 版', 'A 版'])
    expect(puts[1].stamp).toBeGreaterThan(puts[0].stamp)
  })

  describe('同一版本连续 stale 到上限之后', () => {
    beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
    afterEach(() => vi.useRealTimers())

    it('不再无限循环同一个被拒的戳：退避后重新走完整判定、用更大的新戳重写，对方停止后最终写成功；期间超过一分钟出灰字，成功后消失', async () => {
      const events: string[] = []
      outbox.subscribe('p', 'f', (e) => events.push(e.kind === 'stalled' ? `stalled:${e.on}` : e.kind))
      outbox.markUnknown('p', 'f') // 基线未知：stale 一律可提升（对方持续写，服务器戳一直更大）
      // 对方持续写：连续 60 次 stale（够触发好几轮上限 + 退避，累计超过一分钟），每次服务器戳都比我们刚发的更大；之后才停
      next = Array.from({ length: 60 }, (_, i) => ({ stale: true, stamp: 7e15 + i * 10 }))
      outbox.post('p', 'f', 'A 版')
      await vi.advanceTimersByTimeAsync(outbox.WRITE_DEBOUNCE_MS + 10)
      await vi.advanceTimersByTimeAsync(3 * 60_000) // 退避重试，足够长
      expect(puts.length).toBeGreaterThan(60) // 一直在试，不是停在上限
      const stamps = puts.map((x) => x.stamp)
      expect(new Set(stamps).size).toBe(stamps.length) // 每次发的戳都不同（上限之后也在推进），没有重复发同一个被拒的戳
      expect(stamps).toEqual([...stamps].sort((a, b) => a - b))
      expect(outbox.has('p', 'f')).toBe(false) // 最终写成功，槽清空
      expect(events).toContain('stalled:true') // 持续失败超过一分钟出灰字
      expect(events.at(-1)).toBe('accepted')
      expect(events.indexOf('stalled:false')).toBeGreaterThan(-1) // 成功后灰字消失
    })
  })
})

