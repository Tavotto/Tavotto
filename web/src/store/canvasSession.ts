import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { useViewportStore } from './viewportStore'

/**
 * 每画布的 UI 会话：selection / 视口 / 图内编辑上下文 / 左右栏。
 * undo/redo 由 documentStore.canvasSessions 随数据层换入换出；
 * 这里只管 UI 层，进程内存即可（刷新后回到「fit + 无选择」是合理默认）。
 */
interface UiSession {
  selection: string[]
  zoom: number
  panX: number
  panY: number
  elementPanelId: string | null
  selectedGids: string[]
  leftOpen: boolean
  rightOpen: boolean
}

const sessions = new Map<string, UiSession>()

function capture(canvasId: string): void {
  const ui = useUiStore.getState()
  const vp = useViewportStore.getState()
  sessions.set(canvasId, {
    selection: useSelectionStore.getState().ids,
    zoom: vp.zoom,
    panX: vp.panX,
    panY: vp.panY,
    elementPanelId: ui.elementPanelId,
    selectedGids: ui.selectedGids,
    leftOpen: ui.leftOpen,
    rightOpen: ui.rightOpen,
  })
}

function restore(canvasId: string): void {
  const doc = useDocumentStore.getState().doc
  const ui = useUiStore.getState()
  const saved = sessions.get(canvasId)
  // 无论有无会话，先退出跨画布不成立的编辑态
  ui.setEditingText(null)
  ui.setCropTarget(null)
  if (!saved) {
    useSelectionStore.getState().clear()
    ui.setElementPanel(null)
    useViewportStore.getState().fit(doc.page.w, doc.page.h)
    return
  }
  const alive = new Set(doc.objects.map((o) => o.id))
  useSelectionStore.getState().set(saved.selection.filter((id) => alive.has(id)))
  ui.setElementPanel(saved.elementPanelId && alive.has(saved.elementPanelId)
    ? saved.elementPanelId
    : null)
  if (saved.elementPanelId && alive.has(saved.elementPanelId)) {
    useUiStore.setState({ selectedGids: saved.selectedGids })
  }
  useViewportStore.setState({ zoom: saved.zoom, panX: saved.panX, panY: saved.panY })
  useUiStore.setState({ leftOpen: saved.leftOpen, rightOpen: saved.rightOpen })
}

/**
 * 切换（或打开）画布的唯一 UI 入口：捕捉当前会话 → 切数据层 → 恢复目标会话。
 * open=true 时保证目标出现在标签行。
 */
export function activateCanvas(id: string, opts?: { open?: boolean }): void {
  const store = useDocumentStore.getState()
  if (id === store.activeCanvasId) {
    if (opts?.open) store.openCanvasTab(id)
    return
  }
  if (!store.canvases.some((c) => c.id === id)) return
  capture(store.activeCanvasId)
  if (opts?.open) store.openCanvasTab(id)
  else store.switchCanvas(id)
  restore(id)
}

/** 新建画布并切换过去（旧画布会话先捕捉） */
export function createCanvasAndActivate(name?: string): string {
  const store = useDocumentStore.getState()
  capture(store.activeCanvasId)
  const id = store.addCanvas(name) // addCanvas 内部完成数据层切换
  restore(id)
  return id
}

/** 删除画布（数据层守卫最后一张）；删的是激活画布时恢复邻居会话 */
export function deleteCanvasWithSession(id: string): boolean {
  const store = useDocumentStore.getState()
  const wasActive = id === store.activeCanvasId
  const ok = store.deleteCanvas(id)
  if (ok) {
    sessions.delete(id)
    if (wasActive) restore(useDocumentStore.getState().activeCanvasId)
  }
  return ok
}

/** 画布被删除后丢弃它的会话 */
export function dropCanvasSession(id: string): void {
  sessions.delete(id)
}
