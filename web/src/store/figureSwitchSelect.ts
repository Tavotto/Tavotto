/**
 * 换了误差类型之后「选回误差棒」：新图的 manifest（权威 / exact）到了，选中新图里对应的误差棒元素（优先换之前选的那个 gid，
 * 没有就第一个 `role === 'errorbar'`，都没有就不动），属性页停在误差棒页、那一行显示新类型。
 * 与 `store/figureCopySelect` 同一做法：登记一条「待选中」，订阅渲染态 / 文档 / ui，对**当前状态**每次重验；不用计时。
 * 取消（不再等也不去选）= 用户已不在那个现场：换了文档 / 面板不在 / 退出了图内编辑 / 自己动过选区 / 渲染失败 / 面板又换了图（撤销）。
 */
import { exactPanelManifest, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'

interface Pending {
  documentId: string
  panelId: string
  fileId: string
  prefer: string | null
  band: boolean
  selected: string[]
}
let pending: Pending | null = null
let unsubscribe: (() => void) | null = null
const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((g, i) => g === b[i])

function cancel(): void {
  pending = null
  unsubscribe?.()
  unsubscribe = null
}

function evaluate(): void {
  const p = pending
  if (!p) return
  const docState = useDocumentStore.getState()
  const ui = useUiStore.getState()
  const panel = docState.doc.objects.find((o): o is PanelObject => o.id === p.panelId && o.type === 'panel')
  if (docState.documentId !== p.documentId || !panel || panel.fileId !== p.fileId || ui.elementPanelId !== p.panelId || !same(ui.selectedGids, p.selected)) {
    cancel()
    return
  }
  const render = useRenderStore.getState()
  if (render.byKey[renderKeyOf(panel)]?.status === 'error') {
    cancel()
    return
  }
  const exact = exactPanelManifest(render, panel)
  if (!exact) return // 还在画：下一次状态变化再验
  // 同一个 gid 优先（误差棒或阴影带都认）；否则第一个误差棒；阴影带的图再退到第一条带
  const of = (role: string) => exact.elements.filter((e) => e.role === role)
  const hit = exact.elements.find((e) => e.gid === p.prefer && (e.role === 'errorbar' || (p.band && e.role === 'fill'))) ?? of('errorbar')[0] ?? (p.band ? of('fill')[0] : undefined)
  cancel()
  if (hit) ui.setSelectedGids([hit.gid])
}

/** 登记「新图画好后选回误差棒」；`prefer` = 换之前选着的那个 gid。再登记 = 换成新的 */
export function selectErrorBarWhenDrawn(panelId: string, prefer: string | null, band = false): void {
  cancel()
  const panel = useDocumentStore.getState().doc.objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
  if (!panel) return
  pending = { documentId: useDocumentStore.getState().documentId, panelId, fileId: panel.fileId, prefer, band, selected: [...useUiStore.getState().selectedGids] }
  const stop = [useRenderStore.subscribe(evaluate), useMountedPngStore.subscribe(evaluate), useDocumentStore.subscribe(evaluate), useUiStore.subscribe(evaluate)]
  unsubscribe = () => stop.forEach((f) => f())
  evaluate()
}

/** 测试与换项目用 */
export const cancelErrorBarSelect = cancel
export const errorBarSelectPending = (): boolean => pending !== null
