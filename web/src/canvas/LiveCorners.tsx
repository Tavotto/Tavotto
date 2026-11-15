import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg } from '@/i18n'
import {
  cornerWidget,
  pathCorners,
  radiusFromPointer,
  rectRadiusFromPointer,
  rectWidgets,
  roundCorners,
  type CornerInfo,
  type RectCorner,
} from '@/lib/liveCorners'
import { parseAnchorKey, pathFromMm, pathToMm, type AnchorRef } from '@/lib/pathEdit'
import { rotateAbout } from '@/lib/pathGeom'
import { useDocumentStore } from '@/store/documentStore'
import { canEditAnchors, useActivePathEdit, usePathEditStore } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, mmToPx, mmToViewX, mmToViewY, pxToMm, type ViewTransform } from '@/store/viewportStore'
import { objectRotation, type CanvasObject, type PathObject, type ShapeObject } from '@/types/document'
import { trackPointer } from './interactions'

/**
 * 实时圆角的圆点（ADR 0185 M3 第二批，对标 Illustrator 的 live corners）：单选矩形或自由路径、用选择工具时，
 * 每个能圆的角里面一个小圆点（在圆角的圆心上），往里拖圆角变大。
 * - 矩形：拖任何一个都改形状本来就有的「圆角」（四个角一起）；
 * - 路径：编辑锚点时只给选中的那几个角，拖一个就一起圆；没在编辑锚点时每个角都有，拖一个就把**全部**能圆的角一起圆
 *   （Illustrator 选择工具下的行为）。圆出来的是普通锚点 + 圆弧（`lib/liveCorners.roundCorners`），仍是 `path`。
 * 一次拖动一条历史；对象在屏幕上太小（短边 < 32 px）时不出圆点，免得和缩放手柄挤在一起。钢笔时不出现。
 */

const SEL = 'var(--color-sel)'
const R = 4
/** 圆点离角至少这么远（屏幕 px），否则和角上的缩放手柄叠在一起 */
const MIN_PX = 12
const MIN_SIZE_PX = 32

type XY = [number, number]
const center = (o: { x: number; y: number; w: number; h: number }): XY => [o.x + o.w / 2, o.y + o.h / 2]

function useTarget(): { kind: 'rect'; o: ShapeObject } | { kind: 'path'; o: PathObject; only: AnchorRef[] | null } | null {
  const ids = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const tool = useUiStore((s) => s.tool)
  const ed = useActivePathEdit()
  if (ids.length !== 1 || tool !== 'select') return null
  const o: CanvasObject | undefined = objects.find((x) => x.id === ids[0])
  if (!o || o.locked || o.hidden) return null
  if (o.type === 'shape' && o.shape === 'rect') return { kind: 'rect', o }
  if (canEditAnchors(o)) {
    const only = ed && ed.path.id === o.id ? ed.anchors.map(parseAnchorKey).filter((a): a is AnchorRef => a != null) : null
    return { kind: 'path', o, only }
  }
  return null
}

function startRect(e: ReactPointerEvent, o: ShapeObject, corner: RectCorner) {
  e.stopPropagation()
  e.preventDefault()
  if (e.button !== 0) return
  const store = useDocumentStore.getState()
  useInteractionStore.getState().begin('corner')
  store.beginTxn(msg('history.setCornerRadius', undefined, 'inspector'))
  const rot = objectRotation(o)
  trackPointer(e, {
    threshold: 1,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      const q = rotateAbout([p.x, p.y], center(o), -rot)
      const r = rectRadiusFromPointer(o, corner, q)
      store.txnUpdate((d) => {
        const t = d.objects.find((x) => x.id === o.id)
        if (!t || t.type !== 'shape') return
        if (r > 0) t.cornerRadius = r
        else delete t.cornerRadius
      })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

function startPath(e: ReactPointerEvent, o: PathObject, grabbed: CornerInfo, refs: AnchorRef[]) {
  e.stopPropagation()
  e.preventDefault()
  if (e.button !== 0) return
  const store = useDocumentStore.getState()
  const base = pathToMm(o)
  const rot = objectRotation(o)
  useInteractionStore.getState().begin('corner')
  store.beginTxn(msg('history.roundCorners', undefined, 'workspace'))
  trackPointer(e, {
    threshold: 1,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      const q = rotateAbout([p.x, p.y], center(o), -rot)
      const next = pathFromMm(o, roundCorners(base, refs, radiusFromPointer(grabbed, q)))
      if (!next) return
      store.txnUpdate((d) => {
        const i = d.objects.findIndex((x) => x.id === o.id)
        if (i >= 0) d.objects[i] = next
      })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      store.endTxn({ discard: !moved || end.cancelled })
      // 圆过的角换成了两个锚点，原来的锚点下标不再对得上：清掉锚点选择
      if (moved && !end.cancelled) usePathEditStore.getState().setAnchors([])
    },
  })
}

export function LiveCorners({ t }: { t: ViewTransform }) {
  const target = useTarget()
  if (!target) return null
  const o = target.o
  if (Math.min(mmToPx(o.w, t), mmToPx(o.h, t)) < MIN_SIZE_PX) return null
  const rot = objectRotation(o)
  const cx = mmToViewX(o.x + o.w / 2, t)
  const cy = mmToViewY(o.y + o.h / 2, t)
  const minMm = pxToMm(MIN_PX, t)
  const dot = (key: string, at: XY, onDown: (e: ReactPointerEvent) => void, attrs: Record<string, string>) => (
    <circle
      key={key}
      {...attrs}
      cx={mmToViewX(at[0], t)}
      cy={mmToViewY(at[1], t)}
      r={R}
      stroke={SEL}
      strokeWidth={1.5}
      style={{ fill: 'var(--handle-fill)', pointerEvents: 'all', cursor: 'default' }}
      onPointerDown={onDown}
    />
  )
  let dots: React.ReactNode[]
  if (target.kind === 'rect') {
    dots = rectWidgets(target.o, minMm).map((w) =>
      dot(w.corner, w.at, (e) => startRect(e, target.o, w.corner), { 'data-live-corner': w.corner }),
    )
  } else {
    const all = pathCorners(pathToMm(target.o))
    const only = target.only
    const shown = only ? all.filter((c) => only.some((a) => a.sp === c.ref.sp && a.i === c.ref.i)) : all
    const refs = shown.map((c) => c.ref)
    dots = shown.map((c) =>
      dot(`${c.ref.sp}:${c.ref.i}`, cornerWidget(c, 0, minMm), (e) => startPath(e, target.o, c, refs), {
        'data-live-corner': `${c.ref.sp}:${c.ref.i}`,
      }),
    )
  }
  if (!dots.length) return null
  return (
    <g data-live-corners={o.id} transform={rot ? `rotate(${rot} ${cx} ${cy})` : undefined}>
      {dots}
    </g>
  )
}
