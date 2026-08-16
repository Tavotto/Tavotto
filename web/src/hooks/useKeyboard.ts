import { useEffect } from 'react'
import { usePalette } from '@/components/CommandPalette'
import { copySelectedObjects, pasteObjects } from '@/lib/clipboard'
import {
  changeZOrder,
  enterElementEdit,
  deleteSelected,
  duplicateSelected,
  hideElements,
  nudgeSelected,
  selectAll,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore, type Tool } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

const TOOL_KEYS: Record<string, Tool> = {
  v: 'select',
  t: 'text',
  a: 'arrow',
  r: 'rect',
  o: 'ellipse',
  l: 'line',
}

/**
 * 图内元素的「删除」= 写 visible:false（非破坏、可从「已隐藏元素」恢复）。
 * 没有 visible 字段的元素（如整图、色条轴）删不掉，静默跳过。
 */
function hideSelectedElements(panelId: string, gids: string[]) {
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') return
  const elements = useRenderStore.getState().byFile[panel.fileId]?.manifest?.elements ?? []
  const targets = gids
    .map((gid) => elements.find((e) => e.gid === gid))
    .filter(
      (e): e is NonNullable<typeof e> =>
        !!e && e.gid !== 'figure' && e.editable.some((f) => f.prop === 'visible'),
    )
    .map((e) => ({ gid: e.gid, label: e.label }))
  if (!targets.length) return
  hideElements(panelId, targets)
  useUiStore.getState().setSelectedGid(null)
}

function inEditableTarget(e: KeyboardEvent) {
  const el = e.target
  if (!(el instanceof HTMLElement)) return false
  return (
    el.isContentEditable ||
    /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) ||
    el.closest('[role="dialog"]') != null
  )
}

export function useKeyboard() {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const ui = useUiStore.getState()

      if (e.code === 'Space' && !inEditableTarget(e)) {
        if (!e.repeat) useViewportStore.getState().setSpaceDown(true)
        e.preventDefault()
        return
      }

      if (inEditableTarget(e)) return
      const mod = e.metaKey || e.ctrlKey
      const doc = useDocumentStore.getState()

      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        const label = e.shiftKey ? doc.redo() : doc.undo()
        if (label) ui.setStatus(`${e.shiftKey ? '重做' : '撤销'}：${label}`)
        else ui.setStatus(e.shiftKey ? '没有可重做的操作' : '没有可撤销的操作')
        return
      }
      if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        const label = doc.redo()
        if (label) ui.setStatus(`重做：${label}`)
        return
      }
      if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        duplicateSelected()
        return
      }
      if (mod && e.key.toLowerCase() === 'c') {
        // 只有画布上有选中对象才接管 ⌘C；否则让浏览器做普通文本复制
        if (useSelectionStore.getState().ids.length) {
          e.preventDefault()
          void copySelectedObjects()
        }
        return
      }
      if (mod && e.key.toLowerCase() === 'v') {
        // 异步读剪贴板：是本工具的对象负载才粘贴，普通文本不受影响
        void pasteObjects()
        return
      }
      if (mod && e.key.toLowerCase() === 'a') {
        e.preventDefault()
        selectAll()
        return
      }
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        usePalette.getState().setOpen(true)
        return
      }
      if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault()
        ui.setLayoutOpen(true)
        return
      }
      if (mod && e.key.toLowerCase() === 'e') {
        e.preventDefault()
        ui.setExportOpen(true)
        return
      }
      if (mod && (e.key === ']' || e.key === '[')) {
        e.preventDefault()
        changeZOrder(e.shiftKey ? (e.key === ']' ? 'top' : 'bottom') : e.key === ']' ? 'up' : 'down')
        return
      }
      if (mod && (e.key === '=' || e.key === '+' || e.key === '-')) {
        e.preventDefault()
        const vp = useViewportStore.getState()
        vp.setZoomCentered(vp.zoom * (e.key === '-' ? 1 / 1.25 : 1.25))
        return
      }
      if (mod && e.key === '0') {
        e.preventDefault()
        useViewportStore.getState().setZoomCentered(1)
        return
      }
      if (mod && e.key === '1') {
        e.preventDefault()
        const page = doc.doc.page
        useViewportStore.getState().fit(page.w, page.h)
        return
      }

      if (mod) return

      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        // 图内编辑时删的是「这个图内元素」（写 visible:false，可恢复），
        // 而不是把整个面板从画布上删掉
        if (ui.elementPanelId && ui.selectedGids.length) {
          hideSelectedElements(ui.elementPanelId, ui.selectedGids)
          return
        }
        deleteSelected()
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        // 最上层浮层优先：版本抽屉开着就先关它
        if (ui.versionsOpen) ui.setVersionsOpen(false)
        else if (ui.editingTextId) ui.setEditingText(null)
        else if (ui.elementPanelId) {
          // 先退选中的图内元素，再退整个图内编辑态；
          // 退出后选中该面板——属性页落在面板上，「写回原始文件」就在手边
          if (ui.selectedGids.length) ui.setSelectedGid(null)
          else {
            const pid = ui.elementPanelId
            ui.setElementPanel(null)
            useSelectionStore.getState().set([pid])
          }
        } else if (ui.cropTargetId) ui.setCropTarget(null)
        else if (ui.tool !== 'select') ui.setTool('select')
        else useSelectionStore.getState().clear()
        return
      }
      if (e.key === 'Enter') {
        const ids = useSelectionStore.getState().ids
        if (ui.cropTargetId) {
          ui.setCropTarget(null)
          return
        }
        if (ids.length === 1) {
          const obj = doc.doc.objects.find((o) => o.id === ids[0])
          if (obj?.type === 'text') {
            e.preventDefault()
            ui.setEditingText(obj.id)
          } else if (obj?.type === 'panel') {
            e.preventDefault()
            if (obj.script) enterElementEdit(obj.id)
            else ui.setCropTarget(obj.id)
          }
        }
        return
      }
      if (e.key.startsWith('Arrow')) {
        if (!useSelectionStore.getState().ids.length) return
        e.preventDefault()
        const d = e.shiftKey ? 5 : 0.5
        nudgeSelected(
          e.key === 'ArrowLeft' ? -d : e.key === 'ArrowRight' ? d : 0,
          e.key === 'ArrowUp' ? -d : e.key === 'ArrowDown' ? d : 0,
        )
        return
      }

      if (e.key === '?') {
        e.preventDefault()
        ui.setShortcutHelpOpen(true)
        return
      }

      const tool = TOOL_KEYS[e.key.toLowerCase()]
      if (tool) {
        e.preventDefault()
        ui.setTool(tool)
      }
    }

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') useViewportStore.getState().setSpaceDown(false)
    }
    const onBlur = () => useViewportStore.getState().setSpaceDown(false)

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [])
}
