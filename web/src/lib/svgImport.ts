import type { CanvasObject } from '@/types/document'
import { isKnownObject } from '@/types/document'
import { newId } from './id'
import { remapGroupPath } from './groupTree'

/**
 * 外部 SVG 导入的前端那一半（ADR 0185 M5）：解析在后端（`rendercore/svgimport`，不受信任的输入只在那里读），这里只做
 * 「认不认得出是 SVG」与「把后端给的对象落到画布上」——id 与组 id 整批换新、平移到落点。
 */

/** 剪贴板 / 文本是不是一段 SVG 标记（Illustrator、Inkscape 复制出来的是整段 `<svg …>`） */
export function looksLikeSvg(text: string | null | undefined): boolean {
  if (!text) return false
  const head = text.trimStart().slice(0, 2048).toLowerCase()
  return (head.startsWith('<svg') || head.startsWith('<?xml') || head.startsWith('<!--') || head.startsWith('<!doctype svg')) && /<svg[\s>]/.test(head + text.slice(2048, 8192).toLowerCase())
}

/** 导入失败 / 做不准时说一句原因用的原因码：第一个（按后端给的顺序） */
export const firstReason = (unsupported: Record<string, number> | undefined): string | null =>
  unsupported ? (Object.keys(unsupported)[0] ?? null) : null

/**
 * 后端给的对象 → 落到画布上的对象：只收本构建认识的对象；id 与组 id 整批换新；整体平移，使导入内容的视口左上角落在
 * `origin`（页面 mm）。返回 null = 一个能用的都没有。
 */
export function placeImported(raw: readonly unknown[], origin: { x: number; y: number }): CanvasObject[] | null {
  const groups = new Map<string, string>()
  const fresh = (g: string) => {
    if (!groups.has(g)) groups.set(g, newId('g'))
    return groups.get(g)!
  }
  const out: CanvasObject[] = []
  for (const r of raw) {
    if (!r || typeof r !== 'object') continue
    const o = structuredClone(r) as CanvasObject
    if (typeof o.type !== 'string' || !isKnownObject(o) || !Number.isFinite(o.x) || !Number.isFinite(o.y)) continue
    o.id = newId(o.type[0])
    o.x += origin.x
    o.y += origin.y
    remapGroupPath(o, fresh)
    out.push(o)
  }
  return out.length ? out : null
}
