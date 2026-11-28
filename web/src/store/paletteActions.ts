/**
 * 推荐配色的动作（ADR 0210 §一、§四）：读这张画布的系列 → 对上文档里的色键表 → 一次 commit 写回。
 *
 * - 读：每张面板的**结构性** manifest（`structuralPanelManifest`）。这里只要「有哪些系列、叫什么、
 *   身份摘要是什么」，与几何无关；刚写完颜色、新一版还没渲染回来时照样列得出组，不会闪空。
 * - 写：`documentStore.commit` 一次——色键表、每张面板的颜色 override、样式登记的让渡都在同一条历史里，
 *   一次撤销全部回去（ADR 0210 §一）。override 走 `actions.writePaletteOverrides`（与其它写入同一个 upsert）。
 * - 手改过的颜色不覆盖（`planPalette` 跳过）；画布样式写的颜色（`styleOwned` 登记、值仍是样式写的那个）
 *   归配色接手，并从样式的登记里注销，之后样式再改不会把它改回去。
 *
 * 界面入口在 `components/left/CanvasPaletteSection.tsx`，默认关（`lib/paletteUi.ts`）。
 */
import { msg, type UiMessage } from '@/i18n'
import { aliasCovered } from '@/lib/aliasOverrides'
import { paletteById, type CatalogPalette } from '@/lib/palette/catalog'
import {
  collectSeries,
  emptyTable,
  mergeKeys,
  readColorKeys,
  reconcile,
  setNeutral,
  setScheme,
  type ColorKeyTable,
  type Reconciled,
} from '@/lib/palette/colorKeys'
import { planPalette } from '@/lib/palette/plan'
import { initialScheme } from '@/lib/palette/rank'
import { ownedLive, releaseOwned } from '@/lib/styleOwned'
import type { FigureDocument, PanelObject } from '@/types/document'
import { writePaletteOverrides } from './actions'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { requestRender } from './renderScheduler'
import { structuralPanelManifest, useRenderStore } from './renderStore'

const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')

const panelsOf = (doc: FigureDocument): PanelObject[] =>
  doc.objects.filter((o): o is PanelObject => o.type === 'panel')

/** 这张画布此刻的分组（没有表时按一张空表推：只读，不写文档） */
export function canvasPaletteState(
  doc: FigureDocument,
  renders: Parameters<typeof structuralPanelManifest>[0] = useRenderStore.getState(),
): Reconciled & { stored: boolean } {
  const stored = readColorKeys(doc.colorKeys)
  const series = collectSeries(panelsOf(doc), (p) => structuralPanelManifest(renders, p))
  const base = stored ?? emptyTable('')
  const rec = reconcile(base, series)
  // 还没建过表：按此刻的组挑首选方案（默认方案过得了就用它，ADR 0210 §八）
  const scheme = stored?.scheme || initialScheme(rec.groups.map((g) => g.slot))
  return { ...rec, table: setScheme(rec.table, scheme), stored: !!stored }
}

/** 应用结果：写了几条、有几条因为手改过而没动、有几组色数不够没上色 */
export interface PaletteApplyResult {
  written: number
  skipped: number
  uncolored: number
}

/**
 * 把 `next`（已经改好方案 / 合并 / 中性设置的表）连同颜色一起写进文档：一次 commit。
 * 拿不到方案（目录里没有这个 id，比如被许可开关排除了）就什么都不写。
 */
function commitTable(label: UiMessage, edit: (t: ColorKeyTable) => ColorKeyTable): PaletteApplyResult | null {
  finishActiveGesture()
  const doc = useDocumentStore.getState().doc
  const state = canvasPaletteState(doc)
  const table = edit(state.table)
  const palette: CatalogPalette | null = paletteById(table.scheme)
  if (!palette) return null
  // 编辑后的表重新对一遍组（合并 / 中性改了槽，组的颜色跟着变）
  const series = state.groups.flatMap((g) => g.series).concat(state.unlabeled)
  const rec = reconcile(table, series)
  const panels = panelsOf(doc)
  // 与写入时 `upsertOverrides` 清窄端用同一份 manifest：它会清的，这里先看有没有用户手改
  const renders = useRenderStore.getState()
  const plan = planPalette(
    rec.table,
    palette,
    rec.groups,
    panels,
    (p, gid, prop) => !!ownedLive(doc, p, gid, prop),
    (p, gid, prop) => aliasCovered(structuralPanelManifest(renders, p), gid, prop),
  )
  useDocumentStore.getState().commit(label, (d) => {
    for (const { panel, patches } of plan.panels) {
      const o = d.objects.find((x) => x.id === panel.id)
      if (o?.type !== 'panel') continue
      writePaletteOverrides(o, patches)
    }
    // 样式登记过的那几格归配色了：从样式的登记里注销，样式以后再改不会把它改回去。
    // 按 `plan.owned`（配色接手的**每一格**）注销，不按 `patches`：颜色恰好已经是这套方案那一格的，
    // 计划里不写 patch，但它同样归配色了——留着样式的登记，脚本原色一变，样式让位时会把配色选的颜色删掉
    const ownedByPanel = new Map<string, { gid: string; prop: string }[]>()
    for (const k of plan.owned) {
      const list = ownedByPanel.get(k.panel) ?? []
      list.push({ gid: k.gid, prop: k.prop })
      ownedByPanel.set(k.panel, list)
    }
    for (const [id, keys] of ownedByPanel) {
      const o = d.objects.find((x) => x.id === id)
      if (o?.type === 'panel') releaseOwned(d, o, keys)
    }
    d.colorKeys = { ...rec.table, owned: plan.owned }
  })
  for (const { panel } of plan.panels) {
    const next = useDocumentStore.getState().doc.objects.find((x) => x.id === panel.id)
    if (next?.type === 'panel') requestRender(next, true)
  }
  return {
    written: plan.panels.reduce((n, p) => n + p.patches.length, 0),
    skipped: plan.skipped.length,
    uncolored: plan.uncolored.length,
  }
}

/**
 * 选用一套方案（换方案只换「槽 → 颜色」，组与槽不动）。`merges` = 同一次里顺手并掉的组
 * （第一次建表时确认对话框里点了「并成一组」），与上色在同一条历史里。
 */
export function applyPaletteScheme(
  schemeId: string,
  merges: readonly { into: string; from: string }[] = [],
): PaletteApplyResult | null {
  const p = paletteById(schemeId)
  if (!p) return null
  return commitTable(hist('applyPalette', { name: p.name }), (t) =>
    setScheme(merges.reduce((acc, m) => mergeKeys(acc, m.into, m.from), t), schemeId),
  )
}

/** 把一组设成 / 取消中性灰（用户确认之后才调，ADR 0210 §四：只建议，不自动生效） */
export function setPaletteGroupNeutral(key: string, on: boolean): PaletteApplyResult | null {
  return commitTable(hist(on ? 'paletteNeutralOn' : 'paletteNeutralOff'), (t) => setNeutral(t, key, on))
}

/** 把同名不同身份的两组并成一组（中等证据，用户点「并入」才合） */
export function mergePaletteGroups(into: string, from: string): PaletteApplyResult | null {
  return commitTable(hist('paletteMerge'), (t) => mergeKeys(t, into, from))
}
