/**
 * 助手草稿的客户端（ADR 0132，存后端）：每个函数发什么请求、带什么条件、失败时怎么退化。
 * 后端语义（隔离、20 KB、条件删除、过期）在 `tests/test_ai_drafts.py`；这里用同语义的替身后端
 * （`test/aiDraftBackend.ts`）看前端这一侧。**整个模块不碰任何浏览器存储**——有一条用例直接盯着。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createDraftBackend, type DraftBackend } from '@/test/aiDraftBackend'
import {
  __resetWriteStampForTests,
  deleteDraftIf,
  DRAFT_MAX_BYTES,
  isDraftTooLarge,
  loadDraft,
  markSubmitted,
  nextWriteStamp,
  observeStamp,
  removeAccepted,
  saveDraft,
  saveDraftStamped,
  textHash,
  type DraftTarget,
} from './aiDraft'

const T: DraftTarget = { scope: 'figure', gid: null, label: null, target: '整张图' }
let be: DraftBackend

beforeEach(() => {
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
    be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
})

describe('存与读：按项目 × 面板文件', () => {
  it('A 项目的草稿在 B 项目读不到；同项目另一个面板也读不到；对照：A 里读得到', async () => {
    await saveDraft('pjA', 'Fig1.pdf', '把图例移到左上角')
    expect((await loadDraft('pjA', 'Fig1.pdf'))?.text).toBe('把图例移到左上角')
    expect(await loadDraft('pjB', 'Fig1.pdf')).toBeNull()
    expect(await loadDraft('pjA', 'Fig2.pdf')).toBeNull()
  })

  it('没有项目认领（null）/ 没有目标：一个请求都不发', async () => {
    expect(await saveDraft(null, 'F', 'x')).toBe('unavailable')
    expect(await saveDraft('pj', null, 'x')).toBe('unavailable')
    expect(await loadDraft(null, null)).toBeNull()
    await expect(markSubmitted(null, 'F', 'x', 'rid-aaaaaaaa', T)).resolves.toBe('ok') // 没有项目认领：无从记账
    expect(be.calls).toEqual([])
  })

  it('空文本 = 删', async () => {
    await saveDraft('pj', 'F', 'x')
    expect(await saveDraft('pj', 'F', '')).toBe('removed')
    expect(await loadDraft('pj', 'F')).toBeNull()
  })

  it('不碰 localStorage / sessionStorage：写读删一遍之后两个存储里都是空的', async () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem')
    await saveDraft('pj', 'F', '文本')
    await markSubmitted('pj', 'F', '文本', 'rid-aaaaaaaa', T)
    await loadDraft('pj', 'F')
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(spy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    spy.mockRestore()
  })
})

describe('单稿上限（后端 413 为准）', () => {
  it('恰好 20 KB 能存；多一字节后端 413 → too_large，旧稿也不留', async () => {
    expect(await saveDraft('pj', 'F', 'a'.repeat(DRAFT_MAX_BYTES))).toBe('saved')
    expect(await saveDraft('pj', 'F', 'a'.repeat(DRAFT_MAX_BYTES + 1))).toBe('too_large')
    expect(await loadDraft('pj', 'F')).toBeNull()
  })
  it('前端先判（按 UTF-8 字节）：7000 个汉字 = 21000 字节算过长', () => {
    expect(isDraftTooLarge('图'.repeat(7000))).toBe(true)
    expect(isDraftTooLarge('a'.repeat(DRAFT_MAX_BYTES))).toBe(false)
  })
})

describe('失败时不抛、回枚举', () => {
  it('后端不可用：save → unavailable，load → null，账本函数静默', async () => {
    be.setDown(true)
    await expect(saveDraft('pj', 'F', 'x')).resolves.toBe('unavailable')
    await expect(loadDraft('pj', 'F')).resolves.toBeNull()
    await expect(markSubmitted('pj', 'F', 'x', 'rid-aaaaaaaa', T)).resolves.toBe('unknown') // 不知道写没写：调用方据此不发、同 rid 重试
    await expect(removeAccepted('pj', 'F', 'rid-aaaaaaaa')).resolves.toBeUndefined()
    await expect(deleteDraftIf('pj', 'F', 'x')).resolves.toBe(false)
  })
})

describe('keepalive 冲刷', () => {
  it('pagehide 路径：saveDraft(..., true) 的请求带 keepalive；平常的不带', async () => {
    await saveDraft('pj', 'F', 'a', false)
    await saveDraft('pj', 'F', 'b', true)
    expect(be.calls.map((c) => c.keepalive)).toEqual([false, true])
  })
  it('正文超过 keepalive 的 64 KB 上限就不带（带了浏览器会直接拒）', async () => {
    await saveDraft('pj', 'F', 'a'.repeat(100 * 1024), true) // 后端会 413，但请求本身不该因 keepalive 被浏览器拒
    expect(be.calls[0].keepalive).toBe(false)
  })
})

describe('写入戳', () => {
  it('nextWriteStamp 严格递增，即使墙钟回拨；observeStamp 把下限抬到服务端见过的戳之上', () => {
    const real = Date.now
    try {
      const a = nextWriteStamp()
      Date.now = () => 1 // 墙钟回拨
      const b = nextWriteStamp()
      const c = nextWriteStamp()
      expect(b).toBeGreaterThan(a)
      expect(c).toBeGreaterThan(b)
      observeStamp(c + 1_000_000)
      expect(nextWriteStamp()).toBeGreaterThan(c + 1_000_000)
      observeStamp('garbage') // 不是数字：忽略
      observeStamp(Number.NaN)
    } finally {
      Date.now = real
    }
  })

  it('每次 PUT 都带一个新的、更大的戳（含 keepalive）；被服务端判 stale 回 stale', async () => {
    await saveDraft('pj', 'F', 'a')
    await saveDraft('pj', 'F', 'b', true)
    const stamps = be.calls.filter((c) => c.method === 'PUT').map((c) => c.body.stamp as number)
    expect(stamps).toHaveLength(2)
    expect(stamps[1]).toBeGreaterThan(stamps[0])
    // 别的标签页用更大的戳写过：本页之后的写入（戳更小）被判 stale，且不覆盖
    be.putStamped('pj', 'F', '别的标签页', Date.now() * 1000 + 9_000_000_000)
    expect(await saveDraft('pj', 'F', '本页的')).toBe('stale')
    expect(be.draft('pj', 'F')).toBe('别的标签页')
    // 本页抬高了下限：再写一次就比它新，被接受
    expect(await saveDraft('pj', 'F', '本页再写')).toBe('saved')
    expect(be.draft('pj', 'F')).toBe('本页再写')
  })
})

describe('textHash：与后端 ai_history.text_hash 逐字节同算法', () => {
  // 固定文本的期望值由后端实现算出（tests/test_ai_drafts.py 钉同一组）：两边任何一边改了算法，两个测试同时红
  const FIXTURES: [string, string][] = [
    ['', 'cbf29ce484222325'],
    ['a', 'af63dc4c8601ec8c'],
    ['你好，图例', '38f6689529094f15'],
    ['😀 emoji + 中文 + \n换行', '34de6d5cb9e118b8'],
    ['x'.repeat(5000), '5d45fa080be4e345'],
  ]
  it.each(FIXTURES)('%j', (text, expected) => {
    expect(textHash(text)).toBe(expected)
  })
})

describe('写入戳：不同页面不可能生成相同的戳', () => {
  it('两个页面（不同 client 段）在同一毫秒内各生成一个戳：不相等；每个页面内严格递增；量级仍是墙钟微秒', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      vi.setSystemTime(1_800_000_000_000)
      __resetWriteStampForTests(5) // 页面 A
      const a = [nextWriteStamp(), nextWriteStamp(), nextWriteStamp()]
      __resetWriteStampForTests(900) // 页面 B：同一毫秒、重新起步
      const b = [nextWriteStamp(), nextWriteStamp(), nextWriteStamp()]
      expect(a.some((x) => b.includes(x))).toBe(false) // 没有任何一个戳相等
      expect(a[1]).toBeGreaterThan(a[0])
      expect(a[2]).toBeGreaterThan(a[1])
      expect(a[0] % 1024).toBe(5)
      expect(b[0] % 1024).toBe(900)
      expect(a[0]).toBeGreaterThanOrEqual(1_800_000_000_000 * 1000)
      expect(a[0]).toBeLessThan(1_800_000_000_000 * 1000 + 3 * 1024 + 1024) // 量级没有漂移
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('戳代表内容版本的时刻，不是发送尝试的时刻', () => {
  it('显式给的戳原样上线；旧版本哪怕更晚才发（重试 / keepalive），戳更小就被服务端拒绝，不覆盖新版本', async () => {
    const vOld = nextWriteStamp() // 旧版本编辑时分配的戳
    const vNew = nextWriteStamp() // 新版本（更晚编辑）
    expect((await saveDraftStamped('pj', 'F', '新版本', false, vNew)).result).toBe('saved')
    // 旧版本现在才重试：戳是它自己编辑时的，更小
    expect((await saveDraftStamped('pj', 'F', '旧版本', true, vOld)).result).toBe('stale')
    expect(be.draft('pj', 'F')).toBe('新版本')
    expect(be.calls.map((c) => c.body.stamp)).toEqual([vNew, vOld]) // 上线的就是调用方给的戳，发送时没有重新取
  })

  it('同一版本的多次发送带同一个戳', async () => {
    const v = nextWriteStamp()
    be.setDown(true)
    await saveDraftStamped('pj', 'F', '同一版', false, v)
    await saveDraftStamped('pj', 'F', '同一版', false, v)
    be.setDown(false)
    await saveDraftStamped('pj', 'F', '同一版', false, v)
    expect(new Set(be.calls.filter((c) => c.method === 'PUT').map((c) => c.body.stamp))).toEqual(new Set([v]))
  })
})

describe('账本按 rid 一条，删除带条件（两个标签页）', () => {
  it('A 记下待确认 → B 改了文本 → A 收到会话：B 的新稿还在，账本清掉', async () => {
    const a = await saveDraftStamped('pj', 'F', 'A 发出去的话')
    await markSubmitted('pj', 'F', 'A 发出去的话', 'rid-aaaaaaaa', T, null, a.stamp ?? null) // 标签页 A：点发送（记下草稿戳）
    await saveDraft('pj', 'F', 'B 后来写的话') // 标签页 B：防抖保存（戳更新）
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa') // 标签页 A：后端接受
    expect(be.draft('pj', 'F')).toBe('B 后来写的话')
    expect(be.pending('pj', 'F')).toEqual([])
  })

  it('对照：草稿自提交后没被改过，确认之后草稿一并删掉；提交文本与草稿文本不同（trim）也照删——比的是戳不是文本', async () => {
    const a = await saveDraftStamped('pj', 'F', '  同一句话\n') // 草稿原样（带空白）
    await markSubmitted('pj', 'F', '同一句话', 'rid-aaaaaaaa', T, null, a.stamp ?? null) // 账本里是 trim 过的
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(await loadDraft('pj', 'F')).toBeNull()
  })

  it('账本没记草稿戳（发送时草稿没写成）：确认只清账本，不动草稿', async () => {
    await saveDraft('pj', 'F', '不知道是不是同一份')
    await markSubmitted('pj', 'F', 'x', 'rid-aaaaaaaa', T)
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(be.draft('pj', 'F')).toBe('不知道是不是同一份')
    expect(be.pending('pj', 'F')).toEqual([])
  })

  it('草稿文本与待确认原文是两份独立状态：读回来各是各的（待确认重发用的是账本里那一份）', async () => {
    await markSubmitted('pj', 'F', 'A 发出去的话', 'rid-aaaaaaaa', T)
    await saveDraft('pj', 'F', 'B 后来写的话')
    const d = await loadDraft('pj', 'F')
    expect(d?.text).toBe('B 后来写的话') // 草稿文本永远是草稿
    expect(d?.pendings).toEqual([{ rid: 'rid-aaaaaaaa', text: 'A 发出去的话', sent: T }]) // 待确认原文是另一份
  })

  it('另一个标签页在同面板留了没落定的：新 rid 被后端拒（409 ai_pending_unresolved，带它的 rid），什么都不写；supersedes 显式取代它才放行', async () => {
    await markSubmitted('pj', 'F', 'A 的话', 'rid-aaaaaaaa', T)
    await expect(markSubmitted('pj', 'F', 'B 的话', 'rid-bbbbbbbb', T)).rejects.toMatchObject({
      status: 409,
      body: { code: 'ai_pending_unresolved', rids: ['rid-aaaaaaaa'] },
    })
    expect((await loadDraft('pj', 'F'))?.pendings?.map((p) => p.rid)).toEqual(['rid-aaaaaaaa'])
    await markSubmitted('pj', 'F', 'C 的话', 'rid-cccccccc', T, null, null, ['rid-aaaaaaaa'])
    expect((await loadDraft('pj', 'F'))?.pendings?.map((p) => p.rid)).toEqual(['rid-cccccccc'])
  })

  it('deleteDraftIf：文本对不上不删', async () => {
    await saveDraft('pj', 'F', '新稿')
    expect(await deleteDraftIf('pj', 'F', '旧稿')).toBe(false)
    expect(be.draft('pj', 'F')).toBe('新稿')
    expect(await deleteDraftIf('pj', 'F', '新稿')).toBe(true)
  })
})
