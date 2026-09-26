import { msg } from '@/i18n'
import type { MenuAction } from '@/lib/desktop'
import type { AlignMode } from '@/lib/geometry'
import { alignSelectedTo, duplicateSelected, runManualSave } from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { deleteSelection, runUndoRedo, runZoomCommand, yieldsCanvasShortcuts } from './useKeyboard'

const ALIGN_PREFIX = 'menu-align-'

/**
 * 系统菜单（Tauri 壳，`tavotto:menu`）→ 现有 action 的转发。**这里没有第二套行为**：
 * 每一条都落到键盘、顶栏、命令面板、属性页已经在调的那个函数上。
 *
 * 菜单加速键可能先于 webview 的 keydown 截获按键（Windows 上一定如此），所以
 * 挂了加速键的几条要做与 `useKeyboard` **同一个**让位判断——焦点在输入框 /
 * 对话框里时 ⌘D、⌘0、⌘± 什么都不做（与 keydown 在那里 return 一致），⌘S 照存
 * （keydown 在输入框里也拦 ⌘S）。菜单是用鼠标点的还是按键触发的分不出来，
 * 只能按同一条判据走。
 *
 * 画布动作只在项目打开着时有意义（`useKeyboard` 与这些对话框都挂在 Workspace 里）；
 * Project Picker 上只认「打开项目」与撤销/重做（那里的输入框也要能 ⌘Z）。
 */
export function runMenuAction(action: MenuAction) {
  const ui = useUiStore.getState()
  const focused = document.activeElement

  if (action === 'menu-open-project') {
    useProjectStore.getState().showPicker()
    return
  }
  // 撤销/重做不看项目开没开：Picker 上的输入框一样要能 ⌘Z。按焦点分派——文本框里
  // 交还原生文本撤销，画布上走文档 undo 栈；必须走带 undoRedoBlocked 守卫的入口，
  // 菜单加速键在拖动进行中也会触发，直接 undo 会把进行中的事务当场结算掉，
  // 后续位移绕过历史（数据损坏）
  if (action === 'menu-undo' || action === 'menu-redo') {
    const redo = action === 'menu-redo'
    const inText =
      focused instanceof HTMLElement &&
      (focused.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName))
    if (inText) document.execCommand(redo ? 'redo' : 'undo')
    else runUndoRedo(redo)
    return
  }
  if (useProjectStore.getState().phase !== 'open') return

  if (action.startsWith(ALIGN_PREFIX)) {
    // 菜单看不见选区：没选东西时说出口，而不是点了没反应
    if (!useSelectionStore.getState().ids.length) {
      ui.setStatus(msg('quickEdit.needObjects', { count: 1 }, 'workspace'), 'error')
      return
    }
    // id 后缀就是 AlignMode；参照与属性页 / 多选浮动栏共用 arrangeStore 那一份
    alignSelectedTo(action.slice(ALIGN_PREFIX.length) as AlignMode, useArrangeStore.getState().alignRef)
    return
  }

  switch (action) {
    case 'menu-settings':
      ui.setSettingsOpen(true)
      break
    case 'menu-check-updates':
      // 与设置页「检查更新」按钮同一个 action；它自己挡并发
      ui.setSettingsOpen(true, 'update')
      void useUpdateStore.getState().checkDesktop()
      break
    case 'menu-diagnostics':
      ui.setSettingsOpen(true, 'diagnostics')
      break
    case 'menu-save':
      void runManualSave()
      break
    case 'menu-save-layout':
      ui.setLayoutOpen(true, 'save')
      break
    case 'menu-export':
      ui.setExportOpen(true)
      break
    case 'menu-duplicate':
      if (!yieldsCanvasShortcuts(focused)) duplicateSelected()
      break
    case 'menu-delete':
      // 没挂加速键，只会是点出来的：文本框里就删选中的字（与 macOS「编辑 → 删除」一致）
      if (yieldsCanvasShortcuts(focused)) document.execCommand('delete')
      else deleteSelection()
      break
    case 'menu-zoom-in':
    case 'menu-zoom-out':
    case 'menu-zoom-actual':
    case 'menu-zoom-fit':
      if (!yieldsCanvasShortcuts(focused)) runZoomCommand(ZOOM[action])
      break
    case 'menu-toggle-left':
      ui.toggleLeft()
      break
    case 'menu-toggle-right':
      ui.toggleRight()
      break
    case 'menu-shortcut-help':
      ui.setShortcutHelpOpen(true)
      break
  }
}

const ZOOM = {
  'menu-zoom-in': 'in',
  'menu-zoom-out': 'out',
  'menu-zoom-actual': 'actual',
  'menu-zoom-fit': 'fit',
} as const
