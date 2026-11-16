import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg } from '@/i18n'
import { newId } from '@/lib/id'
import { snapXY, toPathSubpaths, type XY } from '@/lib/pathEdit'
import { fitPathBox } from '@/lib/pathGeom'
import { addPoint, isClosed, pencilSubpath, type PencilOptions } from '@/lib/pencil'
import { loadVecops } from '@/lib/vecops/kernel'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { previewPathDraft } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, getTransform, pxToMm, snapTolMm } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { candidatesFor, DRAW_COLOR, trackPointer } from './interactions'

/**
 * 铅笔工具的画布一侧（ADR 0185 M8）：按下拖动画一笔，画的过程只走预览平面（原始轨迹，`previewPathDraft`），松手把这一笔拟合成
 * 贝塞尔（`lib/pencil.pencilSubpath`，内核的「简化」），落成一个普通 `path`——一次 commit、一条历史。
 *
 * - 吸附只在起点：按下那一下吸画布那套候选（对象 / 参考线 / 网格 / 智能参考线），画的过程中不吸、不出参考线；
 * - 终点回到起点附近（`CLOSE_PX` 屏幕像素）自动闭合；
 * - 保真度：拟合容差固定 `FIT_PX` 屏幕像素（按当前缩放换成 mm，缩放不同手感一样）；
 * - 按着 ⌥ 松手 = 不简化，保留原始的点（折线）。
 */

/** 拟合容差、闭合距离、记点间隔（屏幕像素） */
export const FIT_PX = 2
export const CLOSE_PX = 8
const STEP_PX = 0.75

const opts = (): PencilOptions => {
  const t = getTransform()
  return { minStepMm: pxToMm(STEP_PX, t), closeTolMm: pxToMm(CLOSE_PX, t), tolMm: pxToMm(FIT_PX, t) }
}

function preview(pts: readonly XY[]): void {
  previewPathDraft(
    pts.length >= 2
      ? {
          closed: false,
          nodes: pts.map((p) => ({ p, in: p, out: p })),
          rubber: null,
          closeHint: isClosed(pts, opts().closeTolMm),
        }
      : null,
  )
}

/** 起点：吸画布那套候选（与其它绘制工具同一条）；画的过程中不再吸 */
function startPoint(clientX: number, clientY: number): XY {
  const t = getTransform()
  const p = clientToMm(clientX, clientY, t)
  const cands = candidatesFor(new Set())
  if (!cands) return [p.x, p.y]
  const s = snapXY([p.x, p.y], cands, snapTolMm(t))
  return s.p
}

async function commit(pts: readonly XY[], raw: boolean): Promise<string | null> {
  const o = opts()
  const kernel = raw ? null : await loadVecops()
  const sp = pencilSubpath(pts, o, { raw, kernel })
  previewPathDraft(null)
  if (!sp) return null
  const box = fitPathBox(toPathSubpaths([sp]), 0, [0, 0])
  if (!box) return null
  const obj: PathObject = {
    id: newId('p'),
    type: 'path',
    x: box.x,
    y: box.y,
    w: box.w,
    h: box.h,
    subpaths: box.subpaths,
    strokePt: 1,
    color: DRAW_COLOR,
    fill: null,
  }
  useDocumentStore.getState().commit(msg('history.drawPencil', undefined, 'workspace'), (d) => {
    d.objects.push(obj)
  })
  const ui = useUiStore.getState()
  if (ui.elementPanelId) ui.setElementPanel(null)
  useSelectionStore.getState().set([obj.id])
  return obj.id
}

/** 这一笔的拟合（异步：要等内核）；测试用来等它落定 */
let pending: Promise<string | null> | null = null
export const pencilSettled = (): Promise<string | null> => pending ?? Promise.resolve(null)

export function pencilPointerDown(e: ReactPointerEvent): void {
  e.preventDefault()
  let pts: XY[] = [startPoint(e.clientX, e.clientY)]
  useInteractionStore.getState().setSnap([], [])
  // 预取内核：松手时多半已经编译好了
  void loadVecops()
  trackPointer(e, {
    threshold: 0,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      pts = addPoint(pts, [p.x, p.y], opts().minStepMm)
      preview(pts)
    },
    onEnd: (_moved, ev, end) => {
      if (end.cancelled) {
        previewPathDraft(null)
        return
      }
      const p = clientToMm(ev.clientX, ev.clientY)
      pts = addPoint(pts, [p.x, p.y], opts().minStepMm)
      pending = commit(pts, ev.altKey)
    },
  })
}
