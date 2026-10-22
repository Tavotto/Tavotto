import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import { resizePageEdge, type PageEdge } from '@/lib/pageEdgeResize'
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
  // 先把视口定住（在飞的取景补间落到终点）再取快照，取消时整体还原——t0 取在补间半路上，Esc 后 zoom / pan / fitted 会互相对不上
  const view0 = useViewportStore.getState().freezeView()
  const t0 = getTransform()
  // 栏宽每一帧现读：规范目录没加载完时不吸附（见 `resolvePageColumns`），加载完的那一帧起就按绑定的规范吸；
  // 与属性栏页面预设同一个判据
  const columnsNow = () => {
    const ps = useProfileStore.getState()
    const r = resolvePageColumns(d0.profile, toCatalog(ps.specs), ps.loaded)
    return { columns: r.ready ? r.columns : [], specLabel: r.specLabel }
  }
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()
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
      const { columns, specLabel } = columnsNow()
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
      // 每一帧都经 setPan：停掉在飞的取景补间并退出「适应画布」——页面已经不是 `lastFit` 描述的那一页，
      // 之后开关侧栏 / 缩窗口不能再按旧取景重放把页面挪走。右 / 下边平移量为 0（屏幕上的页面不跳），左 / 上边反向补偿
      useViewportStore
        .getState()
        .setPan(t0.panX - mmToWorld(res.shiftX) * t0.zoom, t0.panY - mmToWorld(res.shiftY) * t0.zoom)
      const snapId = res.snap?.id ?? null
      const rolls = snapId !== prevSnap || ev.altKey !== prevAlt
      prevSnap = snapId
      prevAlt = ev.altKey
      useInteractionStore
        .getState()
        .setPageEdge({ edge, snapLabel: res.snap ? columnLabel(specLabel, res.snap) : null, rolls })
    },
    onEnd: (moved, _ev, end) => {
      if (end.cancelled || !moved) {
        store.endTxn({ discard: true })
        // 回到原值就回到原来的取景：zoom / pan / fitted / lastFit 四项整体还原
        if (moved) useViewportStore.getState().restoreFrozenView(view0)
      } else {
        store.endTxn()
      }
      useInteractionStore.getState().end()
    },
  })
}
