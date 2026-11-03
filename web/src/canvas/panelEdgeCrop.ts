import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { cropDragStatus } from '@/lib/dragStatus'
import {
  depthOf,
  inkDepths,
  planEdgeDepth,
  withEdgeDepth,
  type CropSide,
  type EdgeCropState,
} from '@/lib/panelEdgeCrop'
import type { ResizeDir } from '@/lib/geometry'
import { setTxnAnchor, useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { getTransform, pxToMm, snapTolMm } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { CropRect, PanelObject } from '@/types/document'
import { startResizeDrag, trackPointer } from './interactions'

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')

const SIDE_OF: Record<string, CropSide | undefined> = { w: 'l', e: 'r', n: 't', s: 'b' }
const OPPOSITE: Record<CropSide, CropSide> = { l: 'r', r: 'l', t: 'b', b: 't' }

/**
 * 面板的边柄走「裁剪」的条件（ADR 0161 §4）：只在多图排版（`mode === 'layout'`，单图编辑的手势不动）、
 * 面板没锁。**旋转 / 翻转过的面板维持原来的边柄行为**——它们的边在内容坐标系里换了方向，
 * 要走 `startCropDrag` 那套 `pageDirInContent` 反变换；而「内容边缘」停靠点和胶囊的"左 / 右"措辞
 * 都按页面方向说话，两套坐标一起上会让"左 3.1 mm"指到内容的右边。先把常见的未变换面板做对，
 * 变换过的面板想精确裁剪走裁剪工具（双击进裁剪，它本来就支持旋转 / 翻转）。
 */
export function edgeCropApplies(panel: PanelObject): boolean {
  return (
    !panel.locked &&
    useWorkspaceStore.getState().mode === 'layout' &&
    !(panel.rotation ?? 0) &&
    !panel.flipH &&
    !panel.flipV
  )
}

/**
 * 面板缩放手柄的入口（`OverlaySvg` 调它，不直接调 `startResizeDrag`）：
 * 多图排版里未变换面板的四条**边柄** = 裁剪；其余（角柄、其它对象、变换过的面板、单图编辑）照旧。
 *
 * 为什么是单独的入口而不是 `startResizeDrag` 里的分支：本文件要用 `interactions.trackPointer`，
 * 反过来让 `interactions` import 本文件就是一个 import 环（`importArchitecture` 只减不增）。
 */
export function startPanelHandleDrag(e: ReactPointerEvent, objectId: string, dir: ResizeDir) {
  const side = SIDE_OF[dir]
  const target = useDocumentStore.getState().doc.objects.find((o) => o.id === objectId)
  if (side && target?.type === 'panel' && edgeCropApplies(target)) {
    startPanelEdgeCrop(e, target, side)
    return
  }
  startResizeDrag(e, objectId, dir)
}

type PanelBox = { x: number; y: number; w: number; h: number }

const isFullCrop = (c: CropRect) => c.x <= 1e-9 && c.y <= 1e-9 && c.w >= 1 - 1e-9 && c.h >= 1 - 1e-9

/**
 * 拖面板的一条边 = 改 `panel.crop`（与裁剪工具同一字段、同一条历史标签 `adjustCrop`）。
 * 整图锚在画布上不动（与 `startCropDrag` 同一个锚点约定，`setTxnAnchor`），所以内容纹丝不动，
 * 只是这一边的取景窗在收 / 放；图的缩放比（mm / 原图幅）不变。停靠点与 ⌥ 放行见 `lib/panelEdgeCrop`。
 * 拖动中的含义（裁掉多少、停在哪）放进 `interaction.dragStatus`，胶囊只读它。
 */
export function startPanelEdgeCrop(e: ReactPointerEvent, panel: PanelObject, side: CropSide) {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const objectId = panel.id
  const crop0: CropRect = panel.crop ? { ...panel.crop } : { x: 0, y: 0, w: 1, h: 1 }
  const horizontal = side === 'l' || side === 'r'
  // 未裁剪整图的显示尺寸与左上角（页面 mm）：之后每一帧的包围盒都从它们现算
  const fullW = panel.w / crop0.w
  const fullH = panel.h / crop0.h
  const imgX = panel.x - fullW * crop0.x
  const imgY = panel.y - fullH * crop0.y
  const fullDim = horizontal ? fullW : fullH
  const depth0 = depthOf(crop0, side)
  const oppDepth = depthOf(crop0, OPPOSITE[side])
  // 内容边缘只认几何权威（精确 manifest）；缺席 = 没有「紧凑」停靠点，边照样能裁
  const ink = inkDepths(
    exactPanelManifest(useRenderStore.getState(), panel)?.elements.find((el) => el.gid === 'figure')
      ?.ink_bbox,
  )
  const inkDepth = ink ? ink[side] : null

  const ix = () => useInteractionStore.getState()
  const status = (state: EdgeCropState, depth: number, box: PanelBox) =>
    ix().setDragStatus(cropDragStatus({ side, state, mm: depth * fullDim, box }))

  ix().begin('crop')
  status(
    depth0 <= 1e-9
      ? 'full'
      : inkDepth == null
        ? 'trim'
        : Math.abs(depth0 - inkDepth) <= 1e-9
          ? 'tight'
          : depth0 > inkDepth
            ? 'content'
            : 'trim',
    depth0,
    { x: panel.x, y: panel.y, w: panel.w, h: panel.h },
  )
  store.beginTxn(hist('adjustCrop'))
  setTxnAnchor(objectId, { x: imgX + fullW / 2, y: imgY + fullH / 2 })

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      const dMm = pxToMm(horizontal ? dxPx : dyPx, t)
      // l / t 往里 = 坐标增大；r / b 往里 = 坐标减小
      const inward = side === 'l' || side === 't' ? dMm : -dMm
      const tolMm = useUiStore.getState().snapEnabled ? snapTolMm(t) : 0
      const plan = planEdgeDepth({
        depth0,
        want: depth0 + inward / fullDim,
        oppDepth,
        inkDepth,
        tol: tolMm / fullDim,
        alt: ev.altKey,
      })
      const next = withEdgeDepth(crop0, side, plan.depth)
      const full = isFullCrop(next)
      const c = full ? { x: 0, y: 0, w: 1, h: 1 } : next
      const box = { x: imgX + fullW * c.x, y: imgY + fullH * c.y, w: fullW * c.w, h: fullH * c.h }
      status(plan.state, plan.depth, box)
      store.txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === objectId)
        if (!o || o.type !== 'panel') return
        o.crop = full ? undefined : { ...c }
        o.x = box.x
        o.y = box.y
        o.w = box.w
        o.h = box.h
      })
    },
    onEnd: (moved, _ev, end) => {
      ix().end()
      // pointercancel = 这次交互被系统作废，一律丢弃：不留裁剪、不进历史
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}
