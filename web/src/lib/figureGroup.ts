import type { PanelInfo, ReadinessReport, ReadinessSummary, ViewScope } from '@/lib/api'

/**
 * 同一张图的几份文件（ADR 0184）。归组只消费后端给的 `figure_key` / `primary`（`engine/figuregroup`），
 * 不在这里再切文件名——老后端不发这两个字段时，一份文件就是一张图（旧行为）。
 */

/** 这份文件在素材区是不是一张卡：主文件，或老后端（没有 `primary`）的每一份 */
export const isFigureCard = (p: Pick<PanelInfo, 'primary'>): boolean => p.primary !== false

/** 图键 → 这张图的全部文件（老后端：每份文件自成一组，键就是 id） */
export function figureMembers(panels: readonly PanelInfo[]): Map<string, PanelInfo[]> {
  const out = new Map<string, PanelInfo[]>()
  for (const p of panels) {
    const key = p.figure_key ?? `id:${p.id}`
    const list = out.get(key)
    if (list) list.push(p)
    else out.set(key, [p])
  }
  return out
}

/**
 * 有脚本的图里，这份**位图**的矢量主文件（ADR 0184 §二）：进图内编辑改从它走，不再单独做位图采用。
 * 只认后端标了 `script:` 图键、主文件不是位图的那一组；其余（没脚本、只有位图、找不到）一律 null。
 */
export function vectorPrimaryFor(fileId: string, panels: readonly PanelInfo[]): PanelInfo | null {
  const self = panels.find((p) => p.id === fileId)
  const key = self?.figure_key
  if (!key || !key.startsWith('script:')) return null
  const primary = panels.find((p) => p.figure_key === key && p.primary === true)
  if (!primary || primary.id === fileId || primary.kind === 'raster') return null
  return primary
}

/**
 * 这张图在当前范围里看不看得见（ADR 0184 §三）。`folder` = 都看得见；`scripts` = 产出它的脚本在范围里，
 * 或这张图（任一份文件）已经用在排版里——**用着的绝不藏**。`script` 是后端注册表认的脚本（项目相对路径）。
 */
export function inViewScope(script: string | null | undefined, scope: ViewScope, used: boolean): boolean {
  if (scope.mode !== 'scripts') return true
  return used || (!!script && scope.scripts.includes(script))
}

/** 素材区里看得见的图卡（主文件）：一张图一张、按范围过滤；`usedIds` = 排版里用到的文件 id */
export function visibleFigureCards(
  panels: readonly PanelInfo[],
  scope: ViewScope,
  usedIds: ReadonlySet<string> = new Set(),
): PanelInfo[] {
  return panels.filter(
    (p) =>
      isFigureCard(p) &&
      inViewScope(p.script, scope, (p.formats ?? [p.id]).some((id) => usedIds.has(id))),
  )
}

/**
 * 接入状态报告按「看得见的图」重新计数（顶栏胶囊用）：同一张图的几份文件算一张，范围外的不算。
 * `folder` 范围且老后端（没有归组字段）时原样返回，计数与今天逐字相同。
 */
export function scopeReadiness(
  report: ReadinessReport,
  panels: readonly PanelInfo[],
  scope: ViewScope,
): ReadinessReport {
  const grouped = panels.some((p) => p.figure_key !== undefined)
  if (scope.mode !== 'scripts' && !grouped) return report
  const shown = new Set(visibleFigureCards(panels, scope).map((p) => p.id))
  const rows = report.panels.filter((r) => shown.has(r.id))
  const summary = { ...report.summary } as ReadinessSummary
  for (const k of Object.keys(summary) as (keyof ReadinessSummary)[]) summary[k] = 0
  summary.total = rows.length
  for (const r of rows) summary[r.status] = (summary[r.status] ?? 0) + 1
  return { ...report, summary, panels: rows }
}
