import type { PanelOverride } from '@/types/document'

/**
 * 「恢复」动作的对象与数量（审计 T32）。
 *
 * 属性页的两颗恢复按钮各自说清自己的对象——「恢复此元素 · 3 项」「恢复整张图 ·
 * 22 项」——数字必须来自同一组 overrides，而不是各处自己 filter 一遍：数量对不上
 * 的时候用户会以为按下去清掉的是另一批东西。这里是那一份判据；快速编辑的右键
 * 菜单、属性页、历史标签都从这儿取数。
 */
export interface OverrideCounts {
  /** 落在这个元素（gid）上的修改数；没有 gid 时为 0 */
  element: number
  /** 整张图的修改总数（含写回时继承的基线） */
  figure: number
}

export function overrideCounts(
  overrides: readonly PanelOverride[],
  gid: string | null | undefined,
): OverrideCounts {
  const figure = overrides.length
  const element = gid ? overrides.filter((o) => o.gid === gid).length : 0
  return { element, figure }
}
