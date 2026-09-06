/**
 * 样式示例图的几何（审计 T42）。
 *
 * 设置里的「样式」页以前只有一列数字字段：字号 9、线宽 0.5、边框 0.75——
 * 用户看不出选了这套样式之后图会长什么样。这里把一份样式的**内容**翻成一张
 * 固定示例图的几何：字号、线宽、边框线宽、字体族各落到示例里的哪一笔。
 *
 * 纯函数、不跑引擎、不读文档：输入是 `StyleProfileData` 形状的对象（磁盘里的
 * `data`，或编辑中的草稿），输出是一组数字，`StyleSamplePreview` 只负责画。
 * 缺席的字段回落到示例的默认值——**回落只影响示例**，不会写回样式。
 */

/** 示例图默认值（一张 9 pt / 0.5 pt 的典型论文图） */
export const SAMPLE_DEFAULTS = {
  basePt: 9,
  titlePt: 9,
  axisPt: 9,
  tickPt: 9,
  legendPt: 9,
  lineWidthPt: 0.5,
  spinePt: 0.75,
  fontFamily: 'sans-serif',
} as const

export interface StyleSampleGeometry {
  titlePt: number
  axisPt: number
  tickPt: number
  legendPt: number
  lineWidthPt: number
  spinePt: number
  /** 示例文字用的 CSS 字体族（serif / sans-serif / monospace；未知的原样透出） */
  fontFamily: string
  /** 示例里用到的系列颜色（样式没给配色时是示例自己的两色） */
  colors: [string, string]
}

const readNumber = (obj: unknown, path: string[]): number | null => {
  let cur: unknown = obj
  for (const key of path) {
    if (!cur || typeof cur !== 'object') return null
    cur = (cur as Record<string, unknown>)[key]
  }
  return typeof cur === 'number' && Number.isFinite(cur) && cur > 0 ? cur : null
}

const readString = (obj: unknown, path: string[]): string | null => {
  let cur: unknown = obj
  for (const key of path) {
    if (!cur || typeof cur !== 'object') return null
    cur = (cur as Record<string, unknown>)[key]
  }
  return typeof cur === 'string' && cur.trim() ? cur.trim() : null
}

/** matplotlib 的通用族名 → CSS 通用族；具体字体名（Times New Roman）原样交给浏览器 */
export function cssFamilyOf(family: string | null): string {
  if (!family) return SAMPLE_DEFAULTS.fontFamily
  const low = family.toLowerCase()
  if (low === 'serif' || low === 'sans-serif' || low === 'monospace') return low
  if (/times|georgia|garamond|palatino|book|roman|song|宋/.test(low)) return `"${family}", serif`
  if (/mono|courier|consolas/.test(low)) return `"${family}", monospace`
  return `"${family}", sans-serif`
}

/**
 * 把一份样式翻成示例图的几何。角色字号缺席时回落到正文字号（样式里
 * `element.text.fontsize` 就是「其余文字」的基准），正文也没设就是示例默认。
 */
export function styleSampleGeometry(data: Record<string, unknown> | null | undefined): StyleSampleGeometry {
  const d = data ?? {}
  const base = readNumber(d, ['element', 'text', 'fontsize']) ?? SAMPLE_DEFAULTS.basePt
  const role = (r: string, fallback: number) => readNumber(d, ['element', r, 'fontsize']) ?? fallback
  const palette = (d as { palette?: unknown }).palette
  const colors: [string, string] =
    Array.isArray(palette) && palette.length >= 2 && palette.every((c) => typeof c === 'string')
      ? [palette[0] as string, palette[1] as string]
      : ['#1B3A6B', '#C0504D']
  return {
    titlePt: role('title', base),
    axisPt: role('axis_label', base),
    tickPt: role('ticks', base),
    legendPt: role('legend', base),
    lineWidthPt: readNumber(d, ['element', 'line', 'linewidth']) ?? SAMPLE_DEFAULTS.lineWidthPt,
    spinePt: readNumber(d, ['element', 'axes', 'spine_linewidth']) ?? SAMPLE_DEFAULTS.spinePt,
    fontFamily: cssFamilyOf(
      readString(d, ['element', 'text', 'fontfamily']) ?? readString(d, ['element', 'title', 'fontfamily']),
    ),
    colors,
  }
}
