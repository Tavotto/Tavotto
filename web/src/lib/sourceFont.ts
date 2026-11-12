/**
 * 画布文字「用原图那个字体」（ADR 0164，修订 ADR 0060）的前端模型：身份的形状、从图内文字 manifest
 * 取值、本机状态的词汇。
 *
 * 文档里只有身份（名字 + 文件 sha256 + 面序号 + 粗斜）：**没有字体文件，没有路径**。字体字节只经本机服务
 * 供预览（`sourceFontCache.ts`），导出由 RenderCore 在本机读文件。
 *
 * 严格同源（`docs/rules/repo/same-origin-pairs.md`）：
 * - `SOURCE_FONT_REASONS` ↔ `src/tavotto/rendercore/sourcefont.py::REASONS`
 * - 字段形状 ↔ `sourcefont.parse_source_font`（`tests/test_source_font_wire.py` 与本文件的测试各跑一遍同一批向量，
 *   `tests/golden/source_font_vectors.json`）
 */
import type { ManifestElement, ManifestFaceSource } from './api'
import type { SiblingFace, SourceFace, SourceFont, TextObject } from '@/types/document'

/** 一张用户脸此刻不能用的原因（闭集）；`ok` 之外的 `state` 都是它们之一，另加前端自己的 `unsupported`/`unknown`。 */
export const SOURCE_FONT_REASONS = [
  'not_found',
  'embedding_restricted',
  'too_large',
  'unsupported_format',
  'unreadable',
  'invalid',
] as const
export type SourceFontReason = (typeof SOURCE_FONT_REASONS)[number]

/**
 * 本机状态：`ok` 可用；`SourceFontReason` 后端说不能用；`unsupported` 这个宿主没有本机字体服务
 * （浏览器 playground / 旧后端）；`unknown` 还没问到。除 `ok` / `unknown` 外都要在界面上带一个提示。
 */
export type SourceFontState = 'ok' | SourceFontReason | 'unsupported' | 'unknown'

const HEX64 = /^[0-9a-f]{64}$/

/** 一张脸最多带几张同族兄弟（与 `sourcefont.MAX_SIBLINGS` 同值：RIBBI 一族四张，自己占一张） */
const MAX_SIBLINGS = 3

const isIdentity = (o: Record<string, unknown>): boolean =>
  typeof o.sha256 === 'string' &&
  HEX64.test(o.sha256) &&
  typeof o.face === 'number' &&
  Number.isInteger(o.face) &&
  o.face >= 0 &&
  o.face < 1024

const isSibling = (v: unknown): v is SiblingFace => {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  return (
    isIdentity(o) &&
    (o.bold === undefined || typeof o.bold === 'boolean') &&
    (o.italic === undefined || typeof o.italic === 'boolean')
  )
}

const isFace = (v: unknown): v is SourceFace => {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  return (
    isIdentity(o) &&
    typeof o.family === 'string' &&
    o.family.trim() !== '' &&
    o.family.length <= 200 &&
    (o.siblings === undefined ||
      (Array.isArray(o.siblings) && o.siblings.length <= MAX_SIBLINGS && o.siblings.every(isSibling)))
  )
}

/** 文档里读出来的 `sourceFont` 合不合形状（与后端 `parse_source_font` 同一批判据）。 */
export const isSourceFont = (v: unknown): v is SourceFont =>
  isFace(v) && ((v as SourceFont).cjk === undefined || isFace((v as SourceFont).cjk))

const faceOf = (s: ManifestFaceSource): SourceFace => ({
  family: s.family,
  sha256: s.sha256,
  face: s.face,
})

/**
 * 图内文字元素的 manifest 数据 → `TextObject.sourceFont`。**「复制为画布文字标注」取值的唯一入口。**
 *
 * 元素没有 `face_src`（解析不出脸 / 文件读不了 / 老引擎）→ undefined：标注就只带 `fontFamily`，
 * 不编一个身份。`cjk_src` 缺席则不写 `cjk`（汉字由批准的 CJK 脸画）。
 */
export function sourceFontFromElement(
  el: Pick<ManifestElement, 'face_src' | 'cjk_src'>,
): SourceFont | undefined {
  const src = el.face_src
  if (!src || !isFace(src)) return undefined
  const out: SourceFont = faceOf(src)
  if (src.bold) out.bold = true
  if (src.italic) out.italic = true
  if (el.cjk_src && isFace(el.cjk_src)) out.cjk = faceOf(el.cjk_src)
  return out
}

/** 同一张脸（只看身份，不看名字）。 */
export const sameFace = (a: SourceFace, b: SourceFace): boolean =>
  a.sha256 === b.sha256 && a.face === b.face

export const faceKey = (f: Pick<SourceFace, 'sha256' | 'face'>): string => `${f.sha256}:${f.face}`

/** 预览里加载这张脸用的 CSS 族名：由身份派生，不会与任何真实字体名冲突。 */
export const cssFamilyOf = (f: Pick<SourceFace, 'sha256' | 'face'>): string =>
  `tvsrc-${f.sha256.slice(0, 16)}-${f.face}`

/** 一批文字对象里出现的、需要问本机状态的脸（去重；形状不对的不问——它们的状态是 `invalid`）。 */
export function sourceFacesOf(objs: readonly Pick<TextObject, 'sourceFont'>[]): SourceFace[] {
  const seen = new Map<string, SourceFace>()
  for (const o of objs) {
    const sf = o.sourceFont
    if (!isSourceFont(sf)) continue
    for (const f of [sf, sf.cjk]) if (f) seen.set(faceKey(f), f)
  }
  return [...seen.values()]
}
