import { describe, expect, it } from 'vitest'
import { backendCodeMsg } from '@/lib/api'
import { formatMessage } from '@/i18n'

// 保存时不再提示缺 SVG 快照；真正导出 SVG 才说，并且要说清「先重新运行脚本」或「改用 PDF」。
describe('SVG export without a usable snapshot', () => {
  it('names the figure and says what to do in one plain sentence', () => {
    const text = formatMessage(backendCodeMsg('svg_source_unsupported', { id: 'fig1.pdf' }, 'raw'))
    expect(text).toContain('fig1.pdf')
    expect(text).toContain('重新运行')
    expect(text).toContain('PDF')
    expect(text).not.toContain('快照')
    expect(text).not.toContain('{{')
  })
})
