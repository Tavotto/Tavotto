/**
 * 图内文字 → 画布文字标注（ADR 0163「图内文字复制为标注」）：把一段 matplotlib 文字换成**摆在页面上同一处**的
 * `TextObject`，粘出来与原文字重合（⌘V 再按 `DUPLICATE_OFFSET_MM` 向右下错开，与 ⌘D 同一个偏移）。
 *
 * 数据只来自 manifest 的 `text_block`（`engine/manifest.text_block`，包含位置、块尺寸、第一行基线、此刻画出来的样式），
 * 和 `face_src` / `cjk_src`（`lib/sourceFont.sourceFontFromElement`，原图那张脸）。本模块只做换算，不判断、不发请求、不写文档：
 * 要写的对象交给 `lib/clipboard` 走既有的粘贴路径。
 *
 * ## 坐标与角度的合成
 *
 * 全程在**页面 mm** 里算，一条链：
 *
 * 1. figure 分数 → 页面点：`panelFullRect`（含裁剪）摊开，再经 `contentToPageVec`（先翻转、再 90° 旋转，与画布画这张图
 *    同一个变换，`lib/panelTransform`）。块的 `w` / `h` 乘 `full.w` / `full.h`（缩放就在这里）；字号走 `pagePtLens`（ADR 0082）。
 * 2. 阅读方向：matplotlib 转角逆时针为正、y 向上 → 内容坐标（y 向下）的方向向量 `(cosθ, −sinθ)`，同样过
 *    `contentToPageVec`，取 `atan2` 得页面上的**顺时针**角 = `rotationDeg`（y 轴标签 θ = 90 → −90；面板再转 90° 就是 0）。
 *    面板被镜像（翻转一次）时，页面上没有「镜像的文字」：取同一条基线上读得顺的那个方向（朝右 / 竖排时朝上），左右对齐跟着对调。
 * 3. 摆放：标注的框绕**自己的中心**转（`rotationDeg` 的语义）。先让标注的第一行基线落在原文字的第一行基线上
 *    （竖直方向），再让文字块沿阅读方向与原文字块同一条边 / 同一条中线（水平方向，由 `align` 决定）；框比文字宽出的余量
 *    （`fitWidthMm`，与旋转前收框同一个公式）因此不会把字挤偏。
 *
 * ## 竖直方向的一个常数
 *
 * 标注的第一行基线在框内的位置取决于字体度量：导出按 OS/2 的 `sTypoAscender / sTypoDescender`
 * （`rendercore/typography.layout_text`：`基线 = 框顶 + size × ((行高 − (asc − desc)) / 2 + asc)`），
 * 基线比行盒中线低 `size × (asc + desc) / 2`（desc 取负）。前端没有字体度量，批准的三族各取一个常数（千分之一 em），
 * 看护 `tests/test_figure_text_copy_metrics.py` 从字体文件反推它们。用户字体（`sourceFont`）与批准族的差在 0.02 em 内。
 */
import type { ManifestElement, ManifestTextBlock } from '@/lib/api'
import { fitWidthMm } from '@/canvas/textFit'
import { normalizeRotationDeg } from '@/lib/geometry'
import { newId } from '@/lib/id'
import { contentToPageVec, panelContentTransform } from '@/lib/panelTransform'
import { panelFullRect } from '@/lib/elementGeom'
import { serializeRuns, type TextRun } from '@/lib/richText'
import { sourceFontFromElement } from '@/lib/sourceFont'
import { genericFamilyOf } from '@/lib/styleSample'
import { pagePtLens } from '@/lib/stylePresets'
import { styleIsItalic, weightIsBold, type CanvasTextFamily } from '@/lib/typography'
import { MM_PER_PT } from '@/lib/units'
import type { PanelObject, TextObject } from '@/types/document'

/** 批准的三族：基线比行盒中线低多少（千分之一 em）= `(sTypoAscender + sTypoDescender) / 2 × 1000`。 */
export const BASELINE_SANS_PER_MILLE = 259
export const BASELINE_SERIF_PER_MILLE = 239
export const BASELINE_MONO_PER_MILLE = 212

const BASELINE_BELOW_MIDLINE_EM: Record<CanvasTextFamily, number> = {
  'sans-serif': BASELINE_SANS_PER_MILLE / 1000,
  serif: BASELINE_SERIF_PER_MILLE / 1000,
  monospace: BASELINE_MONO_PER_MILLE / 1000,
}

/** 标注的默认行高（`TextView` / `typography.layout_text` 的缺省）；单行文字不写 `lineHeight`。 */
const DEFAULT_LINE_HEIGHT = 1.25

/** 这个元素能不能复制成标注：有 `text_block` 就是一段单独的文字（刻度组、曲线、色块……都没有）。 */
export const isCopyableText = (el: Pick<ManifestElement, 'text_block'>): boolean => !!el.text_block

/**
 * matplotlib 文字 → 标注的 `text` + `textFormat`。
 *
 * * 没有公式：保持普通文本，旧标记里会被误读的写法（`a^{b}`、`\^`）按 `serializeRuns` 的规则转义，字面不变；
 *   matplotlib 在非公式文字里把 `\$` 画成 `$`，这里同样。
 * * 有公式：按 matplotlib 的规则（未转义的 `$` 成对开合）切成文字段与公式段，写成 LaTeX 子集（ADR 0152）
 *   `textFormat: 'latex'`，文字段里的 `\ { } ~ …` 被转义成字面。
 */
export function annotationTextOf(source: string, math: boolean): Pick<TextObject, 'text' | 'textFormat'> {
  if (!math) return { text: serializeRuns([{ text: source.replace(/\\\$/g, '$'), script: '' }]) }
  const runs: TextRun[] = []
  let buf = ''
  let inMath = false
  const flush = () => {
    if (buf) runs.push({ text: buf, script: '', ...(inMath ? { math: true } : {}) })
    buf = ''
  }
  for (let i = 0; i < source.length; i++) {
    const ch = source[i]
    if (ch === '\\' && source[i + 1] === '$') {
      // 公式里的 `\$` 是 mathtext 的源码，原样；文字里是字面 `$`
      buf += inMath ? '\\$' : '$'
      i++
    } else if (ch === '$') {
      flush()
      inMath = !inMath
    } else buf += ch
  }
  flush()
  return { text: serializeRuns(runs, 'latex'), textFormat: 'latex' }
}

const flipAlign = (a: TextObject['align']): TextObject['align'] =>
  a === 'left' ? 'right' : a === 'right' ? 'left' : 'center'

const round = (v: number, digits: number) => {
  const k = 10 ** digits
  return Math.round(v * k) / k
}

/**
 * 图内文字元素 → 与它在页面上重合的 `TextObject`（带新 id）。元素不是单独的文字、或面板的尺寸 / 摆放算不出来
 * 时返回 null。**调用方必须传权威 manifest 里的元素**（读 bbox 之后要写文档：ADR 0017）。
 */
export function figureTextToAnnotation(panel: PanelObject, el: ManifestElement): TextObject | null {
  const tb: ManifestTextBlock | undefined = el.text_block
  if (!tb || typeof tb.text !== 'string' || !tb.text.trim()) return null
  const full = panelFullRect(panel)
  if (!(full.w > 0) || !(full.h > 0)) return null
  const tf = panelContentTransform(panel)
  const pcx = panel.x + panel.w / 2
  const pcy = panel.y + panel.h / 2
  const toPage = (fx: number, fy: number): [number, number] => {
    const [dx, dy] = contentToPageVec(tf, full.x + fx * full.w - pcx, full.y + fy * full.h - pcy)
    return [pcx + dx, pcy + dy]
  }

  // 阅读方向（页面，y 向下）。镜像的面板上取读得顺的那个方向，对齐跟着对调
  const theta = (tb.rotation * Math.PI) / 180
  let u = contentToPageVec(tf, Math.cos(theta), -Math.sin(theta))
  let swapped = false
  if (tf.scaleX * tf.scaleY < 0 && (u[0] < -1e-9 || (Math.abs(u[0]) <= 1e-9 && u[1] > 0))) {
    u = [-u[0], -u[1]]
    swapped = true
  }
  const phi = normalizeRotationDeg((Math.atan2(u[1], u[0]) * 180) / Math.PI)
  const v: [number, number] = [-u[1], u[0]] // 「向下」：阅读方向顺时针转 90°

  const sizePt = Number(pagePtLens(panel).toPage('fontsize', tb.size))
  if (!(sizePt > 0)) return null
  const sizeMm = sizePt * MM_PER_PT

  const lines = tb.text.split('\n').length
  const lineHeight =
    lines > 1 && tb.step && tb.step > 0 ? round((tb.step * full.h) / sizeMm, 3) : DEFAULT_LINE_HEIGHT
  const wText = tb.w * full.w
  const hText = tb.h * full.h
  const ascText = tb.asc * full.h

  const family = genericFamilyOf(el.face_src?.family ?? el.face)
  const ha = tb.ha === 'left' || tb.ha === 'right' ? tb.ha : 'center'
  const align = swapped ? flipAlign(ha) : ha

  const wBox = fitWidthMm(wText, sizePt)
  const hBox = lines * lineHeight * sizeMm
  // 框中心相对文字块中心：竖直 = 让第一行基线重合；水平 = 让对齐的那条边 / 中线重合
  const dv = ascText - hText / 2 - sizeMm * (BASELINE_BELOW_MIDLINE_EM[family] - ((lines - 1) * lineHeight) / 2)
  const slack = (wBox - wText) / 2
  const du = align === 'left' ? slack : align === 'right' ? -slack : 0
  const [cx, cy] = toPage(tb.center[0], tb.center[1])
  const bx = cx + du * u[0] + dv * v[0]
  const by = cy + du * u[1] + dv * v[1]

  const sourceFont = sourceFontFromElement(el)
  const out: TextObject = {
    id: newId('t'),
    type: 'text',
    ...annotationTextOf(tb.text, tb.math),
    x: bx - wBox / 2,
    y: by - hBox / 2,
    w: wBox,
    h: hBox,
    sizePt,
    fontFamily: family,
    ...(sourceFont ? { sourceFont } : {}),
    // 有原图字体身份时粗斜以那张脸为准：字体没有粗体、图里画的是常规脸，复制过来不能再模拟一遍
    bold: sourceFont ? sourceFont.bold === true : weightIsBold(tb.weight),
    ...((sourceFont ? sourceFont.italic === true : styleIsItalic(tb.style)) ? { italic: true } : {}),
    color: tb.color,
    align,
    ...(lines > 1 && lineHeight !== DEFAULT_LINE_HEIGHT ? { lineHeight } : {}),
    ...(phi !== 0 ? { rotationDeg: phi } : {}),
  }
  return out
}
