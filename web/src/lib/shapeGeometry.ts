import type { DashStyle } from '@/types/document'

/** 正 N 边形顶点（内切于包围盒，顶点朝上）；与后端 _polygon_points 同一公式 */
export function polygonPoints(
  sides: number,
  w: number,
  h: number,
  inset: number,
): [number, number][] {
  const n = Math.max(3, Math.min(12, Math.round(sides)))
  const rx = Math.max(w / 2 - inset, 0.001)
  const ry = Math.max(h / 2 - inset, 0.001)
  const pts: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
    pts.push([w / 2 + rx * Math.cos(a), h / 2 + ry * Math.sin(a)])
  }
  return pts
}

/** 虚线间距按线宽比例换算；与后端 _dash_pattern 同一比例 */
export function dashArray(dash: DashStyle | undefined, sw: number): string | undefined {
  if (dash === 'dashed') return `${sw * 4} ${sw * 2.5}`
  if (dash === 'dotted') return `${sw * 0.01} ${sw * 2}`
  return undefined
}
