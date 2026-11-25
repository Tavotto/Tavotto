import type { ManifestElement, ReviewAnchor } from '@/lib/api'
import { engineLabel } from '../inspector/roles/registry'
import { cp } from './changePlanModel'

/** 要写批注的那一处：锚点（元素 / 框选 / 整体）+ 它的显示名 */
export interface NoteTarget {
  anchor: ReviewAnchor
  label: string
}

const UNESCAPED_DOLLAR = /(?<!\\)\$/g

/**
 * 批注上写的元素名：与元素树 / 回复草稿同一个显示名出口（`engineLabel`，mathtext 读作 min⁻¹）。
 * 引擎给的名字常在 mathtext 中间被截断（`… min$^{-1…`，`$` 不成对），`engineLabel` 对不成对的原样返回——这里先把没闭合的花括号与 `$` 补上再走它。
 * 没有名字退回「这一处」，不露 gid。
 */
export function noteLabelOf(raw: string | undefined): string {
  let s = (raw ?? '').trim()
  if ((s.match(UNESCAPED_DOLLAR)?.length ?? 0) % 2 === 1) {
    const ellipsis = s.endsWith('…')
    const body = ellipsis ? s.slice(0, -1) : s
    const unclosed = body.split('{').length - body.split('}').length
    s = `${body}${'}'.repeat(Math.max(0, unclosed))}$${ellipsis ? '…' : ''}`
  }
  return engineLabel(s).trim() || cp('dialog.noteHere')
}

/** 元素 → 批注锚点（与审阅意见同形：元素 gid + 画面框） */
export const elementTarget = (el: ManifestElement): NoteTarget => ({
  anchor: { kind: 'element', gid: el.gid, box: el.bbox.map((v) => Math.round(v * 1e4) / 1e4) as [number, number, number, number] },
  label: noteLabelOf(el.label),
})
