import { useEffect } from 'react'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 选择驱动的面板路由：
 * - 出现选中（画布对象或进入图内编辑）→ 打开属性栏；素材抽屉未钉住则让位。
 *   停留在改图助手时只更新目标，不切换模式。
 * - 选择清空 → 未钉住的属性栏收起，不留「没有选中对象」的占位。
 */
export function useSelectionRouting() {
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  const inElement = useUiStore((s) => s.elementPanelId != null)
  const primaryId = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const primaryGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const active = hasSelection || inElement

  useEffect(() => {
    const ui = useUiStore.getState()
    if (active) ui.autoShowProperties()
    else ui.autoHideProperties()
  }, [active])

  // 换选对象 / 图内元素也要把属性带到眼前（互斥断点下右栏可能正被抽屉挤掉）。
  // 例外：焦点还在左抽屉里（素材批量添加、树的 Shift 多选）时不抢走抽屉——
  // 那是用户正在进行的流程，点画布即可唤出属性。
  useEffect(() => {
    if (!primaryId && !primaryGid) return
    const ui = useUiStore.getState()
    const inDrawer = !!document.activeElement?.closest('[data-left-drawer]')
    if (inDrawer && ui.layout !== 'wide') return
    ui.autoShowProperties()
  }, [primaryId, primaryGid])
}
