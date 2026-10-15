/**
 * 助手草稿的存取规则（ADR 0132）。尺子：每条规则先有一个「规则不存在时会怎样」的对照，
 * 再看规则把它挡住。localStorage 只是缓存——任何失败都静默，永远不抛。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearSubmitted,
  DRAFT_MAX_BYTES,
  DRAFT_MAX_COUNT,
  DRAFT_PREFIX,
  DRAFT_TTL_MS,
  loadDraft,
  markSubmitted,
  removeAccepted,
  saveDraft,
  sweepDrafts,
  type DraftTarget,
} from './aiDraft'

const T: DraftTarget = { scope: 'figure', gid: null, label: null, target: '整张图' }
const DAY = 24 * 60 * 60 * 1000

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('按项目 × 面板文件分键', () => {
  it('A 项目的草稿在 B 项目读不到；同项目另一个面板也读不到；切回 A 还在', () => {
    saveDraft('pjA', 'Fig1.pdf', '把图例移到左上角')
    expect(loadDraft('pjA', 'Fig1.pdf')?.text).toBe('把图例移到左上角') // 对照：尺子量得到
    expect(loadDraft('pjB', 'Fig1.pdf')).toBeNull()
    expect(loadDraft('pjA', 'Fig2.pdf')).toBeNull()
    expect(loadDraft('pjA', 'Fig1.pdf')?.text).toBe('把图例移到左上角')
  })

  it('键名是 tavotto.ai.draft.v1.<项目>.<fileId>', () => {
    saveDraft('pjA', 'Fig1.pdf', 'x')
    expect(localStorage.getItem(`${DRAFT_PREFIX}pjA.Fig1.pdf`)).toBeTruthy()
  })

  it('没有项目认领（null）/ 没有目标：什么都不存、不抛', () => {
    expect(saveDraft(null, 'Fig1.pdf', 'x')).toBe('unavailable')
    expect(saveDraft('pjA', null, 'x')).toBe('unavailable')
    expect(localStorage.length).toBe(0)
    expect(loadDraft(null, null)).toBeNull()
  })

  it('空文本 = 删键（没有待确认请求时）', () => {
    saveDraft('pjA', 'Fig1.pdf', 'x')
    expect(saveDraft('pjA', 'Fig1.pdf', '')).toBe('removed')
    expect(loadDraft('pjA', 'Fig1.pdf')).toBeNull()
  })
})

describe('单稿上限 20 KB', () => {
  it('恰好上限能存；多一字节不存，且旧的较短版本一并删掉（刷新后不会恢复出一句过期的话）', () => {
    expect(saveDraft('pjA', 'F', 'a'.repeat(DRAFT_MAX_BYTES))).toBe('saved')
    expect(saveDraft('pjA', 'F', 'a'.repeat(DRAFT_MAX_BYTES + 1))).toBe('too_large')
    expect(loadDraft('pjA', 'F')).toBeNull()
  })
  it('按 UTF-8 字节而不是字符数：7000 个汉字（21000 字节）超限', () => {
    expect(saveDraft('pjA', 'F', '图'.repeat(7000))).toBe('too_large')
  })
})

describe('过期与 LRU', () => {
  it('30 天内读得到；过了 30 天读不到并当场删键', () => {
    const t0 = 1_000_000_000_000
    saveDraft('pjA', 'F', 'x', t0)
    expect(loadDraft('pjA', 'F', t0 + DRAFT_TTL_MS - DAY)?.text).toBe('x')
    // 上一次读刷新了时间；从那之后再过 31 天
    expect(loadDraft('pjA', 'F', t0 + DRAFT_TTL_MS - DAY + 31 * DAY)).toBeNull()
    expect(localStorage.getItem(`${DRAFT_PREFIX}pjA.F`)).toBeNull()
  })

  it('超过 30 份：淘汰最久没动的，刚写的那份留着', () => {
    for (let i = 0; i < DRAFT_MAX_COUNT; i++) saveDraft('pj', `F${i}`, 't', 1000 + i)
    saveDraft('pj', 'new', 't', 5000)
    expect(loadDraft('pj', 'F0', 5001)).toBeNull() // 最老的被淘汰
    expect(loadDraft('pj', 'F1', 5001)).not.toBeNull()
    expect(loadDraft('pj', 'new', 5001)).not.toBeNull()
    const keys = Object.keys(localStorage).filter((k) => k.startsWith(DRAFT_PREFIX))
    expect(keys.length).toBe(DRAFT_MAX_COUNT)
  })

  it('sweepDrafts 清掉过期的与损坏的', () => {
    const t0 = 2_000_000_000_000
    saveDraft('pj', 'old', 'x', t0 - DRAFT_TTL_MS - DAY)
    saveDraft('pj', 'fresh', 'x', t0)
    localStorage.setItem(`${DRAFT_PREFIX}pj.junk`, '{not json')
    expect(sweepDrafts({ now: t0 })).toBe(2)
    expect(loadDraft('pj', 'fresh', t0)).not.toBeNull()
  })
})

describe('目标面板已不存在', () => {
  it('sweepDrafts 带 exists 判据：面板没了的草稿不留', () => {
    saveDraft('pj', 'Gone.pdf', 'x')
    saveDraft('pj', 'Here.pdf', 'y')
    sweepDrafts({ exists: (_pj, f) => f === 'Here.pdf' })
    expect(loadDraft('pj', 'Gone.pdf')).toBeNull()
    expect(loadDraft('pj', 'Here.pdf')?.text).toBe('y')
  })

  it('有待确认请求的草稿不因面板没了而删（那是「到底发出去没有」的唯一线索）', () => {
    saveDraft('pj', 'Gone.pdf', 'x')
    markSubmitted('pj', 'Gone.pdf', 'x', 'rid-aaaaaaaa', T)
    sweepDrafts({ exists: () => false })
    expect(loadDraft('pj', 'Gone.pdf')?.rid).toBe('rid-aaaaaaaa')
  })
})

describe('请求 id 与删除时机', () => {
  it('markSubmitted 把 rid 与发出时的目标一起落盘；文本防抖再写不会冲掉它', () => {
    markSubmitted('pj', 'F', '指令', 'rid-aaaaaaaa', T)
    saveDraft('pj', 'F', '指令') // 防抖 flush
    const d = loadDraft('pj', 'F')
    expect(d?.rid).toBe('rid-aaaaaaaa')
    expect(d?.sent).toEqual(T)
  })

  it('removeAccepted 只删 rid 对得上的那份：另一次发送 / 另一个标签页写的不被抹掉', () => {
    markSubmitted('pj', 'F', '指令', 'rid-aaaaaaaa', T)
    removeAccepted('pj', 'F', 'rid-bbbbbbbb') // 别的请求的确认
    expect(loadDraft('pj', 'F')?.text).toBe('指令') // 对照：没删
    removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(loadDraft('pj', 'F')).toBeNull()
  })

  it('removeAccepted 不碰没有 rid 的新草稿（确认到达前用户已重新写了一段）', () => {
    markSubmitted('pj', 'F', '旧指令', 'rid-aaaaaaaa', T)
    clearSubmitted('pj', 'F', 'rid-aaaaaaaa')
    saveDraft('pj', 'F', '新写的')
    removeAccepted('pj', 'F', 'rid-aaaaaaaa')
    expect(loadDraft('pj', 'F')?.text).toBe('新写的')
  })

  it('clearSubmitted（明确失败）：留下文本、去掉 rid', () => {
    markSubmitted('pj', 'F', '指令', 'rid-aaaaaaaa', T)
    clearSubmitted('pj', 'F', 'rid-aaaaaaaa')
    const d = loadDraft('pj', 'F')
    expect(d?.text).toBe('指令')
    expect(d?.rid).toBeUndefined()
  })
})

describe('localStorage 不可用时只是退回内存', () => {
  it('读写删全抛：函数照常返回、不抛', () => {
    const boom = () => {
      throw new Error('denied')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(boom)
    expect(() => {
      expect(loadDraft('pj', 'F')).toBeNull()
      expect(saveDraft('pj', 'F', 'x')).toBe('unavailable')
      markSubmitted('pj', 'F', 'x', 'rid-aaaaaaaa', T)
      clearSubmitted('pj', 'F', 'rid-aaaaaaaa')
      removeAccepted('pj', 'F', 'rid-aaaaaaaa')
      sweepDrafts()
    }).not.toThrow()
  })

  it('值被别的东西写坏（非 JSON / 形状不对 / 项目对不上）：当没有，并删掉', () => {
    for (const bad of ['{x', '"str"', JSON.stringify({ project: 'other', fileId: 'F', text: 'x', at: 1 })]) {
      localStorage.setItem(`${DRAFT_PREFIX}pj.F`, bad)
      expect(loadDraft('pj', 'F')).toBeNull()
      expect(localStorage.getItem(`${DRAFT_PREFIX}pj.F`)).toBeNull()
    }
  })
})
