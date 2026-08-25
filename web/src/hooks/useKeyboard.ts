import { useEffect } from 'react'
import { formatMessage, msg } from '@/i18n'
import { usePalette } from '@/components/CommandPalette'
import { handleCopyEvent, handlePasteEvent } from '@/lib/clipboard'
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
import { useInteractionStore } from '@/store/interactionStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
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
  const elements = panelRender(useRenderStore.getState(), panel)?.manifest?.elements ?? []
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

/**
 * 拖动 / 缩放 / 框选 / 画线 / 调端点进行中要忽略撤销重做：`documentStore.undo()`
 * 开头的 `if (state.txn) state.endTxn()` 会把进行中的这次拖动当场结算成一条历史，
 * 紧接着同一次调用里 `past.at(-1)` 取到的正是它，立刻又把它撤销；而
 * `canvas/interactions.ts` 挂在 window 上的 pointermove 毫不知情，此后每次移动都落进
 * `txnUpdate` 里「没有 txn 就直接 set」的静默分支——那段位移既不进历史也撤不回来。
 * 与 `inEditableTarget()` 不冲突：那条按 e.target 挡的是输入框 / 对话框里的原生文本
 * 撤销，这条只看画布拖动是否进行中，两者各管一段、互不覆盖。
 */
export function undoRedoBlocked() {
  return useInteractionStore.getState().kind !== 'none'
}

/** ⌘Z / ⌘⇧Z 的实际动作；拖动中直接放弃（见 undoRedoBlocked） */
export function runUndoRedo(redo: boolean) {
  if (undoRedoBlocked()) return
  const doc = useDocumentStore.getState()
  const label = redo ? doc.redo() : doc.undo()
  const ui = useUiStore.getState()
  // 历史条目存的是描述符，这里在**显示那一刻**才翻——切语言后同一条历史
  // 会用新语言说话
  if (label) {
    ui.setStatus(
      msg(redo ? 'status.redone' : 'status.undone', { label: formatMessage(label) }, 'workspace'),
    )
  } else {
    ui.setStatus(msg(redo ? 'status.nothingToRedo' : 'status.nothingToUndo', undefined, 'workspace'))
  }
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
        runUndoRedo(e.shiftKey)
        return
      }
      if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        if (undoRedoBlocked()) return
        const label = doc.redo()
        if (label) {
          ui.setStatus(msg('status.redone', { label: formatMessage(label) }, 'workspace'))
        }
        return
      }
      if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        duplicateSelected()
        return
      }
      // ⌘C / ⌘V 不在 keydown 层拦：让浏览器派发原生 copy/paste 事件，
      // 由下面注册的 ClipboardEvent 监听同步读写 e.clipboardData——
      // WebKit（Safari / 桌面壳）不给非编辑区的异步 readText/writeText，
      // 走事件是跨标签页复制粘贴在所有浏览器都通的唯一路径。
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
        vp.zoomBy(e.key === '-' ? 1 / 1.25 : 1.25)
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
        useViewportStore.getState().fitAnimated(page.w, page.h)
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
        // 组件自己消费过的 Enter 不再叠加画布捷径（issue #37 实测撞见：
        // 素材卡上按 Enter「加入画布」，加入即选中，同一个事件冒泡到这里
        // 又触发「进入图内编辑」——键盘用户一步被瞬移进编辑态）。
        // 处理过 Enter 的 widget（素材卡/图层树/元素树）都 preventDefault。
        if (e.defaultPrevented) return
        // 焦点在按钮/链接/菜单项上时 Enter 的意思是「激活它」，不是画布捷径。
        // 抢走（preventDefault）的话浏览器不再合成 click——键盘用户选中一个
        // 面板后，顶栏的每一颗按钮都按不动了（审计 P1-09 实测撞见：焦点在
        // 「导出」上按 Enter，打开的却是图内编辑）。
        const at = e.target
        if (at instanceof HTMLElement &&
            at.closest('button, a, [role="button"], [role="menuitem"]')) return
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
    const onCopy = (e: ClipboardEvent) => void handleCopyEvent(e)
    const onPaste = (e: ClipboardEvent) => void handlePasteEvent(e)

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    document.addEventListener('copy', onCopy)
    document.addEventListener('paste', onPaste)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
      document.removeEventListener('copy', onCopy)
      document.removeEventListener('paste', onPaste)
    }
  }, [])
}
