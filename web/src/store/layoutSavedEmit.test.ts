/**
 * ⌘S 存完（本机那一档）发「排版写成了」（ADR 0101 §7）：跟**这次写没写成**走（`wrote`），
 * 不跟写完之后的实时状态走——写的途中又改过，状态照实是 dirty，按下那一刻的内容却已经在盘上
 * （Codex #679）。没写成（失败 / 冲突）不发。
 *
 * 时间线的「保存」点挂在这个事件上而不是 `runManualSave` 里——#674 的「存进项目」
 * 分支提前 return，直接挂在函数末尾的打点在那条路上永远不执行。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const saveNowImpl = vi.fn()
vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  saveNowWithResult: () => saveNowImpl(),
}))
/** 旧写法的替身：只给最终状态时，写成与否按「是不是存好了」推 */
const saveNow = {
  mockResolvedValueOnce: (state: string) =>
    saveNowImpl.mockResolvedValueOnce({ state, wrote: state === 'saved' || state === 'clean' }),
  mockImplementationOnce: (fn: () => Promise<string>) =>
    saveNowImpl.mockImplementationOnce(async () => {
      const state = await fn()
      return { state, wrote: state === 'saved' || state === 'clean' }
    }),
}

import { onLayoutSaved } from '@/lib/layoutSaved'
import { currentTimelineCtx } from '@/lib/timelineContext'
import { runManualSave } from '@/store/actions'
import { useTimelineStore } from '@/store/timelineStore'

let off: (() => void) | null = null
afterEach(() => off?.())

describe('runManualSave → emitLayoutSaved', () => {
  it.each([
    ['saved', ['local']],
    ['clean', ['local']],
    ['conflict', []],
    ['save_error', []],
  ])('saveNow 回 %s → 事件 %j', async (state, expected) => {
    saveNow.mockResolvedValueOnce(state)
    const seen: string[] = []
    off = onLayoutSaved((via) => seen.push(via))
    await runManualSave()
    expect(seen).toEqual(expected)
  })

  it.each([
    [{ state: 'dirty', wrote: true }, ['local']],
    [{ state: 'saved', wrote: false }, []],
  ])('跟「写没写成」走，不跟最终状态走：%j → 事件 %j', async (result, expected) => {
    saveNowImpl.mockResolvedValueOnce(result)
    const seen: string[] = []
    off = onLayoutSaved((via) => seen.push(via))
    await runManualSave()
    expect(seen).toEqual(expected)
  })

  it('事件带的是**发起保存那一刻**的上下文：存的途中换了项目 / 排版，事件说的仍是被存的那一份', async () => {
    const before = currentTimelineCtx()
    saveNow.mockImplementationOnce(async () => {
      useTimelineStore.getState().clear() // await 期间换了项目（代际 +1）
      return 'saved'
    })
    const ctxs: string[] = []
    off = onLayoutSaved((_via, { moment }) => ctxs.push(moment.ctx))
    await runManualSave()
    expect(ctxs).toEqual([before])
    expect(currentTimelineCtx()).not.toBe(before)
  })

  it('本机保存途中接着改：事件带的快照是按下 ⌘S 那一刻（序列化出去的那一份）', async () => {
    const { useDocumentStore } = await import('@/store/documentStore')
    const { literal } = await import('@/i18n')
    const pressed = useDocumentStore.getState().doc
    saveNow.mockImplementationOnce(async () => {
      useDocumentStore.getState().commit(literal('保存途中加字'), (d) => {
        d.guides.push({ axis: 'x', pos: 3 })
      })
      return 'saved'
    })
    const docs: unknown[] = []
    off = onLayoutSaved((_via, { moment }) => docs.push(moment.identity.doc))
    await runManualSave()
    expect(docs).toEqual([pressed])
    expect(docs[0]).toBe(pressed)
    expect(useDocumentStore.getState().doc).not.toBe(pressed)
  })
})
