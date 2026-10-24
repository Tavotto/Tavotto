import { describe, expect, it, vi } from 'vitest'

import { formatMessage, i18n } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { noticeWriteBackMultipage, writeBackMultipageOwner } from '@/lib/writeBackMultipageNotice'

const deps = () => ({ ui: { setStatus: vi.fn(), setExportOpen: vi.fn() } })

describe('写回被多页 PDF 拒绝的胶囊（#1370）', () => {
  it('一句话点名文件 + 一个「导出」按钮，按钮打开导出对话框；不手写产品名', async () => {
    await i18n.changeLanguage('zh-CN')
    const d = deps()
    noticeWriteBackMultipage('all_samples.pdf', d)
    const [status, tone, opts] = d.ui.setStatus.mock.calls[0]
    expect(formatMessage(status)).toContain('all_samples.pdf')
    expect(formatMessage(status)).not.toContain(PRODUCT_NAME)
    expect(tone).toBe('info')
    expect(opts.owner).toBe(writeBackMultipageOwner('all_samples.pdf'))
    expect(formatMessage(opts.action.label)).toBe('导出')
    expect(d.ui.setExportOpen).not.toHaveBeenCalled()
    opts.action.run()
    expect(d.ui.setExportOpen).toHaveBeenCalledWith(true)
  })

  it('英文界面：按钮与句子都是英文', async () => {
    await i18n.changeLanguage('en-US')
    const d = deps()
    noticeWriteBackMultipage('a.pdf', d)
    const [status, , opts] = d.ui.setStatus.mock.calls[0]
    expect(formatMessage(status)).toContain('multi-page PDF')
    expect(formatMessage(opts.action.label)).toBe('Export')
    await i18n.changeLanguage('zh-CN')
  })
})
