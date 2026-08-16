import { MM_PER_PT } from '@/lib/units'
import { dashArray } from '@/lib/shapeGeometry'
import { mmToWorld } from '@/store/viewportStore'
import type { ArrowObject } from '@/types/document'
import { arrowHeads } from '@/types/document'

/**
 * 箭头：两端点存比例坐标；端型独立（triangle 实心 / open 开口 / bar 短线）。
 * 几何与后端 _draw_arrow 一一对应：帽长 4×线宽、帽半宽 1.7×线宽、
 * 仅 triangle 端回缩 0.75×帽长。
 */
export function ArrowView({ obj }: { obj: ArrowObject }) {
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)

  const a = { x: obj.start.rx * w, y: obj.start.ry * h }
  const b = { x: obj.end.rx * w, y: obj.end.ry * h }
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const nx = -uy
  const ny = ux

  const headLen = sw * 4
  const headHalf = sw * 1.7
  const heads = arrowHeads(obj)

  const trianglePts = (tip: { x: number; y: number }, sign: 1 | -1) => {
    const bx = tip.x - sign * ux * headLen
    const by = tip.y - sign * uy * headLen
    return `${tip.x},${tip.y} ${bx + nx * headHalf},${by + ny * headHalf} ${bx - nx * headHalf},${by - ny * headHalf}`
  }
  const openPath = (tip: { x: number; y: number }, sign: 1 | -1) => {
    const bx = tip.x - sign * ux * headLen
    const by = tip.y - sign * uy * headLen
    return `M ${bx + nx * headHalf},${by + ny * headHalf} L ${tip.x},${tip.y} L ${bx - nx * headHalf},${by - ny * headHalf}`
  }
  const barPath = (tip: { x: number; y: number }) =>
    `M ${tip.x + nx * headHalf},${tip.y + ny * headHalf} L ${tip.x - nx * headHalf},${tip.y - ny * headHalf}`

  // 仅实心三角端回缩线段（其余端型线到尖）
  const trim = headLen * 0.75
  const p1 = {
    x: a.x + (heads.start === 'triangle' ? ux * trim : 0),
    y: a.y + (heads.start === 'triangle' ? uy * trim : 0),
  }
  const p2 = {
    x: b.x - (heads.end === 'triangle' ? ux * trim : 0),
    y: b.y - (heads.end === 'triangle' ? uy * trim : 0),
  }

  const stroke = { stroke: obj.color, strokeWidth: sw, strokeLinecap: 'round' as const }
  const renderHead = (kind: string, tip: { x: number; y: number }, sign: 1 | -1) => {
    if (kind === 'triangle') return <polygon points={trianglePts(tip, sign)} fill={obj.color} />
    if (kind === 'open') return <path d={openPath(tip, sign)} fill="none" {...stroke} />
    if (kind === 'bar') return <path d={barPath(tip)} fill="none" {...stroke} />
    return null
  }

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      width={w}
      height={h}
    >
      <line
        x1={p1.x}
        y1={p1.y}
        x2={p2.x}
        y2={p2.y}
        strokeDasharray={dashArray(obj.dash, sw)}
        {...stroke}
      />
      {renderHead(heads.end, b, 1)}
      {renderHead(heads.start, a, -1)}
    </svg>
  )
}
