import { msg } from '@/i18n'
import type { MenuAction } from '@/lib/desktop'
import type { AlignMode } from '@/lib/geometry'
import { alignSelectedTo, duplicateSelected, runManualSave } from '@/store/actions'
import { alignSelectedPanelElements, type AlignBlocked } from '@/store/alignAction'
import { groupBlockedMessage, type GroupBlockReason } from '@/lib/elementGeom'
import { alignRefFor } from '@/store/arrangeStore'
import { runDiscreteAction, type DiscreteScope } from '@/store/gestureCoordinator'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { deleteSelection, inFastEdit, runUndoRedo, runZoomCommand } from './useKeyboard'

const ALIGN_PREFIX = 'menu-align-'

/**
 * 图内对齐被拒时说什么。前三条与属性页对齐工具条（`ElementInspector` 的 `AlignSection`）
 * 同一句；工具条在选得不够时把按钮置灰，菜单没法置灰，只能说出口。
 */
function reportElementAlignBlocked(
  reason: AlignBlocked,
  mode: AlignMode,
  group?: GroupBlockReason,
) {
  const ui = useUiStore.getState()
  if (reason === 'group-blocked' && group) ui.setStatus(groupBlockedMessage(group), 'info')
  else if (reason === 'syncing') ui.setStatus(msg('element.alignSyncing', undefined, 'inspector'), 'progress')
  else if (reason === 'noop') ui.setStatus(msg('element.alignNoop', undefined, 'inspector'), 'info')
  else if (reason === 'invalid') {
    ui.setStatus(msg('element.alignInvalid', undefined, 'inspector'), 'error')
  } else if (reason === 'too-few') {
    const count = mode === 'hdist' || mode === 'vdist' ? 3 : 2
    ui.setStatus(msg('quickEdit.needObjects', { count }, 'workspace'), 'error')
  }
}

/**
 * 每个菜单项的作用域（`runDiscreteAction` 的让位规则）。`Record` 逼每个 action id 都有一格：
 * 新加的菜单项不填这里编译不过，也就绕不开闸门。
 *
 * `canvas` 与 keydown 在输入框 / 对话框里 return 的那几条一一对应：挂了加速键的 ⌘D、⌘0、⌘±、
 * ⌘Z / ⇧⌘Z，以及「删除」（没挂加速键，但文本框里它的意思是删选中的字）。其余是应用级动作，
 * 焦点在哪都照做（⌘S 在输入框里也存——keydown 在那里也拦 ⌘S）。
 */
const MENU_SCOPE: Record<MenuAction, DiscreteScope> = {
  'menu-settings': 'app',
  'menu-check-updates': 'app',
  'menu-open-project': 'app',
  'menu-save': 'app',
  'menu-save-layout': 'app',
  'menu-export': 'app',
  'menu-undo': 'canvas',
  'menu-redo': 'canvas',
  'menu-duplicate': 'canvas',
  'menu-delete': 'canvas',
  'menu-align-left': 'app',
  'menu-align-hcenter': 'app',
  'menu-align-right': 'app',
  'menu-align-top': 'app',
  'menu-align-vcenter': 'app',
  'menu-align-bottom': 'app',
  'menu-align-hdist': 'app',
  'menu-align-vdist': 'app',
  'menu-zoom-in': 'canvas',
  'menu-zoom-out': 'canvas',
  'menu-zoom-actual': 'canvas',
  'menu-zoom-fit': 'canvas',
  'menu-toggle-left': 'app',
  'menu-toggle-right': 'app',
  'menu-shortcut-help': 'app',
  'menu-diagnostics': 'app',
}

/** Project Picker 上也认的几条（那里的输入框也要能 ⌘Z） */
const PICKER_ACTIONS: ReadonlySet<MenuAction> = new Set(['menu-open-project', 'menu-undo', 'menu-redo'])

/**
 * 系统菜单（Tauri 壳，`tavotto:menu`）→ 现有 action 的转发。**这里没有第二套行为**：
 * 每一条都落到键盘、顶栏、命令面板、属性页已经在调的那个函数上。
 *
 * 菜单加速键可能先于 webview 的 keydown 截获按键（Windows 上一定如此），keydown 顶部的
 * 「先落定方向键微调」与输入框让位这里都收不到。所以**每一条**都经 `runDiscreteAction`
 * 闸门（作用域见 `MENU_SCOPE`）：让位时不收手势、不执行（文本框里的撤销 / 删除交给原生），
 * 不让位才先收掉开着的连续编辑再执行（Codex #671）。菜单是用鼠标点的还是按键触发的分不
 * 出来，只能按同一条判据走。
 *
 * 画布动作只在项目打开着时有意义（`useKeyboard` 与这些对话框都挂在 Workspace 里）；
 * Project Picker 上只认 `PICKER_ACTIONS`。
 */
export function runMenuAction(action: MenuAction) {
  if (!PICKER_ACTIONS.has(action) && useProjectStore.getState().phase !== 'open') return
  runDiscreteAction(
    MENU_SCOPE[action],
    document.activeElement,
    () => performMenuAction(action),
    () => yieldMenuAction(action),
  )
}

/** 让位时交给原生的那一份：文本框里撤销 / 重做 / 删掉选中的字；其余什么都不做 */
function yieldMenuAction(action: MenuAction) {
  if (action === 'menu-undo') document.execCommand('undo')
  else if (action === 'menu-redo') document.execCommand('redo')
  else if (action === 'menu-delete') document.execCommand('delete')
}

/** 闸门放行之后的动作本身（手势已经收掉） */
function performMenuAction(action: MenuAction) {
  const ui = useUiStore.getState()

  if (action === 'menu-open-project') {
    useProjectStore.getState().showPicker()
    return
  }
  // 撤销/重做必须走带 undoRedoBlocked 守卫的入口：菜单加速键在拖动进行中也会触发，
  // 直接 undo 会把进行中的事务当场结算掉，后续位移绕过历史（数据损坏）
  if (action === 'menu-undo' || action === 'menu-redo') {
    runUndoRedo(action === 'menu-redo')
    return
  }

  if (action.startsWith(ALIGN_PREFIX)) {
    const mode = action.slice(ALIGN_PREFIX.length) as AlignMode
    // 图内编辑态选着图内元素：对齐的是这些元素（与属性页对齐工具条同一个动作、同一道
    // 几何权威闸），不是把面板在版面上挪走。判据与 Delete 的 `deleteSelection` 同一条；
    // 写的是 override，快速编辑这一屏看得见，所以排在快速编辑闸前面。
    if (ui.elementPanelId && ui.selectedGids.length) {
      const res = alignSelectedPanelElements(ui.elementPanelId, mode)
      if (!res.ok) reportElementAlignBlocked(res.reason, mode, res.group)
      return
    }
    // 画布对齐写的是版面上的 x/y：快速编辑这一屏没有版面，改了用户也看不见
    // （与方向键 / 工具字母同一条判据 `inFastEdit`）
    if (inFastEdit()) return
    // 菜单看不见选区：没选东西时说出口，而不是点了没反应
    const count = useSelectionStore.getState().ids.length
    if (!count) {
      ui.setStatus(msg('quickEdit.needObjects', { count: 1 }, 'workspace'), 'error')
      return
    }
    // id 后缀就是 AlignMode；参照与属性页 / 多选浮动栏同一条规则：单选对画布，多选用 arrangeStore
    alignSelectedTo(mode, alignRefFor(count))
    return
  }

  switch (action) {
    case 'menu-settings':
      ui.setSettingsOpen(true)
      break
    case 'menu-check-updates':
      // 与设置页「检查更新」按钮同一个 action；它自己挡并发
      ui.setSettingsOpen(true, 'about')
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
      // 快速编辑里不往看不见的版面上加副本：判据在 `duplicateSelected` 里（与 ⌘D 同一处）
      duplicateSelected()
      break
    case 'menu-delete':
      deleteSelection()
      break
    case 'menu-zoom-in':
    case 'menu-zoom-out':
    case 'menu-zoom-actual':
    case 'menu-zoom-fit':
      runZoomCommand(ZOOM[action])
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
