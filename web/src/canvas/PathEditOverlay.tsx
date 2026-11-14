import { anchorKey, pathToMm } from '@/lib/pathEdit'
import { subpathD } from '@/lib/pathGeom'
import { MM_PER_PT } from '@/lib/units'
import { useActivePathEdit } from '@/store/pathEditStore'
import { usePreviewPathDraft } from '@/store/svgPreviewStore'
import { mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import { objectRotation } from '@/types/document'
import { DRAW_COLOR } from './interactions'
import { startAnchorDrag, startHandleDrag } from './pathEditDrag'

/**
 * 自由路径的两层覆盖（ADR 0185 M1），都画在 `OverlaySvg` 的屏幕坐标里：
 * - `PenDraft`：钢笔画到一半的那条（读预览平面 `usePreviewPathDraft`，不是文档），按新对象自己的颜色与线宽画，
 *   外加橡皮筋线、锚点、正在拉的手柄和「再点就闭合」的提示圈；
 * - `AnchorEdit`：直接选择时选中路径的锚点（方块；选中的实心）与选中锚点的手柄（线 + 圆点），可拖。
 */

const SEL = 'var(--color-sel)'
const ANCHOR = 7
const ANCHOR_HIT = 14
const HANDLE_R = 3.5

type XY = readonly [number, number]
const toView = (t: ViewTransform) => (q: XY): [number, number] => [mmToViewX(q[0], t), mmToViewY(q[1], t)]

export function PenDraft({ t }: { t: ViewTransform }) {
  const d = usePreviewPathDraft()
  if (!d || !d.nodes.length) return null
  const v = toView(t)
  const sw = Math.max(mmToPx(MM_PER_PT, t), 0.5) // 新路径默认 1 pt
  // 画过的平滑点都把手柄亮出来（不只最后一个）：拉完第二个点再去点第三个时，还看得出第二个点的切线方向
  const withHandles = d.nodes.filter((n) => n.out[0] !== n.p[0] || n.out[1] !== n.p[1] || n.in[0] !== n.p[0] || n.in[1] !== n.p[1])
  return (
    <g data-pen-draft style={{ pointerEvents: 'none' }}>
      <path d={subpathD(d, v)} fill="none" stroke={DRAW_COLOR} strokeWidth={sw} strokeLinejoin="round" strokeLinecap="round" />
      {d.rubber && (
        <line
          data-pen-rubber
          x1={v(d.rubber[0])[0]}
          y1={v(d.rubber[0])[1]}
          x2={v(d.rubber[1])[0]}
          y2={v(d.rubber[1])[1]}
          stroke={SEL}
          strokeWidth={1}
        />
      )}
      {withHandles.map((n, k) => (
        <g key={k} data-pen-handles>
          {(['in', 'out'] as const).map((w) =>
            n[w][0] === n.p[0] && n[w][1] === n.p[1] ? null : (
              <g key={w}>
                <line x1={v(n.p)[0]} y1={v(n.p)[1]} x2={v(n[w])[0]} y2={v(n[w])[1]} stroke={SEL} strokeWidth={1} />
                <circle cx={v(n[w])[0]} cy={v(n[w])[1]} r={HANDLE_R} fill={SEL} />
              </g>
            ),
          )}
        </g>
      ))}
      {d.nodes.map((n, i) => (
        <rect
          key={i}
          data-pen-anchor={i}
          x={v(n.p)[0] - ANCHOR / 2}
          y={v(n.p)[1] - ANCHOR / 2}
          width={ANCHOR}
          height={ANCHOR}
          stroke={SEL}
          strokeWidth={1}
          style={{ fill: i === d.nodes.length - 1 ? SEL : 'var(--handle-fill)' }}
        />
      ))}
      {d.closeHint && (
        <circle data-pen-close-hint cx={v(d.nodes[0].p)[0]} cy={v(d.nodes[0].p)[1]} r={ANCHOR} fill="none" stroke={SEL} strokeWidth={1.5} />
      )}
    </g>
  )
}

/** 直接选择：锚点与手柄。锚点在对象的未旋转空间里，整组绕框中心转到对象朝向 */
export function AnchorEdit({ t }: { t: ViewTransform }) {
  const ed = useActivePathEdit()
  if (!ed) return null
  const { path, anchors } = ed
  const v = toView(t)
  const subs = pathToMm(path)
  const cx = mmToViewX(path.x + path.w / 2, t)
  const cy = mmToViewY(path.y + path.h / 2, t)
  const rot = objectRotation(path)
  const selected = new Set(anchors)
  return (
    <g data-anchor-edit={path.id} transform={rot ? `rotate(${rot} ${cx} ${cy})` : undefined}>
      {subs.map((sp, si) => (
        <path key={`o${si}`} d={subpathD(sp, v)} fill="none" stroke={SEL} strokeWidth={1} style={{ pointerEvents: 'none' }} />
      ))}
      {subs.flatMap((sp, si) =>
        sp.nodes.map((n, i) => {
          const key = anchorKey({ sp: si, i })
          if (!selected.has(key)) return null
          return (['in', 'out'] as const).map((w) => {
            if (n[w][0] === n.p[0] && n[w][1] === n.p[1]) return null
            const [hx, hy] = v(n[w])
            return (
              <g key={`${key}:${w}`}>
                <line x1={v(n.p)[0]} y1={v(n.p)[1]} x2={hx} y2={hy} stroke={SEL} strokeWidth={1} style={{ pointerEvents: 'none' }} />
                <circle
                  data-path-handle={`${key}:${w}`}
                  cx={hx}
                  cy={hy}
                  r={HANDLE_R}
                  fill={SEL}
                  style={{ pointerEvents: 'all', cursor: 'crosshair' }}
                  onPointerDown={(e) => startHandleDrag(e, path, { sp: si, i }, w)}
                />
              </g>
            )
          })
        }),
      )}
      {subs.flatMap((sp, si) =>
        sp.nodes.map((n, i) => {
          const key = anchorKey({ sp: si, i })
          const [x, y] = v(n.p)
          const on = selected.has(key)
          return (
            <g key={key}>
              <rect
                data-path-anchor={key}
                data-selected={on || undefined}
                x={x - ANCHOR / 2}
                y={y - ANCHOR / 2}
                width={ANCHOR}
                height={ANCHOR}
                stroke={SEL}
                strokeWidth={1}
                style={{ fill: on ? SEL : 'var(--handle-fill)', pointerEvents: 'none' }}
              />
              <rect
                data-path-anchor-hit={key}
                x={x - ANCHOR_HIT / 2}
                y={y - ANCHOR_HIT / 2}
                width={ANCHOR_HIT}
                height={ANCHOR_HIT}
                fill="transparent"
                style={{ pointerEvents: 'all', cursor: 'move' }}
                onPointerDown={(e) => startAnchorDrag(e, path, { sp: si, i })}
              />
            </g>
          )
        }),
      )}
    </g>
  )
}
