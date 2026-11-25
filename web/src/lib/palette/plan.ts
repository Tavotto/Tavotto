/**
 * 把色键表落成 override（ADR 0210 §七）：与手动改色同一条链路，plan → overrides → `documentStore.commit`，
 * 一次撤销。这里只出计划，不写文档。
 *
 * 用户手改过的颜色不覆盖：此刻有 override、而它不是 `owned` 里配色上次写下的那个值，就跳过并计数。
 * 画布样式（ADR 0081 §十三）写下、仍归样式的那一格不算手改：调用方经 `yields` 告诉这里（`lib/styleOwned.ownedLive`），
 * 配色照写；写完那一格的值变了，样式的登记自然失效、从此归配色。
 */
import { effectiveOverride } from '@/lib/effectiveOverride'
import type { PanelObject, PanelOverride } from '@/types/document'
import type { CatalogPalette } from './catalog'
import type { ColorGroup, ColorKeyOwned, ColorKeyTable, SeriesRef } from './colorKeys'
import { colorForSlot } from './rank'

export interface PalettePanelPlan {
  panel: PanelObject
  patches: PanelOverride[]
}

export interface PalettePlan {
  panels: PalettePanelPlan[]
  /** 写完之后的 `colorKeys.owned` */
  owned: ColorKeyOwned[]
  /** 用户手改过、这次没动的系列 */
  skipped: SeriesRef[]
  /** 方案色数不够、拿不到颜色的组（不循环复用颜色） */
  uncolored: string[]
}

const same = (a: unknown, b: unknown): boolean =>
  typeof a === 'string' && typeof b === 'string' ? a.toLowerCase() === b.toLowerCase() : a === b

const ownedKey = (o: { panel: string; gid: string; prop: string }) => `${o.panel}\u0000${o.gid}\u0000${o.prop}`

export function planPalette(
  table: ColorKeyTable,
  palette: CatalogPalette,
  groups: readonly ColorGroup[],
  panels: readonly PanelObject[],
  /** 这一格的 override 不是用户的（画布样式写的、仍归样式），配色可以接手 */
  yields: (panel: PanelObject, gid: string, prop: string) => boolean = () => false,
): PalettePlan {
  const byId = new Map(panels.map((p) => [p.id, p]))
  const prevOwned = new Map(table.owned.map((o) => [ownedKey(o), o]))
  const patchesOf = new Map<string, PanelOverride[]>()
  const written = new Map<string, ColorKeyOwned>()
  const skipped: SeriesRef[] = []
  const uncolored: string[] = []

  for (const g of groups) {
    const color = colorForSlot(g.slot, palette)
    if (color === null) {
      uncolored.push(g.key)
      continue
    }
    for (const s of g.series) {
      const panel = byId.get(s.panelId)
      if (!panel) continue
      const k = ownedKey({ panel: s.panelId, gid: s.gid, prop: s.prop })
      const cur = effectiveOverride(panel.overrides, s.gid, s.prop)
      const mine = prevOwned.get(k)
      if (cur !== undefined && !(mine && same(cur.value, mine.value)) && !yields(panel, s.gid, s.prop)) {
        skipped.push(s)
        continue
      }
      written.set(k, { panel: s.panelId, gid: s.gid, prop: s.prop, value: color })
      if (cur !== undefined && same(cur.value, color)) continue
      const list = patchesOf.get(s.panelId) ?? []
      list.push({ gid: s.gid, prop: s.prop, value: color })
      patchesOf.set(s.panelId, list)
    }
  }

  // 上次写下、这次没碰、而 override 仍是那个值的：还是配色的，接着记着
  const owned: ColorKeyOwned[] = [...written.values()]
  for (const [k, o] of prevOwned) {
    if (written.has(k)) continue
    const panel = byId.get(o.panel)
    const cur = panel ? effectiveOverride(panel.overrides, o.gid, o.prop) : undefined
    if (cur !== undefined && same(cur.value, o.value)) owned.push(o)
  }

  return {
    panels: panels.flatMap((p) => {
      const patches = patchesOf.get(p.id)
      return patches?.length ? [{ panel: p, patches }] : []
    }),
    owned,
    skipped,
    uncolored,
  }
}
