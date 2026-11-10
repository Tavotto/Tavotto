/**
 * 导出预览里的一个箭头 / 形状，按它自己的几何与样式画（单位是页面 mm）。
 *
 * 与画布 `ArrowView` / `ShapeView` 取同一套值：端点走 `lineEndpoints`、箭头帽走 `arrowGeometry`、
 * 多边形与大括号走 `shapeOutline`、虚线走 `dashArray`、圆角走 `cornerRadius`，旋转是绕包围盒中心。
 * 缩略图（版本列表 / 画布列表）不走这里：草图里没有这些字段，也不值得为几十张小图画箭头帽。
 */
import {
  arrowGeometry,
  bracePath,
  cornerRadius,
  defaultLineJoin,
  isFilledHead,
  shapeInset,
  shapeOutline,
  strokeOpacityOf,
  svgStroke,
} from '@/lib/shapeGeometry'
import { MM_PER_PT } from '@/lib/units'
import type { ArrowObject, ShapeObject } from '@/types/document'
import { lineEndpoints, objectRotation, shapeStrokeOn } from '@/types/document'

export function ThumbShape({ obj }: { obj: ArrowObject | ShapeObject }) {
  const w = Math.max(obj.w, 0.001)
  const h = Math.max(obj.h, 0.001)
  const sw = Math.max(obj.strokePt * MM_PER_PT, 0.0132)
  const rot = objectRotation(obj)
  const wrap = rot ? `rotate(${rot} ${obj.x + w / 2} ${obj.y + h / 2})` : undefined
  const strokeOn = obj.type === 'arrow' || shapeStrokeOn(obj)
  const stroke = strokeOn
    ? svgStroke(obj, sw, obj.type === 'arrow' ? 'round' : defaultLineJoin(obj.shape))
    : {}
  const at = `translate(${obj.x} ${obj.y})`

  if (obj.type === 'arrow') {
    const g = arrowGeometry(obj, w, h, sw)
    const headStroke = svgStroke(obj, sw, 'round', false)
    const opacity = strokeOpacityOf(obj.strokeOpacity)
    const head = (hd: typeof g.endHead) =>
      !hd ? null : hd.kind === 'dot' ? (
        <circle cx={hd.cx} cy={hd.cy} r={hd.r} fill={obj.color} fillOpacity={opacity} />
      ) : isFilledHead(hd) ? (
        <polygon points={hd.points} fill={obj.color} fillOpacity={opacity} />
      ) : (
        <path d={hd.d} fill="none" {...headStroke} />
      )
    return (
      <g data-thumb-shape="arrow" transform={wrap}>
        <g transform={at}>
          <line x1={g.p1.x} y1={g.p1.y} x2={g.p2.x} y2={g.p2.y} {...stroke} />
          {head(g.endHead)}
          {head(g.startHead)}
        </g>
      </g>
    )
  }

  const inset = shapeInset(obj, sw)
  const fill = obj.fill ?? 'none'
  const fillOpacity = obj.fill ? (obj.fillOpacity ?? 1) : undefined
  let body: React.ReactNode = null
  if (obj.shape === 'rect') {
    const rw = Math.max(w - 2 * inset, 0.001)
    const rh = Math.max(h - 2 * inset, 0.001)
    const r = obj.cornerRadius ? cornerRadius(obj.cornerRadius, rw, rh) : undefined
    body = <rect x={inset} y={inset} width={rw} height={rh} rx={r} ry={r} fill={fill} fillOpacity={fillOpacity} {...stroke} />
  } else if (obj.shape === 'line') {
    const { start: s, end: e } = lineEndpoints(obj)
    body = <line x1={s.rx * w} y1={s.ry * h} x2={e.rx * w} y2={e.ry * h} {...stroke} />
  } else if (obj.shape === 'brace') {
    body = <path d={bracePath(w, h, inset)} fill="none" {...stroke} />
  } else {
    const o = shapeOutline(obj.shape, w, h, inset, obj.sides)
    if (o?.kind === 'ellipse') {
      body = <ellipse cx={o.cx} cy={o.cy} rx={o.rx} ry={o.ry} fill={fill} fillOpacity={fillOpacity} {...stroke} />
    } else if (o?.kind === 'poly') {
      body = (
        <polygon
          points={o.points.map(([x, y]) => `${x},${y}`).join(' ')}
          fill={fill}
          fillOpacity={fillOpacity}
          {...stroke}
        />
      )
    }
  }
  return (
    <g data-thumb-shape={obj.shape} transform={wrap}>
      <g transform={at}>{body}</g>
    </g>
  )
}
