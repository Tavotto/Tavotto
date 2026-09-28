/**
 * 问题面板的**呈现层**：范围、聚合、逐项游标（审计 T09）。
 *
 * 这里只对 `store/validationStore` 已经算好的 `ValidationIssue[]` 做整理——
 * **不跑第二遍求值器**，一条阈值都不在这里（ADR 0030：「这份项目有什么问题」
 * 只有一条链）。三件事：
 *
 * 1. **范围**：「当前图」= 正在快速编辑 / 图内编辑的那张，没有时退化为选中的
 *    面板；都没有就只剩「整份排版」。范围是用户的会话选择（`uiStore.problemScope`），
 *    `null` = 没选过，那就跟着现场走：有正在编辑的图看它，否则看整份排版。
 * 2. **聚合**：同一条规则的多次命中合成一组（组头 = 规则标题 + 受影响对象数），
 *    组内一行一个真实对象。25 行「字号低于绝对下限」重复 25 遍标题，用户扫到
 *    第三行就不看了；组头说一遍，行里只说「谁、现在多少、要多少」。
 * 3. **游标**：「正在处理第几条」与「下一项」。修好一条它会从清单里消失，
 *    这时「下一项」要指向**顶上来的那一条**（同组同位置），而不是跳回开头
 *    ——连续处理五条同类问题不该五次重新找位置。
 *
 * 4. **卡片**（2026-09-28 问题面板卡片化）：先分类、再批量。「按图」一张组图拆成
 *    子图卡片（`subject.part`，判据在 `lib/subplotParts.ts`），「按类别」一类一张
 *    （规则目录的 `category`）；点进一张卡片才是上面第 2 条的逐组清单。
 *
 * 全部是纯函数：不读 store、不碰 DOM，`problemList.test.ts` 直接量。
 */
import { SEVERITIES, type Severity } from './profile'
import { ruleEntry, type ProblemCategory, type ValidationIssue } from './validation'
import type { SubplotPart } from './subplotParts'

/* --------------------------------- 范围 ----------------------------------- */

export type ProblemScope = 'figure' | 'document'

/**
 * 用户的选择 + 现场 → 实际生效的范围。
 *
 * 没有当前图时「当前图」这一档根本不存在，选过也回整份排版；有当前图而用户
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

/* --------------------------------- 卡片 ----------------------------------- */

/** 卡片怎么分：按图（组图 → 子图）/ 按类别。UI 会话状态（`uiStore.problemView`） */
export type ProblemView = 'figure' | 'category'

/**
 * 点进了哪一张卡片（`uiStore.problemDrill`）。键全是呈现层的分组键——
 * 对象 id、axes gid **绝不进文案**，界面上说的是图名、「(a)」「子图 1」。
 */
export type ProblemDrill =
  | { kind: 'category'; key: ProblemCategory }
  /** 一整张图（面板对象 id），或页面级问题（`page:<canvasId>`） */
  | { kind: 'figure'; key: string }
  /** 一张组图里的一个子图簇；`key` 是 `SubplotPart.key`，图里的整图级问题是 {@link WHOLE} */
  | { kind: 'part'; figure: string; key: string }
  /** 「无法自动检查」那一段 */
  | { kind: 'unverifiable' }

/** 子图分组里「不属于任何一个子图」的那份（渲染失败、位图分辨率、suptitle…） */
export const WHOLE = ''

interface Bucket {
  issues: ValidationIssue[]
  /** 组内最高等级 */
  severity: Severity
}

export interface PartBucket extends Bucket {
  key: string
  /** null = 整张图那一份 */
  part: SubplotPart | null
}

export interface FigureBucket extends Bucket {
  key: string
  /** 面板对象 id；页面级问题为 null */
  objectId: string | null
  canvasId: string
  /** 子图簇；图里拆不出子图时只有一份整张图（{@link WHOLE}） */
  parts: PartBucket[]
}

export interface CategoryBucket extends Bucket {
  key: ProblemCategory
}

/** 「无法核验」不是要处理的问题：卡片只装需要处理的，它们另起一段（审计 B06 同一个理由） */
export const isUnverifiable = (i: ValidationIssue) => i.severity === 'not_verifiable'

export const figureKeyOf = (i: ValidationIssue): string =>
  i.objectRef.objectId ?? `page:${i.objectRef.canvasId}`

export const partKeyOf = (i: ValidationIssue): string => i.subject.part?.key ?? WHOLE

export const categoryOf = (i: ValidationIssue): ProblemCategory => ruleEntry(i.ruleCode).category

const worst = (issues: readonly ValidationIssue[]): Severity =>
  issues.reduce<Severity>((w, i) => (rank(i.severity) < rank(w) ? i.severity : w), SEVERITIES[SEVERITIES.length - 1])

/** 保持首次出现的顺序分桶（求值器的遍历序 = 画布 → 对象，图上相邻的在清单里也相邻） */
function bucket<K>(issues: readonly ValidationIssue[], keyOf: (i: ValidationIssue) => K): Map<K, ValidationIssue[]> {
  const out = new Map<K, ValidationIssue[]>()
  for (const i of issues) {
    const k = keyOf(i)
    const list = out.get(k)
    if (list) list.push(i)
    else out.set(k, [i])
  }
  return out
}

/**
 * 按图：一张图一桶（文档顺序），桶里按子图簇再分——整图级那份在前（它往往是
 * 「图还没重建」这种一改全变的事），子图按 `part.order`。
 */
export function bucketsByFigure(issues: readonly ValidationIssue[]): FigureBucket[] {
  const actionable = issues.filter((i) => !isUnverifiable(i))
  return [...bucket(actionable, figureKeyOf).entries()].map(([key, list]) => {
    const parts = [...bucket(list, partKeyOf).entries()]
      .map(([pk, pl]): PartBucket => ({
        key: pk,
        part: pl[0].subject.part ?? null,
        issues: pl,
        severity: worst(pl),
      }))
      .sort((a, b) => (a.part?.order ?? -1) - (b.part?.order ?? -1))
    return {
      key,
      objectId: list[0].objectRef.objectId,
      canvasId: list[0].objectRef.canvasId,
      parts,
      issues: list,
      severity: worst(list),
    }
  })
}

const CATEGORY_ORDER: ProblemCategory[] = ['text', 'lines', 'layout', 'color', 'file', 'other']

/** 按类别：阻断在前；同级项数多的在前；再同就按固定次序（排序稳定，卡片不跳） */
export function bucketsByCategory(issues: readonly ValidationIssue[]): CategoryBucket[] {
  const actionable = issues.filter((i) => !isUnverifiable(i))
  return [...bucket(actionable, categoryOf).entries()]
    .map(([key, list]) => ({ key, issues: list, severity: worst(list) }))
    .sort(
      (a, b) =>
        rank(a.severity) - rank(b.severity) ||
        b.issues.length - a.issues.length ||
        CATEGORY_ORDER.indexOf(a.key) - CATEGORY_ORDER.indexOf(b.key),
    )
}

/** 一张卡片里装的那些问题（点进去之后的清单、卡片上「修复 N」的集合，同一份） */
export function drillIssues(issues: readonly ValidationIssue[], drill: ProblemDrill): ValidationIssue[] {
  switch (drill.kind) {
    case 'unverifiable':
      return issues.filter(isUnverifiable)
    case 'category':
      return issues.filter((i) => !isUnverifiable(i) && categoryOf(i) === drill.key)
    case 'figure':
      return issues.filter((i) => !isUnverifiable(i) && figureKeyOf(i) === drill.key)
    case 'part':
      return issues.filter(
        (i) => !isUnverifiable(i) && figureKeyOf(i) === drill.figure && partKeyOf(i) === drill.key,
      )
  }
}

/**
 * 这条问题在这种看法下落在哪张卡片里。定位 / 游标走到一条卡片外的问题时，
 * 面板跟着换进它那张卡片——「下一项」不能把用户带到一行看不见的地方。
 * 图拆不出子图时卡片就是整张图（与 `bucketsByFigure` 的呈现同一个判据）。
 */
export function drillOf(
  issue: ValidationIssue,
  view: ProblemView,
  figures: readonly FigureBucket[],
): ProblemDrill {
  if (isUnverifiable(issue)) return { kind: 'unverifiable' }
  if (view === 'category') return { kind: 'category', key: categoryOf(issue) }
  const figure = figureKeyOf(issue)
  const bucket = figures.find((f) => f.key === figure)
  return bucket && isSplit(bucket)
    ? { kind: 'part', figure, key: partKeyOf(issue) }
    : { kind: 'figure', key: figure }
}

/**
 * 这张图按子图拆开显示吗：只要有一条问题认得出落在哪个子图里就拆（哪怕只有
 * (c) 有问题——一张「子图 (c)」卡比一张笼统的整图卡说得多）。卡片的呈现与
 * {@link drillOf} 共用这一个判据。
 */
export const isSplit = (f: Pick<FigureBucket, 'parts'>): boolean => f.parts.some((p) => p.part)

export function sameDrill(a: ProblemDrill | null, b: ProblemDrill | null): boolean {
  if (!a || !b) return a === b
  if (a.kind !== b.kind) return false
  if (a.kind === 'unverifiable') return true
  if (a.kind === 'part') return b.kind === 'part' && a.figure === b.figure && a.key === b.key
  return 'key' in b && a.key === b.key
}

/**
 * 卡片的稳定机器标识（`data-problem-card-key`）：新手教程与 e2e 要指向「这条问题
 * 所在的那张卡片」，靠它而不是文案。
 */
export function drillKey(drill: ProblemDrill): string {
  switch (drill.kind) {
    case 'unverifiable':
      return 'unverifiable'
    case 'part':
      return `part:${drill.figure}:${drill.key}`
    default:
      return `${drill.kind}:${drill.key}`
  }
}

/**
 * 一条问题可能落在哪几张卡片上（两种切法、拆没拆子图都算上）。此刻页面上只会有
 * 其中一张——调用方把它们拼成一个选择器列表就能找到它，不必知道现在是哪种切法。
 */
export function drillKeysOf(issue: ValidationIssue): string[] {
  if (isUnverifiable(issue)) return [drillKey({ kind: 'unverifiable' })]
  const figure = figureKeyOf(issue)
  return [
    drillKey({ kind: 'part', figure, key: partKeyOf(issue) }),
    drillKey({ kind: 'figure', key: figure }),
    drillKey({ kind: 'category', key: categoryOf(issue) }),
  ]
}
