/**
 * 把「这张图是怎么从数据画出来的」写到文档里那块面板上（`PanelObject.dataOrigin`，ADR 0178）。
 *
 * 面板第一次出现在文档里时由宿主调用（那一刻面板是「打开并运行」异步放上去的，不是用户这一步的编辑）：走 `applyDerivedUpdate`——
 * 不进撤销历史、置 dirty 并排一次自动保存（与 `panelSourceSync` 同一档：这是外部事实落进文档，不占用户的撤销步数）。
 * 已经有（相同）来历 / 面板不在 = 零改动。换图那一步不走这里：`actions.replaceRuntimeFigure` 在同一次 commit 里写。
 */
import type { CanvasData, CanvasObject, PanelDataOrigin, PanelObject } from '@/types/document'
import { applyDerivedUpdate, useDocumentStore } from './documentStore'

const isPanel = (o: CanvasObject): o is PanelObject => o.type === 'panel'

/** 写成功（面板在、之前没有来历）= true */
export function writePanelDataOrigin(panelId: string, origin: PanelDataOrigin): boolean {
  const s = useDocumentStore.getState()
  const put = (objs: CanvasObject[]): CanvasObject[] | null => {
    const hit = objs.find((o): o is PanelObject => isPanel(o) && o.id === panelId)
    if (!hit || hit.dataOrigin) return null
    return objs.map((o) => (o === hit ? { ...hit, dataOrigin: structuredClone(origin) } : o))
  }
  const here = put(s.doc.objects)
  if (here) {
    applyDerivedUpdate({ doc: { ...s.doc, objects: here } })
    return true
  }
  let touched = false
  const canvases: CanvasData[] = s.canvases.map((c) => {
    if (c.id === s.activeCanvasId) return c
    const objs = put(c.objects)
    if (!objs) return c
    touched = true
    return { ...c, objects: objs }
  })
  if (!touched) return false
  applyDerivedUpdate({ canvases })
  return true
}
