/**
 * ⌘S 存完（本机那一档）发「排版写成了」（ADR 0101 §7）；存失败 / 冲突不发。
 *
 * 时间线的「保存」点挂在这个事件上而不是 `runManualSave` 里——#674 的「存进项目」
 * 分支提前 return，直接挂在函数末尾的打点在那条路上永远不执行。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const saveNow = vi.fn()
vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  saveNow: () => saveNow(),
}))

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

  it('事件带的是**发起保存那一刻**的上下文：存的途中换了项目 / 排版，事件说的仍是被存的那一份', async () => {
    const before = currentTimelineCtx()
    saveNow.mockImplementationOnce(async () => {
      useTimelineStore.getState().clear() // await 期间换了项目（代际 +1）
      return 'saved'
    })
    const ctxs: string[] = []
    off = onLayoutSaved((_via, { ctx }) => ctxs.push(ctx))
    await runManualSave()
    expect(ctxs).toEqual([before])
    expect(currentTimelineCtx()).not.toBe(before)
  })
})
