/**
 * QA-WIN-33：「最近的排版」点别的项目的条目，先弹确认再开；取消什么都不做；同项目 / 归属未记不弹。
 * 主语：确认框 = uiStore.confirm 里挂着请求；"打开了" = readAutosaveDoc 被调用。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  readAutosaveDoc: vi.fn(async () => ({ doc: null, notice: null })),
}))

import { setCurrentProjectId } from '@/lib/session'
import { readAutosaveDoc } from '@/store/documentStore'
import { openRecentEntry } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'

const tick = () => new Promise((r) => setTimeout(r, 0))
const pending = () => useUiStore.getState().confirm

beforeEach(() => {
  vi.mocked(readAutosaveDoc).mockClear()
  useUiStore.getState().setConfirm(null)
  setCurrentProjectId('p_here')
})

describe('openRecentEntry', () => {
  it('别的项目的排版：弹确认，且不先打开', async () => {
    const p = openRecentEntry({ id: 'd1', projectId: 'p_other', projectName: '乙' })
    await tick()
    expect(pending()).not.toBeNull()
    expect(pending()?.body).toMatchObject({ values: { name: '乙' } })
    expect(readAutosaveDoc).not.toHaveBeenCalled()
    pending()!.resolve(false)
    await p
  })

  it('取消：不打开', async () => {
    const p = openRecentEntry({ id: 'd1', projectId: 'p_other' })
    await tick()
    pending()!.resolve(false)
    await p
    expect(readAutosaveDoc).not.toHaveBeenCalled()
  })

  it('确认：才打开', async () => {
    const p = openRecentEntry({ id: 'd1', projectId: 'p_other' })
    await tick()
    pending()!.resolve(true)
    await p
    expect(readAutosaveDoc).toHaveBeenCalledWith('d1')
  })

  it('同项目 / 没记归属：不弹，直接开', async () => {
    await openRecentEntry({ id: 'd2', projectId: 'p_here' })
    await openRecentEntry({ id: 'd3' })
    expect(pending()).toBeNull()
    expect(readAutosaveDoc).toHaveBeenCalledTimes(2)
  })
})
