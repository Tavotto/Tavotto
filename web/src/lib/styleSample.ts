/**
 * 样式示例图的几何（审计 T42）。
 *
 * 设置里的「样式」页以前只有一列数字字段：字号 9、线宽 0.5、边框 0.75——
 * 用户看不出选了这套样式之后图会长什么样。这里把一份样式的**内容**翻成一张
 * 固定示例图的几何：字号、线宽、边框线宽、字体族各落到示例里的哪一笔。
 *
 * 纯函数、不跑引擎、不读文档：输入是 `StyleProfileData` 形状的对象（磁盘里的
 * `data`，或编辑中的草稿），输出是一组数字，`StyleSamplePreview` 只负责画。
 * 缺席的字段是示例的默认值——**默认值只影响示例**，不会写回样式。
 */

import { FIGURE_LINE_ROWS, FIGURE_TEXT_ROWS, type FigureLineRowId, type FigureTextRowId } from './stylePanelModel'

/** 示例图默认值（一张 9 pt / 0.5 pt 的典型论文图） */
export const SAMPLE_DEFAULTS = {
  titlePt: 9,
  axisPt: 9,
  tickPt: 9,
  legendPt: 9,
  lineWidthPt: 0.5,
  spinePt: 0.75,
  fontFamily: 'sans-serif',
  tickLengthPt: 3.5,
  tickWidthPt: 0.75,
} as const

/** 示例里一类文字的字面：字体族（CSS）+ 粗体 / 斜体 */
export interface SampleFace {
  fontFamily: string
  bold: boolean
  italic: boolean
}

export interface StyleSampleGeometry {
  titlePt: number
  axisPt: number
  tickPt: number
  legendPt: number
  lineWidthPt: number
  spinePt: number
  /**
   * 每一类文字各自的字面（CSS 字体族 + 粗 / 斜体；设置 › 样式页 2026-09-28 起每行能改）：
   * 没设的是示例默认字体与常规字面
   */
  faces: { title: SampleFace; axis: SampleFace; tick: SampleFace; legend: SampleFace }
  /** 刻度：方向（`in` / `out` / `inout`）、长度与线宽（pt） */
  tickDirection: 'in' | 'out' | 'inout'
  tickLengthPt: number
  tickWidthPt: number
  /** 示例里用到的系列颜色（样式没给配色时是示例自己的两色） */
  colors: [string, string]
}

const readValue = (obj: unknown, path: string[]): unknown => {
  let cur: unknown = obj
  for (const key of path) {
    if (!cur || typeof cur !== 'object') return null
    cur = (cur as Record<string, unknown>)[key]
  }
  return cur
}

const readNumber = (obj: unknown, path: string[]): number | null => {
  const v = readValue(obj, path)
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null
}

const readString = (obj: unknown, path: string[]): string | null => {
  const v = readValue(obj, path)
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** matplotlib 的通用族名 → CSS 通用族；具体字体名（Times New Roman）原样交给浏览器 */
export function cssFamilyOf(family: string | null): string {
  if (!family) return SAMPLE_DEFAULTS.fontFamily
  const low = family.toLowerCase()
  if (low === 'serif' || low === 'sans-serif' || low === 'monospace') return low
  if (/times|georgia|garamond|palatino|book|roman|song|宋/.test(low)) return `"${family}", serif`
  if (/mono|courier|consolas|menlo/.test(low)) return `"${family}", monospace`
  return `"${family}", sans-serif`
}

const textRow = (id: FigureTextRowId) => FIGURE_TEXT_ROWS.find((r) => r.id === id)!
const lineRow = (id: FigureLineRowId) => FIGURE_LINE_ROWS.find((r) => r.id === id)!

/**
 * 把一份样式翻成示例图的几何。
 *
 * 示例里每一笔只读**它自己那一维**（角色 × 属性与样式面板同一张行表 `FIGURE_TEXT_ROWS` /
 * `FIGURE_LINE_ROWS`），没设就是示例默认值，**不从别的角色回落**：应用样式（`planStyle`）时
 * 每个角色的值只落在那个角色的元素上——`element.text` 只改「其余文字」、边框线宽不改刻度线宽，
 * 示例若让标题跟着 `text` 变、刻度跟着边框变，就是在预告一件应用时不会发生的事（Codex #703）。
 * 「其余文字」（`text`）在示例图里没有对应的一笔。
 */
export function styleSampleGeometry(data: Record<string, unknown> | null | undefined): StyleSampleGeometry {
  const d = data ?? {}
  const palette = (d as { palette?: unknown }).palette
  const colors: [string, string] =
    Array.isArray(palette) && palette.length >= 2 && palette.every((c) => typeof c === 'string')
      ? [palette[0] as string, palette[1] as string]
      : ['#1B3A6B', '#C0504D']
  const size = (id: FigureTextRowId, fallback: number) =>
    readNumber(d, ['element', textRow(id).sizeRole, 'fontsize']) ?? fallback
  const face = (id: FigureTextRowId): SampleFace => {
    const { familyRole, faceRole } = textRow(id)
    const faceOf = (prop: string) => (faceRole ? readString(d, ['element', faceRole, prop]) : null)
    return {
      fontFamily: cssFamilyOf(readString(d, ['element', familyRole, 'fontfamily'])),
      bold: faceOf('weight') === 'bold',
      italic: faceOf('style') === 'italic',
    }
  }
  const linePath = (id: FigureLineRowId) => ['element', lineRow(id).role, lineRow(id).prop]
  const width = (id: FigureLineRowId, fallback: number) => readNumber(d, linePath(id)) ?? fallback
  const dir = readString(d, linePath('tickDirection'))
  // 长度 0 是「不画刻度线」，是个真值；`readNumber` 只认正数，这里单独读
  const rawLength = readValue(d, linePath('tickLength'))
  const tickLength = typeof rawLength === 'number' && Number.isFinite(rawLength) && rawLength >= 0 ? rawLength : null
  return {
    titlePt: size('title', SAMPLE_DEFAULTS.titlePt),
    axisPt: size('axis_label', SAMPLE_DEFAULTS.axisPt),
    tickPt: size('ticks', SAMPLE_DEFAULTS.tickPt),
    legendPt: size('legend', SAMPLE_DEFAULTS.legendPt),
    lineWidthPt: width('dataLine', SAMPLE_DEFAULTS.lineWidthPt),
    spinePt: width('frame', SAMPLE_DEFAULTS.spinePt),
    faces: { title: face('title'), axis: face('axis_label'), tick: face('ticks'), legend: face('legend') },
    tickDirection: dir === 'in' || dir === 'inout' ? dir : 'out',
    tickLengthPt: tickLength ?? SAMPLE_DEFAULTS.tickLengthPt,
    tickWidthPt: width('tickWidth', SAMPLE_DEFAULTS.tickWidthPt),
    colors,
  }
}

/**
 * 示例图能画下的最大字号（示例自己的 viewBox 单位 = pt）。
 *
 * 样式里的字号上限是 72 pt——照原样画的话标题会把整张示例图顶出画框，用户看到
 * 的是一块空白，而那**不是**「这套样式的样子」。所以整张示例图按同一个系数缩，
 * **一律等比**：示例存在的意义就是「9pt 与 7pt 差多少」，非等比缩会把这个比例
 * 弄坏，而弄坏的方式还看不出来。
 */
const SAMPLE_FONT_BUDGET_PT = 14

/**
 * 让这套字号画得进示例图的等比系数（1 = 原样画）。字号最大的那一笔决定它。
 * 线宽不参与——线宽再粗也只是粗，不会把版面撑开。
 */
export function sampleFitScale(g: StyleSampleGeometry): number {
  const biggest = Math.max(g.titlePt, g.axisPt, g.tickPt, g.legendPt)
  if (!(biggest > SAMPLE_FONT_BUDGET_PT)) return 1
  return SAMPLE_FONT_BUDGET_PT / biggest
}

/* ------------------------------- 版面 ------------------------------------- */

/** 示例图里的文字（估外框用的字符数跟着它走，别在画的地方另写一份） */
export const SAMPLE_TEXT = {
  title: 'Reaction kinetics',
  xLabel: 'Time (min)',
  yLabel: 'Conversion',
  legend: 'Catalyst',
  xTicks: ['0', '30', '60'],
  yTicks: ['0.0', '0.5', '1.0'],
} as const

/** 画框的最小尺寸与宽高比（示例图外框固定这个比例：换样式、改刻度时设置页不跳） */
export const SAMPLE_VIEW = { w: 200, h: 128 } as const

/** 估文字外框：字宽按字号的倍数（粗 / 斜体更宽），基线上下各占多少 */
const GLYPH_W = 0.62
const GLYPH_W_BOLD = 0.68
const ASCENT = 0.8
const DESCENT = 0.25
const PAD = 3

export interface Extent {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface SampleLayout {
  /** `viewBox` 的四个数：按全部笔画的外框现算，比例固定为 `SAMPLE_VIEW` */
  viewBox: [number, number, number, number]
  box: { x: number; y: number; w: number; h: number }
  bottom: number
  tickIn: number
  tickOut: number
  titleY: number
  xTickLabelY: number
  xLabelY: number
  /** 纵轴刻度文字的右端（`text-anchor: end`） */
  yTickLabelX: number
  /** 纵轴标题（转 -90°）的基线 x */
  yLabelX: number
  legend: { lineX1: number; lineX2: number; lineY: number; textX: number; textY: number }
  /** 每一笔的估算外框（含线宽的一半、文字的上下伸）：`viewBox` 由它们的并集定 */
  extents: Extent[]
}

const textWidth = (s: string, pt: number, face: SampleFace) => s.length * pt * (face.bold ? GLYPH_W_BOLD : GLYPH_W)

/**
 * 示例图的版面：刻度朝外伸多长、线多粗、字多大，刻度文字与轴标题就往外让多少，
 * `viewBox` 再按所有笔画的外框框住——**不按固定画框裁**（Codex #703：刻度长 20 pt 朝外时，
 * 固定 `0 0 200 128` 会把横轴标题裁掉）。外框比例固定、至少 `SAMPLE_VIEW` 大：内容撑大时
 * 整张图等比缩小，设置页里示例图的外框不跳。`g` 是已按 `sampleFitScale` 缩过字号的几何。
 */
export function sampleLayout(g: StyleSampleGeometry): SampleLayout {
  const L = g.tickLengthPt
  const tickIn = g.tickDirection === 'in' ? L : g.tickDirection === 'inout' ? L / 2 : 0
  const tickOut = g.tickDirection === 'out' ? L : g.tickDirection === 'inout' ? L / 2 : 0
  const box = { x: 0, y: 0, w: 150, h: 72 }
  const bottom = box.y + box.h
  const titleY = box.y - g.spinePt / 2 - 4 - DESCENT * g.titlePt
  const xTickLabelY = bottom + tickOut + 1.5 + g.tickPt
  const xLabelY = xTickLabelY + DESCENT * g.tickPt + 2.5 + g.axisPt
  const yTickLabelX = box.x - tickOut - 2.5
  const yTickW = Math.max(...SAMPLE_TEXT.yTicks.map((s) => textWidth(s, g.tickPt, g.faces.tick)))
  const yLabelX = yTickLabelX - yTickW - 2.5 - DESCENT * g.axisPt
  const legendLineY = box.y + 10 + g.legendPt * 0.35
  const legend = {
    lineX1: box.x + box.w - 62,
    lineX2: box.x + box.w - 50,
    lineY: legendLineY,
    textX: box.x + box.w - 46,
    textY: box.y + 10 + g.legendPt * 0.7,
  }

  const extents: Extent[] = []
  const hText = (s: string, x: number, y: number, pt: number, face: SampleFace, anchor: 'start' | 'middle' | 'end') => {
    const w = textWidth(s, pt, face)
    const x0 = anchor === 'start' ? x : anchor === 'middle' ? x - w / 2 : x - w
    extents.push({ x0, y0: y - ASCENT * pt, x1: x0 + w, y1: y + DESCENT * pt })
  }
  const s2 = g.spinePt / 2
  extents.push({ x0: box.x - s2, y0: box.y - s2, x1: box.x + box.w + s2, y1: bottom + s2 })
  const w2 = g.tickWidthPt / 2
  const lw2 = g.lineWidthPt / 2
  extents.push({ x0: box.x - lw2, y0: box.y - lw2, x1: box.x + box.w + lw2, y1: bottom + lw2 })
  hText(SAMPLE_TEXT.title, box.x + box.w / 2, titleY, g.titlePt, g.faces.title, 'middle')
  SAMPLE_TEXT.xTicks.forEach((s, i) => {
    const x = box.x + (i / 2) * box.w
    extents.push({ x0: x - w2, y0: bottom - tickIn, x1: x + w2, y1: bottom + tickOut })
    hText(s, x, xTickLabelY, g.tickPt, g.faces.tick, 'middle')
  })
  SAMPLE_TEXT.yTicks.forEach((s, i) => {
    const y = bottom - (i / 2) * box.h
    extents.push({ x0: box.x - tickOut, y0: y - w2, x1: box.x + tickIn, y1: y + w2 })
    hText(s, yTickLabelX, y + g.tickPt * 0.35, g.tickPt, g.faces.tick, 'end')
  })
  hText(SAMPLE_TEXT.xLabel, box.x + box.w / 2, xLabelY, g.axisPt, g.faces.axis, 'middle')
  // 转 -90° 的纵轴标题：字宽落在竖直方向，上伸朝左
  const yLabelLen = textWidth(SAMPLE_TEXT.yLabel, g.axisPt, g.faces.axis)
  const yMid = box.y + box.h / 2
  extents.push({
    x0: yLabelX - ASCENT * g.axisPt,
    y0: yMid - yLabelLen / 2,
    x1: yLabelX + DESCENT * g.axisPt,
    y1: yMid + yLabelLen / 2,
  })
  extents.push({ x0: legend.lineX1, y0: legendLineY - lw2, x1: legend.lineX2, y1: legendLineY + lw2 })
  hText(SAMPLE_TEXT.legend, legend.textX, legend.textY, g.legendPt, g.faces.legend, 'start')

  const x0 = Math.min(...extents.map((e) => e.x0)) - PAD
  const y0 = Math.min(...extents.map((e) => e.y0)) - PAD
  const x1 = Math.max(...extents.map((e) => e.x1)) + PAD
  const y1 = Math.max(...extents.map((e) => e.y1)) + PAD
  const aspect = SAMPLE_VIEW.w / SAMPLE_VIEW.h
  const w = Math.max(x1 - x0, (y1 - y0) * aspect, SAMPLE_VIEW.w)
  const h = w / aspect
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  return {
    viewBox: [cx - w / 2, cy - h / 2, w, h],
    box,
    bottom,
    tickIn,
    tickOut,
    titleY,
    xTickLabelY,
    xLabelY,
    yTickLabelX,
    yLabelX,
    legend,
    extents,
  }
}
