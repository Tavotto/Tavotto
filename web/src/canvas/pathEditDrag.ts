import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import {
  anchorKey,
  anchorSnapLines,
  lock45,
  moveAnchors,
  moveHandle,
  parseAnchorKey,
  pathFromMm,
  pathToMm,
  rotatePoint,
  snapXY,
  unrotateDelta,
  type AnchorRef,
  type MmSubpath,
  type XY,
} from '@/lib/pathEdit'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { activePathEdit, usePathEditStore } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { clientToMm, getTransform, pxToMm, snapTolMm } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { candidatesFor, trackPointer } from './interactions'

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
  /** `page` = 指针在页面上走了多少（mm，未换到对象的未旋转空间）；回编辑后的子路径 */
  edit: (subs: MmSubpath[], page: XY, ev: PointerEvent) => MmSubpath[],
): void {
  const store = useDocumentStore.getState()
  const baseMm = pathToMm(base)
  interaction().begin('anchor')
  store.beginTxn(label)
  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      const next = pathFromMm(base, edit(baseMm, [pxToMm(dxPx, t), pxToMm(dyPx, t)], ev))
      if (!next) return
      store.txnUpdate((doc) => {
        const i = doc.objects.findIndex((o) => o.id === base.id)
        if (i >= 0) doc.objects[i] = next
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end() // 连同吸附线一起收掉
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
  // 按住的那个锚点在页面上的起点：⇧ 锁 45° 与吸附都按它算，其余选中的锚点跟着同一段位移走
  const p0 = rotatePoint(path, pathToMm(path)[ref.sp].nodes[ref.i].p)
  // 吸附候选：画布那一套（排除这条路径自己——它的框正在变）+ 这条路径上没在拖的锚点
  const cands = candidatesFor(new Set([path.id]))
  const own = anchorSnapLines(path, new Set(keys))
  const lines = cands ? { xs: [...cands.xs, ...own.xs], ys: [...cands.ys, ...own.ys] } : null
  dragEdit(e, path, hist('moveAnchors'), (subs, page, ev) => {
    let target: XY = [p0[0] + page[0], p0[1] + page[1]]
    if (ev.shiftKey) {
      target = lock45(p0, target)
      interaction().setSnap([], [])
    } else {
      const s = snapXY(target, lines, snapTolMm(getTransform()))
      target = s.p
      interaction().setSnap(s.xs, s.ys)
    }
    return moveAnchors(subs, refs, unrotateDelta(path, [target[0] - p0[0], target[1] - p0[1]]))
  })
}

/** 按在手柄上：拖它；平滑点另一侧保持共线，按着 ⌥ 拆开（变尖角） */
export function startHandleDrag(e: ReactPointerEvent, path: PathObject, ref: AnchorRef, which: 'in' | 'out'): void {
  e.stopPropagation()
  if (e.button !== 0) return
  const start = pathToMm(path)[ref.sp]?.nodes[ref.i]?.[which]
  if (!start) return
  // ⇧：手柄方向相对锚点锁在 45° 的倍数上（在页面上锁，再换回对象的未旋转空间）
  const anchorPage = rotatePoint(path, pathToMm(path)[ref.sp].nodes[ref.i].p)
  const h0 = rotatePoint(path, start)
  dragEdit(e, path, hist('moveHandle'), (subs, page, ev) => {
    let target: XY = [h0[0] + page[0], h0[1] + page[1]]
    if (ev.shiftKey) target = lock45(anchorPage, target)
    const d = unrotateDelta(path, [target[0] - h0[0], target[1] - h0[1]])
    return moveHandle(subs, ref, which, [start[0] + d[0], start[1] + d[1]], ev.altKey)
  })
}

/**
 * 锚点编辑里在空白处按下：框选锚点（⇧ 加选）。框只画细线、不铺底（`AnchorMarquee`），拖的过程中被框住的锚点当场变实心，
 * 松手框就没了（`interaction().end()` 清掉它），只剩选中的锚点与手柄。没拖就是「点空白」——退出锚点编辑并清掉选区（与平时点空白同一个结果）。
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
      interaction().setAnchorMarquee(r)
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
