import { useMemo } from 'react'
import { columnName, resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import { useDocumentStore } from '@/store/documentStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'

/**
 * 页面尺寸预设。文案按 id 查 `inspector:canvas.presets.<id>`，这里只留尺寸。
 *
 * 「单栏 / 双栏」的**宽度不是这里的常量**：出版规范（`profiles/publication.json` 的
 * `widths_mm`）是唯一权威，预检 `page-width` 也读它，所以预设宽度由 `pagePresets()` 经 `lib/pageColumns`（拖页面边的栏宽吸附读同一份）从当前画布
 * 绑定的规范取（与 `validationStore.collectCanvases` 同一条 `resolveDocumentSpec` 路径）。
 * 绑定的规范缺 `widths_mm` / 值不在页面能取的范围（`PAGE_MM_MIN`–`PAGE_MM_MAX`，`setPageSize` 按它夹取——
 * 摆一个夹完就对不上的预设，选了之后页面立刻变成「自定义」）时退回**内置默认规范**的那一档（仍是 `publication.json`，
 * 不在这里写第二个数）；高度只是常见起点，不是规范字段。「整页」「正方形」不在规范里，保持常量。
 */
export interface PagePreset {
  id: string
  w: number
  h: number
  /** 期刊自有栏位的显示名（单栏 / 双栏 / 方形等走词表，不带它） */
  label?: string
  /** 来自规范栏位（相对「整页」「正方形」这两档常量）：按期刊选的项目里，栏只按宽度对 */
  column?: true
}

/**
 * 栏位预设：第一档矮一点（60），其余 100——高度只是常见起点，不是规范字段。
 * `journalH` 给了（项目按期刊选的、且该期刊写了栏位）：选栏只改页宽、高度保持它，栏名一律用 `columnName`（期刊自己的栏名）。
 */
export function pagePresets(columns: PageColumn[], journalH: number | null = null): PagePreset[] {
  return [
    ...columns.map((c, i) => ({
      id: c.id,
      w: c.w,
      h: journalH ?? (i === 0 ? 60 : 100),
      column: true as const,
      ...(journalH == null && (c.id === 'single' || c.id === 'double') ? {} : { label: columnName(c) }),
    })),
    { id: 'full', w: 180, h: 240 },
    { id: 'square', w: 100, h: 100 },
  ]
}

/**
 * 当前页面尺寸对上的那一档预设（画布页下拉的选中项、页面摘要里的名字共用这一个判据）。
 * 按期刊选的项目：期刊的栏只看宽度（高度不是期刊规定的）；其余预设宽高都对上才算。其它项目：宽高都对上。
 */
export function activePreset(
  presets: PagePreset[],
  page: { w: number; h: number },
  journalCols: boolean,
): PagePreset | undefined {
  if (journalCols) {
    const col = presets.find((p) => p.column && Math.abs(p.w - page.w) < 0.05)
    if (col) return col
    return presets.find((p) => !p.column && p.w === page.w && p.h === page.h)
  }
  return presets.find((p) => p.w === page.w && p.h === page.h)
}

/**
 * 当前画布的页面预设清单（跟着绑定的规范走）。画布页与画布状态条共用这一份，状态条上的「双栏」
 * 与画布页下拉里选中的那一档是同一个判据。
 */
export function usePagePresets(): PagePreset[] {
  return usePagePresetState().presets
}

/** 预设清单 + 当前对上的那一档（`activePreset`）。画布页与摘要共用。 */
export function usePagePresetState(): { presets: PagePreset[]; active: PagePreset | undefined } {
  const binding = useDocumentStore((s) => s.doc.profile)
  const page = useDocumentStore((s) => s.doc.page)
  const specs = useProfileStore((s) => s.specs)
  const specsLoaded = useProfileStore((s) => s.loaded)
  const resolved = useMemo(
    () => resolvePageColumns(binding, toCatalog(specs), specsLoaded),
    [binding, specs, specsLoaded],
  )
  const { columns, journal } = resolved
  const presets = useMemo(() => pagePresets(columns, journal ? page.h : null), [columns, journal, page.h])
  const active = activePreset(presets, page, journal)
  return { presets, active }
}
