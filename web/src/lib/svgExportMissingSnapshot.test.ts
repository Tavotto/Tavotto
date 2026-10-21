import { describe, expect, it } from 'vitest'
import { backendCodeMsg } from '@/lib/api'
import { formatMessage } from '@/i18n'

// 保存时不再提示缺 SVG 快照；真正导出 SVG 才说，并且要说清「先重新运行脚本」或「改用 PDF」。
// 「重新运行脚本」只属于执行来源缺快照（svg_snapshot_missing）；没有脚本的来源（静态 PDF / 位图）
// 走中性的 svg_source_unsupported，不能让人去做不可能的事。
describe('SVG export without a usable snapshot', () => {
  it('names the figure and says what to do in one plain sentence', () => {
    const text = formatMessage(backendCodeMsg('svg_snapshot_missing', { id: 'fig1.pdf' }, 'raw'))
    expect(text).toContain('fig1.pdf')
    expect(text).toContain('重新运行')
    expect(text).toContain('PDF')
    expect(text).not.toContain('快照')
    expect(text).not.toContain('{{')
  })

  it('keeps the shared unsupported-source message free of script advice', () => {
    const text = formatMessage(backendCodeMsg('svg_source_unsupported', { id: 'scan.png' }, 'raw'))
    expect(text).toContain('scan.png')
    expect(text).toContain('PDF')
    expect(text).not.toContain('脚本')
    expect(text).not.toContain('重新运行')
    expect(text).not.toContain('{{')
  })
})
