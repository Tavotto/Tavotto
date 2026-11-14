import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import {
  anchorKey,
  moveAnchors,
  moveHandle,
  parseAnchorKey,
  pathFromMm,
  pathToMm,
  rotatePoint,
  unrotateDelta,
  type AnchorRef,
  type MmSubpath,
} from '@/lib/pathEdit'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { activePathEdit, usePathEditStore } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { clientToMm, getTransform, pxToMm } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { trackPointer } from './interactions'

/**
 * 直接选择（锚点编辑）的指针手势（ADR 0185 M1）：拖锚点、拖手柄、框选锚点。
 *
 * 拖动走 `beginTxn / txnUpdate / endTxn`——一次拖动一条历史；没动 / 被取消就丢掉。每一帧都从**按下那一刻的对象**
 * 现算（`lib/pathEdit`），包围盒在同一次更新里按曲线实际范围重算、比例坐标换算回去，画面上没拖的锚点逐点不动。
 * 指针位移先转回对象的未旋转空间再用（旋转过的路径拖起来跟着指针走）。
 */

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')
const interaction = () => useInteractionStore.getState()

function dragEdit(
  e: ReactPointerEvent,
  base: PathObject,
  label: UiMessage,
  edit: (subs: MmSubpath[], d: [number, number], ev: PointerEvent) => MmSubpath[],
): void {
  const store = useDocumentStore.getState()
  const baseMm = pathToMm(base)
  interaction().begin('anchor')
  store.beginTxn(label)
  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      const d = unrotateDelta(base, [pxToMm(dxPx, t), pxToMm(dyPx, t)])
      const next = pathFromMm(base, edit(baseMm, d, ev))
      if (!next) return
      store.txnUpdate((doc) => {
        const i = doc.objects.findIndex((o) => o.id === base.id)
        if (i >= 0) doc.objects[i] = next
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/** 按在锚点上：⇧ = 加选 / 减选（不拖）；否则没选中就只选它，然后拖着所有选中的锚点走 */
export function startAnchorDrag(e: ReactPointerEvent, path: PathObject, ref: AnchorRef): void {
  e.stopPropagation()
  if (e.button !== 0) return
  const pe = usePathEditStore.getState()
  const key = anchorKey(ref)
  if (e.shiftKey) {
    pe.setAnchors(pe.anchors.includes(key) ? pe.anchors.filter((k) => k !== key) : [...pe.anchors, key])
    return
  }
  const keys = pe.anchors.includes(key) ? pe.anchors : [key]
  pe.setAnchors(keys)
  const refs = keys.map(parseAnchorKey).filter((a): a is AnchorRef => a != null)
  dragEdit(e, path, hist('moveAnchors'), (subs, d) => moveAnchors(subs, refs, d))
}

/** 按在手柄上：拖它；平滑点另一侧保持共线，按着 ⌥ 拆开（变尖角） */
export function startHandleDrag(e: ReactPointerEvent, path: PathObject, ref: AnchorRef, which: 'in' | 'out'): void {
  e.stopPropagation()
  if (e.button !== 0) return
  const start = pathToMm(path)[ref.sp]?.nodes[ref.i]?.[which]
  if (!start) return
  dragEdit(e, path, hist('moveHandle'), (subs, d, ev) =>
    moveHandle(subs, ref, which, [start[0] + d[0], start[1] + d[1]], ev.altKey),
  )
}

/**
 * 锚点编辑里在空白处按下：框选锚点（⇧ 加选）。没拖就是「点空白」——退出锚点编辑并清掉选区（与平时点空白同一个结果）。
 * 框是页面上的矩形；旋转过的路径按锚点在页面上的位置判。
 */
export function startAnchorMarquee(e: ReactPointerEvent): void {
  const ed = activePathEdit()
  if (!ed) return
  const path = ed.path
  const additive = e.shiftKey
  const base = additive ? ed.anchors : []
  const start = clientToMm(e.clientX, e.clientY)
  const subs = pathToMm(path)
  interaction().begin('marquee')
  trackPointer(e, {
    onMove: (ev) => {
      const cur = clientToMm(ev.clientX, ev.clientY)
      const r = { x: Math.min(start.x, cur.x), y: Math.min(start.y, cur.y), w: Math.abs(cur.x - start.x), h: Math.abs(cur.y - start.y) }
      interaction().setMarquee(r)
      const hit: string[] = []
      subs.forEach((sp, si) =>
        sp.nodes.forEach((n, i) => {
          const [x, y] = rotatePoint(path, n.p)
          if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) hit.push(anchorKey({ sp: si, i }))
        }),
      )
      usePathEditStore.getState().setAnchors([...base, ...hit])
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      if (!moved && !end.cancelled) {
        usePathEditStore.getState().exit()
        useSelectionStore.getState().clear()
      }
    },
  })
}
