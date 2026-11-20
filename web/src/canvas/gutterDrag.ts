import type { PointerEvent as ReactPointerEvent } from 'react'
import { dragGutter, quickLayoutValid, type Gutter } from '@/lib/quickLayoutGrid'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { runQuickLayoutRatios, useQuickLayoutStore } from '@/store/quickLayoutActions'
import { getTransform, pxToMm } from '@/store/viewportStore'
import { trackPointer } from './interactions'

/**
 * 拖一条行 / 列分隔线（一键排版之后，ADR 0199）。**预览平面只画淡色轮廓**（`useQuickLayoutStore.preview`），
 * 拖动中不 commit、不进历史、不发后端、不重渲染任何图（假实时预览规则）；松手才 `runQuickLayoutRatios`
 * 一次请求 + 一次 commit。Esc / pointercancel 与松手分开：取消 = 收掉轮廓、什么都不发。
 */
export function startGutterDrag(e: ReactPointerEvent, gutter: Gutter) {
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
  trackPointer(e, {
    onMove: (_ev, dxPx, dyPx) => {
      last = dragGutter(memo, gutter.axis, gutter.index, pxToMm(horizontal ? dxPx : dyPx, t0))
      if (last) useQuickLayoutStore.setState({ preview: last.boxes })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      if (end.cancelled || !moved || !last) {
        useQuickLayoutStore.setState({ preview: null })
        return
      }
      const ratios = {
        cols: gutter.axis === 'col' ? last.ratios : memo.colRatios,
        rows: gutter.axis === 'row' ? last.ratios : memo.rowRatios,
      }
      // 轮廓留着等方案：runQuickLayoutRatios 的 finally（成功 / 失败 / 作废）统一收掉
      void runQuickLayoutRatios(ratios).finally(() => useQuickLayoutStore.setState({ preview: null }))
    },
  })
}
