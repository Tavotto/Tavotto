import { useMemo } from 'react'
import { useDocumentStore } from '@/store/documentStore'
import type { CanvasObject, PageSetup } from '@/types/document'
import { normalizeHex } from '../ui/colorPalette'

/**
 * 「排版里的颜色」（界面名词，ADR 0001）：这份排版里已经在用的颜色，按出现次数从多到少（`ColorField` 取色面板的第一组）。
 *
 * 只读文档里**写下来的**颜色：画布对象（文字 / 箭头 / 形状的边与填充、文字底色与描边）、页面背景、
 * 各张图里用户改过的颜色 override。脚本自己画的颜色不在文档里，这里不猜。
 */
export function documentColorsOf(doc: { objects: readonly CanvasObject[]; page: PageSetup }): string[] {
  const counts = new Map<string, number>()
  const add = (v: unknown) => {
    if (typeof v !== 'string') return
    const c = normalizeHex(v)
    if (c) counts.set(c, (counts.get(c) ?? 0) + 1)
  }
  for (const o of doc.objects) {
    if (o.type === 'text') {
      add(o.color)
      add(o.bg)
      add(o.borderColor)
    } else if (o.type === 'arrow') add(o.color)
    else if (o.type === 'shape') {
      add(o.color)
      add(o.fill)
    } else if (o.type === 'panel') for (const ov of o.overrides) add(ov.value)
  }
  if (!doc.page.transparent) add(doc.page.bg)
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c)
}

export function useDocumentColors(): string[] {
  const objects = useDocumentStore((s) => s.doc.objects)
  const page = useDocumentStore((s) => s.doc.page)
  return useMemo(() => documentColorsOf({ objects, page }), [objects, page])
}
