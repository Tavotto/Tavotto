import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import { resizeFigureEdge, type PageEdge } from '@/lib/pageEdgeResize'
import { setTxnAnchor, useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { getTransform, pxToMm } from '@/store/viewportStore'
import { trackPointer } from './interactions'

/** 与画布上缩放手柄同一条历史标签：拖边与拖手柄在撤销面板里是同一个名字 */
const HIST_LABEL: UiMessage = msg('history.resizeObjects', undefined, 'workspace')

function columnLabel(specLabel: string, c: PageColumn): string {
  const name = translate(`canvas.presets.${c.id}.label`, { ns: 'inspector' })
  return specLabel ? `${specLabel} ${name}` : name
}

/**
 * 单图编辑里拖这张图的四条边之一改图幅（面板的 w / h，mm）——手感与多图排版里拖页面边（`startPageEdgeResize`）一致。
 *
 * - **写文档与缩放手柄同一条路径**：每一帧 `txnUpdate` 写面板的 x / y / w / h（与 `startResizeDrag` 同一种写法，
 *   图幅没有第二个入口），登记对侧锚点（`setTxnAnchor`，渲染校正图幅时绕它收尾）；整次拖动一个事务、松手一步撤销，
 *   Esc / 指针被系统打断回到原值。尺寸标注是 `canvas/PageEdgeResize.EdgeDimensions`。
 * - 对侧那条边不动（拖左 / 上边时 x / y 反向补偿）；视口不动。
 * - 宽度吸栏宽 ±2 mm、⌥ 关、最小 30 mm、0.1 mm 一格：数学与拖页面边是同一份（`resizeFigureEdge` → `resizePageEdge`）。
 */
export function startFigureEdgeResize(e: ReactPointerEvent, edge: PageEdge, panelId: string) {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const store = useDocumentStore.getState()
  const target = store.doc.objects.find((o) => o.id === panelId)
  if (!target || target.type !== 'panel' || target.locked) return
  const start = { x: target.x, y: target.y, w: target.w, h: target.h }
  const t0 = getTransform()
  const columnsNow = () => {
    const ps = useProfileStore.getState()
    const r = resolvePageColumns(store.doc.profile, toCatalog(ps.specs), ps.loaded)
    return { columns: r.ready ? r.columns : [], specLabel: r.specLabel }
  }
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()
  const horizontal = edge === 'l' || edge === 'r'
  const interaction = useInteractionStore.getState()

  interaction.begin('figureedge')
  interaction.setPageEdge({ edge, snapLabel: null, rolls: false })
  store.beginTxn(HIST_LABEL)
  // 对侧边的中点：渲染把图幅校正到别的值时，收尾绕它伸缩，这条边仍然不动
  setTxnAnchor(panelId, {
    x: edge === 'l' ? start.x + start.w : edge === 'r' ? start.x : start.x + start.w / 2,
    y: edge === 't' ? start.y + start.h : edge === 'b' ? start.y : start.y + start.h / 2,
  })

  let prevSnap: string | null = null
  let prevAlt = false
  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const { columns, specLabel } = columnsNow()
      const res = resizeFigureEdge({
        edge,
        start,
        delta: pxToMm(horizontal ? dxPx : dyPx, t0),
        columns,
        noSnap: ev.altKey,
      })
      store.txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === panelId)
        if (!o) return
        o.x = res.x
        o.y = res.y
        o.w = res.w
        o.h = res.h
      })
      const snapId = res.snap?.id ?? null
      const rolls = snapId !== prevSnap || ev.altKey !== prevAlt
      prevSnap = snapId
      prevAlt = ev.altKey
      useInteractionStore
        .getState()
        .setPageEdge({ edge, snapLabel: res.snap ? columnLabel(specLabel, res.snap) : null, rolls })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}
