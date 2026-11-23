/**
 * 「从数据到图」临时标签自己的规范绑定（ADR 0178 + 0155）：纯函数。
 *
 * 草稿起初跟着此刻激活画布的 `doc.profile`（宿主 `canvasBinding()` 取一份深拷贝），用户在右栏「期刊」一行改的只是**草稿自己的**那一份，
 * 不写被盖住的画布、不进撤销栈。绑定怎么组装只有这里（复用 `pickJournal` / `pickV2OnlyJournal` + `applyV2`，与文档里选期刊同一套判据），
 * 草稿没有页面，所以页宽联动一律不用（`pageW` 只用来喂 `journalPrev` 的退路，见 `prevOf`）。
 */
import type { JournalCatalog } from './journals'
import { applyV2, pickV2OnlyJournal, type V2IndexEntry, type V2Patch } from './journalV2'
import { pickJournal } from './journalPick'
import { journalStyleTargets } from './journalStyle'
import { sameRules, type JournalPrev } from './specBinding'
import type { DocumentProfile } from '@/types/document'

/**
 * 「选期刊之前」的那一份（与 `actions.journalPrevOf` 同一个口径）：已经按期刊选过就沿用最初的，别把上一本期刊当成「之前」。
 * `pageW` = `setW`：草稿不改页宽，所以退路里「页宽仍是期刊设的才退回」永远是原地不动，绝不会把页宽打回别处。
 */
function prevOf(cur: DocumentProfile | null, pageW: number): JournalPrev {
  if (cur?.journalRef) return cur.journalPrev ? (structuredClone(cur.journalPrev) as JournalPrev) : { profile: null, pageW, setW: pageW }
  return {
    profile: cur ? structuredClone({ id: cur.id, journal: cur.journal, snapshot: cur.snapshot, snapshotVersion: cur.snapshotVersion, follow: cur.follow }) : null,
    pageW,
    setW: pageW,
  }
}

/** 选一本 v1 目录里的刊（`v2` 取到了才叠，v2 优先；取不到就是 null，照 v1） */
export function draftPickJournal(journalId: string, v2: V2Patch | null, cur: DocumentProfile | null, pageW: number, catalog: JournalCatalog): DocumentProfile {
  const pick = pickJournal(journalId, catalog, { pageW, follow: cur?.follow, prev: prevOf(cur, pageW) })
  return applyV2(pick.binding, v2) as DocumentProfile
}

/** 选一本只在 v2 里有的刊（补丁必须已经在手） */
export function draftPickV2Only(entry: Pick<V2IndexEntry, 'id' | 'label'>, v2: V2Patch, cur: DocumentProfile | null, pageW: number): DocumentProfile {
  return pickV2OnlyJournal(entry, v2, { pageW, follow: cur?.follow, prev: prevOf(cur, pageW) }).binding as DocumentProfile
}

/** 「不按期刊」：退回选期刊之前那一份；没有记录 = 默认规范（null） */
export function draftClearJournal(cur: DocumentProfile | null): DocumentProfile | null {
  if (!cur?.journalRef) return cur
  const before = cur.journalPrev?.profile
  return before ? (structuredClone(before) as DocumentProfile) : null
}

/** 两份绑定是不是同一套（内容比较，`sameRules` 那一把尺；null = 默认规范） */
export const sameDraftBinding = (a: DocumentProfile | null, b: DocumentProfile | null): boolean => (a === null || b === null ? a === b : sameRules(a, b))

/** 请求里的 `font_range`：只放期刊自己规定的上下限（`journalStyleTargets` 那一把尺）；绑定为空 / 两端都没规定 = null（不带这个键） */
export function fontRangeOf(binding: DocumentProfile | null): { min_pt?: number; max_pt?: number } | null {
  const t = journalStyleTargets(binding)
  if (!t || (t.minPt === null && t.maxPt === null)) return null
  return { ...(t.minPt !== null ? { min_pt: t.minPt } : {}), ...(t.maxPt !== null ? { max_pt: t.maxPt } : {}) }
}

/** 灰字：「按 … 出图」里用的各部分（宽度、字号）；key 与参数交给调用方翻译 */
export function specParts(s: { width_single_mm: number; width_double_mm: number; font_min_pt?: number; font_max_pt?: number }) {
  const width =
    s.width_single_mm === s.width_double_mm
      ? ({ key: 'widthOne', values: { single: specNumber(s.width_single_mm) } } as const)
      : ({ key: 'widthTwo', values: { single: specNumber(s.width_single_mm), double: specNumber(s.width_double_mm) } } as const)
  const { font_min_pt: lo, font_max_pt: hi } = s
  const font =
    lo != null && hi != null
      ? lo === hi
        ? ({ key: 'fontOne', values: { min: specNumber(lo) } } as const)
        : ({ key: 'fontRange', values: { min: specNumber(lo), max: specNumber(hi) } } as const)
      : lo != null
        ? ({ key: 'fontMin', values: { min: specNumber(lo) } } as const)
        : hi != null
          ? ({ key: 'fontMax', values: { max: specNumber(hi) } } as const)
          : null
  return [width, ...(font ? [font] : [])]
}

/** 数字不带多余的 0（7 / 6.5 / 85.5） */
export const specNumber = (n: number): string => String(Math.round(n * 100) / 100)
