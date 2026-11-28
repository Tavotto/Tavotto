import { beforeEach, describe, expect, it, vi } from 'vitest'

import { formatMessage, i18n } from '@/i18n'
import { noticeLockedWrites, resetLockedWriteNotices } from '@/lib/lockedWriteNotice'

const deps = () => ({ ui: { setStatus: vi.fn(), clearStatusOwnedBy: vi.fn() } })
const text = (d: ReturnType<typeof deps>, i = 0) => formatMessage(d.ui.setStatus.mock.calls[i][0])

beforeEach(() => resetLockedWriteNotices())

describe('写已有文件被拒的提醒胶囊（ADR 0127 修订 2026-11-28）', () => {
  it('一句话点名文件名（不带路径前缀）；info 胶囊、没有按钮', async () => {
    await i18n.changeLanguage('zh-CN')
    const d = deps()
    noticeLockedWrites('o', ['data/plotted.csv'], d)
    expect(d.ui.setStatus).toHaveBeenCalledTimes(1)
    expect(text(d)).toContain('plotted.csv')
    expect(text(d)).not.toContain('data/')
    expect(d.ui.setStatus.mock.calls[0][1]).toBe('info')
    expect(d.ui.setStatus.mock.calls[0][2]).toEqual({ owner: 'o' })
  })

  it('多个文件：点名第一个并给总数；英文同样', async () => {
    await i18n.changeLanguage('en-US')
    const d = deps()
    noticeLockedWrites('o', ['a.csv', 'b.csv', 'c.csv'], d)
    expect(text(d)).toContain('a.csv')
    expect(text(d)).toContain('3 files')
    await i18n.changeLanguage('zh-CN')
  })

  it('同一组文件反复渲染只提醒一次；换了文件再提醒', () => {
    const d = deps()
    noticeLockedWrites('o', ['a.csv'], d)
    noticeLockedWrites('o', ['a.csv'], d)
    expect(d.ui.setStatus).toHaveBeenCalledTimes(1)
    noticeLockedWrites('o', ['b.csv'], d)
    expect(d.ui.setStatus).toHaveBeenCalledTimes(2)
  })

  it('之后的回包不再带它：撤掉提醒；再被拒时重新提醒', () => {
    const d = deps()
    noticeLockedWrites('o', ['a.csv'], d)
    noticeLockedWrites('o', undefined, d)
    expect(d.ui.clearStatusOwnedBy).toHaveBeenCalledWith('o')
    noticeLockedWrites('o', ['a.csv'], d)
    expect(d.ui.setStatus).toHaveBeenCalledTimes(2)
  })

  it('从没提醒过的面板没有 locked_writes：什么都不做', () => {
    const d = deps()
    noticeLockedWrites('o', [], d)
    expect(d.ui.setStatus).not.toHaveBeenCalled()
    expect(d.ui.clearStatusOwnedBy).not.toHaveBeenCalled()
  })
})
