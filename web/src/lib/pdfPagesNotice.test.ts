import { beforeEach, describe, expect, it, vi } from 'vitest'

import { formatMessage, i18n } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { noticePdfPages, resetPdfPagesNotices } from '@/lib/pdfPagesNotice'

const deps = () => ({ ui: { setStatus: vi.fn(), clearStatusOwnedBy: vi.fn() } })
const text = (d: ReturnType<typeof deps>) => formatMessage(d.ui.setStatus.mock.calls[0][0])

beforeEach(() => resetPdfPagesNotices())

describe('多页 PDF 提醒胶囊（#1370）', () => {
  it('一句话点名 PDF、说清每页当一张图、导出是单页；产品名来自 brand 常量；无按钮', async () => {
    await i18n.changeLanguage('zh-CN')
    const d = deps()
    noticePdfPages('o', [{ name: 'all_samples.pdf', pages: 3, captured: 3, stems: [] }], d)
    const sentence = text(d)
    expect(sentence).toContain('3 张图')
    expect(sentence).toContain('all_samples.pdf')
    expect(sentence).toContain(PRODUCT_NAME)
    expect(sentence).toContain('单页文件')
    expect(d.ui.setStatus.mock.calls[0][2]).toEqual({ owner: 'o' }) // 没有 action
  })

  it('英文：单复数与产品名', async () => {
    await i18n.changeLanguage('en-US')
    const one = deps()
    noticePdfPages('o1', [{ name: 'a.pdf', pages: 1, captured: 1, stems: [] }], one)
    expect(text(one)).toContain('1 figure into a.pdf')
    const many = deps()
    noticePdfPages('o2', [{ name: 'a.pdf', pages: 4, captured: 4, stems: [] }], many)
    expect(text(many)).toContain('4 figures into a.pdf')
    expect(text(many)).toContain(PRODUCT_NAME)
    await i18n.changeLanguage('zh-CN')
  })

  it('缓冲区（没有文件名）与多份 PDF 用不点名的那句；超限只说一次', async () => {
    await i18n.changeLanguage('zh-CN')
    const d = deps()
    noticePdfPages(
      'o',
      [
        { name: null, pages: 2, captured: 2, stems: [] },
        { name: 'b.pdf', pages: 70, captured: 64, stems: [], omitted: 6 },
      ],
      d,
    )
    const sentence = text(d)
    expect(sentence).not.toContain('b.pdf')
    expect(sentence).toContain('另有 6 张超出上限')
  })

  it('份数超限的不点名条目（name 为 null、captured 为 0）：计入总数并说明省略', async () => {
    await i18n.changeLanguage('zh-CN')
    const d = deps()
    noticePdfPages(
      'o',
      [
        { name: 'a.pdf', pages: 1, captured: 1, stems: ['a'] },
        { name: null, pages: 1, captured: 0, stems: [], omitted: 1 },
      ],
      d,
    )
    const sentence = text(d)
    expect(sentence).toContain('2 张图')
    expect(sentence).toContain('另有 1 张超出上限')
  })

  it('同一份证据只提醒一次；没有多页 PDF 就撤掉', () => {
    const d = deps()
    const info = [{ name: 'a.pdf', pages: 2, captured: 2, stems: [] }]
    noticePdfPages('o', info, d)
    noticePdfPages('o', info, d)
    expect(d.ui.setStatus).toHaveBeenCalledTimes(1)
    noticePdfPages('o', undefined, d)
    expect(d.ui.clearStatusOwnedBy).toHaveBeenCalledWith('o')
  })
})
