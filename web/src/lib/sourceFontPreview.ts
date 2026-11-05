import { previewFamilyOf, sourceFontState } from './sourceFontCache'
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

/**
 * 挑要画的脸，与 `plan._user_faces` 的 `pick` 同一个顺序：先要粗细对、再要斜体对，不用更粗 / 更斜的兄弟顶常规。
 * 同族没有的粗斜交给浏览器合成，导出同样模拟（ADR 0169 修订）。
 */
function pickName(base: SourceFace, own: FontStyle, want: FontStyle): string | undefined {
  const siblings = (base.siblings ?? [])
    .filter((s) => (want.bold || !s.bold) && (want.italic || !s.italic))
    .map((s) => ({ face: asFace(base, s), bold: s.bold === true, italic: s.italic === true }))
  const rank = (c: FontStyle) => (c.bold === want.bold ? 0 : 2) + (c.italic === want.italic ? 0 : 1)
  const candidates = [{ face: base, ...own }, ...siblings].sort((a, b) => rank(a) - rank(b))
  for (const c of candidates) {
    // 逐张按顺序判：还没问到 / 字节没到 = 等它，不碰后面的候选（否则会把用不上的同族脸全下载）
    const state = sourceFontState(c.face, { bold: c.bold, italic: c.italic })
    if (state === 'unknown') return undefined
    if (state === 'ok') return previewFamilyOf(c.face) // 字节没到时 undefined，同样原地等
  }
  return undefined
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
  const p =
    source && pickName(source, { bold: source.bold === true, italic: source.italic === true }, want)
  if (p) names.push(p)
  const local = source?.cjk && pickName(source.cjk, NO_STYLE, want)
  // 本机中文字体确实用不了时，与导出 `cjk_for` 一样回退批准粗体；只是还在加载就等它，不白取十几 MB
  const pending = source?.cjk && ['ok', 'unknown'].includes(sourceFontState(source.cjk))
  const c =
    local ||
    (want.bold && boldCjk && !pending
      ? usable(boldCjk, { bold: true, unicodeRange: CJK_RANGE })
      : undefined)
  if (c && !names.includes(c)) names.push(c)
  return names.length ? `${names.map((n) => `"${n}"`).join(', ')}, ${approvedStack}` : approvedStack
}
