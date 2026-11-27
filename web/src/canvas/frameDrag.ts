import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import {
  effectiveFramePad,
  frameCanvasBox,
  inkInsetMm,
  isZeroPad,
  planFigureCorner,
  planFigureEdge,
  planPadEdge,
  type FrameCorner,
} from '@/lib/framePad'
import { columnName, resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import type { PageEdge } from '@/lib/pageEdgeResize'
import { setFigureFrame } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { frameCornerDragStatus, frameEdgeResizeDragStatus, framePadDragStatus } from '@/lib/dragStatus'
import { useInteractionStore } from '@/store/interactionStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { getTransform, pxToMm, useViewportStore } from '@/store/viewportStore'
import { holdPlaceholder } from './figureEdgeDrag'
import { trackPointer } from './interactions'

/**
 * 单图编辑拖画布的边与角（ADR 0161；修订 2026-11-27 起与多图排版同一套手势）。
 *
 * - **边 = 这一维的图幅**：改 `figure.size_mm` 被拖的那一维，文字 pt 不变、对边不动；停靠点 起手图幅 / 正方形 / 栏宽
 *   （`planFigureEdge`，⌘ 关吸附）。拖动中只放虚线预览框，松手 `setFigureFrame` 一次。
 * - **⌥ + 边 = 留白 / 裁剪**：图不动，那一边加 / 收留白（`doc.framePad`，导出 mm），停靠点 图边 → 内容边缘，
 *   ⌥ 已表示「要裁」所以越过内容边缘照样放行。整次拖动一个事务、一条历史；留白不碰渲染，画面即时变。
 *   拖动中按 / 松 ⌥ 即时在两种之间切换，松手那一刻的 ⌥ 算数（与排版 `startPanelEdgeCrop` 同一套）。
 * - **角 = 图幅**：等比改 `figure.size_mm`（⌥ 自由比例且吸正方形、⇧ 强制等比、⌘ 关吸附），对角不动；拖动中只放虚线预览框，
 *   松手 `setFigureFrame` 一次（一条历史 + 一次渲染），新图回来前只留虚线框（与拖边改图幅同一个占位）。
 * - 拖动都每帧写 `interaction.dragStatus`（`lib/dragStatus`）：拖动中的状态胶囊（`DragStatusBar`）贴着画布框说这一下的含义，只读不判。
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

export function startFigureEdge(e: ReactPointerEvent, edge: PageEdge, panelId: string) {
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
  const pad0 = effectiveFramePad(d0, inset, facts.sizeMm)
  const storedPad0 = d0.framePad ? { ...d0.framePad } : undefined
  const storedTight0 = d0.framePadTight ? [...d0.framePadTight] : undefined
  const tight0 = (d0.framePadTight ?? []).filter((s) => s !== edge)
  const k = panel.w / facts.sizeMm[0]
  const box0 = { x: panel.x, y: panel.y, w: panel.w, h: panel.h }
  const canvas0 = frameCanvasBox(box0, pad0, k)
  const view0 = useViewportStore.getState().freezeView()
  const t0 = getTransform()
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()
  const horizontal = edge === 'l' || edge === 'r'
  const sign = edge === 'r' || edge === 'b' ? 1 : -1

  const ix = useInteractionStore.getState()
  // 不按 ⌥ = 改图幅（预览框，松手才提交）；按住 ⌥ = 留白 / 裁剪（每帧写文档）。拖动中按 / 松 ⌥ 即时切换，松手落在哪种算哪种
  ix.begin('figureedge')
  ix.setPageEdge({ edge, snapLabel: null, rolls: false })
  // 留白那一路的事务：改图幅时把它还原成起手的样子，松手丢掉
  store.beginTxn(HIST_PAD)
  let prevSnap: string | null = null
  let prevState: string | null = null
  let lastResize: ReturnType<typeof planFigureEdge> | null = null
  let lastAlt = false
  let lastDx = 0
  let lastDy = 0
  let lastNoSnap = false
  let hasMove = false

  const restorePad = (d: typeof d0) => {
    if (storedPad0) d.framePad = { ...storedPad0 }
    else delete d.framePad
    if (storedTight0) d.framePadTight = [...storedTight0]
    else delete d.framePadTight
  }

  const apply = (altKey: boolean, noSnap: boolean, dxPx: number, dyPx: number) => {
    hasMove = true
    lastAlt = altKey
    lastNoSnap = noSnap
    lastDx = dxPx
    lastDy = dyPx
    const { columns, specLabel } = columnsNow(d0.profile)
    const deltaOut = (sign * pxToMm(horizontal ? dxPx : dyPx, t0)) / k
    const now = useInteractionStore.getState()
    // 直接操纵视口：退出「适应画布」（否则画布框一变，适应模式按新框重新取景，图在屏幕上被挪走）
    useViewportStore.getState().setPan(t0.panX, t0.panY)
    if (!altKey) {
      const plan = planFigureEdge({
        edge,
        box0: canvas0,
        sizeMm: facts.sizeMm,
        pad: pad0,
        k,
        deltaOut,
        noSnap,
        columns,
        limits: facts.limits,
      })
      lastResize = plan
      store.txnUpdate(restorePad)
      if (now.kind !== 'figureedge') useInteractionStore.setState({ kind: 'figureedge' })
      const col = plan.snap ? columnLabel(specLabel, plan.snap) : null
      const key = `${plan.snap?.id ?? ''}|${plan.square}`
      now.setFigureEdgeBox(plan.box)
      now.setPageEdge({ edge, snapLabel: col, rolls: key !== prevSnap })
      prevSnap = key
      prevState = null
      now.setDragStatus(
        frameEdgeResizeDragStatus({
          side: edge,
          from: facts.sizeMm,
          to: plan.sizeMm,
          col,
          square: plan.square,
          box: plan.box,
        }),
      )
      return
    }
    lastResize = null
    if (now.kind !== 'framepad') useInteractionStore.setState({ kind: 'framepad' })
    const plan = planPadEdge({
      edge,
      pad0,
      deltaOut,
      sizeMm: facts.sizeMm,
      inset,
      columns: noSnap ? [] : columns,
      // ⌥ 本身就是「要裁」：越过内容边缘不再另设一道闸（与排版 ⌥ 拖边同一个道理）
      allowCrop: true,
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
    const box = frameCanvasBox(box0, pad, k)
    const col = plan.snap ? columnLabel(specLabel, plan.snap) : null
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
  }
  const onKey = (ke: KeyboardEvent) => {
    if (ke.key !== 'Alt' || !hasMove || ke.altKey === lastAlt) return
    ke.preventDefault()
    apply(ke.altKey, lastNoSnap, lastDx, lastDy)
  }
  window.addEventListener('keydown', onKey)
  window.addEventListener('keyup', onKey)

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => apply(ev.altKey, ev.metaKey || ev.ctrlKey, dxPx, dyPx),
    onEnd: (moved, ev, end) => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', onKey)
      // 松手那一刻的 ⌥ 才算数：与最后一次预览不同就先按它重算一次
      if (moved && !end.cancelled && hasMove && ev.altKey !== lastAlt) apply(ev.altKey, lastNoSnap, lastDx, lastDy)
      const resize = moved && !end.cancelled ? lastResize : null
      useInteractionStore.getState().end()
      if (end.cancelled || !moved || resize) {
        store.endTxn({ discard: true })
        if (moved && !resize?.changed) useViewportStore.getState().restoreFrozenView(view0)
      } else {
        store.endTxn()
      }
      if (!resize?.changed) return
      const [w, h] = resize.sizeMm
      const before = useRenderStore.getState().byKey
      setFigureFrame(panelId, resize.sizeMm, resize.pos)
      holdPlaceholder(panelId, { x: resize.pos.x, y: resize.pos.y, w: w * k, h: h * k }, resize.sizeMm, before)
    },
  })
}

/** 角柄的比例：默认等比，⌥ 自由比例，⇧ 强制等比（压过 ⌥）——与排版角柄同一套键（ADR 0161 修订 2026-11-27） */
const cornerFree = (ev: { altKey: boolean; shiftKey: boolean }) => ev.altKey && !ev.shiftKey

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
  const pad = effectiveFramePad(d0, inkInsetMm(manifest, facts.sizeMm), facts.sizeMm)
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
        free: cornerFree(ev),
        noSnap: ev.metaKey || ev.ctrlKey,
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
        frameCornerDragStatus({
          free: cornerFree(ev),
          from: facts.sizeMm,
          to: plan.sizeMm,
          col,
          square: plan.square,
          box: plan.box,
        }),
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
