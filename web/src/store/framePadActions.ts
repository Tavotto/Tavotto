import { msg } from '@/i18n'
import { inkInsetMm, PAD_SIDES, tightPad } from '@/lib/framePad'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { exactPanelManifest, useRenderStore } from './renderStore'

/** 单图画布留白的离散动作（ADR 0161）：胶囊按钮调这里；拖边走 `canvas/frameDrag` 的事务，同一条历史标签 */
const HIST = msg('history.setFramePad', undefined, 'workspace')

/** 「贴合内容」能不能点：要精确 manifest 给出图幅与内容边缘 */
export function canFitFrameToContent(panelId: string): boolean {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (!p || p.type !== 'panel') return false
  const m = exactPanelManifest(useRenderStore.getState(), p)
  const facts = figureFrameFacts(p, m)
  return !!facts && inkInsetMm(m, facts.sizeMm) != null
}

/** 「贴合内容」：四边都落在紧凑停靠点上（图幅以后再变，四边跟着新的内容边缘走） */
export function fitFrameToContent(panelId: string): void {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (!p || p.type !== 'panel') return
  const m = exactPanelManifest(useRenderStore.getState(), p)
  const facts = figureFrameFacts(p, m)
  const inset = facts ? inkInsetMm(m, facts.sizeMm) : null
  if (!inset) return
  finishActiveGesture()
  useDocumentStore.getState().commit(HIST, (d) => {
    d.framePad = tightPad(inset)
    d.framePadTight = [...PAD_SIDES]
  })
}

/** 「去掉留白」：画布回到图幅本身（与从没加过留白逐字节相同） */
export function clearFramePad(): void {
  const d0 = useDocumentStore.getState().doc
  if (!d0.framePad && !d0.framePadTight) return
  finishActiveGesture()
  useDocumentStore.getState().commit(HIST, (d) => {
    delete d.framePad
    delete d.framePadTight
  })
}
