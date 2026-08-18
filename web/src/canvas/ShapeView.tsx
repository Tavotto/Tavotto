import { MM_PER_PT } from '@/lib/units'
import {
  bracePath,
  cornerRadius,
  dashArray,
  hitStrokeWidth,
  PATH_HIT_SHAPES,
  shapeOutline,
} from '@/lib/shapeGeometry'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { ShapeObject } from '@/types/document'
import { lineEndpoints } from '@/types/document'

/**
 * 形状：几何公式与后端 _draw_shape 一一对应（engine 侧同名注释），
 * 显示与 PDF 导出必须逐点一致。
 *
 * `hit` 由 ObjectView 决定：直线和箭头一样是细长线状对象，沿拖动方向铺开时
 * 包围盒另一边被钳到 0.01mm，命中得交给沿线段的透明线；椭圆 / 三角 / 菱形 /
 * 多边形 / 大括号则是「包围盒里大半是空白」的那一型，命中交给沿真实轮廓的
 * 透明形状（PATH_HIT_SHAPES）。矩形不需要，它的包围盒就是它自己。
 */
export function ShapeView({ obj, hit = 'none' }: { obj: ShapeObject; hit?: 'stroke' | 'none' }) {
  const zoom = useViewportStore((s) => s.zoom)
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)
  const inset = sw / 2 // 描边居中，内缩半个线宽让外沿正好贴合包围盒
  const fill = obj.fill ?? 'none'
  const fillOpacity = obj.fill ? (obj.fillOpacity ?? 1) : undefined
  const dash = dashArray(obj.dash, sw)
  // 描边内缩后的矩形（与后端 rect 同一个 Rect），圆角钳制也以它为准
  const rectW = Math.max(w - sw, 0.001)
  const rectH = Math.max(h - sw, 0.001)
  const rectR = obj.cornerRadius
    ? cornerRadius(mmToWorld(obj.cornerRadius), rectW, rectH)
    : undefined

  const stroke = {
    stroke: obj.color,
    strokeWidth: sw,
    strokeDasharray: dash,
    strokeLinejoin: 'round' as const,
    // dotted 的线段长只有 0.01×线宽，「点」全靠圆线帽画出来；缺省 butt 线帽下
    // 点是零面积，整圈描边直接不可见。与后端 _draw_shape 的 lineCap=1 同源。
    strokeLinecap: 'round' as const,
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
      // 与 ArrowView 同一钳制：竖直 / 水平直线的包围盒薄到亚像素时，
      // Chrome 会整个跳过 <svg> 的绘制；视口钳到 ≥1px，内部坐标不变。
      width={Math.max(w, 1)}
      height={Math.max(h, 1)}
    >
      {obj.shape === 'rect' && (
        <rect
          x={inset}
          y={inset}
          width={rectW}
          height={rectH}
          // rx/ry 同值：SVG 只写 rx 时两个方向会各自按半宽/半高钳制，宽高悬殊
          // 时退化成椭圆角，而后端画的是正圆角。钳制公式见 lib/cornerRadius。
          rx={rectR}
          ry={rectR}
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
      {obj.shape === 'line' &&
        (() => {
          // 端点与箭头同构（比例坐标）；旧文档没有 start/end 时兜底成水平中线
          const { start: s, end: e } = lineEndpoints(obj)
          const a = { x: s.rx * w, y: s.ry * h }
          const b = { x: e.rx * w, y: e.ry * h }
          return (
            <>
              {/* 与 ArrowView 同一手法：线状对象的包围盒又薄又大，命中只能交给这条
                  沿真实端点走的透明线（端点一动它就跟着动） */}
              <line
                data-hit-line=""
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="transparent"
                strokeWidth={hitStrokeWidth(sw, zoom)}
                strokeLinecap="round"
                style={{ pointerEvents: hit }}
              />
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...stroke} />
            </>
          )
        })()}
      {(obj.shape === 'triangle' || obj.shape === 'diamond' || obj.shape === 'polygon') &&
        (() => {
          const o = shapeOutline(obj.shape, w, h, inset, obj.sides)
          return o?.kind === 'poly' ? poly(o.points) : null
        })()}
      {obj.shape === 'brace' && (
        <path d={bracePath(w, h, inset)} fill="none" {...stroke} />
      )}
      {/* 真实轮廓的透明命中层：包围盒命中会让三角形 / 大括号的空白角也吃点击
          （外层 div 因此让位，见 ObjectView 的 pointerEvents）。有填充的按
          「整块可点」，空心的只在描边附近命中，与图内曲线同一语义。 */}
      {hit !== 'none' &&
        PATH_HIT_SHAPES.has(obj.shape) &&
        (() => {
          const o = shapeOutline(obj.shape, w, h, inset, obj.sides)
          if (!o) return null
          const filled = obj.shape !== 'brace' && !!obj.fill
          const common = {
            'data-hit-shape': '',
            fill: filled ? 'transparent' : 'none',
            stroke: 'transparent',
            strokeWidth: hitStrokeWidth(sw, zoom),
            strokeLinejoin: 'round' as const,
            style: { pointerEvents: (filled ? 'all' : 'stroke') as 'all' | 'stroke' },
          }
          if (o.kind === 'ellipse') {
            return <ellipse cx={o.cx} cy={o.cy} rx={o.rx} ry={o.ry} {...common} />
          }
          if (o.kind === 'poly') {
            return <polygon points={o.points.map(([x, y]) => `${x},${y}`).join(' ')} {...common} />
          }
          return <path d={o.d} {...common} />
        })()}
    </svg>
  )
}
