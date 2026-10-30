/**
 * 图内文字的公式字体跟正文（ADR 0152 PR 5，维护者 10-29 裁决「自动跟正文」）。
 *
 * matplotlib 默认的公式字体集是 DejaVu Sans：正文是 Times 的轴标题里，`$\mathit{T}$` 画成无衬线斜体
 * （2026-10-29 实测）。引擎的「字体族」修改本来就连 `$…$` 一起换（`overrides._set_text_fontfamily`：
 * 正文按回退链设、公式切到 custom 字体集指向同一族）——所以这里**不加新的引擎能力**：用户在所见即所得
 * 文字框里给这段字用上了样式或公式、而它的字体族还没被改过时，同一次编辑里顺带把字体族写成它**现在的**
 * 正文族。正文不变，公式跟上；写回、重放都走字体族那条已有的路。
 *
 * 只作用于用了样式 / 公式的那段文字（规范段里的斜体 / 粗体 / 上下标）；字体族已经改过的不再写。
 */
import { effectiveOverride } from './effectiveOverride'
import { parseMathtext } from './mathtextCodec'
import type { ManifestElement } from './api'
import type { PanelObject } from '@/types/document'

/** 要顺带写的字体族；不需要回 null */
export function mathFontFollow(panel: PanelObject, el: ManifestElement, nextText: string): string | null {
  const field = el.editable.find((f) => f.prop === 'fontfamily')
  if (!field || typeof field.value !== 'string' || !field.value) return null
  if (effectiveOverride(panel.overrides, el.gid, 'fontfamily')) return null
  const styled = parseMathtext(nextText).some((r) => !r.math && (r.italic || r.bold || r.script !== ''))
  return styled ? field.value : null
}

/** 后端 `mathtypeset.face_of` 收的族名字符集（`[\w .()+&'-]{1,80}`）的镜像：不合法的退默认脸，免得整条请求被拒 */
const FACE_NAME = /^[\p{L}\p{N}_ .()+&'-]{1,80}$/u

/** 族名取第一个（字体栈 `A, B` 的头一个）、去引号；不合法退默认脸 */
function faceName(raw: string): string {
  const first = raw.split(',')[0].trim().replace(/^["']|["']$/g, '')
  return FACE_NAME.test(first) ? first : DEFAULT_MATH_FACE
}

/** matplotlib 默认的公式字体集（`mathtext.fontset = dejavusans`）用的那张脸 */
export const DEFAULT_MATH_FACE = 'DejaVu Sans'

/**
 * 图内文字编辑框里的公式预览用哪个「族」排：`face:<图上画公式的那张脸>`（`/api/mathtext` 的 `face:` 写法）。
 *
 * 优先级（越靠前越新）：
 *  1. 这次编辑会让公式跟正文（`mathFontFollow`）：预览直接用将要写下的正文族，不等 manifest 刷新；
 *  2. 字体族已有 override：引擎把公式字体集切到同一族（`overrides._set_text_fontfamily`），值就是它；
 *  3. manifest 的 `math_face`（含 `$…$` 的元素才有）；
 *  4. 都没有：matplotlib 默认的 DejaVu Sans。
 */
export function figureMathFamily(panel: PanelObject, el: ManifestElement, text: string): string {
  const follow = mathFontFollow(panel, el, text)
  const override = effectiveOverride(panel.overrides, el.gid, 'fontfamily')?.value
  const face =
    follow ?? (typeof override === 'string' && override ? override : null) ?? el.math_face ?? DEFAULT_MATH_FACE
  return `face:${faceName(face)}`
}
