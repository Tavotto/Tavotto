/**
 * 字形归属计划：一段画布文字里的每个字符**由哪张脸画出来**。
 *
 * **与 `src/tavotto/glyphplan.py` 严格同源**（同名常量、同一套分层顺序、
 * 同一份看护向量 `tests/golden/glyph_plan_vectors.json`——pytest 与 vitest
 * 各跑一遍）。改一边必须同步另一边。
 *
 * ### 为什么前端也要有一份
 *
 * 导出那一端问的是真字体，永远是对的。但「这几个字导出后会是方框」必须在
 * **点导出之前**就说出来，而浏览器里没有 PyMuPDF——所以判据要有两份。
 *
 * 两份的算法同源，**oracle 不同源**：Python 侧问真字体，这里读生成物
 * `canvas_coverage.json`（`@glyphcoverage` 别名整份 import 进 bundle，
 * 与 `@profiles` 同一个套路：能力常量绝不在 TS 侧再抄一遍）。这条差异由
 * `scripts/gen_canvas_coverage.py --check` 看护——PyMuPDF 换版本导致覆盖
 * 漂移时红的是那一格，而不是某个用户图上多出来的一个方框。
 *
 * ### 四层，顺序不可交换
 *
 * ```text
 * primary   请求的那个族的 base-14 脸自己画得出
 * cjk       中日韩脸画得出（只有码位在 CJK 段、或前两层都没有时才轮到它）
 * fallback  前两层都没有，但 PyMuPDF 自己挑得出一张脸（实测是 Noto Serif）
 * missing   谁都画不出——导出上就是一个方框，必须进问题系统
 * ```
 */

import coverageTable from '@glyphcoverage'
import {
  DEFAULT_INTERPRETATION,
  interpretRuns,
  parseRuns,
  type TextInterpretation,
} from './richText'

/** 分层名。**闭集**，顺序即优先级（与 `glyphplan.py` 的 `GLYPH_LAYERS` 同源）。 */
export const GLYPH_LAYERS = ['primary', 'cjk', 'fallback', 'missing'] as const
export type GlyphLayer = (typeof GLYPH_LAYERS)[number]

/**
 * 「按中日韩脸走」的码位下界。这是**排版分段**的历史判据，不是覆盖判据
 * （覆盖由区间表回答）。与 `glyphplan.CJK_START` 同源。
 */
export const CJK_START = 0x2e80

export interface GlyphRun {
  text: string
  layer: GlyphLayer
}

type Ranges = readonly (readonly [number, number])[]

interface CoverageTable {
  schema: number
  backend: string
  backend_version: string
  max_codepoint: number
  layers: { primary: Ranges; cjk: Ranges; fallback: Ranges }
}

const TABLE = coverageTable as unknown as CoverageTable

/** 生成物的身份，供诊断与「表是哪一版后端出的」这类问题回答。 */
export const COVERAGE_BACKEND = `${TABLE.backend} ${TABLE.backend_version}`

const inRanges = (ranges: Ranges) => (cp: number): boolean => {
  let lo = 0
  let hi = ranges.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const [a, b] = ranges[mid]
    if (cp < a) hi = mid - 1
    else if (cp > b) lo = mid + 1
    else return true
  }
  return false
}

const hasPrimary = inRanges(TABLE.layers.primary)
const hasCjk = inRanges(TABLE.layers.cjk)
const hasFallback = inRanges(TABLE.layers.fallback)

/** 一个码位归哪一层。**四步的顺序不可交换**（见文件头）。 */
export function layerOf(cp: number): GlyphLayer {
  if (hasPrimary(cp)) return 'primary'
  if (cp > CJK_START && hasCjk(cp)) return 'cjk'
  if (hasFallback(cp)) return 'fallback'
  if (hasCjk(cp)) return 'cjk'
  return 'missing'
}

/**
 * 字符串 → 分层片段。相邻同层合并；空串回空表。
 *
 * **按码位遍历，不按 UTF-16 码元**：`[...text]` 让代理对（emoji、数学字母
 * 数字符号）算一个字符，否则每个 emoji 都会被拆成两个「画不出来的字」。
 */
export function planRuns(text: string): GlyphRun[] {
  const runs: GlyphRun[] = []
  for (const ch of text) {
    const layer = layerOf(ch.codePointAt(0) as number)
    const last = runs[runs.length - 1]
    if (last && last.layer === layer) last.text += ch
    else runs.push({ text: ch, layer })
  }
  return runs
}

/** 这段文字里**画不出来**的字符（去重、保出现顺序）。 */
export function missingChars(text: string): string[] {
  const out: string[] = []
  for (const ch of text) {
    if (layerOf(ch.codePointAt(0) as number) === 'missing' && !out.includes(ch)) out.push(ch)
  }
  return out
}

/**
 * **不是**用请求的那个族画出来的字符（去重、保出现顺序）。
 *
 * 与 `missingChars` 分开：那一档是「画不出来」，这一档是「画出来了，但不是
 * 你选的那张脸」——压成一句就等于把「方框」和「字体不一致」说成同一件事。
 */
export function substitutedChars(text: string): string[] {
  const out: string[] = []
  for (const ch of text) {
    const layer = layerOf(ch.codePointAt(0) as number)
    if (layer !== 'primary' && layer !== 'missing' && !out.includes(ch)) out.push(ch)
  }
  return out
}

/**
 * 一段画布文字**最终画出来**的字里，哪些是方框、哪些换了脸。
 *
 * 量的是渲染表示，不是原文：行内标记（`^{…}`）在这一步已经被拆掉，
 * Unicode 上下标该合成的已经合成——拿原文去问会报出一批不会发生的方框，
 * 而假红比假绿更难查（用户会去修一个不存在的问题）。
 *
 * 与 `src/tavotto/glyphplan.py` 的 `text_diagnostics` 严格同源。
 */
export function textDiagnostics(
  text: string,
  interpretation?: TextInterpretation,
): { missing: string[]; substituted: string[] } {
  const runs = interpretRuns(parseRuns(text), {
    isPrimary: (cp) => layerOf(cp) === 'primary',
    isDrawable: (cp) => layerOf(cp) !== 'missing',
    mode: interpretation ?? DEFAULT_INTERPRETATION,
  })
  const body = runs.map((r) => r.text).join('')
  return { missing: missingChars(body), substituted: substitutedChars(body) }
}
