/**
 * 新手教程演示图的纯函数（ADR 0136）：字号校验、图例夹取、mathtext 切段，以及生成物自洽。
 * 生成物与示例脚本同源由 `tests/test_tutorial_demo_figure.py` 看护（那边跑真 matplotlib）。
 */
import { describe, expect, it } from 'vitest'
import {
  clampLegendOffset,
  DEMO_FIGURE,
  legendBox,
  legendDistance,
  LEGEND_MIN_MOVE_PT,
  mathRuns,
  normalizeTitleSize,
  plainText,
  stepTitleSize,
  TITLE_SIZE,
} from './demoFigure'

describe('标题字号', () => {
  it('初始字号是脚本里的 9 pt（axes.titlesize），不是原型写死的 12', () => {
    expect(TITLE_SIZE.initial).toBe(9)
    expect(TITLE_SIZE.min).toBeLessThan(TITLE_SIZE.initial)
    expect(TITLE_SIZE.max).toBeGreaterThan(TITLE_SIZE.initial)
  })

  it('NaN / Infinity 不是字号；越界夹到边界；吸附到 0.5 pt', () => {
    expect(normalizeTitleSize(Number.NaN)).toBeNull()
    expect(normalizeTitleSize(Number.POSITIVE_INFINITY)).toBeNull()
    expect(normalizeTitleSize(Number.NEGATIVE_INFINITY)).toBeNull()
    expect(normalizeTitleSize(-3)).toBe(TITLE_SIZE.min)
    expect(normalizeTitleSize(0)).toBe(TITLE_SIZE.min)
    expect(normalizeTitleSize(99)).toBe(TITLE_SIZE.max)
    expect(normalizeTitleSize(10.3)).toBe(10.5)
    expect(normalizeTitleSize(10.2)).toBe(10)
  })

  it('加减一步在区间里走、到边就停', () => {
    expect(stepTitleSize(9, 1)).toBe(10)
    expect(stepTitleSize(9, -1)).toBe(8)
    expect(stepTitleSize(TITLE_SIZE.max, 1)).toBe(TITLE_SIZE.max)
    expect(stepTitleSize(TITLE_SIZE.min, -1)).toBe(TITLE_SIZE.min)
  })

  it('上限的大写字母仍在图幅里：标题基线离上沿的距离装得下 12 pt 的字（约 0.7 em 的大写高）', () => {
    expect(DEMO_FIGURE.title.anchor[1]).toBeGreaterThan(TITLE_SIZE.max * 0.7)
  })
})

describe('图例', () => {
  const [ax, ay, aw, ah] = DEMO_FIGURE.axes.box
  const inside = (offset: [number, number]) => {
    const [x, y, w, h] = legendBox(offset)
    return x >= ax - 0.01 && y >= ay - 0.01 && x + w <= ax + aw + 0.01 && y + h <= ay + ah + 0.01
  }

  it('脚本里的原位置在坐标轴框里（loc=(0.22, 0.42)）', () => {
    expect(inside([0, 0])).toBe(true)
  })

  it('夹取：怎么拖都留在坐标轴框里；框里的位置原样', () => {
    for (const off of [
      [-500, -500],
      [500, 500],
      [-500, 500],
      [500, -500],
      [0, -1e6],
    ] as [number, number][]) {
      expect(inside(clampLegendOffset(off)), String(off)).toBe(true)
    }
    expect(clampLegendOffset([0, -20])).toEqual([0, -20])
  })

  it('最小有效位移：往上挪得开（坐标轴框里有空间），手抖不算', () => {
    const up = clampLegendOffset([0, -1000])
    expect(legendDistance(up)).toBeGreaterThan(LEGEND_MIN_MOVE_PT)
    expect(legendDistance([1, 1])).toBeLessThan(LEGEND_MIN_MOVE_PT)
    expect(legendDistance(null)).toBe(0)
  })
})

describe('mathtext', () => {
  it('只认 $^{…}$ 上标：减号换成真正的减号', () => {
    expect(mathRuns('Catalyst (k = 0.125 min$^{-1}$)')).toEqual([
      { text: 'Catalyst (k = 0.125 min', sup: false },
      { text: '−1', sup: true },
      { text: ')', sup: false },
    ])
    expect(mathRuns('plain')).toEqual([{ text: 'plain', sup: false }])
  })

  it('读屏文本写成 Unicode 上标', () => {
    expect(plainText('Blank (k = 0.042 min$^{-1}$)')).toBe('Blank (k = 0.042 min⁻¹)')
  })
})

describe('生成物自洽', () => {
  it('两条曲线各 200 个点（np.linspace(0, 60, 200)），图例两行与曲线一一对应', () => {
    expect(DEMO_FIGURE.lines).toHaveLength(2)
    for (const line of DEMO_FIGURE.lines) expect(line.points).toHaveLength(200)
    expect(DEMO_FIGURE.legend.entries.map((e) => e.text)).toEqual(DEMO_FIGURE.lines.map((l) => l.label))
    expect(DEMO_FIGURE.script).toBe('fig1_kinetics.py')
  })

  it('欢迎页的代码片段非空，而且每行都短到能放进卡片', () => {
    const lines = DEMO_FIGURE.snippet.flat()
    expect(lines.length).toBeGreaterThan(3)
    for (const l of lines) expect(l.length).toBeLessThanOrEqual(48)
  })
})
