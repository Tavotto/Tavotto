import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg } from '@/i18n'
import { arcPathOf, clampOffset, pointAt } from '@/lib/pathText'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useActivePathEdit } from '@/store/pathEditStore'
import { localToPage, startOffsetAt } from '@/store/pathTextActions'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import type { PathTextObject } from '@/types/document'
import { trackPointer } from './interactions'

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
