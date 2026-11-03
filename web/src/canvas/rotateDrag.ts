import type { PointerEvent as ReactPointerEvent } from 'react'

import { msg, type UiMessage } from '@/i18n'
import { normalizeRotationDeg, pointerBearingDeg, snapRotationDeg } from '@/lib/geometry'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { clientToMm } from '@/store/viewportStore'
import { objectRotation } from '@/types/document'
import { fitBeforeRotate } from './textFit'
import { trackPointer } from './interactions'

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')

/** ⇧ 吸附的档位（度） */
export const ROTATE_SNAP_STEP = 15

/**
 * 旋转柄拖动（文字标注的任意角度，ADR 0163）。
 *
 * 角度 = 手势开始时的 `rotationDeg` + 指针相对对象中心的方位角增量，所以按下的那一刻
 * 对象纹丝不动（不会跳到指针所在的方向）。中心取**未旋转框**的中心——旋转本来就绕它，
 * 与 ObjectView 的 CSS rotate、导出的 `rotate_about` 同一个点；x/y/w/h 在手势里一个字都不改。
 * ⇧ = 每 15° 吸附（吸的是最终角度，不是增量）。落盘经 `normalizeRotationDeg`：(-180, 180]，
 * 回到 0 删字段。整个手势一次事务、一条历史。
 */
export function startRotateDrag(e: ReactPointerEvent, objectId: string) {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const target = store.doc.objects.find((o) => o.id === objectId)
  if (!target || target.locked || target.type === 'panel') return
  // 单行文字先收到贴字宽（字不动，只去多余空白），再绕收后的框中心转；收框在手势的同一事务里落，
  // 没真正转起来（回到 0 / 取消）就连收框一起不留
  const fit = fitBeforeRotate(target)
  const box0 = { x: target.x, w: target.w }
  const boxX = fit ? fit.x : target.x
  const boxW = fit ? fit.w : target.w
  const cx = boxX + boxW / 2
  const cy = target.y + target.h / 2
  const start = clientToMm(e.clientX, e.clientY)
  const bearing0 = pointerBearingDeg(cx, cy, start.x, start.y)
  const rot0 = objectRotation(target)
  const interaction = () => useInteractionStore.getState()

  interaction().begin('rotate')
  store.beginTxn(hist('rotateObjects'))
  trackPointer(e, {
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      let deg = rot0 + (pointerBearingDeg(cx, cy, p.x, p.y) - bearing0)
      if (ev.shiftKey) deg = snapRotationDeg(deg, ROTATE_SNAP_STEP)
      const next = normalizeRotationDeg(deg)
      store.txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === objectId)
        if (!o) return
        if (next) o.rotationDeg = next
        else delete o.rotationDeg
        o.x = next ? boxX : box0.x
        o.w = next ? boxW : box0.w
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}
