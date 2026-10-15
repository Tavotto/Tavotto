/**
 * 助手草稿的客户端（ADR 0132，存后端）：每个函数发什么请求、带什么条件、失败时怎么退化。
 * 后端语义（隔离、20 KB、条件删除、过期）在 `tests/test_ai_drafts.py`；这里用同语义的替身后端
 * （`test/aiDraftBackend.ts`）看前端这一侧。**整个模块不碰任何浏览器存储**——有一条用例直接盯着。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createDraftBackend, type DraftBackend } from '@/test/aiDraftBackend'
import {
  clearSubmitted,
  deleteDraftIf,
  DRAFT_MAX_BYTES,
  isDraftTooLarge,
  loadDraft,
  markSubmitted,
  removeAccepted,
  saveDraft,
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
    await markSubmitted(null, 'F', 'x', 'rid-aaaaaaaa', T)
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
    await expect(markSubmitted('pj', 'F', 'x', 'rid-aaaaaaaa', T)).resolves.toBeUndefined()
    await expect(clearSubmitted('pj', 'F', 'rid-aaaaaaaa')).resolves.toBeUndefined()
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

describe('账本按 rid 一条，删除带条件（两个标签页）', () => {
  it('A 记下待确认 → B 改了文本 → A 收到会话：B 的新稿还在，账本清掉', async () => {
    await saveDraft('pj', 'F', 'A 发出去的话')
    await markSubmitted('pj', 'F', 'A 发出去的话', 'rid-aaaaaaaa', T) // 标签页 A：点发送
    await saveDraft('pj', 'F', 'B 后来写的话') // 标签页 B：防抖保存
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa') // 标签页 A：后端接受
    expect(be.draft('pj', 'F')).toBe('B 后来写的话')
    expect(be.pending('pj', 'F')).toEqual([])
  })

  it('对照：文本没被动过，确认之后草稿一并删掉', async () => {
    await saveDraft('pj', 'F', '同一句话')
    await markSubmitted('pj', 'F', '同一句话', 'rid-aaaaaaaa', T)
    await removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(await loadDraft('pj', 'F')).toBeNull()
  })

  it('有账本时读到的是提交时的原文 + rid，不是 B 的新稿（未确认态重发的就是那一份）', async () => {
    await markSubmitted('pj', 'F', 'A 发出去的话', 'rid-aaaaaaaa', T)
    await saveDraft('pj', 'F', 'B 后来写的话')
    expect(await loadDraft('pj', 'F')).toMatchObject({ text: 'A 发出去的话', rid: 'rid-aaaaaaaa', sent: T })
  })

  it('两个标签页各自的 rid 并存；一边被拒绝只清自己那条', async () => {
    await markSubmitted('pj', 'F', 'A 的话', 'rid-aaaaaaaa', T)
    await markSubmitted('pj', 'F', 'B 的话', 'rid-bbbbbbbb', T)
    expect((await loadDraft('pj', 'F'))?.pendings?.map((p) => p.rid)).toEqual(['rid-aaaaaaaa', 'rid-bbbbbbbb'])
    await clearSubmitted('pj', 'F', 'rid-bbbbbbbb')
    expect((await loadDraft('pj', 'F'))?.pendings?.map((p) => p.rid)).toEqual(['rid-aaaaaaaa'])
  })

  it('deleteDraftIf：文本对不上不删', async () => {
    await saveDraft('pj', 'F', '新稿')
    expect(await deleteDraftIf('pj', 'F', '旧稿')).toBe(false)
    expect(be.draft('pj', 'F')).toBe('新稿')
    expect(await deleteDraftIf('pj', 'F', '新稿')).toBe(true)
  })
})
