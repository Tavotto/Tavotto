/**
 * 问题面板的**呈现层**：范围、聚合、逐项游标（审计 T09）。
 *
 * 这里只对 `store/validationStore` 已经算好的 `ValidationIssue[]` 做整理——
 * **不跑第二遍求值器**，一条阈值都不在这里（ADR 0030：「这份项目有什么问题」
 * 只有一条链）。三件事：
 *
 * 1. **范围**：「当前图」= 正在快速编辑 / 图内编辑的那张，没有时退化为选中的
 *    面板；都没有就只剩「整个文档」。范围是用户的会话选择（`uiStore.problemScope`），
 *    `null` = 没选过，那就跟着现场走：有正在编辑的图看它，否则看整个文档。
 * 2. **聚合**：同一条规则的多次命中合成一组（组头 = 规则标题 + 受影响对象数），
 *    组内一行一个真实对象。25 行「字号低于绝对下限」重复 25 遍标题，用户扫到
 *    第三行就不看了；组头说一遍，行里只说「谁、现在多少、要多少」。
 * 3. **游标**：「正在处理第几条」与「下一项」。修好一条它会从清单里消失，
 *    这时「下一项」要指向**顶上来的那一条**（同组同位置），而不是跳回开头
 *    ——连续处理五条同类问题不该五次重新找位置。
 *
 * 全部是纯函数：不读 store、不碰 DOM，`problemList.test.ts` 直接量。
 */
import { SEVERITIES, type Severity } from './profile'
import type { ValidationIssue } from './validation'

/* --------------------------------- 范围 ----------------------------------- */

export type ProblemScope = 'figure' | 'document'

/**
 * 用户的选择 + 现场 → 实际生效的范围。
 *
 * 没有当前图时「当前图」这一档根本不存在，选过也回整个文档；有当前图而用户
 * 没选过时默认看它——在快速编辑里打开问题面板，用户要的是**这张图**的问题。
 */
export function effectiveScope(choice: ProblemScope | null, figureId: string | null): ProblemScope {
  if (!figureId) return 'document'
  return choice ?? 'figure'
}

/**
 * 按范围裁清单。「当前图」= 主语是这个面板对象的那些（图内元素 + 面板本身）；
 * 页面级问题（页宽、比例）的主语是整张画布，不属于任何一张图。
 */
export function issuesInScope(
  issues: readonly ValidationIssue[],
  scope: ProblemScope,
  figureId: string | null,
): ValidationIssue[] {
  if (scope === 'document' || !figureId) return [...issues]
  return issues.filter((i) => i.objectRef.objectId === figureId)
}

/* --------------------------------- 聚合 ----------------------------------- */

export interface IssueGroup {
  ruleCode: string
  /** 组内最高等级（同一条规则的等级来自规范，组内通常一致） */
  severity: Severity
  /** 组内按等级、再按出现顺序 */
  issues: ValidationIssue[]
  /** 受影响对象数：不同的（对象, 元素）算不同的对象 */
  objects: number
}

const rank = (s: Severity) => SEVERITIES.indexOf(s)

/**
 * 同一条规则合成一组；组按最高等级排（阻断在前），同级按第一次出现的顺序。
 * 组内同样按等级再按原顺序——原顺序来自求值器，是画布 → 对象的遍历序，
 * 相邻的行在图上也相邻。
 */
export function groupIssues(issues: readonly ValidationIssue[]): IssueGroup[] {
  const byRule = new Map<string, IssueGroup>()
  issues.forEach((issue) => {
    let g = byRule.get(issue.ruleCode)
    if (!g) {
      g = { ruleCode: issue.ruleCode, severity: issue.severity, issues: [], objects: 0 }
      byRule.set(issue.ruleCode, g)
    }
    g.issues.push(issue)
    if (rank(issue.severity) < rank(g.severity)) g.severity = issue.severity
  })
  const groups = [...byRule.values()]
  for (const g of groups) {
    g.issues = g.issues
      .map((issue, i) => ({ issue, i }))
      .sort((a, b) => rank(a.issue.severity) - rank(b.issue.severity) || a.i - b.i)
      .map((x) => x.issue)
    g.objects = new Set(g.issues.map((i) => `${i.objectRef.objectId ?? ''}|${i.objectRef.gid ?? ''}`)).size
  }
  return groups
    .map((g, i) => ({ g, i }))
    .sort((a, b) => rank(a.g.severity) - rank(b.g.severity) || a.i - b.i)
    .map((x) => x.g)
}

/** 组展开后的顺序（游标在它上面走） */
export const flattenGroups = (groups: readonly IssueGroup[]): ValidationIssue[] =>
  groups.flatMap((g) => g.issues)

/* --------------------------------- 游标 ----------------------------------- */

/**
 * 「正在处理哪一条」。除了身份（`issueId`）还记下它当时的位置：修好之后它会
 * 从清单里消失，那时靠位置才知道「顶上来的是哪一条」。
 */
export interface ProblemCursor {
  issueId: string
  ruleCode: string
  /** 在同组里的下标 */
  index: number
  /** 在展开顺序里的下标 */
  flatIndex: number
}

/** 给一条问题造游标；它不在当前清单里就回 null。 */
export function cursorFor(groups: readonly IssueGroup[], issueId: string): ProblemCursor | null {
  let flat = 0
  for (const g of groups) {
    for (let i = 0; i < g.issues.length; i++, flat++) {
      if (g.issues[i].issueId === issueId) {
        return { issueId, ruleCode: g.ruleCode, index: i, flatIndex: flat }
      }
    }
  }
  return null
}

export interface CursorView {
  /** 游标指着的那条；已经不在清单里（修好了 / 消失了）就是 null */
  current: ValidationIssue | null
  /** 1-based 位置（current 为 null 时是 0） */
  position: number
  total: number
  next: ValidationIssue | null
  prev: ValidationIssue | null
}

/**
 * 把游标落到**当前**清单上。
 *
 * 那条还在：上一条 / 下一条就是展开顺序里的邻居。
 * 那条已经不在（多半是修好了）：「下一项」= 同组同位置顶上来的那条；同组没有
 * 这个位置了就取展开顺序里原位置上的那条；「上一项」同理往前找。**不跳回开头**。
 */
export function cursorView(groups: readonly IssueGroup[], cursor: ProblemCursor | null): CursorView {
  const flat = flattenGroups(groups)
  const empty: CursorView = { current: null, position: 0, total: flat.length, next: null, prev: null }
  if (!cursor || !flat.length) return empty
  const at = flat.findIndex((i) => i.issueId === cursor.issueId)
  if (at >= 0) {
    return {
      current: flat[at],
      position: at + 1,
      total: flat.length,
      next: flat[at + 1] ?? null,
      prev: at > 0 ? flat[at - 1] : null,
    }
  }
  const group = groups.find((g) => g.ruleCode === cursor.ruleCode)
  const replacement = group?.issues[cursor.index] ?? null
  const clamp = (n: number) => Math.min(Math.max(n, 0), flat.length - 1)
  const next = replacement ?? flat[clamp(cursor.flatIndex)]
  const before = group?.issues[cursor.index - 1] ?? (cursor.flatIndex > 0 ? flat[clamp(cursor.flatIndex - 1)] : null)
  return { ...empty, next, prev: before && before !== next ? before : null }
}
