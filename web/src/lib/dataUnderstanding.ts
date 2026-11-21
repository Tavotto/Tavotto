import type { AiDataUnderstanding } from '@/lib/api'
import type { ColumnRoles } from '@/lib/dataFigureAdapter'

/**
 * 「读懂数据」（ADR 0178 第 1 步）：后端给的建议换算成前端能比对的形状。
 * 事实与校验都在后端；这里只做两件事——把响应翻成 `Suggestion`，再和此刻右栏的映射比出「哪几项不同」。
 * 纯函数，不碰 store。
 */

export type SuggestKey = 'x' | 'y' | 'group'
export const SUGGEST_KEYS: SuggestKey[] = ['x', 'y', 'group']

/** 建议：列名，或 `'none'`（不要横轴 / 不分组）；没给的键 = 没意见 */
export interface Suggestion {
  picks: Partial<Record<SuggestKey, string>>
  /** 建议的表头物理行号（后端只在「表头没认准」时才给）；没有 = null */
  headerRow: number | null
}

/** 此刻与建议不同的几项（已按右栏下拉真能选到的列过滤） */
export interface SuggestionDiff {
  picks: Partial<Record<SuggestKey, string>>
  headerRow: number | null
}

const asName = (v: unknown): string | null => {
  const one = Array.isArray(v) ? v[0] : v
  return typeof one === 'string' && one !== '' ? one : null
}

/** `ready` 的响应 → 建议；什么可用的都没有 = null（静默） */
export function suggestionFrom(u: AiDataUnderstanding, tablePath: string | null, withHeader: boolean): Suggestion | null {
  if (u.state !== 'ready') return null
  const picks: Suggestion['picks'] = {}
  const a = u.answers && typeof u.answers === 'object' ? u.answers : {}
  for (const k of SUGGEST_KEYS) {
    const v = asName(a[k])
    if (v !== null) picks[k] = v
  }
  const h = withHeader && tablePath !== null ? u.header_rows?.[tablePath] : undefined
  const headerRow = typeof h === 'number' && Number.isInteger(h) && h >= 1 ? h : null
  if (Object.keys(picks).length === 0 && headerRow === null) return null
  return { picks, headerRow }
}

const NONE = 'none'

/**
 * 建议与此刻的映射比：只留下不同的项。`roles` = 右栏当前的列角色（下拉的值与可选列）；`headerUsed` = 这次实际用的表头行；
 * `headerFixed` = 用户已经自己指过表头行（不覆盖）。这张表的下拉里从没出现过的列不算建议——胶囊点了也不能让右栏显示出来。
 * 没有差别 = null（什么都不显示）。
 */
export function diffSuggestion(s: Suggestion, roles: ColumnRoles | null, headerUsed: number | null, headerFixed: boolean): SuggestionDiff | null {
  const picks: SuggestionDiff['picks'] = {}
  const headerRow = !headerFixed && s.headerRow !== null && s.headerRow !== headerUsed ? s.headerRow : null
  if (headerRow !== null) {
    // 换表头行 = 列要重读，建议的列名是按重读后的表给的：此刻右栏的列不是比对依据，原样带上
    for (const k of SUGGEST_KEYS) if (s.picks[k] !== undefined) picks[k] = s.picks[k]
  } else if (roles) {
    const cur: Record<SuggestKey, string | null> = { x: roles.x, y: roles.y, group: roles.group }
    // 某个角色的下拉不列「此刻被别的角色占着的列」（后端从横轴候选里去掉当前纵轴，反过来也一样）。建议常常是成对换的
    // （纵轴换成 a、原来的纵轴 c 去当横轴）：按「这张表任何一个下拉里出现过、或此刻正被用着的列」核，成对的另一半才不会被丢掉；
    // 列真不合适由后端在下一次出候选时照常校验。
    const known = new Set<string>([...roles.options.x, ...roles.options.y, ...roles.options.group])
    for (const v of [roles.x, roles.y, roles.group]) if (v !== null) known.add(v)
    for (const k of SUGGEST_KEYS) {
      const want = s.picks[k]
      if (want === undefined) continue
      if (want === NONE) {
        // y 必须有列；x / group 的「不要」只有下拉里有那一项（有可选列）才算
        if (k !== 'y' && cur[k] !== null && roles.options[k].length > 0) picks[k] = NONE
        continue
      }
      // 纵轴此刻不止一列（宽表默认把 2–3 列都当纵轴，`yExtra` 记着另外几列）而建议只要一列：即使第一列相同也是不同
      const differs = want !== cur[k] || (k === 'y' && roles.yExtra > 0)
      if (differs && (roles.options[k].includes(want) || known.has(want))) picks[k] = want
    }
  }
  if (Object.keys(picks).length === 0 && headerRow === null) return null
  return { picks, headerRow }
}
