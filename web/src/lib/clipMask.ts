import type { CanvasObject, PathObject, ShapeObject } from '@/types/document'
import { objectRotation } from '@/types/document'
import { toPathObject } from './convertToPath'
import { pathGeometry, rotateAbout, type PathSeg } from './pathGeom'
import { MM_PER_PT } from './units'

/**
 * 剪切蒙版的几何（ADR 0188）：蒙版对象 → 页面 mm（顶原点、y 向下）里的轮廓。画布（CSS `clip-path: path()`）与导出
 * 载荷（蒙版一律以 `path` 发给后端，后端 `plan._clipped` 用 `geometry.path_segments` 的填充几何 + 对象变换）读的是
 * 同一份：形状先经 `convertToPath.toPathObject` 变成路径（画面逐点不动的那一份），所以前后端只有「路径 → 轮廓」这一条规则。
 * 蒙版只借几何：填充、描边、透明度都不参与。同源看护 `tests/golden/clip_mask_vectors.json`。
 */

/** 蒙版的路径形态：路径原样，封闭形状换成等价路径；换不出来 = null */
export function maskAsPath(m: CanvasObject): PathObject | null {
  if (m.type === 'path') return m as PathObject
  if (m.type === 'shape') return toPathObject(m as ShapeObject)
  return null
}

export interface MaskOutline {
  /** 页面 mm 的段（与 `pathGeom.PathSeg` 同形） */
  segs: PathSeg[]
  fillRule: 'nonzero' | 'evenodd'
}

/** 蒙版在页面 mm 里的轮廓：框空间的填充几何 → 绕框中心按对象旋转（顺时针）→ 平移到框的位置 */
export function maskOutline(m: CanvasObject): MaskOutline | null {
  const p = maskAsPath(m)
  if (!p) return null
  const sw = Math.max(p.strokePt, 0.05) * MM_PER_PT
  const fill = pathGeometry(p, p.w, p.h, sw).fill
  if (!fill.length) return null
  const deg = objectRotation(p)
  const c: [number, number] = [p.x + p.w / 2, p.y + p.h / 2]
  const at = (x: number, y: number) => rotateAbout([p.x + x, p.y + y], c, deg)
  const segs: PathSeg[] = fill.map((s) => {
    if (s[0] === 'Z') return ['Z']
    if (s[0] === 'C') {
      const [a, b, e] = [at(s[1], s[2]), at(s[3], s[4]), at(s[5], s[6])]
      return ['C', a[0], a[1], b[0], b[1], e[0], e[1]]
    }
    const q = at(s[1], s[2])
    return [s[0], q[0], q[1]]
  })
  return { segs, fillRule: p.fillRule === 'evenodd' ? 'evenodd' : 'nonzero' }
}

/** 轮廓 → CSS `clip-path` 值（坐标乘 `scale`，单位 px；原点是元素的左上角） */
export function clipPathCss(outline: MaskOutline, scale: number, dx = 0, dy = 0): string {
  const d = outline.segs
    .map((s) => {
      if (s[0] === 'Z') return 'Z'
      const nums = s.slice(1) as number[]
      return `${s[0]} ${nums.map((v, i) => +((v + (i % 2 ? dy : dx)) * scale).toFixed(3)).join(' ')}`
    })
    .join(' ')
  return `path(${outline.fillRule}, '${d}')`
}
