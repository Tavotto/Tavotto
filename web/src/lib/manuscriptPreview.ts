import { pageColumnsOf, type PageColumn } from './pageColumns'
import { PAGE_MM_MAX, PAGE_MM_MIN } from './pageRange'
import { derivePageColumns, type PublicationProfile } from './profile'

export type ManuscriptPlacement = 'single' | 'span' | 'overflow'

export type SingleSlotVariant = 'top-right' | 'mid-left' | 'bottom-right' | 'top-left'
export type SpanSlotVariant = 'mid-span' | 'top-span' | 'bottom-span'
export type ManuscriptSlotVariant = SingleSlotVariant | SpanSlotVariant | 'overflow'

export const SINGLE_SLOT_VARIANTS: readonly SingleSlotVariant[] = [
  'top-right',
  'mid-left',
  'bottom-right',
  'top-left',
]

export const SPAN_SLOT_VARIANTS: readonly SpanSlotVariant[] = [
  'top-span',
  'mid-span',
  'bottom-span',
]

/** CSS 标准物理换算：1 in = 25.4 mm = 96 CSS px，使纸面上的 pt 字号与 Matplotlib 图内 pt 字号严格 1:1 等大 */
export const PX_PER_MM = 96 / 25.4

/** 正文基线网格行高（px）：9 pt 字号配 12 pt（16 px）行距，所有标题与公式高度均对齐其整数倍，杜绝底边半行截断 */
export const BODY_LINE_PX = 16

export type InlineToken =
  | string
  | { italic: string; sub?: string; sup?: string }
  | { roman: string; sub?: string; sup?: string }
  | { sup: string }
  | { bold: string }

export type ManuscriptProseBlock =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; indent: boolean; runs: readonly InlineToken[] }
  | { kind: 'equation'; runs: readonly InlineToken[]; num: string }

export interface ManuscriptSpecimen {
  category: string
  runningTitle: string
  volumeFolio: string
  pageNumber: string
  footerNote: string
  captionLead: string
  captionTitle: string
  captionRuns: readonly InlineToken[]
  primaryBlocks: readonly ManuscriptProseBlock[]
  secondaryBlocks: readonly ManuscriptProseBlock[]
}

/**
 * 学术内页排印样张文本（全部采用原生上/下标 token，避免 Unicode 下标在 Times New Roman 中回退成字母 o）。
 */
export const MANUSCRIPT_SPECIMEN: ManuscriptSpecimen = {
  category: 'RESEARCH ARTICLE',
  runningTitle: 'Quantitative characterization and regime scaling',
  volumeFolio: 'Vol. 628 · No. 4',
  pageNumber: '412',
  footerNote: 'Sample manuscript text for scale reference only · Exports figure only',
  captionLead: 'Fig. 1 |',
  captionTitle: 'Quantitative response and characteristic scaling across experimental conditions.',
  captionRuns: [
    'Representative measurements comparing primary observables, dispersion bounds (mean ± s.d.), and analytical predictions under controlled baseline conditions (',
    { italic: 'n' },
    ' = 6 independent replicates; ',
    { italic: 'R', sup: '2' },
    ' = 0.994, ',
    { italic: 'p' },
    ' < 0.001).',
  ],
  primaryBlocks: [
    { kind: 'heading', text: '2. Results and Discussion' },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'Across the examined parameter regime, the primary observable exhibits a reproducible transition consistent with continuum scaling theory',
        { sup: '1–3' },
        ' (',
        { bold: 'Fig. 1' },
        '). Quantitative regression over independent replicates confirms that local variance remains bounded within experimental uncertainty (',
        { italic: 'R', sup: '2' },
        ' = 0.994, ',
        { italic: 'p' },
        ' < 0.001), ruling out systematic drift across consecutive acquisition cycles.',
      ],
    },
    {
      kind: 'paragraph',
      indent: true,
      runs: [
        'When normalized by the characteristic baseline scale ',
        { italic: 'τ', sub: '0' },
        ', trajectories collapse onto a single master curve spanning nearly two decades of dynamic range',
        { sup: '4,5' },
        '. Minor deviations near the upper boundary arise from finite-aperture relaxation and are captured without free parameters once second-order coupling is included.',
      ],
    },
    {
      kind: 'paragraph',
      indent: true,
      runs: [
        'Comparative benchmarks against reference protocols further demonstrate a 2.4-fold improvement in signal-to-background selectivity while preserving asymptotic linearity across all tested boundary conditions',
        { sup: '6,7' },
        '.',
      ],
    },
    { kind: 'heading', text: '2.1. Asymptotic Regime Identification' },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'Independent cross-validation using held-out calibration series reproduces both the inflection threshold and post-transition slope within ±1.4% relative tolerance, confirming that extracted parameters reflect intrinsic response rather than instrument transfer',
        { sup: '8,9' },
        '.',
      ],
    },
    {
      kind: 'paragraph',
      indent: true,
      runs: [
        'Decomposition of residual fluctuations reveals a stationary Gaussian noise floor across all sampling intervals, with autocorrelation decay occurring well below the primary residence window (',
        { italic: 'τ', sub: 'c' },
        ' / ',
        { italic: 'τ', sub: '0' },
        ' < 0.04). Consequently, ordinary and weighted estimators converge to indistinguishable posteriors.',
      ],
    },
    {
      kind: 'paragraph',
      indent: true,
      runs: [
        'Auxiliary parameter sweeps across three orders of magnitude in driving amplitude yield consistent scaling exponents throughout the entire stability domain',
        { sup: '10' },
        '.',
      ],
    },
  ],
  secondaryBlocks: [
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'Systematic variation of boundary conditions demonstrates that the steady-state plateau is insensitive to initialization history and sampling cadence. Repeating the measurement sequence under orthogonal perturbations yields indistinguishable profiles within ±1.8% relative error',
        { sup: '7,8' },
        '.',
      ],
    },
    {
      kind: 'equation',
      runs: [
        { italic: 'J' },
        '(',
        { italic: 'θ' },
        ') = ',
        { italic: 'J', sub: '0' },
        ' + ∑',
        { italic: 'w', sub: 'i' },
        ' exp(−',
        { italic: 'λ', sub: 'i' },
        ' ',
        { italic: 't' },
        ' / ',
        { italic: 'τ', sub: '0' },
        '),',
      ],
      num: '(1)',
    },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'where ',
        { italic: 'J', sub: '0' },
        ' denotes the equilibrium baseline, ',
        { italic: 'w', sub: 'i' },
        ' represents the modal amplitude, and ',
        { italic: 'λ', sub: 'i' },
        ' characterizes the relaxation spectrum extracted directly from the profiles summarized in ',
        { bold: 'Fig. 1' },
        '.',
      ],
    },
    { kind: 'heading', text: '2.2. Robustness and Scaling Bounds' },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'Sensitivity analysis across temperature, concentration, and acquisition geometry confirms that all principal trends remain invariant under dimensionless rescaling',
        { sup: '9,10' },
        '. Bootstrap confidence intervals (',
        { italic: 'N' },
        ' = 10',
        { sup: '4' },
        ' resamples) closely track analytical error propagation throughout the transition region.',
      ],
    },
    {
      kind: 'paragraph',
      indent: true,
      runs: [
        'Taken together, these observations establish a quantitative link between microscopic kinetics and macroscopic response, providing a direct calibration standard for downstream experimental design and numerical modeling',
        { sup: '11–13' },
        '.',
      ],
    },
    {
      kind: 'equation',
      runs: [
        { italic: 'σ', sup: '2' },
        '(',
        { italic: 'τ' },
        ') = ',
        { italic: 'σ', sub: '0', sup: '2' },
        ' [1 + (',
        { italic: 'τ' },
        ' / ',
        { italic: 'τ', sub: 'c' },
        ')',
        { sup: '−γ' },
        ']',
        { sup: '−1' },
        ',',
      ],
      num: '(2)',
    },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'with effective exponent ',
        { italic: 'γ' },
        ' = 1.48 ± 0.03 across all replicates, matching theoretical bounds predicted by fluctuation–dissipation relations under weak coupling',
        { sup: '14' },
        '.',
      ],
    },
    { kind: 'heading', text: '3. Experimental Protocol' },
    {
      kind: 'paragraph',
      indent: false,
      runs: [
        'All measurements were acquired under stabilized thermal regulation (Δ',
        { italic: 'T' },
        ' < 0.05 K) following automated baseline equilibration. Raw traces were digitized at 16-bit resolution and processed through a zero-phase calibration pipeline prior to aggregation',
        { sup: '15,16' },
        '.',
      ],
    },
  ],
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
  /** 版芯水平边距、总宽、栏距（mm） */
  marginX: number
  textBlockW: number
  bodyColW: number
  gutterMm: number
  /** 单栏绕排时：图所在栏宽 `slotW`，对侧正文栏宽 `oppositeColW` */
  slotW: number
  oppositeColW: number
  textBlockTop: number
  textBlockBottom: number
  headerRuleY: number
  footerRuleY: number
  /** 越界模式下图的绝对落位（mm）；常规模式下按流式版位排布 */
  figX: number
  figY: number
  figW: number
  figH: number
  /** 入版形态：单栏绕排 / 跨栏通栏 / 超出版芯 */
  placement: ManuscriptPlacement
  /** 当前生效的具体版位（右上 / 左中 / 右下 / 左上，或顶置 / 版中 / 底置通栏） */
  slotVariant: ManuscriptSlotVariant
  /** 中段嵌排时上方引导正文行数（整数行，图太高放不下时自动收为 0） */
  leadLines: number
  widthOverflow: boolean
  heightOverflow: boolean
  /** 命中的目标栏位（超宽时为最宽栏） */
  targetColumn: PageColumn
  /** 当前规范是否显式定义了栏宽（期刊底未规定栏宽时为 false，界面不把兜底数冠以期刊名） */
  hasExplicitColumns: boolean
  toleranceMm: number
  showCaption: boolean
}

const ISO_A4_W_MM = 210
const ISO_A4_H_MM = 297
const HEADER_RULE_Y_MM = 15.2
const TOP_FLOAT_Y_MM = 20.5
const BOTTOM_MARGIN_MM = 19.5
const FOOTER_RULE_OFFSET_MM = 13.5

/**
 * 计算一张图（`sizeMm = [w, h]`）按当前 `PublicationProfile` 与版位索引 `slotStep` 放入学术内页时的 1:1 真实毫米版式。
 */
export function computeManuscriptLayout(
  sizeMm: readonly [number, number],
  profile: PublicationProfile,
  slotStep = 0,
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
  const textBlockH = textBlockBottom - textBlockTop

  const firstColW = columns[0].w
  const pairedExact =
    columns.length >= 2 &&
    2 * firstColW + 3.5 <= textBlockW &&
    2 * firstColW + 14 >= textBlockW
  const gutterMm = pairedExact ? textBlockW - 2 * firstColW : 6
  const bodyColW = pairedExact ? firstColW : (textBlockW - gutterMm) / 2

  const matchedCol = columns.find((c) => figW <= c.w + toleranceMm) ?? null
  const canWrapSide =
    matchedCol !== null &&
    columns.length >= 2 &&
    matchedCol.id !== widestCol.id &&
    Math.max(figW, matchedCol.w) + gutterMm + 36 <= textBlockW

  const maxFitHeight = textBlockH - 10
  const widthOverflow = figW > textBlockW + toleranceMm
  const heightOverflow = figH > maxFitHeight

  const placement: ManuscriptPlacement =
    widthOverflow || heightOverflow ? 'overflow' : canWrapSide ? 'single' : 'span'

  const targetColumn = matchedCol ?? widestCol
  const slotW = placement === 'single' && matchedCol ? Math.max(figW, matchedCol.w) : textBlockW
  const oppositeColW =
    placement === 'single' ? Math.max(36, textBlockW - slotW - gutterMm) : bodyColW

  const step = Math.max(0, Math.floor(slotStep))
  const slotVariant: ManuscriptSlotVariant =
    placement === 'overflow'
      ? 'overflow'
      : placement === 'single'
        ? SINGLE_SLOT_VARIANTS[step % SINGLE_SLOT_VARIANTS.length]
        : SPAN_SLOT_VARIANTS[step % SPAN_SLOT_VARIANTS.length]

  // 中段嵌排（mid-left / mid-span）上方留几行引导正文；若图本身较高则自动缩减行数保证不挤出底边
  const spareHeightPx = Math.max(0, (textBlockH - figH - 28) * PX_PER_MM)
  const maxLeadLines = Math.floor(spareHeightPx / BODY_LINE_PX)
  const leadLines =
    slotVariant === 'mid-left'
      ? Math.min(8, maxLeadLines)
      : slotVariant === 'mid-span'
        ? Math.min(6, maxLeadLines)
        : 0

  // 水平坐标：右栏版位对齐版芯右缘，左栏对齐版芯左缘，通栏/越界在版芯内水平居中
  const isRightSlot = slotVariant === 'top-right' || slotVariant === 'bottom-right'
  const slotOriginX =
    placement === 'single'
      ? isRightSlot
        ? marginX + textBlockW - slotW
        : marginX
      : marginX
  const figX = slotOriginX + (slotW - figW) / 2
  const figY = textBlockTop

  const showCaption = !heightOverflow && figH + 10 <= textBlockH

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
    bodyColW,
    gutterMm,
    slotW,
    oppositeColW,
    textBlockTop,
    textBlockBottom,
    headerRuleY: HEADER_RULE_Y_MM,
    footerRuleY: sheetH - FOOTER_RULE_OFFSET_MM,
    figX,
    figY,
    figW,
    figH,
    placement,
    slotVariant,
    leadLines,
    widthOverflow,
    heightOverflow,
    targetColumn,
    hasExplicitColumns,
    toleranceMm,
    showCaption,
  }
}
