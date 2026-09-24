/**
 * 安全自动修复的**画布层**计划（ADR 0030 / 0080）。
 *
 * 修复分两条路，按**修的是谁**分，一类对象只有一个计划器：
 *
 * * **面板内部**（图里的刻度、轴标题、曲线、图例……）：计划、真实渲染与裁决全在
 *   后端（`engine/specfix.py` + `/api/engine/specfix`）。原来这里逐条算、盲写
 *   override：每条规则只算「对我这条最省事的那个数」，于是刻度被提到比轴标题还大、
 *   刻度字变大把轴标题挤出图幅也没人知道、字体一条都修不了。现在改完要真的渲染
 *   一遍、真的过了预检、没有别处变差才提交，这些都只有后端做得到。
 * * **画布层**（画布标注文字的字号、页面宽度）：没有渲染这一步，本文件算。
 *
 * `safe_auto` 的判据仍是三条：目标值唯一、修完真的能过（绝对下限不含等号，
 * 所以"提到正好 8 pt"不算修好）、不动科研数据（色图 / 裁剪 / 重排一律不自动）。
 * 字体从这一版起进了面板那一路：改成规范的拉丁字体是确定的，而「这台机器上装没装」
 * 由后端对真实渲染里画字的那张脸核验，没装就如实退出、不假装换了。
 *
 * **本文件是纯计算**：文档 + 规范 + 这条问题进，计划出——不碰 store、不写磁盘、
 * 不发后端，于是 `lib/validation.ts` 判 `fixKind` 时可以直接调它。
 */
import { FALLBACK_MIN_FONT_SIZE_PT, type PublicationProfile } from './profile'
import type { ValidationIssue } from './validation'
import type { FigureDocument } from '@/types/document'

/** 字号 / 线宽落在人用的 0.5 档格子上，而不是 8.000001 这种数字。 */
const GRID = 0.5
const up = (v: number): number => Math.ceil(v / GRID - 1e-9) * GRID
const down = (v: number): number => Math.floor(v / GRID + 1e-9) * GRID
const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

export type FixChoice = string

/** 一次修复要写的东西。**只描述，不执行**——执行在 `applyFixPlans()`。 */
/**
 * 这条规则**能接受的取值区间**（与计划里的 `value` 同一个单位）。
 *
 * 有它才能把「同一个属性上的两条计划」合并对：一条规则算出的目标值只是
 * "对我这条最省事的那个数"，两条规则各写一遍时后写的赢，而它可能违反前一条
 * （默认规范上一条 6pt 图例文字同时命中 `font-below-absolute-floor`→8.5 与
 * `legend-font-size`→8.0，后者盖掉前者，8.0 仍然过不了绝对下限——PR #214
 * 第三轮评审）。取区间的交集就不会有这个问题。
 *
 * 只有能给出区间的计划参与合并；给不出的一律**整组不修**，不假装修好了。
 */
export interface FixBound {
  min?: number
  max?: number
}

export type FixPlan =
  | { kind: 'textSize'; objectId: string; sizePt: number; bound?: FixBound }
  | { kind: 'pageWidth'; widthMm: number }

/**
 * 这条问题归哪一路修：`canvas` = 本文件的计划、`engine` = 后端事务、`null` = 修不了。
 *
 * 面板内部的问题要有 gid（落在某个元素上）且面板连着脚本（没有脚本就没有引擎
 * 会话，也就不会有这些问题）；能不能真的修好由后端对真实渲染说了算。
 */
export type FixRoute = 'canvas' | 'engine'

/** 后端事务修得了的规则（与 `engine/specfix.FIXABLE_RULES` 严格同源，看护见 tests/test_specfix.py） */
export const ENGINE_FIX_RULES: readonly string[] = [
  'font-below-absolute-floor',
  'font-too-small',
  'font-too-large',
  'legend-font-size',
  'font-family-substituted',
  'line-width-off-preset',
  'tick-direction',
  'legend-frame',
  'spines-not-enclosed',
  'text-weight-policy',
  'element-outside-figure',
]

export function fixRoute(issue: ValidationIssue, doc: FigureDocument): FixRoute | null {
  if (issue.ruleCode === 'page-width') return 'canvas'
  const obj = issue.objectRef.objectId
    ? doc.objects.find((o) => o.id === issue.objectRef.objectId)
    : undefined
  if (obj?.type === 'text') return 'canvas'
  if (obj?.type === 'panel' && obj.script && issue.objectRef.gid && ENGINE_FIX_RULES.includes(issue.ruleCode)) {
    return 'engine'
  }
  return null
}

/** `user_choice` 规则的可选项（界面据此出菜单）。 */
export interface FixOption {
  /** 传回 `applyIssueFix(id, choice)` 的值 */
  choice: FixChoice
  /** 文案 key 的参数（措辞在 `validationText.ts`） */
  labelKey: string
  params?: Record<string, unknown>
}

/**
 * 这条问题的可选修复。只有 `page-width` 这一条：单栏还是双栏是**用户的
 * 排版决定**，两个答案同样合理，工具不许替他挑。
 */
export function fixOptions(issue: ValidationIssue, profile: PublicationProfile): FixOption[] {
  if (issue.ruleCode !== 'page-width') return []
  const { single, double } = profile.widths_mm
  const out: FixOption[] = []
  if (num(single) != null) out.push({ choice: 'single', labelKey: 'single', params: { mm: single } })
  if (num(double) != null) out.push({ choice: 'double', labelKey: 'double', params: { mm: double } })
  return out
}

/**
 * 算一条**画布层**修复计划。**纯函数**：文档 + 规范 + 这条问题进，计划出。
 * 面板内部的问题不在这里算（回 `null`，走 `fixRoute() === 'engine'` 那一路）。
 */
export function planFix(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
  choice?: FixChoice,
): FixPlan | null {
  if (issue.ruleCode === 'page-width') return planPageWidth(profile, choice)
  const obj = issue.objectRef.objectId
    ? doc.objects.find((o) => o.id === issue.objectRef.objectId)
    : undefined
  if (obj?.type !== 'text') return null
  switch (issue.ruleCode) {
    case 'font-too-small':
    case 'font-below-absolute-floor':
      return planFontUp(issue, profile, doc)
    case 'font-too-large':
      return planFontDown(issue, profile, doc)
    default:
      return null
  }
}

/**
 * 提字号。目标是**能通过**的最小 0.5 档：既要大于绝对下限（那条边不含等号，
 * ADR 0006 / 0029），也要不低于规范下限。改完再按同一条判据验一遍，验不过
 * 就不给这颗按钮——「修完还是红的」是最糟的一种修复。
 */
function planFontUp(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
): FixPlan | null {
  const floor = num(profile.absolute_min_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const strict = num(profile.min_effective_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const max = num(profile.max_font_size_pt)
  // 大于 floor 的最小 0.5 档；正好等于 floor 的那一档过不了，再上一档
  let target = up(Math.max(strict, floor))
  if (target <= floor) target += GRID
  if (max != null && target > max) return null
  // 区间下界就是 `target`：`eff <= floor` 才算违规，所以正好等于 floor 的
  // 那一档过不了——下界是**调整过严格性之后**的那个数，不是 floor 本身
  return writeFontSize(issue, doc, target, { min: target, max: max ?? undefined })
}

function planFontDown(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
): FixPlan | null {
  const max = num(profile.max_font_size_pt)
  const floor = num(profile.absolute_min_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  if (max == null) return null
  const target = down(max)
  if (target <= floor) return null
  // 上界是 max，下界是"比 floor 大的第一档"（`eff <= floor` 才算违规）
  return writeFontSize(issue, doc, target, { min: floor + GRID, max })
}

/** 画布标注的 `sizePt` 已经是页面上的绝对 pt，不乘缩放。 */
function writeFontSize(
  issue: ValidationIssue,
  doc: FigureDocument,
  targetEff: number,
  boundEff?: FixBound,
): FixPlan | null {
  const obj = issue.objectRef.objectId
    ? doc.objects.find((o) => o.id === issue.objectRef.objectId)
    : undefined
  if (obj?.type !== 'text' || obj.sizePt === targetEff) return null
  return { kind: 'textSize', objectId: obj.id, sizePt: targetEff, bound: boundEff }
}

function planPageWidth(profile: PublicationProfile, choice?: FixChoice): FixPlan | null {
  if (choice !== 'single' && choice !== 'double') return null
  const w = num(profile.widths_mm[choice])
  return w == null || w <= 0 ? null : { kind: 'pageWidth', widthMm: w }
}
