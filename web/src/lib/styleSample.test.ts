import { describe, expect, it } from 'vitest'
import { cssFamilyOf, fitSampleGeometry, sampleFitScale, SAMPLE_DEFAULTS, styleSampleGeometry } from './styleSample'

describe('styleSampleGeometry（审计 T42：样式页的示例图从样式内容现算）', () => {
  it('每个角色的字号落到示例里自己那一笔；没设的角色是示例默认，不跟「其余文字」（应用时不会跟着变）', () => {
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
    expect(g.axisPt).toBe(SAMPLE_DEFAULTS.axisPt) // 轴标题没单独设：`text` 的 8 只落在 `text` 角色上
    expect(g.legendPt).toBe(SAMPLE_DEFAULTS.legendPt)
    expect(g.lineWidthPt).toBe(1.25)
    expect(g.spinePt).toBe(0.4)
  })

  it('什么都没设的样式画出示例默认值，而不是 0 或 NaN', () => {
    const g = styleSampleGeometry({})
    expect(g.titlePt).toBe(SAMPLE_DEFAULTS.titlePt)
    expect(g.lineWidthPt).toBe(SAMPLE_DEFAULTS.lineWidthPt)
    expect(g.spinePt).toBe(SAMPLE_DEFAULTS.spinePt)
    expect(g.faces.title.fontFamily).toBe('sans-serif')
    expect(styleSampleGeometry(null)).toEqual(g)
  })

  it('非法的数（0、负数、字符串）当作没设', () => {
    const g = styleSampleGeometry({ element: { title: { fontsize: 0 }, line: { linewidth: '2' } } })
    expect(g.titlePt).toBe(SAMPLE_DEFAULTS.titlePt)
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

  it('线宽不决定系数：线粗不会把版面撑开（但字号缩时线宽跟着缩，见下）', () => {
    expect(sampleFitScale(styleSampleGeometry({ element: { line: { linewidth: 9.5 } } }))).toBe(1)
  })

  it('每类文字各自的字面：图例的在 legend_text 上；字体与粗斜体都不从「其余文字」回落（应用时不会跟着变）', () => {
    const g = styleSampleGeometry({
      element: {
        text: { fontfamily: 'serif', weight: 'bold' },
        title: { weight: 'bold', fontfamily: 'Arial' },
        legend_text: { style: 'italic' },
      },
    })
    expect(g.faces.title).toEqual({ fontFamily: '"Arial", sans-serif', bold: true, italic: false })
    expect(g.faces.legend).toEqual({ fontFamily: 'sans-serif', bold: false, italic: true })
    expect(g.faces.axis.fontFamily).toBe('sans-serif')
    expect(g.faces.axis.bold).toBe(false)
    expect(g.faces.tick.bold).toBe(false)
  })

  it('刻度：方向 / 长度 / 线宽按样式；长度 0 是真值，线宽没设是示例默认（不跟边框）', () => {
    const g = styleSampleGeometry({ element: { ticks: { direction: 'in', length: 0 }, axes: { spine_linewidth: 0.4 } } })
    expect(g.tickDirection).toBe('in')
    expect(g.tickLengthPt).toBe(0)
    expect(g.tickWidthPt).toBe(SAMPLE_DEFAULTS.tickWidthPt)
    const d = styleSampleGeometry({ element: { ticks: { direction: 'sideways', width: 1.2 } } })
    expect(d.tickDirection).toBe('out')
    expect(d.tickLengthPt).toBe(SAMPLE_DEFAULTS.tickLengthPt)
    expect(d.tickWidthPt).toBe(1.2)
  })
})

describe('styleSampleGeometry：每一维单独改，示例里只有对应的那一笔变（Codex #703）', () => {
  // 每条 = 样式里的一个点分路径、一个非默认值、示例几何里应当变的那一个字段。
  // 行 × 角色取自面板的行表：字号在 `legend`、图例字体 / 字面在 `legend_text`
  const CASES: Array<[string, unknown, string]> = [
    ['element.title.fontsize', 12, 'titlePt'],
    ['element.axis_label.fontsize', 12, 'axisPt'],
    ['element.ticks.fontsize', 12, 'tickPt'],
    ['element.legend.fontsize', 12, 'legendPt'],
    ['element.title.fontfamily', 'serif', 'faces.title'],
    ['element.axis_label.fontfamily', 'serif', 'faces.axis'],
    ['element.ticks.fontfamily', 'serif', 'faces.tick'],
    ['element.legend_text.fontfamily', 'serif', 'faces.legend'],
    ['element.title.weight', 'bold', 'faces.title'],
    ['element.axis_label.style', 'italic', 'faces.axis'],
    ['element.legend_text.weight', 'bold', 'faces.legend'],
    ['element.line.linewidth', 2, 'lineWidthPt'],
    ['element.axes.spine_linewidth', 2, 'spinePt'],
    ['element.ticks.direction', 'in', 'tickDirection'],
    ['element.ticks.length', 7, 'tickLengthPt'],
    ['element.ticks.width', 2, 'tickWidthPt'],
    ['palette', ['#000000', '#ffffff'], 'colors'],
  ]
  const build = (path: string, value: unknown) => {
    const root: Record<string, unknown> = {}
    const keys = path.split('.')
    let cur = root
    for (const k of keys.slice(0, -1)) cur = (cur[k] = {}) as Record<string, unknown>
    cur[keys[keys.length - 1]] = value
    return root
  }
  const flat = (g: object, prefix = ''): Record<string, string> =>
    Object.fromEntries(
      Object.entries(g).flatMap(([k, v]) =>
        k === 'faces'
          ? Object.entries(v as object).map(([fk, fv]) => [`faces.${fk}`, JSON.stringify(fv)])
          : [[prefix + k, JSON.stringify(v)]],
      ),
    )
  const base = flat(styleSampleGeometry({}))

  it.each(CASES)('%s', (path, value, field) => {
    const got = flat(styleSampleGeometry(build(path, value)))
    const changed = Object.keys(base).filter((k) => got[k] !== base[k])
    expect(changed).toEqual([field])
  })

  it('「其余文字」（element.text）的字号 / 字体 / 字面不动示例里的任何一笔', () => {
    for (const [prop, value] of [
      ['fontsize', 14],
      ['fontfamily', 'serif'],
      ['weight', 'bold'],
      ['style', 'italic'],
    ] as const) {
      expect(flat(styleSampleGeometry(build(`element.text.${prop}`, value)))).toEqual(base)
    }
  })
})

describe('fitSampleGeometry：字号超预算时，所有由样式决定的长度同一个系数缩（Codex #703）', () => {
  const big = styleSampleGeometry({
    element: {
      title: { fontsize: 72 },
      ticks: { fontsize: 72, direction: 'out', length: 20, width: 10 },
      axes: { spine_linewidth: 4 },
      line: { linewidth: 3 },
    },
  })

  it('几何里每一个数都乘同一个系数，别的字段原样', () => {
    const k = sampleFitScale(big)
    expect(k).toBeLessThan(1)
    const fit = fitSampleGeometry(big)
    const numeric = Object.entries(big).filter(([, v]) => typeof v === 'number')
    // 几何里的长度一个不落（期望的键写死：几何多了长度，这里要跟着认）
    expect(numeric.map(([key]) => key).sort()).toEqual(
      ['axisPt', 'legendPt', 'lineWidthPt', 'spinePt', 'tickLengthPt', 'tickPt', 'tickWidthPt', 'titlePt'].sort(),
    )
    for (const [key, v] of numeric) expect((fit as unknown as Record<string, number>)[key], key).toBeCloseTo((v as number) * k, 9)
    for (const [key, v] of Object.entries(big)) if (typeof v !== 'number') expect((fit as unknown as Record<string, unknown>)[key]).toEqual(v)
  })

  it('比例与样式一致：刻度长 : 刻度字号 = 20 : 72，刻度线宽 : 刻度字号 = 10 : 72', () => {
    const fit = fitSampleGeometry(big)
    expect(fit.tickPt).toBeCloseTo(14, 9)
    expect(fit.tickLengthPt / fit.tickPt).toBeCloseTo(20 / 72, 9)
    expect(fit.tickWidthPt / fit.tickPt).toBeCloseTo(10 / 72, 9)
    expect(fit.spinePt / fit.tickPt).toBeCloseTo(4 / 72, 9)
    expect(fit.lineWidthPt / fit.tickPt).toBeCloseTo(3 / 72, 9)
  })

  it('字号在预算内时原样返回（同一个对象）', () => {
    const g = styleSampleGeometry({ element: { ticks: { length: 20 } } })
    expect(fitSampleGeometry(g)).toBe(g)
  })
})

describe('示例图的粗 / 斜体认非规范值（与引擎同一口径，#704）', () => {
  it('semibold / 600 / "700" 画粗，light / 500 不画粗；oblique 画斜', () => {
    const g = styleSampleGeometry({
      element: {
        title: { weight: 'semibold' },
        axis_label: { weight: 600, style: 'oblique' },
        legend_text: { weight: 'light' },
      },
    })
    expect(g.faces.title.bold).toBe(true)
    expect(g.faces.axis).toMatchObject({ bold: true, italic: true })
    expect(g.faces.legend.bold).toBe(false)
  })
})
