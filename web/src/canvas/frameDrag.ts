import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import {
  effectiveFramePad,
  frameCanvasBox,
  inkInsetMm,
  isZeroPad,
  planFigureCorner,
  planPadEdge,
  type FrameCorner,
} from '@/lib/framePad'
import { columnName, resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import type { PageEdge } from '@/lib/pageEdgeResize'
import { setFigureFrame } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { frameCornerDragStatus, framePadDragStatus } from '@/lib/dragStatus'
import { useInteractionStore } from '@/store/interactionStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { getTransform, pxToMm, useViewportStore } from '@/store/viewportStore'
import { holdPlaceholder } from './figureEdgeDrag'
import { trackPointer } from './interactions'

/**
 * 单图编辑拖画布的边与角（ADR 0161）。
 *
 * - **边 = 留白**：图不动，那一边加 / 收留白（`doc.framePad`，导出 mm）。停靠点：图边 → 内容边缘（紧凑）；
 *   越过内容边缘往里要按住 ⌥。整次拖动一个事务、一条历史；留白不碰渲染，画面即时变。
 * - **角 = 图幅**：等比改 `figure.size_mm`（⇧ 自由比例、⌥ 关栏宽吸附），对角不动；拖动中只放虚线预览框，
 *   松手 `setFigureFrame` 一次（一条历史 + 一次渲染），新图回来前只留虚线框（与拖边改图幅同一个占位）。
 * - 两种拖动都每帧写 `interaction.dragStatus`（`lib/dragStatus` 的 `framePadDragStatus` / `frameCornerDragStatus`）：
 *   拖动中的状态胶囊（`DragStatusBar`）贴着画布框说这一下的含义，只读不判。
 * - 权威只认精确 manifest（`figureFrameFacts`）：没有就不起手势。
 */

const HIST_PAD: UiMessage = msg('history.setFramePad', undefined, 'workspace')

function columnsNow(profile: Parameters<typeof resolvePageColumns>[0]) {
  const ps = useProfileStore.getState()
  const r = resolvePageColumns(profile, toCatalog(ps.specs), ps.loaded)
  return { columns: r.ready ? r.columns : ([] as PageColumn[]), specLabel: r.specLabel }
}
function columnLabel(specLabel: string, c: PageColumn): string {
  const name = columnName(c)
  return specLabel ? `${specLabel} ${name}` : name
}

export function startFramePadEdge(e: ReactPointerEvent, edge: PageEdge, panelId: string) {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const store = useDocumentStore.getState()
  const d0 = store.doc
  const panel = d0.objects.find((o) => o.id === panelId)
  if (!panel || panel.type !== 'panel' || panel.locked) return
  const manifest = exactPanelManifest(useRenderStore.getState(), panel)
  const facts = figureFrameFacts(panel, manifest)
  if (!facts) return
  const inset = inkInsetMm(manifest, facts.sizeMm)
  const pad0 = effectiveFramePad(d0, inset)
  const tight0 = (d0.framePadTight ?? []).filter((s) => s !== edge)
  const k = panel.w / facts.sizeMm[0]
  const box0 = { x: panel.x, y: panel.y, w: panel.w, h: panel.h }
  const view0 = useViewportStore.getState().freezeView()
  const t0 = getTransform()
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()
  const horizontal = edge === 'l' || edge === 'r'
  const sign = edge === 'r' || edge === 'b' ? 1 : -1

  const ix = useInteractionStore.getState()
  ix.begin('framepad')
  ix.setPageEdge({ edge, snapLabel: null, rolls: false })
  store.beginTxn(HIST_PAD)
  let prevSnap: string | null = null
  let prevState: string | null = null

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const { columns, specLabel } = columnsNow(d0.profile)
      const plan = planPadEdge({
        edge,
        pad0,
        deltaOut: (sign * pxToMm(horizontal ? dxPx : dyPx, t0)) / k,
        sizeMm: facts.sizeMm,
        inset,
        columns,
        allowCrop: ev.altKey,
      })
      const pad = { ...pad0, [edge]: plan.value }
      const tight = plan.tight ? [...tight0, edge] : tight0
      store.txnUpdate((d) => {
        if (isZeroPad(pad) && !tight.length) {
          delete d.framePad
          delete d.framePadTight
        } else {
          d.framePad = pad
          if (tight.length) d.framePadTight = tight
          else delete d.framePadTight
        }
      })
      // 直接操纵视口：退出「适应画布」（否则画布框一变，适应模式按新框重新取景，图在屏幕上被挪走）
      useViewportStore.getState().setPan(t0.panX, t0.panY)
      const box = frameCanvasBox(box0, pad, k)
      const col = plan.snap ? columnLabel(specLabel, plan.snap) : null
      const now = useInteractionStore.getState()
      const rolls = (plan.snap?.id ?? null) !== prevSnap || plan.state !== prevState
      prevSnap = plan.snap?.id ?? null
      prevState = plan.state
      now.setFigureEdgeBox(box)
      now.setPageEdge({ edge, snapLabel: col, rolls })
      now.setDragStatus(
        framePadDragStatus({
          side: edge,
          state: plan.state,
          mm: Math.abs(plan.value),
          canvas: [facts.sizeMm[0] + pad.l + pad.r, facts.sizeMm[1] + pad.t + pad.b],
          col,
          box,
        }),
      )
    },
    onEnd: (moved, _ev, end) => {
      if (end.cancelled || !moved) {
        store.endTxn({ discard: true })
        if (moved) useViewportStore.getState().restoreFrozenView(view0)
      } else {
        store.endTxn()
      }
      useInteractionStore.getState().end()
    },
  })
}

export function startFigureCornerResize(e: ReactPointerEvent, corner: FrameCorner, panelId: string) {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const d0 = useDocumentStore.getState().doc
  const panel = d0.objects.find((o) => o.id === panelId)
  if (!panel || panel.type !== 'panel' || panel.locked) return
  const manifest = exactPanelManifest(useRenderStore.getState(), panel)
  const facts = figureFrameFacts(panel, manifest)
  if (!facts) return
  const pad = effectiveFramePad(d0, inkInsetMm(manifest, facts.sizeMm))
  const k = panel.w / facts.sizeMm[0]
  const box0 = frameCanvasBox(panel, pad, k)
  const view0 = useViewportStore.getState().freezeView()
  const t0 = getTransform()
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()

  const ix = useInteractionStore.getState()
  ix.begin('figureedge')
  ix.setPageEdge({ edge: corner.includes('l') ? 'l' : 'r', snapLabel: null, rolls: false })
  ix.setFigureEdgeBox({ ...box0 })
  let last: ReturnType<typeof planFigureCorner> | null = null
  let prevSnap: string | null = null

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const { columns, specLabel } = columnsNow(d0.profile)
      const plan = planFigureCorner({
        corner,
        box0,
        sizeMm: facts.sizeMm,
        pad,
        k,
        dx: pxToMm(dxPx, t0),
        dy: pxToMm(dyPx, t0),
        free: ev.shiftKey,
        noSnap: ev.altKey,
        columns,
        limits: facts.limits,
      })
      last = plan
      useViewportStore.getState().setPan(t0.panX, t0.panY)
      const col = plan.snap ? columnLabel(specLabel, plan.snap) : null
      const now = useInteractionStore.getState()
      now.setFigureEdgeBox(plan.box)
      now.setPageEdge({ edge: corner.includes('l') ? 'l' : 'r', snapLabel: col, rolls: (plan.snap?.id ?? null) !== prevSnap })
      prevSnap = plan.snap?.id ?? null
      now.setDragStatus(
        frameCornerDragStatus({ free: ev.shiftKey, from: facts.sizeMm, to: plan.sizeMm, col, box: plan.box }),
      )
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      if (end.cancelled || !moved || !last) {
        if (moved) useViewportStore.getState().restoreFrozenView(view0)
        return
      }
      const [w, h] = last.sizeMm
      if (w === facts.sizeMm[0] && h === facts.sizeMm[1]) return
      const before = useRenderStore.getState().byKey
      setFigureFrame(panelId, last.sizeMm, last.pos)
      holdPlaceholder(panelId, { x: last.pos.x, y: last.pos.y, w: w * k, h: h * k }, last.sizeMm, before)
    },
  })
}
