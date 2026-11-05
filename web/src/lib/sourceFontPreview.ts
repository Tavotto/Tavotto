import { previewFamilyOf, sourceFontState } from './sourceFontCache'
import { siblingFace } from './sourceFont'
import type { SiblingFace, SourceFace, SourceFont } from '@/types/document'

export interface FontStyle {
  bold: boolean
  italic: boolean
}

const NO_STYLE: FontStyle = { bold: false, italic: false }

/** 批准的中文粗体（`rendercore/fonts_allowlist.json`，看护 `tests/test_font_catalog.py`）。 */
const APPROVED_CJK_BOLD: Record<'serif' | 'sans', SourceFace> = {
  serif: {
    family: 'Noto Serif SC',
    sha256: 'c44a5be9342f435b01ac7c20e8b144692334a3454be49d8fb3d26a641f9908ee',
    face: 0,
  },
  sans: {
    family: 'Noto Sans SC',
    sha256: 'c6cb5a93abaa9edc8ee7463b7ebb7f42d618d40e6ed2f7a5371c97b0b64767c0',
    face: 0,
  },
}

/** 批准的中文粗体只画这些字：拉丁字母、数字留给英文那一层 */
const CJK_RANGE = 'U+2E80-9FFF, U+F900-FAFF, U+FE30-FE4F, U+FF00-FFEF, U+20000-2FFFF'
const CJK_TEXT = /[\u2E80-\u9FFF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFFEF]|[\u{20000}-\u{2FFFF}]/u

/**
 * 这段文字加粗时要预览哪张批准的中文粗体；没有中文 = undefined，不取字节（一张十几 MB）。
 *
 * 系统字体栈靠不住：macOS 的 Songti SC 在浏览器里没有粗体，画布是细的、导出是粗的（ADR 0169 修订）。
 */
export const approvedCjkBoldFor = (family: string, text: string): SourceFace | undefined =>
  CJK_TEXT.test(text) ? APPROVED_CJK_BOLD[family === 'serif' ? 'serif' : 'sans'] : undefined

/** 这张脸此刻能不能进预览栈：可用且字节已到才给族名。副作用：把还没问过的脸排进状态请求。 */
function usable(
  f: SourceFace,
  style: Pick<SourceFont, 'bold' | 'italic'> & { unicodeRange?: string },
): string | undefined {
  return sourceFontState(f, style) === 'ok' ? previewFamilyOf(f) : undefined
}

const asFace = (owner: SourceFace, s: SiblingFace): SourceFace => ({
  family: owner.family,
  sha256: s.sha256,
  face: s.face,
})

/** 正文层：对象自己的样式就是那张脸，别的粗斜找同族，没有就不进栈（按批准脸画，与导出一致）。 */
function primaryName(source: SourceFont, want: FontStyle, own: FontStyle): string | undefined {
  if (want.bold === own.bold && want.italic === own.italic) return usable(source, source)
  const s = siblingFace(source, want.bold, want.italic)
  return s ? usable(asFace(source, s), s) : undefined
}

/** 中文层：先要粗斜，再退粗，都没有是常规——与 `plan._user_faces` 的 `cjk` 同一个顺序。 */
function cjkName(cjk: SourceFace, want: FontStyle): string | undefined {
  for (const s of [siblingFace(cjk, want.bold, want.italic), siblingFace(cjk, want.bold, false)]) {
    const name = s && usable(asFace(cjk, s), s)
    if (name) return name
  }
  return usable(cjk, {})
}

/**
 * 预览用的 CSS 字体栈：`[正文脸, CJK 脸, 批准族栈]`——与导出（`plan._user_faces`）同一个顺序与同一个判据。
 * 两张脸**各自独立**判：正文脸不可用不影响 CJK 脸（导出里 `user_primary` / `user_cjk` 也是各自回退）；
 * 某张脸状态不是 `ok` / 字节没到 = 不进栈，那一层按批准族画（正文 = `fontFamily`，CJK = 批准的 Noto），与导出回退一致。
 *
 * `own` 是对象自己的粗斜，`run` 是行内片段再加的粗斜（ADR 0169 修订）：只问要画的那张脸，
 * 用不上的兄弟不取字节（一张中文字体十几 MB）。没选本机中文字体时，要粗体就用 `boldCjk`（`approvedCjkBoldFor`）。
 */
export function previewStackOf(
  source: SourceFont | undefined,
  approvedStack: string,
  own: FontStyle = NO_STYLE,
  run: FontStyle = NO_STYLE,
  boldCjk?: SourceFace,
): string {
  const want = { bold: own.bold || run.bold, italic: own.italic || run.italic }
  const names: string[] = []
  const p = source && primaryName(source, want, own)
  if (p) names.push(p)
  const c = source?.cjk
    ? cjkName(source.cjk, want)
    : want.bold && boldCjk
      ? usable(boldCjk, { bold: true, unicodeRange: CJK_RANGE })
      : undefined
  if (c && !names.includes(c)) names.push(c)
  return names.length ? `${names.map((n) => `"${n}"`).join(', ')}, ${approvedStack}` : approvedStack
}
