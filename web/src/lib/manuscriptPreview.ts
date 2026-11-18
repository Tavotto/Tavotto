import { pageColumnsOf, type PageColumn } from './pageColumns'
import { PAGE_MM_MAX, PAGE_MM_MIN } from './pageRange'
import { derivePageColumns, type PublicationProfile } from './profile'

export type ManuscriptPlacement = 'single' | 'span' | 'overflow'

export interface ManuscriptBodyLine {
  /** 页内水平起点（mm，相对纸张左缘） */
  x: number
  /** 页内竖直位置（mm，相对纸张顶缘） */
  y: number
  /** 线条长度（mm） */
  w: number
  /** 小节标题短横条（加粗一档墨色） */
  heading?: boolean
}

export interface ManuscriptCaption {
  x: number
  y: number
  w: number
  /** 首行前缀粗横条宽（模拟 "Fig. 1."） */
  leadW: number
  /** 后续说明行（相对 y 的偏移与宽度比例） */
  lines: readonly { dy: number; widthRatio: number }[]
}

export interface ManuscriptLayout {
  /** 外层取景框宽高（mm）；未越出纸张时等于 sheetW × sheetH */
  viewportW: number
  viewportH: number
  /** 纸张尺寸（标准 A4 210 × 297 mm；超大版芯按比例外扩） */
  sheetW: number
  sheetH: number
  /** 纸张在取景框内的偏移（mm）；未越界时为 0 */
  sheetOffsetX: number
  sheetOffsetY: number
  /** 版芯水平边距与总宽（mm） */
  marginX: number
  textBlockW: number
  textBlockTop: number
  textBlockBottom: number
  headerY: number
  footerY: number
  /** 图在纸张坐标系下的真实毫米落位（1:1 比例，绝不缩放变形） */
  figX: number
  figY: number
  figW: number
  figH: number
  /** 图归属的目标栏位盒（单栏槽或通栏版芯） */
  slotX: number
  slotW: number
  showSlotGuide: boolean
  /** 入版形态：单栏绕排 / 跨栏通栏 / 超出版芯 */
  placement: ManuscriptPlacement
  widthOverflow: boolean
  heightOverflow: boolean
  /** 命中的目标栏位（超宽时为最宽栏） */
  targetColumn: PageColumn
  /** 当前规范是否显式定义了栏宽（期刊底未规定栏宽时为 false，界面不把兜底数冠以期刊名） */
  hasExplicitColumns: boolean
  toleranceMm: number
  caption: ManuscriptCaption | null
  bodyLines: readonly ManuscriptBodyLine[]
}

const ISO_A4_W_MM = 210
const ISO_A4_H_MM = 297
const HEADER_Y_MM = 13.5
const TOP_FLOAT_Y_MM = 21.5
const BOTTOM_MARGIN_MM = 20
const FOOTER_Y_MM = 12
const LINE_PITCH_MM = 3.4
const PARAGRAPH_GAP_MM = 1.8
const INDENT_MM = 3.8
const RAGGED_TAILS = [0.74, 0.58, 0.82, 0.66] as const
const HEADING_WIDTHS = [0.44, 0.38] as const

function buildColumnLines(
  x: number,
  w: number,
  startY: number,
  endY: number,
  seedOffset: number,
  withLeadingHeading: boolean,
): ManuscriptBodyLine[] {
  if (w < 24 || endY - startY < LINE_PITCH_MM * 2) return []
  const lines: ManuscriptBodyLine[] = []
  let y = startY
  let pIdx = seedOffset

  if (withLeadingHeading && endY - y >= LINE_PITCH_MM * 4) {
    const hw = w * HEADING_WIDTHS[seedOffset % HEADING_WIDTHS.length]
    lines.push({ x, y, w: hw, heading: true })
    y += LINE_PITCH_MM + 1.4
  }

  let firstParagraph = true
  while (y <= endY - 0.5) {
    const paraLen = 5 + (pIdx % 3)
    for (let i = 0; i < paraLen; i++) {
      if (y > endY - 0.5) break
      const isFirst = i === 0 && !firstParagraph
      const isLast = i === paraLen - 1
      const lineX = isFirst ? x + INDENT_MM : x
      const maxW = w - (isFirst ? INDENT_MM : 0)
      const lineW = isLast ? maxW * RAGGED_TAILS[pIdx % RAGGED_TAILS.length] : maxW
      lines.push({ x: lineX, y, w: Math.max(8, lineW) })
      y += LINE_PITCH_MM
    }
    firstParagraph = false
    pIdx += 1
    // 偶发插入一个小节标题，营造真实论文内页的呼吸层次
    if (pIdx - seedOffset === 2 && endY - y >= LINE_PITCH_MM * 5) {
      y += PARAGRAPH_GAP_MM + 1.2
      lines.push({ x, y, w: w * HEADING_WIDTHS[(pIdx + 1) % HEADING_WIDTHS.length], heading: true })
      y += LINE_PITCH_MM + 1.4
      firstParagraph = true
    } else {
      y += PARAGRAPH_GAP_MM
    }
  }
  return lines
}

/**
 * 计算一张图（`sizeMm = [w, h]`）按当前 `PublicationProfile` 放入学术内页时的 1:1 真实毫米版式。
 *
 * 栏宽来源严格复用 `derivePageColumns` / `pageColumnsOf`（单一权威，不维护第二份栏宽常数）：
 * - 图宽能装进窄栏且右侧留得出正文栏（≥ 36 mm）→ 左上单栏浮动（`\begin{figure}[t]`），右栏全文流式绕排；
 * - 图宽超过单栏但不超版芯总宽 → 顶部跨栏浮动（`\begin{figure*}[t]`），下方双栏正文并排；
 * - 图宽超出版芯总宽或图高超出单页版芯 → 如实按 1:1 比例伸出版芯边界并标 `overflow`，直观暴露尺寸越界。
 */
export function computeManuscriptLayout(
  sizeMm: readonly [number, number],
  profile: PublicationProfile,
): ManuscriptLayout {
  const [rawW, rawH] = sizeMm
  const figW = Math.max(1, rawW)
  const figH = Math.max(1, rawH)

  const explicitCols = derivePageColumns(profile).filter(
    (c) =>
      typeof c.width_mm === 'number' &&
      Number.isFinite(c.width_mm) &&
      c.width_mm >= PAGE_MM_MIN &&
      c.width_mm <= PAGE_MM_MAX,
  )
  const hasExplicitColumns = explicitCols.length > 0
  const columns = [...pageColumnsOf(profile)].sort((a, b) => a.w - b.w)
  const rawTol = profile.widths_mm?.tolerance_mm
  const toleranceMm = Math.max(0.5, typeof rawTol === 'number' && Number.isFinite(rawTol) ? rawTol : 0.5)

  const widestCol = columns[columns.length - 1]
  const textBlockW =
    columns.length === 1 && widestCol.w <= 105 ? widestCol.w * 2 + 6 : widestCol.w

  const sheetW = textBlockW <= 188 ? ISO_A4_W_MM : Math.ceil(textBlockW + 24)
  const sheetH =
    sheetW === ISO_A4_W_MM ? ISO_A4_H_MM : Math.round(sheetW * (ISO_A4_H_MM / ISO_A4_W_MM))

  const marginX = (sheetW - textBlockW) / 2
  const textBlockTop = TOP_FLOAT_Y_MM
  const textBlockBottom = sheetH - BOTTOM_MARGIN_MM

  const firstColW = columns[0].w
  const pairedExact =
    columns.length >= 2 &&
    2 * firstColW + 3.5 <= textBlockW &&
    2 * firstColW + 14 >= textBlockW
  const gutterMm = pairedExact ? textBlockW - 2 * firstColW : 6
  const bodyColW = pairedExact ? firstColW : (textBlockW - gutterMm) / 2

  const matchedCol = columns.find((c) => figW <= c.w + toleranceMm) ?? null
  const canWrapRight =
    matchedCol !== null &&
    columns.length >= 2 &&
    matchedCol.id !== widestCol.id &&
    Math.max(figW, matchedCol.w) + gutterMm + 36 <= textBlockW

  const maxFitHeight = textBlockBottom - textBlockTop - 10
  const widthOverflow = figW > textBlockW + toleranceMm
  const heightOverflow = figH > maxFitHeight

  const placement: ManuscriptPlacement =
    widthOverflow || heightOverflow ? 'overflow' : canWrapRight ? 'single' : 'span'

  const targetColumn = matchedCol ?? widestCol
  const slotW = placement === 'single' && matchedCol ? Math.max(figW, matchedCol.w) : textBlockW
  const slotX = marginX
  const figX = slotX + (slotW - figW) / 2
  const figY = textBlockTop

  // 图显著窄于所在栏位（如 65 mm 放进 89 mm 单栏，或 115 mm 放进 150 mm 通栏）时画出浅栏位底衬，一眼看出两侧余量
  const showSlotGuide = placement !== 'overflow' && slotW - figW > Math.max(toleranceMm, 1.5)

  const captionTop = figY + figH + 3.2
  const captionFits = !heightOverflow && captionTop + 6 <= textBlockBottom
  const captionW = Math.min(textBlockW, slotW)
  const caption: ManuscriptCaption | null = captionFits
    ? {
        x: slotX,
        y: captionTop,
        w: captionW,
        leadW: Math.min(11.5, captionW * 0.22),
        lines:
          placement === 'single'
            ? [
                { dy: 2.8, widthRatio: 1 },
                { dy: 5.6, widthRatio: 0.68 },
              ]
            : [
                { dy: 2.8, widthRatio: 0.62 },
              ],
      }
    : null

  const floatBottom = caption
    ? caption.y + (caption.lines[caption.lines.length - 1]?.dy ?? 0) + 5.8
    : figY + figH + 5.5

  const bodyLines: ManuscriptBodyLine[] = []
  if (placement === 'single') {
    bodyLines.push(
      ...buildColumnLines(marginX, Math.min(bodyColW, slotW), floatBottom, textBlockBottom, 0, true),
    )
    const rightX = Math.max(marginX + textBlockW - bodyColW, slotX + slotW + gutterMm)
    const rightW = marginX + textBlockW - rightX
    bodyLines.push(
      ...buildColumnLines(rightX, rightW, textBlockTop, textBlockBottom, 2, true),
    )
  } else {
    bodyLines.push(
      ...buildColumnLines(marginX, bodyColW, floatBottom, textBlockBottom, 0, true),
      ...buildColumnLines(marginX + textBlockW - bodyColW, bodyColW, floatBottom, textBlockBottom, 3, false),
    )
  }

  const overhangX = Math.max(0, -figX, figX + figW - sheetW)
  const overhangTop = Math.max(0, -figY)
  const overhangBottom = Math.max(0, figY + figH - sheetH)
  const padX = overhangX > 0 ? overhangX + 8 : 0
  const padTop = overhangTop > 0 ? overhangTop + 8 : 0
  const padBottom = overhangBottom > 0 ? overhangBottom + 8 : 0

  return {
    viewportW: sheetW + padX * 2,
    viewportH: sheetH + padTop + padBottom,
    sheetW,
    sheetH,
    sheetOffsetX: padX,
    sheetOffsetY: padTop,
    marginX,
    textBlockW,
    textBlockTop,
    textBlockBottom,
    headerY: HEADER_Y_MM,
    footerY: sheetH - FOOTER_Y_MM,
    figX,
    figY,
    figW,
    figH,
    slotX,
    slotW,
    showSlotGuide,
    placement,
    widthOverflow,
    heightOverflow,
    targetColumn,
    hasExplicitColumns,
    toleranceMm,
    caption,
    bodyLines,
  }
}
