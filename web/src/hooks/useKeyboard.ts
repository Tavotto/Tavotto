import { useEffect } from 'react'
import { formatMessage, msg } from '@/i18n'
import { usePalette } from '@/components/CommandPalette'
import { handleCopyEvent, handlePasteEvent } from '@/lib/clipboard'
import {
  beginCrop,
  cancelCrop,
  changeZOrder,
  enterElementEdit,
  finishCrop,
  runManualSave,
  deleteSelected,
  duplicateSelected,
  hideElements,
  selectAll,
  startNamedNode,
  toggleTimeline,
} from '@/store/actions'
import { finishNudge, nudgeKeyDown, nudgeKeyUp } from '@/canvas/nudge'
import { useDocumentStore } from '@/store/documentStore'
import {
  cancelActivePointerGesture,
  finishActiveGesture,
  yieldsCanvasShortcuts,
} from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useTimelineStore } from '@/store/timelineStore'
import { useUiStore, type Tool } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { fitStage, zoomToSelection } from '@/store/zoomToSelection'

/**
 * 快速编辑里**只有这一张图**：页面纸、网格、别的对象全部让开。
 *
 * 版面动作在这一屏上没有可见的对应物——推 x/y 看不见、画一个矩形也看不见，
 * 而两者都会进文档、进历史、跟着导出。顶栏把这组按钮藏了起来，快捷键必须
 * 走同一条判据，否则藏的只是入口不是能力。
 */
export const inFastEdit = () => useWorkspaceStore.getState().mode === 'fast_edit'

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

/** 画布快捷键的让位判据：唯一一份在 gestureCoordinator（菜单、剪贴板事件同一条） */
export { yieldsCanvasShortcuts }

const inEditableTarget = (e: KeyboardEvent) => yieldsCanvasShortcuts(e.target)

/**
 * Delete / Backspace 的画布动作（系统菜单「删除」也走这里）。
 * 图内编辑时删的是「这个图内元素」（写 visible:false，可恢复），
 * 而不是把整个面板从画布上删掉。
 */
export function deleteSelection() {
  const ui = useUiStore.getState()
  if (ui.elementPanelId && ui.selectedGids.length) {
    hideSelectedElements(ui.elementPanelId, ui.selectedGids)
    return
  }
  deleteSelected()
}

export type ZoomCommand = 'in' | 'out' | 'actual' | 'fit' | 'selection'

/** ⌘+ / ⌘− / ⌘0 / ⌘1 / ⇧2 的视口动作（系统菜单「显示」里的四条也走这里） */
export function runZoomCommand(cmd: ZoomCommand) {
  const vp = useViewportStore.getState()
  if (cmd === 'in' || cmd === 'out') vp.zoomBy(cmd === 'out' ? 1 / 1.25 : 1.25)
  else if (cmd === 'actual') vp.setZoomCentered(1)
  else if (cmd === 'selection') zoomToSelection()
  // 快速编辑里适应那张图、排版里适应页面——与舞台双击同一个取景框（`stageFitFrame`）
  else fitStage()
}

/**
 * 自己用方向键的控件（ARIA 复合控件：列表、树、标签页、单选组、菜单、滑块……）。
 * 焦点在它们里面时方向键归它们，不推画布上的选中对象。工具条（`toolbar`）不在此列：
 * 本应用的工具条不做方向键漫游，点完对齐按钮接着按方向键微调是常见动作。
 */
const ARROW_WIDGETS = [
  'listbox',
  'tree',
  'treegrid',
  'grid',
  'tablist',
  'radiogroup',
  'menu',
  'menubar',
  'slider',
  'spinbutton',
  'combobox',
]
  .map((r) => `[role="${r}"]`)
  .join(',')

/**
 * 方向键这一下是不是已经归了别人：控件自己处理过（`preventDefault`，如素材卡、元素树），
 * 或焦点在自己用方向键的控件里。输入框 / 可编辑文本 / 对话框已由 `inEditableTarget` 挡掉。
 */
export function arrowOwnedByWidget(e: KeyboardEvent): boolean {
  if (e.defaultPrevented) return true
  const el = e.target
  return el instanceof Element && el.closest(ARROW_WIDGETS) != null
}

/** ⌘] 族的 key → 未改写的那颗（`}` / `{` 是美式布局 ⇧ 改写出来的） */
const Z_ORDER_KEYS: Record<string, ']' | '[' | undefined> = { ']': ']', '}': ']', '[': '[', '{': '[' }

const MODIFIER_KEYS = new Set(['Shift', 'Alt', 'Meta', 'Control', 'CapsLock'])

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
  // 撤销/重做是离散动作：先把还开着的那一轮连续编辑整个收干净
  // （事务、安静计时器、预览会话、挂起的定稿渲染）。只靠 `undo()` 开头那句
  // `if (state.txn) state.endTxn()` 是不够的——hook 侧的 open 标记与计时器
  // 不知情，会一直悬着（issue #131）。
  finishActiveGesture()
  const doc = useDocumentStore.getState()
  const label = redo ? doc.redo() : doc.undo()
  const ui = useUiStore.getState()
  // 历史条目存的是描述符，这里在**显示那一刻**才翻——切语言后同一条历史
  // 会用新语言说话
  if (label) {
    ui.setStatus(
      msg(redo ? 'status.redone' : 'status.undone', { label: formatMessage(label) }, 'workspace'),
      'done',
    )
  } else {
    ui.setStatus(msg(redo ? 'status.nothingToRedo' : 'status.nothingToUndo', undefined, 'workspace'), 'info')
  }
}

export function useKeyboard() {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const ui = useUiStore.getState()

      // 方向键微调的一段只由方向键续上：按了别的键（删除、⌘D、Esc、打字……）先把这一段
      // 落定，免得后面的动作并进同一个事务 / 同一条撤销
      if (!e.key.startsWith('Arrow') && !MODIFIER_KEYS.has(e.key)) finishNudge()

      if (e.code === 'Space' && !inEditableTarget(e)) {
        if (!e.repeat) useViewportStore.getState().setSpaceDown(true)
        e.preventDefault()
        return
      }

      // 拖动 / 缩放 / 框选进行中按 Esc = **先取消这次手势**（与 pointercancel 同一条路：
      // 还原 DOM、不写 override、不进历史、不渲染），这一下不再做别的。放在一切判断之前：
      // 手势开着时 Esc 若先去清选中 / 退图内编辑态，手势会比它所属的编辑态活得更久，
      // 松手在已经离开的画面里写文档（QA STATE-02-B1）；焦点留在输入框里也照样取消——
      // 那次拖动是画布上的，与输入框无关。
      if (e.key === 'Escape' && cancelActivePointerGesture()) {
        e.preventDefault()
        return
      }

      const mod = e.metaKey || e.ctrlKey

      // ⌘S 在**输入框和对话框里也要拦**：那才是用户最想按它的时刻（刚给
      // 画布改完名、刚在对话框里填完东西）。不拦的话浏览器弹出「保存网页」
      // 另存对话框——用户以为自己存了文档，存下来的是一张 HTML。
      // 这是 inEditableTarget 之前**唯一**的例外：其余快捷键在输入框里都该
      // 让位给原生编辑行为。
      // ⌥⌘S = 把现在存为命名节点（ADR 0101）。按 `code` 判：⌥ 会把 macOS 上的
      // `key` 变成 ß；必须排在 ⌘S 前面——Windows 上 Ctrl+Alt+S 的 `key` 仍是 s，
      // 落进下一条就成了「保存」。它**不是**上面那个例外：在输入框 / 对话框里与
      // 其余快捷键一样让位，这里只是提前认领这组按键、让位时落空而不是落进 ⌘S。
      // Windows 上 AltGr 报成 Ctrl+Alt（波兰语 AltGr+S = ś）：那是在打字，同样落空
      if (mod && e.altKey && !e.shiftKey && e.code === 'KeyS') {
        if (e.getModifierState?.('AltGraph') || inEditableTarget(e)) return
        e.preventDefault()
        startNamedNode()
        return
      }
      // 带 ⌥ 的不认（`lib/keymap` 的 save / saveAs 都登记 `alt: false`）：⌥⇧⌘S 不是「另存为」，Windows 上
      // Ctrl+Alt+Shift+S 还是 AltGr+⇧S（波兰语 Ś），这一条排在输入框让位之前，认了就是吞掉用户在打的字
      if (mod && !e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault()
        // ⇧⌘S = 另存为一份命名的画布文件；⌘S = 真的保存当前文档
        if (e.shiftKey) useUiStore.getState().setLayoutOpen(true, 'save')
        else void runManualSave()
        return
      }

      if (inEditableTarget(e)) return
      const doc = useDocumentStore.getState()

      // ⇧⌘H = 打开 / 关闭排版时间线（ADR 0101；⌥⌘H 是 macOS 的「隐藏其他」，
      // ⇧⌘Y 会落进下面 ⌘Y 的重做分支）
      if (mod && e.shiftKey && !e.altKey && e.code === 'KeyH') {
        e.preventDefault()
        toggleTimeline()
        return
      }
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
          ui.setStatus(msg('status.redone', { label: formatMessage(label) }, 'workspace'), 'done')
        }
        return
      }
      if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        // 快速编辑里不加副本（副本落在版面上、这一屏看不见）：判据在 `duplicateSelected` 里
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
      if (mod && e.key.toLowerCase() === 'e') {
        e.preventDefault()
        ui.setExportOpen(true)
        return
      }
      // ⌘] / ⌘[ / ⇧⌘] / ⇧⌘[：浏览器给的 key 是 ⇧ 改写**之后**的字（美式布局 ⇧] = `}`、⇧[ = `{`），
      // 只认 `]` / `[` 的话置顶 / 置底在真浏览器里永远按不出来。不按 code 认：德语等布局上
      // BracketRight 那颗是 `+`，⌘+ 是放大
      const zKey = Z_ORDER_KEYS[e.key]
      if (mod && zKey) {
        e.preventDefault()
        changeZOrder(e.shiftKey ? (zKey === ']' ? 'top' : 'bottom') : zKey === ']' ? 'up' : 'down')
        return
      }
      if (mod && (e.key === '=' || e.key === '+' || e.key === '-')) {
        e.preventDefault()
        runZoomCommand(e.key === '-' ? 'out' : 'in')
        return
      }
      if (mod && e.key === '0') {
        e.preventDefault()
        runZoomCommand('actual')
        return
      }
      if (mod && e.key === '1') {
        e.preventDefault()
        runZoomCommand('fit')
        return
      }

      if (mod) return

      // ⇧2 = 缩放到选区（`lib/keymap` 的 zoomSelection）。按 code 认：⇧ 把 key 改成 @ / " 因布局而异；
      // ⌥ 组合是在打字，不认
      if (e.shiftKey && !e.altKey && e.code === 'Digit2') {
        e.preventDefault()
        runZoomCommand('selection')
        return
      }

      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        deleteSelection()
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        // 最上层浮层优先：时间线在预览就先退预览，再关抽屉
        if (ui.versionsOpen && useTimelineStore.getState().preview) {
          useTimelineStore.getState().setPreview(null)
        } else if (ui.versionsOpen) ui.setVersionsOpen(false)
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
        } else if (ui.cropTargetId) cancelCrop()
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
          finishCrop()
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
            else beginCrop(obj.id)
          }
        }
        return
      }
      if (e.key.startsWith('Arrow')) {
        // 方向键微调（ADR 0093）：图内编辑态推图内选中的元素，否则推画布选区；
        // 快速编辑里画布对象不动（`canvas/nudge.ts`）。归了微调就不让它冒出去滚界面
        if (arrowOwnedByWidget(e)) return
        if (nudgeKeyDown(e)) e.preventDefault()
        return
      }

      if (e.key === '?') {
        e.preventDefault()
        ui.setShortcutHelpOpen(true)
        return
      }

      const tool = TOOL_KEYS[e.key.toLowerCase()]
      if (tool) {
        // 快速编辑里没有画布标注。`openFastEdit()` 进来时把工具收回 `select`
        // 正是因为这一屏上根本没有它们的位置；顶栏也把这组按钮藏了起来。
        // **只藏按钮不挡快捷键等于没挡**：按一下 R，`CanvasStage` 照样
        // `startDraw()`，用户在一个只显示这张图的画面里画出一个看不见的矩形，
        // 而它进了文档、进了历史、还会跟着导出。
        if (tool !== 'select' && inFastEdit()) return
        e.preventDefault()
        ui.setTool(tool)
      }
    }

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') useViewportStore.getState().setSpaceDown(false)
      nudgeKeyUp(e)
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
