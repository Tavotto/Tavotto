import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg } from '@/i18n'
import { arcPathOf, clampOffset, pointAt } from '@/lib/pathText'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useActivePathEdit } from '@/store/pathEditStore'
import { autoOffsetOf, localToPage, resolvedOffset, snapGap, startOffsetAt } from '@/store/pathTextActions'
import { MM_PER_PT } from '@/lib/units'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import type { PathTextObject } from '@/types/document'
import { trackPointer } from './interactions'
import { usePathTextOutline } from './PathTextView'

/**
 * 路径文字（ADR 0190）选中时的画布柄：路径本身（不画、只在选中时描一道细线，看得出字排在哪）和起点柄（起点处横跨路径的
 * 一道短竖线 + 方块，Illustrator 的「起点括号」）。拖起点柄 = 沿路径挪字，松手一条历史；编辑锚点与钢笔时不出现。
 */

const SEL = 'var(--color-sel)'
const BAR = 9

function dragStart(e: ReactPointerEvent, o: PathTextObject) {
  e.stopPropagation()
  e.preventDefault()
  if (e.button !== 0) return
  const store = useDocumentStore.getState()
  useInteractionStore.getState().begin('pathTextStart')
  store.beginTxn(msg('history.pathTextStart', undefined, 'workspace'))
  trackPointer(e, {
    threshold: 1,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      const off = startOffsetAt(o, [p.x, p.y])
      if (off == null) return
      store.txnUpdate((d) => {
        const x = d.objects.find((q) => q.id === o.id)
        if (!x || x.type !== 'pathText') return
        if (off > 0) x.startOffset = off
        else delete x.startOffset
      })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/**
 * 拖间隙柄（离线）：指针投到起点处字的「上方」那条法线上，量出离线（pt）；⇧ 时离 0（贴线）或自动值 1.5 pt 以内就吸过去
 * （吸到自动 = 删掉字段）。一次拖动一条历史；拖动时 interaction = `pathTextGap`，浮动栏让开、画布字实时跟着动。
 */
function dragGap(e: ReactPointerEvent, o: PathTextObject, start: [number, number], up: [number, number]) {
  e.stopPropagation()
  e.preventDefault()
  if (e.button !== 0) return
  const store = useDocumentStore.getState()
  const auto = autoOffsetOf(o.id)
  useInteractionStore.getState().begin('pathTextGap')
  store.beginTxn(msg('history.pathTextGap', undefined, 'workspace'))
  trackPointer(e, {
    threshold: 1,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      const mm = (p.x - start[0]) * up[0] + (p.y - start[1]) * up[1]
      const v = snapGap(mm / MM_PER_PT, auto, ev.shiftKey)
      store.txnUpdate((d) => {
        const x = d.objects.find((q) => q.id === o.id)
        if (!x || x.type !== 'pathText') return
        if (v === 'auto') delete x.baselineOffsetPt
        else x.baselineOffsetPt = v
      })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

export function PathTextHandles({ t }: { t: ViewTransform }) {
  const ids = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const tool = useUiStore((s) => s.tool)
  const anchorEdit = useActivePathEdit()
  if (ids.length !== 1 || tool !== 'select' || anchorEdit) return null
  const o = objects.find((x) => x.id === ids[0])
  if (!o || o.type !== 'pathText' || o.hidden) return null
  const a = arcPathOf(o, o.w, o.h)
  if (a.pts.length < 2) return null
  const view = (q: [number, number]) => {
    const [px, py] = localToPage(o, q)
    return [mmToViewX(px, t), mmToViewY(py, t)] as const
  }
  const d = a.pts.map((q, i) => `${i ? 'L' : 'M'}${view(q).join(' ')}`).join(' ')
  const total = a.cum[a.cum.length - 1]
  const s = pointAt(a, clampOffset(o.startOffset) * total)
  let bar = null
  if (s) {
    const [sx, sy] = view([s.x, s.y])
    // 切线方向在视图里（旋转过的对象要带上它的角度）
    const [tx, ty] = view([s.x + Math.cos(s.angle), s.y + Math.sin(s.angle)])
    const len = Math.hypot(tx - sx, ty - sy) || 1
    const nx = -(ty - sy) / len
    const ny = (tx - sx) / len
    const gap = <GapHandle o={o} t={t} sx={sx} sy={sy} n={[nx, ny]} startPage={localToPage(o, [s.x, s.y])} />
    bar = (
      <g data-path-text-start="">
        <line x1={sx - nx * BAR} y1={sy - ny * BAR} x2={sx + nx * BAR} y2={sy + ny * BAR} stroke="var(--handle-fill)" strokeWidth={3} style={{ pointerEvents: 'none' }} />
        <line x1={sx - nx * BAR} y1={sy - ny * BAR} x2={sx + nx * BAR} y2={sy + ny * BAR} stroke={SEL} strokeWidth={1.5} style={{ pointerEvents: 'none' }} />
        <rect
          data-path-text-start-handle=""
          x={sx + nx * BAR - 4}
          y={sy + ny * BAR - 4}
          width={8}
          height={8}
          stroke={SEL}
          strokeWidth={1.5}
          style={{ fill: 'var(--handle-fill)', pointerEvents: 'all', cursor: 'move' }}
          onPointerDown={(e) => dragStart(e, o)}
        />
        {gap}
      </g>
    )
  }
  return (
    <g data-path-text-handles={o.id}>
      <path d={d} fill="none" stroke={SEL} strokeWidth={1} strokeDasharray="3 3" style={{ pointerEvents: 'none' }} />
      {bar}
    </g>
  )
}

/**
 * 间隙柄：起点处沿字的上方（−n）离开线「离线」那么远，就是基线所在；一道细虚线连回线上。自动值跟着画布预览的请求回来
 * （同一份缓存），还没回来时先放在线上。
 */
function GapHandle({ o, t, sx, sy, n, startPage }: {
  o: PathTextObject
  t: ViewTransform
  sx: number
  sy: number
  n: [number, number]
  startPage: [number, number]
}) {
  const { outline } = usePathTextOutline(o)
  const off = o.baselineOffsetPt ?? outline?.autoOffsetPt ?? resolvedOffset(o) ?? 0
  const offPx = mmToPx(off * MM_PER_PT, t)
  const gx = sx - n[0] * offPx
  const gy = sy - n[1] * offPx
  return (
    <g data-path-text-gap="">
      <line x1={sx} y1={sy} x2={gx} y2={gy} stroke={SEL} strokeWidth={1} strokeDasharray="2 2" style={{ pointerEvents: 'none' }} />
      <circle
        data-path-text-gap-handle=""
        data-auto={o.baselineOffsetPt == null || undefined}
        cx={gx}
        cy={gy}
        r={4.5}
        stroke={SEL}
        strokeWidth={1.5}
        style={{ fill: 'var(--handle-fill)', pointerEvents: 'all', cursor: 'ns-resize' }}
        onPointerDown={(e) => dragGap(e, o, startPage, [-n[0], -n[1]])}
      />
    </g>
  )
}

