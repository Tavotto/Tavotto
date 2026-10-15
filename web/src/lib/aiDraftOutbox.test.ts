/**
 * 发件箱的「基线未知」模式（首读失败）只在还没有任何一次写成功时成立：写成功过一次就退出，
 * 之后的 stale 若带着本页没见过的戳（别的标签页写了更新稿），按跨标签页规则放弃，不以本地为准重写（#1098 Codex）。
 * 替身是 fetch 层：发件箱在全局 setup 里已被加载，模块 mock 换不掉它对 `./aiDraft` 的引用。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as outbox from './aiDraftOutbox'

const puts: { text: string; stamp: number }[] = []
let next: { stale: boolean; stamp: number }[] = []
const realFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/api/ai/drafts') && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body)) as { text: string; stamp: number }
      puts.push({ text: body.text, stamp: body.stamp })
      const r = next.shift() ?? { stale: false, stamp: body.stamp }
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
  puts.length = 0
  next = []
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

  it('对照：一次都没写成功（仍是基线未知）时，没见过的戳按「已见」处理，以本地为准换戳重写', async () => {
    outbox.markUnknown('p', 'f')
    next = [{ stale: true, stamp: 9e15 }]
    outbox.post('p', 'f', 'v1')
    await outbox.flush('p', 'f')
    expect(puts.map((x) => x.text)).toEqual(['v1', 'v1'])
    expect(puts[1].stamp).toBeGreaterThan(puts[0].stamp)
  })
})
