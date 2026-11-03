import { currentLocale, t as translate } from '@/i18n'
import {
  DEFAULT_PROFILE_ID,
  derivePageColumns,
  loadProfile,
  type PublicationProfile,
} from '@/lib/profile'
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
// 默认规范（课题组）的栏宽必有；期刊底的 null 栏宽只出现在期刊绑定里，那时落到这里兜底。
const FALLBACK_WIDTHS = loadProfile(DEFAULT_PROFILE_ID).widths_mm as { single: number; double: number }

export interface PageColumn {
  /** `single` / `double`，或期刊自有栏位的 id（如 Science 的 `col2` / `col3`） */
  id: string
  w: number
  label: { zh: string; en: string }
}

const positive = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) && v >= PAGE_MM_MIN && v <= PAGE_MM_MAX ? v : fallback

const LEGACY_LABELS = {
  single: { zh: '单栏', en: 'Single column' },
  double: { zh: '双栏', en: 'Double column' },
} as const

/** 旧形状 `{single, double}` → 两档（缺 / 越界的那档退回默认规范）。拖边吸附的单测与老调用方用它。 */
export function pageColumns(
  widths: Partial<PublicationProfile['widths_mm']> | { single?: unknown; double?: unknown } | undefined,
): PageColumn[] {
  return [
    { id: 'single', w: positive(widths?.single, FALLBACK_WIDTHS.single), label: LEGACY_LABELS.single },
    { id: 'double', w: positive(widths?.double, FALLBACK_WIDTHS.double), label: LEGACY_LABELS.double },
  ]
}

/**
 * 一份规范的栏位（有序）：读 `derivePageColumns`（期刊自有 `page_columns`，没写就由 `widths_mm` 派生
 * 单栏 / 双栏），**下游只读这一种形状**。宽度不在页面能取的范围的栏位丢掉；一档都不剩（期刊底没规定栏宽）
 * 时退回默认规范的单栏 / 双栏。
 */
export function pageColumnsOf(spec: Partial<PublicationProfile>): PageColumn[] {
  const cols = derivePageColumns(spec)
    .filter((c) => positive(c.width_mm, -1) > 0)
    .map((c) => ({ id: c.id, w: c.width_mm, label: c.label }))
  return cols.length ? cols : pageColumns(undefined)
}

/** 栏位的显示名：单栏 / 双栏走界面词表（`inspector:canvas.presets.*`），期刊自有栏位用它自己的 label。 */
export function columnName(c: PageColumn): string {
  if (c.id === 'single' || c.id === 'double') {
    return translate(`canvas.presets.${c.id}.label`, { ns: 'inspector' })
  }
  return currentLocale() === 'en-US' ? c.label.en : c.label.zh
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
): { columns: PageColumn[]; specLabel: string; ready: boolean; journal: boolean } {
  const spec = resolveDocumentSpec(binding, catalog).profile
  const selfSufficient = !!binding?.snapshot && binding.follow !== true
  const ready = catalogLoaded || !binding?.id || selfSufficient
  // 按期刊选的、且该期刊自己写了栏位：预设显示期刊自己的栏名（期刊没规定栏宽 = false，仍是课题组预设）
  const journal = !!binding?.journalRef && derivePageColumns(spec).some((c) => positive(c.width_mm, -1) > 0)
  return { columns: pageColumnsOf(spec), specLabel: spec.label, ready, journal }
}
