import { DEFAULT_PROFILE_ID, loadProfile, type PublicationProfile } from '@/lib/profile'
import { PAGE_MM_MAX, PAGE_MM_MIN } from '@/lib/pageRange'

/**
 * 当前画布绑定的出版规范里的**栏宽**（`widths_mm.single / double`）：页面预设（属性栏「画布」页）与
 * 拖页面边时的宽度吸附读的是同一份——规范是唯一权威，预检 `page-width` 也读它。
 *
 * 绑定的规范缺 `widths_mm` / 值不在页面能取的范围（`PAGE_MM_MIN`–`PAGE_MM_MAX`，`setPageSize` 按它夹取——
 * 摆一个夹完就对不上的预设，选了之后页面立刻变成「自定义」）时退回**内置默认规范**的那一档
 * （仍是 `publication.json`，不在这里写第二个数）。
 */
const FALLBACK_WIDTHS = loadProfile(DEFAULT_PROFILE_ID).widths_mm

export type PageColumnId = 'single' | 'double'

export interface PageColumn {
  id: PageColumnId
  w: number
}

const positive = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) && v >= PAGE_MM_MIN && v <= PAGE_MM_MAX ? v : fallback

export function pageColumns(
  widths: Partial<PublicationProfile['widths_mm']> | { single?: unknown; double?: unknown } | undefined,
): PageColumn[] {
  return [
    { id: 'single', w: positive(widths?.single, FALLBACK_WIDTHS.single) },
    { id: 'double', w: positive(widths?.double, FALLBACK_WIDTHS.double) },
  ]
}
