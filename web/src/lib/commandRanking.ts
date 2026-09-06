/**
 * 命令面板的排序判据（审计 T50）：**纯函数，不碰 store**。
 *
 * 空查询时的顺序是「此刻最可能要做的事」：
 *
 * 1. `selection`——当前选区能做的（成组 / 布局 / 进图内编辑…）；没选中就没有这一段；
 * 2. `recent`——最近用过的，按最近一次在前；
 * 3. `common`——常见编辑动作，按 `COMMON_ORDER` 固定顺序；
 * 4. `other`——其余按注册顺序（刷新项目、接入状态、教程这些低频项自然沉到底）。
 *
 * 每条命令只出现一次：先到先得。有查询时四段照旧、只是不显示段标题——
 * 「输入 `导出` 时第一条是导出」靠的仍是这个顺序，不另写一份排序。
 */

export interface RankableCommand {
  id: string
  needsSelection?: boolean
}

export type PaletteSection = 'selection' | 'recent' | 'common' | 'other'

/** 常见编辑动作的固定顺序；不在表里的一律算 `other` */
export const COMMON_ORDER: readonly string[] = [
  'add-text',
  'sub-labels',
  'select-all',
  'edit-elements',
  'export',
  'save-document',
  'fit',
  'new-doc',
]

/** 最近使用记多少条；再多就不是「最近」了 */
export const RECENT_COMMANDS_MAX = 6

export interface RankContext {
  hasSelection: boolean
  /** 最近一次在前 */
  recent: readonly string[]
}

export interface RankedSection<T> {
  section: PaletteSection
  items: T[]
}

export function rankCommands<T extends RankableCommand>(
  pool: readonly T[],
  ctx: RankContext,
): RankedSection<T>[] {
  const byId = new Map(pool.map((c) => [c.id, c] as const))
  const taken = new Set<string>()
  const take = (id: string): T | null => {
    const c = byId.get(id)
    if (!c || taken.has(id)) return null
    taken.add(id)
    return c
  }

  const selection = ctx.hasSelection
    ? pool.filter((c) => c.needsSelection).map((c) => take(c.id)).filter((c): c is T => !!c)
    : []
  const recent = ctx.recent.map(take).filter((c): c is T => !!c)
  const common = COMMON_ORDER.map(take).filter((c): c is T => !!c)
  const other = pool.map((c) => take(c.id)).filter((c): c is T => !!c)

  return (
    [
      { section: 'selection', items: selection },
      { section: 'recent', items: recent },
      { section: 'common', items: common },
      { section: 'other', items: other },
    ] as RankedSection<T>[]
  ).filter((s) => s.items.length > 0)
}

/** 把一条命令记进最近使用：去重、最近在前、封顶 */
export function pushRecent(recent: readonly string[], id: string): string[] {
  return [id, ...recent.filter((x) => x !== id)].slice(0, RECENT_COMMANDS_MAX)
}
