import type { PointerEvent as ReactPointerEvent } from 'react'
import { dragGutter, gutterPair, quickLayoutValid, rowHeightsMm, type Gutter } from '@/lib/quickLayoutGrid'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { fullFromVisible, runQuickLayoutRatios, useQuickLayoutStore } from '@/store/quickLayoutActions'
import { getTransform, pxToMm } from '@/store/viewportStore'
import { trackPointer } from './interactions'
import { QuickLayoutFollow } from './quickLayoutFollow'

/**
 * 拖一条行 / 列分隔线（一键排版之后，ADR 0199）。**预览平面**：图本身按预览框跟手（`QuickLayoutFollow`，只写
 * DOM 的 translate / scale）；没挂上画面的图退回淡色轮廓（`useQuickLayoutStore.preview`）。
 * 拖动中不 commit、不进历史、不发后端、不重渲染任何图（假实时预览规则）；松手才 `runQuickLayoutRatios`
 * 一次请求 + 一次 commit，跟手一直保持到新图落位（见 `quickLayoutFollow`）。
 * Esc / pointercancel 与松手分开：取消 = 原样放回、什么都不发。
 */
export function startGutterDrag(e: ReactPointerEvent, gutter: Gutter, along = 0.5) {
  if (e.button !== 0) return
  const d0 = useDocumentStore.getState().doc
  const memo = d0.quickLayout
  if (!quickLayoutValid(memo, d0.objects)) return
  e.stopPropagation()
  e.preventDefault()
  const t0 = getTransform()
  const horizontal = gutter.axis === 'col'
  const interaction = useInteractionStore.getState()
  interaction.begin('gutter')
  let last: ReturnType<typeof dragGutter> = null
  const follow = new QuickLayoutFollow(memo)
  const live = follow.followedIds
  // 记忆里的可见框 -> 整图框：占比按整图尺寸发给求解器（预览仍按可见框）
  const cropOf0 = new Map(d0.objects.map((o) => [o.id, o.type === 'panel' ? o.crop : undefined] as const))
  const memoFull = Object.fromEntries(
    Object.entries(memo.boxes).map(([id, b]) => [id, fullFromVisible(cropOf0.get(id), b)] as const),
  )
  const pair = gutterPair(memo, gutter.axis, gutter.index)
  trackPointer(e, {
    onMove: (_ev, dxPx, dyPx) => {
      last = dragGutter(memo, gutter.axis, gutter.index, pxToMm(horizontal ? dxPx : dyPx, t0), memoFull)
      if (!last) return
      const d = last.delta
      useQuickLayoutStore.setState({
        preview: last.boxes,
        live,
        drag: pair
          ? { axis: gutter.axis, pos: gutter.pos + d, from: gutter.from, to: gutter.to, along, sizes: [pair[0] + d, pair[1] - d] }
          : null,
      })
      follow.update(last.boxes)
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      if (end.cancelled || !moved || !last) {
        follow.restore()
        useQuickLayoutStore.setState({ preview: null, live: null, drag: null })
        return
      }
      // 预览框是页面上的可见框；求解器按整图高度工作，被裁剪的图要先换算回整图，否则排完会再被 crop 缩一次
      const cropOf = new Map(d0.objects.map((o) => [o.id, o.type === 'panel' ? o.crop : undefined] as const))
      const fullBoxes = Object.fromEntries(
        Object.entries(last.boxes).map(([id, b]) => [id, fullFromVisible(cropOf.get(id), b)] as const),
      )
      const rowsMm = rowHeightsMm(memo, fullBoxes)
      const ratios = {
        cols: gutter.axis === 'col' ? last.ratios : memo.colRatios,
        rows: gutter.axis === 'row' ? last.ratios : memo.rowRatios,
        // 绝对行高取自预览框（已换算成整图高度）：求解器原样采用，结果与预览一致（列的总宽由页面 / 目标定，占比就够）
        ...(rowsMm ? { rows_mm: rowsMm } : {}),
      }
      // 图停在预览框上等方案：成功 = 跟手保持到新图落位（onCommitted）；失败 / 作废 = 原样放回
      void runQuickLayoutRatios(ratios, follow)
        .then((outcome) => {
          if (outcome !== 'applied') follow.restore()
        })
        .catch(() => follow.restore())
        .finally(() => useQuickLayoutStore.setState({ preview: null, live: null, drag: null }))
    },
  })
}
