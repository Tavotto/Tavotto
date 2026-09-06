import { describe, expect, it } from 'vitest'
import { cssFamilyOf, sampleFitScale, SAMPLE_DEFAULTS, styleSampleGeometry } from './styleSample'

describe('styleSampleGeometry（审计 T42：样式页的示例图从样式内容现算）', () => {
  it('每个角色的字号落到示例里自己那一笔；没设的角色回落到正文字号', () => {
    const g = styleSampleGeometry({
      element: {
        text: { fontsize: 8 },
        title: { fontsize: 11 },
        ticks: { fontsize: 7 },
        line: { linewidth: 1.25 },
        axes: { spine_linewidth: 0.4 },
      },
    })
    expect(g.titlePt).toBe(11)
    expect(g.tickPt).toBe(7)
    expect(g.axisPt).toBe(8) // 轴标题没单独设 → 正文
    expect(g.legendPt).toBe(8)
    expect(g.lineWidthPt).toBe(1.25)
    expect(g.spinePt).toBe(0.4)
  })

  it('什么都没设的样式画出示例默认值，而不是 0 或 NaN', () => {
    const g = styleSampleGeometry({})
    expect(g.titlePt).toBe(SAMPLE_DEFAULTS.basePt)
    expect(g.lineWidthPt).toBe(SAMPLE_DEFAULTS.lineWidthPt)
    expect(g.spinePt).toBe(SAMPLE_DEFAULTS.spinePt)
    expect(g.fontFamily).toBe('sans-serif')
    expect(styleSampleGeometry(null).titlePt).toBe(SAMPLE_DEFAULTS.basePt)
  })

  it('非法的数（0、负数、字符串）当作没设', () => {
    const g = styleSampleGeometry({ element: { text: { fontsize: 0 }, line: { linewidth: '2' } } })
    expect(g.titlePt).toBe(SAMPLE_DEFAULTS.basePt)
    expect(g.lineWidthPt).toBe(SAMPLE_DEFAULTS.lineWidthPt)
  })

  it('配色取样式的前两色，没有就用示例自己的', () => {
    expect(styleSampleGeometry({ palette: ['#111111', '#222222', '#333333'] }).colors).toEqual([
      '#111111',
      '#222222',
    ])
    expect(styleSampleGeometry({ palette: ['#111111'] }).colors).toEqual(['#1B3A6B', '#C0504D'])
  })

  it('字体族：通用族原样，具体字体名带上对应的通用回退', () => {
    expect(cssFamilyOf('serif')).toBe('serif')
    expect(cssFamilyOf('Times New Roman')).toBe('"Times New Roman", serif')
    expect(cssFamilyOf('Menlo')).toBe('"Menlo", monospace')
    expect(cssFamilyOf('Arial')).toBe('"Arial", sans-serif')
    expect(cssFamilyOf(null)).toBe('sans-serif')
  })
})

describe('sampleFitScale（示例图画得下这套字号吗）', () => {
  it('常规字号原样画', () => {
    expect(sampleFitScale(styleSampleGeometry({ element: { text: { fontsize: 9 } } }))).toBe(1)
  })

  it('大到画不下时整张等比缩——比例是示例的全部价值，不许被缩坏', () => {
    const g = styleSampleGeometry({
      element: { title: { fontsize: 56 }, ticks: { fontsize: 28 } },
    })
    const k = sampleFitScale(g)
    expect(k).toBeLessThan(1)
    expect(g.titlePt * k).toBeCloseTo(14, 6)
    // 标题是刻度的两倍，缩完还是两倍
    expect((g.titlePt * k) / (g.tickPt * k)).toBeCloseTo(g.titlePt / g.tickPt, 6)
  })

  it('线宽不参与：线粗不会把版面撑开', () => {
    expect(sampleFitScale(styleSampleGeometry({ element: { line: { linewidth: 9.5 } } }))).toBe(1)
  })
})
