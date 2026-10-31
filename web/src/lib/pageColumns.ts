import { DEFAULT_PROFILE_ID, loadProfile, type PublicationProfile } from '@/lib/profile'
import { PAGE_MM_MAX, PAGE_MM_MIN } from '@/lib/pageRange'
import { resolveDocumentSpec, type SpecBinding, type SpecCatalogEntry } from '@/lib/specBinding'

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

/**
 * 当前画布绑定的规范 → 栏宽（属性栏「画布」页预设与拖页面边的吸附**共用这一个判据**）。
 *
 * `ready` = 这条绑定要的那份规范此刻读得到：跟随全局清单的绑定（`follow` / 只有 id、没有快照）要先有
 * 清单（`profileStore.loaded`），没加载完时清单里只有内置规范，`resolveDocumentSpec` 会退到默认规范——
 * 拿那个栏宽去吸附就可能提交一个不属于当前规范的宽度。没就绪时调用方不吸附（拖得动，只是不吸）。
 * 带快照且不跟随的绑定自给自足，清单没到也就绪。
 */
export function resolvePageColumns(
  binding: SpecBinding | null | undefined,
  catalog: SpecCatalogEntry[],
  catalogLoaded: boolean,
): { columns: PageColumn[]; specLabel: string; ready: boolean } {
  const spec = resolveDocumentSpec(binding, catalog).profile
  const selfSufficient = !!binding?.snapshot && binding.follow !== true
  const ready = catalogLoaded || !binding?.id || selfSufficient
  return { columns: pageColumns(spec.widths_mm), specLabel: spec.label, ready }
}
