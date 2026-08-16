import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

/** 撤销/重做、删除或隐藏让对象从画布上消失后，清掉指向它们的选择与编辑态 */
export function subscribePruneSelection(): () => void {
  return useDocumentStore.subscribe((state, prev) => {
    if (state.doc.objects === prev.doc.objects) return
    const alive = new Set(state.doc.objects.map((o) => o.id))
    useSelectionStore.getState().prune(alive)
    // 编辑态（图内编辑/文字编辑/裁剪）比选中态更严：对象被隐藏也要退出，
    // 否则 Inspector 和 overlay 会指向一个画布上看不见的东西
    const usable = (id: string) => {
      const o = state.doc.objects.find((x) => x.id === id)
      return !!o && !o.hidden
    }
    const ui = useUiStore.getState()
    if (ui.editingTextId && !usable(ui.editingTextId)) ui.setEditingText(null)
    if (ui.cropTargetId && !usable(ui.cropTargetId)) ui.setCropTarget(null)
    if (ui.elementPanelId && !usable(ui.elementPanelId)) ui.setElementPanel(null)
  })
}
