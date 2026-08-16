import { MM_PER_PT } from '@/lib/units'
import { dashArray, polygonPoints } from '@/lib/shapeGeometry'
import { mmToWorld } from '@/store/viewportStore'
import type { ShapeObject } from '@/types/document'

/**
 * 形状：几何公式与后端 _draw_shape 一一对应（engine 侧同名注释），
 * 显示与 PDF 导出必须逐点一致。
 */
export function ShapeView({ obj }: { obj: ShapeObject }) {
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)
  const inset = sw / 2 // 描边居中，内缩半个线宽让外沿正好贴合包围盒
  const fill = obj.fill ?? 'none'
  const fillOpacity = obj.fill ? (obj.fillOpacity ?? 1) : undefined
  const dash = dashArray(obj.dash, sw)

  const stroke = {
    stroke: obj.color,
    strokeWidth: sw,
    strokeDasharray: dash,
    strokeLinejoin: 'round' as const,
  }

  const poly = (pts: [number, number][]) => (
    <polygon
      points={pts.map(([x, y]) => `${x},${y}`).join(' ')}
      fill={fill}
      fillOpacity={fillOpacity}
      {...stroke}
    />
  )

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      width={w}
      height={h}
    >
      {obj.shape === 'rect' && (
        <rect
          x={inset}
          y={inset}
          width={Math.max(w - sw, 0.001)}
          height={Math.max(h - sw, 0.001)}
          rx={obj.cornerRadius ? mmToWorld(obj.cornerRadius) : undefined}
          fill={fill}
          fillOpacity={fillOpacity}
          {...stroke}
        />
      )}
      {obj.shape === 'ellipse' && (
        <ellipse
          cx={w / 2}
          cy={h / 2}
          rx={Math.max(w / 2 - inset, 0.001)}
          ry={Math.max(h / 2 - inset, 0.001)}
          fill={fill}
          fillOpacity={fillOpacity}
          {...stroke}
        />
      )}
      {obj.shape === 'line' && (
        <line
          x1={0}
          y1={h / 2}
          x2={w}
          y2={h / 2}
          strokeLinecap="round"
          {...stroke}
        />
      )}
      {obj.shape === 'triangle' &&
        poly([
          [w / 2, inset],
          [w - inset, h - inset],
          [inset, h - inset],
        ])}
      {obj.shape === 'diamond' &&
        poly([
          [w / 2, inset],
          [w - inset, h / 2],
          [w / 2, h - inset],
          [inset, h / 2],
        ])}
      {obj.shape === 'polygon' &&
        poly(polygonPoints(obj.sides ?? 6, w, h, inset))}
      {obj.shape === 'brace' && (
        <path d={bracePath(w, h, inset)} fill="none" strokeLinecap="round" {...stroke} />
      )}
    </svg>
  )
}

/**
 * 大括号「{」：右侧两钩 + 中部左尖，用二次贝塞尔；
 * 旋转（rotationDeg）覆盖其它方向。与后端 _brace_segments 同一构造。
 */
function bracePath(w: number, h: number, inset: number): string {
  const cx = w / 2
  const tipGap = Math.min(h * 0.06, h / 2 - inset)
  return [
    `M ${w - inset},${inset}`,
    `Q ${cx},${inset} ${cx},${h * 0.25}`,
    `L ${cx},${h / 2 - tipGap}`,
    `Q ${cx},${h / 2} ${inset},${h / 2}`,
    `Q ${cx},${h / 2} ${cx},${h / 2 + tipGap}`,
    `L ${cx},${h * 0.75}`,
    `Q ${cx},${h - inset} ${w - inset},${h - inset}`,
  ].join(' ')
}
