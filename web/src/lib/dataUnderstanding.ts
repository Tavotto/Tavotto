import type { AiDataUnderstanding } from '@/lib/api'
import type { ColumnRoles } from '@/lib/dataFigureAdapter'

/**
 * 「读懂数据」（ADR 0178 第 1 步）：后端给的建议换算成前端能比对的形状。
 * 事实与校验都在后端；这里只做两件事——把响应翻成 `Suggestion`，再和此刻右栏的映射比出「哪几项不同」。
 * 纯函数，不碰 store。
 */

export type SuggestKey = 'x' | 'y' | 'group'
export const SUGGEST_KEYS: SuggestKey[] = ['x', 'y', 'group']

/** 一项建议的取值：列名，或 `'none'`（不要横轴 / 不分组）；`y` 还可以是 2 个以上列名的数组（原样带回，顺序有意义） */
export type PickValue = string | string[]

/** 后端一次 propose 最多认的纵轴列数（`max(MULTI_Y_MAX, STACK_MAX_COLUMNS)`）；超过会被当成请求形状错误，所以前端不带 */
const Y_MAX = 8

/**
 * 建议 = 一份「不透明的 answers 补丁」里前端认识并允许套用的键：`x` / `y` / `group` / `x_measure`（`cells` 仍不套用，其余未知键忽略）。
 * 没给的键 = 没意见。
 */
export interface Suggestion {
  picks: Partial<Record<SuggestKey, PickValue>>
  /** `answers.x_measure`：规则选作横轴的那一列其实是测量值；不是下拉的改动，没有 = 不出现这个键 */
  xMeasure?: string
  /** 建议的表头物理行号（后端只在「表头没认准」时才给）；没有 = null */
  headerRow: number | null
}

/** 此刻与建议不同的几项（已按右栏下拉真能选到的列过滤） */
export interface SuggestionDiff {
  picks: Partial<Record<SuggestKey, PickValue>>
  /** 当前请求的 `answers` 里还没有这个值的 `x_measure` 才有 */
  xMeasure?: string
  headerRow: number | null
}

const NONE = 'none'

const asName = (v: unknown): string | null => {
  const one = Array.isArray(v) ? v[0] : v
  return typeof one === 'string' && one !== '' ? one : null
}

/** `y` 的取值：列名；数组 = 每一项都是非空、不是 `none`、不重复的字符串且不超过 `Y_MAX` 列（一列的数组当成那一列）；形状不对 = null（整项丢，不拿第一列凑数） */
function asY(v: unknown): PickValue | null {
  if (!Array.isArray(v)) return asName(v)
  if (v.length === 0 || v.length > Y_MAX) return null
  if (!v.every((c) => typeof c === 'string' && c !== '' && c !== NONE)) return null
  if (new Set(v).size !== v.length) return null
  return v.length === 1 ? (v[0] as string) : (v as string[])
}

const sameList = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((c, i) => c === b[i])

/** `ready` 的响应 → 建议；什么可用的都没有 = null（静默） */
export function suggestionFrom(u: AiDataUnderstanding, tablePath: string | null, withHeader: boolean): Suggestion | null {
  if (u.state !== 'ready') return null
  const picks: Suggestion['picks'] = {}
  const a = u.answers && typeof u.answers === 'object' ? u.answers : {}
  for (const k of SUGGEST_KEYS) {
    const v = k === 'y' ? asY(a[k]) : asName(a[k])
    if (v !== null) picks[k] = v
  }
  const xMeasure = typeof a.x_measure === 'string' && a.x_measure !== '' ? a.x_measure : undefined
  const h = withHeader && tablePath !== null ? u.header_rows?.[tablePath] : undefined
  const headerRow = typeof h === 'number' && Number.isInteger(h) && h >= 1 ? h : null
  if (Object.keys(picks).length === 0 && headerRow === null && xMeasure === undefined) return null
  return { picks, ...(xMeasure !== undefined ? { xMeasure } : {}), headerRow }
}

/**
 * 建议与此刻的映射比：只留下不同的项。`roles` = 右栏当前的列角色（下拉的值与可选列）；`headerUsed` = 这次实际用的表头行；
 * `headerFixed` = 用户已经自己指过表头行（不覆盖）。这张表的下拉里从没出现过的列不算建议——胶囊点了也不能让右栏显示出来。
 * 没有差别 = null（什么都不显示）。
 */
export function diffSuggestion(
  s: Suggestion,
  roles: ColumnRoles | null,
  headerUsed: number | null,
  headerFixed: boolean,
  /** 当前请求的 `answers`：`x_measure` 不对应任何下拉，只能拿它比；数组 `y` 已经原样发过也算「已套用」（后端不认就不认，不反复重发） */
  answers: Record<string, unknown> = {},
): SuggestionDiff | null {
  const picks: SuggestionDiff['picks'] = {}
  let xMeasure: string | undefined
  const headerRow = !headerFixed && s.headerRow !== null && s.headerRow !== headerUsed ? s.headerRow : null
  if (headerRow !== null) {
    // 换表头行 = 列要重读，建议的列名是按重读后的表给的：此刻右栏的列不是比对依据，原样带上
    for (const k of SUGGEST_KEYS) if (s.picks[k] !== undefined) picks[k] = s.picks[k]
    if (s.xMeasure !== undefined && answers.x_measure !== s.xMeasure) xMeasure = s.xMeasure
  } else if (roles) {
    const cur: Record<SuggestKey, string | null> = { x: roles.x, y: roles.y, group: roles.group }
    // 某个角色的下拉不列「此刻被别的角色占着的列」（后端从横轴候选里去掉当前纵轴，反过来也一样）。建议常常是成对换的
    // （纵轴换成 a、原来的纵轴 c 去当横轴）：按「这张表任何一个下拉里出现过、或此刻正被用着的列」核，成对的另一半才不会被丢掉；
    // 列真不合适由后端在下一次出候选时照常校验。
    const known = new Set<string>([...roles.options.x, ...roles.options.y, ...roles.options.group])
    for (const v of [roles.x, roles.y, roles.group, ...roles.yAll]) if (v !== null) known.add(v)
    const selectable = (c: string, k: SuggestKey): boolean => roles.options[k].includes(c) || known.has(c)
    for (const k of SUGGEST_KEYS) {
      const want = s.picks[k]
      if (want === undefined) continue
      if (Array.isArray(want)) {
        // 多列纵轴：每一列都要是这张表里能选到的列，否则整条不算建议。顺序有意义（有横轴时是线的顺序，把列当分组时就是类别顺序），
        // 所以集合相同顺序不同也算不同；此刻的纵轴就是这几列（同序）= 相同；同一份数组已经发过了（后端没采纳）也不再当成不同
        if (k !== 'y' || !want.every((c) => selectable(c, 'y'))) continue
        const sent = answers.y
        if (!sameList(want, roles.yAll) && !(Array.isArray(sent) && sameList(want, sent as string[]))) picks.y = want
        continue
      }
      if (want === NONE) {
        // y 必须有列；x / group 的「不要」只有下拉里有那一项（有可选列）才算
        if (k !== 'y' && cur[k] !== null && roles.options[k].length > 0) picks[k] = NONE
        continue
      }
      // 纵轴此刻不止一列（宽表默认把 2–3 列都当纵轴，`yExtra` 记着另外几列）而建议只要一列：即使第一列相同也是不同
      const differs = want !== cur[k] || (k === 'y' && roles.yExtra > 0)
      if (differs && selectable(want, k)) picks[k] = want
    }
    // x_measure：这一列得是这张表里的列；当前请求里没有同值的才算不同
    if (s.xMeasure !== undefined && answers.x_measure !== s.xMeasure && known.has(s.xMeasure)) xMeasure = s.xMeasure
  }
  if (Object.keys(picks).length === 0 && headerRow === null && xMeasure === undefined) return null
  return { picks, ...(xMeasure !== undefined ? { xMeasure } : {}), headerRow }
}
