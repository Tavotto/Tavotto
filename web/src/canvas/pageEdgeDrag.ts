import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { pageColumns, type PageColumn } from '@/lib/pageColumns'
import { resizePageEdge, type PageEdge } from '@/lib/pageEdgeResize'
import { resolveDocumentSpec } from '@/lib/specBinding'
import { applyPageSize } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { getTransform, mmToWorld, pxToMm, useViewportStore } from '@/store/viewportStore'
import { trackPointer } from './interactions'

/** 与 `setPageSize` 同一条历史标签：属性栏改尺寸与画布上拖边，撤销面板里是同一个名字 */
const HIST_LABEL: UiMessage = msg('history.setPageSize', undefined, 'workspace')

/** 吸上某档栏宽时标注上写的名字：「规范名 栏名」（如「Nature 双栏」） */
function columnLabel(specLabel: string, c: PageColumn): string {
  const name = translate(`canvas.presets.${c.id}.label`, { ns: 'inspector' })
  return specLabel ? `${specLabel} ${name}` : name
}

/**
 * 拖页面四条边之一改画布大小（只在多图排版里挂；单图编辑没有页面）。
 *
 * - **写文档只有一个入口**：每一帧 `txnUpdate(applyPageSize)`——与属性栏 `setPageSize` 是同一个食谱，
 *   夹取规则只有一份；整次拖动一个事务，松手只记一步撤销，Esc / 指针被系统打断回到原值。
 * - **拖左 / 上边时图在屏幕上不动**：页面向左 / 上长出去，原点跟着挪，对象与参考线的页面坐标反向补偿
 *   （食谱里的 `shift`），视口平移同量反向补偿（否则整页会在屏幕上跳一下）。
 * - 宽度吸栏宽（`resizePageEdge`），⌥ 关吸附；与图内 / 对象拖动的对齐吸附（snap）互不相干，不碰 `snapXs`。
 * - 拖动期间 `pageFit` 的「页面尺寸变了就补间取景」让位（`interaction.kind === 'pageedge'`）。
 */
export function startPageEdgeResize(e: ReactPointerEvent, edge: PageEdge) {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const store = useDocumentStore.getState()
  const d0 = store.doc
  const start = { w: d0.page.w, h: d0.page.h }
  const base = {
    objects: new Map(d0.objects.map((o) => [o.id, { x: o.x, y: o.y }] as const)),
    guides: d0.guides.map((g) => g.pos),
  }
  const t0 = getTransform()
  const spec = resolveDocumentSpec(d0.profile, toCatalog(useProfileStore.getState().specs)).profile
  const columns = pageColumns(spec.widths_mm)
  const horizontal = edge === 'l' || edge === 'r'
  const interaction = useInteractionStore.getState()

  interaction.begin('pageedge')
  interaction.setPageEdge({ edge, snapLabel: null, rolls: false })
  store.beginTxn(HIST_LABEL)

  // 上一帧的吸附档与 ⌥ 状态：变了 = 离散跳变（读数滚），没变 = 连续拖动（读数即时换）
  let prevSnap: string | null = null
  let prevAlt = false
  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const res = resizePageEdge({
        edge,
        start,
        delta: pxToMm(horizontal ? dxPx : dyPx, t0),
        columns,
        noSnap: ev.altKey,
      })
      store.txnUpdate((d) =>
        applyPageSize(d, res.w, res.h, { x: res.shiftX, y: res.shiftY, base }),
      )
      if (res.shiftX || res.shiftY || edge === 'l' || edge === 't') {
        useViewportStore
          .getState()
          .setPan(t0.panX - mmToWorld(res.shiftX) * t0.zoom, t0.panY - mmToWorld(res.shiftY) * t0.zoom)
      }
      const snapId = res.snap?.id ?? null
      const rolls = snapId !== prevSnap || ev.altKey !== prevAlt
      prevSnap = snapId
      prevAlt = ev.altKey
      useInteractionStore
        .getState()
        .setPageEdge({ edge, snapLabel: res.snap ? columnLabel(spec.label, res.snap) : null, rolls })
    },
    onEnd: (moved, _ev, end) => {
      if (end.cancelled || !moved) {
        store.endTxn({ discard: true })
        if (moved) useViewportStore.getState().setPan(t0.panX, t0.panY)
      } else {
        store.endTxn()
      }
      useInteractionStore.getState().end()
    },
  })
}
