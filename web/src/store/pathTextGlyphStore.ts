import { create } from 'zustand'
import type { Rect } from '@/lib/geometry'
import type { PathTextObject } from '@/types/document'

/**
 * 路径文字的字实际占到哪（ADR 0190）：画布照后端字形画完以后，把字形轮廓的包围盒（对象局部、未旋转、mm，原点在框左上角）
 * 记在这里。选框与浮动栏要让开字——离线以后字常常伸出路径的框。不是文档的一部分，只给画布用。
 */
interface GlyphState {
  bounds: Record<string, Rect>
  setBounds: (id: string, r: Rect | null) => void
}

export const usePathTextGlyphStore = create<GlyphState>((set) => ({
  bounds: {},
  setBounds: (id, r) =>
    set((s) => {
      const cur = s.bounds[id]
      if (r && cur && cur.x === r.x && cur.y === r.y && cur.w === r.w && cur.h === r.h) return s
      if (!r && !cur) return s
      const next = { ...s.bounds }
      if (r) next[id] = r
      else delete next[id]
      return { bounds: next }
    }),
}))

/**
 * 选框：路径的框并上字的范围（对象局部空间里并），再按对象的旋转放回页面——回一个与对象同样字段的框（中心随并集挪、角度不变），
 * 交给 `toScreen` / `spinOf` 跟别的对象一样画。还不知道字在哪就是路径的框。
 */
export function pathTextFrame(
  o: PathTextObject,
  glyphs: Rect | undefined = usePathTextGlyphStore.getState().bounds[o.id],
): PathTextObject {
  if (!glyphs) return o
  const x0 = Math.min(0, glyphs.x)
  const y0 = Math.min(0, glyphs.y)
  const x1 = Math.max(o.w, glyphs.x + glyphs.w)
  const y1 = Math.max(o.h, glyphs.y + glyphs.h)
  const w = x1 - x0
  const h = y1 - y0
  // 并集中心在局部空间里离框中心多远，按对象的旋转换到页面
  const dx = (x0 + x1) / 2 - o.w / 2
  const dy = (y0 + y1) / 2 - o.h / 2
  const r = ((o.rotationDeg ?? 0) * Math.PI) / 180
  const cx = o.x + o.w / 2 + dx * Math.cos(r) - dy * Math.sin(r)
  const cy = o.y + o.h / 2 + dx * Math.sin(r) + dy * Math.cos(r)
  return { ...o, x: cx - w / 2, y: cy - h / 2, w, h }
}
