import type { ManifestElement, ReviewAnchor } from '@/lib/api'
import { engineLabel } from '../inspector/roles/registry'

/** 要写批注的那一处：锚点（元素 / 框选 / 整体）+ 它的显示名 */
export interface NoteTarget {
  anchor: ReviewAnchor
  label: string
}

/** 元素 → 批注锚点（与审阅意见同形：元素 gid + 画面框） */
export const elementTarget = (el: ManifestElement): NoteTarget => ({
  anchor: { kind: 'element', gid: el.gid, box: el.bbox.map((v) => Math.round(v * 1e4) / 1e4) as [number, number, number, number] },
  label: engineLabel(el.label).trim() || el.gid,
})
