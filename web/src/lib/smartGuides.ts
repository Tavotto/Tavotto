import type { Manifest } from './api'
import { toPathObject } from './convertToPath'
import { isElementHidden, panelFullRect } from './elementGeom'
import { contentToPageVec, panelContentTransform } from './panelTransform'
import { pathToMm, rotatePoint, type MmSubpath } from './pathEdit'
import type { CanvasObject, PanelObject, PathObject } from '@/types/document'

/**
 * 智能参考线的点候选（ADR 0185 M7）：拖动时除了对象的边 / 中线、参考线、网格，还吸**别的路径的锚点与线段中点**，以及
 * **图内的数据点**。吸附仍是一维的（`snapEdge` 吸 x / y 两组线），参考线的样子与现有的一样——这里只负责把点摊成 x、y 两组线。
 *
 * - 路径、路径文字自带的路径、形状（按「转为路径」的同一份几何：多边形 / 星形的顶点、椭圆的四个端点）、箭头（两端）：
 *   每个锚点 + 每段曲线 t = 0.5 处的点，都换到页面（含旋转）。
 * - 图内数据点：manifest 里曲线（role = line）的折线顶点——引擎抽稀过（`engine/pathgeom._thin`），留下来的都是真实的数据点，
 *   只是不全；散点（role = scatter）每颗 marker 轮廓的中心 = 数据点。坐标与 `elementSnapCandidates` 同一个变换（含裁剪、翻转、
 *   旋转），裁剪窗外的点不出线。柱、填充、误差棒没有「点」的坐标可用，不吸。
 * - 每张图最多 `DATA_POINT_CAP` 个点（再多参考线就成了一片噪音，拖起来也慢）。
 */
export type XY = [number, number]
export const DATA_POINT_CAP = 600

const cubicMid = (a: MmSubpath['nodes'][number], b: MmSubpath['nodes'][number]): XY => [
  0.125 * a.p[0] + 0.375 * a.out[0] + 0.375 * b.in[0] + 0.125 * b.p[0],
  0.125 * a.p[1] + 0.375 * a.out[1] + 0.375 * b.in[1] + 0.125 * b.p[1],
]

/** 一个对象的锚点与线段中点（页面 mm，已含旋转）；不是线条类的对象回空 */
export function pathSnapPoints(o: CanvasObject): XY[] {
  if (o.hidden) return []
  const p: PathObject | null =
    o.type === 'path' || o.type === 'pathText'
      ? (o as unknown as PathObject)
      : o.type === 'shape' || o.type === 'arrow'
        ? toPathObject(o)
        : null
  if (!p) return []
  const out: XY[] = []
  for (const sp of pathToMm(p)) {
    const n = sp.nodes.length
    if (n < 2) continue
    for (let i = 0; i < n; i++) {
      out.push(rotatePoint(p, sp.nodes[i].p))
      if (i + 1 < n || sp.closed) out.push(rotatePoint(p, cubicMid(sp.nodes[i], sp.nodes[(i + 1) % n])))
    }
  }
  return out
}

/** 图内数据点（页面 mm）：曲线的顶点、散点的中心；见文件头 */
export function dataSnapPoints(panel: PanelObject, manifest: Manifest): XY[] {
  const full = panelFullRect(panel)
  const tf = panelContentTransform(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  const out: XY[] = []
  const push = (fx: number, fy: number) => {
    const mx = full.x + fx * full.w
    const my = full.y + fy * full.h
    const [dx, dy] = contentToPageVec(tf, mx - cx, my - cy)
    const px = cx + dx
    const py = cy + dy
    if (px < panel.x || px > panel.x + panel.w || py < panel.y || py > panel.y + panel.h) return
    out.push([px, py])
  }
  for (const el of manifest.elements) {
    if (out.length >= DATA_POINT_CAP) break
    if (isElementHidden(el) || !el.geometry) continue
    if (el.role === 'line') {
      for (const path of el.geometry.paths) for (const [fx, fy] of path.points) push(fx, fy)
    } else if (el.role === 'scatter') {
      for (const path of el.geometry.paths) {
        if (!path.points.length) continue
        const sx = path.points.reduce((s, q) => s + q[0], 0) / path.points.length
        const sy = path.points.reduce((s, q) => s + q[1], 0) / path.points.length
        push(sx, sy)
      }
    }
  }
  return out.slice(0, DATA_POINT_CAP)
}
