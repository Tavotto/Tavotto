import { useMemo } from 'react'
import { resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
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
}

export function pagePresets([single, double]: PageColumn[]): PagePreset[] {
  return [
    { id: 'single', w: single.w, h: 60 },
    { id: 'double', w: double.w, h: 100 },
    { id: 'full', w: 180, h: 240 },
    { id: 'square', w: 100, h: 100 },
  ]
}

/**
 * 当前画布的页面预设清单（跟着绑定的规范走）。画布页与画布状态条共用这一份，状态条上的「双栏」
 * 与画布页下拉里选中的那一档是同一个判据。
 */
export function usePagePresets(): PagePreset[] {
  const binding = useDocumentStore((s) => s.doc.profile)
  const specs = useProfileStore((s) => s.specs)
  const specsLoaded = useProfileStore((s) => s.loaded)
  const columns = useMemo(
    () => resolvePageColumns(binding, toCatalog(specs), specsLoaded).columns,
    [binding, specs, specsLoaded],
  )
  return useMemo(() => pagePresets(columns), [columns])
}
