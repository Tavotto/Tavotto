import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { newId } from '@/lib/id'
import { toPathSubpaths } from '@/lib/pathEdit'
import { fitPathBox } from '@/lib/pathGeom'
import { PEN_IDLE, penPreview, penReduce, type PenEvent, type PenOptions, type PenState } from '@/lib/penTool'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { useSelectionStore } from '@/store/selectionStore'
import { previewPathDraft } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, getTransform, pxToMm } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { DRAW_COLOR, trackPointer } from './interactions'

/**
 * 钢笔工具的画布一侧（ADR 0185 M1）。状态机是 `lib/penTool.penReduce`（纯函数）；这里只做：
 * 指针坐标 → 页面 mm、把草稿交给预览平面（`svgPreviewStore.previewPathDraft`——不 commit、不进历史）、
 * 把「完成」变成**一次** commit 落成一个 `path` 对象。
 *
 * 画到一半时登记成「进行中的手势」（`registerGesture`）：撤销、对齐、换画布这类离散动作先调
 * `finishActiveGesture()`，这条路径就先按开放路径落定，不会悬在预览平面上、也不会被并进别的历史。
 * 换掉钢笔工具同样落定。
 */

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')

/** 按下后挪 3 屏幕像素才算拖；离起点 8 屏幕像素以内算点到起点 */
const DRAG_PX = 3
const CLOSE_PX = 8

let state: PenState = PEN_IDLE
let unregister: (() => void) | null = null

const opts = (): PenOptions => {
  const t = getTransform()
  return { dragTolMm: pxToMm(DRAG_PX, t), closeTolMm: pxToMm(CLOSE_PX, t) }
}

const mm = (clientX: number, clientY: number): [number, number] => {
  const p = clientToMm(clientX, clientY)
  return [p.x, p.y]
}

function publish(): void {
  const pv = penPreview(state, opts())
  previewPathDraft(
    pv ? { closed: pv.subpath.closed, nodes: pv.subpath.nodes, rubber: pv.rubber, closeHint: pv.closeHint } : null,
  )
}

function apply(ev: PenEvent): void {
  const r = penReduce(state, ev, opts())
  state = r.state
  if (state.kind === 'drawing' && !unregister) {
    unregister = registerGesture(() => apply({ type: 'finish' }))
  } else if (state.kind === 'idle' && unregister) {
    const un = unregister
    unregister = null
    un()
  }
  publish()
  // 完成的那一条 → 一个 `path` 对象，一次 commit；包围盒按曲线实际范围（`fitPathBox`）
  if (r.commit) {
    const box = fitPathBox(toPathSubpaths([{ closed: r.commit.closed, nodes: r.commit.nodes }]), 0, [0, 0])
    if (!box) return
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
    useDocumentStore.getState().commit(hist('drawPath'), (d) => {
      d.objects.push(obj)
    })
    const ui = useUiStore.getState()
    // 在图内编辑态画的：新对象属于画布层，先退出图内编辑（与其它绘制工具同一条）
    if (ui.elementPanelId) ui.setElementPanel(null)
    useSelectionStore.getState().set([obj.id])
  }
}

/** 钢笔工具下在画布上按下：加点（或点到起点闭合）；按着拖 = 拉手柄（⌥ 拆开） */
export function penPointerDown(e: ReactPointerEvent): void {
  e.preventDefault()
  apply({ type: 'down', at: mm(e.clientX, e.clientY) })
  trackPointer(e, {
    threshold: 0,
    onMove: (ev) => apply({ type: 'move', at: mm(ev.clientX, ev.clientY), alt: ev.altKey }),
    // 系统作废了这一下（pointercancel）：当作松手，点已经落下的留着，不丢整条
    onEnd: (_moved, ev) => apply({ type: 'up', at: mm(ev.clientX, ev.clientY) }),
  })
}

/** 两次点击之间的指针移动：橡皮筋预览 */
export function penHover(clientX: number, clientY: number): void {
  if (state.kind !== 'drawing' || state.press) return
  apply({ type: 'move', at: mm(clientX, clientY) })
}

/** Enter / Esc：结束成开放路径（不足两个点什么都不留）。没在画 = false（按键留给别人） */
export function penFinish(): boolean {
  if (state.kind === 'idle') return false
  apply({ type: 'finish' })
  return true
}

/** 此刻在不在画 */
export const penDrawing = (): boolean => state.kind === 'drawing'

/** 测试与切项目用：丢掉画到一半的（不落对象） */
export function resetPen(): void {
  apply({ type: 'cancel' })
}

// 换掉钢笔工具 = 把画到一半的那条按开放路径落定
useUiStore.subscribe((s, prev) => {
  if (prev.tool === 'pen' && s.tool !== 'pen') penFinish()
})
