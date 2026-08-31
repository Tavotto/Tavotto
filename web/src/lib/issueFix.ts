/**
 * 安全自动修复（ADR 0030）。
 *
 * **只有确定、安全、可撤销的修复才叫 `safe_auto`。** 判据三条，缺一条就
 * 降回「不修」——一颗按了不知道会发生什么的「修复」按钮，比没有按钮更坏：
 *
 * 1. **目标值算得出来且唯一**（把 7.5pt 提到规范允许的最小档位；把线宽吸到
 *    最近的档位）。要用户在两个同样合理的答案里挑，那是 `user_choice`。
 * 2. **修完真的能过**。规范里 `eff <= floor` 是**不含等号**的下限，所以"提到
 *    正好 8 pt"根本过不了——目标值必须落在能通过的那一侧，并且改完再查一遍。
 * 3. **不动科研数据**。字体替换（会不会装了都不知道）、色图替换（改的是数据
 *    语义）、裁剪、重排一律不自动做。
 *
 * 落地全部经统一 document action：一条历史、⌘Z 一次撤回、正确 dirty、
 * autosave 照常（`store/issueFixActions.ts`）。**本文件是纯计算**：文档 +
 * 规范 + 这条问题进，计划出——不碰 store、不写磁盘、不发后端，于是
 * `lib/validation.ts` 判 `fixKind` 时可以直接调它而不把整棵 store 图拖进来。
 */
import { FALLBACK_MIN_FONT_SIZE_PT, type PublicationProfile } from './profile'
import { panelScale } from './preflight'
import type { ValidationIssue } from './validation'
import type { FigureDocument, PanelObject } from '@/types/document'

/** 字号 / 线宽落在人用的 0.5 档格子上，而不是 8.000001 这种数字。 */
const GRID = 0.5
const up = (v: number): number => Math.ceil(v / GRID - 1e-9) * GRID
const down = (v: number): number => Math.floor(v / GRID + 1e-9) * GRID
const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

export type FixChoice = string

/** 一次修复要写的东西。**只描述，不执行**——执行在 `applyFixPlans()`。 */
export type FixPlan =
  | {
      kind: 'override'
      objectId: string
      patches: { gid: string; prop: string; value: unknown }[]
    }
  | { kind: 'textSize'; objectId: string; sizePt: number }
  | { kind: 'pageWidth'; widthMm: number }

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

const panelOf = (doc: FigureDocument, id: string | null): PanelObject | null => {
  const o = id ? doc.objects.find((x) => x.id === id) : undefined
  return o?.type === 'panel' ? o : null
}

/**
 * 算一条修复计划。**纯函数**：文档 + 规范 + 这条问题进，计划出。
 * 算不出来就回 `null`，调用方据此把 `fixKind` 降成 `none`。
 */
export function planFix(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
  choice?: FixChoice,
): FixPlan | null {
  const d = issue.technicalDetails
  const ref = issue.objectRef
  switch (issue.ruleCode) {
    case 'font-too-small':
    case 'font-below-absolute-floor':
      return planFontUp(issue, profile, doc)
    case 'font-too-large':
      return planFontDown(issue, profile, doc)
    case 'legend-font-size':
      return planLegendFont(issue, profile, doc)
    case 'legend-frame':
      return ref.objectId && ref.gid
        ? { kind: 'override', objectId: ref.objectId, patches: [{ gid: ref.gid, prop: 'frameon', value: false }] }
        : null
    case 'tick-direction': {
      const want = profile.axis_policy.tick_direction
      return ref.objectId && ref.gid && typeof want === 'string' && want
        ? { kind: 'override', objectId: ref.objectId, patches: [{ gid: ref.gid, prop: 'direction', value: want }] }
        : null
    }
    case 'text-weight-policy': {
      const want = d.want
      return ref.objectId && ref.gid && (want === 'bold' || want === 'normal')
        ? { kind: 'override', objectId: ref.objectId, patches: [{ gid: ref.gid, prop: 'weight', value: want }] }
        : null
    }
    case 'spines-not-enclosed': {
      const missing = Array.isArray(d.missing) ? d.missing.filter((s) => typeof s === 'string') : []
      return ref.objectId && ref.gid && missing.length
        ? {
            kind: 'override',
            objectId: ref.objectId,
            patches: missing.map((side) => ({ gid: ref.gid!, prop: `spine_${side}`, value: true })),
          }
        : null
    }
    case 'line-width-off-preset':
      return planLineWidth(issue, profile, doc)
    case 'page-width':
      return planPageWidth(profile, choice)
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
  return writeFontSize(issue, doc, target, 'up')
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
  return writeFontSize(issue, doc, target, 'down')
}

function planLegendFont(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
): FixPlan | null {
  const lo = num(profile.legend_policy.min_font_size_pt)
  const hi = num(profile.legend_policy.max_font_size_pt)
  const eff = num(issue.technicalDetails.effective_pt)
  if (lo == null || hi == null || eff == null || lo > hi) return null
  // 区间两端是**闭**的（判据用 `< lo` / `> hi`），所以端点本身就是合法目标
  if (eff < lo) return writeFontSize(issue, doc, Math.min(up(lo), hi), 'up')
  if (eff > hi) return writeFontSize(issue, doc, Math.max(down(hi), lo), 'down')
  return null
}

/**
 * 把「读者量到的 pt」换算回脚本坐标系里的值再写。
 *
 * 面板缩到 60% 时 `eff = size × scale`：直接把 `fontsize` 写成 8.5 的话，
 * 读者量到的是 5.1pt——修复反而制造了一条新的违规。取整方向也要跟着目标走
 * （提字号往上取，降字号往下取），否则两位小数的舍入会把结果推回违规那侧。
 */
function writeFontSize(
  issue: ValidationIssue,
  doc: FigureDocument,
  targetEff: number,
  dir: 'up' | 'down',
): FixPlan | null {
  const ref = issue.objectRef
  if (!ref.objectId) return null
  const obj = doc.objects.find((o) => o.id === ref.objectId)
  if (obj?.type === 'text') {
    // 画布标注的 sizePt 已经是页面上的绝对 pt，不乘 scale
    if (obj.sizePt === targetEff) return null
    return { kind: 'textSize', objectId: obj.id, sizePt: targetEff }
  }
  const panel = panelOf(doc, ref.objectId)
  if (!panel || !ref.gid || !issue.propertyPath) return null
  const scale = panelScale(panel)
  if (!Number.isFinite(scale) || scale <= 0) return null
  const raw = targetEff / scale
  const round = dir === 'up' ? Math.ceil : Math.floor
  const value = round(raw * 100) / 100
  if (!Number.isFinite(value) || value <= 0) return null
  return {
    kind: 'override',
    objectId: panel.id,
    patches: [{ gid: ref.gid, prop: issue.propertyPath, value }],
  }
}

function planLineWidth(
  issue: ValidationIssue,
  profile: PublicationProfile,
  doc: FigureDocument,
): FixPlan | null {
  const ref = issue.objectRef
  const prop = issue.propertyPath
  if (!ref.objectId || !ref.gid || !prop) return null
  const presets =
    prop === 'spine_linewidth'
      ? (profile.axis_policy.frame_linewidth_pt ?? [])
      : (profile.line_widths_pt ?? [])
  const eff = num(issue.technicalDetails.effective_pt)
  if (!presets.length || eff == null) return null
  // 最近的档位；正好等距时取更细的那一档（保守：不无声加粗数据线）
  let best = presets[0]
  for (const p of presets) if (Math.abs(p - eff) < Math.abs(best - eff)) best = p
  const panel = panelOf(doc, ref.objectId)
  if (!panel) return null
  const scale = panelScale(panel)
  if (!Number.isFinite(scale) || scale <= 0) return null
  const value = Math.round((best / scale) * 1000) / 1000
  if (!Number.isFinite(value) || value <= 0) return null
  return { kind: 'override', objectId: panel.id, patches: [{ gid: ref.gid, prop, value }] }
}

function planPageWidth(profile: PublicationProfile, choice?: FixChoice): FixPlan | null {
  if (choice !== 'single' && choice !== 'double') return null
  const w = num(profile.widths_mm[choice])
  return w == null || w <= 0 ? null : { kind: 'pageWidth', widthMm: w }
}
