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
