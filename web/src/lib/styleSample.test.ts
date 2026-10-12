import { describe, expect, it } from 'vitest'
import {
  cssFamilyOf,
  fitSampleGeometry,
  sampleFitScale,
  sampleLayout,
  sampleSeriesPoints,
  SAMPLE_DEFAULTS,
  SAMPLE_SERIES,
  styleSampleGeometry,
} from './styleSample'

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
    expect(g.faces.title).toEqual({ fontFamily: '"Arial", sans-serif', weight: 700, style: null, bold: true, italic: false })
    expect(g.faces.legend).toEqual({ fontFamily: 'sans-serif', weight: null, style: 'italic', bold: false, italic: true })
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

describe('示例图认字体候选列表', () => {
  it('fontfamily 是一串候选时按第一个画（matplotlib 也先试它）', () => {
    const g = styleSampleGeometry({ element: { title: { fontfamily: ['Times New Roman', 'serif'] } } })
    expect(g.faces.title.fontFamily).toBe('"Times New Roman", serif')
  })
})

describe('示例图用原值画字重 / 字形（与引擎拿到的同一个值，Codex #703）', () => {
  it('light 画 200、600 与 semibold 画 600、bold 画 700、normal / 400 不写；oblique 画 oblique', () => {
    const g = styleSampleGeometry({
      element: {
        title: { weight: 'light' },
        axis_label: { weight: 600, style: 'oblique' },
        legend_text: { weight: 'semibold', style: 'normal' },
      },
    })
    expect(g.faces.title).toMatchObject({ weight: 200, style: null, bold: false })
    expect(g.faces.axis).toMatchObject({ weight: 600, style: 'oblique', bold: true, italic: true })
    expect(g.faces.legend).toMatchObject({ weight: 600, style: null, bold: true, italic: false })
    expect(styleSampleGeometry({ element: { title: { weight: 400 } } }).faces.title.weight).toBeNull()
    expect(styleSampleGeometry({ element: { title: { weight: 'normal' } } }).faces.title.weight).toBeNull()
    expect(styleSampleGeometry({ element: { title: { weight: 'wide' } } }).faces.title.weight).toBeNull()
  })
})

describe('sampleLayout：图例不压曲线（2026-10 设计审计第二轮）', () => {
  // 主语：示例 SVG 里图例那两笔（色样线 + 文字，`extents` 末两项）的外框，与两条数据折线在**同一横向范围内**的竖向关系。
  // 曲线都从左下升到右上；图例在右下角时，图例横跨的每一处两条曲线都必须在它上方、还隔着半个线宽
  const cases: [string, Record<string, unknown> | null][] = [
    ['默认', null],
    ['图例大字号', { element: { legend: { fontsize: 14 } } }],
    ['刻度朝里且长', { element: { ticks: { direction: 'in', length: 8 } } }],
    ['粗线', { element: { line: { linewidth: 3 } } }],
    // Codex #946：两个上限同时取到——14 pt 图例 + 朝里 20 pt 刻度
    ['图例大字号且刻度朝里最长', { element: { legend: { fontsize: 14 }, ticks: { direction: 'in', length: 20 } } }],
    ['图例大字号且刻度朝里最长、粗线', { element: { legend: { fontsize: 14 }, ticks: { direction: 'in', length: 20, width: 3 }, line: { linewidth: 3 } } }],
    // Codex #946 第二条：粗体 14 pt 图例 + 10 pt 刻度宽 + 7…10 pt 数据线——右下角两处都放不下
    ...[7, 10].map((lw): [string, Record<string, unknown>] => [
      `粗体大图例、最宽刻度、${lw} pt 数据线`,
      { element: { legend: { fontsize: 14 }, legend_text: { weight: 'bold' }, ticks: { width: 10 }, line: { linewidth: lw } } },
    ]),
  ]
  for (const [name, data] of cases) {
    it(name, () => {
      const g = fitSampleGeometry(styleSampleGeometry(data as never))
      const lay = sampleLayout(g)
      const [line, text] = lay.extents.slice(-2)
      const x0 = Math.min(line.x0, text.x0)
      const x1 = Math.max(line.x1, text.x1)
      const top = Math.min(line.y0, text.y0)
      for (const fn of SAMPLE_SERIES) {
        const pts = sampleSeriesPoints(lay.box, lay.bottom, fn)
        for (let i = 1; i < pts.length; i += 1) {
          const [ax, ay] = pts[i - 1]
          const [bx, by] = pts[i]
          if (bx < x0 || ax > x1) continue
          // 折线段在图例横向范围内的最低点（SVG 里 y 越大越低）
          expect(Math.max(ay, by) + g.lineWidthPt / 2, `${name}: 曲线在 x≈${bx.toFixed(0)} 压到图例`).toBeLessThan(top)
        }
      }
    })
  }
})
