import { describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { styleSummaryText } from './styleSummary'

describe('样式摘要一行', () => {
  it('字体 · 轴标题/刻度字号 · 轴标题加粗 · 线宽，只说样式里写着的', async () => {
    await setLocale('zh-CN')
    expect(
      styleSummaryText({
        element: {
          axis_label: { fontfamily: 'Times New Roman', fontsize: 9, weight: 'bold' },
          ticks: { fontsize: 8 },
          line: { linewidth: 0.75 },
        },
      }),
    ).toBe('Times New Roman · 9/8 pt · 轴标题加粗 · 线 0.75 pt')
  })

  it('字号相同只写一个；通用族名走中文名；没写数据线宽就用边框线宽', async () => {
    await setLocale('zh-CN')
    const text = styleSummaryText({
      element: {
        axis_label: { fontfamily: { primary: 'serif', fallback: 'DejaVu Serif' }, fontsize: 8 },
        ticks: { fontsize: 8 },
        axes: { spine_linewidth: 1 },
      },
    })
    expect(text).toBe('衬线 · 8 pt · 线 1 pt')
  })

  it('只写了标题字号时退到标题 / 图例；一项都没写 = null（不摆那一行）', async () => {
    await setLocale('zh-CN')
    expect(styleSummaryText({ element: { title: { fontsize: 12, weight: 'bold' } } })).toBe('12 pt · 标题加粗')
    expect(styleSummaryText({ element: {} })).toBeNull()
    expect(styleSummaryText(null)).toBeNull()
  })

  it('英文界面：同一份摘要换语言', async () => {
    await setLocale('en-US')
    try {
      expect(styleSummaryText({ element: { axis_label: { fontsize: 9, weight: 'bold' }, line: { linewidth: 0.5 } } })).toBe(
        '9 pt · bold axis titles · line 0.5 pt',
      )
    } finally {
      await setLocale('zh-CN')
    }
  })
})
