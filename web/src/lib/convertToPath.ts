import type {
  ArrowObject,
  CanvasObject,
  PathNode,
  PathObject,
  PathPoint,
  PathSubpath,
  ShapeObject,
} from '@/types/document'
import { arrowHeads, isStrokeOnlyShape, lineEndpoints, objectRotation, shapeStrokeOn } from '@/types/document'
import { fitPathBox, PATH_DEFAULT_JOIN } from './pathGeom'
import { arcPoints, braceParts, cornerRadius, defaultLineJoin, KAPPA, outlineOf, shapeInset, shapeOutline } from './shapeGeometry'
import { MM_PER_PT } from './units'
import { remapGradients } from './gradient'

/**
 * 「转为路径」（ADR 0185 §二）：形状 / 箭头 → 自由路径 `path`。
 *
 * 这里是这件事的唯一出处（纯函数，不碰 store、不开事务、不进历史——那三件事在
 * `store/actions.convertToPath`：一次 commit、不换 id、数组位置不动）。跨族转换，不走 `lib/shapeSwitch`
 * （那里只管族内互换）；单向，撤销负责回去。
 *
 * **画面逐点不动**：几何取自画布正在画的那一份（`lib/shapeGeometry` 的 `shapeInset` / `shapeOutline` /
 * `cornerRadius` / `braceParts` / `lineEndpoints`），在对象自己的 mm 里算出锚点，再由 `pathGeom.fitPathBox`
 * 把包围盒收到曲线的实际范围（ADR 0185：路径的框按曲线算），旋转中心随框挪了的那一点整体补回去。
 * 椭圆与圆角是 4 段三次贝塞尔（κ = 0.5522847498，与后端 `rendercore/geometry.KAPPA` 同一个数），
 * 大括号的二次贝塞尔精确升成三次。填充 / 描边 / 线型 / 端型字段照搬；缺省仍不写（路径的缺省拐角是圆角，
 * 直线与大括号原来的缺省是尖角，所以给它们写明 `lineJoin: 'miter'`）。
 */


/** 这个对象能不能转为路径：形状（全部七种）与箭头 */
export function canConvertToPath(o: CanvasObject): o is ShapeObject | ArrowObject {
  return o.type === 'shape' || o.type === 'arrow'
}

type XY = [number, number]
const node = (p: XY, inH?: XY, outH?: XY, smooth?: boolean): PathNode => ({
  p,
  ...(inH ? { in: inH } : {}),
  ...(outH ? { out: outH } : {}),
  ...(smooth ? { smooth: true as const } : {}),
})

/** 对象自己的 mm（原点在框左上、未旋转）里的子路径 */
function localSubpaths(o: ShapeObject | ArrowObject, sw: number): PathSubpath[] {
  const w = Math.max(o.w, 0.001)
  const h = Math.max(o.h, 0.001)
  if (o.type === 'arrow') {
    return [{ closed: false, nodes: [node([o.start.rx * w, o.start.ry * h]), node([o.end.rx * w, o.end.ry * h])] }]
  }
  const inset = shapeInset(o, sw)
  switch (o.shape) {
    case 'line': {
      const { start, end } = lineEndpoints(o)
      return [{ closed: false, nodes: [node([start.rx * w, start.ry * h]), node([end.rx * w, end.ry * h])] }]
    }
    case 'rect': {
      // 与 ShapeView 的 <rect> 同一个矩形：描边内缩后的尺寸，圆角按它钳制（`cornerRadius`）
      const x0 = inset
      const y0 = inset
      const rw = Math.max(w - 2 * inset, 0.001)
      const rh = Math.max(h - 2 * inset, 0.001)
      const x1 = x0 + rw
      const y1 = y0 + rh
      const r = o.cornerRadius ? cornerRadius(o.cornerRadius, rw, rh) : 0
      if (!(r > 0)) {
        return [{ closed: true, nodes: [node([x0, y0]), node([x1, y0]), node([x1, y1]), node([x0, y1])] }]
      }
      const k = KAPPA * r
      return [
        {
          closed: true,
          nodes: [
            node([x0 + r, y0], [x0 + r - k, y0], undefined, true),
            node([x1 - r, y0], undefined, [x1 - r + k, y0], true),
            node([x1, y0 + r], [x1, y0 + r - k], undefined, true),
            node([x1, y1 - r], undefined, [x1, y1 - r + k], true),
            node([x1 - r, y1], [x1 - r + k, y1], undefined, true),
            node([x0 + r, y1], undefined, [x0 + r - k, y1], true),
            node([x0, y1 - r], [x0, y1 - r + k], undefined, true),
            node([x0, y0 + r], undefined, [x0, y0 + r - k], true),
          ],
        },
      ]
    }
    case 'ellipse': {
      const e = shapeOutline('ellipse', w, h, inset)
      if (e?.kind !== 'ellipse') return []
      const { cx, cy, rx, ry } = e
      const kx = KAPPA * rx
      const ky = KAPPA * ry
      // 与后端 `ellipse_segments` 同一个起点与方向（最右点起、y 向下的空间里顺时针）
      return [
        {
          closed: true,
          nodes: [
            node([cx + rx, cy], [cx + rx, cy - ky], [cx + rx, cy + ky], true),
            node([cx, cy + ry], [cx + kx, cy + ry], [cx - kx, cy + ry], true),
            node([cx - rx, cy], [cx - rx, cy + ky], [cx - rx, cy - ky], true),
            node([cx, cy - ry], [cx - kx, cy - ry], [cx + kx, cy - ry], true),
          ],
        },
      ]
    }
    case 'triangle':
    case 'diamond':
    case 'polygon':
    case 'star': {
      const out = outlineOf(o, w, h, inset)
      return out?.kind === 'poly' ? [{ closed: true, nodes: out.points.map((p) => node(p)) }] : []
    }
    case 'brace': {
      // 二次贝塞尔 Q(p0, c, p1) 精确等于三次 C(p0 + ⅔(c − p0), p1 + ⅔(c − p1), p1)
      const { start, parts } = braceParts(w, h, inset)
      const nodes: PathNode[] = [node(start)]
      const lift = (a: XY, c: XY): XY => [a[0] + (2 / 3) * (c[0] - a[0]), a[1] + (2 / 3) * (c[1] - a[1])]
      for (const part of parts) {
        const prev = nodes[nodes.length - 1]
        if (part.kind === 'Q') {
          prev.out = lift(prev.p, part.c)
          nodes.push(node(part.p, lift(part.p, part.c)))
        } else {
          nodes.push(node(part.p))
        }
      }
      return [{ closed: false, nodes }]
    }
    case 'arc': {
      const [p0, c1, c2, p3] = arcPoints(w, h, inset)
      return [{ closed: false, nodes: [node(p0, undefined, c1), node(p3, c2)] }]
    }
    default:
      return []
  }
}

/**
 * 一个形状 / 箭头 → 同 id 的路径对象（画面逐点不动）。不能转（几何退化到没有两个锚点）→ null。
 * 文档层的通用字段（id / name / locked / hidden / groupId / layoutPinned / rotationDeg）原样带过去。
 */
export function toPathObject(o: ShapeObject | ArrowObject): PathObject | null {
  const sw = Math.max(o.strokePt, 0.05) * MM_PER_PT
  const local = localSubpaths(o, sw)
  const toPage = (q: PathPoint | undefined): PathPoint | undefined => (q ? [q[0] + o.x, q[1] + o.y] : undefined)
  const page: PathSubpath[] = local.map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((n) => ({
      p: toPage(n.p)!,
      ...(n.in ? { in: toPage(n.in) } : {}),
      ...(n.out ? { out: toPage(n.out) } : {}),
      ...(n.smooth ? { smooth: true as const } : {}),
    })),
  }))
  const deg = objectRotation(o)
  const box = fitPathBox(page, deg, [o.x + o.w / 2, o.y + o.h / 2])
  if (!box) return null

  const lineLike = o.type === 'arrow' || isStrokeOnlyShape(o.shape)
  const closedShape = o.type === 'shape' && !lineLike
  // 路径的缺省拐角是圆角；原来的实际拐角不是圆角时写明（直线 / 大括号的缺省是尖角）
  const join = o.lineJoin ?? (o.type === 'arrow' ? 'round' : defaultLineJoin(o.shape))
  const heads = o.type === 'arrow' ? arrowHeads(o) : null
  const fill = closedShape ? (o as ShapeObject).fill : null
  const out: PathObject = {
    id: o.id,
    type: 'path',
    x: box.x,
    y: box.y,
    w: box.w,
    h: box.h,
    ...(o.locked ? { locked: o.locked } : {}),
    ...(o.hidden ? { hidden: o.hidden } : {}),
    ...(o.name != null ? { name: o.name } : {}),
    ...(o.groupId != null ? { groupId: o.groupId } : {}),
    // 嵌套组的整条链与剪切蒙版的身份一起带过去（ADR 0188）：只带 groupId 会把对象从外层组里摘出来
    ...(o.groupPath ? { groupPath: [...o.groupPath] } : {}),
    ...(o.clipMask ? { clipMask: true as const } : {}),
    ...(o.layoutPinned ? { layoutPinned: o.layoutPinned } : {}),
    ...(o.rotationDeg ? { rotationDeg: o.rotationDeg } : {}),
    subpaths: box.subpaths,
    strokePt: o.strokePt,
    color: o.color,
    fill,
    ...(fill && o.type === 'shape' && o.fillOpacity != null ? { fillOpacity: o.fillOpacity } : {}),
    ...(closedShape && !shapeStrokeOn(o as ShapeObject) ? { stroke: false } : {}),
    ...(o.strokeOpacity != null ? { strokeOpacity: o.strokeOpacity } : {}),
    ...(o.dash ? { dash: o.dash } : {}),
    ...(o.dashPattern ? { dashPattern: o.dashPattern } : {}),
    ...(o.miterLimit != null ? { miterLimit: o.miterLimit } : {}),
    ...(o.lineCap ? { lineCap: o.lineCap } : {}),
    ...(join !== PATH_DEFAULT_JOIN ? { lineJoin: join } : {}),
    ...(heads && heads.start !== 'none' ? { headStart: heads.start } : {}),
    ...(heads && heads.end !== 'none' ? { headEnd: heads.end } : {}),
    ...(o.type === 'arrow' && o.headSizeStart ? { headSizeStart: o.headSizeStart } : {}),
    ...(o.type === 'arrow' && o.headSizeEnd ? { headSizeEnd: o.headSizeEnd } : {}),
    // 渐变（ADR 0186）：填充渐变跟着填充走；比例坐标从形状的框换到路径的框（下面 remapGradients）
    ...(fill && o.type === 'shape' && o.fillGradient ? { fillGradient: o.fillGradient } : {}),
    ...(o.type === 'shape' && o.strokeGradient ? { strokeGradient: o.strokeGradient } : {}),
  }
  return remapGradients(out, o, out, deg)
}
