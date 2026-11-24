import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { newId } from '@/lib/id'
import {
  anchorSnapLines,
  pathFromMm,
  pathToMm,
  rotatePoint,
  snapXY,
  toPathSubpaths,
  unrotatePoint,
  type MmNode,
  type XY,
} from '@/lib/pathEdit'
import { fitPathBox } from '@/lib/pathGeom'
import { PEN_IDLE, penPreview, penReduce, type PenEvent, type PenOptions, type PenState } from '@/lib/penTool'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { previewPathDraft } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, getTransform, pxToMm, snapTolMm } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { candidatesFor, DRAW_COLOR, trackPointer } from './interactions'

/**
 * 钢笔工具的画布一侧（ADR 0185 M1）。状态机是 `lib/penTool.penReduce`（纯函数）；这里只做：
 * 指针坐标 → 页面 mm（吸附到画布那套候选与同一条路径的锚点；⇧ 时不吸、由状态机锁 45°）、
 * 把草稿交给预览平面（`svgPreviewStore.previewPathDraft`——不 commit、不进历史）、
 * 把「完成」变成**一次** commit：新画的落成一个新 `path`；续画（点在一条开放路径的端点上接着画）改的是
 * **同一个对象、同一个 id**。
 *
 * 画到一半时登记成「进行中的手势」（`registerGesture`）：撤销、对齐、换画布这类离散动作先调
 * `finishActiveGesture()`，这条路径就先按开放路径落定，不会悬在预览平面上、也不会被并进别的历史。
 * 换掉钢笔工具同样落定。
 *
 * 续画旋转过的路径：状态机在那条路径**未旋转的编辑空间**里跑（`unrotatePoint`），预览再转回页面（`rotatePoint`），
 * 落盘时由 `pathFromMm` 收框并补回旋转中心的挪动——与锚点编辑同一套坐标。
 */

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')

/** 按下后挪 3 屏幕像素才算拖；离起点 8 屏幕像素以内算点到起点（也是续画认端点的距离） */
const DRAG_PX = 3
const CLOSE_PX = 8

let state: PenState = PEN_IDLE
let unregister: (() => void) | null = null
/**
 * 续画：接的是哪条路径的哪条子路径、从哪一端（从起点接 = 把子路径倒过来画，落盘时再倒回去）；
 * `start` 是续画开始时状态机拿到的那份点（判「什么都没改」用）
 */
let cont: { base: PathObject; sp: number; fromStart: boolean; start: MmNode[] } | null = null

const opts = (): PenOptions => {
  const t = getTransform()
  return { dragTolMm: pxToMm(DRAG_PX, t), closeTolMm: pxToMm(CLOSE_PX, t) }
}

const toPage = (q: readonly [number, number]): XY => (cont ? rotatePoint(cont.base, [q[0], q[1]]) : [q[0], q[1]])

/** 指针 → 页面 mm，吸附（⇧ 时不吸：方向锁优先），再换到状态机的坐标系（续画旋转路径时是它的未旋转空间） */
function at(clientX: number, clientY: number, shift: boolean): XY {
  const t = getTransform()
  const p = clientToMm(clientX, clientY, t)
  let page: XY = [p.x, p.y]
  const cands = shift ? null : candidatesFor(new Set(cont ? [cont.base.id] : []))
  if (cands) {
    const own = cont ? anchorSnapLines(cont.base) : { xs: [], ys: [] }
    const drawn = state.kind === 'drawing' ? state.nodes.map((n) => toPage(n.p)) : []
    const s = snapXY(
      page,
      {
        xs: [...cands.xs, ...own.xs, ...drawn.map((q) => q[0])],
        ys: [...cands.ys, ...own.ys, ...drawn.map((q) => q[1])],
      },
      snapTolMm(t),
    )
    page = s.p
    useInteractionStore.getState().setSnap(s.xs, s.ys)
  } else {
    useInteractionStore.getState().setSnap([], [])
  }
  return cont ? unrotatePoint(cont.base, page) : page
}

function publish(): void {
  const pv = penPreview(state, opts())
  const n = (m: MmNode) => ({ p: toPage(m.p), in: toPage(m.in), out: toPage(m.out) })
  previewPathDraft(
    pv
      ? {
          closed: pv.subpath.closed,
          nodes: pv.subpath.nodes.map(n),
          rubber: pv.rubber ? [toPage(pv.rubber[0]), toPage(pv.rubber[1])] : null,
          closeHint: pv.closeHint,
        }
      : null,
  )
}

const reverse = (nodes: readonly MmNode[]): MmNode[] =>
  nodes
    .slice()
    .reverse()
    .map((m) => ({ p: m.p, in: m.out, out: m.in, smooth: m.smooth }))

function select(id: string): void {
  const ui = useUiStore.getState()
  // 在图内编辑态画的：新对象属于画布层，先退出图内编辑（与其它绘制工具同一条）
  if (ui.elementPanelId) ui.setElementPanel(null)
  useSelectionStore.getState().set([id])
}

function commit(c: { closed: boolean; nodes: MmNode[] }, from: typeof cont): void {
  if (from) {
    const { base, sp, fromStart, start } = from
    // 续画了个寂寞（点了端点就结束，没加点、没拉手柄、没闭合）：不进历史
    if (!c.closed && JSON.stringify(c.nodes) === JSON.stringify(start)) return
    const subs = pathToMm(base)
    subs[sp] = { closed: c.closed, nodes: fromStart ? reverse(c.nodes) : c.nodes }
    const next = pathFromMm(base, subs)
    if (!next) return
    useDocumentStore.getState().commit(hist('continuePath'), (d) => {
      const i = d.objects.findIndex((o) => o.id === base.id)
      if (i >= 0) d.objects[i] = next
    })
    select(base.id)
    return
  }
  const box = fitPathBox(toPathSubpaths([c]), 0, [0, 0])
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
  select(obj.id)
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
  const from = cont
  if (state.kind === 'idle') {
    cont = null
    useInteractionStore.getState().setSnap([], [])
  }
  publish()
  if (r.commit) commit(r.commit, from)
}

/**
 * 续画的目标：钢笔在 idle 时按下的位置离某条开放路径（看得见、没锁）的端点不到 `CLOSE_PX`——从那个端点接着画。
 * 选区里的路径优先，其次按图层从上往下。
 */
function resumeTarget(clientX: number, clientY: number): { base: PathObject; sp: number; fromStart: boolean } | null {
  const t = getTransform()
  const p = clientToMm(clientX, clientY, t)
  const tol = pxToMm(CLOSE_PX, t)
  const objects = useDocumentStore.getState().doc.objects
  const sel = new Set(useSelectionStore.getState().ids)
  const order = [...objects].reverse().sort((a, b) => Number(sel.has(b.id)) - Number(sel.has(a.id)))
  for (const o of order) {
    if (o.type !== 'path' || o.hidden || o.locked) continue
    const subs = pathToMm(o)
    for (let si = 0; si < subs.length; si++) {
      const sp = subs[si]
      if (sp.closed || sp.nodes.length < 2) continue
      for (const fromStart of [false, true]) {
        const n = sp.nodes[fromStart ? 0 : sp.nodes.length - 1]
        const [x, y] = rotatePoint(o, n.p)
        if (Math.hypot(x - p.x, y - p.y) <= tol) return { base: o, sp: si, fromStart }
      }
    }
  }
  return null
}

/** 钢笔工具下在画布上按下：加点（或点到起点闭合、点到开放路径的端点续画）；按着拖 = 拉手柄（⌥ 拆开，⇧ 锁 45°，⌘ / Ctrl 只折不弯） */
export function penPointerDown(e: ReactPointerEvent): void {
  e.preventDefault()
  const target = state.kind === 'idle' ? resumeTarget(e.clientX, e.clientY) : null
  if (target) {
    const nodes = pathToMm(target.base)[target.sp].nodes
    const start = target.fromStart ? reverse(nodes) : nodes
    cont = { ...target, start }
    apply({ type: 'resume', nodes: start })
  } else {
    apply({ type: 'down', at: at(e.clientX, e.clientY, e.shiftKey), shift: e.shiftKey })
  }
  trackPointer(e, {
    threshold: 0,
    onMove: (ev) => apply({ type: 'move', at: at(ev.clientX, ev.clientY, ev.shiftKey), alt: ev.altKey, shift: ev.shiftKey, mod: ev.metaKey || ev.ctrlKey }),
    // 系统作废了这一下（pointercancel）：当作松手，点已经落下的留着，不丢整条
    onEnd: (_moved, ev) => apply({ type: 'up', at: at(ev.clientX, ev.clientY, ev.shiftKey) }),
  })
}

/** 两次点击之间的指针移动：橡皮筋预览 */
export function penHover(clientX: number, clientY: number, shift = false): void {
  if (state.kind !== 'drawing' || state.press) return
  apply({ type: 'move', at: at(clientX, clientY, shift), shift })
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
